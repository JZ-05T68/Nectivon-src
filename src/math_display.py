"""Read-mode rendering for AI/transcript content that may contain math.

Product contract (visual red team R2, 2026-09-27):

- READ surfaces must display math the way a student reads it — rendered
  formulas, not LaTeX source.  The transcripts produced by handwriting
  vision routinely contain ``$...$`` / ``$$...$$`` LaTeX mixed with
  markdown; Streamlit's ``st.markdown`` renders both (KaTeX) natively and
  does NOT allow raw HTML unless ``unsafe_allow_html=True``, so this is
  XSS-safe by default.
- EDIT surfaces (``st.text_area``) intentionally keep the raw source so
  the user can correct it; they must not be switched to this renderer.
- Malformed LaTeX degrades to visible source instead of breaking the
  page — an honest, readable-enough fallback.

One shared renderer, not per-page regex patches (§15 architecture rule).
"""

from __future__ import annotations

import re

import streamlit as st

_SUBSCRIPT_TRANSLATION = str.maketrans("0123456789+-", "₀₁₂₃₄₅₆₇₈₉₊₋")
_SUPERSCRIPT_TRANSLATION = str.maketrans("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")
_MHCHEM_COMMAND = re.compile(r"\\ce\s*\{")
_EXPLICIT_SUBSCRIPT = re.compile(r"_\{([0-9]+)\}|_([0-9]+)")
_EXPLICIT_CHARGE = re.compile(r"\^\{([0-9]*[+-])\}|\^([0-9]*[+-])")
_PLAIN_FORMULA_NUMBER = re.compile(r"(?<=[A-Za-z\)\]])([0-9]+)")
_EXISTING_MATH_BLOCK = re.compile(r"(\$\$.*?\$\$|\$.*?\$)", re.DOTALL)
_LINE_START_NEGATIVE_NUMBER = re.compile(
    r"(?m)^(?P<indent>[ \t]*)-(?=[ \t]+(?:[\u2212-][ \t]*)?\d)"
)
_PLAIN_SCHOOL_MATH = re.compile(
    r"(?P<mixed>(?<![A-Za-z0-9_/])[+-]?\d+\s+\d+/\d+(?![A-Za-z0-9_/]))"
    r"|(?P<fraction>(?<![A-Za-z0-9_/])[+-]?\d+/[+-]?\d+(?![A-Za-z0-9_/]))"
    r"|(?P<power>(?:\([^()\n]+\)|[A-Za-z]|[+-]?\d+)\^(?:\{[+-]?\d+\}|[+-]?\d+))"
)
_CHOICE_LABEL = re.compile(r"(?<![A-Za-z0-9])([A-D])[.．][ \t]*")
_QUESTION_EXPRESSION = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:\|[^|\n]{1,24}\||[+\-]?\([A-Za-z0-9+\-*/×÷]{1,60}\)|[A-Za-z]{1,4}|[+\-]?\d+[A-Za-z]?)"
    r"(?:[ \t]*[+\-×÷*/=≤≥<>^][ \t]*"
    r"(?:\|[^|\n]{1,24}\||[+\-]?\([A-Za-z0-9+\-*/×÷]{1,60}\)|[+\-]?[A-Za-z]{1,4}|[+\-]?\d+[A-Za-z]?))+"
)
_QUESTION_ABSOLUTE = re.compile(r"\|[A-Za-z0-9+\-]{1,24}\|")
_QUESTION_COMPLEX_ABSOLUTE = re.compile(
    r"\|(?=[A-Za-z0-9(+-])[^|，,。；;：:\n]{1,100}"
    r"[()÷/][^|，,。；;：:\n]{0,100}\|"
)
_QUESTION_MONOMIAL = re.compile(r"(?<![A-Za-z0-9])[+\-]?\d+(?:\.\d+)?[A-Za-z]{1,3}(?![A-Za-z0-9])")
_QUESTION_LETTER = re.compile(r"(?<![A-Za-z\\])([A-Za-z]{1,3})(?![A-Za-z.])")
_ABSOLUTE_FRACTION = re.compile(r"\|([A-Za-z][A-Za-z0-9]*)\|/([A-Za-z][A-Za-z0-9]*)")
_PAREN_FRACTION = re.compile(r"\(([^()]{1,60})\)[÷/]([A-Za-z0-9]{1,12})")
_LETTER_FRACTION = re.compile(r"(?<![A-Za-z0-9])([A-Za-z])/([A-Za-z0-9]{1,12})")
_NUMERIC_FRACTION = re.compile(r"(?<![A-Za-z0-9])([+\-]?\d+)[÷/]([+\-]?\d+)")
_QUESTION_NUMBER = re.compile(r"(?<![A-Za-z0-9])([+\-]?\d+(?:\.\d+)?)(?![A-Za-z0-9])")


