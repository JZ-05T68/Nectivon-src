"""Unit tests for TrainingSession models and service lifecycle (Phase 5)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.database import Database
from src.question_source_models import VerificationStatus, VerifiedQuestion
from src.targeted_training_models import TrainingTask
from src.training_session_models import (
    ErrorType,
    SessionStatus,
    TrainingResult,
    TrainingSession,
)
from src.training_session_service import TrainingSessionService


@pytest.fixture
def temp_db() -> Database:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path)
    # Ensure mastery_evidence table exists for testing feedback
    with db._connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mastery_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER,
                family_id INTEGER,
                event_type TEXT NOT NULL,
                result TEXT NOT NULL,
                independence TEXT,
                explanation_state TEXT,
                hint_used INTEGER DEFAULT 0,
                source TEXT NOT NULL,
                provenance TEXT,
                ai_evaluation TEXT,
                user_note TEXT,
                next_review_at TEXT,
                review_status TEXT,
                idempotency_key TEXT UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
    return db


@pytest.fixture
def sample_task() -> TrainingTask:
    return TrainingTask(
        training_type="方法族",
        target="动量定理",
        subject="物理",
        family_id=101,
    )


@pytest.fixture
def sample_questions() -> list[VerifiedQuestion]:
    return [
        VerifiedQuestion(
            id="vq_physics_1",
            question_text="【2024年高考物理新课标卷·第24题】质量为 m 的滑块冲上斜面...",
            source_name="2024年普通高等学校招生全国统一考试物理试题（新课标卷）",
            source_url="https://gaokao.neea.edu.cn/archive/2024/physics_q24.pdf",
            year=2024,
            exam_or_contest_name="2024年高考物理新课标卷",
            question_number="24",
            subject="物理",
            applicable_scope="基础教育 · 普通",
            verification_status=VerificationStatus.VERIFIED,
            reference_answer="由动量定理 I_合 = Δp = m*v_top - m*v0",
            question_item_id=85121,
            family_id=101,
        ),
        VerifiedQuestion(
            id="vq_physics_2",
            question_text="【2023年高考物理全国甲卷·第25题】质量为 m 的物块滑上质量为 M 的小车...",
            source_name="2023年普通高等学校招生全国统一考试物理试题（全国甲卷）",
            source_url="https://gaokao.neea.edu.cn/archive/2023/physics_q25.pdf",
            year=2023,
            exam_or_contest_name="2023年高考物理全国甲卷",
            question_number="25",
            subject="物理",
            applicable_scope="基础教育 · 普通",
            verification_status=VerificationStatus.VERIFIED,
            reference_answer="动量守恒 m*v0 = (M + m)*v_共",
            question_item_id=85122,
            family_id=101,
        ),
    ]


def test_session_models_serialization(
    sample_task: TrainingTask, sample_questions: list[VerifiedQuestion]
) -> None:
    """Test full serialization and deserialization of training session domain models."""
    session = TrainingSession(
        id="ts_test_001",
        task=sample_task,
        questions=sample_questions,
        current_question_index=0,
        status=SessionStatus.CREATED,
    )

    data = session.to_dict()
    assert data["id"] == "ts_test_001"
    assert data["status"] == "created"
    assert len(data["questions"]) == 2

    restored = TrainingSession.from_dict(data)
    assert restored.id == session.id
    assert restored.task.target == "动量定理"
    assert restored.status == SessionStatus.CREATED
    assert restored.current_question is not None
    assert restored.current_question.id == "vq_physics_1"


def test_session_lifecycle_and_persistence(
    temp_db: Database, sample_task: TrainingTask, sample_questions: list[VerifiedQuestion]
) -> None:
    """Test session creation, database persistence, and retrieval."""
    service = TrainingSessionService(temp_db)

    # 1. Create session
    session = service.create_session(sample_task, sample_questions, user_id="user_test")
    assert session.id.startswith("ts_")
    assert session.status == SessionStatus.CREATED
    assert session.total_questions == 2

    # 2. Retrieve session
    loaded = service.get_session(session.id)
    assert loaded is not None
    assert loaded.id == session.id
    assert loaded.task.target == "动量定理"
    assert len(loaded.questions) == 2

    # 3. Active session lookup for resumption
    active = service.get_active_session_for_task(sample_task, user_id="user_test")
    assert active is not None
    assert active.id == session.id

    # 4. Advance question
    updated = service.advance_question(session.id, 1)
    assert updated.current_question_index == 1
    assert updated.current_question is not None
    assert updated.current_question.id == "vq_physics_2"


def test_empty_questions_rejection(temp_db: Database, sample_task: TrainingTask) -> None:
    """Creating session with empty questions must raise ValueError."""
    service = TrainingSessionService(temp_db)
    with pytest.raises(ValueError, match="无法为没有任何已验证题目的任务创建训练计划"):
        service.create_session(sample_task, [])


def test_complete_session_and_analytics(
    temp_db: Database, sample_task: TrainingTask, sample_questions: list[VerifiedQuestion]
) -> None:
    """Test session completion, analytics calculation, and result storage."""
    service = TrainingSessionService(temp_db)
    session = service.create_session(sample_task, sample_questions)

    # Attempt question 1 (correct)
    service.evaluate_and_record_attempt(
        session_id=session.id,
        question_id="vq_physics_1",
        user_answer="由动量定理 I_合 = Δp = m*v_top - m*v0",
        is_correct=True,
    )

    # Attempt question 2 (incorrect with error attribution)
    service.evaluate_and_record_attempt(
        session_id=session.id,
        question_id="vq_physics_2",
        user_answer="动量不守恒，能量守恒",
        is_correct=False,
        error_type=ErrorType.CONCEPT,
        error_analysis="漏掉了水平方向合外力为零的动量守恒条件",
    )

    # Complete session
    result = service.complete_session(session.id)
    assert isinstance(result, TrainingResult)
    assert result.total_questions == 2
    assert result.attempted_count == 2
    assert result.correct_count == 1
    assert result.accuracy_rate == 50.0
    assert result.error_type_distribution.get("概念不清") == 1
    assert len(result.method_summary) >= 1

    # Check status changed to completed in database
    refetched = service.get_session(session.id)
    assert refetched is not None
    assert refetched.status == SessionStatus.COMPLETED
    assert refetched.is_completed is True

    # Check result retrieval
    stored_result = service.get_result(session.id)
    assert stored_result is not None
    assert stored_result.accuracy_rate == 50.0
    assert stored_result.correct_count == 1
