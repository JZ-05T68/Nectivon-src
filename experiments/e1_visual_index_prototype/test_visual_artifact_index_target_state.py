"""FAIL-016 ADR Option E-1: visual-artifact retrieval index unit tests.

Covers the normalize contract (``2000h`` ↔ ``2000小时``), the idempotent
rebuild from on-disk reading artifacts, freshness skipping, and the
additive-only merge behaviour of the visual adapter pool.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path

from src.agent_document_reader import READING_FORMAT_VERSION, AgentReadingStore, PageReading
from src.ai.visual_artifact_index import (
    VisualArtifactIndex,
    normalize_visual_text,
)
from src.database import Database
from src.document_service import DocumentService
from src.migrations import SCHEMA_VERSION
from src.text_utils import build_agent_page_text


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
    page_number: int,
    page_id: int,
    key_facts: tuple[str, ...],
    keywords: tuple[str, ...],
) -> None:
    page = database.get_page(page_id)
    source_text, _kind = build_agent_page_text(
        extracted_text=page.extracted_text,
        ocr_text=page.ocr_text,
        manual_text=page.markdown_content,
    )
    reading = PageReading(
        format_version=READING_FORMAT_VERSION,
        document_id=document_id,
        page_id=page_id,
        page_number=page_number,
        document_sha256="0" * 64,
        source_text_sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        source_text_kind="pdf_text",
        model="test-model",
        summary="轴承温度随运行小时的趋势图",
        keywords=keywords,
        key_facts=key_facts,
        read_at="2026-09-06T21:00:00+00:00",
    )
    readings.save_page_reading(reading)


def test_normalize_visual_text_token_forms() -> None:
    assert "2000 小时" in normalize_visual_text("2000h: 63°C")
    assert normalize_visual_text("2000h").count("小时") == 1
    assert "°c" in normalize_visual_text("63℃")
    assert normalize_visual_text("AB cD") == "ab cd"
    assert normalize_visual_text("5000h 75°C") == normalize_visual_text("5000 小时 75°c")


def test_rebuild_search_and_freshness(tmp_path: Path) -> None:
    db_path = tmp_path / "knowledge.db"
    database = Database(db_path)
    # Later additive migrations must preserve the original v15 index contract.
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0] == SCHEMA_VERSION
        table = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'page_visual_index'"
        ).fetchone()
        assert table is not None and "USING fts5" in table[0]
    document_id = _import_one_page_pdf(tmp_path, database)
    page = database.list_pages(document_id)[0]

    readings = AgentReadingStore(tmp_path / "readings")
    _save_reading(
        readings,
        database,
        document_id,
        page_number=page.page_number,
        page_id=page.id,
        key_facts=("图2标注2000h: 63°C", "数据来自二车间2026复测，取代一车间旧记录"),
        keywords=("轴承温度", "二车间"),
    )
    index = VisualArtifactIndex(database, readings)
    stats = index.rebuild()
    assert stats["pages_indexed"] == 1
    # Idempotent rebuild keeps the same row count.
    assert index.rebuild()["pages_indexed"] == 1

    # The canonical Chinese query form finds the page whose artifact uses 2000h.
    assert page.id in index.search("2000小时 轴承温度", limit=5)
    # Same token form also matches directly.
    assert page.id in index.search("2000h", limit=5)
    # A stale reading (source hash no longer matching) must be skipped.
    stale = replace(
        readings.page_reading(page.id),
        source_text_sha256="deadbeef" * 8,
    )
    readings.save_page_reading(stale)
    assert index.rebuild()["pages_skipped_stale_or_empty"] >= 1
    assert index.search("2000小时 轴承温度", limit=5) == []


def test_adapter_pool_merge_is_additive_and_bounded(tmp_path: Path) -> None:
    from src.agent.tools.adapters.page_visual import PageVisualAdapter, merge_visual_hits
    from src.models import PageStatus, SearchResult

    visual_page = SearchResult(
        page_id=99,
        document_id=9,
        document_title="图表页",
        filename="chart.pdf",
        page_number=1,
        image_path=Path("img.png"),
        content="视觉阅读索引命中",
        snippet="",
        rank=0.0,
        status=PageStatus.READY,
        match_type="视觉阅读索引",
    )
    lexical_page = replace(visual_page, page_id=1, document_id=1, document_title="文本页")
    duplicate = replace(visual_page, page_id=1, document_id=1, document_title="文本页")

    class FakeIndex:
        def search_hits(self, query: str, limit: int = 4):
            return [visual_page, duplicate]

    class FakeService:
        def search(self, query: str, limit: int = 8):
            return [lexical_page]

    merged = merge_visual_hits(FakeService().search("q"), FakeIndex().search_hits("q"))
    assert [(hit.document_id, hit.page_id) for hit in merged] == [(1, 1), (9, 99)]
    # Original lexical pool object is not mutated.
    assert len(FakeService().search("q")) == 1

    # The adapter accepts the visual_index keyword (merge is wired through it).
    adapter = PageVisualAdapter(
        FakeService(),  # type: ignore[arg-type]
        kb_uuid="kb",
        vision_provider=object(),
        pages_dir=tmp_path,
        visual_index=FakeIndex(),
    )
    assert adapter.tool_name == "page_visual_search"
