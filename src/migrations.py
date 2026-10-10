"""Transactional SQLite schema migrations with automatic pre-upgrade backups."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

LOGGER = logging.getLogger(__name__)
SCHEMA_VERSION = 36

# 仅用于 Phase 2B-V 失败注入验证；生产运行时恒为 None，永不触发。
_V10_INJECTION_POINT: str | None = None
# 仅用于 Phase 3B 失败注入验证；生产运行时恒为 None，永不触发。
_V11_INJECTION_POINT: str | None = None
# 仅用于 Phase 2-A v12 失败注入验证；生产运行时恒为 None，永不触发。
_V12_INJECTION_POINT: str | None = None
# 仅用于 v0.7 Phase 1 v13 失败注入验证；生产运行时恒为 None，永不触发。
_V13_INJECTION_POINT: str | None = None
# 仅用于 v0.7.4 v14 失败注入验证；生产运行时恒为 None，永不触发。
_V14_INJECTION_POINT: str | None = None
# 仅用于 v0.8.3 v15 失败注入验证；生产运行时恒为 None，永不触发。
_V15_INJECTION_POINT: str | None = None
# 仅用于 v0.8.4 AI Ledger attribution 失败注入验证；生产运行时恒为 None。
_V16_INJECTION_POINT: str | None = None


def _inject_v10_failure(point: str) -> None:
    """Raise inside ``_apply_version_ten`` when ``point`` matches the test hook."""

    if _V10_INJECTION_POINT == point:
        raise MigrationError(f"v10 迁移失败注入点：{point}")


def _inject_v11_failure(point: str) -> None:
    """Raise inside ``_apply_version_eleven`` when ``point`` matches the test hook."""

    if _V11_INJECTION_POINT == point:
        raise MigrationError(f"v11 迁移失败注入点：{point}")


def _inject_v12_failure(point: str) -> None:
    """Raise inside ``_apply_version_twelve`` when ``point`` matches the test hook."""

    if _V12_INJECTION_POINT == point:
        raise MigrationError(f"v12 迁移失败注入点：{point}")


def _inject_v13_failure(point: str) -> None:
    """Raise inside ``_apply_version_thirteen`` when ``point`` matches the hook."""

    if _V13_INJECTION_POINT == point:
        raise MigrationError(f"v13 迁移失败注入点：{point}")


def _inject_v14_failure(point: str) -> None:
    """Raise inside ``_apply_version_fourteen`` when ``point`` matches the hook."""

    if _V14_INJECTION_POINT == point:
        raise MigrationError(f"v14 迁移失败注入点：{point}")


def _inject_v15_failure(point: str) -> None:
    """Raise inside ``_apply_version_fifteen`` when ``point`` matches the hook."""

    if _V15_INJECTION_POINT == point:
        raise MigrationError(f"v15 迁移失败注入点：{point}")


def _inject_v16_failure(point: str) -> None:
    """Raise inside ``_apply_version_sixteen`` when ``point`` matches."""

    if _V16_INJECTION_POINT == point:
        raise MigrationError(f"v16 迁移失败注入点：{point}")


class MigrationError(RuntimeError):
    """Raised when the database cannot be backed up or migrated safely."""


def migrate_database(database_path: Path) -> Path | None:
    """Migrate ``database_path`` to the latest schema and return any backup path.

    Existing non-empty databases are backed up with SQLite's online backup API
    before a version-changing write. Migration v2 itself is one transaction, so
    an error leaves the source database at its previous schema version.
    """

    database_path.parent.mkdir(parents=True, exist_ok=True)
    current_version = _read_schema_version(database_path)
    if current_version > SCHEMA_VERSION:
        raise MigrationError(
            f"数据库版本 {current_version} 高于程序支持的 {SCHEMA_VERSION}，请升级程序。"
        )

    backup_path: Path | None = None
    needs_backup = (
        database_path.exists()
        and database_path.stat().st_size > 0
        and current_version < SCHEMA_VERSION
    )
    if needs_backup:
        backup_path = backup_database(database_path, current_version)

    connection = sqlite3.connect(database_path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        _validate_migration_ledger(connection)
        _validate_database_integrity(connection, stage="迁移前")
        current_version = _connection_schema_version(connection)
        if current_version < 1:
            _apply_version_one(connection)
            current_version = 1
        if current_version < 2:
            _apply_version_two(connection)
            current_version = 2
        if current_version < 3:
            _apply_version_three(connection)
            current_version = 3
        if current_version < 4:
            _apply_version_four(connection)
        if current_version < 5:
            _apply_version_five(connection)
        if current_version < 6:
            _apply_version_six(connection)
        if current_version < 7:
            _apply_version_seven(connection)
        if current_version < 8:
            _apply_version_eight(connection)
        if current_version < 9:
            _apply_version_nine(connection)
        if current_version < 10:
            _apply_version_ten(connection)
        if current_version < 11:
            _apply_version_eleven(connection)
            current_version = 11
        if current_version < 12:
            _apply_version_twelve(connection)
            current_version = 12
        if current_version < 13:
            _apply_version_thirteen(connection)
            current_version = 13
        if current_version < 14:
            _apply_version_fourteen(connection)
            current_version = 14
        if current_version < 15:
            _apply_version_fifteen(connection)
            current_version = 15
        if current_version < 16:
            _apply_version_sixteen(connection)
            current_version = 16
        if current_version < 17:
            _apply_version_seventeen(connection)
            current_version = 17
        if current_version < 18:
            _apply_version_eighteen(connection)
            current_version = 18
        if current_version < 19:
            _apply_version_nineteen(connection)
            current_version = 19
        if current_version < 20:
            _apply_version_twenty(connection)
            current_version = 20
        if current_version < 21:
            _apply_version_twenty_one(connection)
            current_version = 21
        if current_version < 22:
            _apply_version_twenty_two(connection)
            current_version = 22
        if current_version < 23:
            _apply_version_twenty_three(connection)
            current_version = 23
        if current_version < 24:
            _apply_version_twenty_four(connection)
            current_version = 24
        if current_version < 25:
            _apply_version_twenty_five(connection)
            current_version = 25
        if current_version < 26:
            _apply_version_twenty_six(connection)
            current_version = 26
        if current_version < 27:
            _apply_version_twenty_seven(connection)
            current_version = 27
        if current_version < 28:
            _apply_version_twenty_eight(connection)
            current_version = 28
        if current_version < 29:
            _apply_version_twenty_nine(connection)
            current_version = 29
        if current_version < 30:
            _apply_version_thirty(connection)
            current_version = 30
        if current_version < 31:
            _apply_version_thirty_one(connection)
            current_version = 31
        if current_version < 32:
            _apply_version_thirty_two(connection)
            current_version = 32
        if current_version < 33:
            _apply_version_thirty_three(connection)
            current_version = 33
        if current_version < 34:
            _apply_version_thirty_four(connection)
            current_version = 34
        if current_version < 35:
            _apply_version_thirty_five(connection)
            current_version = 35
        if current_version < 36:
            _apply_version_thirty_six(connection)
            current_version = 36
        if current_version >= 17:
            _validate_version_fifteen_schema(connection)
            _validate_version_sixteen_schema(connection)
            _validate_version_seventeen_schema(connection)
        if current_version >= 18:
            _validate_version_eighteen_schema(connection)
        if current_version >= 19:
            _validate_version_nineteen_schema(connection)
        if current_version >= 20:
            _validate_version_twenty_schema(connection)
        if current_version >= 21:
            _validate_version_twenty_one_schema(connection)
        if current_version >= 22:
            _validate_version_twenty_two_schema(connection)
        if current_version >= 23:
            _validate_version_twenty_three_schema(connection)
        if current_version >= 24:
            _validate_version_twenty_four_schema(connection)
        if current_version >= 25:
            _validate_version_twenty_five_schema(connection)
        if current_version >= 26:
            _validate_version_twenty_six_schema(connection)
        if current_version >= 27:
            _validate_version_twenty_seven_schema(connection)
        if current_version >= 28:
            _validate_version_twenty_eight_schema(connection)
        if current_version >= 29:
            _validate_version_twenty_nine_schema(connection)
        if current_version >= 30:
            _validate_version_thirty_schema(connection)
        if current_version >= 31:
            _validate_version_thirty_one_schema(connection)
        if current_version >= 32:
            _validate_version_thirty_two_schema(connection)
        if current_version >= 33:
            _validate_version_thirty_three_schema(connection)
        if current_version >= 34:
            _validate_version_thirty_four_schema(connection)
        if current_version >= 35:
            _validate_version_thirty_five_schema(connection)
        if current_version >= 36:
            _validate_version_thirty_six_schema(connection)
        connection.execute("PRAGMA foreign_keys = ON")
        _validate_database_integrity(connection, stage="迁移后")
    except Exception as exc:
        connection.rollback()
        LOGGER.exception("数据库迁移失败，原数据库和迁移前备份均已保留")
        if isinstance(exc, MigrationError):
            raise
        raise MigrationError(
            f"数据库迁移失败：{exc}。原数据库与迁移前备份均已保留。"
        ) from exc
    finally:
        connection.close()
    return backup_path


def backup_database(database_path: Path, version: int | None = None) -> Path:
    """Create and verify a consistent SQLite backup without modifying the source."""

    backup_dir = database_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    version_label = f"v{version}" if version else "legacy"
    backup_path = backup_dir / f"{database_path.stem}.{version_label}.{timestamp}.db"
    try:
        with closing(sqlite3.connect(database_path)) as source, closing(
            sqlite3.connect(backup_path)
        ) as destination:
            source.backup(destination)
            destination.commit()
        with closing(sqlite3.connect(backup_path)) as verification:
            integrity = verification.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            backup_path.unlink(missing_ok=True)
            raise MigrationError(f"数据库备份完整性检查失败：{integrity}")
    except Exception as exc:
        backup_path.unlink(missing_ok=True)
        if isinstance(exc, MigrationError):
            raise
        raise MigrationError(f"无法创建迁移前数据库备份：{exc}") from exc
    LOGGER.info("已创建数据库迁移前备份：%s", backup_path)
    return backup_path


def _read_schema_version(database_path: Path) -> int:
    if not database_path.exists() or database_path.stat().st_size == 0:
        return 0
    try:
        connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
        try:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            if table is None:
                return 0
            return _connection_schema_version(connection)
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise MigrationError(f"无法读取现有数据库版本：{exc}") from exc


def _connection_schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()
    return int(row[0])


def _validate_migration_ledger(connection: sqlite3.Connection) -> None:
    """Reject a non-contiguous migration ledger instead of trusting MAX(version)."""

    versions = [
        int(row[0])
        for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    ]
    expected = list(range(1, (versions[-1] if versions else 0) + 1))
    if versions != expected:
        raise MigrationError(
            "schema_migrations 版本记录不连续："
            f"实际={versions}，期望={expected}"
        )


def _validate_database_integrity(
    connection: sqlite3.Connection, *, stage: str
) -> None:
    """Validate source/target integrity while the active transaction can roll back."""

    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise MigrationError(f"{stage}数据库完整性检查失败：{integrity}")
    foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_violations:
        raise MigrationError(
            f"{stage}发现 {len(foreign_key_violations)} 条外键违规记录"
        )


def _validate_version_fifteen_schema(connection: sqlite3.Connection) -> None:
    """Require the v15 name to be the expected FTS5 virtual table and shape."""

    row = connection.execute(
        "SELECT type, sql FROM sqlite_master WHERE name='page_visual_index'"
    ).fetchone()
    sql = str(row[1] or "").casefold() if row is not None else ""
    compact_sql = "".join(sql.split())
    columns = [
        str(column[1])
        for column in connection.execute(
            "PRAGMA table_info(page_visual_index)"
        ).fetchall()
    ]
    if (
        row is None
        or str(row[0]) != "table"
        or "createvirtualtablepage_visual_index" not in compact_sql
        or "usingfts5(visual_text,page_idunindexed)" not in compact_sql
        or columns != ["visual_text", "page_id"]
    ):
        raise MigrationError(
            "schema v15 的 page_visual_index 不是预期的 FTS5 虚拟表"
        )


def _validate_version_sixteen_schema(connection: sqlite3.Connection) -> None:
    """Require additive provider attribution columns on ``ai_calls``."""

    columns = {
        str(row[1]): row
        for row in connection.execute("PRAGMA table_info(ai_calls)").fetchall()
    }
    provider = columns.get("provider")
    resolved_model = columns.get("resolved_model")
    if (
        provider is None
        or int(provider[3]) != 1
        or str(provider[4]).strip("'\"") != "qwen"
        or resolved_model is None
    ):
        raise MigrationError("schema v16 的 ai_calls Provider attribution 字段无效")
    invalid = connection.execute(
        "SELECT COUNT(*) FROM ai_calls WHERE provider NOT IN "
        "('deepseek', 'qwen', 'kimi', 'hunyuan')"
    ).fetchone()[0]
    if int(invalid) != 0:
        raise MigrationError("schema v16 的 ai_calls 包含非法 Provider ID")


def _validate_version_seventeen_schema(connection: sqlite3.Connection) -> None:
    """Require the append-only request-level Agent audit ledger."""

    expected = [
        "id", "run_id", "request_id", "started_at", "duration_ms",
        "decision_status", "decision_finish_reason", "decision_output_chars",
        "decision_output_tokens", "decision_tool", "decision_arguments", "tool_status",
        "tool_result_status", "result_count", "top_evidence_page_ids",
        "final_status", "final_finish_reason",
        "final_declared_insufficient", "evidence_count", "evidence_page_ids",
        "evidence_excerpt_chars", "ui_failure_reason_code", "outcome",
        "error_code", "created_at",
    ]
    actual = [
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(agent_run_audits)"
        ).fetchall()
    ]
    if actual != expected:
        raise MigrationError("schema v17 的 Agent 请求审计表结构无效")


def _apply_version_one(connection: sqlite3.Connection) -> None:
    """Install the historical v0.0.1 schema for a fresh database."""

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL CHECK (length(trim(title)) > 0),
            filename TEXT NOT NULL CHECK (length(trim(filename)) > 0),
            source_path TEXT NOT NULL CHECK (length(trim(source_path)) > 0),
            sha256 TEXT NOT NULL UNIQUE CHECK (length(sha256) = 64),
            page_count INTEGER NOT NULL DEFAULT 0 CHECK (page_count >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL REFERENCES documents(id),
            page_number INTEGER NOT NULL CHECK (page_number > 0),
            image_path TEXT NOT NULL CHECK (length(trim(image_path)) > 0),
            extracted_text TEXT NOT NULL DEFAULT '',
            markdown_content TEXT NOT NULL DEFAULT '',
            markdown_path TEXT,
            status TEXT NOT NULL CHECK (status IN ('ready', 'pending')),
            search_extracted_text TEXT NOT NULL DEFAULT '',
            search_markdown_content TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (document_id, page_number)
        );
        CREATE INDEX IF NOT EXISTS idx_pages_document_page ON pages(document_id, page_number);
        CREATE INDEX IF NOT EXISTS idx_pages_status ON pages(status);
        CREATE VIRTUAL TABLE IF NOT EXISTS page_search USING fts5(
            search_extracted_text,
            search_markdown_content,
            content='pages',
            content_rowid='id',
            tokenize='unicode61 remove_diacritics 2'
        );
        CREATE TRIGGER IF NOT EXISTS pages_fts_insert AFTER INSERT ON pages BEGIN
            INSERT INTO page_search(rowid, search_extracted_text, search_markdown_content)
            VALUES (new.id, new.search_extracted_text, new.search_markdown_content);
        END;
        CREATE TRIGGER IF NOT EXISTS pages_fts_delete AFTER DELETE ON pages BEGIN
            INSERT INTO page_search(
                page_search, rowid, search_extracted_text, search_markdown_content
            ) VALUES (
                'delete', old.id, old.search_extracted_text, old.search_markdown_content
            );
        END;
        CREATE TRIGGER IF NOT EXISTS pages_fts_update AFTER UPDATE ON pages BEGIN
            INSERT INTO page_search(
                page_search, rowid, search_extracted_text, search_markdown_content
            ) VALUES (
                'delete', old.id, old.search_extracted_text, old.search_markdown_content
            );
            INSERT INTO page_search(rowid, search_extracted_text, search_markdown_content)
            VALUES (new.id, new.search_extracted_text, new.search_markdown_content);
        END;
        """
    )
    connection.execute("INSERT INTO page_search(page_search) VALUES ('rebuild')")
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)", (_utc_now(),)
    )
    connection.commit()


def _apply_version_two(connection: sqlite3.Connection) -> None:
    """Add v0.0.2 status, organization, import history, and search structures."""

    try:
        connection.execute("BEGIN IMMEDIATE")
        existing_document_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(documents)")
        }
        document_columns = {
            "import_status": "TEXT NOT NULL DEFAULT 'completed'",
            "processed_page_count": "INTEGER NOT NULL DEFAULT 0",
            "text_page_count": "INTEGER NOT NULL DEFAULT 0",
            "review_page_count": "INTEGER NOT NULL DEFAULT 0",
            "import_error": "TEXT NOT NULL DEFAULT ''",
            "imported_at": "TEXT",
        }
        for name, definition in document_columns.items():
            if name not in existing_document_columns:
                connection.execute(f"ALTER TABLE documents ADD COLUMN {name} {definition}")
        connection.execute(
            """
            UPDATE documents SET
                processed_page_count = CASE
                    WHEN processed_page_count = 0 THEN page_count ELSE processed_page_count END,
                text_page_count = CASE
                    WHEN text_page_count = 0 THEN (
                        SELECT COUNT(*) FROM pages
                        WHERE pages.document_id = documents.id AND status = 'ready'
                    ) ELSE text_page_count END,
                review_page_count = CASE
                    WHEN review_page_count = 0 THEN (
                        SELECT COUNT(*) FROM pages
                        WHERE pages.document_id = documents.id AND status = 'pending'
                    ) ELSE review_page_count END,
                imported_at = COALESCE(imported_at, created_at)
            """
        )

        for trigger in ("pages_fts_insert", "pages_fts_delete", "pages_fts_update"):
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        connection.execute("DROP TABLE IF EXISTS page_search")
        connection.execute(
            """
            CREATE TABLE pages_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                page_number INTEGER NOT NULL CHECK (page_number > 0),
                image_path TEXT NOT NULL CHECK (length(trim(image_path)) > 0),
                extracted_text TEXT NOT NULL DEFAULT '',
                ocr_text TEXT NOT NULL DEFAULT '',
                markdown_content TEXT NOT NULL DEFAULT '',
                markdown_path TEXT,
                status TEXT NOT NULL CHECK (status IN (
                    'text_extracted', 'ocr_completed', 'pending_review',
                    'manually_reviewed', 'failed'
                )),
                processing_error TEXT NOT NULL DEFAULT '',
                search_extracted_text TEXT NOT NULL DEFAULT '',
                search_ocr_text TEXT NOT NULL DEFAULT '',
                search_markdown_content TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (document_id, page_number)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO pages_v2(
                id, document_id, page_number, image_path, extracted_text, ocr_text,
                markdown_content, markdown_path, status, processing_error,
                search_extracted_text, search_ocr_text, search_markdown_content,
                created_at, updated_at
            )
            SELECT
                id, document_id, page_number, image_path, extracted_text, '',
                markdown_content, markdown_path,
                CASE
                    WHEN length(trim(markdown_content)) > 0 THEN 'manually_reviewed'
                    WHEN status = 'ready' THEN 'text_extracted'
                    ELSE 'pending_review'
                END,
                '', search_extracted_text, '', search_markdown_content,
                created_at, updated_at
            FROM pages
            """
        )
        connection.execute("DROP TABLE pages")
        connection.execute("ALTER TABLE pages_v2 RENAME TO pages")
        connection.execute(
            "CREATE INDEX idx_pages_document_page ON pages(document_id, page_number)"
        )
        connection.execute("CREATE INDEX idx_pages_status ON pages(status)")
        connection.execute("CREATE INDEX idx_documents_import_status ON documents(import_status)")
        _create_v2_tables(connection)
        _create_v2_fts(connection)
        connection.execute("INSERT INTO page_search(page_search) VALUES ('rebuild')")
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (2, ?)", (_utc_now(),)
        )
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v2")
    except Exception:
        connection.rollback()
        raise


