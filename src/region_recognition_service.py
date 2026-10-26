"""One bounded cloud re-recognition of a cropped original region (P0-2).

When the first recognition got a fragment wrong, the student should not
retype the formula: select the region on the ORIGINAL photo, send only that
crop to the vision model, and pick from the returned candidates.  This
module performs exactly that single audited call.

Rules honoured here:

- The crop happens locally (Pillow); only the cropped pixels travel.
- One call per user action — no retry because an answer "looks unsatisfying".
- The model may return up to three honest readings; an unreadable glyph is
  marked ▢, never guessed (识别不完整时明确标记，不捏造).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from src.question_visual_regions import crop_region

if TYPE_CHECKING:
    from src.ai.provider import AuditedAIProvider

LOGGER = logging.getLogger(__name__)

MAX_CANDIDATE_READINGS = 3
MAX_REGION_TEXT_CHARS = 2000


class RegionRecognitionError(RuntimeError):
    """Region re-recognition was refused or returned nothing usable."""


@dataclass(slots=True)
class RegionRecognition:
    """Candidate readings of one cropped region, newest first."""

    texts: list[str]
    region_role: str
    warnings: list[str]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def build_region_prompt(hint: str) -> str:
    """Ask for faithful transcriptions of ONLY the cropped region."""

    context = (
        f"\n题目上下文（仅供断句参考，不得抄入结果）：{hint[:300]}\n"
        if hint.strip() else "\n"
    )
    return (
        "你是教材习题转录助手。下面是学生从原页照片中框选的一块区域。"
        "请只转录这块裁剪区域内的印刷内容（题干片段、公式、选项、表格文字）。"
        "区域外的任何内容、学生手写笔迹、透页阴影都不是转录对象。\n"
        + context
        + "要求：\n"
        "1. 数学变量、希腊字母、上下标、分式、根式、单位、科学计数法一律用"
        "标准 LaTeX 写在 $...$ 内（如 $\\omega_n$、$\\Delta p$、$K_p$、"
        "$3.2\\times10^{-3}\\,\\mathrm{m}^2$）；普通文字保持原样。\n"
        "2. 返回最多 3 种对这块区域的忠实读法：字形可能有多义（如 0/o、l/1、"
        "ζ/z）时才提供多种，否则只返回 1 种。不得解题、不得概括、不得按教材"
        "知识补写。\n"
        "3. 看不清的字符用 ▢ 占位并在 warning 里说明；绝不猜测。区域是插图/"
        "电路图/结构图而几乎没有文字时，warning 说明这是图形区域。\n"
        '4. 只输出 JSON：{"texts": ["…", …], "warnings": ["…", …]}，不要输出其它文字。'
        "JSON 字符串里的反斜杠按 JSON 规则双写。\n"
    )


def parse_region_payload(raw: str) -> tuple[list[str], list[str]]:
    """Parse and bound the model JSON; never half-trust free-form text."""

    cleaned = str(raw).strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1 :]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise RegionRecognitionError("区域重识别未返回有效 JSON。")
    from src.recognition_json import repair_latex_json_escapes

    try:
        data = json.loads(repair_latex_json_escapes(cleaned[start : end + 1]))
    except json.JSONDecodeError as exc:
        raise RegionRecognitionError(f"区域重识别 JSON 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise RegionRecognitionError("区域重识别 JSON 结构无效。")
    texts: list[str] = []
    for value in data.get("texts", []) if isinstance(data.get("texts"), list) else []:
        text = str(value).strip()[:MAX_REGION_TEXT_CHARS]
        if text and text not in texts:
            texts.append(text)
        if len(texts) >= MAX_CANDIDATE_READINGS:
            break
    warnings = [
        str(item).strip()[:200]
        for item in (data.get("warnings") or [])
        if str(item).strip()
    ] if isinstance(data.get("warnings"), list) else []
    if not texts:
        raise RegionRecognitionError(
            warnings[0] if warnings else "这块区域没有识别出可用的印刷文字。"
        )
    return texts, warnings


def recognize_region(
    provider: AuditedAIProvider,
    image_path: Path | str,
    region: dict,
    *,
    page_id: int,
    hint: str = "",
) -> RegionRecognition:
    """Crop locally and re-recognize that region once with the vision model."""

    role = str(region.get("role") or "question")
    png_bytes = crop_region(Path(image_path), region, original=True)
    if not png_bytes:
        raise RegionRecognitionError("无法从原页裁剪这块区域，请重新框选。")
    # Scan-style enhancement (round-2 issue 3) helps the model read through
    # shadows/uneven light; the untouched raw photo stays the evidence.
    from src.figure_scan_service import enhance_figure_scan

    try:
        png_bytes = enhance_figure_scan(png_bytes)
    except Exception:  # noqa: BLE001 - enhancement is best-effort for reading
        LOGGER.debug("区域增强失败，按原图识别", exc_info=True)
    from src.visual_input_budget import prepare_page_image

    prepared = prepare_page_image(png_bytes)
    completion = provider.complete_vision(
        build_region_prompt(hint),
        prepared.data_url,
        model=provider.default_model,
        max_completion_tokens=2048,
        json_output=True,
        source_feature="region_rerecognition",
        target_refs=(f"page:{page_id}",),
    )
    texts, warnings = parse_region_payload(completion.text)
    if any("▢" in text for text in texts):
        warnings = [*warnings, "结果含 ▢ 待核对字符。"]
    return RegionRecognition(texts=texts, region_role=role, warnings=warnings)


def revision_entry(
    *,
    kind: str,
    detail: str,
) -> dict:
    """Uniform provenance record for candidate stem changes."""

    return {"at": _utc_now(), "kind": kind, "detail": detail[:MAX_REGION_TEXT_CHARS]}
