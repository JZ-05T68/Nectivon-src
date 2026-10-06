"""Ask the local Agent questions about documents the user explicitly added.

The page keeps one simple path: question → answer → original document pages.

Frontend ownership notes for later polish:

- ``src/demo_ui.py`` owns every display mapping (states, badges, labels,
  citation chips, session-key contracts). Keep this page free of business
  mapping logic; it only orchestrates Streamlit widgets and renders the
  view models.
- ``src/agent_client.py`` owns the Mode 1 loopback transport; Mode 2 uses
  the frozen ``src/demo`` mock client. Never add provider/tool fields or a
  second response schema here.
- All custom HTML goes through ``demo_ui.escape_text``; answers never render
  DTO JSON, internal ids, paths or traces.
"""

from __future__ import annotations

import logging
import re
import time
from types import SimpleNamespace
from typing import Any

import streamlit as st

from src import demo_ui
from src.agent.local_client import LocalDocumentAgentClient
from src.agent_chat_history_service import AgentChatHistoryService
from src.agent_document_reader import AgentReadingStore
from src.ai.provider import AIUnavailableError
from src.ai_ui import render_ai_unconfigured_notice
from src.demo import MockDemoClient
from src.demo.contracts import DemoHTTPError
from src.demo_ui import AgentMode, AskRecord, DisplayOutcome
from src.math_display import render_math_markdown
from src.runtime import (
    application_ai_provider,
    application_ai_vision_provider,
    application_database,
    application_knowledge_memory_service,
    application_page_image_reader,
    application_settings,
)
from src.source_metadata import InvalidSourceId, parse_source_id
from src.text_utils import ui_plaintext_digest
from src.time_display import format_beijing_time, to_beijing_time
from src.workspace_ui import render_workspace

LOGGER = logging.getLogger(__name__)

PAGE_MILESTONE_VERSION = "v0.6.1"
PAGE_VERSION_LINE = "答案不是终点：结论、适用条件和原文依据都能随时核对。"

MODE_KEY = "agent_mode"
QUESTION_INPUT_KEY = "agent_question_input"
PENDING_KEY = "agent_pending_question"
SEQ_KEY = "agent_request_seq"
RESULT_KEY = "agent_result"
SELECTED_KEY = "agent_selected_source_id"
LIVE_BASE_KEY = "agent_base_url"
MOCK_CLIENT_KEY = "agent_mock_client"
LIVE_CLIENT_KEY = "agent_live_client"
SAVED_MEMORY_KEY = "agent_saved_memory_sequence"
CONV_KEY = "agent_conversation_id"
#: NOTE: must NOT equal the「新建对话」button's widget key — Streamlit forbids
#: writing a widget-backed session key after instantiation (R3 dynamic-state
#: red team catch: the collision silently killed the 新建对话 handler).
NEW_CONV_KEY = "agent_new_conversation_flag"