def _apply_version_three(connection: sqlite3.Connection) -> None:
    """Add the v0.0.3 manual-review lifecycle without losing v2 process state.

    The historical ``status`` column is retained as the page-processing result.
    ``review_status`` becomes the canonical user workflow state. Because v0.0.2
    automatically marked any saved Markdown as manually reviewed, non-empty
    legacy notes are conservatively migrated to ``draft`` rather than claiming
    that a human explicitly confirmed them.
    """

    try:
        connection.execute("BEGIN IMMEDIATE")
        for trigger in ("pages_fts_insert", "pages_fts_delete", "pages_fts_update"):
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        connection.execute("DROP TABLE IF EXISTS page_search")
        connection.execute(
            """
            CREATE TABLE pages_v3 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                page_number INTEGER NOT NULL CHECK (page_number > 0),
                image_path TEXT NOT NULL CHECK (length(trim(image_path)) > 0),
                extracted_text TEXT NOT NULL DEFAULT '',
                ocr_text TEXT NOT NULL DEFAULT '',
                markdown_content TEXT NOT NULL DEFAULT '',
                markdown_path TEXT,
                status TEXT NOT NULL CHECK (status IN (
                    'text_extracted', 'ocr_completed', 'pending_review',
                    'manually_reviewed', 'failed'
                )),
                review_status TEXT NOT NULL CHECK (review_status IN (
                    'pending', 'draft', 'reviewed', 'skipped', 'failed'
                )),
                processing_error TEXT NOT NULL DEFAULT '',
                search_extracted_text TEXT NOT NULL DEFAULT '',
                search_ocr_text TEXT NOT NULL DEFAULT '',
                search_markdown_content TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                note_updated_at TEXT,
                reviewed_at TEXT,
                last_viewed_at TEXT,
                UNIQUE (document_id, page_number)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO pages_v3(
                id, document_id, page_number, image_path, extracted_text, ocr_text,
                markdown_content, markdown_path, status, review_status,
                processing_error, search_extracted_text, search_ocr_text,
                search_markdown_content, created_at, updated_at, note_updated_at,
                reviewed_at, last_viewed_at
            )
            SELECT
                id, document_id, page_number, image_path, extracted_text, ocr_text,
                markdown_content, markdown_path, status,
                CASE
                    WHEN status = 'failed' AND length(trim(markdown_content)) = 0
                        THEN 'failed'
                    WHEN length(trim(markdown_content)) > 0 THEN 'draft'
                    WHEN status = 'manually_reviewed' THEN 'reviewed'
                    ELSE 'pending'
                END,
                processing_error, search_extracted_text, search_ocr_text,
                search_markdown_content, created_at, updated_at,
                CASE WHEN length(trim(markdown_content)) > 0 THEN updated_at ELSE NULL END,
                CASE
                    WHEN status = 'manually_reviewed'
                         AND length(trim(markdown_content)) = 0
                    THEN updated_at ELSE NULL
                END,
                NULL
            FROM pages
            """
        )
        connection.execute("DROP TABLE pages")
        connection.execute("ALTER TABLE pages_v3 RENAME TO pages")
        connection.execute(
            "CREATE INDEX idx_pages_document_page ON pages(document_id, page_number)"
        )
        connection.execute("CREATE INDEX idx_pages_status ON pages(status)")
        connection.execute(
            "CREATE INDEX idx_pages_review_status "
            "ON pages(review_status, document_id, page_number)"
        )
        _create_v2_fts(connection)
        connection.execute("INSERT INTO page_search(page_search) VALUES ('rebuild')")
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (3, ?)",
            (_utc_now(),),
        )
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v3")
    except Exception:
        connection.rollback()
        raise


def _apply_version_four(connection: sqlite3.Connection) -> None:
    """Add durable evidence baskets without rewriting page or FTS data."""

    fingerprint = _core_data_fingerprint(connection)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE evidence_baskets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL CHECK (
                    length(trim(name)) > 0 AND length(name) <= 100
                ),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE evidence_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                basket_id INTEGER NOT NULL
                    REFERENCES evidence_baskets(id) ON DELETE CASCADE,
                document_id INTEGER NOT NULL
                    REFERENCES documents(id) ON DELETE CASCADE,
                page_id INTEGER NOT NULL
                    REFERENCES pages(id) ON DELETE CASCADE,
                document_title TEXT NOT NULL CHECK (length(trim(document_title)) > 0),
                filename TEXT NOT NULL CHECK (length(trim(filename)) > 0),
                page_number INTEGER NOT NULL CHECK (page_number > 0),
                review_status TEXT NOT NULL CHECK (review_status IN (
                    'pending', 'draft', 'reviewed', 'skipped', 'failed'
                )),
                projects_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]',
                evidence_text TEXT NOT NULL CHECK (length(trim(evidence_text)) > 0),
                text_kind TEXT NOT NULL CHECK (text_kind IN (
                    'original_material', 'user_excerpt'
                )),
                context TEXT NOT NULL DEFAULT '',
                context_kind TEXT NOT NULL CHECK (context_kind IN (
                    'system_generated', 'user_provided'
                )),
                user_note TEXT NOT NULL DEFAULT '' CHECK (length(user_note) <= 4000),
                source_text_sha256 TEXT NOT NULL CHECK (length(source_text_sha256) = 64),
                source_locator TEXT NOT NULL CHECK (length(trim(source_locator)) > 0),
                selection_sha256 TEXT NOT NULL CHECK (length(selection_sha256) = 64),
                added_at TEXT NOT NULL,
                position INTEGER NOT NULL CHECK (position > 0),
                UNIQUE (basket_id, page_id, selection_sha256),
                UNIQUE (basket_id, position)
            )
            """
        )
        connection.execute(
            "CREATE INDEX idx_evidence_items_page ON evidence_items(page_id)"
        )
        connection.execute(
            "CREATE INDEX idx_evidence_items_document ON evidence_items(document_id)"
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (4, ?)",
            (_utc_now(),),
        )
        if _core_data_fingerprint(connection) != fingerprint:
            raise MigrationError("schema v4 迁移改变了现有文档、页面或 FTS 数据")
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v4")
    except Exception:
        connection.rollback()
        raise


def _apply_version_five(connection: sqlite3.Connection) -> None:
    """Add the unified structured-notes table without touching existing data.

    v0.3.0 structured notes: one table, four ``note_type`` values. Ownership is
    mutually exclusive (document notes reference documents; page-scoped notes
    reference pages only). Anchor fields are type-exclusive and validated by
    CHECK constraints so the database itself rejects malformed combinations.
    """

    fingerprint = _core_data_fingerprint(connection)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_type TEXT NOT NULL CHECK (note_type IN (
                    'document', 'page', 'text_selection', 'image_region'
                )),
                document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
                page_id INTEGER REFERENCES pages(id) ON DELETE CASCADE,

                personal_note TEXT NOT NULL CHECK (
                    length(personal_note) BETWEEN 1 AND 20000
                ),

                source_kind TEXT CHECK (source_kind IS NULL
                    OR source_kind IN ('pdf_text', 'ocr_text')),
                source_page_text_sha256 TEXT CHECK (source_page_text_sha256 IS NULL
                    OR length(source_page_text_sha256) = 64),
                source_excerpt_snapshot TEXT CHECK (source_excerpt_snapshot IS NULL
                    OR length(source_excerpt_snapshot) BETWEEN 1 AND 20000),
                selection_start INTEGER,
                selection_end INTEGER,
                user_excerpt TEXT CHECK (user_excerpt IS NULL
                    OR length(user_excerpt) BETWEEN 1 AND 20000),

                region_image_sha256 TEXT CHECK (region_image_sha256 IS NULL
                    OR length(region_image_sha256) = 64),
                region_image_width INTEGER,
                region_image_height INTEGER,
                region_x0 INTEGER,
                region_y0 INTEGER,
                region_x1 INTEGER,
                region_y1 INTEGER,

                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,

                CHECK (
                    note_type = 'document'
                    AND document_id IS NOT NULL
                    AND page_id IS NULL
                OR
                    note_type IN ('page', 'text_selection', 'image_region')
                    AND document_id IS NULL
                    AND page_id IS NOT NULL
                ),

                CHECK (
                    note_type IN ('document', 'page')
                    AND source_kind IS NULL
                    AND source_page_text_sha256 IS NULL
                    AND source_excerpt_snapshot IS NULL
                    AND selection_start IS NULL AND selection_end IS NULL
                    AND user_excerpt IS NULL
                    AND region_image_sha256 IS NULL
                    AND region_image_width IS NULL AND region_image_height IS NULL
                    AND region_x0 IS NULL AND region_y0 IS NULL
                    AND region_x1 IS NULL AND region_y1 IS NULL
                OR
                    note_type = 'text_selection'
                    AND source_kind IS NOT NULL
                    AND source_page_text_sha256 IS NOT NULL
                    AND source_excerpt_snapshot IS NOT NULL
                    AND selection_start IS NOT NULL AND selection_start >= 0
                    AND selection_end IS NOT NULL AND selection_end > selection_start
                    AND length(source_excerpt_snapshot)
                        = selection_end - selection_start
                    AND user_excerpt IS NOT NULL
                    AND region_image_sha256 IS NULL
                    AND region_image_width IS NULL AND region_image_height IS NULL
                    AND region_x0 IS NULL AND region_y0 IS NULL
                    AND region_x1 IS NULL AND region_y1 IS NULL
                OR
                    note_type = 'image_region'
                    AND region_image_sha256 IS NOT NULL
                    AND region_image_width IS NOT NULL AND region_image_width > 0
                    AND region_image_height IS NOT NULL AND region_image_height > 0
                    AND region_x0 IS NOT NULL AND region_x0 >= 0
                    AND region_y0 IS NOT NULL AND region_y0 >= 0
                    AND region_x1 IS NOT NULL AND region_x1 > region_x0
                    AND region_y1 IS NOT NULL AND region_y1 > region_y0
                    AND region_x1 <= region_image_width
                    AND region_y1 <= region_image_height
                    AND source_kind IS NULL
                    AND source_page_text_sha256 IS NULL
                    AND source_excerpt_snapshot IS NULL
                    AND selection_start IS NULL AND selection_end IS NULL
                    AND user_excerpt IS NULL
                )
            )
            """
        )
        connection.execute(
            "CREATE INDEX idx_notes_document ON notes(document_id, updated_at DESC)"
        )
        connection.execute(
            "CREATE INDEX idx_notes_page ON notes(page_id, updated_at DESC)"
        )
        connection.execute(
            "CREATE INDEX idx_notes_type ON notes(note_type, updated_at DESC)"
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (5, ?)",
            (_utc_now(),),
        )
        if _core_data_fingerprint(connection) != fingerprint:
            raise MigrationError("schema v5 迁移改变了现有文档、页面或 FTS 数据")
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v5")
    except Exception:
        connection.rollback()
        raise


def _apply_version_six(connection: sqlite3.Connection) -> None:
    """Add note importance and display preferences without touching existing data.

    v0.3.1 (frozen design): one additive column on ``notes`` (constant default
    'normal', so legacy rows need no rewrite), one single-row preferences table
    and one index. No other schema objects are introduced.
    """

    fingerprint = _core_data_fingerprint(connection)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            ALTER TABLE notes ADD COLUMN importance TEXT NOT NULL DEFAULT 'normal'
                CHECK (importance IN ('primary', 'secondary', 'normal'))
            """
        )
        connection.execute(
            """
            CREATE TABLE note_display_preferences (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                color_primary TEXT NOT NULL DEFAULT '#c0392b'
                    CHECK (color_primary GLOB '#[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'),
                color_secondary TEXT NOT NULL DEFAULT '#2563eb' CHECK (
                    color_secondary GLOB '#[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                ),
                color_normal TEXT NOT NULL DEFAULT '#000000'
                    CHECK (color_normal GLOB '#[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'),
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO note_display_preferences (id, updated_at) VALUES (1, ?)",
            (_utc_now(),),
        )
        connection.execute(
            "CREATE INDEX idx_notes_importance ON notes(importance, updated_at DESC)"
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (6, ?)",
            (_utc_now(),),
        )
        if _core_data_fingerprint(connection) != fingerprint:
            raise MigrationError("schema v6 迁移改变了现有文档、页面或 FTS 数据")
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v6")
    except Exception:
        connection.rollback()
        raise


def _apply_version_seven(connection: sqlite3.Connection) -> None:
    """Rebuild evidence_items with typed evidence and manual confirmation state.

    v0.4.0 (slice 1-3): ``evidence_items`` is rebuilt create-copy-drop-rename
    to carry ``evidence_type`` (page / text_selection / image_region), the
    image-region anchor columns (same CHECK semantics as the notes table) and
    the manual confirmation pair (``confirmation_status`` / ``confirmed_at``).
    Every legacy row is preserved and maps to ``evidence_type='text_selection'``,
    ``confirmation_status='unconfirmed'``, ``confirmed_at=NULL`` and all-NULL
    region columns. The core fingerprint does not cover this table, so the row
    count and id set are verified explicitly inside the same transaction.
    """

    fingerprint = _core_data_fingerprint(connection)
    legacy_item_ids = _evidence_item_ids(connection)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE evidence_items_v7 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                basket_id INTEGER NOT NULL
                    REFERENCES evidence_baskets(id) ON DELETE CASCADE,
                document_id INTEGER NOT NULL
                    REFERENCES documents(id) ON DELETE CASCADE,
                page_id INTEGER NOT NULL
                    REFERENCES pages(id) ON DELETE CASCADE,
                document_title TEXT NOT NULL CHECK (length(trim(document_title)) > 0),
                filename TEXT NOT NULL CHECK (length(trim(filename)) > 0),
                page_number INTEGER NOT NULL CHECK (page_number > 0),
                review_status TEXT NOT NULL CHECK (review_status IN (
                    'pending', 'draft', 'reviewed', 'skipped', 'failed'
                )),
                projects_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]',
                evidence_type TEXT NOT NULL DEFAULT 'text_selection'
                    CHECK (evidence_type IN ('page', 'text_selection', 'image_region')),
                evidence_text TEXT NOT NULL DEFAULT '' CHECK (
                    evidence_type IN ('page', 'image_region')
                    OR length(trim(evidence_text)) > 0
                ),
                text_kind TEXT NOT NULL CHECK (text_kind IN (
                    'original_material', 'user_excerpt'
                )),
                context TEXT NOT NULL DEFAULT '',
                context_kind TEXT NOT NULL CHECK (context_kind IN (
                    'system_generated', 'user_provided'
                )),
                user_note TEXT NOT NULL DEFAULT '' CHECK (length(user_note) <= 4000),
                region_image_sha256 TEXT CHECK (region_image_sha256 IS NULL
                    OR length(region_image_sha256) = 64),
                region_image_width INTEGER,
                region_image_height INTEGER,
                region_x0 INTEGER,
                region_y0 INTEGER,
                region_x1 INTEGER,
                region_y1 INTEGER,
                source_text_sha256 TEXT NOT NULL CHECK (length(source_text_sha256) = 64),
                source_locator TEXT NOT NULL CHECK (length(trim(source_locator)) > 0),
                selection_sha256 TEXT NOT NULL CHECK (length(selection_sha256) = 64),
                confirmation_status TEXT NOT NULL DEFAULT 'unconfirmed'
                    CHECK (confirmation_status IN ('unconfirmed', 'confirmed')),
                confirmed_at TEXT,
                added_at TEXT NOT NULL,
                position INTEGER NOT NULL CHECK (position > 0),
                UNIQUE (basket_id, page_id, selection_sha256),
                UNIQUE (basket_id, position),
                CHECK (
                    confirmation_status = 'confirmed' AND confirmed_at IS NOT NULL
                    OR confirmation_status = 'unconfirmed' AND confirmed_at IS NULL
                ),
                CHECK (
                    evidence_type = 'image_region'
                    AND region_image_sha256 IS NOT NULL
                    AND region_image_width IS NOT NULL AND region_image_width > 0
                    AND region_image_height IS NOT NULL AND region_image_height > 0
                    AND region_x0 IS NOT NULL AND region_x0 >= 0
                    AND region_y0 IS NOT NULL AND region_y0 >= 0
                    AND region_x1 IS NOT NULL AND region_x1 > region_x0
                    AND region_y1 IS NOT NULL AND region_y1 > region_y0
                    AND region_x1 <= region_image_width
                    AND region_y1 <= region_image_height
                OR
                    evidence_type IN ('page', 'text_selection')
                    AND region_image_sha256 IS NULL
                    AND region_image_width IS NULL AND region_image_height IS NULL
                    AND region_x0 IS NULL AND region_y0 IS NULL
                    AND region_x1 IS NULL AND region_y1 IS NULL
                )
            )
            """
        )
        connection.execute(
            """
            INSERT INTO evidence_items_v7(
                id, basket_id, document_id, page_id, document_title, filename,
                page_number, review_status, projects_json, tags_json,
                evidence_type, evidence_text, text_kind, context, context_kind,
                user_note, region_image_sha256, region_image_width,
                region_image_height, region_x0, region_y0, region_x1, region_y1,
                source_text_sha256, source_locator, selection_sha256,
                confirmation_status, confirmed_at, added_at, position
            )
            SELECT
                id, basket_id, document_id, page_id, document_title, filename,
                page_number, review_status, projects_json, tags_json,
                'text_selection', evidence_text, text_kind, context, context_kind,
                user_note, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                source_text_sha256, source_locator, selection_sha256,
                'unconfirmed', NULL, added_at, position
            FROM evidence_items
            """
        )
        connection.execute("DROP TABLE evidence_items")
        connection.execute("ALTER TABLE evidence_items_v7 RENAME TO evidence_items")
        connection.execute(
            "CREATE INDEX idx_evidence_items_page ON evidence_items(page_id)"
        )
        connection.execute(
            "CREATE INDEX idx_evidence_items_document ON evidence_items(document_id)"
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (7, ?)",
            (_utc_now(),),
        )
        if _core_data_fingerprint(connection) != fingerprint:
            raise MigrationError("schema v7 迁移改变了现有文档、页面或 FTS 数据")
        if _evidence_item_ids(connection) != legacy_item_ids:
            raise MigrationError("schema v7 迁移未能完整保留原有证据条目")
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v7")
    except Exception:
        connection.rollback()
        raise


def _apply_version_eight(connection: sqlite3.Connection) -> None:
    """Add the page_embeddings persistence table without touching existing data.

    v0.5.0 Phase 7: page-level embedding persistence. One additive table,
    no rebuild of any existing table. The current embedding of a page is
    uniquely keyed by ``(page_id, model, dimensions, config_version)`` so a
    re-embedding after a text change updates the row in place instead of
    accumulating stale vectors; ``source_text_sha256`` is the freshness
    fingerprint of the embedded text. Rows cascade away with their page.
    """

    fingerprint = _core_data_fingerprint(connection)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE page_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
                source_text_sha256 TEXT NOT NULL CHECK (
                    length(source_text_sha256) = 64
                ),
                model TEXT NOT NULL CHECK (length(trim(model)) > 0),
                dimensions INTEGER NOT NULL CHECK (dimensions > 0),
                config_version INTEGER NOT NULL CHECK (config_version > 0),
                vector BLOB NOT NULL CHECK (length(vector) = 1 + 4 * dimensions),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (page_id, model, dimensions, config_version)
            )
            """
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (8, ?)",
            (_utc_now(),),
        )
        if _core_data_fingerprint(connection) != fingerprint:
            raise MigrationError("schema v8 迁移改变了现有文档、页面或 FTS 数据")
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v8")
    except Exception:
        connection.rollback()
        raise


