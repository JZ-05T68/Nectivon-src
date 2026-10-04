# ruff: noqa: E501
"""HBENCH_V2 Night 采集驱动：open-session / record / close-session。

约定（对齐 spec §3.2 与 prompt §8）：
- interaction 行在每个交互完成后立即 append（实时落盘）；
- session 行在 session 闭合时 append（携带真实 SESSION_END 与最终判定）；
- 进行中的 session 记录在 open_sessions.json（工作状态文件，可重写，不是 raw）；
- SESSION_ID 全局唯一性由 sessions.jsonl（closed）+ open_sessions.json（in-flight）
  双重保证；CORPUS_REF 自动取当前冻结值。

用法示例：
  python scripts/hbv2_record.py open-session --id SIMV2-A-20260908-001 --persona A \
      --type NEW --goal "..." --first-action "..." [--special-cohort FAIL024] \
      [--first-action-success true|false]
  python scripts/hbv2_record.py record --id SIMV2-A-20260908-001 --task-id A1-T2 \
      --task-class product_meta --question "..." --expected "..." --actual "..." \
      --classification pass [--response-summary "..."] [--flags COMMISSION=true ...] \
      [--evidence "doc:页"] [--notes "..."]
  python scripts/hbv2_record.py close-session --id SIMV2-A-20260908-001 \
      --task-success success [--trust-failure] [--user-blocked] [--notes "..."]

boolean flags（可选，默认 false）：COMMISSION FABRICATION SILENT_STALE
VERSION_CONDITION_ERROR EVIDENCE_ERROR EXPERIENCE_AUTHORITY_ERROR
HONEST_OMISSION RECOVERY_EVENT RECOVERY_SUCCESS TRUST_FAILURE USER_BLOCKED INVALID
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from hbv2_toolkit import (  # noqa: E402
    BENCHMARK_VERSION,
    append_jsonl_row,
    load_corpus_refs,
    load_jsonl,
    record_interaction,
    validate_interaction_row,
    validate_session_row,
)

ART_DIR = PROJECT_ROOT / "artifacts" / "human_simulation_v2"
TZ = timezone(timedelta(hours=8))

FLAG_NAMES = [
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

CLASSIFICATION_FAMILY = {
    "pass": {},
    "commission_wrong_fact": {"COMMISSION": True, "TRUST_FAILURE": True},
    "commission_wrong_negative_claim": {"COMMISSION": True, "TRUST_FAILURE": True},
    "commission_wrong_condition_attribution": {"COMMISSION": True, "TRUST_FAILURE": True},
    "commission_wrong_version": {"COMMISSION": True, "TRUST_FAILURE": True},
    "commission_wrong_evidence": {"COMMISSION": True, "TRUST_FAILURE": True},
    "commission_wrong_object_binding": {"COMMISSION": True, "TRUST_FAILURE": True},
    "fabrication": {"FABRICATION": True, "TRUST_FAILURE": True},
    "silent_stale": {"SILENT_STALE": True},
    "honest_omission_recall_gap": {"HONEST_OMISSION": True},
    "honest_omission_reference_cut": {"HONEST_OMISSION": True},
    "honest_omission_anchor_shadow": {"HONEST_OMISSION": True},
    "honest_omission_blur": {"HONEST_OMISSION": True},
    "honest_omission_out_of_scope": {},
    "honest_boundary": {},
    "honest_refusal": {},
    "honest_meta": {},
    "user_blocked": {"USER_BLOCKED": True},
    "recovery_success": {"RECOVERY_EVENT": True, "RECOVERY_SUCCESS": True},
    "invalid": {"INVALID": True},
}


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _load_open() -> dict[str, Any]:
    path = ART_DIR / "open_sessions.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"sessions": {}}


def _save_open(state: dict[str, Any]) -> None:
    path = ART_DIR / "open_sessions.json"
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _existing_session_ids() -> set[str]:
    rows = load_jsonl(ART_DIR / "sessions.jsonl")
    return {r["SESSION_ID"] for r in rows}


def _str(args: argparse.Namespace, name: str) -> str:
    value = getattr(args, name)
    return value if value is not None else ""


def cmd_open_session(args: argparse.Namespace) -> int:
    state = _load_open()
    sid = args.id
    if sid in state["sessions"] or sid in _existing_session_ids():
        print(f"REJECTED: SESSION_ID 已存在：{sid}", file=sys.stderr)
        return 1
    _, refs = load_corpus_refs(ART_DIR)
    corpus_ref = args.corpus_ref or sorted(refs)[-1]
    start = _now()
    preview: dict[str, Any] = {
        "BENCHMARK_VERSION": BENCHMARK_VERSION,
        "CORPUS_REF": corpus_ref,
        "SESSION_ID": sid,
        "SESSION_TYPE": args.type,
        "PERSONA": args.persona,
        "SPECIAL_COHORT": args.special_cohort,
        "SESSION_START": start,
        "SESSION_END": start,  # 闭合时以真实结束时间写入 raw
        "USER_GOAL": args.goal,
        "FIRST_ACTION": args.first_action,
        "FIRST_ACTION_SUCCESS": args.first_action_success,
        "TASK_SUCCESS": "success",  # 闭合时更新
        "TRUST_FAILURE": False,
        "USER_BLOCKED": False,
        "NOTES": args.notes or "",
    }
    validate_session_row(preview, refs)
    state["sessions"][sid] = preview
    _save_open(state)
    print(f"OPEN {sid} @ {start} corpus={corpus_ref}")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    state = _load_open()
    sid = args.id
    if sid not in state["sessions"]:
        print(f"REJECTED: session 未开启或已闭合：{sid}", file=sys.stderr)
        return 1
    meta = state["sessions"][sid]
    rows = load_jsonl(ART_DIR / "interactions.jsonl")
    index = sum(1 for r in rows if r["SESSION_ID"] == sid) + 1
    if args.corpus_ref:
        meta = dict(meta)
        meta["CORPUS_REF"] = args.corpus_ref
    flags: dict[str, bool] = {name: getattr(args, name.lower()) for name in FLAG_NAMES}
    family = CLASSIFICATION_FAMILY.get(args.classification)
    if family is None:
        print(f"REJECTED: 未知 classification：{args.classification}", file=sys.stderr)
        return 1
    for name, value in family.items():
        if not flags[name]:
            flags[name] = value
    now = _now()
    row: dict[str, Any] = {
        "BENCHMARK_VERSION": BENCHMARK_VERSION,
        "CORPUS_REF": meta["CORPUS_REF"],
        "SESSION_ID": sid,
        "INTERACTION_ID": f"{sid}#T{index}",
        "INTERACTION_INDEX": index,
        "PERSONA": meta["PERSONA"],
        "SESSION_TYPE": meta["SESSION_TYPE"],
        "TASK_ID": args.task_id or f"{meta['SESSION_ID'].split('-')[-2]}-{meta['SESSION_ID'].split('-')[-1]}-T{index}",
        "TASK_CLASS": args.task_class,
        "SESSION_START": meta["SESSION_START"],
        "SESSION_END": now,
        "EXPECTED_BEHAVIOR": _str(args, "expected"),
        "ACTUAL_BEHAVIOR": _str(args, "actual"),
        "QUESTION": _str(args, "question"),
        "RESPONSE_SUMMARY": _str(args, "response_summary"),
        "EVIDENCE_CITED": _str(args, "evidence"),
        "FIRST_ACTION_SUCCESS": meta["FIRST_ACTION_SUCCESS"],
        "TASK_SUCCESS": "success",
        "RECOVERY_EVENT": flags["RECOVERY_EVENT"],
        **flags,
        "CLASSIFICATION": args.classification,
        "NOTES": _str(args, "notes") or "无",
    }
    row = {k: v for k, v in row.items() if k in set(row) }  # keep order stable
    _, refs = load_corpus_refs(ART_DIR)
    validate_interaction_row(row, refs)
    record_interaction(ART_DIR, row)
    print(f"RECORDED {row['INTERACTION_ID']} classification={args.classification}")
    return 0


def cmd_close_session(args: argparse.Namespace) -> int:
    state = _load_open()
    sid = args.id
    if sid not in state["sessions"]:
        print(f"REJECTED: session 未开启：{sid}", file=sys.stderr)
        return 1
    meta = state["sessions"][sid]
    interactions = load_jsonl(ART_DIR / "interactions.jsonl")
    mine = [r for r in interactions if r["SESSION_ID"] == sid]
    if not mine:
        print(f"REJECTED: session 无任何 interaction，禁止空闭合：{sid}", file=sys.stderr)
        return 1
    meta["SESSION_END"] = _now()
    meta["TASK_SUCCESS"] = args.task_success
    meta["TRUST_FAILURE"] = args.trust_failure or any(r["TRUST_FAILURE"] for r in mine)
    meta["USER_BLOCKED"] = args.user_blocked or any(r["USER_BLOCKED"] for r in mine)
    meta["NOTES"] = _str(args, "notes") or meta["NOTES"]
    _, refs = load_corpus_refs(ART_DIR)
    validate_session_row(meta, refs)
    append_jsonl_row(ART_DIR / "sessions.jsonl", meta)
    del state["sessions"][sid]
    _save_open(state)
    print(f"CLOSED {sid} interactions={len(mine)} task_success={meta['TASK_SUCCESS']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HBENCH_V2 采集驱动")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("open-session")
    p.add_argument("--id", required=True)
    p.add_argument("--persona", required=True, choices=["A", "B", "C", "D"])
    p.add_argument("--type", required=True, choices=["CONTINUOUS", "NEW", "REFRESH", "RESTART", "CROSS_DAY"])
    p.add_argument("--goal", required=True)
    p.add_argument("--first-action", required=True)
    p.add_argument("--first-action-success", type=bool, default=True)
    p.add_argument("--special-cohort", default=None)
    p.add_argument("--corpus-ref", default=None)
    p.add_argument("--notes", default="")
    p.set_defaults(func=cmd_open_session)

    p = sub.add_parser("record")
    p.add_argument("--id", required=True)
    p.add_argument("--corpus-ref", default=None, help="覆盖 session 的 CORPUS_REF（corpus 升级后的后续交互）")
    p.add_argument("--task-id", default=None)
    p.add_argument("--task-class", required=True)
    p.add_argument("--question", default="")
    p.add_argument("--expected", required=True)
    p.add_argument("--actual", required=True)
    p.add_argument("--response-summary", default="")
    p.add_argument("--evidence", default="")
    p.add_argument("--classification", required=True)
    p.add_argument("--notes", default="")
    for name in FLAG_NAMES:
        p.add_argument(f"--{name.lower()}", action="store_true")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("close-session")
    p.add_argument("--id", required=True)
    p.add_argument("--task-success", default="success", choices=["success", "partial", "incomplete", "failed"])
    p.add_argument("--trust-failure", action="store_true")
    p.add_argument("--user-blocked", action="store_true")
    p.add_argument("--notes", default="")
    p.set_defaults(func=cmd_close_session)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
