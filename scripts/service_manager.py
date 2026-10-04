"""Windows-friendly lifecycle manager for the local Streamlit service."""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

SCRIPT_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
PROJECT_ROOT: Final[Path] = (
    SCRIPT_ROOT / "app"
    if (SCRIPT_ROOT / "VERSION").is_file() and (SCRIPT_ROOT / "app" / "src").is_dir()
    else SCRIPT_ROOT
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import (  # noqa: E402
    OfficialEndpointError,
    Settings,
    StorageConfigurationError,
    get_settings,
    is_installed_distribution,
    staging_settings,
)

# Stable development-task identifier retained for backward compatibility.
TASK_NAME: Final[str] = "EngineeringKnowledgeBase"
HEALTH_PATH: Final[str] = "/_stcore/health"
START_TIMEOUT_SECONDS: Final[int] = 30
STOP_TIMEOUT_SECONDS: Final[int] = 10
LOGGER = logging.getLogger("service_manager")

#: Settings of the instance being managed; ``None`` means production.
_ACTIVE_SETTINGS: Settings | None = None
#: Explicit staging root/port when the manager was started with
#: ``--staging-root`` + ``--staging-port``; propagated to the spawned app
#: process so the child resolves exactly the same isolated instance.
_STAGING_EXPLICIT_ROOT: Path | None = None
_STAGING_EXPLICIT_PORT: int | None = None


def active_settings() -> Settings:
    """Return the settings of the managed instance (production by default).

    Production callers see ``get_settings()`` exactly as before; with
    ``--staging`` the manager operates on the isolated staging instance
    (port 8511, staging pid/log/runtime paths) instead.
    """

    return _ACTIVE_SETTINGS if _ACTIVE_SETTINGS is not None else get_settings()


@dataclass(frozen=True, slots=True)
class ServiceState:
    """Detected service state and concise user-facing detail."""

    code: str
    detail: str
    pid: int | None = None


def configure_manager_logging() -> None:
    """Write manager events to a bounded local log."""

    settings = active_settings()
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        settings.logs_dir / "service-manager.log",
        maxBytes=1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOGGER.setLevel(logging.INFO)
    LOGGER.addHandler(handler)


def health_url(port: int) -> str:
    return f"http://127.0.0.1:{port}{HEALTH_PATH}"


def is_healthy(port: int, timeout: float = 1.0) -> bool:
    """Check Streamlit's privacy-safe loopback health endpoint."""

    try:
        with urllib.request.urlopen(health_url(port), timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def is_port_open(port: int) -> bool:
    """Return whether a loopback TCP listener currently owns ``port``."""

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def is_process_alive(pid: int) -> bool:
    """Check one PID without signaling or enumerating unrelated Python processes."""

    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    process_query = 0x1000
    still_active = 259
    handle = ctypes.windll.kernel32.OpenProcess(process_query, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def process_executable(pid: int) -> Path | None:
    """Return the executable of one Windows PID for stale/PID-reuse protection."""

    if os.name != "nt":
        return None
    process_query = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(process_query, False, pid)
    if not handle:
        return None
    try:
        size = ctypes.c_ulong(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        success = ctypes.windll.kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        )
        return Path(buffer.value) if success else None
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def read_pid_record() -> dict[str, object] | None:
    """Read a validated project PID record; malformed records are treated as stale."""

    path = active_settings().pid_path
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("pid"), int):
            raise ValueError("PID 记录格式不正确")
        return value
    except (OSError, ValueError, json.JSONDecodeError):
        LOGGER.warning("发现无效 PID 文件，已清理：%s", path, exc_info=True)
        path.unlink(missing_ok=True)
        return None


def write_pid_record(pid: int) -> None:
    """Atomically persist process identity for precise future stop operations."""

    settings = active_settings()
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "pid": pid,
        "project_root": str(PROJECT_ROOT),
        "python": str(expected_python()),
        "port": settings.port,
        "started_at": time.time(),
    }
    temporary = settings.pid_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(settings.pid_path)


def expected_python(*, windowed: bool = False) -> Path:
    """Return the installed private interpreter or the development venv."""

    name = "pythonw.exe" if windowed else "python.exe"
    configured = os.environ.get("NECTIVON_PYTHON", "").strip()
    if configured:
        configured_path = Path(sys.executable).resolve()
        if windowed:
            windowed_path = configured_path.with_name(name)
            if windowed_path.is_file():
                return windowed_path
        return configured_path
    if is_installed_distribution(PROJECT_ROOT):
        return PROJECT_ROOT.parent / "runtime" / "python" / name
    return PROJECT_ROOT / ".venv" / "Scripts" / name


def record_matches_process(record: dict[str, object]) -> bool:
    """Ensure a live PID still belongs to this project's recorded interpreter."""

    pid = int(record["pid"])
    executable = process_executable(pid)
    if executable is None:
        return os.name != "nt"
    expected = Path(str(record.get("python", expected_python())))
    return executable.resolve() == expected.resolve()


def detect_state(*, clean_stale: bool = True) -> ServiceState:
    """Combine PID, process identity, health, and port state."""

    settings = active_settings()
    record = read_pid_record()
    if record is not None:
        pid = int(record["pid"])
        if not is_process_alive(pid):
            if clean_stale:
                settings.pid_path.unlink(missing_ok=True)
            if is_port_open(settings.port):
                return ServiceState("port_occupied", "PID 已失效，端口被其他程序占用")
            return ServiceState("abnormal", "服务异常退出，已识别并清理过期 PID")
        if not record_matches_process(record):
            if clean_stale:
                settings.pid_path.unlink(missing_ok=True)
            return ServiceState("abnormal", "PID 已被其他程序复用，未终止该进程")
        if is_healthy(settings.port):
            return ServiceState("running", "服务正常运行", pid)
        started_at = float(record.get("started_at", 0))
        if time.time() - started_at <= START_TIMEOUT_SECONDS:
            return ServiceState("starting", "服务正在启动", pid)
        return ServiceState("abnormal", "进程存在但健康检查失败", pid)
    if is_port_open(settings.port):
        detail = (
            "已有 Streamlit 服务在运行，但无本实例 PID 记录（可能由其他目录或手动方式启动）"
            if is_healthy(settings.port)
            else "端口被其他程序占用"
        )
        return ServiceState("port_occupied", detail)
    return ServiceState("stopped", "服务未运行")


def _terminate_process_tree(pid: int) -> bool:
    """Terminate one owned pid together with its child process tree.

    Windows venv ``python.exe`` is a launcher that re-execs the base
    interpreter as a child process; terminating only the recorded pid used
    to orphan the real server, which kept holding the port as an unrecorded
    ghost (the 2026-09-25 launcher outage). ``taskkill /T`` targets the pid
    tree precisely — never a process name — and the fallback below keeps
    working when taskkill is unavailable.
    """

    if os.name == "nt":
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            # Chinese Windows taskkill output is GBK; under PYTHONUTF8=1 the
            # default decode crashed the output reader thread (V086-201).
            # The stop itself always worked - only the console noise is fixed.
            encoding="utf-8",
            errors="replace",
            check=False,
            creationflags=0x08000000,
        )
        if completed.returncode == 0:
            deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
            while time.monotonic() < deadline and is_process_alive(pid):
                time.sleep(0.1)
            return not is_process_alive(pid)
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline and is_process_alive(pid):
        time.sleep(0.25)
    if is_process_alive(pid):
        if os.name == "nt":
            handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, pid)
            if handle:
                try:
                    ctypes.windll.kernel32.TerminateProcess(handle, 1)
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
        else:
            os.kill(pid, signal.SIGKILL)
    return not is_process_alive(pid)


