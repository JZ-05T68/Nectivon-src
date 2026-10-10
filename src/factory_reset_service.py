"""Clear failed-import traces without touching knowledge assets or workflows."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from src.database import Database


class FactoryResetError(RuntimeError):
    """Raised when the scoped failure cleanup cannot be started safely."""


@dataclass(frozen=True, slots=True)
class FactoryResetResult:
    """Counts of failed import traces and log contents cleared by a reset."""

    failed_import_records: int
    failed_queue_entries: int
    cleared_log_files: int
    cleared_log_bytes: int
    log_failures: tuple[str, ...] = ()


def reset_failed_import_traces(
    database: Database, logs_dir: Path | str
) -> FactoryResetResult:
    """Remove failed import history and empty local log files in place.

    Only rows whose status is exactly ``failed`` are removed. In-progress,
    interrupted, completed, and partially completed imports remain available
    for resuming or review. Log files are emptied, never removed; files outside
    the configured log directory and non-log files are left untouched.
    """

    log_directory = Path(logs_dir)
    if log_directory.is_symlink():
        raise FactoryResetError("日志目录是符号链接，已停止清理以保护其他目录。")
    if log_directory.exists() and not log_directory.is_dir():
        raise FactoryResetError("日志位置不是目录，未执行清理。")

    with database._connection() as connection:
        failed_import_records = connection.execute(
            "DELETE FROM import_records WHERE status = 'failed'"
        ).rowcount
        failed_queue_entries = connection.execute(
            "DELETE FROM import_queue WHERE status = 'failed'"
        ).rowcount

    cleared_log_files = 0
    cleared_log_bytes = 0
    log_failures: list[str] = []
    if log_directory.is_dir():
        for log_path in sorted(log_directory.iterdir(), key=lambda path: path.name):
            try:
                if (not re.search(r"\.log(?:\.\d+)?$", log_path.name, re.IGNORECASE)
                        or log_path.is_symlink()):
                    continue
                if not log_path.is_file():
                    continue
                original_size = log_path.stat().st_size
                with log_path.open("w", encoding="utf-8"):
                    pass
            except OSError:
                log_failures.append(log_path.name)
                continue
            cleared_log_files += 1
            cleared_log_bytes += original_size

    return FactoryResetResult(
        failed_import_records=max(0, failed_import_records),
        failed_queue_entries=max(0, failed_queue_entries),
        cleared_log_files=cleared_log_files,
        cleared_log_bytes=cleared_log_bytes,
        log_failures=tuple(log_failures),
    )
