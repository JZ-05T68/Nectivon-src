"""学习整理：三层整理、一层输出、两翼布局的完整工作流（v0.8.6）。

Overnight real-corpus round (2026-09-26) product rules:

- Tab order follows the learning path: 题目库（第一层）→ 归纳族（第二层）→
  掌握训练（第三层）→ 方法触发 → 边界反例 → 导出 → 训练配置。The two wings are
  two independent tabs, never one笼统的「两翼」。
- 第一层 is a per-question card workflow (① 题干 ② 我的作答 ③ 判定
  ④ 订正 ⑤ 错因 ⑥ 方法): AI fills *empty* fields as drafts, the user
  reviews/edits/saves; user content is never silently overwritten and a
  correct question never gets a fabricated correction.
- Internal engineering identifiers (database ids, English schema keys,
  raw JSON payloads) never reach the student surface — display labels
  come from :mod:`src.display_labels`.
- Final error-cause tags may never be 粗心/马虎/计算错误-style words.
- After every save the layer-2 auto organizing runs idempotently
  (multi-membership allowed, secondary conclusions only when real).
- Output booklets export as Markdown AND plain text (UTF-8 BOM).
"""

from __future__ import annotations

import logging
from pathlib import Path

import streamlit as st

from src import __version__
from src.display_labels import (
    BOUNDARY_COUNTEREXAMPLE_FIELD_LABELS,
    CONFIDENCE_LABELS,
    FAMILY_KIND_LABELS,
    METHOD_TRIGGER_FIELD_LABELS,
    OUTCOME_LABELS,
    QUESTION_KIND_LABELS,
    QUESTION_STATUS_LABELS,
    RELATION_LABELS,
    REVISION_KIND_LABELS,
    VERDICT_LABELS,
    WING_KIND_LABELS,
    family_picker_label,
    humanize_provenance_text,
    normalize_reason_tags,
    question_picker_label,
)
from src.learning_ai_draft_service import (
    LearningAIDraftError,
    LearningAIDraftService,
)
from src.learning_workflow_service import (
    WING_KINDS,
    LearningWorkflowError,
    MarkdownCollectionRenderer,
    MasteryService,
    OutputCollectionService,
    PlainTextCollectionRenderer,
    QuestionOrganizationService,
    QuestionService,
    TwoWingsService,
    absolute_language_risk,
    conclusion_condition_risk,
    question_form_content_changed,
    wing_provenance_label,
)
from src.math_display import render_math_markdown, render_question_math_markdown
from src.math_formatting_service import display_field, schedule_math_formatting, split_math_tags
from src.question_content_ui import render_question_content, render_region_images
from src.question_visual_regions import crop_region, normalize_regions
from src.runtime import (
    application_database,
    application_question_source_retrieval_service,
    application_targeted_training_service,
    application_training_profile_service,
    application_training_session_service,
)
from src.targeted_training_ui import render_targeted_training_section
from src.text_utils import ui_plaintext_digest
from src.time_display import beijing_now, format_beijing_time, to_beijing_time
from src.training_profile_ui import render_training_profile_page
from src.workspace_ui import (
    _PROJECT_ROOT,
    empty_panel,
    render_workspace,
    section_heading,
)

LOGGER = logging.getLogger(__name__)

st.set_page_config(
    page_title=f"学习整理 · Nectivon v{__version__}", page_icon="📚", layout="wide"
)
render_workspace("pages/18_学习整理.py")
st.title("学习整理")
st.caption("把看过的资料变成能复习的学习资产：整理单题、归纳方法、跟踪掌握、生成输出。")

try:
    database = application_database()
    question_service = QuestionService(database)
    organization = QuestionOrganizationService(database)
    mastery = MasteryService(database)
    output_service = OutputCollectionService(database)
    wings_service = TwoWingsService(database)
except Exception as exc:
    LOGGER.exception("学习整理服务初始化失败")
    st.error(f"学习整理服务初始化失败：{exc}")
    st.stop()

section_heading("总览", "所有数字来自同一个学习资产库")


def _safe_count(query: str, parameters: tuple = ()) -> int:
    """Count rows through the shared database; failures degrade to zero."""

    try:
        with database._connection() as connection:
            return int(connection.execute(query, parameters).fetchone()[0])
    except Exception:
        LOGGER.exception("学习整理统计失败")
        return 0


columns = st.columns(4)
columns[0].metric("整理题", _safe_count("SELECT COUNT(*) FROM question_items"))
columns[1].metric(
    "题型/方法/结论族",
    _safe_count(
        "SELECT COUNT(*) FROM question_families f "
        "WHERE f.status != 'retired' AND EXISTS ("
        "SELECT 1 FROM question_family_members m WHERE m.family_id = f.id)"
    ),
)
columns[2].metric("掌握练习记录", _safe_count("SELECT COUNT(*) FROM mastery_evidence"))
columns[3].metric("导出文档", _safe_count("SELECT COUNT(*) FROM output_collections"))

entry_columns = st.columns(3)
with entry_columns[0]:
    st.markdown("**第一步 · 待核对**")
    st.caption("对照原始页面检查识别文字，并把值得保留的题目加入学习整理。")
    pending_count = _safe_count(
        "SELECT COUNT(*) FROM pages WHERE review_status = 'pending'"
    )
    st.metric("待复核页", pending_count)
    if st.button("进入待核对", use_container_width=True, key="go_pending"):
        st.switch_page(st.Page(_PROJECT_ROOT / "pages/5_待整理页面.py"))
with entry_columns[1]:
    st.markdown("**已整理的题目**")
    st.caption("错题、好题、典型题、方法题都保存在同一个整理库里。")
    for kind, label in QUESTION_KIND_LABELS.items():
        count = _safe_count(
            "SELECT COUNT(*) FROM question_items WHERE question_kind = ?", (kind,)
        )
        st.caption(f"{label}：{count} 道")
with entry_columns[2]:
    st.markdown("**归纳与掌握**")
    st.caption("同一类题、同一方法、同一结论会自动归入同一个族。")
    for kind, label in FAMILY_KIND_LABELS.items():
        count = _safe_count(
            "SELECT COUNT(*) FROM question_families f "
            "WHERE f.family_kind = ? AND f.status != 'retired' AND EXISTS ("
            "SELECT 1 FROM question_family_members m WHERE m.family_id = f.id)",
            (kind,),
        )
        st.caption(f"{label}：{count} 个")
    st.caption(f"待复习的族：{_safe_count('SELECT COUNT(*) FROM mastery_profiles')} 个")

st.divider()

if _safe_count("SELECT COUNT(*) FROM question_items") == 0:
    empty_panel(
        "还没有整理过的题目",
        "导入资料后题目不会自动出现——需要逐页拆出：打开左侧「待核对」，"
        "翻到有题目的页面，在「加入学习整理」区点「AI 拆分本页题目候选」，"
        "核对每道题的题干后逐题加入。纯文字资料也可以直接核对识别文字手动整理。",
    )


def _split_tags(text: str) -> list[str]:
    return split_math_tags(text)


_COLLECTION_KIND_LABELS = {
    "error_book": "错题本",
    "good_book": "好题本",
    "review_pack": "复习包",
    "topic_pack": "专题包",
    "conclusion_handbook": "二级结论手册",
    "weakness_report": "易错报告",
}


def _collection_item_label(item: dict) -> str:
    """Human label for one collection item (no internal ids)."""

    layer = str(item.get("item_layer"))
    item_id = int(item.get("item_id"))
    if layer == "question":
        try:
            return question_picker_label(question_service.get_question_item(item_id))
        except LearningWorkflowError:
            return "（题目已不存在）"
    if layer == "family":
        try:
            return family_picker_label(organization.get_family(item_id))
        except LearningWorkflowError:
            return "（族已不存在）"
    if layer == "mastery":
        return "掌握概览"
    return "（未知条目）"


def _ai_service() -> LearningAIDraftService | None:
    """Build the AI draft service, or None when AI is not configured."""

    try:
        from src.runtime import application_ai_provider

        provider = application_ai_provider()
    except Exception:  # noqa: BLE001 - AI is an optional enhancement
        LOGGER.debug("读取 AI provider 失败", exc_info=True)
        return None
    if provider is None:
        return None
    return LearningAIDraftService(provider)


def _question_selectbox(label: str, key: str, *, question_id: int | None = None):
    """Shared question picker; returns the selected QuestionItem or None."""

    questions = question_service.list_question_items()
    if not questions:
        st.caption("还没有整理过的题目。")
        return None
    ids = [question.id for question in questions]
    default = ids.index(question_id) if question_id in ids else 0
    selected_id = st.selectbox(
        label,
        options=ids,
        index=default,
        format_func=lambda value: question_picker_label(
            next(q for q in questions if q.id == value)
        ),
        key=key,
    )
    return question_service.get_question_item(selected_id)


library_tab, family_tab, mastery_tab, trigger_tab, boundary_tab, output_tab, profile_tab = st.tabs(
    [
        "题目库（第一层）",
        "归纳族（第二层）",
        "掌握训练（第三层）",
        "方法触发",
        "边界反例",
        "导出",
        "训练配置",
    ]
)

with profile_tab:
    render_training_profile_page(application_training_profile_service())


def _wing_field_label(wing_kind: str, field: str) -> str:
    label_map = (
        METHOD_TRIGGER_FIELD_LABELS
        if wing_kind == "method_trigger"
        else BOUNDARY_COUNTEREXAMPLE_FIELD_LABELS
    )
    return label_map.get(field, field)


def _render_wing_editor(
    *,
    wing_kind: str,
    question_id: int | None = None,
    family_id: int | None = None,
) -> None:
    """Shared two-wings read/write editor for a question or family target."""

    key_prefix = f"q{question_id}" if question_id is not None else f"f{family_id}"
    try:
        entries = wings_service.list_entries_for_question(
            question_id, wing_kind=wing_kind
        ) if question_id is not None else wings_service.list_entries_for_family(
            family_id, wing_kind=wing_kind
        )
    except LearningWorkflowError as exc:
        st.error(str(exc))
        return
    from src.learning_workflow_service import (
        BOUNDARY_COUNTEREXAMPLE_FIELDS,
        METHOD_TRIGGER_FIELDS,
    )

    field_names = (
        METHOD_TRIGGER_FIELDS if wing_kind == "method_trigger" else BOUNDARY_COUNTEREXAMPLE_FIELDS
    )
    wing_name = WING_KIND_LABELS[wing_kind]

    if not entries:
        st.caption("这一部分还没有内容（允许为空，证据不足时不强行生成）。")
    for entry in entries:
        # G2-B §39/§77: provenance must be visible up front — the student
        # can tell "来自你的资料" (evidence-bound) from "AI 补充，建议核对"
        # without opening advanced metadata.
        provenance = wing_provenance_label(entry.origin, entry.confidence, entry.status)
        if entry.evidence_item_id is not None:
            provenance = "来自你的资料（已绑定原始证据） · " + provenance
        state_label = "草稿（待确认）" if entry.status == "draft" else "已确认"
        with st.expander(f"{provenance} · {state_label}"):
            if wing_kind == "boundary_counterexample":
                joined = "\n".join(
                    str(value)
                    for value in entry.fields.values()
                    if value
                )
                risk_hits = absolute_language_risk(joined)
                if risk_hits:
                    st.warning(
                        "注意绝对化表述："
                        + "、".join(f"「{hit}」" for hit in risk_hits)
                        + "。规律一般都有前提（区域/尺度/季节），建议改成『一般/通常』并写明前提。"
                    )
            current_fields = {}
            for field in field_names:
                value = entry.fields.get(field, "")
                if isinstance(value, list):
                    shown = "\n".join(str(item) for item in value)
                else:
                    shown = "" if value is None else str(value)
                current_fields[field] = st.text_area(
                    _wing_field_label(wing_kind, field),
                    value=shown,
                    height=60,
                    key=f"wing_{entry.id}_{field}",
                )
            edit_columns = st.columns(2)
            if edit_columns[0].button("保存修改（转为我的确认内容）", key=f"wing_save_{entry.id}"):
                try:
                    payload = {}
                    for field in field_names:
                        if isinstance(entry.fields.get(field), list):
                            payload[field] = [
                                line.strip()
                                for line in current_fields[field].splitlines()
                                if line.strip()
                            ]
                        else:
                            payload[field] = current_fields[field].strip()
                    wings_service.update_entry(entry.id, **payload)
                    st.success("已保存为你的确认内容。")
                    st.rerun()
                except LearningWorkflowError as exc:
                    st.error(f"保存失败：{exc}")
            if entry.status != "confirmed" and edit_columns[1].button(
                "不改了，直接确认这条内容", key=f"wing_confirm_{entry.id}"
            ):
                try:
                    wings_service.confirm_entry(entry.id)
                    st.success("已确认。")
                    st.rerun()
                except LearningWorkflowError as exc:
                    st.error(f"确认失败：{exc}")
            with st.expander("修订历史", expanded=False):
                history = wings_service.entry_history(entry.id)
                if not history:
                    st.caption("暂无修订记录。")
                for record in history:
                    st.caption(
                        f"{format_beijing_time(record.get('created_at'))} · "
                        f"{record.get('revision_kind', '')}"
                        + (f" · {record.get('note')}" if record.get("note") else "")
                    )
    _WING_LIST_FIELD_NAMES = frozenset(
        {
            "trigger_conditions",
            "recognition_signals",
            "applicability_prerequisites",
            "validity_conditions",
            "invalidation_conditions",
            "boundary_cases",
            "counterexamples",
            "common_misuses",
            "confusing_conclusions",
        }
    )
    # §58: the blank manual form no longer sits permanently expanded under
    # the AI draft — the main flow is AI draft → user review, so manual
    # creation is a collapsed secondary entry.
    created = False
    with st.expander(f"+ 手动新增一条「{wing_name}」"):
        with st.form(f"wing_create_form_{key_prefix}_{wing_kind}", clear_on_submit=True):
            st.markdown(f"**新建「{wing_name}」内容**")
            new_fields = {}
            for field in field_names:
                new_fields[field] = st.text_area(
                    _wing_field_label(wing_kind, field),
                    value="",
                    height=50,
                    key=f"wing_new_{key_prefix}_{wing_kind}_{field}",
                )
            created = st.form_submit_button("新建（我的内容）")
    if created:
        try:
            payload = {
                field: (
                    [line.strip() for line in new_fields[field].splitlines() if line.strip()]
                    if field in _WING_LIST_FIELD_NAMES
                    else new_fields[field].strip()
                )
                for field in field_names
            }
            wings_service.create_entry(
                wing_kind=wing_kind,
                target_layer="question" if question_id is not None else "family",
                question_id=question_id,
                family_id=family_id,
                origin="user",
                confidence="confirmed",
                **payload,
            )
            st.success("已新建。")
            st.rerun()
        except LearningWorkflowError as exc:
            st.error(f"新建失败：{exc}")


