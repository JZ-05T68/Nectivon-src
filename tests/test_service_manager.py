"""Tests for safe local lifecycle detection without killing real processes."""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import scripts.service_manager as manager
from src.config import OfficialEndpointError


def make_settings(tmp_path: Path, port: int) -> SimpleNamespace:
    """Return the small settings surface used by lifecycle detection."""

    runtime_dir = tmp_path / "runtime"
    logs_dir = tmp_path / "logs"

    def ensure_directories() -> None:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)

    return SimpleNamespace(
        port=port,
        runtime_dir=runtime_dir,
        pid_path=runtime_dir / "engineering-kb.pid.json",
        logs_dir=logs_dir,
        ensure_directories=ensure_directories,
    )


def test_stale_pid_is_detected_and_cleaned(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path, 49321)
    settings.ensure_directories()
    settings.pid_path.write_text(json.dumps({"pid": 999999}), encoding="utf-8")
    monkeypatch.setattr(manager, "get_settings", lambda: settings)
    monkeypatch.setattr(manager, "is_process_alive", lambda pid: False)
    monkeypatch.setattr(manager, "is_port_open", lambda port: False)

    state = manager.detect_state()

    assert state.code == "abnormal"
    assert "过期 PID" in state.detail
    assert not settings.pid_path.exists()


def test_foreign_port_listener_is_not_treated_as_our_service(
    tmp_path: Path, monkeypatch
) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        settings = make_settings(tmp_path, port)
        monkeypatch.setattr(manager, "get_settings", lambda: settings)

        state = manager.detect_state()

    assert state.code == "port_occupied"
    assert "其他程序" in state.detail


