"""Deletion policy for learning assets (v0.8.6 duty B).

Policy under test: organized questions deliberately SURVIVE document
deletion, detached from the document/page (ON DELETE SET NULL). Current
schema evidence relationships and confirmation state survive while page
provenance is detached; family memberships and mastery records stay intact.
The preview must show this impact to the user before anything happens.
"""

from __future__ import annotations

import hashlib
from contextlib import closing
from pathlib import Path

import pytest

from src.database import Database
from src.document_deletion_service import DocumentDeletionService
from src.learning_workflow_service import (
    MasteryService,
    QuestionOrganizationService,
    QuestionService,
)
from src.models import DocumentAggregationImpact
from src.question_group_service import (
    add_group_material,
    attach_question_to_group,
    confirm_group,
    draft_group,
    set_question_evidence,
)


@pytest.fixture()
def organized_workspace(tmp_path: Path) -> tuple[Database, Path]:
    """One document with pages, organized questions, evidence and mastery."""

    database = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "pages" / "1").mkdir(parents=True)
    raw = tmp_path / "data" / "raw" / "试卷.pdf"
    raw.write_bytes(b"%PDF-1.7 fixture")
    document = database.create_document(
        title="高三数学周测",
        filename="试卷.pdf",
        source_path=raw,
        sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
        page_count=2,
        import_status="completed",
    )
    page_one = tmp_path / "data" / "pages" / "1" / "page-1.png"
    page_one.write_bytes(b"png1")
    page_two = tmp_path / "data" / "pages" / "1" / "page-2.png"
    page_two.write_bytes(b"png2")
    database.create_page(
        document_id=document.id, page_number=1, image_path=page_one, status="ready"
    )
    database.create_page(
        document_id=document.id, page_number=2, image_path=page_two, status="ready"
    )
    questions = QuestionService(database)
    question = questions.create_question_item(
        document_id=document.id,
        page_id=1,
        question_kind="error",
        question_number="7",
        stem_text="设函数 f(x) 求单调区间。",
        stem_confidence="probable",
        teacher_verdict="incorrect",
    )
    same_paper_question = questions.create_question_item(
        document_id=document.id,
        page_id=2,
        question_kind="good",
        question_number="12",
        stem_text="证明数列单调有界。",
    )
    group_id = draft_group(
        database,
        document_id=document.id,
        group_number="7",
        page_ids=[1],
        subquestion_numbers=["7"],
    )
    confirm_group(database, group_id)
    attach_question_to_group(database, question.id, group_id)
    material_id = add_group_material(
        database,
        group_id=group_id,
        material_kind="figure",
        material_label="函数图像",
        page_id=1,
    )
    set_question_evidence(
        database,
        question_item_id=question.id,
        evidence_type="figure",
        source_id=material_id,
        page_id=1,
        status="user_confirmed",
        confidence="confirmed",
    )
    organization = QuestionOrganizationService(database)
    family_id = organization.create_family(
        family_kind="type",
        title="函数与导数：单调区间",
        description="先定义域后求导。",
    )
    organization.assign_to_family(question.id, family_id)
    organization.assign_to_family(same_paper_question.id, family_id)
    MasteryService(database).record_practice(question.id, outcome="incorrect")
    return database, tmp_path


def _delete_document(database: Database, data_dir: Path, document_id: int) -> None:
    service = DocumentDeletionService(
        database=database,
        raw_dir=data_dir / "data" / "raw",
        pages_dir=data_dir / "data" / "pages",
        markdown_dir=data_dir / "data" / "markdown",
        data_dir=data_dir / "data",
        app_version="0.8.6-test",
    )
    result = service.delete_document(document_id, expected_title="高三数学周测")
    assert result.deleted is True


def test_preview_reports_learning_layer_impact(organized_workspace) -> None:
    database, data_dir = organized_workspace
    service = DocumentDeletionService(
        database=database,
        raw_dir=data_dir / "data" / "raw",
        pages_dir=data_dir / "data" / "pages",
        markdown_dir=data_dir / "data" / "markdown",
        data_dir=data_dir / "data",
        app_version="0.8.6-test",
    )

    preview = service.preview_document_deletion(1)

    assert preview.learning_question_count == 2
    assert preview.learning_family_count == 1
    assert preview.learning_mastery_record_count == 1
    assert preview.learning_evidence_link_count == 1
    assert preview.path_anomalies == ()