def _render_question_wing_ai(question, wing_kind: str) -> None:
    """AI wing draft generation for one question (left/right wing tabs)."""

    key_prefix = f"q{question.id}_{wing_kind}"
    if st.button(f"AI 生成{WING_KIND_LABELS[wing_kind]}草稿", key=f"wing_ai_{key_prefix}"):
        ai = _ai_service()
        if ai is None:
            st.warning("AI 服务未配置：可以手动填写下面的新建表单。")
        else:
            try:
                with st.spinner("正在生成两翼草稿……"):
                    fields = ai.generate_wing_draft(question, wing_kind)
            except LearningAIDraftError as exc:
                st.error(f"草稿生成失败：{exc}")
            except Exception as exc:  # noqa: BLE001 - user-facing failure
                LOGGER.exception("AI 两翼草稿失败")
                st.error(f"草稿生成失败：{exc}")
            else:
                try:
                    wings_service.create_entry(
                        wing_kind=wing_kind,
                        target_layer="question",
                        question_id=question.id,
                        origin="ai_draft",
                        confidence="uncertain",
                        **fields,
                    )
                except LearningWorkflowError as exc:
                    st.error(f"草稿保存失败：{exc}")
                else:
                    st.success("草稿已生成（AI 草稿，待你确认）。请核对后保存或确认。")
                    st.rerun()

# ================================================================== 第一层
def _source_context_line(question) -> str:
    if question.source_available:
        title_label = question.source_document_title_snapshot or "（未记录资料标题）"
        page_label = question.source_page_label_snapshot or ""
        return f"来源：{title_label}{' · ' + page_label if page_label else ''}"
    if question.source_document_title_snapshot or question.source_page_label_snapshot:
        return (
            "来源不可用（原始资料已删除）——原为："
            f"{question.source_document_title_snapshot or '（未记录资料标题）'}"
            f" {question.source_page_label_snapshot}".rstrip()
            + "；原始证据已随资料删除。"
        )
    return "来源不可用（没有记录这道题的来源，无法追溯原文）。"


def _apply_ai_drafts_to_widgets(question, drafts: dict) -> None:
    """Pre-populate the edit widgets with AI drafts (DB untouched until save).

    Priority: USER_CONFIRMED > USER_EDITED > AI_DRAFT > EMPTY.  A field the
    user already edited or confirmed is never touched; AI drafts only fill
    empty (or still-draft) fields so the user reviews everything in the
    visible widgets before saving.
    """

    key_prefix = f"q{question.id}"
    if drafts.get("stem") and (
        not question.stem_text.strip()
        or (not question.user_edited and question.stem_confidence != "confirmed")
    ):
        st.session_state[f"edit_stem_{key_prefix}"] = drafts["stem"]
    if drafts.get("student_answer") and not question.student_answer.strip():
        st.session_state[f"edit_answer_{key_prefix}"] = drafts["student_answer"]
    if drafts.get("verdict") and drafts["verdict"] != "none" and not question.teacher_verdict:
        st.session_state[f"edit_verdict_{key_prefix}"] = drafts["verdict"]
    if drafts.get("correction") and not question.correction_note.strip():
        st.session_state[f"edit_correction_{key_prefix}"] = drafts["correction"]
    if drafts.get("analysis") and not question.analysis_note.strip():
        st.session_state[f"edit_analysis_{key_prefix}"] = drafts["analysis"]
    if drafts.get("reason_tags") and not question.reason_tags:
        st.session_state[f"edit_reason_{key_prefix}"] = "、".join(
            normalize_reason_tags(drafts["reason_tags"])
        )
    if drafts.get("method_tags") and not question.method_tags:
        st.session_state[f"edit_type_{key_prefix}"] = "、".join(drafts["method_tags"])
    if drafts.get("solution_method") and not question.solution_method.strip():
        st.session_state[f"edit_solution_method_{key_prefix}"] = drafts["solution_method"]


def _question_visual_source(question) -> tuple[Path | None, list[dict]]:
    """Resolve local source pixels and region references stored with this item."""

    draft = question.ai_draft if isinstance(question.ai_draft, dict) else {}
    material = draft.get("visual_material", {})
    if not isinstance(material, dict) or material.get("dependency") == "none":
        return None, []
    if material.get("dependency") not in ("required", "uncertain") and not material.get("regions"):
        return None, []
    if not question.source_available or question.page_id is None:
        return None, []
    try:
        page = application_database().get_page(int(question.page_id))
    except Exception:  # noqa: BLE001 - a missing source must not hide saved text
        LOGGER.exception("读取题目原图失败")
        return None, []
    path = Path(page.image_path) if page is not None else None
    regions = [r for r in normalize_regions(material.get("regions", []))
               if r.get("page_id", question.page_id) == question.page_id]
    from src.question_content_ui import question_regions

    regions = question_regions(path, question.question_number, regions, page_id=question.page_id)
    return path, regions


def _render_saved_question_content(question) -> bool:
    """Keep per-option images visible in every full-question learning view."""

    path, regions = _question_visual_source(question)
    original = (question.ai_draft or {}).get("image_recognized_original", {})
    if (original.get("human_stem_preserved") and original.get("stem")
            and original["stem"] != question.stem_text):
        with st.expander("本次 AI 读图原题（人工修订已保留）"):
            render_question_content(original["stem"], image_path=path, regions=regions)
    # The read/teach-back view already displayed shared crops with the parent
    # conditions. Count those successful crops so a full-page fallback is not
    # repeated below the child's stem.
    shown = bool(question.shared_context.strip()) and any(
        crop_region(path, region) is not None
        for region in regions if region["role"] == "shared" and path is not None
    )
    if not question.shared_context.strip():
        shown = render_region_images(path, [r for r in regions if r["role"] == "shared"])
    return render_question_content(
        display_field(question, "stem_text"), image_path=path, regions=regions,
    ) or shown


def _render_visual_material_section(
    question, key_prefix: str, *, show_full_page: bool = True,
) -> None:
    """G2-A-02/03: the visual material is PART of the question, not an attachment.

    When the question declares a visual dependency, the page image (the one
    and only RAW asset — referenced, never copied) renders directly in the
    main reading flow between 题干 and 我的作答.  Binding provenance stays
    honest: AI association is a draft until the user confirms it here.
    """

    draft = question.ai_draft if isinstance(question.ai_draft, dict) else {}
    material = draft.get("visual_material")
    if not isinstance(material, dict):
        return
    dependency = str(material.get("dependency") or "uncertain")
    if dependency == "none":
        return
    if not question.source_available or question.page_id is None:
        st.caption(
            "关联材料：原页面已随资料删除；这道题的文字内容仍属于你，可继续整理。"
        )
        return
    try:
        page = application_database().get_page(int(question.page_id))
    except Exception:  # noqa: BLE001 - auxiliary section must never kill layer 1
        page = None
    image_path = getattr(page, "image_path", None)
    if page is None or image_path is None or not Path(image_path).exists():
        st.warning("关联材料：本页图像文件缺失，无法显示原图；题目文字不受影响。")
        return
    notes = str(material.get("material_notes") or "").strip()
    confirmed = bool(material.get("binding_confirmed"))
    st.markdown("**关联材料**" + (f"　{notes}" if notes else ""))
    from src.question_image_editor import render_question_crop_editor, render_question_image_editor

    _, regions = _question_visual_source(question)

    render_question_image_editor(
        image_path, page_id=page.id, number=question.question_number, regions=regions,
        key=f"learning_image_{key_prefix}",
    )
    render_question_crop_editor(
        image_path, page_id=page.id, number=question.question_number, regions=regions,
        key=f"learning_crop_{key_prefix}",
    )
    if st.button(
        "定位原页",
        key=f"vm_locate_{key_prefix}",
        help="跳到「我的资料」的这一页，对照原始版面。",
    ):
        # Same F6 P0 fix as the group source-page buttons: navigation
        # params MUST go through switch_page(query_params=...), otherwise
        # the reader falls back to the most-recent document.
        st.switch_page(
            "pages/17_我的资料.py",
            query_params={
                "document": str(question.document_id),
                "page": str(getattr(page, "page_number", 1) or 1),
            },
        )
    if confirmed:
        st.caption(
            "✓ 你已确认：这就是解答本题所需的图表（"
            + str(material.get("binding_provenance") or "USER CONFIRMED BINDING")
            + "）"
        )
    else:
        st.caption(
            "图表关联待核对："
            + str(
                material.get("binding_provenance")
                or "AI BINDING DRAFT（AI 关联建议，未经你确认）"
            )
        )
        confirm_col, none_col = st.columns(2)
        if confirm_col.button(
            "✓ 确认关联（这就是本题的图表）",
            key=f"vm_confirm_{key_prefix}",
            type="primary",
        ):
            updated = dict(material)
            updated["binding_confirmed"] = True
            updated["binding_provenance"] = "USER CONFIRMED BINDING"
            question_service.update_visual_material(question.id, updated)
            st.toast("已确认图表关联（记录为你的确认）。")
            st.rerun()
        if none_col.button("本题无需图表", key=f"vm_none_{key_prefix}"):
            updated = dict(material)
            updated["dependency"] = "none"
            updated["binding_provenance"] = "USER OVERRIDE（你标注本题无需图表）"
            question_service.update_visual_material(question.id, updated)
            st.toast("已按你的标注关闭本题的关联材料显示。")
            st.rerun()


