"""Partitioned pytest runner for the wide regression (Common Fix Round 2).

Splits tests/ into ~8-file chunks (proven reliable on this machine), runs
each chunk with the safe-delete shim disabled, and writes one JSON+text
report per chunk so real failures separate from environment flakiness.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

PYTHON = sys.executable
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "fixround2-regression"
OUT.mkdir(parents=True, exist_ok=True)

test_files = sorted((ROOT / "tests").glob("test_*.py"))
chunks = [test_files[i : i + 8] for i in range(0, len(test_files), 8)]
_SELECTED = __import__("os").environ.get("RUNNER_CHUNKS", "")
if _SELECTED:
    SELECTED = {int(part) for part in _SELECTED.split(",") if part}
else:
    START = int(__import__("os").environ.get("RUNNER_START", "1"))
    END = int(__import__("os").environ.get("RUNNER_END", str(len(chunks))))
    SELECTED = set(range(START, END + 1))
PER_CHUNK_TIMEOUT = int(__import__("os").environ.get("RUNNER_TIMEOUT", "900"))

summary = {"total_files": len(test_files), "chunks": len(chunks), "results": []}
start = time.time()
for index, chunk in enumerate(chunks, start=1):
    if index not in SELECTED:
        continue
    report_path = OUT / f"chunk_{index:02d}.json"
    command = [PYTHON, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", *(
        str(path) for path in chunk
    )]
    # Inherit the FULL environment (HOME/USERPROFILE are required by
    # Streamlit's config loader) and only override the safe-delete shim.
    environment = dict(os.environ)
    environment["CODEBUDDY_SAFE_DELETE_ENABLED"] = "0"
    environment["PYTHONPATH"] = str(ROOT)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=PER_CHUNK_TIMEOUT,
        env=environment,
    )
    tail = completed.stdout.strip().splitlines()[-6:]
    failed = completed.returncode != 0
    summary["results"].append(
        {
            "chunk": index,
            "files": [path.name for path in chunk],
            "exit": completed.returncode,
            "tail": tail,
        }
    )
    report_path.write_text(
        completed.stdout[-20000:] + "\n===STDERR===\n" + completed.stderr[-4000:],
        encoding="utf-8",
    )
    status = "FAIL" if failed else "ok"
    print(f"[{index:02d}/{len(chunks)}] exit={completed.returncode} {status}", flush=True)

elapsed = time.time() - start
failed_chunks = [item for item in summary["results"] if item["exit"] != 0]
summary_path = OUT / "summary.json"
summary_path.write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(
    f"DONE files={len(test_files)} chunks={len(chunks)} "
    f"failed_chunks={len(failed_chunks)} elapsed={elapsed:.0f}s",
    flush=True,
)
for item in failed_chunks:
    print(f"FAILED chunk {item['chunk']}: {item['files']}", flush=True)