def test_delete_survives_questions_and_detaches_evidence_page_provenance(
    organized_workspace,
) -> None:
    database, data_dir = organized_workspace
    _delete_document(database, data_dir, 1)

    with closing(sqlite3_connection(database)) as connection:
        questions = connection.execute(
            "SELECT id, document_id, page_id, stem_text FROM question_items ORDER BY id"
        ).fetchall()
        evidence_links = connection.execute(
            "SELECT question_item_id, source_id, page_id, status, confidence "
            "FROM question_item_evidence ORDER BY id"
        ).fetchall()
        materials = connection.execute(
            "SELECT id, page_id FROM question_group_materials ORDER BY id"
        ).fetchall()
        members = connection.execute(
            "SELECT COUNT(*) FROM question_family_members"
        ).fetchone()[0]
        mastery = connection.execute(
            "SELECT COUNT(*) FROM mastery_records"
        ).fetchone()[0]
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    assert len(questions) == 2
    assert all(row["document_id"] is None and row["page_id"] is None for row in questions)
    assert questions[0]["stem_text"] == "设函数 f(x) 求单调区间。"
    assert evidence_links[0]["question_item_id"] == questions[0]["id"]
    assert evidence_links[0]["status"] == "user_confirmed"
    assert evidence_links[0]["confidence"] == "confirmed"
    assert evidence_links[0]["page_id"] is None
    assert evidence_links[0]["source_id"] == materials[0]["id"]
    assert materials[0]["page_id"] is None
    assert members == 2
    assert mastery == 1
    assert violations == []

    # 学习层 FTS 仍然可检索（幸存内容不被静默移除）
    with closing(sqlite3_connection(database)) as connection:
        hits = connection.execute(
            "SELECT rowid FROM question_search WHERE question_search MATCH '单调'"
        ).fetchall()
    assert hits


def sqlite3_connection(database: Database):
    import sqlite3

    connection = sqlite3.connect(database.database_path)
    connection.row_factory = sqlite3.Row
    return connection


def test_delete_document_without_learning_assets_reports_zero_impact(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "pages" / "1").mkdir(parents=True)
    raw = tmp_path / "data" / "raw" / "plain.pdf"
    raw.write_bytes(b"%PDF-1.7 plain")
    document = database.create_document(
        title="普通文档",
        filename="plain.pdf",
        source_path=raw,
        sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
        import_status="completed",
    )
    image = tmp_path / "data" / "pages" / "1" / "page-1.png"
    image.write_bytes(b"png")
    database.create_page(
        document_id=document.id, page_number=1, image_path=image, status="pending"
    )
    service = DocumentDeletionService(
        database=database,
        raw_dir=tmp_path / "data" / "raw",
        pages_dir=tmp_path / "data" / "pages",
        markdown_dir=tmp_path / "data" / "markdown",
        data_dir=tmp_path / "data",
        app_version="0.8.6-test",
    )

    preview = service.preview_document_deletion(document.id)

    assert preview.learning_question_count == 0
    assert preview.learning_family_count == 0
    assert preview.learning_mastery_record_count == 0
    assert preview.learning_evidence_link_count == 0
    assert preview.aggregation_impact == DocumentAggregationImpact()
    result = service.delete_document(document.id, expected_title="普通文档")
    assert result.deleted is True


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "pages" / "1").mkdir(parents=True)
    raw = tmp_path / "data" / "raw" / "试卷.pdf"
    raw.write_bytes(b"%PDF-1.7 fixture")
    image = tmp_path / "data" / "pages" / "1" / "page-1.png"
    image.write_bytes(b"png")
    document = database.create_document(
        title="高三数学周测",
        filename="试卷.pdf",
        source_path=raw,
        sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
        page_count=1,
        import_status="completed",
    )
    database.create_page(
        document_id=document.id, page_number=1, image_path=image, status="ready"
    )
    return database


def test_question_requires_existing_page_at_creation(database: Database) -> None:
    service = QuestionService(database)
    with pytest.raises(Exception, match="页面不存在"):
        service.create_question_item(
            document_id=1, page_id=999, question_kind="error"
        )
