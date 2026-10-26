"""Deterministic plain-text → LaTeX conversion for student math input (P1-3).

Students correcting one symbol should not retype a whole formula.  This
module converts the small math fragments they actually type — ``omega_n``,
``K_p``, ``3.2e-3``, ``sqrt(p1-p2)`` — into LaTeX source that KaTeX renders,
entirely offline and without any AI call.

Safety contract (P1-3C):

- Conversion is opt-in per fragment: callers feed a math snippet or a
  find/replace fragment, never a whole prose stem.  Ordinary English words
  and numbers are therefore never rewritten by accident.
- Pasted LaTeX (input containing ``\\`` commands or ``$``) passes through
  with only subscript/superscript bracing.
- Ambiguous subscripts return every defensible reading so the UI can ask
  the user instead of silently changing meaning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.question_recognition_rules import LATEX_SYMBOLS

# Greek letter names students type, mapped to the same commands the project
# already uses for rendering (see question_recognition_rules.LATEX_SYMBOLS).
_GREEK_COMMANDS: dict[str, str] = {
    "alpha": r"\alpha", "beta": r"\beta", "gamma": r"\gamma",
    "delta": r"\delta", "epsilon": r"\varepsilon", "varepsilon": r"\varepsilon",
    "zeta": r"\zeta", "eta": r"\eta", "theta": r"\theta", "vartheta": r"\vartheta",
    "iota": r"\iota", "kappa": r"\kappa", "lambda": r"\lambda",
    "mu": r"\mu", "nu": r"\nu", "xi": r"\xi", "pi": r"\pi",
    "rho": r"\rho", "varrho": r"\varrho", "sigma": r"\sigma", "varsigma": r"\varsigma",
    "tau": r"\tau", "upsilon": r"\upsilon", "phi": r"\varphi",
    "varphi": r"\varphi", "chi": r"\chi", "psi": r"\psi", "omega": r"\omega",
    "Gamma": r"\Gamma", "Delta": r"\Delta", "Theta": r"\Theta",
    "Lambda": r"\Lambda", "Xi": r"\Xi", "Pi": r"\Pi", "Sigma": r"\Sigma",
    "Upsilon": r"\Upsilon", "Phi": r"\Phi", "Psi": r"\Psi", "Omega": r"\Omega",
}
_GREEK_PATTERN = "|".join(sorted(_GREEK_COMMANDS, key=len, reverse=True))
_GREEK_NAME = re.compile(rf"(?<![A-Za-z\\])({_GREEK_PATTERN})(?![A-Za-z])")

_FUNCTIONS = "sin|cos|tan|cot|sec|csc|arcsin|arccos|arctan|sinh|cosh|tanh|log|ln|exp|lim"
_FUNCTION_NAME = re.compile(rf"(?<![A-Za-z\\])({_FUNCTIONS})(?![A-Za-z])")

# SI units for engineering exercise input; subject-neutral by design.
# ``\\Omega`` forms appear after typed Ω has already become a LaTeX command.
_UNITS = (
    r"k\\Omega|M\\Omega|\\Omega|mm|cm|km|kg|mg|ms|kN|MPa|kPa|Hz|kHz|MHz|"
    r"kΩ|MΩ|μF|nF|pF|mH|μH|mA|kV|mV|kW|mW|rpm|m|s|N|Pa|V|A|W|J|F|H|Ω|C|K|T|L"
)

_SCI_NOTATION = re.compile(
    r"(?<![A-Za-z0-9_])(\d+(?:\.\d+)?)[eE]([+-]?\d+)(?![A-Za-z0-9_])"
)
_SCI_TIMES_TEN = re.compile(
    r"(?<![A-Za-z0-9_])(\d+(?:\.\d+)?)\s*[×x\*]\s*10\s*\^?\s*\{?\(?"
    r"([+-]?\d+)\)?\}?(?![A-Za-z0-9_])"
)
_SQRT_CALL = re.compile(r"sqrt\s*\(([^()]*)\)")
_UNIT_AFTER_NUMBER = re.compile(
    rf"(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>{_UNITS})"
    r"(?P<exp>\^\{?[+-]?\d+\}?|(?P<uni>[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+))?(?![A-Za-z0-9_])"
)
_UNICODE_SUPER = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
# Longest symbol first so 「μF」 wins over 「μ」 during replacement.
_UNICODE_MATH = sorted(LATEX_SYMBOLS.items(), key=lambda item: -len(item[0]))
_SCRIPT_RUN = r"[+-]?(?:[0-9]+|[A-Za-z][0-9A-Za-z]*)"


@dataclass(slots=True)
class MathConversion:
    """Result of one fragment conversion."""

    latex: str
    candidates: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ambiguous(self) -> bool:
        return len(self.candidates) > 1


def _unicode_math(value: str) -> str:
    """Rewrite typed Unicode math glyphs to LaTeX commands."""

    for symbol, command in _UNICODE_MATH:
        if symbol in value:
            following = value[value.index(symbol) + len(symbol):][:1]
            # A letter directly after a command would extend its name
            # (\pi r → \pir); a digit terminates it safely, so no space.
            value = value.replace(symbol, command + (" " if following.isalpha() else ""))
    return value


def _brace_scripts(token: str) -> str:
    """Brace bare ``_x`` / ``^x`` scripts, keeping the operand verbatim."""

    token = re.sub(r"_\(([^()]+)\)", r"_{\1}", token)
    token = re.sub(rf"_\{{({_SCRIPT_RUN})\}}", r"_{\1}", token)
    token = re.sub(rf"_({_SCRIPT_RUN})", r"_{\1}", token)
    token = re.sub(r"\^\(([^()]+)\)", r"^{\1}", token)
    token = re.sub(rf"\^\{{({_SCRIPT_RUN})\}}", r"^{\1}", token)
    token = re.sub(rf"\^({_SCRIPT_RUN})", r"^{\1}", token)
    return token


def _convert_token(token: str) -> str:
    """Apply notation-only rewrites inside one math-looking fragment."""

    value = _unicode_math(token)
    value = _brace_scripts(value)
    value = _GREEK_NAME.sub(lambda match: _GREEK_COMMANDS[match.group(1)], value)
    value = _SQRT_CALL.sub(lambda match: f"\\sqrt{{{match.group(1).strip()}}}", value)
    value = _FUNCTION_NAME.sub(lambda match: "\\" + match.group(1) + " ", value)
    if value.count("{") > value.count("}"):
        value += "}" * (value.count("{") - value.count("}"))
    return value


def _fractions(value: str) -> str:
    """Typeset ``a/b`` for simple operands only; prose slashes stay intact."""

    operand = r"[A-Za-z0-9\\{}^_()+.-]{1,24}"

    def strip_grouping(text: str) -> str:
        # ``(d^{2}x)`` is pure grouping around the numerator/denominator.
        while text.startswith("(") and text.endswith(")") and text.count("(") == 1:
            inner = text[1:-1]
            if "(" in inner or ")" in inner:
                break
            text = inner
        return text

    return re.sub(
        rf"({operand})\s*/\s*({operand})",
        lambda match: (
            f"\\dfrac{{{strip_grouping(match.group(1))}}}{{{strip_grouping(match.group(2))}}}"
            if re.fullmatch(operand, match.group(1))
            and re.fullmatch(operand, match.group(2))
            else match.group(0)
        ),
        value,
    )


def _ambiguous_script_candidates(value: str) -> list[str]:
    """Offer grouped-subscript readings for ``x_i+1`` style input.

    The default conversion keeps ``+1`` outside the subscript (meaning
    preserved).  When the pattern allows a compound subscript, surface it as
    an explicit alternative instead of choosing silently.
    """

    grouped = re.sub(
        r"_\{([A-Za-z])\}\s*\+\s*(\d+)",
        lambda match: f"_{{{match.group(1)}+{match.group(2)}}}",
        value,
    )
    return [value] if grouped == value else [value, grouped]


def _format_unit(match: re.Match[str]) -> str:
    """Typeset a number+unit pair as ``n\\,\\mathrm{u}^{e}``."""

    exponent = match.group("exp") or match.group("uni") or ""
    if match.group("uni"):
        exponent = "^{" + match.group("uni").translate(_UNICODE_SUPER) + "}"
    elif exponent and not exponent.startswith("^{"):
        exponent = "^{" + exponent.lstrip("^").strip("{}") + "}"
    return f"{match.group('number')}\\,\\mathrm{{{match.group('unit')}}}{exponent}"


def plain_math_to_latex(fragment: str, *, aggressive: bool = True) -> MathConversion:
    """Convert one student-typed math fragment to LaTeX source.

    ``aggressive=False`` only performs safe notation fixes (greek names,
    script bracing); fractions, scientific notation and units are skipped so
    callers can stay conservative on ambiguous input.
    """

    text = str(fragment or "").strip()
    if not text:
        return MathConversion("")
    if "$" in text:
        # Pasted LaTeX/markdown math: keep it exactly, KaTeX renders as-is.
        return MathConversion(text, notes=["检测到 $ 定界，按已粘贴的 LaTeX 原样保留。"])
    if re.search(r"\\[A-Za-z]+", text):
        # Mixed plain + pasted LaTeX: brace scripts and convert only bare
        # greek NAMES — an existing command (\omega) is never re-touched.
        return MathConversion(
            _GREEK_NAME.sub(
                lambda match: _GREEK_COMMANDS[match.group(1)], _brace_scripts(text)
            ),
            notes=["检测到 LaTeX 命令，仅补全上下标花括号并转换裸希腊字母名。"],
        )

    notes: list[str] = []
    converted = "".join(
        part if index % 2 else _convert_token(part)
        for index, part in enumerate(re.split(r"(\s+)", text))
    )
    if aggressive:
        before_sci = converted
        converted = _SCI_TIMES_TEN.sub(
            lambda match: f"{match.group(1)}\\times10^{{{match.group(2)}}}", converted,
        )
        converted = _SCI_NOTATION.sub(
            lambda match: f"{match.group(1)}\\times10^{{{match.group(2)}}}", converted,
        )
        if converted != before_sci:
            notes.append("科学计数法已转换为 \\times10^{…} 形式。")
        before_frac = converted
        converted = _fractions(converted)
        if converted != before_frac:
            notes.append("斜杠分式已转换为 \\dfrac。")
        converted = _UNIT_AFTER_NUMBER.sub(_format_unit, converted)

    candidates = _ambiguous_script_candidates(converted)
    if len(candidates) > 1:
        notes.append("下标存在两种可能读法，请确认数学含义后选择。")
    return MathConversion(converted, candidates=candidates, notes=notes)


def wrap_inline(latex: str) -> str:
    """Wrap LaTeX source in the inline delimiters the renderer expects."""

    value = str(latex or "").strip()
    if not value:
        return ""
    if value.startswith("$"):
        return value
    return f"${value}$"


SNIPPET_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("ω", r"\omega"), ("α", r"\alpha"), ("β", r"\beta"), ("θ", r"\theta"),
    ("σ", r"\sigma"), ("ε", r"\varepsilon"), ("τ", r"\tau"), ("λ", r"\lambda"),
    ("ζ", r"\zeta"), ("φ", r"\varphi"), ("Δ", r"\Delta"), ("Ω", r"\Omega"),
    ("π", r"\pi"), ("μ", r"\mu"), ("ρ", r"\rho"), ("ξ", r"\xi"),
    ("x²", "^{2}"), ("xⁿ", "^{n}"), ("xₙ", "_{n}"), ("xᵢ₊₁", "_{i+1}"),
    ("a/b", r"\dfrac{a}{b}"), ("√x", r"\sqrt{x}"), ("×10⁻³", r"\times10^{-3}"),
    ("≤", r"\leq"), ("≥", r"\geq"), ("≈", r"\approx"), ("±", r"\pm"),
    ("·", r"\cdot"), ("∂", r"\partial"), ("∫", r"\int_{}^{}"), ("∑", r"\sum"),
    ("→", r"\to"), ("°", r"^{\circ}"), ("Ω单位", r"\,\mathrm{\Omega}"), ("d/dt", r"\dfrac{d}{dt}"),
)
