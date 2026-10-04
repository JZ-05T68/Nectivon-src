"""F6 cross-page question-group detection contract tests.

Real exam papers routinely put the shared material + the first members of
a choice group at the bottom of one page and continue on the next.  The
single-page detector (F3) can never see them — the F6 audit listed the
missing cross-page mode as its core gap.  These tests pin the generic
signal: a printed hint range whose members SPLIT across consecutive
pages, with exact coverage.  Under-coverage / duplicates / three-page
spreads produce NO draft (宁可漏报，不可错报).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.choice_group_detector import (
    detect_choice_groups,
    detect_cross_page_groups,
)


@dataclass(slots=True)
class _Cand:
    number: str
    stem: str


MATERIAL = (
    "巴尔喀什湖地处哈萨克斯坦共和国东部，湖面海拔约340米，湖形狭长，"
    "中部有一半岛，半岛以北湖域较浅。据此完成下面3～5小题。"
)


def _mc(number: str, ask: str) -> _Cand:
    return _Cand(
        number=number,
        stem=f"{MATERIAL}\n{ask}\nA. 甲　B. 乙　C. 丙　D. 丁",
    )


def test_multiline_option_lines_never_leak_into_material() -> None:
    """Real PDFs put each option on its own line (F6-04, browser-found):
    the material must start AFTER the previous question's last option
    LINE, not after the "D." marker itself."""

    page7_text = (
        "12. 关于某区域农业发展的叙述，正确的是\n"
        "A. 热量充足，复种指数高\n"
        "B. 降水丰沛，水源稳定\n"
        "C. 地形平坦，土壤肥沃\n"
        "D. 光照充足，昼夜温差大\n"
        + MATERIAL
    )
    drafts = detect_cross_page_groups(
        {7: [_mc("3", "q3"), _mc("4", "q4")], 8: [_mc("5", "q5")]},
        {7: page7_text, 8: "页8。"},
    )
    assert len(drafts) == 1
    shared = drafts[0].shared_text
    assert "巴尔喀什湖" in shared
    for leaked in ("复种指数", "水源稳定", "地形平坦", "光照充足"):
        assert leaked not in shared, leaked


def test_cross_page_group_detected_with_pages_and_members() -> None:
    page7_text = (
        "上一题的题干……关于某地人口迁移的叙述。"
        "A. 甲地迁出　B. 乙地迁入　C. 丙地持平　D. 丁地波动\n"
        + MATERIAL
    )
    drafts = detect_cross_page_groups(
        {7: [_mc("3", "第3题问法"), _mc("4", "第4题问法")],
         8: [_mc("5", "第5题问法")]},
        {7: page7_text, 8: "第 5 题的选项与图。"},
    )
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.member_numbers == ["3", "4", "5"]
    assert draft.page_numbers == [7, 8]
    assert draft.confidence == "probable"
    assert "跨页" in "".join(draft.signals)
    # shared material comes from the tail of the earlier page
    assert "巴尔喀什湖" in draft.shared_text
    # the previous question's option block must NOT be in the material
    assert "甲地迁出" not in draft.shared_text
    # and the single-page detector must stay usable on page 7 alone
    assert detect_choice_groups([], page7_text) == []


def test_single_page_range_never_produces_cross_page_draft() -> None:
    page_text = f"{MATERIAL}"
    drafts = detect_cross_page_groups(
        {7: [_mc("3", "q3"), _mc("4", "q4"), _mc("5", "q5")]},
        {7: page_text},
    )
    assert drafts == []


def test_missing_member_produces_no_draft() -> None:
    drafts = detect_cross_page_groups(
        {7: [_mc("3", "q3")], 8: [_mc("5", "q5")]},
        {7: MATERIAL, 8: "页8。"},
    )
    assert drafts == []


def test_duplicate_member_across_pages_produces_no_draft() -> None:
    drafts = detect_cross_page_groups(
        {7: [_mc("3", "q3"), _mc("4", "q4")],
         8: [_mc("3", "q3-dup"), _mc("5", "q5")]},
        {7: MATERIAL, 8: "页8。"},
    )
    assert drafts == []


def test_three_page_spread_produces_no_draft() -> None:
    drafts = detect_cross_page_groups(
        {7: [_mc("3", "q3")], 8: [_mc("4", "q4")], 9: [_mc("5", "q5")]},
        {7: MATERIAL, 8: "页8。", 9: "页9。"},
    )
    assert drafts == []