def _render_question_read_view(question, key_prefix: str, edit_flag_key: str) -> None:
    """READ/VERIFY 态（R3 P0-A）：先看内容、核对状态，点「修改」才进编辑。

    数学内容用统一读态渲染器排版；没有作答就老实说「未记录作答」，
    绝不显示任何编造的学生行为。删除/来源细节收进「更多」，保持
    阅读卡干净，但危险操作不藏没。
    """

    structure = (
        question.ai_draft.get("question_structure", {})
        if isinstance(question.ai_draft, dict)
        else {}
    )
    ancestors = structure.get("ancestor_labels", [])
    parent_label = str(ancestors[-1]).strip() if ancestors else ""
    if parent_label:
        st.info(
            f"这是大题 {parent_label} 的独立小题 {question.question_number}。"
            f"错因、知识点和掌握记录只属于 {question.question_number}。"
        )
    if question.shared_context.strip():
        with st.expander("本小题需要的父题公共条件", expanded=True):
            render_question_math_markdown(question.shared_context)
            source_path, regions = _question_visual_source(question)
            render_region_images(source_path, [r for r in regions if r["role"] == "shared"])
            if question.shared_image_refs:
                st.caption("共享图像：" + "、".join(question.shared_image_refs))
            # Recursive split already carries these inherited provenance
            # references on every leaf.  They are part of the shared context,
            # so surface them here instead of silently keeping them only in
            # the database.
            if question.shared_page_refs:
                page_labels: list[str] = []
                for page_id in question.shared_page_refs:
                    try:
                        shared_page = application_database().get_page(int(page_id))
                        shared_document = (
                            application_database().get_document(shared_page.document_id)
                            if shared_page is not None
                            else None
                        )
                    except Exception:  # noqa: BLE001 - provenance display degrades
                        shared_page = None
                        shared_document = None
                    if shared_page is not None:
                        title = getattr(shared_document, "title", None) or "原题资料"
                        page_labels.append(
                            f"{title} · 第 {int(shared_page.page_number)} 页"
                        )
                if page_labels:
                    st.caption("原题来源：" + "、".join(dict.fromkeys(page_labels)))
            if question.shared_answer_refs:
                st.caption(
                    "正式答案来源："
                    + "、".join(dict.fromkeys(question.shared_answer_refs))
                )
    st.markdown("**题干**")
    cropped = False
    if question.stem_text.strip():
        cropped = _render_saved_question_content(question)
    else:
        st.caption("（题干待补充——点「修改」补上，或在题目库上方先重新拆分。）")
    _render_visual_material_section(question, key_prefix, show_full_page=not cropped)
    reference = (question.ai_draft or {}).get("learning_reference")
    if isinstance(reference, dict) and reference.get("status") == "pending_review":
        st.caption("订正、解析、题型和方法：AI 参考版，待你核对；可点击「修改」二次修订。")
    st.markdown("**我的作答**")
    if question.student_answer.strip():
        render_question_math_markdown(display_field(question, "student_answer"))
    else:
        st.caption("未记录作答。可以点「修改」补写，或到「待核对」页核对原始页面。")
    verdict_label = VERDICT_LABELS.get(question.teacher_verdict or "none", "未判定")
    st.markdown(f"**判定**：{verdict_label}")
    if question.correction_note.strip():
        st.markdown("**订正**")
        render_question_math_markdown(display_field(question, "correction_note"))
    if question.analysis_note.strip():
        st.markdown("**解析**")
        render_question_math_markdown(display_field(question, "analysis_note"))
    if question.reason_tags:
        st.markdown("**错因**")
        render_question_math_markdown(display_field(question, "reason_tags"))
    if question.method_tags:
        st.markdown("**题型**")
        render_question_math_markdown(display_field(question, "method_tags"))
    if question.solution_method.strip():
        st.markdown("**方法**")
        render_question_math_markdown(display_field(question, "solution_method"))
    if not (question.reason_tags or question.method_tags or question.solution_method):
        st.caption("还没有错因、题型和方法（点「修改」补充）。")
    if st.button("修改", key=f"start_edit_{key_prefix}", type="primary"):
        st.session_state[edit_flag_key] = True
        st.rerun()

    with st.expander("更多（原始来源内容 · 来源与追溯 · 删除这道题）"):
        draft_is_source = isinstance(question.ai_draft, dict) and (
            question.ai_draft.get("kind") == "source_context"
        )
        if draft_is_source:
            provenance = humanize_provenance_text(
                str(question.ai_draft.get("provenance") or "（未记录来源）")
            )
            content = str(question.ai_draft.get("content") or "")
            if content:
                st.markdown(f"**原始来源内容**（来自：{provenance}，不可改动）")
                render_math_markdown(content)
        st.caption(_source_context_line(question))
        if question.shared_answer_refs:
            st.caption(
                "正式答案来源："
                + "、".join(dict.fromkeys(question.shared_answer_refs))
            )
        if not question.source_available:
            st.caption(
                "原始页面与证据已随资料删除；题目内容（题干/作答/批注/订正）"
                "仍然属于你，可继续整理与复习。"
            )
        st.divider()
        # V086-R1 FIX-3: a joined/wrong question must be removable.  The delete
        # only removes THIS library entry — original page images and source
        # documents are never touched.  (R3 P0-A: moved into「更多」as a
        # secondary dangerous action — still reachable, never hidden.)
        st.markdown("**删除这道题（从整理库移除）**")
        st.caption(
            "只删除题目库里的这一条整理记录；原始页面图片与资料不受影响，"
            "也不会删除同页的其它题目。"
        )
        delete_confirmed = st.checkbox(
            "我确认要删除这道题（不可从界面恢复）",
            key=f"delete_confirm_{key_prefix}",
        )
        if st.button(
            "删除这道题",
            key=f"delete_question_{key_prefix}",
            disabled=not delete_confirmed,
        ):
            try:
                question_service.delete_question_item(question.id)
            except LearningWorkflowError as exc:
                st.error(f"删除失败：{exc}")
            else:
                st.session_state["library_flash"] = (
                    f"已删除一道题（{dict(QUESTION_KIND_LABELS)[question.question_kind]}）。"
                    "原始页面不受影响。"
                )
                st.rerun()


def _render_question_card(question) -> None:
    """One question = READ/VERIFY first; 修改 switches to the edit form."""

    status_label = QUESTION_STATUS_LABELS.get(question.status, question.status)
    confidence_label = CONFIDENCE_LABELS.get(
        question.stem_confidence, question.stem_confidence
    )
    st.markdown(
        f"**{question_picker_label(question)}**　状态：{status_label}"
        f"　题干把握：{confidence_label}"
        f"　{'已人工修订' if question.user_edited else '未人工修订'}"
    )
    st.caption(_source_context_line(question))
    families_of_question = organization.list_families_for_question(question.id)
    if families_of_question:
        st.caption(
            "被归入："
            + "；".join(family_picker_label(f) for f in families_of_question)
        )
    else:
        st.caption("还没有被归入任何族（保存后系统会自动归纳，也可在「归纳族」页手动处理）。")

    key_prefix = f"q{question.id}"
    edit_flag_key = f"edit_mode_{key_prefix}"
    if not st.session_state.get(edit_flag_key):
        _render_question_read_view(question, key_prefix, edit_flag_key)
        return
    if st.button("返回阅读（不保存本次修改）", key=f"cancel_edit_{key_prefix}"):
        st.session_state.pop(edit_flag_key, None)
        st.rerun()
    reference = (question.ai_draft or {}).get("learning_reference")
    if isinstance(reference, dict):
        st.caption("订正、解析、题型和方法已提供 AI 参考版，可在下方修改后保存。")

    with st.form("question_edit_form", clear_on_submit=False):
        if question.shared_context.strip():
            st.caption(
                "这道叶子小问已关联父题共享上下文；修改题干不会覆盖父题。"
            )
        st.markdown("**① 题干**")
        stem_text = st.text_area(
            "题干", value=question.stem_text, height=120, key=f"edit_stem_{key_prefix}"
        )
        st.markdown("**② 我的作答**")
        student_answer = st.text_area(
            "你的作答",
            value=question.student_answer,
            height=90,
            key=f"edit_answer_{key_prefix}",
            help="这里只放你自己手写/输入的作答。"
            "AI 的推导和图表解析不会自动进入这里；"
            "如果上方视觉草稿被你删除，这里的 AI 转写内容会一并清空。"
            "原始页面图像始终保留。",
        )
        st.markdown("**③ 判定**")
        verdict = st.selectbox(
            "判定结果",
            options=["correct", "incorrect", "uncertain", "none"],
            format_func=lambda v: VERDICT_LABELS[v],
            index=["correct", "incorrect", "uncertain", "none"].index(
                question.teacher_verdict or "none"
            ),
            key=f"edit_verdict_{key_prefix}",
        )
        st.markdown("**④ 订正**")
        # Streamlit forms intentionally do not rerun when a selectbox changes.
        # Rendering this editor conditionally therefore made it appear only
        # after saving once when the user changed 未判定 → 错误.  Keep it
        # visible in every verdict state so the six-step form works in one
        # natural pass; non-error content is preserved as a reference note.
        correction_note = st.text_area(
            "订正（简要写出正确结果与关键步骤）",
            value=question.correction_note,
            height=110,
            key=f"edit_correction_{key_prefix}",
            help=(
                "做错时填写订正；典型题/好题也可保存经你核对的参考解析。"
                "AI 生成时没有联网核验，请对照原始答案确认。"
            ),
        )
        if verdict != "incorrect":
            st.caption("当前不是「错误」判定；这里的内容会作为参考解析保留，不会冒充你的作答。")
        st.markdown("**⑤ 解析**")
        analysis_note = st.text_area(
            "解析",
            value=question.analysis_note,
            height=180,
            key=f"edit_analysis_{key_prefix}",
            help="解释为什么这样做、每一步如何得到；有歧义就写明待老师确认，不猜答案。",
        )
        st.markdown("**⑥ 错因**")
        reason_tags = st.text_input(
            "错因标签（用 、 或 , 分隔）",
            value="、".join(question.reason_tags),
            key=f"edit_reason_{key_prefix}",
            help="禁止只写「粗心/马虎/计算错误」这类词；要落到具体根因，"
            "例如：找不到二阶差分规律、不懂得画树状图逆推。"
            "不能把不会说成漏看，也不要用笼统的知识点掌握不牢代替具体原因。",
        )
        st.markdown("**⑦ 题型**")
        method_tags = st.text_input(
            "题型标签（用 、 或 , 分隔，可以有几个）",
            value="、".join(question.method_tags),
            key=f"edit_type_{key_prefix}",
            help="描述题目属于哪一类；不要在这里写解题步骤。",
        )
        st.markdown("**⑧ 方法**")
        solution_method = st.text_area(
            "方法（写明可操作的解题思路）",
            value=question.solution_method,
            height=100,
            key=f"edit_solution_method_{key_prefix}",
            help="写下一次遇到同类题能照着做的步骤；与题型分开保存。",
        )
        status = st.selectbox(
            "状态",
            options=["draft", "organized", "archived"],
            format_func=lambda v: QUESTION_STATUS_LABELS[v],
            index=["draft", "organized", "archived"].index(question.status),
            key=f"edit_status_{key_prefix}",
        )
        submitted = st.form_submit_button("保存", type="primary")

    if submitted:
        try:
            normalized_reasons = normalize_reason_tags(_split_tags(reason_tags))
            # V086-R1 FIX-1: user_edited must mean "the user actually changed
            # content", not "a save happened".  A no-change submit keeps the
            # row's provenance untouched (AI-derived bytes stay AI-derived
            # and the「已人工修订」badge cannot appear from a blank save).
            verdict_value = None if verdict == "none" else verdict
            content_changed = question_form_content_changed(
                question,
                stem_text=stem_text,
                student_answer=student_answer,
                teacher_verdict=verdict_value,
                correction_note=correction_note,
                analysis_note=analysis_note,
                reason_tags=normalized_reasons,
                method_tags=_split_tags(method_tags),
                solution_method=solution_method,
                status=status,
            )
            question_service.update_question_item(
                question.id,
                stem_text=stem_text,
                student_answer=student_answer,
                teacher_verdict=verdict_value,
                teacher_comment=question.teacher_comment,
                correction_note=correction_note,
                analysis_note=analysis_note,
                reason_tags=normalized_reasons,
                method_tags=_split_tags(method_tags),
                solution_method=solution_method,
                status=status,
                user_edited=content_changed,
            )
        except LearningWorkflowError as exc:
            st.error(f"保存失败：{exc}")
        else:
            math_queued = (
                schedule_math_formatting(database.database_path, question.id, _ai_service())
                if content_changed else False
            )
            suggestions = st.session_state.pop(f"ai_suggestions_{key_prefix}", None)
            try:
                outcome = organization.organize_with_confidence(
                    question.id,
                    type_family=(suggestions or {}).get("type_family"),
                    method_families=(suggestions or {}).get("method_families"),
                    secondary_conclusion=(suggestions or {}).get("secondary_conclusion"),
                )
            except LearningWorkflowError as exc:
                LOGGER.warning("自动归纳失败：%s", exc)
                outcome = None
            # G2-B §7-§11: three-tier student-language feedback.  High
            # confidence is applied with its reason and stays revocable;
            # medium/low become a "待你核对" queue instead of silent writes.
            message = (
                "已保存。AI 正在后台检查数学排版；原文不变。"
                if math_queued else "已保存。数学符号已按本地规则排版；原文不变。"
            )
            if outcome:
                parts: list[str] = []
                if outcome["auto_labels"]:
                    parts.append(
                        "已自动归纳到：" + "、".join(outcome["auto_labels"]) + "。"
                        "可以在「归纳」页移出不合适的。"
                    )
                if outcome["recommended"]:
                    parts.append(
                        "这道题可能属于几个已有分类，请到「归纳 → 待你核对」确认。"
                    )
                if outcome["new_drafts"]:
                    parts.append(
                        "暂未找到合适的已有分类，AI 建议了新分类，"
                        "请到「归纳 → 待你核对」核对。"
                    )
                if parts:
                    message += "".join(parts)
            st.session_state["library_flash"] = message
            st.session_state.pop(edit_flag_key, None)  # 保存成功 → 回到 READ/VERIFY
            st.rerun()

    flash = st.session_state.pop("library_flash", None)
    if flash:
        st.success(flash)

    # V086-R1 FIX-3: a joined/wrong question must be removable.  The delete
    # only removes THIS library entry — original page images and source
    # documents are never touched.
    st.divider()
    st.markdown("**删除这道题（从整理库移除）**")
    st.caption(
        "只删除题目库里的这一条整理记录；原始页面图片与资料不受影响，"
        "也不会删除同页的其它题目。"
    )
    delete_confirmed = st.checkbox(
        "我确认要删除这道题（不可从界面恢复）",
        key=f"delete_confirm_{key_prefix}",
    )
    if st.button(
        "删除这道题",
        key=f"delete_question_{key_prefix}",
        disabled=not delete_confirmed,
    ):
        try:
            question_service.delete_question_item(question.id)
        except LearningWorkflowError as exc:
            st.error(f"删除失败：{exc}")
        else:
            st.session_state["library_flash"] = (
                f"已删除一道题（{dict(QUESTION_KIND_LABELS)[question.question_kind]}）。"
                "原始页面不受影响。"
            )
            st.rerun()


