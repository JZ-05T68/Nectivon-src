"""Run the v0.8 Frozen Reliability Eval against the 8511 staging instance.

E2E only: every case question goes through the real single-step Agent pipeline
(``LocalDocumentAgentClient``) with the real hard model. Ground truth stays in
the case file's ``expect`` block and is read only by the judge after the
response is produced. The runner forces staging; running it against the formal
database fails fast on the corpus manifest check.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

# Staging is a hard precondition: set before any src import reads settings.
os.environ["EKB_STAGING_INSTANCE"] = "1"

# Windows consoles/redirects default to legacy codepages; keep UTF-8 so
# tracebacks and Chinese output never die inside the print call itself.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent.local_document import LocalDocumentAgent  # noqa: E402
from src.agent_document_reader import AgentReadingStore  # noqa: E402
from src.config import staging_settings  # noqa: E402
from src.database import Database  # noqa: E402
from src.source_metadata import InvalidSourceId, parse_source_id  # noqa: E402

FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "reliability_eval"
DEFAULT_CASES = FIXTURE_DIR / "frozen_cases_v1.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "docs" / "v0.8-eval-results"

NO_ANSWER_MARKERS = (
    "没有提供", "未提供", "未找到", "信息不足", "无法确定", "没有找到", "没有找到相关",
)
CONFLICT_MARKERS = ("不同", "差异", "冲突", "不一致", "不一样", "改为", "修订为")


def _normalize(text: str) -> str:
    return " ".join(str(text).split())


_NUMERIC_FRAGMENT = re.compile(r"\d+(?:\.\d+)?")


def _fragment_in(text: str, fragment: str) -> bool:
    """Substring match with digit boundaries for pure-numeric fragments.

    A bare-number fragment such as "26" must not match inside longer tokens:
    the document title FAIL026 contains "26", and "2.6"/"126" embed digits.
    Plain substring matching produced false forbid hits (holdout H26-4) and
    false answer-fragment passes; numeric fragments therefore require that
    the surrounding characters are not digits or a decimal point.
    """
    fragment = str(fragment)
    if not _NUMERIC_FRAGMENT.fullmatch(fragment):
        return fragment in text
    pattern = re.compile(
        rf"(?<![0-9.]){re.escape(fragment)}(?![0-9])"
    )
    return pattern.search(text) is not None


def _is_no_answer(answer: str) -> bool:
    if any(marker in answer for marker in NO_ANSWER_MARKERS):
        return True
    scattered_negation = ("没有" in answer or "未" in answer) and "找到" in answer
    return scattered_negation


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_cases(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise RuntimeError(f"case 文件为空或格式错误：{path}")
    return data


def _check_corpus_integrity(
    database: Database, output: dict[str, object], corpus_ref: str
) -> bool:
    """Compare live corpus against the manifest named by the case corpus_ref.

    Morning-closure §5: each case file declares which corpus version its
    ground truth is bound to. A mismatch between case corpus_ref and the
    live corpus is CORPUS_MISMATCH and refuses to score unless
    ``--allow-drift`` is given explicitly.
    """

    manifest_by_ref = {
        "v0.8-frozen-corpus-v1": "corpus_manifest_v1.json",
        "dirty-long-term-v1": "corpus_manifest_dirty_v2.json",
        "dirty-long-term-v2": "corpus_manifest_dirty_v3.json",
        "dirty-long-term-v3": "corpus_manifest_dirty_v4.json",
        "hbv2-corpus-20260908-r4": "corpus_manifest_hbv2_r4.json",
    }
    manifest_name = manifest_by_ref.get(corpus_ref)
    if manifest_name is None:
        raise RuntimeError(f"未知 corpus_ref：{corpus_ref}")
    manifest_path = FIXTURE_DIR / manifest_name
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_docs = manifest["documents"]
    live_docs = [
        {"title": d.title, "sha256": d.sha256}
        for d in sorted(database.list_documents(), key=lambda item: item.id)
    ]
    # Same-title documents are legitimate (same-name different-content
    # fixture); compare multisets of (title, sha256) pairs, never title dicts.
    from collections import Counter

    expected_pairs = Counter(
        (str(item["title"]), str(item["sha256"])) for item in expected_docs
    )
    live_pairs = Counter(
        (str(item["title"]), str(item["sha256"])) for item in live_docs
    )
    def _mark(title: str, sha: str) -> str:
        side = "+" if expected_pairs[(title, sha)] > live_pairs[(title, sha)] else "-"
        return f"{title}:{sha[:8]}{side}"

    difference = sorted(
        _mark(title, sha)
        for title, sha in (expected_pairs - live_pairs) | (live_pairs - expected_pairs)
    )
    missing = difference
    changed = []
    count_changed = len(live_docs) != len(expected_docs)
    con = re_connection(database)
    memory_count = con.execute(
        "SELECT count(*) FROM knowledge_memory_entries "
        "WHERE status != 'deleted'"
    ).fetchone()[0]
    drift = bool(
        missing
        or changed
        or count_changed
        or memory_count != manifest["knowledge_memory_entry_count"]
    )
    output["corpus_integrity"] = {
        "corpus_ref": corpus_ref,
        "manifest": manifest_name,
        "missing_documents": missing,
        "sha_changed_documents": changed,
        "document_count_live": len(live_docs),
        "document_count_expected": len(expected_docs),
        "memory_entry_count_live": memory_count,
        "memory_entry_count_expected": manifest["knowledge_memory_entry_count"],
        "seed_drift_detected": drift,
    }
    return drift


def re_connection(database: Database) -> object:
    """Return a read-only sqlite connection mirroring the database path."""
    import sqlite3

    path = Path(database.database_path) if hasattr(database, "database_path") else None
    if path is None:
        path = PROJECT_ROOT / "staging-data" / "data" / "database" / "knowledge.db"
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _citation_locations(
    database: Database, citations: tuple[str, ...]
) -> tuple[list[dict[str, object]], list[int]]:
    """Resolve stable ids to (doc title, page) and memory entry ids."""
    pages: list[dict[str, object]] = []
    memory_ids: list[int] = []
    for stable_id in citations:
        try:
            _, kind, local_id = parse_source_id(stable_id)
        except InvalidSourceId:
            continue
        if kind == "page":
            page = database.get_page(local_id)
            if page is None:
                continue
            document = database.get_document(page.document_id)
            if document is not None:
                pages.append({"doc": document.title, "page": page.page_number})
        elif kind == "knowledge_memory":
            memory_ids.append(local_id)
    return pages, memory_ids


def _location_match(
    locations: list[dict[str, object]], expected: dict[str, object]
) -> bool:
    return any(
        str(expected["doc"]) in str(item["doc"]) and item["page"] == expected["page"]
        for item in locations
    )


def _judge_answer(expect: dict[str, object], answer: str) -> tuple[bool, list[str]]:
    """Judge the answer text; return (passed, failed check names)."""
    checks: list[str] = []
    text = _normalize(answer)

    no_answer = _is_no_answer(answer)

    fragments = expect.get("answer_fragments") or []
    if fragments and not all(_fragment_in(text, f) for f in fragments):
        checks.append("answer_fragments")

    for group in expect.get("answer_any_fragments") or []:
        if not any(_fragment_in(text, option) for option in group):
            checks.append("answer_any_fragments")

    for fragment in expect.get("forbid_fragments") or []:
        if _fragment_in(text, fragment):
            checks.append("forbid_fragments")

    for pattern in expect.get("forbid_regex") or []:
        if re.search(pattern, _normalize(answer)):
            checks.append("forbid_regex")

    if expect.get("conflict_required") and not any(
        marker in answer for marker in CONFLICT_MARKERS
    ):
        checks.append("conflict_required")

    if expect.get("no_answer_ok") and not no_answer and "answer_fragments" not in checks:
        # Negative cases: honest refusal OR the allowed attribution groups.
        groups = expect.get("answer_any_fragments") or []
        if not groups:
            checks.append("no_answer_refusal")
        elif not any(any(option in text for option in group) for group in groups):
            checks.append("no_answer_refusal")

    blur = expect.get("blur_lenient")
    if isinstance(blur, dict):
        positives = blur.get("positives") or []
        uncertainty = blur.get("uncertainty") or []
        if not any(any(option in text for option in group) for group in positives):
            if not any(marker in answer for marker in uncertainty):
                checks.append("blur_confident_wrong")

    return (not checks), checks


def _judge_case(
    case: dict[str, object],
    answer: str,
    locations: list[dict[str, object]],
    memory_ids: list[int],
    selected_tool: str | None,
) -> tuple[dict[str, bool], list[str]]:
    expect = case.get("expect") or {}
    failed: list[str] = []

    tool_expect = expect.get("tool")
    tool_ok = True
    if tool_expect is not None:
        allowed = {tool_expect} if isinstance(tool_expect, str) else set(tool_expect)
        tool_ok = selected_tool in allowed
        if not tool_ok:
            failed.append("tool")

    answer_ok, answer_checks = _judge_answer(expect, answer)
    failed.extend(answer_checks)

    citation_ok = True
    cite_must = expect.get("cite_must") or []
    if cite_must and not all(_location_match(locations, item) for item in cite_must):
        citation_ok = False
        failed.append("cite_must")

    memory_expect = expect.get("memory_must")
    if memory_expect and not all(mid in memory_ids for mid in memory_expect):
        citation_ok = False
        failed.append("memory_must")

    memory_any = expect.get("memory_must_any")
    if memory_any and not any(mid in memory_ids for mid in memory_any):
        citation_ok = False
        failed.append("memory_must_any")

    memory_forbid = expect.get("memory_forbid") or []
    if memory_forbid and any(mid in memory_ids for mid in memory_forbid):
        citation_ok = False
        failed.append("memory_forbid")

    if expect.get("memory_forbid_all") and memory_ids:
        citation_ok = False
        failed.append("memory_forbid_all")

    checks = {"tool": tool_ok, "answer": answer_ok, "citation": citation_ok}
    return checks, failed


def _run_single_case(
    case: dict[str, object],
    index: int,
    total: int,
    agent: LocalDocumentAgent,
    database: Database,
    settings: object,
) -> tuple[dict[str, object], dict[str, bool], list[str]]:
    """Execute and judge one case; never raises (crash = failed record)."""
    case_id = str(case["case_id"])
    question = str(case["question"])
    hard_model = str(settings.ai_llm_model_hard)  # type: ignore[attr-defined]
    before_call_ids = {call.call_uuid for call in database.list_ai_calls(limit=500)}
    started = time.monotonic()
    try:
        response = agent.ask(question)
    except Exception as error:  # noqa: BLE001 - overnight run must survive one case
        elapsed_ms = int((time.monotonic() - started) * 1000)
        record: dict[str, object] = {
            "case_id": case_id,
            "family": case.get("family"),
            "taxonomy": case.get("taxonomy"),
            "question": question,
            "status": "crashed",
            "error_code": "RUNNER_EXCEPTION",
            "error_detail": f"{type(error).__name__}: {error}",
            "selected_tool": None,
            "tool_ok": False,
            "answer_ok": False,
            "citation_ok": False,
            "failed_checks": ["RUNNER_EXCEPTION"],
            "overall_passed": False,
            "answer": "",
            "citation_locations": [],
            "citation_memory_ids": [],
            "grounded": False,
            "model_verified_from_ai_ledger": False,
            "elapsed_ms": elapsed_ms,
        }
        return record, {"tool": False, "answer": False, "citation": False}, [
            "RUNNER_EXCEPTION"
        ]

    elapsed_ms = int((time.monotonic() - started) * 1000)

    new_calls = tuple(
        call for call in database.list_ai_calls(limit=500)
        if call.call_uuid not in before_call_ids
    )
    model_calls = tuple(
        call for call in new_calls
        if call.source_feature in {"agent_decision", "agent_final_answer"}
    )
    model_verified = bool(model_calls) and all(
        call.model == hard_model and call.status == "success"
        for call in model_calls
    )

    locations, memory_ids = _citation_locations(database, response.citations)
    trace = response.trace
    selected_tool = trace.selected_tool if trace is not None else None

    completed = response.status == "completed"
    checks, failed = _judge_case(case, response.answer, locations, memory_ids, selected_tool)
    if not completed:
        failed.append(f"status:{response.error.code if response.error else 'unknown'}")
    if not model_verified:
        failed.append("MODEL_UNVERIFIED")

    record = {
        "case_id": case_id,
        "family": case.get("family"),
        "taxonomy": case.get("taxonomy"),
        "question": question,
        "status": str(response.status),
        "error_code": response.error.code if response.error else None,
        "selected_tool": selected_tool,
        "tool_ok": checks["tool"],
        "answer_ok": checks["answer"],
        "citation_ok": checks["citation"],
        "failed_checks": failed,
        "overall_passed": not failed,
        "answer": response.answer,
        "citation_locations": locations,
        "citation_memory_ids": memory_ids,
        "grounded": response.grounded,
        "model_verified_from_ai_ledger": model_verified,
        "elapsed_ms": elapsed_ms,
    }
    return record, checks, failed


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="v0.8 Frozen Reliability Eval runner")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument(
        "--phase", required=True,
        help="BEFORE_FIX / AFTER_FIX / HOLDOUT_BEFORE / HOLDOUT_AFTER",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--only", nargs="*", help="只运行指定 case_id")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=1.0, help="case 间隔秒数")
    parser.add_argument("--allow-drift", action="store_true", help="语料漂移时仍继续（记录 H2）")
    args = parser.parse_args(argv)

    settings = staging_settings()
    database = Database(settings.database_path)
    reading_store = AgentReadingStore(settings.agent_readings_dir)

    case_doc = _load_cases(args.cases)
    all_cases = list(case_doc["cases"])  # type: ignore[arg-type]
    if args.only:
        wanted = set(args.only)
        all_cases = [case for case in all_cases if case["case_id"] in wanted]
    if args.limit is not None:
        all_cases = all_cases[: args.limit]

    header: dict[str, object] = {
        "phase": args.phase,
        "case_file": args.cases.name,
        "case_count": len(all_cases),
        "started_at_utc": datetime.now(UTC).isoformat(),
        "model_expected": case_doc.get("model_expectation"),
        "model_hard": settings.ai_llm_model_hard,
        "database": str(settings.database_path),
    }
    corpus_ref = str(case_doc.get("corpus_ref", "v0.8-frozen-corpus-v1"))
    header["corpus_ref"] = corpus_ref
    # PROCESS-INTEGRITY: metrics must be validated BEFORE any case runs —
    # a post-run validation crash would waste an entire paid run (this
    # actually happened: run CC_W2 lost 30 completed cases to a summary-time
    # RuntimeError on an illegal metrics value).
    check_keys = {"tool": "tool_ok", "answer": "answer_ok", "citation": "citation_ok"}
    for case in all_cases:
        for check in (case.get("metrics") or {}).values():  # type: ignore[union-attr]
            if str(check) not in check_keys:
                raise RuntimeError(
                    f"case {case['case_id']} 引用未知 check 名：{check}"
                )
    drift = _check_corpus_integrity(database, header, corpus_ref)
    if drift and not args.allow_drift:
        print("语料与冻结清单不一致（H2 SEED_DRIFT），中止。详情见输出 header。")
        _write_json(args.output_dir / f"aborted_{args.phase}.json", header)
        return 2
    if drift:
        header["seed_drift_allowed"] = True

    unread = []
    for document in database.list_documents():
        state = reading_store.document_state(document.id)
        if not (state and state.completed and state.model == settings.ai_llm_model_hard):
            unread.append(document.title)
    if unread:
        print("以下资料尚未被硬模型完整阅读，请先在 8511 触发阅读：", "、".join(unread))
        return 2

    agent = LocalDocumentAgent(
        database=database,
        provider=_build_provider(),
        readings=reading_store,
        model=settings.ai_llm_model_hard,
        vision_provider=_build_provider(),
        vision_model=settings.ai_vision_model,
        pages_dir=settings.pages_dir,
    )

    results: list[dict[str, object]] = []
    for index, case in enumerate(all_cases, start=1):
        record, checks, failed = _run_single_case(
            case, index, len(all_cases), agent, database, settings
        )
        results.append(record)
        verdict = "PASS" if not failed else "FAIL"
        print(
            f"[{index}/{len(all_cases)}] {verdict} {record['case_id']} "
            f"tool={record['selected_tool']} "
            f"checks={ {k: v for k, v in checks.items()} }",
            flush=True,
        )
        if failed:
            print(f"    failed: {failed}", flush=True)
            print(f"    answer: {str(record['answer'])[:220]}", flush=True)
        if args.delay > 0:
            time.sleep(args.delay)

    # Persist raw evidence before scoring so a scoring bug cannot lose the run.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = args.output_dir / f"run_{args.phase}_{stamp}.json"
    raw_output = {
        **header,
        "finished_at_utc": datetime.now(UTC).isoformat(),
        "results": results,
    }
    _write_json(out_path, raw_output)

    metrics: dict[str, dict[str, int]] = {}
    for case, record in zip(all_cases, results, strict=True):
        for metric, check in (case.get("metrics") or {}).items():  # type: ignore[union-attr]
            bucket = metrics.setdefault(str(metric), {"numerator": 0, "denominator": 0})
            bucket["denominator"] += 1
            if record[check_keys[str(check)]]:  # type: ignore[literal-required]
                bucket["numerator"] += 1

    scorecard = {
        metric: {
            **bucket,
            "pct": round(bucket["numerator"] / bucket["denominator"] * 100, 1),
            "small_sample": bucket["denominator"] < 5,
        }
        for metric, bucket in sorted(metrics.items())
    }

    output = {
        **raw_output,
        "scorecard": scorecard,
        "overall": {
            "cases_passed": sum(1 for r in results if r["overall_passed"]),
            "cases_total": len(results),
        },
    }
    _write_json(out_path, output)

    print("\n=== SCORECARD ===")
    for metric, bucket in scorecard.items():
        note = " (n<5)" if bucket["small_sample"] else ""
        print(
            f"{metric}: {bucket['numerator']}/{bucket['denominator']}"
            f" = {bucket['pct']}%{note}"
        )
    print(
        f"overall: {output['overall']['cases_passed']}/{output['overall']['cases_total']}"
    )
    print(f"results -> {out_path}")
    return 0


def _build_provider() -> object:
    from src.runtime import application_ai_provider

    return application_ai_provider()


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - never die silently overnight
        import traceback

        traceback.print_exc()
        raise SystemExit(3) from None
