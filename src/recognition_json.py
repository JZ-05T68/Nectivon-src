"""Repair only known LaTeX escape mistakes in structured AI transcription."""

from __future__ import annotations

import re

from src.question_recognition_rules import LATEX_SYMBOLS

_COMMANDS = {
    "frac",
    "dfrac",
    "tfrac",
    "sqrt",
    "sin",
    "cos",
    "tan",
    "cot",
    "sec",
    "csc",
    "arcsin",
    "arccos",
    "arctan",
    "sinh",
    "cosh",
    "tanh",
    "log",
    "ln",
    "exp",
    "lim",
    "int",
    "iint",
    "iiint",
    "oint",
    "sum",
    "prod",
    "left",
    "right",
    "begin",
    "end",
    "mathrm",
    "mathbf",
    "mathit",
    "mathbb",
    "mathcal",
    "text",
    "operatorname",
    "times",
    "div",
    "cdot",
    "le",
    "leq",
    "ge",
    "geq",
    "ne",
    "neq",
    "pm",
    "mp",
    "in",
    "notin",
    "subset",
    "subseteq",
    "cup",
    "cap",
    "emptyset",
    "vec",
    "overline",
    "underline",
    "hat",
    "bar",
    "lvert",
    "rvert",
    "triangle",
    "ldots",
    "cdots",
    "vdots",
    "ddots",
    "quad",
    "qquad",
    "displaystyle",
    *(command[1:] for command in LATEX_SYMBOLS.values()),
}
_UNESCAPED_LATEX = re.compile(
    r"(?<!\\)\\(" + "|".join(sorted(_COMMANDS, key=len, reverse=True)) + r")(?![A-Za-z])"
)
_DOUBLED_LATEX_COMMAND = re.compile(
    r"(?<!\\)\\{2}(?=(?:" + "|".join(sorted(_COMMANDS, key=len, reverse=True)) + r")(?![A-Za-z]))"
)


def repair_latex_json_escapes(payload: str) -> str:
    """Double lone LaTeX backslashes before JSON consumes f/b/n/t escapes.

    Valid JSON escapes and already doubled LaTeX backslashes stay untouched.
    This changes serialization only; it never changes mathematical content.
    """

    return _UNESCAPED_LATEX.sub(lambda match: "\\" + match.group(0), payload)


def normalize_latex_command_escapes(formula: str) -> str:
    """Repair doubled command escapes inside math, retaining matrix row breaks.

    A row break followed by a command contains three backslashes and stays
    intact. Callers restrict this repair to explicitly delimited formulas.
    """

    return _DOUBLED_LATEX_COMMAND.sub(lambda _: "\\", formula)