_CSS = """
/* Nectivon Agent Workspace — page-local styles only (ekb-aw-* namespace). */


/* Hide the Streamlit toolbar chrome (Deploy menu / status widget) on this
   competition surface only; sidebar navigation stays reachable. */
div[data-testid="stAppDeployButton"] { display: none; }
/* The fixed Streamlit header bar is opaque white and, at this page's compact
   0.9rem top padding, would cover the brand kicker. Keep it transparent so
   the brand line shows while the sidebar toggle stays reachable. */
[data-testid="stHeader"] { background-color: transparent; }

/* --- header & brand --- */
.ekb-aw-header { display: flex; align-items: flex-end; justify-content: space-between;
    gap: 1.5rem; padding-bottom: 0.65rem; margin-bottom: 0.6rem;
    border-bottom: 1px solid #e3eae5; }
.ekb-aw-brand { font-size: 12.5px; font-weight: 700; letter-spacing: 0.14em;
    color: #167d65; text-transform: uppercase; }
.ekb-aw-title { font-size: 30px; font-weight: 650; color: #20382f; margin: 2px 0 3px; }
.ekb-aw-sub { font-size: 13px; color: #75867c; }
.ekb-aw-mode-badge { display: inline-flex; align-items: center; gap: 7px;
    padding: 6px 14px; border-radius: 999px; font-size: 13px; font-weight: 800;
    white-space: nowrap; }
.ekb-aw-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.ekb-aw-mode-mock { background: #fef3c7; color: #92400e; border: 1px solid #fcd34d; }
.ekb-aw-mode-mock .ekb-aw-dot { background: #d97706; }
.ekb-aw-mode-live { background: #ecfdf5; color: #166534; border: 1px solid #a7f3d0; }
.ekb-aw-mode-live .ekb-aw-dot { background: #16a34a; }

/* R5 closure (R4-02 regression): the conversation-bar expander label must
   stay on one line - "历史对话（12）" never wraps, it just widens. */
section[data-testid="stExpander"] summary p { white-space: nowrap; }

.ekb-aw-card { background: #ffffff; border: 1px solid #e3eae5; border-radius: 12px;
    padding: 16px 22px; margin-bottom: 12px; }
.ekb-aw-q-kicker { font-size: 12px; font-weight: 700; color: #167d65;
    letter-spacing: 0.1em; margin-bottom: 3px; }
.ekb-aw-q-text { font-size: 18.5px; font-weight: 750; color: #20382f;
    line-height: 1.55; margin-bottom: 8px; }
.ekb-aw-badges { display: flex; flex-wrap: wrap; gap: 8px; margin: 2px 0 10px;
    align-items: center; }
.ekb-aw-badge { display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 12px; border-radius: 999px; font-size: 13px; font-weight: 750; }
.ekb-aw-badge-ok { background: #ecfdf5; color: #15803d; border: 1px solid #a7f3d0; }
.ekb-aw-badge-warn { background: #fffbeb; color: #b45309; border: 1px solid #fde68a; }
.ekb-aw-badge-empty { background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; }
.ekb-aw-badge-error { background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; }
.ekb-aw-badge-muted { background: #f6f8f5; color: #75867c; border: 1px solid #e3eae5; }
.ekb-aw-count { display: inline-flex; align-items: center; gap: 5px;
    padding: 4px 12px; border-radius: 999px; font-size: 12.5px; font-weight: 700;
    background: #edf6ef; color: #167d65; border: 1px solid #cce5d6; }

.ekb-aw-answer { font-size: 16.5px; line-height: 1.9; color: #1e293b; }
/* pre-line: model-inserted newlines (e.g. normalized "1. / 2. / 3." items)
   must render as real line breaks instead of collapsing into one run-on
   paragraph; spaces still collapse and long CJK text still wraps normally.
   break-word: a long unbroken token (URL / model id) may break inside
   itself rather than overflow the card. */
.ekb-aw-answer p { margin: 0 0 10px; white-space: pre-line;
    overflow-wrap: break-word; }
.ekb-aw-answer p:last-child { margin-bottom: 2px; }
.ekb-aw-answer-head { display:flex; align-items:center; justify-content:space-between; gap:12px;
    border-top:1px solid #e3eae5; padding-top:13px; margin-top:6px; }
.ekb-aw-answer-head span { color:#20382f; font-size:14px; font-weight:700; }
.ekb-aw-answer-head small { color:#75867c; font-size:11.5px; }
.ekb-aw-cite { display: inline-flex; align-items: center; padding: 0 7px;
    margin: 0 2px; border-radius: 999px; background: #edf6ef; color: #167d65;
    border: 1px solid #cce5d6; font-size: 12.5px; font-weight: 700; line-height: 1.75;
    text-decoration:none; transition:background-color 160ms cubic-bezier(.16,1,.3,1),
    border-color 160ms cubic-bezier(.16,1,.3,1); }
.ekb-aw-cite:hover { background:#dfeee6; border-color:#94bda9; }
.ekb-aw-scope { margin-top:14px; padding:11px 13px; border-left:2px solid #9bbbad;
    background:#f7f9f8; color:#52675d; border-radius:0 8px 8px 0;
    font-size:13px; line-height:1.7; }
.ekb-aw-scope b { display:block; color:#334b40; font-size:12px; margin-bottom:2px; }

.ekb-aw-warn-banner { display: flex; gap: 8px; align-items: flex-start;
    background: #fffbeb; border: 1px solid #fde68a; color: #92400e;
    border-radius: 10px; padding: 10px 14px; font-size: 14px; margin: 8px 0 4px;
    line-height: 1.6; }

.ekb-aw-empty { text-align: center; padding: 30px 26px; border-style: dashed; }
.ekb-aw-empty-icon { font-size: 28px; color: #94a3b8; margin-bottom: 6px; font-weight: 800; }
.ekb-aw-empty-title { font-size: 18px; font-weight: 800; color: #334155; margin-bottom: 8px; }
.ekb-aw-empty p { font-size: 14.5px; color: #475569; line-height: 1.8; margin: 0 0 6px; }
.ekb-aw-hint { color: #75867c; font-size: 13px !important; }

.ekb-aw-fail { border-left: 4px solid #b91c1c; }
.ekb-aw-fail-title { font-size: 17px; font-weight: 800; color: #b91c1c; margin-bottom: 6px; }
.ekb-aw-fail p { color: #475569; font-size: 14.5px; line-height: 1.75; margin: 0 0 6px; }

.ekb-aw-idle-title { font-size: 19px; font-weight: 800; color: #20382f; margin-bottom: 6px; }
.ekb-aw-idle p { font-size: 14.5px; color: #475569; line-height: 1.7; margin: 0; }
.ekb-aw-idle ul { list-style: none; padding: 0; margin: 12px 0 0; }
.ekb-aw-idle li { padding: 8px 2px; font-size: 14.5px; color: #334155;
    border-top: 1px dashed #e3eae5; line-height: 1.6; }
.ekb-aw-idle li:first-child { border-top: none; }

.ekb-aw-panel-head { display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 8px; }
.ekb-aw-panel-title { font-size: 15.5px; font-weight: 800; color: #20382f; }
.ekb-aw-count-badge { font-size: 12.5px; font-weight: 700; color: #167d65;
    background: #edf6ef; border: 1px solid #cce5d6; border-radius: 999px; padding: 3px 10px; }
.ekb-aw-selected-flag { font-size: 12.5px; font-weight: 800; color: #167d65;
    margin: 10px 0 2px; }
.ekb-aw-panel-empty { text-align: center; color: #75867c; padding: 24px 18px;
    border: 1px dashed #cbd5e1; border-radius: 12px; font-size: 13.5px;
    line-height: 1.8; background: #f6f8f5; }
.ekb-aw-source-anchor { display:block; scroll-margin-top:24px; }

.ekb-aw-viewer { background: #f6f8f5; border: 1px solid #e3eae5; border-radius: 12px;
    padding: 14px 18px; margin-top: 12px; }
.ekb-aw-viewer-kicker { font-size: 11.5px; font-weight: 800; color: #167d65;
    letter-spacing: 0.1em; margin-bottom: 5px; }
.ekb-aw-viewer-title { font-size: 16.5px; font-weight: 800; color: #20382f;
    line-height: 1.5; margin-bottom: 8px; }
.ekb-aw-chiprow { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.ekb-aw-chip { font-size: 12.5px; font-weight: 700; color: #475569; background: #ffffff;
    border: 1px solid #cbd5e1; border-radius: 999px; padding: 2px 10px; }
.ekb-aw-viewer-row { font-size: 13.5px; color: #475569; margin-bottom: 4px; line-height: 1.6; }
.ekb-aw-viewer-row b { color: #334155; }
.ekb-aw-support { background: #edf6ef; border: 1px solid #cce5d6; color: #17654f;
    border-radius: 10px; padding: 9px 12px; font-size: 13.5px; line-height: 1.7;
    margin-top: 8px; }
.ekb-aw-support b { display: block; margin-bottom: 2px; }
.ekb-aw-integrity { border-radius: 10px; padding: 10px 13px; font-size: 13.5px;
    margin-top: 8px; line-height: 1.7; }
.ekb-aw-integrity-ok { background: #ecfdf5; border: 1px solid #a7f3d0; color: #166534; }
.ekb-aw-integrity-warn { background: #fffbeb; border: 1px solid #fde68a; color: #92400e; }
.ekb-aw-integrity-error { background: #fef2f2; border: 1px solid #fecaca; color: #b91c1c; }
.ekb-aw-integrity-muted { background: #f1f5f9; border: 1px solid #e3eae5; color: #75867c; }
.ekb-aw-integrity b { display: block; margin-bottom: 2px; }
.ekb-aw-note { font-size: 12.5px; color: #75867c; margin-top: 8px; line-height: 1.7; }

.ekb-aw-section-label { font-size: 13px; font-weight: 700; color: #334155;
    margin: 8px 0 6px; }
.ekb-aw-preset-q { font-size: 12.5px; color: #75867c; line-height: 1.55;
    margin: 4px 2px 0; min-height: 2.6em; overflow: hidden; display: -webkit-box;
    -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
"""


