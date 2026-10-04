"""Central user-facing display labels for the learning workspace (v0.8.6).

Product rule (overnight real-corpus round, 2026-09-26): internal engineering
identifiers — database ids, English schema keys, raw enum values — must never
reach a student-facing surface.  Every page renders through the maps in this
module so the wording stays consistent:

- ``系统识别出的文字`` replaces developer slang (OCR) in user-visible copy;
  the internal columns/logs/tests keep using OCR.
- ``AI 视觉识别草稿`` / ``Agent 阅读结果`` are distinguishable sources.
- Questions are shown by their printed exam number (``第4题``) or a stem
  digest, never by ``#id``.

The module is pure data + small pure helpers: no I/O, no Streamlit, no AI.
"""

from __future__ import annotations

from src.learning_workflow_service import QuestionFamily, QuestionItem
from src.text_utils import ui_plaintext_digest

QUESTION_KIND_LABELS = {
    "error": "错题",
    "good": "好题",
    "typical": "典型题",
    "method": "方法题",
}

FAMILY_KIND_LABELS = {
    "type": "题型族",
    "method": "方法族",
    "conclusion": "二级结论",
}

VERDICT_LABELS = {
    "correct": "正确",
    "incorrect": "错误",
    "uncertain": "不确定",
    "none": "（未判定）",
}

QUESTION_STATUS_LABELS = {
    "draft": "草稿",
    "organized": "已整理",
    "archived": "已归档",
}

CONFIDENCE_LABELS = {
    "confirmed": "已确认",
    "probable": "较可能",
    "uncertain": "不确定",
}

RELATION_LABELS = {
    "member": "成员",
    "variant": "变式",
    "counterexample": "反例",
}

REVISION_KIND_LABELS = {
    "narrowed": "收窄",
    "broadened": "放宽",
    "corrected": "修正",
    "merged": "合并",
}

OUTCOME_LABELS = {
    "correct": "做对",
    "incorrect": "做错",
    "partial": "部分会",
}

# --------------------------------------------------------------- two wings
METHOD_TRIGGER_FIELD_LABELS = {
    "trigger_conditions": "看到这些条件",
    "recognition_signals": "哪些特征提醒你用这个方法",
    "candidate_method": "可以想到的方法",
    "selection_reason": "为什么选这个方法",
    "applicability_prerequisites": "用这个方法的前提",
    "application_to_current_question": "这道题具体怎么用",
    "similar_method_distinction": "容易和什么方法混淆",
}

BOUNDARY_COUNTEREXAMPLE_FIELD_LABELS = {
    "validity_conditions": "什么时候这个结论成立",
    "invalidation_conditions": "什么时候不能这样用",
    "boundary_cases": "边界情况",
    "counterexamples": "反例",
    "common_misuses": "常见误用",
    "confusing_conclusions": "容易混淆的结论",
    "condition_change_effect": "条件变化后结论怎么变",
}

WING_KIND_LABELS = {
    "method_trigger": "方法触发",
    "boundary_counterexample": "边界反例",
}

# ------------------------------------------------------- provenance wording
SOURCE_ORIGIN_LABELS = {
    "user_manual_text": "你校对过的文字",
    "stage2_visual_draft": "AI 视觉识别草稿",
    "agent_page_reading": "Agent 阅读结果（AI 生成，仅供参考）",
    "page_ocr_text": "系统识别出的文字",
    "page_extracted_text": "资料自带的文字",
    "none": "（无可用来源）",
}

PROVENANCE_LABELS = {
    "HANDWRITING_VISION": "AI 视觉识别草稿",
    "IMAGE_REGION": "AI 图表解析草稿",
    "DIAGRAM_INTERPRETATION": "AI 图形理解草稿",
    "TEXT_LAYER": "资料自带文字",
}

INTERPRETATION_STATUS_LABELS = {
    "draft": "草稿（待你确认）",
    "confirmed": "已确认",
    "archived": "已归档",
}

