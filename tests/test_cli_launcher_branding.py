"""CLI and Windows launcher presentation tests (post-reconciliation)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

NECTIVON_BATCH_LAUNCHERS = (
    "启动正式版.bat",
    "stop_release.bat",
    "启动测试版8511.bat",
    "停止测试版8511.bat",
    "启动测试版8512.bat",
    "停止测试版8512.bat",
)

#: The one canonical launcher implementation every thin wrapper delegates to
#: (2026-09-25 launcher hardening: wrappers only declare instance/action).
CANONICAL_LAUNCHER = "_nectivon_launcher.cmd"

NEUTRAL_NAMED_LAUNCHERS = ("check_environment.bat", "run_all_tests.bat")

SILENT_LAUNCHER = "静默启动Nectivon.vbs"

#: Formally removed old-brand launcher files (2026-09-21 reconciliation).
FORMALLY_REMOVED = (
    "启动Nectivon.bat",
    "停止Nectivon.bat",
    "启动Nectivon_测试服_8511.bat",
    "启动Nectivon_测试服_8511_无浏览器.bat",
    "停止Nectivon_测试服_8511.bat",
    "启动Nectivon_测试服_8512.bat",
    "停止Nectivon_测试服_8512.bat",
    "启动Nectivon_测试服_8512_无浏览器.bat",
    "查看运行状态.bat",
    "启用开机自启.bat",
    "关闭开机自启.bat",
    "启动工程知识库.bat",
    "停止工程知识库.bat",
    "启动工程知识库_测试服_8511.bat",
    "启动工程知识库_测试服_8511_无浏览器.bat",
    "停止工程知识库_测试服_8511.bat",
    "静默启动工程知识库.vbs",
)

CLI_SCRIPTS = (
    "scripts/service_manager.py",
    "scripts/restore_backup.py",
    "scripts/ai_embedding_experiment.py",
    "scripts/ai_real_hybrid_fusion_probe.py",
    "scripts/ai_real_natural_language_probe.py",
    "scripts/ai_real_page_index.py",
    "scripts/ai_real_query_probe.py",
    "scripts/ai_smoke_test.py",
    "scripts/release_check.py",
)

OLD_VISIBLE_BRAND = re.compile(r"\bEKB\b|Engineering Knowledge Base|工程知识库")


def _read_batch(name: str) -> str:
    return (PROJECT_ROOT / name).read_text(encoding="utf-8-sig")


def _read_vbs(name: str) -> str:
    return (PROJECT_ROOT / name).read_text(encoding="utf-16")


def test_nectivon_batch_launchers_delegate_to_canonical_implementation() -> None:
    """Wrappers only declare instance/action; lifecycle logic lives once."""

    canonical = _read_batch(CANONICAL_LAUNCHER)
    assert "service_manager.py" in canonical
    assert all(not line.strip().startswith("chcp ") for line in canonical.splitlines())
    assert not OLD_VISIBLE_BRAND.search(canonical)
    for removed in FORMALLY_REMOVED:
        assert removed not in canonical, f"{CANONICAL_LAUNCHER} references {removed}"

    for name in NECTIVON_BATCH_LAUNCHERS:
        text = _read_batch(name)
        # Thin wrapper: declares the instance, then delegates exactly once to
        # the canonical implementation — no duplicated lifecycle logic.
        assert text.count(f'call "%~dp0{CANONICAL_LAUNCHER}"') == 1
        assert "service_manager.py" not in text
        assert "streamlit run" not in text
        assert "-m streamlit" not in text
        assert not OLD_VISIBLE_BRAND.search(text)
        for removed in FORMALLY_REMOVED:
            assert removed not in text, f"{name} still references {removed}"

    for name in NEUTRAL_NAMED_LAUNCHERS:
        text = _read_batch(name)
        assert "Nectivon" in text

    for removed in FORMALLY_REMOVED:
        assert not (PROJECT_ROOT / removed).is_file(), (
            f"old-brand launcher must stay removed: {removed}"
        )


def test_silent_launcher_is_standalone_and_currently_branded() -> None:
    text = _read_vbs(SILENT_LAUNCHER)

    assert "service_manager.py" in text
    assert text.count("shell.Run") == 1
    assert not OLD_VISIBLE_BRAND.search(text)
    assert "EngineeringKnowledgeBase" not in text
    for removed in FORMALLY_REMOVED:
        assert removed not in text, f"{SILENT_LAUNCHER} still references {removed}"


def test_launcher_presentation_uses_nectivon() -> None:
    launchers = (
        *NEUTRAL_NAMED_LAUNCHERS,
        *NECTIVON_BATCH_LAUNCHERS,
    )
    for name in launchers:
        visible_lines = [
            line.strip()
            for line in _read_batch(name).splitlines()
            if line.strip().casefold().startswith(("title ", "echo "))
        ]
        assert not OLD_VISIBLE_BRAND.search("\n".join(visible_lines))
        assert any("Nectivon" in line for line in visible_lines)

    silent = _read_vbs(SILENT_LAUNCHER)
    assert "Nectivon 启动失败" in silent
    assert "工程知识库启动失败" not in silent


def test_scheduled_task_and_startup_identifiers_remain_compatible() -> None:
    source = (PROJECT_ROOT / "scripts/service_manager.py").read_text(encoding="utf-8")
    assert 'TASK_NAME: Final[str] = "EngineeringKnowledgeBase"' in source
    assert '/ "EngineeringKnowledgeBase.cmd"' in source
    assert 'TASK_NAME: Final[str] = "Nectivon"' not in source
    assert 'Nectivon.cmd' not in source


@pytest.mark.parametrize("relative_path", CLI_SCRIPTS)
def test_current_cli_help_uses_nectivon(relative_path: str) -> None:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / relative_path), "--help"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Nectivon" in result.stdout
    assert not OLD_VISIBLE_BRAND.search(result.stdout)
