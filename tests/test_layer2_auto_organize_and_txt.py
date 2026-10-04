"""Overnight-round tests: layer-2 auto organizing + explanation attempts + TXT."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.database import Database
from src.learning_workflow_service import (
    MasteryService,
    OutputCollectionService,
    PlainTextCollectionRenderer,
    QuestionOrganizationService,
    QuestionService,
    TwoWingsService,
)


@pytest.fixture()
def env(tmp_path: Path):
    db = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "pages" / "1").mkdir(parents=True)
    raw = tmp_path / "data" / "raw" / "试卷.pdf"
    raw.write_bytes(b"%PDF-1.7 fixture")
    image = tmp_path / "data" / "pages" / "1" / "page-1.png"
    image.write_bytes(b"png")
    db.create_document(
        title="高三数学周测",
        filename="试卷.pdf",
        source_path=raw,
        sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
        page_count=1,
        import_status="completed",
    )
    db.create_page(
        document_id=1,
        page_number=1,
        image_path=image,
        extracted_text="第1题 已知函数，求极值。",
        status="ready",
    )
    questions = QuestionService(db)
    organization = QuestionOrganizationService(db)
    question = questions.create_question_item(
        document_id=1,
        page_id=1,
        question_kind="error",
        question_number="1",
        stem_text="已知函数 f(x)=x²lnx，求极值。",
        method_tags=["分类讨论"],
    )
    return db, questions, organization, question


def test_auto_organize_is_idempotent_and_multi_membership(env) -> None:
    db, _questions, organization, question = env
    first = organization.auto_organize_question(
        question.id,
        type_family={"title": "函数与导数求极值", "description": ""},
        method_families=[{"title": "分类讨论", "description": ""}],
    )
    assert first["type"]
    assert first["method"]

    second = organization.auto_organize_question(
        question.id,
        type_family={"title": "函数与导数求极值", "description": ""},
        method_families=[{"title": "分类讨论", "description": ""}],
    )
    assert second == {"type": [], "method": [], "conclusion": []}

    # Same title resolves to the SAME family, never a duplicate.
    families = organization.list_families()
    type_families = [f for f in families if f.family_kind == "type"]
    assert len(type_families) == 1

    # A second question joins the existing family instead of cloning it.
    question2 = QuestionService(db).create_question_item(
        document_id=1,
        page_id=1,
        question_kind="error",
        question_number="2",
        stem_text="已知函数 g(x)=xe^x，讨论单调性。",
    )
    organization.auto_organize_question(
        question2.id,
        type_family={"title": "函数与导数求极值", "description": ""},
        method_families=[{"title": "分类讨论", "description": ""}],
    )
    assert organization.get_family(type_families[0].id).member_count == 2


def test_question_can_belong_to_two_type_families_and_two_method_families(env) -> None:
    db, questions, organization, question = env
    type_a = organization.create_family(family_kind="type", title="函数与导数求极值")
    type_b = organization.create_family(family_kind="type", title="分类讨论题结构")
    method_a = organization.create_family(family_kind="method", title="导数法")
    method_b = organization.create_family(family_kind="method", title="分类讨论")
    organization.assign_to_family(question.id, type_a)
    organization.assign_to_family(question.id, type_b)
    organization.assign_to_family(question.id, method_a)
    organization.assign_to_family(question.id, method_b)
    families = organization.list_families_for_question(question.id)
    kinds = [f.family_kind for f in families]
    assert kinds.count("type") == 2
    assert kinds.count("method") == 2


def test_secondary_conclusion_is_optional_and_never_fabricated(env) -> None:
    db, _questions, organization, question = env
    created = organization.auto_organize_question(
        question.id,
        type_family={"title": "函数与导数求极值", "description": ""},
        method_families=[{"title": "分类讨论", "description": ""}],
        secondary_conclusion=None,
    )
    assert created["conclusion"] == []
    assert organization.conclusion_frequency() == []

    created2 = organization.auto_organize_question(
        question.id,
        type_family={"title": "函数与导数求极值", "description": ""},
        method_families=[{"title": "分类讨论", "description": ""}],
        secondary_conclusion={
            "title": "x·lnx 型函数的极值点为 e^(-1/2)",
            "description": "f'(x)=lnx+1 零点。",
            "derivation": "∵ f'(x)=lnx+1，∴ 令 f'(x)=0 得 x=e^(-1/2)……",
        },
    )
    assert created2["conclusion"]
    frequency = organization.conclusion_frequency(limit=5)
    assert frequency and frequency[0]["question_count"] == 1

    # Re-running never duplicates the conclusion.
    created3 = organization.auto_organize_question(
        question.id,
        type_family={"title": "函数与导数求极值", "description": ""},
        method_families=[{"title": "分类讨论", "description": ""}],
        secondary_conclusion={
            "title": "x·lnx 型函数的极值点为 e^(-1/2)",
            "description": "",
            "derivation": "",
        },
    )
    assert created3["conclusion"] == []


def test_similar_ai_titles_reuse_family_not_fragment(env) -> None:
    """§20 anti-fragmentation: wording-variant titles join the same family."""

    db, questions, organization, _question = env
    q1 = questions.create_question_item(
        document_id=1, page_id=1, question_kind="error",
        question_number="1", stem_text="已知函数 f(x)=x²lnx，求极值。",
    )
    organization.auto_organize_question(
        q1.id,
        type_family={"title": "函数与导数求极值", "description": "利用导数研究函数极值"},
        method_families=[{"title": "导数法", "description": "求导找极值点"}],
    )
    q2 = questions.create_question_item(
        document_id=1, page_id=1, question_kind="error",
        question_number="2", stem_text="已知函数 g(x)=xe^x，讨论极值。",
    )
    # Different wording, same core concepts — must REUSE, not clone.
    organization.auto_organize_question(
        q2.id,
        type_family={"title": "利用导数研究函数的极值问题", "description": "通过导数判断极值"},
        method_families=[{"title": "求导数求极值点", "description": "对函数求导"}],
    )
    stats = organization.fragmentation_stats()
    assert stats["type_families"] == 1, stats
    assert stats["method_families"] == 1, stats
    assert organization.get_family(
        organization.list_families(family_kind="type")[0].id
    ).member_count == 2


def test_fragmentation_stats_shape(env) -> None:
    _db, _questions, organization, question = env
    organization.auto_organize_question(
        question.id,
        type_family={"title": "函数与导数求极值", "description": ""},
        method_families=[{"title": "分类讨论", "description": ""}],
    )
    stats = organization.fragmentation_stats()
    assert stats["question_items"] == 1
    assert stats["type_families"] == 1
    assert stats["method_families"] == 1
    assert stats["conclusion_families"] == 0
    assert stats["multi_family_questions"] == 1


def test_explanation_attempt_history_roundtrip(env) -> None:
    db, _questions, _organization, question = env
    mastery = MasteryService(db)
    attempt_id = mastery.record_explanation_attempt(
        question.id, content="先求导……因为定义域……"
    )
    mastery.update_explanation_feedback(attempt_id, "「你讲对了什么」先求导正确。")
    attempts = mastery.list_explanation_attempts(question.id)
    assert len(attempts) == 1
    assert attempts[0]["content"] == "先求导……因为定义域……"
    assert "你讲对了什么" in attempts[0]["feedback"]


def test_plain_text_export_structure_and_utf8_bom(env) -> None:
    db, _questions, organization, question = env
    QuestionService(db).update_question_item(
        question.id,
        teacher_verdict="incorrect",
        correction_note="先写定义域再求导。",
        reason_tags=["条件遗漏"],
        method_tags=["分类讨论"],
    )
    organization.auto_organize_question(
        question.id,
        type_family={"title": "函数与导数求极值", "description": ""},
        method_families=[{"title": "分类讨论", "description": ""}],
        secondary_conclusion={
            "title": "极值点结论",
            "description": "f'(x)=0。",
            "derivation": "∵ f'(x)=0，∴ ……",
        },
    )
    TwoWingsService(db).create_entry(
        wing_kind="method_trigger",
        target_layer="question",
        question_id=question.id,
        origin="user",
        confidence="confirmed",
        candidate_method="导数法",
        trigger_conditions=["出现对数与多项式相乘"],
    )
    output = OutputCollectionService(db)
    collection_id = output.create_collection(kind="error_book", title="错题本第一期")
    output.add_collection_item(collection_id, item_layer="question", item_id=question.id)
    rendered = PlainTextCollectionRenderer(db).render(collection_id)
    text = rendered.decode("utf-8-sig")
    assert rendered.startswith(b"\xef\xbb\xbf")
    for marker in (
        "【题目】",
        "【我的作答】",
        "【判定】",
        "【订正】",
        "【错因】",
        "【方法】",
        "【方法触发】",
        "【二级结论】",
    ):
        assert marker in text, marker
    assert "第1题" in text  # printed exam number, not "#1"
    assert "常见推导方法" in text


def test_plain_text_export_reports_deleted_source(env) -> None:
    db, _questions, _organization, question = env
    output = OutputCollectionService(db)
    collection_id = output.create_collection(kind="error_book", title="册子")
    output.add_collection_item(collection_id, item_layer="question", item_id=question.id)
    # Simulate a deleted source (document gone but item snapshot remains).
    with db._connection() as connection:
        connection.execute(
            "UPDATE question_items SET document_id = NULL, page_id = NULL WHERE id = ?",
            (question.id,),
        )
    text = PlainTextCollectionRenderer(db).render(collection_id).decode("utf-8-sig")
    assert "原始来源已删除，当前保留的是学习整理内容。" in text
