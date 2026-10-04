"""Uninstall stop-helper ownership contracts (v0.8.5 lifecycle hardening).

Covers the incident where Windows Installer deleted the bundled runtime out
from under a still-running Streamlit process.  The helper may only terminate
processes proven (by strict path-based evidence) to belong to THIS install
root; dev venvs, stable runtimes, foreign Python, and user data must never be
matched, killed, or modified.  No MSI is installed or uninstalled here.
"""

from __future__ import annotations

import importlib.util
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging" / "windows"
WIX_NS = {"w": "http://wixtoolset.org/schemas/v4/wxs"}


def load_stop_helper() -> ModuleType:
    path = PACKAGING / "scripts" / "uninstall_stop.py"
    spec = importlib.util.spec_from_file_location("nectivon_uninstall_stop", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = load_stop_helper()
    local_app_data = tmp_path / "LocalAppData"
    local_app_data.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    return module


def write_pid_record(payload: dict[str, object]) -> Path:
    base = Path(os.environ["LOCALAPPDATA"]) / "Nectivon" / "runtime-state"
    base.mkdir(parents=True, exist_ok=True)
    path = base / "engineering-kb.pid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --- pid-record proof ---------------------------------------------------------


def test_pid_record_proof_matches_install_owned_interpreter(stop, monkeypatch) -> None:
    install_root = Path(r"D:\Stub\Install\Nectivon")
    write_pid_record(
        {
            "pid": 4242,
            "python": r"D:\Stub\Install\Nectivon\runtime\python\python.exe",
            "port": 8501,
        }
    )
    monkeypatch.setattr(stop, "_is_alive", lambda pid: pid == 4242)
    monkeypatch.setattr(stop, "_iter_process_images", lambda: [])

    targets = stop.collect_target_pids(install_root)

    assert (4242, "pid-record") in targets


def test_pid_record_pointing_at_dev_venv_is_never_matched(stop, monkeypatch) -> None:
    install_root = Path(r"D:\Stub\Install\Nectivon")
    write_pid_record(
        {"pid": 111, "python": r"X:\Synthetic\dev\.venv\Scripts\python.exe"},
    )
    monkeypatch.setattr(stop, "_is_alive", lambda pid: True)
    monkeypatch.setattr(stop, "_iter_process_images", lambda: [])

    assert stop.collect_target_pids(install_root) == []


def test_stable_runtime_python_is_never_matched(stop, monkeypatch) -> None:
    install_root = Path(r"D:\Stub\Install\Nectivon")
    monkeypatch.setattr(stop, "_iter_process_images", lambda: [
        (222, Path(r"X:\Synthetic\stable\.venv\Scripts\python.exe")),
    ])
    monkeypatch.setattr(stop, "_is_alive", lambda pid: True)

    assert stop.collect_target_pids(install_root) == []


# --- image-path proof ---------------------------------------------------------


def test_image_proof_matches_only_install_root_interpreters(stop, monkeypatch) -> None:
    install_root = Path(r"D:\Stub\Install\Nectivon")
    monkeypatch.setattr(stop, "_iter_process_images", lambda: [
        (100, Path(r"D:\Stub\Install\Nectivon\runtime\python\python.exe")),
        (101, Path(r"D:\Stub\Install\Nectivon\runtime\python\pythonw.exe")),
        (102, Path(r"X:\Synthetic\dev\.venv\Scripts\python.exe")),
        (103, Path(r"C:\Windows\py.exe")),
        (104, Path(r"D:\Stub\Install\Nectivon-evil\python.exe")),
    ])
    monkeypatch.setattr(stop, "_is_alive", lambda pid: True)
    monkeypatch.setattr(stop, "read_pid_record", lambda: None)

    targets = stop.collect_target_pids(install_root)

    assert sorted(pid for pid, _proof in targets) == [100, 101]
    assert all(proof == "image-path" for _pid, proof in targets)


def test_renamed_config_msi_image_is_covered_only_by_pid_record(
    stop, monkeypatch
) -> None:
    """An image renamed into C:\\Config.Msi must be unreachable by path scan
    alone, yet still stoppable through the pid-record proof."""

    install_root = Path(r"D:\Stub\Install\Nectivon")
    write_pid_record(
        {
            "pid": 4242,
            "python": r"D:\Stub\Install\Nectivon\runtime\python\python.exe",
        },
    )
    monkeypatch.setattr(stop, "_iter_process_images", lambda: [
        (4242, Path(r"C:\Config.Msi\4162b26.rbf")),
        (34012, Path(r"C:\Config.Msi\2d662c2.rbf")),
    ])
    monkeypatch.setattr(stop, "_is_alive", lambda pid: pid == 4242)

    targets = stop.collect_target_pids(install_root)

    assert targets == [(4242, "pid-record")]


def test_orphan_config_msi_image_without_record_is_safely_refused(
    stop, monkeypatch
) -> None:
    """No record, image outside install root -> refuse to match (no kill)."""

    install_root = Path(r"D:\Stub\Install\Nectivon")
    monkeypatch.setattr(stop, "_iter_process_images", lambda: [
        (34012, Path(r"C:\Config.Msi\2d662c2.rbf")),
    ])
    monkeypatch.setattr(stop, "_is_alive", lambda pid: True)

    assert stop.collect_target_pids(install_root) == []


def test_sibling_prefix_install_root_is_rejected(stop, monkeypatch) -> None:
    install_root = Path(r"D:\Stub\Install\Nectivon")
    monkeypatch.setattr(stop, "_iter_process_images", lambda: [
        (105, Path(r"D:\Stub\Install\Nectivon-old\runtime\python\python.exe")),
    ])
    monkeypatch.setattr(stop, "_is_alive", lambda pid: True)
    monkeypatch.setattr(stop, "read_pid_record", lambda: None)

    assert stop.collect_target_pids(install_root) == []


# --- user data safety ----------------------------------------------------------


def test_helper_never_deletes_or_writes_any_file(stop, monkeypatch) -> None:
    install_root = Path(r"D:\Stub\Install\Nectivon")
    write_pid_record(
        {"pid": 4242, "python": r"D:\Stub\Install\Nectivon\runtime\python\python.exe"},
    )
    monkeypatch.setattr(stop, "_is_alive", lambda pid: False)

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("uninstall helper must never delete files")

    monkeypatch.setattr(stop.os, "remove", explode)
    monkeypatch.setattr(stop.os, "unlink", explode)
    monkeypatch.setattr(stop.os, "rmdir", explode)
    import shutil

    monkeypatch.setattr(shutil, "rmtree", explode)

    assert stop.collect_target_pids(install_root) == []
    # Running twice must stay read-only as well.
    assert stop.collect_target_pids(install_root) == []


def test_terminate_pid_confirms_exit_bounded(stop, monkeypatch) -> None:
    calls: list[tuple[str, int]] = []
    exit_codes = iter([259, 0, 0, 0])

    class FakeKernel:
        def OpenProcess(self, access, inherit, pid):
            calls.append(("open", pid))
            return 7777

        def TerminateProcess(self, handle, code):
            calls.append(("terminate", handle))
            return 1

        def CloseHandle(self, handle):
            calls.append(("close", handle))
            return 1

        def GetExitCodeProcess(self, handle, pointer):
            pointer._obj.value = next(exit_codes, 0)
            return 1

    monkeypatch.setattr(stop, "kernel32", FakeKernel())
    monkeypatch.setattr(stop.time, "sleep", lambda seconds: None)

    assert stop.terminate_pid(4242) is True
    assert ("terminate", 7777) in calls


def test_main_returns_zero_when_no_targets(stop, monkeypatch) -> None:
    monkeypatch.setattr(stop, "collect_target_pids", lambda root: [])
    assert stop.main(["uninstall_stop.py", r"D:\Stub\Install\Nectivon"]) == 0


def test_main_returns_three_when_termination_fails(stop, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        stop, "collect_target_pids", lambda root: [(4242, "pid-record")]
    )
    monkeypatch.setattr(stop, "terminate_pid", lambda pid: False)
    monkeypatch.setattr(stop, "port_released", lambda port: False)

    assert stop.main(["uninstall_stop.py", r"D:\Stub\Install\Nectivon"]) == 3
    out = capsys.readouterr().out
    assert "pid=4242" in out
    assert "could not be terminated" in out


def test_main_reports_success_when_all_targets_exit(
    stop, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        stop,
        "collect_target_pids",
        lambda root: [(4242, "pid-record"), (34012, "image-path")],
    )
    monkeypatch.setattr(stop, "terminate_pid", lambda pid: True)
    monkeypatch.setattr(stop, "port_released", lambda port: True)

    assert stop.main(["uninstall_stop.py", r"D:\Stub\Install\Nectivon"]) == 0
    out = capsys.readouterr().out
    assert "port 8501 released = True" in out


# --- installer contracts (static inspection only; nothing is installed) --------


def test_wix_uninstall_stops_install_owned_runtime_before_removal() -> None:
    tree = ET.parse(PACKAGING / "wix" / "Package.wxs")
    actions = tree.getroot().findall(".//w:CustomAction", WIX_NS)
    sequences = tree.getroot().findall(
        ".//w:InstallExecuteSequence/w:Custom", WIX_NS
    )

    stop_actions = [a for a in actions if a.attrib.get("Id") == "StopNectivonRuntimeBeforeRemoval"]
    assert len(stop_actions) == 1
    action = stop_actions[0]
    assert action.attrib["Execute"] == "deferred"
    assert action.attrib["Impersonate"] == "yes"
    assert action.attrib["Return"] == "ignore"
    assert "runtime\\python\\python.exe" in action.attrib["ExeCommand"]
    assert "scripts\\uninstall_stop.py" in action.attrib["ExeCommand"]

    stop_sequences = [
        s
        for s in sequences
        if s.attrib.get("Action") == "StopNectivonRuntimeBeforeRemoval"
    ]
    assert len(stop_sequences) == 1
    assert stop_sequences[0].attrib["Before"] == "RemoveFiles"
    assert stop_sequences[0].attrib["Condition"] == 'REMOVE~="ALL"'


def test_wix_uninstall_never_uses_broad_process_kills() -> None:
    text = (PACKAGING / "wix" / "Package.wxs").read_text(encoding="utf-8")

    assert "taskkill" not in text.casefold()
    assert "StopNectivonRuntimeBeforeRemoval" in text


def test_autostart_cleanup_contract_is_unchanged() -> None:
    tree = ET.parse(PACKAGING / "wix" / "Package.wxs")
    actions = tree.getroot().findall(".//w:CustomAction", WIX_NS)
    autostart = [a for a in actions if a.attrib.get("Id") == "RemoveNectivonAutostart"]

    assert len(autostart) == 1
    assert autostart[0].attrib["Return"] == "ignore"
    assert "CurrentVersion\\Run" in autostart[0].attrib["ExeCommand"]
    assert "/v Nectivon /f" in autostart[0].attrib["ExeCommand"]


def test_build_script_stages_the_uninstall_stop_helper() -> None:
    text = (PACKAGING / "build.ps1").read_text(encoding="utf-8")

    assert "uninstall_stop.py" in text


def test_required_files_include_the_uninstall_stop_helper() -> None:
    spec = importlib.util.spec_from_file_location(
        "nectivon_package_tools",
        PACKAGING / "scripts" / "package_tools.py",
    )
    assert spec is not None and spec.loader is not None
    tools = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tools)

    assert "scripts/uninstall_stop.py" in tools.REQUIRED_FILES