# ---------------------------------------------------------------------------
# Session-state callbacks (run before the widgets re-instantiate)
# ---------------------------------------------------------------------------


def _clear_result_state() -> None:
    for key in demo_ui.STATE_KEYS_CLEARED_ON_MODE_SWITCH:
        st.session_state.pop(key, None)


def _on_mode_change() -> None:
    _clear_result_state()


def _on_base_url_change() -> None:
    st.session_state.pop(LIVE_CLIENT_KEY, None)
    _clear_result_state()


def _queue_question(question: str) -> None:
    """Queue one ask from a callback (widget keys still assignable there)."""

    text = question.strip()
    if not text:
        return
    st.session_state[QUESTION_INPUT_KEY] = text
    st.session_state[PENDING_KEY] = text
    for key in demo_ui.STATE_KEYS_CLEARED_ON_ASK:
        st.session_state.pop(key, None)


def _select_source(stable_id: str) -> None:
    st.session_state[SELECTED_KEY] = stable_id


def _switch_to_mock(question: str) -> None:
    st.session_state[MODE_KEY] = AgentMode.MOCK_DEMO
    _clear_result_state()
    _queue_question(question)


def _reset_demo() -> None:
    """Operator reset: clear ALL demo presentation state, nothing else.

    Clears session keys only (mode, result, selected source, request
    sequence, live base URL override). Never touches the database, files,
    fixtures or services.
    """

    for key in demo_ui.AGENT_SESSION_KEYS:
        st.session_state.pop(key, None)


# ---------------------------------------------------------------------------
# Client seam
# ---------------------------------------------------------------------------


def _safe_ai_provider() -> Any | None:
    """Fail closed to the shared setup UX when local AI config is invalid."""

    try:
        return application_ai_provider()
    except AIUnavailableError:
        LOGGER.warning("AI Provider 配置当前不可用")
        return None


def _resolve_client(mode: AgentMode) -> Any:
    if mode is AgentMode.MOCK_DEMO:
        client = st.session_state.get(MOCK_CLIENT_KEY)
        if client is None:
            client = MockDemoClient()
            st.session_state[MOCK_CLIENT_KEY] = client
        return client
    cached = st.session_state.get(LIVE_CLIENT_KEY)
    if isinstance(cached, LocalDocumentAgentClient):
        return cached
    settings = application_settings()
    provider = _safe_ai_provider()
    client = LocalDocumentAgentClient(
        database=application_database(),
        provider=provider,
        readings=AgentReadingStore(settings.agent_readings_dir),
        page_image_reader=application_page_image_reader(),
        model=provider.default_model if provider is not None else "",
        vision_provider=application_ai_vision_provider(),
        vision_model=provider.default_model if provider is not None else None,
        pages_dir=settings.pages_dir,
    )
    st.session_state[LIVE_CLIENT_KEY] = client
    return client


# ---------------------------------------------------------------------------
# Ask execution (one place; coarse presentation states only)
# ---------------------------------------------------------------------------