def _apply_version_nine(connection: sqlite3.Connection) -> None:
    """Add the v0.5.2 knowledge-foundation tables without touching existing data.

    Four additive tables, no rebuild of any existing table:

    - ``knowledge_objects``: the durable, source-linked knowledge asset;
    - ``knowledge_object_sources``: polymorphic source-traceability links
      (target existence is enforced by the service layer, not a foreign key);
    - ``knowledge_relations``: typed directed links between objects;
    - ``knowledge_memory_entries``: user-authored memory plus the automatic
      append-only ``knowledge_change`` log.

    All foreign keys that can be declared in SQLite are declared; document and
    page links use ``ON DELETE SET NULL`` so deleting source material never
    destroys memory entries. The core fingerprint still covers only the v1-v8
    tables, so this migration must be a pure addition.
    """

    fingerprint = _core_data_fingerprint(connection)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE knowledge_objects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK (kind IN (
                    'concept', 'fact', 'principle', 'experience',
                    'problem', 'decision'
                )),
                title TEXT NOT NULL CHECK (
                    length(trim(title)) BETWEEN 1 AND 200
                ),
                content TEXT NOT NULL CHECK (
                    length(content) BETWEEN 1 AND 20000
                ),
                importance TEXT NOT NULL DEFAULT 'normal'
                    CHECK (importance IN ('primary', 'secondary', 'normal')),
                status TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'reviewed', 'archived')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                reviewed_at TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_objects_kind "
            "ON knowledge_objects(kind, updated_at DESC)"
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_objects_importance "
            "ON knowledge_objects(importance, updated_at DESC)"
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_objects_status "
            "ON knowledge_objects(status, updated_at DESC)"
        )
        connection.execute(
            """
            CREATE TABLE knowledge_object_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                knowledge_object_id INTEGER NOT NULL
                    REFERENCES knowledge_objects(id) ON DELETE CASCADE,
                source_type TEXT NOT NULL CHECK (
                    source_type IN ('document', 'page', 'note', 'evidence')
                ),
                source_id INTEGER NOT NULL,
                source_note TEXT NOT NULL DEFAULT ''
                    CHECK (length(source_note) <= 500),
                created_at TEXT NOT NULL,
                UNIQUE (knowledge_object_id, source_type, source_id)
            )
            """
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_object_sources_target "
            "ON knowledge_object_sources(source_type, source_id)"
        )
        connection.execute(
            """
            CREATE TABLE knowledge_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_ko_id INTEGER NOT NULL
                    REFERENCES knowledge_objects(id) ON DELETE CASCADE,
                target_ko_id INTEGER NOT NULL
                    REFERENCES knowledge_objects(id) ON DELETE CASCADE,
                relation_type TEXT NOT NULL CHECK (relation_type IN (
                    'relates_to', 'derived_from', 'supports',
                    'contradicts', 'example_of', 'requires'
                )),
                description TEXT NOT NULL DEFAULT ''
                    CHECK (length(description) <= 1000),
                created_at TEXT NOT NULL,
                UNIQUE (source_ko_id, target_ko_id, relation_type),
                CHECK (source_ko_id <> target_ko_id)
            )
            """
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_relations_source "
            "ON knowledge_relations(source_ko_id, relation_type)"
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_relations_target "
            "ON knowledge_relations(target_ko_id, relation_type)"
        )
        connection.execute(
            """
            CREATE TABLE knowledge_memory_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK (kind IN (
                    'problem_solving', 'experience', 'decision',
                    'knowledge_change'
                )),
                title TEXT NOT NULL CHECK (
                    length(trim(title)) BETWEEN 1 AND 200
                ),
                content TEXT NOT NULL DEFAULT ''
                    CHECK (length(content) <= 20000),
                root_cause TEXT NOT NULL DEFAULT ''
                    CHECK (length(root_cause) <= 4000),
                lesson TEXT NOT NULL DEFAULT ''
                    CHECK (length(lesson) <= 4000),
                knowledge_object_id INTEGER
                    REFERENCES knowledge_objects(id) ON DELETE SET NULL,
                document_id INTEGER
                    REFERENCES documents(id) ON DELETE SET NULL,
                page_id INTEGER
                    REFERENCES pages(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_memory_kind "
            "ON knowledge_memory_entries(kind, updated_at DESC)"
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_memory_ko "
            "ON knowledge_memory_entries(knowledge_object_id, updated_at DESC)"
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (9, ?)",
            (_utc_now(),),
        )
        if _core_data_fingerprint(connection) != fingerprint:
            raise MigrationError("schema v9 迁移改变了现有文档、页面或 FTS 数据")
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v9")
    except Exception:
        connection.rollback()
        raise


def _apply_version_ten(connection: sqlite3.Connection) -> None:
    """Rebuild the knowledge schema onto the v0.5.2 Phase 2B orthogonal model.

    v10 changes (ADR-01/02/04/05/07 of the Phase 2A-R1 decision document):

    - ``knowledge_base_meta``: single-row table with a locally generated UUID v4
      used to build stable IDs (``<kb_uuid>:<object_type>:<local_id>``);
    - ``knowledge_objects`` is rebuilt: the compressed ``status``/``reviewed_at``
      pair is replaced by orthogonal ``lifecycle``/``confirmation_*`` fields plus
      ``authorship``/``epistemic_basis``/``current_revision`` and the
      ``superseded_by_ko_id`` successor pointer (ON DELETE RESTRICT). Migrated
      rows keep ``authorship='user'`` and ``epistemic_basis='unknown_legacy'`` —
      no origin inference is performed on legacy data;
    - ``knowledge_object_sources`` gains fingerprint columns; legacy rows keep
      ``source_fingerprint=NULL`` (the fingerprint state machine is Phase 2C);
    - ``knowledge_memory_entries`` is rebuilt to hold user-authored memory only
      (``knowledge_change`` kind removed, ``status`` added);
    - ``knowledge_object_revisions`` is created as an append-only history table
      with stable identity snapshots and no foreign key, so deleting a
      knowledge object never modifies a revision row. Every legacy
      ``knowledge_change`` row is migrated as a ``legacy_event`` (no fabricated
      before/after) and every v9 object receives one ``legacy_baseline``
      revision representing its full content at migration time.
    """

    fingerprint = _core_data_fingerprint(connection)
    migration_timestamp = _utc_now()
    kb_uuid = str(uuid4())
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE knowledge_base_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                kb_uuid TEXT NOT NULL UNIQUE CHECK (length(kb_uuid) = 36),
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO knowledge_base_meta(id, kb_uuid, created_at) VALUES (1, ?, ?)",
            (kb_uuid, migration_timestamp),
        )
        _inject_v10_failure("v10_meta")

        connection.execute(
            """
            CREATE TABLE knowledge_objects_v10 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK (kind IN (
                    'concept', 'fact', 'principle', 'experience',
                    'problem', 'decision'
                )),
                authorship TEXT NOT NULL DEFAULT 'user'
                    CHECK (authorship IN ('user', 'ai')),
                epistemic_basis TEXT NOT NULL DEFAULT 'unknown_legacy'
                    CHECK (epistemic_basis IN (
                        'source_derived', 'personal_experience',
                        'personal_judgment', 'direct_observation',
                        'decision_record', 'problem_definition',
                        'unknown_legacy'
                    )),
                title TEXT NOT NULL CHECK (
                    length(trim(title)) BETWEEN 1 AND 200
                ),
                content TEXT NOT NULL CHECK (
                    length(content) BETWEEN 1 AND 20000
                ),
                importance TEXT NOT NULL DEFAULT 'normal'
                    CHECK (importance IN ('primary', 'secondary', 'normal')),
                lifecycle TEXT NOT NULL DEFAULT 'active'
                    CHECK (lifecycle IN ('active', 'superseded', 'archived')),
                superseded_by_ko_id INTEGER
                    REFERENCES knowledge_objects_v10(id) ON DELETE RESTRICT,
                confirmation_status TEXT NOT NULL DEFAULT 'unconfirmed'
                    CHECK (confirmation_status IN ('unconfirmed', 'confirmed')),
                confirmed_at TEXT,
                confirmed_revision INTEGER,
                current_revision INTEGER NOT NULL DEFAULT 1
                    CHECK (current_revision >= 1),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (
                    confirmation_status = 'confirmed'
                    AND confirmed_at IS NOT NULL
                    AND confirmed_revision IS NOT NULL
                OR
                    confirmation_status = 'unconfirmed'
                    AND confirmed_at IS NULL
                ),
                CHECK (
                    confirmed_revision IS NULL
                    OR confirmed_revision <= current_revision
                ),
                CHECK (
                    lifecycle IN ('active', 'archived')
                    AND superseded_by_ko_id IS NULL
                OR
                    lifecycle = 'superseded'
                    AND superseded_by_ko_id IS NOT NULL
                )
            )
            """
        )
        connection.execute(
            """
            INSERT INTO knowledge_objects_v10 (
                id, kind, authorship, epistemic_basis, title, content, importance,
                lifecycle, superseded_by_ko_id, confirmation_status, confirmed_at,
                confirmed_revision, current_revision, created_at, updated_at
            )
            SELECT
                id, kind, 'user', 'unknown_legacy', title, content, importance,
                CASE status WHEN 'archived' THEN 'archived' ELSE 'active' END,
                NULL,
                CASE status WHEN 'reviewed' THEN 'confirmed' ELSE 'unconfirmed' END,
                reviewed_at,
                CASE status WHEN 'reviewed' THEN (
                    SELECT COUNT(*) FROM knowledge_memory_entries
                    WHERE kind = 'knowledge_change'
                      AND knowledge_object_id = knowledge_objects.id
                ) + 1 ELSE NULL END,
                (
                    SELECT COUNT(*) FROM knowledge_memory_entries
                    WHERE kind = 'knowledge_change'
                      AND knowledge_object_id = knowledge_objects.id
                ) + 1,
                created_at, updated_at
            FROM knowledge_objects
            """
        )
        _inject_v10_failure("v10_objects_copy")
        _inject_v10_failure("v10_before_drop_rename")
        connection.execute("DROP TABLE knowledge_objects")
        connection.execute("ALTER TABLE knowledge_objects_v10 RENAME TO knowledge_objects")
        connection.execute(
            "CREATE INDEX idx_knowledge_objects_kind "
            "ON knowledge_objects(kind, updated_at DESC)"
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_objects_importance "
            "ON knowledge_objects(importance, updated_at DESC)"
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_objects_lifecycle "
            "ON knowledge_objects(lifecycle, updated_at DESC)"
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_objects_superseded_by "
            "ON knowledge_objects(superseded_by_ko_id)"
        )

        connection.execute(
            """
            CREATE TABLE knowledge_object_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                knowledge_object_id INTEGER,
                object_local_id_snapshot INTEGER,
                object_stable_id_snapshot TEXT,
                object_title_snapshot TEXT NOT NULL,
                object_kind_snapshot TEXT NOT NULL,
                revision_number INTEGER NOT NULL,
                event_type TEXT NOT NULL CHECK (event_type IN (
                    'legacy_baseline', 'legacy_event', 'created',
                    'content_updated', 'confirmation_changed',
                    'lifecycle_changed', 'supersession_changed',
                    'source_linked', 'source_unlinked'
                )),
                before_title TEXT,
                after_title TEXT,
                before_content TEXT,
                after_content TEXT,
                before_lifecycle TEXT,
                after_lifecycle TEXT,
                before_confirmation TEXT,
                after_confirmation TEXT,
                superseded_by_before INTEGER,
                superseded_by_after INTEGER,
                source_ref TEXT,
                payload_version INTEGER NOT NULL DEFAULT 1
                    CHECK (payload_version >= 1),
                detail TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE (knowledge_object_id, revision_number)
            )
            """
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_object_revisions_object "
            "ON knowledge_object_revisions(knowledge_object_id, revision_number)"
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_object_revisions_stable "
            "ON knowledge_object_revisions(object_stable_id_snapshot)"
        )

        object_rows = connection.execute(
            "SELECT id, kind, title, content, lifecycle, confirmation_status "
            "FROM knowledge_objects ORDER BY id"
        ).fetchall()
        for object_row in object_rows:
            object_id = int(object_row["id"])
            stable_id = f"{kb_uuid}:knowledge_object:{object_id}"
            legacy_rows = connection.execute(
                "SELECT id, title, content, created_at FROM knowledge_memory_entries "
                "WHERE kind = 'knowledge_change' AND knowledge_object_id = ? "
                "ORDER BY created_at ASC, id ASC",
                (object_id,),
            ).fetchall()
            for number, legacy_row in enumerate(legacy_rows, start=1):
                connection.execute(
                    """
                    INSERT INTO knowledge_object_revisions (
                        knowledge_object_id, object_local_id_snapshot,
                        object_stable_id_snapshot, object_title_snapshot,
                        object_kind_snapshot, revision_number, event_type,
                        detail, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'legacy_event', ?, ?)
                    """,
                    (
                        object_id,
                        object_id,
                        stable_id,
                        str(object_row["title"]),
                        str(object_row["kind"]),
                        number,
                        f"{legacy_row['title']}\n{legacy_row['content']}",
                        str(legacy_row["created_at"]),
                    ),
                )
                _inject_v10_failure("v10_legacy_events")
            baseline_number = len(legacy_rows) + 1
            connection.execute(
                """
                INSERT INTO knowledge_object_revisions (
                    knowledge_object_id, object_local_id_snapshot,
                    object_stable_id_snapshot, object_title_snapshot,
                    object_kind_snapshot, revision_number, event_type,
                    after_title, after_content, after_lifecycle,
                    after_confirmation, detail, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'legacy_baseline', ?, ?, ?, ?, ?, ?)
                """,
                (
                    object_id,
                    object_id,
                    stable_id,
                    str(object_row["title"]),
                    str(object_row["kind"]),
                    baseline_number,
                    str(object_row["title"]),
                    str(object_row["content"]),
                    str(object_row["lifecycle"]),
                    str(object_row["confirmation_status"]),
                    "迁移基线：v9→v10 迁移时点完整内容快照",
                    migration_timestamp,
                ),
            )
            _inject_v10_failure("v10_baselines")
        orphan_rows = connection.execute(
            "SELECT id, title, content, created_at FROM knowledge_memory_entries "
            "WHERE kind = 'knowledge_change' AND knowledge_object_id IS NULL "
            "ORDER BY created_at ASC, id ASC"
        ).fetchall()
        for orphan_row in orphan_rows:
            connection.execute(
                """
                INSERT INTO knowledge_object_revisions (
                    object_title_snapshot, object_kind_snapshot,
                    revision_number, event_type, detail, created_at
                ) VALUES (?, 'unknown', 0, 'legacy_event', ?, ?)
                """,
                (
                    _legacy_change_title_snapshot(str(orphan_row["title"])),
                    f"{orphan_row['title']}\n{orphan_row['content']}",
                    str(orphan_row["created_at"]),
                ),
            )

        connection.execute(
            """
            CREATE TABLE knowledge_memory_entries_v10 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK (kind IN (
                    'problem_solving', 'experience', 'decision'
                )),
                title TEXT NOT NULL CHECK (
                    length(trim(title)) BETWEEN 1 AND 200
                ),
                content TEXT NOT NULL DEFAULT ''
                    CHECK (length(content) <= 20000),
                root_cause TEXT NOT NULL DEFAULT ''
                    CHECK (length(root_cause) <= 4000),
                lesson TEXT NOT NULL DEFAULT ''
                    CHECK (length(lesson) <= 4000),
                knowledge_object_id INTEGER
                    REFERENCES knowledge_objects(id) ON DELETE SET NULL,
                document_id INTEGER
                    REFERENCES documents(id) ON DELETE SET NULL,
                page_id INTEGER
                    REFERENCES pages(id) ON DELETE SET NULL,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'archived')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO knowledge_memory_entries_v10 (
                id, kind, title, content, root_cause, lesson,
                knowledge_object_id, document_id, page_id, status,
                created_at, updated_at
            )
            SELECT
                id, kind, title, content, root_cause, lesson,
                knowledge_object_id, document_id, page_id, 'active',
                created_at, updated_at
            FROM knowledge_memory_entries
            WHERE kind IN ('problem_solving', 'experience', 'decision')
            """
        )
        _inject_v10_failure("v10_memory_copy")
        connection.execute("DROP TABLE knowledge_memory_entries")
        connection.execute(
            "ALTER TABLE knowledge_memory_entries_v10 RENAME TO knowledge_memory_entries"
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_memory_kind "
            "ON knowledge_memory_entries(kind, updated_at DESC)"
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_memory_ko "
            "ON knowledge_memory_entries(knowledge_object_id, updated_at DESC)"
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_memory_status "
            "ON knowledge_memory_entries(status, updated_at DESC)"
        )

        connection.execute(
            "ALTER TABLE knowledge_object_sources ADD COLUMN source_fingerprint TEXT"
        )
        connection.execute(
            "ALTER TABLE knowledge_object_sources "
            "ADD COLUMN fingerprint_version INTEGER NOT NULL DEFAULT 1"
        )
        connection.execute(
            "ALTER TABLE knowledge_object_sources "
            f"ADD COLUMN captured_at TEXT NOT NULL DEFAULT '{migration_timestamp}'"
        )

        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (10, ?)",
            (migration_timestamp,),
        )
        _inject_v10_failure("v10_version_record")
        if _core_data_fingerprint(connection) != fingerprint:
            raise MigrationError("schema v10 迁移改变了现有文档、页面或 FTS 数据")
        _inject_v10_failure("v10_before_commit")
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v10")
    except Exception:
        connection.rollback()
        raise


def _apply_version_eleven(connection: sqlite3.Connection) -> None:
    """Add the v0.5.2 knowledge FTS layer without touching existing data.

    v11 changes (Phase 3 retrieval contract, ADR-06 supplement):

    - ``knowledge_objects`` and ``knowledge_memory_entries`` gain tokenized
      shadow columns through ``ALTER TABLE ADD COLUMN`` only — the existing
      tables are never dropped or rebuilt;
    - every legacy row is backfilled with the exact page-FTS canonical
      tokenization (``src.database._tokenize_for_fts``, imported lazily to
      avoid a top-level circular import), so the tokenizer stays a single
      source of truth and page retrieval semantics are untouched;
    - two external-content FTS5 tables and their six sync triggers are
      created, then rebuilt from the shadow columns;
    - ``knowledge_object_revisions`` never gets an FTS index.
    """

    fingerprint = _knowledge_data_fingerprint(connection)
    migration_timestamp = _utc_now()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "ALTER TABLE knowledge_objects "
            "ADD COLUMN search_title TEXT NOT NULL DEFAULT ''"
        )
        connection.execute(
            "ALTER TABLE knowledge_objects "
            "ADD COLUMN search_content TEXT NOT NULL DEFAULT ''"
        )
        _inject_v11_failure("v11_ko_columns")
        connection.execute(
            "ALTER TABLE knowledge_memory_entries "
            "ADD COLUMN search_title TEXT NOT NULL DEFAULT ''"
        )
        connection.execute(
            "ALTER TABLE knowledge_memory_entries "
            "ADD COLUMN search_content TEXT NOT NULL DEFAULT ''"
        )
        connection.execute(
            "ALTER TABLE knowledge_memory_entries "
            "ADD COLUMN search_root_cause TEXT NOT NULL DEFAULT ''"
        )
        connection.execute(
            "ALTER TABLE knowledge_memory_entries "
            "ADD COLUMN search_lesson TEXT NOT NULL DEFAULT ''"
        )
        _inject_v11_failure("v11_memory_columns")

        _backfill_knowledge_shadow_columns(connection)
        _inject_v11_failure("v11_ko_backfill")
        _backfill_memory_shadow_columns(connection)
        _inject_v11_failure("v11_memory_backfill")

        connection.execute(
            """
            CREATE VIRTUAL TABLE knowledge_object_search USING fts5(
                search_title,
                search_content,
                content='knowledge_objects',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            )
            """
        )
        _inject_v11_failure("v11_ko_fts")
        connection.execute(
            """
            CREATE TRIGGER knowledge_objects_fts_insert
            AFTER INSERT ON knowledge_objects BEGIN
                INSERT INTO knowledge_object_search(rowid, search_title, search_content)
                VALUES (new.id, new.search_title, new.search_content);
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER knowledge_objects_fts_delete
            AFTER DELETE ON knowledge_objects BEGIN
                INSERT INTO knowledge_object_search(
                    knowledge_object_search, rowid, search_title, search_content
                ) VALUES (
                    'delete', old.id, old.search_title, old.search_content
                );
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER knowledge_objects_fts_update
            AFTER UPDATE ON knowledge_objects BEGIN
                INSERT INTO knowledge_object_search(
                    knowledge_object_search, rowid, search_title, search_content
                ) VALUES (
                    'delete', old.id, old.search_title, old.search_content
                );
                INSERT INTO knowledge_object_search(rowid, search_title, search_content)
                VALUES (new.id, new.search_title, new.search_content);
            END
            """
        )
        _inject_v11_failure("v11_ko_triggers")
        connection.execute(
            """
            CREATE VIRTUAL TABLE knowledge_memory_search USING fts5(
                search_title,
                search_content,
                search_root_cause,
                search_lesson,
                content='knowledge_memory_entries',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            )
            """
        )
        _inject_v11_failure("v11_memory_fts")
        connection.execute(
            """
            CREATE TRIGGER knowledge_memory_fts_insert
            AFTER INSERT ON knowledge_memory_entries BEGIN
                INSERT INTO knowledge_memory_search(
                    rowid, search_title, search_content, search_root_cause, search_lesson
                ) VALUES (
                    new.id, new.search_title, new.search_content,
                    new.search_root_cause, new.search_lesson
                );
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER knowledge_memory_fts_delete
            AFTER DELETE ON knowledge_memory_entries BEGIN
                INSERT INTO knowledge_memory_search(
                    knowledge_memory_search, rowid, search_title, search_content,
                    search_root_cause, search_lesson
                ) VALUES (
                    'delete', old.id, old.search_title, old.search_content,
                    old.search_root_cause, old.search_lesson
                );
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER knowledge_memory_fts_update
            AFTER UPDATE ON knowledge_memory_entries BEGIN
                INSERT INTO knowledge_memory_search(
                    knowledge_memory_search, rowid, search_title, search_content,
                    search_root_cause, search_lesson
                ) VALUES (
                    'delete', old.id, old.search_title, old.search_content,
                    old.search_root_cause, old.search_lesson
                );
                INSERT INTO knowledge_memory_search(
                    rowid, search_title, search_content, search_root_cause, search_lesson
                ) VALUES (
                    new.id, new.search_title, new.search_content,
                    new.search_root_cause, new.search_lesson
                );
            END
            """
        )
        _inject_v11_failure("v11_memory_triggers")

        connection.execute(
            "INSERT INTO knowledge_object_search(knowledge_object_search) VALUES ('rebuild')"
        )
        connection.execute(
            "INSERT INTO knowledge_memory_search(knowledge_memory_search) VALUES ('rebuild')"
        )
        _inject_v11_failure("v11_rebuild")

        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (11, ?)",
            (migration_timestamp,),
        )
        _inject_v11_failure("v11_version_record")
        if _knowledge_data_fingerprint(connection) != fingerprint:
            raise MigrationError("schema v11 迁移改变了现有知识对象、记忆、来源、关系或修订数据")
        _inject_v11_failure("v11_before_commit")
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v11")
    except Exception:
        connection.rollback()
        raise


def _apply_version_twelve(connection: sqlite3.Connection) -> None:
    """Add the v0.5.3 AI audit ledger and experience-model groundwork.

    v12 changes (Phase 2-A, V53-ADR-06) are pure incremental:

    - three new tables: ``ai_calls`` (append-only AI call audit ledger),
      ``ai_outputs`` (AI output audit anchors) and ``knowledge_project_links``
      (project-to-knowledge association);
    - ``knowledge_memory_entries`` gains ``content_revision``, ``outcome`` and
      ``context_conditions`` through ``ALTER TABLE ADD COLUMN`` only, with
      conservative defaults (1 / '' / '') — legacy rows are never backfilled
      from their historical content;
    - no existing v1-v11 table, index or FTS object is dropped, rebuilt or
      recreated.

    Data integrity is checked by comparing the v12 fingerprint before and
    after: row sets and content fields of the existing knowledge, document,
    page, note and evidence tables must be byte-identical.
    """

    fingerprint = _v12_data_fingerprint(connection)
    migration_timestamp = _utc_now()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE ai_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                call_uuid TEXT NOT NULL UNIQUE,
                capability TEXT NOT NULL
                    CHECK (capability IN ('completion', 'embedding', 'rerank')),
                model TEXT NOT NULL,
                prompt_sha256 TEXT NOT NULL
                    CHECK (length(prompt_sha256) = 64),
                input_chars INTEGER NOT NULL DEFAULT 0
                    CHECK (input_chars >= 0),
                status TEXT NOT NULL
                    CHECK (status IN ('success', 'error', 'rejected')),
                error_class TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0
                    CHECK (retry_count >= 0),
                latency_ms INTEGER,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                finish_reason TEXT,
                source_feature TEXT NOT NULL,
                target_refs TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )
            """
        )
        _inject_v12_failure("v12_ai_calls")
        connection.execute(
            "CREATE INDEX idx_ai_calls_created_at ON ai_calls(created_at)"
        )
        connection.execute(
            "CREATE INDEX idx_ai_calls_feature_created "
            "ON ai_calls(source_feature, created_at)"
        )
        connection.execute(
            "CREATE INDEX idx_ai_calls_status ON ai_calls(status)"
        )
        connection.execute(
            """
            CREATE TABLE ai_outputs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                output_uuid TEXT NOT NULL UNIQUE,
                call_uuid TEXT,
                model TEXT NOT NULL,
                context_package_sha256 TEXT,
                output_sha256 TEXT NOT NULL
                    CHECK (length(output_sha256) = 64),
                output_kind TEXT NOT NULL
                    CHECK (output_kind IN ('imported_answer', 'prompt_package')),
                source_feature TEXT NOT NULL,
                target_refs TEXT NOT NULL DEFAULT '[]',
                recheck_path TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        _inject_v12_failure("v12_ai_outputs")
        connection.execute(
            "CREATE INDEX idx_ai_outputs_created_at ON ai_outputs(created_at)"
        )
        connection.execute(
            """
            CREATE TABLE knowledge_project_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL
                    REFERENCES projects(id) ON DELETE CASCADE,
                target_type TEXT NOT NULL
                    CHECK (target_type IN ('knowledge_object', 'knowledge_memory')),
                target_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(project_id, target_type, target_id)
            )
            """
        )
        _inject_v12_failure("v12_project_links")
        connection.execute(
            "CREATE INDEX idx_knowledge_project_links_target "
            "ON knowledge_project_links(target_type, target_id)"
        )
        connection.execute(
            "ALTER TABLE knowledge_memory_entries "
            "ADD COLUMN content_revision INTEGER NOT NULL DEFAULT 1 "
            "CHECK (content_revision >= 1)"
        )
        connection.execute(
            "ALTER TABLE knowledge_memory_entries "
            "ADD COLUMN outcome TEXT NOT NULL DEFAULT ''"
        )
        connection.execute(
            "ALTER TABLE knowledge_memory_entries "
            "ADD COLUMN context_conditions TEXT NOT NULL DEFAULT ''"
        )
        _inject_v12_failure("v12_memory_columns")

        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (12, ?)",
            (migration_timestamp,),
        )
        _inject_v12_failure("v12_version_record")
        if _v12_data_fingerprint(connection) != fingerprint:
            raise MigrationError(
                "schema v12 迁移改变了现有知识、文档、页面、笔记或证据数据"
            )
        _inject_v12_failure("v12_before_commit")
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v12")
    except Exception:
        connection.rollback()
        raise


# v13 确定性 raw_qa 分类条件：只有内容精确匹配保存问答按钮写入格式
# （以“问题：”开头且包含“Agent 回答：”标记）的 experience 行才会被重分类。
# 该表达式在迁移前指纹与迁移复制中逐字复用，保证分类与校验完全一致。
_V13_RAW_QA_KIND_CASE = (
    "CASE WHEN kind = 'experience' "
    "AND substr(content, 1, 3) = '问题：' "
    "AND instr(content, 'Agent 回答：') > 0 "
    "THEN 'raw_qa' ELSE kind END"
)
_V13_RAW_QA_ORIGIN_CASE = (
    "CASE WHEN kind = 'experience' "
    "AND substr(content, 1, 3) = '问题：' "
    "AND instr(content, 'Agent 回答：') > 0 "
    "THEN 'human_saved' ELSE NULL END"
)


def _apply_version_thirteen(connection: sqlite3.Connection) -> None:
    """Separate raw saved Q&A from personal experience (v0.7 Phase 1).

    v13 changes (Raw Q&A Identity Remediation):

    - ``knowledge_memory_entries`` is rebuilt in place, because the ``kind``
      and ``status`` CHECK constraints cannot be widened by ALTER:
      * ``kind`` gains ``raw_qa`` — a verbatim saved question + agent answer
        that is explicitly not user experience;
      * ``status`` gains the ``deleted`` soft-delete tombstone;
      * new columns ``creation_origin`` (``human_saved`` / ``agent_assisted``,
        NULL = legacy row with unverifiable origin), ``citation_snapshot``
        (constrained JSON), ``content_fingerprint`` (exact-duplicate key),
        ``source_title`` and ``root_cause_confirmed``; ``source_entry_id``
        is added after the rebuild so its self-reference resolves against the
        final table name;
    - legacy classification is deterministic only: an ``experience`` row whose
      content matches the exact save-button format becomes ``raw_qa`` with
      ``creation_origin='human_saved'``; every other row keeps its kind and
      receives ``creation_origin=NULL`` — unknown origins are never guessed;
    - classified rows are backfilled with ``content_fingerprint`` and a
      citation snapshot rebuilt from their surviving document/page links;
    - ids, FTS shadow columns and all other tables are preserved verbatim;
      the memory FTS triggers are recreated and the FTS index rebuilt.
    """

    fingerprint = _v13_data_fingerprint(connection)
    migration_timestamp = _utc_now()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE knowledge_memory_entries_v13 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK (kind IN (
                    'problem_solving', 'experience', 'decision', 'raw_qa'
                )),
                title TEXT NOT NULL CHECK (
                    length(trim(title)) BETWEEN 1 AND 200
                ),
                content TEXT NOT NULL DEFAULT ''
                    CHECK (length(content) <= 20000),
                root_cause TEXT NOT NULL DEFAULT ''
                    CHECK (length(root_cause) <= 4000),
                lesson TEXT NOT NULL DEFAULT ''
                    CHECK (length(lesson) <= 4000),
                knowledge_object_id INTEGER
                    REFERENCES knowledge_objects(id) ON DELETE SET NULL,
                document_id INTEGER
                    REFERENCES documents(id) ON DELETE SET NULL,
                page_id INTEGER
                    REFERENCES pages(id) ON DELETE SET NULL,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'archived', 'deleted')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                content_revision INTEGER NOT NULL DEFAULT 1
                    CHECK (content_revision >= 1),
                outcome TEXT NOT NULL DEFAULT '',
                context_conditions TEXT NOT NULL DEFAULT '',
                creation_origin TEXT
                    CHECK (creation_origin IS NULL OR creation_origin IN (
                        'human_saved', 'agent_assisted'
                    )),
                citation_snapshot TEXT NOT NULL DEFAULT ''
                    CHECK (length(citation_snapshot) <= 20000),
                content_fingerprint TEXT
                    CHECK (content_fingerprint IS NULL
                        OR length(content_fingerprint) = 64),
                source_title TEXT
                    CHECK (source_title IS NULL OR length(source_title) <= 200),
                root_cause_confirmed INTEGER NOT NULL DEFAULT 0
                    CHECK (root_cause_confirmed IN (0, 1)),
                search_title TEXT NOT NULL DEFAULT '',
                search_content TEXT NOT NULL DEFAULT '',
                search_root_cause TEXT NOT NULL DEFAULT '',
                search_lesson TEXT NOT NULL DEFAULT ''
            )
            """
        )
        _inject_v13_failure("v13_create_table")
        connection.execute(
            f"""
            INSERT INTO knowledge_memory_entries_v13 (
                id, kind, title, content, root_cause, lesson,
                knowledge_object_id, document_id, page_id, status,
                created_at, updated_at, content_revision, outcome,
                context_conditions, creation_origin, citation_snapshot,
                content_fingerprint, source_title, root_cause_confirmed,
                search_title, search_content, search_root_cause, search_lesson
            )
            SELECT
                id,
                {_V13_RAW_QA_KIND_CASE},
                title, content, root_cause, lesson,
                knowledge_object_id, document_id, page_id, status,
                created_at, updated_at, content_revision, outcome,
                context_conditions,
                {_V13_RAW_QA_ORIGIN_CASE},
                '',
                NULL,
                NULL,
                0,
                search_title, search_content, search_root_cause, search_lesson
            FROM knowledge_memory_entries
            ORDER BY id
            """
        )
        _inject_v13_failure("v13_memory_copy")
        _backfill_v13_raw_qa_classifications(connection)
        _inject_v13_failure("v13_memory_backfill")
        connection.execute("DROP TABLE knowledge_memory_entries")
        _inject_v13_failure("v13_drop_old")
        connection.execute(
            "ALTER TABLE knowledge_memory_entries_v13 RENAME TO knowledge_memory_entries"
        )
        _inject_v13_failure("v13_rename")
        connection.execute(
            "CREATE INDEX idx_knowledge_memory_kind "
            "ON knowledge_memory_entries(kind, updated_at DESC)"
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_memory_ko "
            "ON knowledge_memory_entries(knowledge_object_id, updated_at DESC)"
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_memory_status "
            "ON knowledge_memory_entries(status, updated_at DESC)"
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_memory_fingerprint "
            "ON knowledge_memory_entries(content_fingerprint) "
            "WHERE content_fingerprint IS NOT NULL"
        )
        _inject_v13_failure("v13_indexes")
        connection.execute(
            """
            CREATE TRIGGER knowledge_memory_fts_insert
            AFTER INSERT ON knowledge_memory_entries BEGIN
                INSERT INTO knowledge_memory_search(
                    rowid, search_title, search_content, search_root_cause, search_lesson
                ) VALUES (
                    new.id, new.search_title, new.search_content,
                    new.search_root_cause, new.search_lesson
                );
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER knowledge_memory_fts_delete
            AFTER DELETE ON knowledge_memory_entries BEGIN
                INSERT INTO knowledge_memory_search(
                    knowledge_memory_search, rowid, search_title, search_content,
                    search_root_cause, search_lesson
                ) VALUES (
                    'delete', old.id, old.search_title, old.search_content,
                    old.search_root_cause, old.search_lesson
                );
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER knowledge_memory_fts_update
            AFTER UPDATE ON knowledge_memory_entries BEGIN
                INSERT INTO knowledge_memory_search(
                    knowledge_memory_search, rowid, search_title, search_content,
                    search_root_cause, search_lesson
                ) VALUES (
                    'delete', old.id, old.search_title, old.search_content,
                    old.search_root_cause, old.search_lesson
                );
                INSERT INTO knowledge_memory_search(
                    rowid, search_title, search_content, search_root_cause, search_lesson
                ) VALUES (
                    new.id, new.search_title, new.search_content,
                    new.search_root_cause, new.search_lesson
                );
            END
            """
        )
        _inject_v13_failure("v13_triggers")
        connection.execute(
            "INSERT INTO knowledge_memory_search(knowledge_memory_search) VALUES ('rebuild')"
        )
        _inject_v13_failure("v13_rebuild")
        connection.execute(
            "ALTER TABLE knowledge_memory_entries "
            "ADD COLUMN source_entry_id INTEGER "
            "REFERENCES knowledge_memory_entries(id) ON DELETE SET NULL"
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_memory_source "
            "ON knowledge_memory_entries(source_entry_id) "
            "WHERE source_entry_id IS NOT NULL"
        )
        _inject_v13_failure("v13_source_column")
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (13, ?)",
            (migration_timestamp,),
        )
        _inject_v13_failure("v13_version_record")
        if _v13_data_fingerprint(connection) != fingerprint:
            raise MigrationError(
                "schema v13 迁移改变了现有知识记忆的稳定内容或分类结果"
            )
        _inject_v13_failure("v13_before_commit")
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v13")
    except Exception:
        connection.rollback()
        raise


