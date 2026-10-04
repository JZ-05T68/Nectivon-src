"""HBENCH_V2 采集与完整性工具箱。

实现 docs/EKB_HUMAN_BENCHMARK_V2_SPEC_2026-09-07.md §17 的数据完整性机制：

1. Session registry（append-only + 唯一性双保证）
2. Interaction uniqueness validator
3. CORPUS_REF manifest（freeze-corpus，含文档/页面/记忆基线）
4. Benchmark revision manifest（corpus_refs.json 追加式登记）
5. Append-only writer（flush + fsync + 前缀完整性 manifest）
6. Metrics generator（全部由 machine-readable raw 计算）
7. Denominator validator（invalid 剔除 / recovery 仅用预定义事件）
8. Mandatory field validator（spec §3.2 / §4.2 冻结字段）
9. Corpus drift fail-fast（schema/文档/页面/视觉索引漂移即阻断）
10. Raw SHA-256 manifest（字节前缀哈希，append-only 篡改证据）
11. Evaluation leakage checker（fixture 与 prompt 泄漏检测）
12. Campaign state updater（campaign_state.json 原子更新）

约定：
- SIMULATED 层数据位于 artifacts/human_simulation_v2/，SESSION_ID 必须是
  SIMV2- 前缀；REAL_USER 层（ZB- 前缀）属于独立目录，物理分离（spec §15）。
- 交互行的 SESSION_END 写入该交互自身的时间戳（会话进行中无法预知最终
  SESSION_END）；会话真值以 session 行的 SESSION_END 为准。
- 记忆集合变化（保存问答/经验演化等被测工作流本身产生）与视觉索引行数
  变化（E-1 派生索引随 agent bootstrap 滞后/更新）记为观察项，不判 corpus
  drift；文档集/页面数/schema 变化才是硬漂移（必须冻结新 CORPUS_REF 后才
  允许继续采集）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ART_DIR = REPO_ROOT / "artifacts" / "human_simulation_v2"
DEFAULT_STAGING_DB = REPO_ROOT / "staging-data" / "data" / "database" / "knowledge.db"
PROMPT_DIR = REPO_ROOT / "src" / "ai"

BENCHMARK_VERSION = "HBENCH_V2"
SESSION_ID_RE = re.compile(r"^SIMV2-([ABCD])-(\d{8})-(\d{3})$")
REAL_USER_PREFIX = "ZB-"

SESSION_TYPES = {"CONTINUOUS", "NEW", "REFRESH", "RESTART", "CROSS_DAY"}
PERSONAS = {"A", "B", "C", "D"}
TASK_SUCCESS_VALUES = {"success", "partial", "incomplete", "failed"}

COMMISSION_CLASSES = {
    "commission_wrong_fact",
    "commission_wrong_negative_claim",
    "commission_wrong_condition_attribution",
    "commission_wrong_version",
    "commission_wrong_evidence",
    "commission_wrong_object_binding",
}
OMISSION_CLASSES = {
    "honest_omission_recall_gap",
    "honest_omission_reference_cut",
    "honest_omission_anchor_shadow",
    "honest_omission_blur",
}
OMISSION_OUT_OF_SCOPE = "honest_omission_out_of_scope"
CLASSIFICATIONS = (
    {"pass"}
    | COMMISSION_CLASSES
    | {"fabrication", "silent_stale"}
    | OMISSION_CLASSES
    | {OMISSION_OUT_OF_SCOPE}
    | {
        "honest_boundary",
        "honest_refusal",
        "honest_meta",
        "user_blocked",
        "recovery_success",
        "invalid",
    }
)

CAMPAIGN_STATUS_VALUES = {
    "INITIALIZING",
    "RUNNING",
    "PARTIAL_CHECKPOINTED",
    "COMPLETE",
    "BLOCKED_GIT",
    "BLOCKED_CORPUS_DRIFT",
    "BLOCKED_FORMAL_DATA_RISK",
    "BLOCKED_P0",
    "BLOCKED_P1",
    "BLOCKED_P2",
    "BLOCKED_ARCHITECTURE_DECISION",
}

SESSION_MANDATORY = [
    "BENCHMARK_VERSION",
    "CORPUS_REF",
    "SESSION_ID",
    "SESSION_TYPE",
    "PERSONA",
    "SPECIAL_COHORT",
    "SESSION_START",
    "SESSION_END",
    "USER_GOAL",
    "FIRST_ACTION",
    "FIRST_ACTION_SUCCESS",
    "TASK_SUCCESS",
    "TRUST_FAILURE",
    "USER_BLOCKED",
    "NOTES",
]

INTERACTION_MANDATORY = [
    "BENCHMARK_VERSION",
    "CORPUS_REF",
    "SESSION_ID",
    "INTERACTION_ID",
    "PERSONA",
    "SESSION_TYPE",
    "TASK_ID",
    "TASK_CLASS",
    "SESSION_START",
    "SESSION_END",
    "EXPECTED_BEHAVIOR",
    "ACTUAL_BEHAVIOR",
    "FIRST_ACTION_SUCCESS",
    "TASK_SUCCESS",
    "COMMISSION",
    "FABRICATION",
    "SILENT_STALE",
    "VERSION_CONDITION_ERROR",
    "EVIDENCE_ERROR",
    "EXPERIENCE_AUTHORITY_ERROR",
    "HONEST_OMISSION",
    "RECOVERY_EVENT",
    "RECOVERY_SUCCESS",
    "TRUST_FAILURE",
    "USER_BLOCKED",
    "INVALID",
    "CLASSIFICATION",
    "NOTES",
]

INTERACTION_BOOL_FLAGS = [
    "FIRST_ACTION_SUCCESS",
    "COMMISSION",
    "FABRICATION",
    "SILENT_STALE",
    "VERSION_CONDITION_ERROR",
    "EVIDENCE_ERROR",
    "EXPERIENCE_AUTHORITY_ERROR",
    "HONEST_OMISSION",
    "RECOVERY_EVENT",
    "RECOVERY_SUCCESS",
    "TRUST_FAILURE",
    "USER_BLOCKED",
    "INVALID",
]

SESSION_BOOL_FLAGS = ["FIRST_ACTION_SUCCESS", "TRUST_FAILURE", "USER_BLOCKED"]

RAW_FILES = ("sessions.jsonl", "interactions.jsonl")

ISO_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)$")


class ValidationError(Exception):
    """数据校验失败（机制级，阻断写入或判分）。"""


class DriftError(Exception):
    """Corpus 硬漂移（schema/文档/页面/视觉索引），必须冻结新 CORPUS_REF。"""


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# JSONL append-only writer（§44 写入完整性：flush + fsync）
# ---------------------------------------------------------------------------


def append_jsonl_row(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False, sort_keys=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            raise ValidationError(f"{path.name}:{lineno} 存在空行（append-only 完整性破坏）")
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"{path.name}:{lineno} JSON 解析失败：{exc}") from exc
    return rows


# ---------------------------------------------------------------------------
# 行级校验（mandatory fields + 枚举 + classification/flag 一致性）
# ---------------------------------------------------------------------------


def _require_bool(row: dict[str, Any], field: str, label: str) -> None:
    value = row.get(field)
    if not isinstance(value, bool):
        raise ValidationError(f"{label}: {field} 必须是 bool，实际 {value!r}")


def _require_iso(row: dict[str, Any], field: str, label: str) -> None:
    value = row.get(field)
    if not isinstance(value, str) or not ISO_TS_RE.match(value):
        raise ValidationError(f"{label}: {field} 必须是 ISO8601+TZ 时间戳，实际 {value!r}")


def validate_session_row(
    row: dict[str, Any],
    corpus_refs: set[str],
    expected_layer: str = "simulated",
) -> None:
    label = f"session {row.get('SESSION_ID', '?')}"
    for field in SESSION_MANDATORY:
        if field == "SPECIAL_COHORT":
            continue  # str | null，null 合法（单独校验）
        if row.get(field) in (None, ""):
            raise ValidationError(f"{label}: mandatory 字段 {field} 缺失或为空")
    if row["BENCHMARK_VERSION"] != BENCHMARK_VERSION:
        raise ValidationError(f"{label}: BENCHMARK_VERSION 必须是 {BENCHMARK_VERSION}")
    session_id = row["SESSION_ID"]
    if expected_layer == "simulated":
        if not SESSION_ID_RE.match(session_id):
            raise ValidationError(
                f"{label}: SESSION_ID 不符合 SIMV2-{{A|B|C|D}}-YYYYMMDD-SEQ 格式"
            )
    elif not session_id.startswith(REAL_USER_PREFIX):
        raise ValidationError(f"{label}: REAL_USER 层 SESSION_ID 必须以 {REAL_USER_PREFIX} 开头")
    if row["SESSION_TYPE"] not in SESSION_TYPES:
        raise ValidationError(f"{label}: SESSION_TYPE 非法：{row['SESSION_TYPE']}")
    if row["PERSONA"] not in PERSONAS:
        raise ValidationError(f"{label}: PERSONA 必须是 A/B/C/D，实际 {row['PERSONA']!r}")
    if row["CORPUS_REF"] not in corpus_refs:
        raise ValidationError(f"{label}: CORPUS_REF {row['CORPUS_REF']!r} 未在冻结登记中")
    if row["TASK_SUCCESS"] not in TASK_SUCCESS_VALUES:
        raise ValidationError(f"{label}: TASK_SUCCESS 非法：{row['TASK_SUCCESS']}")
    for field in SESSION_BOOL_FLAGS:
        _require_bool(row, field, label)
    _require_iso(row, "SESSION_START", label)
    _require_iso(row, "SESSION_END", label)
    if row["SESSION_END"] < row["SESSION_START"]:
        raise ValidationError(f"{label}: SESSION_END 早于 SESSION_START")
    special = row["SPECIAL_COHORT"]
    if special is not None and not isinstance(special, str):
        raise ValidationError(f"{label}: SPECIAL_COHORT 必须是 str 或 null")


def validate_interaction_row(
    row: dict[str, Any],
    corpus_refs: set[str],
    expected_layer: str = "simulated",
) -> None:
    label = f"interaction {row.get('INTERACTION_ID', '?')}"
    for field in INTERACTION_MANDATORY:
        if row.get(field) in (None, ""):
            raise ValidationError(f"{label}: mandatory 字段 {field} 缺失或为空")
    session_id = row["SESSION_ID"]
    if expected_layer == "simulated":
        if not SESSION_ID_RE.match(session_id):
            raise ValidationError(f"{label}: SESSION_ID 不符合 SIMV2 格式：{session_id!r}")
    elif not session_id.startswith(REAL_USER_PREFIX):
        raise ValidationError(f"{label}: REAL_USER 层 SESSION_ID 必须以 {REAL_USER_PREFIX} 开头")
    if "INTERACTION_INDEX" not in row:
        raise ValidationError(f"{label}: 缺少 INTERACTION_INDEX")
    expected_iid = f"{session_id}#T{row['INTERACTION_INDEX']}"
    if row["INTERACTION_ID"] != expected_iid:
        raise ValidationError(f"{label}: INTERACTION_ID 必须是 {expected_iid}")
    index = row["INTERACTION_INDEX"]
    if not isinstance(index, int) or isinstance(index, bool) or index < 1:
        raise ValidationError(f"{label}: INTERACTION_INDEX 必须是 >=1 的整数")
    if row["BENCHMARK_VERSION"] != BENCHMARK_VERSION:
        raise ValidationError(f"{label}: BENCHMARK_VERSION 必须是 {BENCHMARK_VERSION}")
    if row["SESSION_TYPE"] not in SESSION_TYPES:
        raise ValidationError(f"{label}: SESSION_TYPE 非法：{row['SESSION_TYPE']}")
    if row["PERSONA"] not in PERSONAS:
        raise ValidationError(f"{label}: PERSONA 必须是 A/B/C/D")
    if row["CORPUS_REF"] not in corpus_refs:
        raise ValidationError(f"{label}: CORPUS_REF {row['CORPUS_REF']!r} 未在冻结登记中")
    if row["TASK_SUCCESS"] not in TASK_SUCCESS_VALUES:
        raise ValidationError(f"{label}: TASK_SUCCESS 非法：{row['TASK_SUCCESS']}")
    classification = row["CLASSIFICATION"]
    if classification not in CLASSIFICATIONS:
        raise ValidationError(f"{label}: CLASSIFICATION 非法：{classification!r}")
    for field in INTERACTION_BOOL_FLAGS:
        _require_bool(row, field, label)
    _require_iso(row, "SESSION_START", label)
    _require_iso(row, "SESSION_END", label)
    _validate_flag_consistency(row, label)


def _validate_flag_consistency(row: dict[str, Any], label: str) -> None:
    classification = row["CLASSIFICATION"]
    invalid = row["INVALID"]
    if invalid != (classification == "invalid"):
        raise ValidationError(f"{label}: INVALID 与 CLASSIFICATION=invalid 不一致")
    if invalid:
        error_flags = [
            f
            for f in ("COMMISSION", "FABRICATION", "SILENT_STALE", "HONEST_OMISSION")
            if row[f]
        ]
        if error_flags:
            raise ValidationError(f"{label}: invalid 行不得携带错误 flag：{error_flags}")
        return
    is_commission = classification in COMMISSION_CLASSES
    if is_commission != row["COMMISSION"]:
        raise ValidationError(f"{label}: COMMISSION flag 与 CLASSIFICATION={classification} 不一致")
    if row["FABRICATION"] != (classification == "fabrication"):
        raise ValidationError(f"{label}: FABRICATION flag 与 CLASSIFICATION 不一致")
    if row["SILENT_STALE"] != (classification == "silent_stale"):
        raise ValidationError(f"{label}: SILENT_STALE flag 与 CLASSIFICATION 不一致")
    if row["HONEST_OMISSION"] != (classification in OMISSION_CLASSES):
        raise ValidationError(
            f"{label}: HONEST_OMISSION 必须恰好对应 honest_omission_* 家族"
            "（out_of_scope 单列不计）"
        )
    if is_commission or row["FABRICATION"]:
        if not row["TRUST_FAILURE"]:
            raise ValidationError(f"{label}: commission/fabrication 必须置 TRUST_FAILURE=true")
    if classification == "recovery_success" and not (
        row["RECOVERY_EVENT"] and row["RECOVERY_SUCCESS"]
    ):
        raise ValidationError(
            f"{label}: CLASSIFICATION=recovery_success 要求"
            " RECOVERY_EVENT 与 RECOVERY_SUCCESS 均 true"
        )
    if row["RECOVERY_SUCCESS"] and not row["RECOVERY_EVENT"]:
        raise ValidationError(
            f"{label}: RECOVERY_SUCCESS=true 但 RECOVERY_EVENT=false（禁止事后推断分母）"
        )


# ---------------------------------------------------------------------------
# Corpus freeze（组件 3/4）
# ---------------------------------------------------------------------------


def census_staging_db(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        raise DriftError(f"staging DB 不存在：{db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        schema_version = cur.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        documents = cur.execute(
            "SELECT id, title, sha256 FROM documents ORDER BY id"
        ).fetchall()
        pages = cur.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        visual_index = cur.execute("SELECT COUNT(*) FROM page_visual_index").fetchone()[0]
        memories = cur.execute(
            "SELECT id, status, content_fingerprint FROM knowledge_memory_entries ORDER BY id"
        ).fetchall()
        integrity = cur.execute("PRAGMA integrity_check").fetchone()[0]
        fk = cur.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()
    if integrity != "ok":
        raise DriftError(f"staging DB integrity 异常：{integrity}")
    if fk:
        raise DriftError(f"staging DB 外键检查失败：{fk}")
    doc_rows = [{"id": d[0], "title": d[1], "sha256": d[2]} for d in documents]
    active_memory_rows = [
        {"id": m[0], "content_fingerprint": m[2]} for m in memories if m[1] == "active"
    ]
    return {
        "schema_version": schema_version,
        "documents": doc_rows,
        "pages_count": pages,
        "visual_index_count": visual_index,
        "memories_active": active_memory_rows,
        "memories_total": len(memories),
    }


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze_corpus(
    art_dir: Path,
    db_path: Path,
    benchmark_version: str = BENCHMARK_VERSION,
    git_head: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """清点 staging 并追加冻结一条新 CORPUS_REF（append-only 登记）。"""
    census = census_staging_db(db_path)
    refs_path = art_dir / "corpus_refs.json"
    history: list[dict[str, Any]] = []
    if refs_path.exists():
        history = json.loads(refs_path.read_text(encoding="utf-8"))["refs"]
    revision = len(history)
    date_tag = datetime.now(UTC).astimezone().strftime("%Y%m%d")
    ref_id = f"HBV2-CORPUS-{date_tag}-R{revision}"
    seed_scripts = {
        p.name: _sha256_file(p)
        for p in sorted(REPO_ROOT.glob("scripts/build_dirty_long_term_v2*.py"))
    }
    eval_fixtures = REPO_ROOT / "tests" / "fixtures" / "reliability_eval"
    human_fixtures = REPO_ROOT / "tests" / "fixtures" / "human_simulation"
    fixture_manifests = {
        p.name: _sha256_file(p)
        for p in sorted(eval_fixtures.glob("corpus_manifest_dirty_v*.json"))
        + sorted(human_fixtures.glob("benchmark_v1.json"))
    }
    record = {
        "corpus_ref_id": ref_id,
        "benchmark_version": benchmark_version,
        "frozen_at_utc": utc_now_iso(),
        "git_head": git_head,
        "staging_db_path": str(db_path),
        "staging_db_sha256_at_freeze": _sha256_file(db_path),
        "corpus_root": str(db_path.parent.parent),
        "seed_identifier": (
            "dirty-long-term-v3 (lineage: corpus_manifest_dirty_v4.json"
            " + 后续合法扩容至 66 docs)"
        ),
        "seed_script_sha256": seed_scripts,
        "fixture_manifest_sha256": fixture_manifests,
        "schema_version": census["schema_version"],
        "documents_count": len(census["documents"]),
        "pages_count": census["pages_count"],
        "memories_active_count": len(census["memories_active"]),
        "memories_total_count": census["memories_total"],
        "visual_index_count": census["visual_index_count"],
        "documents": census["documents"],
        "memories_active_ids": [m["id"] for m in census["memories_active"]],
        "memories_active_fingerprints": {
            str(m["id"]): m["content_fingerprint"] for m in census["memories_active"]
        },
        "notes": notes,
    }
    history.append(record)
    refs_path.parent.mkdir(parents=True, exist_ok=True)
    refs_path.write_text(
        json.dumps({"refs": history}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return record


def _current_corpus_ref(art_dir: Path) -> str:
    refs_path = art_dir / "corpus_refs.json"
    refs = json.loads(refs_path.read_text(encoding="utf-8"))["refs"]
    return refs[-1]["corpus_ref_id"]


def load_corpus_refs(art_dir: Path) -> tuple[dict[str, Any], set[str]]:
    refs_path = art_dir / "corpus_refs.json"
    if not refs_path.exists():
        raise ValidationError("corpus_refs.json 不存在：先执行 freeze-corpus")
    history = json.loads(refs_path.read_text(encoding="utf-8"))["refs"]
    if not history:
        raise ValidationError("corpus_refs.json 无冻结记录")
    return history[-1], {r["corpus_ref_id"] for r in history}


def check_corpus_drift(frozen: dict[str, Any], db_path: Path) -> dict[str, Any]:
    """硬漂移 fail-fast：schema / 文档集 / 页面数 / 视觉索引。

    记忆集合变化属被测工作流产物，记为观察项不阻断。
    """
    census = census_staging_db(db_path)
    drift: list[str] = []
    if census["schema_version"] != frozen["schema_version"]:
        drift.append(
            f"schema {frozen['schema_version']} -> {census['schema_version']}"
        )
    frozen_docs = {d["id"]: d["sha256"] for d in frozen["documents"]}
    current_docs = {d["id"]: d["sha256"] for d in census["documents"]}
    removed = sorted(set(frozen_docs) - set(current_docs))
    added = sorted(set(current_docs) - set(frozen_docs))
    changed = sorted(
        k
        for k in set(frozen_docs) & set(current_docs)
        if frozen_docs[k] != current_docs[k]
    )
    if removed:
        drift.append(f"冻结文档被删除：{removed}")
    if changed:
        drift.append(f"冻结文档内容变化：{changed}")
    if added:
        drift.append(f"新增文档（须冻结新 CORPUS_REF）：{added}")
    if census["pages_count"] != frozen["pages_count"]:
        drift.append(f"pages {frozen['pages_count']} -> {census['pages_count']}")
    frozen_mem = set(frozen["memories_active_ids"])
    current_mem = {m["id"] for m in census["memories_active"]}
    memory_observations = {
        "new_memory_ids": sorted(current_mem - frozen_mem),
        "missing_or_inactive_frozen_memory_ids": sorted(frozen_mem - current_mem),
        "changed_frozen_memory_ids": sorted(
            mid
            for mid in frozen_mem & current_mem
            if census_fingerprint(census, mid)
            != frozen["memories_active_fingerprints"].get(str(mid))
        ),
    }
    observations = {
        "visual_index_count_change": (
            f"{frozen['visual_index_count']} -> {census['visual_index_count']}"
            if census["visual_index_count"] != frozen["visual_index_count"]
            else None
        ),
    }
    return {
        "drift": drift,
        "memory_observations": memory_observations,
        "derived_index_observations": observations,
        "census": {
            "schema_version": census["schema_version"],
            "documents_count": len(census["documents"]),
            "pages_count": census["pages_count"],
            "memories_active_count": len(census["memories_active"]),
            "visual_index_count": census["visual_index_count"],
        },
    }


def census_fingerprint(census: dict[str, Any], memory_id: int) -> str | None:
    for m in census["memories_active"]:
        if m["id"] == memory_id:
            return m["content_fingerprint"]
    return None


# ---------------------------------------------------------------------------
# Registry + 唯一性（组件 1/2）
# ---------------------------------------------------------------------------


def load_rows(art_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sessions = load_jsonl(art_dir / "sessions.jsonl")
    interactions = load_jsonl(art_dir / "interactions.jsonl")
    return sessions, interactions


def register_session(art_dir: Path, row: dict[str, Any], db_path: Path | None = None) -> None:
    frozen, refs = load_corpus_refs(art_dir)
    validate_session_row(row, refs)
    if row["CORPUS_REF"] != frozen["corpus_ref_id"]:
        raise ValidationError(
            f"必须使用当前冻结 CORPUS_REF {frozen['corpus_ref_id']}，"
            f"实际 {row['CORPUS_REF']}（corpus 变更须先 freeze-corpus）"
        )
    if db_path is not None:
        drift_report = check_corpus_drift(frozen, db_path)
        if drift_report["drift"]:
            raise DriftError("；".join(drift_report["drift"]))
    sessions, _ = load_rows(art_dir)
    existing = {s["SESSION_ID"] for s in sessions}
    if row["SESSION_ID"] in existing:
        raise ValidationError(f"SESSION_ID 重复（append-only registry 拒绝）：{row['SESSION_ID']}")
    append_jsonl_row(art_dir / "sessions.jsonl", row)


def registered_session_ids(art_dir: Path) -> set[str]:
    """closed（sessions.jsonl）+ in-flight（open_sessions.json）全集。"""
    closed = {s["SESSION_ID"] for s in load_jsonl(art_dir / "sessions.jsonl")}
    open_path = art_dir / "open_sessions.json"
    if open_path.exists():
        state = json.loads(open_path.read_text(encoding="utf-8"))
        closed |= set(state.get("sessions", {}))
    return closed


def record_interaction(art_dir: Path, row: dict[str, Any], db_path: Path | None = None) -> None:
    frozen, refs = load_corpus_refs(art_dir)
    validate_interaction_row(row, refs)
    if row["CORPUS_REF"] != frozen["corpus_ref_id"]:
        raise ValidationError(
            f"必须使用当前冻结 CORPUS_REF {frozen['corpus_ref_id']}，"
            f"实际 {row['CORPUS_REF']}（corpus 变更须先 freeze-corpus）"
        )
    if db_path is not None:
        drift_report = check_corpus_drift(frozen, db_path)
        if drift_report["drift"]:
            raise DriftError("；".join(drift_report["drift"]))
    sessions, interactions = load_rows(art_dir)
    session_ids = registered_session_ids(art_dir)
    if row["SESSION_ID"] not in session_ids:
        raise ValidationError(
            f"SESSION_ID 未注册（必须先 register-session）：{row['SESSION_ID']}"
        )
    seen = {(i["SESSION_ID"], i["INTERACTION_INDEX"]) for i in interactions}
    key = (row["SESSION_ID"], row["INTERACTION_INDEX"])
    if key in seen:
        raise ValidationError(f"(session, index) 重复：{key}")
    indexes = sorted(
        i["INTERACTION_INDEX"]
        for i in interactions
        if i["SESSION_ID"] == row["SESSION_ID"]
    )
    if indexes != list(range(1, len(indexes) + 1)):
        raise ValidationError(f"session {row['SESSION_ID']} 既有 index 不连续：{indexes}")
    if row["INTERACTION_INDEX"] != len(indexes) + 1:
        raise ValidationError(
            f"INTERACTION_INDEX 必须 = {len(indexes) + 1}（逐条递增），"
            f"实际 {row['INTERACTION_INDEX']}"
        )
    append_jsonl_row(art_dir / "interactions.jsonl", row)


# ---------------------------------------------------------------------------
# 全量校验（组件 8/9 + ledger 一致性）
# ---------------------------------------------------------------------------


def validate_all(
    art_dir: Path,
    db_path: Path | None,
    allow_open_sessions: bool = False,
    expected_current_ref: str | None = None,
) -> dict[str, Any]:
    frozen, refs = load_corpus_refs(art_dir)
    sessions, interactions = load_rows(art_dir)
    errors: list[str] = []
    seen_session_ids: set[str] = set()
    for s in sessions:
        try:
            validate_session_row(s, refs)
        except ValidationError as exc:
            errors.append(str(exc))
        if s["SESSION_ID"] in seen_session_ids:
            errors.append(f"SESSION_ID 重复：{s['SESSION_ID']}")
        seen_session_ids.add(s["SESSION_ID"])
    seen_keys: set[tuple[str, int]] = set()
    by_session: dict[str, list[int]] = {}
    for i in interactions:
        try:
            validate_interaction_row(i, refs)
        except ValidationError as exc:
            errors.append(str(exc))
        key = (i["SESSION_ID"], i["INTERACTION_INDEX"])
        if key in seen_keys:
            errors.append(f"interaction 重复：{key}")
        seen_keys.add(key)
        by_session.setdefault(i["SESSION_ID"], []).append(i["INTERACTION_INDEX"])
    for sid, indexes in sorted(by_session.items()):
        if indexes != list(range(1, len(indexes) + 1)):
            errors.append(f"{sid} index 不连续：{indexes}")
    orphans = sorted(set(by_session) - seen_session_ids)
    if orphans:
        errors.append(f"interaction 存在未注册 session：{orphans}")
    empty_sessions = sorted(seen_session_ids - set(by_session))
    if empty_sessions and not allow_open_sessions:
        errors.append(f"session 无任何 interaction（未闭合）：{empty_sessions}")
    refs_in_use = sorted({r["CORPUS_REF"] for r in sessions + interactions})
    unknown_refs = sorted(set(refs_in_use) - refs)
    if unknown_refs:
        errors.append(f"存在未登记 CORPUS_REF 的行：{unknown_refs}")
    if expected_current_ref and frozen["corpus_ref_id"] != expected_current_ref:
        errors.append(f"当前冻结 CORPUS_REF={frozen['corpus_ref_id']}，预期 {expected_current_ref}")
    manifest_report = check_raw_manifest(art_dir)
    if manifest_report["violations"]:
        errors.extend(f"manifest: {v}" for v in manifest_report["violations"])
    drift_report: dict[str, Any] | None = None
    if db_path is not None:
        drift_report = check_corpus_drift(frozen, db_path)
        if drift_report["drift"]:
            errors.extend(f"corpus drift: {d}" for d in drift_report["drift"])
    report = {
        "validated_at_utc": utc_now_iso(),
        "sessions_rows": len(sessions),
        "unique_session_ids": len(seen_session_ids),
        "interactions_rows": len(interactions),
        "unique_interaction_keys": len(seen_keys),
        "open_sessions": empty_sessions if allow_open_sessions else [],
        "manifest_report": manifest_report,
        "corpus_drift_report": drift_report,
        "errors": errors,
        "pass": not errors,
    }
    return report


# ---------------------------------------------------------------------------
# Raw manifest（组件 10，字节前缀完整性）
# ---------------------------------------------------------------------------


def write_raw_manifest(art_dir: Path) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for name in RAW_FILES:
        path = art_dir / name
        if path.exists():
            data = path.read_bytes()
            files[name] = {"bytes": len(data), "sha256_prefix": hashlib.sha256(data).hexdigest()}
    manifest = {
        "generated_at_utc": utc_now_iso(),
        "note": (
            "sha256_prefix 覆盖 manifest 生成时文件全部字节；"
            "validator 校验前缀未被改写（append-only 证据）"
        ),
        "files": files,
    }
    (art_dir / "raw_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return manifest


def check_raw_manifest(art_dir: Path) -> dict[str, Any]:
    manifest_path = art_dir / "raw_manifest.json"
    violations: list[str] = []
    if not manifest_path.exists():
        return {"present": False, "violations": violations}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, record in manifest["files"].items():
        path = art_dir / name
        if not path.exists():
            violations.append(f"{name} 已被删除")
            continue
        data = path.read_bytes()
        if len(data) < record["bytes"]:
            violations.append(
                f"{name} 字节数 {len(data)} < manifest 记录 {record['bytes']}（历史被改写）"
            )
        elif hashlib.sha256(data[: record["bytes"]]).hexdigest() != record["sha256_prefix"]:
            violations.append(f"{name} 前 {record['bytes']} 字节哈希不符（历史被改写）")
    return {"present": True, "violations": violations}


# ---------------------------------------------------------------------------
# Metrics generator + denominator validator（组件 6/7）
# ---------------------------------------------------------------------------


def _excluded_interaction_ids(art_dir: Path) -> dict[str, str]:
    """corrections.jsonl 中标记 excluded_from_metrics 的交互（失效记录，保留 raw）。"""
    path = art_dir / "corrections.jsonl"
    if not path.exists():
        return {}
    excluded: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        iid = entry.get("interaction_id")
        if entry.get("excluded_from_metrics") and iid:
            excluded[iid] = entry.get("correction_id", "?")
    return excluded


def compute_metrics(art_dir: Path) -> dict[str, Any]:
    """全部指标由 raw JSONL 计算；invalid 与 correction 失效行不进入任何可靠性分母。"""
    sessions, interactions = load_rows(art_dir)
    excluded = _excluded_interaction_ids(art_dir)
    valid = [
        i
        for i in interactions
        if not i["INVALID"] and i["INTERACTION_ID"] not in excluded
    ]
    invalid = [
        i
        for i in interactions
        if i["INVALID"] or i["INTERACTION_ID"] in excluded
    ]
    unique_sessions = {s["SESSION_ID"] for s in sessions}
    n_valid = len(valid)

    def count_flag(flag: str) -> int:
        return sum(1 for i in valid if i[flag])

    omissions = count_flag("HONEST_OMISSION")
    out_of_scope = sum(1 for i in valid if i["CLASSIFICATION"] == OMISSION_OUT_OF_SCOPE)
    recovery_denominator = sum(1 for i in valid if i["RECOVERY_EVENT"])
    recovery_success = sum(1 for i in valid if i["RECOVERY_SUCCESS"])
    task_success_rows = [s["TASK_SUCCESS"] for s in sessions]
    session_trust_rows = sum(1 for s in sessions if s["TRUST_FAILURE"])
    session_blocked_rows = sum(1 for s in sessions if s["USER_BLOCKED"])

    def rate(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    by_persona: dict[str, int] = {}
    by_session_type: dict[str, int] = {}
    for s in sessions:
        by_persona[s["PERSONA"]] = by_persona.get(s["PERSONA"], 0) + 1
        by_session_type[s["SESSION_TYPE"]] = by_session_type.get(s["SESSION_TYPE"], 0) + 1
    classification_counts: dict[str, int] = {}
    for i in valid:
        classification_counts[i["CLASSIFICATION"]] = (
            classification_counts.get(i["CLASSIFICATION"], 0) + 1
        )
    # ledger 一致性前置门槛：metrics 只在校验通过的数据上生成
    report = validate_all(art_dir, None, allow_open_sessions=True)
    if not report["pass"]:
        raise ValidationError(
            "metrics 生成被拒绝：raw 校验未通过 -> " + "; ".join(report["errors"])
        )
    metrics = {
        "generated_at_utc": utc_now_iso(),
        "data_layer": "SIMULATED",
        "note": "全部为模拟 Agent 用户数据，不是 real-user metrics（spec §15）",
        "benchmark_version": BENCHMARK_VERSION,
        "corpus_ref": _current_corpus_ref(art_dir),
        "unique_sessions": len(unique_sessions),
        "valid_interactions": n_valid,
        "invalid_interactions": len(invalid),
        "correction_excluded_interactions": sorted(excluded),
        "invalid_reasons": sorted({i["NOTES"][:60] for i in invalid if i["NOTES"]}),
        "commission_count": count_flag("COMMISSION"),
        "commission_rate": rate(count_flag("COMMISSION"), n_valid),
        "honest_omission_count": omissions,
        "honest_omission_rate": rate(omissions, n_valid),
        "honest_omission_out_of_scope_count": out_of_scope,
        "fabrication_count": count_flag("FABRICATION"),
        "silent_stale_count": count_flag("SILENT_STALE"),
        "version_condition_error_count": count_flag("VERSION_CONDITION_ERROR"),
        "evidence_error_count": count_flag("EVIDENCE_ERROR"),
        "experience_authority_error_count": count_flag("EXPERIENCE_AUTHORITY_ERROR"),
        "trust_failure_interactions": count_flag("TRUST_FAILURE"),
        "task_success_distribution": {
            v: task_success_rows.count(v) for v in sorted(TASK_SUCCESS_VALUES)
        },
        "session_trust_failure_rows": session_trust_rows,
        "session_trust_failure_unique_sessions": len(
            {i["SESSION_ID"] for i in interactions if i["TRUST_FAILURE"] and not i["INVALID"]}
        ),
        "user_blocked_rows": session_blocked_rows,
        "user_blocked_unique_sessions": len(
            {i["SESSION_ID"] for i in interactions if i["USER_BLOCKED"] and not i["INVALID"]}
        ),
        "first_action_success_sessions": sum(1 for s in sessions if s["FIRST_ACTION_SUCCESS"]),
        "recovery_event_denominator": recovery_denominator,
        "recovery_success_count": recovery_success,
        "recovery_success_rate": rate(recovery_success, recovery_denominator),
        "persona_counts": dict(sorted(by_persona.items())),
        "session_type_counts": dict(sorted(by_session_type.items())),
        "classification_counts": dict(sorted(classification_counts.items())),
    }
    return metrics


def write_metrics(art_dir: Path) -> dict[str, Any]:
    metrics = compute_metrics(art_dir)
    (art_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return metrics


# ---------------------------------------------------------------------------
# Campaign state updater（组件 12）
# ---------------------------------------------------------------------------


def update_campaign_state(art_dir: Path, updates: dict[str, Any]) -> dict[str, Any]:
    state_path = art_dir / "campaign_state.json"
    state: dict[str, Any] = {}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    status = updates.get("campaign_status", state.get("campaign_status"))
    if status is not None and status not in CAMPAIGN_STATUS_VALUES:
        raise ValidationError(f"campaign_status 非法：{status!r}")
    state.update(updates)
    state["last_state_update"] = utc_now_iso()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, state_path)
    return state


# ---------------------------------------------------------------------------
# Evaluation leakage checker（组件 11）
# ---------------------------------------------------------------------------

_DEVICE_TOKEN_RE = re.compile(r"\b[A-Z]{1,6}[-_]?\d+[A-Za-z0-9-]*\b")
_VALUE_TOKEN_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:N·m|Nm|°C|A|V|MPa|kPa|h|Hz|mm|kW|W|°)\b")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _extract_string_literals(path: Path) -> list[str]:
    """提取可能成为 prompt 内容的字符串常量；排除 docstring（开发者文档，
    从不发送给模型）与纯技术标识符噪声。"""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    literals: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and len(node.value) >= 6
            and id(node) not in docstrings
        ):
            literals.append(node.value)
    return literals


def check_leakage(fixture_paths: list[Path], prompt_dir: Path = PROMPT_DIR) -> dict[str, Any]:
    """fixture 答案值/设备名/问题模板不得出现在 production prompt 中。"""
    prompt_literals: list[str] = []
    raw_prompt_text: list[str] = []
    for py in sorted(prompt_dir.glob("*.py")):
        literals = _extract_string_literals(py)
        prompt_literals.append(_normalize("\n".join(literals)))
        raw_prompt_text.append("\n".join(literals))
    prompt_blob = "\n".join(prompt_literals)
    raw_blob = "\n".join(raw_prompt_text)
    findings: list[dict[str, str]] = []

    def scan(value: Any, source: str) -> None:
        if isinstance(value, str):
            for token_match in _DEVICE_TOKEN_RE.finditer(value):
                token = token_match.group(0)
                # 词边界匹配原始 prompt 文本，避免 sha256 之类的标识符子串误报
                if re.search(rf"\b{re.escape(token)}\b", raw_blob):
                    findings.append({"fixture": source, "type": "device_token", "token": token})
            for token_match in _VALUE_TOKEN_RE.finditer(value):
                token = _normalize(token_match.group(0))
                if token in prompt_blob:
                    findings.append({"fixture": source, "type": "value_token", "token": token})
            if len(value) >= 12 and _normalize(value) in prompt_blob:
                findings.append(
                    {"fixture": source, "type": "literal_template", "token": value[:60]}
                )
        elif isinstance(value, list):
            for item in value:
                scan(item, source)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key in {"notes", "description", "rationale"}:
                    continue
                scan(item, source)

    for fixture in fixture_paths:
        data = json.loads(fixture.read_text(encoding="utf-8"))
        scan(data, fixture.name)
    scanned = [str(p) for p in fixture_paths]
    return {"findings": findings, "pass": not findings, "scanned_files": scanned}


# ---------------------------------------------------------------------------
# Infra smoke（prompt §16：12 项 PASS，sandbox 合成数据，不触碰真实 raw）
# ---------------------------------------------------------------------------


def _build_sandbox_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY);
        INSERT INTO schema_migrations VALUES (15);
        CREATE TABLE documents (id INTEGER PRIMARY KEY, title TEXT, sha256 TEXT);
        INSERT INTO documents VALUES (1, 'smoke_doc', 'a' * 64);
        CREATE TABLE pages (id INTEGER PRIMARY KEY);
        INSERT INTO pages VALUES (1), (2);
        CREATE TABLE page_visual_index (id INTEGER PRIMARY KEY);
        INSERT INTO page_visual_index VALUES (1);
        CREATE TABLE knowledge_memory_entries (
            id INTEGER PRIMARY KEY, status TEXT, content_fingerprint TEXT
        );
        INSERT INTO knowledge_memory_entries VALUES (1, 'active', 'fp1');
        """
    )
    conn.commit()
    conn.close()


