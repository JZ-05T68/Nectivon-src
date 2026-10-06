"""Ask-AI section for the search page (v0.5.3 Phase 3, UI first stage).

This is not a chatbot and not an agent. It renders one controlled entry
point: the user asks a question about the already-visible search results,
those results are projected into a ``KnowledgeContextPackage``, and the
audited answer chain returns one traceable ``AuditedAIOutput``.

Provider failures are contained inside this section and never affect the
existing search UI.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import streamlit as st

from src.ai.provider import (
    AIError,
    AIProductionCompositionError,
    AIUnavailableError,
    require_production_audited_provider,
)
from src.ai.rag_answer_service import RagAnswerError, RagAnswerService
from src.ai_ui import render_ai_unconfigured_notice
from src.database import Database
from src.knowledge_context import ContextItemProjector, ContextProjectionError
from src.knowledge_context_packager import (
    KnowledgeContextError,
    KnowledgeContextPackager,
)
from src.math_display import render_math_markdown
from src.models import ContextItem, KnowledgeSearchResult, SearchResult
from src.time_display import format_beijing_time

LOGGER = logging.getLogger(__name__)
ASK_QUESTION_KEY = "rag_ask_question"


def build_page_context_items(
    database: Database, results: Sequence[SearchResult]
) -> list[ContextItem]:
    """Project page-scope search results into read-only ContextItems."""

    projector = ContextItemProjector(database)
    items: list[ContextItem] = []
    for result in results:
        try:
            items.append(projector.project_page(result.page_id))
        except ContextProjectionError as exc:
            LOGGER.warning("页面投影失败，已跳过：%s", exc)
    return items


def build_knowledge_context_items(
    database: Database, results: Sequence[KnowledgeSearchResult]
) -> list[ContextItem]:
    """Project knowledge-scope results into read-only ContextItems."""

    projector = ContextItemProjector(database)
    items: list[ContextItem] = []
    for result in results:
        try:
            if result.result_type.value == "knowledge_object":
                items.append(projector.project_knowledge_object(result.id))
            elif result.result_type.value == "knowledge_memory":
                items.append(projector.project_knowledge_memory(result.id))
        except ContextProjectionError as exc:
            LOGGER.warning("知识投影失败，已跳过：%s", exc)
    return items


def _resolve_provider() -> object | None:
    """Return the audited provider; never substitute a user-visible mock."""

    from src.runtime import application_ai_provider

    try:
        provider = application_ai_provider()
    except AIProductionCompositionError:
        raise
    except Exception as exc:
        LOGGER.error("解析 AI provider 失败：error_type=%s", type(exc).__name__)
        provider = None
    return (
        require_production_audited_provider(provider)
        if provider is not None
        else None
    )


def render_ask_ai_section(
    database: Database,
    results: Sequence[object],
    *,
    scope: str,
    kb_uuid: str,
    app_version: str,
) -> None:
    """Render the controlled Ask-AI block for one search scope."""

    st.divider()
    st.subheader("Ask AI（受控回答）")
    st.caption(
        "AI 仅依据下方已选择的知识上下文回答；回答不会写回知识库，"
        "也不会修改任何原始资料。"
    )
    question = st.text_area(
        "向 AI 提问（基于当前检索结果）",
        key=ASK_QUESTION_KEY,
        height=80,
        placeholder="例如：这些资料对“编码器接线错误导致 PID 震荡”说明了什么？",
    )
    if st.button(
        "Ask AI",
        key=f"ask_ai_{scope}",
        type="primary",
        use_container_width=True,
    ):
        if scope == "knowledge":
            items = build_knowledge_context_items(database, results)  # type: ignore[arg-type]
        else:
            items = build_page_context_items(database, results)  # type: ignore[arg-type]
        if not items:
            st.warning("当前没有可送入 AI 的知识项（结果均已失效或被排除）。")
            return
        packager = KnowledgeContextPackager(
            kb_uuid=kb_uuid, app_version=app_version
        )
        try:
            package = packager.build(items, question=question)
        except KnowledgeContextError as exc:
            st.warning(str(exc))
            return
        provider = _resolve_provider()
        if provider is None:
            st.session_state.pop("rag_answer_output", None)
            st.session_state.pop("rag_answer_mock", None)
            render_ai_unconfigured_notice()
            return
        try:
            output = RagAnswerService(provider).answer(question, package)
        except RagAnswerError as exc:
            st.warning(str(exc))
            return
        except AIUnavailableError as exc:
            st.warning(f"AI 能力不可用（检索功能不受影响）：{exc}")
            return
        except AIError as exc:
            st.error(f"AI 服务调用失败（检索功能不受影响）：{exc}")
            return
        except Exception as exc:
            LOGGER.exception("Ask AI 回答失败")
            st.error(f"AI 回答失败：{exc}")
            return
        st.session_state["rag_answer_output"] = output
        st.session_state.pop("rag_answer_mock", None)

    output = st.session_state.get("rag_answer_output")
    if not output:
        return
    st.markdown("#### AI 回答")
    render_math_markdown(output.answer)
    st.caption(f"模型：{output.model}　|　生成时间：{format_beijing_time(output.generated_at)}")
    if output.token_usage is not None:
        st.caption(f"Token 用量：{output.token_usage.total_tokens}")
    with st.expander("查看引用来源与上下文范围", expanded=True):
        st.markdown("**AI 使用的知识（上下文范围）**")
        for stable_id in output.context_stable_ids:
            st.caption(stable_id)
        st.markdown("**引用映射**")
        for stable_id, number in output.citations:
            st.caption(f"{number} → {stable_id}")
        if output.answer_citations:
            st.markdown("**回答实际引用（已校验）**")
            for stable_id in output.answer_citations:
                st.caption(stable_id)
        if output.warnings:
            st.warning("；".join(output.warnings))
        if output.excluded:
            st.markdown("**AI 未使用的内容（排除项）**")
            for stable_id, reason in output.excluded:
                st.caption(f"{stable_id}：{reason}")
