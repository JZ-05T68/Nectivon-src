"""V086-305/208 regression: NULL-source learning assets stay first-class.

The deletion flow detaches question_items (document_id/page_id SET NULL).
Every read path must accept NULL sources natively — no TypeError, no
filtered-out rows, no crash of the five learning tabs — and the v21 source
snapshot must keep "where did this come from" answerable after deletion.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.database import Database
from src.document_deletion_service import DocumentDeletionError, DocumentDeletionService
from src.learning_workflow_service import QuestionService


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "pages" / "1").mkdir(parents=True)
    raw = tmp_path / "data" / "raw" / "试卷.pdf"
    raw.write_bytes(b"%PDF-1.7 fixture")
    image = tmp_path / "data" / "pages" / "1" / "page-1.png"
    image.write_bytes(b"png")
    document = db.create_document(
        title="16苏州数学",
        filename="试卷.pdf",
        source_path=raw,
        sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
        page_count=1,
        import_status="completed",
    )
    db.create_page(
        document_id=document.id,
        page_number=1,
        image_path=image,
        extracted_text="第1题 已知函数 f(x)=x²lnx，求极值。",
        status="ready",
    )
    return db


def _deletion_service(database: Database, tmp_path: Path) -> DocumentDeletionService:
    return DocumentDeletionService(
        database=database,
        raw_dir=tmp_path / "data" / "raw",
        pages_dir=tmp_path / "data" / "pages",
        markdown_dir=tmp_path / "data" / "markdown",
        data_dir=tmp_path / "data",
    )


def test_snapshot_recorded_at_creation(database: Database) -> None:
    service = QuestionService(database)
    question = service.create_question_item(
        document_id=1, page_id=1, question_kind="error", stem_text="题干"
    )
    assert question.source_available
    assert question.source_document_title_snapshot == "16苏州数学"
    assert question.source_page_label_snapshot == "第 1 页"


def test_from_row_accepts_null_source_like_existing_dirty_rows(database: Database) -> None:
    """A pre-existing NULL-source row (8511 id=1/2, 8512 id=1/2) must read."""

    with database._connection() as connection:
        connection.execute(
            """
            INSERT INTO question_items(
                document_id, page_id, question_kind, stem_text, stem_confidence,
                created_at, updated_at
            ) VALUES (NULL, NULL, 'error', '', 'uncertain', '2026-09-25T00:00:00+00:00',
                      '2026-09-25T00:00:00+00:00')
            """
        )
    service = QuestionService(database)
    questions = service.list_question_items()
    assert len(questions) == 1
    question = questions[0]
    assert question.document_id is None
    assert question.page_id is None
    assert not question.source_available
    # Detail view round-trip works too (page 18 calls get_question_item).
    assert service.get_question_item(question.id).document_id is None


def test_five_list_paths_survive_null_source(database: Database) -> None:
    """The five learning tabs all read through list/entries paths."""
    with database._connection() as connection:
        connection.execute(
            """
            INSERT INTO question_items(
                document_id, page_id, question_kind, stem_text, stem_confidence,
                created_at, updated_at
            ) VALUES (NULL, NULL, 'good', '', 'uncertain', '2026-09-25T00:00:00+00:00',
                      '2026-09-25T00:00:00+00:00')
            """
        )
    service = QuestionService(database)
    # 题目库 / 掌握训练 / 输出册子 pickers
    assert len(service.list_question_items()) == 1
    assert len(service.list_question_items(question_kind="good")) == 1
    assert service.search_questions("") == []
    # 两翼 / 归纳族 member joins (QuestionOrganizationService._member_from_row)
    from src.learning_workflow_service import QuestionOrganizationService

    organization = QuestionOrganizationService(database)
    family_id = organization.create_family(family_kind="method", title="族")
    assert organization.list_family_members(family_id) == []


def test_delete_detaches_asset_and_keeps_snapshot_and_usability(
    database: Database, tmp_path: Path
) -> None:
    """The V086-305 chain end-to-end: delete → asset usable, no TypeError."""
    service = QuestionService(database)
    question = service.create_question_item(
        document_id=1, page_id=1, question_kind="good", stem_text="抛体射程问题"
    )
    deletion = _deletion_service(database, tmp_path)
    result = deletion.delete_document(1, expected_title="16苏州数学")
    assert result.deleted
    survivor = service.get_question_item(question.id)
    assert survivor.id == question.id  # real learning asset retained
    assert not survivor.source_available
    assert survivor.document_id is None
    assert survivor.page_id is None
    # Snapshot keeps provenance alive; evidence is honestly unavailable.
    assert survivor.source_document_title_snapshot == "16苏州数学"
    assert survivor.source_page_label_snapshot == "第 1 页"
    # The list path that used to crash the whole 学习整理 page now works.
    assert any(item.id == question.id for item in service.list_question_items())


def test_pre_snapshot_null_row_is_honestly_unknown(database: Database) -> None:
    """Rows detached before the snapshot existed must not gain fake sources."""

    with database._connection() as connection:
        connection.execute(
            """
            INSERT INTO question_items(
                document_id, page_id, question_kind, stem_text, stem_confidence,
                created_at, updated_at
            ) VALUES (NULL, NULL, 'method', '', 'uncertain',
                      '2026-09-25T00:00:00+00:00', '2026-09-25T00:00:00+00:00')
            """
        )
    question = QuestionService(database).list_question_items()[0]
    assert question.source_document_title_snapshot == ""
    assert question.source_page_label_snapshot == ""
    assert not question.source_available


def test_delete_title_mismatch_still_aborts_before_any_change(
    database: Database, tmp_path: Path
) -> None:
    deletion = _deletion_service(database, tmp_path)
    with pytest.raises(DocumentDeletionError):
        deletion.delete_document(1, expected_title="别的标题")
    assert QuestionService(database).list_question_items() == []
