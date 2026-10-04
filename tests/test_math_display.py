"""Read-mode math renderer contract (visual red team R2)."""

from __future__ import annotations

import pytest

from src import math_display


def test_render_math_markdown_uses_markdown_not_plain_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def _fake_markdown(text: str, *args: object, **kwargs: object) -> None:
        calls.append(text)

    monkeypatch.setattr(math_display.st, "markdown", _fake_markdown)
    math_display.render_math_markdown(
        "已知 $ f'(1)=2 $，则 $$ \\frac{f(1+h)-f(1)}{2h} \\to -1 $$"
    )
    assert len(calls) == 1
    assert "$ f'(1)=2 $" in calls[0]
    assert "\\frac" in calls[0]


def test_render_math_markdown_tolerates_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _fake_markdown(text: str, *args: object, **kwargs: object) -> None:
        calls.append(text)

    monkeypatch.setattr(math_display.st, "markdown", _fake_markdown)
    math_display.render_math_markdown("")
    assert calls == [""]


def test_plain_junior_math_uses_fraction_bars_and_superscripts() -> None:
    rendered = math_display.normalize_plain_school_math(
        "计算：-1^2021×[4-(-3)^2]+3÷(-3/4)，再算-3 1/6。"
    )

    assert "/" not in rendered
    assert "^2021" not in rendered
    assert "$-1^{2021}$" in rendered
    assert "$(-3)^{2}$" in rendered
    assert "$-\\frac{3}{4}$" in rendered
    assert "$-3\\frac{1}{6}$" in rendered


def test_plain_ai_math_with_braced_exponent_is_rendered() -> None:
    rendered = math_display.normalize_plain_school_math(
        "先算 -1^{2021}，再算 (-3)^{2}。"
    )

    assert "$-1^{2021}$" in rendered
    assert "$(-3)^{2}$" in rendered
    assert "-1^{2021}" not in rendered.replace("$-1^{2021}$", "")


def test_existing_latex_is_not_double_wrapped() -> None:
    source = r"已有 $\frac{3}{4}$，另有 5/6。"
    rendered = math_display.normalize_plain_school_math(source)

    assert rendered == r"已有 $\frac{3}{4}$，另有 $\frac{5}{6}$。"


def test_line_start_negative_numbers_do_not_become_markdown_lists() -> None:
    rendered = math_display.normalize_plain_school_math(
        "行程：+8，-6，+4，\n- 8, - 4, +3, +3."
    )

    assert "\n\\- 8, - 4" in rendered


def test_real_prose_markdown_list_is_preserved() -> None:
    rendered = math_display.normalize_plain_school_math("- 先算乘方\n- 再算乘除")

    assert rendered == "- 先算乘方\n- 再算乘除"


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        (r"$\ce{NH4+}$", "NH₄+"),
        (r"\ce{SO4^2-}", "SO₄²⁻"),
        (r"\ce{VO^2+} / \ce{VO2+}", "VO²⁺ / VO₂+"),
        (r"\ce{CaO2*8H2O}", "CaO₂·8H₂O"),
        (r"\ce{[Cu(C2O4)2]^{2-}}", "[Cu(C₂O₄)₂]²⁻"),
        (r"\ce{2H2 + O2 <=> 2H2O}", "2H₂ + O₂ ⇌ 2H₂O"),
    ),
)
def test_mhchem_is_deterministically_degraded_to_copy_safe_unicode(
    source: str, expected: str
) -> None:
    rendered = math_display.normalize_chemistry_markdown(source)
    assert rendered == expected
    assert r"\ce" not in rendered


def test_malformed_mhchem_fails_closed_without_raw_command() -> None:
    rendered = math_display.normalize_chemistry_markdown(r"答案：\ce{Fe3+")
    assert rendered == "答案：[化学式源码不完整：Fe₃+]"
    assert r"\ce" not in rendered


def test_standard_katex_chemistry_is_left_unchanged() -> None:
    source = (
        r"$\mathrm{SO}_4^{2-}$；$K_{sp}=2.6\times10^{-13}$；"
        r"$\xrightarrow{\text{50 ℃}}$；$\rightleftharpoons$"
    )
    assert math_display.normalize_chemistry_markdown(source) == source


def test_question_fraction_letters_and_options_render_without_raw_mix() -> None:
    rendered = math_display.normalize_question_math(
        "已知|a|=-a，|b|/b=-1，求x≤1的解（　）A. 1个　B. 2个　C. 3个　D. 4个"
    )
    assert r"$|a|=-a$" in rendered
    assert r"$\dfrac{|b|}{b}=-1$" in rendered
    assert r"$x\leq 1$" in rendered
    assert "\n\nA. $1$个\n\nB. $2$个\n\nC. $3$个\n\nD. $4$个" in rendered
    assert "|$a$|" not in rendered


def test_question_existing_latex_and_prose_are_preserved() -> None:
    source = r"原题 $\frac{|b|}{b}=-1$，请看图。"
    assert math_display.normalize_question_math(source) == source.replace(r"\frac", r"\dfrac")


def test_question_parenthesized_division_displays_stacked_fraction() -> None:
    rendered = math_display.normalize_question_math(
        "中点位置为(6+p)÷2，两点距离为|(6+p)÷2-(p-4)÷2|。"
    )
    assert r"$\dfrac{6+p}{2}$" in rendered
    assert r"$|\dfrac{6+p}{2}-\dfrac{p-4}{2}|$" in rendered
    assert r"\div" not in rendered
    assert math_display.normalize_question_math(r"$(6+p)\div2$") == r"$\dfrac{6+p}{2}$"


def test_manual_math_input_gets_immediate_read_mode_typesetting() -> None:
    rendered = math_display.normalize_question_math(
        "人工写的：|b|/b=-1，(a+1)^2，(6+p)/2，x≤1，结果 -2$$c &#x20;"
    )
    assert r"$\dfrac{|b|}{b}=-1$" in rendered
    assert r"$(a+1)^{2}$" in rendered
    assert r"$\dfrac{6+p}{2}$" in rendered
    assert r"$x\leq 1$" in rendered
    assert r"$-2c$" in rendered
    assert "$$" not in rendered
    assert "&#x20;" not in rendered


def test_manual_simple_letters_powers_and_numeric_division_use_math_type() -> None:
    rendered = math_display.normalize_question_math(
        "人工输入：a/b，a^2，|10÷2|，10÷2，(p-4)÷2"
    )
    assert r"$\dfrac{a}{b}$" in rendered
    assert r"$a^{2}$" in rendered
    assert r"$|\dfrac{10}{2}|$" in rendered
    assert r"$\dfrac{10}{2}$" in rendered
    assert r"$\dfrac{p-4}{2}$" in rendered
    assert r"\div" not in rendered


def test_adjacent_absolute_value_steps_do_not_merge_or_leak_placeholders() -> None:
    rendered = math_display.normalize_question_math(
        "分别去绝对值：|a+b|=-(a+b)，|a-c|=c-a，|b-c|=c-b。"
    )
    assert r"$|a+b|=-(a+b)$" in rendered
    assert r"$|a-c|=c-a$" in rendered
    assert r"$|b-c|=c-b$" in rendered
    assert "\ue000" not in rendered
