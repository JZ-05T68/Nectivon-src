"""Overnight-round tests: stage-2 visual drafts become user-editable (§7)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.database import Database
from src.page_visual_service import PageVisualError, PageVisualService


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "pages" / "1").mkdir(parents=True)
    raw = tmp_path / "data" / "raw" / "手写页.pdf"
    raw.write_bytes(b"%PDF-1.7 fixture")
    image = tmp_path / "data" / "pages" / "1" / "page-1.png"
    image.write_bytes(b"png")
    db.create_document(
        title="手写测试",
        filename="手写页.pdf",
        source_path=raw,
        sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
        page_count=1,
        import_status="completed",
    )
    db.create_page(
        document_id=1,
        page_number=1,
        image_path=image,
        extracted_text="",
        status="ready",
    )
    return db


def test_user_edit_persists_and_flips_to_confirmed(database: Database) -> None:
    service = PageVisualService(database)
    interpretation_id = service.record_interpretation(
        1,
        provenance="HANDWRITING_VISION",
        content="AI 识别的原始草稿：x=1",
        confidence="uncertain",
        origin="ai_vision",
    )
    result = service.update_interpretation(
        interpretation_id, content="用户修正：x = 1（经检验）"
    )
    assert result["status"] == "confirmed"
    assert result["user_edited"] is True

    state = service.get_page_visual_state(1)
    reading = state["interpretations"][0]
    assert reading["content"] == "用户修正：x = 1（经检验）"
    assert reading["status"] == "confirmed"
    assert reading["user_edited"] == 1
    # The original AI draft stays auditable for undo / future re-runs.
    assert reading["original_ai_content"] == "AI 识别的原始草稿：x=1"


def test_second_edit_keeps_first_ai_snapshot(database: Database) -> None:
    service = PageVisualService(database)
    interpretation_id = service.record_interpretation(
        1,
        provenance="HANDWRITING_VISION",
        content="AI 草稿 v1",
        origin="ai_vision",
    )
    service.update_interpretation(interpretation_id, content="用户第一次修改")
    service.update_interpretation(interpretation_id, content="用户第二次修改")
    state = service.get_page_visual_state(1)
    reading = state["interpretations"][0]
    assert reading["content"] == "用户第二次修改"
    assert reading["original_ai_content"] == "AI 草稿 v1"


def test_empty_edit_is_rejected(database: Database) -> None:
    service = PageVisualService(database)
    interpretation_id = service.record_interpretation(
        1, provenance="HANDWRITING_VISION", content="草稿", origin="ai_vision"
    )
    with pytest.raises(PageVisualError):
        service.update_interpretation(interpretation_id, content="   ")