def _backfill_v13_raw_qa_classifications(connection: sqlite3.Connection) -> None:
    """Backfill fingerprint + citation snapshot for classified ``raw_qa`` rows.

    Runs inside the migration transaction on the ``_v13`` staging table. The
    snapshot is rebuilt only from the row's surviving ``document_id`` /
    ``page_id`` links (the single citation legacy saves kept); deleted targets
    yield an honest sparse item instead of a fabricated reference.
    """

    kb_row = connection.execute(
        "SELECT kb_uuid FROM knowledge_base_meta WHERE id = 1"
    ).fetchone()
    kb_uuid = str(kb_row["kb_uuid"]) if kb_row is not None else ""
    rows = connection.execute(
        "SELECT id, content, document_id, page_id FROM knowledge_memory_entries_v13 "
        "WHERE kind = 'raw_qa' ORDER BY id"
    ).fetchall()
    for row in rows:
        canonical = (
            str(row["content"]).replace("\r\n", "\n").replace("\r", "\n").strip()
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        snapshot = _v13_legacy_citation_snapshot(
            connection, kb_uuid, row["document_id"], row["page_id"]
        )
        connection.execute(
            "UPDATE knowledge_memory_entries_v13 "
            "SET content_fingerprint = ?, citation_snapshot = ? WHERE id = ?",
            (digest, snapshot, int(row["id"])),
        )


def _v13_legacy_citation_snapshot(
    connection: sqlite3.Connection,
    kb_uuid: str,
    document_id: object,
    page_id: object,
) -> str:
    """Build the single-item legacy citation snapshot, or '' when no link."""

    from src.models import PAGE_STABLE_TYPE, build_stable_id  # noqa: PLC0415

    if document_id is None and page_id is None:
        return ""
    item: dict[str, object] = {
        "document_id": None,
        "document_title": "",
        "document_sha256": None,
        "page_id": None,
        "page_number": None,
        "stable_id": "",
    }
    page_row = None
    if page_id is not None:
        page_row = connection.execute(
            "SELECT id, document_id, page_number FROM pages WHERE id = ?",
            (page_id,),
        ).fetchone()
        if page_row is not None:
            item["page_id"] = int(page_row["id"])
            item["page_number"] = int(page_row["page_number"])
            if kb_uuid:
                item["stable_id"] = build_stable_id(
                    kb_uuid, PAGE_STABLE_TYPE, int(page_row["id"])
                )
    effective_document_id = document_id
    if effective_document_id is None and page_row is not None:
        effective_document_id = page_row["document_id"]
    if effective_document_id is not None:
        document_row = connection.execute(
            "SELECT id, title, sha256 FROM documents WHERE id = ?",
            (effective_document_id,),
        ).fetchone()
        if document_row is not None:
            item["document_id"] = int(document_row["id"])
            item["document_title"] = str(document_row["title"])
            item["document_sha256"] = (
                str(document_row["sha256"])
                if document_row["sha256"] is not None
                else None
            )
    return json.dumps([item], ensure_ascii=False, separators=(",", ":"))


def _v13_data_fingerprint(connection: sqlite3.Connection) -> tuple[object, ...]:
    """Return invariants the v13 rebuild must preserve.

    Rows are fingerprinted on the stable columns only; ``kind`` is evaluated
    through the exact classification CASE both before (against the v12 table)
    and after (against the rebuilt table), so the check simultaneously proves
    that content was copied verbatim and that reclassification matched the
    deterministic rule.
    """

    return tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT id, "
            + _V13_RAW_QA_KIND_CASE
            + ", title, content, root_cause, lesson,"
            " knowledge_object_id, document_id, page_id, status, created_at,"
            " updated_at, content_revision, outcome, context_conditions"
            " FROM knowledge_memory_entries ORDER BY id"
        ).fetchall()
    )


