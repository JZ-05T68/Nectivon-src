"""Overnight-round tests: AI draft service contracts (pure parts, fake provider)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.database import Database
from src.learning_ai_draft_service import (
    LearningAIDraftError,
    LearningAIDraftService,
    _parse_json_object,
    explanation_review_quality_violation,
    first_pass_quality_violation,
    scope_guard_violation,
)


@dataclass
class _FakeResult:
    text: str = ""


@dataclass
class _FakeProvider:
    responses: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)

    def complete(self, prompt: str, **_: object) -> _FakeResult:
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("no scripted response left")
        return _FakeResult(text=self.responses.pop(0))


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "pages" / "1").mkdir(parents=True)
    raw = tmp_path / "data" / "raw" / "试卷.pdf"
    raw.write_bytes(b"%PDF-1.7 fixture")
    image = tmp_path / "data" / "pages" / "1" / "page-1.png"
    image.write_bytes(b"png")
    db.create_document(
        title="试卷",
        filename="试卷.pdf",
        source_path=raw,
        sha256="a" * 64,
        page_count=1,
        import_status="completed",
    )
    db.create_page(
        document_id=1, page_number=1, image_path=image, extracted_text="", status="ready"
    )
    return db


def _make_question(database: Database, **overrides):
    from src.learning_workflow_service import QuestionService

    payload = dict(
        document_id=1,
        page_id=1,
        question_kind="error",
        stem_text="",
        ai_draft={
            "kind": "source_context",
            "content": "第3题 已知…求…。学生作答：…（有红笔×）",
        },
    )
    payload.update(overrides)
    return QuestionService(database).create_question_item(**payload)


def test_parse_json_object_tolerates_code_fences() -> None:
    raw = "```json\n{\"a\": 1}\n```"
    assert _parse_json_object(raw) == {"a": 1}


def test_parse_json_object_preserves_one_pass_unescaped_latex_commands() -> None:
    # This is a real provider shape: \dfrac is invalid JSON while \times
    # would silently become a tab if parsed without normalizing math strings.
    raw = r'{"formula":"$\dfrac{1}{2}\times x\neq 0$","note":"首行\n次行"}'
    parsed = _parse_json_object(raw, math_strings=True)
    assert parsed["formula"] == r"$\dfrac{1}{2}\times x\neq 0$"
    assert parsed["note"] == "首行\n次行"
    assert "\t" not in parsed["formula"]


def test_parse_json_object_keeps_already_valid_escaped_latex() -> None:
    payload = {"formula": r"$\dfrac{5}{12}\times 36$"}
    assert _parse_json_object(json.dumps(payload), math_strings=True) == payload


def test_parse_json_object_preserves_unescaped_prose_quotes() -> None:
    raw = r'{"feedback":"原题没写"第一次"，要并列 $1\to4\to2\to1$ 的两种读法。"}'
    assert _parse_json_object(raw, math_strings=True) == {
        "feedback": r"原题没写" + '"第一次"' + r"，要并列 $1\to4\to2\to1$ 的两种读法。"
    }


def test_parse_json_object_repairs_inner_quote_before_prose_comma() -> None:
    raw = '{"feedback":"题目没写"第一次",但要说明两种读法。","missing":["先核对题意","再画树"]}'
    assert _parse_json_object(raw, math_strings=True) == {
        "feedback": '题目没写"第一次",但要说明两种读法。',
        "missing": ["先核对题意", "再画树"],
    }


def test_grade7_review_rejects_naked_latex_and_high_stage_terms() -> None:
    assert "LaTeX 命令" in explanation_review_quality_violation(
        {"missing": [r"没有解释\dfrac{5}{12}的来源"]},
        grade="初一",
        subject="数学",
    )
    assert "术语" in explanation_review_quality_violation(
        {"full_explanation": "对任意实数成立。"},
        grade="初一",
        subject="数学",
    )
    assert "区间记号" in explanation_review_quality_violation(
        {"full_explanation": "在区间[-1,0]上继续计算。"},
        grade="初一",
        subject="数学",
    )
    assert explanation_review_quality_violation(
        {"full_explanation": r"$\dfrac{5}{12}\times36=15$。"},
        grade="初一",
        subject="数学",
    ) is None


def test_scope_guard_flags_competition_markers() -> None:
    assert scope_guard_violation("推荐使用竞赛中的放缩技巧") == "竞赛"
    assert scope_guard_violation("正常课堂内容") is None


def test_grade7_first_pass_rejects_missing_steps_and_generic_cause() -> None:
    draft = {
        "analysis": "因此选 A。",
        "reason_tags": ["漏看条件"],
        "method_tags": ["周期规律题"],
        "solution_method": "找周期。",
    }
    assert "解析不足" in first_pass_quality_violation(
        draft, grade="初一", subject="数学"
    )
    draft["analysis"] = (
        "先按题目给出的三次变化写出每一步的位置，再观察每一组三步后是否回到原状态。"
        "然后用总步数除以三，余下几步就从同一个状态继续走几步，最后对照题目中的选项核对。"
    )
    assert "漏看" in first_pass_quality_violation(draft, grade="初一", subject="数学")
    draft["reason_tags"] = ["不懂得建立三步循环与余数的对应关系"]
    draft["solution_method"] = "先列出前三步形成的循环，再除以周期，最后按余数定位。"
    assert first_pass_quality_violation(draft, grade="初一", subject="数学") is None
    draft["analysis"] += "结果是 -2$$c"
    assert "数学公式分隔符" in first_pass_quality_violation(
        draft, grade="初一", subject="数学"
    )
    draft["analysis"] = (
        "第一步先根据绝对值的定义判断各字母取值的正负，再分别判断三个绝对值符号"
        "里面的式子是正还是负。第二步按符号逐项去绝对值，第三步去括号、"
        "合并同类项，最后将结果代回检查。"
    )
    draft["stem"] = "已知|b|/b=-1，求结果。"
    assert "数学格式" in first_pass_quality_violation(
        draft, grade="初一", subject="数学"
    )
    draft["stem"] = "已知 $\\dfrac{|b|}{b}=-1$，求结果。"
    assert first_pass_quality_violation(draft, grade="初一", subject="数学") is None
    draft["secondary_conclusion"] = {"title": "未经验证的规律"}
    assert "二级结论" in first_pass_quality_violation(
        draft, grade="初一", subject="数学"
    )


def test_grade7_first_pass_rejects_doubled_latex_command_slash() -> None:
    draft = {
        "stem": r"已知 $\\dfrac{|b|}{b}=-1$。",
        "analysis": (
            "先用绝对值的定义判断字母的正负，再逐项判断绝对值里面的式子"
            "是正还是负。然后按符号去掉每一层绝对值，最后去括号并合并同类项，"
            "把结果代回题目检查。"
        ),
        "reason_tags": ["不会利用绝对值条件判断字母正负"],
        "method_tags": ["绝对值化简题"],
        "solution_method": "先判断符号，再逐项去绝对值，最后去括号合并同类项。",
        "secondary_conclusion": None,
    }
    assert "反斜杠" in first_pass_quality_violation(
        draft, grade="初一", subject="数学"
    )


def test_grade7_first_pass_refuses_unobserved_error_claim(database: Database) -> None:
    question = _make_question(
        database,
        subject="数学",
        stem_text="已知 a=1。",
        ai_draft={"kind": "source_context", "content": "已知 a=1。"},
        teacher_verdict="incorrect",
    )
    draft = {
        "stem": "已知 $a=1$。",
        "verdict": "incorrect",
        "correction": "参考解析：由 $a=1$ 得答案为 $1$。",
        "analysis": (
            "第一步读出题目给定的等式，确认字母代表的数。第二步把给定的数"
            "代入题目要求的位置，逐项检查计算关系。第三步将所得结果与原条件"
            "核对，确认没有改变题目给出的数或运算符号。"
        ),
        "reason_tags": ["去绝对值符号法则运用错误"],
        "method_tags": ["已知数求值题"],
        "solution_method": "先读清已知数，再代入相应位置，最后回到原条件核对。",
        "secondary_conclusion": None,
    }
    provider = _FakeProvider(responses=[json.dumps(draft, ensure_ascii=False)])
    profile = SimpleNamespace(basic=SimpleNamespace(stage="初中", grade="初一"))
    with pytest.raises(LearningAIDraftError, match="没有作答证据"):
        LearningAIDraftService(provider).generate_question_drafts(
            question, learner_profile=profile
        )


def test_exact_steps_ambiguity_cannot_be_forced_to_one_option() -> None:
    draft = {
        "stem": "如果自然数 $m$ 恰好经过 $7$ 步得到 $1$，有几个？",
        "correction": "逆推后排除先到 $1$ 的数，共 $4$ 个，选 B。",
        "analysis": (
            "先逆推七步可得六个数；如果按首次到达只剩四个。"
            "虽然原题没写首次，最后按四个选 B。"
            "这里还有若干步可以验证，所以应该请老师确认具体意思。"
        ),
        "reason_tags": ["不会画树状图逆推"],
        "method_tags": ["规则逆推题"],
        "solution_method": "先从结果逆推各分支，再正向验证每一条路径。",
        "secondary_conclusion": None,
    }
    violation = first_pass_quality_violation(
        draft,
        grade="初一",
        subject="数学",
        source_text="如果自然数m恰好经过7步运算可得到1，有几个？",
    )
    assert violation is not None and "两种结果" in violation


def test_correct_verdict_never_carries_correction(database: Database) -> None:
    question = _make_question(database)
    provider = _FakeProvider(
        responses=[
            json.dumps(
                {
                    "stem": "题干",
                    "verdict": "correct",
                    "correction": "不应该出现的订正",
                    "reason_tags": [],
                    "method_tags": ["方法A"],
                    "type_family": {"title": "题型族A", "description": ""},
                    "method_families": [],
                    "secondary_conclusion": None,
                },
                ensure_ascii=False,
            )
        ]
    )
    drafts = LearningAIDraftService(provider).generate_question_drafts(question)
    assert drafts["verdict"] == "correct"
    assert drafts["correction"] == ""


def test_out_of_scope_draft_is_refused(database: Database) -> None:
    question = _make_question(database)
    provider = _FakeProvider(
        responses=["先补充一个竞赛里常用的反证技巧……{\"stem\": \"x\"}"]
    )
    with pytest.raises(LearningAIDraftError):
        LearningAIDraftService(provider).generate_question_drafts(question)


def test_invalid_json_is_reported_honestly(database: Database) -> None:
    question = _make_question(database)
    provider = _FakeProvider(responses=["这不是 JSON"])
    with pytest.raises(LearningAIDraftError):
        LearningAIDraftService(provider).generate_question_drafts(question)


def test_uncertain_ocr_origin_character_is_blocked_before_ai_call(
    database: Database,
) -> None:
    question = _make_question(
        database,
        stem_text="如图是一根起点为O且标有单位长度的射线。",
    )
    provider = _FakeProvider(responses=[])
    with pytest.raises(LearningAIDraftError, match="数字 0"):
        LearningAIDraftService(provider).generate_question_drafts(question)


def test_wing_draft_requires_nonempty_content_before_review(database: Database) -> None:
    question = _make_question(
        database,
        stem_text="第2题 求椭圆的离心率范围。",
        ai_draft=None,
    )
    provider = _FakeProvider(
        responses=[
            json.dumps(
                {
                    "trigger_conditions": ["出现椭圆与焦点距离之和"],
                    "candidate_method": "定义法",
                    "selection_reason": "第一定义直接可用",
                },
                ensure_ascii=False,
            )
        ]
    )
    fields = LearningAIDraftService(provider).generate_wing_draft(
        question, "method_trigger"
    )
    assert fields["candidate_method"] == "定义法"
    assert "trigger_conditions" in fields


def test_explanation_review_returns_feedback_text(database: Database) -> None:
    question = _make_question(
        database, stem_text="第2题 求椭圆的离心率范围。", ai_draft=None
    )
    provider = _FakeProvider(
        responses=[
            "「你讲对了什么」用到了第一定义。\n「逻辑缺了哪一步」没说明 a、c 如何得出。"
        ]
    )
    feedback = LearningAIDraftService(provider).review_self_explanation(
        question, "因为椭圆上点到两焦点距离和是常数，所以……"
    )
    assert "你讲对了什么" in feedback


def test_explanation_task_rejects_final_answer_conflicting_with_reviewed_correction(
    database: Database,
) -> None:
    question = _make_question(
        database,
        stem_text="计算：-1^2021×[4-(-3)^2]+3÷(-3/4)。",
        correction_note="按顺序计算，本题结果为 1。",
        ai_draft=None,
    )
    provider = _FakeProvider(
        responses=[
            json.dumps(
                {
                    "verdict": "gaps",
                    "what_worked": ["说清了运算顺序"],
                    "missing": ["结论错误：学生说答案是 1"],
                    "feedback": "正确答案应为 -5。",
                },
                ensure_ascii=False,
            )
        ]
    )

    with pytest.raises(LearningAIDraftError, match="与已人工核对订正冲突"):
        LearningAIDraftService(provider).review_explanation_task(
            question,
            "我先算乘方，再算乘除，最后算加减，答案是 1。",
        )


def test_explanation_task_accepts_gap_review_that_respects_reviewed_answer(
    database: Database,
) -> None:
    question = _make_question(
        database,
        stem_text="计算：-1^2021×[4-(-3)^2]+3÷(-3/4)。",
        correction_note="按顺序计算，本题结果为 1。",
        ai_draft=None,
    )
    provider = _FakeProvider(
        responses=[
            json.dumps(
                {
                    "verdict": "gaps",
                    "what_worked": ["最终结果与已核对订正一致"],
                    "missing": ["没有写出括号内与除法转乘法的中间步骤"],
                    "feedback": "结果 1 可以保留，但还要说明每一步为什么这样算。",
                    "improvements": ["写出括号内的计算，再把除法改写为乘倒数。"],
                    "full_explanation": "先算乘方，再算括号，最后算乘除和加减，得到结果 1。",
                },
                ensure_ascii=False,
            )
        ]
    )

    result = LearningAIDraftService(provider).review_explanation_task(
        question,
        "我先算乘方，再算乘除，最后算加减，答案是 1。",
        learner_stage="初中",
        learner_grade="初一",
    )

    assert result["verdict"] == "gaps"
    assert "结果 1 可以保留" in result["feedback"]
    assert "写出括号内" in result["improvements"][0]
    assert "得到结果 1" in result["full_explanation"]
    assert "初中 / 初一" in provider.prompts[0]


def test_explanation_task_accepts_realistic_unescaped_latex_json(
    database: Database,
) -> None:
    question = _make_question(
        database,
        stem_text="计算：5/12÷1/36。",
        correction_note="结果为 15。",
        ai_draft=None,
    )
    raw = (
        r'{"verdict":"gaps","missing":["没有解释倒数"],'
        r'"feedback":"先说明除法为什么变为乘法。",'
        r'"full_explanation":"$\dfrac{5}{12}\div\dfrac{1}{36}'
        r'=\dfrac{5}{12}\times36=15$。"}'
    )
    result = LearningAIDraftService(_FakeProvider(responses=[raw])).review_explanation_task(
        question,
        "我直接得15。",
        learner_stage="初中",
        learner_grade="初一",
    )
    assert result["verdict"] == "gaps"
    assert r"\dfrac{5}{12}\times36" in result["full_explanation"]


def test_explanation_task_rejects_single_example_as_global_minimum_proof(
    database: Database,
) -> None:
    question = _make_question(
        database,
        stem_text="求 |x+3|+|x-1| 的最小值。",
        correction_note="当 -3≤x≤1 时，两段距离之和为 4；范围外更大，故最小值为 4。",
        ai_draft=None,
    )
    provider = _FakeProvider(
        responses=[json.dumps({
            "verdict": "gaps",
            "feedback": "要说明所有范围。",
            "full_explanation": "在中间这一段得到 4。比如令 x=2，算得 6，所以最小值为 4。",
        }, ensure_ascii=False)]
    )
    with pytest.raises(LearningAIDraftError, match="单个取值说明全局最值"):
        LearningAIDraftService(provider).review_explanation_task(
            question,
            "我只代了一个数。",
            learner_stage="初中",
            learner_grade="初一",
        )


def test_explanation_task_rejects_missing_full_explanation_when_correction_exists(
    database: Database,
) -> None:
    question = _make_question(
        database,
        stem_text="计算：1+1。",
        correction_note="1+1=2。",
        ai_draft=None,
    )
    provider = _FakeProvider(
        responses=[json.dumps({
            "verdict": "gaps",
            "missing": ["没有说明加法过程"],
            "feedback": "请补上关键步骤。",
            "full_explanation": "",
        }, ensure_ascii=False)]
    )
    with pytest.raises(LearningAIDraftError, match="完整参考讲解"):
        LearningAIDraftService(provider).review_explanation_task(question, "答案是2")


def test_explanation_task_rejects_calling_matching_student_result_wrong(
    database: Database,
) -> None:
    question = _make_question(
        database,
        stem_text="计算：-1^2021×[4-(-3)^2]+3÷(-3/4)。",
        correction_note="按顺序计算，本题结果为 1。",
        ai_draft=None,
    )
    provider = _FakeProvider(
        responses=[
            json.dumps(
                {
                    "verdict": "gaps",
                    "what_worked": ["说出了运算顺序"],
                    "missing": ["学生说我算出来是1，属于计算错误"],
                    "feedback": "你说我算出来是1，但这里恰好算错了。",
                },
                ensure_ascii=False,
            )
        ]
    )

    with pytest.raises(LearningAIDraftError, match="把与人工订正一致的学生答案判错"):
        LearningAIDraftService(provider).review_explanation_task(
            question,
            "我先算乘方，再算乘除，最后算加减，我算出来是1。",
        )


@pytest.mark.parametrize(
    "level",
    [
        "complete",
        "direction_incomplete",
        "evidence_gap",
        "partial",
        "incorrect",
    ],
)
def test_open_answer_review_preserves_five_distinct_levels(
    database: Database, level: str
) -> None:
    question = _make_question(
        database,
        stem_text="分析土壤中卤水层的形成过程。",
        correction_note="海水入侵补给；蒸发和植物蒸腾；砂层下渗；黏土层阻隔。",
        ai_draft=None,
    )
    provider = _FakeProvider(
        responses=[
            json.dumps(
                {
                    "level": level,
                    "what_worked": ["提到海水入侵"],
                    "missing": ["未说明黏土层阻隔"],
                    "evidence_chain": "有部分过程链",
                    "feedback": "方向可核对，但还需补全过程。",
                },
                ensure_ascii=False,
            )
        ]
    )

    result = LearningAIDraftService(provider).review_open_answer(
        question,
        "海水进入土壤后盐分增加。",
        reference_answer=question.correction_note,
    )

    assert result["level"] == level
    assert result["rubric_capability"] == "unavailable"


def test_open_answer_review_refuses_invented_scores_without_rubric(
    database: Database,
) -> None:
    question = _make_question(database, stem_text="分析形成过程。", ai_draft=None)
    provider = _FakeProvider(
        responses=[
            json.dumps(
                {
                    "level": "partial",
                    "what_worked": ["方向正确"],
                    "missing": ["缺过程"],
                    "evidence_chain": "不完整",
                    "feedback": "本题可得 2 分。",
                },
                ensure_ascii=False,
            )
        ]
    )

    with pytest.raises(LearningAIDraftError, match="未提供正式评分标准"):
        LearningAIDraftService(provider).review_open_answer(
            question, "因为海水入侵。", reference_answer="海水入侵并蒸发浓缩。"
        )


def test_open_answer_review_allows_incidental_question_point_value(
    database: Database,
) -> None:
    question = _make_question(database, stem_text="解释形成过程。（4分）", ai_draft=None)
    provider = _FakeProvider(
        responses=[
            json.dumps(
                {
                    "level": "partial",
                    "what_worked": ["写出砂层透水"],
                    "missing": ["漏盐分浓缩"],
                    "evidence_chain": "链条不完整",
                    "feedback": "这是一道4分过程题，但这里只做学习诊断，不给分。",
                },
                ensure_ascii=False,
            )
        ]
    )

    result = LearningAIDraftService(provider).review_open_answer(
        question, "砂层透水，黏土隔水。", reference_answer="海水入侵并蒸发浓缩。"
    )

    assert result["level"] == "partial"


# ---------------------------------------------------------------
# §P trust guard: corrections must never fabricate the student's answer
# ---------------------------------------------------------------
def test_correction_fabrication_guard_flags_claims_without_answer() -> None:
    from src.learning_ai_draft_service import correction_fabrication_violation

    assert (
        correction_fabrication_violation(
            "学生选了 B（-1），错在把定义用反了", has_student_answer=False
        )
        == "学生选"
    )
    assert (
        correction_fabrication_violation(
            "你选了 B，正确答案是 C", has_student_answer=False
        )
        == "你选"
    )
    assert (
        correction_fabrication_violation(
            "参考解析：正确答案为 B（-1）。", has_student_answer=False
        )
        is None
    )
    assert (
        correction_fabrication_violation("学生选了 B", has_student_answer=True)
        is None
    )


def test_generate_correction_refuses_fabricated_student_claim(
    database: Database,
) -> None:
    question = _make_question(database, student_answer="", teacher_verdict="incorrect")
    provider = _FakeProvider(
        responses=["学生选了 B（-1），错误在于…正确解法：…"]
    )
    with pytest.raises(LearningAIDraftError, match="编造学生的作答"):
        LearningAIDraftService(provider).generate_correction_draft(question)


def test_generate_correction_allows_reference_analysis_without_answer(
    database: Database,
) -> None:
    question = _make_question(database, student_answer="", teacher_verdict="incorrect")
    provider = _FakeProvider(
        responses=[
            "没有你的作答记录，以下是参考解析：\n"
            "极限用导数定义整理为 -f'(1)/2 = -1，正确答案 B。"
        ]
    )
    out = LearningAIDraftService(provider).generate_correction_draft(question)
    assert "参考解析" in out


def test_first_layer_prompt_contract_forbids_fabricated_choice() -> None:
    import inspect

    from src import learning_ai_draft_service as svc

    src = inspect.getsource(svc)
    assert "绝不能编造学生选了什么选项" in src
    assert "绝对不要假设或编造" in src
