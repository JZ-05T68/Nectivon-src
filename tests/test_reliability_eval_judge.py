"""Unit tests for the v0.8 reliability eval judge (no LLM, no database)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.reliability_eval import (  # noqa: E402
    _judge_answer,
    _judge_case,
)


def _expect(**kwargs: object) -> dict[str, object]:
    return dict(kwargs)


def test_fragments_all_required() -> None:
    expect = _expect(answer_fragments=["25", "28"], conflict_required=True)
    ok, failed = _judge_answer(expect, "v1.0 是 25，v1.1 修订为 28，两者不同。")
    assert ok
    assert failed == []


def test_conflict_marker_required() -> None:
    expect = _expect(answer_fragments=["25", "28"], conflict_required=True)
    ok, failed = _judge_answer(expect, "v1.0 是 25，v1.1 是 28。")
    assert not ok
    assert "conflict_required" in failed


def test_any_fragments_group() -> None:
    expect = _expect(answer_any_fragments=[["对调", "U/V"], ["3.2"]])
    ok, failed = _judge_answer(expect, "先确认固件是 DemoSuite 3.2，再对调 U/V 两相线。")
    assert ok
    assert failed == []


def test_forbid_fragments_triggers() -> None:
    expect = _expect(answer_fragments=["500"], forbid_fragments=["300"])
    ok, failed = _judge_answer(expect, "高粉尘环境下应缩短到 300 小时。")
    assert not ok
    assert "answer_fragments" in failed
    assert "forbid_fragments" in failed


def test_forbid_regex_blocks_fabricated_dates() -> None:
    expect = _expect(no_answer_ok=True, forbid_regex=[r"\d+\s*月\s*\d+\s*[日号]"])
    ok, failed = _judge_answer(expect, "机器人比赛在 3 月 5 日举行。")
    assert not ok
    assert "forbid_regex" in failed


def test_no_answer_ok_passes_honest_refusal() -> None:
    expect = _expect(no_answer_ok=True)
    ok, failed = _judge_answer(expect, "没有在当前知识库中找到可支持该问题的资料。")
    assert ok
    assert failed == []


def test_blur_lenient_accepts_uncertainty() -> None:
    expect = _expect(blur_lenient={
        "positives": [["密封失效"], ["45"]],
        "uncertainty": ["模糊", "无法", "看不清"],
    })
    ok, failed = _judge_answer(expect, "页面严重模糊，无法可靠读出占比数值。")
    assert ok
    assert failed == []


def test_blur_lenient_rejects_confident_wrong() -> None:
    expect = _expect(blur_lenient={
        "positives": [["密封失效"], ["45"]],
        "uncertainty": ["模糊", "无法", "看不清"],
    })
    ok, failed = _judge_answer(expect, "占比最大的是轴承磨损。")
    assert not ok
    assert "blur_confident_wrong" in failed


def test_tool_expectation_single_value() -> None:
    case = {"expect": _expect(tool="page_visual_search"), "metrics": {}}
    checks, failed = _judge_case(case, "P-203", [], [], "page_search")
    assert checks["tool"] is False
    assert "tool" in failed


def test_tool_expectation_alternatives() -> None:
    case = {"expect": _expect(tool=["page_visual_search", "page_search"]), "metrics": {}}
    checks, failed = _judge_case(case, "28 Nm", [], [], "page_search")
    assert checks["tool"] is True
    assert failed == []


def test_memory_must_and_forbid() -> None:
    case = {"expect": _expect(memory_must=[12], memory_forbid=[11]), "metrics": {}}
    checks, failed = _judge_case(case, "M8 用 25 Nm。", [], [12], "knowledge_search")
    assert checks["citation"] is True

    checks2, failed2 = _judge_case(case, "M8 用 25 Nm。", [], [11, 12], "knowledge_search")
    assert checks2["citation"] is False
    assert "memory_forbid" in failed2


def test_memory_forbid_all() -> None:
    case = {"expect": _expect(memory_forbid_all=True, no_answer_ok=True), "metrics": {}}
    checks, failed = _judge_case(case, "没有找到相关资料。", [], [], "page_search")
    assert checks["citation"] is True

    checks2, failed2 = _judge_case(case, "不知道。", [], [7], "knowledge_search")
    assert checks2["citation"] is False
    assert "memory_forbid_all" in failed2


def test_cite_must_location_matching() -> None:
    case = {
        "expect": _expect(cite_must=[{"doc": "v1.1", "page": 1}]),
        "metrics": {},
    }
    locations = [{"doc": "泵维护视觉手册_v1.1", "page": 1}]
    checks, failed = _judge_case(case, "28 Nm", locations, [], "page_visual_search")
    assert checks["citation"] is True
    assert failed == []


@pytest.mark.parametrize("answer", ["", "（空）"])
def test_empty_answer_fails_fragments(answer: str) -> None:
    expect = _expect(answer_fragments=["46"])
    ok, failed = _judge_answer(expect, answer)
    assert not ok
    assert "answer_fragments" in failed


def test_numeric_forbid_not_triggered_by_document_title() -> None:
    """H26-4 regression: doc title FAIL026 must not fire forbid "26"."""
    expect = _expect(
        answer_fragments=["2.6"],
        forbid_fragments=["26"],
    )
    answer = (
        "图7-1 真小数折线图的第1个数据点是 **2.6**。\n"
        "在《FAIL026图表标签边界测试集》第 7 页中，底部说明文字明确记录："
        "数据点标签：2.6、5.5、10.2。"
    )
    ok, failed = _judge_answer(expect, answer)
    assert ok
    assert failed == []


def test_numeric_forbid_still_fires_on_standalone_integer() -> None:
    expect = _expect(answer_fragments=["2.6"], forbid_fragments=["26"])
    ok, failed = _judge_answer(expect, "第1个数据点是 26（错误地去掉了小数点）。")
    assert not ok
    assert "forbid_fragments" in failed


def test_numeric_answer_fragment_not_satisfied_by_title_substring() -> None:
    expect = _expect(answer_fragments=["26"])
    ok, failed = _judge_answer(expect, "见《FAIL026图表标签边界测试集》。")
    assert not ok
    assert "answer_fragments" in failed


def test_numeric_fragment_boundaries_reject_embedded_digits() -> None:
    expect = _expect(answer_fragments=["2.6"])
    ok, failed = _judge_answer(expect, "该值记录为 12.6。")
    assert not ok
    assert "answer_fragments" in failed

    ok2, failed2 = _judge_answer(expect, "该值记录为 2.6。")
    assert ok2
    assert failed2 == []
