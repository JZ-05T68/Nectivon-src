"""Streamlit UI component for Targeted Training Goal Selection (Phase 2).

Renders within the '掌握训练' (Mastery Training) area:
Row 1: 题型族 | 方法族 (single-select radio, identical style to Phase 1)
Row 2: Dynamic selectbox reading authentic Layer 2 families
Button: 生成针对训练计划 -> generates TrainingTask object
Strictly forbids question generation, online search, and fake data injection.
"""

from __future__ import annotations

import logging
from typing import Final

import streamlit as st

from src.question_source_retrieval_service import QuestionSourceRetrievalService
from src.question_source_ui import render_training_questions_section
from src.targeted_training_models import (
    TRAINING_TYPES,
    TargetedTrainingError,
    TrainingTask,
)
from src.targeted_training_service import TargetedTrainingService
from src.training_execution_ui import ACTIVE_SESSION_ID_KEY, render_training_session_ui
from src.training_profile_models import LearnerProfile
from src.training_session_service import TrainingSessionService

LOGGER = logging.getLogger(__name__)

RADIO_KEY: Final[str] = "targeted_training_type_radio"
SELECTBOX_KEY: Final[str] = "targeted_training_target_selectbox"
ACTIVE_TASK_SESSION_KEY: Final[str] = "active_targeted_training_task"