with library_tab:
    st.subheader("题目库")
    reference_flash = st.session_state.pop("learning_reference_flash", None)
    if reference_flash:
        st.info(reference_flash)
    filter_column, search_column = st.columns([1, 2])
    kind_choice = filter_column.selectbox(
        "按类型过滤",
        options=["all", "error", "good", "typical", "method"],
        format_func=lambda value: "全部" if value == "all" else QUESTION_KIND_LABELS[value],
        key="library_kind_filter",
    )
    search_term = search_column.text_input("搜索题干", key="library_search", value="")
    try:
        if search_term.strip():
            questions = question_service.search_questions(search_term.strip())
        else:
            questions = question_service.list_question_items(
                question_kind=None if kind_choice == "all" else kind_choice
            )
    except LearningWorkflowError as exc:
        st.error(str(exc))
        questions = []
    if not questions:
        st.caption("没有符合条件的题目。")
    else:
        selected = st.selectbox(
            "选择一道题查看/编辑",
            options=questions,
            format_func=question_picker_label,
            key="library_question_picker",
        )
        question = question_service.get_question_item(selected.id)
        _render_question_card(question)

    # ---------------- G3-B P0-1: comprehensive question group view ---------
    try:
        from src.question_group_service import (
            QuestionGroupError,
            confirm_question_evidence,
            dissolve_group,
            group_children,
            groups_for_document,
            question_evidence,
            resolve_group_source_pages,
            set_question_evidence,
        )

        all_documents = application_database().list_documents()
        group_docs = [
            doc for doc in all_documents
            if groups_for_document(application_database(), int(doc.id))
        ]
        if group_docs:
            st.divider()
            st.subheader("综合题组")
            group_doc_labels = {
                int(doc.id): doc.title for doc in group_docs
            }
            group_doc_choice = st.selectbox(
                "选择资料（含题组）",
                options=list(group_doc_labels),
                format_func=lambda did: group_doc_labels.get(did, str(did)),
                key="group_doc_picker",
            )
            doc_groups = groups_for_document(
                application_database(), int(group_doc_choice)
            )

            def _group_kind_label(group_row: dict) -> str:
                kind = str(group_row.get("group_type") or "comprehensive")
                return {
                    "choice": "选择题组",
                    "comprehensive": "综合题",
                    "other": "题组",
                }.get(kind, "题组")

            group_labels = {
                int(g["id"]): (
                    f"{_group_kind_label(g)} 第 {g['group_number']}"
                    f"（{g['status']}）"
                )
                for g in doc_groups
            }
            group_choice = st.selectbox(
                "选择题组",
                options=list(group_labels),
                format_func=lambda gid: group_labels.get(gid, str(gid)),
                key="group_picker",
            )
            if group_choice is not None:
                payload = group_children(application_database(), int(group_choice))
                group = payload["group"]
                # F6 P0: source_pages stores global page_ids; the UI must
                # show and jump by document-local page numbers (never leak
                # "第 784 页" internal ids, never jump by page_id).
                resolved_source = resolve_group_source_pages(
                    application_database(), group
                )
                group_pages = resolved_source["resolved"]
                missing_page_ids = resolved_source["missing"]
                group_kind_label = _group_kind_label(group)
                st.markdown(
                    f"**{_group_kind_label(group)} 第 {group['group_number']}**"
                    f"　·　状态：{group['status']}"
                )
                if group_pages:
                    page_bits = [
                        f"第 {item['page_number']} 页" for item in group_pages
                    ]
                    st.markdown(
                        "**来源页：" + " · ".join(page_bits) + "**"
                        + (
                            f"（本题组跨 {len(group_pages)} 页）"
                            if len(group_pages) > 1
                            else ""
                        )
                    )
                    jump_cols = st.columns(len(group_pages))
                    for col, item in zip(
                        jump_cols, group_pages, strict=False
                    ):
                        # F6 P0 fix: pass navigation params through
                        # switch_page(query_params=...) — assigning
                        # st.query_params then calling switch_page() loses
                        # ALL params (Streamlit clears them on navigation),
                        # which made this button land on the wrong document.
                        if col.button(
                            f"📖 回看第 {item['page_number']} 页",
                            key=f"group_src_{group['id']}_{item['page_id']}",
                        ):
                            st.switch_page(
                                "pages/17_我的资料.py",
                                query_params={
                                    "document": str(group["document_id"]),
                                    "page": str(item["page_number"]),
                                },
                            )
                    if missing_page_ids:
                        st.caption(
                            f"⚠ 有 {len(missing_page_ids)} 个来源页已不存在"
                            "（可能已被删除），无法回看。"
                        )
                else:
                    st.caption("来源页信息缺失。")
                    if missing_page_ids:
                        st.caption(
                            f"⚠ 该题组登记的 {len(missing_page_ids)} 个来源页"
                            "已不存在（可能已被删除）。"
                        )
                materials = payload["materials"]
                if materials:
                    st.markdown("**共享材料 / 图（共享原始证据，未被复制）**")
                    # F6-02: never leak internal page_id on the student
                    # surface — show the document-local page number instead.
                    page_number_by_id = {
                        item["page_id"]: item["page_number"]
                        for item in group_pages
                    }
                    kind_labels = {
                        "text_material": "文字材料",
                        "figure": "图",
                    }
                    for material in materials:
                        kind_label = kind_labels.get(
                            str(material["material_kind"] or ""),
                            str(material["material_kind"] or "材料"),
                        )
                        page_id_value = material["page_id"]
                        if page_id_value is None:
                            page_note = "未关联具体页面"
                        else:
                            page_number = page_number_by_id.get(
                                int(page_id_value)
                            )
                            if page_number is None:
                                page_row = application_database().get_page(
                                    int(page_id_value)
                                )
                                page_number = getattr(
                                    page_row, "page_number", None
                                )
                            page_note = (
                                f"第 {page_number} 页"
                                if page_number is not None
                                else "关联页面缺失"
                            )
                        st.caption(
                            f"- {material['material_label']}"
                            f"（{kind_label}，{page_note}）"
                        )
                else:
                    st.caption("这个组还没有登记共享材料。")
                subquestions = payload["subquestions"]
                st.markdown(
                    f"**小问列表（{len(subquestions)} 问）**——每问独立整理、"
                    "独立归纳、独立训练；依赖的证据逐问确认。"
                )
                if not subquestions:
                    st.caption(
                        "还没有小问加入。回到「待核对」，把该页候选小问"
                        "逐个「加入学习整理」，会自动归入本组。"
                    )
                material_options = {
                    int(m["id"]): m["material_label"] for m in materials
                }
                for sub in subquestions:
                    with st.expander(
                        f"({sub['question_number']})　"
                        f"{(sub['stem_text'] or '')[:40]}　·　{sub['status']}"
                    ):
                        current = question_evidence(
                            application_database(), int(sub["id"])
                        )
                        if current:
                            for ev in current:
                                state = (
                                    "✓ 已确认"
                                    if ev["status"] == "user_confirmed"
                                    else "AI 草稿（待确认）"
                                    if ev["status"] == "ai_draft"
                                    else "已拒绝"
                                )
                                st.caption(
                                    f"- {ev['material_label'] or ev['evidence_type']}"
                                    f"　·　{state}"
                                )
                        else:
                            st.caption("本问还没有绑定证据（AI 不会代你全绑）。")
                        if material_options:
                            picked = st.multiselect(
                                "本问需要哪些材料/图？（确认后写入证据子集）",
                                options=list(material_options),
                                format_func=lambda mid: material_options.get(
                                    mid, str(mid)
                                ),
                                default=[
                                    ev["source_id"]
                                    for ev in current
                                    if ev["source_id"] in material_options
                                    and ev["status"] == "user_confirmed"
                                ],
                                key=f"ev_pick_{sub['id']}",
                            )
                            if st.button(
                                "确认本问证据绑定",
                                key=f"ev_save_{sub['id']}",
                            ):
                                try:
                                    existing_sources = {
                                        ev["source_id"]: ev
                                        for ev in current
                                    }
                                    for mid in picked:
                                        prior = existing_sources.get(mid)
                                        set_question_evidence(
                                            application_database(),
                                            question_item_id=int(sub["id"]),
                                            evidence_type=(
                                                "figure"
                                                if any(
                                                    m["id"] == mid
                                                    and m["material_kind"]
                                                    == "figure"
                                                    for m in materials
                                                )
                                                else "text_material"
                                            ),
                                            source_id=mid,
                                            page_id=next(
                                                (
                                                    int(m["page_id"])
                                                    for m in materials
                                                    if int(m["id"]) == mid
                                                    and m["page_id"] is not None
                                                ),
                                                None,
                                            ),
                                            status="user_confirmed",
                                            confidence="confirmed",
                                        )
                                        if prior is not None:
                                            confirm_question_evidence(
                                                application_database(),
                                                int(sub["id"]),
                                                int(prior["id"]),
                                            )
                                    st.toast("已确认本问证据绑定。")
                                    st.rerun()
                                except QuestionGroupError as exc:
                                    st.error(f"证据绑定失败：{exc}")
                # F6-05: a confirmed group must never be a dead end — the
                # user can undo the grouping entirely (subquestions survive,
                # group structure does not). Two-step confirm on purpose.
                with st.expander("更多：解散这个题组"):
                    st.caption(
                        "解散后小问本身全部保留、回到未归组状态，"
                        "本组的共享材料登记与证据绑定会一并移除；"
                        "题目数据不会被删除。"
                    )
                    dissolve_ok = st.checkbox(
                        "我确认要解散这个题组", key=f"dissolve_ok_{group['id']}"
                    )
                    if st.button(
                        "解散题组",
                        key=f"dissolve_{group['id']}",
                        disabled=not dissolve_ok,
                    ):
                        try:
                            dissolve_group(
                                application_database(), int(group["id"])
                            )
                        except QuestionGroupError as exc:
                            st.error(f"解散失败：{exc}")
                        else:
                            st.toast("题组已解散，小问已恢复为未归组状态。")
                            st.rerun()
    except Exception:  # noqa: BLE001 - group view must never break layer 1
        import logging as _logging

        _logging.getLogger(__name__).debug(
            "综合题组视图不可用", exc_info=True
        )

