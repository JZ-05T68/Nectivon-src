"""Schema v22 migration: stage-2 user edits + explanation attempts."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from src.database import Database
from src.migrations import SCHEMA_VERSION, migrate_database


@pytest.fixture()
def legacy_db_path(tmp_path: Path) -> Path:
    """A database downgraded from v22 to a v21 state (columns removed)."""

    db_path = tmp_path / "data" / "database" / "knowledge.db"
    db = Database(db_path)  # builds the full current schema and migrates to v22
    raw = db_path.parent.parent / "raw" / "手写.pdf"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(b"%PDF-1.7 fixture")
    image = db_path.parent.parent / "pages" / "1" / "page-1.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"png")
    document = db.create_document(
        title="手写",
        filename="手写.pdf",
        source_path=raw,
        sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
        page_count=1,
        import_status="completed",
    )
    db.create_page(
        document_id=document.id,
        page_number=1,
        image_path=image,
        extracted_text="",
        status="ready",
    )
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        INSERT INTO page_visual_interpretations(
            page_id, provenance, content, created_at, updated_at
        ) VALUES (1, 'HANDWRITING_VISION', '旧草稿', '2026-09-26T00:00:00', '2026-09-26T00:00:00')
        """
    )
    # Downgrade to a v21-shaped database: drop the v22 objects and ledger row.
    connection.execute("DROP TABLE explanation_attempts")
    connection.execute(
        "ALTER TABLE page_visual_interpretations DROP COLUMN user_edited"
    )
    connection.execute(
        "ALTER TABLE page_visual_interpretations DROP COLUMN original_ai_content"
    )
    connection.execute("DELETE FROM schema_migrations WHERE version >= 22")
    connection.commit()
    connection.close()
    return db_path


def test_v22_migration_adds_columns_and_table_and_keeps_rows(legacy_db_path: Path) -> None:
    migrate_database(legacy_db_path)
    connection = sqlite3.connect(legacy_db_path)
    try:
        columns = {
            row[1]
            for row in connection.execute('PRAGMA table_info(page_visual_interpretations)')
        }
        assert {"user_edited", "original_ai_content"} <= columns
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "explanation_attempts" in tables
        version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        assert version == SCHEMA_VERSION
        row = connection.execute(
            "SELECT content, user_edited, original_ai_content FROM page_visual_interpretations"
        ).fetchone()
        assert row[0] == "旧草稿"
        assert row[1] == 0
        assert row[2] == ""
    finally:
        connection.close()
