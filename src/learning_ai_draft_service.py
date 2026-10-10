"""AI draft generation for the learning workflow (v0.8.6 overnight round).

Product semantics (user-ratified, 2026-09-26):

- The student must never face a blank form.  Once a question enters the
  learning store the AI produces *drafts* the user reviews, edits and
  confirms — the user's words always win (USER_CONFIRMED > USER_EDITED >
  AI_DRAFT > EMPTY).
- Drafts are stored with explicit ``ai_draft`` provenance and never
  overwrite user-edited fields; only empty fields are filled.
- Two hard pressure lines are enforced both in the prompt and by a
  post-generation lexical guard: drafts must stay inside the course/exam
  scope of the source material, and must never chase "depth" with
  competition tricks, exotic problems, or content real exams do not test.
- The runtime has no web-access capability.  Correction drafts are
  therefore always labelled 未完成联网核验 — the product never pretends an
  external verification happened.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Final

from src.ai.completion_stage import CompletionStage, completion_stage_scope
from src.learning_subject_policy import is_foreign_language_subject
from src.question_recognition_rules import MATH_NOTATION_RULES

LOGGER = logging.getLogger(__name__)

_SCOPE_GUARD_PROMPT: Final = (
    "【范围高压线——必须遵守】\n"
    "1. 只能使用与这道题来源材料相同课程、相同年级、真实考试范围内的知识；\n"
    "2. 绝对不能超纲；绝对不能为了显得有深度而生成偏题、怪题、冷门竞赛技巧、"
    "超难题或真实考试根本不考的内容；\n"
    + MATH_NOTATION_RULES
    + "3. 表达必须贴近这个课程阶段的规范书写习惯。\n"
    "4. 遇到化学内容时，不得输出 \\ce{...}；化学式、电荷、平衡常数、"
    "科学计数法和反应箭头只用标准 KaTeX 可支持的 LaTeX，并逐式检查"
    "下标、上标、电荷、系数、结晶水中点号与反应方向；无法从原图"
    "确认的有机结构式只指向原图并说明限制，不得猜造。\n"
    "5. 化学实验或流程题必须结合当前物质性质、酸碱性、溶解度、平衡或"
    "副反应解释操作原因，不得只输出「使反应完全」「低温有利」等通用套话。\n"
)

_VERDICTS: Final = ("correct", "incorrect", "uncertain", "none")
_REFERENCE_FINAL_NUMBER = re.compile(
    r"(?:本题\s*)?(?:最终\s*)?(?:结果|答案)\s*(?:为|是)\s*([+\-−]?\s*\d+(?:\.\d+)?)"
)
_AI_FINAL_NUMBER = re.compile(
    r"(?:正确答案|最终答案|本题结果|最终结果|答案)\s*"
    r"(?:应为|应该是|为|是)\s*([+\-−]?\s*\d+(?:\.\d+)?)"
)
_STUDENT_FINAL_NUMBER = re.compile(
    r"(?:(?:答案|结果)\s*(?:为|是)|(?:我|学生)?\s*算出来\s*(?:为|是)?)\s*"
    r"([+\-−]?\s*\d+(?:\.\d+)?)"
)


class LearningAIDraftError(RuntimeError):
    """AI draft generation failed or produced out-of-scope content."""

    def __init__(self, message: str, *, reference: dict | None = None) -> None:
        super().__init__(message)
        self.reference = reference


def _numeric_claims(pattern: re.Pattern[str], text: str) -> set[str]:
    """Return normalized explicit numeric answer claims from prose.

    This intentionally recognizes only phrases that explicitly identify a
    final answer.  Intermediate results such as ``(-3)^2 的结果是 9`` are not
    treated as competing final answers.
    """

    claims: set[str] = set()
    for match in pattern.findall(str(text or "")):
        value = str(match).replace("−", "-").replace(" ", "")
        try:
            claims.add(str(float(value)) if "." in value else str(int(value)))
        except ValueError:
            continue
    return claims


# ------------------------------------------------------------------ helpers
def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1 :]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    return cleaned.strip()


def _repair_review_json_transport(text: str) -> str:
    r"""Preserve one-pass AI prose and math through common JSON escaping slips.

    This is a transport-level JSON repair, not a rewrite of the formula or
    the review.  In particular, ``\times`` must not become JSON's tab escape
    and ``\neq`` must not become a newline.  Already escaped ``\\times`` and
    prose newlines outside ``$...$`` remain untouched.  An unescaped ASCII
    quotation mark followed by prose is escaped, while one followed by a
    JSON delimiter still closes the string.  No review claims are rewritten.
    """

    output: list[str] = []
    inside_string = False
    inside_math = False
    containers: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if not inside_string:
            output.append(char)
            if char in "{[":
                containers.append(char)
            elif char in "}]" and containers:
                containers.pop()
            elif char == '"':
                inside_string = True
                inside_math = False
            index += 1
            continue
        if char == "\\":
            end = index
            while end < len(text) and text[end] == "\\":
                end += 1
            slashes = text[index:end]
            following = text[end] if end < len(text) else ""
            unicode_escape = following == "u" and bool(
                re.fullmatch(r"[0-9a-fA-F]{4}", text[end + 1:end + 5])
            )
            if len(slashes) % 2 and following and not unicode_escape:
                # JSON newlines around a display equation are still newlines.
                # Only multi-letter commands such as \neq/\times collide with
                # JSON control escapes; a lone \n is not a LaTeX command.
                math_command = inside_math and bool(re.match(r"[A-Za-z]{2,}", text[end:]))
                if math_command or following not in '"\\/bfnrtu':
                    slashes += "\\"
            output.append(slashes)
            if following == '"' and len(slashes) % 2:
                output.append(following)
                index = end + 1
            else:
                index = end
            continue
        if char == '"':
            remainder = text[index + 1 :].lstrip()
            following = remainder[:1]
            container = containers[-1] if containers else ""
            closes_string = (
                (following == ":" and container == "{")
                or (following == "}" and container == "{")
                or (following == "]" and container == "[")
                or (
                    following == ","
                    and (
                        (
                            container == "{"
                            and bool(
                                re.match(r'\s*"(?:\\.|[^"\\])*"\s*:', remainder[1:])
                            )
                        )
                        or (
                            container == "["
                            and bool(
                                re.match(
                                    r'\s*(?:"|\{|\[|-?\d|true|false|null)',
                                    remainder[1:],
                                )
                            )
                        )
                    )
                )
            )
            if not closes_string:
                output.append('\\"')
            else:
                inside_string = False
                inside_math = False
                output.append(char)
            index += 1
            continue
        if char == "$":
            delimiter = "$$" if text[index : index + 2] == "$$" else "$"
            inside_math = not inside_math
            output.append(delimiter)
            index += len(delimiter)
            continue
        output.append(char)
        index += 1
    return "".join(output)


def _parse_json_object(text: str, *, math_strings: bool = False) -> dict:
    cleaned = _strip_code_fence(text)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LearningAIDraftError("AI 未返回有效的 JSON 草稿。")
    try:
        payload = cleaned[start : end + 1]
        value = json.loads(
            _repair_review_json_transport(payload) if math_strings else payload
        )
    except json.JSONDecodeError as exc:
        raise LearningAIDraftError(f"AI 草稿 JSON 解析失败：{exc}") from exc
    if not isinstance(value, dict):
        raise LearningAIDraftError("AI 草稿不是 JSON 对象。")
    return value


def explanation_review_quality_violation(data: dict, *, grade: str, subject: str) -> str | None:
    """Fail closed on malformed grade-7 math feedback without rewriting it."""

    if grade != "初一" or subject != "数学":
        return None
    for field in ("what_worked", "missing", "feedback", "improvements", "full_explanation"):
        value = data.get(field)
        parts = value if isinstance(value, list) else [value]
        for part in parts:
            text = _clean_str(part)
            if not text:
                continue
            if any(term in text for term in ("任意实数", "所有实数", "闭区间", "导数", "极限")):
                return f"{field} 使用了不适合初一的术语"
            if re.search(r"\[\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*\]", text):
                return f"{field} 使用了不适合初一的区间记号"
            if "&#x" in text or re.search(r"(?<=[0-9A-Za-z])\$\$(?=[A-Za-z])", text):
                return f"{field} 含损坏的数学格式"
            if text.count("$") % 2:
                return f"{field} 的数学公式分隔符不成对"
            outside_math = re.sub(r"\$\$.*?\$\$|\$.*?\$", "", text, flags=re.DOTALL)
            if re.search(r"\\[A-Za-z]+", outside_math):
                return f"{field} 的 LaTeX 命令没有放入数学公式"
    return None


#: Out-of-scope markers that must never appear in a recommended draft.
_SCOPE_VIOLATION_MARKERS: Final = (
    "竞赛",
    "奥赛",
    "联赛",
    "IMO",
    "CMO",
    "超纲",
)


def scope_guard_violation(text: str) -> str | None:
    """Return the first out-of-scope marker found, or None when clean."""

    lowered = str(text)
    for marker in _SCOPE_VIOLATION_MARKERS:
        if marker in lowered:
            return marker
    return None


#: Phrases that assert what the STUDENT did.  When no student answer is
#: recorded, a correction containing any of these is fabricated evidence —
#: the AI inventing user history (visual red team R2, §11 例1/例2: 作答空 +
#: 判定错误 + 订正声称「学生选了 B」).
_STUDENT_CLAIM_MARKERS: Final = (
    "你选",
    "学生选",
    "你答错",
    "你的错误",
    "你错在",
    "你填",
    "你写的是",
    "你的作答中",
)


def correction_fabrication_violation(text: str, *, has_student_answer: bool) -> str | None:
    """Return the first fabricated student-claim marker, or None.

    Only meaningful when ``has_student_answer`` is False: with no recorded
    answer the AI has NO basis to say what the student chose or did wrong,
    so second-person/student claims in a correction draft are treated as
    fabricated user history and refused — the same hard-line style as the
    scope guard.
    """

    if has_student_answer:
        return None
    lowered = str(text)
    for marker in _STUDENT_CLAIM_MARKERS:
        if marker in lowered:
            return marker
    return None


#: Phrases that assert textbook/exam authority (G2-B §40).  An AI draft
#: claiming 「教材指出」「高考常考」 without such evidence in the user's
#: materials is fabricating a source of authority — refused at generation
#: time, exactly like the scope guard.  User-typed content is never
#: blocked by this guard: it applies to AI output only.
_AUTHORITY_CLAIM_MARKERS: Final = (
    "教材指出",
    "课本指出",
    "教材明确",
    "高考常考",
    "高考真题",
    "历年高考",
    "考试大纲规定",
    "课程标准要求",
)


def boundary_authority_violation(text: str) -> str | None:
    """Return the first fabricated authority marker, or None (G2-B §40)."""

    lowered = str(text)
    for marker in _AUTHORITY_CLAIM_MARKERS:
        if marker in lowered:
            return marker
    return None


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _clean_str(value: object) -> str:
    return str(value).strip() if value is not None else ""


def first_pass_quality_violation(
    data: dict, *, grade: str, subject: str, source_text: str = "", reference_only: bool = False,
) -> str | None:
    """Reject a deficient first draft; never silently rewrite it into a pass.

    These checks intentionally target the reviewed Grade-7 math workflow.
    Other curricula need their own age-appropriate acceptance rules.
    """

    if grade != "初一" or subject != "数学":
        return None
    exact_steps_to_one = bool(
        re.search(r"恰好.{0,12}\d+\s*步.{0,12}(?:到|得)到?\s*1", source_text)
        and not re.search(r"首(?:次|回)|第一次", source_text)
    )
    if exact_steps_to_one and not reference_only:
        correction = _clean_str(data.get("correction"))
        analysis_for_steps = _clean_str(data.get("analysis"))
        if not all(part in correction for part in ("4", "6")) or not any(
            part in correction for part in ("待老师", "待核实", "待确认")
        ):
            return "原题未说明首次到达，订正必须并列两种结果并注明待核实"
        if re.search(r"(?:最终|综上|所以|因此).{0,35}选\s*[A-D]", analysis_for_steps):
            return "解析承认题意歧义却仍强行给出单一选项"
        if any("隐含" in tag and "首次" in tag for tag in _as_list(data.get("reason_tags"))):
            return "错因把原题未写的首次到达当成隐含条件"
    if data.get("secondary_conclusion") is not None:
        return "第一层首稿不应生成尚未核验的二级结论"
    analysis = _clean_str(data.get("analysis"))
    if len(analysis) < 65:
        return "解析不足以展示逐步推理（少于 65 字）"
    reasons = _as_list(data.get("reason_tags"))
    if not reasons and not reference_only:
        return "缺少具体错因"
    generic = ("粗心", "马虎", "大意", "漏看", "计算错误", "知识点掌握不牢固")
    for reason in reasons:
        if any(word in reason for word in generic):
            return f"错因仍是笼统或未经证实的归因：{reason}"
    if len(_clean_str(data.get("solution_method"))) < 12:
        return "方法没有写出可操作的步骤"
    if not _as_list(data.get("method_tags")):
        return "缺少题型标签"
    for field in ("stem", "correction", "analysis", "solution_method"):
        value = _clean_str(data.get(field))
        if "闭区间" in value:
            return f"{field} 使用了不适合初一的闭区间表述"
        if "\\cdots\\cdots" in value:
            return f"{field} 把带余除法写成了不规范的省略号"
        without_display = re.sub(r"\$\$[^$]+\$\$", "", value, flags=re.DOTALL)
        if value.count("$") % 2 or re.search(r"(?<=[0-9A-Za-z])\$\$", without_display):
            return f"{field} 存在损坏的数学公式分隔符"
        blocks = re.findall(r"\$\$.*?\$\$|\$[^$]*\$", value, flags=re.DOTALL)
        math_spans = [block[2:-2] if block.startswith("$$") else block[1:-1] for block in blocks]
        if any(re.search(r"\\\\(?=[A-Za-z])", span) for span in math_spans):
            return f"{field} 的 LaTeX 命令多写了反斜杠"
        if any(re.match(r"\s*(?:\\(?:le|ge|leq|geq)|[<>≤≥])", span) for span in math_spans):
            return f"{field} 的数学式缺少比较符号左边的对象"
        if any(re.search(r"(?<=[A-Za-z0-9|})])/(?=[A-Za-z0-9|({])", span) for span in math_spans):
            return f"{field} 的分数仍用斜杠而非上下分数线"
        outside_math = re.sub(r"\$\$.*?\$\$|\$[^$]*\$", "", value, flags=re.DOTALL)
        bare_math = (
            r"[|∣][A-Za-z][|∣]|\b[A-Za-z]\s*[+−*/=<>≤≥]\s*[A-Za-z0-9]"
            r"|\b[0-9]+[A-Za-z]\b"
        )
        if re.search(bare_math, outside_math):
            return f"{field} 仍有未用数学格式包裹的字母或算式"
    return None


def _question_with_context(question, fallback: str = "", limit: int = 6000) -> str:
    """Compose an atomic prompt with its composite ancestors, without copying."""

    shared = _clean_str(getattr(question, "shared_context", ""))
    local = _clean_str(getattr(question, "stem_text", "")) or _clean_str(fallback)
    if shared and local:
        return f"【父题共享上下文】\n{shared}\n\n【当前叶子小问】\n{local}"[:limit]
    return (shared or local)[:limit]


def student_answer_gate_state(question) -> dict[str, object]:
    """§24 provenance gate for the "我的作答" auto-transcription.

    ``student_answer`` means *what the student actually wrote by hand*.  It
    may only be auto-transcribed when the handwriting provenance chain is
    verifiable: either the model itself declared handwriting ``confirmed``
    (fix round §18 gate), or the user saved/confirmed the reading
    themselves.  AI derivations, chart descriptions, printed stems, OCR
    output and model reasoning must never become the student's answer —
    that exact path carried a hallucinated handwriting draft into a
    printed-only page's student_answer (human review 2026-09-27).
    """

    draft = question.ai_draft if isinstance(question.ai_draft, dict) else {}
    user_confirmed = bool(draft.get("user_confirmed"))
    presence = draft.get("handwriting_presence")
    gate_open = user_confirmed or presence == "confirmed"
    return {
        "gate_open": gate_open,
        "user_confirmed": user_confirmed,
        "handwriting_presence": presence or "undeclared",
    }


class LearningAIDraftService:
    """Generate first-layer / wing / correction / explanation-review drafts."""

    def __init__(self, provider) -> None:
        self._provider = provider

    # ------------------------------------------------------------ internals
    def _complete(
        self, prompt: str, *, target_refs: tuple[str, ...], max_tokens: int = 2048,
        image_data: str | None = None,
    ) -> str:
        # Learning drafts are bounded structured-JSON requests: the stage
        # scope lets the adapter disable thinking and raise the transport
        # timeout floor (V086-203 family fix, overnight round 2026-09-27).
        with completion_stage_scope(CompletionStage.LEARNING_DRAFT):
            options = {"max_completion_tokens": max_tokens,
                       "source_feature": "learning_ai_draft", "target_refs": target_refs}
            result = (
                self._provider.complete_vision(prompt, image_data, json_output=True, **options)
                if image_data else self._provider.complete(prompt, **options)
            )
        return getattr(result, "text", "") or ""

    def generate_question_reference(
        self, question, *, learner_profile=None, image_data: str | None = None,
    ) -> dict:
        """Generate four editable reference fields once, without judging the student."""

        if is_foreign_language_subject(getattr(question, "subject", "")):
            return self._language_drafts(question, reference_only=True, image_data=image_data)
        source = _question_with_context(question)
        if not source:
            raise LearningAIDraftError("本题题干为空，无法生成参考版。")
        basic = getattr(learner_profile, "basic", None)
        grade = _clean_str(getattr(basic, "grade", ""))
        stage = _clean_str(getattr(basic, "stage", ""))
        subject = _clean_str(getattr(question, "subject", ""))
        grade_rules = (
            "本题按初一数学解释：图形在数轴上的滚动，只列顶点的数、前几次位置和周期。"
            "不要推测旋转角度，不引入三角函数、旋转矩阵、投影坐标或同余记号。\n"
            if grade == "初一" and subject == "数学" else ""
        )
        prompt = (
            "为刚加入学习整理的一道独立小题生成参考版，只处理当前小题。"
            "这是正确解法与解题方法的参考，不是对学生作答的判定。"
            "不得编造学生选了什么、写了什么、错因或批改结论；"
            "不修改题干、学生作答或已保存的人工内容。\n"
            f"学习范围：{stage}{grade or '按原题课程范围'}；学科：{subject}。\n"
            f"{grade_rules}"
            f"【已保存题干及公共条件】\n{source}\n"
            + ("附图是本题当前关联截图，可能包括题干图、公共图和选项图。"
               "必须直接读图核对数轴、标签和几何关系，不能把手写标记当作印刷条件。\n"
               if image_data else "")
            + "只返回 JSON：{\"correction\":\"正确结果与关键步骤\","
            "\"analysis\":\"从条件开始逐步解释、计算并核对结论的完整解析\","
            "\"method_tags\":[\"具体题型\"],"
            "\"solution_method\":\"同类题下次可照做的解题步骤\","
            "\"type_family\":{\"title\":\"题型族名\",\"description\":\"共同特征\"},"
            "\"method_families\":[{\"title\":\"方法族名\",\"description\":\"方法要点\"}],"
            "\"secondary_conclusion\":null}。前四个字段必须完整，"
            "correction 无论是否已判定错误都要给参考解法，不能返回空字符串。"
            "题型与方法分开；不生成错因。题意或图像不清楚时明确标注待核对，"
            "不得猜答案。恰好走 N 步若没有首次到达的条件，不得偷换成首次到达；"
            "不同理解产生不同答案时分别说明并注明待老师核实。\n"
            "只给最后整理好的参考版，不输出内部试算、反复推翻的过程。"
            "订正简述结论和关键算式（约160字以内），解析分3到6步，"
            "解释每步依据，题型1到3项，方法列出可复用步骤。"
            "无法确认时直说待核对，并说明缺少什么；不猜唯一答案。"
            "没有联网或标准答案核验，禁止声称查证了教材、权威解法或标准答案。\n"
            + MATH_NOTATION_RULES + _SCOPE_GUARD_PROMPT
        )
        raw = self._complete(
            prompt, target_refs=(f"question:{question.id}",), max_tokens=8192,
            image_data=image_data,
        )
        data = _parse_json_object(raw, math_strings=True)
        original_reference = dict(data)
        from src.math_display import normalize_question_math

        for field in ("correction", "analysis", "solution_method"):
            data[field] = normalize_question_math(_clean_str(data.get(field)))
        data["method_tags"] = [normalize_question_math(tag)
                               for tag in _as_list(data.get("method_tags"))]
        required = ("correction", "analysis", "method_tags", "solution_method")
        if any(not data[field] for field in required):
            raise LearningAIDraftError(
                "AI 参考版不完整，本次未写入，请核对题干和模型配置。",
                reference=original_reference,
            )
        violation = scope_guard_violation(json.dumps(data, ensure_ascii=False))
        # Check reference claims independently of any existing student answer.
        fabricated = correction_fabrication_violation(
            data["correction"] + data["analysis"], has_student_answer=False,
        )
        quality = first_pass_quality_violation(
            {**data, "stem": "", "secondary_conclusion": None},
            grade=grade, subject=subject, source_text=source, reference_only=True,
        )
        if violation or fabricated or quality:
            raise LearningAIDraftError(
                f"AI 参考版待核对，本次未写入：{violation or fabricated or quality}",
                reference=original_reference,
            )
        data["original_reference"] = original_reference
        data["secondary_conclusion"] = None
        data["reason_tags"] = []
        return data

    # ---------------------------------------------------------- first layer
    def generate_question_drafts(self, question, *, learner_profile=None) -> dict:
        """Draft stem/answer/verdict/reasons/methods plus layer-2 suggestions.

        The caller decides what to fill: only empty, never user-edited.
        ``correction`` is always empty when the verdict is not 错误.
        """

        if is_foreign_language_subject(getattr(question, "subject", "")):
            return self._language_drafts(question, reference_only=False)
        source_content = ""
        if isinstance(question.ai_draft, dict):
            source_content = _clean_str(question.ai_draft.get("content"))
        source_content = _question_with_context(question, source_content, 6000)
        if not source_content:
            raise LearningAIDraftError("这道题还没有可用的来源内容，无法生成草稿。")
        if re.search(r"起点为\s*O(?=[且，。])", source_content):
            raise LearningAIDraftError(
                "来源文字的起点字符可能把数字 0 识别成字母 O；"
                "仅凭 OCR 无法核实原题，本次未调用 AI、未生成草稿。"
            )

        # §24 gate: only a confirmed (or user-saved) handwriting chain may
        # be transcribed into student_answer.  When the gate is closed the
        # field is removed from the requested schema AND forced empty after
        # parsing, so a model that ignores instructions cannot leak a
        # derivation into "我的作答".
        gate = student_answer_gate_state(question)
        answer_field_spec = (
            '  "student_answer": "只转录原始内容中明确属于学生手写的作答'
            '（AI 的推导、图表解析、印刷题干、系统识别文字都不是学生作答，'
            '不要写进来）；没有就给空字符串",\n'
            if gate["gate_open"]
            else ""
        )
        basic = getattr(learner_profile, "basic", None)
        grade = _clean_str(getattr(basic, "grade", ""))
        stage = _clean_str(getattr(basic, "stage", ""))
        subject = _clean_str(getattr(question, "subject", ""))
        learner_context = (
            f"【学生学习范围】{stage}{grade}；学科：{subject or '按原题判断'}。"
            "只用该年级已经学过的表达，不使用更高学段术语。\n"
            if grade and stage
            else "【学生学习范围】未提供年级；只用原题所需的基础知识，不臆测高阶知识。\n"
        )
        known_verdict = _clean_str(getattr(question, "teacher_verdict", ""))
        first_layer_scope = (
            "【本轮仅做第一层整理】secondary_conclusion 必须为 null。"
            "不要在这一层推测图形转动角度、写未经原图验证的二级定理。\n"
            if grade == "初一" and subject == "数学"
            else ""
        )
        prompt = (
            "你是学习整理助手。下面是从用户资料里提取的一道题的原始内容"
            "（可能包含题干、手写作答、批改痕迹、多道题混在一页的情况）。\n\n"
            f"{learner_context}"
            f"{first_layer_scope}"
            f"【已保存的判定】{known_verdict or '未判定'}；不能把错误题解释成只是粗心。\n\n"
            "【原始内容】\n"
            f"{source_content[:6000]}\n\n"
            "请只根据原始内容，输出一个 JSON 对象（不要输出其它文字），字段：\n"
            "{\n"
            '  "stem": "题干原文（只保留这一道题的题干，不要把别的题混进来；"\n'
            '          "若无法区分，取最完整的主体部分）",\n'
            f"{answer_field_spec}"
            '  "verdict": "correct|incorrect|uncertain|none（根据批改痕迹判定；"\n'
            '          "看不出就 none）",\n'
            '  "correction": "仅当 verdict 是 incorrect 时给正确结论和关键步骤。若原始内容里'
            '没有学生自己的作答/错误痕迹，只能写「参考解析：正确解法与答案」，'
            '绝不能编造学生选了什么选项、错在哪一步；否则给空字符串",\n'
            '  "analysis": "给该年级学生看的详细逐步解析：从条件开始，逐步列式、计算、'
            '解释每一步为什么成立，最后核对结论；'
            '原题或答案有歧义时列明不同理解并标注待老师确认，不能强行选答案",\n'
            '  "reason_tags": ["具体不会的知识或方法，例如 找不到二阶差分规律、'
            '不会画树状图逆推；没有作答证据时不得编造学生漏看了什么。'
            '禁止 粗心、马虎、计算错误、漏看、知识点掌握不牢固 等笼统词"],\n'
            '  "method_tags": ["题型名称数组，描述题目类型，不能冒充解题方法"],\n'
            '  "solution_method": "可操作的解题方法与步骤，与题型分开",\n'
            '  "type_family": {"title": "题型族名（按核心考点+条件结构命名，"\n'
            '          "不超过 16 字）", "description": "这类题的共同特征"},\n'
            '  "method_families": [{"title": "方法族名（不超过 16 字）",\n'
            '          "description": "方法要点"}],\n'
            '  "secondary_conclusion": {"title": "二级结论名", "description": "结论内容",\n'
            '          "derivation": "常见推导方法：用 ∵/∴ 书写，说明用了什么课本知识、'
            '关键变换在哪里"} 或 null\n'
            "}\n"
            "注意：不是每道题都有二级结论，没有把握就给 null，不要硬造。\n"
            "【首稿验收要求】这是一次生成，不依赖用户事后替你补全。\n"
            "1. 题干与原题逐字核对，唯一允许的形式变化是给数学内容加规范公式标记；"
            "题干里的算式也必须是 $...$，不能原样照抄 OCR 的普通符号。"
            "O 与 0、字母与数字看不清时标记待核验，不能猜。"
            "选择题题干末尾换行，A、B、C、D 每个选项单独一行。\n"
            "2. 订正只放结论和关键步骤；解析单列，必须让该年级学生能跟上每一步，"
            "不能只给一句答案或跳过中间推理。\n"
            "3. 错因写本题具体未掌握的知识或方法，不能写粗心、漏看、计算错误，"
            "不能凭空声称学生做过什么；题型是题目类别，方法是下次可照做的步骤。\n"
            "4. 所有数学式、字母变量、绝对值、分数、指数、根号、不等式都写在"
            "单个 $...$ 内，用规范 KaTeX。分数用 \\dfrac{分子}{分母}，"
            "禁止 a/b 和除号替代分数线；不得出现破损的 $ 分隔符。\n"
            "5. 对初一数学，用 $-3\\le x\\le 1$ 这类已学表达，"
            "不要写闭区间等高中术语。\n"
            "6. '恰好走 N 步到达' 若没有'首次'，不能偷换成首次到达；"
            "如两种解读答案不同，订正和解析都要分别算清两种结果，"
            "末尾写待老师核实，不得凭猜测认定出题意图或强行选一个选项；"
            "错因也不能说首次到达是隐含条件。\n"
            "7. 带余除法写为 $n=3q+r$ 这样的等式，不能用连续省略号代替余数。\n"
            "归族要克制：核心考点相同、条件结构相似就归为同一族，"
            "不要因为细小差异拆出碎片族。\n"
            + _SCOPE_GUARD_PROMPT
        )
        raw = self._complete(
            prompt, target_refs=(f"question:{question.id}",), max_tokens=4096
        )
        violation = scope_guard_violation(raw)
        if violation:
            raise LearningAIDraftError(
                f"AI 草稿包含疑似超纲内容（命中「{violation}」），已拒绝写入。"
            )
        data = _parse_json_object(raw)
        quality = first_pass_quality_violation(
            data, grade=grade, subject=subject, source_text=source_content
        )
        if quality:
            raise LearningAIDraftError(
                f"AI 首次生成未达到初一数学整理要求：{quality}；本次草稿未写入。"
            )
        verdict = _clean_str(data.get("verdict")) or "none"
        if verdict not in _VERDICTS:
            verdict = "none"
        if known_verdict in _VERDICTS and known_verdict != "none":
            verdict = known_verdict
        correction = _clean_str(data.get("correction"))
        if verdict != "incorrect":
            correction = ""
        # §P trust guard: with no recorded student answer, a correction that
        # claims what the student chose/did wrong is fabricated user history.
        has_answer = bool(_clean_str(question.student_answer)) or (
            bool(gate["gate_open"]) and bool(_clean_str(data.get("student_answer")))
        )
        if not has_answer:
            for reason in _as_list(data.get("reason_tags")):
                if any(
                    claim in reason
                    for claim in ("未正确", "错把", "误把", "做错", "算错", "错误", "失误")
                ):
                    raise LearningAIDraftError(
                        f"AI 错因声称学生已发生具体错误（「{reason}」），"
                        "但没有作答证据；本次草稿未写入。"
                    )
        fabrication = correction_fabrication_violation(
            correction, has_student_answer=has_answer
        )
        if fabrication:
            raise LearningAIDraftError(
                f"AI 订正草稿试图编造学生的作答（命中「{fabrication}」而本题没有"
                "作答记录），已拒绝写入。请先补上你的作答再生成订正。"
            )
        type_family = data.get("type_family") if isinstance(data.get("type_family"), dict) else None
        secondary = (
            data.get("secondary_conclusion")
            if isinstance(data.get("secondary_conclusion"), dict)
            else None
        )
        transcribed_answer = (
            _clean_str(data.get("student_answer")) if gate["gate_open"] else ""
        )
        return {
            "stem": _clean_str(data.get("stem")),
            "student_answer": transcribed_answer,
            "student_answer_gate": "open" if gate["gate_open"] else "closed",
            "verdict": verdict,
            "correction": correction,
            "analysis": _clean_str(data.get("analysis")),
            "reason_tags": _as_list(data.get("reason_tags")),
            "method_tags": _as_list(data.get("method_tags")),
            "solution_method": _clean_str(data.get("solution_method")),
            "type_family": {
                "title": _clean_str(type_family.get("title")) if type_family else "",
                "description": _clean_str(type_family.get("description")) if type_family else "",
            },
            "method_families": [
                {
                    "title": _clean_str(item.get("title")),
                    "description": _clean_str(item.get("description")),
                }
                for item in (data.get("method_families") or [])
                if isinstance(item, dict) and _clean_str(item.get("title"))
            ],
            "secondary_conclusion": (
                {
                    "title": _clean_str(secondary.get("title")),
                    "description": _clean_str(secondary.get("description")),
                    "derivation": _clean_str(secondary.get("derivation")),
                }
                if secondary and _clean_str(secondary.get("title"))
                else None
            ),
        }

    # ------------------------------------------------------------ correction
    def generate_correction_draft(self, question) -> str:
        """Correction draft for a wrong answer; honestly NOT web-verified."""

        if question.teacher_verdict != "incorrect":
            raise LearningAIDraftError("这道题没有判定为错误，不需要生成订正。")
        if is_foreign_language_subject(getattr(question, "subject", "")):
            return self._language_drafts(question, reference_only=True)["correction"]
        source_content = ""
        if isinstance(question.ai_draft, dict):
            source_content = _clean_str(question.ai_draft.get("content"))
        has_answer = bool(_clean_str(question.student_answer))
        answer_block = question.student_answer if has_answer else "（未记录作答）"
        no_answer_rule = (
            "【学生作答】是（未记录作答）时的硬性规定：绝对不要假设或编造"
            "学生选了什么选项、写错了哪一步；整份订正按「参考解析」来写"
            "（给出正确解法与答案），并明确写一句「没有你的作答记录，"
            "以下是参考解析」。\n"
            if not has_answer
            else ""
        )
        prompt = (
            "你是学习整理助手。这道题被判定为做错，请给出订正草稿：\n\n"
            f"【题干】\n{_question_with_context(question, source_content, 3000)}\n\n"
            f"【学生作答】\n{answer_block}\n\n"
            f"{no_answer_rule}"
            "要求：先定位错误点（错在哪一步、缺了什么条件），再给出正确解法，"
            "按学生能跟着一步步看的写法。\n"
            "注意：你没有联网检索能力，不要声称答案经过网络核验。\n"
            + _SCOPE_GUARD_PROMPT
        )
        raw = self._complete(prompt, target_refs=(f"question:{question.id}",))
        violation = scope_guard_violation(raw)
        if violation:
            raise LearningAIDraftError(
                f"订正草稿包含疑似超纲内容（命中「{violation}」），已拒绝写入。"
            )
        cleaned = _strip_code_fence(raw)
        fabrication = correction_fabrication_violation(
            cleaned, has_student_answer=has_answer
        )
        if fabrication:
            raise LearningAIDraftError(
                f"AI 订正草稿试图编造学生的作答（命中「{fabrication}」而本题没有"
                "作答记录），已拒绝写入。请先补上你的作答再生成订正。"
            )
        return cleaned

    # ----------------------------------------------------------------- wings
    def generate_wing_draft(self, question, wing_kind: str) -> dict:
        """Draft one wing for a question; fields keep the service schema keys."""

        if is_foreign_language_subject(getattr(question, "subject", "")):
            raise LearningAIDraftError("外语学科只做第一层整理，不生成两翼。")
        if wing_kind not in ("method_trigger", "boundary_counterexample"):
            raise LearningAIDraftError("翼类型无效。")
        source_content = ""
        if isinstance(question.ai_draft, dict):
            source_content = _clean_str(question.ai_draft.get("content"))
        stem = _question_with_context(question, source_content, 3000)
        method_hint = "、".join(question.method_tags) or "（尚未标注方法）"
        if wing_kind == "method_trigger":
            field_spec = (
                '{\n'
                '  "trigger_conditions": ["看到什么条件/特征时想到这个方法，逐条"],\n'
                '  "recognition_signals": ["题面里提醒你用这个方法的信号词/结构，逐条"],\n'
                '  "candidate_method": "可以想到的方法名",\n'
                '  "selection_reason": "为什么这道题选这个方法",\n'
                '  "applicability_prerequisites": ["使用这个方法的前提条件，逐条"],\n'
                '  "application_to_current_question": "这道题具体怎么用（落到本题的步骤要点）",\n'
                '  "similar_method_distinction": "容易和什么方法混淆、怎么区分"\n'
                '}'
            )
            semantic = (
                "左翼（方法触发）训练：看到什么条件 → 想到什么方法 → 为什么 → 如何落到当前题。"
            )
        else:
            field_spec = (
                '{\n'
                '  "validity_conditions": ["什么时候这个结论成立，逐条；'
                '写明具体前提（区域/尺度/季节/数据口径）"],\n'
                '  "invalidation_conditions": ["什么时候不能这样用，逐条"],\n'
                '  "boundary_cases": ["边界情况，逐条"],\n'
                '  "counterexamples": ["反例（可以用文字描述），逐条"],\n'
                '  "common_misuses": ["常见误用，逐条"],\n'
                '  "confusing_conclusions": ["容易混淆的结论，逐条"],\n'
                '  "condition_change_effect": "条件变化后结论怎么变"\n'
                '}'
            )
            semantic = (
                "右翼（边界反例）训练：什么时候不能用 → 缺少什么条件 → 常见误用 → "
                "反例/边界 → 条件变化后的结论。\n"
                "硬要求：成立条件必须具体（区域/尺度/季节/前提/数据口径），"
                "不要把『一般』写成『必然』；结论只来自本题材料时，"
                "写明『根据本题材料』，不要升级成普遍地理事实；"
                "没有把握的反例给空数组——允许留空，绝不硬编，"
                "绝不能假借教材或高考的名义（没有教材证据就不要写『教材指出』）。"
            )
        prompt = (
            "你是学习整理助手。请针对下面这道题，为它生成一翼的学习草稿。\n\n"
            f"【题目】\n{stem}\n\n【本题已标注的方法】{method_hint}\n\n"
            f"{semantic}\n"
            "只输出一个 JSON 对象（不要输出其它文字），字段如下（每一项都紧扣本题，"
            "不要空话；证据不足的字段给空字符串或空数组，不要编造）：\n"
            f"{field_spec}\n"
            + _SCOPE_GUARD_PROMPT
        )
        raw = self._complete(prompt, target_refs=(f"question:{question.id}",))
        violation = scope_guard_violation(raw)
        if violation:
            raise LearningAIDraftError(
                f"两翼草稿包含疑似超纲内容（命中「{violation}」），已拒绝写入。"
            )
        data = _parse_json_object(raw)
        if wing_kind == "boundary_counterexample":
            # G2-B §40: an AI boundary draft must never pose as textbook or
            # exam authority — no such evidence exists in the user's
            # materials, so the claim itself is fabricated provenance.
            serialized = json.dumps(data, ensure_ascii=False)
            authority = boundary_authority_violation(serialized)
            if authority:
                raise LearningAIDraftError(
                    f"边界反例草稿试图假借权威出处（命中「{authority}」"
                    "而资料里没有这类证据），已拒绝写入。"
                )
        fields: dict[str, object] = {}
        for key, value in data.items():
            if isinstance(value, list):
                cleaned_list = [str(item).strip() for item in value if str(item).strip()]
                if cleaned_list:
                    fields[key] = cleaned_list
            else:
                cleaned = _clean_str(value)
                if cleaned:
                    fields[key] = cleaned
        return fields

    # ----------------------------------------------------- self-explanation
    #: The student speaks first.  Only after their attempt has been saved may
    #: the AI assess it and offer a reference explanation grounded in the
    #: saved correction.  Never fabricate an answer when that basis is absent.
    def review_explanation_task(
        self,
        question,
        content: str,
        *,
        learner_stage: str = "",
        learner_grade: str = "",
    ) -> dict:
        """Review one EXPLAIN_BACK task; returns verdict + specific feedback.

        Verdict: ``gaps``（讲解有缺口）/ ``basically_clear``（基本讲清）/
        ``complete``（条件完整）。The student's words are audited, never
        rewritten into a pass.
        """

        if is_foreign_language_subject(getattr(question, "subject", "")):
            return self._review_language_reasoning(question, content)
        if not content.strip():
            raise LearningAIDraftError("自我讲解内容为空。")
        source_content = ""
        if isinstance(question.ai_draft, dict):
            source_content = _clean_str(question.ai_draft.get("content"))
        reviewed_correction = _clean_str(getattr(question, "correction_note", ""))
        saved_analysis = _clean_str(getattr(question, "analysis_note", ""))
        correction_block = (
            "【已保存订正（可能仍需人工核对）】\n"
            f"{reviewed_correction[:3000]}\n"
            "这段订正是本轮审核的优先参考，但不能冒称官方答案。不得自行改写其中的最终答案；"
            "若你认为它与题干冲突，只能说明无法确认并停止结论，不得另造答案。\n\n"
            if reviewed_correction
            else "【已保存订正】\n未提供。不得猜测唯一答案，也不得写完整参考讲解。\n\n"
        )
        prompt = (
            "你是学习审核助手。学生试着用自己的话讲解下面这道题为什么这样做。"
            "请审核这份讲解（不是润色），逐项检查：\n"
            "1) 有没有说清「为什么想到这个方法」；2) 核心条件是否说全"
            "（只检查本题真正涉及的条件，不套用其他学科的条件）；"
            "3) 关键步骤有没有跳步；4) 结论是否说清；"
            "5) 若题目涉及方法适用前提，再检查边界；无此要求不能据此扣分；"
            "6) 有没有概念混淆或因果倒置；7) 是不是只是复述答案。\n\n"
            f"【学生当前范围】{learner_stage or '未配置学段'} / "
            f"{learner_grade or '未配置年级'} / "
            f"{_clean_str(getattr(question, 'subject', '')) or '未核对学科'}\n"
            f"【题目】\n{_question_with_context(question, source_content, 2500)}\n\n"
            f"{correction_block}"
            f"【已保存解析（可能仍需人工核对）】\n{saved_analysis[:4000] or '未提供'}\n\n"
            f"【学生的讲解】\n{content[:3000]}\n\n"
            "反馈硬要求：\n"
            "- 禁止空话：不要出现「讲得很好」「继续加油」这类没有信息的评价；\n"
            "- 缺什么必须点名：例如「你说了要比较人口密度，但没有说明"
            "为什么人口总量不能直接用于不同面积的区域比较」；\n"
            "- 学生提交之后，先逐点审核，再给出修改意见；\n"
            "- 这是初一学生口头讲题，不是书面满分证明：如果已用自己的话讲清"
            "关键关系、主要步骤和正确结论，即使未逐行写算式或未说专业术语，"
            "也至少应判 basically_clear；把这些表达改进写进 improvements，"
            "不能仅因此判 gaps。只有数学错误、缺失不可省略的推理分支、"
            "或核心步骤确实无法复现时才判 gaps；\n"
            "- 学生当场自行纠正的口误，要按纠正后的意思审核；不能凭一次口误"
            "推断其知识点不会或掌握不牢；也不能把真正不会轻描淡写成漏看、粗心；\n"
            "- 若已保存订正足以支撑结论，再给出适合当前年级的完整、通顺、逐步参考讲解；"
            "若订正缺失或与题干冲突，full_explanation 留空并明确说待人工核对，禁止猜答案；\n"
            "- 参考讲解必须核对每步数学推导；证明全局最小值时不能仅举一个"
            "区间外的例子代替证明。遇到绝对值分段题，先列出每个不同的变号点，"
            "逐段去绝对值并比较；N 个不同变号点把数轴分为 N+1 段，"
            "不能漏段，也不能把一段里的例子推广为整段结论。特别核对每段端点"
            "是否属于本段：若写 x<k，就不能声称在 x=k 时在这一段取到最小值；"
            "严格不等号对应的端点不能当成可取值。比较全局最值时，先分别"
            "说明每段所有实际可取的值，再合并结论；\n"
            "- 使用倒推时，应先写清『已知当前结果，怎样求前一个数』，"
            "每条逆推分支都要代回原来的正向规则核对，不能把正向公式直接当逆公式；\n"
            "- 有正负方向的行程，带符号数相加得到位置变化，不是总行驶路程；"
            "不要把带方向的和叫作距离。对于动点，说明结论时只描述题目允许的运动范围，"
            "不能说它会到达题目不允许的位置；\n"
            "- 数学式用规范 LaTeX 包在 $...$ 中，分式用 \\dfrac，不混用普通字母与公式；\n"
            "- 每一个 LaTeX 命令都必须放在成对的 $...$ 内，绝不能在普通句子里直接写"
            " \\dfrac、\\times 等命令；数学式不能出现残缺的 $$；\n"
            "- 对初一学生不得使用『实数』『闭区间』等尚未学习的术语；"
            "也不得写 [a,b] 这种区间记号。可改说『无论这个数取什么值』"
            "『从 -3 到 1（含端点）』或相应不等式；\n"
            "- JSON 字符串里的 LaTeX 反斜杠必须按 JSON 规则写成双反斜杠，"
            "如 $\\\\dfrac{1}{2}$；不要输出裸反斜杠；\n"
            "- JSON 字符串里的普通引述一律用中文引号「」或“”，不要在字符串内部"
            "直接写未经 JSON 转义的英文双引号；\n"
            "- 只按这门课程当前阶段的范围纠正。\n"
            "只输出一个 JSON 对象（不要输出其它文字）：\n"
            '{"verdict": "gaps|basically_clear|complete", '
            '"what_worked": ["学生说对了的要点，逐条"], '
            '"missing": ["缺了/混淆了什么，逐条，具体到步骤或条件"], '
            '"feedback": "两三句具体反馈，引用学生的原话来指出缺口", '
            '"improvements": ["怎样补齐每个关键步骤，逐条"], '
            '"full_explanation": "提交后供核对的完整逐步讲解；依据不足时为空"}\n'
            "verdict 标准：确有核心逻辑缺口或数学错误 = gaps；"
            "核心都讲到、数学正确，但表达仍可更详细 = basically_clear；"
            "方法选择理由、必要条件、步骤、结论都清楚，且仅在适用时交代边界 = complete。\n"
            + _SCOPE_GUARD_PROMPT
        )
        # A worked teachback reference can be much longer than a compact
        # first-pass draft.  The default 2048 cap truncated Q10's seven-level
        # reverse tree mid-JSON, discarding an otherwise useful single call.
        raw = self._complete(
            prompt, target_refs=(f"question:{question.id}",), max_tokens=4096
        )
        violation = scope_guard_violation(raw)
        if violation:
            raise LearningAIDraftError(
                f"审核反馈包含疑似超纲内容（命中「{violation}」），已拒绝写入。"
            )
        data = _parse_json_object(raw, math_strings=True)
        verdict = _clean_str(data.get("verdict"))
        if verdict not in ("gaps", "basically_clear", "complete"):
            raise LearningAIDraftError("AI 未返回有效的讲题审核结论，本次不判定通过或未通过。")
        def _lines(value: object) -> list[str]:
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            cleaned = _clean_str(value)
            return [cleaned] if cleaned else []
        what_worked = _lines(data.get("what_worked"))
        missing = _lines(data.get("missing"))
        feedback = _clean_str(data.get("feedback"))
        improvements = _lines(data.get("improvements"))
        full_explanation = _clean_str(data.get("full_explanation"))
        formatting_violation = explanation_review_quality_violation(
            data,
            grade=learner_grade,
            subject=_clean_str(getattr(question, "subject", "")),
        )
        if formatting_violation:
            raise LearningAIDraftError(
                f"AI 讲题反馈未满足初一数学表达要求：{formatting_violation}。"
                "本次反馈未保存，请稍后重新提交或人工核对。"
            )
        stem = _clean_str(getattr(question, "stem_text", ""))
        if (
            "绝对值" in (stem + _clean_str(getattr(question, "shared_context", "")))
            or "|" in stem
        ) and any(term in stem for term in ("最小值", "最大值")) and re.search(
            r"(?:比如令|例如取|取一个具体的数|举个例子)", full_explanation
        ):
            raise LearningAIDraftError(
                "AI 参考讲解用单个取值说明全局最值，不能替代完整的分段证明；"
                "本次反馈未保存。"
            )
        serialized_feedback = "\n".join(
            [*what_worked, *missing, feedback, *improvements, full_explanation]
        )
        reference_answers = _numeric_claims(
            _REFERENCE_FINAL_NUMBER, reviewed_correction
        )
        ai_answers = _numeric_claims(_AI_FINAL_NUMBER, serialized_feedback)
        if reference_answers and any(
            answer not in reference_answers for answer in ai_answers
        ):
            raise LearningAIDraftError(
                "AI 审核给出的最终答案与已人工核对订正冲突；本次反馈已拒绝保存。"
            )
        student_answers = _numeric_claims(_STUDENT_FINAL_NUMBER, content)
        if (
            reference_answers
            and student_answers.intersection(reference_answers)
            and any(
                marker in serialized_feedback
                for marker in (
                    "算错",
                    "计算错误",
                    "答案错误",
                    "结果错误",
                    "结论错误",
                    "答案不对",
                    "结果不对",
                )
            )
        ):
            raise LearningAIDraftError(
                "AI 审核把与人工订正一致的学生答案判错；本次反馈已拒绝保存。"
            )
        if not feedback and not missing and not what_worked:
            raise LearningAIDraftError("AI 审核返回了空反馈，请重试一次。")
        if full_explanation and not reviewed_correction:
            raise LearningAIDraftError(
                "没有已保存订正，AI 却给出了完整讲解；本次反馈已拒绝保存。"
            )
        if reviewed_correction and not full_explanation:
            raise LearningAIDraftError(
                "AI 没有按要求给出完整参考讲解；本次反馈未通过校验。"
            )
        return {
            "verdict": verdict,
            "what_worked": what_worked,
            "missing": missing,
            "feedback": feedback,
            "improvements": improvements,
            "full_explanation": full_explanation,
        }

    # ------------------------------------------------------- language layer 1
    def _language_context(self, question, content: str = "") -> str:
        """Use the same saved question/provenance and the user's own words."""

        draft = question.ai_draft if isinstance(question.ai_draft, dict) else {}
        source = _question_with_context(question, _clean_str(draft.get("content")), limit=24001)
        if not source:
            raise LearningAIDraftError("本题原文或题目为空，请先核对并补充资料。")
        if len(source) > 24000:
            raise LearningAIDraftError("本题外语材料过长，请保留相关原文与当前小问后再讲解。")
        return (
            "你是外语学科学习整理助手，只做第一层，完成讲解和整理后结束。"
            "学科由用户填写并保存，禁止额外进行 AI 学科识别。\n"
            f"【已保存学科】{question.subject}\n【原文、公共材料及当前题目】\n{source}\n"
            f"【用户实际作答】\n{question.student_answer or '未提供'}\n"
            f"【用户提供的对照答案与批改说明】\n{question.teacher_comment or '未提供'}\n"
            f"【已保存判定】{question.teacher_verdict or '未判定'}\n"
            f"【已有订正/参考版（可能是 AI 草稿，不是标准答案）】\n"
            f"{question.correction_note or '未提供'}\n"
            f"【用户当时的做题思路】\n{content or '未提供'}\n"
            "用户先独立做题，再对照答案批改订正，最后描述当时的思路。"
            "根据实际题型调整重点，不强行套阅读理解模式。阅读理解逐题核对原文依据、"
            "选项与用户原话：作答依据是否成立，词汇理解障碍、同义替换、定位错误、"
            "选项理解偏差以及是否存在猜测。答对也检查是否真正理解，"
            "不能只根据正误下结论，更不能没有证据就认定用户是猜的。"
            "区分已证实的问题、可能的问题和无法判断；对每个诊断引用原文/题号/用户原话，"
            "没有相应证据时明确说无法判断，可请用户补充原来的依据，不能编造。"
            "学生自行纠正的口误按纠正后的意思理解。保留英文、日文原句，不把字母当数学变量。"
            "语法、完形、翻译、写作等按本题语言要求解释。禁止套公式推导、方法归纳、"
            "举一反三，不进入第二层、第三层、两翼，不安排额外复盘或训练。"
            "资料不足或答案与原文冲突时说明待核对，不猜唯一答案，不声称联网核验。\n"
        )

    def _language_drafts(
        self, question, *, reference_only: bool, image_data: str | None = None,
    ) -> dict:
        """Return the existing editable first-layer fields without family suggestions."""

        prompt = self._language_context(question) + (
            "当前只生成参考讲解，用户尚未描述当时思路，不诊断用户的理解或错因。"
            "附图如有是本题材料，请核对原文。只返回 JSON："
            '{"correction":"本题参考答案及原文依据；不足时写明待核对",'
            '"analysis":"结合题型解释词句、原文与选项的关系",'
            '"method_tags":["实际题型"],"solution_method":"本题的作答依据与语言要点"}。'
            "不输出归纳族或二级结论，不虚构学生行为。"
        )
        raw = self._complete(
            prompt, target_refs=(f"question:{question.id}",), max_tokens=4096,
            image_data=image_data,
        )
        data = _parse_json_object(raw)
        fields = {key: _clean_str(data.get(key))
                  for key in ("correction", "analysis", "solution_method")}
        tags = _as_list(data.get("method_tags"))
        if not all(fields.values()) or not tags:
            raise LearningAIDraftError("外语参考讲解不完整，本次未写入，请核对原文与题目。")
        verdict = getattr(question, "teacher_verdict", None) or "none"
        return {
            **fields, "method_tags": tags, "original_reference": dict(data),
            "stem": "", "student_answer": "", "student_answer_gate": "closed",
            "verdict": verdict, "reason_tags": [], "type_family": None,
            "method_families": [], "secondary_conclusion": None,
            "correction": fields["correction"] if reference_only or verdict == "incorrect" else "",
        }

    def _review_language_reasoning(self, question, content: str) -> dict:
        """Review actual reasoning using the existing explanation feedback contract."""

        if not content.strip():
            raise LearningAIDraftError("请先描述你当时的做题思路。")
        prompt = self._language_context(question, content) + (
            "审核这份实际思路，不是润色，也不是新的训练任务。"
            "判定只描述思路的证据充分程度，不是作答对错或掌握评级。"
            "缺少原文、选项、对照答案或实际依据时明确说明缺什么；"
            "full_explanation 不得补造未提供的答案。只返回 JSON："
            '{"verdict":"gaps|basically_clear|complete",'
            '"what_worked":["有证据支持的理解与依据"],'
            '"missing":["具体问题或尚缺证据，标明区别"],'
            '"feedback":"逐题引用用户原话与原文依据说明判断",'
            '"improvements":["本题需澄清的词句或选项理解"],'
            '"full_explanation":"结合实际题型的讲解，资料不足时留空"}。'
        )
        raw = self._complete(prompt, target_refs=(f"question:{question.id}",), max_tokens=4096)
        data = _parse_json_object(raw)
        verdict = _clean_str(data.get("verdict"))
        if verdict not in ("gaps", "basically_clear", "complete") or not data.get("feedback"):
            raise LearningAIDraftError("AI 未返回有效的外语思路讲解，本次反馈未保存。")
        # A correct/incorrect answer is never evidence that the user guessed.
        # Reject an explicit guessing accusation when their own account contains none;
        # negative statements such as "不能认定你在猜" remain valid uncertainty feedback.
        claims = json.dumps(data, ensure_ascii=False)
        if not re.search(r"猜|蒙|随机|随便|guess|at random", content, re.IGNORECASE):
            for clause in re.split(r"[。！？\n，；]", claims):
                if re.search(r"无法|不能|不可|没有依据|无依据|不足以|不应|不是|并非", clause):
                    continue
                if re.search(r"(?:你|学生|用户).{0,12}(?:猜|蒙)|猜对|蒙对|靠猜|靠蒙", clause):
                    raise LearningAIDraftError(
                        "AI 在缺少你明确描述的情况下认定猜测，本次反馈未保存；"
                        "你的思路记录保留，请对照原文人工核对。"
                    )
        return {
            "verdict": verdict,
            **{key: _as_list(data.get(key)) for key in ("what_worked", "missing", "improvements")},
            "feedback": _clean_str(data.get("feedback")),
            "full_explanation": _clean_str(data.get("full_explanation")),
        }

    # ------------------------------------------------------- open answers
    def review_open_answer(
        self,
        question,
        content: str,
        *,
        reference_answer: str = "",
        scoring_standard: str = "",
    ) -> dict:
        """Diagnose an open-ended answer on a five-level learning scale.

        A reference answer is evidence about expected content, not an
        official point-by-point rubric.  Unless a genuine scoring standard
        is supplied separately, the model is forbidden to invent scores or
        point allocations.  The result is a learning diagnosis, never an
        exam mark.
        """

        answer = content.strip()
        if not answer:
            raise LearningAIDraftError("开放题作答内容为空。")
        source_content = ""
        if isinstance(question.ai_draft, dict):
            source_content = _clean_str(question.ai_draft.get("content"))
        rubric_note = (
            f"【正式评分标准】\n{scoring_standard[:3000]}\n"
            if scoring_standard.strip()
            else (
                "【正式评分标准】\n未提供。不得推测每点分值、总分或命题人的给分口径；"
                "只能做学习诊断。\n"
            )
        )
        prompt = (
            "你是高中地理开放题学习诊断助手。请把学生作答与题目、参考解析逐点对照，"
            "必须在五个等级中选且只选一个；不要退化成对/错二分。\n\n"
            f"【题目】\n{(question.stem_text or source_content)[:5000]}\n\n"
            f"【参考解析（不是评分标准）】\n{reference_answer[:3000] or '未提供'}\n\n"
            f"{rubric_note}\n"
            f"【学生作答】\n{answer[:3000]}\n\n"
            "五档定义：\n"
            "- complete：核心要点与因果/证据链基本完整；\n"
            "- direction_incomplete：分析方向正确，但漏掉一个或多个独立关键点；\n"
            "- evidence_gap：结论或方向可能正确，但没有材料依据、机制或因果链；\n"
            "- partial：只答对部分内容，或正确与错误表述混杂，尚未形成完整正确方向；\n"
            "- incorrect：答非所问、核心方向错误或与材料明显矛盾。\n"
            "只输出 JSON 对象："
            '{"level":"complete|direction_incomplete|evidence_gap|partial|incorrect",'
            '"what_worked":["答对的具体点"],"missing":["漏点或错误"],'
            '"evidence_chain":"学生是否建立了材料→过程→结论链",'
            '"feedback":"两三句具体诊断；没有正式评分标准时禁止给分"}'
        )
        raw = self._complete(prompt, target_refs=(f"question:{question.id}",))
        if not scoring_standard.strip() and re.search(
            r"(?:每点|给分点|评分标准|得分|可得\s*\d+(?:\.\d+)?\s*分|"
            r"给\s*\d+(?:\.\d+)?\s*分|判为\s*\d+(?:\.\d+)?\s*分|满分)",
            raw,
        ):
            raise LearningAIDraftError(
                "未提供正式评分标准，AI 却尝试构造分值；本次诊断已拒绝保存。"
            )
        data = _parse_json_object(raw)
        level = _clean_str(data.get("level"))
        valid_levels = {
            "complete",
            "direction_incomplete",
            "evidence_gap",
            "partial",
            "incorrect",
        }
        if level not in valid_levels:
            raise LearningAIDraftError("AI 未返回有效的五档掌握结论。")
        what_worked = _as_list(data.get("what_worked"))
        missing = _as_list(data.get("missing"))
        evidence_chain = _clean_str(data.get("evidence_chain"))
        feedback = _clean_str(data.get("feedback"))
        if not (what_worked or missing or evidence_chain or feedback):
            raise LearningAIDraftError("AI 开放题诊断为空，请重试一次。")
        return {
            "level": level,
            "what_worked": what_worked,
            "missing": missing,
            "evidence_chain": evidence_chain,
            "feedback": feedback,
            "rubric_capability": (
                "available" if scoring_standard.strip() else "unavailable"
            ),
        }

    def review_self_explanation(self, question, content: str) -> str:
        """Review a student's own explanation; logic audit, not polish."""

        if not content.strip():
            raise LearningAIDraftError("自我讲解内容为空。")
        source_content = ""
        if isinstance(question.ai_draft, dict):
            source_content = _clean_str(question.ai_draft.get("content"))
        prompt = (
            "你是学习审核助手。学生试着用自己的话讲解下面这道题为什么这样做。"
            "请审核这份讲解（不是润色），按以下重点检查：\n"
            "1) 逻辑链是否完整；2) 关键条件是否遗漏；3) 是否概念混淆；"
            "4) 是否因果倒置；5) 是否跳过关键步骤；6) 是否只是复述答案；"
            "7) 是否真正解释了“为什么”；8) 是否把公式当理由；9) 是否存在表达误区。\n\n"
            f"【题目】\n{_question_with_context(question, source_content, 2500)}\n\n"
            f"【学生的讲解】\n{content[:3000]}\n\n"
            "请用中文输出审核反馈，分四段，用「」标题：\n"
            "「你讲对了什么」逐条列出；「逻辑缺了哪一步」指出缺口；"
            "「遗漏/混淆了什么」列出遗漏条件或概念混淆；「建议怎么改」给出具体改法，"
            "最后可给一个更清楚的改写示例。改写示例只作参考，不要说成唯一答案。\n"
            "只能按这门课程当前阶段的范围纠正，不能用超纲知识“纠正”学生。\n"
            + _SCOPE_GUARD_PROMPT
        )
        raw = self._complete(prompt, target_refs=(f"question:{question.id}",))
        violation = scope_guard_violation(raw)
        if violation:
            raise LearningAIDraftError(
                f"审核反馈包含疑似超纲内容（命中「{violation}」），已拒绝写入。"
            )
        return _strip_code_fence(raw)
