"""R5 closure: explicit, non-silent port-conflict handling on start.

Covers the 2026-09-27 R4 finding: a bash-resident Streamlit holding the
staging port made ``start`` ambiguous - the user could believe a new
instance was started while the browser silently reached an old process.
The manager must now distinguish:

A) a healthy Streamlit already on the port -> report "already running",
   open the existing instance, warn the code version cannot be verified;
B) a foreign/unknown listener -> precise refusal with the owner pid,
   an explicit "will not kill" statement, exit code 2;

and must never spawn a second instance while the port stays occupied.
"""

from __future__ import annotations

import socket
from pathlib import Path
from types import SimpleNamespace

import scripts.service_manager as manager


def make_settings(tmp_path: Path, port: int) -> SimpleNamespace:
    """Return the small settings surface used by lifecycle tests."""

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


def _port_occupied(monkeypatch, settings: SimpleNamespace) -> None:
    monkeypatch.setattr(manager, "get_settings", lambda: settings)
    monkeypatch.setattr(
        manager,
        "detect_state",
        lambda **kwargs: manager.ServiceState("port_occupied", "端口被占用"),
    )


def _forbid_spawn(monkeypatch) -> None:
    def no_popen(*args, **kwargs):
        raise AssertionError("start must not spawn while the port is occupied")

    monkeypatch.setattr(manager.subprocess, "Popen", no_popen)


# ------------------------------------------------------------------- case A
def test_start_reports_existing_streamlit_without_respawning(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    settings = make_settings(tmp_path, 8591)
    _port_occupied(monkeypatch, settings)
    monkeypatch.setattr(manager, "is_healthy", lambda port, timeout=1.0: True)
    monkeypatch.setattr(
        manager,
        "_describe_port_owner",
        lambda port: (4321, r"D:\somewhere\python.exe"),
    )
    _forbid_spawn(monkeypatch)
    opened: list[str] = []
    monkeypatch.setattr(
        manager.webbrowser, "open", lambda url: opened.append(url) or True
    )

    assert manager.start_service(open_browser=True) == 0

    out = capsys.readouterr().out
    assert "已在运行" in out
    assert "未重复启动" in out
    assert "4321" in out
    assert "可能不是最新代码" in out
    assert opened == ["http://127.0.0.1:8591"]


def test_start_existing_streamlit_without_browser_still_reports(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    settings = make_settings(tmp_path, 8591)
    _port_occupied(monkeypatch, settings)
    monkeypatch.setattr(manager, "is_healthy", lambda port, timeout=1.0: True)
    monkeypatch.setattr(manager, "_describe_port_owner", lambda port: (None, ""))
    _forbid_spawn(monkeypatch)
    opened: list[str] = []
    monkeypatch.setattr(
        manager.webbrowser, "open", lambda url: opened.append(url) or True
    )

    assert manager.start_service(open_browser=False) == 0

    out = capsys.readouterr().out
    assert "已在运行" in out
    assert "未能识别" in out
    assert opened == []


# ------------------------------------------------------------------- case B
def test_start_refuses_unknown_listener_without_killing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    settings = make_settings(tmp_path, 8592)
    _port_occupied(monkeypatch, settings)
    monkeypatch.setattr(manager, "is_healthy", lambda port, timeout=1.0: False)
    monkeypatch.setattr(
        manager,
        "_describe_port_owner",
        lambda port: (4322, r"C:\Tools\other.exe"),
    )
    _forbid_spawn(monkeypatch)

    assert manager.start_service(open_browser=False) == 2

    out = capsys.readouterr().out
    assert "已被其他进程占用" in out
    assert "未启动" in out
    assert "4322" in out
    assert "不会自动结束" in out


def test_real_dummy_listener_is_refused(tmp_path: Path, monkeypatch, capsys) -> None:
    """Integration: a real listening socket yields exit 2 and no spawn."""

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        settings = make_settings(tmp_path, port)
        _port_occupied(monkeypatch, settings)
        monkeypatch.setattr(manager, "is_healthy", lambda p, timeout=1.0: False)
        monkeypatch.setattr(manager, "_describe_port_owner", lambda p: (None, ""))
        _forbid_spawn(monkeypatch)

        assert manager.start_service(open_browser=False) == 2

    assert "已被其他进程占用" in capsys.readouterr().out


# ------------------------------------------------------- owner identification
def test_describe_port_owner_parses_netstat(tmp_path: Path, monkeypatch) -> None:
    output = "\n".join(
        [
            "",
            "活动连接",
            "  协议  本地地址          外部地址        状态           PID",
            "  TCP    127.0.0.1:8593         0.0.0.0:0              LISTENING       4323",
            "  TCP    127.0.0.1:8594         0.0.0.0:0              LISTENING       4324",
        ]
    )
    monkeypatch.setattr(
        manager.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=output),
    )
    monkeypatch.setattr(
        manager, "process_executable", lambda pid: Path(r"D:\x\python.exe")
    )

    assert manager._describe_port_owner(8593) == (4323, str(Path(r"D:\x\python.exe")))
    assert manager._describe_port_owner(8594) == (4324, str(Path(r"D:\x\python.exe")))
    assert manager._describe_port_owner(9999) == (None, "")


def test_describe_port_owner_ignores_similar_port_suffix(
    tmp_path: Path, monkeypatch
) -> None:
    output = (
        "  TCP    127.0.0.1:18512       0.0.0.0:0              LISTENING       4325\n"
    )
    monkeypatch.setattr(
        manager.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=output),
    )
    assert manager._describe_port_owner(8512) == (None, "")


