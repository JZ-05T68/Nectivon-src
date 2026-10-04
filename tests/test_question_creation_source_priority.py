"""V086-209 regression: visual drafts enter questions; shells are forbidden.

「加入学习整理」 must carry the page's best available source into the new
question item (manual text > Stage 2 draft > Agent reading > OCR), never
create a silent empty shell when a source exists, and never duplicate an
identical source on repeated clicks.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.database import Database
from src.learning_entry_ui import _resolve_page_source
from src.learning_workflow_service import QuestionService
from src.models import Page


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "pages" / "1").mkdir(parents=True)
    raw = tmp_path / "data" / "raw" / "手写.pdf"
    raw.write_bytes(b"%PDF-1.7 fixture")
    image = tmp_path / "data" / "pages" / "1" / "page-1.png"
    image.write_bytes(b"png")
    document = db.create_document(
        title="第2章复习题",
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
        ocr_text="OCR 出来的手写内容",
        status="ready",
    )
    return db


def _page(database: Database, page_id: int = 1) -> Page:
    page = database.get_page(page_id)
    assert page is not None
    return page


def test_source_priority_manual_text_wins(database: Database) -> None:
    page = _page(database)
    database.update_page(page.id, markdown_content="用户人工校对的文字")
    content, label, extra = _resolve_page_source(_page(database))
    assert content.startswith("用户人工校对的文字")
    assert label == "人工校对文字"
    assert extra["origin"] == "user_manual_text"


def test_source_priority_prefers_stage2_draft_over_ocr(database: Database) -> None:
    from src.page_visual_service import PageVisualService

    service = PageVisualService(database)
    interpretation_id = service.record_interpretation(
        1,
        provenance="HANDWRITING_VISION",
        content="视觉草稿转录的 1781 字符内容",
        confidence="uncertain",
        origin="ai_vision",
        # Presence gate (fix round §18/§24): a handwriting reading only
        # becomes the page source when the model declared handwriting
        # present; the source-priority test must respect that contract.
        region_json={"handwriting_presence": "confirmed"},
    )
    content, label, extra = _resolve_page_source(
        _page(database), visual_service=service
    )
    assert "视觉草稿转录" in content
    # Overnight humanization: the raw enum key never surfaces; the label
    # reads as a plain-Chinese draft description and carries the presence
    # verdict so the user knows what the model decided.
    assert label.startswith("AI 视觉识别草稿")
    assert "已判定存在手写" in label
    assert extra["interpretation_id"] == interpretation_id
    assert extra["origin"] == "stage2_visual_draft"


def test_ocr_fallback_is_used_when_nothing_else_exists(database: Database) -> None:
    content, label, extra = _resolve_page_source(_page(database))
    assert content == "OCR 出来的手写内容"
    # Overnight humanization: developer slang (OCR) never reaches users.
    assert label == "系统识别出的文字"
    assert extra["origin"] == "page_ocr_text"


def test_create_with_source_keeps_draft_and_uncertain_stem(database: Database) -> None:
    """The created item is not a shell: ai_draft carries the full source."""

    page = _page(database)
    content, label, extra = _resolve_page_source(page)
    source_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    question = QuestionService(database).create_question_item(
        document_id=page.document_id,
        page_id=page.id,
        question_kind="good",
        stem_confidence="uncertain",
        ai_draft={
            "kind": "source_context",
            "segmentation": "pending",
            "provenance": label,
            "content": content,
            "source_sha256": source_sha256,
            **extra,
        },
    )
    assert question.ai_draft is not None
    assert question.ai_draft["content"] == "OCR 出来的手写内容"
    assert question.ai_draft["segmentation"] == "pending"
    assert question.stem_confidence == "uncertain"


def test_duplicate_guard_blocks_identical_source(database: Database) -> None:
    service = QuestionService(database)
    page = _page(database)
    content, label, extra = _resolve_page_source(page)
    fingerprint = hashlib.sha256(content.encode("utf-8")).hexdigest()
    draft = {
        "kind": "source_context",
        "segmentation": "pending",
        "provenance": label,
        "content": content,
        "source_sha256": fingerprint,
        **extra,
    }
    first = service.create_question_item(
        document_id=page.document_id,
        page_id=page.id,
        question_kind="good",
        ai_draft=draft,
    )
    duplicate = service.find_duplicate_source(
        document_id=page.document_id,
        page_id=page.id,
        question_kind="good",
        source_sha256=fingerprint,
    )
    assert duplicate is not None
    assert duplicate.id == first.id
    # A different kind on the same page is a separate asset, not a duplicate.
    assert (
        service.find_duplicate_source(
            document_id=page.document_id,
            page_id=page.id,
            question_kind="error",
            source_sha256=fingerprint,
        )
        is None
    )


def test_different_source_on_same_page_is_not_a_duplicate(database: Database) -> None:
    service = QuestionService(database)
    page = _page(database)
    service.create_question_item(
        document_id=page.document_id,
        page_id=page.id,
        question_kind="good",
        ai_draft={"source_sha256": "a" * 64, "content": "旧草稿"},
    )
    assert (
        service.find_duplicate_source(
            document_id=page.document_id,
            page_id=page.id,
            question_kind="good",
            source_sha256="b" * 64,
        )
        is None
    )


def test_exam_structure_is_preserved_verbatim_never_assumed(database: Database) -> None:
    """User boundary (2026-09-26): no fixed 22/19-question shape.

    Question numbers, per-question scores and section titles found on the
    page must survive verbatim into the item's source evidence; this module
    must not inject or derive question numbers from fixed ranges.
    """

    page = _page(database)
    database.update_page(
        page.id,
        ocr_text=(
            "二、多项选择题：本题共 3 小题，每小题 6 分，共 18 分。\n"
            "9．已知函数 f(x)，下列说法正确的是（多选）\n"
            "17．（17 分）已知抛物线 C:x²=2py。\n"
            "第1页（共6页）"
        ),
    )
    content, label, extra = _resolve_page_source(_page(database))
    # Structure lines (题型标题 / 题号 / 分值) are preserved as raw evidence…
    assert "本题共 3 小题，每小题 6 分" in content
    assert "17．（17 分）" in content
    assert "9．" in content
    # …and the creation path must not invent question numbering.
    source_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    question = QuestionService(database).create_question_item(
        document_id=page.document_id,
        page_id=page.id,
        question_kind="good",
        stem_confidence="uncertain",
        ai_draft={
            "kind": "source_context",
            "segmentation": "pending",
            "provenance": label,
            "content": content,
            "source_sha256": source_sha256,
            **extra,
        },
    )
    assert question.question_number == ""  # never derived from fixed ranges
    assert question.ai_draft["content"] == content  # verbatim source evidence