#: Legacy rows may carry raw enum tokens inside stored provenance strings
#: (e.g. "视觉草稿（HANDWRITING_VISION）" written by pre-central-label code).
#: Display surfaces humanize them instead of leaking internal enums.
_ENUM_DISPLAY_TOKENS = {
    "HANDWRITING_VISION": "手写识别",
    "IMAGE_REGION": "图表解析",
    "DIAGRAM_INTERPRETATION": "图形理解",
    "TEXT_LAYER": "文字层",
}


def humanize_provenance_text(text: str) -> str:
    """Replace raw provenance enum tokens in stored strings for display."""

    result = str(text or "")
    for token, label in _ENUM_DISPLAY_TOKENS.items():
        result = result.replace(token, label)
    return result

#: Final error-cause labels may never be these perfunctory words; they are
#: at most a visible phenomenon and must be traced to a concrete root cause.
BANNED_FINAL_REASON_TAGS = frozenset(
    {"粗心", "马虎", "粗心大意", "不仔细", "计算错误", "算错", "看错题"}
)

#: Phenomenon → honest root-cause placeholder shown to the user instead.
REASON_TAG_ROOT_CAUSE_PLACEHOLDER = "根因待确认（现象：计算出错）"

CONCRETE_REASON_TAG_SUGGESTIONS = (
    "知识不熟练",
    "概念混淆",
    "条件遗漏",
    "方法触发失败",
    "公式不熟",
    "代数变换不熟",
    "符号控制失败",
    "步骤组织不足",
    "边界条件遗漏",
    "看懂但不会迁移",
    "审题关键条件遗漏",
)


def normalize_reason_tags(tags: list[str]) -> list[str]:
    """Return tags with perfunctory final labels replaced by a root-cause ask.

    ``计算错误`` and friends may describe a phenomenon but never end the
    analysis; the normalized tag explicitly tells the user a root cause is
    still pending instead of pretending the mistake is explained.
    """

    normalized: list[str] = []
    for tag in tags:
        cleaned = str(tag).strip()
        if not cleaned:
            continue
        if cleaned in BANNED_FINAL_REASON_TAGS:
            placeholder = REASON_TAG_ROOT_CAUSE_PLACEHOLDER
            if placeholder not in normalized:
                normalized.append(placeholder)
            continue
        if cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


def stem_digest(stem_text: str, limit: int = 24) -> str:
    """Return a short human digest of a question stem.

    R5.1 (2026-09-27): the digest is the plain-UI rendering of the
    Markdown + LaTeX source — LaTeX becomes readable plain math
    (``$x^2$`` → ``x²``, ``\\frac{a}{b}`` → ``a/b``) and Markdown markers
    are stripped, so selectors and card titles never show source.  Only
    after conversion is the result truncated (convert-then-truncate).
    """

    cleaned = " ".join(str(stem_text or "").split())
    if not cleaned:
        return "（题干待补充）"
    return ui_plaintext_digest(cleaned, limit)


def question_display_title(question: QuestionItem) -> str:
    """User-facing title: printed exam number or stem digest — never ``#id``."""

    number = (question.question_number or "").strip()
    kind = QUESTION_KIND_LABELS.get(question.question_kind, question.question_kind)
    if number:
        return f"第{number}题 · {kind}"
    return f"{kind} · {stem_digest(question.stem_text)}"


def question_picker_label(question: QuestionItem) -> str:
    """Picker line: title + source snapshot, still without any internal id."""

    source = question.source_document_title_snapshot or ""
    page = question.source_page_label_snapshot or ""
    origin = "　".join(part for part in (source, page) if part)
    title = question_display_title(question)
    if origin:
        return f"{title}（{origin}）"
    return title


def family_display_title(family: QuestionFamily) -> str:
    """User-facing family title without the internal ``#id`` prefix."""

    kind = FAMILY_KIND_LABELS.get(family.family_kind, family.family_kind)
    return f"{kind}「{family.title}」（{family.member_count} 题）"


def family_picker_label(family: QuestionFamily) -> str:
    return family_display_title(family)