def _execute_ask(question: str, mode: AgentMode, client: Any) -> None:
    """Run one ask synchronously and store a sequence-guarded record.

    The status labels are frontend presentation states only — the backend
    exposes no chain-of-thought, tool trace or step events, and none is
    claimed here. The pacing between labels is visual; mock responses
    themselves are instantaneous.
    """

    sequence = demo_ui.next_request_sequence(st.session_state.get(SEQ_KEY, 0))
    st.session_state[SEQ_KEY] = sequence
    steps = demo_ui.PRESENTATION_STEPS
    pace = demo_ui.PRESENTATION_PACE_SECONDS
    with st.status("正在处理问题…", expanded=True) as status:
        st.write(steps[0])
        time.sleep(pace)
        try:
            st.write(steps[1])
            time.sleep(pace)
            response = client.run_agent(question)
            st.write(steps[2])
            time.sleep(pace)
        except DemoHTTPError as error:
            failure = error.failure
            st.session_state[RESULT_KEY] = AskRecord(
                sequence=sequence,
                mode=mode,
                question=question,
                failure_code=failure.error.code,
                failure_http_status=error.http_status,
                failure_message=failure.error.message,
            )
            _persist_exchange(st.session_state[RESULT_KEY])
            status.update(label="请求未能完成", state="error", expanded=False)
            return
        except Exception:
            # Unexpected local errors still render the safe failed state;
            # the traceback stays in the local log, never in the UI.
            LOGGER.exception("Agent 请求发生未预期错误")
            st.session_state[RESULT_KEY] = AskRecord(
                sequence=sequence,
                mode=mode,
                question=question,
                failure_code="internal_failure",
            )
            _persist_exchange(st.session_state[RESULT_KEY])
            status.update(label="请求未能完成", state="error", expanded=False)
            return
        metadata: dict[str, Any] = {}
        unavailable: list[str] = []
        if response.citations:
            st.write(steps[3])
            for stable_id in response.citations:
                try:
                    metadata[stable_id] = client.get_source(stable_id)
                except DemoHTTPError:
                    LOGGER.warning("来源详情获取失败：%s", stable_id)
                    metadata[stable_id] = None
                    unavailable.append(stable_id)
        status.update(label="回答就绪", state="complete", expanded=False)
        st.session_state[RESULT_KEY] = AskRecord(
            sequence=sequence,
            mode=mode,
            question=question,
            response=response,
            source_metadata=metadata,
            source_unavailable=tuple(unavailable),
        )
        if response.citations:
            st.session_state[SELECTED_KEY] = response.citations[0]
        _persist_exchange(st.session_state[RESULT_KEY])


# ---------------------------------------------------------------------------
# 对话历史（R3 P0-B）：本地持久化 + 恢复 + 历史切换。
# 持久化失败绝不影响本次回答；失败调用按失败状态如实记录，不伪造答案。
# ---------------------------------------------------------------------------


def _chat_history_service() -> AgentChatHistoryService | None:
    try:
        return AgentChatHistoryService(application_database())
    except Exception:  # noqa: BLE001 - history must never break the page
        LOGGER.exception("对话历史服务不可用")
        return None


def _persist_exchange(record: AskRecord) -> None:
    """Best-effort persistence of one Q&A exchange (success OR failure)."""

    service = _chat_history_service()
    if service is None:
        return
    try:
        conversation_id = service.ensure_conversation(
            st.session_state.get(CONV_KEY),
            title=ui_plaintext_digest(record.question or "", 40),
        )
        st.session_state[CONV_KEY] = conversation_id
        st.session_state.pop(NEW_CONV_KEY, None)
        mode_label = (
            record.mode.value if hasattr(record.mode, "value") else str(record.mode)
        )
        if record.response is not None:
            outcome = demo_ui.classify_outcome(record.response)
            if outcome is DisplayOutcome.NO_EVIDENCE:
                status = "no_evidence"
            elif outcome is DisplayOutcome.FAILED:
                status = "failed"
            else:
                status = "success"
            service.record_exchange(
                conversation_id=conversation_id,
                question=record.question or "",
                answer=getattr(record.response, "answer", "") or "",
                status=status,
                mode=mode_label,
                model=str(getattr(record.response, "model", "") or ""),
                citations=tuple(getattr(record.response, "citations", ()) or ()),
            )
        else:
            service.record_exchange(
                conversation_id=conversation_id,
                question=record.question or "",
                answer="",
                status="failed",
                mode=mode_label,
                failure_code=str(record.failure_code or ""),
            )
    except Exception:  # noqa: BLE001 - answering must never depend on storage
        LOGGER.exception("对话历史保存失败（不影响本次回答）")


