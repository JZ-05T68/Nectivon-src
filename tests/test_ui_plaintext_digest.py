"""R5.1: plain-UI digest — Markdown + LaTeX source → human-readable text.

Content-rendering contract (2026-09-27): rich READ surfaces render via
KaTeX, EDIT surfaces keep raw source, and every plain short-string UI
(selectors, card titles, history titles, candidate previews) must show
:func:`src.text_utils.ui_plaintext_digest` output — human-readable plain
math, no ``$`` delimiters, no backslash commands, no Markdown markers.
Malformed LaTeX degrades honestly and never raises.
"""

from __future__ import annotations

import pytest

from src.display_labels import question_display_title, stem_digest
from src.text_utils import contains_raw_markup, ui_plaintext_digest


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("$f'(1)=2$", "f′(1)=2"),
        ("$x^2$", "x²"),
        ("$x_1$", "x₁"),
        ("$x^{n+1}$", "xⁿ⁺¹"),
        ("$\\alpha+\\beta$", "α+β"),
        ("$h\\to0$", "h→0"),
        ("$x\\le 2$", "x≤ 2"),
        ("$x\\neq0$", "x≠0"),
        ("$\\sqrt{x}$", "√x"),
        ("$G(s)=\\frac{10}{s(s+2)}$", "G(s)=10/[s(s+2)]"),
        ("$\\frac{3}{7}$", "3/7"),
        ("$\\frac{f(1+h)-f(1)}{h}$", "[f(1+h)-f(1)]/h"),
        ("$\\lim_{h\\to0}$", "lim(h→0)"),
        ("$\\int_0^1 x^2\\,dx$", "∫₀¹ x² dx"),
        ("$\\sum_{i=1}^{n} i$", "Σ(i=1→n) i"),
        ("$\\omega_n=5$", "ωₙ=5"),
        ("$\\zeta=0.6$", "ζ=0.6"),
        ("$60^{\\circ}$", "60°"),
        ("$i\\in\\mathbb{N}^*$", "i∈ℕ^(*)"),
        ("$A(-1,0)$", "A(-1,0)"),
    ],
)
def test_single_math_expressions_convert_faithfully(source: str, expected: str) -> None:
    assert ui_plaintext_digest(source) == expected


def test_cases_environment_becomes_readable_rows() -> None:
    digest = ui_plaintext_digest(
        "$f(x)=\\begin{cases}x^2,&x\\ge0\\\\-x,&x<0\\end{cases}$"
    )
    assert digest == "f(x)=分段：x²，x≥0；-x，x<0"


def test_control_theory_closed_loop_formula() -> None:
    digest = ui_plaintext_digest("$\\Phi(s)=\\frac{G(s)}{1+G(s)H(s)}$")
    assert digest == "Φ(s)=G(s)/[1+G(s)H(s)]"


def test_mixed_chinese_and_math_keeps_prose() -> None:
    digest = ui_plaintext_digest(
        "已知 $f'(1)=2$，求 $\\lim_{h\\to0}\\frac{f(1+h)-f(1)}{h}$ 的值。"
    )
    assert digest == "已知 f′(1)=2，求 lim(h→0)[f(1+h)-f(1)]/h 的值。"


def test_markdown_markers_are_stripped() -> None:
    assert ui_plaintext_digest("**重点**：证明 $x^2\\ge 0$") == "重点：证明 x²≥ 0"
    assert ui_plaintext_digest("### 标题\n正文") == "标题 正文"
    assert ui_plaintext_digest("[链接文字](http://x.local)") == "链接文字"
    assert ui_plaintext_digest("`code` 片段") == "code 片段"
    assert ui_plaintext_digest("> 引用行\n正文") == "引用行 正文"


def test_escaped_dollar_is_a_price_sign_not_a_delimiter() -> None:
    digest = ui_plaintext_digest("某商品打 \\$5 折后售价为 $x$ 元，求原价。")
    assert "$x$" not in digest
    assert "x 元" in digest
    assert "＄5" in digest


def test_malformed_latex_degrades_without_raising() -> None:
    digest = ui_plaintext_digest("由 OCR 得到 $\\frac{3}{ 与 $x^2$，请核对原页。")
    # The broken formula becomes an honest placeholder; the readable parts
    # survive; neither delimiters nor commands leak.
    assert "公式(可能有误)" in digest
    assert "$" not in digest
    assert "\\" not in digest
    assert "由 OCR 得到" in digest
    assert "请核对原页" in digest


def test_unmatched_close_brace_never_crashes() -> None:
    digest = ui_plaintext_digest("坏输入 $x^2}+1$ 与 $y$。")
    assert "y" in digest
    assert "\\" not in digest


def test_multiline_block_math_is_inline_converted() -> None:
    digest = ui_plaintext_digest(
        "求解：\n$$\\begin{cases}x+y=3\\\\x-y=1\\end{cases}$$\n并写出 $xy$ 的值。"
    )
    assert "$$" not in digest
    assert "x+y=3" in digest
    assert "xy" in digest


def test_plain_text_without_math_passes_through() -> None:
    assert ui_plaintext_digest("普通中文题干，没有公式。") == "普通中文题干，没有公式。"


def test_truncation_happens_after_conversion() -> None:
    source = "已知 $f'(1)=2$，求 $\\lim_{h\\to0}\\frac{f(1+h)-f(1)}{h}$ 的值。"
    digest = ui_plaintext_digest(source, 16)
    assert len(digest.rstrip("…")) <= 16
    assert digest.startswith("已知 f′(1)=2，")
    assert digest.endswith("…")
    # The old source form must never survive.
    assert "$" not in digest
    assert "\\lim" not in digest


def test_empty_inputs_stay_empty() -> None:
    assert ui_plaintext_digest("") == ""
    assert ui_plaintext_digest(None) == ""


@pytest.mark.parametrize(
    "source",
    [
        "错题 · 已知 $f'(1)=2$，求…",
        "典型题 · $G(s)=\\frac{10}{s(s+2)}$",
        "方法题 · $\\begin{cases}...",
        "积分 $\\int_0^1...",
        "**重点** · x",
        "\\frac{1}{2}",
        "$x$ 与 $y$",
    ],
)
def test_lint_flags_raw_markup(source: str) -> None:
    assert contains_raw_markup(source)


@pytest.mark.parametrize(
    "source",
    [
        "错题 · 已知 f′(1)=2，求极限…",
        "典型题 · 传递函数 G(s)=10/[s(s+2)]",
        "方法题 · 分段函数讨论",
        "好题 · 定积分计算",
        "打＄5 折的商品",
        "重点 · x²≥0",
    ],
)
def test_lint_accepts_converted_plain_text(source: str) -> None:
    assert not contains_raw_markup(source)


def test_stem_digest_now_renders_plain_math() -> None:
    digest = stem_digest("已知 $f'(1)=2$，求 $\\lim_{h\\to0}\\frac{f(1+h)-f(1)}{h}$ 的值。")
    assert "错题" not in digest  # kind prefix is added by display layer
    assert "已知 f′(1)=2" in digest
    assert contains_raw_markup(digest) is False


def test_question_display_title_is_plain_for_no_number_question() -> None:
    class _Q:  # minimal duck-typed stand-in for QuestionItem
        id = 1
        question_kind = "error"
        question_number = ""
        stem_text = "$G(s)=\\frac{10}{s(s+2)}$，求单位阶跃响应。"

    title = question_display_title(_Q())
    assert title.startswith("错题 · G(s)=10/[s(s+2)]")
    assert contains_raw_markup(title) is False
