"""student_answer provenance gate + upstream invalidation tests.

Covers fix round §24 (student_answer provenance) and §25 (upstream
invalidation).  No network and no AI calls anywhere.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from src.database import Database
from src.learning_ai_draft_service import (
    LearningAIDraftService,
    student_answer_gate_state,
)
from src.learning_workflow_service import QuestionService
from src.page_visual_service import PageVisualService


@pytest.fixture()
def services(tmp_path: Path) -> tuple[QuestionService, PageVisualService, Database]:
    db = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "pages" / "1").mkdir(parents=True)
    db.create_document(
        title="门禁夹具",
        filename="fixture.pdf",
        source_path=tmp_path / "data" / "raw" / "fixture.pdf",
        sha256=hashlib.sha256(b"fixture").hexdigest(),
        page_count=1,
        import_status="completed",
    )
    db.create_page(
        document_id=1,
        page_number=1,
        image_path=tmp_path / "data" / "pages" / "1" / "page-1.png",
        extracted_text="printed",
        status="ready",
    )
    return QuestionService(db), PageVisualService(db), db


def _make_question(service: QuestionService, **draft_extra) -> int:
    draft = {"kind": "source_context", "content": "x", **draft_extra}
    item = service.create_question_item(
        document_id=1,
        page_id=1,
        question_kind="error",
        ai_draft=draft,
    )
    return item.id


@dataclass
class _FakeCompletion:
    text: str


class _FakeProvider:
    """Returns a scripted JSON draft; records the prompt for assertions."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.last_prompt = ""

    def complete(self, prompt: str, **_kwargs) -> _FakeCompletion:
        self.last_prompt = prompt
        return _FakeCompletion(text=json.dumps(self._payload, ensure_ascii=False))


# --------------------------------------------------------------------------
# §24 gate state
# --------------------------------------------------------------------------
def test_gate_closed_without_provenance(services) -> None:
    service, _, _ = services
    qid = _make_question(service)
    question = service.get_question_item(qid)
    state = student_answer_gate_state(question)
    assert state["gate_open"] is False


def test_gate_open_on_confirmed_presence(services) -> None:
    service, _, _ = services
    qid = _make_question(service, handwriting_presence="confirmed")
    question = service.get_question_item(qid)
    assert student_answer_gate_state(question)["gate_open"] is True


def test_gate_open_on_user_confirmed_draft(services) -> None:
    service, _, _ = services
    qid = _make_question(service, user_confirmed=True)
    question = service.get_question_item(qid)
    assert student_answer_gate_state(question)["gate_open"] is True


def test_gate_closed_forces_empty_student_answer(services) -> None:
    """Even if the model disobeys and returns a student_answer, the gate
    must force it empty when the provenance chain is closed."""

    service, _, _ = services
    qid = _make_question(service)
    question = service.get_question_item(qid)
    provider = _FakeProvider(
        {
            "stem": "2-1 试建立图 2-66 所示电路的动态微分方程。",
            "student_answer": "u_i = R_1 C du_o/dt + ...（模型违令返回的推导）",
            "verdict": "none",
            "reason_tags": [],
            "method_tags": [],
            "type_family": None,
            "method_families": [],
            "secondary_conclusion": None,
        }
    )
    drafts = LearningAIDraftService(provider).generate_question_drafts(question)
    assert drafts["student_answer"] == ""
    assert drafts["student_answer_gate"] == "closed"
    # The closed-gate prompt must not even request the field.
    assert '"student_answer"' not in provider.last_prompt


def test_gate_open_lets_confirmed_handwriting_through(services) -> None:
    service, _, _ = services
    qid = _make_question(service, handwriting_presence="confirmed")
    question = service.get_question_item(qid)
    provider = _FakeProvider(
        {
            "stem": "2-1 试建立图 2-66 所示电路的动态微分方程。",
            "student_answer": "u_i = i_1 R_1 + u_o（手写转录）",
            "verdict": "none",
            "reason_tags": [],
            "method_tags": [],
            "type_family": None,
            "method_families": [],
            "secondary_conclusion": None,
        }
    )
    drafts = LearningAIDraftService(provider).generate_question_drafts(question)
    assert drafts["student_answer_gate"] == "open"
    assert "手写转录" in drafts["student_answer"]
    assert '"student_answer"' in provider.last_prompt


# --------------------------------------------------------------------------
# §25 upstream invalidation
# --------------------------------------------------------------------------
def test_invalidation_hard_clears_pure_ai_item(services) -> None:
    service, visual, _ = services
    qid = _make_question(service)
    service.update_question_item(
        qid,
        student_answer="幻觉推导",
        reason_tags=["概念混淆"],
        method_tags=["KVL/KCL"],
        user_edited=False,
    )
    reading_id = visual.record_interpretation(
        1, provenance="HANDWRITING_VISION", content="幻觉手写"
    )
    visual.delete_interpretation(reading_id)
    result = service.invalidate_ai_derived_fields(qid, reason="用户删除视觉草稿")
    question = service.get_question_item(qid)
    assert result["cleared_fields"] == [
        "student_answer",
        "reason_tags",
        "method_tags",
    ]
    assert question.student_answer == ""
    assert question.reason_tags == []
    assert question.method_tags == []
    draft = question.ai_draft or {}
    assert "invalidated_reason" in draft


def test_invalidation_never_silently_overwrites_user_edited(services) -> None:
    service, _, _ = services
    qid = _make_question(service)
    service.update_question_item(
        qid,
        student_answer="用户自己写的作答",
        reason_tags=["条件遗漏"],
        user_edited=True,
    )
    result = service.invalidate_ai_derived_fields(qid, reason="上游证据被删")
    question = service.get_question_item(qid)
    assert result["cleared_fields"] == []
    assert "student_answer" in result["stale_fields"]
    assert question.student_answer == "用户自己写的作答"
    assert question.reason_tags == ["条件遗漏"]
    assert "stale_fields" in (question.ai_draft or {})


def test_invalidation_updates_all_page_questions(services) -> None:
    service, _, _ = services
    q1 = _make_question(service)
    q2 = _make_question(service)
    assert service.list_questions_for_page(1) == service.list_questions_for_page(1)
    ids = [q.id for q in service.list_questions_for_page(1)]
    assert ids == [q1, q2]