# ================================================================== 第二层
#: §48: family-kind-specific field labels.  Storage columns stay stable
#: (title/description/derivation); only the human-facing labels change so
#: choosing 题型族 no longer shows a 常见推导方法 field.
_FAMILY_FIELD_LABELS = {
    "type": {"description": "题型描述（这类题的共同特征）", "derivation": "识别条件与必要特征"},
    "method": {"description": "方法说明", "derivation": "使用要点 / 与相似方法的区别"},
    "conclusion": {"description": "结论内容与成立条件", "derivation": "常见推导方法"},
}
_FAMILY_FIELD_DEFAULTS = {"description": "描述/结论", "derivation": "常见推导方法"}


def _family_field_labels(kind: str) -> dict[str, str]:
    return _FAMILY_FIELD_LABELS.get(kind, _FAMILY_FIELD_DEFAULTS)


def _render_review_queue() -> None:
    """G2-B1 §5-§11: pending AI recommendations grouped per question.

    The student answers "现在最值得我核对哪几道题", never "还有 31 条
    记录": one card per question, MEDIUM decisions first, newest first.
    「暂时不处理」 is a first-class deferred state (kept, NOT rejected,
    NOT deleted) and the only batch action is the low-risk 「全部暂时
    搁置」 — batch-accepting AI categories is deliberately not offered.
    """

    try:
        groups = organization.list_review_groups(status="pending")
        deferred_count = len(organization.list_review_items(status="deferred"))
    except LearningWorkflowError as exc:
        st.error(str(exc))
        return
    if not groups:
        st.caption(
            "没有待核对的归纳。保存题目时系统会自动整理有把握的，"
            "没把握的会放到这里由你确认。"
        )
        if deferred_count:
            _render_deferred_section()
        return
    top_note = (
        f"**待你核对**：{len(groups)} 道题有归纳建议等你确认"
        if len(groups) > 1
        else "**待你核对**：1 道题有归纳建议等你确认"
    )
    st.markdown(top_note)
    st.caption(
        "AI 拿不准的不会自动归类；你确认之后才会真正归入。"
        "没时间的可以先点「这题先不处理」，以后随时恢复。"
    )
    if st.button("这些建议先全部搁置（以后再看）", key="review_batch_defer_all"):
        try:
            all_ids = [
                int(item["id"])
                for group in groups
                for item in group["items"]
            ]
            deferred = organization.defer_review_items(all_ids)
            st.success(
                f"已搁置 {deferred} 条建议（没有删除、也没有算作拒绝；"
                "随时可以恢复）。"
            )
            st.rerun()
        except LearningWorkflowError as exc:
            st.error(f"操作失败：{exc}")
    for group in groups:
        stem_digest = ui_plaintext_digest(str(group["stem_text"] or ""), 60)
        items = group["items"]
        has_medium = any(
            str(item["confidence"]) == "medium" for item in items
        )
        headline = (
            f"这道题有 {len(items)} 个归纳建议"
            + ("（含需要你选择的）" if has_medium else "")
        )
        with st.container(border=True):
            st.markdown(f"**题目：{stem_digest}**　{headline}")
            question_id = int(group["question_id"])
            for item in items:
                _render_review_item_actions(item, in_group=True)
            queue_cols = st.columns(2)
            if queue_cols[0].button(
                "这题先不处理", key=f"qdefer_{question_id}"
            ):
                try:
                    organization.defer_review_items(
                        [int(item["id"]) for item in items]
                    )
                    st.success("已搁置这道题的建议（可随时恢复）。")
                    st.rerun()
                except LearningWorkflowError as exc:
                    st.error(f"操作失败：{exc}")
            if queue_cols[1].button(
                "这些都不合适", key=f"qreject_{question_id}"
            ):
                try:
                    for item in items:
                        organization.reject_review_item(
                            int(item["id"]),
                            note="都不合适（用户按题拒绝）",
                        )
                    st.success(
                        "已记录。这些族以后不会被自动归入这道题。"
                    )
                    st.rerun()
                except LearningWorkflowError as exc:
                    st.error(f"操作失败：{exc}")
    _render_cold_start_section()
    if deferred_count:
        _render_deferred_section()


def _render_cold_start_section() -> None:
    """G2-B1 §14-§16: shared base-family candidates for a LOW cluster.

    New-subject cold start is legitimate — the honest product answer is
    ONE confirmation covering the whole semantic cluster, never lowered
    thresholds and never page-based junk families.  The candidate stays a
    draft until the user confirms; declining just defers it.
    """

    try:
        pending = organization.list_review_items(status="pending")
    except LearningWorkflowError:
        return
    low = [item for item in pending if str(item["confidence"]) == "low"]
    if len(low) < 2:
        return
    by_kind: dict[str, list[int]] = {}
    for item in low:
        by_kind.setdefault(str(item["suggestion_kind"]), []).append(
            int(item["question_id"])
        )
    proposals: list[tuple[int, str, list[int]]] = []
    for kind, qids in by_kind.items():
        candidates_to_try: list[list[int]] = [sorted(set(qids))]
        # Pairwise fallback: one off-topic question kills the whole-set
        # intersection, but a REAL pair-level shared core is still worth
        # surfacing (G2-B1 §15).  Only the FIRST (best) proposal per kind
        # is created — proposing every pair would itself become a queue
        # pile-up, the exact problem G2B-M1 forbids.
        ordered_qids = sorted(set(qids))
        candidates_to_try.extend(
            [ordered_qids[i], ordered_qids[j]]
            for i in range(len(ordered_qids))
            for j in range(i + 1, len(ordered_qids))
        )
        for group_ids in candidates_to_try:
            try:
                proposal = organization.propose_batch_base_family(
                    group_ids, suggestion_kind=kind
                )
            except LearningWorkflowError:
                continue
            if proposal is not None:
                proposals.append((proposal[0], kind, proposal[1]))
                break
    if not proposals:
        return
    # Proposals are created (or found) by the calls above — fetch fresh
    # rows for display; the pre-call snapshot never contains new ids.
    with st.expander("这批题目的共同点（可以先一起建一个基础族）"):
        st.caption(
            "AI 发现几道题的建议共享同一个核心。确认一次就能整批归入，"
            "不用一条一条接受；不想要就先搁置。"
        )
        for review_id, kind, cluster in proposals:
            try:
                shared_item = organization.list_review_items(
                    question_id=None, status=None, limit=500
                )
                shared_row = next(
                    (
                        row
                        for row in shared_item
                        if int(row["id"]) == review_id
                    ),
                    None,
                )
            except LearningWorkflowError:
                shared_row = None
            if shared_row is None:
                continue
            kind_label = FAMILY_KIND_LABELS.get(kind, kind)
            st.markdown(
                f"- {kind_label}「{shared_row['title']}」　"
                f"{shared_row['reason']}"
            )
            if st.button(
                f"确认一起归入「{shared_row['title']}」",
                key=f"coldstart_{review_id}",
            ):
                try:
                    organization.confirm_review_item(
                        review_id, also_question_ids=cluster
                    )
                    st.success(
                        f"已建立并整批归入 {len(cluster)} 道题"
                        "（以后遇到同类题会自动复用这个族）。"
                    )
                    st.rerun()
                except LearningWorkflowError as exc:
                    st.error(f"操作失败：{exc}")


def _render_deferred_section() -> None:
    """Collapsed 「已搁置」 area: kept history, reopenable (G2-B1 §8)."""

    deferred = organization.list_review_items(status="deferred")
    with st.expander(f"已暂时搁置（{len(deferred)} 条）", expanded=False):
        st.caption("搁置不等于拒绝：随时可以恢复，AI 也不会自动替你处理。")
        for item in deferred:
            stem_digest = ui_plaintext_digest(str(item["stem_text"] or ""), 40)
            item_cols = st.columns([6, 2])
            item_cols[0].caption(
                f"题目：{stem_digest} · {item['suggestion_kind']}「{item['title']}」"
            )
            if item_cols[1].button("恢复", key=f"reopen_{item['id']}"):
                try:
                    organization.reopen_review_item(int(item["id"]))
                    st.rerun()
                except LearningWorkflowError as exc:
                    st.error(f"操作失败：{exc}")


def _render_review_item_actions(item: dict, *, in_group: bool) -> None:
    """Per-suggestion actions inside a question group (G2-B1 §6/§11)."""

    kind_label = FAMILY_KIND_LABELS.get(
        str(item["suggestion_kind"]), str(item["suggestion_kind"])
    )
    review_key = f"review_{item['id']}"
    line = f"- {kind_label}「{item['title']}」：{item['reason'] or '（AI 没有给出解释）'}"
    if str(item["confidence"]) == "medium":
        st.markdown(line)
        candidates: list[tuple[int, str]] = []
        matched = item["matched_family_id"]
        if matched is not None:
            try:
                fam = organization.get_family(int(matched))
                candidates.append((fam.id, f"「{fam.title}」"))
            except LearningWorkflowError:
                pass
        try:
            for fid, fkind, ftitle, _score in organization.find_candidate_families(
                int(item["question_id"]), limit=3
            ):
                if fkind == str(item["suggestion_kind"]) and all(
                    fid != existing[0] for existing in candidates
                ):
                    candidates.append((fid, f"「{ftitle}」"))
        except LearningWorkflowError:
            pass
        if candidates:
            candidate_map = dict(candidates)
            candidate_ids = [cid for cid, _ in candidates]
            choice = st.radio(
                "这道题属于哪个分类？",
                options=candidate_ids,
                format_func=lambda cid, mapping=candidate_map: mapping.get(
                    cid, str(cid)
                ),
                key=f"{review_key}_choice",
                label_visibility="collapsed",
            )
            if st.button("选择这个", key=f"{review_key}_accept"):
                try:
                    organization.confirm_review_item(
                        item["id"], family_id=int(choice)
                    )
                    st.success("已按你的选择归入。")
                    st.rerun()
                except LearningWorkflowError as exc:
                    st.error(f"操作失败：{exc}")
        else:
            st.caption("原来推荐的族已不存在，可以用下方的「这些都不合适」。")
    else:
        st.markdown(line)
        action_cols = st.columns(4)
        if action_cols[0].button("接受", key=f"{review_key}_accept"):
            try:
                organization.confirm_review_item(item["id"])
                st.success("已创建并归入（相近族会直接复用）。")
                st.rerun()
            except LearningWorkflowError as exc:
                st.error(f"操作失败：{exc}")
        rename_value = action_cols[1].text_input(
            "改名后接受", key=f"{review_key}_rename", placeholder="更准确的名字"
        )
        if action_cols[1].button("确认改名", key=f"{review_key}_rename_go"):
            try:
                organization.accept_review_item_renamed(
                    item["id"], new_title=rename_value
                )
                st.success("已按新名称创建并归入。")
                st.rerun()
            except LearningWorkflowError as exc:
                st.error(f"操作失败：{exc}")
        merge_options = [
            fam
            for fam in organization.list_families(
                family_kind=str(item["suggestion_kind"])
            )
            if fam.status != "retired"
        ]
        merged = action_cols[2].selectbox(
            "合并到已有族",
            options=merge_options,
            format_func=family_picker_label,
            key=f"{review_key}_merge",
            label_visibility="collapsed",
        )
        if action_cols[2].button("确认合并", key=f"{review_key}_merge_go"):
            try:
                organization.confirm_review_item(item["id"], family_id=merged.id)
                st.success("已并入你选择的族（没有新建族）。")
                st.rerun()
            except LearningWorkflowError as exc:
                st.error(f"操作失败：{exc}")
        if action_cols[3].button("暂不归类", key=f"{review_key}_reject"):
            try:
                organization.reject_review_item(
                    item["id"], note="暂不归类（用户拒绝）"
                )
                st.success("已记录。以后遇到同类题会再问你，不会偷偷建族。")
                st.rerun()
            except LearningWorkflowError as exc:
                st.error(f"操作失败：{exc}")


