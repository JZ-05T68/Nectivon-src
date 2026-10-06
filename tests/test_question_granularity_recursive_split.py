"""Focused regression for recursive question granularity (v0.8.6)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.database import Database
from src.learning_workflow_service import QuestionService
from src.migrations import SCHEMA_VERSION
from src.question_candidate_service import (
    QuestionCandidate,
    QuestionCandidateError,
    QuestionCandidateStore,
    build_extraction_prompt,
    iter_atomic_leaves,
    parse_candidates_payload,
)
from src.question_structure_service import (
    QuestionStructureError,
    _labelled_parent_prompt,
    _shared_parent_prompt,
    context_for_question_item,
    link_atomic_question,
    materialize_candidate_tree,
    tree_overview,
)


def test_parent_common_context_excludes_child_formulas_and_ocr_garbage() -> None:
    raw_parent = (
        "（16分）计算：\n"
        "(1)(-5)+(-4)-(+101)-(-9)；\n"
        "(2)-1^2021×[4-(-3)^2]+3÷(-3/4)；\n"
        "(3)(5/12-7/9+2/3)÷1/36；\n"
        "(4)-3 1/6×7……\n2龄：0："
    )

    assert _shared_parent_prompt(raw_parent) == "（16分）计算："


@pytest.mark.parametrize(
    "prompt",
    (
        "23.（8分）（1）若(x-1)²+|y+2|=0，求值。\n(2)已知另一条件。",
        "23（8分）(1)若x=1，求值。\n(2)若y=2，求值。",
        "（8分）(1)若x=1，求值。\n(2)若y=2，求值。",
        "(1)独立小问一。\n(2)独立小问二。",
        "23.（8分）",
    ),
)
def test_independent_subquestions_have_no_shared_parent_prompt(prompt: str) -> None:
    assert _shared_parent_prompt(prompt) == ""


def test_inline_first_child_preserves_real_shared_conditions() -> None:
    assert _shared_parent_prompt("已知a、b互为相反数：(1)求a+b。(2)求积。") == (
        "已知a、b互为相反数："
    )


@pytest.mark.parametrize(
    ("label", "prompt", "expected"),
    (
        ("24", "24.（10分）请借助数轴探索。", "24\n（10分）请借助数轴探索。"),
        ("25", "（8分）已知数轴上三点。", "25\n（8分）已知数轴上三点。"),
    ),
)
def test_parent_context_prefixes_question_label_exactly_once(
    label: str, prompt: str, expected: str
) -> None:
    assert _labelled_parent_prompt(label, prompt) == expected


@pytest.fixture()
def source(database: Database, tmp_path: Path) -> tuple[int, int, int]:
    digest = hashlib.sha256(b"granularity").hexdigest()
    document = database.create_document(
        title="南京盐城模拟卷",
        filename="paper.pdf",
        source_path=tmp_path / "paper.pdf",
        sha256=digest,
    )
    first = database.create_page(
        document_id=document.id,
        page_number=1,
        image_path=tmp_path / "p1.png",
        status="ready",
    )
    second = database.create_page(
        document_id=document.id,
        page_number=2,
        image_path=tmp_path / "p2.png",
        status="ready",
    )
    return int(document.id), int(first.id), int(second.id)


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    return Database(tmp_path / "data" / "database" / "knowledge.db")


def _atomic(label: str, prompt: str, **overrides) -> QuestionCandidate:
    values = {
        "number": label,
        "stem": prompt,
        "completeness": "complete",
        "visual_dependency": "none",
        "split_source": "explicit_numbering",
        "split_confidence": "high",
    }
    values.update(overrides)
    return QuestionCandidate(**values)


def test_saved_leaf_does_not_inherit_sibling_from_inline_parent(
    database: Database, source: tuple[int, int, int]
) -> None:
    document_id, page_id, _ = source
    original_parent = "23.（8分）（1）若x=1。\n(2)若y=2。\n(3)若a=3。"
    root = QuestionCandidate(
        number="23", stem=original_parent, completeness="complete",
        question_kind="composite", children=[
            _atomic("23(1)", "若x=1。"),
            _atomic("23(2)", "若y=2。"),
            _atomic("23(3)", "若a=3。"),
        ],
    )
    mapping = materialize_candidate_tree(
        database, document_id=document_id, source_page_id=page_id, root_candidate=root
    )
    question = QuestionService(database).create_question_item(
        document_id=document_id, page_id=page_id, question_kind="error",
        question_number="23(3)", stem_text="若a=3。",
    )
    link_atomic_question(database, node_id=mapping["0.2"], question_item_id=question.id)
    context = context_for_question_item(database, question.id)
    assert context is not None
    assert context.context_text == ""
    assert context.page_refs == (page_id,)
    with database._connection() as connection:  # noqa: SLF001
        assert connection.execute(
            "SELECT local_prompt FROM question_nodes WHERE id=?", (mapping["0"],)
        ).fetchone()[0] == original_parent


def _chemistry_tree(page_id: int) -> QuestionCandidate:
    nested = QuestionCandidate(
        number="16(2)",
        stem="根据流程图完成各小问。",
        completeness="complete",
        question_kind="composite",
        children=[
            _atomic("16(2)①", "写出反应方程式并说明理由。"),
            _atomic("16(2)②", "计算转化率。"),
        ],
        page_refs=[page_id],
        image_refs=["合成路线图"],
        split_source="explicit_numbering",
        split_confidence="high",
    )
    return QuestionCandidate(
        number="16",
        stem="A→B→C→D 的有机合成路线如图。",
        completeness="complete",
        question_kind="composite",
        children=[
            _atomic("16(1)", "写出 B 的结构简式。"),
            nested,
            _atomic("16(3)", "求该反应的平衡常数。"),
        ],
        page_refs=[page_id],
        image_refs=["合成路线图"],
        split_source="explicit_numbering",
        split_confidence="high",
    )


def test_schema_v31_has_recursive_question_nodes(database: Database) -> None:
    assert SCHEMA_VERSION >= 31
    with database._connection() as connection:  # noqa: SLF001
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(question_nodes)")
        }
    assert {
        "parent_question_id", "root_question_id", "question_label",
        "question_level", "question_kind", "shared_context_refs",
        "page_refs", "image_refs", "answer_refs", "is_leaf",
        "split_source", "split_confidence",
    } <= columns


def test_atomic_candidate_stays_one_leaf() -> None:
    raw = json.dumps(
        {"candidates": [{
            "number": "9", "stem": "为什么选B？", "completeness": "complete",
            "question_kind": "atomic", "children": [],
            "split_source": "explicit_numbering", "split_confidence": "high",
        }]},
        ensure_ascii=False,
    )
    roots = parse_candidates_payload(raw)
    leaves = list(iter_atomic_leaves(roots))
    assert len(roots) == len(leaves) == 1
    assert roots[0].question_kind == "atomic"


@pytest.mark.parametrize(
    ("number", "stem"),
    [("9", "为什么选B？"), ("12", "该反应的平衡常数为____。")],
)
def test_single_choice_and_short_blank_stay_atomic(number: str, stem: str) -> None:
    raw = json.dumps(
        {"candidates": [{
            "number": number, "stem": stem, "completeness": "complete",
            "question_kind": "atomic", "children": [],
            "split_source": "explicit_numbering", "split_confidence": "high",
        }]},
        ensure_ascii=False,
    )
    candidate = parse_candidates_payload(raw)[0]
    assert candidate.is_leaf


def test_one_level_composite_splits_only_to_explicit_children() -> None:
    root = QuestionCandidate(
        number="16",
        stem="共享题干",
        completeness="complete",
        question_kind="composite",
        children=[_atomic("16(1)", "求速度并说明方向"), _atomic("16(2)", "求冲量")],
        split_source="explicit_numbering",
        split_confidence="high",
    )
    assert [leaf.number for _, leaf, _, _ in iter_atomic_leaves([root])] == [
        "16(1)", "16(2)"
    ]


def test_bare_subquestion_numbers_are_qualified_by_parent(tmp_path: Path) -> None:
    store = QuestionCandidateStore(tmp_path)
    store.save_page_candidates(
        1,
        [
            QuestionCandidate(
                number="20",
                stem="计算：",
                completeness="complete",
                question_kind="composite",
                children=[
                    _atomic("(1)", "第一小题"),
                    _atomic("（2）", "第二小题"),
                    _atomic("20(3)", "第三小题"),
                    _atomic("20(4)", "第四小题"),
                ],
            )
        ],
    )

    loaded = store.page_candidates(1)

    assert loaded is not None
    assert [child.number for child in loaded[0].children] == [
        "20(1)",
        "20(2)",
        "20(3)",
        "20(4)",
    ]


def test_composite_two_level_recursive_split() -> None:
    raw = json.dumps(
        {"candidates": [{
            "number": "16", "stem": "共享材料", "completeness": "complete",
            "question_kind": "composite", "split_source": "explicit_numbering",
            "split_confidence": "high", "children": [
                {"number": "16(1)", "stem": "小问一", "completeness": "complete"},
                {"number": "16(2)", "stem": "小问二共享条件",
                 "completeness": "complete", "question_kind": "composite",
                 "children": [
                     {"number": "16(2)①", "stem": "小问二之一", "completeness": "complete"},
                     {"number": "16(2)②", "stem": "小问二之二", "completeness": "complete"},
                 ]},
            ],
        }]},
        ensure_ascii=False,
    )
    roots = parse_candidates_payload(raw)
    leaves = list(iter_atomic_leaves(roots))
    assert [entry[1].number for entry in leaves] == ["16(1)", "16(2)①", "16(2)②"]
    assert [entry[2] for entry in leaves] == ["0.0", "0.1.0", "0.1.1"]


def test_recursive_candidate_store_roundtrip(tmp_path: Path) -> None:
    tree = _chemistry_tree(17)
    store = QuestionCandidateStore(tmp_path / "candidates")
    store.save_page_candidates(17, [tree])
    loaded = store.page_candidates(17)
    assert loaded is not None
    assert loaded[0].children[1].children[0].number == "16(2)①"
    assert loaded[0].image_refs == ["合成路线图"]


def test_manual_split_and_merge_keep_structure_correctable(tmp_path: Path) -> None:
    store = QuestionCandidateStore(tmp_path / "candidates")
    store.save_page_candidates(
        17,
        [_atomic("8", "共享实验材料"), _atomic("9", "另一道题")],
    )

    split = store.split_leaf(
        17,
        "0",
        [
            _atomic("8(1)", "说明现象"),
            _atomic("8(2)", "写出方程式并说明理由"),
        ],
    )
    assert split[0].question_kind == "composite"
    assert split[0].split_source == "manual"
    assert [entry[1].number for entry in iter_atomic_leaves([split[0]])] == [
        "8(1)", "8(2)"
    ]

    merged = store.merge_subtree(17, "0", merged_stem="写出方程式并说明理由")
    assert merged[0].is_leaf
    assert merged[0].stem == "写出方程式并说明理由"
    assert merged[0].split_source == "manual"


def test_manual_structure_correction_refuses_joined_leaf(tmp_path: Path) -> None:
    store = QuestionCandidateStore(tmp_path / "candidates")
    tree = _chemistry_tree(17)
    tree.children[0].status = "added"
    store.save_page_candidates(17, [tree])
    with pytest.raises(QuestionCandidateError, match="已加入"):
        store.merge_subtree(17, "0")
    with pytest.raises(QuestionCandidateError, match="已加入"):
        store.split_leaf(
            17,
            "0.0",
            [_atomic("16(1)①", "拆分后的子题")],
        )


def test_parent_root_leaf_and_shared_context_inheritance(
    database: Database, source: tuple[int, int, int]
) -> None:
    document_id, page_id, _ = source
    tree = _chemistry_tree(page_id)
    mapping = materialize_candidate_tree(
        database,
        document_id=document_id,
        source_page_id=page_id,
        root_candidate=tree,
    )
    with database._connection() as connection:  # noqa: SLF001
        rows = connection.execute(
            "SELECT * FROM question_nodes ORDER BY question_level, id"
        ).fetchall()
    assert len(rows) == 6
    root = next(row for row in rows if row["node_path"] == "0")
    nested = next(row for row in rows if row["node_path"] == "0.1")
    leaf = next(row for row in rows if row["node_path"] == "0.1.0")
    assert root["root_question_id"] == root["id"]
    assert nested["parent_question_id"] == root["id"]
    assert leaf["parent_question_id"] == nested["id"]
    assert leaf["root_question_id"] == root["id"]
    assert leaf["is_leaf"] == 1 and root["is_leaf"] == 0

    question = QuestionService(database).create_question_item(
        document_id=document_id,
        page_id=page_id,
        question_kind="typical",
        question_number="16(2)①",
        stem_text="写出反应方程式并说明理由。",
    )
    link_atomic_question(
        database,
        node_id=mapping["0.1.0"],
        question_item_id=question.id,
    )
    context = context_for_question_item(database, question.id)
    assert context is not None
    assert "A→B→C→D" in context.context_text
    assert "根据流程图" in context.context_text
    assert context.page_refs == (page_id,)
    assert context.image_refs == ("合成路线图",)
    assert QuestionService(database).get_question_item(question.id).shared_context


def test_composite_parent_cannot_be_learning_item(
    database: Database, source: tuple[int, int, int]
) -> None:
    document_id, page_id, _ = source
    tree = _chemistry_tree(page_id)
    mapping = materialize_candidate_tree(
        database,
        document_id=document_id,
        source_page_id=page_id,
        root_candidate=tree,
    )
    question = QuestionService(database).create_question_item(
        document_id=document_id, page_id=page_id, question_kind="typical"
    )
    with pytest.raises(QuestionStructureError, match="atomic"):
        link_atomic_question(database, node_id=mapping["0"], question_item_id=question.id)


def test_cross_page_refs_and_local_image_stay_distinct(
    database: Database, source: tuple[int, int, int]
) -> None:
    document_id, first, second = source
    tree = QuestionCandidate(
        number="17",
        stem="题干跨页延续",
        completeness="complete",
        question_kind="composite",
        page_refs=[first, second],
        image_refs=["公共实验装置"],
        answer_refs=["答案页 8", "评分页 9"],
        children=[
            _atomic("17(1)", "解释现象"),
            _atomic("17(2)", "根据局部图计算", image_refs=["局部坐标图"]),
        ],
        split_source="layout",
        split_confidence="medium",
    )
    mapping = materialize_candidate_tree(
        database,
        document_id=document_id,
        source_page_id=first,
        root_candidate=tree,
    )
    question = QuestionService(database).create_question_item(
        document_id=document_id, page_id=first, question_kind="typical",
        question_number="17(2)", stem_text="根据局部图计算",
    )
    link_atomic_question(database, node_id=mapping["0.1"], question_item_id=question.id)
    context = context_for_question_item(database, question.id)
    assert context is not None
    assert context.page_refs == (first, second)
    assert context.image_refs == ("公共实验装置", "局部坐标图")
    assert context.answer_refs == ("答案页 8", "评分页 9")


def test_no_over_split_for_joint_instruction_or_scoring_points() -> None:
    raw = json.dumps({"candidates": [{
        "number": "14(2)",
        "stem": "写出反应方程式并说明理由。评分：方程式2分，理由1分。",
        "completeness": "complete", "question_kind": "atomic", "children": [],
        "split_source": "explicit_numbering", "split_confidence": "high",
    }]}, ensure_ascii=False)
    roots = parse_candidates_payload(raw)
    assert roots[0].is_leaf
    prompt = build_extraction_prompt("写出方程式并说明理由", "")
    assert "评分点不是子题" in prompt
    assert "手写答案" in prompt


def test_ocr_missing_number_keeps_low_confidence() -> None:
    raw = json.dumps({"candidates": [{
        "number": "16", "stem": "题干", "completeness": "complete",
        "question_kind": "composite", "split_source": "layout",
        "split_confidence": "low", "children": [
            {"number": "", "stem": "编号丢失的小问", "completeness": "complete",
             "split_source": "layout", "split_confidence": "low"},
            {"number": "16(2)", "stem": "编号清晰的小问", "completeness": "complete"},
        ],
    }]}, ensure_ascii=False)
    root = parse_candidates_payload(raw)[0]
    assert root.question_kind == "composite"
    assert root.children[0].split_confidence == "low"


def test_physics_question_16_uses_same_generic_tree() -> None:
    tree = QuestionCandidate(
        number="16",
        stem="带电粒子经电场后进入磁场。",
        completeness="complete",
        question_kind="composite",
        children=[
            _atomic("16(1)", "求电场力"),
            _atomic("16(2)", "求磁场中轨道半径"),
            _atomic("16(3)", "用动量定理求冲量"),
        ],
        split_source="explicit_numbering",
        split_confidence="high",
    )
    assert [entry[1].number for entry in iter_atomic_leaves([tree])] == [
        "16(1)", "16(2)", "16(3)"
    ]
    assert "chemistry" not in build_extraction_prompt("", "").lower()


def test_parent_overview_aggregates_leaf_evidence_only(
    database: Database, source: tuple[int, int, int]
) -> None:
    document_id, page_id, _ = source
    tree = _chemistry_tree(page_id)
    mapping = materialize_candidate_tree(
        database,
        document_id=document_id,
        source_page_id=page_id,
        root_candidate=tree,
    )
    question = QuestionService(database).create_question_item(
        document_id=document_id, page_id=page_id, question_kind="typical",
        question_number="16(1)", stem_text="写出 B 的结构简式。",
    )
    link_atomic_question(database, node_id=mapping["0.0"], question_item_id=question.id)
    with database._connection() as connection:  # noqa: SLF001
        connection.execute(
            """
            INSERT INTO mastery_evidence(
                question_id, event_type, result, independence, source,
                created_at, updated_at
            ) VALUES (?, 'practice', 'correct', 'independent', 'user_manual',
                      datetime('now'), datetime('now'))
            """,
            (question.id,),
        )
    overview = tree_overview(database, mapping["0"])
    assert overview["leaf_count"] == 4
    assert overview["joined_count"] == 1
    assert overview["mastered_count"] == 1
    # mastery_evidence references question_items, while the parent exists only
    # in question_nodes; no API can create a parent mastery record.
