"""F5 answer/rubric trust gate — evidence-based relation assessment.

Round-2 real case (doc173): a pure reference answer (23 choice letters +
essay answers, ZERO score marks) could silently pass as a scoring
standard.  This module is a conservative, EXPLAINABLE detector:

    evidence → conservative suggestion → warnings → USER DECIDES

Core principle: 「有答案」≠「有评分标准」.  The detector NEVER writes a
relation_kind — it only suggests; the user confirmation machinery in
``document_relation_service`` stays the only authority.  Rubric capability
degrades honestly (available / unknown / unavailable).

Anti-gaming guards (F-task §17):

* A bare 「（4分）」 is题目总分 evidence, NOT a rubric — score marks only
  support scoring_standard when paired with 细则-style wording
  (每点X分 / 答出X点得X分 / 评分细则 / 给分点 …) INSIDE the text.
* The filename/title is weak evidence only; a title claiming 「评分标准」
  without body evidence produces a WARNING, never a confident upgrade.
* Exam-paper features (注意事项/考试时间/考生…) → the text is a paper,
  not an answer document → unknown (never misclassified either way).
* OCR noise (4刀/6芬/每点?) matches nothing → low-confidence unknown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- evidence patterns -------------------------------------------------------

# Strong 细则 wording (must appear for any scoring_standard suggestion).
_SCORING_RULE_RE = re.compile(
    r"评分细则|评分标准|给分点|评分点|答出.{0,8}得\s*\d+\s*分|每点\s*\d+\s*分"
    r"|每空\s*\d+\s*分|每[空点]一分|酌情给分|答出任意.{0,6}\d+\s*点.{0,4}得"
)
# Bare score marks — weak alone (题目总分 looks identical).
_SCORE_MARK_RE = re.compile(r"[（(]\s*\d{1,2}\s*分\s*[）)]|共\s*\d{1,2}\s*分")
# Answer-shaped content.
_CHOICE_ANSWER_RE = re.compile(r"^\s*\d{1,2}\s*[.、．]\s*[A-D]\s*$", re.M)
_SUBQ_ANSWER_RE = re.compile(r"[（(]\s*\d\s*[）)]")
# Exam-PAPER features (the text is a paper, not an answer document).
_PAPER_FEATURE_RE = re.compile(
    r"注意事项|考试时间|考生须知|第[ⅠⅡ]卷|答题卡|本试卷分|参考公式"
)
# Title-side words (weak evidence, never sufficient alone).
_TITLE_SCORING_RE = re.compile(r"评分标准|评分细则")
_TITLE_ANSWER_RE = re.compile(r"答案")

_MIN_SCORE_MARKS_FOR_STANDARD = 3


@dataclass(slots=True)
class AnswerRelationAssessment:
    """A conservative PROPOSAL — the user always has the final say."""

    suggested_relation_kind: str  # reference_answer | scoring_standard | unknown
    confidence: str  # probable | uncertain | low
    evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rubric_capability: str = "unknown"  # available | unknown | unavailable
    reason: str = ""


def assess_answer_document(
    text: str,
    *,
    title: str = "",
    filename: str = "",
) -> AnswerRelationAssessment:
    """Assess whether an answer-ish document is a reference answer or a
    scoring standard.  Pure function; no DB, no AI, fully explainable."""

    body = text or ""
    title_line = title or filename or ""
    warnings: list[str] = []
    evidence: list[str] = []

    # --- paper guard: an exam paper is neither answer nor rubric --------
    paper_hits = len(_PAPER_FEATURE_RE.findall(body))
    if paper_hits >= 2:
        return AnswerRelationAssessment(
            suggested_relation_kind="unknown",
            confidence="uncertain",
            evidence=[f"命中试卷特征 {paper_hits} 处"],
            warnings=["文本更像试卷本身（注意事项/答题说明），不是答案或评分标准。"],
            rubric_capability="unavailable",
            reason="试卷特征明显，无法作为答案/评分关系依据。",
        )

    rule_hits = len(_SCORING_RULE_RE.findall(body))
    rule_words = sorted({m.group(0) for m in _SCORING_RULE_RE.finditer(body)})
    score_marks = len(_SCORE_MARK_RE.findall(body))
    choice_answers = len(_CHOICE_ANSWER_RE.findall(body))
    subq_answers = len(_SUBQ_ANSWER_RE.findall(body))
    answer_signals = choice_answers + (1 if subq_answers >= 3 else 0)
    title_says_scoring = bool(_TITLE_SCORING_RE.search(title_line))
    title_says_answer = bool(_TITLE_ANSWER_RE.search(title_line))

    if choice_answers:
        evidence.append(f"选择题答案行 {choice_answers} 条")
    if subq_answers:
        evidence.append(f"综合题小问答案标记 {subq_answers} 处")
    if rule_words:
        evidence.append("评分细则类用语：" + "、".join(rule_words[:4]))
    if score_marks:
        evidence.append(f"分值标记 {score_marks} 处")

    has_answer_content = answer_signals >= 1 or subq_answers >= 3
    # 细则词 + 分值标记必须成对出现才构成评分标准证据：
    # 只有细则词（哪怕来自标题行）而没有分值拆分 = 无评分细则；
    # 只有分值标记（哪怕很多）而没有细则词 = 题目总分，不是 rubric。
    scoring_body_evidence = rule_hits >= 1 and score_marks >= 2

    # --- scoring_standard: 细则词 REQUIRED, bare marks never enough -----
    if scoring_body_evidence:
        if title_says_answer and not title_says_scoring:
            # Case D: body has rules but title says only "答案" → conflict
            warnings.append(
                "标题写「答案」，但正文存在评分细则与分值拆分——标题与正文"
                "证据不一致，请核对后选择关系类型。"
            )
            return AnswerRelationAssessment(
                suggested_relation_kind="unknown",
                confidence="uncertain",
                evidence=evidence,
                warnings=warnings,
                rubric_capability="unknown",
                reason="标题与正文评分证据冲突，交由用户裁决。",
            )
        if title_says_scoring:
            return AnswerRelationAssessment(
                suggested_relation_kind="scoring_standard",
                confidence="probable",
                evidence=evidence,
                warnings=[
                    "仍需你在确认关系时人工核实：系统只提供证据建议，"
                    "不代替你判定评分口径。"
                ],
                rubric_capability="available",
                reason="正文含评分细则类用语且分值标记充足，标题一致。",
            )
        return AnswerRelationAssessment(
            suggested_relation_kind="scoring_standard",
            confidence="uncertain",
            evidence=evidence,
            warnings=["正文有评分细则但标题未声明，请人工核实。"],
            rubric_capability="available",
            reason="正文含评分细则类用语且分值标记充足（标题未声明）。",
        )

    # --- title says scoring but body lacks rule+mark pairing → Case C ---
    if title_says_scoring and not scoring_body_evidence:
        warnings.append(
            "标题与正文评分证据不一致：标题写「评分标准」，但正文未发现评分"
            "细则、给分点或分值拆分。不能仅凭标题按评分标准使用。"
        )

    # --- Case E guard: bare score marks are题目总分, never rubric --------
    if score_marks >= 3 and rule_hits == 0:
        warnings.append(
            f"检测到 {score_marks} 处分值标记，但均为题目/答案分值，"
            "未发现「每点X分 / 答出X点得X分 / 评分细则」类给分规则——"
            "这些分值不能当作评分点使用。"
        )

    # --- default: reference answer (has answer content, no rules) -------
    if has_answer_content:
        if rule_hits == 0:
            warnings.append(
                "检测到参考答案内容，但未发现足够的评分点或给分规则。"
                "这份材料更像参考答案，而非评分标准。"
            )
        rubric_capability = (
            "available"
            if (rule_hits >= 1 and score_marks >= 1)
            else "unknown"
        )
        # Case E reinforcement (real doc169 finding): a text full of bare
        # score marks with NO rule wording is likely the PAPER itself —
        # the system must not even call it a reference answer.
        if score_marks >= 3 and rule_hits == 0:
            return AnswerRelationAssessment(
                suggested_relation_kind="unknown",
                confidence="uncertain",
                evidence=evidence,
                warnings=warnings,
                rubric_capability="unknown",
                reason=(
                    "仅见题目分值而无给分规则：可能是试卷或未含细则的答案，"
                    "请人工核对。"
                ),
            )
        return AnswerRelationAssessment(
            suggested_relation_kind="reference_answer",
            confidence="probable" if rule_hits == 0 else "uncertain",
            evidence=evidence,
            warnings=warnings,
            rubric_capability=rubric_capability,
            reason="存在答案内容；评分能力按证据诚实降级。",
        )

    # --- fallback: some marks/rules but no answer shape ------------------
    return AnswerRelationAssessment(
        suggested_relation_kind="unknown",
        confidence="low",
        evidence=evidence,
        warnings=warnings
        or ["证据混杂，无法给出可靠的关系建议，请人工核对该文档。"],
        rubric_capability="unavailable",
        reason="证据混杂或 OCR 不稳定，交由用户裁决。",
    )