def _ensure_restored_from_history() -> None:
    """Rebuild the newest exchange of the current conversation after reload."""

    if RESULT_KEY in st.session_state:
        return
    service = _chat_history_service()
    if service is None:
        return
    try:
        conversation_id = st.session_state.get(CONV_KEY)
        if conversation_id is None:
            if st.session_state.get(NEW_CONV_KEY):
                return  # 用户刚点了「新建对话」：保持空白，不偷偷恢复旧会话
            conversations = service.list_conversations(limit=1)
            if not conversations:
                return
            conversation_id = conversations[0].id
            st.session_state[CONV_KEY] = conversation_id
        turns = service.list_turns(conversation_id)
        answer_turn = next(
            (turn for turn in reversed(turns) if turn.role == "answer"), None
        )
        if answer_turn is None:
            return
        question_turn = next(
            (
                turn
                for turn in reversed(turns)
                if turn.role == "question"
                and turn.created_at <= answer_turn.created_at
            ),
            None,
        )
        if question_turn is None:
            return
        if answer_turn.status == "failed":
            record = AskRecord(
                sequence=0,
                mode=AgentMode.LOCAL_AGENT,
                question=question_turn.content,
                failure_code=answer_turn.failure_code or "internal_failure",
            )
        else:
            response = SimpleNamespace(
                answer=answer_turn.content,
                citations=tuple(answer_turn.citations),
                status="completed",
                grounded=answer_turn.status == "success",
                warnings=(),
                error=None,
                citations_detail=None,
                model=answer_turn.model or "",
            )
            record = AskRecord(
                sequence=0,
                mode=AgentMode.LOCAL_AGENT,
                question=question_turn.content,
                response=response,
            )
            if response.citations:
                try:
                    client = _resolve_client(AgentMode.LOCAL_AGENT)
                    for stable_id in response.citations:
                        try:
                            record.source_metadata[stable_id] = client.get_source(
                                stable_id
                            )
                        except Exception:  # noqa: BLE001 - per-citation degrade
                            record.source_metadata[stable_id] = None
                except Exception:  # noqa: BLE001 - viewer degrades gracefully
                    LOGGER.debug("恢复来源详情失败（降级为无详情）", exc_info=True)
        st.session_state[RESULT_KEY] = record
        if record.response is not None and record.response.citations:
            st.session_state[SELECTED_KEY] = record.response.citations[0]
    except Exception:  # noqa: BLE001 - restore must never break the page
        LOGGER.exception("恢复对话历史失败")


def _render_conversation_bar() -> None:
    """新建对话 + 历史对话切换（最小可用，不做聊天产品化）。"""

    service = _chat_history_service()
    if service is None:
        return
    try:
        # R5 closure: the 1.4-ratio column re-introduced the R4-02 wrap
        # (「历史对话（1）」 broke onto two lines).  Widen the bar column and
        # pin the expander summary to one line.
        bar_columns = st.columns([0.9, 2.1, 5.0])
        current_id = st.session_state.get(CONV_KEY)
        if bar_columns[0].button("新建对话", key="agent_new_conversation"):
            st.session_state.pop(CONV_KEY, None)
            st.session_state[NEW_CONV_KEY] = True
            st.session_state.pop(RESULT_KEY, None)
            st.session_state.pop(SELECTED_KEY, None)
            st.session_state.pop(SEQ_KEY, None)
            st.rerun()
        conversations = [
            item
            for item in service.list_conversations(limit=12)
            if item.id != current_id
        ]
        if not conversations:
            bar_columns[1].caption("还没有其它历史对话。")
            return
        with bar_columns[1].expander(f"历史对话（{len(conversations)}）"):
            for item in conversations:
                row = st.columns([5, 1])
                title_text = demo_ui.escape_text(item.title or "（未命名对话）")
                when_text = demo_ui.escape_text(format_beijing_time(item.updated_at))
                row[0].markdown(
                    "<span style='font-size:12.5px'>"
                    f"**{title_text}** · {item.message_count} 条 · {when_text}</span>",
                    unsafe_allow_html=True,
                )
                if row[1].button("打开", key=f"agent_open_conv_{item.id}"):
                    st.session_state[CONV_KEY] = item.id
                    st.session_state.pop(RESULT_KEY, None)
                    st.session_state.pop(SELECTED_KEY, None)
                    st.rerun()
    except Exception:  # noqa: BLE001 - history bar must never break the page
        LOGGER.exception("历史对话栏渲染失败")


# ---------------------------------------------------------------------------
# HTML fragments (all dynamic text goes through demo_ui.escape_text)
# ---------------------------------------------------------------------------


def _badges_html(view_model: Any) -> str:
    parts = [
        f"<span class='ekb-aw-badge ekb-aw-badge-{badge.tone}'>"
        f"<span aria-hidden='true'>{badge.icon}</span>{demo_ui.escape_text(badge.text)}</span>"
        for badge in view_model.badges
    ]
    if view_model.chips:
        parts.append(
            f"<span class='ekb-aw-count'>依据 {view_model.citation_count} 条</span>"
        )
    if view_model.is_mock:
        parts.append("<span class='ekb-aw-badge ekb-aw-badge-muted'>预置演示数据</span>")
    return f"<div class='ekb-aw-badges'>{''.join(parts)}</div>"


def _answer_paragraphs_html(view_model: Any) -> str:
    paragraphs: list[str] = []
    for segments in view_model.paragraphs:
        pieces: list[str] = []
        for segment in segments:
            if segment.kind == "citation" and segment.index is not None:
                pieces.append(
                    f"<a class='ekb-aw-cite' href='#nectivon-source-{segment.index}' "
                    f"title='跳转到来源 {segment.index}'>[{segment.index}]</a>"
                )
            else:
                plain_text = segment.text.replace("**", "")
                plain_text = re.sub(r"(^|\s)[*-]\s+", r"\1• ", plain_text)
                pieces.append(demo_ui.escape_text(plain_text))
        paragraphs.append(f"<p>{''.join(pieces)}</p>")
    return f"<div class='ekb-aw-answer'>{''.join(paragraphs)}</div>"