def format_multiple_choice_lines(text: str) -> str:
    """Put a real A–D option set on separate lines without editing stored OCR."""

    source = str(text or "")
    matches = list(_CHOICE_LABEL.finditer(source))
    if len(matches) < 2 or matches[0].group(1) != "A":
        return source
    # Require ordered labels so an incidental abbreviation in prose is not split.
    labels = [match.group(1) for match in matches]
    if labels != list("ABCD"[: len(labels)]):
        return source
    pieces = [source[: matches[0].start()].rstrip()]
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        option = source[match.start() : end].strip()
        pieces.append(option)
    return "\n\n".join(pieces)


def _question_expression_to_latex(value: str) -> str:
    """Apply notation-only rewrites; never infer a value or change an operator."""

    normalized = re.sub(r"\s+", "", value)
    normalized = _ABSOLUTE_FRACTION.sub(
        lambda match: f"\\dfrac{{|{match.group(1)}|}}{{{match.group(2)}}}",
        normalized,
    )
    normalized = _PAREN_FRACTION.sub(
        lambda match: f"\\dfrac{{{match.group(1)}}}{{{match.group(2)}}}",
        normalized,
    )
    normalized = _LETTER_FRACTION.sub(
        lambda match: f"\\dfrac{{{match.group(1)}}}{{{match.group(2)}}}",
        normalized,
    )
    normalized = _NUMERIC_FRACTION.sub(
        lambda match: (
            f"-\\dfrac{{{match.group(1)[1:]}}}{{{match.group(2)}}}"
            if match.group(1).startswith("-")
            else f"\\dfrac{{{match.group(1).lstrip('+')}}}{{{match.group(2)}}}"
        ),
        normalized,
    )
    normalized = normalized.replace("×", "\\times ").replace("÷", "\\div ")
    normalized = normalized.replace("≤", "\\leq ").replace("≥", "\\geq ")
    normalized = re.sub(r"\^([+\-]?\d+)", r"^{\1}", normalized)
    return normalized


def _normalize_existing_question_math(block: str) -> str:
    """Typeset unambiguous divisions inside already delimited math spans."""

    delimiter = "$$" if block.startswith("$$") else "$"
    value = block[len(delimiter) : -len(delimiter)].replace(r"\frac", r"\dfrac")
    value = re.sub(
        r"\(([^()]{1,60})\)\s*\\div\s*([A-Za-z0-9]{1,12})",
        lambda match: f"\\dfrac{{{match.group(1)}}}{{{match.group(2)}}}",
        value,
    )
    value = re.sub(
        r"(?<![A-Za-z0-9])([A-Za-z0-9]+)\s*\\div\s*([A-Za-z0-9]+)",
        lambda match: f"\\dfrac{{{match.group(1)}}}{{{match.group(2)}}}",
        value,
    )
    return f"{delimiter}{value}{delimiter}"


