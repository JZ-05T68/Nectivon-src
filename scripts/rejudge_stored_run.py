"""Offline re-judgment of a stored eval run against (possibly revised) cases.

Used for CASE_DESIGN_ERROR handling: the frozen case file may be revised
between the BEFORE and AFTER *runs* — this script re-judges BOTH sides from
the stored raw answers/locations/tool so the delta stays comparable without
spending new API calls. Never modifies the stored run files.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.reliability_eval import _judge_case  # noqa: E402


def rejudge(run_path: Path, case_path: Path) -> dict[str, object]:
    run = json.loads(run_path.read_text(encoding="utf-8"))
    case_doc = json.loads(case_path.read_text(encoding="utf-8"))
    cases = {str(c["case_id"]): c for c in case_doc["cases"]}

    scorecard: dict[str, list[bool]] = {}
    rows: list[dict[str, object]] = []
    for record in run["results"]:
        case = cases[str(record["case_id"])]
        checks, failed = _judge_case(
            case,
            str(record.get("answer") or ""),
            list(record.get("citation_locations") or []),
            [int(m) for m in (record.get("citation_memory_ids") or [])],
            record.get("selected_tool"),
        )
        passed = not failed and record.get("status") != "error"
        for metric in (case.get("metrics") or {}):
            scorecard.setdefault(str(metric), []).append(bool(passed))
        scorecard.setdefault("overall", []).append(bool(passed))
        rows.append({"case_id": record["case_id"], "passed": passed, "failed": failed})

    summary = {
        metric: f"{sum(v)}/{len(v)}"
        for metric, v in scorecard.items()
        if metric != "overall"
    }
    return {
        "run": run_path.name,
        "case_file": case_path.name,
        "overall": f"{sum(scorecard['overall'])}/{len(scorecard['overall'])}",
        "scorecard": summary,
        "cases": rows,
    }


if __name__ == "__main__":
    run_path = Path(sys.argv[1])
    case_path = Path(sys.argv[2])
    out = rejudge(run_path, case_path)
    print(json.dumps(out, ensure_ascii=False, indent=1))