def render_targeted_training_section(
    service: TargetedTrainingService,
    *,
    current_subject: str | None = None,
    retrieval_service: QuestionSourceRetrievalService | None = None,
    learner_profile: LearnerProfile | None = None,
    session_service: TrainingSessionService | None = None,
) -> TrainingTask | None:
    """Render the Phase 2 & 3 & 5 training goal selection, questions, and execution.

    Parameters
    ----------
    service:
        TargetedTrainingService connected to the active local database.
    current_subject:
        Active subject discipline (e.g. '地理', '物理') to enforce cross-discipline
        isolation.
    retrieval_service:
        Optional QuestionSourceRetrievalService for Phase 3 authentic question retrieval.
    learner_profile:
        Optional LearnerProfile for profile boundary constraints.
    session_service:
        Optional TrainingSessionService for Phase 5 interactive training execution.

    Returns
    -------
    TrainingTask | None:
        The newly generated or currently active training task object.
    """
    # 0. 如果有正在进行或已完成的训练 Session，优先渲染训练执行/报告界面
    active_session_id = st.session_state.get(ACTIVE_SESSION_ID_KEY)
    if active_session_id and session_service is not None:
        render_training_session_ui(active_session_id, session_service)
        return None

    # 1. 确保 Session State 状态存在
    if RADIO_KEY not in st.session_state:
        st.session_state[RADIO_KEY] = "题型族"

    # ==================================================================
    # 阶段7：今日学习状态卡片与复习建议（零数据防幻觉保护）
    # ==================================================================
    if current_subject:
        from src.runtime import application_review_queue_service

        rev_service = application_review_queue_service()
        status_summary = rev_service.get_learning_status_summary(
            user_id="default_local_user", subject=current_subject
        )
        with st.container(border=True):
            st.markdown(f"#### 📅 今日学习状态 · {current_subject}")
            if not status_summary.has_sufficient_data:
                st.caption(
                    status_summary.message
                    or "暂无足够学习数据，未生成复习计划与薄弱画像。"
                )
            else:
                c1, c2, c3 = st.columns(3)
                c1.metric("阶段掌握目标", f"{status_summary.stage_mastered_count} 个")
                c2.metric("待加强薄弱目标", f"{status_summary.needs_improvement_count} 个")
                c3.metric("训练中目标", f"{status_summary.in_training_count} 个")

                if status_summary.today_recommended_items:
                    st.markdown("**🔔 今日建议复习计划：**")
                    for rec in status_summary.today_recommended_items:
                        col_text, col_act = st.columns(
                            [0.75, 0.25], vertical_alignment="center"
                        )
                        with col_text:
                            prio_color = (
                                "🔴 高"
                                if rec.review_priority == "高"
                                else ("🟡 中" if rec.review_priority == "中" else "🟢 低")
                            )
                            st.markdown(
                                f"- **{rec.target}** ｜ 优先级：{prio_color} ｜ "
                                f"建议复习：`{rec.next_review_at}`"
                            )
                            st.caption(f"  *复习原因*：{rec.review_reason}")
                        with col_act:
                            if st.button(
                                "🚀 立即复习",
                                key=f"rev_btn_{rec.id}",
                                use_container_width=True,
                            ):
                                st.session_state[RADIO_KEY] = rec.training_type
                                task_obj = service.create_training_task(
                                    training_type=rec.training_type,
                                    target=rec.target,
                                    subject=current_subject,
                                )
                                st.session_state[ACTIVE_TASK_SESSION_KEY] = (
                                    task_obj.to_dict()
                                )
                                st.rerun()

    # ==================================================================
    # 第一行：训练类型选择（题型族 | 方法族）
    # 使用与“基础教育 | 高等教育”相同风格的单选组件，二选一，不允许同时选择
    # ==================================================================
    selected_type = st.radio(
        "选择训练类型",
        options=list(TRAINING_TYPES),
        index=TRAINING_TYPES.index(st.session_state[RADIO_KEY]),
        horizontal=True,
        key=RADIO_KEY,
        help="针对整理出的题型结构或解题方法开展专项突破。",
    )

    # ==================================================================
    # 第二行：具体训练目标选择（根据第一行动态变化，读取第二层整理数据）
    # ==================================================================
    available_targets = service.get_available_targets(
        selected_type, subject=current_subject
    )
    target_titles = [f.title for f in available_targets]

    selected_target: str | None = None

    if selected_type == "题型族":
        label_text = "请选择你需要针对训练的题型"
        if not target_titles:
            st.info(service.get_empty_message("题型族"))
        else:
            selected_target = st.selectbox(
                label_text,
                options=target_titles,
                key=f"{SELECTBOX_KEY}_type",
                help="读取已有第二层归纳整理的题型族数据。",
            )
    else:  # 方法族
        label_text = "请选择你需要针对训练的方法"
        if not target_titles:
            st.info(service.get_empty_message("方法族"))
        else:
            selected_target = st.selectbox(
                label_text,
                options=target_titles,
                key=f"{SELECTBOX_KEY}_method",
                help="读取已有第二层归纳整理的方法族数据。",
            )

    # ==================================================================
    # 按钮：生成针对训练计划
    # 阶段8重构：原「生成针对训练计划」修改为「开始针对训练」
    # 点击后在下方展开题目来源交互区域，仅本地查询，不直接出题、不调用大模型
    # ==================================================================
    button_label = "开始针对训练"
    btn_disabled = selected_target is None or len(target_titles) == 0

    if st.button(
        button_label,
        key="btn_generate_targeted_training_plan",
        type="primary",
        disabled=btn_disabled,
    ):
        if selected_target:
            try:
                task = service.create_training_task(
                    training_type=selected_type,
                    target=selected_target,
                    subject=current_subject,
                )
                st.session_state[ACTIVE_TASK_SESSION_KEY] = task.to_dict()
                st.success(f"已创建针对训练任务：{task.training_type} · {task.target}")
            except TargetedTrainingError as exc:
                st.error(f"创建训练任务失败：{exc}")

    # ==================================================================
    # 显示已生成的训练任务对象卡片
    # ==================================================================
    active_task_dict = st.session_state.get(ACTIVE_TASK_SESSION_KEY)
    if active_task_dict:
        with st.container(border=True):
            st.markdown(
                f"🎯 **训练任务：{active_task_dict.get('training_type')} · "
                f"{active_task_dict.get('target')}**"
            )
            c1, c2, c3 = st.columns(3)
            c1.caption(f"**训练类型**：{active_task_dict.get('training_type')}")
            c2.caption(f"**数据来源**：{active_task_dict.get('source')}")
            c3.caption("**任务状态**：待本地查询")

            with st.expander("查看内部任务数据（兼容历史状态码）", expanded=False):
                st.json(active_task_dict)

        # 阶段3：检索真实题源并渲染已验证训练题
        if retrieval_service is not None:
            task_obj = TrainingTask(
                training_type=active_task_dict["training_type"],
                target=active_task_dict["target"],
                source=active_task_dict.get("source", "第二层整理数据"),
                status=active_task_dict.get("status", "waiting_for_question_generation"),
                family_id=active_task_dict.get("family_id"),
                subject=active_task_dict.get("subject"),
                created_at=active_task_dict.get("created_at", ""),
            )
            render_training_questions_section(
                task=task_obj,
                retrieval_service=retrieval_service,
                profile=learner_profile,
                session_service=session_service,
            )

    # 阶段6：学科错因画像与掌握度档案面板（仅基于真实历史，零虚构）
    if session_service is not None and current_subject:
        with st.expander(f"📊 {current_subject}学科错因画像与掌握度档案", expanded=False):
            err_service = session_service.error_service
            err_profile = err_service.generate_error_profile(
                "default_local_user", current_subject
            )
            if not err_profile.has_sufficient_data:
                st.info(err_profile.message)
            else:
                c1, c2, c3 = st.columns(3)
                c1.metric("累计训练", f"{err_profile.total_attempts} 次")
                c2.metric("累计失误", f"{err_profile.total_errors} 次")
                c3.metric(
                    "主要薄弱方向",
                    err_profile.primary_weakness or "当前掌握良好",
                )

                if err_profile.frequent_errors:
                    st.markdown("**⚠️ 高频失误预警标签：**")
                    for ftag in err_profile.frequent_errors:
                        st.warning(ftag.tag_label)

                if err_profile.error_distribution_by_type:
                    st.markdown("**错因分类统计：**")
                    cols = st.columns(len(err_profile.error_distribution_by_type))
                    for i, (etype, count) in enumerate(
                        err_profile.error_distribution_by_type.items()
                    ):
                        with cols[i]:
                            st.metric(etype, f"{count} 次")

    # 阶段7：历史已保存学习报告面板
    if current_subject:
        from src.learning_report_ui import render_historical_reports_expander
        from src.runtime import application_learning_report_service

        rep_service = application_learning_report_service()
        render_historical_reports_expander(
            rep_service, user_id="default_local_user", subject=current_subject
        )

    return None
