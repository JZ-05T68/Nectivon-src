"""Regression tests for Final Answer numbered-list rendering (v0.8.4).

A model that returns an enumerated answer as one run-on paragraph
(``1. 类型A 2. 类型B 3. 类型C``) used to be rendered as a single line by the
Markdown UI. These tests lock the two-part fix:

- a prompt format rule that asks for one item per line;
- a deterministic, whitespace-only normalization that separates such items
  while never touching version numbers, decimals or section references.
"""

from __future__ import annotations

import re

import pytest

from src.ai.answer_format import normalize_numbered_list_lines
from src.ai.provider import CompletionResult
from src.ai.rag_answer_service import RagAnswerService
from src.ai.rag_prompt_builder import _RAG_EXTRA_RULES, RagPromptBuilder
from src.knowledge_context_packager import KnowledgeContextPackager
from src.models import (
    ContextAnchorType,
    ContextFingerprintState,
    ContextItem,
    ContextItemType,
    ContextSourceAnchor,
)

KB_UUID = "12345678-1234-1234-1234-123456789abc"


def _sourced_item(local_id: int = 1) -> ContextItem:
    return ContextItem(
        type=ContextItemType.KNOWLEDGE_OBJECT,
        local_id=local_id,
        stable_id=f"{KB_UUID}:knowledge_object:{local_id}",
        title="编码器接线经验",
        content="A/B 相接反会导致 PID 震荡。",
        kind="experience",
        kind_label="经验",
        status="active",
        status_label="现行",
        importance="primary",
        updated_at=None,
        revision_ref="第 1 版",
        source_anchors=(
            ContextSourceAnchor(
                anchor_type=ContextAnchorType.PAGE.value,
                anchor_id=7,
                anchor_label="页面 7",
                fingerprint_state=ContextFingerprintState.VALID.value,
            ),
        ),
        relation_refs=(),
    )


class _Provider:
    def __init__(self, text: str) -> None:
        self._text = text

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_completion_tokens: int | None = None,
    ) -> CompletionResult:
        return CompletionResult(text=self._text, model=model or "fake-1")


def _marker_at_line_start(text: str, number: int) -> bool:
    """True when ``number`` followed by a list separator starts some line."""

    return re.search(rf"(?m)^{number}[.)、]", text) is not None


def _content_preserved(raw: str, normalized: str) -> bool:
    """Whitespace-only change: non-space characters identical and in order."""

    return "".join(normalized.split()) == "".join(raw.split())


# --- prompt-level format constraint ----------------------------------------


def test_prompt_carries_numbered_list_layout_rule_26() -> None:
    assert "编号列表排版" in _RAG_EXTRA_RULES
    assert "每一项必须" in _RAG_EXTRA_RULES
    # The rule must name the numeric shapes that must NOT be re-flowed.
    for literal in ("0.8.4", "3.11", "1.25", "第2.3节"):
        assert literal in _RAG_EXTRA_RULES


def test_prompt_builder_injects_rule_26() -> None:
    package = KnowledgeContextPackager(kb_uuid=KB_UUID).build([_sourced_item()])
    prompt = RagPromptBuilder().build("有哪些类型？", package)
    assert "26. 编号列表排版" in prompt


# --- normalization unit behavior -------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "类型分为 1. 通电延时 2. 断电延时 3. 保持型。",
        "1. 通电延时 2. 断电延时 3. 保持型",
        "答：1. A 2. B 3. C",
        "1、甲 2、乙 3、丙",
        "1) 第一项 2) 第二项 3) 第三项",
    ],
)
def test_run_on_list_is_split_one_item_per_line(raw: str) -> None:
    normalized = normalize_numbered_list_lines(raw)
    assert _content_preserved(raw, normalized)
    for number in (1, 2, 3):
        assert _marker_at_line_start(normalized, number), number
    # a run-on single line must have become multiple lines
    assert len([line for line in normalized.splitlines() if line.strip()]) >= 3


@pytest.mark.parametrize(
    "raw",
    [
        # version numbers / interpreter versions
        "当前版本 0.8.4，运行于 Python 3.11。",
        # decimals, including one glued to a unit symbol (温度 3.5℃)
        "系数为 1.25，另一档为 2.50，取值区间 0.5 到 3.0。",
        "温度为 3.5℃，压力为 1.25MPa。",
        # dates written with dots (2026.09.24)
        "日期 2026.09.24 与 2026.12.31。",
        # section references
        "见第2.3节与第3.1节的规定。",
        # identifiers with dotted digits
        "固件 v1.0 与 v2.0 均适用。",
        # an already well-formed list must stay byte-for-byte unchanged
        "1. 通电延时\n2. 断电延时\n3. 保持型",
        # prose that merely starts with a number but is not a list
        "1. 这是唯一一条，没有后续序号。",
        # non-monotonic digits are not a list
        "共 3 项，其中 1 项已完成。",
    ],
)
def test_numeric_literals_are_never_reflowed(raw: str) -> None:
    assert normalize_numbered_list_lines(raw) == raw


def test_code_fence_content_is_untouched() -> None:
    raw = (
        "示例代码：\n"
        "```\n"
        "1. 保持原样 2. 保持原样 3. 保持原样\n"
        "```\n"
        "以上是代码。"
    )
    assert normalize_numbered_list_lines(raw) == raw


def test_two_independent_lists_are_each_split() -> None:
    raw = "第一组：1. 甲 2. 乙 3. 丙。第二组：1. A 2. B 3. C。"
    normalized = normalize_numbered_list_lines(raw)
    assert _content_preserved(raw, normalized)
    # both lists restart at 1; each number must begin some line
    assert len(re.findall(r"(?m)^1\.", normalized)) == 2
    assert len(re.findall(r"(?m)^2\.", normalized)) == 2
    assert len(re.findall(r"(?m)^3\.", normalized)) == 2


def test_empty_and_non_string_inputs_pass_through() -> None:
    assert normalize_numbered_list_lines("") == ""
    assert normalize_numbered_list_lines(None) is None  # type: ignore[arg-type]


# --- end-to-end through the audited answer chain ---------------------------


def test_answer_service_normalizes_run_on_list_and_keeps_citation() -> None:
    raw = (
        "时间继电器分为 1. 通电延时型 2. 断电延时型 3. 保持型。"
        "依据：【来源 #1】"
    )
    package = KnowledgeContextPackager(kb_uuid=KB_UUID).build([_sourced_item()])
    output = RagAnswerService(_Provider(raw)).answer("有哪些类型？", package)

    assert _content_preserved(raw, output.answer)
    for number in (1, 2, 3):
        assert _marker_at_line_start(output.answer, number), number
    # citation validation still passes on the normalized text
    assert output.answer_citations == (f"{KB_UUID}:knowledge_object:1",)
    # audit records the raw model output length, not the re-flowed length
    assert output.output_chars == len(raw)


def test_answer_service_leaves_version_numbers_untouched() -> None:
    raw = "该软件 0.8.4 版运行于 Python 3.11，系数 1.25。依据：【来源 #1】"
    package = KnowledgeContextPackager(kb_uuid=KB_UUID).build([_sourced_item()])
    output = RagAnswerService(_Provider(raw)).answer("版本与参数？", package)
    assert output.answer == raw
    assert output.answer_citations == (f"{KB_UUID}:knowledge_object:1",)
