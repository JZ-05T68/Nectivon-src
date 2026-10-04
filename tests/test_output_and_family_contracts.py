"""V086-308 + V086-303 service-layer regressions.

Output rendering must use user-readable Chinese labels (never leak internal
enums like ``good`` / ``uncertain`` into user documents), and family
listing/membership must behave consistently for the 归纳族 UI.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.database import Database
from src.learning_workflow_service import (
    MarkdownCollectionRenderer,
    MasteryService,
    OutputCollectionService,
    QuestionOrganizationService,
    QuestionService,
)


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "pages" / "1").mkdir(parents=True)
    raw = tmp_path / "data" / "raw" / "卷.pdf"
    raw.write_bytes(b"%PDF-1.7 fixture")
    image = tmp_path / "data" / "pages" / "1" / "page-1.png"
    image.write_bytes(b"png")
    db.create_document(
        title="抛体运动卷",
        filename="卷.pdf",
        source_path=raw,
        sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
        page_count=1,
        import_status="completed",
    )
    db.create_page(
        document_id=1,
        page_number=1,
        image_path=image,
        extracted_text="第2题 抛体射程。",
        status="ready",
    )
    return db


def test_output_renders_chinese_labels_not_internal_enums(database: Database) -> None:
    questions = QuestionService(database)
    question = questions.create_question_item(
        document_id=1,
        page_id=1,
        question_kind="good",
        stem_text="已知函数 f(x)=x²lnx，求极值。",
        teacher_verdict="incorrect",
        stem_confidence="uncertain",
    )
    output = OutputCollectionService(database)
    collection_id = output.create_collection(kind="error_book", title="抛体运动错题册")
    output.add_collection_item(collection_id, item_layer="question", item_id=question.id)
    rendered = MarkdownCollectionRenderer(database).render(collection_id).decode("utf-8")
    # V086-308: user-facing output uses Chinese labels.
    assert "类型：好题" in rendered
    assert "类型：good" not in rendered
    assert "题干置信度：不确定" in rendered
    assert "题干置信度：uncertain" not in rendered
    assert "判定：× 错误" in rendered
    # Underlying DB enums stay untouched.
    stored = questions.get_question_item(question.id)
    assert stored.question_kind == "good"
    assert stored.stem_confidence == "uncertain"


def test_output_marks_unavailable_source_honestly(database: Database) -> None:
    questions = QuestionService(database)
    question = questions.create_question_item(
        document_id=1, page_id=1, question_kind="error", stem_text="题干"
    )
    with database._connection() as connection:
        connection.execute(
            "UPDATE question_items SET document_id = NULL, page_id = NULL WHERE id = ?",
            (question.id,),
        )
    output = OutputCollectionService(database)
    collection_id = output.create_collection(kind="error_book", title="册子")
    output.add_collection_item(collection_id, item_layer="question", item_id=question.id)
    rendered = MarkdownCollectionRenderer(database).render(collection_id).decode("utf-8")
    assert "原始资料已删除，证据不可用" in rendered


def test_mastery_history_labels_are_localized(database: Database) -> None:
    """The mastery selectbox maps 做错 → incorrect (V086-307 UI contract).

    Overnight round: the label maps moved to the central
    ``src.display_labels`` module; the mapping contract is pinned there so
    做错 can never regress to raw "incorrect" on any page.
    """

    question = QuestionService(database).create_question_item(
        document_id=1, page_id=1, question_kind="error", stem_text="题干"
    )
    mastery = MasteryService(database)
    mastery.record_practice(question.id, outcome="incorrect")
    records = mastery.list_practice_records(question.id)
    assert records[0]["outcome"] == "incorrect"  # DB enum unchanged
    from pathlib import Path as _Path

    labels_path = (
        _Path(__file__).resolve().parents[1] / "src" / "display_labels.py"
    )
    source = labels_path.read_text(encoding="utf-8")
    assert '"incorrect": "做错"' in source
    assert '"correct": "做对"' in source


def test_family_list_covers_all_kinds_and_member_join_is_idempotent(
    database: Database,
) -> None:
    """V086-303: a method family is visible with the all-filter and the
    member join lands and repeats safely (idempotent upsert)."""

    organization = QuestionOrganizationService(database)
    family_id = organization.create_family(
        family_kind="method", title="消参后用 sin2α 值域"
    )
    # The UI default filter is "all" → list_families() must see every kind.
    kinds = {family.family_kind for family in organization.list_families()}
    assert kinds == {"method"}
    assert any(family.id == family_id for family in organization.list_families())
    question = QuestionService(database).create_question_item(
        document_id=1, page_id=1, question_kind="good", stem_text="抛体射程"
    )
    organization.assign_to_family(question.id, family_id, relation="member")
    organization.assign_to_family(question.id, family_id, relation="member")
    members = organization.list_family_members(family_id)
    assert len(members) == 1  # idempotent, no duplicate member rows
    assert members[0][1].id == question.id
    assert organization.get_family(family_id).member_count == 1