def _wait_port_released(settings: Settings) -> bool:
    """Wait briefly for the instance port to be released after a stop."""

    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline and is_port_open(settings.port):
        time.sleep(0.25)
    return not is_port_open(settings.port)


def _stop_owned_instance(settings: Settings, pid: int) -> bool:
    """Precisely stop one owned but unhealthy instance before respawning.

    v0.8.5 BUG B fix: ``start_service`` used to fall through an
    ``abnormal`` state with a live owned pid and spawn a second instance
    beside it (the 2026-09-24 double-bind incident).  Ownership is
    re-verified against the pid record and the recorded interpreter so a
    reused pid can never be terminated; foreign port occupants never reach
    this helper and keep the fail-closed ``port_occupied`` path.
    """

    record = read_pid_record()
    if record is None or int(record["pid"]) != pid:
        return False
    if not record_matches_process(record):
        settings.pid_path.unlink(missing_ok=True)
        return False
    LOGGER.info("停止不健康实例：pid=%s", pid)
    if not _terminate_process_tree(pid):
        return False
    settings.pid_path.unlink(missing_ok=True)
    # Port release is awaited by the caller: a lingering listener means the
    # caller must refuse to spawn with its own precise message and code.
    return True


def _port_owner_pids(port: int) -> list[int]:
    """All process ids with a LISTENING socket on ``port`` (best effort).

    Windows allows several sockets to bind the same loopback port without
    SO_EXCLUSIVEADDRUSE - the 2026-09-27 R5 ghost incident had two Streamlit
    servers on 8512 at once - so callers must treat this as a SET, not one
    owner.  Read-only: nothing here may terminate a process.
    """

    if os.name != "nt":
        return []
    try:
        completed = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            creationflags=0x08000000,
        )
    except OSError:
        return []
    pids: list[int] = []
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 5 and fields[3].upper() == "LISTENING" and fields[1].endswith(f":{port}"):
            try:
                pid = int(fields[4])
            except ValueError:
                continue
            if pid not in pids:
                pids.append(pid)
    return pids


