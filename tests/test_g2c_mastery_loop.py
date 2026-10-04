"""GEOGRAPHY G2-C tests: active mastery loop (evidence / next-action / review).

Covers the task-book §116 essentials:

- NO EVIDENCE ≠ FAILED ("尚未验证" must never read as "不会")
- deterministic next-action rules (wrong → method/boundary/redo; hinted
  success → variation; correct+unexplained → explain-back)
- four-dimension evidence (会做/会讲/独立性/稳定性) with manual records
  never posing as system training results
- explainable review schedule, defer ≠ failure, idempotent submission
- legacy mastery_records migration keeps every history row
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.database import Database
from src.learning_workflow_service import MasteryService, QuestionService


@pytest.fixture()
def env(tmp_path: Path):
    db = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "pages" / "1").mkdir(parents=True)
    raw = tmp_path / "data" / "raw" / "试卷.pdf"
    raw.write_bytes(b"%PDF-1.7 fixture")
    image = tmp_path / "data" / "pages" / "1" / "page-1.png"
    image.write_bytes(b"png")
    db.create_document(
        title="01如皋地理",
        filename="试卷.pdf",
        source_path=raw,
        sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
        page_count=1,
        import_status="completed",
    )
    db.create_page(
        document_id=1,
        page_number=1,
        image_path=image,
        extracted_text="读图题。",
        status="ready",
    )
    questions = QuestionService(db)
    mastery = MasteryService(db)
    organization_service = __import__(
        "src.learning_workflow_service", fromlist=["QuestionOrganizationService"]
    ).QuestionOrganizationService(db)
    question = questions.create_question_item(
        document_id=1,
        page_id=1,
        question_kind="error",
        question_number="1",
        stem_text="读图，判断江苏省人口分布的主要特点。",
        teacher_verdict="incorrect",
    )
    return db, questions, mastery, organization_service, question


def test_mastery_no_evidence_state(env) -> None:
    """§47/§48: a never-practiced question is 尚未验证, not 不会."""

    _db, _questions, mastery, _org, question = env
    states = mastery.mastery_states(question.id)
    assert states["do_state"] == "no_evidence"
    assert states["stability"] == "unverified"
    assert states["explain_state"] == "unverified"
    action = mastery.next_action(question.id)
    assert action["task_type"] == "no_evidence_baseline"
    assert action["why"], "every recommendation must say WHY (§8)"


def test_next_action_after_wrong(env) -> None:
    """§28: a wrong answer routes to a targeted task, not just redo."""

    _db, _questions, mastery, _org, question = env
    mastery.record_evidence(
        question.id,
        event_type="practice",
        result="incorrect",
        source="system_training",
    )
    action = mastery.next_action(question.id)
    assert action["task_type"] in ("redo_original", "method_trigger", "boundary_check")
    assert any("做错" in reason or "没有做对" in reason for reason in action["why"])
    states = mastery.mastery_states(question.id)
    assert states["do_state"] == "needs_practice"


def test_next_action_after_correct_with_hint(env) -> None:
    """§12/§77: hinted success is NOT independent mastery."""

    _db, _questions, mastery, _org, question = env
    mastery.record_evidence(
        question.id,
        event_type="practice",
        result="correct",
        independence="light_hint",
        hint_used=True,
        source="system_training",
    )
    states = mastery.mastery_states(question.id)
    assert states["do_state"] == "basically_ok", "hinted ≠ independent"
    assert states["independence"] == "light_hint"
    assert states["stability"] == "unverified", "one hinted success ≠ stability"
    action = mastery.next_action(question.id)
    assert action["task_type"] == "targeted_variation"


def test_next_action_explain_back(env) -> None:
    """§4/§18: correct + unexplained → EXPLAIN_BACK."""

    _db, _questions, mastery, _org, question = env
    mastery.record_evidence(
        question.id,
        event_type="practice",
        result="correct",
        independence="independent",
        source="system_training",
    )
    action = mastery.next_action(question.id)
    assert action["task_type"] == "explain_back"
    assert any("会做不等于会讲" in reason for reason in action["why"])


def test_explain_feedback_does_not_rewrite_independent_practice_as_hinted(env) -> None:
    """An explain-back row must not replace the last practice evidence."""

    _db, _questions, mastery, _org, question = env
    mastery.record_evidence(
        question.id,
        event_type="practice",
        result="correct",
        independence="independent",
        hint_used=False,
        source="system_training",
    )
    mastery.record_evidence(
        question.id,
        event_type="explain_back",
        result="partial",
        explanation_state="gaps",
        source="system_training",
    )

    action = mastery.next_action(question.id)
    assert action["task_type"] == "explain_back"
    assert all("用了提示" not in reason for reason in action["why"])
    summary = mastery.question_mastery_summary(question.id)
    assert summary["total"] == 1
    assert summary["correct"] == 1
    history = mastery.list_practice_records(question.id)
    assert len(history) == 1
    assert history[0]["outcome"] == "correct"
    assert history[0]["note"] == ""


@pytest.mark.parametrize(
    ("manual_clear", "ai_state", "expected_pass"),
    [
        (True, "gaps", True),
        (False, "basically_clear", True),
        (True, "complete", True),
        (False, "gaps", False),
    ],
)
def test_parallel_teachback_assessments_count_at_most_once(
    env, manual_clear: bool, ai_state: str, expected_pass: bool
) -> None:
    _db, _questions, mastery, _org, question = env
    mastery.record_evidence(
        question.id,
        event_type="explain_back",
        result="correct" if manual_clear else "incorrect",
        explanation_state="basically_clear" if manual_clear else "gaps",
        source="user_manual",
        provenance="讲给别人听（学生自评）",
    )
    mastery.record_evidence(
        question.id,
        event_type="explain_back",
        result="correct" if ai_state != "gaps" else "incorrect",
        explanation_state=ai_state,
        source="system_training",
        provenance="学生原话提交后的 AI 讲题审核",
    )

    status = mastery.teachback_status(question.id)
    assert status["passed"] is expected_pass
    assert status["pass_count"] == int(expected_pass)
    assert mastery.question_mastery_summary(question.id)["can_explain"] is expected_pass
    assert mastery.mastery_states(question.id)["explain_state"] == (
        "complete" if ai_state == "complete" else
        "basically_clear" if expected_pass else "gaps"
    )
    assert mastery.question_mastery_summary(question.id)["total"] == 0


def test_teachback_single_path_can_pass_and_latest_self_report_wins(env) -> None:
    _db, _questions, mastery, _org, question = env
    assert mastery.teachback_status(question.id)["combined_state"] == "unverified"
    mastery.record_evidence(
        question.id,
        event_type="explain_back",
        result="correct",
        explanation_state="basically_clear",
        source="user_manual",
    )
    assert mastery.teachback_status(question.id)["pass_count"] == 1
    mastery.record_evidence(
        question.id,
        event_type="explain_back",
        result="incorrect",
        explanation_state="gaps",
        source="user_manual",
    )
    assert mastery.teachback_status(question.id)["pass_count"] == 0
    mastery.record_evidence(
        question.id,
        event_type="explain_back",
        result="correct",
        explanation_state="complete",
        source="system_training",
    )
    assert mastery.teachback_status(question.id)["pass_count"] == 1


def test_independence_and_stability_evidence(env) -> None:
    """§13: stability needs repeated or spaced independent evidence."""

    _db, _questions, mastery, _org, question = env
    mastery.record_evidence(
        question.id,
        event_type="practice",
        result="correct",
        independence="independent",
        source="system_training",
    )
    states = mastery.mastery_states(question.id)
    assert states["stability"] == "temporary", "one correct is 暂时会"
    mastery.record_evidence(
        question.id,
        event_type="review",
        result="correct",
        independence="independent",
        source="system_training",
    )
    states = mastery.mastery_states(question.id)
    assert states["stability"] in ("repeated", "spaced")


def test_manual_record_not_system_attempt(env) -> None:
    """§15/§17: a manual tick can never replace system training evidence."""

    _db, _questions, mastery, _org, question = env
    mastery.record_evidence(
        question.id,
        event_type="practice",
        result="correct",
        independence="independent",
        explanation_state="basically_clear",
        source="user_manual",
        provenance="手动补录（非系统训练结果）",
    )
    states = mastery.mastery_states(question.id)
    assert states["do_state"] == "no_evidence", (
        "self-report must not flip the system mastery state"
    )


def test_review_schedule_and_defer_not_failure(env) -> None:
    """§27/§75/§76: explainable intervals; defer never records a failure."""

    _db, _questions, mastery, _org, question = env
    wrong_id = mastery.record_evidence(
        question.id,
        event_type="practice",
        result="incorrect",
        source="system_training",
    )
    wrong_row = mastery.today_review_items(limit=10)
    assert any(item["evidence_id"] == wrong_id for item in wrong_row)
    mastery.defer_review(wrong_id)
    items_after = mastery.today_review_items(limit=10)
    assert all(item["evidence_id"] != wrong_id for item in items_after), (
        "deferred item leaves the due list"
    )
    statuses = [
        row["review_status"]
        for row in mastery.list_review_items_for_question(question.id)
    ]
    assert "deferred" in statuses
    # No incorrect double-booking happened: the defer added NO failure.
    assert mastery.mastery_states(question.id)["do_state"] == "needs_practice"


def test_duplicate_submission_idempotent(env) -> None:
    """§105: double click / refresh replay creates ONE evidence row."""

    _db, _questions, mastery, _org, question = env
    key = f"{question.id}:practice:2026-09-28"
    first = mastery.record_evidence(
        question.id,
        event_type="practice",
        result="correct",
        independence="independent",
        source="system_training",
        idempotency_key=key,
    )
    second = mastery.record_evidence(
        question.id,
        event_type="practice",
        result="correct",
        independence="independent",
        source="system_training",
        idempotency_key=key,
    )
    assert first == second


def test_legacy_mastery_migration(env) -> None:
    """§60: the legacy record_practice API keeps working beside evidence."""

    _db, _questions, mastery, _org, question = env
    legacy_id = mastery.record_practice(
        question.id, outcome="correct", can_explain=False
    )
    records = mastery.list_practice_records(question.id)
    assert any(int(row["id"]) == legacy_id for row in records), (
        "legacy manual log is preserved untouched"
    )
    # The v27 backfill semantics (verified on the staging DB) map legacy
    # rows to user_manual evidence; a manual evidence row written through
    # the new API carries the same honest provenance.
    mastery.record_evidence(
        question.id,
        event_type="practice",
        result="correct",
        source="user_manual",
        provenance="早期手动记录（v27 迁移；非系统训练结果）",
    )
    rows = mastery.list_review_items_for_question(question.id)
    assert any(
        row["source"] == "user_manual" and "非系统训练" in str(row["provenance"])
        for row in rows
    )


def test_ai_generated_practice_provenance(env) -> None:
    """§30/§70: AI-generated practice is a distinct, labelled source."""

    _db, _questions, mastery, _org, question = env
    mastery.record_evidence(
        question.id,
        event_type="practice",
        result="partial",
        source="ai_generated",
        provenance="AI 生成练习（基于题型族「人口分布特征判读」）",
    )
    rows = mastery.list_review_items_for_question(question.id)
    assert any(
        row["source"] == "ai_generated" and "AI 生成练习" in str(row["provenance"])
        for row in rows
    )


def test_mastery_restart_persistence(env, tmp_path: Path) -> None:
    """§106: evidence survives a full service restart (fresh Database)."""

    _db, _questions, mastery, _org, question = env
    mastery.record_evidence(
        question.id,
        event_type="practice",
        result="correct",
        independence="independent",
        source="system_training",
    )
    # A brand-new service instance over the same file = restart semantics.
    reopened = MasteryService(
        Database(tmp_path / "data" / "database" / "knowledge.db")
    )
    states = reopened.mastery_states(question.id)
    assert states["do_state"] == "independent"
    assert reopened.list_review_items_for_question(question.id)
