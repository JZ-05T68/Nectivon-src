"""Optional background AI math typesetting after a human saves a question.

The AI can nominate spans, but the saved human text is never rewritten.  A
span is accepted only when its LaTeX round-trips to exactly the same symbols.
Unaccepted or unavailable AI leaves the deterministic read renderer in place.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

LOGGER = logging.getLogger(__name__)
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nectivon-math")
_FIELDS = ("stem_text", "student_answer", "correction_note", "analysis_note", "solution_method")
_FRACTION = re.compile(r"\\d?frac\{([^{}]+)\}\{([^{}]+)\}")


def _canonical_math(text: str) -> str:
    value = str(text).replace("$", "").replace(" ", "")
    value = _FRACTION.sub(lambda match: f"{match.group(1)}/{match.group(2)}", value)
    for command, symbol in (
        (r"\left", ""), (r"\right", ""),
        (r"\lvert", "|"), (r"\rvert", "|"),
        (r"\times", "×"), (r"\div", "÷"),
        (r"\leq", "≤"), (r"\geq", "≥"),
        (r"\triangle", "△"),
    ):
        value = value.replace(command, symbol)
    return value.replace("{", "").replace("}", "").replace("\n", "")


def apply_verified_math_spans(source: str, spans: object) -> str:
    """Accept only source-identical, non-overlapping, meaning-preserving spans."""

    if not isinstance(spans, list):
        return source
    accepted: list[tuple[int, int, str]] = []
    last_end = 0
    for item in sorted(
        (value for value in spans if isinstance(value, dict)),
        key=lambda value: value.get("start", -1) if type(value.get("start")) is int else -1,
    ):
        start, end = item.get("start"), item.get("end")
        latex = item.get("latex")
        literal = item.get("text")
        if (
            type(start) is not int or type(end) is not int
            or not isinstance(latex, str) or not isinstance(literal, str)
            or not (last_end <= start < end <= len(source))
            or source[start:end] != literal
            or "$" in latex or "\n" in latex
            or _canonical_math(latex) != _canonical_math(literal)
        ):
            continue
        accepted.append((start, end, latex))
        last_end = end
    output: list[str] = []
    cursor = 0
    for start, end, latex in accepted:
        output.extend((source[cursor:start], f"${latex}$"))
        cursor = end
    output.append(source[cursor:])
    return "".join(output)


def _format_saved_question(database_path: Path, question_id: int, ai_service) -> None:
    try:
        with sqlite3.connect(database_path, timeout=30) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT " + ", ".join(_FIELDS) + " FROM question_items WHERE id = ?",
                (question_id,),
            ).fetchone()
            if row is None:
                return
            source = {field: str(row[field] or "") for field in _FIELDS}
        prompt = (
            "你只做数学排版，不解题、不改写、不更正原文。返回 JSON 对象，"
            "每个字段是跨度数组：{\"start\":0,\"end\":3,\"text\":\"原文子串\","
            "\"latex\":\"同义 LaTeX 数学源码\"}。start/end 是 Python 字符位置，"
            "必须精确对应原文；不能增删任何数字、字母或运算符。"
            "不确定就返回空数组。字段只限下面这些：\n"
            + json.dumps(source, ensure_ascii=False)
        )
        raw = ai_service._complete(prompt, target_refs=(f"question:{question_id}",))
        from src.learning_ai_draft_service import _parse_json_object

        suggestions = _parse_json_object(raw)
        display = {
            field: {
                "source": value,
                "display": apply_verified_math_spans(value, suggestions.get(field)),
            }
            for field, value in source.items()
        }
        with sqlite3.connect(database_path, timeout=30) as connection:
            connection.row_factory = sqlite3.Row
            latest = connection.execute(
                "SELECT " + ", ".join(_FIELDS) + " FROM question_items WHERE id = ?",
                (question_id,),
            ).fetchone()
            if latest is None or any(
                str(latest[field] or "") != source[field] for field in _FIELDS
            ):
                return
            connection.execute(
                "UPDATE question_items SET math_display_json = ? WHERE id = ?",
                (json.dumps(display, ensure_ascii=False), question_id),
            )
    except Exception:  # noqa: BLE001 - optional AI may fail without affecting human notes
        LOGGER.warning(
            "后台数学排版未完成，保留确定性排版：question_id=%s",
            question_id,
            exc_info=True,
        )


def schedule_math_formatting(database_path: Path, question_id: int, ai_service) -> bool:
    """Queue an optional AI pass; return False when no provider is configured."""

    if ai_service is None:
        return False
    _EXECUTOR.submit(_format_saved_question, Path(database_path), question_id, ai_service)
    return True


def display_field(question, field: str) -> str:
    original = str(getattr(question, field, "") or "")
    derived = (question.math_display or {}).get(field)
    if isinstance(derived, dict) and derived.get("source") == original:
        return str(derived.get("display") or original)
    return original
