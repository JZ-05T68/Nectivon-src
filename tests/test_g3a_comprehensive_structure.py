"""G3-A contract tests: comprehensive-question structure foundation.

Covers schema v28 (question groups / shared materials / document relations /
rubric skeleton) with zero Round-1 regression.  Named per the G3-A task
specification (§78), engineering naming convention.

Connection discipline: every helper opens its own short ``Database``
connection; tests never nest two write connections on one SQLite file.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.database import Database
from src.document_relation_service import (
    DocumentRelationError,
    confirm_document_relation,
    list_document_relations,
    reject_document_relation,
    suggest_document_relation,
)
from src.migrations import SCHEMA_VERSION, migrate_database


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


def _seed_question(
    database: Database,
    document_id: int,
    number: str,
    stem: str,
) -> int:
    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            """
            INSERT INTO question_items(
                document_id, question_number, question_kind, stem_text,
                search_stem_text, stem_confidence, created_at, updated_at
            ) VALUES (?, ?, 'typical', ?, ?, 'confirmed',
                      datetime('now'), datetime('now'))
            """,
            (document_id, number, stem, stem),
        )
        return int(cursor.lastrowid)


def _insert_group(
    connection,  # noqa: ANN001 - raw sqlite3 connection inside an open block
    document_id: int,
    group_number: str,
    *,
    theme: str = "",
    status: str = "ai_draft",
    provenance: str = "ai_draft",
    source_pages: str = "[]",
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO question_groups(
            document_id, group_number, group_theme, status, provenance,
            source_pages, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (document_id, group_number, theme, status, provenance, source_pages),
    )
    return int(cursor.lastrowid)


# --- schema foundation -----------------------------------------------------


def test_schema_is_v28() -> None:
    # G3-A introduced v28; G3-B moved the floor to v29.  The contract is
    # "at least the G3-A foundation", not a frozen number.
    assert SCHEMA_VERSION >= 28


def test_question_group_tables_exist(database: Database) -> None:
    with database._connection() as connection:  # noqa: SLF001
        names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "question_groups",
        "question_group_materials",
        "document_relations",
        "rubric_entries",
    }.issubset(names)


def test_v27_to_v28_migration_preserves_round1(tmp_path: Path) -> None:
    """Downgrade a seeded DB to a v27 shape, re-migrate, verify no loss."""

    db_path = tmp_path / "fresh" / "data" / "database" / "knowledge.db"
    fresh = Database(db_path)
    document_id = _seed_document(fresh, "回归卷")
    question_id = _seed_question(fresh, document_id, "23", "综合题题干")
    with fresh._connection() as connection:  # noqa: SLF001
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            INSERT INTO question_families(
                family_kind, title, description, derivation, variant_pattern,
                confusion_notes, status, created_at, updated_at
            ) VALUES ('method', '农业区位分析方法', '', '', '', '', 'active',
                      datetime('now'), datetime('now'))
            """
        )
        family_id = connection.execute(
            "SELECT id FROM question_families"
        ).fetchone()["id"]
        connection.execute(
            """
            INSERT INTO question_family_members(
                family_id, question_id, relation, created_at, provenance
            ) VALUES (?, ?, 'member', datetime('now'), 'test')
            """,
            (family_id, question_id),
        )
    # Simulate the v27 state: drop the v28/v29 additions.
    with fresh._connection() as connection:  # noqa: SLF001
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DROP TABLE IF EXISTS rubric_entries")
        connection.execute("DROP TABLE IF EXISTS document_relations")
        connection.execute("DROP TABLE IF EXISTS question_group_materials")
        connection.execute("DROP TABLE IF EXISTS question_groups")
        connection.execute("DROP TABLE IF EXISTS handwriting_regions")
        connection.execute("DROP INDEX IF EXISTS idx_question_items_group")
        connection.execute("ALTER TABLE question_items DROP COLUMN group_id")
        connection.execute(
            "DELETE FROM schema_migrations WHERE version >= 28"
        )
    # Re-run the migration chain: v27 -> v28 must be purely additive.
    migrate_database(db_path)
    reopened = Database(db_path)
    with reopened._connection() as connection:  # noqa: SLF001
        assert connection.execute(
            "SELECT COUNT(*) FROM question_items"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM question_families"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM question_family_members"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT stem_text FROM question_items WHERE id = ?",
            (question_id,),
        ).fetchone()["stem_text"] == "综合题题干"
        grouped = connection.execute(
            "SELECT COUNT(*) FROM question_items WHERE group_id IS NOT NULL"
        ).fetchone()[0]
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
    assert grouped == 0  # Round-1 rows stay standalone
    # The chain migrates PAST v28 (v29+ now); the guarantee is that the
    # upgrade is additive and Round-1 data survives every step.
    assert version >= 28


