"""Regression tests for numbered-list line breaks in the Agent answer card.

The Final Answer normalization (``src/ai.answer_format``) separates run-on
``1. 2. 3.`` enumerations with single newlines. The RAG 问答 surface renders
the answer through ``st.markdown``, so those newlines become an ordered list.
The 知识Agent surface renders escaped HTML paragraphs instead; there the
newlines survive only if the display CSS preserves them.

Locked contract (v0.8.5 user-feedback closure):

- the ``.ekb-aw-answer p`` rule carries ``white-space: pre-line`` so a
  model/normalizer-inserted ``\\n`` renders as a real line break while
  long CJK text still wraps automatically and spaces still collapse;
- the same rule carries ``overflow-wrap: break-word`` so a long unbroken
  token (URL / model id) can break inside itself instead of overflowing;
- the view-model mapping layer never collapses the single newlines: the
  paragraph splitter keeps them inside the escaped text and the plain
  reassembly round-trips them byte-for-byte.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.demo_ui import (
    AnswerSegment,
    build_answer_view_model,
    escape_text,
    split_answer_paragraph,
)

PAGE_FILE = Path(__file__).resolve().parents[1] / "pages" / "0_知识Agent.py"

NORMALIZED_LIST_ANSWER = "1. 通电延时型\n2. 断电延时型\n3. 保持型"


def _answer_p_rule() -> str:
    """Return the ``.ekb-aw-answer p`` CSS rule body from the page file."""

    css = PAGE_FILE.read_text(encoding="utf-8")
    match = re.search(
        r"\.ekb-aw-answer p\s*\{([^}]*)\}",
        css,
    )
    assert match is not None, ".ekb-aw-answer p rule must exist in the page CSS"
    return match.group(1)


def test_answer_paragraph_rule_preserves_newlines() -> None:
    rule = _answer_p_rule()
    assert "white-space: pre-line" in rule, rule


def test_answer_paragraph_rule_breaks_long_tokens() -> None:
    rule = _answer_p_rule()
    assert "overflow-wrap: break-word" in rule, rule


def test_paragraph_splitter_keeps_inner_newlines() -> None:
    segments = split_answer_paragraph(
        NORMALIZED_LIST_ANSWER, render_markers=False, citation_count=0
    )
    assert len(segments) == 1
    assert isinstance(segments[0], AnswerSegment)
    # nothing in the mapping layer may collapse the single newlines
    assert segments[0].text == NORMALIZED_LIST_ANSWER


def test_answer_text_round_trip_preserves_newlines() -> None:
    class _Response:
        status = "completed"
        answer = NORMALIZED_LIST_ANSWER
        warnings: tuple[str, ...] = ()
        citations: tuple[str, ...] = ()
        citations_detail = None

    view_model = build_answer_view_model("q", _Response(), {})
    assert view_model.answer_text == NORMALIZED_LIST_ANSWER


def test_escape_text_keeps_newlines() -> None:
    escaped = escape_text(NORMALIZED_LIST_ANSWER)
    assert "\n" in escaped
    assert escaped.count("\n") == 2