def _answer_paragraphs_markdown(view_model: Any) -> str:
    """Reassemble the mapped answer for Markdown/KaTeX read-mode rendering."""

    paragraphs: list[str] = []
    for segments in view_model.paragraphs:
        pieces: list[str] = []
        for segment in segments:
            if segment.kind == "citation" and segment.index is not None:
                pieces.append(
                    f"[来源 {segment.index}](#nectivon-source-{segment.index})"
                )
            else:
                pieces.append(segment.text)
        paragraphs.append("".join(pieces))
    return "\n\n".join(paragraphs)


def _question_card_open_html(view_model: Any) -> str:
    return (
        "<div class='ekb-aw-card'>"
        "<div class='ekb-aw-q-kicker'>当前问题</div>"
        f"<div class='ekb-aw-q-text'>{demo_ui.escape_text(view_model.question)}</div>"
    )


def _render_idle_card() -> None:
    st.markdown(
        "<div class='ekb-aw-card' style='padding:22px 26px'>"
        "<div class='ekb-aw-idle-title'>基于你的知识开始提问</div>"
        "<p>AI 会从已经读完的资料里整理答案，并把关键结论连接回原文。</p>"
        "<ul>"
        "<li><b>答案</b> —— 先给出当前资料能够支持的结论</li>"
        "<li><b>依据与原文</b> —— 引用可定位到资料名称和页码</li>"
        "<li><b>由你决定是否保存</b> —— 问答不会自动成为长期知识</li>"
        "</ul></div>",
        unsafe_allow_html=True,
    )


def _render_grounded_answer(view_model: Any) -> None:
    # Raw HTML blocks do not pass their inner text through Streamlit's math
    # Markdown pipeline.  Keep the decorative shell as escaped HTML, but put
    # the answer itself on the shared read-mode renderer so standard LaTeX is
    # handled by KaTeX and unsupported mhchem fails closed.
    with st.container(border=True):
        st.markdown(
            "<div class='ekb-aw-q-kicker'>当前问题</div>"
            f"<div class='ekb-aw-q-text'>{demo_ui.escape_text(view_model.question)}</div>"
            + _badges_html(view_model),
            unsafe_allow_html=True,
        )
        if view_model.warnings:
            for warning in view_model.warnings:
                st.markdown(
                    "<div class='ekb-aw-warn-banner'><span aria-hidden='true'>⚠</span>"
                    f"<span>{demo_ui.escape_text(warning)}</span></div>",
                    unsafe_allow_html=True,
                )
        st.markdown(
            "<div class='ekb-aw-answer-head'><span>答案</span>"
            "<small>引用锚点可跳转到下方来源</small></div>",
            unsafe_allow_html=True,
        )
        render_math_markdown(_answer_paragraphs_markdown(view_model))
        st.markdown(
            "<div class='ekb-aw-scope'><b>适用条件</b>"
            "结论仅覆盖当前已读资料明确说明的范围；涉及版本、参数或现场条件时，"
            "请以引用原文为准。</div>",
            unsafe_allow_html=True,
        )


def _render_empty_state(view_model: Any) -> None:
    st.markdown(
        _question_card_open_html(view_model)
        + _badges_html(view_model)
        + "<div class='ekb-aw-card ekb-aw-empty'>"
        + "<div class='ekb-aw-empty-icon' aria-hidden='true'>○</div>"
        + "<div class='ekb-aw-empty-title'>当前知识库中没有找到足够资料支持这个问题。</div>"
        + f"<p>{demo_ui.escape_text(view_model.answer_text)}</p>"
        + "<p class='ekb-aw-hint'>"
        + demo_ui.escape_text(view_model.hint or "")
        + "</p></div></div>",
        unsafe_allow_html=True,
    )
def _render_failure_state(view_model: Any, mode: AgentMode, question: str) -> None:
    st.markdown(
        _question_card_open_html(view_model)
        + _badges_html(view_model)
        + "<div class='ekb-aw-fail' style='border-radius:10px;padding:12px 16px'>"
        + "<div class='ekb-aw-fail-title'>"
        + demo_ui.escape_text(view_model.failure_headline or "本次请求失败")
        + "</div><p>"
        + demo_ui.escape_text(view_model.failure_detail or "")
        + "</p></div></div>",
        unsafe_allow_html=True,
    )
    st.button(
        "重试",
        key="failure_retry",
        on_click=_queue_question,
        args=(question,),
    )


def _render_answer_area() -> None:
    record = st.session_state.get(RESULT_KEY)
    sequence = st.session_state.get(SEQ_KEY, 0)
    if record is None or demo_ui.is_stale_result(record.sequence, sequence):
        _render_idle_card()
        return
    if record.transport_failed:
        view_model = demo_ui.build_failure_view_model(record)
    else:
        view_model = demo_ui.build_answer_view_model(
            record.question, record.response, record.source_metadata
        )
    if view_model.outcome is DisplayOutcome.NO_EVIDENCE:
        _render_empty_state(view_model)
    elif view_model.outcome in (
        DisplayOutcome.FAILED,
        DisplayOutcome.RATE_LIMITED,
        DisplayOutcome.BACKEND_UNAVAILABLE,
    ):
        _render_failure_state(view_model, record.mode, record.question)
    else:
        _render_grounded_answer(view_model)


