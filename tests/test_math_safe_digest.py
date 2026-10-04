"""Closure R5: math-segment-safe short digests for student-facing surfaces.

Every selector / card title / chat-history title that shortens a
LaTeX-bearing stem must never show a dangling ``$``, half a ``$$`` pair,
or a broken backslash command (R5 §10 surface rule).  These tests pin the
guarantee for all twelve fixture families used in the manual 8512 surface
sweep, plus the consumer surfaces (stem digest, candidate previews).
"""

from __future__ import annotations

import re

import pytest

from src.display_labels import question_display_title, stem_digest
from src.text_utils import math_safe_prefix, short_math_digest

FIXTURE_STEMS = {
    "limit": "已知 $f'(1)=2$，求 $\\lim_{h\\to0}\\frac{f(1+h)-f(1)}{h}$ 的值。",
    "fraction": "计算 $\\frac{3}{7}+\\frac{2}{7}$ 的结果，结果用最简分数表示。",
    "derivative": "设 $f(x)=x^3-2x$，求曲线在 $x=1$ 处的导数值 $f'(1)$。",
    "integral": "计算定积分 $\\int_0^1 x^2\\,dx$ 并说明几何意义。",
    "radical": "化简 $\\sqrt{x^2+2x+1}$，其中 $x\\ge-1$。",
    "scripts": "已知 $a_i^2+b^2=c^2$ 对每个 $i\\in\\mathbb{N}^*$ 成立。",
    "cases": (
        "设分段函数 $f(x)=\\begin{cases}x^2,&x\\ge0\\\\-x,&x<0\\end{cases}$，"
        "求 $f(2)$ 与 $f(-3)$。"
    ),
    "greek": "若 $\\alpha+\\beta=\\pi$，判断 $\\cos\\alpha$ 与 $\\cos\\beta$ 的关系。",
    "transfer": "已知传递函数 $G(s)=\\frac{10}{s(s+2)}$，求单位阶跃响应象函数 $C(s)$。",
    "mixed_cn": (
        "如图，在 $\\triangle ABC$ 中 $\\angle A=60^{\\circ}$，"
        "求证：$AB^2=AC^2+BC^2-AC\\cdot BC$。"
    ),
    "long": (
        "如图，已知抛物线 $y=ax^2+bx+c$ 与 $x$ 轴交于 $A(-1,0)$、$B(3,0)$ 两点，"
        "与 $y$ 轴交于点 $C(0,-3)$，点 $P$ 是直线 $BC$ 下方抛物线上一动点，"
        "连接 $OP$ 交 $BC$ 于点 $Q$，求线段 $PQ$ 的最大值及此时点 $P$ 的坐标。"
    ),
    "multiline": (
        "求解如下方程组并写出 $xy$ 的值：\n"
        "$$\\begin{cases}x+y=3\\\\x-y=1\\end{cases}$$\n"
        "再判断 $x+y$ 与 $x-y$ 的奇偶性。"
    ),
    "escaped_dollar": r"某商品打 \$5 折后售价为 $x$ 元，求原价。",
}

#: A kept prefix must never end in an unmatched ``$`` / ``$$`` or a ``\fr``-style
#: broken command.
PARTIAL_COMMAND = re.compile(r"\\[A-Za-z]+$")


def _assert_math_safe(prefix: str) -> None:
    assert not prefix.endswith("\\")
    assert PARTIAL_COMMAND.search(prefix) is None
    # Count unescaped dollar signs; parity must be even.
    unescaped = 0
    index = 0
    while index < len(prefix):
        if prefix[index] == "\\":
            index += 2
            continue
        if prefix[index] == "$":
            unescaped += 1
        index += 1
    assert unescaped % 2 == 0, f"dangling $ in {prefix!r}"


@pytest.mark.parametrize("limit", [8, 10, 12, 16, 20, 24, 30, 40, 70, 160])
@pytest.mark.parametrize(("name", "stem"), sorted(FIXTURE_STEMS.items()))
def test_math_safe_prefix_never_cuts_inside_formula(name: str, stem: str, limit: int) -> None:
    prefix = math_safe_prefix(stem, limit)
    _assert_math_safe(prefix)
    if len(stem) > limit:
        assert len(prefix) <= limit
    else:
        assert prefix == stem


@pytest.mark.parametrize(("name", "stem"), sorted(FIXTURE_STEMS.items()))
def test_short_math_digest_appends_ellipsis_only_when_truncated(
    name: str, stem: str
) -> None:
    assert short_math_digest(stem, 500) == stem
    if len(stem) > 12:
        digest = short_math_digest(stem, 12)
        assert digest.endswith("…")
        assert digest[:-1] == math_safe_prefix(stem, 12)


def test_limit_stem_cuts_before_second_formula() -> None:
    stem = FIXTURE_STEMS["limit"]
    digest = stem_digest(stem)
    # R5.1: the digest is the plain-UI rendering — LaTeX is converted to
    # readable math and truncated after conversion, so neither the source
    # nor a broken formula can ever appear.
    assert digest.startswith("已知 f′(1)=2")
    assert "\\lim" not in digest
    assert "$" not in digest
    assert digest.endswith("…")


def test_cases_stem_cuts_before_begin_block() -> None:
    stem = FIXTURE_STEMS["cases"]
    prefix = math_safe_prefix(stem, 24)
    _assert_math_safe(prefix)
    assert "begin" not in prefix


def test_multiline_block_formula_dropped_or_whole() -> None:
    stem = FIXTURE_STEMS["multiline"]
    prefix = math_safe_prefix(stem, 24)
    _assert_math_safe(prefix)
    # The $$ block starts at index 10 and ends way past 24 — it must be
    # dropped entirely, not half-open.
    assert "$$" not in prefix


def test_escaped_dollar_is_not_treated_as_math_delimiter() -> None:
    stem = FIXTURE_STEMS["escaped_dollar"]
    prefix = math_safe_prefix(stem, 20)
    # "\$5" is plain text; the $ of "$x$" must not be mis-trimmed away.
    _assert_math_safe(prefix)
    assert "\\$5" in prefix


def test_stem_digest_placeholder_kept_for_empty_stem() -> None:
    assert stem_digest("") == "（题干待补充）"
    assert stem_digest(None) == "（题干待补充）"


def test_question_display_title_uses_safe_digest() -> None:
    class _Q:  # minimal duck-typed stand-in for QuestionItem
        id = 1
        question_kind = "error"
        question_number = ""
        stem_text = FIXTURE_STEMS["limit"]

    title = question_display_title(_Q())
    assert "\\lim" not in title
    assert "$" not in title
    assert "错题" in title
