"""F6 P0 contract tests: group source_pages = page_ids, UI shows page numbers.

Round-F6 audit proved a real P0: ``question_groups.source_pages`` stores
GLOBAL ``pages.id`` values, but the UI rendered them as document-local
page numbers ("第 784 页") and jumped with them, and the navigation lost
ALL query params (``switch_page`` clears them by default), landing the
user on the WRONG DOCUMENT.  These tests pin the resolution contract:
page_id -> (document_id, page_number), deduped, ordered by page_number,
orphans honestly reported as ``missing`` — never silently dropped.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.database import Database
from src.question_group_service import (
    draft_group,
    resolve_group_source_pages,
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


def _insert_group_raw(
    database: Database,
    document_id: int,
    source_pages: str,
) -> int:
    """Insert a group row directly to control the raw source_pages JSON."""

    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            """
            INSERT INTO question_groups(
                document_id, group_number, group_summary, status,
                provenance, source_pages, group_type, created_at, updated_at
            ) VALUES (?, '1~2题组', '测试组', 'user_confirmed',
                      'user_confirmed', ?, 'choice',
                      datetime('now'), datetime('now'))
            """,
            (document_id, source_pages),
        )
        return int(cursor.lastrowid)


def _group_row(database: Database, group_id: int) -> dict:
    import sqlite3

    with database._connection() as connection:  # noqa: SLF001
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM question_groups WHERE id = ?", (group_id,)
        ).fetchone()
    assert row is not None
    return dict(row)


def test_page_ids_resolve_to_document_local_page_numbers(
    database: Database,
) -> None:
    """doc A pages get ids 100/101/102 → [100,102] means 第1页+第3页."""

    document_id = _seed_document(database, "试卷A")
    pid_p1 = _seed_page(database, document_id, 1)
    _seed_page(database, document_id, 2)
    pid_p3 = _seed_page(database, document_id, 3)
    group_id = _insert_group_raw(
        database, document_id, json.dumps([pid_p1, pid_p3])
    )
    resolved = resolve_group_source_pages(
        database, _group_row(database, group_id)
    )
    assert resolved["missing"] == []
    assert resolved["resolved"] == [
        {"page_id": pid_p1, "page_number": 1},
        {"page_id": pid_p3, "page_number": 3},
    ]


def test_resolved_ordered_by_page_number_not_by_page_id(
    database: Database,
) -> None:
    """Import order must not decide display order: page_number sorts."""

    document_id = _seed_document(database, "试卷B")
    pid_p2 = _seed_page(database, document_id, 2)
    pid_p1 = _seed_page(database, document_id, 1)
    group_id = _insert_group_raw(
        database, document_id, json.dumps([pid_p2, pid_p1])
    )
    resolved = resolve_group_source_pages(
        database, _group_row(database, group_id)
    )
    assert [item["page_number"] for item in resolved["resolved"]] == [1, 2]


def test_duplicate_page_ids_deduplicated(database: Database) -> None:
    document_id = _seed_document(database, "试卷C")
    pid = _seed_page(database, document_id, 1)
    group_id = _insert_group_raw(
        database, document_id, json.dumps([pid, pid, pid])
    )
    resolved = resolve_group_source_pages(
        database, _group_row(database, group_id)
    )
    assert resolved["resolved"] == [{"page_id": pid, "page_number": 1}]
    assert resolved["missing"] == []


def test_orphan_page_ids_reported_missing_not_dropped(
    database: Database,
) -> None:
    """Deleted page / foreign-document page must stay visible as missing."""

    document_id = _seed_document(database, "试卷D")
    other_document = _seed_document(database, "别的试卷")
    pid_ok = _seed_page(database, document_id, 1)
    pid_foreign = _seed_page(database, other_document, 9)
    group_id = _insert_group_raw(
        database, document_id, json.dumps([pid_ok, pid_foreign, 999999])
    )
    resolved = resolve_group_source_pages(
        database, _group_row(database, group_id)
    )
    assert resolved["resolved"] == [{"page_id": pid_ok, "page_number": 1}]
    assert sorted(resolved["missing"]) == sorted([pid_foreign, 999999])


def test_broken_or_empty_source_pages_degrade_to_empty(
    database: Database,
) -> None:
    document_id = _seed_document(database, "试卷E")
    for raw in ("not-json", "null", "[]", ""):
        group_id = _insert_group_raw(database, document_id, raw)
        resolved = resolve_group_source_pages(
            database, _group_row(database, group_id)
        )
        assert resolved == {"resolved": [], "missing": []}, raw


def test_draft_group_contract_still_writes_page_ids(
    database: Database,
) -> None:
    """The writer side is unchanged: draft_group(page_ids=...) stores ids."""

    document_id = _seed_document(database, "试卷F")
    pid = _seed_page(database, document_id, 1)
    group_id = draft_group(
        database,
        document_id=document_id,
        group_number="1~2题组",
        page_ids=[pid],
        subquestion_numbers=["1", "2"],
        group_type="choice",
    )
    resolved = resolve_group_source_pages(
        database, _group_row(database, group_id)
    )
    assert resolved["resolved"] == [{"page_id": pid, "page_number": 1}]