def test_missing_virtual_environment_returns_clear_failure(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    settings = make_settings(tmp_path, 49322)
    monkeypatch.setattr(manager, "get_settings", lambda: settings)
    monkeypatch.setattr(
        manager,
        "detect_state",
        lambda: manager.ServiceState("stopped", "服务未运行"),
    )
    monkeypatch.setattr(manager, "expected_python", lambda: tmp_path / "missing.exe")

    assert manager.start_service(open_browser=False) == 3
    assert "私有 Python runtime" in capsys.readouterr().out


def test_pid_reuse_refuses_to_stop_unrelated_process(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    settings = make_settings(tmp_path, 49323)
    settings.ensure_directories()
    settings.pid_path.write_text(json.dumps({"pid": 12345}), encoding="utf-8")
    monkeypatch.setattr(manager, "get_settings", lambda: settings)
    monkeypatch.setattr(manager, "is_process_alive", lambda pid: True)
    monkeypatch.setattr(manager, "record_matches_process", lambda record: False)

    assert manager.stop_service() == 3
    assert "拒绝停止" in capsys.readouterr().out
    assert not settings.pid_path.exists()


def test_duplicate_start_reuses_running_instance(monkeypatch, capsys) -> None:
    settings = SimpleNamespace(port=8501)
    monkeypatch.setattr(manager, "get_settings", lambda: settings)
    monkeypatch.setattr(
        manager,
        "detect_state",
        lambda: manager.ServiceState("running", "服务正常运行", 24680),
    )

    assert manager.start_service(open_browser=False) == 0
    assert "Nectivon 已经运行" in capsys.readouterr().out


def test_cli_reports_invalid_formal_endpoint(monkeypatch, capsys) -> None:
    def invalid_settings():
        raise OfficialEndpointError(
            "正式服务端点必须为 127.0.0.1:8501；收到端口 49344。"
        )

    monkeypatch.setattr(manager, "configure_manager_logging", lambda: None)
    monkeypatch.setattr(manager, "get_settings", invalid_settings)
    monkeypatch.setattr(sys, "argv", ["service_manager.py", "start", "--no-browser"])

    assert manager.main() == 2
    output = capsys.readouterr().out
    assert "正式服务配置错误" in output
    assert "127.0.0.1:8501" in output


# --- v0.8.5 BUG B: owned abnormal instances must never double-spawn ----------


def _make_python_file(tmp_path: Path) -> Path:
    python = tmp_path / "python.exe"
    python.write_bytes(b"stub")
    return python


def _patch_start_environment(monkeypatch, tmp_path: Path) -> list:
    """Patch the spawn path so start_service never touches a real process."""

    spawned: list[list[str]] = []

    class FakePopen:
        def __init__(self, command, *args, **kwargs):
            spawned.append(list(command))
            self.pid = 24680

        def poll(self):
            return None

    monkeypatch.setattr(manager.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(manager, "expected_python", lambda: _make_python_file(tmp_path))
    monkeypatch.setattr(manager, "is_healthy", lambda port: True)
    # The spawned launcher (pid 24680) itself answers health in this fake.
    monkeypatch.setattr(manager, "_port_owner_pids", lambda port: [24680])
    monkeypatch.setattr(manager, "_rotate_console_log", lambda path: None)
    return spawned


def test_owned_abnormal_instance_is_stopped_then_spawned_once(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    settings = make_settings(tmp_path, 49325)
    settings.ensure_directories()
    settings.pid_path.write_text(json.dumps({"pid": 13579}), encoding="utf-8")
    monkeypatch.setattr(manager, "get_settings", lambda: settings)
    monkeypatch.setattr(
        manager,
        "detect_state",
        lambda: manager.ServiceState("abnormal", "进程存在但健康检查失败", 13579),
    )
    monkeypatch.setattr(manager, "read_pid_record", lambda: {"pid": 13579})
    monkeypatch.setattr(manager, "record_matches_process", lambda record: True)
    tree_kills: list[int] = []
    monkeypatch.setattr(
        manager,
        "_terminate_process_tree",
        lambda pid: tree_kills.append(pid) or True,
    )
    monkeypatch.setattr(manager, "is_port_open", lambda port: False)
    spawned = _patch_start_environment(monkeypatch, tmp_path)

    assert manager.start_service(open_browser=False) == 0
    assert len(spawned) == 1
    assert tree_kills == [13579]
    out = capsys.readouterr().out
    assert "安全停止旧实例" in out


def test_owned_abnormal_stop_failure_refuses_to_spawn(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    settings = make_settings(tmp_path, 49326)
    settings.ensure_directories()
    monkeypatch.setattr(manager, "get_settings", lambda: settings)
    monkeypatch.setattr(
        manager,
        "detect_state",
        lambda: manager.ServiceState("abnormal", "进程存在但健康检查失败", 13579),
    )
    # PID was reused by another program: the helper must fail closed.
    monkeypatch.setattr(manager, "read_pid_record", lambda: {"pid": 13579})
    monkeypatch.setattr(manager, "record_matches_process", lambda record: False)
    monkeypatch.setattr(manager, "is_port_open", lambda port: False)
    spawned = _patch_start_environment(monkeypatch, tmp_path)

    assert manager.start_service(open_browser=False) == 7
    assert spawned == []
    assert "旧实例停止失败" in capsys.readouterr().out


def test_owned_abnormal_port_still_occupied_refuses(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    settings = make_settings(tmp_path, 49327)
    settings.ensure_directories()
    monkeypatch.setattr(manager, "get_settings", lambda: settings)
    monkeypatch.setattr(
        manager,
        "detect_state",
        lambda: manager.ServiceState("abnormal", "进程存在但健康检查失败", 13579),
    )
    monkeypatch.setattr(manager, "read_pid_record", lambda: {"pid": 13579})
    monkeypatch.setattr(manager, "record_matches_process", lambda record: True)
    monkeypatch.setattr(manager, "_terminate_process_tree", lambda pid: True)
    monkeypatch.setattr(manager, "is_port_open", lambda port: True)
    spawned = _patch_start_environment(monkeypatch, tmp_path)

    assert manager.start_service(open_browser=False) == 2
    assert spawned == []
    assert "未释放" in capsys.readouterr().out
