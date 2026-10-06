"""Staging instance lifecycle tests: dual local services, zero interference.

Covers the Phase 10B service-manager extension: independent pid/log/runtime
paths, independent ports, stop operations scoped to exactly one instance,
and the in-process staging settings resolver. No network, no AI calls.
"""

from __future__ import annotations

import importlib
import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.service_manager as manager
import src.runtime as runtime
from src import config
from src.config import STAGING_ENV_VAR, staging_settings


@pytest.fixture(autouse=True)
def _reset_active_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Keep management state and default staging writes isolated per test."""

    monkeypatch.setattr(manager, "_ACTIVE_SETTINGS", None)
    monkeypatch.setattr(config, "DEFAULT_STAGING_ROOT", tmp_path / "staging-data")
    yield


def test_expected_python_honors_validated_launcher_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python = tmp_path / "python.exe"
    python.write_bytes(b"")
    monkeypatch.setenv("NECTIVON_PYTHON", str(python))

    assert manager.expected_python() == Path(sys.executable).resolve()


def _spawn_child() -> subprocess.Popen:
    """Spawn a real venv-python child process for lifecycle tests."""

    python = manager.expected_python()
    return subprocess.Popen(
        [str(python), "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _write_record(pid_path: Path, pid: int) -> None:
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(
        json.dumps(
            {
                "pid": pid,
                "project_root": str(manager.PROJECT_ROOT),
                "python": str(manager.expected_python()),
                "port": 8511,
                "started_at": time.time(),
            }
        ),
        encoding="utf-8",
    )


# ------------------------------------------------------------- settings wiring
def test_active_settings_defaults_to_production(monkeypatch) -> None:
    sentinel = object()
    monkeypatch.setattr(manager, "get_settings", lambda: sentinel)
    assert manager.active_settings() is sentinel


def test_active_settings_staging_when_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = staging_settings(tmp_path / "staging")
    monkeypatch.setattr(manager, "_ACTIVE_SETTINGS", staging)
    assert manager.active_settings() is staging


def test_parser_accepts_staging_flag_on_lifecycle_commands() -> None:
    parser = manager.build_parser()
    for command in ("start", "stop", "status"):
        arguments = parser.parse_args([command, "--staging"])
        assert arguments.staging is True
    assert parser.parse_args(["status"]).staging is False


def test_parser_accepts_explicit_staging_root_and_port() -> None:
    parser = manager.build_parser()
    for command in ("start", "stop", "status"):
        arguments = parser.parse_args(
            [
                command,
                "--staging",
                "--staging-root",
                r"D:\tmp\instance-8512",
                "--staging-port",
                "8512",
            ]
        )
        assert arguments.staging_root == Path(r"D:\tmp\instance-8512")
        assert arguments.staging_port == 8512


def test_explicit_staging_root_and_port_build_isolated_settings(
    tmp_path: Path,
) -> None:
    staging = staging_settings(tmp_path / "instance-8512", port=8512)
    assert staging.port == 8512
    assert staging.database_path == (
        tmp_path / "instance-8512" / "data" / "database" / "knowledge.db"
    )
    assert staging.pid_path.name == "engineering-kb-staging.pid.json"
    assert staging.log_path.name == "engineering-kb-staging.log"


def test_explicit_staging_port_never_takes_formal_port(tmp_path: Path) -> None:
    with pytest.raises(config.StorageConfigurationError):
        staging_settings(tmp_path / "bad", port=8501)
    with pytest.raises(config.StorageConfigurationError):
        staging_settings(tmp_path / "bad", port=70000)
    with pytest.raises(config.StorageConfigurationError):
        staging_settings(tmp_path / "bad", port=0)


def test_explicit_staging_root_must_not_overlap_formal_data() -> None:
    from src.config import PROJECT_ROOT

    with pytest.raises(config.StorageConfigurationError):
        staging_settings(PROJECT_ROOT / "data" / "nested-instance", port=8512)


def test_staging_env_pair_selects_explicit_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EKB_STAGING_ROOT", str(tmp_path / "env-instance"))
    monkeypatch.setenv("EKB_STAGING_PORT", "8512")
    staging = staging_settings()
    assert staging.port == 8512
    assert "env-instance" in str(staging.data_dir)


def test_staging_env_root_without_port_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EKB_STAGING_ROOT", str(tmp_path / "env-instance"))
    monkeypatch.delenv("EKB_STAGING_PORT", raising=False)
    with pytest.raises(config.StorageConfigurationError):
        staging_settings()


def test_staging_env_port_must_be_integer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EKB_STAGING_ROOT", str(tmp_path / "env-instance"))
    monkeypatch.setenv("EKB_STAGING_PORT", "not-a-port")
    with pytest.raises(config.StorageConfigurationError):
        staging_settings()


def test_main_rejects_staging_root_without_matching_port(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["service_manager.py", "status", "--staging", "--staging-root", "X"],
    )
    assert manager.main() == 2
    assert "同时提供" in capsys.readouterr().out


def test_runtime_settings_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(STAGING_ENV_VAR, "1")
    staging = config.runtime_settings()
    assert staging.port == 8511
    assert "staging-data" in str(staging.database_path)

    monkeypatch.delenv(STAGING_ENV_VAR)
    production = config.runtime_settings()
    assert production.port == 8501
    assert "staging-data" not in str(production.database_path)


def test_application_settings_uses_staging_when_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(STAGING_ENV_VAR, "1")
    importlib.reload(runtime)  # 绕过 conftest 的 application_settings 防护桩
    try:
        runtime.application_settings.cache_clear()
        settings = runtime.application_settings()
        assert settings.port == 8511
        assert "staging-data" in str(settings.database_path)
    finally:
        runtime.application_settings.cache_clear()
        for handler in logging.getLogger().handlers[:]:
            if "staging" in getattr(handler, "baseFilename", ""):
                logging.getLogger().removeHandler(handler)
        monkeypatch.delenv(STAGING_ENV_VAR)
        importlib.reload(runtime)


# ------------------------------------------------------- explicit instance env
def test_start_service_propagates_explicit_staging_env_to_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The spawned app must resolve the explicit root/port, never the default."""

    staging = staging_settings(tmp_path / "instance", port=8512)
    monkeypatch.setattr(manager, "_ACTIVE_SETTINGS", staging)
    monkeypatch.setattr(manager, "_STAGING_EXPLICIT_ROOT", tmp_path / "instance")
    monkeypatch.setattr(manager, "_STAGING_EXPLICIT_PORT", 8512)
    monkeypatch.setattr(
        manager,
        "detect_state",
        lambda **kwargs: manager.ServiceState("stopped", "服务未运行"),
    )
    fake_python = tmp_path / "python.exe"
    fake_python.write_bytes(b"")
    monkeypatch.setattr(manager, "expected_python", lambda **kwargs: fake_python)
    monkeypatch.setattr(manager, "is_healthy", lambda port, timeout=1.0: True)
    monkeypatch.setattr(manager, "write_pid_record", lambda pid: None)
    monkeypatch.setattr(manager, "_rotate_console_log", lambda path: None)
    # The fake spawned launcher pid answers health on the port.
    monkeypatch.setattr(manager, "_port_owner_pids", lambda port: [4321])

    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 4321

        def poll(self) -> None:
            return None

    def fake_popen(command, *, cwd=None, env=None, **kwargs):
        captured["env"] = env
        return FakeProcess()

    monkeypatch.setattr(manager.subprocess, "Popen", fake_popen)

    assert manager.start_service(open_browser=False) == 0
    child_env = captured["env"]
    assert child_env["EKB_STAGING_INSTANCE"] == "1"
    assert child_env["EKB_STAGING_ROOT"] == str(tmp_path / "instance")
    assert child_env["EKB_STAGING_PORT"] == "8512"