def test_describe_port_owner_survives_netstat_failure(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise OSError("netstat unavailable")

    monkeypatch.setattr(manager.subprocess, "run", boom)
    assert manager._describe_port_owner(8595) == (None, "")


def test_describe_port_owner_handles_unparsable_pid(monkeypatch) -> None:
    output = (
        "  TCP    127.0.0.1:8596       0.0.0.0:0              LISTENING       not-a-pid\n"
    )
    monkeypatch.setattr(
        manager.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=output),
    )
    assert manager._describe_port_owner(8596) == (None, "")


# ------------------------------------------------------------------ status text
def test_unmanaged_healthy_streamlit_status_is_explained(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path, 8597)
    monkeypatch.setattr(manager, "get_settings", lambda: settings)
    monkeypatch.setattr(manager, "is_port_open", lambda port: True)
    monkeypatch.setattr(manager, "is_healthy", lambda port, timeout=1.0: True)

    state = manager.detect_state()

    assert state.code == "port_occupied"
    assert "无本实例 PID 记录" in state.detail
    assert "Streamlit" in state.detail


# ------------------------------------------------- spawned-tree ownership gate
def _spawnable(monkeypatch, tmp_path: Path, settings: SimpleNamespace) -> None:
    monkeypatch.setattr(manager, "get_settings", lambda: settings)
    monkeypatch.setattr(
        manager,
        "detect_state",
        lambda **kwargs: manager.ServiceState("stopped", "服务未运行"),
    )
    fake_python = tmp_path / "python.exe"
    fake_python.write_bytes(b"")
    monkeypatch.setattr(manager, "expected_python", lambda **kwargs: fake_python)
    monkeypatch.setattr(manager, "write_pid_record", lambda pid: None)
    monkeypatch.setattr(manager, "_rotate_console_log", lambda path: None)

    class FakeProcess:
        pid = 4321

        def poll(self) -> None:
            return None

    monkeypatch.setattr(manager.subprocess, "Popen", lambda *a, **k: FakeProcess())


def test_start_fails_when_ghost_answers_health(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A ghost holding the port must never make start report success (R5 HIGH)."""

    settings = make_settings(tmp_path, 8598)
    _spawnable(monkeypatch, tmp_path, settings)
    monkeypatch.setattr(manager, "START_TIMEOUT_SECONDS", 2)
    monkeypatch.setattr(manager, "is_healthy", lambda port, timeout=1.0: True)
    monkeypatch.setattr(manager, "_port_owner_pids", lambda port: [9999])
    monkeypatch.setattr(manager, "_parent_pid", lambda pid: None)

    assert manager.start_service(open_browser=False) == 8

    out = capsys.readouterr().out
    assert "其他进程" in out
    assert "9999" in out
    assert "启动成功" not in out


def test_start_succeeds_when_owner_is_spawned_pid(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    settings = make_settings(tmp_path, 8598)
    _spawnable(monkeypatch, tmp_path, settings)
    monkeypatch.setattr(manager, "is_healthy", lambda port, timeout=1.0: True)
    monkeypatch.setattr(manager, "_port_owner_pids", lambda port: [4321])

    assert manager.start_service(open_browser=False) == 0
    assert "启动成功" in capsys.readouterr().out


def test_start_succeeds_when_owner_is_child_of_spawned(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    settings = make_settings(tmp_path, 8598)
    _spawnable(monkeypatch, tmp_path, settings)
    monkeypatch.setattr(manager, "is_healthy", lambda port, timeout=1.0: True)
    monkeypatch.setattr(manager, "_port_owner_pids", lambda port: [5555])
    monkeypatch.setattr(manager, "_parent_pid", lambda pid: 4321 if pid == 5555 else None)

    assert manager.start_service(open_browser=False) == 0
    assert "启动成功" in capsys.readouterr().out


def test_start_succeeds_when_one_of_dual_bind_owners_is_spawned(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Dual-bind: any LISTENING pid inside the spawned tree counts as ours."""

    settings = make_settings(tmp_path, 8598)
    _spawnable(monkeypatch, tmp_path, settings)
    monkeypatch.setattr(manager, "is_healthy", lambda port, timeout=1.0: True)
    monkeypatch.setattr(manager, "_port_owner_pids", lambda port: [9999, 5555])
    monkeypatch.setattr(manager, "_parent_pid", lambda pid: 4321 if pid == 5555 else None)

    assert manager.start_service(open_browser=False) == 0
    assert "启动成功" in capsys.readouterr().out


def test_port_owner_pids_collects_all_listeners(tmp_path: Path, monkeypatch) -> None:
    output = "\n".join(
        [
            "  TCP    127.0.0.1:8599         0.0.0.0:0              LISTENING       4323",
            "  TCP    127.0.0.1:8599         0.0.0.0:0              LISTENING       4326",
            "  TCP    127.0.0.1:8600         0.0.0.0:0              LISTENING       4327",
        ]
    )
    monkeypatch.setattr(
        manager.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=output),
    )

    assert manager._port_owner_pids(8599) == [4323, 4326]
    assert manager._port_owner_pids(8600) == [4327]
    assert manager._port_owner_pids(8601) == []


def test_pid_in_tree_walks_ancestors() -> None:
    chain = {5556: 5555, 5555: 4321}
    monkey_target = {}
    import scripts.service_manager as manager_module

    original = manager_module._parent_pid
    manager_module._parent_pid = lambda pid: chain.get(pid)
    try:
        assert manager_module._pid_in_tree(4321, 4321) is True
        assert manager_module._pid_in_tree(4321, 5555) is True
        assert manager_module._pid_in_tree(4321, 5556) is True
        assert manager_module._pid_in_tree(4321, 9999) is False
    finally:
        manager_module._parent_pid = original
    monkey_target.clear()
