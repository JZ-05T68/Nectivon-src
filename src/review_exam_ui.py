"""Whole-exam recognition and source checking confined to the review screen."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import streamlit as st

from src.question_candidate_service import QuestionCandidateStore
from src.review_exam_service import ReviewExamService, publish_exam_candidates, question_text

if TYPE_CHECKING:
    from src.database import Database
    from src.models import Document

LOGGER = logging.getLogger(__name__)


def _service(root: Path) -> ReviewExamService:
    """Loading local drafts must not initialize or require an AI provider."""

    return ReviewExamService(root, None)


def render_exam_scan(document: Document, database: Database) -> dict | None:
    """One explicit action scans the document, reusing successful page transcripts."""

    # The database carries the configured reading location even when the
    # database itself has been relocated independently of user materials.
    readings = database.image_readings_dir
    root = (Path(readings).parent if readings is not None
            else database.database_path.parent.parent) / "review-exams"
    service = _service(root)
    st.caption(
        "整卷识别会把跨页题干、选项、图片及共享阅读材料一起核对切块；"
        "按页转录后统一整理，不会因分页直接拆题。结果先作为待人工复核草稿保存。"
    )
    if st.button("扫描整卷并统一切分题目", key=f"review_exam_scan_{document.id}"):
        bar = st.progress(0.0, text="正在读取整卷原页……")
        try:
            from src.runtime import application_question_vision_provider, application_settings

            service.provider = application_question_vision_provider()
            report = service.scan(
                document.id, database.list_pages(document.id),
                expected_page_count=document.page_count,
                progress=lambda current, total: bar.progress(
                    current / total,
                    text=(f"已转录 {current}/{total - 1} 页，随后统一整理题目"
                          if current < total else "整卷切块已保存，请逐题核对来源"),
                ),
            )
            publish_exam_candidates(
                report,
                QuestionCandidateStore(application_settings().data_dir / "question-candidates"),
                service.root / "previous-candidates",
            )
        except Exception as exc:  # noqa: BLE001 - keep source review available
            LOGGER.exception("整卷切块失败：document_id=%s", document.id)
            st.error(f"整卷识别未完成：{exc}。已成功转录的页面会保留，下次点击可继续。")
        else:
            st.success("整卷题目已自动整理并保存为候选；有疑点的内容已标注。")
    try:
        report = service.load(document.id)
    except (OSError, ValueError):
        LOGGER.exception("读取整卷切块草稿失败：document_id=%s", document.id)
        st.error("整卷切块草稿读取失败；原始文件和原有单页切块仍可查看。")
        return None
    if report:
        pages = {p.id: p for p in database.list_pages(document.id)}
        if set(pages) != {p["id"] for p in report["pages"]}:
            st.warning("原卷页数已变化，旧整卷切块已停用；请重新扫描整卷。")
            return None
        for source in report["pages"]:
            page = pages.get(source["id"])
            if (page is None or not page.image_path.is_file()
                    or hashlib.sha256(page.image_path.read_bytes()).hexdigest()
                    != source["image_sha256"]):
                st.warning("原页已变化，旧整卷切块已停用；请重新扫描整卷。")
                return None
    return report


def render_exam_questions(report: dict, current_page_id: int) -> None:
    """Expose full shared materials and every original page before confirmation."""

    from src.runtime import application_settings

    st.subheader("整卷题目切块 · 待人工复核")
    questions = report["questions"]
    by_id = {b["id"]: b for b in report["blocks"]}
    pages = {p["id"]: p for p in report["pages"]}
    st.caption(
        f"已扫描 {len(pages)} 页，整理出 {len(questions)} 道题、"
        f"{len(report['materials'])} 份共享材料。AI 草稿均需人工核对；"
        "题干、选项与共享材料由系统自动拼接，无需人工重组；"
        "有疑点的候选标为不完整，学习整理等工作流不会自动更新。"
    )
    if report["unassigned"]:
        st.warning(f"还有 {len(report['unassigned'])} 个来源块未确定归属，不能确认整卷完整。")
        with st.expander("查看未归属内容", expanded=True):
            for identifier in report["unassigned"]:
                block = by_id[identifier]
                st.caption(f"PDF第{block['page_number']}页 · {identifier}")
                st.write(block["text"] or "（原图内容）")
    if not questions:
        st.warning("没有可确认的题目，请对照原卷检查内容块。")
        return

    def label(index: int) -> str:
        question = questions[index]
        source_pages = list(dict.fromkeys(by_id[i]["page_number"] for i in question["source_ids"]))
        state = ("有疑点" if question["issues"] or report["unassigned"]
                 else "AI 完整性检查通过（未经人工确认）")
        return f"第{question['number']}题 · PDF第{'、'.join(map(str, source_pages))}页 · {state}"

    initial = next((i for i, q in enumerate(questions)
                    if any(by_id[b]["page_id"] == current_page_id for b in q["block_ids"])), 0)
    index = st.selectbox("选择整卷题目", range(len(questions)), index=initial, format_func=label,
                         key=f"review_exam_question_{report['fingerprint']}")
    question = questions[index]
    if question["issues"]:
        st.warning("需人工处理：" + "；".join(question["issues"]))
    elif report["unassigned"]:
        st.warning("整卷仍有未归属内容，该候选的完整性待核对。")
    else:
        st.caption("AI 完整性检查通过，尚未经人工确认。可对照原页，图像范围不合适时微调。")
    text = question_text(report, question)
    st.markdown(text)
    with st.expander("原始页码与来源定位（含所有共享材料）", expanded=True):
        source_pages = list(dict.fromkeys(by_id[i]["page_id"] for i in question["source_ids"]))
        selected_page_id = st.selectbox(
            "对照来源页", source_pages,
            format_func=lambda pid: f"PDF第{pages[pid]['number']}页（page_id={pid}）",
            key=f"review_exam_source_{report['fingerprint']}_{question['id']}",
        )
        source = pages[selected_page_id]
        st.image(source["image_path"], width="stretch")
        for identifier in question["source_ids"]:
            block = by_id[identifier]
            if block["page_id"] == selected_page_id:
                st.caption(f"来源块 {identifier} · 原图相对坐标 {block['region']['bbox']}")
        st.page_link(
            f"http://127.0.0.1:{application_settings().port}/待整理页面"
            f"?document={report['document_id']}&page_id={selected_page_id}&page={source['number']}",
            label=f"定位 PDF 第{source['number']}页", icon="📄",
        )
        from src.question_image_editor import render_question_crop_editor

        render_question_crop_editor(
            source["image_path"], page_id=selected_page_id, number=question["number"],
            regions=[by_id[i]["region"] for i in question["source_ids"]
                     if by_id[i]["page_id"] == selected_page_id],
            key=f"exam_crop_{report['fingerprint']}_{question['id']}_{selected_page_id}",
        )
