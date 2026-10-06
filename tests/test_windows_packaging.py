"""Windows installer contracts without building or installing an MSI."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import scripts.service_manager as manager
import src.config as config

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging" / "windows"
WIX_NS = {"w": "http://wixtoolset.org/schemas/v4/wxs"}


def load_script(name: str) -> ModuleType:
    path = PACKAGING / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"nectivon_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_staging(tmp_path: Path) -> Path:
    root = tmp_path / "staging"
    for relative in (
        "app/app.py",
        "app/src/__init__.py",
        "runtime/python/python.exe",
        "runtime/python/pythonw.exe",
        "runtime/python/msvcp140.dll",
        "runtime/python/msvcp140_1.dll",
        "scripts/service_manager.py",
        "scripts/launch.vbs",
        "scripts/cleanup_user_data.py",
        "scripts/uninstall_stop.py",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder\n", encoding="utf-8")
    (root / "VERSION").write_text("0.8.5\n", encoding="utf-8")
    return root


def test_installed_storage_root_is_separate_from_program_files(tmp_path: Path) -> None:
    root = tmp_path / "LocalAppData" / "Nectivon"
    layout = config.installed_settings_layout(root)

    assert layout["data_dir"] == root / "data"
    assert layout["config_dir"] == root / "config"
    assert layout["logs_dir"] == root / "logs"
    assert layout["backups_dir"] == root / "backups"
    assert layout["cache_dir"] == root / "cache"
    assert layout["runtime_dir"] == root / "runtime-state"


def test_dev_storage_root_remains_repository_local() -> None:
    settings = config.Settings(_env_file=None)

    assert settings.data_dir == config.PROJECT_ROOT / "data"
    assert settings.runtime_dir == config.PROJECT_ROOT / "runtime"


def test_installed_distribution_and_config_path_detection(tmp_path: Path) -> None:
    install = tmp_path / "Programs" / "Nectivon"
    app = install / "app"
    (install / "runtime" / "python").mkdir(parents=True)
    app.mkdir()
    (install / "VERSION").write_text("0.8.5", encoding="utf-8")
    local = tmp_path / "LocalAppData"

    assert config.is_installed_distribution(app)
    assert config.settings_env_path(app, local) == local / "Nectivon" / "config" / ".env"
    assert config.settings_env_path(tmp_path / "dev", local) == tmp_path / "dev" / ".env"


def test_formal_settings_switch_to_installed_writable_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "LocalAppData" / "Nectivon"
    monkeypatch.setattr(
        config,
        "is_installed_distribution",
        lambda project_root=config.PROJECT_ROOT: True,
    )
    monkeypatch.setattr(config, "user_data_root", lambda local_app_data=None: root)
    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
    finally:
        config.get_settings.cache_clear()

    assert settings.data_dir == root / "data"
    assert settings.config_dir == root / "config"
    assert settings.logs_dir == root / "logs"
    assert settings.runtime_dir == root / "runtime-state"


def test_runtime_python_discovery_never_uses_venv_in_installed_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(manager, "is_installed_distribution", lambda root: True)

    expected = manager.PROJECT_ROOT.parent / "runtime" / "python" / "python.exe"
    assert manager.expected_python() == expected
    assert ".venv" not in str(expected)


def test_launcher_contract_is_windowless_and_uses_launch_command() -> None:
    launcher = (PACKAGING / "scripts" / "launch.vbs").read_text(encoding="utf-8")

    assert "pythonw.exe" in launcher
    assert '" launch"' in launcher
    assert "shell.Run command, 0, False" in launcher


def test_service_command_is_loopback_8501_and_opens_existing_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(port=8501)
    opened: list[str] = []
    monkeypatch.setattr(manager, "get_settings", lambda: settings)
    monkeypatch.setattr(
        manager,
        "detect_state",
        lambda: manager.ServiceState("running", "服务正常运行", 24680),
    )
    monkeypatch.setattr(manager.webbrowser, "open", opened.append)

    assert manager.start_service(open_browser=True) == 0
    assert opened == ["http://127.0.0.1:8501"]


def test_service_launch_arguments_explicitly_disable_lan_binding() -> None:
    source = (ROOT / "scripts" / "service_manager.py").read_text(encoding="utf-8")

    assert '"--server.address",\n        "127.0.0.1"' in source
    assert '"--server.port",\n        str(settings.port)' in source
    assert '"--server.headless",\n        "true"' in source


def test_runtime_lock_is_exact_has_ocr_and_omits_development_dependencies() -> None:
    lines = [
        line.strip()
        for line in (PACKAGING / "runtime-requirements.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.startswith("#")
    ]
    lowered = "\n".join(lines).casefold()

    assert all("==" in line for line in lines)
    assert "rapidocr" not in lowered
    assert "onnxruntime" not in lowered
    assert "rapidfuzz" not in lowered
    assert "pytest" not in lowered
    assert "ruff" not in lowered


def test_product_runtime_has_no_rapidfuzz_import() -> None:
    candidates = [
        ROOT / "app.py",
        *sorted((ROOT / "pages").glob("*.py")),
        *sorted((ROOT / "src").rglob("*.py")),
    ]
    imports = "\n".join(path.read_text(encoding="utf-8") for path in candidates)

    assert "import rapidfuzz" not in imports.casefold()
    assert "from rapidfuzz" not in imports.casefold()


def test_wix_package_is_x64_per_user_with_fixed_upgrade_contract() -> None:
    tree = ET.parse(PACKAGING / "wix" / "Package.wxs")
    package = tree.getroot().find("w:Package", WIX_NS)
    assert package is not None

    assert package.attrib["Version"] == "0.8.5"
    assert package.attrib["Scope"] == "perUser"
    assert package.attrib["ProductCode"] == "*"
    assert package.attrib["UpgradeCode"] == "{76EF2892-AB92-4BC3-8F22-D4EA00C55B5B}"
    assert package.find("w:MajorUpgrade", WIX_NS) is not None
    assert "LocalAppDataFolder" in (PACKAGING / "wix" / "Package.wxs").read_text(encoding="utf-8")


def test_wix_shortcut_and_uninstall_autostart_cleanup_contract() -> None:
    tree = ET.parse(PACKAGING / "wix" / "Package.wxs")
    shortcut = tree.getroot().find(".//w:Shortcut", WIX_NS)
    action = tree.getroot().find(".//w:CustomAction", WIX_NS)
    sequence = tree.getroot().find(".//w:InstallExecuteSequence/w:Custom", WIX_NS)

    assert shortcut is not None and "wscript.exe" in shortcut.attrib["Target"]
    assert shortcut.attrib["Name"] == "Nectivon"
    assert action is not None and action.attrib["Id"] == "RemoveNectivonAutostart"
    assert action.attrib["Directory"] == "SystemFolder"
    assert action.attrib["Execute"] == "deferred"
    assert action.attrib["Impersonate"] == "yes"
    assert action.attrib["Return"] == "ignore"
    assert action.attrib["ExeCommand"] == (
        'reg.exe delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" '
        "/v Nectivon /f"
    )
    assert sequence is not None
    assert sequence.attrib == {
        "Action": "RemoveNectivonAutostart",
        "Before": "RemoveFiles",
        'Condition': 'REMOVE~="ALL"',
    }


def test_normal_uninstall_preserves_user_data_by_ownership() -> None:
    wix = (PACKAGING / "wix" / "Package.wxs").read_text(encoding="utf-8")

    assert "NectivonProgramMenuFolder" in wix
    assert "INSTALLFOLDER" in wix
    assert "LocalAppDataFolder" in wix
    assert "runtime-state" not in wix
    assert "knowledge.db" not in wix


def test_cleanup_targets_are_exact_and_escape_is_rejected(tmp_path: Path) -> None:
    cleanup = load_script("cleanup_user_data")
    root = cleanup.expected_user_root(tmp_path / "LocalAppData")
    targets = cleanup.cleanup_targets(root)

    assert {path.name for path in targets} == cleanup.ALLOWED_CHILDREN
    assert all(path.parent == root for path in targets)
    with pytest.raises(ValueError, match="escapes"):
        cleanup.validate_cleanup_target(root.parent / "OtherApp", root)
    with pytest.raises(ValueError, match="allowlisted"):
        cleanup.validate_cleanup_target(root / "unknown", root)


def test_cleanup_rejects_redirected_user_root(tmp_path: Path) -> None:
    cleanup = load_script("cleanup_user_data")
    local = tmp_path / "LocalAppData"
    outside = tmp_path / "Outside"
    local.mkdir()
    outside.mkdir()
    redirected = local / "Nectivon"
    try:
        redirected.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable for this Windows account")
    if not redirected.is_symlink():
        # Some sandboxed Windows tokens return success while silently
        # materialising a plain file instead of a reparse point.
        pytest.skip("symlink creation silently degraded on this Windows account")

    with pytest.raises(ValueError, match="symlink or junction"):
        cleanup.cleanup_targets(redirected)


def test_cleanup_deletes_only_allowlisted_children(tmp_path: Path) -> None:
    cleanup = load_script("cleanup_user_data")
    root = cleanup.expected_user_root(tmp_path / "LocalAppData")
    sibling = root.parent / "OtherApp"
    sibling.mkdir(parents=True)
    (sibling / "keep.txt").write_text("keep", encoding="utf-8")
    for target in cleanup.cleanup_targets(root):
        target.mkdir(parents=True)
        (target / "remove.txt").write_text("remove", encoding="utf-8")

    removed = cleanup.delete_user_data(root)

    assert len(removed) == len(cleanup.ALLOWED_CHILDREN)
    assert (sibling / "keep.txt").is_file()


def test_legacy_detection_never_migrates(tmp_path: Path) -> None:
    legacy = load_script("legacy_data")
    database = tmp_path / "legacy" / "database" / "knowledge.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"synthetic")

    assert legacy.legacy_data_status(tmp_path / "legacy") == "LEGACY_DATA_FOUND"
    assert database.read_bytes() == b"synthetic"


def test_staging_inventory_accepts_minimum_valid_layout(tmp_path: Path) -> None:
    tools = load_script("package_tools")
    staging = make_staging(tmp_path)

    assert tools.validate_staging(staging) == ()


def test_staging_inventory_rejects_secrets_user_db_and_dev_files(tmp_path: Path) -> None:
    tools = load_script("package_tools")
    staging = make_staging(tmp_path)
    for relative in (".env", ".git/config", "tests/test_bad.py", "data/database/knowledge.db"):
        path = staging / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("secret-like payload", encoding="utf-8")

    errors = "\n".join(tools.validate_staging(staging))

    assert ".env" in errors
    assert ".git" in errors
    assert "tests" in errors
    assert "knowledge.db" in errors


def test_runtime_manifest_is_traceable_and_hashed(tmp_path: Path) -> None:
    tools = load_script("package_tools")
    staging = make_staging(tmp_path)
    output = staging / "runtime-manifest.json"
    manifest = tools.write_manifest(
        staging,
        output,
        source_git_sha="670d234156a04c5f669eac4914fcbfbff7734bcb",
        source_tree_dirty=True,
        dependency_lock_sha256="a" * 64,
        msvc_runtime_version="14.51.36247.0",
        msvc_redist_sha256="b" * 64,
        timestamp="2026-09-22T00:00:00+00:00",
    )

    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded == manifest
    assert loaded["version"] == "0.8.5"
    assert loaded["arch"] == "x86_64"
    assert loaded["python_version"] == "3.11.9"
    assert loaded["msvc_runtime_version"] == "14.51.36247.0"
    assert loaded["msvc_redist_sha256"] == "b" * 64
    assert loaded["source_tree_dirty"] is True
    assert loaded["signing_state"] == "UNSIGNED_ENGINEERING_BUILD"
    assert all(len(item["sha256"]) == 64 for item in loaded["files"])


def test_generated_wix_inventory_has_one_component_per_file(tmp_path: Path) -> None:
    tools = load_script("package_tools")
    staging = make_staging(tmp_path)
    output = tmp_path / "Files.generated.wxs"

    tools.write_wix_files(staging, output)

    tree = ET.parse(output)
    components = tree.getroot().findall(".//w:Component", WIX_NS)
    assert len(components) == len(tools.inventory(staging))
    assert all(component.attrib["Guid"] == "*" for component in components)


def test_build_script_rejects_dev_venv_and_pins_runtime_inputs() -> None:
    script = (PACKAGING / "build.ps1").read_text(encoding="utf-8")

    assert ".venv is audit-only" in script
    assert "python-3.11.9-embed-amd64.zip" in script
    assert "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b" in script
    assert "843068991daaa1f73ad9f6239bce4d0f6a07a51f18c37ea2a867e9beca71295c" in (
        PACKAGING / "toolchain.json"
    ).read_text(encoding="utf-8")
    assert "msvcp140.dll" in script
    assert "Get-AuthenticodeSignature" in script
    assert "670d234156a04c5f669eac4914fcbfbff7734bcb" in script
    assert "-arch x64" in script
    assert '$machine -ne "X64"' in script
    assert 'machine -notin @("AMD64", "x86_64")' in script
    assert "UNSIGNED ENGINEERING BUILD" in script


def test_build_allowlist_excludes_development_and_user_directories() -> None:
    script = (PACKAGING / "build.ps1").read_text(encoding="utf-8")
    tracked_line = next(line for line in script.splitlines() if "ls-files --" in line)

    assert "app.py pages src .streamlit/config.toml" in tracked_line
    for excluded in (" data", " backups", " artifacts", " tests", " experiments"):
        assert excluded not in tracked_line


def test_signing_is_disabled_by_default_but_has_explicit_future_path() -> None:
    script = (PACKAGING / "build.ps1").read_text(encoding="utf-8")

    assert "[bool]$SigningEnabled = $false" in script
    assert "signtool.exe" in script
    assert "SigningCertificateThumbprint" in script


def test_no_generated_installer_or_package_cache_is_tracked() -> None:
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--",
            "*.msi",
            "packaging/windows/dist",
            "packaging/windows/build",
            "packaging/windows/cache",
        ],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    )
    tracked = {line for line in result.stdout.splitlines() if line}

    assert not tracked
