"""Overnight-round tests: bulk delete orchestration (§16)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.database import Database
from src.document_bulk_delete_service import (
    DocumentBulkDeleteService,
)
from src.document_deletion_service import DocumentDeletionService
from src.learning_workflow_service import (
    MasteryService,
    QuestionOrganizationService,
    QuestionService,
)


def _make_service(database: Database, tmp_path: Path) -> DocumentDeletionService:
    return DocumentDeletionService(
        database=database,
        raw_dir=tmp_path / "data" / "raw",
        pages_dir=tmp_path / "data" / "pages",
        markdown_dir=tmp_path / "data" / "markdown",
        data_dir=tmp_path / "data",
    )


@pytest.fixture()
def two_documents(tmp_path: Path) -> tuple[Database, Path, list[int]]:
    db = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "pages").mkdir(parents=True)
    (tmp_path / "data" / "markdown").mkdir(parents=True)
    ids: list[int] = []
    for index in (1, 2):
        raw = tmp_path / "data" / "raw" / f"试卷{index}.pdf"
        raw.write_bytes(b"%PDF-1.7 " + bytes([index]))
        image_dir = tmp_path / "data" / "pages" / str(index)
        image_dir.mkdir(parents=True, exist_ok=True)
        image = image_dir / "page-1.png"
        image.write_bytes(b"png")
        document = db.create_document(
            title=f"试卷{index}",
            filename=f"试卷{index}.pdf",
            source_path=raw,
            sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
            page_count=1,
            import_status="completed",
        )
        db.create_page(
            document_id=document.id,
            page_number=1,
            image_path=image,
            extracted_text=f"第{index}题",
            status="ready",
        )
        ids.append(document.id)
    return db, tmp_path, ids


def test_bulk_delete_removes_all_documents_and_keeps_learning_assets(
    two_documents,
) -> None:
    db, tmp_path, ids = two_documents
    service = DocumentBulkDeleteService(_make_service(db, tmp_path))

    questions = QuestionService(db)
    question = questions.create_question_item(
        document_id=ids[0],
        page_id=1,
        question_kind="error",
        question_number="1",
        stem_text="题干",
    )
    MasteryService(db).record_practice(question.id, outcome="incorrect")

    preview = service.preview(ids)
    assert preview.document_count == 2
    assert preview.page_count == 2
    assert preview.surviving_learning_questions == 1

    outcomes = service.execute(ids)
    assert all(outcome.deleted for outcome in outcomes)
    assert service.summarize(outcomes) == "成功 2 份，失败 0 份。"

    assert db.get_document(ids[0]) is None
    assert db.get_document(ids[1]) is None
    # The learning asset survives, detached, and mastery records stay.
    survivor = questions.get_question_item(question.id)
    assert survivor.source_available is False
    assert MasteryService(db).question_mastery_summary(question.id)["total"] == 1


def test_bulk_delete_isolates_partial_failures(two_documents) -> None:
    db, tmp_path, ids = two_documents
    deletion_service = _make_service(db, tmp_path)
    service = DocumentBulkDeleteService(deletion_service)

    # Give the first document a path anomaly (a recorded file outside its
    # owning root): the staged deletion must abort before touching anything
    # while the second document deletes normally.
    rogue = tmp_path / "outside" / "rogue.png"
    rogue.parent.mkdir(parents=True, exist_ok=True)
    rogue.write_bytes(b"png")
    with db._connection() as connection:
        connection.execute(
            "UPDATE pages SET image_path = ? WHERE document_id = ?",
            (str(rogue), ids[0]),
        )

    outcomes = service.execute(ids)
    failed = service.failed_ids(outcomes)
    assert failed == [ids[0]]
    assert not outcomes[0].deleted
    assert outcomes[0].message
    assert outcomes[1].deleted
    assert db.get_document(ids[1]) is None
    # The anomalous document still exists (abort means nothing changed).
    assert db.get_document(ids[0]) is not None

    # Retry still reports the same honest failure.
    retry_outcomes = service.execute(failed)
    assert retry_outcomes[0].deleted is False


def test_bulk_preview_aggregates_and_flags_missing_documents(two_documents) -> None:
    db, tmp_path, ids = two_documents
    service = DocumentBulkDeleteService(_make_service(db, tmp_path))
    preview = service.preview([ids[0], 99999])
    assert preview.document_count == 1
    assert preview.errors and preview.errors[0][0] == 99999


def test_learning_assets_still_resolve_after_bulk_delete(two_documents) -> None:
    db, tmp_path, ids = two_documents
    service = DocumentBulkDeleteService(_make_service(db, tmp_path))
    questions = QuestionService(db)
    organization = QuestionOrganizationService(db)
    question = questions.create_question_item(
        document_id=ids[0], page_id=1, question_kind="good", stem_text="好题"
    )
    family_id = organization.create_family(family_kind="type", title="函数与导数求极值")
    organization.assign_to_family(question.id, family_id)
    service.execute([ids[0]])
    families = organization.list_families_for_question(question.id)
    assert [family.title for family in families] == ["函数与导数求极值"]
    assert organization.get_family(family_id).member_count == 1
