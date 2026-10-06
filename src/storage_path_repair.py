"""Repair verified asset references after a local data directory is moved."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from src.database import Database
from src.deletion_recovery import sha256_file

LOGGER = logging.getLogger(__name__)


def _ordinary_file(path: Path, root: Path) -> bool:
    """Accept only existing files inside their owner, without link-like ancestors."""

    if not path.is_absolute() or ".." in path.parts:
        return False
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        return False
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part.stat(), "st_file_attributes", 0) & 0x400:
            return False
    return True


def repair_relocated_asset_paths(
    database: Database, *, data_dir: Path, raw_dir: Path, pages_dir: Path, markdown_dir: Path
) -> int:
    """Rebase stale paths only for a complete, hash-verified local document.

    The old data root must no longer exist. All recorded assets must retain
    their relative layout and exist as ordinary files under the current roots.
    A SQLite snapshot precedes the atomic metadata update; source files and
    note contents are never written. Ambiguous or incomplete documents retain
    their original paths, including the deletion service's boundary checks.
    """

    repaired = 0
    with database._connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        plans: list[tuple[int, Path, list[tuple[int, Path, Path | None]]]] = []
        for document in connection.execute("SELECT id, source_path, sha256 FROM documents"):
            source = Path(document["source_path"])
            if source.resolve().is_relative_to(raw_dir.resolve()):
                continue
            old_data = source.parent.parent
            if (
                not source.is_absolute()
                or ".." in source.parts
                or source.parent.name != raw_dir.name
                or old_data.name != data_dir.name
                or old_data.exists()
            ):
                continue
            try:
                new_source = raw_dir / source.name
                if not _ordinary_file(new_source, raw_dir):
                    raise ValueError("本地原文件缺失或路径不安全")
                if sha256_file(new_source) != document["sha256"]:
                    raise ValueError("本地原文件 SHA-256 不匹配")
                page_plan: list[tuple[int, Path, Path | None]] = []
                for page in connection.execute(
                    "SELECT id, image_path, markdown_path FROM pages WHERE document_id = ?",
                    (document["id"],),
                ):
                    new_paths: list[Path | None] = []
                    for field, root in (("image_path", pages_dir), ("markdown_path", markdown_dir)):
                        recorded = page[field]
                        if recorded is None and field == "markdown_path":
                            new_paths.append(None)
                            continue
                        original = Path(recorded)
                        old_owner = old_data / root.name / str(document["id"])
                        if ".." in original.parts or original.parent != old_owner:
                            raise ValueError("登记页面路径不属于原文档目录")
                        destination = root / str(document["id"]) / original.name
                        if not _ordinary_file(destination, root / str(document["id"])):
                            raise ValueError("本地页面文件缺失或路径不安全")
                        new_paths.append(destination)
                    page_plan.append((page["id"], new_paths[0], new_paths[1]))
                plans.append((document["id"], new_source, page_plan))
            except (OSError, ValueError) as exc:
                LOGGER.warning("未修复文档路径：document_id=%s 原因=%s", document["id"], exc)
        if not plans:
            return 0

        snapshot = database.database_path.with_name(
            f"{database.database_path.name}.paths-{uuid4().hex}.bak"
        )
        with closing(sqlite3.connect(database.database_path)) as source_connection:
            with closing(sqlite3.connect(snapshot)) as destination_connection:
                source_connection.backup(destination_connection)
        for document_id, new_source, page_plan in plans:
            connection.execute(
                "UPDATE documents SET source_path = ? WHERE id = ?", (str(new_source), document_id)
            )
            connection.executemany(
                "UPDATE pages SET image_path = ?, markdown_path = ? WHERE id = ?",
                [(str(image), str(markdown) if markdown else None, page_id)
                 for page_id, image, markdown in page_plan],
            )
            repaired += 1
    LOGGER.info("已修复 %s 份搬迁文档的路径，原数据库快照：%s", repaired, snapshot)
    return repaired
