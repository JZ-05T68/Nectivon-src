"""Streamlit UI components for Targeted Training Execution & Feedback (Phase 5).

Provides:
1. Interactive question answering workflow with progress tracking.
2. Prominent [🔍 查询题目来源] button on every question card.
3. Empty-answer rejection and repeat submission counting.
4. Diagnostic error cause taxonomy (概念不清 / 计算失误 / 方法不熟 / 审题不清 / 其他).
5. Core method reinforcement cards and feedback to Layer 1 Three-Tier Organization.
6. Diagnostic result summary report upon completion.
7. Resume-after-exit midway support.
"""

from __future__ import annotations

import logging
from typing import Final

import streamlit as st

from src.local_training_source_policy import is_locatable_local_training_question
from src.math_display import render_math_markdown
from src.question_source_ui import render_question_source_button
from src.targeted_training_models import TrainingTask
from src.training_session_models import ErrorType, SessionStatus, TrainingSession
from src.training_session_service import TrainingSessionService

LOGGER = logging.getLogger(__name__)

ACTIVE_SESSION_ID_KEY: Final[str] = "targeted_training_active_session_id"


def launch_variant_training_session(
    service: TrainingSessionService,
    session: TrainingSession,
    exclude_question_id: str | None = None,
) -> bool:
    """Launch a variant training session with authentic questions only.

    Strict rules:
    - Questions must come strictly from the user's locally imported materials.
    - If none available: returns False (UI shows '暂未找到符合条件的可靠训练题。').
    - Never generate AI questions.
    """
    try:
        from src.runtime import (
            application_question_source_retrieval_service,
            application_training_profile_service,
        )

        retrieval_service = application_question_source_retrieval_service()
        profile_service = application_training_profile_service()
        profile = profile_service.get_profile()

        target_subject = session.task.subject or (
            session.questions[0].subject if session.questions else None
        )
        target_family_id = session.task.family_id or (
            session.questions[0].family_id if session.questions else None
        )

        task = TrainingTask(
            training_type=session.task.training_type,
            target=session.task.target,
            subject=target_subject,
            family_id=target_family_id,
        )

        retrieval_result = retrieval_service.retrieve_local_only(
            task=task, profile=profile
        )
        verified_qs = retrieval_result.questions

        if exclude_question_id:
            verified_qs = [q for q in verified_qs if q.id != exclude_question_id]

        if not verified_qs:
            return False

        new_session = service.create_session(
            task=task,
            user_id=session.user_id,
            questions=verified_qs,
        )
        st.session_state[ACTIVE_SESSION_ID_KEY] = new_session.id
        return True
    except Exception as exc:
        LOGGER.exception("发起变式训练失败: %s", exc)
        return False


def render_training_session_ui(
    session_id: str,
    service: TrainingSessionService,
) -> None:
    """Render the active training execution or completed report view."""
    session = service.get_session(session_id)
    if not session:
        st.error(f"未找到训练会话：{session_id}")
        if st.button("返回目标选择"):
            st.session_state.pop(ACTIVE_SESSION_ID_KEY, None)
            st.rerun()
        return

    if not session.questions or any(
        not is_locatable_local_training_question(q) for q in session.questions
    ):
        st.warning(
            "此历史训练会话含无法定位到本地原题的题目，当前版本不再展示或继续作答。"
            "原有记录仍保留，供人工核查。"
        )
        if st.button("返回目标选择"):
            st.session_state.pop(ACTIVE_SESSION_ID_KEY, None)
            st.rerun()
        return

    if session.status == SessionStatus.COMPLETED:
        render_training_session_result_view(session.id, service)
        return

    _render_active_answering_view(session, service)