def _v12_data_fingerprint(connection: sqlite3.Connection) -> tuple[object, ...]:
    """Return raw invariants that the v12 pure-incremental migration must keep.

    ``knowledge_memory_entries`` is fingerprinted by its pre-v12 column list
    only, so the three newly added columns can never be mistaken for data
    mutation. All other existing tables are untouched by v12 and are compared
    row-by-row with ``SELECT *``.
    """

    def _rows(table: str) -> tuple[tuple[object, ...], ...]:
        return tuple(
            tuple(row)
            for row in connection.execute(
                f"SELECT * FROM {table} ORDER BY id"
            ).fetchall()
        )

    memory_rows = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT id, kind, title, content, root_cause, lesson,"
            " knowledge_object_id, document_id, page_id, status, created_at,"
            " updated_at FROM knowledge_memory_entries ORDER BY id"
        ).fetchall()
    )
    return (
        _rows("knowledge_objects"),
        memory_rows,
        _rows("knowledge_object_sources"),
        _rows("knowledge_relations"),
        _rows("knowledge_object_revisions"),
        _rows("documents"),
        _rows("pages"),
        _rows("notes"),
        _rows("evidence_items"),
    )


def _backfill_knowledge_shadow_columns(connection: sqlite3.Connection) -> None:
    """Tokenize every existing knowledge object into its v11 shadow columns."""

    # Deferred import keeps the canonical page-FTS tokenizer a single source of
    # truth while avoiding a top-level circular import (src.database imports
    # this module). No page-retrieval semantics are changed.
    from src.database import _tokenize_for_fts  # noqa: PLC0415

    rows = connection.execute(
        "SELECT id, title, content FROM knowledge_objects ORDER BY id"
    ).fetchall()
    for row in rows:
        connection.execute(
            "UPDATE knowledge_objects SET search_title = ?, search_content = ? WHERE id = ?",
            (
                _tokenize_for_fts(str(row[1])),
                _tokenize_for_fts(str(row[2])),
                int(row[0]),
            ),
        )


def _backfill_memory_shadow_columns(connection: sqlite3.Connection) -> None:
    """Tokenize every existing memory entry into its v11 shadow columns."""

    # Deferred import mirrors ``_backfill_knowledge_shadow_columns``.
    from src.database import _tokenize_for_fts  # noqa: PLC0415

    rows = connection.execute(
        "SELECT id, title, content, root_cause, lesson "
        "FROM knowledge_memory_entries ORDER BY id"
    ).fetchall()
    for row in rows:
        connection.execute(
            """
            UPDATE knowledge_memory_entries SET
                search_title = ?, search_content = ?,
                search_root_cause = ?, search_lesson = ?
            WHERE id = ?
            """,
            (
                _tokenize_for_fts(str(row[1])),
                _tokenize_for_fts(str(row[2])),
                _tokenize_for_fts(str(row[3])),
                _tokenize_for_fts(str(row[4])),
                int(row[0]),
            ),
        )


def _knowledge_data_fingerprint(connection: sqlite3.Connection) -> tuple[object, ...]:
    """Return raw Knowledge Foundation invariants that v11 must preserve.

    Shadow columns are deliberately excluded: they are v11-derived fields and
    their backfill must never be mistaken for raw-data mutation. Row tuples
    are compared exactly, which is stronger than a content hash.
    """

    knowledge_object_ids = tuple(
        int(row[0])
        for row in connection.execute(
            "SELECT id FROM knowledge_objects ORDER BY id"
        ).fetchall()
    )
    knowledge_object_rows = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT id, kind, authorship, epistemic_basis, title, content,"
            " importance, lifecycle, superseded_by_ko_id, confirmation_status,"
            " confirmed_at, confirmed_revision, current_revision, created_at,"
            " updated_at FROM knowledge_objects ORDER BY id"
        ).fetchall()
    )
    memory_ids = tuple(
        int(row[0])
        for row in connection.execute(
            "SELECT id FROM knowledge_memory_entries ORDER BY id"
        ).fetchall()
    )
    memory_rows = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT id, kind, title, content, root_cause, lesson,"
            " knowledge_object_id, document_id, page_id, status, created_at,"
            " updated_at FROM knowledge_memory_entries ORDER BY id"
        ).fetchall()
    )
    source_count = int(
        connection.execute("SELECT COUNT(*) FROM knowledge_object_sources").fetchone()[0]
    )
    relation_count = int(
        connection.execute("SELECT COUNT(*) FROM knowledge_relations").fetchone()[0]
    )
    revision_rows = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT * FROM knowledge_object_revisions ORDER BY id"
        ).fetchall()
    )
    return (
        knowledge_object_ids,
        knowledge_object_rows,
        memory_ids,
        memory_rows,
        source_count,
        relation_count,
        revision_rows,
    )


def _apply_version_fourteen(connection: sqlite3.Connection) -> None:
    """Add the experience-evolution link table (v0.7.4, pure additive).

    v14 creates ``knowledge_memory_links``: one experience may confirm, refine,
    contradict or supersede another. Existing rows and tables are untouched;
    the migration is a single transaction with a failure-injection hook and a
    version record, following the v7-v13 discipline.
    """

    migration_timestamp = _utc_now()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE knowledge_memory_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_entry_id INTEGER NOT NULL
                    REFERENCES knowledge_memory_entries(id) ON DELETE CASCADE,
                to_entry_id INTEGER NOT NULL
                    REFERENCES knowledge_memory_entries(id) ON DELETE CASCADE,
                relation_type TEXT NOT NULL
                    CHECK (relation_type IN (
                        'confirms', 'refines', 'contradicts', 'supersedes'
                    )),
                note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 500),
                created_at TEXT NOT NULL,
                CHECK (from_entry_id <> to_entry_id),
                UNIQUE (from_entry_id, to_entry_id, relation_type)
            )
            """
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_memory_links_from"
            " ON knowledge_memory_links(from_entry_id)"
        )
        connection.execute(
            "CREATE INDEX idx_knowledge_memory_links_to"
            " ON knowledge_memory_links(to_entry_id)"
        )
        _inject_v14_failure("v14_links_table")
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (14, ?)",
            (migration_timestamp,),
        )
        _inject_v14_failure("v14_version_record")
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v14")
    except Exception:
        connection.rollback()
        raise


def _apply_version_fifteen(connection: sqlite3.Connection) -> None:
    """Create the visual-artifact retrieval index (v0.8.3 E-1, pure additive).

    v15 creates the FTS5 virtual table ``page_visual_index`` over per-page
    visual reading artifacts (summary / keywords / key_facts). It is a
    retrieval aid only: no existing table, column or row is touched, and the
    index contents are (re)derived lazily by
    :class:`src.ai.visual_artifact_index.VisualArtifactIndex`, never by this
    migration. A failure leaves the database at v14 with all data intact.
    """

    migration_timestamp = _utc_now()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS page_visual_index
            USING fts5(visual_text, page_id UNINDEXED)
            """
        )
        _validate_version_fifteen_schema(connection)
        _inject_v15_failure("v15_visual_index_table")
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (15, ?)",
            (migration_timestamp,),
        )
        _inject_v15_failure("v15_version_record")
        _validate_database_integrity(connection, stage="v15 提交前")
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v15")
    except Exception:
        connection.rollback()
        raise


def _apply_version_sixteen(connection: sqlite3.Connection) -> None:
    """Add minimal, non-secret provider/model attribution to the AI ledger.

    The existing ``ai_calls.model`` column already stores the model sent in
    the request, so it remains in place and is exposed as ``requested_model``
    by the application layer.  Historical rows came from the Qwen-only era:
    the additive provider column therefore defaults to ``qwen``.  No historic
    resolved model is inferred; ``resolved_model`` stays NULL.
    """

    preserved = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT id, call_uuid, capability, model, prompt_sha256, input_chars, "
            "status, error_class, retry_count, latency_ms, prompt_tokens, "
            "completion_tokens, total_tokens, finish_reason, source_feature, "
            "target_refs, created_at FROM ai_calls ORDER BY id"
        ).fetchall()
    )
    migration_timestamp = _utc_now()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "ALTER TABLE ai_calls ADD COLUMN provider TEXT NOT NULL "
            "DEFAULT 'qwen' CHECK (provider IN "
            "('deepseek', 'qwen', 'kimi', 'hunyuan'))"
        )
        _inject_v16_failure("v16_provider_column")
        connection.execute("ALTER TABLE ai_calls ADD COLUMN resolved_model TEXT")
        _inject_v16_failure("v16_resolved_model_column")
        connection.execute(
            "CREATE INDEX idx_ai_calls_provider_created "
            "ON ai_calls(provider, created_at)"
        )
        _validate_version_sixteen_schema(connection)
        current = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT id, call_uuid, capability, model, prompt_sha256, input_chars, "
                "status, error_class, retry_count, latency_ms, prompt_tokens, "
                "completion_tokens, total_tokens, finish_reason, source_feature, "
                "target_refs, created_at FROM ai_calls ORDER BY id"
            ).fetchall()
        )
        if current != preserved:
            raise MigrationError("schema v16 迁移改变了历史 AI 调用内容")
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (16, ?)",
            (migration_timestamp,),
        )
        _inject_v16_failure("v16_version_record")
        _validate_database_integrity(connection, stage="v16 提交前")
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v16")
    except Exception:
        connection.rollback()
        raise


def _apply_version_seventeen(connection: sqlite3.Connection) -> None:
    """Add an append-only, non-secret request-level Agent audit ledger."""

    migration_timestamp = _utc_now()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE agent_run_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL UNIQUE,
                request_id TEXT,
                started_at TEXT NOT NULL,
                duration_ms INTEGER,
                decision_status TEXT,
                decision_finish_reason TEXT,
                decision_output_chars INTEGER,
                decision_output_tokens INTEGER,
                decision_tool TEXT,
                decision_arguments TEXT NOT NULL DEFAULT '{}',
                tool_status TEXT NOT NULL,
                tool_result_status TEXT,
                result_count INTEGER NOT NULL DEFAULT 0 CHECK (result_count >= 0),
                top_evidence_page_ids TEXT NOT NULL DEFAULT '[]',
                final_status TEXT,
                final_finish_reason TEXT,
                final_declared_insufficient INTEGER NOT NULL DEFAULT 0
                    CHECK (final_declared_insufficient IN (0, 1)),
                evidence_count INTEGER NOT NULL DEFAULT 0 CHECK (evidence_count >= 0),
                evidence_page_ids TEXT NOT NULL DEFAULT '[]',
                evidence_excerpt_chars INTEGER NOT NULL DEFAULT 0
                    CHECK (evidence_excerpt_chars >= 0),
                ui_failure_reason_code TEXT,
                outcome TEXT NOT NULL,
                error_code TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX idx_agent_run_audits_created "
            "ON agent_run_audits(created_at)"
        )
        connection.execute(
            "CREATE INDEX idx_agent_run_audits_reason "
            "ON agent_run_audits(ui_failure_reason_code, created_at)"
        )
        _validate_version_seventeen_schema(connection)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (17, ?)",
            (migration_timestamp,),
        )
        _validate_database_integrity(connection, stage="v17 提交前")
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v17")
    except Exception:
        connection.rollback()
        raise


def _validate_version_eighteen_schema(connection: sqlite3.Connection) -> None:
    """Verify the v18 legacy-import ledger exists with its idempotency key."""

    table = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name = 'legacy_imports'"
    ).fetchone()
    if table is None:
        raise MigrationError("v18 校验失败：legacy_imports 表缺失。")
    indexes = connection.execute("PRAGMA index_list(legacy_imports)").fetchall()
    has_source_unique = any(int(row[2]) == 1 for row in indexes)
    if not has_source_unique:
        raise MigrationError("v18 校验失败：legacy_imports.source_sha256 唯一键缺失。")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(legacy_imports)")}
    required = {
        "id",
        "source_kb_uuid",
        "source_sha256",
        "source_schema_version",
        "source_root",
        "import_mode",
        "status",
        "document_count",
        "page_count",
        "note_count",
        "evidence_count",
        "report_path",
        "created_at",
    }
    missing = required - columns
    if missing:
        raise MigrationError(f"v18 校验失败：legacy_imports 缺列：{sorted(missing)}")


def _apply_version_eighteen(connection: sqlite3.Connection) -> None:
    """Add the legacy-import idempotency ledger (v0.8.6 duty A foundation).

    One row per completed import of an external source snapshot into this
    workspace. The source database file hash (of the WAL-merged copy) is the
    unique key, so re-importing the same snapshot can always be detected and
    refused instead of duplicating user knowledge assets.
    """

    migration_timestamp = _utc_now()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS legacy_imports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_kb_uuid TEXT
                    CHECK (source_kb_uuid IS NULL OR length(source_kb_uuid) = 36),
                source_sha256 TEXT NOT NULL UNIQUE CHECK (length(source_sha256) = 64),
                source_schema_version INTEGER NOT NULL
                    CHECK (source_schema_version >= 1),
                source_root TEXT NOT NULL CHECK (length(trim(source_root)) > 0),
                import_mode TEXT NOT NULL
                    CHECK (import_mode IN ('copy_to_empty', 'merge')),
                status TEXT NOT NULL CHECK (status IN ('completed', 'dry_run')),
                document_count INTEGER NOT NULL DEFAULT 0 CHECK (document_count >= 0),
                page_count INTEGER NOT NULL DEFAULT 0 CHECK (page_count >= 0),
                note_count INTEGER NOT NULL DEFAULT 0 CHECK (note_count >= 0),
                evidence_count INTEGER NOT NULL DEFAULT 0 CHECK (evidence_count >= 0),
                report_path TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        _validate_version_eighteen_schema(connection)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (18, ?)",
            (migration_timestamp,),
        )
        _validate_database_integrity(connection, stage="v18 提交前")
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v18")
    except Exception:
        connection.rollback()
        raise


def _validate_version_nineteen_schema(connection: sqlite3.Connection) -> None:
    """Verify the v19 learning-workflow layer tables and FTS index exist."""

    required_tables = {
        "question_items",
        "question_item_evidence",
        "question_families",
        "question_family_members",
        "conclusion_revisions",
        "mastery_records",
        "mastery_profiles",
        "output_collections",
        "output_collection_items",
        "question_search",
    }
    # FTS5 virtual tables report type 'table' in sqlite_master on modern
    # SQLite builds; include shadow-table suffixes defensively.
    existing = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','virtual table')"
        ).fetchall()
    }
    missing = {
        name
        for name in required_tables
        if name not in existing
        and not any(item == name or item.startswith(name + "_") for item in existing)
    }
    if missing:
        raise MigrationError(f"v19 校验失败：学习层表缺失：{sorted(missing)}")


