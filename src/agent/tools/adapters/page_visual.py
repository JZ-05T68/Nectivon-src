"""``page_visual_search`` read-only Tool Adapter (v0.7.2 Visual Understanding).

One bounded step: locate pages with the existing lexical search, then read
the top page image with the vision model and return the visually-derived
facts together with the page reference, so every visual answer stays
traceable to the original page.

Boundaries:

- the vision prompt demands verbatim chart/table reading and explicit
  "看不清" confessions; fabrication is refused at the prompt level and the
  raw model text is returned unedited;
- at most ``limit`` (1..2) pages are read for a standalone question; when the
  retrieved pool itself spans several documents of one version family (same
  normalized title, e.g. 手册 1.0 / 1.1 / 2.0 草案), the missing family
  members are appended so the family is covered — still bounded, at most 4
  pages per call (FAIL-014 fix; expansion metadata is returned in
  ``family_expansion``);
- without a vision-capable provider the tool is simply not registered.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path

from src.agent.tools.adapters._common import (
    AdapterInputError,
    empty_result,
    failed_result,
    internal_failure_result,
    optional_int,
    reject_unknown_arguments,
    require_text,
    success_result,
)
from src.agent.tools.adapters._version_family import (
    FAMILY_POOL_LIMIT,
    select_family_expanded,
)
from src.agent.tools.contracts import (
    ToolContext,
    ToolDefinition,
    ToolErrorCode,
    ToolInput,
    ToolReference,
    ToolResult,
    ToolSideEffect,
)
from src.models import PAGE_STABLE_TYPE, SearchResult, build_stable_id
from src.search_service import SearchService
from src.text_utils import build_agent_page_text

LOGGER = logging.getLogger(__name__)

MAX_LIMIT = 2
DEFAULT_LIMIT = 1
MAX_QUERY_LENGTH = 500

#: Maximum extra pages the visual-artifact index may append to the lexical
#: pool (bounded, additive-only merge; FAIL-016 ADR Option E-1).
VISUAL_MERGE_LIMIT = 4

ALLOWED_ARGUMENTS = frozenset({"query", "limit"})

_VISION_PROMPT = (
    "你在帮用户读取一页资料图片中的视觉信息（表格、柱状图、折线图、饼图、"
    "散点图、流程图、框图或页面上的关键视觉事实）。\n"
    "要求：\n"
    "1. 先判断这一页是否真的包含表格或图表：如果这一页只有文字、没有任何"
    "表格或图表，必须明确回答“本页没有图表或表格”，并转写页面上的关键文字，"
    "禁止为了回应请求而虚构任何图表、坐标轴或数据点。\n"
    "2. 只描述图片中真实可见的内容；所有数值必须来自图中，禁止编造。\n"
    "3. 如果图片模糊、太小或某处无法辨认，必须明确说明"
    "“图片模糊，无法可靠读取该处”，禁止猜测。\n"
    "4. 表格逐行转写关键行；图表说明坐标轴含义并读出关键数据点。\n"
    "5. 用简体中文要点输出。\n"
    "6. 数字标签读取规则：中文字体渲染的图表标签里，同一个数字内部可能因"
    "字形宽度出现明显间隙（例如“2 6”其实是整数 26，不是小数 2.6）。"
    "只有看到明确的小数点“.”才读作小数；只有间隙时必须把各数位合并读成"
    "一个整数，并与下方文字层交叉核对；仍无法确定时按规则 3 如实说明，"
    "禁止猜测。\n"
)
MAX_SOURCE_TEXT_CHARS = 3000

#: FAIL-026 minimal guard: when the vision reading reports a decimal ``X.Y``
#: that the page text layer never states while the digit concatenation ``XY``
#: appears there as a standalone number, the two layers disagree in exactly
#: the digit-gap misread shape. The resulting note forces disclosure in the
#: Final Answer stage; it never rewrites either layer, so true decimals that
#: the text layer corroborates are untouched and pure-visual pages (no text
#: layer) are unaffected.
NUMERIC_CONFLICT_HEADER = "数值一致性提示（自动核对）："
MAX_NUMERIC_CONFLICTS = 3

_DECIMAL_RE = re.compile(r"(?<![0-9.vV])([0-9]+)\.([0-9]+)(?![0-9])")
_INT_RE = re.compile(r"(?<![0-9.])[0-9]+(?![0-9.])")

_WIDTH_TABLE = {
    ord(full): ord(ascii_)
    for full, ascii_ in zip("０１２３４５６７８９．", "0123456789.", strict=True)
}


def numeric_conflict_note(source_text: str, visual_text: str) -> str | None:
    """Return a numeric-conflict disclosure note, or ``None``.

    Generic over all numbers (no fixture- or value-specific rules): for every
    decimal in the vision reading that the text layer does not itself state,
    the digit concatenation is looked up in the text layer's standalone
    integers. Nothing is rewritten — the caller appends the note so the Final
    Answer stage must present both values with their source layers.
    """

    source = (source_text or "").translate(_WIDTH_TABLE)
    visual = (visual_text or "").translate(_WIDTH_TABLE)
    if not source.strip() or not visual.strip():
        return None
    source_decimals = {match.group(0) for match in _DECIMAL_RE.finditer(source)}
    source_ints = {match.group(0) for match in _INT_RE.finditer(source)}
    conflicts: list[str] = []
    seen: set[tuple[str, str]] = set()
    for match in _DECIMAL_RE.finditer(visual):
        decimal = match.group(0)
        if decimal in source_decimals:
            continue  # the text layer corroborates the true decimal
        joined = match.group(1) + match.group(2)
        if joined in source_ints and (decimal, joined) not in seen:
            seen.add((decimal, joined))
            conflicts.append(f"图中读取 {decimal}，文字层记录 {joined}")
            if len(conflicts) >= MAX_NUMERIC_CONFLICTS:
                break
    if not conflicts:
        return None
    return (
        NUMERIC_CONFLICT_HEADER
        + "图内数值与同页文字层数值不一致，疑似数字字形间隙被读作小数点："
        + "；".join(conflicts)
        + "。回答时必须并列披露两个数值并说明来源层不同，"
        "禁止单方面采信图片。"
    )

PAGE_VISUAL_SEARCH_DEFINITION = ToolDefinition(
    name="page_visual_search",
    description=(
        "读取资料页面图片中的视觉信息：表格数值、柱状图/折线图/饼图数据点、"
        "流程图与框图结构。用于答案在图里而不在正文里的问题。"
        "同一主题存在多个版本或同名多份资料时，会自动把整个版本族一起读出，"
        "避免只看到其中一份就下结论。"
        "图片模糊时会如实说明看不清，不会编造数值。"
    ),
    side_effect=ToolSideEffect.READ_ONLY,
    input_schema={
        "query": {
            "type": "string",
            "required": True,
            "description": "与目标图表/表格相关的关键词，原样保留名词和数字",
        },
        "limit": {
            "type": "integer",
            "default": DEFAULT_LIMIT,
            "min": 1,
            "max": MAX_LIMIT,
            "description": "最多读取几页图片",
        },
    },
    timeout_seconds=60.0,
)


class PageVisualAdapter:
    """Locate pages lexically, then read the page image with vision."""

    tool_name = "page_visual_search"

    def __init__(
        self,
        search_service: SearchService,
        *,
        kb_uuid: str,
        vision_provider: object,
        pages_dir: Path,
        vision_model: str | None = None,
        visual_index: object | None = None,
    ) -> None:
        self._service = search_service
        self._kb_uuid = kb_uuid
        self._provider = vision_provider
        self._pages_dir = Path(pages_dir)
        self._vision_model = vision_model
        # Retrieval aid only (E-1): visual artifacts help *find* a page; the
        # answer is still read from the real page image below.
        self._visual_index = visual_index

    def __call__(self, tool_input: ToolInput, context: ToolContext) -> ToolResult:
        try:
            reject_unknown_arguments(tool_input.arguments, ALLOWED_ARGUMENTS)
            query = require_text(
                tool_input.arguments, "query", max_length=MAX_QUERY_LENGTH
            )
            limit = optional_int(
                tool_input.arguments,
                "limit",
                default=DEFAULT_LIMIT,
                min_value=1,
                max_value=MAX_LIMIT,
            )
        except AdapterInputError as exc:
            return failed_result(
                self.tool_name, ToolErrorCode.INVALID_INPUT, exc.message
            )
        try:
            pool = self._service.search(query, limit=FAMILY_POOL_LIMIT)
        except Exception as exc:
            return internal_failure_result(
                self.tool_name, exc, safe_message="页面检索执行失败"
            )
        visual_hits: list[SearchResult] = []
        if self._visual_index is not None:
            try:
                visual_hits = self._visual_index.search_hits(query)
            except Exception:
                LOGGER.warning("视觉阅读索引查询失败，按词法池继续", exc_info=True)
                visual_hits = []
            pool = merge_visual_hits(pool, visual_hits)
        if not pool:
            return empty_result(
                self.tool_name,
                data={"query": query, "limit": limit, "total": 0, "results": []},
            )
        # FAIL-014: when the lexical pool already spans several documents of
        # one version family (same normalized title), read the missing family
        # members too, so a multi-version question is never answered from a
        # partial view of the family. Non-family queries keep the exact
        # historical top-``limit`` selection.
        hits, expansion = select_family_expanded(
            pool, base_limit=limit, visual_pages=visual_hits
        )
        if expansion.get("triggered") and pool:
            # A family member with a thin text layer can rank below the pool
            # window for the caller's terms; one co-query with an anchor
            # family member's own document title (title words are family
            # words) gives those members a second, still family-only, path
            # into the selection.
            coquery_title = expansion.get("coquery_title")
            if coquery_title:
                try:
                    co_pool = self._service.search(
                        str(coquery_title), limit=FAMILY_POOL_LIMIT
                    )
                except Exception:
                    LOGGER.warning(
                        "版本族协同检索失败，按首次池选择继续", exc_info=True
                    )
                    co_pool = []
                if co_pool:
                    seen: set[tuple[int, int]] = {
                        (hit.document_id, hit.page_id) for hit in pool
                    }
                    merged = list(pool)
                    for hit in co_pool:
                        key = (hit.document_id, hit.page_id)
                        if key not in seen:
                            seen.add(key)
                            merged.append(hit)
                    hits, expansion = select_family_expanded(
                        merged, base_limit=limit, visual_pages=visual_hits
                    )
        results: list[dict[str, object]] = []
        references: list[ToolReference] = []
        failures: list[str] = []
        for hit in hits:
            try:
                source_text = build_agent_page_text(
                    extracted_text=hit.extracted_text,
                    ocr_text=hit.ocr_text,
                    manual_text=hit.markdown_content,
                )[0]
                visual_text = self._read_page_image(
                    hit.image_path, source_text=source_text
                )
            except Exception:
                failures.append(f"第 {hit.page_number} 页图片读取失败")
                LOGGER.warning(
                    "视觉读取失败：page_id=%s", hit.page_id, exc_info=True
                )
                continue
            if not visual_text.strip():
                failures.append(f"第 {hit.page_number} 页没有可辨认的视觉内容")
                continue
            visual_content = visual_text.strip()
            source_excerpt = source_text.strip()[:MAX_SOURCE_TEXT_CHARS]
            combined_content = visual_content
            numeric_note: str | None = None
            if source_excerpt:
                combined_content += (
                    "\n\n同页原始文字（用于与图片交叉核对）：\n" + source_excerpt
                )
                numeric_note = numeric_conflict_note(source_text, visual_content)
            if numeric_note:
                combined_content += "\n\n" + numeric_note
            row: dict[str, object] = {
                "page_id": hit.page_id,
                "document_id": hit.document_id,
                "document_title": hit.document_title,
                "page_number": hit.page_number,
                # "content" is the projection key the Final Answer mapper
                # reads; "visual_facts" keeps the explicit meaning.
                "content": combined_content,
                "visual_facts": visual_content,
                "source_page_text": source_excerpt,
                "source": "page_image",
            }
            if numeric_note:
                row["numeric_consistency_note"] = numeric_note
            results.append(row)
            references.append(
                ToolReference(
                    stable_id=build_stable_id(
                        self._kb_uuid, PAGE_STABLE_TYPE, hit.page_id
                    ),
                    anchor_label=f"第 {hit.page_number} 页（图片读取）",
                )
            )
        if not results:
            if failures:
                return success_result(
                    self.tool_name,
                    {
                        "query": query,
                        "total": 0,
                        "results": [],
                        "notes": failures,
                        "note": (
                            "图片内容无法可靠读取；如需回答请提供更清晰的资料。"
                        ),
                    },
                    warnings=tuple(failures),
                )
            return empty_result(
                self.tool_name,
                data={"query": query, "limit": limit, "total": 0, "results": []},
            )
        data: dict[str, object] = {
            "query": query,
            "limit": limit,
            "total": len(results),
            "results": results,
        }
        if expansion.get("triggered"):
            data["family_expansion"] = expansion
        if failures:
            data["notes"] = failures
        return success_result(self.tool_name, data, references=tuple(references))

    def _read_page_image(
        self, image_path: Path | str, *, source_text: str = ""
    ) -> str:
        path = Path(image_path)
        if not path.exists():
            candidate = self._pages_dir / path
            if candidate.exists():
                path = candidate
        png_bytes = path.read_bytes()
        encoded = base64.b64encode(png_bytes).decode("ascii")
        wrapper = getattr(self._provider, "complete_vision", None)
        if wrapper is None:
            from src.ai.provider import AIUnavailableError

            raise AIUnavailableError("当前 AI 提供方不支持视觉读取")
        prompt = _VISION_PROMPT
        if source_text.strip():
            bounded_source = source_text.strip()[:MAX_SOURCE_TEXT_CHARS]
            prompt += (
                "7. 下方是从同一原始页面直接提取的文字层，仅用于与图片交叉核对精确的"
                "型号、标签和数值，不是视觉索引摘要。图片中文字重叠或字形易混淆时，"
                "优先用文字层确认；若两者确有冲突，必须明确披露，禁止静默猜测。\n"
                f"同页原始文字(JSON 字符串)：{json.dumps(bounded_source, ensure_ascii=False)}\n"
            )
        result = wrapper(
            prompt,
            encoded,
            model=self._vision_model,
            source_feature="page_visual_search",
        )
        return result.text


def merge_visual_hits(
    pool: list[SearchResult], visual_hits: list[SearchResult]
) -> list[SearchResult]:
    """Append bounded visual-index hits to the lexical pool (additive-only).

    Lexical order is protected: existing pool members keep their positions
    and are never dropped; visual hits are appended after them, deduped by
    ``(document_id, page_id)`` and capped at :data:`VISUAL_MERGE_LIMIT`, so
    downstream family selection keeps its precision guard. The input pool
    object is not mutated.
    """

    if not visual_hits:
        return list(pool)
    seen = {(hit.document_id, hit.page_id) for hit in pool}
    merged = list(pool)
    added = 0
    for hit in visual_hits:
        key = (hit.document_id, hit.page_id)
        if key in seen:
            continue
        seen.add(key)
        merged.append(hit)
        added += 1
        if added >= VISUAL_MERGE_LIMIT:
            break
    return merged


__all__ = [
    "PAGE_VISUAL_SEARCH_DEFINITION",
    "PageVisualAdapter",
    "MAX_SOURCE_TEXT_CHARS",
    "NUMERIC_CONFLICT_HEADER",
    "merge_visual_hits",
    "numeric_conflict_note",
]
