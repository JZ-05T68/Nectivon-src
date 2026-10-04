"""Source-preserving checks for optional background math formatting."""

from __future__ import annotations

from src.math_formatting_service import apply_verified_math_spans


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