def _render_save_memory_button() -> None:
    """Offer one explicit user-controlled write after a completed answer.

    The saved record is a raw Q&A copy (``kind='raw_qa'``): it only means the
    user chose to keep this exact question and answer. It is never presented
    as user experience, and the save path neither truncates silently nor
    creates exact duplicates.
    """

    record = st.session_state.get(RESULT_KEY)
    sequence = st.session_state.get(SEQ_KEY, 0)
    if (
        record is None
        or demo_ui.is_stale_result(record.sequence, sequence)
        or record.transport_failed
        or record.response is None
        or record.response.status != "completed"
        or not record.response.answer.strip()
    ):
        return
    st.divider()
    st.caption(
        "保存的是这个问题和这次回答的副本，只表示你留下了这一问一答，"
        "不会自动变成你的知识或笔记。保存后可在“我保存过的内容”中再次查看。"
    )
    saved = st.session_state.get(SAVED_MEMORY_KEY) == sequence
    if st.button(
        "保存这次问答",
        icon=":material/bookmark_add:",
        key=f"save_agent_memory_{sequence}",
        disabled=saved,
        use_container_width=False,
    ):
        cited_page_ids: list[int] = []
        for stable_id in record.response.citations:
            try:
                _, kind, local_id = parse_source_id(stable_id)
            except InvalidSourceId:
                continue
            if kind == "page":
                cited_page_ids.append(local_id)
        try:
            result = application_knowledge_memory_service().create_raw_qa_entry(
                question=record.question,
                answer=record.response.answer,
                cited_page_ids=tuple(cited_page_ids),
            )
        except Exception as exc:
            LOGGER.exception("保存 Agent 对话失败")
            st.error(f"这次对话没有保存成功：{exc}")
            return
        st.session_state[SAVED_MEMORY_KEY] = sequence
        if result.entry is None and result.duplicate_of is not None:
            existing = result.duplicate_of
            st.info(
                "这次问答已经保存过（"
                f"{_saved_date(existing.created_at)}），没有重复创建。"
            )
        else:
            st.success("这次问答已保存，可在“我保存过的内容”中再次查看。")
            if result.skipped_citations:
                st.warning(
                    f"有 {result.skipped_citations} 条引用无法在本机解析，"
                    "没有写入保存副本。"
                )
    elif saved:
        st.success("这次问答已经保存，可在“我保存过的内容”中再次查看。")


def _saved_date(value: Any) -> str:
    """Return a short local date for duplicate-save messages."""

    local_value = to_beijing_time(value)
    return f"{local_value.month} 月 {local_value.day} 日"


# ---------------------------------------------------------------------------
# Right panel: verified sources + source viewer
# ---------------------------------------------------------------------------


def _viewer_html(source: Any) -> str:
    chip_row = [
        f"<span class='ekb-aw-chip'>{demo_ui.escape_text(source.type_label)}</span>",
        f"<span class='ekb-aw-chip'>{demo_ui.escape_text(source.location)}</span>",
    ]
    if source.citation_index is not None:
        chip_row.append(f"<span class='ekb-aw-chip'>出处 #{source.citation_index}</span>")
    support_html = ""
    if source.support_note and not source.unavailable:
        support_html = (
            "<div class='ekb-aw-support'><b>为什么和回答有关</b>"
            f"{demo_ui.escape_text(source.support_note)}</div>"
        )
    return (
        "<div class='ekb-aw-viewer'>"
        "<div class='ekb-aw-viewer-kicker'>原文上下文 · 原始资料</div>"
        f"<div class='ekb-aw-viewer-title'>{demo_ui.escape_text(source.title)}</div>"
        f"<div class='ekb-aw-chiprow'>{''.join(chip_row)}</div>"
        f"{support_html}"
        "</div>"
    )


def _viewer_details_html(source: Any) -> str:
    """Render advanced provenance details only after the user expands them."""

    integrity_html = ""
    if source.integrity is not None:
        integrity_html = (
            f"<div class='ekb-aw-integrity ekb-aw-integrity-{source.integrity.tone}'>"
            f"<b>核对提示 · {demo_ui.escape_text(source.integrity.label)}</b>"
            f"{demo_ui.escape_text(source.integrity.explanation)}</div>"
        )
    elif source.integrity_note:
        integrity_html = (
            "<div class='ekb-aw-integrity ekb-aw-integrity-muted'>"
            f"<b>核对提示</b>{demo_ui.escape_text(source.integrity_note)}</div>"
        )
    note_html = ""
    if source.note:
        note_html = f"<div class='ekb-aw-note'>{demo_ui.escape_text(source.note)}</div>"
    anchor_html = ""
    if source.anchor_note:
        anchor_html = (
            "<div class='ekb-aw-note'><b>引用上下文</b><br>"
            f"{demo_ui.escape_text(source.anchor_note)}</div>"
        )
    return f"{anchor_html}{integrity_html}{note_html}"