def _apply_version_nineteen(connection: sqlite3.Connection) -> None:
    """Add the three-layer learning workflow schema (v0.8.6 duty C).

    Layer 1 (``question_items``) keeps one row per organized question and
    deliberately separates printed stem, student answer, teacher verdict,
    teacher comments and corrections — real senior-high papers mix printed
    questions with handwritten work and different teachers' √/× marks, and
    low-confidence readings must stay flagged ``uncertain`` instead of being
    forced into facts.  Layer 2 groups questions into type/method/conclusion
    families with an explicit revision log so new evidence can narrow or
    correct earlier summaries instead of duplicating them.  Layer 3 keeps
    mastery records with the 会做/会讲 (can-do vs can-explain) separation and
    per-family review profiles.  The output layer stores formal collections
    whose items reference any learning-layer object plus a selection/range
    contract; renderers are layered above this schema.  ``two_wings_links``
    is a deliberately neutral interface skeleton: the official 两翼 product
    definition is still pending user input, so no semantics are invented.

    Deletion coupling: organized questions deliberately survive document
    deletion (document_id/page_id are ``ON DELETE SET NULL``) — the typed
    understanding belongs to the student, not to the source paper.  Their
    ``question_item_evidence`` links flip to ``unavailable`` inside the
    deletion transaction (enforced by the deletion service), never silently
    dangling, and the deletion preview shows this impact explicitly.
    """

    migration_timestamp = _utc_now()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS question_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                page_id INTEGER REFERENCES pages(id) ON DELETE SET NULL,
                question_number TEXT NOT NULL DEFAULT '',
                question_kind TEXT NOT NULL
                    CHECK (question_kind IN ('error', 'good', 'typical', 'method')),
                stem_text TEXT NOT NULL DEFAULT '',
                search_stem_text TEXT NOT NULL DEFAULT '',
                stem_confidence TEXT NOT NULL DEFAULT 'uncertain'
                    CHECK (stem_confidence IN ('confirmed', 'probable', 'uncertain')),
                student_answer TEXT NOT NULL DEFAULT '',
                teacher_verdict TEXT
                    CHECK (teacher_verdict IN ('correct', 'incorrect', 'uncertain')),
                teacher_comment TEXT NOT NULL DEFAULT '',
                correction_note TEXT NOT NULL DEFAULT '',
                reason_tags TEXT NOT NULL DEFAULT '[]',
                method_tags TEXT NOT NULL DEFAULT '[]',
                source_region TEXT,
                ai_draft TEXT,
                user_edited INTEGER NOT NULL DEFAULT 0 CHECK (user_edited IN (0, 1)),
                status TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'organized', 'archived')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_question_items_document ON question_items(document_id);
            CREATE INDEX IF NOT EXISTS idx_question_items_page ON question_items(page_id);
            CREATE INDEX IF NOT EXISTS idx_question_items_kind ON question_items(question_kind);
            CREATE VIRTUAL TABLE IF NOT EXISTS question_search USING fts5(
                search_stem_text,
                content='question_items',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            );
            CREATE TRIGGER IF NOT EXISTS question_items_fts_insert
                AFTER INSERT ON question_items BEGIN
                INSERT INTO question_search(rowid, search_stem_text)
                VALUES (new.id, new.search_stem_text);
            END;
            CREATE TRIGGER IF NOT EXISTS question_items_fts_delete
                AFTER DELETE ON question_items BEGIN
                INSERT INTO question_search(question_search, rowid, search_stem_text)
                VALUES ('delete', old.id, old.search_stem_text);
            END;
            CREATE TRIGGER IF NOT EXISTS question_items_fts_update
                AFTER UPDATE ON question_items BEGIN
                INSERT INTO question_search(question_search, rowid, search_stem_text)
                VALUES ('delete', old.id, old.search_stem_text);
                INSERT INTO question_search(rowid, search_stem_text)
                VALUES (new.id, new.search_stem_text);
            END;

            CREATE TABLE IF NOT EXISTS question_item_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER NOT NULL
                    REFERENCES question_items(id) ON DELETE CASCADE,
                evidence_item_id INTEGER REFERENCES evidence_items(id),
                region_json TEXT,
                status TEXT NOT NULL DEFAULT 'linked'
                    CHECK (status IN ('linked', 'unavailable')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (question_id, evidence_item_id)
            );

            CREATE TABLE IF NOT EXISTS question_families (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                family_kind TEXT NOT NULL
                    CHECK (family_kind IN ('type', 'method', 'conclusion')),
                title TEXT NOT NULL CHECK (length(trim(title)) > 0),
                description TEXT NOT NULL DEFAULT '',
                derivation TEXT NOT NULL DEFAULT '',
                variant_pattern TEXT NOT NULL DEFAULT '',
                confusion_notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'revised', 'retired')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS question_family_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                family_id INTEGER NOT NULL
                    REFERENCES question_families(id) ON DELETE CASCADE,
                question_id INTEGER NOT NULL
                    REFERENCES question_items(id) ON DELETE CASCADE,
                relation TEXT NOT NULL DEFAULT 'member'
                    CHECK (relation IN ('member', 'variant', 'counterexample')),
                created_at TEXT NOT NULL,
                UNIQUE (family_id, question_id)
            );
            CREATE INDEX IF NOT EXISTS idx_question_family_members_question
                ON question_family_members(question_id);
            CREATE TABLE IF NOT EXISTS conclusion_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                family_id INTEGER NOT NULL
                    REFERENCES question_families(id) ON DELETE CASCADE,
                trigger_question_id INTEGER
                    REFERENCES question_items(id) ON DELETE SET NULL,
                revision_kind TEXT NOT NULL
                    CHECK (revision_kind IN ('narrowed', 'broadened', 'corrected', 'merged')),
                note TEXT NOT NULL CHECK (length(trim(note)) > 0),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mastery_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER NOT NULL
                    REFERENCES question_items(id) ON DELETE CASCADE,
                practiced_at TEXT NOT NULL,
                outcome TEXT NOT NULL
                    CHECK (outcome IN ('correct', 'incorrect', 'partial')),
                can_explain INTEGER CHECK (can_explain IN (0, 1)),
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_mastery_records_question ON mastery_records(question_id);

            CREATE TABLE IF NOT EXISTS mastery_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                family_id INTEGER REFERENCES question_families(id) ON DELETE SET NULL,
                scope TEXT NOT NULL DEFAULT 'family'
                    CHECK (scope IN ('family', 'general')),
                weak_points TEXT NOT NULL DEFAULT '[]',
                next_review_at TEXT,
                review_interval_days INTEGER NOT NULL DEFAULT 1
                    CHECK (review_interval_days >= 1),
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS output_collections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK (kind IN (
                    'error_book', 'good_book', 'review_pack',
                    'topic_pack', 'conclusion_handbook', 'weakness_report'
                )),
                title TEXT NOT NULL CHECK (length(trim(title)) > 0),
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS output_collection_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_id INTEGER NOT NULL
                    REFERENCES output_collections(id) ON DELETE CASCADE,
                item_layer TEXT NOT NULL
                    CHECK (item_layer IN ('question', 'family', 'mastery')),
                item_id INTEGER NOT NULL,
                range_spec TEXT NOT NULL DEFAULT '{}',
                position INTEGER NOT NULL DEFAULT 0 CHECK (position >= 0),
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_output_collection_items_collection
                ON output_collection_items(collection_id);

            CREATE TABLE IF NOT EXISTS two_wings_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wing TEXT NOT NULL CHECK (wing IN ('left', 'right')),
                target_layer TEXT NOT NULL
                    CHECK (target_layer IN ('question', 'family', 'mastery', 'output')),
                target_id INTEGER NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute("INSERT INTO question_search(question_search) VALUES ('rebuild')")
        _validate_version_nineteen_schema(connection)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (19, ?)",
            (migration_timestamp,),
        )
        _validate_database_integrity(connection, stage="v19 提交前")
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v19")
    except Exception:
        connection.rollback()
        raise


def _validate_version_twenty_schema(connection: sqlite3.Connection) -> None:
    """Verify the v20 two-wings and dual-stage parsing objects exist."""

    required_tables = {
        "wing_entries",
        "wing_entry_revisions",
        "page_visual_interpretations",
    }
    existing = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    missing = required_tables - existing
    if missing:
        raise MigrationError(f"v20 校验失败：表缺失：{sorted(missing)}")
    if "two_wings_links" in existing:
        raise MigrationError("v20 校验失败：两翼占位表 two_wings_links 未移除。")
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(pages)")
    }
    for column in (
        "has_handwriting",
        "has_visual_content",
        "visual_detection_status",
        "visual_detected_at",
    ):
        if column not in columns:
            raise MigrationError(f"v20 校验失败：pages 缺少 {column} 列。")
    wing_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(wing_entries)")
    }
    required_wing_columns = {
        "id", "wing_kind", "target_layer", "question_id", "family_id",
        "trigger_conditions", "recognition_signals", "candidate_method",
        "selection_reason", "applicability_prerequisites",
        "application_to_current_question", "similar_method_distinction",
        "validity_conditions", "invalidation_conditions", "boundary_cases",
        "counterexamples", "common_misuses", "confusing_conclusions",
        "condition_change_effect", "origin", "confidence", "status",
        "evidence_item_id", "region_json", "created_at", "updated_at",
    }
    missing_wing = required_wing_columns - wing_columns
    if missing_wing:
        raise MigrationError(
            f"v20 校验失败：wing_entries 缺列：{sorted(missing_wing)}"
        )


def _apply_version_twenty(connection: sqlite3.Connection) -> None:
    """Add the two-wings layer and dual-stage page parsing (v0.8.6 RUN 2).

    Two wings (user-ratified definitions) are horizontal capability
    attachments, never extra layers: one ``wing_entries`` row attaches the
    Method Trigger Wing or the Boundary & Counterexample Wing to a question
    item or a question family.  A CHECK pin guarantees exactly one target.
    ``wing_entry_revisions`` keeps the AI-draft to user-revision history so
    AI output can never silently pose as user-confirmed knowledge; entries
    carry an explicit origin plus confirmed/probable/uncertain confidence,
    and evidence binding mirrors question_item_evidence (unavailable
    semantics handled by the deletion service contract).

    Dual-stage parsing: ``pages`` gains visual-presence detection state
    (``has_handwriting`` / ``has_visual_content`` - NULL means "not yet
    detected", so detection is never confused with understanding), and
    ``page_visual_interpretations`` stores stage-2 user-triggered readings
    with an explicit provenance (text layer, handwriting vision, image
    region, diagram interpretation) so vision output can never pose as the
    PDF text layer.
    """

    migration_timestamp = _utc_now()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.executescript(
            """
            DROP TABLE IF EXISTS two_wings_links;

            CREATE TABLE IF NOT EXISTS wing_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wing_kind TEXT NOT NULL
                    CHECK (wing_kind IN ('method_trigger', 'boundary_counterexample')),
                target_layer TEXT NOT NULL CHECK (target_layer IN ('question', 'family')),
                question_id INTEGER REFERENCES question_items(id) ON DELETE CASCADE,
                family_id INTEGER REFERENCES question_families(id) ON DELETE CASCADE,
                trigger_conditions TEXT NOT NULL DEFAULT '[]',
                recognition_signals TEXT NOT NULL DEFAULT '[]',
                candidate_method TEXT NOT NULL DEFAULT '',
                selection_reason TEXT NOT NULL DEFAULT '',
                applicability_prerequisites TEXT NOT NULL DEFAULT '[]',
                application_to_current_question TEXT NOT NULL DEFAULT '',
                similar_method_distinction TEXT NOT NULL DEFAULT '',
                validity_conditions TEXT NOT NULL DEFAULT '[]',
                invalidation_conditions TEXT NOT NULL DEFAULT '[]',
                boundary_cases TEXT NOT NULL DEFAULT '[]',
                counterexamples TEXT NOT NULL DEFAULT '[]',
                common_misuses TEXT NOT NULL DEFAULT '[]',
                confusing_conclusions TEXT NOT NULL DEFAULT '[]',
                condition_change_effect TEXT NOT NULL DEFAULT '',
                origin TEXT NOT NULL DEFAULT 'user'
                    CHECK (origin IN ('user', 'ai_draft')),
                confidence TEXT NOT NULL DEFAULT 'probable'
                    CHECK (confidence IN ('confirmed', 'probable', 'uncertain')),
                status TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'confirmed', 'archived')),
                evidence_item_id INTEGER REFERENCES evidence_items(id),
                region_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (
                    (target_layer = 'question'
                     AND question_id IS NOT NULL AND family_id IS NULL)
                    OR
                    (target_layer = 'family'
                     AND family_id IS NOT NULL AND question_id IS NULL)
                )
            );
            CREATE INDEX IF NOT EXISTS idx_wing_entries_question
                ON wing_entries(question_id);
            CREATE INDEX IF NOT EXISTS idx_wing_entries_family
                ON wing_entries(family_id);
            CREATE INDEX IF NOT EXISTS idx_wing_entries_kind
                ON wing_entries(wing_kind);

            CREATE TABLE IF NOT EXISTS wing_entry_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wing_entry_id INTEGER NOT NULL
                    REFERENCES wing_entries(id) ON DELETE CASCADE,
                revision_kind TEXT NOT NULL
                    CHECK (revision_kind IN (
                        'ai_draft', 'user_revision', 'confirmed', 'archived'
                    )),
                note TEXT NOT NULL DEFAULT '',
                snapshot TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_wing_entry_revisions_entry
                ON wing_entry_revisions(wing_entry_id);

            CREATE TABLE IF NOT EXISTS page_visual_interpretations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
                provenance TEXT NOT NULL CHECK (provenance IN (
                    'TEXT_LAYER', 'HANDWRITING_VISION', 'IMAGE_REGION',
                    'DIAGRAM_INTERPRETATION'
                )),
                content TEXT NOT NULL DEFAULT '',
                region_json TEXT,
                confidence TEXT NOT NULL DEFAULT 'uncertain'
                    CHECK (confidence IN ('confirmed', 'probable', 'uncertain')),
                origin TEXT NOT NULL DEFAULT 'ai_vision'
                    CHECK (origin IN ('ai_vision', 'user', 'ocr')),
                status TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'confirmed', 'archived')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_page_visual_interpretations_page
                ON page_visual_interpretations(page_id);
            """
        )
        # SQLite has no ADD COLUMN IF NOT EXISTS; guard each one so a
        # shaped/rebuilt fixture database re-migrates cleanly.
        page_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(pages)")
        }
        guarded_columns = (
            ("has_handwriting", "INTEGER CHECK (has_handwriting IN (0, 1))"),
            ("has_visual_content", "INTEGER CHECK (has_visual_content IN (0, 1))"),
            (
                "visual_detection_status",
                "TEXT DEFAULT 'not_checked' CHECK (visual_detection_status IN "
                "('not_checked', 'checked', 'uncertain'))",
            ),
            ("visual_detected_at", "TEXT"),
        )
        for column, definition in guarded_columns:
            if column not in page_columns:
                connection.execute(f"ALTER TABLE pages ADD COLUMN {column} {definition}")
        _validate_version_twenty_schema(connection)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (20, ?)",
            (migration_timestamp,),
        )
        _validate_database_integrity(connection, stage="v20 提交前")
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v20")
    except Exception:
        connection.rollback()
        raise


def _apply_version_twenty_one(connection: sqlite3.Connection) -> None:
    """Common Fix Round 2 (2026-09-26): provenance, artifacts, durable queue.

    1. **Source snapshots for learning assets (V086-305/208).**
       ``question_items`` gains ``source_document_title_snapshot``,
       ``source_page_label_snapshot`` and ``source_printed_page_number``.
       Learning assets survive document deletion detached (``SET NULL``);
       with the snapshot they can still tell the user *where they came
       from* after the source is gone, without keeping any copy of the
       deleted file. Rows whose source predates this migration and is
       already detached keep empty snapshots — that history is honestly
       unknown, never fabricated.
    2. **Scan-artifact-free knowledge text + printed page facts
       (V086-309).** ``pages`` gains ``visual_detection_method``,
       ``printed_page_number``, ``printed_total_pages`` and
       ``document_footer``. Existing ``search_extracted_text`` /
       ``search_ocr_text`` columns are re-derived through the scan-artifact
       filter so scanner branding (「夸克扫描王」「扫码使用」…) leaves the
       FTS/knowledge text while the paper's own printed footers
       (「第1页（共6页）」) stay. Raw ``ocr_text`` / ``extracted_text``,
       page images and source PDFs are untouched; user corrections
       (``markdown_content``) are never filtered.
    3. **Durable import queue (V086-301).** ``import_queue`` records one row
       per uploaded file so a multi-file batch survives navigation: an
       interrupted batch is honestly marked and resumable, never silently
       lost.
    """

    migration_timestamp = _utc_now()
    try:
        connection.execute("BEGIN IMMEDIATE")

        def _guarded_add_column(table: str, column: str, definition: str) -> None:
            existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

        _guarded_add_column(
            "question_items",
            "source_document_title_snapshot",
            "TEXT NOT NULL DEFAULT ''",
        )
        _guarded_add_column(
            "question_items",
            "source_page_label_snapshot",
            "TEXT NOT NULL DEFAULT ''",
        )
        _guarded_add_column(
            "question_items",
            "source_printed_page_number",
            "INTEGER",
        )
        _guarded_add_column("pages", "visual_detection_method", "TEXT NOT NULL DEFAULT ''")
        _guarded_add_column("pages", "printed_page_number", "INTEGER")
        _guarded_add_column("pages", "printed_total_pages", "INTEGER")
        _guarded_add_column("pages", "document_footer", "TEXT NOT NULL DEFAULT ''")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS import_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN (
                        'queued', 'importing', 'imported',
                        'complete', 'failed', 'interrupted'
                    )),
                document_id INTEGER,
                error_message TEXT NOT NULL DEFAULT '',
                run_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_import_queue_batch
                ON import_queue(batch_id);
            CREATE INDEX IF NOT EXISTS idx_import_queue_status
                ON import_queue(status);
            """
        )

        # Backfill source snapshots for every learning asset whose source is
        # still attached, so the next deletion always finds a snapshot even
        # if a future code path misses the delete-time refresh.
        connection.execute(
            """
            UPDATE question_items SET
                source_document_title_snapshot = COALESCE(
                    (SELECT title FROM documents
                     WHERE documents.id = question_items.document_id),
                    source_document_title_snapshot
                ),
                source_page_label_snapshot = COALESCE(
                    '第 ' || (SELECT page_number FROM pages
                              WHERE pages.id = question_items.page_id) || ' 页',
                    source_page_label_snapshot
                )
            WHERE document_id IS NOT NULL
            """
        )

        # Re-derive knowledge search text through the scan-artifact filter
        # and extract printed page-number facts. The pages_fts_update
        # trigger keeps page_search in sync automatically; ocr_text and
        # extracted_text themselves are never modified.
        from src.database import _tokenize_for_fts
        from src.scan_artifact_filter import extract_printed_page_info, filter_knowledge_text

        page_rows = connection.execute(
            "SELECT id, ocr_text, extracted_text, markdown_content FROM pages"
        ).fetchall()
        for row in page_rows:
            filtered_ocr = filter_knowledge_text(str(row["ocr_text"] or ""))
            filtered_extracted = filter_knowledge_text(str(row["extracted_text"] or ""))
            # The user correction text itself is never modified; only its
            # retrieval mirror is knowledge text (V086-309 §10.3).
            filtered_markdown = filter_knowledge_text(str(row["markdown_content"] or ""))
            info = extract_printed_page_info(
                filtered_ocr.filtered_text or filtered_extracted.filtered_text
            )
            connection.execute(
                """
                UPDATE pages SET
                    search_ocr_text = ?,
                    search_extracted_text = ?,
                    search_markdown_content = ?,
                    printed_page_number = ?,
                    printed_total_pages = ?,
                    document_footer = ?
                WHERE id = ?
                """,
                (
                    _tokenize_for_fts(filtered_ocr.filtered_text),
                    _tokenize_for_fts(filtered_extracted.filtered_text),
                    _tokenize_for_fts(filtered_markdown.filtered_text),
                    info.printed_page_number,
                    info.printed_total_pages,
                    info.footer_text,
                    int(row["id"]),
                ),
            )

        # Stage-1 provenance backfill (V086-302): rows that already carry a
        # detection timestamp predate the method column and can only have
        # been produced by the local PyMuPDF structure detector, so they are
        # labelled honestly instead of looking unprovenanced.
        connection.execute(
            """
            UPDATE pages SET visual_detection_method = 'pymupdf_structure'
            WHERE visual_detected_at IS NOT NULL
              AND (visual_detection_method IS NULL OR visual_detection_method = '')
            """
        )

        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (21, ?)",
            (migration_timestamp,),
        )
        _validate_version_twenty_one_schema(connection)
        _validate_database_integrity(connection, stage="v21 提交前")
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v21")
    except Exception:
        connection.rollback()
        raise


def _validate_version_twenty_one_schema(connection: sqlite3.Connection) -> None:
    """Verify the v21 Common-Fix-Round-2 schema additions exist."""

    required_tables = {"import_queue"}
    existing = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    missing = required_tables - existing
    if missing:
        raise MigrationError(f"v21 校验失败：表缺失：{sorted(missing)}")
    question_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(question_items)")
    }
    for column in (
        "source_document_title_snapshot",
        "source_page_label_snapshot",
        "source_printed_page_number",
    ):
        if column not in question_columns:
            raise MigrationError(f"v21 校验失败：question_items 缺少 {column} 列。")
    page_columns = {row[1] for row in connection.execute("PRAGMA table_info(pages)")}
    for column in (
        "visual_detection_method",
        "printed_page_number",
        "printed_total_pages",
        "document_footer",
    ):
        if column not in page_columns:
            raise MigrationError(f"v21 校验失败：pages 缺少 {column} 列。")


def _apply_version_twenty_two(connection: sqlite3.Connection) -> None:
    """Schema v22: stage-2 user edits + self-explanation attempts (v0.8.6).

    - ``page_visual_interpretations`` gains ``user_edited`` and
      ``original_ai_content``: a user edit turns the AI draft into
      user-confirmed content while the original AI reading stays auditable
      (USER CONFIRMED > AI DRAFT priority, overnight round §7).
    - ``explanation_attempts`` stores each “我自己讲一遍” attempt together
      with its AI review feedback as a first-class history (§15).
    """

    migration_timestamp = _utc_now()
    try:
        interpretation_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(page_visual_interpretations)"
            )
        }
        if "user_edited" not in interpretation_columns:
            connection.execute(
                "ALTER TABLE page_visual_interpretations "
                "ADD COLUMN user_edited INTEGER NOT NULL DEFAULT 0"
            )
        if "original_ai_content" not in interpretation_columns:
            connection.execute(
                "ALTER TABLE page_visual_interpretations "
                "ADD COLUMN original_ai_content TEXT NOT NULL DEFAULT ''"
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS explanation_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER NOT NULL REFERENCES question_items(id)
                    ON DELETE CASCADE,
                content TEXT NOT NULL,
                feedback TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_explanation_attempts_question
                ON explanation_attempts(question_id)
            """
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (22, ?)",
            (migration_timestamp,),
        )
        _validate_version_twenty_two_schema(connection)
        _validate_database_integrity(connection, stage="v22 提交前")
        connection.commit()
        LOGGER.info("数据库已迁移到 schema v22")
    except Exception:
        connection.rollback()
        raise