def normalize_question_math(text: str) -> str:
    """Render school-math tokens as KaTeX on question read views only.

    Original OCR and the human edit boxes remain byte-for-byte unchanged.
    Ambiguous mathematics is never solved here; unsupported spans stay visible.
    """

    def plain_chunk(chunk: str) -> str:
        protected: list[str] = []

        def save_math(latex: str) -> str:
            protected.append(f"${latex}$")
            return chr(0xE000 + len(protected) - 1)

        chunk = re.sub(
            r"△([A-Z]{3})",
            lambda match: save_math(f"\\triangle {match.group(1)}"),
            chunk,
        )
        chunk = _QUESTION_COMPLEX_ABSOLUTE.sub(
            lambda match: save_math(_question_expression_to_latex(match.group(0))),
            chunk,
        )
        chunk = _QUESTION_EXPRESSION.sub(
            lambda match: save_math(_question_expression_to_latex(match.group(0))),
            chunk,
        )
        chunk = _QUESTION_ABSOLUTE.sub(
            lambda match: save_math(match.group(0)), chunk
        )
        chunk = _QUESTION_MONOMIAL.sub(
            lambda match: save_math(match.group(0)), chunk
        )
        chunk = re.sub(r"=(?=_{2,})", lambda _match: save_math("="), chunk)
        chunk = _QUESTION_LETTER.sub(
            lambda match: (
                match.group(0)
                if match.group(0) in {"AI", "PDF", "OCR"}
                else save_math(match.group(0))
            ),
            chunk,
        )
        chunk = _QUESTION_NUMBER.sub(
            lambda match: save_math(match.group(0)), chunk
        )
        for index, math in enumerate(protected):
            chunk = chunk.replace(chr(0xE000 + index), math)
        return chunk.replace("$$", "")

    # A malformed AI/user fragment such as ``-2$$c &#x20;`` is not a display
    # equation.  Remove only this exact impossible in-word delimiter and the
    # observed encoded space; preserve the editable source unchanged.
    display_source = str(text or "").replace("&#x20;", " ")
    display_source = re.sub(r"(?<=[0-9])\$\$(?=[A-Za-z])", "", display_source)
    choices = format_multiple_choice_lines(display_source)
    parts = _EXISTING_MATH_BLOCK.split(choices)
    return "".join(
        _normalize_existing_question_math(part)
        if _EXISTING_MATH_BLOCK.fullmatch(part) else plain_chunk(part)
        for part in parts
    )


def render_question_math_markdown(text: str) -> None:
    """Read-only first-layer renderer with option layout and mathematical type."""

    st.markdown(
        normalize_plain_school_math(normalize_question_math(normalize_chemistry_markdown(text)))
    )


def _unicode_chemical_formula(source: str) -> str:
    """Conservatively turn one mhchem payload into copy-safe plain text.

    Streamlit's bundled KaTeX build does not load the optional mhchem
    extension.  Leaving ``\\ce{...}`` in the page therefore exposes source or
    a partial parse.  This fallback intentionally handles only notation whose
    semantics can be preserved deterministically; it never balances or invents
    a chemical equation.
    """

    value = str(source).strip()
    value = (
        value.replace("\\rightleftharpoons", "⇌")
        .replace("\\leftrightharpoons", "⇌")
        .replace("\\longrightarrow", "→")
        .replace("\\rightarrow", "→")
        .replace("\\longleftarrow", "←")
        .replace("\\leftarrow", "←")
        .replace("<=>", "⇌")
        .replace("<->", "⇌")
        .replace("->", "→")
        .replace("<-", "←")
        .replace("\\cdot", "·")
        .replace("*", "·")
        .replace("\\downarrow", "↓")
        .replace("\\uparrow", "↑")
    )
    value = _EXPLICIT_CHARGE.sub(
        lambda match: (match.group(1) or match.group(2)).translate(
            _SUPERSCRIPT_TRANSLATION
        ),
        value,
    )
    value = _EXPLICIT_SUBSCRIPT.sub(
        lambda match: (match.group(1) or match.group(2)).translate(
            _SUBSCRIPT_TRANSLATION
        ),
        value,
    )
    # mhchem permits ordinary formula digits (H2O, [Cu(C2O4)2]^2-).  A
    # leading stoichiometric coefficient has no element/group immediately to
    # its left, so it stays baseline while atom/group counts become subscripts.
    value = _PLAIN_FORMULA_NUMBER.sub(
        lambda match: match.group(1).translate(_SUBSCRIPT_TRANSLATION), value
    )
    return value.replace("{", "").replace("}", "")


def _balanced_argument(text: str, opening_brace: int) -> tuple[str, int] | None:
    depth = 0
    for index in range(opening_brace, len(text)):
        character = text[index]
        if character == "{" and (index == 0 or text[index - 1] != "\\"):
            depth += 1
        elif character == "}" and (index == 0 or text[index - 1] != "\\"):
            depth -= 1
            if depth == 0:
                return text[opening_brace + 1 : index], index + 1
    return None