with family_tab:
    st.subheader("归纳族")
    # G2-B: the pending-review queue comes first — reviewing what the AI
    # suggested is the main flow; taxonomy management is secondary.
    _render_review_queue()
    st.divider()
    # V086-303: the browse filter defaults to 全部 so newly created families
    # never disappear behind a default filter.
    family_kind = st.selectbox(
        "族类型",
        options=["all", "type", "method", "conclusion"],
        format_func=lambda v: "全部" if v == "all" else FAMILY_KIND_LABELS[v],
        key="family_kind_select",
    )
    create_column, browse_column = st.columns([1, 2])
    with create_column:
        # §49: manual family creation is a secondary entry now — the main
        # flow is AI auto-organization at question save time, reviewed by
        # the user.  The blank form no longer dominates the column.
        with st.expander("+ 手动新建归纳（通常用不到：保存题目后会自动归纳）"):
            with st.form("family_create_form", clear_on_submit=True):
                st.markdown("**新建族**")
                new_kind = st.selectbox(
                    "族类型",
                    options=["type", "method", "conclusion"],
                    format_func=lambda v: FAMILY_KIND_LABELS[v],
                    index=["type", "method", "conclusion"].index(
                        family_kind if family_kind != "all" else "type"
                    ),
                    key="family_new_kind",
                )
                new_labels = _family_field_labels(new_kind)
                new_title = st.text_input("标题", key="family_new_title")
                new_description = st.text_area(
                    new_labels["description"], key="family_new_desc", height=80
                )
                new_derivation = st.text_area(
                    new_labels["derivation"], key="family_new_deriv", height=60
                )
                make_family = st.form_submit_button("新建", type="primary")
            if make_family:
                try:
                    organization.create_family(
                        family_kind=new_kind,
                        title=new_title,
                        description=new_description,
                        derivation=new_derivation,
                    )
                    st.success("已新建族。")
                    st.rerun()
                except LearningWorkflowError as exc:
                    st.error(f"新建失败：{exc}")
        with st.expander("高频二级结论（系统统计）"):
            frequency_rows = organization.conclusion_frequency(limit=10)
            if not frequency_rows:
                st.caption("还没有二级结论。")
            for row in frequency_rows:
                st.caption(
                    f"「{row['title']}」 · 出现于 {row['question_count']} 道题"
                )
        with st.expander("归纳健康度（系统统计）"):
            stats = organization.fragmentation_stats()
            st.caption(
                f"题目 {stats['question_items']} 道 · "
                f"题型族 {stats['type_families']} · 方法族 {stats['method_families']} · "
                f"二级结论 {stats['conclusion_families']} · "
                f"只挂了 0-1 题的族 {stats['singleton_families']} 个 · "
                f"同时属于多个族的题 {stats['multi_family_questions']} 道"
            )
    with browse_column:
        try:
            families = organization.list_families(
                family_kind=None if family_kind == "all" else family_kind
            )
        except LearningWorkflowError as exc:
            st.error(str(exc))
            families = []
        if not families:
            if family_kind == "all":
                st.caption("还没有族。")
            else:
                st.caption(
                    f"还没有「{FAMILY_KIND_LABELS[family_kind]}」类型的族"
                    "（可在族类型里选「全部」查看其它族）。"
                )
        else:
            family = organization.get_family(
                st.selectbox(
                    "选择族",
                    options=families,
                    format_func=family_picker_label,
                    key="family_picker",
                ).id
            )
            family_kind_label = FAMILY_KIND_LABELS.get(family.family_kind, "")
            st.markdown(
                f"**{family_kind_label}「{family.title}」**　"
                f"成员：{family.member_count} 题"
            )
            family_labels = _family_field_labels(family.family_kind)
            if family.description:
                st.markdown(f"{family_labels['description']}：{family.description}")
                # G2-B §19: a secondary conclusion without explicit
                # conditions (region/scale/season/premise) says so loudly.
                if family.family_kind == "conclusion":
                    condition_warning = conclusion_condition_risk(
                        family.description
                    )
                    if condition_warning:
                        st.caption(f"⚠ {condition_warning}")
            if family.derivation:
                st.caption(f"{family_labels['derivation']}：{family.derivation}")
            if family.confusion_notes:
                st.caption(f"易混淆：{family.confusion_notes}")
            # G2-B §43/§74: surface family origin + boundary reminders when
            # this family carries boundary assets — new members see the
            # reminder instead of each question duplicating a 600-word note.
            try:
                family_boundary = wings_service.list_entries_for_family(
                    family.id, wing_kind="boundary_counterexample"
                )
            except LearningWorkflowError:
                family_boundary = []
            if family_boundary:
                latest = family_boundary[0]
                validity = latest.fields.get("validity_conditions") or []
                first = str(validity[0]) if validity else "（成立条件待补充）"
                st.warning(f"这个族有边界提醒：使用前先看条件。成立条件示例：{first}")
            members = organization.list_family_members(family.id)
            if members:
                st.markdown("**成员题目**（含归纳来源，可移出）")
                for relation, member, provenance in members:
                    source = (
                        f" · 来源：{provenance}" if provenance else " · 来源：早期归纳（未记录）"
                    )
                    member_cols = st.columns([9, 1])
                    member_cols[0].caption(
                        f"{RELATION_LABELS.get(relation, relation)} · "
                        f"{question_picker_label(member)}{source}"
                    )
                    if member_cols[1].button(
                        "移出",
                        key=f"family_remove_{family.id}_{member.id}",
                    ):
                        try:
                            organization.remove_from_family(
                                member.id,
                                family.id,
                                note="用户在族详情里移出",
                            )
                            st.success(
                                "已移出。这道题以后不会被自动加回这个族；"
                                "需要时可以在题目编辑里手动加入。"
                            )
                            st.rerun()
                        except LearningWorkflowError as exc:
                            st.error(f"移出失败：{exc}")
            else:
                st.caption("还没有成员题目。")
            with st.form("family_assign_form", clear_on_submit=True):
                st.markdown("**把题目加入这个族**")
                assign_question = _question_selectbox("选择题目", f"assign_q_{family.id}")
                relation = st.selectbox(
                    "关系",
                    options=["member", "variant", "counterexample"],
                    format_func=lambda v: RELATION_LABELS[v],
                    key=f"assign_rel_{family.id}",
                )
                do_assign = st.form_submit_button("加入")
            if do_assign and assign_question is not None:
                try:
                    organization.assign_to_family(assign_question.id, family.id, relation=relation)
                    st.success("已加入族。")
                    st.rerun()
                except LearningWorkflowError as exc:
                    st.error(f"加入失败：{exc}")
            elif do_assign:
                st.error("加入失败：还没有选择题目。")
            with st.form("family_revise_form", clear_on_submit=True):
                st.markdown("**修订这个族的结论**")
                revision_kind = st.selectbox(
                    "修订类型",
                    options=["narrowed", "broadened", "corrected", "merged"],
                    format_func=lambda v: REVISION_KIND_LABELS[v],
                    key=f"rev_kind_{family.id}",
                )
                revision_note = st.text_area(
                    "修订说明（必填）", key=f"rev_note_{family.id}", height=60
                )
                new_title = st.text_input("新标题（可选）", key=f"rev_title_{family.id}")
                new_description = st.text_area(
                    "新结论（可选）", key=f"rev_desc_{family.id}", height=60
                )
                do_revise = st.form_submit_button("提交修订")
            if do_revise:
                try:
                    organization.revise_family(
                        family.id,
                        revision_kind=revision_kind,
                        note=revision_note,
                        new_title=new_title or None,
                        new_description=new_description or None,
                    )
                    st.success("已记录修订。")
                    st.rerun()
                except LearningWorkflowError as exc:
                    st.error(f"修订失败：{exc}")
            with st.expander("修订历史（这个旧结论为什么被修正）"):
                history = organization.family_revision_history(family.id)
                if not history:
                    st.caption("暂无修订记录。")
                for record in history:
                    st.caption(
                        f"{format_beijing_time(record['created_at'])} · "
                        f"{record['revision_kind']} · {record['note']}"
                    )
            st.divider()
            st.markdown("**这个族的两翼**")
            family_wing_kind = st.radio(
                "翼",
                options=list(WING_KINDS),
                format_func=lambda v: WING_KIND_LABELS[v],
                horizontal=True,
                key=f"family_wing_kind_{family.id}",
                label_visibility="collapsed",
            )
            _render_wing_editor(wing_kind=family_wing_kind, family_id=family.id)




with trigger_tab:
    st.subheader("方法触发")
    st.caption(
        "看到什么条件 → 想到什么方法 → 为什么 → 如何落到这道题。"
        "内容可以是空的；AI 生成的草稿必须由你确认后才算数。"
    )
    trigger_question = _question_selectbox("选择题目", "trigger_question_picker")
    if trigger_question is not None:
        _render_question_wing_ai(trigger_question, "method_trigger")
        _render_wing_editor(wing_kind="method_trigger", question_id=trigger_question.id)

with boundary_tab:
    st.subheader("边界反例")
    st.caption(
        "什么时候不能用 → 缺少什么条件 → 常见误用 → 反例/边界 → 条件变化后的结论。"
        "内容可以是空的；AI 生成的草稿必须由你确认后才算数。"
    )
    boundary_question = _question_selectbox("选择题目", "boundary_question_picker")
    if boundary_question is not None:
        _render_question_wing_ai(boundary_question, "boundary_counterexample")
        _render_wing_editor(wing_kind="boundary_counterexample", question_id=boundary_question.id)

# ================================================================== 第三层
_MASTERY_DO_STATE_LABELS = {
    "no_evidence": "尚未验证",
    "needs_practice": "需要再练",
    "basically_ok": "基本会做",
    "independent": "能独立完成",
}
_MASTERY_EXPLAIN_LABELS = {
    "unverified": "未验证",
    "gaps": "讲解有缺口",
    "basically_clear": "基本讲清",
    "complete": "条件完整",
}
_MASTERY_INDEPENDENCE_LABELS = {
    "unknown": "未知",
    "hint_dependent": "依赖提示",
    "light_hint": "少量提示",
    "independent": "独立完成",
}
_MASTERY_STABILITY_LABELS = {
    "unverified": "未验证",
    "temporary": "暂时会",
    "repeated": "重复正确",
    "spaced": "隔时稳定",
}
_OPEN_ANSWER_LEVEL_LABELS = {
    "complete": "完整掌握",
    "direction_incomplete": "方向正确但不完整",
    "evidence_gap": "缺证据链",
    "partial": "部分掌握",
    "incorrect": "错误 / 答非所问",
}
_OPEN_ANSWER_RESULT = {
    "complete": "correct",
    "direction_incomplete": "partial",
    "evidence_gap": "partial",
    "partial": "partial",
    "incorrect": "incorrect",
}


def _review_at_human(value: object) -> str:
    """把 next_review_at 的时间说成学生语言（§71：今天/明天/N 天后）。"""

    text = str(value or "").strip()
    if not text:
        return "未排期"
    try:
        when = to_beijing_time(text)
    except ValueError:
        return text[:16]
    today = beijing_now().date()
    delta = (when.date() - today).days
    if delta <= 0:
        return "今天"
    if delta == 1:
        return "明天"
    return f"{delta} 天后"


def _render_training_submit(question_id: int, event_type: str, heading: str) -> None:
    """One training task's result form with idempotent submission (§105)."""

    with st.form(f"training_form_{event_type}_{question_id}", clear_on_submit=True):
        st.markdown(f"**{heading}——记录这次结果**")
        result_cols = st.columns(2)
        result = result_cols[0].selectbox(
            "结果",
            options=["correct", "incorrect", "partial"],
            format_func=lambda v: OUTCOME_LABELS[v],
            key=f"training_result_{event_type}_{question_id}",
        )
        independence = result_cols[1].selectbox(
            "完成方式",
            options=["independent", "light_hint", "heavy_hint", "after_answer"],
            format_func=lambda v: {
                "independent": "完全独立完成",
                "light_hint": "轻提示后完成",
                "heavy_hint": "强提示后完成",
                "after_answer": "看了答案/解析后完成",
            }[v],
            key=f"training_indep_{event_type}_{question_id}",
        )
        training_note = st.text_input("备注", key=f"training_note_{event_type}_{question_id}")
        submit_training = st.form_submit_button("提交这次训练", type="primary")
    if submit_training:
        # §105: the same question + task + date collapses into ONE row.
        key = f"{question_id}:{event_type}:{beijing_now().date().isoformat()}"
        try:
            evidence_id = mastery.record_evidence(
                question_id,
                event_type=event_type,
                result=result,
                independence=independence,
                hint_used=independence != "independent",
                source="system_training",
                provenance="系统训练任务结果",
                user_note=training_note,
                idempotency_key=key,
            )
        except LearningWorkflowError as exc:
            st.error(f"记录失败：{exc}")
        else:
            refreshed = mastery.mastery_states(question_id)
            st.success(
                f"已记录（证据 #{evidence_id}）。当前状态："
                f"{_MASTERY_DO_STATE_LABELS[refreshed['do_state']]} · "
                f"{_MASTERY_STABILITY_LABELS[refreshed['stability']]}"
                "。下面的「当前建议」已按新证据重新计算。"
            )
            st.rerun()


