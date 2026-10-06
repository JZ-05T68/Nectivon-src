"""Streamlit UI components for Learning Report Presentation & Export (Phase 7).

Provides:
1. Interactive Learning Report dashboard with key metrics and mastery badges.
2. Direct Markdown report export and local download.
3. Traceable question evidence review with full source provenance citations.
4. Historical learning reports browser.
"""

from __future__ import annotations

import logging

import streamlit as st

from src.learning_report_models import LearningReport
from src.learning_report_service import LearningReportService
from src.time_display import beijing_now, format_beijing_time

LOGGER = logging.getLogger(__name__)


def render_learning_report_view(
    report: LearningReport,
    service: LearningReportService | None = None,
) -> None:
    """Render a comprehensive diagnostic learning report with export actions."""
    if not report.has_sufficient_data:
        st.info(report.message or "暂无足够学习数据，无法生成学习报告。")
        return

    st.markdown(f"### 📋 针对训练学习报告 · {report.subject} · {report.target or '学科综合'}")
    gen_time_str = format_beijing_time(report.generated_at)
    st.caption(
        f"教育类型：**{report.education_type}** ｜ 生成时间：{gen_time_str} ｜ "
        f"时间跨度：{report.time_range}"
    )

    # Action bar: Markdown download & save snapshot
    col_dl, col_save, _ = st.columns([0.35, 0.35, 0.3])
    with col_dl:
        md_text = report.to_markdown()
        file_name = (
            f"nectivon_report_{report.subject}_{beijing_now().strftime('%Y%m%d_%H%M%S')}.md"
        )
        st.download_button(
            label="📥 导出 Markdown 报告",
            data=md_text,
            file_name=file_name,
            mime="text/markdown",
            use_container_width=True,
            type="primary",
        )
    with col_save:
        if service is not None:
            if st.button(
                "💾 保存报告快照",
                use_container_width=True,
                key=f"save_rep_{report.report_id}",
            ):
                service.save_report(report)
                st.toast("学习报告快照已持久化保存在本地数据库！")

    st.markdown("---")

    # 1. 核心数据指标卡
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("累计训练", f"{report.total_sessions} 次")
    m2.metric("完成题数", f"{report.total_attempts} 题")
    m3.metric("正确 / 失误", f"{report.correct_count} / {report.error_count}")
    m4.metric("综合正确率", f"{report.accuracy_rate}%")

    # 2. 掌握度状态
    with st.container(border=True):
        st.markdown(f"#### 🎯 当前掌握度等级：`【{report.mastery_level}】`")
        st.caption(
            f"连续正确次数：**{report.consecutive_correct}** 次 ｜ "
            f"主要薄弱方向：**{report.primary_weakness or '当前掌握良好'}**"
        )

        if report.frequent_errors:
            st.markdown("**⚠️ 高频失误预警标签：**")
            for ftag in report.frequent_errors:
                st.warning(ftag.get("tag_label", "高频错因"))

    # 3. 错因分类分布
    if report.error_distribution_by_type:
        st.markdown("#### 📊 错因分类分布诊断")
        cols = st.columns(len(report.error_distribution_by_type))
        for idx, (etype, count) in enumerate(report.error_distribution_by_type.items()):
            with cols[idx]:
                st.metric(etype, f"{count} 次")

    # 4. 核心解题方法小结
    if report.method_summaries:
        st.markdown("#### 🧠 核心解题方法小结沉淀")
        for summary in report.method_summaries:
            st.info(summary)

    # 5. 做题凭证与试题复盘
    st.markdown("---")
    st.markdown("#### 📑 训练试题复盘与题源核验记录")
    for idx, qe in enumerate(report.question_evidences, 1):
        icon = "✅" if qe.is_correct else "❌"
        scope_tag = f"[{qe.applicable_scope}] · {qe.exam_or_contest_name}"
        if qe.year:
            scope_tag += f"（{qe.year}年）"
        with st.expander(f"{icon} 试题 {idx}：{scope_tag}", expanded=False):
            st.markdown(f"**题干**：{qe.question_text}")
            st.markdown(f"**题源出处**：[{qe.source_name}]({qe.source_url})")
            st.markdown(f"**你的解答**：`{qe.user_answer}`（提交次数：{qe.submission_count} 次）")
            st.markdown(f"**参考答案**：{qe.reference_answer or '暂无简答文本'}")
            if not qe.is_correct:
                st.markdown(
                    f"**归因错因**：`{qe.error_type or '未指定'}` ｜ 反思：{qe.user_note or '无'}"
                )
            if qe.method_reinforcement:
                st.caption(f"💡 方法强化：{qe.method_reinforcement}")


def render_historical_reports_expander(
    service: LearningReportService,
    user_id: str = "default_local_user",
    subject: str | None = None,
) -> None:
    """Render expandable list of previously saved historical reports."""
    reports = service.list_historical_reports(user_id=user_id, subject=subject)
    if not reports:
        return

    with st.expander(f"📁 查看历史保存的学习报告（共 {len(reports)} 份）", expanded=False):
        for rep in reports:
            col_info, col_act = st.columns([0.7, 0.3], vertical_alignment="center")
            with col_info:
                st.markdown(
                    f"**{rep.subject} · {rep.target or '学科综合'}** "
                    f"（等级：`{rep.mastery_level}` ｜ 正确率：`{rep.accuracy_rate}%`）"
                )
                st.caption(f"保存时间：{format_beijing_time(rep.generated_at)}")
            with col_act:
                md_content = rep.to_markdown()
                st.download_button(
                    "下载报告",
                    data=md_content,
                    file_name=f"report_{rep.subject}_{rep.report_id}.md",
                    key=f"dl_hist_{rep.report_id}",
                    use_container_width=True,
                )
            st.markdown("---")