# --- question group / subquestion / shared material ------------------------


def test_question_group_structure(database: Database) -> None:
    document_id = _seed_document(database, "2023江苏高考地理")
    _seed_question(database, document_id, "23(1)", "小问1")
    _seed_question(database, document_id, "23(2)", "小问2")
    with database._connection() as connection:  # noqa: SLF001
        group_id = _insert_group(connection, document_id, "23")
        connection.execute(
            "UPDATE question_items SET group_id = ? WHERE question_number IN"
            " ('23(1)', '23(2)') AND document_id = ?",
            (group_id, document_id),
        )
        count = connection.execute(
            "SELECT COUNT(*) AS n FROM question_groups WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        grouped = connection.execute(
            "SELECT COUNT(*) AS n FROM question_items WHERE group_id = ?",
            (group_id,),
        ).fetchone()
    assert group_id > 0
    assert count["n"] == 1
    assert grouped["n"] == 2


def test_subquestion_parent_group(database: Database) -> None:
    document_id = _seed_document(database, "题组测试卷")
    sub1 = _seed_question(database, document_id, "18(1)", "第一小问")
    sub2 = _seed_question(database, document_id, "18(2)", "第二小问")
    with database._connection() as connection:  # noqa: SLF001
        group_id = _insert_group(connection, document_id, "18")
        connection.execute(
            "UPDATE question_items SET group_id = ? WHERE id IN (?, ?)",
            (group_id, sub1, sub2),
        )
        rows = connection.execute(
            "SELECT question_number FROM question_items WHERE group_id = ?"
            " ORDER BY question_number",
            (group_id,),
        ).fetchall()
    assert [row["question_number"] for row in rows] == ["18(1)", "18(2)"]


def test_shared_material_zero_copy(database: Database) -> None:
    """Shared materials reference the original page; never copied per sub."""

    document_id = _seed_document(database, "材料零拷贝卷")
    page = database.create_page(
        document_id=document_id,
        page_number=5,
        image_path="pages/p5.png",
        extracted_text="材料一……",
    )
    page_id = int(page.id)
    with database._connection() as connection:  # noqa: SLF001
        group_id = _insert_group(
            connection,
            document_id,
            "20",
            status="user_confirmed",
            provenance="user_confirmed",
            source_pages="[5]",
        )
        connection.execute(
            """
            INSERT INTO question_group_materials(
                group_id, material_kind, material_label, content_text,
                page_id, created_at
            ) VALUES (?, 'text_material', '材料一', '', ?, datetime('now'))
            """,
            (group_id, page_id),
        )
        shared_rows = connection.execute(
            "SELECT COUNT(*) AS n FROM question_group_materials"
            " WHERE group_id = ?",
            (group_id,),
        ).fetchone()
    subs = [
        _seed_question(database, document_id, f"20({i})", f"问{i}")
        for i in (1, 2, 3)
    ]
    with database._connection() as connection:  # noqa: SLF001
        connection.execute(
            "UPDATE question_items SET group_id = ? WHERE id IN (?, ?, ?)",
            (group_id, *subs),
        )
        copies = connection.execute(
            "SELECT COUNT(*) AS n FROM question_group_materials"
        ).fetchone()
        linked_page = connection.execute(
            "SELECT page_id FROM question_group_materials WHERE group_id = ?",
            (group_id,),
        ).fetchone()
    assert shared_rows["n"] == 1  # one shared row
    assert copies["n"] == 1  # zero-copy: no duplication per subquestion
    assert linked_page["page_id"] == page_id  # references the original page