def _render_task_materials(question) -> None:
    """G3-B P0-3: the training task panel shows the material image INLINE.

    G3-A HIGH: layer-3 training sent the user back to layer 1 to look at
    the map.  The image now renders directly above the task (ZERO-COPY:
    it is the original page image, not a copy).  If the page is gone the
    panel says so honestly and training still works on text.
    """

    page_id = getattr(question, "page_id", None)
    if page_id is None:
        return
    try:
        page = application_database().get_page(int(page_id))
    except Exception:  # noqa: BLE001 - inline material must never kill training
        page = None
    image_path = getattr(page, "image_path", None)
    if page is None or image_path is None or not Path(image_path).exists():
        return
    with st.expander("📄 任务材料（原图就在这里，无需翻页）", expanded=True):
        st.image(str(image_path), width=420, caption="本页原图（共享原始证据，未被复制）")


def _render_training_task(question, action: dict) -> None:
    """Render the concrete task panel for one next-action (§5/§31-§37)."""

    _render_task_materials(question)
    task_type = str(action["task_type"])
    payload = action.get("payload") or {}
    if task_type in ("no_evidence_baseline", "redo_original"):
        st.info("重做这道题：先不看答案和解析，做完再回来提交结果。")
        _render_training_submit(question.id, "practice", "重做原题")
    elif task_type == "targeted_variation":
        variation = payload.get("variation") or {}
        if variation.get("available"):
            st.markdown(f"**训练题来源**：{variation['provenance']}")
            st.markdown(f"**题目（{variation['question_number']}）**：{variation['stem']}")
            if variation.get("page_id"):
                st.caption("任务材料已在上方「任务材料」展开图中直接显示。")
            _render_training_submit(question.id, "practice", "同族变式训练")
        else:
            st.info(
                "同族里还没有其它真实题，先用原题巩固；"
                "系统不会用 AI 编一道假题来冒充训练。"
            )
            _render_training_submit(question.id, "practice", "原题巩固")
    elif task_type == "method_trigger":
        trigger = payload.get("trigger") or {}
        if trigger.get("available"):
            st.markdown(f"**方法**：{trigger.get('method') or '（未记录）'}")
            st.markdown(f"看到这些条件/信号就该想到它（{trigger['provenance']}）：")
            for line in (trigger.get("triggers") or []) + (trigger.get("signals") or []):
                st.markdown(f"- {line}")
            st.caption("先自己把思路接上，再提交结果。")
        else:
            st.info("这个族还没有方法触发内容——先在第二层「方法触发」补一条，训练才有抓手。")
        _render_training_submit(question.id, "method_trigger", "方法触发训练")
    elif task_type == "boundary_check":
        boundary = payload.get("boundary") or {}
        if boundary.get("available"):
            st.markdown(f"以下说法出自这个族的边界资产（{boundary['provenance']}）。")
            st.markdown("**成立条件**")
            for line in boundary.get("validity") or []:
                st.markdown(f"- {line}")
            st.markdown("**什么时候不能套用——请对照题目，逐条想一遍**")
            for line in boundary.get("invalidation") or []:
                st.markdown(f"- {line}")
            st.caption("判断这道题有没有踩到「不能套用」的情况，再提交结果。")
        else:
            st.info("这个族还没有边界资产——先在第二层补边界反例，训练才有辨析材料。")
        _render_training_submit(question.id, "boundary_check", "边界条件辨析")
    elif task_type == "spaced_review":
        st.info("这道题你已能独立完成并通过讲解——今天的任务只是隔时确认，做完提交即可。")
        _render_training_submit(question.id, "review", "间隔复习")
    elif task_type == "explain_back":
        st.info("这次建议先讲清思路。请到本页上方「讲题」区域，可以自评，也可以提交给 AI 审核。")


def _render_manual_record_form(question_id: int) -> None:
    """手动补录：线下纸质训练 / 老师口试 / 其它平台（§16/§17 降级区）。"""

    with st.form("practice_form", clear_on_submit=True):
        st.markdown("**手动补录一次练习**")
        st.caption(
            "用于线下纸质训练、老师口试、家长检查或其它平台做题。"
            "这里记录的是你自己的补充记录，不是系统训练结果。"
        )
        outcome = st.selectbox(
            "结果",
            options=["correct", "incorrect", "partial"],
            format_func=lambda v: OUTCOME_LABELS[v],
            key="practice_outcome",
        )
        can_explain = st.checkbox("能讲给别人听（会讲）", key="practice_explain")
        practice_note = st.text_input("备注", key="practice_note")
        do_record = st.form_submit_button("记录")
    if do_record:
        try:
            mastery.record_evidence(
                question_id,
                event_type="practice",
                result=outcome,
                independence="unknown",
                explanation_state=(
                    "basically_clear" if can_explain else "unverified"
                ),
                source="user_manual",
                provenance="手动补录（非系统训练结果）",
                user_note=practice_note,
                idempotency_key=(
                    f"{question_id}:manual:{beijing_now().date().isoformat()}:"
                    f"{outcome}"
                ),
            )
        except LearningWorkflowError as exc:
            st.error(f"记录失败：{exc}")
        else:
            st.success("已作为手动补录记录（不会冒充系统训练结果）。")
            st.rerun()


def _teachback_review_markdown(review: dict) -> str:
    """Preserve every AI review section in one readable attempt record."""

    verdict = _MASTERY_EXPLAIN_LABELS[str(review["verdict"])]
    sections = [f"**AI 审核结果：{verdict}**"]
    for title, key in (
        ("讲清楚的地方", "what_worked"),
        ("仍有缺口的地方", "missing"),
        ("修改意见", "improvements"),
    ):
        lines = review.get(key) or []
        if lines:
            sections.append(f"**{title}**\n" + "\n".join(f"- {line}" for line in lines))
    if review.get("feedback"):
        sections.append(f"**审核说明**\n{review['feedback']}")
    explanation = str(review.get("full_explanation") or "").strip()
    sections.append(
        "**完整参考讲解（提交后显示，仍请对照原卷核对）**\n"
        + (explanation or "已保存订正不足，暂不能可靠地给出完整讲解。")
    )
    return "\n\n".join(sections)


def _render_teachback_section() -> None:
    """Show the parallel teach-back checks before targeted training."""

    st.subheader("讲题：把思路说清楚")
    st.caption(
        "选一道已整理的题。下面两项评价并联：自评能讲清，或 AI 审核基本讲清，"
        "有一项通过就只记本次讲题通过一次；一次通过不代表已经完全掌握。"
    )
    teachback_question = _question_selectbox("选择要讲的题", "teachback_question_picker")
    if teachback_question is not None:
        st.markdown("**先看题目，再试着讲**")
        if teachback_question.shared_context:
            st.markdown("**父题公共条件**")
            render_question_math_markdown(teachback_question.shared_context)
            st.markdown("**本小题**")
        if teachback_question.shared_context:
            source_path, regions = _question_visual_source(teachback_question)
            render_region_images(source_path, [r for r in regions if r["role"] == "shared"])
        cropped = _render_saved_question_content(teachback_question)
        if not cropped:
            source_path, _ = _question_visual_source(teachback_question)
            if source_path is not None and source_path.is_file():
                st.image(str(source_path), width=650, caption="本题来源原页")
        st.caption("提交讲解前不会显示参考讲解；来源和原页请到第一层核对。")
        peer_column, ai_column = st.columns([1, 2], gap="large")
        with peer_column:
            st.markdown("### 讲给别人听")
            st.caption("想象对方没做过这题：你能把为什么这样做、关键步骤讲明白吗？")
            peer_choice = st.radio(
                "能把这道题讲清楚吗？",
                options=("是，能讲清楚", "否，还讲不清楚"),
                index=None,
                key=f"teachback_peer_choice_{teachback_question.id}",
                label_visibility="collapsed",
            )
            if st.button("保存自评", key=f"teachback_peer_save_{teachback_question.id}"):
                if peer_choice is None:
                    st.warning("请先选择“是”或“否”。")
                else:
                    peer_yes = peer_choice == "是，能讲清楚"
                    try:
                        mastery.record_evidence(
                            teachback_question.id,
                            event_type="explain_back",
                            result="correct" if peer_yes else "incorrect",
                            explanation_state="basically_clear" if peer_yes else "gaps",
                            source="user_manual",
                            provenance="讲给别人听（学生自评，未经过 AI 核验）",
                            user_note="自评能讲清楚" if peer_yes else "自评还讲不清楚",
                        )
                    except LearningWorkflowError as exc:
                        st.error(f"自评保存失败：{exc}")
                    else:
                        st.rerun()
        with ai_column:
            st.markdown("### 向 AI 说明我的解题思路")
            st.caption("先写你的理解；保存后 AI 才审核，并给出具体修改意见与参考讲解。")
            explanation_text = st.text_area(
                "我的讲解",
                height=250,
                key=f"teachback_text_{teachback_question.id}",
                placeholder="这道题要我求什么？我为什么用这个方法？关键条件和每一步是什么？",
            )
            button_space, button_column = st.columns([3, 2])
            with button_column:
                submit_explanation = st.button(
                    "保存并交给 AI 审核",
                    key=f"teachback_submit_{teachback_question.id}",
                    type="primary",
                )
            if submit_explanation:
                if not explanation_text.strip():
                    st.warning("先写下自己的讲解，再提交审核。")
                else:
                    try:
                        attempt_id = mastery.record_explanation_attempt(
                            teachback_question.id, content=explanation_text
                        )
                    except LearningWorkflowError as exc:
                        st.error(f"讲解保存失败：{exc}")
                    else:
                        ai = _ai_service()
                        if ai is None:
                            st.info("讲解已保存。AI 服务未配置，暂时不能审核；自评仍可单独使用。")
                        else:
                            try:
                                profile = application_training_profile_service().get_profile()
                                basic_profile = profile.basic
                                with st.spinner("AI 正在核对你讲的条件、步骤和结论……"):
                                    review = ai.review_explanation_task(
                                        teachback_question,
                                        explanation_text,
                                        learner_stage=(
                                            basic_profile.stage if basic_profile else ""
                                        ),
                                        learner_grade=(
                                            basic_profile.grade if basic_profile else ""
                                        ),
                                    )
                                review_text = _teachback_review_markdown(review)
                                mastery.update_explanation_feedback(attempt_id, review_text)
                                mastery.record_evidence(
                                    teachback_question.id,
                                    event_type="explain_back",
                                    result=(
                                        "correct"
                                        if review["verdict"] in ("basically_clear", "complete")
                                        else "incorrect"
                                    ),
                                    explanation_state=review["verdict"],
                                    source="system_training",
                                    provenance="学生原话提交后的 AI 讲题审核",
                                    ai_evaluation=review_text,
                                    idempotency_key=f"teachback-ai-attempt-{attempt_id}",
                                )
                            except LearningAIDraftError as exc:
                                st.error(f"讲解已保存，但 AI 审核未通过校验：{exc}")
                            except LearningWorkflowError as exc:
                                st.error(f"讲解已保存，但审核结果保存失败：{exc}")
                            except Exception:  # noqa: BLE001 - keep saved student work
                                LOGGER.exception("AI 讲题审核失败")
                                st.error("讲解已保存，但 AI 审核失败；本次不记 AI 通过。")
                            else:
                                st.rerun()

        status = mastery.teachback_status(teachback_question.id)
        attempts = mastery.list_explanation_attempts(teachback_question.id)
        latest_review_pending = bool(
            attempts and not str(attempts[-1].get("feedback") or "").strip()
        )
        st.divider()
        st.markdown("**该题讲题证据概况**")
        st.caption(
            "这里合并的是最近一次有效自评与最近一次通过校验的 AI 审核；"
            "历史通过不代表刚提交的讲解已经通过。"
        )
        if latest_review_pending:
            st.warning(
                "最新一次讲解原话已保存，但还没有通过校验的 AI 反馈。"
                "下列有效评价如有通过，来自较早记录或独立自评。"
            )
        manual_label = (
            "通过（自评）" if status["manual_pass"]
            else "未通过（自评）" if status["manual_state"] == "gaps"
            else "未记录"
        )
        ai_label = (
            "通过" if status["ai_pass"]
            else "未通过" if status["ai_state"] == "gaps"
            else "未审核"
        )
        st.caption(f"最近有效自评：{manual_label}　|　最近有效 AI 审核：{ai_label}")
        if status["passed"]:
            st.success(
                "该题已有讲题通过证据。两项并联，均通过也只算一次；"
                "还需后续练习验证是否真正掌握。"
            )
        elif status["combined_state"] == "gaps":
            st.warning("该题尚无讲题通过证据，可对照修改意见再讲一次。")
        else:
            st.info("还没有讲题评价。")
        if attempts:
            st.markdown("**我的讲解与 AI 反馈**")
            for index, attempt in reversed(list(enumerate(attempts, start=1))):
                with st.expander(
                    f"第 {index} 次 · {format_beijing_time(attempt['created_at'])}",
                    expanded=index == len(attempts),
                ):
                    st.markdown("**我原来是这样讲的**")
                    render_question_math_markdown(str(attempt["content"]))
                    if attempt.get("feedback"):
                        render_question_math_markdown(str(attempt["feedback"]))
                    else:
                        st.caption("已保存原话；尚无通过校验的 AI 反馈。")


