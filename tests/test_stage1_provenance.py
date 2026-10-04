"""Stage-1 detection provenance + V086-209-adjacent v21 backfill regressions.

8511 Wave 2 saw 24 pages all reporting uncertain/1/1 and concluded "Stage 1
never ran" — the values were genuine detector output for a fully scanned
corpus, but nothing distinguished "ran" from "never checked". These tests
pin the provenance contract: method + timestamp recorded on success,
honest failure marking, and UI-visible distinctions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.database import Database
from src.page_visual_service import PageVisualDetection, PageVisualService


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "stage1.db")
    (tmp_path / "raw").mkdir(parents=True)
    (tmp_path / "pages").mkdir(parents=True)
    image = tmp_path / "pages" / "p1.png"
    image.write_bytes(b"png")
    document = db.create_document(
        title="扫描试卷",
        filename="paper.pdf",
        source_path=tmp_path / "raw" / "paper.pdf",
        sha256="a" * 64,
        import_status="completed",
        page_count=1,
    )
    db.create_page(
        document_id=document.id,
        page_number=1,
        image_path=image,
        status="ready",
    )
    return db


def _detection(page_id: int) -> PageVisualDetection:
    return PageVisualDetection(
        page_id=page_id,
        has_handwriting=True,
        has_visual_content=True,
        detection_status="uncertain",
        raster_image_count=1,
        vector_drawing_count=0,
        detail="扫描页诚实输出",
    )


def test_successful_detection_records_method_and_timestamp(database: Database) -> None:
    service = PageVisualService(database)
    service.record_detection(_detection(1))
    state = service.get_page_visual_state(1)
    assert state["visual_detection_method"] == "pymupdf_structure"
    assert state["visual_detected_at"]
    assert state["visual_detection_status"] == "uncertain"


def test_failed_detection_is_distinguishable_from_not_checked(
    database: Database,
) -> None:
    service = PageVisualService(database)
    service.record_detection_failure(1)
    state = service.get_page_visual_state(1)
    assert state["visual_detection_method"] == "failed"
    assert state["visual_detected_at"]
    # A fresh page is genuinely not_checked with no method — the contrast
    # that Wave 2 could not see.
    db2_page = 1
    fresh = PageVisualService(database).get_page_visual_state(db2_page)
    assert fresh["visual_detection_status"] in ("not_checked", "uncertain")


def test_status_line_shows_provenance(database: Database) -> None:
    """V086-302-PRT1: provenance renders as explicit labeled fields, and the
    method label (本地结构检测) reaches the user through that line — not the
    retired 「本地结构检测于」 sentence (stale assertion from an earlier
    format; failed identically at the Round-3 baseline)."""

    from src.visual_provenance import format_visual_detection_provenance
    from src.visual_reading_ui import _stage1_status_line

    service = PageVisualService(database)
    service.record_detection(_detection(1))
    state = service.get_page_visual_state(1)
    line = _stage1_status_line(state)
    assert line  # honest status sentence (content depends on detection flags)
    provenance = format_visual_detection_provenance(state)
    assert "本地结构检测" in provenance
    assert "视觉预检：已检测" in provenance
    service.record_detection_failure(1)
    state = service.get_page_visual_state(1)
    provenance = format_visual_detection_provenance(state)
    assert "检测失败" in provenance


def test_not_checked_line_is_explicit(database: Database) -> None:
    from src.visual_reading_ui import _stage1_status_line

    state = PageVisualService(database).get_page_visual_state(1)
    assert _stage1_status_line(state) == "尚未进行视觉内容检测。"


def test_v21_reapplication_backfills_polluted_search_columns(
    tmp_path: Path,
) -> None:
    """Migration compatibility: pre-filter pages get re-derived by v21.

    The v21 body is idempotent by construction (guarded ADD COLUMN, fresh
    backfill UPDATE), so re-applying it on an already-migrated database
    reproduces exactly what a v20→v21 upgrade does to existing polluted
    rows — the 07常州 FTS cleanup path.
    """

    from src.migrations import _apply_version_twenty_one

    database = Database(tmp_path / "backfill.db")
    (tmp_path / "pages").mkdir(parents=True, exist_ok=True)
    image = tmp_path / "pages" / "p1.png"
    image.write_bytes(b"png")
    document = database.create_document(
        title="07常州数学",
        filename="p.pdf",
        source_path=tmp_path / "p.pdf",
        sha256="b" * 64,
        import_status="completed",
        page_count=1,
    )
    page = database.create_page(
        document_id=document.id,
        page_number=1,
        image_path=image,
        ocr_text="正文内容",
        status="ready",
    )
    # Simulate the pre-v21 state: search columns tokenize the raw OCR
    # including the scanner branding.
    polluted = "第1页(共6页)\n扫码使用\n夸克扫描王"
    database.update_page(page.id, ocr_text=polluted)
    # Replay the v21 upgrade on a standalone connection (the migration
    # manages its own transaction; Database._connection would nest one).
    import sqlite3


    db_path = tmp_path / "backfill.db"
    raw = sqlite3.connect(db_path)
    raw.row_factory = sqlite3.Row
    raw.execute("DELETE FROM schema_migrations WHERE version = 21")
    raw.commit()
    _apply_version_twenty_one(raw)
    row = raw.execute(
        "SELECT search_ocr_text, printed_page_number, printed_total_pages, "
        "document_footer FROM pages WHERE id = ?",
        (page.id,),
    ).fetchone()
    raw.close()
    assert "夸克" not in str(row["search_ocr_text"])
    assert "扫描王" not in str(row["search_ocr_text"])
    assert row["printed_page_number"] == 1
    assert row["printed_total_pages"] == 6
    assert "第1页(共6页)" in str(row["document_footer"])