def _render_active_answering_view(
    session: TrainingSession,
    service: TrainingSessionService,
) -> None:
    """Render the in-progress question answering card and navigation."""
    total = session.total_questions
    current_idx = session.current_question_index
    q = session.current_question

    if q is None:
        st.warning("当前题目索引异常，尝试重置到第1题。")
        service.advance_question(session.id, 0)
        st.rerun()
        return

    # ==================================================================
    # 顶部导航与进度栏
    # ==================================================================
    col_title, col_exit = st.columns([0.75, 0.25], vertical_alignment="center")
    with col_title:
        st.markdown(f"### 🎯 针对训练 · {session.task.target}")
        st.caption(
            f"训练类别：{session.task.training_type} ｜ 学科：{session.task.subject or '通用'} ｜ "
            f"会话编号：`{session.id}`"
        )
    with col_exit:
        if st.button(
            "⏸️ 中途退出并保存",
            use_container_width=True,
            help="保存当前作答进度，随时可恢复",
        ):
            st.session_state.pop(ACTIVE_SESSION_ID_KEY, None)
            st.toast("训练进度已安全保存在本地，下次进入时可自动继续作答。")
            st.rerun()

    progress_val = min(1.0, max(0.0, (current_idx + 1) / total))
    st.progress(progress_val, text=f"第 {current_idx + 1} / {total} 题")

    # ==================================================================
    # 题目展示卡片（严格绑定来源与核验状态）
    # ==================================================================
    with st.container(border=True):
        col_badge, col_source_btn = st.columns([0.7, 0.3], vertical_alignment="center")
        with col_badge:
            tags_display = f"🏷️ **[{q.applicable_scope}]** · {q.exam_or_contest_name}"
            if q.year:
                tags_display += f"（{q.year}年）"
            if q.question_number:
                tags_display += f" · 第{q.question_number}题"
            st.markdown(tags_display)
        with col_source_btn:
            # 核心功能：【查询题目来源】全流程可见且可跳转
            render_question_source_button(q, key=f"exec_src_{q.id}")

        st.markdown("---")
        st.markdown("##### 题目")
        render_math_markdown(q.question_text)

    # ==================================================================
    # 作答与提交区域
    # ==================================================================
    attempt = session.get_attempt(q.id)

    st.markdown("##### ✍️ 你的解答")
    default_text = attempt.user_answer if attempt else ""
    user_input = st.text_area(
        "请输入你的解答过程或核心结论：",
        value=default_text,
        height=130,
        key=f"input_q_{q.id}_{attempt.submission_count if attempt else 0}",
        placeholder="在此输入解答、计算步骤或推理过程...",
        label_visibility="collapsed",
    )

    if not attempt:
        # 首次提交
        if st.button("🚀 提交答案", type="primary", use_container_width=True):
            clean_ans = user_input.strip()
            if not clean_ans:
                st.warning("请先填写答案。")
            else:
                try:
                    service.evaluate_and_record_attempt(
                        session_id=session.id,
                        question_id=q.id,
                        user_answer=clean_ans,
                    )
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
    else:
        # 已提交状态展示
        col_sub_info, _ = st.columns([0.5, 0.5])
        with col_sub_info:
            st.caption(
                f"ℹ️ 提交记录：已作答（第 **{attempt.submission_count}** 次提交） ｜ "
                f"时间：{attempt.attempted_at[:19].replace('T', ' ')}"
            )

        # 结果判定与人工确认
        if attempt.is_correct:
            st.success("🎉 **回答正确！** 题目核心思路与结论符合标准要求。")
        else:
            st.warning("💡 **回答有待改进或需人工核对**")
            col_tog, _ = st.columns([0.4, 0.6])
            with col_tog:
                is_checked = st.checkbox(
                    "人工标记为正确（确认解法可行）",
                    value=attempt.is_correct,
                    key=f"toggle_correct_{q.id}",
                )
                if is_checked != attempt.is_correct:
                    service.evaluate_and_record_attempt(
                        session_id=session.id,
                        question_id=q.id,
                        user_answer=attempt.user_answer,
                        is_correct=is_checked,
                        error_type=attempt.error_type,
                        error_analysis=attempt.error_analysis,
                    )
                    st.rerun()

            # 错因归因分析体系
            if not is_checked:
                st.markdown("**🔍 错因归因分析**")
                err_labels = [
                    "暂不判断",
                    "概念不清",
                    "计算失误",
                    "方法不熟",
                    "审题不清",
                    "其他",
                ]
                cur_err_label = (
                    attempt.error_type.label if attempt.error_type else "暂不判断"
                )
                def_idx = (
                    err_labels.index(cur_err_label)
                    if cur_err_label in err_labels
                    else 0
                )

                selected_label = st.radio(
                    "失误主要原因：",
                    err_labels,
                    index=def_idx,
                    horizontal=True,
                    key=f"err_radio_{q.id}",
                )
                err_note = st.text_input(
                    "反思记录（选填）：",
                    value=attempt.error_analysis,
                    placeholder="简要记录思维盲区或改进方向...",
                    key=f"err_input_{q.id}",
                )
                new_err_type = (
                    None
                    if selected_label == "暂不判断"
                    else ErrorType.from_label(selected_label)
                )
                if new_err_type is None:
                    st.caption("没有足够证据时可以暂不判断错因。")
                elif (
                    new_err_type != attempt.error_type
                    or err_note != attempt.error_analysis
                ):
                    if st.button("更新错因记录", key=f"save_err_{q.id}"):
                        service.update_error_attribution(
                            session_id=session.id,
                            question_id=q.id,
                            error_type=new_err_type,
                            error_analysis=err_note,
                        )
                        st.toast("已更新错因归因记录。")
                        st.rerun()

                if st.button(
                    "🔁 针对该问题继续训练（变式训练）",
                    key=f"active_variant_{q.id}",
                    help="基于该题暴露的问题，匹配真实相似试题发起巩固训练",
                ):
                    launched = launch_variant_training_session(
                        service, session, exclude_question_id=q.id
                    )
                    if launched:
                        st.toast("已匹配真实变式训练题，开启巩固训练！")
                        st.rerun()
                    else:
                        st.info("暂未找到符合条件的可靠训练题。")

        # 本地订正可能来自用户/教师/开发测试，不得冒充官方答案。
        with st.expander("📖 查看整理参考解答（非官方答案）", expanded=True):
            render_math_markdown(q.reference_answer or "（暂未整理参考解答）")

        # 核心方法强化卡片
        st.info(attempt.method_reinforcement)

        # 底部动作栏
        st.markdown("---")
        col_act1, col_act2 = st.columns([0.4, 0.6])
        with col_act1:
            if st.button("🔄 重新提交修改解答", key=f"resubmit_{q.id}", use_container_width=True):
                clean_ans = user_input.strip()
                if not clean_ans:
                    st.warning("请先填写答案。")
                else:
                    service.evaluate_and_record_attempt(
                        session_id=session.id,
                        question_id=q.id,
                        user_answer=clean_ans,
                    )
                    st.toast("已更新提交解答！")
                    st.rerun()

        with col_act2:
            if current_idx + 1 < total:
                if st.button("下一题 ➡️", type="primary", use_container_width=True):
                    service.advance_question(session.id, current_idx + 1)
                    st.rerun()
            else:
                if st.button("🏁 完成训练并查看报告", type="primary", use_container_width=True):
                    service.complete_session(session.id)
                    st.rerun()


