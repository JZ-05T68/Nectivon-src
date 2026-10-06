"""Regression guards for Nectivon's public brand and compatibility boundary.

This suite deliberately does not require a repository-wide zero count for the
former EKB / Engineering Knowledge Base name. Historical records and stable
technical identifiers have different compatibility obligations from current
product presentation.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

OLD_VISIBLE_BRAND = re.compile(
    r"(?<![A-Za-z0-9_])EKB(?![A-Za-z0-9_])|Engineering Knowledge Base|工程知识库"
)

README_POLICIES = {
    "README.md": (
        "# Nectivon v",
        "**Nectivon**（曾用名：Engineering Knowledge Base / EKB）",
        "### v0.0.1 — 初始本地工程知识库 MVP",
    ),
    "README_EN.md": (
        "# Nectivon v",
        "**Nectivon** (formerly **Engineering Knowledge Base / EKB**)",
        "### v0.0.1 — Initial Local Engineering Knowledge Base MVP",
    ),
    "README_JP.md": (
        "# Nectivon v",
        "**Nectivon**（旧称：Engineering Knowledge Base / EKB）",
        "### v0.0.1 — 初期ローカル工学ナレッジベース MVP",
    ),
}

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

#: Nectivon-named launchers that carry the full logic after the 2026-09-21
#: brand reconciliation (docs/BRAND_LAUNCHER_FAILURE_AUDIT_2026-09-21.md).
NECTIVON_LAUNCHERS = (
    "启动正式版.bat",
    "stop_release.bat",
    "启动测试版8511.bat",
    "停止测试版8511.bat",
    "启动测试版8512.bat",
    "停止测试版8512.bat",
    "check_environment.bat",
    "run_all_tests.bat",
    "静默启动Nectivon.vbs",
)

#: The former old-brand wrapper/logic files were formally removed; their
#: filenames themselves were user-visible old-brand surface.
FORMALLY_REMOVED_LAUNCHERS = (
    "启动Nectivon.bat",
    "停止Nectivon.bat",
    "启动Nectivon_测试服_8511.bat",
    "启动Nectivon_测试服_8511_无浏览器.bat",
    "停止Nectivon_测试服_8511.bat",
    "启动Nectivon_测试服_8512.bat",
    "停止Nectivon_测试服_8512.bat",
    "启动Nectivon_测试服_8512_无浏览器.bat",
    "启用开机自启.bat",
    "关闭开机自启.bat",
    "查看运行状态.bat",
    "启动工程知识库.bat",
    "停止工程知识库.bat",
    "启动工程知识库_测试服_8511.bat",
    "启动工程知识库_测试服_8511_无浏览器.bat",
    "停止工程知识库_测试服_8511.bat",
    "静默启动工程知识库.vbs",
)

HISTORICAL_PATH_RULES = (
    re.compile(r"^docs/archive/"),
    re.compile(r"^docs/v[^/]*\.md$"),
    re.compile(r"^docs/v0\.8-eval-results/"),
    re.compile(r"^docs/adr/"),
    re.compile(r"^docs/zero-batch/"),
    re.compile(r"^docs/BRAND_NAME_[^/]*\.md$"),
    re.compile(r"^docs/EKB_v0\.8[^/]*\.md$"),
    re.compile(r"^artifacts/"),
    re.compile(r"^benchmarks/"),
    re.compile(r"^tests/fixtures/"),
    re.compile(r"^CHANGELOG\.md$"),
)

TECHNICAL_COMPATIBILITY_MARKERS = {
    ".env.example": (
        "EKB_STORAGE_DIR",
        "EKB_STAGING_STORAGE_DIR",
        "EKB_AI_API_KEY",
    ),
    "src/config.py": ('env_prefix="EKB_"', '"engineering-kb.log"'),
    "src/backup_service.py": (
        '"engineering-knowledge-base-directory"',
        'f".ekb-restore-',
        'f".ekb-rollback-',
    ),
    "src/knowledge_export_service.py": (
        '"engineering-knowledge-base-knowledge-export"',
    ),
    "src/ai_ledger_export_service.py": (
        '"engineering-knowledge-base-ai-ledger-export"',
    ),
    "src/source_fingerprint.py": (
        '"ekb-doc-fp-v1\\n"',
        '"ekb-page-fp-v1\\n"',
        '"ekb-note-fp-v1\\n"',
        '"ekb-evidence-fp-v1\\n"',
    ),
    "scripts/service_manager.py": (
        'TASK_NAME: Final[str] = "EngineeringKnowledgeBase"',
        '/ "EngineeringKnowledgeBase.cmd"',
    ),
    "scripts/check_scale_consistency.py": ("FORMAL_PROJECT_ROOT: Final[Path] = PROJECT_ROOT",),
    "scripts/export_stable_runtime.py": ("PROJECT_ROOT.parent / \"ekb-runtime\" / \"stable\"",),
    "src/hosted_config.py": ('prefix=".ekb-wp1-"', '"engineering-kb.log"'),
    "src/hosted/storage.py": ('prefix=".ekb-seed-"',),
    "src/workspace.css": (".ekb-brand", "--ekb-green"),
    "Dockerfile": (
        "groupadd --gid 10001 ekb",
        "useradd --uid 10001 --gid 10001",
    ),
    "tests/fixtures/reliability_eval/frozen_cases_v1.json": (
        '"eval_id": "EKB-RELIABILITY-FROZEN-EVAL-v1"',
    ),
}


def _read(relative_path: str, *, encoding: str = "utf-8") -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding=encoding)


def _is_historical_brand_path(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/")
    return any(rule.search(normalized) for rule in HISTORICAL_PATH_RULES)


@pytest.mark.parametrize(
    ("relative_path", "title_prefix", "former_name_marker", "history_marker"),
    [
        (relative_path, *markers)
        for relative_path, markers in README_POLICIES.items()
    ],
)
def test_readme_current_identity_and_history_are_separate(
    relative_path: str,
    title_prefix: str,
    former_name_marker: str,
    history_marker: str,
) -> None:
    text = _read(relative_path)
    first_line = text.splitlines()[0]

    assert first_line.startswith(title_prefix)
    assert former_name_marker in text
    assert history_marker in text


def test_streamlit_page_titles_use_current_brand() -> None:
    page_files = [PROJECT_ROOT / "app.py", *sorted((PROJECT_ROOT / "pages").glob("*.py"))]

    for page_file in page_files:
        title_lines = [
            line.strip()
            for line in page_file.read_text(encoding="utf-8").splitlines()
            if "page_title=" in line
        ]
        assert title_lines, f"missing page_title in {page_file.relative_to(PROJECT_ROOT)}"
        for title_line in title_lines:
            assert "Nectivon" in title_line
            assert not OLD_VISIBLE_BRAND.search(title_line)


def test_current_identity_reports_and_demo_use_nectivon() -> None:
    required_markers = {
        "AGENTS.md": (
            "# Nectivon — Codex Instructions",
            "This repository contains Nectivon",
        ),
        "src/workspace_ui.py": (
            "<b>Nectivon 个人知识空间</b>",
            "我的 Nectivon",
        ),
        "src/config.py": ('app_title: str = f"Nectivon v{__version__}"',),
        "src/hosted_api/app.py": ('title="Nectivon Hosted Agent API"',),
        "pages/0_知识Agent.py": (
            'page_title="知识 Agent · Nectivon"',
            "我的 Nectivon",
        ),
        "src/diagnostic_service.py": ("# Nectivon 脱敏诊断报告",),
        "src/evidence_service.py": ("# Nectivon 引用证据包",),
        "src/demo/fixtures.py": ("Nectivon 不会把过期来源静默当作可靠事实",),
        "src/demo/data/demo_catalog.json": (
            "Nectivon 不会把过期来源静默当作可靠事实",
        ),
    }

    for relative_path, markers in required_markers.items():
        text = _read(relative_path)
        for marker in markers:
            assert marker in text, f"missing current brand marker in {relative_path}: {marker}"


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


@pytest.mark.parametrize(
    "relative_path",
    (
        "docs/archive/showcase-v0.3.0/README.md",
        "docs/v0.8.3-fail017-root-cause-report.md",
        "docs/v0.8-eval-results/example.json",
        "docs/adr/001-example.md",
        "docs/zero-batch/README.md",
        "docs/BRAND_NAME_RESEARCH.md",
        "docs/EKB_v0.8.4_RELEASE.md",
        "artifacts/frozen-evidence.json",
        "benchmarks/historical-fixture.json",
        "tests/fixtures/reliability_eval/frozen_cases_v1.json",
        "CHANGELOG.md",
    ),
)
def test_historical_brand_path_families_are_allowlisted(relative_path: str) -> None:
    assert _is_historical_brand_path(relative_path)


@pytest.mark.parametrize(
    "relative_path",
    (
        "README.md",
        "app.py",
        "pages/0_知识Agent.py",
        "src/workspace_ui.py",
        "scripts/service_manager.py",
    ),
)
def test_historical_allowlist_does_not_cover_current_surfaces(relative_path: str) -> None:
    assert not _is_historical_brand_path(relative_path)


def test_stable_technical_identifiers_remain_present() -> None:
    for relative_path, markers in TECHNICAL_COMPATIBILITY_MARKERS.items():
        text = _read(relative_path)
        for marker in markers:
            assert marker in text, f"missing compatibility marker in {relative_path}: {marker}"

def test_nectivon_launchers_are_standalone_after_brand_reconciliation() -> None:
    """The launcher matrix end state: Nectivon-named files carry the logic.

    The M3-era coexistence architecture (Nectivon-named one-call wrappers
    forwarding to old-brand-named logic holders) was formally replaced:
    every capability now lives in a Nectivon-named standalone launcher and
    the old-brand-named files are removed.
    """

    # 2026-09-25 hardening: lifecycle logic lives once in the canonical
    # launcher implementation; Nectivon-named entries are thin wrappers.
    canonical = (PROJECT_ROOT / "_nectivon_launcher.cmd").read_text(encoding="utf-8-sig")
    assert "service_manager.py" in canonical
    assert not OLD_VISIBLE_BRAND.search(canonical)

    for name in NECTIVON_LAUNCHERS:
        path = PROJECT_ROOT / name
        assert path.is_file(), f"missing Nectivon launcher: {name}"
        text = path.read_text(encoding="utf-16" if name.endswith(".vbs") else "utf-8-sig")
        # Wrappers delegate exactly once to the canonical implementation and
        # never to an old-brand wrapper.
        if name in {
            "启动正式版.bat",
            "stop_release.bat",
            "启动测试版8511.bat",
            "停止测试版8511.bat",
            "启动测试版8512.bat",
            "停止测试版8512.bat",
        }:
            assert text.count('call "%~dp0_nectivon_launcher.cmd"') == 1, name
        for removed in FORMALLY_REMOVED_LAUNCHERS:
            assert removed not in text, f"{name} still references {removed}"
        visible_lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip().casefold().startswith(("title ", "echo ", "msgbox"))
        ]
        assert not OLD_VISIBLE_BRAND.search("\n".join(visible_lines)), name
        assert any("Nectivon" in line for line in visible_lines), name

    for name in FORMALLY_REMOVED_LAUNCHERS:
        assert not (PROJECT_ROOT / name).is_file(), f"old-brand launcher must stay removed: {name}"
