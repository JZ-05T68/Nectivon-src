"""Verified local training questions with direct provenance to imported material.

There is no online-question path and no fixed target count.  Zero genuine
local variations means zero training questions, never generated filler.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

import streamlit as st

from src.local_training_source_policy import (
    is_local_training_result_payload,
    is_locatable_local_training_question,
)
from src.question_content_ui import render_training_question
from src.question_source_models import (
    QuestionRetrievalResult,
    VerifiedQuestion,
)
from src.question_source_retrieval_service import QuestionSourceRetrievalService
from src.targeted_training_models import TrainingTask
from src.training_profile_models import EducationType, LearnerProfile

ACTIVE_SESSION_ID_KEY: Final[str] = "targeted_training_active_session_id"

if TYPE_CHECKING:
    from src.training_session_service import TrainingSessionService

LOGGER = logging.getLogger(__name__)

RETRIEVAL_RESULT_SESSION_KEY: Final[str] = "targeted_training_retrieval_result"
RETRIEVAL_OFFSET_SESSION_KEY: Final[str] = "targeted_training_retrieval_offset"
SOURCE_STATUS_SESSION_KEY: Final[str] = "targeted_training_source_status"
SOURCE_POLICY_VERSION: Final[int] = 3


def render_question_source_button(q: VerifiedQuestion, key: str | None = None) -> None:
    """Render the standard [查询题目来源] button according to question source type."""
    if is_locatable_local_training_question(q):
        # 本地资料库一等题源：直达系统本地浏览资料页面并定位到具体页码
        target_page = q.page_number or 1
        st.page_link(
            "pages/3_浏览资料.py",
            label="🔍 查询题目来源",
            query_params={"document": str(q.document_id), "page": str(target_page)},
            help="直接跳转到本地真实资料库的对应原题页面",
            use_container_width=True,
        )
    else:
        # A stale/legacy non-local result must not offer an external link.
        st.warning("这道题缺少可定位的本地原页，不可作为训练题。")


def _render_question_card(idx: int, q: VerifiedQuestion) -> None:
    """Render a single verified question card with provenance button."""
    with st.container(border=True):
        col_content, col_action = st.columns([0.78, 0.22])

        with col_content:
            # 顶部标签栏
            year_str = f"{q.year}年 · " if q.year else ""
            q_num_str = f"第 {q.question_number} 题 · " if q.question_number else ""
            extra_badge = ""
            if q.school_name:
                extra_badge = f" &nbsp; `🏫 {q.school_name}`"
            elif q.contest_name:
                extra_badge = f" &nbsp; `🏆 {q.contest_name}`"

            st.markdown(
                f"**第 {idx} 题** &nbsp; "
                f"`✅ 已验证真实来源` &nbsp; "
                f"`{year_str}{q.exam_or_contest_name}` &nbsp; "
                f"`{q_num_str}{q.applicable_scope}`"
                f"{extra_badge}"
            )
            # 题目题干渲染
            render_training_question(q)

            # 展开查看防伪指纹与原题核验凭据
            with st.expander("🔍 查看真实来源核验详情与数字指纹", expanded=False):
                c1, c2 = st.columns(2)
                c1.caption(f"**来源名称**：{q.source_name}")
                c1.caption(f"**来源URL**：`{q.source_url}`")
                if q.school_name or q.major_direction:
                    school_info = f"{q.school_name or '-'} · {q.major_direction or '-'}"
                    c1.caption(f"**高校/专业方向**：{school_info}")
                if q.contest_name or q.contest_tier:
                    c2.caption(
                        f"**竞赛赛事/层级**：{q.contest_name or '-'} · {q.contest_tier or '-'}"
                    )
                c2.caption(f"**防伪指纹**：`{q.question_fingerprint}`")
                c2.caption(f"**核验时间**：{q.verified_at}")

        with col_action:
            st.write("")  # 垂直对齐间隙
            render_question_source_button(q, key=f"btn_src_{q.id}")


def render_training_questions_section(
    task: TrainingTask,
    retrieval_service: QuestionSourceRetrievalService,
    profile: LearnerProfile | None = None,
    session_service: TrainingSessionService | None = None,
) -> None:
    """Render the Phase 8 Local-First verified question retrieval and execution section."""
    st.markdown("### 📝 针对训练试题")

    task_key = f"{task.training_type}_{task.target}_{task.subject or 'all'}"
    result_key = f"{RETRIEVAL_RESULT_SESSION_KEY}_{task_key}"
    offset_key = f"{RETRIEVAL_OFFSET_SESSION_KEY}_{task_key}"
    status_key = f"{SOURCE_STATUS_SESSION_KEY}_{task_key}"
    policy_key = f"targeted_training_source_policy_{task_key}"

    if offset_key not in st.session_state:
        st.session_state[offset_key] = 0

    if st.session_state.get(policy_key) != SOURCE_POLICY_VERSION:
        st.session_state.pop(result_key, None)
        st.session_state.pop(status_key, None)
        st.session_state[policy_key] = SOURCE_POLICY_VERSION

    current_status = st.session_state.get(status_key)
    # A browser session opened before the local-only change may still hold an
    # external result.  Never render or reuse that cached payload.
    if current_status not in (None, "local_done"):
        st.session_state.pop(result_key, None)
        st.session_state.pop(status_key, None)
        current_status = None

    # ==================================================================
    # Only locally imported and individually locatable original questions.
    # ==================================================================
    with st.container(border=True):
        st.markdown("#### 🔍 从本地资料库查找变式题")
        st.caption("只返回能定位到本机原始资料的其他题目；不联网、不生成、不凑数量。")

        # 勾选框权限逻辑（仅高中生且开启相应项目时显示，默认不勾选）
        is_high_school = (
            profile is not None
            and profile.education_type == EducationType.BASIC
            and profile.basic is not None
            and profile.basic.stage == "高中"
        )
        show_sb_checkbox = bool(is_high_school and profile.basic.in_strong_base)
        show_comp_checkbox = bool(is_high_school and profile.basic.in_competition)

        col_src1, col_src2 = st.columns(2)
        include_sb = False
        include_comp = False

        if show_sb_checkbox:
            with col_src1:
                include_sb = st.checkbox(
                    "强基计划题源",
                    value=False,
                    key=f"chk_sb_{task_key}",
                    help="勾选后允许在检索中纳入目标高校对应专业的强基校测试题",
                )
        if show_comp_checkbox:
            with col_src2:
                include_comp = st.checkbox(
                    "学科竞赛题源",
                    value=False,
                    key=f"chk_comp_{task_key}",
                    help="勾选后允许在检索中纳入对应学科与备赛阶段的竞赛真题",
                )

        # No arbitrary page-size cap: all verified local variations are shown.
        btn_local_col, _ = st.columns([0.3, 0.7])
        with btn_local_col:
            if st.button(
                "🔍 本地查询",
                type="primary",
                key=f"btn_local_query_{task_key}",
                use_container_width=True,
                help="仅从本地已导入的资料库中检索真实原题",
            ):
                with st.spinner("正在检索本地资料库试题..."):
                    st.session_state.pop(result_key, None)
                    st.session_state.pop(status_key, None)
                    local_res = retrieval_service.retrieve_local_only(
                        task=task,
                        profile=profile,
                        include_strong_base=include_sb,
                        include_competition=include_comp,
                    )
                    local_payload = local_res.to_dict()
                    if not is_local_training_result_payload(local_payload):
                        st.error("题源校验失败：查询结果含无法定位的非本地题目，已拒绝展示。")
                    else:
                        st.session_state[result_key] = local_payload
                        st.session_state[status_key] = "local_done"
                        st.rerun()

    # 如果尚未执行任何查询，提示等待用户点击【本地查询】
    if current_status is None or result_key not in st.session_state:
        st.caption("👈 请点击上方【本地查询】按钮开始检索本地资料库。")
        return

    # 重构已缓存检索结果
    raw_dict = st.session_state[result_key]
    if not is_local_training_result_payload(raw_dict):
        st.session_state.pop(result_key, None)
        st.session_state.pop(status_key, None)
        st.warning("旧查询结果含无法定位的非本地题源，已停用；请重新本地查询。")
        return
    retrieval_result = QuestionRetrievalResult(
        training_type=raw_dict["training_type"],
        target=raw_dict["target"],
        subject=raw_dict.get("subject"),
        questions=[
            VerifiedQuestion(
                id=q["id"],
                question_text=q["question_text"],
                source_name=q["source_name"],
                source_url=q["source_url"],
                year=q.get("year"),
                exam_or_contest_name=q["exam_or_contest_name"],
                question_number=q.get("question_number"),
                subject=q["subject"],
                applicable_scope=q["applicable_scope"],
                school_name=q.get("school_name"),
                major_direction=q.get("major_direction"),
                contest_name=q.get("contest_name"),
                contest_tier=q.get("contest_tier"),
                reference_answer=q.get("reference_answer", ""),
                difficulty_level=q.get("difficulty_level", "中等"),
                question_fingerprint=q.get("question_fingerprint", ""),
                document_id=q.get("document_id"),
                page_id=q.get("page_id"),
                page_number=q.get("page_number"),
                question_item_id=q.get("question_item_id"),
                family_id=q.get("family_id"),
                verified_at=q.get("verified_at", ""),
            )
            for q in raw_dict["questions"]
        ],
        total_candidates=raw_dict["total_candidates"],
        verified_count=raw_dict["verified_count"],
        rejected_count=raw_dict["rejected_count"],
        rejected_reasons=raw_dict.get("rejected_reasons", []),
        prompt_message=raw_dict.get("prompt_message", ""),
        can_refresh=raw_dict.get("can_refresh", False),
        can_retry=raw_dict.get("can_retry", False),
        page_offset=raw_dict.get("page_offset", 0),
        total_available_verified=raw_dict.get("total_available_verified", 0),
    )

    # ==================================================================
    # One local-only result path: 0 means no training; any positive count can start.
    # ==================================================================
    if retrieval_result.verified_count == 0:
        st.warning(retrieval_result.prompt_message)
        if retrieval_result.rejected_reasons:
            with st.expander("查看本地题源核验拒绝原因", expanded=False):
                for reason in retrieval_result.rejected_reasons:
                    st.caption(f"• {reason}")
    else:
        st.success(retrieval_result.prompt_message)
        for idx, q in enumerate(retrieval_result.questions, start=1):
            _render_question_card(idx, q)
        st.markdown("---")
        if st.button(
            "🚀 开始训练",
            type="primary",
            use_container_width=True,
            key=f"btn_start_local_{task_key}",
        ):
            if session_service is not None:
                new_sess = session_service.create_session(task, retrieval_result.questions)
                st.session_state[ACTIVE_SESSION_ID_KEY] = new_sess.id
                st.rerun()

    if st.button("🔄 重新查询本地题目", key=f"btn_reset_query_{task_key}"):
        st.session_state.pop(result_key, None)
        st.session_state.pop(status_key, None)
        st.rerun()