def _validate_version_twenty_two_schema(connection: sqlite3.Connection) -> None:
    """Verify the v22 schema additions exist."""

    interpretation_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(page_visual_interpretations)")
    }
    for column in ("user_edited", "original_ai_content"):
        if column not in interpretation_columns:
            raise MigrationError(f"v22 校验失败：page_visual_interpretations 缺少 {column} 列。")
    existing = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "explanation_attempts" not in existing:
        raise MigrationError("v22 校验失败：表缺失：explanation_attempts")


def _legacy_change_title_snapshot(title: str) -> str:
    """Extract the object-title portion from a legacy ``知识XX：标题`` log title."""

    if "：" in title:
        return title.rsplit("：", 1)[1].strip() or title
    return title


def _evidence_item_ids(connection: sqlite3.Connection) -> tuple[int, ...]:
    """Return the ordered evidence item id set; equality implies equal counts."""

    return tuple(
        int(row[0])
        for row in connection.execute(
            "SELECT id FROM evidence_items ORDER BY id"
        ).fetchall()
    )


def _core_data_fingerprint(connection: sqlite3.Connection) -> tuple[object, ...]:
    """Return invariants that schema-only migrations must preserve exactly."""

    document_count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    page_count = connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    fts_count = connection.execute("SELECT COUNT(*) FROM page_search").fetchone()[0]
    statuses = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT review_status, COUNT(*) FROM pages "
            "GROUP BY review_status ORDER BY review_status"
        ).fetchall()
    )
    document_paths = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT id, source_path FROM documents ORDER BY id"
        ).fetchall()
    )
    page_paths = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT id, image_path FROM pages ORDER BY id"
        ).fetchall()
    )
    return (
        document_count,
        page_count,
        fts_count,
        statuses,
        document_paths,
        page_paths,
    )


def _create_v2_tables(connection: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE import_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            sha256 TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
            total_pages INTEGER NOT NULL DEFAULT 0,
            processed_pages INTEGER NOT NULL DEFAULT 0,
            text_pages INTEGER NOT NULL DEFAULT 0,
            review_pages INTEGER NOT NULL DEFAULT 0,
            failed_pages INTEGER NOT NULL DEFAULT 0,
            error_message TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL,
            finished_at TEXT
        )
        """,
        "CREATE INDEX idx_import_records_started ON import_records(started_at DESC)",
        "CREATE INDEX idx_import_records_status ON import_records(status)",
        """
        CREATE TABLE tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL CHECK (length(trim(name)) > 0),
            normalized_name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE document_tags (
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            PRIMARY KEY (document_id, tag_id)
        )
        """,
        "CREATE INDEX idx_document_tags_tag ON document_tags(tag_id, document_id)",
        """
        CREATE TABLE page_tags (
            page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            PRIMARY KEY (page_id, tag_id)
        )
        """,
        "CREATE INDEX idx_page_tags_tag ON page_tags(tag_id, page_id)",
        """
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL CHECK (length(trim(name)) > 0),
            normalized_name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE project_documents (
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            PRIMARY KEY (project_id, document_id)
        )
        """,
        "CREATE INDEX idx_project_documents_document ON project_documents(document_id, project_id)",
        """
        CREATE TABLE project_pages (
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            PRIMARY KEY (project_id, page_id)
        )
        """,
        "CREATE INDEX idx_project_pages_page ON project_pages(page_id, project_id)",
    )
    for statement in statements:
        connection.execute(statement)


def _create_v2_fts(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE VIRTUAL TABLE page_search USING fts5(
            search_extracted_text,
            search_ocr_text,
            search_markdown_content,
            content='pages',
            content_rowid='id',
            tokenize='unicode61 remove_diacritics 2'
        )
        """
    )
    connection.execute(
        """
        CREATE TRIGGER pages_fts_insert AFTER INSERT ON pages BEGIN
            INSERT INTO page_search(
                rowid, search_extracted_text, search_ocr_text, search_markdown_content
            ) VALUES (
                new.id, new.search_extracted_text, new.search_ocr_text,
                new.search_markdown_content
            );
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER pages_fts_delete AFTER DELETE ON pages BEGIN
            INSERT INTO page_search(
                page_search, rowid, search_extracted_text, search_ocr_text,
                search_markdown_content
            ) VALUES (
                'delete', old.id, old.search_extracted_text, old.search_ocr_text,
                old.search_markdown_content
            );
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER pages_fts_update AFTER UPDATE ON pages BEGIN
            INSERT INTO page_search(
                page_search, rowid, search_extracted_text, search_ocr_text,
                search_markdown_content
            ) VALUES (
                'delete', old.id, old.search_extracted_text, old.search_ocr_text,
                old.search_markdown_content
            );
            INSERT INTO page_search(
                rowid, search_extracted_text, search_ocr_text, search_markdown_content
            ) VALUES (
                new.id, new.search_extracted_text, new.search_ocr_text,
                new.search_markdown_content
            );
        END
        """
    )


def _apply_version_twenty_three(connection: sqlite3.Connection) -> None:
    """Schema v23: per-member provenance on family assignments (fix §46/§47).

    ``question_family_members`` gains ``provenance`` so every family
    assignment records what it was derived from (stem / confirmed user
    answer / AI draft suggestion / manual user action).  Second-layer
    induction can then never silently blend "user confirmed" with
    "AI inferred" provenance.
    """

    migration_timestamp = _utc_now()
    member_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(question_family_members)")
    }
    if "provenance" not in member_columns:
        connection.execute(
            "ALTER TABLE question_family_members "
            "ADD COLUMN provenance TEXT NOT NULL DEFAULT ''"
        )
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (23, ?)",
        (migration_timestamp,),
    )
    connection.commit()


def _validate_version_twenty_three_schema(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(question_family_members)")
    }
    if "provenance" not in columns:
        raise MigrationError("v23 校验失败：question_family_members.provenance 缺失。")


def _apply_version_twenty_four(connection: sqlite3.Connection) -> None:
    """Schema v24: persisted knowledge-chat history (R3 P0-B).

    Knowledge-Chat answers previously lived only in ``st.session_state``:
    a refresh / reconnect / service restart threw away an answer the user
    had already paid tokens for (red team R1-06 / R2-05, HIGH).  This
    migration adds a local conversation store:

    - ``agent_conversations`` — one row per conversation thread;
    - ``agent_messages`` — one row per question or answer turn, with an
      explicit ``status`` (success / no_evidence / failed).  Failed calls
      are recorded honestly; the store never invents an answer.

    Citations are kept as their stable source ids so the sources panel can
    be rebuilt after reload.  Chat history stays conversation history: it
    never silently becomes knowledge, notes or question items.
    """

    migration_timestamp = _utc_now()
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL
                REFERENCES agent_conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (role IN ('question', 'answer')),
            content TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'success'
                CHECK (status IN ('success', 'no_evidence', 'failed')),
            mode TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            citations_json TEXT NOT NULL DEFAULT '[]',
            failure_code TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_messages_conversation "
        "ON agent_messages(conversation_id)"
    )
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (24, ?)",
        (migration_timestamp,),
    )
    connection.commit()


def _validate_version_twenty_four_schema(connection: sqlite3.Connection) -> None:
    conversations = {
        row[1]
        for row in connection.execute("PRAGMA table_info(agent_conversations)")
    }
    required_conversation_columns = {"id", "title", "created_at", "updated_at"}
    if not required_conversation_columns.issubset(conversations):
        raise MigrationError(
            "v24 校验失败：agent_conversations 缺列："
            f"{sorted(required_conversation_columns - conversations)}"
        )
    messages = {
        row[1]
        for row in connection.execute("PRAGMA table_info(agent_messages)")
    }
    required_message_columns = {
        "id",
        "conversation_id",
        "role",
        "content",
        "status",
        "mode",
        "model",
        "citations_json",
        "failure_code",
        "created_at",
    }
    if not required_message_columns.issubset(messages):
        raise MigrationError(
            "v24 校验失败：agent_messages 缺列："
            f"{sorted(required_message_columns - messages)}"
        )


def _apply_version_twenty_five(connection: sqlite3.Connection) -> None:
    """Schema v25: layer-2 confidence workflow (GEOGRAPHY G2-B).

    ``auto_organize_question`` previously attached every AI proposal
    immediately: the student could see "系统背后自动塞了几个族" without
    knowing how sure the system was, and an explicit user rejection was
    silently overwritten on the next save.  This migration adds the two
    small stores the confidence workflow needs:

    - ``family_review_items`` — recommendations that are NOT auto-applied
      (medium-confidence candidate families and low-confidence
      new-family drafts).  A row becomes a real membership only after the
      user confirms it; low-confidence proposals never create formal
      families on their own.
    - ``user_family_rejections`` — explicit user rejections ("移出这个族"
      or "都不合适").  Auto-organize must respect them forever: a rejected
      family may be re-suggested with an honest reason, never silently
      re-attached.
    """

    migration_timestamp = _utc_now()
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS family_review_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL
                REFERENCES question_items(id) ON DELETE CASCADE,
            suggestion_kind TEXT NOT NULL
                CHECK (suggestion_kind IN ('type', 'method')),
            confidence TEXT NOT NULL
                CHECK (confidence IN ('medium', 'low')),
            title TEXT NOT NULL CHECK (length(trim(title)) > 0),
            description TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            matched_family_id INTEGER
                REFERENCES question_families(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'accepted', 'rejected')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_family_review_items_question "
        "ON family_review_items(question_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_family_review_items_status "
        "ON family_review_items(status)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS user_family_rejections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL
                REFERENCES question_items(id) ON DELETE CASCADE,
            family_id INTEGER REFERENCES question_families(id) ON DELETE CASCADE,
            candidate_title TEXT NOT NULL DEFAULT '',
            candidate_kind TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE (question_id, family_id)
        )
        """
    )
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (25, ?)",
        (migration_timestamp,),
    )
    connection.commit()


def _validate_version_twenty_five_schema(connection: sqlite3.Connection) -> None:
    review_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(family_review_items)")
    }
    required_review_columns = {
        "id",
        "question_id",
        "suggestion_kind",
        "confidence",
        "title",
        "description",
        "reason",
        "matched_family_id",
        "status",
        "created_at",
        "updated_at",
    }
    if not required_review_columns.issubset(review_columns):
        raise MigrationError(
            "v25 校验失败：family_review_items 缺列："
            f"{sorted(required_review_columns - review_columns)}"
        )
    rejection_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(user_family_rejections)")
    }
    required_rejection_columns = {
        "id",
        "question_id",
        "family_id",
        "candidate_title",
        "candidate_kind",
        "note",
        "created_at",
    }
    if not required_rejection_columns.issubset(rejection_columns):
        raise MigrationError(
            "v25 校验失败：user_family_rejections 缺列："
            f"{sorted(required_rejection_columns - rejection_columns)}"
        )


def _apply_version_twenty_six(connection: sqlite3.Connection) -> None:
    """Schema v26: review queue gains DEFERRED (GEOGRAPHY G2-B1, §8).

    "暂时不处理" must be a first-class state: it is neither a rejection
    (which would poison future auto-organize via user_family_rejections)
    nor a deletion.  SQLite cannot alter a CHECK constraint in place, so
    this migration rebuilds ``family_review_items`` with the extended
    status domain, preserving every existing row verbatim.
    """

    migration_timestamp = _utc_now()
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.executescript(
        """
        CREATE TABLE family_review_items_v26 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL
                REFERENCES question_items(id) ON DELETE CASCADE,
            suggestion_kind TEXT NOT NULL
                CHECK (suggestion_kind IN ('type', 'method')),
            confidence TEXT NOT NULL
                CHECK (confidence IN ('medium', 'low')),
            title TEXT NOT NULL CHECK (length(trim(title)) > 0),
            description TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            matched_family_id INTEGER
                REFERENCES question_families(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'accepted', 'rejected', 'deferred')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        INSERT INTO family_review_items_v26(
            id, question_id, suggestion_kind, confidence, title,
            description, reason, matched_family_id, status, created_at, updated_at
        )
        SELECT id, question_id, suggestion_kind, confidence, title,
               description, reason, matched_family_id, status, created_at, updated_at
        FROM family_review_items;

        DROP TABLE family_review_items;
        ALTER TABLE family_review_items_v26 RENAME TO family_review_items;

        CREATE INDEX IF NOT EXISTS idx_family_review_items_question
            ON family_review_items(question_id);
        CREATE INDEX IF NOT EXISTS idx_family_review_items_status
            ON family_review_items(status);
        """
    )
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (26, ?)",
        (migration_timestamp,),
    )
    connection.commit()


def _validate_version_twenty_six_schema(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='family_review_items'"
    ).fetchone()
    if row is None or row[0] is None:
        raise MigrationError("v26 校验失败：family_review_items 不存在。")
    ddl = str(row[0])
    if "'deferred'" not in ddl:
        raise MigrationError(
            "v26 校验失败：family_review_items.status 不支持 deferred。"
        )


def _apply_version_twenty_seven(connection: sqlite3.Connection) -> None:
    """Schema v27: mastery evidence ledger (GEOGRAPHY G2-C).

    Layer 3 previously answered "练习了几次/做对几次/会做吗" from
    ``mastery_records`` — a manual learning log.  The active mastery loop
    needs per-event EVIDENCE with the four dimensions separated (会做 /
    会讲 / 独立性 / 稳定性), explicit provenance (system training result
    vs user manual record vs AI-generated practice), independence tracking
    (hint dependency must never count as independent mastery), and a
    deterministic next-review schedule.  Legacy ``mastery_records`` rows
    are migrated in as user-manual evidence so no history is lost.
    """

    migration_timestamp = _utc_now()
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS mastery_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER
                REFERENCES question_items(id) ON DELETE CASCADE,
            family_id INTEGER REFERENCES question_families(id) ON DELETE SET NULL,
            event_type TEXT NOT NULL
                CHECK (event_type IN ('practice', 'explain_back',
                                      'method_trigger', 'boundary_check',
                                      'review')),
            result TEXT NOT NULL DEFAULT 'unevaluated'
                CHECK (result IN ('correct', 'incorrect', 'partial',
                                  'unevaluated')),
            independence TEXT NOT NULL DEFAULT 'unknown'
                CHECK (independence IN ('independent', 'light_hint',
                                        'heavy_hint', 'after_answer',
                                        'unknown')),
            explanation_state TEXT NOT NULL DEFAULT 'unverified'
                CHECK (explanation_state IN ('unverified', 'gaps',
                                             'basically_clear', 'complete')),
            hint_used INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL
                CHECK (source IN ('system_training', 'user_manual',
                                  'ai_generated')),
            provenance TEXT NOT NULL DEFAULT '',
            ai_evaluation TEXT NOT NULL DEFAULT '',
            user_note TEXT NOT NULL DEFAULT '',
            next_review_at TEXT,
            review_status TEXT NOT NULL DEFAULT 'scheduled'
                CHECK (review_status IN ('scheduled', 'done', 'deferred')),
            idempotency_key TEXT UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_mastery_evidence_question "
        "ON mastery_evidence(question_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_mastery_evidence_family "
        "ON mastery_evidence(family_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_mastery_evidence_review "
        "ON mastery_evidence(review_status, next_review_at)"
    )
    # Legacy manual records migrate in as user-manual evidence (§60):
    # history is preserved, provenance says exactly what it was.
    connection.execute(
        """
        INSERT INTO mastery_evidence(
            question_id, event_type, result, independence, source,
            provenance, user_note, created_at, updated_at
        )
        SELECT question_id, 'practice', outcome, 'unknown', 'user_manual',
               '早期手动记录（v27 迁移；非系统训练结果）',
               COALESCE(note, ''), practiced_at, practiced_at
        FROM mastery_records
        """
    )
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (27, ?)",
        (migration_timestamp,),
    )
    connection.commit()


def _validate_version_twenty_seven_schema(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(mastery_evidence)")
    }
    required = {
        "id", "question_id", "family_id", "event_type", "result",
        "independence", "explanation_state", "hint_used", "source",
        "provenance", "ai_evaluation", "user_note", "next_review_at",
        "review_status", "idempotency_key", "created_at", "updated_at",
    }
    if not required.issubset(columns):
        raise MigrationError(
            "v27 校验失败：mastery_evidence 缺列："
            f"{sorted(required - columns)}"
        )


def _apply_version_twenty_eight(connection: sqlite3.Connection) -> None:
    """Schema v28: comprehensive-question structure foundation (GEOGRAPHY G3-A).

    Round 1 treated every ``question_items`` row as an isolated question.
    Comprehensive geography questions are structured as::

        DOCUMENT -> QUESTION GROUP -> MATERIAL BLOCKS / FIGURES
                   -> SUBQUESTIONS (each with its own evidence subset)

    This migration adds the minimal first-class foundation:

    * ``question_groups`` — the group identity (group_number verbatim from
      the source; no invented titles).
    * ``question_group_materials`` — shared material blocks and figures,
      ZERO-COPY: they reference the original page (``page_id``) instead of
      duplicating content per subquestion.
    * ``question_items.group_id`` — the subquestion parent link. Subquestions
      keep their independent family / method / mastery machinery.
    * ``document_relations`` — question paper ↔ reference answer ↔ scoring
      standard relations, ``ai_suggested`` until user-confirmed.
    * ``rubric_entries`` — scoring-standard skeleton (point/level based),
      with the source text preserved verbatim (never overwritten).

    Everything is additive; Round 1 data is untouched.
    """

    migration_timestamp = _utc_now()
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS question_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER
                REFERENCES documents(id) ON DELETE SET NULL,
            group_number TEXT NOT NULL DEFAULT '',
            group_title TEXT NOT NULL DEFAULT '',
            group_summary TEXT NOT NULL DEFAULT '',
            group_theme TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'ai_draft'
                CHECK (status IN ('ai_draft', 'user_confirmed')),
            provenance TEXT NOT NULL DEFAULT 'ai_draft',
            source_pages TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_question_groups_document "
        "ON question_groups(document_id)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS question_group_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL
                REFERENCES question_groups(id) ON DELETE CASCADE,
            material_kind TEXT NOT NULL
                CHECK (material_kind IN ('text_material', 'figure')),
            material_label TEXT NOT NULL DEFAULT '',
            content_text TEXT NOT NULL DEFAULT '',
            page_id INTEGER REFERENCES pages(id) ON DELETE SET NULL,
            region TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_question_group_materials_group "
        "ON question_group_materials(group_id)"
    )
    # Subquestion parent link: added column keeps every existing row NULL
    # (Round 1 questions stay standalone until explicitly grouped).
    existing_item_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(question_items)")
    }
    if "group_id" not in existing_item_columns:
        connection.execute(
            """
            ALTER TABLE question_items ADD COLUMN group_id INTEGER
                REFERENCES question_groups(id) ON DELETE SET NULL
            """
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_question_items_group "
        "ON question_items(group_id)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS document_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            primary_document_id INTEGER NOT NULL
                REFERENCES documents(id) ON DELETE CASCADE,
            related_document_id INTEGER NOT NULL
                REFERENCES documents(id) ON DELETE CASCADE,
            relation_kind TEXT NOT NULL
                CHECK (relation_kind IN ('reference_answer',
                                         'scoring_standard', 'answer',
                                         'other')),
            status TEXT NOT NULL DEFAULT 'ai_suggested'
                CHECK (status IN ('ai_suggested', 'confirmed', 'rejected')),
            provenance TEXT NOT NULL DEFAULT 'ai_suggested',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (primary_document_id, related_document_id, relation_kind)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_document_relations_primary "
        "ON document_relations(primary_document_id)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS rubric_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER
                REFERENCES question_groups(id) ON DELETE SET NULL,
            question_item_id INTEGER
                REFERENCES question_items(id) ON DELETE SET NULL,
            scoring_mode TEXT NOT NULL DEFAULT 'unknown'
                CHECK (scoring_mode IN ('point_based', 'level_based',
                                        'hybrid', 'unknown')),
            max_score REAL,
            source_text TEXT NOT NULL DEFAULT '',
            source_document_id INTEGER
                REFERENCES documents(id) ON DELETE SET NULL,
            source_page_id INTEGER REFERENCES pages(id) ON DELETE SET NULL,
            point_count INTEGER,
            level_count INTEGER,
            provenance TEXT NOT NULL DEFAULT 'ai_draft',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_rubric_entries_item "
        "ON rubric_entries(question_item_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_rubric_entries_group "
        "ON rubric_entries(group_id)"
    )
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (28, ?)",
        (migration_timestamp,),
    )
    connection.commit()


def _validate_version_twenty_eight_schema(
    connection: sqlite3.Connection,
) -> None:
    group_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(question_groups)")
    }
    required_group = {
        "id", "document_id", "group_number", "group_title", "group_summary",
        "group_theme", "status", "provenance", "source_pages",
        "created_at", "updated_at",
    }
    if not required_group.issubset(group_columns):
        raise MigrationError(
            "v28 校验失败：question_groups 缺列："
            f"{sorted(required_group - group_columns)}"
        )
    material_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(question_group_materials)")
    }
    required_material = {
        "id", "group_id", "material_kind", "material_label", "content_text",
        "page_id", "region", "created_at",
    }
    if not required_material.issubset(material_columns):
        raise MigrationError(
            "v28 校验失败：question_group_materials 缺列："
            f"{sorted(required_material - material_columns)}"
        )
    item_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(question_items)")
    }
    if "group_id" not in item_columns:
        raise MigrationError(
            "v28 校验失败：question_items 缺少 group_id 列。"
        )
    relation_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(document_relations)")
    }
    required_relation = {
        "id", "primary_document_id", "related_document_id", "relation_kind",
        "status", "provenance", "created_at", "updated_at",
    }
    if not required_relation.issubset(relation_columns):
        raise MigrationError(
            "v28 校验失败：document_relations 缺列："
            f"{sorted(required_relation - relation_columns)}"
        )
    rubric_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(rubric_entries)")
    }
    required_rubric = {
        "id", "group_id", "question_item_id", "scoring_mode", "max_score",
        "source_text", "source_document_id", "source_page_id",
        "point_count", "level_count", "provenance", "created_at", "updated_at",
    }
    if not required_rubric.issubset(rubric_columns):
        raise MigrationError(
            "v28 校验失败：rubric_entries 缺列："
            f"{sorted(required_rubric - rubric_columns)}"
        )


def _apply_version_twenty_nine(connection: sqlite3.Connection) -> None:
    """Schema v29: subquestion evidence subsets + handwriting regions (G3-B).

    G3-A proved the comprehensive-question gap: subquestions could not say
    WHICH material/figure they need, and handwriting stayed a page-level
    draft with no identity structure.

    * ``question_item_evidence`` is rebuilt (it was an empty legacy shell):
      one row = one subquestion ↔ one evidence reference.  ``source_id``
      points at ``question_group_materials.id`` (ZERO-COPY: the material
      row itself references the original page, nothing is duplicated).
      ``status`` starts at ``ai_draft`` and only the user confirms.
      OVER-BINDING is structurally discouraged: there is no code path that
      binds every material of a group to every subquestion at once.
    * ``handwriting_regions``: region-level handwriting structure with
      ``identity_draft`` defaulting to UNKNOWN — the AI never guesses
      teacher/student identity; only the user can set it
      (``identity_status='user_confirmed'``).
    """

    migration_timestamp = _utc_now()
    # The legacy shell (G3-A era) carried a different shape and zero rows;
    # rebuilding it keeps the v29 contract clean without touching any data.
    legacy_tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "question_item_evidence" in legacy_tables:
        legacy_count = connection.execute(
            "SELECT COUNT(*) FROM question_item_evidence"
        ).fetchone()[0]
        if legacy_count:
            raise MigrationError(
                "v29 迁移中止：question_item_evidence 存在历史数据，拒绝自动重建。"
            )
    connection.execute("DROP TABLE IF EXISTS question_item_evidence")
    connection.execute(
        """
        CREATE TABLE question_item_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_item_id INTEGER NOT NULL
                REFERENCES question_items(id) ON DELETE CASCADE,
            evidence_type TEXT NOT NULL
                CHECK (evidence_type IN ('text_material', 'figure',
                                         'page_region')),
            source_id INTEGER
                REFERENCES question_group_materials(id) ON DELETE CASCADE,
            page_id INTEGER REFERENCES pages(id) ON DELETE SET NULL,
            confidence TEXT NOT NULL DEFAULT 'uncertain'
                CHECK (confidence IN ('confirmed', 'probable', 'uncertain')),
            status TEXT NOT NULL DEFAULT 'ai_draft'
                CHECK (status IN ('ai_draft', 'user_confirmed', 'rejected')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (question_item_id, evidence_type, source_id)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_question_item_evidence_item "
        "ON question_item_evidence(question_item_id)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS handwriting_regions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id INTEGER NOT NULL
                REFERENCES pages(id) ON DELETE CASCADE,
            interpretation_id INTEGER
                REFERENCES page_visual_interpretations(id) ON DELETE SET NULL,
            bbox TEXT,
            text TEXT NOT NULL DEFAULT '',
            ink_color TEXT NOT NULL DEFAULT 'unknown',
            identity_draft TEXT NOT NULL DEFAULT 'unknown'
                CHECK (identity_draft IN ('unknown', 'student', 'teacher')),
            identity_status TEXT NOT NULL DEFAULT 'ai_draft'
                CHECK (identity_status IN ('ai_draft', 'user_confirmed')),
            target_subquestion INTEGER
                REFERENCES question_items(id) ON DELETE SET NULL,
            confidence TEXT NOT NULL DEFAULT 'uncertain'
                CHECK (confidence IN ('confirmed', 'probable', 'uncertain')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_handwriting_regions_page "
        "ON handwriting_regions(page_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_handwriting_regions_target "
        "ON handwriting_regions(target_subquestion)"
    )
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (29, ?)",
        (migration_timestamp,),
    )
    connection.commit()


