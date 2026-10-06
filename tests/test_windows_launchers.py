"""Windows launcher chain regression tests (2026-09-25 hardening).

Byte-level encoding contracts hold on every platform (they are plain file
reads); real-process smoke tests (cmd.exe, service lifecycle) run only on
Windows. The lifecycle smoke uses an explicit staging root/port pair under
``tmp_path``, so the formal 8501 service and the 8511 default staging
instance are never touched.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

CANONICAL_LAUNCHER = "_nectivon_launcher.cmd"

WRAPPER_CASES = {
    "启动正式版.bat": ("start", "stable", None, None),
    "stop_release.bat": ("stop", "stable", None, None),
    "启动测试版8511.bat": ("start", "staging", "8511", "staging-data"),
    "停止测试版8511.bat": ("stop", "staging", "8511", "staging-data"),
    "启动测试版8512.bat": ("start", "staging", "8512", "staging-data-8512"),
    "停止测试版8512.bat": ("stop", "staging", "8512", "staging-data-8512"),
}

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Windows 启动链专属测试")


def _read_bytes(name: str) -> bytes:
    return (PROJECT_ROOT / name).read_bytes()


def _run_cmd(
    arguments: list[str], env_overrides: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run one cmd.exe command non-interactively with a merged environment."""

    environment = os.environ.copy()
    environment.pop("NECTIVON_PYTHON", None)
    environment["PYTHONUTF8"] = "1"
    if env_overrides:
        for key, value in env_overrides.items():
            if value is None:
                environment.pop(key, None)
            else:
                environment[key] = value
    return subprocess.run(
        ["cmd", "/c", *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        check=False,
    )


# ------------------------------------------------------------ encoding contract
@pytest.mark.parametrize("name", sorted(WRAPPER_CASES))
def test_wrappers_are_ascii_crlf_without_bom(name: str) -> None:
    """Wrappers stay pure ASCII so any console code page parses them."""

    content = _read_bytes(name)
    assert content.startswith(b"@echo off"), f"{name}: 首行必须是 @echo off"
    assert not content.startswith(b"\xef\xbb\xbf"), f"{name}: 不允许 BOM"
    assert all(byte < 128 for byte in content), f"{name}: wrapper 必须保持纯 ASCII"
    assert b"\r\n" in content
    assert content.replace(b"\r\n", b"").find(b"\n") == -1, f"{name}: 存在孤立 LF"


def test_canonical_launcher_encoding_contract() -> None:
    """Impl is pure ASCII (no BOM, CRLF) and never switches the code page.

    ``chcp`` mid-batch makes cmd.exe re-read the file; line positions can
    shift and whole commands vanish or execute as garbage (observed as the
    intermittent launcher outages). All user-facing Chinese therefore comes
    from the Python service manager, which is Unicode-safe on consoles and
    UTF-8 on pipes. This file must stay pure ASCII with no chcp command.
    """

    content = _read_bytes(CANONICAL_LAUNCHER)
    assert content.startswith(b"@echo off")
    assert not content.startswith(b"\xef\xbb\xbf")
    assert all(byte < 128 for byte in content), "impl 必须保持纯 ASCII"
    assert content.replace(b"\r\n", b"").find(b"\n") == -1
    for line in content.split(b"\r\n"):
        assert not line.strip().startswith(b"chcp "), f"不允许 chcp 命令: {line!r}"
    assert b"service_manager" in content


def test_silent_vbs_keeps_utf16_and_stable_contract() -> None:
    content_bytes = _read_bytes("静默启动Nectivon.vbs")
    assert content_bytes.startswith(b"\xff\xfe"), "VBS 必须是 UTF-16LE（单 BOM）"
    text = content_bytes.decode("utf-16")
    assert "ekb-runtime" in text or "EKB_STABLE_RUNTIME_ROOT" in text
    assert text.count("shell.Run") == 1


# ---------------------------------------------------------- delegation contract
@pytest.mark.parametrize("name", sorted(WRAPPER_CASES))
def test_wrapper_declares_expected_instance(name: str) -> None:
    action, instance, port, staging_root = WRAPPER_CASES[name]
    text = _read_bytes(name).decode("ascii")
    assert f'set "NECTIVON_ACTION={action}"' in text
    assert f'set "NECTIVON_INSTANCE={instance}"' in text
    assert text.count('call "%~dp0_nectivon_launcher.cmd"') == 1
    if instance == "staging":
        assert staging_root is not None and staging_root in text
        assert port is not None and f'set "NECTIVON_STAGING_PORT={port}"' in text
    if instance == "stable":
        assert 'set "NECTIVON_RUNTIME_ROOT=%~dp0"' in text
        assert "%~dp0" in text


# ------------------------------------------------------------ real error paths
@WINDOWS_ONLY
def test_impl_without_configuration_fails_readably() -> None:
    result = _run_cmd([CANONICAL_LAUNCHER])
    assert result.returncode == 9
    assert "CONFIG ERROR" in result.stdout


@WINDOWS_ONLY
def test_impl_missing_python_fails_readably(tmp_path: Path) -> None:
    result = _run_cmd(
        [CANONICAL_LAUNCHER],
        {
            "NECTIVON_ACTION": "start",
            "NECTIVON_INSTANCE": "stable",
            "NECTIVON_RUNTIME_ROOT": str(tmp_path),
        },
    )
    assert result.returncode == 3
    assert "Python interpreter not found" in result.stdout
    assert str(tmp_path) in result.stdout


@WINDOWS_ONLY
def test_impl_missing_manager_fails_readably(tmp_path: Path) -> None:
    scripts_dir = tmp_path / ".venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "python.exe").write_bytes(b"")
    result = _run_cmd(
        [CANONICAL_LAUNCHER],
        {
            "NECTIVON_ACTION": "start",
            "NECTIVON_INSTANCE": "stable",
            "NECTIVON_RUNTIME_ROOT": str(tmp_path),
        },
    )
    assert result.returncode == 4
    assert "service_manager.py" in result.stdout


@WINDOWS_ONLY
def test_formal_wrapper_declares_local_runtime_precheck() -> None:
    """Double-click entry performs local Python and dependency checks."""

    text = _read_bytes("启动正式版.bat").decode("ascii")
    assert 'if exist "%~dp0.venv\\Scripts\\python.exe"' in text
    assert "Python 3.11 or newer" in text
    assert "Runtime dependencies are incomplete" in text


@WINDOWS_ONLY
def test_staging_wrapper_without_port_fails_closed(tmp_path: Path) -> None:
    result = _run_cmd(
        [CANONICAL_LAUNCHER],
        {
            "NECTIVON_ACTION": "start",
            "NECTIVON_INSTANCE": "staging",
            "NECTIVON_RUNTIME_ROOT": str(PROJECT_ROOT),
            "NECTIVON_STAGING_PORT": None,
            "NECTIVON_STAGING_ROOT": str(tmp_path),
        },
    )
    assert result.returncode == 9


# ------------------------------------------------------- real lifecycle smoke
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_port(port: int, *, expected: bool, timeout: float = 45.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            opened = probe.connect_ex(("127.0.0.1", port)) == 0
        if opened == expected:
            return True
        time.sleep(0.4)
    return False


@WINDOWS_ONLY
def test_full_lifecycle_smoke_on_isolated_instance(tmp_path: Path) -> None:
    """Cold start → reuse → stop on an explicit 8512-style test instance.

    The staging root deliberately contains Chinese characters and spaces so
    the whole cmd → impl → manager → streamlit chain is exercised under the
    worst-case quoting. The formal 8501 service is never touched.
    """

    staging_root = tmp_path / "测试 实例" / "staging"
    port = _free_port()
    base_env = {
        "NECTIVON_ACTION": "start",
        "NECTIVON_INSTANCE": "staging",
        "NECTIVON_RUNTIME_ROOT": str(PROJECT_ROOT),
        "NECTIVON_STAGING_ROOT": str(staging_root),
        "NECTIVON_STAGING_PORT": str(port),
        "NECTIVON_NO_BROWSER": "1",
        "NECTIVON_PYTHON": sys.executable,
    }

    result = _run_cmd(
        [CANONICAL_LAUNCHER], {**base_env, "NECTIVON_ACTION": "start"}
    )
    try:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "启动成功" in result.stdout
        assert f"127.0.0.1:{port}" in result.stdout
        assert _wait_port(port, expected=True)

        # Child app must resolve the explicit instance, not default staging.
        # (The manager writes the pid record into the explicit root; the
        # child-side env propagation is covered by a dedicated unit test in
        # test_service_manager_staging.py, because Streamlit's /_stcore/health
        # does not execute the app script and would never create its log.)
        record_path = staging_root / "runtime" / "engineering-kb-staging.pid.json"
        assert record_path.is_file(), "子进程未写入显式实例的 PID 记录"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        assert record["port"] == port

        # Second start must reuse the healthy instance, never double-spawn.
        reuse = _run_cmd(
            [CANONICAL_LAUNCHER], {**base_env, "NECTIVON_ACTION": "start"}
        )
        assert reuse.returncode == 0, reuse.stdout + reuse.stderr
        assert "已经运行" in reuse.stdout

        status = _run_cmd(
            [CANONICAL_LAUNCHER], {**base_env, "NECTIVON_ACTION": "status"}
        )
        assert status.returncode == 0, status.stdout + status.stderr
        assert "正常运行" in status.stdout
    finally:
        stop = _run_cmd(
            [CANONICAL_LAUNCHER], {**base_env, "NECTIVON_ACTION": "stop"}
        )
        stopped_cleanly = stop.returncode == 0

    assert stopped_cleanly, stop.stdout + stop.stderr
    assert _wait_port(port, expected=False), "停止后端口仍未释放"

    final_status = _run_cmd(
        [CANONICAL_LAUNCHER], {**base_env, "NECTIVON_ACTION": "status"}
    )
    assert final_status.returncode == 0
    assert "未运行" in final_status.stdout