def normalize_chemistry_markdown(text: str) -> str:
    """Remove unsupported mhchem commands without changing stored content.

    The transformation runs at display time, so returning to or reloading a
    page is stable.  Plain Unicode is deliberate: it keeps subscripts, charges,
    hydrate dots and arrows copyable even when mhchem is unavailable.  An
    unclosed command fails closed with a visible warning and never leaks a raw
    ``\\ce`` token to the page.
    """

    source = str(text or "")
    output: list[str] = []
    cursor = 0
    while True:
        match = _MHCHEM_COMMAND.search(source, cursor)
        if match is None:
            output.append(source[cursor:].replace("\\ce", "[化学式标记无法解析]"))
            break
        opening_brace = match.end() - 1
        parsed = _balanced_argument(source, opening_brace)
        if parsed is None:
            output.append(source[cursor : match.start()])
            remainder = source[match.end() :].strip()
            fallback = _unicode_chemical_formula(remainder)
            output.append(
                f"[化学式源码不完整：{fallback}]" if fallback else "[化学式源码不完整]"
            )
            break
        payload, parsed_end = parsed
        prefix = source[cursor : match.start()]
        # A standalone ``$\\ce{...}$`` / ``$$\\ce{...}$$`` must leave math
        # mode entirely: the fallback already carries real Unicode scripts,
        # and keeping the dollar pair would make KaTeX reinterpret it.
        if prefix.endswith("$$") and source.startswith("$$", parsed_end):
            prefix = prefix[:-2]
            parsed_end += 2
        elif prefix.endswith("$") and source.startswith("$", parsed_end):
            prefix = prefix[:-1]
            parsed_end += 1
        output.append(prefix)
        output.append(_unicode_chemical_formula(payload))
        cursor = parsed_end
    return "".join(output)


def normalize_plain_school_math(text: str) -> str:
    """Turn common plain OCR math into student-facing KaTeX notation.

    The stored/editable text remains unchanged.  Only read surfaces convert
    ``3/4`` to a stacked fraction, ``-3 1/6`` to a mixed number, and
    ``(-3)^2`` to a real superscript.  Existing ``$...$`` math is skipped so
    valid LaTeX is never wrapped twice.
    """

    def replace_plain_chunk(chunk: str) -> str:
        def replace_match(match: re.Match[str]) -> str:
            token = match.group(0)
            if match.group("mixed") is not None:
                whole, fraction = token.split(maxsplit=1)
                numerator, denominator = fraction.split("/", maxsplit=1)
                return f"${whole}\\frac{{{numerator}}}{{{denominator}}}$"
            if match.group("fraction") is not None:
                numerator, denominator = token.split("/", maxsplit=1)
                sign = ""
                if numerator.startswith(("+", "-")):
                    sign, numerator = numerator[0], numerator[1:]
                return f"${sign}\\frac{{{numerator}}}{{{denominator}}}$"
            base, exponent = token.rsplit("^", maxsplit=1)
            exponent = exponent.removeprefix("{").removesuffix("}")
            return f"${base}^{{{exponent}}}$"

        return _PLAIN_SCHOOL_MATH.sub(replace_match, chunk)

    # OCR frequently wraps a negative-number sequence onto a new line, for
    # example ``- 8, - 4``.  CommonMark interprets the first hyphen as a list
    # marker, so students see a bullet and can reasonably think the sign was
    # lost.  Escape only line-leading hyphens whose following token is numeric;
    # ordinary prose lists remain real lists.
    markdown_safe = _LINE_START_NEGATIVE_NUMBER.sub(
        lambda match: f"{match.group('indent')}\\-", str(text or "")
    )
    parts = _EXISTING_MATH_BLOCK.split(markdown_safe)
    return "".join(
        part if _EXISTING_MATH_BLOCK.fullmatch(part) else replace_plain_chunk(part)
        for part in parts
    )


def render_math_markdown(text: str) -> None:
    """Render read-only content with real math (LaTeX via KaTeX).

    Use for every READ surface that shows AI-generated or transcribed
    content (source transcripts, secondary conclusions, method notes).
    Never use for editable fields.
    """

    chemistry_safe = normalize_chemistry_markdown(text)
    st.markdown(normalize_plain_school_math(chemistry_safe))