_SMOKE_CORPUS_REF = "SANDBOX-R0"


def _smoke_session(
    session_id: str, persona: str, session_type: str, task_success: str
) -> dict[str, Any]:
    start = "2026-01-01T10:00:00+08:00"
    end = "2026-01-01T10:30:00+08:00"
    return {
        "BENCHMARK_VERSION": BENCHMARK_VERSION,
        "CORPUS_REF": _SMOKE_CORPUS_REF,
        "SESSION_ID": session_id,
        "SESSION_TYPE": session_type,
        "PERSONA": persona,
        "SPECIAL_COHORT": None,
        "SESSION_START": start,
        "SESSION_END": end,
        "USER_GOAL": "smoke 合成目标",
        "FIRST_ACTION": "打开首页",
        "FIRST_ACTION_SUCCESS": True,
        "TASK_SUCCESS": task_success,
        "TRUST_FAILURE": False,
        "USER_BLOCKED": False,
        "NOTES": "sandbox smoke",
    }


def _smoke_interaction(
    session_id: str,
    index: int,
    persona: str,
    session_type: str,
    classification: str,
    **overrides: Any,
) -> dict[str, Any]:
    row = {
        "BENCHMARK_VERSION": BENCHMARK_VERSION,
        "CORPUS_REF": _SMOKE_CORPUS_REF,
        "SESSION_ID": session_id,
        "INTERACTION_ID": f"{session_id}#T{index}",
        "INTERACTION_INDEX": index,
        "PERSONA": persona,
        "SESSION_TYPE": session_type,
        "TASK_ID": f"SMOKE-T{index}",
        "TASK_CLASS": "smoke",
        "SESSION_START": "2026-01-01T10:00:00+08:00",
        "SESSION_END": "2026-01-01T10:30:00+08:00",
        "EXPECTED_BEHAVIOR": "smoke 期望",
        "ACTUAL_BEHAVIOR": "smoke 实际",
        "QUESTION": "smoke 问题？",
        "RESPONSE_SUMMARY": "smoke 回答摘要",
        "FIRST_ACTION_SUCCESS": True,
        "TASK_SUCCESS": "success",
        "COMMISSION": False,
        "FABRICATION": False,
        "SILENT_STALE": False,
        "VERSION_CONDITION_ERROR": False,
        "EVIDENCE_ERROR": False,
        "EXPERIENCE_AUTHORITY_ERROR": False,
        "HONEST_OMISSION": False,
        "RECOVERY_EVENT": False,
        "RECOVERY_SUCCESS": False,
        "TRUST_FAILURE": False,
        "USER_BLOCKED": False,
        "INVALID": False,
        "CLASSIFICATION": classification,
        "NOTES": "sandbox smoke",
    }
    row.update(overrides)
    return row