def _describe_port_owner(port: int) -> tuple[int | None, str]:
    """Best-effort (pid, executable) of a LISTENING socket on ``port``."""

    pids = _port_owner_pids(port)
    if not pids:
        return None, ""
    for pid in pids:
        executable = process_executable(pid)
        if executable is not None:
            return pid, str(executable)
    return pids[0], ""


def _refuse_port_occupied(settings: Settings, *, open_browser: bool) -> int:
    """Handle ``port_occupied`` on start with explicit, non-silent cases.

    R4 finding: an unmanaged (bash-resident) Streamlit on the staging port
    made ``start`` look ambiguous - the user could believe a new instance
    was running while the browser silently reached an old process. This
    helper must never spawn a second instance and never kill anything:

    - healthy Streamlit on the port  -> report "already running", open the
      existing instance, and warn that its code version cannot be verified;
    - any other listener             -> precise refusal with the owner pid,
      explicit "will not kill" statement, exit code 2.
    """

    port = settings.port
    pid, executable = _describe_port_owner(port)
    if is_healthy(port):
        owner = f"PID {pid}" if pid else "占用进程未能识别"
        if executable:
            owner += f"（{executable}）"
        print(f"Nectivon 服务已在运行：127.0.0.1:{port}，本次未重复启动。")
        print(f"占用进程：{owner}。")
        print("无法确认该实例的代码版本；若它由其他目录或旧代码启动，可能不是最新代码。")
        print("如需以当前目录代码运行，请先停止现有实例（对应的 停止Nectivon*.bat）")
        print("或关闭该进程后重试。")
        LOGGER.info("start 拒绝重复启动：端口 %s 已有未托管 Streamlit 服务（%s）", port, owner)
        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{port}")
        return 0
    owner = f"（占用进程 PID {pid}，{executable}）" if pid else "（未能识别占用进程）"
    print(f"启动失败：{port} 端口已被其他进程占用，未启动新的测试服。{owner}")
    print("Nectivon 不会自动结束其他进程；请先释放该端口后重试。")
    LOGGER.info("start 拒绝启动：端口 %s 被其他进程占用（pid=%s）", port, pid)
    return 2


def _parent_pid(pid: int) -> int | None:
    """Return the parent process id of ``pid`` (Windows toolhelp snapshot)."""

    if os.name != "nt":
        return None
    th32cs_snapprocess = 0x00000002
    invalid_handle_value = -1
    kernel32 = ctypes.windll.kernel32

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    snapshot = kernel32.CreateToolhelp32Snapshot(th32cs_snapprocess, 0)
    if snapshot == invalid_handle_value or snapshot == 0:
        return None
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return None
        while True:
            if int(entry.th32ProcessID) == pid:
                return int(entry.th32ParentProcessID)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                return None
    finally:
        kernel32.CloseHandle(snapshot)


def _pid_in_tree(spawned_pid: int, owner_pid: int) -> bool:
    """True when ``owner_pid`` is ``spawned_pid`` or one of its descendants."""

    if owner_pid == spawned_pid:
        return True
    current = owner_pid
    for _ in range(8):
        current = _parent_pid(current)
        if current is None or current <= 0:
            return False
        if current == spawned_pid:
            return True
    return False


def _owns_serving_process(spawned_pid: int, owner_pids: list[int]) -> bool:
    """True when any port owner belongs to the freshly spawned process tree.

    Closure R5 HIGH fix: the health endpoint only proves *some* Streamlit
    answers on the port.  A ghost instance that still holds the port after a
    double-bind used to make ``start`` report success while the browser
    silently reached an old process (the exact R4 incident class).  The
    responder must therefore be the spawned launcher itself or one of its
    descendants; an unidentifiable owner set fails closed.
    """

    if not owner_pids:
        return False
    return any(_pid_in_tree(spawned_pid, pid) for pid in owner_pids)


