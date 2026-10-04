"""F5 contract tests: reference answer vs scoring standard trust gate.

Real-case anchor (doc173): a pure reference answer must never be packaged
as a scoring standard; rubric capability degrades honestly to unknown.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.answer_relation_assessor import assess_answer_document


@pytest.fixture()
def database(tmp_path: Path):
    from src.database import Database

    return Database(tmp_path / "data" / "database" / "knowledge.db")


def test_case_a_pure_reference_answer() -> None:
    text = (
        "三模地理参考答案\n"
        "1. A\n2. B\n3. C\n"
        "24、(1)南极洲板块和美洲板块相互碰撞，岩层受挤压上拱，形成高大山脉。\n"
        "(2) 甲：温带落叶阔叶林带；成因：甲地位于40-50°之间的大陆西岸。\n"
    )
    assessment = assess_answer_document(text, title="四模地理参考答案")
    assert assessment.suggested_relation_kind == "reference_answer"
    assert assessment.rubric_capability == "unknown"
    assert any("参考答案" in w for w in assessment.warnings)


def test_case_b_explicit_scoring_standard() -> None:
    text = (
        "参考答案及评分标准\n"
        "1. A\n2. B\n"
        "24、(1)甲对应②，乙对应①。(2分)甲地夏季气温低于乙地。(4分)\n"
        "(2)可耕地面积广大；气候干燥晴天多。(6分)\n"
        "评分细则：第25题答出任意三点得6分，每点2分。\n"
    )
    assessment = assess_answer_document(text, title="参考答案及评分标准")
    assert assessment.suggested_relation_kind == "scoring_standard"
    assert assessment.rubric_capability == "available"
    assert assessment.confidence == "probable"


def test_case_c_title_says_scoring_body_empty() -> None:
    text = (
        "评分标准\n"
        "1. A\n2. B\n3. C\n"
        "24、(1)南极洲板块和美洲板块相互碰撞，形成高大山脉。\n"
    )
    assessment = assess_answer_document(text, title="参考答案及评分标准")
    assert assessment.suggested_relation_kind != "scoring_standard" or (
        assessment.confidence == "uncertain"
    )
    assert any("标题与正文" in w or "未发现" in w for w in assessment.warnings)
    # 标题不能单独升级：rubric 不得 available
    if assessment.suggested_relation_kind == "reference_answer":
        assert assessment.rubric_capability != "available"


def test_case_d_title_answer_body_has_rules() -> None:
    text = (
        "参考答案\n"
        "24、(1)甲对应②，乙对应①。(2分)成因说明。(4分)\n"
        "评分细则：答出任意三点得6分，每点2分。\n"
    )
    assessment = assess_answer_document(text, title="参考答案")
    assert any("不一致" in w for w in assessment.warnings)
    assert assessment.suggested_relation_kind in ("unknown", "scoring_standard")
    # 不得静默决定：必须带警告交用户裁决
    assert assessment.warnings


def test_case_e_paper_score_marks_are_not_rubric() -> None:
    """题目总分（4分/6分/18分）绝不能当作评分细则。"""

    text = (
        "24、(1)南极洲板块和美洲板块相互碰撞。(6分)\n"
        "(2)温带落叶阔叶林带。(6分)\n"
        "(3)河流流向。(6分)\n"
    )
    assessment = assess_answer_document(text, title="某试卷")
    assert not any("评分细则类用语" in e for e in assessment.evidence)
    if assessment.suggested_relation_kind == "reference_answer":
        assert any("不能当作评分点" in w for w in assessment.warnings)
    assert assessment.rubric_capability != "available"


def test_case_f_choice_letters_only() -> None:
    text = "1. A\n2. C\n3. B\n4. D\n5. A\n"
    assessment = assess_answer_document(text, title="答案")
    assert assessment.suggested_relation_kind == "reference_answer"
    assert assessment.rubric_capability == "unknown"


def test_case_g_essay_answer_no_per_point_scores() -> None:
    text = (
        "24、(1)中心城区分布密集；近郊区多分布在临近中心城区密集地带。\n"
        "(2)便利的交通运输；新区政策优势；廉价的土地成本。\n"
        "(3)主要分布在中心城区；老城区历史悠久。\n"
    )
    assessment = assess_answer_document(text, title="答案")
    assert assessment.suggested_relation_kind == "reference_answer"
    assert assessment.rubric_capability == "unknown"


def test_case_h_ocr_noise_stays_unknown() -> None:
    text = "第1题 4刀 第2题 6芬 每点? 第3题 答……"
    assessment = assess_answer_document(text, title="评分标淮（OCR）")
    assert assessment.suggested_relation_kind == "unknown"
    assert assessment.confidence in ("low", "uncertain")


def test_case_i_user_override_wins(database=None) -> None:
    """The detector only SUGGESTS; the service layer records what the user
    confirmed and nothing silently rewrites it (contract with G3-A)."""

    # The authoritative confirm path takes the USER's relation kind as an
    # argument — the detector output never flows in automatically.
    import inspect

    from src.document_relation_service import (
        suggest_document_relation,  # noqa: F401
    )

    signature = inspect.signature(suggest_document_relation)
    assert "relation_kind" in signature.parameters


def test_case_j_legacy_relation_not_rewritten(database) -> None:
    """Historically confirmed relations keep their kind and status."""

    import hashlib

    from src.document_relation_service import (
        confirm_document_relation,
        list_document_relations,
        suggest_document_relation,
    )

    def seed(title: str) -> int:
        document = database.create_document(
            title=title,
            filename=f"{title}.pdf",
            source_path=f"C:/corpus/{title}.pdf",
            sha256=hashlib.sha256(title.encode()).hexdigest(),
        )
        return int(document.id)

    primary = seed("试卷L")
    answer = seed("答案L")
    relation_id = suggest_document_relation(
        database,
        primary_document_id=primary,
        related_document_id=answer,
        relation_kind="reference_answer",
    )
    confirm_document_relation(database, relation_id)
    # Run the detector — it has no write path at all.
    assess_answer_document("1. A\n2. B", title="答案L")
    relations = list_document_relations(database, primary)
    assert len(relations) == 1
    assert relations[0]["relation_kind"] == "reference_answer"
    assert relations[0]["status"] == "confirmed"


def test_paper_guard_exam_paper_is_neither() -> None:
    text = (
        "注意事项：1.本试卷分第Ⅰ卷（选择题）和第Ⅱ卷（非选择题）。"
        "考试时间75分钟。考生作答须填涂答题卡。24．阅读图文材料，完成下列要求。（18分）"
    )
    assessment = assess_answer_document(text, title="某试卷")
    assert assessment.suggested_relation_kind == "unknown"
    assert assessment.rubric_capability == "unavailable"