def test_subquestion_evidence_subset(database: Database) -> None:
    """Group-level shared materials exist; per-sub binding needs v29.

    Honest v28 state: ``question_item_evidence`` has no material reference,
    so subquestion↔material subsets are NOT yet first-class.  This test
    pins what exists today so the gap cannot silently regress.
    """

    document_id = _seed_document(database, "证据子集卷")
    with database._connection() as connection:  # noqa: SLF001
        group_id = _insert_group(connection, document_id, "21")
        for kind, label in (("text_material", "材料一"), ("figure", "图1")):
            connection.execute(
                """
                INSERT INTO question_group_materials(
                    group_id, material_kind, material_label, page_id,
                    created_at
                ) VALUES (?, ?, ?, NULL, datetime('now'))
                """,
                (group_id, kind, label),
            )
        materials = connection.execute(
            "SELECT material_kind FROM question_group_materials"
            " WHERE group_id = ? ORDER BY material_kind",
            (group_id,),
        ).fetchall()
        evidence_cols = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(question_item_evidence)"
            )
        }
    assert [m["material_kind"] for m in materials] == [
        "figure",
        "text_material",
    ]
    assert "material_id" not in evidence_cols  # documented gap


# --- document relations ------------------------------------------------------


def test_document_relation(database: Database) -> None:
    primary = _seed_document(database, "24南京盐城一模地理")
    answer = _seed_document(database, "24南京盐城一模地理答案")
    scoring = _seed_document(database, "24南京盐城一模地理参考答案及评分标准")
    rel1 = suggest_document_relation(
        database,
        primary_document_id=primary,
        related_document_id=answer,
        relation_kind="reference_answer",
    )
    suggest_document_relation(
        database,
        primary_document_id=primary,
        related_document_id=scoring,
        relation_kind="scoring_standard",
    )
    with database._connection() as connection:  # noqa: SLF001
        status1 = connection.execute(
            "SELECT status FROM document_relations WHERE id = ?", (rel1,)
        ).fetchone()["status"]
        total = connection.execute(
            "SELECT COUNT(*) AS n FROM document_relations WHERE"
            " primary_document_id = ?",
            (primary,),
        ).fetchone()
    assert status1 == "ai_suggested"  # AI may only suggest
    assert total["n"] == 2


def test_reference_answer_not_truth(database: Database) -> None:
    """Confirming a relation must not overwrite student answers."""

    primary = _seed_document(database, "试卷")
    answer = _seed_document(database, "参考答案")
    _seed_question(database, primary, "1", "题干")
    with database._connection() as connection:  # noqa: SLF001
        connection.execute(
            "UPDATE question_items SET student_answer = '我的手写答案'"
            " WHERE document_id = ?",
            (primary,),
        )
    rel = suggest_document_relation(
        database,
        primary_document_id=primary,
        related_document_id=answer,
        relation_kind="reference_answer",
    )
    confirm_document_relation(database, rel)
    with database._connection() as connection:  # noqa: SLF001
        student_answer = connection.execute(
            "SELECT student_answer FROM question_items WHERE document_id = ?",
            (primary,),
        ).fetchone()["student_answer"]
        status = connection.execute(
            "SELECT status FROM document_relations WHERE id = ?", (rel,)
        ).fetchone()["status"]
    assert student_answer == "我的手写答案"  # untouched by the relation
    assert status == "confirmed"


def test_restart_document_relations(database: Database) -> None:
    """Confirmed relations persist across a full service restart cycle."""

    primary = _seed_document(database, "试卷R")
    answer = _seed_document(database, "答案R")
    rel = suggest_document_relation(
        database,
        primary_document_id=primary,
        related_document_id=answer,
        relation_kind="reference_answer",
    )
    confirm_document_relation(database, rel)
    reopened = Database(database.database_path)
    relations = list_document_relations(reopened, primary)
    assert len(relations) == 1
    assert relations[0]["status"] == "confirmed"