def _expect_error(exc_list: list[bool], label: str, fn: Any, *args: Any) -> None:
    try:
        fn(*args)
        exc_list.append(False)
        print(f"  [smoke] {label}: 未按预期拒绝 !!")
    except (ValidationError, DriftError):
        exc_list.append(True)
        print(f"  [smoke] {label}: 正确拒绝 PASS")


def run_smoke(sandbox: Path) -> bool:
    """12 项机制验证；全部通过才返回 True。"""
    import shutil

    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.mkdir(parents=True)
    results: list[bool] = []
    db_path = sandbox / "data" / "database" / "knowledge.db"
    _build_sandbox_db(db_path)
    record = freeze_corpus(
        sandbox,
        db_path,
        notes="smoke sandbox corpus",
    )
    global _SMOKE_CORPUS_REF
    _SMOKE_CORPUS_REF = record["corpus_ref_id"]
    print("  [smoke] corpus freeze OK")

    s1, s2 = "SIMV2-A-20260101-001", "SIMV2-B-20260101-001"
    register_session(sandbox, _smoke_session(s1, "A", "CONTINUOUS", "partial"))
    register_session(sandbox, _smoke_session(s2, "B", "NEW", "success"))
    sessions, _ = load_rows(sandbox)
    results.append(len(sessions) == 2)
    print("  [smoke] registry append PASS" if results[-1] else "  [smoke] registry append FAIL")

    # 1. SESSION_ID 唯一
    dup_session = _smoke_session(s1, "A", "NEW", "success")
    _expect_error(results, "dup session rejected", register_session, sandbox, dup_session)

    # 6. CORPUS_REF match
    bad_ref = _smoke_session(s1, "A", "NEW", "success")
    bad_ref["CORPUS_REF"] = "NOT-FROZEN"
    _expect_error(results, "unknown corpus ref rejected", register_session, sandbox, bad_ref)

    # 10. SIMULATED / REAL_USER 分离
    zb = _smoke_session("ZB-X-20260101-001", "A", "NEW", "success")
    _expect_error(results, "real-user id rejected in SIMV2 layer", register_session, sandbox, zb)

    # 8. mandatory fields
    missing = _smoke_interaction(s1, 1, "A", "CONTINUOUS", "pass")
    del missing["EXPECTED_BEHAVIOR"]
    _expect_error(results, "missing mandatory rejected", record_interaction, sandbox, missing)

    # 9. recovery denominator：RECOVERY_SUCCESS=true 必须有预定义 RECOVERY_EVENT
    bad_recovery = _smoke_interaction(
        s1, 1, "A", "CONTINUOUS", "recovery_success", RECOVERY_EVENT=False, RECOVERY_SUCCESS=True
    )
    _expect_error(
        results,
        "recovery without predefined event rejected",
        record_interaction,
        sandbox,
        bad_recovery,
    )

    # 正常写入 6 条（含 invalid + commission + omission + recovery）
    record_interaction(sandbox, _smoke_interaction(s1, 1, "A", "CONTINUOUS", "pass"))
    record_interaction(
        sandbox,
        _smoke_interaction(
            s1, 2, "A", "CONTINUOUS", "honest_omission_recall_gap", HONEST_OMISSION=True
        ),
    )
    record_interaction(
        sandbox,
        _smoke_interaction(
            s1,
            3,
            "A",
            "CONTINUOUS",
            "commission_wrong_fact",
            COMMISSION=True,
            TRUST_FAILURE=True,
            TASK_SUCCESS="partial",
        ),
    )
    record_interaction(
        sandbox,
        _smoke_interaction(
            s1, 4, "A", "CONTINUOUS", "invalid", INVALID=True, NOTES="smoke snapshot timeout"
        ),
    )
    record_interaction(
        sandbox,
        _smoke_interaction(
            s2,
            1,
            "B",
            "NEW",
            "recovery_success",
            RECOVERY_EVENT=True,
            RECOVERY_SUCCESS=True,
        ),
    )
    record_interaction(
        sandbox,
        _smoke_interaction(s2, 2, "B", "NEW", "honest_refusal", CLASSIFICATION="honest_refusal"),
    )

    # 2. interaction 唯一
    dup = _smoke_interaction(s1, 1, "A", "CONTINUOUS", "pass")
    _expect_error(results, "dup (session,index) rejected", record_interaction, sandbox, dup)

    # commission 必须置 TRUST_FAILURE
    no_trust = _smoke_interaction(
        s2, 3, "B", "NEW", "commission_wrong_fact", COMMISSION=True, TRUST_FAILURE=False
    )
    _expect_error(
        results,
        "commission without trust_failure rejected",
        record_interaction,
        sandbox,
        no_trust,
    )

    # 5. ledger 一致性：未注册 session 的 interaction
    orphan = _smoke_interaction("SIMV2-C-20260101-001", 1, "C", "NEW", "pass")
    _expect_error(results, "orphan session rejected", record_interaction, sandbox, orphan)

    # 12. manifest SHA + 4. append-only 前缀完整性（篡改必须被发现）
    write_raw_manifest(sandbox)
    check = check_raw_manifest(sandbox)
    results.append(not check["violations"])
    sessions_path = sandbox / "sessions.jsonl"
    original_bytes = sessions_path.read_bytes()
    tampered = original_bytes.replace(b"CONTINUOUS", b"XXNTINUOUS", 1)
    sessions_path.write_bytes(tampered)
    check = check_raw_manifest(sandbox)
    tamper_detected = bool(check["violations"])
    sessions_path.write_bytes(original_bytes)
    check = check_raw_manifest(sandbox)
    results.append(tamper_detected and not check["violations"])
    outcome = "PASS" if results[-1] else "FAIL"
    print(f"  [smoke] append-only tamper detection {outcome}")

    # 全量校验
    report = validate_all(sandbox, db_path)
    results.append(report["pass"])
    if report["pass"]:
        print("  [smoke] validate_all PASS")
    else:
        print(f"  [smoke] validate_all FAIL: {report['errors'][:3]}")

    # 7/8/9. metrics：invalid 剔除 + 分母 + recovery 预定义分母
    metrics = compute_metrics(sandbox)
    expected = {
        "unique_sessions": 2,
        "valid_interactions": 5,
        "invalid_interactions": 1,
        "commission_count": 1,
        "commission_rate": 0.2,
        "honest_omission_count": 1,
        "honest_omission_rate": 0.2,
        "fabrication_count": 0,
        "silent_stale_count": 0,
        "recovery_event_denominator": 1,
        "recovery_success_count": 1,
        "recovery_success_rate": 1.0,
        "persona_counts": {"A": 1, "B": 1},
        "session_type_counts": {"CONTINUOUS": 1, "NEW": 1},
    }
    actual = {k: metrics[k] for k in expected}
    metrics_ok = actual == expected
    results.append(metrics_ok)
    if metrics_ok:
        print("  [smoke] metrics hand-check PASS")
    else:
        print(f"  [smoke] metrics mismatch: {actual}")

    # 11. leakage guard：植入泄漏必须检出，干净 fixture 必须通过
    leak_prompt = sandbox / "fake_prompts"
    leak_prompt.mkdir()
    (leak_prompt / "fake_prompt.py").write_text(
        'PROMPT = "根据资料回答。示例设备 SMOKE-999 力矩 25 Nm。"', encoding="utf-8"
    )
    leak_fixture = sandbox / "leak_fixture.json"
    leak_payload = {"question": "测试", "device": "SMOKE-999", "answer": "25 Nm"}
    leak_fixture.write_text(json.dumps(leak_payload, ensure_ascii=False), encoding="utf-8")
    leak_report = check_leakage([leak_fixture], prompt_dir=leak_prompt)
    clean_fixture = sandbox / "clean_fixture.json"
    clean_fixture.write_text(
        json.dumps({"question": "完全无关的问题", "answer": "73 °C"}, ensure_ascii=False),
        encoding="utf-8",
    )
    clean_report = check_leakage([clean_fixture], prompt_dir=leak_prompt)
    leak_ok = leak_report["findings"] and clean_report["pass"]
    results.append(bool(leak_ok))
    print("  [smoke] leakage guard PASS" if leak_ok else "  [smoke] leakage guard FAIL")

    # 3. mandatory fields 全量校验已含；12 项汇总
    all_pass = all(results)
    print(f"  [smoke] {sum(results)}/{len(results)} checks passed")
    return all_pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_row_json(arg: str) -> dict[str, Any]:
    if arg.startswith("@"):
        return json.loads(Path(arg[1:]).read_text(encoding="utf-8"))
    return json.loads(arg)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HBENCH_V2 采集与完整性工具箱")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("freeze-corpus", help="清点 staging 并追加冻结 CORPUS_REF")
    p.add_argument("--art-dir", type=Path, default=DEFAULT_ART_DIR)
    p.add_argument("--db", type=Path, default=DEFAULT_STAGING_DB)
    p.add_argument("--git-head", default="")
    p.add_argument("--notes", default="")

    p = sub.add_parser("register-session", help="校验并追加 session 行")
    p.add_argument("--art-dir", type=Path, default=DEFAULT_ART_DIR)
    p.add_argument("--json", required=True, help="JSON 字符串或 @file")
    p.add_argument("--check-drift", action="store_true")

    p = sub.add_parser("record-interaction", help="校验并追加 interaction 行")
    p.add_argument("--art-dir", type=Path, default=DEFAULT_ART_DIR)
    p.add_argument("--json", required=True)
    p.add_argument("--check-drift", action="store_true")

    p = sub.add_parser("validate", help="全量校验（唯一性/字段/漂移/manifest）")
    p.add_argument("--art-dir", type=Path, default=DEFAULT_ART_DIR)
    p.add_argument("--db", type=Path, default=None)
    p.add_argument("--allow-open-sessions", action="store_true")

    p = sub.add_parser("metrics", help="由 raw 重新生成 metrics.json")
    p.add_argument("--art-dir", type=Path, default=DEFAULT_ART_DIR)

    p = sub.add_parser("manifest", help="写 raw SHA-256 前缀 manifest")
    p.add_argument("--art-dir", type=Path, default=DEFAULT_ART_DIR)

    p = sub.add_parser("leakage-check", help="评测泄漏检查")
    p.add_argument("fixtures", nargs="+", type=Path)

    p = sub.add_parser("update-state", help="更新 campaign_state.json")
    p.add_argument("--art-dir", type=Path, default=DEFAULT_ART_DIR)
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--set-json", action="append", default=[], metavar="KEY=JSON")

    p = sub.add_parser("smoke", help="基础设施 smoke（sandbox 合成数据）")
    p.add_argument("--sandbox", type=Path, required=True)

    args = parser.parse_args(argv)

    if args.command == "freeze-corpus":
        record = freeze_corpus(args.art_dir, args.db, git_head=args.git_head, notes=args.notes)
        summary = {k: v for k, v in record.items() if k != "documents"}
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        return 0
    if args.command == "register-session":
        row = _read_row_json(args.json)
        register_session(args.art_dir, row, args.db if args.check_drift else None)
        print(f"OK session {row['SESSION_ID']}")
        return 0
    if args.command == "record-interaction":
        row = _read_row_json(args.json)
        record_interaction(args.art_dir, row, args.db if args.check_drift else None)
        print(f"OK interaction {row['INTERACTION_ID']}")
        return 0
    if args.command == "validate":
        report = validate_all(args.art_dir, args.db, allow_open_sessions=args.allow_open_sessions)
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0 if report["pass"] else 1
    if args.command == "metrics":
        print(json.dumps(write_metrics(args.art_dir), ensure_ascii=False, indent=1))
        return 0
    if args.command == "manifest":
        write_raw_manifest(args.art_dir)
        print("OK manifest")
        return 0
    if args.command == "leakage-check":
        result = check_leakage(args.fixtures)
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0 if result["pass"] else 1
    if args.command == "update-state":
        updates: dict[str, Any] = {}
        for item in args.set:
            key, _, value = item.partition("=")
            updates[key] = value
        for item in args.set_json:
            key, _, value = item.partition("=")
            updates[key] = json.loads(value)
        state = update_campaign_state(args.art_dir, updates)
        print(json.dumps(state, ensure_ascii=False, indent=1))
        return 0
    if args.command == "smoke":
        passed = run_smoke(args.sandbox)
        print("SMOKE PASS" if passed else "SMOKE FAIL")
        return 0 if passed else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
