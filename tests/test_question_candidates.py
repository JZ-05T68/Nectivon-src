"""Question candidate card tests (fix round §37-42).  No network, no AI."""

from __future__ import annotations

import json

import pytest

from src.question_candidate_service import (
    QuestionCandidate,
    QuestionCandidateError,
    QuestionCandidateStore,
    build_extraction_prompt,
    parse_candidates_payload,
    stem_confidence_for,
)


@pytest.fixture()
def store(tmp_path) -> QuestionCandidateStore:
    return QuestionCandidateStore(tmp_path / "question-candidates")


def _candidate(number: str = "2-16", **overrides) -> QuestionCandidate:
    defaults = dict(
        number=number,
        stem="2-16 试求图 2-75 所示系统的闭环传递函数。",
        completeness="complete",
        incomplete_reason="",
        figure_refs=["图 2-75"],
        status="pending",
        extracted_at="2026-09-27T01:00:00+00:00",
    )
    defaults.update(overrides)
    return QuestionCandidate(**defaults)


def test_store_roundtrip(store) -> None:
    store.save_page_candidates(7, [_candidate(), _candidate("2-17", completeness="incomplete")])
    loaded = store.page_candidates(7)
    assert loaded is not None and len(loaded) == 2
    assert loaded[0].number == "2-16"
    assert loaded[1].completeness == "incomplete"


def test_store_absent_returns_none(store) -> None:
    assert store.page_candidates(999) is None


def test_mark_status_only_touches_candidate(store) -> None:
    store.save_page_candidates(7, [_candidate()])
    store.mark_status(7, "2-16", "added")
    assert store.page_candidates(7)[0].status == "added"
    with pytest.raises(QuestionCandidateError):
        store.mark_status(7, "2-99", "added")


def test_parse_rejects_non_json() -> None:
    with pytest.raises(QuestionCandidateError):
        parse_candidates_payload("我觉得这页大概有三道题吧")


def test_parse_normalizes_bad_completeness() -> None:
    candidates = parse_candidates_payload(
        json.dumps(
            {
                "candidates": [
                    {"number": "2-15", "stem": "只有图", "completeness": "不完整"},
                ]
            },
            ensure_ascii=False,
        )
    )
    assert candidates[0].completeness == "incomplete"


def test_parse_empty_candidates_allowed() -> None:
    assert parse_candidates_payload('{"candidates": []}') == []


def test_parse_repairs_missing_comma_between_known_candidate_fields() -> None:
    raw = (
        '{"candidates": [{"number": "23", "stem": "分析卤水层形成过程" '
        '"completeness": "complete", "incomplete_reason": "", '
        '"visual_dependency": "required", "visual_notes": "图12", '
        '"figure_refs": ["图12"],}]} '
    )

    candidates = parse_candidates_payload(raw)

    assert len(candidates) == 1
    assert candidates[0].number == "23"
    assert candidates[0].stem == "分析卤水层形成过程"
    assert candidates[0].visual_dependency == "required"


def test_prompt_forbids_adjacent_page_candidates() -> None:
    prompt = build_extraction_prompt("2-15 …", "图2-74 结构…")
    assert "相邻页" in prompt
    assert "绝不要凭教材知识补写" in prompt


def test_stem_confidence_mapping() -> None:
    assert stem_confidence_for("complete") == "probable"
    assert stem_confidence_for("incomplete") == "uncertain"


def test_candidate_validation() -> None:
    with pytest.raises(QuestionCandidateError):
        _candidate(number="", stem="").validate()
    with pytest.raises(QuestionCandidateError):
        _candidate(completeness="maybe").validate()


def test_mark_status_supports_skipped_state(store) -> None:
    """V086-R1 FIX-2: 暂不整理 gets its own recoverable state."""

    store.save_page_candidates(7, [_candidate()])
    store.mark_status(7, "2-16", "skipped")
    loaded = store.page_candidates(7)
    assert loaded[0].status == "skipped"
    store.mark_status(7, "2-16", "pending")
    assert store.page_candidates(7)[0].status == "pending"


def test_mark_status_rejects_unknown_state(store) -> None:
    store.save_page_candidates(7, [_candidate()])
    with pytest.raises(QuestionCandidateError):
        store.mark_status(7, "2-16", "snoozed")
