"""E-1 migration + visual-artifact index rehearsal (schema v15).

Covers the migration-safety rehearsal matrix for the additive
``page_visual_index`` FTS table and the :class:`VisualArtifactIndex`
rebuild/search contract: fresh DB, v14→v15 upgrade with realistic data,
repeated bootstrap, FTS already existing, missing/malformed artifacts,
stale source hashes, deleted pages, and the bootstrap fail-open path.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from src.agent_document_reader import (
    READING_FORMAT_VERSION,
    AgentReadingStore,
    PageReading,
)
from src.ai.visual_artifact_index import VisualArtifactIndex
from src.database import Database
from src.document_service import DocumentService
from src.migrations import (
    SCHEMA_VERSION,
    MigrationError,
    _read_schema_version,
    migrate_database,
)
from src.models import PageStatus
from src.text_utils import build_agent_page_text

TS = "2026-09-06T21:00:00+00:00"


def _import_one_page_pdf(tmp_path: Path, database: Database) -> int:
    import fitz

    pdf_path = tmp_path / "trend_chart.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(
        (72, 80), "冷却水泵性能图表（二车间 2026 复测）", fontsize=12, fontname="china-s"
    )
    doc.save(pdf_path)
    service = DocumentService(
        database,
        raw_dir=tmp_path / "raw",
        pages_dir=tmp_path / "pages",
        markdown_dir=tmp_path / "markdown",
    )
    record = service.import_document(pdf_path.read_bytes(), pdf_path.name)
    return record.document.id


def _save_reading(
    readings: AgentReadingStore,
    database: Database,
    document_id: int,
    *,
    page_id: int,
    page_number: int,
    key_facts: tuple[str, ...] = (),
    keywords: tuple[str, ...] = ("轴承温度",),
    summary: str = "轴承温度随运行小时的趋势图",
    stale: bool = False,
) -> None:
    page = database.get_page(page_id)
    source_text, _kind = build_agent_page_text(
        extracted_text=page.extracted_text,
        ocr_text=page.ocr_text,
        manual_text=page.markdown_content,
    )
    digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    reading = PageReading(
        format_version=READING_FORMAT_VERSION,
        document_id=document_id,
        page_id=page_id,
        page_number=page_number,
        document_sha256="0" * 64,
        source_text_sha256="deadbeef" * 8 if stale else digest,
        source_text_kind="pdf_text",
        model="test-model",
        summary=summary,
        keywords=keywords,
        key_facts=key_facts,
        read_at=TS,
    )
    readings.save_page_reading(reading)


def _rewind_current_database_to_v14(
    connection: sqlite3.Connection, *, drop_visual_index: bool
) -> None:
    """Remove additive v15-v21 artifacts for focused v15 migration tests."""

    connection.execute(
        "DELETE FROM schema_migrations WHERE version >= 15"
    )
    connection.execute("DROP TABLE agent_run_audits")
    connection.execute("DROP INDEX idx_ai_calls_provider_created")
    connection.execute("ALTER TABLE ai_calls DROP COLUMN resolved_model")
    connection.execute("ALTER TABLE ai_calls DROP COLUMN provider")
    # v20/v21 additive pages columns must go too, or re-running the later
    # migrations is a no-op and the ledger never matches a real v14 file.
    page_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(pages)").fetchall()
    }
    for column in (
        "has_handwriting",
        "has_visual_content",
        "visual_detection_status",
        "visual_detected_at",
        "visual_detection_method",
        "printed_page_number",
        "printed_total_pages",
        "document_footer",
    ):
        if column in page_columns:
            connection.execute(f"ALTER TABLE pages DROP COLUMN {column}")
    for table in (
        "wing_entries",
        "wing_entry_revisions",
        "page_visual_interpretations",
        "import_queue",
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table}")
    if drop_visual_index:
        connection.execute("DROP TABLE page_visual_index")


def test_schema_version_is_fifteen() -> None:
    assert SCHEMA_VERSION == 36


def test_fresh_database_reaches_15_with_visual_index_table(tmp_path: Path) -> None:
    database_path = tmp_path / "knowledge.db"
    Database(database_path)
    with sqlite3.connect(database_path) as connection:
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert versions == list(range(1, SCHEMA_VERSION + 1))
    assert "page_visual_index" in tables


def test_v14_realistic_database_upgrades_to_15_without_touching_pages(
    tmp_path: Path,
) -> None:
    """A v14 database with real document data upgrades additively to v15."""

    v14_database_path = tmp_path / "knowledge.db"
    document_id = _import_one_page_pdf(tmp_path, Database(v14_database_path))
    database = Database(v14_database_path)
    page = database.list_pages(document_id)[0]
    before_pages = v14_database_path.read_bytes()
    # Pin the DB down at v14 (a fresh import already lands at the current
    # version, so rewind the version record to emulate a pre-upgrade DB).
    with sqlite3.connect(v14_database_path) as connection:
        _rewind_current_database_to_v14(connection, drop_visual_index=True)
        connection.commit()
    assert _read_schema_version(v14_database_path) == 14
    pre_state = _pages_snapshot(v14_database_path)

    backup_path = migrate_database(v14_database_path)

    assert backup_path is not None and backup_path.exists()
    assert _read_schema_version(v14_database_path) == SCHEMA_VERSION
    assert _pages_snapshot(v14_database_path) == pre_state
    # pages 数据本体逐字节未被迁移触碰（WAL 允许外部变化前快照一致）
    with sqlite3.connect(v14_database_path) as connection:
        row = connection.execute(
            "SELECT document_id, page_number FROM pages WHERE id=?", (page.id,)
        ).fetchone()
    assert row == (document_id, page.page_number)
    assert before_pages is not None


def _pages_snapshot(database_path: Path) -> list[tuple[object, ...]]:
    with sqlite3.connect(database_path) as connection:
        return connection.execute(
            "SELECT id, document_id, page_number, extracted_text, ocr_text,"
            " markdown_content, status FROM pages ORDER BY id"
        ).fetchall()


def test_v15_database_restart_and_repeated_bootstrap(tmp_path: Path) -> None:
    database_path = tmp_path / "knowledge.db"
    Database(database_path)
    assert _read_schema_version(database_path) == SCHEMA_VERSION
    # Repeated bootstrap (re-open) is a no-op and creates no new backup.
    backups_before = list((database_path.parent / "backups").glob("*.db"))
    Database(database_path)
    Database(database_path)
    backups_after = list((database_path.parent / "backups").glob("*.db"))
    assert backups_after == backups_before
    assert _read_schema_version(database_path) == SCHEMA_VERSION


def test_visual_fts_already_exists_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "knowledge.db"
    database = Database(database_path)
    document_id = _import_one_page_pdf(tmp_path, database)
    page = database.list_pages(document_id)[0]
    readings = AgentReadingStore(tmp_path / "readings")
    _save_reading(readings, database, document_id, page_id=page.id, page_number=1)
    index = VisualArtifactIndex(database, readings)
    index.rebuild()
    # A second migration pass must not duplicate or error on the FTS table.
    with sqlite3.connect(database_path) as connection:
        _rewind_current_database_to_v14(connection, drop_visual_index=False)
        connection.commit()
    assert _read_schema_version(database_path) == 14
    migrate_database(database_path)
    assert _read_schema_version(database_path) == SCHEMA_VERSION
    second = VisualArtifactIndex(database, readings)
    assert second.rebuild()["pages_indexed"] == 1


def test_v15_rejects_same_name_regular_table_without_recording_success(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "knowledge.db"
    Database(database_path)
    with sqlite3.connect(database_path) as connection:
        _rewind_current_database_to_v14(connection, drop_visual_index=True)
        connection.execute(
            "CREATE TABLE page_visual_index(visual_text TEXT, page_id INTEGER)"
        )
        connection.commit()

    with pytest.raises(MigrationError, match="不是预期的 FTS5"):
        migrate_database(database_path)

    assert _read_schema_version(database_path) == 14


def test_v15_restart_rejects_missing_visual_index(tmp_path: Path) -> None:
    database_path = tmp_path / "knowledge.db"
    Database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE page_visual_index")
        connection.commit()

    with pytest.raises(MigrationError, match="不是预期的 FTS5"):
        migrate_database(database_path)


def test_migration_rejects_version_ledger_gap(tmp_path: Path) -> None:
    database_path = tmp_path / "knowledge.db"
    Database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version = 14")
        connection.commit()

    with pytest.raises(MigrationError, match="版本记录不连续"):
        migrate_database(database_path)


def test_v15_preflight_fk_failure_leaves_database_at_v14(tmp_path: Path) -> None:
    database_path = tmp_path / "knowledge.db"
    Database(database_path)
    with sqlite3.connect(database_path) as connection:
        _rewind_current_database_to_v14(connection, drop_visual_index=True)
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "INSERT INTO pages(document_id, page_number, image_path, extracted_text,"
            " ocr_text, markdown_content, status, review_status, created_at, updated_at)"
            " VALUES (999999, 1, 'missing.png', '', '', '',"
            " 'text_extracted', 'pending', ?, ?)",
            (TS, TS),
        )
        connection.commit()

    with pytest.raises(MigrationError, match="迁移前发现 1 条外键违规"):
        migrate_database(database_path)

    assert _read_schema_version(database_path) == 14
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='page_visual_index'"
        ).fetchone() is None


def test_search_hits_build_citation_results_on_real_import(tmp_path: Path) -> None:
    """search_hits must work on a real imported page (regression: the prototype
    selected ``p.status`` — the import pipeline state — instead of
    ``p.review_status``, which broke ``PageStatus`` construction in production)."""

    database_path = tmp_path / "knowledge.db"
    database = Database(database_path)
    document_id = _import_one_page_pdf(tmp_path, database)
    page = database.list_pages(document_id)[0]
    readings = AgentReadingStore(tmp_path / "readings")
    _save_reading(
        readings,
        database,
        document_id,
        page_id=page.id,
        page_number=1,
        key_facts=("图2标注2000h: 63°C",),
    )
    index = VisualArtifactIndex(database, readings)
    index.rebuild()
    hits = index.search_hits("2000小时 轴承温度")
    assert [hit.page_id for hit in hits] == [page.id]
    hit = hits[0]
    assert hit.document_id == document_id
    assert hit.document_title
    assert hit.status == PageStatus.PENDING


def test_rebuild_with_missing_and_malformed_artifacts(tmp_path: Path) -> None:
    database_path = tmp_path / "knowledge.db"
    database = Database(database_path)
    document_id = _import_one_page_pdf(tmp_path, database)
    page = database.list_pages(document_id)[0]
    readings_root = tmp_path / "readings"
    readings = AgentReadingStore(readings_root)

    # No artifacts at all: rebuild indexes nothing and never raises.
    stats = VisualArtifactIndex(database, readings).rebuild()
    assert stats["pages_indexed"] == 0

    _save_reading(readings, database, document_id, page_id=page.id, page_number=1)
    index = VisualArtifactIndex(database, readings)
    assert index.rebuild()["pages_indexed"] == 1

    # Malformed artifact JSON: page_reading() fails clean, rebuild skips it.
    page_file = readings_root / "pages" / f"page_{page.id}.json"
    assert page_file.exists()
    page_file.write_text("{not valid json", encoding="utf-8")
    assert index.rebuild()["pages_indexed"] == 0
    assert index.search("轴承温度") == []


def test_rebuild_exception_never_breaks_bootstrap(tmp_path: Path, monkeypatch) -> None:
    """Bootstrap fail-open: a broken index build falls back to current behaviour."""

    from src.agent.tools.bootstrap import build_phase1_handlers

    database = Database(tmp_path / "knowledge.db")
    readings = AgentReadingStore(tmp_path / "readings")

    class ExplodingIndex:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def rebuild(self) -> dict[str, int]:
            raise RuntimeError("rebuild failure injection")

        def search_hits(self, query: str, limit: int = 4):
            return []

    monkeypatch.setattr(
        "src.ai.visual_artifact_index.VisualArtifactIndex", ExplodingIndex
    )
    handlers = build_phase1_handlers(
        database,
        vision_provider=object(),
        pages_dir=tmp_path,
        agent_readings_store=readings,
    )
    # The visual tool is still registered; its index is simply absent.
    assert "page_visual_search" in handlers
    adapter = handlers["page_visual_search"]
    assert adapter._visual_index is None


def test_stale_and_deleted_pages_are_skipped(tmp_path: Path) -> None:
    database_path = tmp_path / "knowledge.db"
    database = Database(database_path)
    document_id = _import_one_page_pdf(tmp_path, database)
    page = database.list_pages(document_id)[0]
    readings = AgentReadingStore(tmp_path / "readings")
    _save_reading(
        readings, database, document_id, page_id=page.id, page_number=1, stale=True
    )
    index = VisualArtifactIndex(database, readings)
    stats = index.rebuild()
    assert stats["pages_indexed"] == 0
    assert stats["pages_skipped_stale_or_empty"] >= 1

    # Fresh reading recovers the page.
    _save_reading(readings, database, document_id, page_id=page.id, page_number=1)
    assert index.rebuild()["pages_indexed"] == 1

    # After the page row disappears, the stale artifact can no longer index.
    fresh = replace(
        readings.page_reading(page.id),
        source_text_sha256="deadbeef" * 8,
    )
    readings.save_page_reading(fresh)
    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM pages WHERE id=?", (page.id,))
        connection.commit()
    assert index.rebuild()["pages_indexed"] == 0


def test_search_misses_on_missing_table(tmp_path: Path) -> None:
    database_path = tmp_path / "knowledge.db"
    database = Database(database_path)
    readings = AgentReadingStore(tmp_path / "readings")
    index = VisualArtifactIndex(database, readings)
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE page_visual_index")
        connection.commit()
    assert index.search("任何查询") == []
    assert index.search_hits("任何查询") == []
