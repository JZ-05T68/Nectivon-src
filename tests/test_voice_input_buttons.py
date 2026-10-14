"""Regression checks for optional dictation, without audio or an AI provider."""

import shutil
import subprocess
from pathlib import Path

import pytest

from src.voice_input_ui import render_voice_input


def test_browser_dictation_lifecycle():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for browser dictation regression checks")
    script = Path(__file__).parent / "js/voice_input.test.cjs"
    result = subprocess.run(
        [node, "--test", str(script)], capture_output=True, text=True,
        encoding="utf-8", timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_dictation_installer_uses_only_shipped_static_assets(monkeypatch):
    calls = []
    monkeypatch.setattr("src.voice_input_ui.st.html", lambda *a, **kw: calls.append((a, kw)))
    render_voice_input()
    (body,), options = calls[0]
    assert options == {"unsafe_allow_javascript": True}
    assert "<script>" in body
    # Literal SVG markup inside script text makes DOMPurify remove the script.
    script = body.split("<script>", 1)[1]
    assert "<svg" not in script
    assert "fetch(" not in script
    assert "getUserMedia(" not in script
