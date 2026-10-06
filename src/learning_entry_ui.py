"""Context entry "加入学习整理" on the page review surface (v0.8.6 RUN 3).

The user is looking at a recognised page; adding a question must not
require leaving the page and hunting for it again.  One click registers a
draft question item bound to THIS page through the same
:class:`src.learning_workflow_service.QuestionService` every other entry
uses - there is exactly one learning-asset store, never per-entry copies.

V086-209 fix: an item is never a silent empty shell.  When the page has a
usable source, the source is preserved inside the item (``ai_draft`` keeps
the exact source content plus its provenance and a fingerprint) with an
explicit "待切分" state, so the user confirms/splits the stem in the first
layer instead of finding an unusable row later.  Content priority:

1. 用户人工确认文本（``pages.markdown_content``）
2. 人工确认保存的历史读图文字
3. 当前有效的 AI 直接读图结果
4. 原文件文本层（仅供本地阅读，自动切题仍使用原图）

A fingerprint-based duplicate guard (V086-209 §5.3) stops repeated clicks
from producing identical shells: the second click reports the existing
item instead of creating a copy.

Exam-structure boundary (user supplement, 2026-09-26): gaokao math papers
are NOT a fixed shape (22-question 8+4+4+6 and 19-question 8+3+3+5 both
exist in the real corpus).  This module never assumes question-number
ranges, counts of big questions, or score layouts: question numbers,
types, printed page numbers and per-question scores stay inside the
verbatim source content (raw evidence) for the user to split, and are
never used for automatic segmentation or scoring here.  A future
paper_schema / scoring capability can build on the preserved source
without breaking this contract.

Fail-safe by design: if the shared database is unavailable the section
degrades to a message instead of breaking page review.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import streamlit as st

from src.learning_subject_policy import (
    ADVANCED_SUBJECT,
    normalize_subject_name,
    subject_selection_policy,
)
from src.learning_workflow_service import LearningWorkflowError, QuestionItem, QuestionService
from src.math_display import render_question_math_markdown
from src.math_formatting_service import candidate_display_stem, schedule_candidate_math
from src.models import Page
from src.question_candidate_service import (
    QuestionCandidate,
    QuestionCandidateStore,
    iter_atomic_leaves,
    iter_question_nodes,
    stem_confidence_for,
)
from src.question_content_ui import render_question_content, render_region_images
from src.question_group_service import (
    QuestionGroupError,
    add_group_material,
    attach_question_to_group,
    draft_group,
    group_member_numbers,
    latest_group_for_page,
    normalize_candidate_number,
)
from src.text_utils import ui_plaintext_digest
from src.training_profile_models import LearnerProfile

LOGGER = logging.getLogger(__name__)

_KIND_LABELS = (
    ("error", "错题"),
    ("good", "好题"),
    ("typical", "典型题"),
    ("method", "方法题"),
)

_MAX_SOURCE_CHARS = 4000


def _resolve_page_source(
    page: Page, visual_service: object | None = None
) -> tuple[str, str, dict | None]:
    """Pick the best available source for this page.

    Returns ``(content, provenance_label, extra_draft_fields)``.  The
    content is stored verbatim inside the question's ``ai_draft`` — never
    silently dropped — together with where it came from.  ``visual_service``
    is injectable so harnesses without a runtime can exercise the draft
    priority; production callers omit it and the runtime service is used
    when available.
    """

    manual_text = (page.markdown_content or "").strip()
    if manual_text:
        return (
            manual_text[:_MAX_SOURCE_CHARS],
            "人工校对文字",
            {
                "origin": "user_manual_text",
            },
        )
    # A user-saved historical image transcription is an authoritative note.
    # Unconfirmed AI drafts and OCR never become recognition input.
    if visual_service is not None:
        for item in visual_service.get_page_visual_state(page.id).get("interpretations", []):
            if (item.get("status") == "confirmed"
                    and str(item.get("content", "")).strip()):
                return (
                    str(item["content"])[:_MAX_SOURCE_CHARS],
                    "人工核对的读图文字（已保存确认）",
                    {
                        "origin": "stage2_visual_draft",
                        "user_confirmed": True,
                        "interpretation_id": item.get("id"),
                    },
                )
    try:
        from src.page_image_ui import current_image_reading
        from src.runtime import application_settings

        reading = current_image_reading(page, application_settings().agent_readings_dir)
        if reading is not None and reading.transcript.strip():
            return (
                reading.transcript[:_MAX_SOURCE_CHARS],
                "AI 直接读图文字",
                {
                    "origin": "page_image_reading",
                    "model": reading.model,
                },
            )
    except Exception:  # noqa: BLE001 - keep manual review usable
        LOGGER.warning("读取页面读图结果失败：page_id=%s", page.id, exc_info=True)
    extracted_text = (page.extracted_text or "").strip()
    if extracted_text:
        return (
            extracted_text[:_MAX_SOURCE_CHARS],
            "原文件文本层",
            {
                "origin": "page_extracted_text",
            },
        )
    return "", "请直接读图并切分本页", {"origin": "none"}


def _render_answer_assessment_banner(page: Page) -> None:
    """F5: evidence-based reference-answer vs scoring-standard assessment.

    Shown for documents whose title/filename suggests an answer or rubric.
    The assessment is a SUGGESTION with warnings; the user keeps the final
    relation-kind decision (document_relation_service confirm path), and
    rubric capability degrades honestly (unknown/unavailable).
    """

    try:
        from src.answer_relation_assessor import assess_answer_document
        from src.runtime import application_database

        database = application_database()
        document = database.get_document(page.document_id)
        if document is None:
            return
        title_line = (
            (getattr(document, "title", "") or "") + " " + (getattr(document, "filename", "") or "")
        )
        if not any(k in title_line for k in ("答案", "评分", "答题卡")):
            return
        text = page.extracted_text or ""
        if len(text.strip()) < 20:
            return
        assessment = assess_answer_document(text, title=getattr(document, "title", "") or "")
        if assessment.suggested_relation_kind == "unknown":
            st.info(
                "🧾 文档性质评估（F5）：无法可靠判断这份材料是参考答案还是"
                "评分标准——"
                + (assessment.warnings or ["请人工核对。"])[0]
                + "　评分能力："
                + assessment.rubric_capability
                + "。"
            )
            return
        if assessment.suggested_relation_kind == "reference_answer":
            st.info(
                "🧾 文档性质评估（F5）：这份材料更像是【参考答案】。"
                + (assessment.warnings[0] if assessment.warnings else "")
                + "　评分能力："
                + assessment.rubric_capability
                + "（系统不会把它当作正式评分标准使用；关系类型由你在确认时决定）。"
            )
        else:
            st.info(
                "🧾 文档性质评估（F5）：正文含评分细则与分值证据，建议关系为"
                "【评分标准】（仍需你确认）。评分能力：available。"
            )
    except Exception:  # noqa: BLE001 - banner must never break review
        LOGGER.debug("答案/评分评估失败（跳过）", exc_info=True)


def _render_source_conflict_banner(page: Page) -> None:
    """F1: surface a filename ↔ printed-paper-title conflict (P0 finding).

    Stateless and honest: the detector only SUGGESTS a conflict (exam-kind
    words disagreeing between the imported file name and the printed paper
    header); it never renames anything and the user decides what to trust.
    """

    try:
        from src.runtime import application_database
        from src.source_conflict_detector import (
            detect_source_conflict,
            paper_title_from_ocr,
        )

        database = application_database()
        document = database.get_document(page.document_id)
        if document is None:
            return
        ocr_text = page.extracted_text or ""
        paper_title = paper_title_from_ocr(ocr_text)
        if not paper_title:
            return
        conflict = detect_source_conflict(getattr(document, "filename", "") or "", paper_title)
        if conflict is not None:
            st.warning(f"⚠️ 来源核对：{conflict.message}")
    except Exception:  # noqa: BLE001 - banner must never break review
        LOGGER.debug("来源冲突检测失败（跳过）", exc_info=True)


def render_join_learning_section(page: Page) -> None:
    """Register one draft question of the chosen kind against this page.

    Only atomic question candidates can join the learning workflow.  A page
    must first be split or have individual questions supplied manually.

    Geography G1 (2026-09-28): this section IS the page-level workflow —
    the page review surface renders it as 「本页识别出的内容」 with an
    at-a-glance summary (候选/已加入/页面图表), per-candidate user edits
    and a manual add path for questions the AI missed.
    """

    _render_source_conflict_banner(page)
    _render_answer_assessment_banner(page)
    reference_flash = st.session_state.pop("learning_reference_flash", None)
    if reference_flash:
        st.info(reference_flash)
    st.markdown("**加入学习整理**")
    try:
        question_service = QuestionService(_database())
    except Exception:  # noqa: BLE001 - auxiliary entry must never break review
        LOGGER.debug("加入学习整理不可用", exc_info=True)
        return

    existing_document_questions = question_service.list_questions_for_document(page.document_id)
    selected_subject = _render_subject_picker(page.document_id, existing_document_questions)
    st.caption("学科由你在待核对时确认；同一平台可以分别整理不同学科。")
    if not selected_subject:
        st.info("请先选择学科；需要人工填写时，填写并确认后即可加入学习整理。")
    if existing_document_questions:
        if st.button(
            "把所选学科应用到这份资料已加入的题目",
            disabled=not selected_subject,
            key=f"join_learning_subject_apply_{page.document_id}",
        ):
            updated_count = question_service.update_subject_for_document(
                page.document_id, selected_subject
            )
            st.success(f"已把 {updated_count} 道已加入题目标记为“{selected_subject}”。")
            st.rerun()

    kind_column, _ = st.columns([3, 2])
    selected_kind = kind_column.radio(
        "题目类型",
        options=[kind for kind, _ in _KIND_LABELS],
        format_func=lambda kind: dict(_KIND_LABELS)[kind],
        horizontal=True,
        key=f"join_learning_kind_{page.id}",
        label_visibility="collapsed",
    )
    st.caption("先选类型（错题/好题/典型题/方法题），下方所有「加入」按钮都会用它。")

    store = _candidate_store()
    candidates = store.page_candidates(page.id) if store is not None else None

    if candidates is not None:
        leaf_entries = list(iter_atomic_leaves(candidates))
        pending_entries = [entry for entry in leaf_entries if entry[1].status == "pending"]
        pending = [entry[1] for entry in pending_entries]
        added = [entry[1] for entry in leaf_entries if entry[1].status == "added"]
        skipped_entries = [entry for entry in leaf_entries if entry[1].status == "skipped"]
        skipped = [entry[1] for entry in skipped_entries]
        atomic_roots = [
            candidate
            for candidate in candidates
            if candidate.is_leaf and candidate.status == "pending"
        ]
        composite_roots = [candidate for candidate in candidates if candidate.children]
        figure_count = _page_visual_figure_count(page)
        summary_bits = [f"题目候选 {len(pending)} 道待核对"]
        if added:
            summary_bits.append(f"已加入 {len(added)} 道")
        if skipped:
            summary_bits.append(f"暂不整理 {len(skipped)} 道")
        if figure_count:
            summary_bits.append(f"页面图表/图像 {figure_count} 处")
        st.markdown(f"**本页识别出的内容**：{'　·　'.join(summary_bits)}。")
        if pending:
            st.caption("请对照原图核对题干和公式；发现错误可展开「修改这道题」，保存后再加入。")
            source_labels = {
                "page_image": "AI 直接识别原页图片",
                "user_corrected_text": "人工校对文字（AI 按原文拆分）",
                "source_text": "提供的页面文字",
            }
            split_source_label = "、".join(
                dict.fromkeys(
                    "人工补录/修订"
                    if candidate.user_edited
                    else source_labels.get(candidate.recognition_source, "历史识别结果")
                    for candidate in pending
                )
            )
            st.caption(f"候选题干的文字来源：{split_source_label}。")
            if _page_has_unconfirmed_visual_draft(page):
                st.caption(
                    "提示：本页的 AI 视觉识别草稿还没有经你保存确认，"
                    "系统按约定不会自动采用它。先在上方「AI 视觉识别草稿」"
                    "里核对并保存，再重新拆分本页，题干质量通常会更好。"
                )
            _render_question_tree_section(page, composite_roots)
            _render_choice_group_section(page, atomic_roots)
            _render_cross_page_group_section(page, atomic_roots)
            _render_group_draft_section(page, atomic_roots)
            _render_candidate_cards(
                page,
                pending_entries,
                selected_kind,
                selected_subject,
                question_service,
                store,
            )
        if added:
            st.caption(
                "已加入学习整理的候选："
                + "、".join(c.number or "（无编号）" for c in added)
                + "。可在「学习整理」页继续整理。"
            )
        if skipped:
            with st.expander(f"已暂不整理（{len(skipped)} 条）—— 点「恢复」回到待处理"):
                for _root, skipped_candidate, skipped_path, _ancestors in skipped_entries:
                    restore_col, preview_col = st.columns([1, 5])
                    if restore_col.button(
                        "恢复",
                        key=f"cand_restore_{page.id}_{skipped_path}",
                    ):
                        store.mark_status_at_path(page.id, skipped_path, "pending")
                        st.rerun()
                    preview_col.caption(
                        f"{skipped_candidate.number or '（无编号）'}　"
                        f"{ui_plaintext_digest(skipped_candidate.stem or '', 70)}"
                    )

    # F4: handwriting regions are a PAGE-level surface — they render even
    # when this page has no candidate record at all (a handwritten answer
    # sheet that was never split into candidates is exactly the case where
    # region-level identity work matters).

    if store is not None:
        _render_manual_add(page, store, candidates)
    _render_candidate_split_section(page, candidates)


def _render_subject_picker(
    document_id: int,
    existing_questions: list[QuestionItem],
    *,
    profile: LearnerProfile | None = None,
) -> str:
    """Use saved training choices while surviving widget cleanup/navigation."""

    if profile is None:
        try:
            from src.runtime import application_training_profile_service

            profile = application_training_profile_service().get_profile(check_grade_upgrade=False)
        except Exception:  # noqa: BLE001 - offline organizing must stay available
            LOGGER.exception("读取训练配置的学科范围失败")
            st.warning("训练配置暂时无法读取，可先人工填写并确认学科。")
    policy = subject_selection_policy(profile)
    st.caption(f"学科范围跟随已保存的训练配置：{policy.label}。")

    durable_key = f"learning_subject_choice_{document_id}"
    widget_key = f"join_learning_subject_{document_id}"
    custom_key = f"join_learning_subject_custom_{document_id}"
    subjects = {normalize_subject_name(q.subject) for q in existing_questions if q.subject.strip()}
    existing_subject = next(iter(subjects)) if len(subjects) == 1 else ""
    remembered = dict(st.session_state.get(durable_key) or {})
    if not remembered:
        remembered = {
            "choice": existing_subject
            if existing_subject in policy.subjects
            else (ADVANCED_SUBJECT if policy.allows_advanced and existing_subject else ""),
            "custom": existing_subject
            if policy.manual_only
            or (policy.allows_advanced and existing_subject not in policy.subjects)
            else "",
            "confirmed": "",
        }
    if remembered.get("policy") != policy.signature:
        remembered["confirmed"] = ""
        remembered["manual_mode"] = ""
        remembered["policy"] = policy.signature
    if not policy.manual_only and remembered.get("choice") not in policy.choices:
        remembered["choice"] = existing_subject if existing_subject in policy.subjects else ""
    remembered.setdefault("custom", "")
    st.session_state[durable_key] = remembered
    choice = ""
    if not policy.manual_only:
        st.session_state[widget_key] = remembered["choice"] or None
        choice = (
            st.selectbox(
                "本次整理学科（人工选择）",
                options=policy.choices,
                key=widget_key,
                index=None,
                placeholder="请选择学科",
                help="选项跟随训练配置；所选学科只保存到本次整理的题目。",
                on_change=_remember_subject_value,
                args=(document_id, "choice", widget_key),
            )
            or ""
        )
    if policy.manual_only or choice == ADVANCED_SUBJECT:
        manual_mode = policy.label if policy.manual_only else ADVANCED_SUBJECT
        if remembered.get("manual_mode") != manual_mode:
            remembered["confirmed"] = ""
            remembered["manual_mode"] = manual_mode
        st.session_state[custom_key] = remembered["custom"]
        custom = st.text_input(
            "本次整理学科（人工填写）" if policy.manual_only else "填写强基/竞赛学科或方向",
            key=custom_key,
            placeholder=(
                "例如：高等数学、理论力学" if policy.manual_only else "例如：数学竞赛、物理强基"
            ),
            on_change=_remember_subject_value,
            args=(document_id, "custom", custom_key),
        ).strip()
        remembered["custom"] = custom
        if st.button("确认学科", key=f"join_subject_confirm_{document_id}", disabled=not custom):
            remembered["confirmed"] = custom
            st.toast(f"已确认学科：{custom}")
        confirmed = bool(custom) and remembered.get("confirmed") == custom
        st.caption(f"已确认本次整理学科：{custom}。" if confirmed else "填写后请点击「确认学科」。")
        result = custom if confirmed else ""
    else:
        result = choice
    remembered["choice"] = choice
    st.session_state[durable_key] = remembered
    return result


def _remember_subject_value(document_id: int, field: str, widget_key: str) -> None:
    """Capture user input before the rerun or widget cleanup can discard it."""

    durable_key = f"learning_subject_choice_{document_id}"
    remembered = dict(st.session_state[durable_key])
    remembered[field] = st.session_state[widget_key] or ""
    remembered["confirmed"] = ""
    st.session_state[durable_key] = remembered


def _page_visual_figure_count(page: Page) -> int:
    """Count usable AI figure/diagram readings on this page (summary only)."""

    try:
        from src.runtime import application_page_visual_service

        state = application_page_visual_service().get_page_visual_state(page.id)
        return sum(
            1
            for reading in state.get("interpretations", [])
            if reading.get("provenance") in ("IMAGE_REGION", "DIAGRAM_INTERPRETATION")
            and str(reading.get("content", "")).strip()
        )
    except Exception:  # noqa: BLE001 - summary must never break review
        LOGGER.debug("统计页面图表数失败（按 0 处理）", exc_info=True)
        return 0


def _render_manual_add(page: Page, store, candidates) -> None:
    """Manual path for a question the AI missed (Geography G1 §26)."""

    with st.expander("题目漏了？手动补一道"):
        manual_number = st.text_input(
            "题号（可留空）",
            key=f"cand_manual_number_{page.id}",
            placeholder="如：13",
        )
        manual_stem = st.text_area(
            "题干",
            key=f"cand_manual_stem_{page.id}",
            height=140,
            placeholder="把题干抄进来或按图描述；也可以先只写「见原图第 3 题」，稍后在题目库补全。",
        )
        if st.button(
            "补入候选列表",
            key=f"cand_manual_add_{page.id}",
            type="primary",
            disabled=not (manual_number.strip() or manual_stem.strip()),
        ):
            try:
                store.add_manual_candidate(page.id, manual_number, manual_stem)
            except Exception as exc:  # noqa: BLE001 - user-facing failure surface
                if isinstance(exc, LearningWorkflowError):
                    st.error(f"补录失败：{exc}")
                else:
                    LOGGER.exception("手动补录候选失败")
                    st.error("补录失败，请稍后重试。")
            else:
                _format_candidate_after_save(page, store, manual_stem.strip())
                st.toast("已补入候选列表（标记为你手动补录）。")
                st.rerun()
        st.caption("补录的候选会明确标记「手动补录」，不会冒充 AI 识别结果。")


def _page_has_unconfirmed_visual_draft(page: Page, visual_service: object | None = None) -> bool:
    """True when a handwriting draft exists but the presence gate skipped it.

    Mirrors the gate in :func:`_resolve_page_source`: the page has a
    ``HANDWRITING_VISION`` reading that is neither user-saved nor
    presence-confirmed, so the split fell back to a weaker source (usually
    raw OCR).  Surfacing this turns "why are my stems garbled?" into an
    actionable hint instead of a silent quality loss.
    """

    try:
        if visual_service is None:
            from src.runtime import application_page_visual_service

            visual_service = application_page_visual_service()
        state = visual_service.get_page_visual_state(page.id)
    except Exception:  # noqa: BLE001 - hint must never break review
        return False
    for reading in state.get("interpretations", []):
        if reading.get("provenance") != "HANDWRITING_VISION":
            continue
        if reading.get("user_edited"):
            continue
        presence = None
        raw_region = reading.get("region_json")
        if raw_region:
            try:
                region = json.loads(raw_region) if isinstance(raw_region, str) else raw_region
                if isinstance(region, dict):
                    presence = region.get("handwriting_presence")
            except (TypeError, ValueError):
                presence = None
        if presence != "confirmed":
            return True
    return False


def _handwriting_region_source_text(page: Page) -> str | None:
    """Page-level handwriting draft text eligible for region splitting.

    Same presence gate as :func:`_resolve_page_source`: only a reading the
    model declared ``confirmed`` or one the user saved may feed regions —
    a "none/possible" reading never becomes region rows.
    """

    try:
        from src.runtime import application_page_visual_service

        visual_service = application_page_visual_service()
        state = visual_service.get_page_visual_state(page.id)
    except Exception:  # noqa: BLE001 - section must never break review
        return None
    for reading in reversed(state.get("interpretations", [])):
        if reading.get("provenance") != "HANDWRITING_VISION":
            continue
        content = str(reading.get("content", "")).strip()
        if not content:
            continue
        if reading.get("user_edited"):
            return content
        presence = None
        raw_region = reading.get("region_json")
        if raw_region:
            try:
                region = json.loads(raw_region) if isinstance(raw_region, str) else raw_region
                if isinstance(region, dict):
                    presence = region.get("handwriting_presence")
            except (TypeError, ValueError):
                presence = None
        if presence == "confirmed":
            return content
    return None


def _render_handwriting_region_section(page: Page) -> None:
    """F4 minimal reliable pipeline (user-driven, never AI-guessed).

    page-level handwriting draft → region split (BY LINES — the only
    split that cannot fabricate semantics; no bbox is invented) →
    ``handwriting_regions`` rows with identity=unknown → the USER sets
    student/teacher identity and target subquestion.  The page-level
    draft stays untouched (original evidence is never consumed), and a
    region the user has not confirmed stays honestly "unknown".
    """

    from src.question_group_service import (
        add_handwriting_region,
        page_handwriting_regions,
        set_region_identity,
        set_region_target,
    )

    source_text = _handwriting_region_source_text(page)
    if not source_text:
        return
    with st.expander("✍️ 手写区域整理（先拆区域，再逐区确认身份与对应小问）"):
        st.caption(
            "原始页级识别内容永远保留在上方草稿里；这里只是把它拆成可逐条"
            "确认的区域。AI 不会替你猜这是谁写的、对应哪一问。"
        )
        regions = page_handwriting_regions(_database(), page.id)
        col_split, _ = st.columns([2, 3])
        if col_split.button(
            "把页级手写草稿拆分成区域（按行）",
            key=f"hw_split_{page.id}",
        ):
            lines = [line.strip() for line in source_text.splitlines() if line.strip()]
            created = 0
            for line in lines:
                add_handwriting_region(_database(), page_id=page.id, text=line)
                created += 1
            if created:
                st.toast(f"已拆分出 {created} 个手写区域，身份均为「未确认」。")
                st.rerun()
        if not regions:
            st.caption("还没有手写区域。先点上方按钮拆分。")
            return
        try:
            question_service = QuestionService(_database())
            doc_questions = question_service.list_questions_for_document(page.document_id)
        except Exception:  # noqa: BLE001 - binding stays optional
            doc_questions = []
        question_options = {0: "（暂不对应某一问）"}
        for item in doc_questions:
            label = f"第 {item.question_number} 题"
            stem_head = (item.stem_text or "").strip()[:18]
            if stem_head:
                label += f"　{stem_head}…"
            question_options[int(item.id)] = label
        identity_labels = {
            "unknown": "未确认（默认，AI 不猜）",
            "student": "学生作答",
            "teacher": "老师批注/讲评",
        }
        for region in regions:
            region_id = int(region["id"])
            identity = str(region.get("identity_draft") or "unknown")
            status = str(region.get("identity_status") or "ai_draft")
            status_label = "（你已确认）" if status == "user_confirmed" else ""
            with st.container(border=True):
                st.markdown(
                    f"**区域 #{region_id}**　"
                    f"{identity_labels.get(identity, identity)}"
                    f"　{status_label}"
                )
                text_value = str(region.get("text") or "")
                if text_value:
                    st.text(text_value)
                target_number = region.get("target_number")
                if target_number:
                    st.caption(f"当前对应：第 {target_number} 题")
                id_col, tgt_col = st.columns(2)
                picked_identity = id_col.selectbox(
                    "这是谁写的？",
                    options=["unknown", "student", "teacher"],
                    format_func=lambda value: identity_labels.get(value, value),
                    key=f"hw_identity_{region_id}",
                )
                if picked_identity in ("student", "teacher"):
                    if id_col.button("确认身份", key=f"hw_id_save_{region_id}"):
                        try:
                            set_region_identity(_database(), region_id, picked_identity)
                        except QuestionGroupError as exc:
                            st.error(f"身份确认失败：{exc}")
                        else:
                            st.toast("身份已确认。")
                            st.rerun()
                picked_target = tgt_col.selectbox(
                    "对应哪一问？",
                    options=list(question_options),
                    format_func=lambda value: question_options.get(value, str(value)),
                    index=0,
                    key=f"hw_target_{region_id}",
                )
                if tgt_col.button("保存对应关系", key=f"hw_tgt_save_{region_id}"):
                    try:
                        set_region_target(
                            _database(),
                            region_id,
                            int(picked_target) if picked_target else None,
                        )
                    except QuestionGroupError as exc:
                        st.error(f"对应关系保存失败：{exc}")
                    else:
                        st.toast("对应关系已保存。")
                        st.rerun()


def _candidate_status_line(candidate) -> str:
    """Show actionable figure dependencies without repeating completeness."""

    parts = []
    if candidate.visual_dependency == "required":
        parts.append("需结合图表")
    elif candidate.visual_dependency == "uncertain":
        parts.append("是否需图表待核对")
    return " · ".join(parts)


def _render_choice_group_section(page: Page, pending: list) -> None:
    """F3 P0: choice question-groups share their material ONCE.

    When the deterministic detector finds a choice group (common shared
    material + consecutive sub-question numbers), the review page shows
    ONE group card — shared material once, sub-questions separately with
    their OWN remaining stem — instead of N duplicate-stem candidates.
    The user confirms (creates the group) or ignores it; nothing is
    auto-locked.
    """

    try:
        from src.choice_group_detector import detect_choice_groups

        drafts = detect_choice_groups(pending, page.extracted_text or "")
    except Exception:  # noqa: BLE001 - detection must never break review
        LOGGER.debug("选择题组检测失败", exc_info=True)
        return
    if not drafts:
        return
    already_confirmed = bool(latest_group_for_page(_database(), page.id))
    for index, draft in enumerate(drafts):
        with st.container(border=True):
            st.markdown(
                f"**🧩 疑似选择题组（AI 草稿 · 待你确认）**　"
                f"小题 {'、'.join(draft.member_numbers)}　·　"
                f"识别把握：{draft.confidence}"
            )
            st.caption("检测依据：" + "；".join(draft.signals) + "。")
            if already_confirmed:
                st.success("✓ 本页已有确认的题组。下面新加入的小题会自动归入。")
            with st.expander("共享材料（全组共用，只显示/存一份）", expanded=True):
                st.text(draft.shared_text)
            st.markdown(f"**小题列表（{len(draft.member_numbers)} 题）**")
            import re as _re

            for number in draft.member_numbers:
                # display residual (fuzzy, review-only) with a quality gate:
                # a residual that still carries option markers or is barely
                # shorter than the stem is NOT readable — show the honest
                # pointer instead of noise.  The STORED stem keeps the
                # verbatim source either way (F3: no fabricated cuts).
                display = draft.display_unique_stems.get(number, "").strip()
                original_len = len(next((c.stem for c in pending if c.number == number), "")) or 1
                readable = (
                    display
                    and not _re.search(r"[ABCD][.．、]", display)
                    and len(display) <= 0.6 * original_len
                )
                st.markdown(
                    f"- **第 {number} 题**　"
                    + (
                        display[:80]
                        if readable
                        else "（题干以共享材料+选项作答，完整原文见下方独立候选卡）"
                    )
                )
            col_confirm, col_ignore = st.columns([2, 2])
            confirm_key = f"choice_group_confirm_{page.id}_{index}"
            if col_confirm.button(
                "✓ 确认为选择题组（加入的小题自动归组，独有题干入库）",
                key=confirm_key,
                type="primary",
            ):
                try:
                    database = _database()
                    group_id = draft_group(
                        database,
                        document_id=page.document_id,
                        group_number=draft.group_number,
                        page_ids=[page.id],
                        subquestion_numbers=draft.member_numbers,
                        group_type="choice",
                    )
                    add_group_material(
                        database,
                        group_id=group_id,
                        material_kind="text_material",
                        # draft.group_number already ends with 「题组」
                        # (e.g. "1~2题组") — appending another one produced
                        # 「第1~2题组题组共享材料」 on the student surface.
                        material_label=f"{draft.group_number}共享材料",
                        page_id=page.id,
                        content_text=draft.shared_text,
                    )
                    from src.question_group_service import confirm_group

                    confirm_group(database, group_id)
                    st.toast("已确认为选择题组：共享材料一份，小题独立加入。")
                except QuestionGroupError as exc:
                    st.error(f"选择题组确认失败：{exc}")
                except Exception:  # noqa: BLE001 - user-facing failure surface
                    LOGGER.exception("选择题组确认失败")
                    st.error("选择题组确认失败，请稍后重试。")
                else:
                    st.rerun()
            if col_ignore.button(
                "不按题组处理（保持独立候选）",
                key=f"choice_group_ignore_{page.id}_{index}",
            ):
                st.session_state[f"choice_group_dismissed_{page.id}_{index}"] = True
                st.rerun()


def _render_cross_page_group_section(page: Page, pending: list) -> None:
    """F6: suggest question groups whose hint range SPANS pages.

    Looks at a small window of neighbouring pages of the SAME document,
    runs the deterministic cross-page detector, and — for drafts that
    involve THIS page — shows one proposal card.  Confirming registers
    ONE group whose source_pages hold every involved page id, and ONE
    material row per source page (zero-copy page refs, never duplicated
    content).  Under-coverage or duplicates produce no card at all.
    """

    try:
        from src.choice_group_detector import detect_cross_page_groups

        database = _database()
        store = _candidate_store()
        if store is None:
            return
        document_pages = sorted(
            database.list_pages(page.document_id),
            key=lambda item: item.page_number,
        )
        window = [item for item in document_pages if abs(item.page_number - page.page_number) <= 3]
        candidates_by_page: dict[int, list] = {}
        page_texts: dict[int, str] = {}
        page_id_by_number: dict[int, int] = {}
        for item in window:
            item_candidates = store.page_candidates(item.id) or []
            pending_items = [c for c in item_candidates if c.status == "pending"]
            if pending_items:
                candidates_by_page[item.page_number] = pending_items
            page_texts[item.page_number] = item.extracted_text or ""
            page_id_by_number[item.page_number] = item.id
        if len(candidates_by_page) < 2:
            return
        drafts = detect_cross_page_groups(candidates_by_page, page_texts)
        relevant = [draft for draft in drafts if page.page_number in draft.page_numbers]
        if not relevant:
            return
        already_confirmed = bool(latest_group_for_page(_database(), page.id))
        for index, draft in enumerate(relevant):
            with st.container(border=True):
                st.markdown(
                    f"**🧩 疑似跨页题组（AI 草稿 · 待你确认）**　"
                    f"小题 {'、'.join(draft.member_numbers)}　·　"
                    f"跨第 {'、'.join(str(n) for n in draft.page_numbers)} 页"
                )
                st.caption("检测依据：" + "；".join(draft.signals) + "。")
                if already_confirmed:
                    st.success("✓ 本页已有确认的题组。下面新加入的小题会自动归入。")
                if draft.shared_text:
                    with st.expander("共享材料（全组共用，只显示/存一份）", expanded=True):
                        st.text(draft.shared_text)
                member_page_bits = []
                for number in draft.member_numbers:
                    owner_page = next(
                        (
                            pn
                            for pn in draft.page_numbers
                            if any(c.number == number for c in candidates_by_page.get(pn, []))
                        ),
                        draft.page_numbers[0],
                    )
                    member_page_bits.append(f"第 {number} 题（第 {owner_page} 页）")
                st.markdown("**小题与所在页**：" + "；".join(member_page_bits))
                col_confirm, col_ignore = st.columns([2, 2])
                confirm_key = f"cross_page_confirm_{page.id}_{index}"
                if col_confirm.button(
                    "✓ 确认为跨页题组（两页各自登记来源，材料只存一份）",
                    key=confirm_key,
                    type="primary",
                ):
                    try:
                        source_page_ids = [
                            page_id_by_number[pn]
                            for pn in draft.page_numbers
                            if pn in page_id_by_number
                        ]
                        database2 = _database()
                        group_id = draft_group(
                            database2,
                            document_id=page.document_id,
                            group_number=draft.group_number,
                            page_ids=source_page_ids,
                            subquestion_numbers=draft.member_numbers,
                            group_type="choice",
                        )
                        for position, pn in enumerate(draft.page_numbers):
                            if pn not in page_id_by_number:
                                continue
                            if position == 0:
                                label = f"{draft.group_number}共享材料"
                                content = draft.shared_text
                            else:
                                label = f"{draft.group_number}共享材料（第 {pn} 页延续）"
                                content = ""
                            add_group_material(
                                database2,
                                group_id=group_id,
                                material_kind="text_material",
                                material_label=label,
                                page_id=page_id_by_number[pn],
                                content_text=content,
                            )
                        from src.question_group_service import confirm_group

                        confirm_group(database2, group_id)
                        st.toast("已确认为跨页题组：来源页已逐页登记，小题独立加入。")
                    except QuestionGroupError as exc:
                        st.error(f"跨页题组确认失败：{exc}")
                    except Exception:  # noqa: BLE001 - user-facing failure
                        LOGGER.exception("跨页题组确认失败")
                        st.error("跨页题组确认失败，请稍后重试。")
                    else:
                        st.rerun()
                if col_ignore.button(
                    "不按跨页题组处理（保持独立候选）",
                    key=f"cross_page_ignore_{page.id}_{index}",
                ):
                    st.session_state[f"cross_page_dismissed_{page.id}_{index}"] = True
                    st.rerun()
    except Exception:  # noqa: BLE001 - detection must never break review
        LOGGER.debug("跨页题组检测不可用", exc_info=True)


def _subquestion_numbers(numbers: list[str]) -> list[str]:
    """Return candidate numbers that look like subquestions of ONE group.

    Matches ``(1)`` / ``1)`` / ``23(1)`` / ``23（1）`` — the shapes the
    Round-2 splitter produced.  At least TWO matches are required before a
    group card is even suggested.
    """

    import re as _re

    pattern = _re.compile(r"^\s*(?:\d+)?[（(]\s*\d+\s*[)）]\s*$")
    return [n for n in numbers if n and pattern.match(n or "")]


def _render_group_draft_section(page: Page, pending: list) -> None:
    """G3-B P0-1: aggregate subquestion candidates into ONE group card.

    The AI only *suggests* the aggregation; nothing is written until the
    user clicks 「确认为题组」.  Keeping the questions independent stays a
    fully supported choice (the card says so explicitly).
    """

    sub_numbers = _subquestion_numbers([c.number for c in pending])
    if len(sub_numbers) < 2:
        return
    existing = None
    try:
        existing = latest_group_for_page(_database(), page.id)
    except Exception:  # noqa: BLE001 - group section must never break review
        LOGGER.debug("读取题组失败", exc_info=True)
    if existing is not None:
        st.success(
            f"✓ 已确认题组：第 {existing['group_number']} 题综合题。"
            "下面新加入的题目会自动归入这个组（小问保持独立整理）。"
        )
        return
    try:
        document = _database().get_document(page.document_id)
        document_title = getattr(document, "title", "") or f"文档#{page.document_id}"
    except Exception:  # noqa: BLE001 - display fallback only
        document_title = f"文档#{page.document_id}"
    group_number_guess = f"第{page.page_number}页综合题"
    with st.container(border=True):
        st.markdown(
            f"**🧩 疑似综合题组（AI 草稿 · 待你确认）**　"
            f"小问 {'、'.join(sub_numbers)}　·　来源：{document_title} 第"
            f" {page.page_number} 页"
        )
        st.caption(
            "综合题的材料和图是整道题共用的；确认成组后，各小问仍独立整理、"
            "独立归纳、独立训练，只是不再互不相干。你也可以不确认，"
            "让它们保持独立题目。"
        )
        if st.button(
            "✓ 确认为题组（AI 已按小问聚组，你来确认）",
            key=f"group_confirm_{page.id}",
            type="primary",
        ):
            try:
                database = _database()
                group_id = draft_group(
                    database,
                    document_id=page.document_id,
                    group_number=group_number_guess,
                    page_ids=[page.id],
                    subquestion_numbers=sub_numbers,
                )
                visual_notes = next(
                    (c.visual_notes for c in pending if c.needs_visual and c.visual_notes),
                    "",
                )
                add_group_material(
                    database,
                    group_id=group_id,
                    material_kind="figure" if visual_notes else "text_material",
                    material_label=visual_notes or "本页共享材料",
                    page_id=page.id,
                    content_text="",
                )
                from src.question_group_service import confirm_group

                confirm_group(database, group_id)
                st.toast(
                    f"已确认为题组：第 {group_number_guess} 题综合题。之后加入的小问会自动归组。"
                )
            except QuestionGroupError as exc:
                st.error(f"题组确认失败：{exc}")
            except Exception:  # noqa: BLE001 - user-facing failure surface
                LOGGER.exception("题组确认失败")
                st.error("题组确认失败，请稍后重试。")
            else:
                st.rerun()


def _render_question_tree_section(page: Page, roots: list) -> None:
    """Show composite structure before any leaf can enter learning."""

    if not roots:
        return
    leaf_count = sum(1 for root in roots for node in iter_question_nodes([root]) if node.is_leaf)
    st.info(f"检测到综合题，已拆成 {leaf_count} 个可单独整理的小题。")

    def lines(node, prefix: str = "") -> list[str]:
        marker = "└─" if prefix else ""
        label = node.number or "（未识别题号）"
        suffix = " [拆分把握不高，请对照原卷]" if node.split_confidence == "low" else ""
        result = [f"{prefix}{marker}{label}{suffix}"]
        child_prefix = prefix + ("  " if prefix else "")
        for child in node.children:
            result.extend(lines(child, child_prefix or "  "))
        return result

    with st.container(border=True):
        st.markdown("**题目结构树（父题只保留上下文，不产生掌握状态）**")
        tree_lines: list[str] = []
        for root in roots:
            tree_lines.extend(lines(root))
        st.code("\n".join(tree_lines), language=None)
        st.caption(
            "下方只给可单独作答的小题显示「加入学习整理」按钮；"
            "每个小题仍会保留这道大题的公共材料、图像和来源。"
        )


def _render_candidate_cards(
    page: Page,
    pending_entries: list,
    selected_kind: str,
    selected_subject: str,
    question_service: QuestionService,
    store,
) -> None:
    for root, candidate, node_path, ancestors in pending_entries:
        # ``pending_entries`` shrinks as soon as a question is joined/skipped.
        # An enumerate()-based key therefore reuses the previous card's
        # Streamlit widget state for the next question and can overwrite that
        # question with stale text.  ``node_path`` comes from the complete
        # persisted question tree, so it remains stable across lifecycle
        # changes to sibling candidates.
        candidate_key = _candidate_widget_suffix(
            page.id,
            node_path=node_path,
            number=candidate.number,
            stem=candidate.stem,
            extracted_at=candidate.extracted_at,
        )
        figure_label = (
            "　涉及：" + "、".join(candidate.figure_refs) if candidate.figure_refs else ""
        )
        stem_display = candidate_display_stem(store.root, page.id, candidate.stem)
        with st.container(border=True):
            head = (
                f"**{candidate.number or '（未检测到题号）'}**"
                f"　{_candidate_status_line(candidate)}{figure_label}"
            )
            if candidate.user_edited:
                head += "　·　手动补录/已修订"
            st.markdown(head)
            if candidate.needs_visual and candidate.visual_notes:
                if candidate.binding_confirmed:
                    st.caption(f"📄 {candidate.visual_notes}　·　图表关联已确认")
                else:
                    st.caption(
                        f"📄 {candidate.visual_notes}　·　图表关联待核对"
                        "（原图就在本页，尚未确认绑定）"
                    )
            shared_stems = [
                ancestor.stem for ancestor in ancestors
                if ancestor.has_shared_stem is not False and ancestor.stem.strip()
            ]
            if shared_stems:
                with st.expander("本小题需要的公共题干", expanded=True):
                    for shared_stem in shared_stems:
                        render_question_math_markdown(shared_stem)
            regions = _visual_material_block(candidate, page, ancestors=ancestors)["regions"]
            shared_regions = [r for r in regions if r["role"] == "shared"]
            if shared_regions:
                st.markdown("**公共图像材料**")
                render_region_images(page.image_path, shared_regions)
            render_question_content(
                stem_display or "（未识别到题干文字）",
                image_path=page.image_path,
                regions=regions,
            )
            if (
                candidate.image_recognized_stem
                and candidate.image_recognized_stem != candidate.stem
            ):
                with st.expander("本次 AI 读图原题（人工修订已保留）"):
                    render_question_content(
                        candidate.image_recognized_stem,
                        image_path=page.image_path,
                        regions=regions,
                    )
            from src.question_image_editor import (
                render_question_crop_editor,
                render_question_image_editor,
            )

            render_question_image_editor(
                page.image_path, page_id=page.id, number=candidate.number, regions=regions,
                key=f"candidate_image_{candidate_key}",
            )
            render_question_crop_editor(
                page.image_path, page_id=page.id, number=candidate.number, regions=regions,
                key=f"candidate_crop_{candidate_key}",
            )
            col_join, col_skip, col_ignore = st.columns([2, 2, 2])
            join_key = f"cand_join_{candidate_key}"
            skip_key = f"cand_skip_{candidate_key}"
            ignore_key = f"cand_ignore_{candidate_key}"
            if col_join.button(
                "加入学习整理",
                key=join_key,
                type="primary",
                disabled=not selected_subject,
            ):
                _add_candidate(
                    page,
                    candidate,
                    selected_kind,
                    selected_subject,
                    question_service,
                    store,
                    root_candidate=root,
                    node_path=node_path,
                    ancestors=ancestors,
                )
            if col_skip.button("暂不整理", key=skip_key):
                store.mark_status_at_path(page.id, node_path, "skipped")
                st.toast("已暂不整理（保留在下方「已暂不整理」，可随时恢复）。")
                st.rerun()
            if col_ignore.button("忽略此候选", key=ignore_key):
                store.mark_status_at_path(page.id, node_path, "ignored")
                st.toast("已忽略此候选（不影响原图；重新拆分本页会重出候选）。")
                st.rerun()
            if candidate.needs_visual and not candidate.binding_confirmed:
                if st.button(
                    "✓ 核对关联（确认这就是本题的图表）",
                    key=f"cand_bind_{candidate_key}",
                ):
                    store.confirm_visual_binding_at_path(page.id, node_path)
                    st.toast("已确认图表关联（记录为你的确认，而非 AI 判断）。")
                    st.rerun()
            _render_candidate_edit(page, candidate, store, candidate_key, node_path=node_path)


def _candidate_widget_suffix(
    page_id: int,
    *,
    node_path: str,
    number: str,
    stem: str,
    extracted_at: str,
) -> str:
    """Return a stable Streamlit widget suffix for one persisted candidate.

    ``node_path`` is page-level and therefore separates repeated sub-question
    labels under different roots.  Candidate content and extraction time keep
    the key stable across sibling lifecycle changes while ensuring a saved
    edit receives fresh widget state on the next rerun.
    """

    identity = json.dumps(
        [page_id, node_path, number, stem, extracted_at],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"{page_id}_{digest}"


def _root_local_node_path(page_node_path: str) -> str:
    """Translate a page-level candidate path to a root-local structure path."""

    parts = str(page_node_path).split(".")
    return ".".join(["0", *parts[1:]])


def _render_candidate_edit(
    page: Page,
    candidate,
    store,
    candidate_key: str,
    *,
    node_path: str,
) -> None:
    """Per-candidate user edit (题号/题干/图像绑定) — Geography G1 §26."""

    with st.expander("修改这道题"):
        edited_number = st.text_input(
            "题号",
            value=candidate.number,
            key=f"cand_edit_number_{candidate_key}",
        )
        edited_stem = st.text_area(
            "题干（可对照上方原图修改）",
            value=candidate.stem,
            height=160,
            key=f"cand_edit_stem_{candidate_key}",
        )
        edited_figures = st.text_input(
            "涉及的图（逗号分隔，如：图 1，长江流域示意）",
            value="、".join(candidate.figure_refs),
            key=f"cand_edit_figures_{candidate_key}",
        )
        save_clicked = st.button(
            "保存修改",
            key=f"cand_edit_save_{candidate_key}",
            type="primary",
        )
        if save_clicked:
            figure_list = [
                part.strip()
                for part in edited_figures.replace("，", ",").split(",")
                if part.strip()
            ]
            try:
                store.update_candidate_at_path(
                    page.id,
                    node_path,
                    new_number=edited_number,
                    stem=edited_stem,
                    figure_refs=figure_list,
                )
            except Exception as exc:  # noqa: BLE001 - user-facing failure surface
                if isinstance(exc, LearningWorkflowError):
                    st.error(f"保存修改失败：{exc}")
                else:
                    LOGGER.exception("候选修改保存失败")
                    st.error("保存修改失败，请稍后重试。")
            else:
                _format_candidate_after_save(page, store, edited_stem.strip())
                st.toast("候选已更新（记录为你修订过的版本）。")
                st.rerun()


def _format_candidate_after_save(page: Page, store: QuestionCandidateStore, source: str) -> None:
    """Queue optional AI typesetting; never delay the saved local math preview."""

    try:
        from src.learning_ai_draft_service import LearningAIDraftService
        from src.runtime import application_ai_provider

        provider = application_ai_provider()
        if provider is not None and source:
            schedule_candidate_math(store.root, page.id, source, LearningAIDraftService(provider))
    except Exception:  # noqa: BLE001 - optional AI must never block human saves
        LOGGER.warning("候选数学排版不可用，保留人工修改和本地排版", exc_info=True)


def _visual_material_block(candidate, page: Page, *, ancestors: tuple = ()) -> dict:
    """Build the ai_draft.visual_material payload for layer 1 (G2-A-02).

    The ORIGINAL page image stays the single raw asset — the question only
    ever references it (page_id + material description).  AI association is
    a draft until the user confirms; nothing here copies image bytes.
    """

    inherited = [
        ancestor
        for ancestor in ancestors
        if getattr(ancestor, "visual_dependency", "none") in ("required", "uncertain")
    ]
    local_dependency = str(getattr(candidate, "visual_dependency", "uncertain"))
    effective_dependency = (
        local_dependency if local_dependency != "none" else ("required" if inherited else "none")
    )
    material_notes = str(getattr(candidate, "visual_notes", "") or "").strip()
    if not material_notes and inherited:
        material_notes = "；".join(
            dict.fromkeys(
                str(getattr(item, "visual_notes", "") or "").strip()
                for item in inherited
                if str(getattr(item, "visual_notes", "") or "").strip()
            )
        )
    figure_refs = list(getattr(candidate, "figure_refs", []) or [])
    for item in inherited:
        for reference in list(getattr(item, "figure_refs", []) or []):
            if reference not in figure_refs:
                figure_refs.append(reference)
    binding_confirmed = bool(getattr(candidate, "binding_confirmed", False))
    if inherited:
        binding_confirmed = binding_confirmed and all(
            bool(getattr(item, "binding_confirmed", False)) for item in inherited
        )
    regions = list(getattr(candidate, "visual_regions", []) or [])
    for ancestor in ancestors:
        for region in getattr(ancestor, "visual_regions", []) or []:
            if region.get("role") in ("stem", "shared"):
                shared_region = {**region, "role": "shared"}
                if shared_region not in regions:
                    regions.append(shared_region)
    from src.question_content_ui import question_regions

    regions = question_regions(
        getattr(page, "image_path", None), str(candidate.number or ""), regions, page_id=page.id,
        committed_only=True,
    )
    if regions:
        effective_dependency = "required"
    return {
        "dependency": effective_dependency,
        "material_notes": material_notes,
        "figure_refs": figure_refs,
        "binding_confirmed": binding_confirmed,
        "binding_provenance": (
            "USER CONFIRMED BINDING"
            if binding_confirmed
            else "AI BINDING DRAFT（AI 关联建议，未经你确认）"
        ),
        "source_page_id": page.id,
        "regions": regions,
    }


def _page_pending(page: Page, store) -> list:
    """Current pending candidates of one page (detection input)."""

    candidates = store.page_candidates(page.id) if store is not None else None
    return [c for c in (candidates or []) if c.status == "pending"]


def _add_candidate(
    page: Page,
    candidate,
    selected_kind: str,
    selected_subject: str,
    question_service: QuestionService,
    store,
    *,
    root_candidate=None,
    node_path: str = "0",
    ancestors: tuple = (),
) -> None:
    # F3: when this candidate belongs to a detected choice group, the
    # question stores only its OWN remaining stem; the shared material
    # lives once in the group.  Full AI source text stays in ai_draft.
    stem_text = candidate.stem
    shared_text_used = ""
    try:
        from src.choice_group_detector import detect_choice_groups

        pending_now = _page_pending(page, store)
        for draft in detect_choice_groups(pending_now, page.extracted_text or ""):
            if candidate.number in draft.member_numbers:
                unique = draft.unique_stems.get(candidate.number, "").strip()
                if unique:
                    stem_text = unique
                    shared_text_used = draft.shared_text
                break
    except Exception:  # noqa: BLE001 - stem narrowing must never break joining
        LOGGER.debug("选择题组独有题干计算失败（保留原文）", exc_info=True)
    try:
        from src.question_structure_service import (
            link_atomic_question,
            materialize_candidate_tree,
        )

        root_candidate = root_candidate or candidate
        node_ids = materialize_candidate_tree(
            _database(),
            document_id=page.document_id,
            source_page_id=page.id,
            root_candidate=root_candidate,
        )
        structure_node_path = _root_local_node_path(node_path)
        structure_node_id = node_ids[structure_node_path]
        question = question_service.create_question_item(
            document_id=page.document_id,
            page_id=page.id,
            question_kind=selected_kind,
            subject=selected_subject,
            question_number=candidate.number,
            stem_text=stem_text,
            stem_confidence=stem_confidence_for(candidate.completeness),
            ai_draft={
                "kind": "question_candidate",
                "origin": "user_manual_candidate"
                if candidate.user_edited
                else "ai_question_candidate_split",
                "provenance": (
                    "用户手动补录/修订的候选（系统忠实保存，未经 AI 改写）"
                    if candidate.user_edited
                    else "AI 题目识别候选（依据原卷，数学表达已排版，待核对）"
                ),
                "candidate": {
                    "number": candidate.number,
                    "stem": candidate.stem,
                    "completeness": candidate.completeness,
                    "incomplete_reason": candidate.incomplete_reason,
                    "figure_refs": candidate.figure_refs,
                    "user_edited": candidate.user_edited,
                    "visual_dependency": candidate.visual_dependency,
                    "visual_notes": candidate.visual_notes,
                    "binding_confirmed": candidate.binding_confirmed,
                    "recognition_source": candidate.recognition_source,
                    "visual_regions": candidate.visual_regions,
                },
                "math_display": {
                    "stem_text": {
                        "source": stem_text,
                        "display": candidate_display_stem(store.root, page.id, stem_text),
                    }
                },
                "choice_group_sharing": {
                    "shared_text_len": len(shared_text_used),
                    "shared_text": shared_text_used,
                    "full_source_stem": candidate.stem,
                    "note": ("本小题属于选择题组：独有题干已入库，共享材料只在题组保存一份（F3）")
                    if shared_text_used
                    else "",
                },
                "visual_material": _visual_material_block(candidate, page, ancestors=ancestors),
                "question_structure": {
                    "node_id": structure_node_id,
                    "root_node_id": node_ids["0"],
                    "node_path": structure_node_path,
                    "question_kind": "atomic",
                    "ancestor_labels": [str(parent.number or "") for parent in ancestors],
                    "split_source": candidate.split_source,
                    "split_confidence": candidate.split_confidence,
                    "note": "叶子题通过 question_nodes 动态继承父题上下文",
                },
            },
        )
        link_atomic_question(
            _database(),
            node_id=structure_node_id,
            question_item_id=int(question.id),
        )
        store.mark_status_at_path(page.id, node_path, "added")
        # G3-B: attach to the page's confirmed group, then let layer-2
        # suggestions surface immediately (family draft AFTER the question
        # joins layer 1 — the G3-A timing gap).  Both steps are best-effort:
        # a group/family failure never undoes the join itself.
        try:
            group = latest_group_for_page(_database(), page.id)
            member_numbers = group_member_numbers(group) if group is not None else None
            candidate_number = normalize_candidate_number(candidate.number)
            # F-BOSS-02: auto-attach ONLY when the candidate number is a
            # printed member of the group.  Same-page strays (the previous
            # question's leftover stem) must NOT join the group silently.
            if (
                group is not None
                and member_numbers is not None
                and candidate_number in member_numbers
            ):
                attach_question_to_group(_database(), int(question.id), int(group["id"]))
        except Exception:  # noqa: BLE001 - grouping must never break joining
            LOGGER.debug("题目归组失败（题目已加入）", exc_info=True)
        try:
            from src.learning_workflow_service import QuestionOrganizationService

            QuestionOrganizationService(_database()).auto_organize_question(int(question.id))
        except Exception:  # noqa: BLE001 - family draft must never break joining
            LOGGER.debug("加入后自动归纳失败（题目已加入）", exc_info=True)
        # Joining owns one reference generation; failures never undo the saved question.
        try:
            from src.learning_reference_service import generate_join_reference
            from src.learning_workflow_service import QuestionOrganizationService
            from src.runtime import (
                application_ai_provider,
                application_question_vision_provider,
                application_training_profile_service,
            )

            with st.spinner("正在生成订正、解析、题型和方法的参考版…"):
                drafts = generate_join_reference(
                    question_service.get_question_item(int(question.id)), _database(),
                    provider=application_ai_provider(),
                    vision_provider=application_question_vision_provider(),
                    learner_profile=application_training_profile_service().get_profile(),
                )
                question_service.save_ai_reference(int(question.id), drafts)
            try:
                QuestionOrganizationService(_database()).organize_with_confidence(
                    int(question.id), type_family=drafts.get("type_family"),
                    method_families=drafts.get("method_families") or None,
                    secondary_conclusion=None,
                )
            except Exception:  # noqa: BLE001 - classification never discards the reference
                LOGGER.warning("加入后归类建议未完成：question_id=%s", question.id, exc_info=True)
            st.session_state["learning_reference_flash"] = (
                "已加入学习整理，并生成订正、解析、题型和方法的 AI 参考版，请核对后修改保存。"
            )
        except Exception as reference_exc:  # noqa: BLE001 - preserve the successful join
            from src.ai.provider import AIExecutionError, AIUnavailableError
            from src.learning_ai_draft_service import LearningAIDraftError

            if isinstance(reference_exc, LearningAIDraftError) and reference_exc.reference:
                question_service.record_reference_failure(
                    int(question.id), str(reference_exc), reference_exc.reference,
                )

            safe_message = (
                str(reference_exc)
                if isinstance(reference_exc, (
                    LearningAIDraftError, AIExecutionError, AIUnavailableError,
                ))
                else "参考版生成未完成，请检查模型配置和网络环境。"
            )
            LOGGER.warning("加入后参考版未完成：question_id=%s", question.id, exc_info=True)
            st.session_state["learning_reference_flash"] = (
                "题目已加入学习整理，但参考版暂未生成：" + safe_message
            )
    except Exception as exc:  # noqa: BLE001 - user-facing failure surface
        if isinstance(exc, LearningWorkflowError):
            st.error(f"加入学习整理失败：{exc}")
            return
        LOGGER.exception("加入学习整理失败")
        st.error("加入学习整理失败，请稍后重试。")
        return
    st.success(
        f"已把 {candidate.number or '该候选'} 加入学习整理"
        f"（{dict(_KIND_LABELS)[selected_kind]}）。请到「学习整理」页核对题干后确认。"
    )
    st.rerun()


def _render_candidate_split_section(page: Page, candidates: list[QuestionCandidate] | None) -> None:
    """Split/re-split pages; whole pages cannot become learning questions."""

    has_candidates = candidates is not None
    expander_title = "重新拆分本页题目" if has_candidates else "先拆分本页题目"
    with st.expander(expander_title):
        if not has_candidates:
            extract_clicked = st.button(
                "AI 拆分本页题目候选",
                key=f"extract_candidates_{page.id}",
                type="primary",
            )
            if extract_clicked:
                _extract_candidates(page)
                return
            st.caption(
                "请先按原卷题号和小问拆分，再逐题加入学习整理。"
                "一页只有一道大题时，也应按其中可独立作答的小问整理；"
                "还可以用「手动补一道」逐题补录。"
            )
        else:
            # Geography G1 (§26): a re-split must stay reachable after the
            # first extraction — e.g. after confirming a visual draft or a
            # source-priority fix — otherwise stale stems are stuck.
            st.caption(
                "重新直接读取原页图片并更新 AI 候选；"
                "人工修订和已加入/已忽略状态会保留。"
                "已加入学习整理的题目保留人工修改，新识别的图像区域会同步到对应题目。"
            )
            if st.button(
                "重新读图并切分本页",
                key=f"resplit_candidates_{page.id}",
            ):
                _extract_candidates(page)
                return


def _extract_candidates(page: Page) -> None:
    """Run one AI extraction of per-question candidates for this page."""

    try:
        from src.runtime import application_page_image_reader

        reader = application_page_image_reader()
        if reader.provider is None:
            st.warning("请先启用支持直接读图的 Qwen 3.8 Max 或 Flash。")
            return
        with st.spinner("正在直接读图、识别公式并切分题目与图表……"):
            st.caption(
                f"本次识别使用：{reader.provider.provider_id} / {reader.provider.default_model}"
            )
            reader.read_page(page.id)
        st.success("本页读图结果已保存，人工修订和已加入状态已保留。")
    except Exception as exc:  # noqa: BLE001 - keep the review page usable
        LOGGER.exception("页面直接读图失败：page_id=%s", page.id)
        st.error(f"读图失败：{exc}")
        return
    st.rerun()


def _add_whole_page(
    page: Page,
    selected_kind: str,
    selected_subject: str,
    question_service: QuestionService,
) -> None:
    try:
        content, provenance_label, extra_fields = _resolve_page_source(page)
        source_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest() if content else ""
        if content:
            duplicate = question_service.find_duplicate_source(
                document_id=page.document_id,
                page_id=page.id,
                question_kind=selected_kind,
                source_sha256=source_sha256,
            )
            if duplicate is not None:
                st.info(
                    "这一页的同来源内容已经整理过（"
                    f"{dict(_KIND_LABELS)[selected_kind]}），没有重复创建。"
                    "可在「学习整理」页继续整理。"
                )
                return
        question = question_service.create_question_item(
            document_id=page.document_id,
            page_id=page.id,
            question_kind=selected_kind,
            subject=selected_subject,
            stem_confidence="uncertain",
            ai_draft={
                "kind": "source_context",
                "segmentation": "pending",
                "question_granularity": {
                    "question_kind": "atomic",
                    "split_source": "manual",
                    "split_confidence": "high",
                    "confirmed_by_user": True,
                },
                "provenance": provenance_label,
                "content": content,
                "source_sha256": source_sha256,
                **extra_fields,
            },
        )
        from src.question_candidate_service import QuestionCandidate
        from src.question_structure_service import (
            link_atomic_question,
            materialize_candidate_tree,
        )

        manual_root = QuestionCandidate(
            number="",
            stem=content,
            completeness="complete" if content else "incomplete",
            split_source="manual",
            split_confidence="high",
        )
        node_ids = materialize_candidate_tree(
            _database(),
            document_id=page.document_id,
            source_page_id=page.id,
            root_candidate=manual_root,
        )
        link_atomic_question(_database(), node_id=node_ids["0"], question_item_id=int(question.id))
    except LearningWorkflowError as exc:
        st.error(f"加入学习整理失败：{exc}")
        return
    except Exception:  # noqa: BLE001 - never break the review page
        LOGGER.exception("加入学习整理失败")
        st.error("加入学习整理失败，请稍后重试。")
        return
    if content:
        st.success(
            f"已加入学习整理（{dict(_KIND_LABELS)[selected_kind]}）。"
            f"来源：{provenance_label}。请到「学习整理」页查看自动草稿并确认——"
            "一页往往有多道题，先核对再用。"
        )
    else:
        st.warning(
            "已加入学习整理，但这一页没有可用的文字来源"
            "（无识别文字 / 无草稿 / 无人工文本）。请在「学习整理」页手动补题干，"
            "或先执行文字识别。"
        )


def _candidate_store():
    try:
        from src.question_candidate_service import QuestionCandidateStore
        from src.runtime import application_settings

        return QuestionCandidateStore(Path(application_settings().data_dir) / "question-candidates")
    except Exception:  # noqa: BLE001 - auxiliary entry must never break review
        LOGGER.debug("题目候选存储不可用", exc_info=True)
        return None


def _database():
    from src.runtime import application_database

    return application_database()