def start_service(*, open_browser: bool = True) -> int:
    """Start one detached local service and wait for a verified health response."""

    settings = active_settings()
    state = detect_state()
    if state.code == "running":
        print(f"Nectivon 已经运行（PID {state.pid}）。")
        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{settings.port}")
        return 0
    if state.code == "starting":
        print(f"Nectivon 正在启动（PID {state.pid}），请稍候。")
        return 0
    if state.code == "port_occupied":
        return _refuse_port_occupied(settings, open_browser=open_browser)

    if state.code == "abnormal" and state.pid is not None:
        # Owned instance alive but unhealthy: stop it first, confirm the
        # port is released, and only then spawn exactly one new instance.
        print("检测到本项目服务进程存在但健康检查失败，正在安全停止旧实例……")
        if not _stop_owned_instance(settings, state.pid):
            print("启动失败：旧实例停止失败，为避免双实例拒绝启动。")
            return 7
        deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
        while time.monotonic() < deadline and is_port_open(settings.port):
            time.sleep(0.25)
        if is_port_open(settings.port):
            print(f"启动失败：端口 {settings.port} 未释放，拒绝启动。")
            return 2

    python_path = expected_python()
    if not python_path.is_file():
        print(f"启动失败：找不到 Nectivon 私有 Python runtime：{python_path}")
        print("请修复或重新安装 Nectivon。")
        return 3
    app_path = PROJECT_ROOT / "app.py"
    if not app_path.is_file():
        print(f"启动失败：找不到应用入口：{app_path}")
        return 4

    settings.ensure_directories()
    console_log = settings.logs_dir / "server-console.log"
    _rotate_console_log(console_log)
    command = [
        str(python_path),
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(settings.port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]
    creation_flags = 0
    if os.name == "nt":
        creation_flags = 0x00000008 | 0x00000200 | 0x08000000
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    if _ACTIVE_SETTINGS is not None:
        # staging 子进程必须整体运行在 staging_settings 之上，而不是
        # 临时改写 production 配置。
        environment["EKB_STAGING_INSTANCE"] = "1"
        if _STAGING_EXPLICIT_ROOT is not None and _STAGING_EXPLICIT_PORT is not None:
            # Explicit test instance (8512+): the child must resolve the same
            # isolated root/port, not the default staging-data/8511 pair.
            environment["EKB_STAGING_ROOT"] = str(_STAGING_EXPLICIT_ROOT)
            environment["EKB_STAGING_PORT"] = str(_STAGING_EXPLICIT_PORT)
    else:
        environment.pop("EKB_STAGING_INSTANCE", None)
    with console_log.open("a", encoding="utf-8") as output:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
            close_fds=True,
        )
    write_pid_record(process.pid)
    LOGGER.info("启动服务：pid=%s port=%s", process.pid, settings.port)

    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            settings.pid_path.unlink(missing_ok=True)
            print(f"启动失败：服务异常退出（退出码 {process.returncode}）。")
            _print_log_tail(console_log)
            return 5
        if is_healthy(settings.port):
            owner_pids = _port_owner_pids(settings.port)
            if not _owns_serving_process(process.pid, owner_pids):
                # Another (ghost) instance is answering on this port; our own
                # child did not win the bind or has not surfaced yet.  Never
                # report success for somebody else's process.
                if time.monotonic() < deadline - 1.0:
                    time.sleep(0.5)
                    continue
                settings.pid_path.unlink(missing_ok=True)
                print(
                    "启动失败：端口上应答的是其他进程"
                    f"（PID {', '.join(str(p) for p in owner_pids) or '未知'}），"
                    "本次启动的新实例未获得端口。"
                )
                print("Nectivon 不会自动结束其他进程；请先释放该端口后重试。")
                LOGGER.warning(
                    "start 端口归属校验失败：owners=%s spawned=%s",
                    owner_pids,
                    process.pid,
                )
                return 8
            print(f"Nectivon 启动成功：http://127.0.0.1:{settings.port}")
            if open_browser:
                webbrowser.open(f"http://127.0.0.1:{settings.port}")
            return 0
        time.sleep(0.5)
    print("启动失败：等待健康检查超时，服务可能仍在初始化。")
    _print_log_tail(console_log)
    return 6