def test_source_conflict_warning(database: Database) -> None:
    """Two answer documents both stay attached; the system never auto-picks."""

    primary = _seed_document(database, "冲突卷")
    answer_a = _seed_document(database, "答案A")
    answer_b = _seed_document(database, "答案B")
    rel_a = suggest_document_relation(
        database,
        primary_document_id=primary,
        related_document_id=answer_a,
        relation_kind="reference_answer",
    )
    rel_b = suggest_document_relation(
        database,
        primary_document_id=primary,
        related_document_id=answer_b,
        relation_kind="reference_answer",
    )
    confirm_document_relation(database, rel_a)
    confirm_document_relation(database, rel_b)
    relations = list_document_relations(database, primary)
    assert len(relations) == 2  # both sources preserved, no silent merge
    assert {r["status"] for r in relations} == {"confirmed"}


def test_document_relation_reject(database: Database) -> None:
    primary = _seed_document(database, "拒绝卷主")
    other = _seed_document(database, "无关文档")
    rel = suggest_document_relation(
        database,
        primary_document_id=primary,
        related_document_id=other,
        relation_kind="other",
    )
    reject_document_relation(database, rel)
    assert list_document_relations(database, primary) == []


def test_document_relation_self_forbidden(database: Database) -> None:
    doc = _seed_document(database, "自关联")
    with pytest.raises(DocumentRelationError):
        suggest_document_relation(
            database,
            primary_document_id=doc,
            related_document_id=doc,
            relation_kind="reference_answer",
        )


# --- rubric skeleton ---------------------------------------------------------


def test_level_rubric_not_point_list(database: Database) -> None:
    """A level-based rubric is stored as ONE entry, never split into points."""

    document_id = _seed_document(database, "等级评分卷")
    sub = _seed_question(database, document_id, "22(1)", "小问")
    with database._connection() as connection:  # noqa: SLF001
        connection.execute(
            """
            INSERT INTO rubric_entries(
                question_item_id, scoring_mode, max_score, source_text,
                point_count, level_count, provenance, created_at, updated_at
            ) VALUES (?, 'level_based', 6,
                      '一级：……(4分) 二级：……(2分) 三级：……(1分)', NULL, 3,
                      'ai_draft', datetime('now'), datetime('now'))
            """,
            (sub,),
        )
        row = connection.execute(
            "SELECT COUNT(*) AS n, scoring_mode FROM rubric_entries"
            " WHERE question_item_id = ? GROUP BY scoring_mode",
            (sub,),
        ).fetchone()
    assert row["n"] == 1
    assert row["scoring_mode"] == "level_based"


def test_point_rubric_structure(database: Database) -> None:
    document_id = _seed_document(database, "采点评分卷")
    sub = _seed_question(database, document_id, "22(2)", "小问")
    with database._connection() as connection:  # noqa: SLF001
        connection.execute(
            """
            INSERT INTO rubric_entries(
                question_item_id, scoring_mode, max_score, source_text,
                point_count, level_count, provenance, created_at, updated_at
            ) VALUES (?, 'point_based', 6, '任答3点，每点2分，共6分', 3, NULL,
                      'ai_draft', datetime('now'), datetime('now'))
            """,
            (sub,),
        )
        row = connection.execute(
            "SELECT scoring_mode, max_score, point_count FROM rubric_entries"
            " WHERE question_item_id = ?",
            (sub,),
        ).fetchone()
    assert row["scoring_mode"] == "point_based"
    assert row["point_count"] == 3
    assert row["max_score"] == 6


# --- family discipline -------------------------------------------------------


def test_group_theme_not_method(database: Database) -> None:
    """group_theme lives on the group; it must never become a family."""

    document_id = _seed_document(database, "主题与方法卷")
    with database._connection() as connection:  # noqa: SLF001
        _insert_group(
            connection,
            document_id,
            "19",
            theme="某区域产业发展",
            status="user_confirmed",
            provenance="user_confirmed",
        )
        stored = connection.execute(
            "SELECT group_theme FROM question_groups WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        leak = connection.execute(
            "SELECT COUNT(*) AS n FROM question_families WHERE title = ?",
            ("某区域产业发展",),
        ).fetchone()
    assert stored["group_theme"] == "某区域产业发展"
    assert leak["n"] == 0


