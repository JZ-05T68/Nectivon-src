"""Streamlit widgets for per-course learning goals (STEM patch P1-4).

Two entry points share one renderer:

- the join-learning subject picker (goal chosen while organizing), and
- the 训练配置 tab (manage every course's goal in one place).

Changing a goal only writes ``learning_goals.json`` guidance metadata; it
never touches questions, explanations or correction records.
"""

from __future__ import annotations

import logging

import streamlit as st

from src.learning_goal_service import (
    GOALS,
    LearningGoalError,
    goal_for,
    layer_guidance,
    read_goals,
    save_goal,
)

LOGGER = logging.getLogger(__name__)


def render_learning_goal_picker(
    database_path,
    subject: str,
    *,
    key: str | None = None,
    show_guidance: bool = True,
) -> str:
    """One selectbox for one course's goal; persist on change, nothing else."""

    subject_clean = str(subject).strip()
    if not subject_clean:
        return ""
    try:
        current = goal_for(database_path, subject_clean)
    except LearningGoalError as exc:
        st.warning(f"课程学习目标配置暂时无法读取：{exc}")
        return ""
    goal = str(
        st.selectbox(
            f"「{subject_clean}」学习目标（只影响推荐整理深度，可随时调整）",
            GOALS,
            index=GOALS.index(current),
            key=key or f"learning_goal_{subject_clean}",
            help=(
                "课程达标：完成第一层（逐题核对与订正）即达标；"
                "系统掌握：整理到第二层归纳；考研重点：第二层归纳为基座、按需进入第三层训练。"
                "调整目标不会改动已整理的题目、讲解与订正记录。"
            ),
        )
    )
    if show_guidance:
        st.caption(layer_guidance(goal))
    if goal != current:
        try:
            save_goal(database_path, subject_clean, goal)
        except (LearningGoalError, OSError) as exc:
            LOGGER.exception("保存课程学习目标失败")
            st.error(f"学习目标保存失败：{exc}")
        else:
            st.toast(f"已保存「{subject_clean}」学习目标：{goal}（已有整理内容不受影响）。")
    return goal


def render_learning_goal_manager(database_path, subjects: list[str]) -> None:
    """训练配置 tab section: every saved course plus quick adding."""

    st.subheader("课程学习目标与整理深度")
    st.caption(
        "为每门课程设置学习目标，系统据此推荐整理到第几层；"
        "这只是推荐，任何课程都可以随时手动进入任意一层。"
        "调整目标不会删除或覆盖原始题目、历史讲解与订正记录。"
    )
    try:
        saved = read_goals(database_path)
    except LearningGoalError as exc:
        st.error(f"课程学习目标配置暂时无法读取：{exc}")
        return
    known = sorted({*saved, *(str(s).strip() for s in subjects if str(s).strip())})
    if not known:
        st.caption("还没有课程学习目标。加入学习整理时选择学科即可设置。")
    for subject in known:
        with st.container(border=True):
            render_learning_goal_picker(database_path, subject, key=f"goal_mgr_{subject}")
    with st.expander("为其他课程添加学习目标"):
        new_subject = st.text_input(
            "课程名称（例如：液压与气动、自动控制原理）", key="goal_mgr_new_subject",
        ).strip()
        if new_subject:
            render_learning_goal_picker(database_path, new_subject, key="goal_mgr_add")
