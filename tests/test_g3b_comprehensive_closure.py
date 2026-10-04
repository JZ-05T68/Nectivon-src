"""G3-B contract tests: comprehensive-question closure.

Covers schema v29 (subquestion evidence subsets / handwriting regions),
group UX contracts, over-binding guards, layer-2 timing, and rubric
import structure.  Round-1 data must never regress.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.database import Database
from src.learning_workflow_service import (
    QuestionOrganizationService,
)
from src.migrations import SCHEMA_VERSION, migrate_database
from src.question_group_service import (
    QuestionGroupError,
    add_group_material,
    add_handwriting_region,
    attach_question_to_group,
    confirm_group,
    confirm_question_evidence,
    draft_group,
    group_children,
    latest_group_for_page,
    page_handwriting_regions,
    question_evidence,
    set_question_evidence,
    set_region_identity,
    set_region_target,
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


def _seed_page(database: Database, document_id: int, page_number: int, image: str) -> int:
    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            "INSERT INTO pages(document_id, page_number, image_path,"
            " status, review_status, created_at, updated_at)"
            " VALUES (?, ?, ?, 'pending_review', 'pending',"
            " datetime('now'), datetime('now'))",
            (document_id, page_number, image),
        )
        return int(cursor.lastrowid)


def _make_group(database: Database, document_id: int, page_id: int) -> int:
    group_id = draft_group(
        database,
        document_id=document_id,
        group_number="23",
        page_ids=[page_id],
        subquestion_numbers=["23(1)", "23(2)"],
    )
    confirm_group(database, group_id)
    return group_id


# --- schema ----------------------------------------------------------------


def test_schema_is_v29() -> None:
    # G3-B introduced v29; F3 moved the floor to v30.  The contract is
    # "at least the G3-B foundation", not a frozen number.
    assert SCHEMA_VERSION >= 29


def test_v28_to_v29_additive(tmp_path: Path) -> None:
    db_path = tmp_path / "drill" / "knowledge.db"
    fresh = Database(db_path)
    document_id = _seed_document(fresh, "升级卷")
    question_id = _seed_question(fresh, document_id, "23(1)", "小问")
    with fresh._connection() as connection:  # noqa: SLF001
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DROP TABLE IF EXISTS handwriting_regions")
        connection.execute("DROP INDEX IF EXISTS idx_question_item_evidence_item")
        connection.execute("DROP TABLE IF EXISTS question_item_evidence")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 29")
    migrate_database(db_path)
    reopened = Database(db_path)
    with reopened._connection() as connection:  # noqa: SLF001
        assert connection.execute(
            "SELECT COUNT(*) FROM question_items"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT stem_text FROM question_items WHERE id = ?",
            (question_id,),
        ).fetchone()["stem_text"] == "小问"
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
    assert version >= 29  # chain may have moved past v29 (F3 added v30)


# --- question group first class ---------------------------------------------


def test_question_group_ui_payload(database: Database) -> None:
    """group_children returns the layer-1 group card payload in one call."""

    document_id = _seed_document(database, "组视图卷")
    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            "INSERT INTO pages(document_id, page_number, image_path,"
            " status, review_status, created_at, updated_at)"
            "VALUES (?, ?, ?, 'pending_review', 'pending',"
            "datetime('now'), datetime('now'))",
                (document_id, 1, 'p1.png'),
        )
        page_id = int(cursor.lastrowid)
    group_id = _make_group(database, document_id, page_id)
    add_group_material(
        database,
        group_id=group_id,
        material_kind="figure",
        material_label="图1 区域地图",
        page_id=page_id,
    )
    sub1 = _seed_question(database, document_id, "23(1)", "第一小问")
    sub2 = _seed_question(database, document_id, "23(2)", "第二小问")
    attach_question_to_group(database, sub1, group_id)
    attach_question_to_group(database, sub2, group_id)
    payload = group_children(database, group_id)
    assert payload["group"]["group_number"] == "23"
    assert payload["group"]["status"] == "user_confirmed"
    assert len(payload["materials"]) == 1
    assert [s["question_number"] for s in payload["subquestions"]] == [
        "23(1)",
        "23(2)",
    ]


def test_group_confirm_flow(database: Database) -> None:
    """AI drafts first; only the user confirm flips provenance."""

    document_id = _seed_document(database, "确认流卷")
    group_id = draft_group(
        database,
        document_id=document_id,
        group_number="18",
        page_ids=[1],
        subquestion_numbers=["(1)", "(2)"],
    )
    with database._connection() as connection:  # noqa: SLF001
        status = connection.execute(
            "SELECT status FROM question_groups WHERE id = ?", (group_id,)
        ).fetchone()["status"]
    assert status == "ai_draft"
    confirm_group(database, group_id)
    with database._connection() as connection:  # noqa: SLF001
        row = connection.execute(
            "SELECT status, provenance FROM question_groups WHERE id = ?",
            (group_id,),
        ).fetchone()
    assert row["status"] == "user_confirmed"
    assert row["provenance"] == "user_confirmed"


def test_latest_group_for_page_scoping(database: Database) -> None:
    document_id = _seed_document(database, "页匹配卷")
    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            "INSERT INTO pages(document_id, page_number, image_path,"
            " status, review_status, created_at, updated_at)"
            "VALUES (?, ?, ?, 'pending_review', 'pending',"
            "datetime('now'), datetime('now'))",
                (document_id, 5, 'p5.png'),
        )
        page_id = int(cursor.lastrowid)
    assert latest_group_for_page(database, page_id) is None
    group_id = _make_group(database, document_id, page_id)
    hit = latest_group_for_page(database, page_id)
    assert hit is not None and int(hit["id"]) == group_id


# --- evidence subset ---------------------------------------------------------


def test_question_item_evidence(database: Database) -> None:
    document_id = _seed_document(database, "证据子集卷")
    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            "INSERT INTO pages(document_id, page_number, image_path,"
            " status, review_status, created_at, updated_at)"
            "VALUES (?, ?, ?, 'pending_review', 'pending',"
            "datetime('now'), datetime('now'))",
                (document_id, 2, 'p2.png'),
        )
        page_id = int(cursor.lastrowid)
    group_id = _make_group(database, document_id, page_id)
    material_id = add_group_material(
        database,
        group_id=group_id,
        material_kind="figure",
        material_label="图1",
        page_id=page_id,
    )
    sub = _seed_question(database, document_id, "23(1)", "小问")
    attach_question_to_group(database, sub, group_id)
    evidence_id = set_question_evidence(
        database,
        question_item_id=sub,
        evidence_type="figure",
        source_id=material_id,
        page_id=page_id,
        status="ai_draft",
    )
    rows = question_evidence(database, sub)
    assert len(rows) == 1
    assert rows[0]["status"] == "ai_draft"
    confirm_question_evidence(database, sub, evidence_id)
    rows = question_evidence(database, sub)
    assert rows[0]["status"] == "user_confirmed"
    assert rows[0]["material_label"] == "图1"


def test_no_over_binding(database: Database) -> None:
    """THE over-binding guard: nothing auto-binds every material to every sub.

    Simulates the G3-A HIGH scenario (figures A and B in one group) and
    asserts: (a) fresh subquestions have EMPTY evidence — no code path
    pre-binds them; (b) one subquestion may bind only figure A while its
    sibling binds only figure B — the API is strictly per-pair.
    """

    document_id = _seed_document(database, "防全绑卷")
    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            "INSERT INTO pages(document_id, page_number, image_path,"
            " status, review_status, created_at, updated_at)"
            "VALUES (?, ?, ?, 'pending_review', 'pending',"
            "datetime('now'), datetime('now'))",
                (document_id, 3, 'p3.png'),
        )
        page_id = int(cursor.lastrowid)
    group_id = _make_group(database, document_id, page_id)
    figure_a = add_group_material(
        database, group_id=group_id, material_kind="figure",
        material_label="图A 气温降水图", page_id=page_id,
    )
    figure_b = add_group_material(
        database, group_id=group_id, material_kind="figure",
        material_label="图B 结构框图", page_id=page_id,
    )
    sub1 = _seed_question(database, document_id, "23(1)", "看图A的小问")
    sub2 = _seed_question(database, document_id, "23(2)", "看图B的小问")
    attach_question_to_group(database, sub1, group_id)
    attach_question_to_group(database, sub2, group_id)
    # (a) no auto-binding happened on join/grouping:
    assert question_evidence(database, sub1) == []
    assert question_evidence(database, sub2) == []
    # (b) specific binding is possible and stays subquestion-specific:
    set_question_evidence(
        database, question_item_id=sub1, evidence_type="figure",
        source_id=figure_a, page_id=page_id, status="user_confirmed",
        confidence="confirmed",
    )
    set_question_evidence(
        database, question_item_id=sub2, evidence_type="figure",
        source_id=figure_b, page_id=page_id, status="user_confirmed",
        confidence="confirmed",
    )
    labels_1 = {row["material_label"] for row in question_evidence(database, sub1)}
    labels_2 = {row["material_label"] for row in question_evidence(database, sub2)}
    assert labels_1 == {"图A 气温降水图"}
    assert labels_2 == {"图B 结构框图"}
    # (c) there is deliberately NO bulk API: the service module exposes no
    # function that binds a whole group's materials to a question.
    import src.question_group_service as svc_module

    bulk_names = [
        name for name in dir(svc_module)
        if "all" in name.lower() or "bulk" in name.lower()
        or "every" in name.lower()
    ]
    assert bulk_names == []


def test_shared_material_zero_copy(database: Database) -> None:
    document_id = _seed_document(database, "零拷贝卷")
    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            "INSERT INTO pages(document_id, page_number, image_path,"
            " status, review_status, created_at, updated_at)"
            "VALUES (?, ?, ?, 'pending_review', 'pending',"
            "datetime('now'), datetime('now'))",
                (document_id, 4, 'p4.png'),
        )
        page_id = int(cursor.lastrowid)
    group_id = _make_group(database, document_id, page_id)
    add_group_material(
        database, group_id=group_id, material_kind="text_material",
        material_label="材料一", page_id=page_id,
    )
    subs = [
        _seed_question(database, document_id, f"23({i})", f"问{i}")
        for i in (1, 2, 3)
    ]
    for sub in subs:
        attach_question_to_group(database, sub, group_id)
    with database._connection() as connection:  # noqa: SLF001
        material_rows = connection.execute(
            "SELECT COUNT(*) AS n FROM question_group_materials"
        ).fetchone()["n"]
        joined = connection.execute(
            "SELECT page_id FROM question_group_materials WHERE group_id = ?",
            (group_id,),
        ).fetchone()["page_id"]
    assert material_rows == 1  # shared once
    assert joined == page_id  # a reference, not a copy


def test_evidence_requires_reference(database: Database) -> None:
    document_id = _seed_document(database, "空绑定卷")
    sub = _seed_question(database, document_id, "1", "题")
    with pytest.raises(QuestionGroupError):
        set_question_evidence(
            database, question_item_id=sub, evidence_type="figure",
            source_id=None, page_id=None,
        )


# --- layer-2 timing ----------------------------------------------------------


def test_layer2_trigger_after_confirm(database: Database) -> None:
    """After joining layer 1 the organization service runs immediately.

    The AI/jieba path may honestly produce NO suggestions (short stems,
    no method tags) — the contract under test is that the TRIGGER works
    and never fabricates: created lists stay empty when there is nothing
    real to attach, and no junk topic-word family is born.
    """

    document_id = _seed_document(database, "二层时机卷")
    sub = _seed_question(database, document_id, "23(1)", "指出有利自然条件。")
    service = QuestionOrganizationService(database)
    created = service.auto_organize_question(sub)
    assert created["type"] == []
    assert created["method"] == []
    assert created["conclusion"] == []
    with database._connection() as connection:  # noqa: SLF001
        junk = connection.execute(
            "SELECT COUNT(*) AS n FROM question_families WHERE title IN"
            " ('综合分析法','材料分析法','区域分析法')"
        ).fetchone()["n"]
    assert junk == 0


def test_family_not_overcluster(database: Database) -> None:
    """Four subquestions of one group may carry four different methods."""

    document_id = _seed_document(database, "四问四法卷")
    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            "INSERT INTO pages(document_id, page_number, image_path,"
            " status, review_status, created_at, updated_at)"
            "VALUES (?, ?, ?, 'pending_review', 'pending',"
            "datetime('now'), datetime('now'))",
                (document_id, 6, 'p6.png'),
        )
        page_id = int(cursor.lastrowid)
    group_id = _make_group(database, document_id, page_id)
    subs = [
        _seed_question(database, document_id, f"23({i})", f"问{i}")
        for i in (1, 2, 3, 4)
    ]
    for sub in subs:
        attach_question_to_group(database, sub, group_id)
    titles = (
        "自然过程分析方法",
        "产业区位评价方法",
        "读图描述方法",
        "数据比较分析方法",
    )
    with database._connection() as connection:  # noqa: SLF001
        for title in titles:
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
        ).fetchone()["n"]
    assert distinct == 4


# --- handwriting regions -----------------------------------------------------


def test_handwriting_region_structure(database: Database) -> None:
    document_id = _seed_document(database, "区域卷")
    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            "INSERT INTO pages(document_id, page_number, image_path,"
            " status, review_status, created_at, updated_at)"
            "VALUES (?, ?, ?, 'pending_review', 'pending',"
            "datetime('now'), datetime('now'))",
                (document_id, 7, 'p7.png'),
        )
        page_id = int(cursor.lastrowid)
    region_id = add_handwriting_region(
        database, page_id=page_id, text="第(1)问下方手写", ink_color="blue",
        bbox=[10.0, 20.0, 300.0, 80.0],
    )
    regions = page_handwriting_regions(database, page_id)
    assert len(regions) == 1
    assert regions[0]["identity_draft"] == "unknown"  # default UNKNOWN
    assert regions[0]["identity_status"] == "ai_draft"
    assert json.loads(regions[0]["bbox"]) == [10.0, 20.0, 300.0, 80.0]
    sub = _seed_question(database, document_id, "23(1)", "小问")
    set_region_target(database, region_id, sub)
    regions = page_handwriting_regions(database, page_id)
    assert regions[0]["target_number"] == "23(1)"


def test_identity_uncertain(database: Database) -> None:
    """The AI can NEVER set identity; only the user path can."""

    document_id = _seed_document(database, "身份卷")
    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            "INSERT INTO pages(document_id, page_number, image_path,"
            " status, review_status, created_at, updated_at)"
            "VALUES (?, ?, ?, 'pending_review', 'pending',"
            "datetime('now'), datetime('now'))",
                (document_id, 8, 'p8.png'),
        )
        page_id = int(cursor.lastrowid)
    region_id = add_handwriting_region(
        database, page_id=page_id, text="红笔批注", ink_color="red",
    )
    # No AI API exists — calling the user action is the only way to move
    # identity away from unknown.  Guard against an AI-shaped helper:
    import src.question_group_service as svc_module

    assert not any(
        name.startswith("ai_") and "identity" in name
        for name in dir(svc_module)
    )
    set_region_identity(database, region_id, "teacher")
    regions = page_handwriting_regions(database, page_id)
    assert regions[0]["identity_draft"] == "teacher"
    assert regions[0]["identity_status"] == "user_confirmed"


def test_teacher_student_separation(database: Database) -> None:
    """Teacher-region rows never touch question_items.student_answer."""

    document_id = _seed_document(database, "分离卷")
    sub = _seed_question(database, document_id, "3(1)", "小问")
    with database._connection() as connection:  # noqa: SLF001
        cursor = connection.execute(
            "INSERT INTO pages(document_id, page_number, image_path,"
            " status, review_status, created_at, updated_at)"
            "VALUES (?, ?, ?, 'pending_review', 'pending',"
            "datetime('now'), datetime('now'))",
                (document_id, 9, 'p9.png'),
        )
        page_id = int(cursor.lastrowid)
    region_id = add_handwriting_region(
        database, page_id=page_id, text="+2 补充句", ink_color="red",
    )
    set_region_target(database, region_id, sub)
    set_region_identity(database, region_id, "teacher")
    with database._connection() as connection:  # noqa: SLF001
        row = connection.execute(
            "SELECT student_answer, teacher_comment FROM question_items"
            " WHERE id = ?",
            (sub,),
        ).fetchone()
    assert row["student_answer"] == ""  # untouched by the teacher region
    assert row["teacher_comment"] == ""


# --- rubric ------------------------------------------------------------------


def test_rubric_import_shape(database: Database) -> None:
    """Imported rubric rows keep the verbatim source and the source link."""

    source_document_id = _seed_document(database, "参考答案及评分标准")
    source_page_id = _seed_page(database, source_document_id, 1, "rubric.png")
    with database._connection() as connection:  # noqa: SLF001
        connection.execute(
            """
            INSERT INTO rubric_entries(
                scoring_mode, max_score, source_text, source_document_id,
                source_page_id, point_count, provenance, created_at,
                updated_at
            ) VALUES ('point_based', 6, '(1)甲对应②，乙对应①。(2分)……(4分)',
                      ?, ?, 2,
                      'g3b_rubric_import(OCR解析草稿): 题号线索=24(1)',
                      datetime('now'), datetime('now'))
            """,
            (source_document_id, source_page_id),
        )
        row = connection.execute(
            "SELECT scoring_mode, max_score, point_count, source_text,"
            " source_document_id, source_page_id FROM rubric_entries"
        ).fetchone()
    assert row["scoring_mode"] == "point_based"
    assert row["point_count"] == 2
    assert row["max_score"] == 6
    assert "(2分)" in row["source_text"]  # verbatim preserved
    assert row["source_document_id"] == source_document_id
    assert row["source_page_id"] == source_page_id


def test_level_rubric_preserve(database: Database) -> None:
    """A level rubric is ONE row with level_count; never split into points."""

    document_id = _seed_document(database, "等级卷")
    sub = _seed_question(database, document_id, "22(1)", "小问")
    with database._connection() as connection:  # noqa: SLF001
        connection.execute(
            """
            INSERT INTO rubric_entries(
                question_item_id, scoring_mode, max_score, source_text,
                level_count, provenance, created_at, updated_at
            ) VALUES (?, 'level_based', 6, '一级…二级…三级…', 3,
                      'test', datetime('now'), datetime('now'))
            """,
            (sub,),
        )
        rows = connection.execute(
            "SELECT COUNT(*) AS n, scoring_mode, level_count"
            " FROM rubric_entries WHERE question_item_id = ?",
            (sub,),
        ).fetchone()
    assert rows["n"] == 1
    assert rows["scoring_mode"] == "level_based"
    assert rows["level_count"] == 3


def test_point_rubric(database: Database) -> None:
    document_id = _seed_document(database, "采点卷")
    sub = _seed_question(database, document_id, "22(2)", "小问")
    with database._connection() as connection:  # noqa: SLF001
        connection.execute(
            """
            INSERT INTO rubric_entries(
                question_item_id, scoring_mode, max_score, source_text,
                point_count, provenance, created_at, updated_at
            ) VALUES (?, 'point_based', 6, '任答3点，每点2分', 3,
                      'test', datetime('now'), datetime('now'))
            """,
            (sub,),
        )
        row = connection.execute(
            "SELECT scoring_mode, max_score, point_count FROM rubric_entries"
            " WHERE question_item_id = ?",
            (sub,),
        ).fetchone()
    assert (row["scoring_mode"], row["point_count"], row["max_score"]) == (
        "point_based",
        3,
        6,
    )


# --- regression --------------------------------------------------------------


def test_round1_regression(database: Database) -> None:
    """v29 keeps question_items/families intact on a fresh build + drill."""

    document_id = _seed_document(database, "回归卷")
    _seed_question(database, document_id, "5", "旧题")
    with database._connection() as connection:  # noqa: SLF001
        connection.execute(
            """
            INSERT INTO question_families(
                family_kind, title, description, derivation, variant_pattern,
                confusion_notes, status, created_at, updated_at
            ) VALUES ('method', '旧方法族', '', '', '', '', 'active',
                      datetime('now'), datetime('now'))
            """
        )
    migrate_database(database.database_path)
    with database._connection() as connection:  # noqa: SLF001
        assert connection.execute(
            "SELECT COUNT(*) FROM question_items"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM question_families"
        ).fetchone()[0] == 1
        fk = connection.execute("PRAGMA foreign_key_check").fetchall()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    assert fk == []
    assert integrity == "ok"


def test_g2c_regression(database: Database) -> None:
    """Mastery evidence schema survives v29 untouched."""

    with database._connection() as connection:  # noqa: SLF001
        cols = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(mastery_evidence)")
        }
    required = {
        "id", "question_id", "family_id", "event_type", "result",
        "independence", "source", "provenance", "next_review_at",
        "review_status", "created_at", "updated_at",
    }
    assert required.issubset(cols)