def test_subquestions_multiple_methods(database: Database) -> None:
    """Subquestions of one group may belong to different method families."""

    document_id = _seed_document(database, "一题多法卷")
    subs = [
        _seed_question(database, document_id, f"17({i})", f"问{i}")
        for i in (1, 2, 3)
    ]
    with database._connection() as connection:  # noqa: SLF001
        group_id = _insert_group(
            connection,
            document_id,
            "17",
            theme="区域自然过程",
            status="user_confirmed",
            provenance="user_confirmed",
        )
        connection.execute(
            "UPDATE question_items SET group_id = ? WHERE id IN (?, ?, ?)",
            (group_id, *subs),
        )
        for title in ("自然过程分析方法", "产业区位评价方法", "读图描述方法"):
            connection.execute(
                """
                INSERT INTO question_families(
                    family_kind, title, description, derivation,
                    variant_pattern, confusion_notes, status,
                    created_at, updated_at
                ) VALUES ('method', ?, '', '', '', '', 'active',
                          datetime('now'), datetime('now'))
                """,
                (title,),
            )
        family_ids = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM question_families ORDER BY id"
            ).fetchall()
        ]
        for sub, family_id in zip(subs, family_ids, strict=True):
            connection.execute(
                """
                INSERT INTO question_family_members(
                    family_id, question_id, relation, created_at, provenance
                ) VALUES (?, ?, 'member', datetime('now'), 'test')
                """,
                (family_id, sub),
            )
        distinct = connection.execute(
            """
            SELECT COUNT(DISTINCT qfm.family_id) AS n
            FROM question_family_members qfm
            JOIN question_items qi ON qi.id = qfm.question_id
            WHERE qi.group_id = ?
            """,
            (group_id,),
        ).fetchone()
    assert distinct["n"] == 3  # no over-cluster


# --- handwriting provenance --------------------------------------------------


def test_student_handwriting_provenance(database: Database) -> None:
    """Handwriting drafts stay AI drafts until confirmed; nothing merges."""

    document_id = _seed_document(database, "手写卷")
    page = database.create_page(
        document_id=document_id,
        page_number=2,
        image_path="pages/p2.png",
        extracted_text="",
    )
    with database._connection() as connection:  # noqa: SLF001
        connection.execute(
            """
            INSERT INTO page_visual_interpretations(
                page_id, provenance, content, region_json, confidence,
                origin, status, created_at, updated_at, user_edited,
                original_ai_content
            ) VALUES (?, 'HANDWRITING_VISION', '红色手写批注……',
                      '{"handwriting_presence": "confirmed"}', 'uncertain',
                      'ai_vision', 'draft', datetime('now'), datetime('now'),
                      0, '')
            """,
            (page.id,),
        )
        row = connection.execute(
            "SELECT status, provenance, user_edited"
            " FROM page_visual_interpretations"
        ).fetchone()
    assert row["status"] == "draft"
    assert row["provenance"] == "HANDWRITING_VISION"
    assert row["user_edited"] == 0


def test_teacher_annotation_not_student_answer(database: Database) -> None:
    """Teacher annotation fields are separate; never merged into the answer."""

    document_id = _seed_document(database, "批注卷")
    sub = _seed_question(database, document_id, "3(1)", "小问")
    with database._connection() as connection:  # noqa: SLF001
        row = connection.execute(
            "SELECT student_answer, teacher_comment FROM question_items"
            " WHERE id = ?",
            (sub,),
        ).fetchone()
        cols = {
            r["name"]
            for r in connection.execute("PRAGMA table_info(question_items)")
        }
    assert row["student_answer"] == ""
    assert row["teacher_comment"] == ""
    assert {"student_answer", "teacher_comment", "teacher_verdict"}.issubset(
        cols
    )
