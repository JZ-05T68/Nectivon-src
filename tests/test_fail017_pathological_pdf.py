"""FAIL-017 regression guard: pathological lowres-pixmap PDF layers.

The original incident (2026-09-06, dirty-long-term-v2 --read phase) reported a
native-level silent process exit (code 0, no traceback, no faulthandler output)
when reading a page image re-encoded via ``0.5x pixmap + insert_image``. This
campaign's layered reproduction could NOT reproduce any native crash on the
current stack (MuPDF 1.29.0 / Pillow 12.3.0 / Python 3.11.9):

- PyMuPDF render of the pathological PDF: clean;
- PIL decode/resize of the rendered PNG: clean;
- ``AgentDocumentReader.read_document``: fails CLOSED with an explicit
  Chinese error on the rasterized (no text layer) page — never a crash.

These tests pin exactly that behaviour. Each native-touching layer runs in an
isolated subprocess so a future native-level silent exit becomes a loud,
visible test failure instead of an silently dying test process.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAYER_SCRIPT = PROJECT_ROOT / "tests" / "fixtures" / "fail017" / "_fail017_layer_probe.py"


def _run_layer(layer: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(LAYER_SCRIPT),
            layer,
            "--workdir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        cwd=str(PROJECT_ROOT),
        env={
            **__import__("os").environ.copy(),
            "EKB_STAGING_INSTANCE": "",
        },
    )


def test_fail017_render_layer_does_not_crash(tmp_path: Path) -> None:
    """PyMuPDF rendering the pathological PDF must exit cleanly (no native exit)."""

    proc = _run_layer("render", tmp_path)
    assert proc.returncode == 0, (
        "FAIL-017 guard: render layer returned "
        f"{proc.returncode}\nstdout={proc.stdout[-2000:]}\nstderr={proc.stderr[-2000:]}"
    )
    assert "LAYER_DONE" in proc.stdout


def test_fail017_pil_layer_does_not_crash(tmp_path: Path) -> None:
    """PIL decode/resize of the pathological render must exit cleanly."""

    proc = _run_layer("pil", tmp_path)
    assert proc.returncode == 0, (
        "FAIL-017 guard: PIL layer returned "
        f"{proc.returncode}\nstdout={proc.stdout[-2000:]}\nstderr={proc.stderr[-2000:]}"
    )
    assert "LAYER_DONE" in proc.stdout


def test_fail017_read_document_fails_closed(tmp_path: Path) -> None:
    """Rasterized no-text-layer page: read_document must fail closed, not crash."""

    proc = _run_layer("read", tmp_path)
    assert proc.returncode == 0, (
        "FAIL-017 guard: read layer returned "
        f"{proc.returncode}\nstdout={proc.stdout[-2000:]}\nstderr={proc.stderr[-2000:]}"
    )
    assert "FAIL_CLOSED" in proc.stdout
    # V086-101/203: the document-level error is now the honest aggregate
    # (per-page isolation keeps successful pages); the per-page reason
    # ("还没有可读文字") is logged, not surfaced as the aggregate text.
    assert "AI 阅读未全部完成" in proc.stdout
    assert "成功 0/1 页" in proc.stdout