def _validate_version_twenty_nine_schema(
    connection: sqlite3.Connection,
) -> None:
    evidence_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(question_item_evidence)")
    }
    required_evidence = {
        "id", "question_item_id", "evidence_type", "source_id", "page_id",
        "confidence", "status", "created_at", "updated_at",
    }
    if not required_evidence.issubset(evidence_columns):
        raise MigrationError(
            "v29 校验失败：question_item_evidence 缺列："
            f"{sorted(required_evidence - evidence_columns)}"
        )
    region_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(handwriting_regions)")
    }
    required_region = {
        "id", "page_id", "interpretation_id", "bbox", "text", "ink_color",
        "identity_draft", "identity_status", "target_subquestion",
        "confidence", "created_at", "updated_at",
    }
    if not required_region.issubset(region_columns):
        raise MigrationError(
            "v29 校验失败：handwriting_regions 缺列："
            f"{sorted(required_region - region_columns)}"
        )


def _apply_version_thirty(connection: sqlite3.Connection) -> None:
    """Schema v30: one unified question_groups model gains ``group_type``.

    F3 fix — choice question-groups ("1～2题共用材料") previously had NO
    shared structure, so the splitter produced N duplicate-stem candidates.
    Instead of inventing a second "choice_group" system, the existing
    G3-B ``question_groups`` table is EXTENDED:

        group_type = 'choice' | 'comprehensive' | 'other'

    Existing rows keep their comprehensive semantics via the DEFAULT, and
    the shared-material machinery (question_group_materials, zero-copy
    page references) applies to choice groups unchanged.
    """

    migration_timestamp = _utc_now()
    group_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(question_groups)")
    }
    if "group_type" not in group_columns:
        connection.execute(
            """
            ALTER TABLE question_groups ADD COLUMN group_type TEXT
                NOT NULL DEFAULT 'comprehensive'
                CHECK (group_type IN ('choice', 'comprehensive', 'other'))
            """
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_question_groups_type "
        "ON question_groups(group_type)"
    )
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (30, ?)",
        (migration_timestamp,),
    )
    connection.commit()


def _validate_version_thirty_schema(
    connection: sqlite3.Connection,
) -> None:
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(question_groups)")
    }
    if "group_type" not in columns:
        raise MigrationError(
            "v30 校验失败：question_groups 缺少 group_type 列。"
        )
    legacy_ok = connection.execute(
        "SELECT COUNT(*) AS n FROM question_groups"
        " WHERE group_type IS NULL OR group_type = ''"
    ).fetchone()["n"]
    if legacy_ok:
        raise MigrationError(
            "v30 校验失败：存在 group_type 为空的历史题组行。"
        )


def _apply_version_thirty_one(connection: sqlite3.Connection) -> None:
    """Schema v31: recursive question-granularity tree.

    ``question_items`` remains the learning/mastery unit and therefore only
    represents atomic leaves.  ``question_nodes`` stores the source-faithful
    hierarchy around those leaves: composite parents are context containers,
    never independent mastery records.  Context and media stay referenced by
    JSON identifiers instead of being copied into every leaf.
    """

    migration_timestamp = _utc_now()
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS question_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
            source_page_id INTEGER REFERENCES pages(id) ON DELETE SET NULL,
            question_item_id INTEGER UNIQUE
                REFERENCES question_items(id) ON DELETE SET NULL,
            parent_question_id INTEGER
                REFERENCES question_nodes(id) ON DELETE CASCADE,
            root_question_id INTEGER
                REFERENCES question_nodes(id) ON DELETE CASCADE,
            tree_fingerprint TEXT NOT NULL,
            node_path TEXT NOT NULL,
            question_label TEXT NOT NULL DEFAULT '',
            question_level INTEGER NOT NULL DEFAULT 0
                CHECK (question_level >= 0),
            question_kind TEXT NOT NULL
                CHECK (question_kind IN ('atomic', 'composite')),
            local_prompt TEXT NOT NULL DEFAULT '',
            shared_context_refs TEXT NOT NULL DEFAULT '[]',
            page_refs TEXT NOT NULL DEFAULT '[]',
            image_refs TEXT NOT NULL DEFAULT '[]',
            answer_refs TEXT NOT NULL DEFAULT '[]',
            display_order INTEGER NOT NULL DEFAULT 0,
            is_leaf INTEGER NOT NULL CHECK (is_leaf IN (0, 1)),
            split_source TEXT NOT NULL DEFAULT 'ai_inference'
                CHECK (split_source IN ('explicit_numbering', 'layout',
                                        'ai_inference', 'manual')),
            split_confidence TEXT NOT NULL DEFAULT 'low'
                CHECK (split_confidence IN ('high', 'medium', 'low')),
            status TEXT NOT NULL DEFAULT 'ai_draft'
                CHECK (status IN ('ai_draft', 'user_confirmed', 'manual')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (document_id, source_page_id, tree_fingerprint, node_path)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_question_nodes_parent "
        "ON question_nodes(parent_question_id, display_order)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_question_nodes_root "
        "ON question_nodes(root_question_id, question_level, display_order)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_question_nodes_item "
        "ON question_nodes(question_item_id)"
    )
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (31, ?)",
        (migration_timestamp,),
    )
    connection.commit()


def _validate_version_thirty_one_schema(
    connection: sqlite3.Connection,
) -> None:
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(question_nodes)")
    }
    required = {
        "id", "document_id", "source_page_id", "question_item_id",
        "parent_question_id", "root_question_id", "tree_fingerprint",
        "node_path", "question_label", "question_level", "question_kind",
        "local_prompt", "shared_context_refs", "page_refs", "image_refs",
        "answer_refs", "display_order", "is_leaf", "split_source",
        "split_confidence", "status", "created_at", "updated_at",
    }
    if not required.issubset(columns):
        raise MigrationError(
            "v31 校验失败：question_nodes 缺列："
            f"{sorted(required - columns)}"
        )


def _apply_version_thirty_two(connection: sqlite3.Connection) -> None:
    """Schema v32: persist the human-reviewed subject on each question.

    Subject is deliberately question-owned rather than learner-profile-owned:
    one local workspace can organize mathematics, physics, history and other
    disciplines without changing the student's education-stage configuration.
    """

    migration_timestamp = _utc_now()
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(question_items)")
    }
    if "subject" not in columns:
        connection.execute(
            "ALTER TABLE question_items ADD COLUMN subject TEXT NOT NULL DEFAULT ''"
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_question_items_subject "
        "ON question_items(subject)"
    )
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (32, ?)",
        (migration_timestamp,),
    )
    connection.commit()


def _validate_version_thirty_two_schema(connection: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(question_items)")
    }
    if "subject" not in columns:
        raise MigrationError("v32 校验失败：question_items 缺少 subject 列。")


def _apply_version_thirty_three(connection: sqlite3.Connection) -> None:
    """Keep the brief correction, teaching explanation and method separate."""

    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(question_items)")
    }
    for column in ("analysis_note", "solution_method", "math_display_json"):
        if column not in columns:
            connection.execute(
                f"ALTER TABLE question_items ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
            )
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (33, ?)",
        (_utc_now(),),
    )
    connection.commit()


def _validate_version_thirty_three_schema(connection: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(question_items)")
    }
    missing = {"analysis_note", "solution_method", "math_display_json"} - columns
    if missing:
        raise MigrationError(f"v33 校验失败：question_items 缺列：{sorted(missing)}")


def _apply_version_thirty_four(connection: sqlite3.Connection) -> None:
    """Persist semantic shared-stem decisions without guessing for legacy rows."""

    columns = {row[1] for row in connection.execute("PRAGMA table_info(question_nodes)")}
    if "has_shared_stem" not in columns:
        connection.execute(
            "ALTER TABLE question_nodes ADD COLUMN has_shared_stem INTEGER "
            "CHECK (has_shared_stem IN (0, 1))"
        )
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (34, ?)", (_utc_now(),)
    )
    connection.commit()


def _validate_version_thirty_four_schema(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(question_nodes)")}
    if "has_shared_stem" not in columns:
        raise MigrationError("v34 校验失败：question_nodes 缺少 has_shared_stem 列。")


def _apply_version_thirty_five(connection: sqlite3.Connection) -> None:
    """Link learning questions to existing knowledge objects without copying either."""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS question_knowledge_links (
            question_id INTEGER NOT NULL REFERENCES question_items(id) ON DELETE CASCADE,
            knowledge_object_id INTEGER NOT NULL
                REFERENCES knowledge_objects(id) ON DELETE CASCADE,
            note TEXT NOT NULL DEFAULT '' CHECK(length(note) <= 1000),
            created_at TEXT NOT NULL,
            PRIMARY KEY (question_id, knowledge_object_id)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_question_knowledge_links_knowledge "
        "ON question_knowledge_links(knowledge_object_id)"
    )
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (35, ?)", (_utc_now(),)
    )
    connection.commit()


def _validate_version_thirty_five_schema(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(question_knowledge_links)")}
    if columns != {"question_id", "knowledge_object_id", "note", "created_at"}:
        raise MigrationError("v35 校验失败：题目与知识关联表结构不完整。")
    foreign_keys = {
        (row[2], row[3], row[4], row[6])
        for row in connection.execute("PRAGMA foreign_key_list(question_knowledge_links)")
    }
    if foreign_keys != {
        ("question_items", "question_id", "id", "CASCADE"),
        ("knowledge_objects", "knowledge_object_id", "id", "CASCADE"),
    }:
        raise MigrationError("v35 校验失败：题目与知识关联缺少完整的引用约束。")


def _apply_version_thirty_six(connection: sqlite3.Connection) -> None:
    """Store confirmed knowledge classifications separately from original material."""

    with connection:
        connection.execute("BEGIN")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS knowledge_subject_classifications ("
            "knowledge_object_id INTEGER PRIMARY KEY "
            "REFERENCES knowledge_objects(id) ON DELETE CASCADE, "
            "subject TEXT NOT NULL CHECK(length(subject) BETWEEN 1 AND 120), "
            "subdiscipline TEXT NOT NULL DEFAULT '' CHECK(length(subdiscipline) <= 120))"
        )
        _validate_version_thirty_six_schema(connection)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (36, ?)", (_utc_now(),)
        )


def _validate_version_thirty_six_schema(connection: sqlite3.Connection) -> None:
    """Reject incomplete classification metadata schemas before loading assets."""

    columns = {row[1] for row in connection.execute(
        "PRAGMA table_info(knowledge_subject_classifications)")}
    foreign_keys = {
        (row[2], row[3], row[4], row[6])
        for row in connection.execute("PRAGMA foreign_key_list(knowledge_subject_classifications)")
    }
    if columns != {"knowledge_object_id", "subject", "subdiscipline"} or foreign_keys != {
        ("knowledge_objects", "knowledge_object_id", "id", "CASCADE"),
    }:
        raise MigrationError("v36 校验失败：知识点学科分类表结构或引用约束不完整。")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


__all__ = ["MigrationError", "SCHEMA_VERSION", "backup_database", "migrate_database"]
