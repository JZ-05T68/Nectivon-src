"""Zero-Batch 真人测试匿名汇总脚本（离线、独立于产品运行时）。

依据 docs/zero-batch/EKB_v0.8.4_ZERO_BATCH_METRICS_V1.md 计算第零批真实用户
指标：counts / rates / 按 user track 分组 / incident 摘要。

边界（Campaign §44/§56）：
- 只读 `--data-dir` 下的匿名 CSV 记录（sessions/observations/turns/incidents/feedback）；
- 不连接、不修改任何数据库（正式库 / staging 均不触碰）；
- 不被 app.py / pages/ / src/ 导入，不进入 8501 运行时清单；
- 数据层为 REAL_USER，与 artifacts/human_simulation_v2 的 SIMULATED 层禁止合并。

用法：
    python scripts/zero_batch/summarize_zero_batch.py \
        --data-dir artifacts/real_user_zero_batch \
        --out artifacts/real_user_zero_batch/metrics
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FORMAL_ENVIRONMENT = "8501_formal"
REHEARSAL_ENVIRONMENT = "8511_rehearsal"
TRACKS = ("A", "B", "C", "D", "E")
WAVES = ("0", "1", "2", "3")  # 0 = 内部演练（Wave 0）
SESSION_TYPES = ("NEW", "CONTINUOUS", "REFRESH", "RESTART", "CROSS_DAY")
TASK_KINDS = ("GUIDED", "USER_OWNED")
TASK_SUCCESS_VALUES = ("success", "partial", "incomplete", "failed", "not_run")
TRI_VALUES = ("true", "false", "na")
BOOL_VALUES = ("true", "false")
UNDERSTOOD_VALUES = ("yes", "partial", "no", "na")
TASK_ERROR_TYPES = (
    "NONE",
    "COMMISSION",
    "FABRICATION",
    "SILENT_STALE",
    "VERSION_CONDITION_OBJECT",
    "EVIDENCE",
    "EXPERIENCE_AUTHORITY",
    "HONEST_OMISSION",
    "HONEST_BOUNDARY",
    "USER_BLOCKED",
    "OTHER",
)
SEVERITIES = ("NONE", "P0", "P1", "P2", "P3")
CLASSIFICATIONS = (
    "pass",
    "commission_wrong_fact",
    "commission_wrong_version",
    "commission_wrong_object_binding",
    "commission_wrong_condition",
    "commission_wrong_evidence",
    "fabrication",
    "silent_stale",
    "honest_omission_recall_gap",
    "honest_boundary",
    "honest_refusal",
    "honest_meta",
    "clarification",
    "recovery_success",
    "invalid",
    "other",
)
COMMISSION_CLASSIFICATIONS = (
    "commission_wrong_fact",
    "commission_wrong_version",
    "commission_wrong_object_binding",
    "commission_wrong_condition",
    "commission_wrong_evidence",
)
RETURN_INTENTS = ("yes", "unsure", "no")
MENTAL_MODELS = ("CORRECT", "PARTIAL", "INCORRECT", "NOT_ASKED")
TEACHBACK_VALUES = ("correct", "partial", "wrong", "na")
VALUE_SIGNALS = (
    "VS_MORE_MATERIAL",
    "VS_RETURN_QUERY",
    "VS_SAVE_EXPERIENCE",
    "VS_PAST_PAIN",
    "VS_OTHER",
)

TURN_FLAG_FIELDS = (
    "commission",
    "fabrication",
    "silent_stale",
    "version_condition_object_error",
    "evidence_error",
    "experience_authority_error",
    "honest_omission",
    "out_of_scope",
    "trust_failure",
    "user_blocked",
    "recovery_event",
    "recovery_success",
)

SESSION_COLUMNS = (
    "session_id",
    "user_pseudonym",
    "user_track",
    "wave",
    "session_date",
    "start_time",
    "end_time",
    "environment",
    "app_version",
    "session_type",
    "first_task_id",
    "first_action_success",
    "user_blocked",
    "trust_failure",
    "session_completion",
    "notes",
)
OBSERVATION_COLUMNS = (
    "session_id",
    "task_id",
    "task_kind",
    "user_track",
    "task_success",
    "first_action",
    "first_action_success",
    "user_blocked",
    "query",
    "expected",
    "actual",
    "source_opened",
    "source_correct",
    "user_understood_source",
    "error_type",
    "recovery_event",
    "recovery_success",
    "user_judgment",
    "user_comment",
    "observer_note",
    "severity",
)
TURN_COLUMNS = (
    "session_id",
    "task_id",
    "turn_index",
    "query",
    "answer_summary",
    "citations_given",
    "classification",
    *TURN_FLAG_FIELDS,
    "invalid",
    "notes",
)
INCIDENT_COLUMNS = (
    "incident_id",
    "session_id",
    "task_id",
    "severity",
    "error_type",
    "summary",
    "evidence_path",
    "reported_to_owner",
    "follow_up",
)
FEEDBACK_COLUMNS = (
    "user_pseudonym",
    "user_track",
    "session_id",
    "q1_want_to_solve",
    "q2_stuck_step",
    "q3_untrusted_answer",
    "q4_miss_but_exists",
    "q5_most_valuable",
    "q6_least_useful",
    "q7_return_intent",
    "q7_reason",
    "q8_next_request",
    "free_text",
    "value_signals",
    "top_value_feature",
    "mental_model",
    "teachback_done",
    "teachback_q1",
    "teachback_q2",
    "teachback_q3",
    "teachback_q4",
    "teachback_q5",
    "feature_requests",
)


class RecordError(Exception):
    """记录文件格式或取值非法（fail-fast，不产出指标）。"""


def load_csv(path: Path, required_columns: tuple[str, ...]) -> list[dict[str, str]]:
    """读取匿名记录 CSV；缺列 / 占位残留即报错。文件不存在返回空列表。"""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return []
        header = [name.strip() for name in reader.fieldnames]
        missing = [name for name in required_columns if name not in header]
        if missing:
            raise RecordError(f"{path.name}: 缺少必需列 {missing}（模板见 templates/zero_batch/）")
        rows: list[dict[str, str]] = []
        for line_no, raw in enumerate(reader, start=2):
            row = {(key or "").strip(): (value or "").strip() for key, value in raw.items()}
            if not any(row.values()):
                continue
            placeholder = [k for k, v in row.items() if ("{" in v or "|" in v)]
            if placeholder:
                raise RecordError(
                    f"{path.name} 第 {line_no} 行：检测到模板占位符（列 {placeholder[:3]}），"
                    "复制模板时须删除取值说明占位行"
                )
            row["_line"] = str(line_no)
            rows.append(row)
        return rows


def _require(row: dict[str, str], field: str, label: str) -> str:
    value = row.get(field, "")
    if not value:
        raise RecordError(f"{label}: 必填字段 {field} 为空（第 {row.get('_line', '?')} 行）")
    return value


def _require_enum(row: dict[str, str], field: str, allowed: tuple[str, ...], label: str) -> str:
    value = _require(row, field, label)
    if value not in allowed:
        raise RecordError(
            f"{label}: 字段 {field} 取值 {value!r} 非法，允许 {list(allowed)}"
            f"（第 {row.get('_line', '?')} 行）"
        )
    return value


def _require_tri(row: dict[str, str], field: str, label: str) -> str:
    return _require_enum(row, field, TRI_VALUES, label)


def _require_bool(row: dict[str, str], field: str, label: str) -> str:
    return _require_enum(row, field, BOOL_VALUES, label)


def validate_sessions(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    for row in rows:
        label = f"sessions 第 {row['_line']} 行"
        session_id = _require(row, "session_id", label)
        if not session_id.startswith("ZB-"):
            raise RecordError(f"{label}: session_id 必须以 ZB- 开头（REAL_USER 层硬约束）")
        if session_id in seen:
            raise RecordError(f"{label}: session_id 重复：{session_id}")
        seen.add(session_id)
        _require(row, "user_pseudonym", label)
        _require_enum(row, "user_track", TRACKS, label)
        _require_enum(row, "wave", WAVES, label)
        _require_enum(row, "environment", (FORMAL_ENVIRONMENT, REHEARSAL_ENVIRONMENT), label)
        _require_enum(row, "session_type", SESSION_TYPES, label)
        _require_tri(row, "first_action_success", label)
        _require_bool(row, "user_blocked", label)
        _require_bool(row, "trust_failure", label)
        _require_enum(row, "session_completion", ("completed", "abandoned"), label)
    return rows


def validate_observations(
    rows: list[dict[str, str]], session_ids: set[str]
) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    for row in rows:
        label = f"observations 第 {row['_line']} 行"
        session_id = _require(row, "session_id", label)
        task_id = _require(row, "task_id", label)
        if session_id not in session_ids:
            raise RecordError(f"{label}: session_id {session_id} 未在 sessions.csv 登记")
        key = (session_id, task_id)
        if key in seen:
            raise RecordError(f"{label}: (session_id, task_id) 重复：{key}")
        seen.add(key)
        _require_enum(row, "task_kind", TASK_KINDS, label)
        _require_enum(row, "task_success", TASK_SUCCESS_VALUES, label)
        _require_tri(row, "first_action_success", label)
        _require_bool(row, "user_blocked", label)
        _require_enum(row, "source_opened", TRI_VALUES, label)
        _require_enum(row, "source_correct", TRI_VALUES, label)
        _require_enum(row, "user_understood_source", UNDERSTOOD_VALUES, label)
        _require_enum(row, "error_type", TASK_ERROR_TYPES, label)
        _require_bool(row, "recovery_event", label)
        _require_bool(row, "recovery_success", label)
        _require_enum(row, "severity", SEVERITIES, label)
    return rows


def validate_turns(
    rows: list[dict[str, str]], session_ids: set[str], task_keys: set[tuple[str, str]]
) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str]] = set()
    indexes_by_task: dict[tuple[str, str], list[int]] = {}
    for row in rows:
        label = f"turns 第 {row['_line']} 行"
        session_id = _require(row, "session_id", label)
        task_id = _require(row, "task_id", label)
        turn_index = _require(row, "turn_index", label)
        if session_id not in session_ids:
            raise RecordError(f"{label}: session_id {session_id} 未在 sessions.csv 登记")
        if (session_id, task_id) not in task_keys:
            raise RecordError(
                f"{label}: (session_id, task_id) {(session_id, task_id)} 未在 observations.csv 登记"
            )
        if not turn_index.isdigit() or int(turn_index) < 1:
            raise RecordError(f"{label}: turn_index 必须是 >=1 的整数")
        key = (session_id, task_id, turn_index)
        if key in seen:
            raise RecordError(f"{label}: (session_id, task_id, turn_index) 重复：{key}")
        seen.add(key)
        indexes_by_task.setdefault((session_id, task_id), []).append(int(turn_index))
        _require_enum(row, "classification", CLASSIFICATIONS, label)
        _require_bool(row, "citations_given", label)
        for flag in TURN_FLAG_FIELDS:
            _require_bool(row, flag, label)
        _require_bool(row, "invalid", label)
        if row["invalid"] == "true" and row["classification"] != "invalid":
            raise RecordError(f"{label}: invalid=true 时 classification 必须为 invalid")
        if row["invalid"] == "false" and row["classification"] == "invalid":
            raise RecordError(f"{label}: classification=invalid 时 invalid 必须为 true")
        is_commission = row["classification"] in COMMISSION_CLASSIFICATIONS
        if is_commission != (row["commission"] == "true"):
            raise RecordError(
                f"{label}: commission 旗标与 classification={row['classification']} 不一致"
            )
        if (row["classification"] == "fabrication") != (row["fabrication"] == "true"):
            raise RecordError(f"{label}: fabrication 旗标与 classification 不一致")
        if (row["classification"] == "honest_omission_recall_gap") != (
            row["honest_omission"] == "true"
        ):
            raise RecordError(f"{label}: honest_omission 旗标与 classification 不一致")
    for task_key, indexes in indexes_by_task.items():
        if sorted(indexes) != list(range(1, len(indexes) + 1)):
            raise RecordError(
                f"turns: {task_key} 的 turn_index 不连续：{sorted(indexes)}（补跑用新 task 记录）"
            )
    return rows


def validate_incidents(
    rows: list[dict[str, str]], session_ids: set[str]
) -> list[dict[str, str]]:
    seen: set[str] = set()
    for row in rows:
        label = f"incidents 第 {row['_line']} 行"
        incident_id = _require(row, "incident_id", label)
        if not incident_id.startswith("ZBINC-"):
            raise RecordError(f"{label}: incident_id 必须以 ZBINC- 开头（独立 namespace）")
        if incident_id in seen:
            raise RecordError(f"{label}: incident_id 重复：{incident_id}")
        seen.add(incident_id)
        if _require(row, "session_id", label) not in session_ids:
            raise RecordError(f"{label}: session_id 未在 sessions.csv 登记")
        severity = _require_enum(row, "severity", ("P0", "P1", "P2", "P3"), label)
        if severity in ("P0", "P1") and not row.get("evidence_path"):
            raise RecordError(f"{label}: P0/P1 事件必须提供 evidence_path（证据包文件）")
    return rows


def validate_feedback(
    rows: list[dict[str, str]], session_ids: set[str]
) -> list[dict[str, str]]:
    seen: set[str] = set()
    for row in rows:
        label = f"feedback 第 {row['_line']} 行"
        user = _require(row, "user_pseudonym", label)
        if user in seen:
            raise RecordError(f"{label}: user_pseudonym 重复（每用户一行）：{user}")
        seen.add(user)
        _require_enum(row, "user_track", TRACKS, label)
        if _require(row, "session_id", label) not in session_ids:
            raise RecordError(f"{label}: session_id 未在 sessions.csv 登记")
        _require_enum(row, "q7_return_intent", RETURN_INTENTS, label)
        _require_enum(row, "mental_model", MENTAL_MODELS, label)
        _require_enum(row, "teachback_done", BOOL_VALUES, label)
        teachback_fields = (
            "teachback_q1",
            "teachback_q2",
            "teachback_q3",
            "teachback_q4",
            "teachback_q5",
        )
        for field in teachback_fields:
            _require_enum(row, field, TEACHBACK_VALUES, label)
        for signal in filter(None, (s.strip() for s in row.get("value_signals", "").split(";"))):
            if signal not in VALUE_SIGNALS:
                raise RecordError(f"{label}: value_signals 含未定义代码 {signal!r}")
    return rows


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(numerator / denominator, 4) if denominator else None,
    }


def compute_metrics(
    sessions: list[dict[str, str]],
    observations: list[dict[str, str]],
    turns: list[dict[str, str]],
    incidents: list[dict[str, str]],
    feedback: list[dict[str, str]],
) -> dict[str, Any]:
    """按 METRICS_V1 计算全部指标；formal 环境之外（演练）行剔除并披露。"""
    formal_sessions = [s for s in sessions if s["environment"] == FORMAL_ENVIRONMENT]
    rehearsal_count = len(sessions) - len(formal_sessions)
    formal_ids = {s["session_id"] for s in formal_sessions}
    formal_observations = [o for o in observations if o["session_id"] in formal_ids]
    formal_turns = [t for t in turns if t["session_id"] in formal_ids]
    formal_incidents = [i for i in incidents if i["session_id"] in formal_ids]

    valid_turns = [t for t in formal_turns if t["invalid"] == "false"]
    invalid_turns = [t for t in formal_turns if t["invalid"] == "true"]
    valid_tasks = [o for o in formal_observations if o["task_success"] != "not_run"]
    incomplete_tasks = [o for o in valid_tasks if o["task_success"] == "incomplete"]
    primary_tasks = [o for o in valid_tasks if o["task_success"] != "incomplete"]

    def turn_flag(flag: str) -> int:
        return sum(1 for t in valid_turns if t[flag] == "true")

    task_success_dist = Counter(o["task_success"] for o in valid_tasks)
    by_track_tasks: dict[str, Counter[str]] = {}
    for o in valid_tasks:
        by_track_tasks.setdefault(o["user_track"], Counter())[o["task_success"]] += 1

    citation_denominator = [o for o in valid_tasks if o["source_opened"] != "na"]
    understood_denominator = [o for o in valid_tasks if o["user_understood_source"] != "na"]
    first_action_denominator = [o for o in valid_tasks if o["first_action_success"] != "na"]

    first_task_by_session: dict[str, dict[str, str]] = {}
    for o in formal_observations:
        current = first_task_by_session.get(o["session_id"])
        if current is None or o["task_id"] < current["task_id"]:
            first_task_by_session[o["session_id"]] = o
    first_action_sessions = [
        o for o in first_task_by_session.values() if o["first_action_success"] != "na"
    ]

    user_owned = [o for o in primary_tasks if o["task_kind"] == "USER_OWNED"]
    guided = [o for o in primary_tasks if o["task_kind"] == "GUIDED"]

    def task_success_rate(rows: list[dict[str, str]]) -> dict[str, Any]:
        return _rate(sum(1 for o in rows if o["task_success"] == "success"), len(rows))

    severity_counts = Counter(i["severity"] for i in formal_incidents)
    feedback_users = {f["user_pseudonym"] for f in feedback}
    session_users = {s["user_pseudonym"] for s in formal_sessions}
    feedback_dist = Counter(f["q7_return_intent"] for f in feedback)
    mental_model_dist = Counter(f["mental_model"] for f in feedback)
    top_value_dist = Counter(
        f["top_value_feature"] for f in feedback if f["top_value_feature"]
    )
    value_signal_dist: Counter[str] = Counter()
    for f in feedback:
        for signal in filter(None, (s.strip() for s in f["value_signals"].split(";"))):
            value_signal_dist[signal] += 1
    teachback_done = [f for f in feedback if f["teachback_done"] == "true"]

    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "data_layer": "REAL_USER",
        "note": "全部为真实用户数据；与 SIMULATED 层禁止合并（HBV2 spec §15）",
        "metrics_version": "ZERO_BATCH_METRICS_V1",
        "exclusion_note": "演练环境（8511_rehearsal）行不计入任何分母",
        "scale": {
            "users": len(feedback_users or session_users),
            "sessions": len(formal_sessions),
            "rehearsal_sessions_excluded": rehearsal_count,
            "valid_tasks": len(valid_tasks),
            "tasks_excluded_incomplete": len(incomplete_tasks),
            "valid_turns": len(valid_turns),
            "invalid_turns": len(invalid_turns),
        },
        "turn_level": {
            "commission": _rate(turn_flag("commission"), len(valid_turns)),
            "fabrication": _rate(turn_flag("fabrication"), len(valid_turns)),
            "silent_stale": _rate(turn_flag("silent_stale"), len(valid_turns)),
            "version_condition_object_error": _rate(
                turn_flag("version_condition_object_error"), len(valid_turns)
            ),
            "evidence_error": _rate(turn_flag("evidence_error"), len(valid_turns)),
            "experience_authority_error": _rate(
                turn_flag("experience_authority_error"), len(valid_turns)
            ),
            "honest_omission": _rate(turn_flag("honest_omission"), len(valid_turns)),
            "honest_omission_out_of_scope": _rate(
                turn_flag("out_of_scope"), len(valid_turns)
            ),
            "trust_failure": _rate(turn_flag("trust_failure"), len(valid_turns)),
        },
        "task_level": {
            "task_success_distribution": dict(sorted(task_success_dist.items())),
            "task_success_rate": task_success_rate(primary_tasks),
            "guided_task_success_rate": task_success_rate(guided),
            "user_owned_task_success_rate": task_success_rate(user_owned),
            "task_success_by_track": {
                track: dict(sorted(counter.items()))
                for track, counter in sorted(by_track_tasks.items())
            },
            "user_blocked": _rate(
                sum(1 for o in valid_tasks if o["user_blocked"] == "true"), len(valid_tasks)
            ),
            "citation_open": _rate(
                sum(1 for o in citation_denominator if o["source_opened"] == "true"),
                len(citation_denominator),
            ),
            "citation_understanding": _rate(
                sum(1 for o in understood_denominator if o["user_understood_source"] == "yes"),
                len(understood_denominator),
            ),
            "first_action_success": _rate(
                sum(1 for o in first_action_denominator if o["first_action_success"] == "true"),
                len(first_action_denominator),
            ),
            "recovery_success": _rate(
                sum(1 for o in valid_tasks if o["recovery_success"] == "true"),
                sum(1 for o in valid_tasks if o["recovery_event"] == "true"),
            ),
        },
        "session_level": {
            "first_action_success": _rate(
                sum(1 for o in first_action_sessions if o["first_action_success"] == "true"),
                len(first_action_sessions),
            ),
            "user_blocked": _rate(
                sum(1 for s in formal_sessions if s["user_blocked"] == "true"),
                len(formal_sessions),
            ),
            "trust_failure": _rate(
                sum(1 for s in formal_sessions if s["trust_failure"] == "true"),
                len(formal_sessions),
            ),
            "completion": _rate(
                sum(1 for s in formal_sessions if s["session_completion"] == "completed"),
                len(formal_sessions),
            ),
            "sessions_by_track": dict(
                sorted(Counter(s["user_track"] for s in formal_sessions).items())
            ),
            "sessions_by_wave": dict(
                sorted(Counter(s["wave"] for s in formal_sessions).items())
            ),
        },
        "incidents": {
            "counts_by_severity": dict(sorted(severity_counts.items())),
            "per_session_rate": {
                severity: _rate(count, len(formal_sessions))
                for severity, count in sorted(severity_counts.items())
            },
            "summary": [
                {
                    "incident_id": i["incident_id"],
                    "severity": i["severity"],
                    "error_type": i["error_type"],
                    "summary": i["summary"],
                    "evidence_path": i["evidence_path"],
                }
                for i in formal_incidents
            ],
        },
        "user_level": {
            "return_intent": dict(sorted(feedback_dist.items())),
            "mental_model": dict(sorted(mental_model_dist.items())),
            "top_value_feature": dict(sorted(top_value_dist.items())),
            "value_signals": dict(sorted(value_signal_dist.items())),
            "teachback_coverage": _rate(len(teachback_done), len(feedback)),
        },
    }


def render_markdown(metrics: dict[str, Any]) -> str:
    """把指标渲染成简明 Markdown 摘要（计数优先，比率并列分子/分母）。"""

    def fmt(rate_block: dict[str, Any]) -> str:
        num, den, rate = rate_block["numerator"], rate_block["denominator"], rate_block["rate"]
        return f"{num}/{den} = {rate}" if rate is not None else f"{num}/{den} = n/a"

    scale = metrics["scale"]
    lines = [
        "# Zero-Batch REAL_USER 指标摘要",
        "",
        f"生成时间（UTC）：{metrics['generated_at_utc']}",
        "",
        f"- Sessions（formal）：{scale['sessions']}；"
        f"演练剔除：{scale['rehearsal_sessions_excluded']}",
        f"- Users：{scale['users']}；Valid tasks：{scale['valid_tasks']}"
        f"（incomplete 排除 {scale['tasks_excluded_incomplete']}）；"
        f"Valid turns：{scale['valid_turns']}（invalid 剔除 {scale['invalid_turns']}）",
        "",
        "## Turn 级可靠性",
        "",
        "| 指标 | 分子/分母 = 比率 |",
        "|---|---|",
    ]
    for name, block in metrics["turn_level"].items():
        lines.append(f"| {name} | {fmt(block)} |")
    lines += ["", "## Task 级", ""]
    lines.append(f"- Task Success（primary）：{fmt(metrics['task_level']['task_success_rate'])}")
    lines.append(f"- Guided：{fmt(metrics['task_level']['guided_task_success_rate'])}")
    lines.append(f"- User-Owned：{fmt(metrics['task_level']['user_owned_task_success_rate'])}")
    lines.append(f"- 分布：{metrics['task_level']['task_success_distribution']}")
    lines.append(f"- 按 Track：{metrics['task_level']['task_success_by_track']}")
    lines.append(f"- User Blocked（task）：{fmt(metrics['task_level']['user_blocked'])}")
    lines.append(f"- Citation Open：{fmt(metrics['task_level']['citation_open'])}")
    lines.append(
        f"- Citation Understanding：{fmt(metrics['task_level']['citation_understanding'])}"
    )
    lines.append(f"- First Action（task）：{fmt(metrics['task_level']['first_action_success'])}")
    lines.append(f"- Recovery Success：{fmt(metrics['task_level']['recovery_success'])}")
    lines += ["", "## Session 级", ""]
    for name in ("first_action_success", "user_blocked", "trust_failure", "completion"):
        lines.append(f"- {name}：{fmt(metrics['session_level'][name])}")
    lines.append(f"- 按 Track：{metrics['session_level']['sessions_by_track']}")
    lines.append(f"- 按 Wave：{metrics['session_level']['sessions_by_wave']}")
    lines += ["", "## Incident", ""]
    lines.append(f"- 计数：{metrics['incidents']['counts_by_severity'] or '（无）'}")
    for severity, block in metrics["incidents"]["per_session_rate"].items():
        lines.append(f"- {severity} per session：{fmt(block)}")
    lines += ["", "## User 级价值与信任", ""]
    lines.append(f"- Return Intent：{metrics['user_level']['return_intent'] or '（无）'}")
    lines.append(f"- Mental Model：{metrics['user_level']['mental_model'] or '（无）'}")
    lines.append(f"- TOP_VALUE_FEATURE：{metrics['user_level']['top_value_feature'] or '（无）'}")
    lines.append(f"- VALUE_SIGNALS：{metrics['user_level']['value_signals'] or '（无）'}")
    lines.append(f"- Teach-back 覆盖：{fmt(metrics['user_level']['teachback_coverage'])}")
    lines.append("")
    return "\n".join(lines)


def summarize(data_dir: Path, out_dir: Path) -> dict[str, Any]:
    """读取、校验并汇总；任何记录错误都 fail-fast（不产出部分指标）。"""
    sessions = validate_sessions(load_csv(data_dir / "sessions.csv", SESSION_COLUMNS))
    session_ids = {s["session_id"] for s in sessions}
    observations = validate_observations(
        load_csv(data_dir / "observations.csv", OBSERVATION_COLUMNS), session_ids
    )
    task_keys = {(o["session_id"], o["task_id"]) for o in observations}
    turns = validate_turns(load_csv(data_dir / "turns.csv", TURN_COLUMNS), session_ids, task_keys)
    incidents = validate_incidents(
        load_csv(data_dir / "incidents.csv", INCIDENT_COLUMNS), session_ids
    )
    feedback = validate_feedback(load_csv(data_dir / "feedback.csv", FEEDBACK_COLUMNS), session_ids)
    metrics = compute_metrics(sessions, observations, turns, incidents, feedback)
    if not sessions:
        metrics["no_data"] = True
        metrics["note"] += "；data-dir 无记录，输出为空表结构"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "summary.md").write_text(render_markdown(metrics), encoding="utf-8")
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zero-Batch 真人测试匿名汇总（离线）")
    parser.add_argument("--data-dir", type=Path, required=True, help="匿名记录 CSV 目录")
    parser.add_argument(
        "--out", type=Path, default=None, help="输出目录（默认 <data-dir>/metrics）"
    )
    args = parser.parse_args(argv)
    try:
        metrics = summarize(args.data_dir, args.out or (args.data_dir / "metrics"))
    except RecordError as exc:
        print(f"[汇总失败] {exc}", file=sys.stderr)
        return 1
    print(
        f"OK：sessions={metrics['scale']['sessions']} "
        f"tasks={metrics['scale']['valid_tasks']} turns={metrics['scale']['valid_turns']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