def _render_sources_panel() -> None:
    record = st.session_state.get(RESULT_KEY)
    sequence = st.session_state.get(SEQ_KEY, 0)
    fresh = record is not None and not demo_ui.is_stale_result(record.sequence, sequence)
    chips: tuple[Any, ...] = ()
    if fresh and not record.transport_failed:
        chips = demo_ui.build_citation_chips(record.response, record.source_metadata)
    st.markdown(
        "<div class='ekb-aw-panel-head'><span class='ekb-aw-panel-title'>来源与依据</span>"
        f"<span class='ekb-aw-count-badge'>{len(chips)} 条</span></div>",
        unsafe_allow_html=True,
    )
    if not chips:
        if not fresh:
            hint = "提问后，这里会显示答案来自哪份资料、哪一页。"
        elif record is not None and (
            record.transport_failed or record.response.status == "failed"
        ):
            hint = "请求未完成，暂时没有来源可显示。"
        else:
            hint = "这次没有找到能回答问题的资料，因此没有可查看的出处。"
        st.markdown(
            f"<div class='ekb-aw-panel-empty'>{demo_ui.escape_text(hint)}</div>",
            unsafe_allow_html=True,
        )
        return
    unavailable = set(record.source_unavailable)
    for position, chip in enumerate(chips):
        anchor_index = chip.display_index or position + 1
        st.markdown(
            f"<span id='nectivon-source-{anchor_index}' class='ekb-aw-source-anchor'></span>",
            unsafe_allow_html=True,
        )
        selected = st.session_state.get(SELECTED_KEY) == chip.stable_id
        if selected:
            st.markdown(
                "<div class='ekb-aw-selected-flag'>正在查看 ▸</div>",
                unsafe_allow_html=True,
            )
        location = chip.location or demo_ui.LOCATION_FALLBACK
        st.button(
            f"《{chip.title}》· {location}",
            key=f"src_{position}_{chip.stable_id}",
            on_click=_select_source,
            args=(chip.stable_id,),
            use_container_width=True,
            type="primary" if selected else "secondary",
        )
        caption = chip.type_label
        if chip.stable_id in unavailable:
            caption += " · 该来源暂时不可检查"
        st.caption(caption)
    selected_id = st.session_state.get(SELECTED_KEY)
    selected_chip = next((c for c in chips if c.stable_id == selected_id), None)
    if selected_chip is None:
        st.markdown(
            "<div class='ekb-aw-panel-empty'>点击上方一条来源，查看它为什么支持这条回答。</div>",
            unsafe_allow_html=True,
        )
        return
    metadata = record.source_metadata.get(selected_chip.stable_id)
    if metadata is None:
        viewer = demo_ui.build_source_unavailable(selected_chip)
    else:
        viewer = demo_ui.build_source_view_model(selected_chip, metadata)
    st.markdown(_viewer_html(viewer), unsafe_allow_html=True)
    details_html = _viewer_details_html(viewer)
    if details_html:
        with st.expander("展开来源详情（高级）"):
            st.markdown(details_html, unsafe_allow_html=True)
    try:
        _, source_kind, local_id = parse_source_id(selected_chip.stable_id)
    except InvalidSourceId:
        return
    if source_kind != "page":
        return
    page = application_database().get_page(local_id)
    if page is None:
        return
    if st.button(
        "定位到文件与原文",
        key=f"open_agent_source_page_{page.id}",
        icon=":material/open_in_new:",
        use_container_width=True,
    ):
        st.switch_page(
            "pages/17_我的资料.py",
            query_params={
                "document": str(page.document_id),
                "page": str(page.page_number),
            },
        )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="知识 Agent · Nectivon",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="auto",
)
render_workspace("pages/0_知识Agent.py")
st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)

mode = AgentMode.LOCAL_AGENT
st.session_state[MODE_KEY] = mode

mode_class = "ekb-aw-mode-live"
st.markdown(
    "<div class='ekb-aw-header'><div>"
    "<div class='ekb-aw-brand'>我的 Nectivon</div>"
    "<div class='ekb-aw-title'>与知识对话</div>"
    f"<div class='ekb-aw-sub'>{demo_ui.escape_text(PAGE_VERSION_LINE)}</div>"
    "</div><div>"
    f"<span class='ekb-aw-mode-badge {mode_class}'>"
    "<span class='ekb-aw-dot' aria-hidden='true'></span>"
    "仅使用已读资料</span>"
    "</div></div>",
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="ekb-agent-flow"><span><b>01</b> 写下问题</span>'
    '<span><b>02</b> 核对答案与条件</span><span><b>03</b> 回到来源原文</span></div>',
    unsafe_allow_html=True,
)

st.caption("先让知识进入工作台，再用自己的话提问；资料没有支持的内容不会被当作事实保存。")

_render_conversation_bar()

if _safe_ai_provider() is None:
    render_ai_unconfigured_notice()
    st.stop()

with st.form("agent_ask_form", border=True):
    composer_columns = st.columns([5, 1])
    question_value = composer_columns[0].text_input(
        "你的问题",
        key=QUESTION_INPUT_KEY,
        placeholder="例如：这份资料里规定的检验周期是多久？",
        label_visibility="collapsed",
    )
    submitted = composer_columns[1].form_submit_button(
        "提问", type="primary", use_container_width=True
    )
if submitted:
    text = (question_value or "").strip()
    if text:
        st.session_state[PENDING_KEY] = text
        for key in demo_ui.STATE_KEYS_CLEARED_ON_ASK:
            st.session_state.pop(key, None)
    else:
        st.info("请先输入一个问题。")

pending = st.session_state.pop(PENDING_KEY, None)
if pending is not None:
    try:
        client = _resolve_client(mode)
    except ValueError as error:
        st.error(str(error))
        st.stop()
    _execute_ask(pending, mode, client)
_ensure_restored_from_history()
_render_answer_area()
_render_sources_panel()
_render_save_memory_button()