def test_start_service_production_child_has_no_staging_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    production_logs = tmp_path / "logs"
    production_logs.mkdir(parents=True)
    production_ns = SimpleNamespace(
        port=8501,
        logs_dir=production_logs,
        ensure_directories=lambda: None,
    )
    monkeypatch.setattr(manager, "_ACTIVE_SETTINGS", None)
    monkeypatch.setattr(manager, "get_settings", lambda: production_ns)
    monkeypatch.setattr(
        manager,
        "detect_state",
        lambda **kwargs: manager.ServiceState("stopped", "服务未运行"),
    )
    fake_python = tmp_path / "python.exe"
    fake_python.write_bytes(b"")
    monkeypatch.setattr(manager, "expected_python", lambda **kwargs: fake_python)
    monkeypatch.setattr(manager, "is_healthy", lambda port, timeout=1.0: True)
    monkeypatch.setattr(manager, "write_pid_record", lambda pid: None)
    monkeypatch.setattr(manager, "_rotate_console_log", lambda path: None)
    # The fake spawned launcher pid answers health on the port.
    monkeypatch.setattr(manager, "_port_owner_pids", lambda port: [4322])

    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 4322

        def poll(self) -> None:
            return None

    def fake_popen(command, *, cwd=None, env=None, **kwargs):
        captured["env"] = env
        return FakeProcess()

    monkeypatch.setattr(manager.subprocess, "Popen", fake_popen)

    assert manager.start_service(open_browser=False) == 0
    assert "EKB_STAGING_INSTANCE" not in captured["env"]
    assert "EKB_STAGING_ROOT" not in captured["env"]