def stop_service() -> int:
    """Stop only the exact process recorded for this project."""

    settings = active_settings()
    record = read_pid_record()
    if record is None:
        state = detect_state()
        if state.code == "port_occupied":
            print(f"未停止任何进程：{state.detail}。")
            return 2
        print("Nectivon 未运行。")
        return 0
    pid = int(record["pid"])
    if not is_process_alive(pid):
        settings.pid_path.unlink(missing_ok=True)
        print("服务已经停止，过期 PID 文件已清理。")
        return 0
    if not record_matches_process(record):
        settings.pid_path.unlink(missing_ok=True)
        print("拒绝停止：PID 已属于其他程序；已清理本项目过期记录。")
        return 3

    LOGGER.info("停止服务：pid=%s", pid)
    if not _terminate_process_tree(pid):
        print("停止失败：服务进程未能终止，未影响其他 Python 进程。")
        return 3
    settings.pid_path.unlink(missing_ok=True)
    if not _wait_port_released(settings):
        print("警告：服务进程已停止，但端口尚未释放，可能存在残留子进程。")
        return 4
    print("Nectivon 已停止；未影响其他 Python 进程。")
    return 0


def show_status() -> int:
    """Print the five-state local lifecycle result."""

    state = detect_state()
    suffix = f"（PID {state.pid}）" if state.pid else ""
    print(f"{state.detail}{suffix}")
    return 0 if state.code in {"running", "starting", "stopped"} else 1


