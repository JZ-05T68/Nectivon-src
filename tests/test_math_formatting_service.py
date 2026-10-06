"""Source-preserving checks for optional background math formatting."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from src.math_formatting_service import (
    apply_verified_math_spans,
    candidate_display_stem,
    display_field,
    format_candidate_math,
)


def test_verified_latex_span_keeps_exact_original_meaning() -> None:
    source = "已知|b|/b=-1。"
    formatted = apply_verified_math_spans(
        source,
        [{"start": 2, "end": 10, "text": "|b|/b=-1", "latex": r"\frac{|b|}{b}=-1"}],
    )
    assert formatted == r"已知$\frac{|b|}{b}=-1$。"


def test_changed_operator_or_wrong_source_is_rejected() -> None:
    source = "a+b=2"
    assert apply_verified_math_spans(
        source, [{"start": 0, "end": 5, "text": source, "latex": "a-b=2"}]
    ) == source
    assert apply_verified_math_spans(
        source, [{"start": 0, "end": 5, "text": "a+b=3", "latex": "a+b=3"}]
    ) == source


def test_ai_fraction_cannot_change_operand_grouping() -> None:
    source = "a/b+c"
    assert apply_verified_math_spans(source, [
        {"start": 0, "end": len(source), "text": source, "latex": r"\frac{a}{b+c}"}
    ]) == source
    source = "(a+b)/c"
    assert apply_verified_math_spans(source, [
        {"start": 0, "end": len(source), "text": source, "latex": r"\frac{a+b}{c}"}
    ]) == r"$\frac{a+b}{c}$"


def test_ai_candidate_typesetting_is_cached_by_exact_human_source(tmp_path: Path) -> None:
    source = "已知|b|/b=-1。"

    class AI:
        calls = 0

        def _complete(self, prompt: str, *, target_refs: tuple[str, ...]) -> str:
            self.calls += 1
            assert target_refs == ("page:3",)
            return json.dumps({"stem": [
                {"start": 2, "end": 10, "text": "|b|/b=-1", "latex": r"\frac{|b|}{b}=-1"}
            ]})

    ai = AI()
    assert format_candidate_math(tmp_path, 3, source, ai)
    assert ai.calls == 1
    assert candidate_display_stem(tmp_path, 3, source) == r"已知$\dfrac{|b|}{b}=-1$。"
    edited = "已知|b|/b=1。"
    assert candidate_display_stem(tmp_path, 3, edited) == edited
    assert candidate_display_stem(tmp_path, 4, source) == source


def test_optional_ai_failure_keeps_manual_source_without_retry(tmp_path: Path) -> None:
    class FailingAI:
        calls = 0

        def _complete(self, prompt: str, *, target_refs: tuple[str, ...]) -> str:
            self.calls += 1
            raise RuntimeError("provider unavailable")

    ai = FailingAI()
    assert not format_candidate_math(tmp_path, 3, "a+b=2", ai)
    assert ai.calls == 1
    assert candidate_display_stem(tmp_path, 3, "a+b=2") == "a+b=2"
    assert not format_candidate_math(tmp_path, 3, "a+b=2", None)


def test_candidate_ai_display_survives_join_but_never_overrides_later_edits() -> None:
    question = SimpleNamespace(
        stem_text="a/b", math_display=None,
        ai_draft={"math_display": {
            "stem_text": {"source": "a/b", "display": r"$\dfrac{a}{b}$"}
        }},
    )
    assert display_field(question, "stem_text") == r"$\dfrac{a}{b}$"
    question.stem_text = "a/c"
    assert display_field(question, "stem_text") == "a/c"


def test_partial_ai_spans_cannot_fragment_a_complete_local_formula(tmp_path: Path) -> None:
    source = "已知|b|/b=-1，求(a+1)²和x≤1。"

    class PartialAI:
        def _complete(self, prompt: str, *, target_refs: tuple[str, ...]) -> str:
            assert r"\\dfrac" in prompt
            return json.dumps({"stem": [
                {"start": 2, "end": 5, "text": "|b|", "latex": r"\left|b\right|"},
                {"start": 6, "end": 7, "text": "b", "latex": "b"},
            ]})

    assert format_candidate_math(tmp_path, 3, source, PartialAI())
    display = candidate_display_stem(tmp_path, 3, source)
    assert r"$\dfrac{|b|}{b}=-1$" in display
    assert r"$(a+1)^{2}$" in display
    assert r"$x\leq 1$" in display
    assert "$/" not in display
def test_candidate_save_never_waits_for_ai_and_deduplicates_inflight(tmp_path: Path) -> None:
    from src.math_display import normalize_question_math
    from src.math_formatting_service import candidate_display_stem, schedule_candidate_math

    entered, release = Event(), Event()

    class AI:
        def _complete(self, prompt: str, *, target_refs: tuple[str, ...]) -> str:
            entered.set()
            assert release.wait(timeout=5)
            return '{"stem": []}'

    source = "求(x+1)^3。"
    future = schedule_candidate_math(tmp_path, 7, source, AI())
    assert future is not None
    try:
        assert entered.wait(timeout=3)
        assert not future.done()
        assert normalize_question_math(source) == r"求$(x+1)^{3}$。"
        assert schedule_candidate_math(tmp_path, 7, source, AI()) is None
    finally:
        release.set()
    assert future.result(timeout=5)
    assert candidate_display_stem(tmp_path, 7, source) == r"求$(x+1)^{3}$。"
    assert schedule_candidate_math(tmp_path, 7, source, AI()) is None
