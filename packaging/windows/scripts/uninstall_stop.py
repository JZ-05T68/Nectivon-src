"""Stop only THIS install's Nectivon runtime before MSI payload removal.

Invoked by the WiX uninstall CustomAction using the staged bundled Python::

    [INSTALLFOLDER]runtime\\python\\python.exe
        [INSTALLFOLDER]scripts\\uninstall_stop.py [INSTALLFOLDER]

Background (v0.8.5 incident): Windows Installer's Restart Manager cannot
gracefully close a console Python process, so uninstall used to delete the
bundled runtime out from under a still-running Streamlit server.  The
survivor kept port 8501 bound (health ``ok``, UI ``500`` because its static
files were deleted) and every stable-runtime start failed closed with
``port_occupied``.

Safety contract (fail closed):

- A process is terminated only when its identity is proven to belong to THIS
  install root by one of exactly two proofs:

  1. pid-record proof: the local pid record
     (``%LOCALAPPDATA%\\Nectivon\\runtime-state\\engineering-kb.pid.json``)
     names the pid, and the recorded interpreter path resolves inside the
     install root.  This also covers images already renamed into
     ``C:\\Config.Msi\\*.rbf`` by Windows Installer while the process kept
     running.
  2. image-path proof: the live image path (``QueryFullProcessImageNameW``)
     resolves inside the install root and the image name is a Python
     interpreter (``python.exe`` / ``pythonw.exe``).

- Dev virtual environments in separate project checkouts, stable runtimes, and any
  foreign Python live outside the install root and are unreachable by
  construction; a broad ``python.exe`` match is deliberately impossible.
- User data (database, config, logs, backups, credentials under
  ``%LOCALAPPDATA%\\Nectivon``) is never touched: this helper only terminates
  proven processes and reports.
- Graceful first: console Python has no window/message pump, so the only
  reliable Windows channel is ``TerminateProcess``.  The graceful part of
  this helper is *precision* (proven targets only) plus a bounded exit
  confirmation window before MSI continues to ``RemoveFiles``.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Final

EXIT_OK: Final[int] = 0
EXIT_TERMINATE_FAILED: Final[int] = 3
EXIT_USAGE: Final[int] = 4

_EXIT_TIMEOUT_SECONDS: Final[float] = 15.0
_PORT_RELEASE_TIMEOUT_SECONDS: Final[float] = 10.0
_PRODUCT_PORT: Final[int] = 8501
_INTERPRETER_NAMES: Final[frozenset[str]] = frozenset({"python.exe", "pythonw.exe"})

kernel32 = ctypes.windll.kernel32
_PROCESS_QUERY_LIMITED: Final[int] = 0x1000
_PROCESS_TERMINATE: Final[int] = 0x0001
_STILL_ACTIVE: Final[int] = 259
_INVALID_HANDLE_VALUE: Final[int] = -1
_TH32CS_SNAPPROCESS: Final[int] = 0x2


class _PROCESSENTRY32W(ctypes.Structure):
    """winnt PROCESS entry used by the tool-help snapshot walk."""

    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * 260),
    ]


def _image_path(pid: int) -> Path | None:
    """Return the resolved executable path of one pid, or ``None``."""

    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED, False, pid)
    if not handle:
        return None
    try:
        size = wt.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
        return Path(buffer.value) if ok else None
    finally:
        kernel32.CloseHandle(handle)


def _is_alive(pid: int) -> bool:
    """Return whether one pid still runs (no signaling, no enumeration)."""

    if pid <= 0:
        return False
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED, False, pid)
    if not handle:
        return False
    try:
        code = wt.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _iter_process_images() -> list[tuple[int, Path]]:
    """Return ``(pid, image)`` for every accessible process image."""

    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot == _INVALID_HANDLE_VALUE or not snapshot:
        return []
    results: list[tuple[int, Path]] = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            image = _image_path(entry.th32ProcessID)
            if image is not None:
                results.append((entry.th32ProcessID, image))
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return results


def _path_inside(candidate: Path, root: Path) -> bool:
    """Case-insensitive containment check that rejects sibling prefixes."""

    try:
        resolved = candidate.resolve(strict=False)
        base = root.resolve(strict=False)
    except OSError:
        return False
    prefix = str(base).rstrip("\\/") + os.sep
    return str(resolved).lower().startswith(prefix.lower())


def read_pid_record() -> dict[str, object] | None:
    """Read the installed instance's pid record without ever writing it."""

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    path = (
        Path(local_app_data)
        / "Nectivon"
        / "runtime-state"
        / "engineering-kb.pid.json"
    )
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or not isinstance(value.get("pid"), int):
        return None
    return value


