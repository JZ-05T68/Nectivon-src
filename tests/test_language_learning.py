"""Language questions reuse layer one without induction, paid loops or review scheduling."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

import src.runtime as runtime
from src.database import Database
from src.learning_ai_draft_service import LearningAIDraftError, LearningAIDraftService
from src.learning_subject_policy import is_foreign_language_subject
from src.learning_workflow_service import (
    LearningWorkflowError,
    MasteryService,
    QuestionOrganizationService,
    QuestionService,
    TwoWingsService,
)
from src.training_profile_service import TrainingProfileService


class Provider:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.prompts: list[str] = []

    def complete(self, prompt: str, **kwargs) -> SimpleNamespace:
        self.prompts.append(prompt)
        return SimpleNamespace(text=json.dumps(self.response, ensure_ascii=False))


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "database" / "knowledge.db")
    document = db.create_document(
        title="外语测试资料", filename="language.pdf", source_path=tmp_path / "language.pdf",
        sha256="a" * 64, import_status="completed",
    )
    db.create_page(
        document_id=document.id, page_number=1, image_path=tmp_path / "page.png",
        extracted_text="She went to school by train.", status="ready",
    )
    return db


def question(database: Database, subject: str = "英语"):
    return QuestionService(database).create_question_item(
        document_id=1, page_id=1, question_kind="good", subject=subject,
        stem_text="How did she get to school? A. By train. B. On foot.",
        student_answer="A", teacher_verdict="correct",
        teacher_comment="用户对照答案：第1题 A；答案来自教师提供的答案页。",
        method_tags=["阅读理解"],
    )


@pytest.mark.parametrize("name", [
    "英语", "日语阅读", "高中英语", "大学公共英语", "English Reading", "Japanese",
    "0502 外国语言文学", "德语", "法语", "西班牙语",
])
def test_language_names_route_without_question_recognition(name):
    assert is_foreign_language_subject(name)


@pytest.mark.parametrize("name", ["数学", "物理", "地理", "语言学", "数学（英文授课）", ""])
def test_other_subjects_keep_existing_route(name):
    assert not is_foreign_language_subject(name)


def test_reference_reuses_fields_and_never_suggests_families(database):
    q = question(database)
    provider = Provider({
        "correction": "A. By train.", "analysis": "原文 by train 对应 A，不是 on foot。",
        "method_tags": ["阅读理解"], "solution_method": "理解 by train 与 on foot 的区别。",
        "type_family": {"title": "模型误给的族"}, "method_families": [{"title": "误给方法"}],
        "secondary_conclusion": {"title": "误给结论"},
    })
    drafts = LearningAIDraftService(provider).generate_question_reference(q)
    assert drafts["correction"] == "A. By train."
    assert drafts["type_family"] is None
    assert drafts["method_families"] == []
    assert drafts["secondary_conclusion"] is None
    assert drafts["reason_tags"] == []
    assert "数学变量" in provider.prompts[0]
    assert "列式、计算" not in provider.prompts[0]
    saved = QuestionService(database).save_ai_reference(q.id, drafts)
    assert saved.student_answer == "A"
    assert saved.teacher_comment == q.teacher_comment
    assert saved.analysis_note == drafts["analysis"]
    assert len(provider.prompts) == 1


def test_correct_answer_review_uses_original_and_actual_thought(database):
    q = replace(question(database), shared_context="原文：She went to school by train.")
    thought = "我选 A，但我当时把 train 理解成训练，所以不确定这里的意思。"
    provider = Provider({
        "verdict": "gaps", "what_worked": [], "missing": ["train 在本句指火车"],
        "feedback": "答案 A 正确，但你说 train 是训练，词义理解仍有问题。",
        "improvements": ["本句 by train 表示乘火车"],
        "full_explanation": "原文 by train 支持 A，与步行无关。",
    })
    result = LearningAIDraftService(provider).review_explanation_task(q, thought)
    assert result["verdict"] == "gaps"
    prompt = provider.prompts[0]
    for evidence in (q.shared_context, q.stem_text, q.teacher_comment, thought):
        assert evidence in prompt
    for constraint in ("同义替换", "定位错误", "选项理解偏差", "答对也检查",
                       "没有证据就认定用户是猜的", "不强行套阅读理解模式"):
        assert constraint in prompt
    assert "逐项检查：" not in prompt  # the math teachback template was not used
    assert QuestionService(database).get_question_item(q.id).teacher_verdict == "correct"
    assert len(provider.prompts) == 1


def test_language_review_requires_actual_words_and_valid_response(database):
    provider = Provider({"verdict": "correct"})
    service = LearningAIDraftService(provider)
    with pytest.raises(LearningAIDraftError, match="做题思路"):
        service.review_explanation_task(question(database, "日语"), "")
    assert not provider.prompts
    with pytest.raises(LearningAIDraftError, match="有效"):
        service.review_explanation_task(question(database, "日语"), "我根据助词关系选的。")
    assert len(provider.prompts) == 1


def test_correctness_is_not_evidence_of_guessing(database):
    q = question(database)
    provider = Provider({
        "verdict": "gaps", "feedback": "虽然答案正确，但你显然是猜对的。",
    })
    service = LearningAIDraftService(provider)
    with pytest.raises(LearningAIDraftError, match="认定猜测"):
        service.review_explanation_task(q, "我定位到原文 by train，所以选 A。")
    assert len(provider.prompts) == 1  # semantic rejection does not trigger paid retries
    assert service.review_explanation_task(q, "我没看懂，所以当时随机选了 A。")["verdict"] == "gaps"


def test_language_question_never_enters_families_wings_or_review(database):
    q = question(database, "日语")
    organization = QuestionOrganizationService(database)
    assert organization.auto_organize_question(q.id) == {"type": [], "method": [], "conclusion": []}
    result = organization.organize_with_confidence(q.id, type_family={"title": "阅读理解"})
    assert not result["auto"] and not result["recommended"] and not result["new_drafts"]
    family_id = organization.create_family(family_kind="type", title="测试族")
    with pytest.raises(LearningWorkflowError, match="第一层"):
        organization.assign_to_family(q.id, family_id)
    with pytest.raises(LearningWorkflowError, match="第一层"):
        TwoWingsService(database).create_entry(
            wing_kind="method_trigger", target_layer="question", question_id=q.id,
            candidate_method="不应创建的外语训练方法",
        )
    mastery = MasteryService(database)
    with pytest.raises(LearningWorkflowError, match="第一层"):
        mastery.record_evidence(q.id, event_type="review", source="user_manual")
    with pytest.raises(LearningWorkflowError, match="第一层"):
        mastery.record_practice(q.id, outcome="correct")
    with pytest.raises(LearningWorkflowError, match="第一层"):
        mastery.next_action(q.id)
    assert not mastery.today_review_items()
    assert not organization.list_review_items()
    assert not QuestionService(database).list_question_items(include_first_layer_only=False)
    assert len(QuestionService(database).list_question_items()) == 1


@pytest.mark.parametrize("ai_enabled", [False, True])
def test_first_layer_saves_language_thought_without_training(database, tmp_path, monkeypatch,
                                                           ai_enabled):
    q = question(database)
    provider = Provider({
        "verdict": "basically_clear", "what_worked": ["定位到 by train"], "missing": [],
        "feedback": "你引用的 by train 支持 A；没有依据认定你在猜。",
        "improvements": [], "full_explanation": "by train 表示乘火车。",
    })
    monkeypatch.setattr(runtime, "application_database", lambda: database)
    monkeypatch.setattr(
        runtime, "application_ai_provider", lambda: provider if ai_enabled else None,
    )
    monkeypatch.setattr(runtime, "application_training_profile_service", lambda:
                        TrainingProfileService(tmp_path / "profile.db"))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "pages/18_学习整理.py"))
    app.run(timeout=60)
    assert not app.exception
    assert any(q.stem_text == item.value for item in app.markdown)
    assert not any("$By$" in item.value for item in app.markdown)
    higher_picker_keys = {"mastery_question_picker", "trigger_question_picker",
                          "boundary_question_picker", "teachback_question_picker"}
    assert not any(item.key in higher_picker_keys for item in app.selectbox)
    thought = "我定位到 by train，因此选 A。日本語も入力できます。"
    app.text_area(key=f"language_thought_q{q.id}").set_value(thought)
    label = "保存并请 AI 讲解" if ai_enabled else "保存思路"
    next(button for button in app.button if button.label == label).click().run(timeout=60)
    assert not app.exception
    attempts = MasteryService(database).list_explanation_attempts(q.id)
    assert attempts[0]["content"] == thought
    assert bool(attempts[0]["feedback"]) is ai_enabled
    with database._connection() as conn:
        for table in ("mastery_evidence", "mastery_records", "mastery_profiles",
                      "question_family_members", "family_review_items", "wing_entries"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert len(provider.prompts) == int(ai_enabled)