def enable_autostart() -> int:
    """Create an ONLOGON task, falling back to the current-user Startup folder."""

    if os.name != "nt":
        print("开机自启仅支持 Windows。")
        return 2
    pythonw = expected_python(windowed=True)
    if not pythonw.is_file():
        print(f"启用失败：找不到 Nectivon Python runtime：{pythonw}")
        return 3
    script = Path(__file__).resolve()
    if is_installed_distribution(PROJECT_ROOT):
        try:
            import winreg

            launcher = PROJECT_ROOT.parent / "scripts" / "launch.vbs"
            command = f'wscript.exe "{launcher}"'
            with winreg.CreateKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
            ) as key:
                winreg.SetValueEx(key, "Nectivon", 0, winreg.REG_SZ, command)
        except OSError as exc:
            print(f"启用开机自启失败：{exc}")
            return 1
        print("已启用当前用户登录后自动启动：Nectivon")
        return 0
    task_command = f'"{pythonw}" "{script}" start --no-browser'
    result = subprocess.run(
        [
            "schtasks.exe",
            "/Create",
            "/TN",
            TASK_NAME,
            "/SC",
            "ONLOGON",
            "/TR",
            task_command,
            "/F",
            "/RL",
            "LIMITED",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        try:
            startup_entry = _startup_entry_path()
            startup_entry.parent.mkdir(parents=True, exist_ok=True)
            startup_entry.write_text(
                "@echo off\n"
                f'"{pythonw}" "{script}" start --no-browser\n',
                encoding="utf-8",
            )
        except OSError as exc:
            print("启用开机自启失败：任务计划程序被拒绝，启动文件夹也无法写入。")
            print(str(exc))
            return result.returncode or 1
        print("任务计划程序被当前策略拒绝，已改用当前用户启动文件夹。")
        print(f"已启用登录后自动启动：{startup_entry}")
        return 0
    print(f"已启用当前用户登录后自动启动：计划任务 {TASK_NAME}")
    return 0


def disable_autostart() -> int:
    """Remove the optional current-user scheduled task."""

    if os.name != "nt":
        print("开机自启仅支持 Windows。")
        return 2
    if is_installed_distribution(PROJECT_ROOT):
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                try:
                    winreg.DeleteValue(key, "Nectivon")
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"关闭开机自启失败：{exc}")
            return 1
        print("已关闭 Nectivon 当前用户开机自启。")
        return 0
    startup_entry = _startup_entry_path()
    startup_existed = startup_entry.exists()
    try:
        startup_entry.unlink(missing_ok=True)
    except OSError as exc:
        print(f"无法移除启动文件夹入口：{exc}")
        return 1
    result = subprocess.run(
        ["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        combined = (result.stderr or result.stdout).strip()
        not_found = "不存在" in combined or "cannot find" in combined.casefold()
        if not_found or startup_existed:
            if startup_existed:
                print("已关闭并移除启动文件夹中的开机自启入口。")
            else:
                print("开机自启任务原本就不存在。")
            return 0
        print(f"关闭开机自启失败：{combined}")
        return result.returncode or 1
    print(f"已关闭并移除开机自启任务：{TASK_NAME}")
    return 0


def _startup_entry_path() -> Path:
    app_data = os.environ.get("APPDATA")
    if not app_data:
        raise OSError("找不到当前用户 APPDATA 目录")
    return (
        Path(app_data)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
        / "EngineeringKnowledgeBase.cmd"
    )


def _rotate_console_log(path: Path, max_bytes: int = 2 * 1024 * 1024) -> None:
    if not path.exists() or path.stat().st_size < max_bytes:
        return
    oldest = path.with_suffix(path.suffix + ".3")
    oldest.unlink(missing_ok=True)
    for index in range(2, 0, -1):
        source = path.with_suffix(path.suffix + f".{index}")
        if source.exists():
            source.replace(path.with_suffix(path.suffix + f".{index + 1}"))
    path.replace(path.with_suffix(path.suffix + ".1"))


def _print_log_tail(path: Path, line_count: int = 12) -> None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    if lines:
        print("最近的启动日志：")
        print("\n".join(lines[-line_count:]))


def _add_staging_instance_arguments(parser: argparse.ArgumentParser) -> None:
    """Add optional explicit staging root/port pair to one lifecycle parser."""

    parser.add_argument(
        "--staging-root",
        type=Path,
        default=None,
        help="显式测试实例数据根（必须与 --staging-port 成对提供）",
    )
    parser.add_argument(
        "--staging-port",
        type=int,
        default=None,
        help="显式测试实例端口，例如 8512（不能是正式端口 8501）",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Nectivon 本地服务管理器")
    subparsers = parser.add_subparsers(dest="command", required=True)
    start_parser = subparsers.add_parser("start", help="启动后台服务")
    start_parser.add_argument("--no-browser", action="store_true")
    start_parser.add_argument(
        "--staging", action="store_true", help="管理隔离的 staging 实例（8511）"
    )
    _add_staging_instance_arguments(start_parser)
    stop_parser = subparsers.add_parser("stop", help="停止本项目服务")
    stop_parser.add_argument(
        "--staging", action="store_true", help="停止 staging 实例（不影响 production）"
    )
    _add_staging_instance_arguments(stop_parser)
    status_parser = subparsers.add_parser("status", help="查看运行状态")
    status_parser.add_argument(
        "--staging", action="store_true", help="查看 staging 实例状态"
    )
    _add_staging_instance_arguments(status_parser)
    subparsers.add_parser("enable-autostart", help="启用当前用户登录后自启")
    subparsers.add_parser("disable-autostart", help="关闭当前用户登录后自启")
    subparsers.add_parser("launch", help="从安装版快捷方式启动")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    if getattr(arguments, "staging", False):
        global _ACTIVE_SETTINGS, _STAGING_EXPLICIT_ROOT, _STAGING_EXPLICIT_PORT
        root = getattr(arguments, "staging_root", None)
        port = getattr(arguments, "staging_port", None)
        if (root is None) != (port is None):
            print("参数错误：--staging-root 与 --staging-port 必须同时提供。")
            return 2
        try:
            _ACTIVE_SETTINGS = staging_settings(root, port=port)
        except StorageConfigurationError as exc:
            print(f"测试实例配置错误：{exc}")
            return 2
        _STAGING_EXPLICIT_ROOT = root
        _STAGING_EXPLICIT_PORT = port
    configure_manager_logging()
    try:
        if arguments.command == "start":
            return start_service(open_browser=not arguments.no_browser)
        if arguments.command == "stop":
            return stop_service()
        if arguments.command == "status":
            return show_status()
        if arguments.command == "enable-autostart":
            return enable_autostart()
        if arguments.command == "disable-autostart":
            return disable_autostart()
        if arguments.command == "launch":
            result = start_service(open_browser=True)
            if result != 0 and os.name == "nt":
                ctypes.windll.user32.MessageBoxW(
                    None,
                    "Nectivon 无法启动。\n\n"
                    "请确认 127.0.0.1:8501 未被其他程序占用，"
                    "或查看 %LOCALAPPDATA%\\Nectivon\\logs 中的日志。",
                    "Nectivon 启动失败",
                    0x10,
                )
            return result
    except OfficialEndpointError as exc:
        print(f"正式服务配置错误：{exc}")
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