def render_training_session_result_view(
    session_id: str,
    service: TrainingSessionService,
) -> None:
    """Render the comprehensive diagnostic feedback report for a completed session."""
    result = service.get_result(session_id)
    session = service.get_session(session_id)
    if not result or not session:
        st.error("未能加载训练报告数据")
        return

    st.markdown("### 🏆 针对训练完成报告")
    st.caption(
        f"训练目标：**{result.task_target}** ｜ 类别：{result.training_type} ｜ "
        f"完成时间：{result.completed_at[:19].replace('T', ' ')}"
    )

    # 4 项关键指标展示
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("总试题数", f"{result.total_questions} 题")
    c2.metric("已作答题数", f"{result.attempted_count} 题")
    c3.metric("正确题数", f"{result.correct_count} 题")
    c4.metric("正确率", f"{result.accuracy_rate}%")

    st.markdown("---")

    # 错因分布分析
    st.markdown("#### 📊 错因分布诊断")
    if result.error_type_distribution:
        err_cols = st.columns(len(result.error_type_distribution))
        for idx, (label, count) in enumerate(result.error_type_distribution.items()):
            with err_cols[idx]:
                st.metric(label, f"{count} 次")
    else:
        if result.correct_count == result.total_questions:
            st.success("🌟 本次训练全对通关！未产生任何错误归因。")
        else:
            st.info("本次训练未记录具体错因分布。")

    st.markdown("---")

    # 方法小结沉淀
    st.markdown("#### 🧠 核心方法小结沉淀")
    for summary_text in result.method_summary:
        st.info(summary_text)

    # 试题复盘与来源追溯
    st.markdown("---")
    st.markdown("#### 📑 训练试题复盘与来源追溯")
    for idx, q in enumerate(session.questions, 1):
        att = session.get_attempt(q.id)
        status_icon = "✅" if att and att.is_correct else "❌"
        with st.expander(
            f"{status_icon} 第 {idx} 题：{q.exam_or_contest_name} ({q.year or '未知'}年)",
            expanded=False,
        ):
            st.markdown(f"**题干**：{q.question_text}")
            if att:
                st.markdown(
                    f"**你的解答**：`{att.user_answer}`（共提交 {att.submission_count} 次）"
                )
                if not att.is_correct and att.error_type:
                    ref_text = att.error_analysis or "无"
                    st.markdown(f"**归因错因**：`{att.error_type.label}` ｜ 反思：{ref_text}")
            st.markdown(f"**参考答案**：{q.reference_answer or '暂无'}")

            # 来源查询按钮
            render_question_source_button(q, key=f"rev_src_{q.id}")

    # ==================================================================
    # 阶段6：掌握度状态与进阶评估
    # ==================================================================
    st.markdown("---")
    st.markdown("#### 🎯 目标掌握度进阶状态")
    target_subject = session.task.subject or (
        session.questions[0].subject if session.questions else "通用"
    )
    mastery_state = service.error_service.get_mastery_state(
        user_id=session.user_id,
        subject=target_subject,
        target=session.task.target,
    )
    col_lvl, col_acc, col_cnt, col_streak = st.columns(4)
    with col_lvl:
        st.metric("掌握度等级", f"【{mastery_state.level.value}】")
    with col_acc:
        st.metric("累计正确率", f"{mastery_state.accuracy_rate}%")
    with col_cnt:
        st.metric("累计训练", f"{mastery_state.total_attempts} 次")
    with col_streak:
        st.metric("连续正确", f"{mastery_state.consecutive_correct} 次")

    level_descs = {
        "未建立": "尚未建立有效练习记录。",
        "训练中": "已开启训练，正在积累解题证据与验证结论。",
        "待加强": "近期出现失误或方法盲区，建议针对性巩固薄弱点。",
        "阶段掌握": "已连续多次正确解答，形成稳定的解题方法与思维路径。",
    }
    st.caption(f"ℹ️ 等级说明：{level_descs.get(mastery_state.level.value, '')}")

    # ==================================================================
    # 阶段6：变式训练入口
    # ==================================================================
    if (
        result.correct_count < result.total_questions
        or mastery_state.level.value != "阶段掌握"
    ):
        st.markdown("---")
        st.markdown("#### 🔁 针对该问题继续训练（变式训练）")
        st.caption("基于本次训练暴露的错因与思维盲区，检索真实相似变式试题继续巩固。")
        col_var_btn, _ = st.columns([0.4, 0.6])
        with col_var_btn:
            if st.button(
                "🔁 发起变式强化训练",
                type="primary",
                use_container_width=True,
                key=f"btn_variant_{session.id}",
            ):
                launched = launch_variant_training_session(service, session)
                if launched:
                    st.toast("已成功匹配真实变式试题，开启新一轮针对训练！")
                    st.rerun()
                else:
                    st.info("暂未找到符合条件的可靠训练题。")

    # ==================================================================
    # 阶段7：学习报告导出与归档
    # ==================================================================
    st.markdown("---")
    st.markdown("#### 📑 阶段学习报告与资产导出")
    if st.button(
        "📊 生成并查看完整学习报告",
        use_container_width=True,
        key=f"btn_show_rep_{session.id}",
    ):
        st.session_state[f"show_full_report_{session.id}"] = True

    if st.session_state.get(f"show_full_report_{session.id}"):
        from src.learning_report_ui import render_learning_report_view
        from src.runtime import application_learning_report_service

        rep_service = application_learning_report_service()
        learning_report = rep_service.generate_report(
            user_id=session.user_id,
            subject=target_subject,
            target=session.task.target,
        )
        render_learning_report_view(learning_report, service=rep_service)

    # 底部返回操作
    st.markdown("---")
    if st.button("🔙 返回训练目标选择", type="primary"):
        st.session_state.pop(ACTIVE_SESSION_ID_KEY, None)
        st.rerun()