with mastery_tab:
    st.subheader("掌握训练")
    _render_teachback_section()
    st.divider()

    # ==================================================================
    # 针对训练任务配置与训练目标选择系统（阶段2）
    # ==================================================================
    targeted_service = application_targeted_training_service()
    profile_service = application_training_profile_service()
    active_profile = profile_service.get_profile()
    available_subjects = sorted(
        {
            question.subject.strip()
            for question in question_service.list_question_items()
            if question.subject.strip()
        }
    )
    current_subject = None
    if len(available_subjects) == 1:
        current_subject = available_subjects[0]
        st.caption(f"当前训练学科：{current_subject}（来自待核对题目）")
    elif available_subjects:
        current_subject = st.selectbox(
            "查看哪个学科的针对训练",
            options=available_subjects,
            key="mastery_subject_picker",
            help="这里只切换已整理题目的学科，不修改教学阶段或年级配置。",
        )
    else:
        st.info("请先在待核对页面为资料人工选择学科，再加入学习整理。")

    retrieval_service = application_question_source_retrieval_service()
    session_service = application_training_session_service()
    render_targeted_training_section(
        targeted_service,
        current_subject=current_subject,
        retrieval_service=retrieval_service,
        learner_profile=active_profile,
        session_service=session_service,
    )

    st.divider()
    st.markdown("**复习计划（按族）**")
    review_rows = mastery.list_review_queue()
    if not review_rows:
        st.caption("还没有族级复习计划。完成训练后，系统会自动安排。")
    for row in review_rows:
        title = row.get("title") or "（未命名族）"
        next_at = format_beijing_time(row.get("next_review_at"), empty="未排期")
        st.caption(f"{title} · 下次复习：{next_at} · 间隔 {row.get('review_interval_days')} 天")

    with st.expander("单题掌握建议与历史记录", expanded=False):
        # ---------- 今天建议复习（跨题目的到期项，最多 5 条，§74 防堆积）
        due_items = mastery.today_review_items(limit=5)
        if due_items:
            st.markdown("**今天建议先复习这几项**")
            for item in due_items:
                with st.container(border=True):
                    family_note = (
                        f" · {item['family_title']}" if item.get("family_title") else ""
                    )
                    st.markdown(f"**{item['stem']}**{family_note}")
                    st.caption(
                        f"为什么现在：{item['why']}"
                        f"（上次：{OUTCOME_LABELS.get(item['last_result'], item['last_result'])}）"
                    )
                    review_cols = st.columns(2)
                    due_question = None
                    try:
                        due_question = question_service.get_question_item(
                            int(item["question_id"])
                        )
                    except LearningWorkflowError:
                        due_question = None
                    if review_cols[0].button(
                        "开始复习", key=f"review_start_{item['evidence_id']}"
                    ):
                        st.session_state["g2c_active_task"] = {
                            "question_id": int(item["question_id"]),
                            "evidence_id": int(item["evidence_id"]),
                        }
                        st.rerun()
                    if review_cols[1].button(
                        "稍后再练", key=f"review_defer_{item['evidence_id']}"
                    ):
                        try:
                            mastery.defer_review(int(item["evidence_id"]))
                            st.success("已推迟到明天（不算失败）。")
                            st.rerun()
                        except LearningWorkflowError as exc:
                            st.error(f"操作失败：{exc}")
                    if (
                        st.session_state.get("g2c_active_task", {}).get(
                            "evidence_id"
                        )
                        == int(item["evidence_id"])
                        and due_question is not None
                    ):
                        due_action = mastery.next_action(due_question.id)
                        _render_training_task(due_question, due_action)
            st.divider()
        else:
            st.caption("今天没有必须复习的内容——完成新训练后，系统会自动排下一次复习时间。")

        mastery_question = _question_selectbox("选择题目", "mastery_question_picker")
        if mastery_question is not None:
            # ---------- 当前建议（第一屏主角，§5/§43）
            states = mastery.mastery_states(mastery_question.id)
            action = mastery.next_action(mastery_question.id)
            st.markdown(f"### 当前建议：{action['task_label']}")
            for reason in action["why"]:
                st.caption(f"为什么：{reason}")
            active_task = st.session_state.get("g2c_active_task") or {}
            task_open = active_task.get("question_id") == mastery_question.id
            if not task_open:
                if st.button("开始这个任务", key=f"start_task_{mastery_question.id}"):
                    st.session_state["g2c_active_task"] = {
                        "question_id": mastery_question.id
                    }
                    st.rerun()
            else:
                _render_training_task(mastery_question, action)

            # ---------- 掌握证据摘要（四维，学生语言；无历史=尚未验证 §47）
            st.divider()
            evidence_cols = st.columns(4)
            evidence_cols[0].metric("会做", _MASTERY_DO_STATE_LABELS[states["do_state"]])
            evidence_cols[1].metric("讲解验证", _MASTERY_EXPLAIN_LABELS[states["explain_state"]])
            evidence_cols[2].metric("独立性", _MASTERY_INDEPENDENCE_LABELS[states["independence"]])
            evidence_cols[3].metric(
                "稳定性",
                _MASTERY_STABILITY_LABELS[states["stability"]],
                help="一次做对只是「暂时会」；隔了几天仍然做对，才算「隔时稳定」。",
            )
            summary = mastery.question_mastery_summary(mastery_question.id)
            st.caption(
                f"历史：练过 {summary['total']} 次 · 做对 {summary['correct']} 次"
                "（历史信息，不作为掌握结论）"
            )
            records = mastery.list_practice_records(mastery_question.id)
            if records:
                with st.expander("练习历史"):
                    for record in records:
                        explain = record.get("can_explain")
                        outcome_label = OUTCOME_LABELS.get(
                            str(record["outcome"]), str(record["outcome"])
                        )
                        explain_suffix = (
                            f" · 会讲={'是' if explain else '否'}"
                            if explain is not None
                            else ""
                        )
                        st.caption(
                            f"{format_beijing_time(record['practiced_at'])} · {outcome_label}"
                            + explain_suffix
                            + (f" · {record['note']}" if record.get("note") else "")
                        )

            # ---------- 手动补录（降级区，§16）
            with st.expander("手动补录（线下纸质训练 / 老师口试 / 其它平台）"):
                _render_manual_record_form(mastery_question.id)

# ================================================================== 导出层
with output_tab:
    st.subheader("导出")
    st.caption(
        "导出永远来自结构化学习资产：可以创建错题本、复习册、专项册等导出文档，"
        "并下载 Markdown 与 TXT（记事本可直接打开）。Word 导出暂未开放。"
    )
    create_column, manage_column = st.columns([1, 2])
    with create_column:
        with st.form("collection_create_form", clear_on_submit=True):
            st.markdown("**新建导出文档**")
            collection_kind = st.selectbox(
                "类型",
                options=[
                    "error_book",
                    "good_book",
                    "review_pack",
                    "topic_pack",
                    "conclusion_handbook",
                    "weakness_report",
                ],
                format_func=lambda v: _COLLECTION_KIND_LABELS[v],
                key="collection_kind",
            )
            collection_title = st.text_input("标题", key="collection_title")
            collection_desc = st.text_area("说明", key="collection_desc", height=60)
            make_collection = st.form_submit_button("新建", type="primary")
        if make_collection:
            try:
                output_service.create_collection(
                    kind=collection_kind, title=collection_title, description=collection_desc
                )
                st.success("已新建导出文档。")
                st.rerun()
            except LearningWorkflowError as exc:
                st.error(f"新建失败：{exc}")
    with manage_column:
        try:
            collections = output_service.list_collections()
        except LearningWorkflowError as exc:
            st.error(str(exc))
            collections = []
        if not collections:
            st.caption("还没有导出文档。")
        else:
            collection = output_service.get_collection(
                st.selectbox(
                    "选择导出文档",
                    options=[c["id"] for c in collections],
                    format_func=lambda value: next(
                        f"{c['title']}（{c['item_count']} 条）"
                        for c in collections
                        if c["id"] == value
                    ),
                    key="collection_picker",
                )
            )
            st.markdown(f"**{collection['title']}**")
            if collection.get("description"):
                st.caption(str(collection["description"]))
            items = output_service.list_collection_items(collection["id"])
            if items:
                st.caption(
                    "条目："
                    + "；".join(
                        _collection_item_label(item) for item in items
                    )
                )
            with st.form("collection_item_form", clear_on_submit=True):
                st.markdown("**添加内容（选择范围）**")
                item_layer = st.selectbox(
                    "内容层",
                    options=["question", "family"],
                    format_func=lambda v: "题目" if v == "question" else "族",
                    key="collection_item_layer",
                )
                if item_layer == "question":
                    all_questions = question_service.list_question_items()
                    item_id = st.selectbox(
                        "选择题目",
                        options=[q.id for q in all_questions],
                        format_func=lambda value: question_picker_label(
                            next(q for q in all_questions if q.id == value)
                        ),
                        key="collection_item_question",
                    ) if all_questions else None
                else:
                    all_families = organization.list_families()
                    item_id = st.selectbox(
                        "选择族",
                        options=[f.id for f in all_families],
                        format_func=lambda value: family_picker_label(
                            next(f for f in all_families if f.id == value)
                        ),
                        key="collection_item_family",
                    ) if all_families else None
                do_add = st.form_submit_button("添加到导出文档")
            if do_add:
                if item_id is None:
                    st.error("没有可添加的内容。")
                else:
                    try:
                        output_service.add_collection_item(
                            collection["id"], item_layer=item_layer, item_id=item_id
                        )
                        st.success("已添加到导出文档。")
                        st.rerun()
                    except LearningWorkflowError as exc:
                        st.error(f"加入失败：{exc}")
            if items:
                if st.button("预览并导出", key="render_collection", type="primary"):
                    try:
                        rendered = MarkdownCollectionRenderer(database).render(
                            collection["id"]
                        )
                        st.text_area(
                            "Markdown 预览",
                            value=rendered.decode("utf-8"),
                            height=360,
                            key="collection_preview",
                        )
                        st.download_button(
                            "下载 Markdown",
                            data=rendered,
                            file_name=f"collection_{collection['id']}.md",
                            mime="text/markdown",
                            key="collection_download",
                        )
                        plain = PlainTextCollectionRenderer(database).render(
                            collection["id"]
                        )
                        st.text_area(
                            "TXT 预览",
                            value=plain.decode("utf-8-sig"),
                            height=360,
                            key="collection_plain_preview",
                        )
                        st.download_button(
                            "下载 TXT（记事本可读）",
                            data=plain,
                            file_name=f"collection_{collection['id']}.txt",
                            mime="text/plain",
                            key="collection_download_txt",
                        )
                    except LearningWorkflowError as exc:
                        st.error(f"渲染失败：{exc}")
