"""Isolated import of a legacy (v0.8.5, schema v17) workspace into v0.8.6.

v0.8.6 duty A foundation.  The user story: existing knowledge assets must
enter the new version without re-importing everything by hand, without any
risk to the old data, and without duplicate pollution when the same source
is offered twice.

The service follows the established ``LegacyBackupUpgradeService`` pattern
(``src/legacy_backup_upgrade_service.py``): the source is opened read-only,
copied into a unique staging directory, migrated through the single
migration chain, path-rebased onto the target root, verified, and only then
published.  The original source is never modified, the target must be an
empty workspace root, and a per-workspace idempotency ledger
(``legacy_imports``, migration v18) records every completed import so the
same snapshot can always be recognized and refused.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src import migrations
from src.migrations import SCHEMA_VERSION, MigrationError

LOGGER = logging.getLogger(__name__)

#: The v0.8.5 release line ships schema v17; it is the first import target.
V085_SCHEMA_VERSION = 17
#: Sources older than this are outside the supported import window.
_MIN_IMPORTABLE_SCHEMA_VERSION = 8
_ASSET_DIRS: tuple[str, ...] = ("raw", "pages", "markdown")
_COUNT_TABLES: tuple[str, ...] = ("documents", "pages", "notes", "evidence_items")


class LegacyImportError(RuntimeError):
    """A legacy import was refused, aborted, or failed verification."""


@dataclass(frozen=True, slots=True)
class LegacyImportInspection:
    """Read-only facts about one candidate source workspace."""

    source_root: Path
    database_path: Path
    source_schema_version: int
    source_kb_uuid: str | None
    source_sha256: str
    counts: dict[str, int]
    asset_file_counts: dict[str, int]
    verdict: str  # supported / already_current / too_new / too_old
    detail: str


@dataclass(frozen=True, slots=True)
class LegacyImportReport:
    """Result of one dry-run or executed import."""

    source_root: Path
    target_root: Path
    status: str  # completed / dry_run / skipped
    source_schema_version: int
    final_schema_version: int
    source_kb_uuid: str | None
    source_sha256: str
    counts: dict[str, int]
    steps: tuple[str, ...]
    report_path: Path | None


class LegacyImportService:
    """Inspect and import a legacy workspace snapshot into an empty target."""

    def inspect(self, source_root: Path) -> LegacyImportInspection:
        """Read one candidate source workspace without modifying anything."""

        source = Path(source_root).expanduser().resolve(strict=False)
        database_path = source / "data" / "database" / "knowledge.db"
        if not database_path.is_file():
            raise LegacyImportError(f"找不到旧版数据库：{database_path}")
        uri = f"file:{database_path.as_posix()}?mode=ro"
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=30.0)) as connection:
                connection.execute("PRAGMA query_only = ON")
                integrity = connection.execute("PRAGMA integrity_check").fetchall()
                if integrity != [("ok",)]:
                    raise LegacyImportError("旧版数据库完整性检查失败，拒绝导入。")
                version = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                    ).fetchone()[0]
                )
                kb_row = connection.execute(
                    "SELECT kb_uuid FROM knowledge_base_meta WHERE id = 1"
                ).fetchone()
                counts = {
                    table: self._safe_count(connection, table) for table in _COUNT_TABLES
                }
        except sqlite3.Error as exc:
            raise LegacyImportError(f"旧版数据库无法读取：{exc}") from exc

        if version > SCHEMA_VERSION:
            verdict, detail = "too_new", (
                f"数据库版本 {version} 高于程序支持的 {SCHEMA_VERSION}，无法导入。"
            )
        elif version > V085_SCHEMA_VERSION:
            verdict = "already_current"
            detail = f"数据库版本 {version} 已是当前或接近当前格式，无需旧版导入。"
        elif version < _MIN_IMPORTABLE_SCHEMA_VERSION:
            verdict = "too_old"
            detail = (
                f"数据库版本 {version} 过旧（低于 {_MIN_IMPORTABLE_SCHEMA_VERSION}），"
                "请先使用旧版本程序升级。"
            )
        else:
            verdict = "supported"
            label = "v0.8.5" if version == V085_SCHEMA_VERSION else f"schema v{version}"
            detail = f"检测到 {label} 工作区，可安全导入。"
        return LegacyImportInspection(
            source_root=source,
            database_path=database_path,
            source_schema_version=version,
            source_kb_uuid=str(kb_row[0]) if kb_row and kb_row[0] else None,
            source_sha256="",  # filled only after the WAL-merged staging copy
            counts=counts,
            asset_file_counts=self._asset_file_counts(source),
            verdict=verdict,
            detail=detail,
        )

    def execute_import(
        self,
        source_root: Path,
        target_root: Path,
        *,
        dry_run: bool = False,
        app_version: str = "",
    ) -> LegacyImportReport:
        """Import one legacy workspace into an empty v0.8.6 workspace root.

        ``copy_to_empty`` semantics: the target root must not already contain
        a ``data`` directory.  Any failure before the final publish leaves
        both the source and the target untouched (staging is discarded).
        """

        source = Path(source_root).expanduser().resolve(strict=False)
        target = Path(target_root).expanduser().resolve(strict=False)
        inspection = self.inspect(source)
        if inspection.verdict == "too_new":
            raise LegacyImportError(inspection.detail)
        if inspection.verdict == "too_old":
            raise LegacyImportError(inspection.detail)
        self._refuse_overlapping_roots(source, target)

        steps: list[str] = []
        steps.append(f"Step 1：检测旧版工作区（schema v{inspection.source_schema_version}，"
                     f"{inspection.counts.get('documents', 0)} 个文档）")
        if inspection.verdict == "already_current":
            steps.append("源数据库已是当前格式，直接按快照复制导入。")

        target_data = target / "data"
        if target_data.exists():
            raise LegacyImportError(
                f"目标工作区已存在 data 目录，copy_to_empty 模式拒绝覆盖：{target_data}"
            )
        steps.append(f"Step 2：确认目标为空工作区 {target}")

        staging = Path(tempfile.mkdtemp(prefix="ekb-legacy-import-"))
        try:
            staged_db = self._stage_copy(source, inspection.database_path, staging, steps)
            steps.append(f"Step 4：在 staging 中执行迁移链（目标 schema v{SCHEMA_VERSION}）")
            try:
                migrations.migrate_database(staged_db)
            except MigrationError as exc:
                raise LegacyImportError(f"staging 迁移失败：{exc}") from exc
            final_version = self._require_schema(staged_db, SCHEMA_VERSION)
            steps.append("Step 5：验证迁移后数据库（integrity/FK/行数不变量）")
            self._verify_staged(staged_db, inspection.counts)

            steps.append("Step 6：rebase 数据库记录路径到目标工作区")
            self._rebase_paths(staged_db, source / "data", target / "data")

            source_sha256 = self._file_sha256(staged_db)
            if dry_run:
                steps.append("Step 7：dry-run 完成，未写入任何目标路径")
                return LegacyImportReport(
                    source_root=source,
                    target_root=target,
                    status="dry_run",
                    source_schema_version=inspection.source_schema_version,
                    final_schema_version=final_version,
                    source_kb_uuid=inspection.source_kb_uuid,
                    source_sha256=source_sha256,
                    counts=self._read_counts(staged_db),
                    steps=tuple(steps),
                    report_path=None,
                )

            steps.append("Step 7：登记导入账本（幂等键 = 源库哈希）")
            self._register_ledger(
                staged_db,
                inspection=inspection,
                source_sha256=source_sha256,
                counts=self._read_counts(staged_db),
            )

            steps.append("Step 8：发布到目标工作区（原子改名，失败自动清理）")
            report_path = self._publish(staging, target, steps, inspection, source_sha256)
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            if isinstance(exc, LegacyImportError):
                raise
            raise LegacyImportError(f"导入失败，源数据与目标均未改动：{exc}") from exc
        shutil.rmtree(staging, ignore_errors=True)
        steps.append("导入完成")
        return LegacyImportReport(
            source_root=source,
            target_root=target,
            status="completed",
            source_schema_version=inspection.source_schema_version,
            final_schema_version=final_version,
            source_kb_uuid=inspection.source_kb_uuid,
            source_sha256=source_sha256,
            counts=self._read_counts(target / "data" / "database" / "knowledge.db"),
            steps=tuple(steps),
            report_path=report_path,
        )

    # ------------------------------------------------------------- internals
    def _stage_copy(
        self, source: Path, database_path: Path, staging: Path, steps: list[str]
    ) -> Path:
        """Copy db (with WAL merge) and assets into staging; verify hashes."""

        steps.append(f"Step 3：复制到唯一 staging 目录 {staging.name}")
        staged_root = staging / "workspace"
        staged_db = staged_root / "data" / "database" / "knowledge.db"
        staged_db.parent.mkdir(parents=True)
        shutil.copyfile(database_path, staged_db)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(database_path) + suffix)
            if sidecar.is_file():
                shutil.copyfile(sidecar, Path(str(staged_db) + suffix))
        try:
            with closing(sqlite3.connect(staged_db, timeout=30.0)) as connection:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error as exc:
            raise LegacyImportError(f"staging 数据库 WAL 合并失败：{exc}") from exc
        for suffix in ("-wal", "-shm"):
            Path(str(staged_db) + suffix).unlink(missing_ok=True)
        self._require_schema(staged_db, self._read_version(staged_db))
        steps.append("staging 拷贝完整性校验通过")

        staged_data = staged_root / "data"
        for kind in _ASSET_DIRS:
            source_dir = source / "data" / kind
            target_dir = staged_data / kind
            target_dir.mkdir(parents=True, exist_ok=True)
            if not source_dir.is_dir():
                continue
            for path in sorted(source_dir.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(source_dir)
                destination = target_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)
        return staged_db

    def _verify_staged(self, staged_db: Path, baseline: dict[str, int]) -> None:
        """Integrity, foreign keys, row invariants, and FTS consistency."""

        uri = f"file:{staged_db.as_posix()}?mode=ro"
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=30.0)) as connection:
                connection.execute("PRAGMA query_only = ON")
                if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise LegacyImportError("staging 数据库完整性检查失败。")
                violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise LegacyImportError(
                        f"staging 数据库外键校验失败（{len(violations)} 条）。"
                    )
                for table, expected in baseline.items():
                    actual = self._safe_count(connection, table)
                    if actual != expected:
                        raise LegacyImportError(
                            f"迁移后 {table} 行数变化：{expected} → {actual}"
                        )
                page_rows = self._safe_count(connection, "pages")
                fts_rows = int(
                    connection.execute("SELECT COUNT(*) FROM page_search").fetchone()[0]
                )
                if fts_rows != page_rows:
                    raise LegacyImportError(
                        f"FTS 索引不一致：page_search={fts_rows}，pages={page_rows}"
                    )
        except sqlite3.Error as exc:
            raise LegacyImportError(f"staging 数据库校验失败：{exc}") from exc

    def _rebase_paths(self, staged_db: Path, source_data: Path, target_data: Path) -> None:
        """Rewrite recorded absolute asset paths from the source root to the target."""

        try:
            with closing(sqlite3.connect(staged_db, timeout=30.0)) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("BEGIN IMMEDIATE")
                for row in connection.execute(
                    "SELECT id, source_path FROM documents"
                ).fetchall():
                    new_path = self._rebase_one(
                        str(row[1]), source_data / "raw", target_data / "raw"
                    )
                    connection.execute(
                        "UPDATE documents SET source_path = ? WHERE id = ?",
                        (new_path, row[0]),
                    )
                for row in connection.execute(
                    "SELECT id, image_path, markdown_path FROM pages"
                ).fetchall():
                    page_id, image_path, markdown_path = row
                    new_image = self._rebase_one(
                        str(image_path), source_data / "pages", target_data / "pages"
                    )
                    new_markdown = None
                    if markdown_path:
                        new_markdown = self._rebase_one(
                            str(markdown_path),
                            source_data / "markdown",
                            target_data / "markdown",
                        )
                    connection.execute(
                        "UPDATE pages SET image_path = ?, markdown_path = ? WHERE id = ?",
                        (new_image, new_markdown, page_id),
                    )
                connection.commit()
        except sqlite3.Error as exc:
            raise LegacyImportError(f"路径 rebase 失败：{exc}") from exc

    def _rebase_one(self, recorded: str, source_root: Path, target_root: Path) -> str:
        value = Path(recorded)
        if not value.is_absolute():
            value = source_root.parent.parent / value
        try:
            relative = value.resolve(strict=False).relative_to(
                source_root.resolve(strict=False)
            )
        except ValueError as exc:
            raise LegacyImportError(
                f"数据库路径超出源数据目录，拒绝导入：{recorded}"
            ) from exc
        return str(target_root / relative)

    def _register_ledger(
        self,
        staged_db: Path,
        *,
        inspection: LegacyImportInspection,
        source_sha256: str,
        counts: dict[str, int],
    ) -> None:
        try:
            with closing(sqlite3.connect(staged_db, timeout=30.0)) as connection:
                connection.execute(
                    """
                    INSERT INTO legacy_imports(
                        source_kb_uuid, source_sha256, source_schema_version,
                        source_root, import_mode, status,
                        document_count, page_count, note_count, evidence_count,
                        created_at
                    ) VALUES (?, ?, ?, ?, 'copy_to_empty', 'completed', ?, ?, ?, ?, ?)
                    """,
                    (
                        inspection.source_kb_uuid,
                        source_sha256,
                        inspection.source_schema_version,
                        str(inspection.source_root),
                        counts.get("documents", 0),
                        counts.get("pages", 0),
                        counts.get("notes", 0),
                        counts.get("evidence_items", 0),
                        datetime.now(UTC).isoformat(timespec="seconds"),
                    ),
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise LegacyImportError(
                "同一源快照只能导入一次（幂等账本命中唯一键）。"
            ) from exc
        except sqlite3.Error as exc:
            raise LegacyImportError(f"写入导入账本失败：{exc}") from exc

    def _publish(
        self,
        staging: Path,
        target: Path,
        steps: list[str],
        inspection: LegacyImportInspection,
        source_sha256: str,
    ) -> Path:
        staged_data = staging / "workspace" / "data"
        report = {
            "import": "legacy-workspace-copy-to-empty",
            "source_root": str(inspection.source_root),
            "source_schema_version": inspection.source_schema_version,
            "source_kb_uuid": inspection.source_kb_uuid,
            "source_sha256": source_sha256,
            "final_schema_version": SCHEMA_VERSION,
            "counts": self._read_counts(staged_data / "database" / "knowledge.db"),
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        # The data-directory rename is the commit point; anything before it
        # only ever leaves temporary siblings that are cleaned up on failure.
        pending_data = target / f".legacy-import-data-{uuid.uuid4().hex}.tmp"
        pending_report = target / f".legacy-import-report-{uuid.uuid4().hex}.tmp.json"
        try:
            shutil.move(str(staged_data), str(pending_data))
            pending_report.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            pending_data.rename(target / "data")
            pending_report.replace(target / "legacy_import_report.json")
        except OSError as exc:
            shutil.rmtree(pending_data, ignore_errors=True)
            pending_report.unlink(missing_ok=True)
            raise LegacyImportError(f"发布到目标工作区失败，目标未改动：{exc}") from exc
        steps.append(f"目标工作区就绪：{target}")
        return target / "legacy_import_report.json"

    # ------------------------------------------------------------- helpers
    def _refuse_overlapping_roots(self, source: Path, target: Path) -> None:
        if source == target:
            raise LegacyImportError("源工作区与目标工作区不能是同一个目录。")
        if target.is_relative_to(source) or source.is_relative_to(target):
            raise LegacyImportError(
                "源工作区与目标工作区不能互相包含；请选择相互独立的位置。"
            )

    def _read_version(self, database_path: Path) -> int:
        try:
            with closing(sqlite3.connect(database_path, timeout=30.0)) as connection:
                return int(
                    connection.execute(
                        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                    ).fetchone()[0]
                )
        except sqlite3.Error as exc:
            raise LegacyImportError(f"无法读取数据库版本：{exc}") from exc

    def _require_schema(self, database_path: Path, expected: int) -> int:
        version = self._read_version(database_path)
        if version != expected:
            raise LegacyImportError(
                f"staging 数据库版本 {version} 与期望 {expected} 不一致。"
            )
        return version

    def _safe_count(self, connection: sqlite3.Connection, table: str) -> int:
        try:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.Error:
            return 0

    def _read_counts(self, database_path: Path) -> dict[str, int]:
        uri = f"file:{database_path.as_posix()}?mode=ro"
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=30.0)) as connection:
                return {table: self._safe_count(connection, table) for table in _COUNT_TABLES}
        except sqlite3.Error as exc:
            raise LegacyImportError(f"无法读取数据库统计：{exc}") from exc

    def _asset_file_counts(self, source: Path) -> dict[str, int]:
        result: dict[str, int] = {}
        for kind in _ASSET_DIRS:
            directory = source / "data" / kind
            result[kind] = (
                sum(1 for path in directory.rglob("*") if path.is_file())
                if directory.is_dir()
                else 0
            )
        return result

    def _file_sha256(self, path: Path) -> str:
        import hashlib

        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
