"""V086-209 regression: visual drafts enter questions; shells are forbidden.

「加入学习整理」 must carry the page's best available source into the new
question item (manual text > image transcript > native text), never
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
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Database:
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
    from types import SimpleNamespace

    import src.page_image_ui as image_ui
    import src.runtime as runtime

    monkeypatch.setattr(
        runtime, "application_settings", lambda: SimpleNamespace(agent_readings_dir="unused")
    )
    monkeypatch.setattr(
        image_ui,
        "current_image_reading",
        lambda page, root: SimpleNamespace(
            transcript="AI直接读取原图的完整文字", model="qwen3.8-max"
        ),
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


def test_image_reading_source_keeps_model_provenance(database: Database) -> None:
    content, label, extra = _resolve_page_source(_page(database))
    assert content == "AI直接读取原图的完整文字"
    assert extra["origin"] == "page_image_reading" and extra["model"] == "qwen3.8-max"
    assert "OCR" not in content


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
    assert question.ai_draft["content"] == "AI直接读取原图的完整文字"
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
        markdown_content=(
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
