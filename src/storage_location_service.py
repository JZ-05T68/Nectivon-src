"""Safe, local-only relocation of the knowledge-base data directory."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import stat
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from src.backup_service import (
    BackupError,
    BackupService,
    DatabaseSummary,
    read_database_summary,
)
from src.config import PROJECT_ROOT, Settings, storage_layout

StorageEnvironmentKey = Literal["EKB_STORAGE_DIR", "EKB_STAGING_STORAGE_DIR"]
StorageLocationName = Literal[
    "raw", "pages", "markdown", "database", "logs", "runtime"
]
_CORE_DATA_NAMES = frozenset({"raw", "pages", "markdown", "database"})
_ACTIVE_DATABASE_NAMES = frozenset(
    {"knowledge.db", "knowledge.db-wal", "knowledge.db-shm"}
)
_FORMAL_LOCATION_KEYS: dict[StorageLocationName, str] = {
    "raw": "EKB_STORAGE_RAW_DIR",
    "pages": "EKB_STORAGE_PAGES_DIR",
    "markdown": "EKB_STORAGE_MARKDOWN_DIR",
    "database": "EKB_STORAGE_DATABASE_PATH",
    "logs": "EKB_STORAGE_LOGS_DIR",
    "runtime": "EKB_STORAGE_RUNTIME_DIR",
}
_STAGING_LOCATION_KEYS: dict[StorageLocationName, str] = {
    "raw": "EKB_STAGING_RAW_DIR",
    "pages": "EKB_STAGING_PAGES_DIR",
    "markdown": "EKB_STAGING_MARKDOWN_DIR",
    "database": "EKB_STAGING_DATABASE_PATH",
    "logs": "EKB_STAGING_LOGS_DIR",
    "runtime": "EKB_STAGING_RUNTIME_DIR",
}


class StorageLocationError(RuntimeError):
    """Raised when a storage relocation cannot complete safely."""


@dataclass(frozen=True, slots=True)
class StorageRelocationResult:
    """A verified new data copy plus the retained rollback artifacts."""

    source_data_dir: Path
    target_data_dir: Path
    backup_path: Path
    database_summary: DatabaseSummary
    supplemental_files: int


@dataclass(frozen=True, slots=True)
class StoragePathRelocationResult:
    """One independently relocated local path and its rollback backup."""

    location: StorageLocationName
    source_path: Path
    target_path: Path
    backup_path: Path
    copied_files: int


def relocate_storage(
    settings: Settings,
    target_data_dir: Path | str,
    *,
    env_path: Path | None = None,
    env_key: StorageEnvironmentKey = "EKB_STORAGE_DIR",
    project_root: Path = PROJECT_ROOT,
) -> StorageRelocationResult:
    """Copy verified local data and persist the new root for the next start.

    The active source is never moved or deleted.  A validated backup is kept
    in the existing backup directory, the target database is restored through
    the normal backup pipeline (which rebases recorded asset paths), and the
    dotenv pointer is written only after target validation succeeds.
    """

    source = settings.data_dir.resolve(strict=False)
    target = _validated_target(
        Path(target_data_dir), source=source, project_root=project_root
    )
    _require_quarantine_clear(source / ".deletion-quarantine")

    if env_key in os.environ:
        raise StorageLocationError(
            f"当前存储位置由系统环境变量 {env_key} 管理，"
            "请先移除该环境变量后再从界面修改。"
        )

    try:
        source_backup = BackupService(
            app_version=settings.app_version,
            data_dir=settings.data_dir,
            raw_dir=settings.raw_dir,
            pages_dir=settings.pages_dir,
            markdown_dir=settings.markdown_dir,
            database_path=settings.database_path,
            backups_dir=settings.backups_dir,
            host=settings.host,
            port=settings.port,
            minimum_text_length=settings.minimum_text_length,
            pdf_render_dpi=settings.pdf_render_dpi,
        )
        backup = source_backup.create_backup(
            settings.backups_dir,
            backup_name=None,
        )
        layout = storage_layout(target)
        target_backup = BackupService(
            app_version=settings.app_version,
            data_dir=layout["data_dir"],
            raw_dir=layout["raw_dir"],
            pages_dir=layout["pages_dir"],
            markdown_dir=layout["markdown_dir"],
            database_path=layout["database_path"],
            backups_dir=settings.backups_dir,
            host=settings.host,
            port=settings.port,
            minimum_text_length=settings.minimum_text_length,
            pdf_render_dpi=settings.pdf_render_dpi,
        )
        restored = target_backup.restore_backup(
            backup.backup_path,
            require_existing_target=False,
        )
        supplemental_files = _copy_supplemental_data(source, target)
        _persist_storage_location(
            env_path or project_root / ".env",
            target,
            env_key=env_key,
        )
    except (BackupError, OSError, ValueError) as exc:
        if isinstance(exc, StorageLocationError):
            raise
        raise StorageLocationError(f"修改存储位置失败，原位置保持不变：{exc}") from exc

    return StorageRelocationResult(
        source_data_dir=source,
        target_data_dir=target,
        backup_path=backup.backup_path,
        database_summary=restored.database_summary,
        supplemental_files=supplemental_files,
    )


def relocate_storage_path(
    settings: Settings,
    location: StorageLocationName,
    target_path: Path | str,
    *,
    env_path: Path | None = None,
    staging: bool = False,
    project_root: Path = PROJECT_ROOT,
) -> StoragePathRelocationResult:
    """Relocate one configured path while retaining the old copy.

    Raw PDFs, page images, Markdown and the database remain constrained below
    ``data_dir`` so complete backup/restore and deletion safety stay intact.
    Logs and runtime state may use an independent directory outside the source
    tree.  Only one location should be changed before restarting the service.
    """

    source = _source_path(settings, location).resolve(strict=False)
    target = _validated_individual_target(
        Path(target_path),
        source=source,
        location=location,
        data_dir=settings.data_dir.resolve(strict=False),
        project_root=project_root,
    )
    _require_no_location_overlap(settings, location, target)
    env_key = (
        _STAGING_LOCATION_KEYS[location]
        if staging
        else _FORMAL_LOCATION_KEYS[location]
    )
    if env_key in os.environ:
        raise StorageLocationError(
            f"当前位置由系统环境变量 {env_key} 管理，"
            "请先移除该环境变量后再从界面修改。"
        )

    rebased = False
    try:
        backup = _create_source_backup(settings)
        if location == "database":
            copied_files = _copy_database(source, target)
        else:
            copied_files = _copy_entry(source, target)
        if location in {"raw", "pages", "markdown"}:
            _rebase_active_database(settings, location, source, target)
            rebased = True
        _persist_env_updates(
            env_path or project_root / ".env",
            {env_key: target},
        )
    except (
        BackupError,
        OSError,
        sqlite3.Error,
        StorageLocationError,
        ValueError,
    ) as exc:
        if rebased:
            try:
                _rebase_active_database(settings, location, target, source)
            except Exception as rollback_exc:
                raise StorageLocationError(
                    "新位置配置保存失败，数据库路径自动回滚也失败；"
                    f"切换前备份仍保留。原始错误：{exc}；回滚错误：{rollback_exc}"
                ) from rollback_exc
        raise StorageLocationError(f"修改存储位置失败，原位置保持不变：{exc}") from exc

    return StoragePathRelocationResult(
        location=location,
        source_path=source,
        target_path=target,
        backup_path=backup.backup_path,
        copied_files=copied_files,
    )


def _create_source_backup(settings: Settings):
    service = BackupService(
        app_version=settings.app_version,
        data_dir=settings.data_dir,
        raw_dir=settings.raw_dir,
        pages_dir=settings.pages_dir,
        markdown_dir=settings.markdown_dir,
        database_path=settings.database_path,
        backups_dir=settings.backups_dir,
        host=settings.host,
        port=settings.port,
        minimum_text_length=settings.minimum_text_length,
        pdf_render_dpi=settings.pdf_render_dpi,
    )
    return service.create_backup(settings.backups_dir)


def _source_path(settings: Settings, location: StorageLocationName) -> Path:
    return {
        "raw": settings.raw_dir,
        "pages": settings.pages_dir,
        "markdown": settings.markdown_dir,
        "database": settings.database_path,
        "logs": settings.logs_dir,
        "runtime": settings.runtime_dir,
    }[location]


def _validated_individual_target(
    target: Path,
    *,
    source: Path,
    location: StorageLocationName,
    data_dir: Path,
    project_root: Path,
) -> Path:
    expanded = target.expanduser()
    if not expanded.is_absolute():
        raise StorageLocationError("新的存储位置必须使用绝对路径。")
    _reject_reparse_ancestors(expanded)
    resolved = expanded.resolve(strict=False)
    if resolved == Path(resolved.anchor):
        raise StorageLocationError("新的存储位置不能直接使用磁盘根目录。")
    if resolved == source:
        raise StorageLocationError("新的存储位置与当前位置相同。")
    if _is_within(resolved, source) or _is_within(source, resolved):
        raise StorageLocationError("新的存储位置不能与当前位置互相包含。")

    if location in {"raw", "pages", "markdown", "database"}:
        container = resolved.parent if location == "database" else resolved
        if not _is_within(container, data_dir):
            raise StorageLocationError("资料和数据库的新位置必须位于当前数据目录内。")
    elif _is_within(resolved, project_root.resolve(strict=False)):
        raise StorageLocationError("新的日志或运行状态目录必须位于程序目录之外。")

    if location == "database":
        if resolved.exists():
            raise StorageLocationError("新的数据库文件已存在，系统不会覆盖。")
        resolved.parent.mkdir(parents=True, exist_ok=True)
    elif resolved.exists():
        if not resolved.is_dir():
            raise StorageLocationError("新的存储位置不是目录。")
        try:
            next(resolved.iterdir())
        except StopIteration:
            pass
        else:
            raise StorageLocationError("新的存储位置必须为空，系统不会覆盖已有文件。")
    else:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _require_no_location_overlap(
    settings: Settings,
    location: StorageLocationName,
    target: Path,
) -> None:
    candidate = target.parent if location == "database" else target
    occupied = {
        "raw": settings.raw_dir,
        "pages": settings.pages_dir,
        "markdown": settings.markdown_dir,
        "database": settings.database_path.parent,
        "logs": settings.logs_dir,
        "runtime": settings.runtime_dir,
    }
    for name, path in occupied.items():
        if name == location:
            continue
        other = path.resolve(strict=False)
        if (
            candidate == other
            or _is_within(candidate, other)
            or _is_within(other, candidate)
        ):
            raise StorageLocationError(f"新的存储位置不能与“{name}”位置互相包含。")


def _copy_database(source: Path, target: Path) -> int:
    try:
        source_summary = read_database_summary(source)
        source_connection = sqlite3.connect(
            f"file:{source.as_posix()}?mode=ro", uri=True
        )
        try:
            target_connection = sqlite3.connect(target)
            try:
                with target_connection:
                    source_connection.backup(target_connection)
            finally:
                target_connection.close()
        finally:
            source_connection.close()
        target_summary = read_database_summary(target)
        if (
            target_summary.integrity_check != "ok"
            or target_summary.foreign_key_violations
            or target_summary.statistics != source_summary.statistics
        ):
            raise StorageLocationError("新数据库副本未通过完整性、外键或统计核对。")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return 1


def _rebase_active_database(
    settings: Settings,
    location: Literal["raw", "pages", "markdown"],
    source: Path,
    target: Path,
) -> None:
    with closing(sqlite3.connect(settings.database_path, timeout=30.0)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        if location == "raw":
            rows = connection.execute("SELECT id, source_path FROM documents").fetchall()
            for record_id, value in rows:
                connection.execute(
                    "UPDATE documents SET source_path = ? WHERE id = ?",
                    (str(_relocated_record(value, source, target)), record_id),
                )
        elif location == "pages":
            rows = connection.execute("SELECT id, image_path FROM pages").fetchall()
            for record_id, value in rows:
                connection.execute(
                    "UPDATE pages SET image_path = ? WHERE id = ?",
                    (str(_relocated_record(value, source, target)), record_id),
                )
        else:
            rows = connection.execute(
                "SELECT id, markdown_path FROM pages WHERE markdown_path IS NOT NULL"
            ).fetchall()
            for record_id, value in rows:
                connection.execute(
                    "UPDATE pages SET markdown_path = ? WHERE id = ?",
                    (str(_relocated_record(value, source, target)), record_id),
                )
        connection.commit()


def _relocated_record(value: str, source: Path, target: Path) -> Path:
    recorded = Path(str(value))
    if not recorded.is_absolute():
        recorded = source.parent.parent / recorded
    try:
        relative = recorded.resolve(strict=False).relative_to(source)
    except ValueError as exc:
        raise StorageLocationError(f"数据库路径超出当前位置：{value}") from exc
    return target / relative


def _validated_target(target: Path, *, source: Path, project_root: Path) -> Path:
    _reject_reparse_ancestors(target.expanduser())
    try:
        layout = storage_layout(target)
    except ValueError as exc:
        raise StorageLocationError(str(exc)) from exc
    resolved = layout["data_dir"]
    protected = project_root.resolve(strict=False)
    if resolved == source:
        raise StorageLocationError("新的存储位置与当前位置相同。")
    if _is_within(resolved, source) or _is_within(source, resolved):
        raise StorageLocationError("新的存储位置不能与当前位置互相包含。")
    if _is_within(resolved, protected):
        raise StorageLocationError("新的存储位置必须位于程序目录之外。")
    if resolved.exists():
        if not resolved.is_dir():
            raise StorageLocationError("新的存储位置不是目录。")
        try:
            next(resolved.iterdir())
        except StopIteration:
            pass
        else:
            raise StorageLocationError("新的存储位置必须为空，系统不会覆盖已有文件。")
    else:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _require_quarantine_clear(quarantine: Path) -> None:
    if not quarantine.exists():
        return
    try:
        next(quarantine.iterdir())
    except StopIteration:
        return
    raise StorageLocationError(
        "删除隔离区仍有待处理内容，请先在“系统维护”完成检查后再修改存储位置。"
    )


def _copy_supplemental_data(source: Path, target: Path) -> int:
    """Copy local data omitted by the portable backup format, with hashes."""

    copied = 0
    for entry in source.iterdir():
        if entry.name in _CORE_DATA_NAMES:
            continue
        copied += _copy_entry(entry, target / entry.name)

    source_database = source / "database"
    target_database = target / "database"
    if source_database.is_dir():
        for entry in source_database.iterdir():
            if entry.name in _ACTIVE_DATABASE_NAMES:
                continue
            copied += _copy_entry(entry, target_database / entry.name)
    return copied


def _copy_entry(source: Path, target: Path) -> int:
    if _is_link_like(source):
        raise StorageLocationError(f"拒绝复制符号链接或重解析点：{source}")
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        total = 0
        for child in source.iterdir():
            total += _copy_entry(child, target / child.name)
        return total
    if not source.is_file():
        raise StorageLocationError(f"拒绝复制非常规文件：{source}")
    if target.exists():
        raise StorageLocationError(f"目标文件已存在，不会覆盖：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    source_hash = hashlib.sha256()
    target_hash = hashlib.sha256()
    with source.open("rb") as source_handle, target.open("xb") as target_handle:
        while chunk := source_handle.read(1024 * 1024):
            source_hash.update(chunk)
            target_handle.write(chunk)
            target_hash.update(chunk)
        target_handle.flush()
        os.fsync(target_handle.fileno())
    if source_hash.digest() != target_hash.digest():
        raise StorageLocationError(f"补充文件复制后哈希不一致：{source}")
    return 1


def _persist_storage_location(
    env_path: Path,
    target: Path,
    *,
    env_key: StorageEnvironmentKey,
) -> None:
    """Atomically set one non-secret dotenv key without exposing other values."""

    mapping = (
        _STAGING_LOCATION_KEYS
        if env_key == "EKB_STAGING_STORAGE_DIR"
        else _FORMAL_LOCATION_KEYS
    )
    data_child_keys = {mapping[name] for name in ("raw", "pages", "markdown", "database")}
    _persist_env_updates(
        env_path,
        {env_key: target},
        remove_keys=data_child_keys,
    )


def _persist_env_updates(
    env_path: Path,
    updates: dict[str, Path],
    *,
    remove_keys: set[str] | None = None,
) -> None:
    """Atomically update non-secret path keys while preserving all other lines."""

    path = env_path.resolve(strict=False)
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
    except (OSError, UnicodeError) as exc:
        raise StorageLocationError(f"无法读取本地配置文件：{exc}") from exc

    replaced_keys = set(updates) | (remove_keys or set())
    key_pattern = re.compile(
        r"^\s*(?:export\s+)?(" + "|".join(
            re.escape(key) for key in sorted(replaced_keys)
        ) + r")\s*=.*$"
    )
    retained = [
        line for line in existing.splitlines() if not key_pattern.match(line)
    ]
    for key, value in updates.items():
        quoted = (
            '"'
            + str(value).replace("\\", "\\\\").replace('"', '\\"')
            + '"'
        )
        retained.append(f"{key}={quoted}")
    content = "\n".join(retained) + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.storage-location-{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            shutil.copymode(path, temporary)
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise StorageLocationError(f"无法保存新的存储位置：{exc}") from exc


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _reject_reparse_ancestors(path: Path) -> None:
    """Reject a target reached through a symlink or Windows junction."""

    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    while True:
        if _is_link_like(current):
            raise StorageLocationError(
                f"新的存储位置不能经过符号链接或 Windows 重解析点：{current}"
            )
        if current == current.parent:
            break
        current = current.parent


def _is_link_like(path: Path) -> bool:
    try:
        attributes = int(getattr(path.lstat(), "st_file_attributes", 0))
    except OSError:
        attributes = 0
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return path.is_symlink() or bool(attributes & reparse_flag)


__all__ = [
    "StorageLocationError",
    "StorageRelocationResult",
    "relocate_storage",
]