# --------------------------------------------------------------- pid isolation
def test_staging_pid_record_is_separate_from_production(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = staging_settings(tmp_path / "staging")
    production_pid = tmp_path / "prod" / "runtime" / "engineering-kb.pid.json"
    monkeypatch.setattr(manager, "_ACTIVE_SETTINGS", staging)

    manager.write_pid_record(12345)

    assert staging.pid_path.is_file()
    record = json.loads(staging.pid_path.read_text(encoding="utf-8"))
    assert record["pid"] == 12345
    assert record["port"] == 8511
    assert not production_pid.exists()


def test_stop_staging_never_touches_production_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    child = _spawn_child()
    try:
        production = tmp_path / "prod"
        production_pid = production / "runtime" / "engineering-kb.pid.json"
        _write_record(production_pid, child.pid)
        staging = staging_settings(tmp_path / "staging")
        monkeypatch.setattr(manager, "_ACTIVE_SETTINGS", staging)
        # 本机 8511 可能有真实 staging 服务；本测试只关心 stop 的进程作用域
        monkeypatch.setattr(manager, "is_port_open", lambda port: False)

        # staging 无 PID 记录：stop 不得触碰 production 记录指向的进程
        assert manager.stop_service() == 0
        assert "未运行" in capsys.readouterr().out
        assert manager.is_process_alive(child.pid)
        assert production_pid.is_file()

        # staging 记录指向该进程：stop 只终止记录中的进程，production 记录原样保留
        _write_record(staging.pid_path, child.pid)
        assert manager.stop_service() == 0
        assert not manager.is_process_alive(child.pid)
        assert production_pid.is_file()
        assert json.loads(production_pid.read_text(encoding="utf-8"))["pid"] == child.pid
    finally:
        if manager.is_process_alive(child.pid):
            child.kill()


def test_stop_production_never_touches_staging_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    child = _spawn_child()
    try:
        staging = staging_settings(tmp_path / "staging")
        _write_record(staging.pid_path, child.pid)
        # production 模式（默认），且 production 无 PID 记录
        production = tmp_path / "prod"
        prod_ns = SimpleNamespace(
            port=8501,
            runtime_dir=production / "runtime",
            pid_path=production / "runtime" / "engineering-kb.pid.json",
            logs_dir=production / "logs",
            ensure_directories=lambda: None,
        )
        monkeypatch.setattr(manager, "get_settings", lambda: prod_ns)
        # 本机 8501 可能有真实服务；本测试只关心 stop 的进程作用域
        monkeypatch.setattr(manager, "is_port_open", lambda port: False)

        assert manager.stop_service() == 0
        assert "未运行" in capsys.readouterr().out
        assert manager.is_process_alive(child.pid)
        assert staging.pid_path.is_file()
    finally:
        if manager.is_process_alive(child.pid):
            child.kill()


# --------------------------------------------------------------- port isolation
def test_instances_probe_independent_ports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probed: list[int] = []
    monkeypatch.setattr(
        manager, "is_port_open", lambda port: probed.append(port) or False
    )
    staging = staging_settings(tmp_path / "staging")
    monkeypatch.setattr(manager, "_ACTIVE_SETTINGS", staging)
    manager.detect_state()
    assert probed == [8511]

    probed.clear()
    monkeypatch.setattr(manager, "_ACTIVE_SETTINGS", None)
    production = SimpleNamespace(port=8501, pid_path=tmp_path / "p" / "pid.json")
    monkeypatch.setattr(manager, "get_settings", lambda: production)
    manager.detect_state()
    assert probed == [8501]


# ---------------------------------------------------------------- log isolation
def test_manager_log_goes_to_staging_logs_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = staging_settings(tmp_path / "staging")
    monkeypatch.setattr(manager, "_ACTIVE_SETTINGS", staging)
    manager.LOGGER.handlers.clear()

    manager.configure_manager_logging()

    try:
        assert manager.LOGGER.handlers
        handler_path = Path(manager.LOGGER.handlers[-1].baseFilename)
        assert staging.logs_dir in handler_path.parents
    finally:
        manager.LOGGER.handlers.clear()
