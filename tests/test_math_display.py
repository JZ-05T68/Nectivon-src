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


def test_doubled_ai_command_escapes_render_as_commands_without_changing_plain_text() -> None:
    source = r"公式 $2\\times3+\\dfrac{1}{2}+\\sin\\alpha$，目录 C:\\times。"
    normalized = math_display.normalize_math_delimiters(source)
    assert normalized == r"公式 $2\times3+\dfrac{1}{2}+\sin\alpha$，目录 C:\\times。"
    assert math_display.normalize_math_delimiters(normalized) == normalized


def test_matrix_rows_and_row_followed_by_command_keep_their_backslashes() -> None:
    source = r"$\begin{matrix}1 & 2\\\frac{1}{2} & 4\\5 & 6\end{matrix}$"
    assert math_display.normalize_math_delimiters(source) == source


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


def test_unicode_power_and_roots_are_typeset_without_changing_source() -> None:
    source = "已知(a+1)²+|b+5|=b+5，m²=9，求√(a+1)、√2、π。"
    rendered = math_display.normalize_question_math(source)
    assert r"$(a+1)^{2}+|b+5|=b+5$" in rendered
    assert r"$m^{2}$" in rendered
    assert r"$\sqrt{a+1}$" in rendered
    assert r"$\sqrt{2}$" in rendered
    assert r"$\pi$" in rendered
    assert "²" in source


@pytest.mark.parametrize("separator", [".", "．", "、", "：", ")"])
def test_long_choice_options_have_individual_paragraphs(separator: str) -> None:
    options = [f"{label}{separator} " + (f"选项{label}的完整说明。" * 15) for label in "ABCD"]
    source = "请选择正确说法（）。" + " ".join(options)
    rendered = math_display.format_multiple_choice_lines(source)
    assert rendered.split("\n\n") == ["请选择正确说法（）。", *options]


def test_option_like_labels_inside_existing_math_are_not_split() -> None:
    source = r"说明 $\text{A. B. C. D.}$。 A. 甲 B. 乙 C. 丙 D. 丁"
    rendered = math_display.format_multiple_choice_lines(source)
    assert rendered.startswith(r"说明 $\text{A. B. C. D.}$。" + "\n\nA. 甲")


def test_adjacent_pi_letter_spans_keep_valid_latex_commands() -> None:
    rendered = math_display.normalize_question_math("周长为2πr。")
    assert r"\pi r" in rendered
    assert r"\pir" not in rendered


def test_abbreviations_within_long_english_choices_do_not_break_option_layout() -> None:
    source = "Choose a sentence. A. This refers to U.S.A. B. Second sentence. C. Third. D. Fourth."
    rendered = math_display.format_multiple_choice_lines(source)
    assert "\n\nA. This refers to U.S.A.\n\nB. Second sentence." in rendered


@pytest.mark.parametrize(("source", "expected"), [
    ("x^2", "$x^{2}$"), ("(x+1)^3", "$(x+1)^{3}$"),
    ("a^n", "$a^{n}$"), ("x^{n+1}", "$x^{n+1}$"),
    ("(x+1)^(n+1)", "$(x+1)^{n+1}$"), ("10^-3", "$10^{-3}$"),
])
def test_typed_caret_exponent_renders_immediately(source: str, expected: str) -> None:
    assert math_display.normalize_question_math(source) == expected


def test_advanced_latex_from_first_recognition_remains_complete() -> None:
    source = (
        r"计算 $\sin\alpha+\cos\theta$、$\int_{0}^{1}x^{2}\,\mathrm{d}x$、"
        r"$\lim_{n\to\infty}\dfrac{1}{n}$、$\sum_{k=1}^{n}k$、"
        r"$\begin{pmatrix}a&b\\c&d\end{pmatrix}$。"
    )
    assert math_display.normalize_question_math(source) == source


def test_plain_functions_greek_and_integral_use_one_math_span() -> None:
    result = math_display.normalize_question_math("求sinα+cosθ以及∫_0^1 x^2 dx。")
    assert r"$\sin \alpha +\cos \theta$" in result
    assert r"$\int _{0}^{1} x^{2} dx$" in result


def test_standard_latex_delimiters_are_accepted_by_katex() -> None:
    result = math_display.normalize_question_math(
        r"计算 \(\sin\alpha+x^{2}\)，以及 \[\int_0^1 x^2\,dx\]。"
    )
    assert r"$\sin\alpha+x^{2}$" in result
    assert r"$$\int_{0}^{1} x^{2}\,dx$$" in result
    assert r"\(" not in result and r"\[" not in result
def test_explicit_grouped_math_indices_are_not_split_into_baseline_text():
    from src.math_display import normalize_question_math

    rendered = normalize_question_math(r'$\sum_(k=1)^n a_2023+\alpha_(i+1)$')
    assert r'\sum_{k=1}^{n}' in rendered
    assert 'a_{2023}' in rendered
    assert r'\alpha_{i+1}' in rendered


def test_adjacent_inline_and_display_equations_remain_separate_and_idempotent():
    source = r"先看 $x=1$$y=2$，第2步$$\alpha^{2}+\beta^{2}=1$$结束。"
    display = math_display.normalize_question_math(source)
    assert "$x=1$ $y=2$" in display
    assert "\n\n" + r"$$\alpha^{2}+\beta^{2}=1$$" + "\n\n" in display
    assert math_display.normalize_question_math(display) == display


def test_display_layout_escapes_do_not_show_literal_newline_commands():
    source = r"结果：$$\n\nu+x^2\n$$。"
    display = math_display.normalize_question_math(source)
    assert r"$$\nu+x^{2}$$" in display
    assert r"\n\nu" in source
    assert math_display.normalize_question_math(display) == display


@pytest.mark.parametrize("source", ["(a+1)/(b-2)", "$(a+1)/(b-2)$"])
def test_typed_fraction_retains_both_operand_groups_after_save(source: str):
    assert math_display.normalize_question_math(source) == r"$\dfrac{a+1}{b-2}$"


def test_typed_fraction_with_a_grouped_denominator_remains_equivalent():
    assert math_display.normalize_question_math("a/(b+1)") == r"$\dfrac{a}{b+1}$"
