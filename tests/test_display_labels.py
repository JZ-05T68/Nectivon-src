"""Overnight-round tests: display labels, reason-tag guard, no internal ids."""

from __future__ import annotations

from src.display_labels import (
    BANNED_FINAL_REASON_TAGS,
    QUESTION_KIND_LABELS,
    family_display_title,
    normalize_reason_tags,
    question_display_title,
    stem_digest,
)
from src.learning_workflow_service import QuestionFamily


def test_banned_reason_tags_are_replaced_by_root_cause_ask() -> None:
    """粗心/马虎/计算错误 may be a phenomenon, never the final error cause."""

    normalized = normalize_reason_tags(["计算错误", "粗心", "条件遗漏"])
    assert "条件遗漏" in normalized
    for banned in BANNED_FINAL_REASON_TAGS:
        assert banned not in normalized
    assert any(tag.startswith("根因待确认") for tag in normalized)


def test_normalize_reason_tags_keeps_concrete_roots_and_dedupes() -> None:
    normalized = normalize_reason_tags(
        ["概念混淆", "概念混淆", "", "方法触发失败"]
    )
    assert normalized == ["概念混淆", "方法触发失败"]


def test_question_display_title_uses_printed_number_or_digest() -> None:
    class _Q:  # minimal duck-typed stand-in for QuestionItem
        id = 12
        question_kind = "error"
        question_number = "4"
        stem_text = "已知函数 f(x)=x²lnx，求极值。"

    title = question_display_title(_Q())
    assert "#12" not in title
    assert "第4题" in title
    assert QUESTION_KIND_LABELS["error"] in title

    class _QNoNumber(_Q):
        question_number = ""
        stem_text = "抛体运动：炮弹从 A 处发射，求最大射角与最大射程。"

    digest_title = question_display_title(_QNoNumber())
    assert "#12" not in digest_title
    assert stem_digest(_QNoNumber.stem_text) in digest_title


def test_family_display_title_has_no_internal_id() -> None:
    family = QuestionFamily(
        id=9,
        family_kind="conclusion",
        title="和差定区别",
        description="",
        derivation="",
        variant_pattern="",
        confusion_notes="",
        status="active",
        member_count=3,
    )
    assert "#9" not in family_display_title(family)
    assert "二级结论" in family_display_title(family)
    assert "3" in family_display_title(family)
