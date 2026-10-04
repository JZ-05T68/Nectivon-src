"""F6-05: dissolve_group must undo group structure without losing questions.

A wrong confirmation can never be a dead end: dissolving keeps every
subquestion row (link cleared), removes the group's material rows and the
evidence rows that referenced them, and deletes the group — all in one
transaction.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.database import Database
from src.question_group_service import (
    add_group_material,
    attach_question_to_group,
    confirm_group,
    dissolve_group,
    draft_group,
    set_question_evidence,
)


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    return Database(tmp_path / "data" / "database" / "knowledge.db")


def _seed_document(database: Database, title: str) -> int:
    fake_hash = hashlib.sha256(title.encode("utf-8")).hexdigest()
    document = database.create_document(
        title=title,
        filename=f"{title}.pdf",
        source_path=f"C:/corpus/{title}.pdf",
        sha256=fake_hash,
    )
    return int(document.id)


def _seed_page(database: Database, document_id: int, page_number: int) -> int:
    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            "INSERT INTO pages(document_id, page_number, image_path,"
            " status, review_status, created_at, updated_at)"
            " VALUES (?, ?, 'p.png', 'pending_review', 'pending',"
            " datetime('now'), datetime('now'))",
            (document_id, page_number),
        )
        return int(cursor.lastrowid)


def _seed_question(database: Database, document_id: int, stem: str) -> int:
    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            """
            INSERT INTO question_items(
                document_id, question_number, question_kind, stem_text,
                search_stem_text, stem_confidence, created_at, updated_at
            ) VALUES (?, '1', 'error', ?, ?, 'confirmed',
                      datetime('now'), datetime('now'))
            """,
            (document_id, stem, stem),
        )
        return int(cursor.lastrowid)


def test_dissolve_keeps_subquestions_and_removes_group_structure(
    database: Database,
) -> None:
    document_id = _seed_document(database, "解散测试卷")
    pid = _seed_page(database, document_id, 1)
    group_id = draft_group(
        database,
        document_id=document_id,
        group_number="3~5题组",
        page_ids=[pid],
        subquestion_numbers=["3", "4", "5"],
        group_type="choice",
    )
    confirm_group(database, group_id)
    material_id = add_group_material(
        database,
        group_id=group_id,
        material_kind="text_material",
        material_label="3~5题组共享材料",
        page_id=pid,
        content_text="材料全文",
    )
    q1 = _seed_question(database, document_id, "第3题题干")
    q2 = _seed_question(database, document_id, "第4题题干")
    attach_question_to_group(database, q1, group_id)
    attach_question_to_group(database, q2, group_id)
    evidence_id = set_question_evidence(
        database,
        question_item_id=q1,
        evidence_type="text_material",
        source_id=material_id,
        page_id=pid,
        status="user_confirmed",
        confidence="confirmed",
    )

    dissolve_group(database, group_id)

    with database._connection() as connection:  # noqa: SLF001
        connection.row_factory = lambda cursor, row: row
        assert connection.execute(
            "SELECT COUNT(*) FROM question_groups WHERE id = ?", (group_id,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM question_group_materials WHERE group_id = ?",
            (group_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM question_item_evidence WHERE id = ?",
            (evidence_id,),
        ).fetchone()[0] == 0
        # subquestions SURVIVE with cleared links
        for qid in (q1, q2):
            row = connection.execute(
                "SELECT group_id FROM question_items WHERE id = ?", (qid,)
            ).fetchone()
            assert row[0] is None
            assert connection.execute(
                "SELECT COUNT(*) FROM question_items WHERE id = ?", (qid,)
            ).fetchone()[0] == 1


def test_dissolve_missing_group_raises(database: Database) -> None:
    from src.question_group_service import QuestionGroupError

    with pytest.raises(QuestionGroupError):
        dissolve_group(database, 999999)


def test_group_source_pages_roundtrip_after_dissolve_can_recreate(
    database: Database,
) -> None:
    """拆组 → 重组：the same pages can be grouped again afterwards."""

    document_id = _seed_document(database, "重组测试卷")
    pid = _seed_page(database, document_id, 1)
    group_id = draft_group(
        database,
        document_id=document_id,
        group_number="1~2题组",
        page_ids=[pid],
        subquestion_numbers=["1", "2"],
        group_type="choice",
    )
    confirm_group(database, group_id)
    dissolve_group(database, group_id)
    new_group_id = draft_group(
        database,
        document_id=document_id,
        group_number="1~2题组",
        page_ids=[pid],
        subquestion_numbers=["1", "2"],
        group_type="choice",
    )
    confirm_group(database, new_group_id)
    with database._connection() as connection:  # noqa: SLF001
        connection.row_factory = lambda cursor, row: row
        row = connection.execute(
            "SELECT source_pages, status FROM question_groups WHERE id = ?",
            (new_group_id,),
        ).fetchone()
    assert json.loads(row[0]) == [pid]
    assert row[1] == "user_confirmed"