def collect_target_pids(install_root: Path) -> list[tuple[int, str]]:
    """Return ``(pid, proof)`` pairs proven to belong to ``install_root``.

    Proof ``pid-record`` requires the recorded interpreter to live inside the
    install root (covers images renamed into ``C:\\Config.Msi``); proof
    ``image-path`` requires the live interpreter image to live there.  Both
    proofs are strictly path-based, so dev/stable/foreign interpreters can
    never match.
    """

    targets: list[tuple[int, str]] = []
    seen: set[int] = set()

    record = read_pid_record()
    if record is not None:
        recorded_pid = int(record["pid"])
        recorded_python = record.get("python")
        if (
            recorded_pid > 0
            and isinstance(recorded_python, str)
            and recorded_python
            and _path_inside(Path(recorded_python), install_root)
            and _is_alive(recorded_pid)
        ):
            targets.append((recorded_pid, "pid-record"))
            seen.add(recorded_pid)

    for pid, image in _iter_process_images():
        if pid in seen:
            continue
        if image.name.lower() not in _INTERPRETER_NAMES:
            continue
        if _path_inside(image, install_root):
            targets.append((pid, "image-path"))
            seen.add(pid)
    return targets


def terminate_pid(pid: int) -> bool:
    """Terminate one proven pid and confirm its exit within the time bound."""

    if not _is_alive(pid):
        return True
    handle = kernel32.OpenProcess(_PROCESS_TERMINATE, False, pid)
    if not handle:
        return False
    try:
        if not kernel32.TerminateProcess(handle, 1):
            return False
    finally:
        kernel32.CloseHandle(handle)
    deadline = time.monotonic() + _EXIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if not _is_alive(pid):
            return True
        time.sleep(0.25)
    return not _is_alive(pid)


def port_released(port: int) -> bool:
    """Report whether the product port is free again (diagnostics only)."""

    deadline = time.monotonic() + _PORT_RELEASE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                time.sleep(0.25)
        except OSError:
            return True
    return False


def main(argv: list[str]) -> int:
    """Entry point: ``uninstall_stop.py <INSTALLFOLDER>``."""

    if len(argv) != 2:
        print("用法：uninstall_stop.py <INSTALLFOLDER>")
        return EXIT_USAGE
    install_root = Path(argv[1])
    if not install_root.is_absolute():
        install_root = Path(os.getcwd()) / install_root
    targets = collect_target_pids(install_root)
    if not targets:
        print("UNINSTALL_STOP: no install-owned runtime process found.")
        return EXIT_OK
    print(f"UNINSTALL_STOP: {len(targets)} install-owned process(es) found.")
    failed: list[int] = []
    for pid, proof in targets:
        print(f"UNINSTALL_STOP: stopping pid={pid} proof={proof}")
        if terminate_pid(pid):
            print(f"UNINSTALL_STOP: pid={pid} exited.")
        else:
            failed.append(pid)
            print(f"UNINSTALL_STOP: pid={pid} could not be terminated.")
    released = port_released(_PRODUCT_PORT)
    print(f"UNINSTALL_STOP: port {_PRODUCT_PORT} released = {released}")
    return EXIT_OK if not failed else EXIT_TERMINATE_FAILED


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
