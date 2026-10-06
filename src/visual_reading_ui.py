"""User-triggered stage-2 visual reading entry (v0.8.6 duty D).

Rendered inside the page review workspace.  Stage 1 state is shown as an
honest status line ("detected" never means "understood"); the two actions
(读取手写内容 / 解析图片与图表) are strictly user-triggered, run only when
an AI provider is configured, and store their output through
:class:`src.page_visual_service.PageVisualService` with explicit provenance
(HANDWRITING_VISION / IMAGE_REGION / DIAGRAM_INTERPRETATION), ``ai_vision``
origin, ``draft`` status and ``uncertain`` confidence - the user confirms
readings, the model never does.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import streamlit as st

from src.ai.provider import AIExecutionError, AIUnavailableError, ProviderCallError
from src.display_labels import (
    INTERPRETATION_STATUS_LABELS,
    PROVENANCE_LABELS,
)
from src.models import Page
from src.page_visual_service import (
    HANDWRITING_PRESENCE_KEY,
    PageVisualError,
    PageVisualService,
)
from src.question_recognition_rules import MATH_NOTATION_RULES
from src.runtime import application_ai_provider, application_page_visual_service
from src.time_display import format_beijing_time
from src.visual_input_budget import (
    VisualInputBudgetError,
    prepare_page_image,
)
from src.visual_provenance import visual_detection_provenance_fields

LOGGER = logging.getLogger(__name__)

#: The model must first answer "does recognisable handwriting exist at
#: all?" (fix round §18) instead of being asked to transcribe handwriting
#: unconditionally — the unconditional ask produced hallucinated handwriting
#: on a purely printed page (IMG_20260926_230438.jpg, verified by human
#: review 2026-09-27 morning).  One call, two phases: declare presence on
#: the first line, then (only when present) transcribe with per-item
#: position pointers (§19 evidence reference).
_READ_HANDWRITING_PROMPT = (
    "第一步（必答）：先判断这张图片是否真的存在可辨识的手写内容"
    "（学生手写解题过程、演算、批注、圈画、人工标记）。"
    "你的回复第一行必须原样输出以下三种之一：\n"
    "HANDWRITING_PRESENCE: none\n"
    "HANDWRITING_PRESENCE: possible\n"
    "HANDWRITING_PRESENCE: confirmed\n"
    "判定标准：none = 纯印刷页面，没有任何可辨识手写；"
    "possible = 疑似有手写但没有把握；confirmed = 确实存在可辨识手写。\n"
    "第二阶段（按判定执行）：\n"
    "- none：不得输出任何手写转录，不得把印刷文字说成手写；"
    "只用两三行说明你的判断依据。"
    "- possible：列出疑似手写的候选项，每条注明不确定的原因，"
    "整体标注【待核对】。"
    "- confirmed：按出现顺序只转录确实可见的手写内容，"
    "每条末尾用（位置：…）注明它在页面上的位置"
    "（靠近哪个印刷元素/图/页面的哪块区域），便于与原图核对。\n"
    "印刷体的题干和选项不属于手写转录范围：不要整段复制印刷题干；"
    "如确需上下文锚点，用一行【印刷上下文】简短标注即可。"
    "禁止编造不存在的手写内容；看不清就写看不清，不要猜。"
    + MATH_NOTATION_RULES
)
_INTERPRET_VISUAL_PROMPT = (
    "请描述这张页面上的图片/图表/示意图的结构与标注内容。"
    "只描述确实可见的元素；无法确定的部分明确说明不确定。不要编造图表中不存在的信息。"
    "\n\n如果图中包含控制系统框图、信号流图或电路图，必须先做结构化抽取，"
    "再写描述，两个阶段的结论必须一致：\n"
    "第一阶段（结构化清单，逐行输出，不要写成散文）：\n"
    "BLOCKS: 图中每个方块/元件的名字（如 G1、G2、H1、R、C，按图中实际记号）\n"
    "SUMMING_POINTS: 每个求和点（比较点）：编号，哪些信号进入、各自是 + 还是 -"
    "（正负号看不清就标「符号不确定」）\n"
    "TAKEOFF_POINTS: 每个引出点（分支点）：从哪条连线取出信号、引到哪里\n"
    "EDGES: 每条连线一行，格式「from -> to」，箭头方向按图中实际箭头；"
    "起点/终点看不清时写「不确定，需核对原图」\n"
    "FEEDBACK/FEEDFORWARD: 明确列出哪些是反馈通路、哪些是前馈通路"
    "（只能依据上面列出的 EDGES 判断，不得另行脑补）\n"
    "第二阶段（人类可读描述）：只基于第一阶段清单写一段简短连接关系描述。\n"
    "高压线：禁止凭领域常识或教材套路补线、猜连接、猜正负号；"
    "看不清引出点、箭头、正负号、器件身份时，必须明确写「不确定，需核对原图」。"
    "不要把推测写成「确实可见」。\n"
    + MATH_NOTATION_RULES
    + "数学排版要求只适用于本次可见标注，不要扩大到转录全部印刷题干。\n"
    "描述完后，你的回复最后一行必须原样输出以下三种之一"
    "（表示你在这张图中是否看到手写内容/人工书写标注）：\n"
    "HANDWRITING_PRESENCE_SEEN: none\n"
    "HANDWRITING_PRESENCE_SEEN: possible\n"
    "HANDWRITING_PRESENCE_SEEN: confirmed"
)

_PRESENCE_LABELS = {
    "none": "AI 判断：未检测到手写内容",
    "possible": "AI 判断：疑似手写（待核对）",
    "confirmed": "AI 判断：存在手写内容",
}
_PRESENCE_LINE_PATTERN = re.compile(
    r"(?im)^\s*HANDWRITING_PRESENCE\s*[:：]\s*(none|possible|confirmed)\s*$"
)
_PRESENCE_SEEN_PATTERN = re.compile(
    r"(?im)^\s*HANDWRITING_PRESENCE_SEEN\s*[:：]\s*(none|possible|confirmed)\s*$"
)


def _split_presence_declaration(
    content: str, pattern: re.Pattern[str]
) -> tuple[str | None, str]:
    """Split a declared presence line out of the model output.

    Returns ``(level, body)``.  ``level`` is None when the model failed to
    follow the format — callers treat that as "undeclared" (conservative),
    never as an implicit confirmation.
    """

    match = pattern.search(content)
    if match is None:
        return None, content.strip()
    level = match.group(1).lower()
    body = (content[: match.start()] + content[match.end() :]).strip()
    return level, body


def _presence_badge(level: str | None) -> str:
    if level in _PRESENCE_LABELS:
        return _PRESENCE_LABELS[level]
    return "AI 未给出手写判定（需人工核对）"


def render_visual_reading_section(page: Page) -> None:
    """Stage-1 status + stage-2 user-triggered actions for one page.

    Fail-safe by design: the visual section is auxiliary, so any failure to
    build its service (for example inside AppTest harnesses that forbid real
    settings) silently skips the section instead of breaking page review.
    """

    try:
        service = application_page_visual_service()
        state = service.get_page_visual_state(page.id)
    except Exception:  # noqa: BLE001 - auxiliary section must never break review
        LOGGER.debug("页面视觉区块不可用", exc_info=True)
        return
    st.markdown("**视觉内容（双阶段解析）**")
    # V086-302-PRT1: the provenance checklist (已检测/状态/方式/时间) is
    # rendered as explicit labeled fields on its own line — a bracketed
    # suffix inside a sentence was not visible enough in the real retest.
    provenance = visual_detection_provenance_fields(state)
    st.caption(" · ".join(f"{key}：{value}" for key, value in provenance.items()))
    st.caption(_stage1_status_line(state))

    # V086-R1 FIX-5: multiple same-looking drafts must be tellable apart
    # from the collapsed titles — carry the record time and mark the newest.
    latest_reading_id = max(
        (int(r["id"]) for r in state["interpretations"]), default=None
    )
    for reading in state["interpretations"]:
        provenance_label = PROVENANCE_LABELS.get(
            str(reading["provenance"]), "AI 识别草稿"
        )
        status_label = INTERPRETATION_STATUS_LABELS.get(
            str(reading["status"]), str(reading["status"])
        )
        user_edited = bool(reading.get("user_edited"))
        edited_badge = " · 你已修改" if user_edited else ""
        created_at_text = format_beijing_time(reading.get("created_at"), empty="")
        time_badge = f" · {created_at_text}" if created_at_text else ""
        is_newest = (
            latest_reading_id is not None
            and int(reading["id"]) == latest_reading_id
        )
        newest_badge = " · 最新" if is_newest else ""
        presence_label = ""
        raw_region = reading.get("region_json")
        if raw_region:
            try:
                import json as _json

                region = _json.loads(raw_region) if isinstance(raw_region, str) else raw_region
                if isinstance(region, dict):
                    presence_label = (
                        " · " + _presence_badge(region.get(HANDWRITING_PRESENCE_KEY))
                    )
            except (TypeError, ValueError):
                presence_label = ""
        expander_title = (
            f"{provenance_label} · {status_label}"
            f"{edited_badge}{newest_badge}{time_badge}{presence_label}"
        )
        with st.expander(expander_title):
            st.caption(
                "AI 识别草稿仅供参考；你可以直接修改并保存，保存后就是你的确认内容，"
                "重新识别也不会覆盖你保存的修改。"
            )
            edited_content = st.text_area(
                "识别内容（可直接修改）",
                value=str(reading["content"]),
                height=220,
                key=f"visual_edit_{reading['id']}",
                label_visibility="collapsed",
            )
            action_columns = st.columns(3)
            save_clicked = action_columns[0].button(
                "保存修改（成为我的确认内容）",
                key=f"save_visual_{reading['id']}",
                type="primary",
            )
            confirm_clicked = False
            if reading["status"] == "draft":
                confirm_clicked = action_columns[1].button(
                    "不改内容，直接确认这条",
                    key=f"confirm_visual_{reading['id']}",
                )
            # §25: deleting a reading is a user *negation*; downstream
            # AI-derived, not-yet-confirmed fields on this page's questions
            # are invalidated instead of silently surviving the deletion.
            delete_clicked = action_columns[2].button(
                "删除这条草稿（不影响原图与页面）",
                key=f"delete_visual_{reading['id']}",
            )
            if delete_clicked:
                try:
                    binding = service.delete_interpretation(int(reading["id"]))
                except PageVisualError as exc:
                    st.error(f"删除失败：{exc}")
                else:
                    _invalidate_downstream_questions(binding["page_id"], str(reading["id"]))
                    st.success("已删除该草稿；本页关联题目的 AI 派生草稿已同步失效。")
                    st.rerun()
            if save_clicked:
                try:
                    service.update_interpretation(
                        int(reading["id"]), content=edited_content
                    )
                    st.success("已保存为你确认的内容。")
                    st.rerun()
                except PageVisualError as exc:
                    st.error(f"保存失败：{exc}")
            elif confirm_clicked:
                try:
                    service.confirm_interpretation(int(reading["id"]))
                    st.rerun()
                except PageVisualError as exc:
                    st.error(f"确认失败：{exc}")

    provider = None
    try:
        provider = application_ai_provider()
    except Exception as exc:  # noqa: BLE001 - UI must survive any provider error
        LOGGER.warning("读取 AI provider 失败：%s", type(exc).__name__)
        provider = None
    if provider is None:
        st.caption("视觉读取需要先在设置中配置 AI 服务；未配置时此功能保持关闭。")
        return

    action_column, parse_column = st.columns(2)
    # V086-304: a second click must be an explicit re-read, never an
    # accidental duplicate. When an active draft already exists for a
    # provenance, the button is relabelled so the user sees the existing
    # draft above and consciously chooses to add a new reading.
    active_draft_kinds = {
        str(reading["provenance"])
        for reading in state["interpretations"]
        if reading.get("status") == "draft"
    }
    handwriting_label = (
        "重新读取手写内容（会另存一条新草稿）"
        if "HANDWRITING_VISION" in active_draft_kinds
        else "读取手写内容"
    )
    visual_label = (
        "重新解析图片与图表（会另存一条新草稿）"
        if "IMAGE_REGION" in active_draft_kinds
        else "解析图片与图表"
    )
    if action_column.button(handwriting_label, key=f"read_handwriting_{page.id}"):
        _run_vision_read(service, page, "HANDWRITING_VISION", _READ_HANDWRITING_PROMPT)
    if parse_column.button(visual_label, key=f"parse_visual_{page.id}"):
        _run_vision_read(service, page, "IMAGE_REGION", _INTERPRET_VISUAL_PROMPT)


def _invalidate_downstream_questions(page_id: int, deleted_reading_id: str) -> None:
    """§25 invalidation hook: clear AI-derived unconfirmed question fields
    bound to this page after the user deleted one of its visual readings."""

    try:
        from src.learning_workflow_service import QuestionService
        from src.runtime import application_database

        question_service = QuestionService(application_database())
    except Exception:  # noqa: BLE001 - auxiliary section must never break review
        LOGGER.debug("下游失效不可用（跳过）", exc_info=True)
        return
    for question in question_service.list_questions_for_page(page_id):
        try:
            question_service.invalidate_ai_derived_fields(
                question.id,
                reason=f"用户删除了页面视觉草稿 #{deleted_reading_id}，"
                "AI 派生的未确认内容随之失效。",
            )
        except Exception:  # noqa: BLE001 - never break the review page
            LOGGER.warning("下游失效失败：question=%s", question.id, exc_info=True)


def _run_vision_read(
    service: PageVisualService, page: Page, provenance: str, prompt: str
) -> None:
    image_path = Path(page.image_path)
    if not image_path.is_file():
        st.error("页面图片文件缺失，无法执行视觉读取。")
        return
    provider = application_ai_provider()
    if provider is None:
        st.warning("AI 服务未配置，视觉读取不可用。")
        return
    # The full-resolution page PNG is never inlined raw (V086-204): the
    # visual input budget downscales/re-encodes it into a bounded JPEG
    # payload before the request, with size-only telemetry logged.
    try:
        prepared = prepare_page_image(image_path.read_bytes())
    except VisualInputBudgetError as exc:
        LOGGER.error("视觉输入预算拦截：page_id=%s detail=%s", page.id, exc)
        st.error(f"视觉读取失败：{exc}")
        return
    try:
        completion = provider.complete_vision(
            prompt,
            prepared.data_url,
            source_feature="page_visual_reading",
            target_refs=(f"page:{page.id}",),
        )
    except ProviderCallError as exc:
        # Redaction-safe diagnostics: failure class, HTTP status, retry count
        # and sizes - never request/response content.
        LOGGER.error(
            "视觉读取失败（Provider 调用）：page_id=%s error_class=%s retry_count=%s "
            "http_status=%s input_chars=%s finish_reason=%s",
            page.id,
            exc.error_class,
            exc.retry_count,
            getattr(getattr(exc, "detail", None), "http_status", None),
            len(prompt) + prepared.base64_chars,
            exc.finish_reason,
        )
        st.error(f"视觉读取失败：{exc}")
        return
    except AIUnavailableError as exc:
        LOGGER.warning(
            "视觉读取未执行（AI 不可用）：page_id=%s detail=%s", page.id, exc
        )
        st.warning(f"视觉读取不可用：{exc}")
        return
    except AIExecutionError as exc:
        LOGGER.error(
            "视觉读取失败（执行错误）：page_id=%s error_class=%s retry_count=%s "
            "input_chars=%s",
            page.id,
            exc.error_class,
            exc.retry_count,
            len(prompt) + prepared.base64_chars,
        )
        st.error(f"视觉读取失败：{exc}")
        return
    except Exception as exc:  # noqa: BLE001 - user-facing failure surface
        LOGGER.error("视觉读取失败：%s detail=%s", type(exc).__name__, exc)
        st.error(f"视觉读取失败：{exc}")
        return
    content = getattr(completion, "text", "") or ""
    if not content.strip():
        st.warning("视觉读取未返回内容。")
        return
    # Presence gate (fix round §18): parse the model's own declaration.
    # Undeclared output degrades to "possible" (待核对), never to confirmed.
    region_json: dict = {}
    if provenance == "HANDWRITING_VISION":
        level, body = _split_presence_declaration(content, _PRESENCE_LINE_PATTERN)
        if level is None:
            LOGGER.warning(
                "手写读取未返回 presence 声明：page_id=%s（按 possible 处理）", page.id
            )
            level = "possible"
            st.caption("模型未按格式给出手写判定，已按「疑似手写（待核对）」处理。")
        region_json[HANDWRITING_PRESENCE_KEY] = level
        content = body if body else content
    elif provenance == "IMAGE_REGION":
        level, body = _split_presence_declaration(content, _PRESENCE_SEEN_PATTERN)
        if level is not None:
            region_json[HANDWRITING_PRESENCE_KEY] = level
            content = body if body else content
        # §30 lightweight topology marker: when the model followed the
        # structured-first contract, record it so downstream can tell an
        # auditable edge list from free-form prose.
        if "EDGES:" in content and "BLOCKS:" in content:
            region_json["topology_structured"] = True
    try:
        service.record_interpretation(
            page.id,
            provenance=provenance,
            content=content,
            confidence="uncertain",
            origin="ai_vision",
            region_json=region_json or None,
        )
    except PageVisualError as exc:
        st.error(f"保存解析结果失败：{exc}")
        return
    if region_json.get(HANDWRITING_PRESENCE_KEY) == "none":
        st.success("已读取：未检测到可辨识的手写内容（纯印刷页），不会生成手写整理。")
    else:
        st.success("解析结果已保存（草稿状态，等你确认）。")
    # Cross-channel consistency (fix round §20): a handwriting claim that
    # contradicts the region channel must surface immediately, not later.
    if region_json.get(HANDWRITING_PRESENCE_KEY) in {"possible", "confirmed"}:
        try:
            consistency = service.check_handwriting_consistency(page.id)
        except PageVisualError:
            consistency = None
        if consistency is not None and consistency.get("conflict"):
            st.warning(
                "两个视觉通道结论冲突：手写通道声称存在手写，"
                "但图片/图表通道未看到任何手写标注。"
                "请对照原图人工核对后再确认，不要直接采信。"
            )
    st.rerun()


def _safe_state(service: PageVisualService, page_id: int) -> dict | None:
    try:
        return service.get_page_visual_state(page_id)
    except PageVisualError as exc:
        LOGGER.error("读取页面视觉状态失败：%s", exc)
        st.error(f"读取页面视觉状态失败：{exc}")
        return None


def _stage1_status_line(state: dict) -> str:
    status = state["visual_detection_status"]
    if status == "not_checked" and not state.get("visual_detected_at"):
        return "尚未进行视觉内容检测。"
    parts = []
    if state["has_handwriting"] == 1:
        parts.append("检测到疑似手写/扫描内容，可进一步读取")
    if state["has_visual_content"] == 1:
        parts.append("检测到图片/图表类内容，可进一步解析")
    if not parts:
        parts.append("未检测到手写或图片/图表类视觉内容")
    line = "；".join(parts) + "。"
    if status == "uncertain":
        line += "（检测结果不确定，以实际读取为准）"
    # Provenance itself (ran/when/how/failed) is rendered separately as
    # explicit labeled fields by render_visual_reading_section (V086-302).
    return line
