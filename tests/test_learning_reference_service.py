"""Join-time references are complete, independently editable, and image-grounded."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from src.database import Database
from src.learning_ai_draft_service import LearningAIDraftError, LearningAIDraftService
from src.learning_reference_service import generate_join_reference
from src.learning_workflow_service import QuestionService
from src.math_formatting_service import _format_saved_question, display_field, split_math_tags
from src.question_region_store import QuestionRegionStore


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "database" / "knowledge.db")
    raw = tmp_path / "test.pdf"
    raw.write_bytes(b"%PDF-1.7")
    scan = tmp_path / "scan.png"
    Image.new("RGB", (800, 600), "white").save(scan)
    db.create_document(title="试卷", filename="test.pdf", source_path=raw,
                       sha256="a" * 64, page_count=1, import_status="completed")
    db.create_page(document_id=1, page_number=1, image_path=scan, extracted_text="不可信 OCR",
                   status="ready")
    return db


def question(database: Database, **kwargs):
    return QuestionService(database).create_question_item(
        document_id=1, page_id=1, question_kind="error", question_number="6", subject="数学",
        stem_text="求 $x^{2}$ 的值。", **kwargs,
    )


def reference() -> dict:
    return {"correction": "$x^{2}=4$。",
            "analysis": "将已知条件代入表达式，逐步核对每个符号和运算顺序。"
                        "然后计算 $x^{2}=4$，检查结果与题目条件是否一致。"
                        "这里平方表示同一个数相乘，不能把指数作为乘数，最后确认结论。",
            "method_tags": ["指数幂次"],
            "solution_method": "先确定底数，再按指数计算，最后核对符号。",
            "secondary_conclusion": None}


class Provider:
    def __init__(self):
        self.calls = []

    def complete(self, prompt, **kwargs):
        self.calls.append((prompt, None))
        return SimpleNamespace(text=json.dumps(reference(), ensure_ascii=False))

    def complete_vision(self, prompt, image, **kwargs):
        self.calls.append((prompt, image))
        return SimpleNamespace(text=json.dumps(reference(), ensure_ascii=False))


def test_join_generates_all_four_fields_without_judging_or_rewriting_student(database):
    item = question(database)
    provider = Provider()
    profile = SimpleNamespace(basic=SimpleNamespace(grade="初一", stage="初中"))
    draft = generate_join_reference(item, database, provider=provider, learner_profile=profile)
    saved = QuestionService(database).save_ai_reference(item.id, draft)
    assert len(provider.calls) == 1
    assert all((saved.correction_note, saved.analysis_note,
                saved.method_tags, saved.solution_method))
    assert saved.stem_text == item.stem_text
    assert saved.student_answer == "" and saved.teacher_verdict is None and saved.reason_tags == []
    assert not saved.user_edited
    assert saved.ai_draft["learning_reference"]["origin"] == "AI_REFERENCE"
    assert "Greek" not in provider.calls[0][0]  # prompt uses the common Chinese rule
    assert "希腊字母" in provider.calls[0][0] and "KaTeX" in provider.calls[0][0]


def test_figures_use_vision_model_and_only_current_question_crops(database):
    page = database.get_page(1)
    store = QuestionRegionStore(page.image_path, "6")
    store.save_regions([{"id": "a" * 32, "bounds": [90, 100, 190, 200], "role": "stem"}],
                       expected_revision="original")
    image = Image.new("RGB", (100, 100), "red")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    store.save_crop("a" * 32, buffer.getvalue(), expected_revision=store.state()["revision"])
    item = question(database, ai_draft={
        "visual_material": {"dependency": "required", "regions": []},
    })
    text, vision = Provider(), Provider()
    generate_join_reference(item, database, provider=text, vision_provider=vision)
    assert text.calls == [] and len(vision.calls) == 1
    prompt, data = vision.calls[0]
    assert "不可信 OCR" not in prompt
    with Image.open(io.BytesIO(base64.b64decode(data.split(",")[1]))) as supplied:
        assert supplied.size[0] < 800 and supplied.size[1] < 600
        assert supplied.getpixel((40, 60))[0] > 240  # saved crop, rather than untouched white scan


def test_reference_fills_empty_only_and_is_idempotent(database):
    service = QuestionService(database)
    item = question(database, correction_note="人工订正", student_answer="我选 A")
    saved = service.save_ai_reference(item.id, reference())
    assert saved.correction_note == "人工订正" and saved.student_answer == "我选 A"
    reviewed = service.update_question_item(saved.id, analysis_note="人工解析", user_edited=True)
    repeated = service.save_ai_reference(saved.id, {**reference(), "analysis": "不应覆盖"})
    assert repeated.analysis_note == reviewed.analysis_note and repeated.user_edited


def test_missing_network_model_or_figure_is_reported_without_inventing_reference(database):
    item = question(database)
    with pytest.raises(LearningAIDraftError, match="未配置"):
        generate_join_reference(item, database, provider=None)
    item = question(database, ai_draft={
        "visual_material": {"dependency": "required", "regions": []},
    })
    provider = Provider()
    with pytest.raises(LearningAIDraftError, match="截图尚待核对"):
        generate_join_reference(item, database, provider=provider, vision_provider=provider)
    assert provider.calls == []


def test_incomplete_reference_does_not_retry_or_save(database):
    provider = Provider()
    provider.complete = lambda *args, **kwargs: SimpleNamespace(text='{"correction":""}')
    with pytest.raises(LearningAIDraftError, match="不完整"):
        LearningAIDraftService(provider).generate_question_reference(question(database))


def test_every_manual_reference_field_gets_math_display_without_changing_source(database):
    service = QuestionService(database)
    item = question(database, correction_note="α²", analysis_note=r"\int_{0}^{1}x^{2}\,dx",
                    method_tags=[r"矩阵\begin{bmatrix}1&2\\3&4\end{bmatrix}"],
                    solution_method="cos θ = 1，β₁=2")

    class Formatting:
        calls = 0

        def _complete(self, prompt, **kwargs):
            self.calls += 1
            assert all(field in prompt for field in
                       ("correction_note", "analysis_note", "method_tags", "solution_method"))
            return "{}"

    formatting = Formatting()
    _format_saved_question(database.database_path, item.id, formatting)
    saved = service.get_question_item(item.id)
    assert formatting.calls == 1
    assert saved.correction_note == item.correction_note and saved.method_tags == item.method_tags
    assert r"\alpha ^{2}" in display_field(saved, "correction_note")
    assert r"$\int_{0}^{1}x^{2}\,dx$" == display_field(saved, "analysis_note")
    assert r"$\begin{bmatrix}1&2\\3&4\end{bmatrix}$" in display_field(saved, "method_tags")


def test_tags_keep_formulas_with_commas_together():
    assert split_math_tags(r"函数 $f(x,y)$、矩阵 $\begin{pmatrix}a,b\end{pmatrix}$,积分") == [
        r"函数 $f(x,y)$", r"矩阵 $\begin{pmatrix}a,b\end{pmatrix}$", "积分",
    ]
    assert split_math_tags("矩阵 $$f(x,y)$$、积分") == ["矩阵 $$f(x,y)$$", "积分"]


def test_reference_formats_adjacent_inline_and_display_math_before_validation(database):
    payload = reference()
    payload["analysis"] += r"第2步$$x^{2}=4$$核对 $x=2$$y=1$。"
    provider = Provider()
    provider.complete = lambda *args, **kwargs: SimpleNamespace(text=json.dumps(payload))
    profile = SimpleNamespace(basic=SimpleNamespace(grade="初一", stage="初中"))
    generated = LearningAIDraftService(provider).generate_question_reference(
        question(database), learner_profile=profile,
    )
    assert "$x=2$ $y=1$" in generated["analysis"]
    assert "\n\n$$x^{2}=4$$\n\n" in generated["analysis"]
    assert generated["original_reference"]["analysis"] == payload["analysis"]
