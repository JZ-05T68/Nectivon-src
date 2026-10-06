"""Optional background AI math typesetting after a human saves a question.

The AI can nominate spans, but the saved human text is never rewritten.  A
span is accepted only when its LaTeX round-trips to exactly the same symbols.
Unaccepted or unavailable AI leaves the deterministic read renderer in place.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING
from uuid import uuid4

from src.question_recognition_rules import LATEX_SYMBOLS, MATH_NOTATION_RULES

if TYPE_CHECKING:
    from src.learning_ai_draft_service import LearningAIDraftService

LOGGER = logging.getLogger(__name__)
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nectivon-math")
_CANDIDATE_PENDING: set[Path] = set()
_CANDIDATE_LOCK = Lock()
_FIELDS = ("stem_text", "student_answer", "correction_note", "analysis_note", "solution_method",
           "reason_tags", "method_tags")
_FRACTION = re.compile(r"\\d?frac\{([^{}]+)\}\{([^{}]+)\}")


def _canonical_math(text: str) -> str:
    """Compare notation conservatively, retaining fraction operand grouping."""

    value = re.sub(r"\s+", "", str(text).replace("$", ""))
    superscripts = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
    value = re.sub(
        r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+",
        lambda match: "^" + match.group(0).translate(superscripts),
        value,
    )
    value = value.replace(r"\left", "").replace(r"\right", "")
    value = re.sub(r"\\(?:mathrm|mathit|mathbf|operatorname)\{([^{}]+)\}", r"\1", value)
    for symbol, command in sorted(LATEX_SYMBOLS.items(), key=lambda item: -len(item[1])):
        value = value.replace(command, symbol)
    value = re.sub(r"\\(?=(?:sin|cos|tan|log|ln|lim|exp)\b)", "", value)
    value = re.sub(r"\\[,;!: ]", "", value)

    def operand(token: str) -> str:
        if token.startswith("(") and token.endswith(")"):
            return token
        return f"({token})" if re.search(r"[+*/=]|(?<!^)-", token) else token

    value = _FRACTION.sub(
        lambda match: f"{operand(match.group(1))}/{operand(match.group(2))}", value
    )
    for command, symbol in (
        (r"\left", ""), (r"\right", ""),
        (r"\lvert", "|"), (r"\rvert", "|"),
        (r"\times", "×"), (r"\div", "÷"),
        (r"\leq", "≤"), (r"\geq", "≥"),
        (r"\triangle", "△"), (r"\pi", "π"), (r"\cdot", "×"),
    ):
        value = value.replace(command, symbol)
    return value.replace("{", "").replace("}", "").replace("*", "×")


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
            or "$" in latex or "\n" in latex or "$" in literal
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


def _verified_field_display(
    source: dict[str, str], ai_service: LearningAIDraftService, *, target_refs: tuple[str, ...]
) -> dict[str, dict[str, str]]:
    """Make one optional AI call and verify every proposed mathematical span."""

    from src.learning_ai_draft_service import _parse_json_object
    from src.math_display import normalize_question_math

    # Establish complete local formulas first.  Otherwise a partial AI span
    # such as $|b|$/$b$ would hide the full fraction from the read renderer.
    # The model may enhance remaining text, but cannot fragment existing math.
    prepared = {field: normalize_question_math(value) for field, value in source.items()}

    prompt = (
        "你只做数学排版，不解题、不改写、不更正原文。返回 JSON 对象，"
        "每个字段是跨度数组：{\"start\":0,\"end\":3,\"text\":\"原文子串\","
        "\"latex\":\"同义 LaTeX 数学源码\"}。start/end 是 Python 字符位置，"
        "必须精确对应原文；不能增删任何数字、字母或运算符。"
        "字母、分数分式、指数、根号、绝对值、不等式使用标准 KaTeX 语法；"
        "已在 $...$ 内的公式无需重复处理。保留选项和题干文字。"
        "不确定就返回空数组。字段只限下面这些：\n"
        + MATH_NOTATION_RULES
        + "本任务的 latex 字段只放公式内部源码，不含 $；渲染器会加上成对的 $。\n"
        + json.dumps(prepared, ensure_ascii=False)
    )
    suggestions = _parse_json_object(ai_service._complete(prompt, target_refs=target_refs))
    return {
        field: {
            "source": value,
            "display": apply_verified_math_spans(prepared[field], suggestions.get(field)),
        }
        for field, value in source.items()
    }


def _candidate_display_path(cache_root: Path, page_id: int, source: str) -> Path:
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return Path(cache_root) / "math-display" / f"page_{page_id}_{digest}.json"


def candidate_display_stem(cache_root: Path, page_id: int, source: str) -> str:
    """Read source-matched derived typesetting without changing candidate state."""

    path = _candidate_display_path(cache_root, page_id, source)
    try:
        derived = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(derived, dict) and derived.get("source") == source:
            return str(derived.get("display") or source)
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        LOGGER.warning("候选数学排版缓存无法读取：page_id=%s", page_id, exc_info=True)
    return source


def format_candidate_math(
    cache_root: Path, page_id: int, source: str, ai_service: LearningAIDraftService | None
) -> bool:
    """Typeset a saved candidate once; human text and lifecycle stay authoritative."""

    if ai_service is None or not source:
        return False
    try:
        display = _verified_field_display(
            {"stem": source}, ai_service, target_refs=(f"page:{page_id}",)
        )["stem"]
        path = _candidate_display_path(cache_root, page_id, source)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        temporary.write_text(json.dumps(display, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
        return True
    except Exception:  # noqa: BLE001 - typesetting must never undo a saved human edit
        LOGGER.warning("候选 AI 数学排版未完成：page_id=%s", page_id, exc_info=True)
        return False


def schedule_candidate_math(
    cache_root: Path, page_id: int, source: str, ai_service: LearningAIDraftService | None,
) -> Future[bool] | None:
    """Enhance saved math once in the background; local caret rendering is immediate."""

    if ai_service is None or not source:
        return None
    path = _candidate_display_path(cache_root, page_id, source)
    with _CANDIDATE_LOCK:
        if path in _CANDIDATE_PENDING or path.is_file():
            return None
        _CANDIDATE_PENDING.add(path)

    def run() -> bool:
        try:
            return format_candidate_math(cache_root, page_id, source, ai_service)
        finally:
            with _CANDIDATE_LOCK:
                _CANDIDATE_PENDING.discard(path)

    try:
        return _EXECUTOR.submit(run)
    except RuntimeError:
        with _CANDIDATE_LOCK:
            _CANDIDATE_PENDING.discard(path)
        LOGGER.warning("候选数学排版任务未启动：page_id=%s", page_id, exc_info=True)
        return None


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
            source = _row_math_source(row)
        display = _verified_field_display(
            source, ai_service, target_refs=(f"question:{question_id}",)
        )
        with sqlite3.connect(database_path, timeout=30) as connection:
            connection.row_factory = sqlite3.Row
            latest = connection.execute(
                "SELECT " + ", ".join(_FIELDS) + " FROM question_items WHERE id = ?",
                (question_id,),
            ).fetchone()
            if latest is None or _row_math_source(latest) != source:
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
    """Use source-matched formatting, including the candidate's saved AI display."""

    value = getattr(question, field, "") or ""
    original = "、".join(value) if isinstance(value, list) else str(value)
    derived = (question.math_display or {}).get(field)
    if not derived and isinstance(question.ai_draft, dict):
        candidate_display = question.ai_draft.get("math_display")
        if isinstance(candidate_display, dict):
            derived = candidate_display.get(field)
    if isinstance(derived, dict) and derived.get("source") == original:
        return str(derived.get("display") or original)
    return original


def _row_math_source(row) -> dict[str, str]:
    """Format textual tags too, while retaining their original JSON storage."""

    return {field: "、".join(json.loads(row[field] or "[]"))
            if field in ("reason_tags", "method_tags") else str(row[field] or "")
            for field in _FIELDS}


def split_math_tags(text: str) -> list[str]:
    """Split user tags without splitting commas inside formulas or grouped notation."""

    parts, start, depth, delimiter, index = [], 0, 0, "", 0
    while index < len(text):
        char = text[index]
        if char == "$" and (index == 0 or text[index - 1] != "\\"):
            token = "$$" if text[index:index + 2] == "$$" else "$"
            delimiter = "" if delimiter == token else token if not delimiter else delimiter
            index += len(token)
            continue
        if not delimiter:
            if char in "({[（":
                depth += 1
            elif char in ")}]）":
                depth = max(0, depth - 1)
            elif char in "、,，" and not depth:
                if tag := text[start:index].strip():
                    parts.append(tag)
                start = index + 1
        index += 1
    if tag := text[start:].strip():
        parts.append(tag)
    return parts
