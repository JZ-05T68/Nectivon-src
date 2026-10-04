"""Regression tests for stable question-candidate Streamlit widget keys."""

from src.learning_entry_ui import _candidate_widget_suffix


def test_candidate_widget_suffix_is_stable_when_prior_candidate_leaves_pending() -> None:
    """A later candidate keeps its key after an earlier candidate is joined."""

    candidate = {
        "node_path": "0",
        "number": "10",
        "stem": "取一个自然数……",
        "extracted_at": "2026-10-02T09:24:09+00:00",
    }

    before = _candidate_widget_suffix(2, **candidate)
    after = _candidate_widget_suffix(2, **candidate)

    assert before == after


def test_candidate_widget_suffix_supports_nested_question_paths() -> None:
    """Composite-question leaf paths are safe and remain distinguishable."""

    shared = {
        "number": "20（1）",
        "stem": "计算第一小题",
        "extracted_at": "2026-10-02T09:24:09+00:00",
    }
    first = _candidate_widget_suffix(3, node_path="0.0", **shared)
    second = _candidate_widget_suffix(3, node_path="0.1", **shared)

    assert first != second


def test_candidate_widget_suffix_changes_after_user_edit() -> None:
    """Fresh saved text cannot inherit stale pre-edit widget state."""

    original = _candidate_widget_suffix(
        2,
        node_path="0",
        number="10",
        stem="AI 草稿",
        extracted_at="2026-10-02T09:24:09+00:00",
    )
    edited = _candidate_widget_suffix(
        2,
        node_path="0",
        number="10",
        stem="人工核对后的原文",
        extracted_at="2026-10-02T09:24:09+00:00",
    )

    assert original != edited
