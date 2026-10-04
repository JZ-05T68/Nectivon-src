"""Unit tests for Phase 6 Error Analysis and Diagnostic Profiling.

Verifies:
1. Structured error cause records persistence and retrieval.
2. Standard 5-tier taxonomy (知识点错误, 方法选择错误, 计算错误, 审题错误, 其他).
3. Backward compatibility mapping from Phase 5 labels.
4. Error reflection note updates and mastery state synchronization.
5. Zero-history protection: never forge an error profile without data.
6. Frequent error pattern identification (>= 2 occurrences).
7. Primary weakness determination from real history.
8. Strict cross-discipline data isolation.
"""

from __future__ import annotations

import pytest

from src.database import Database
from src.error_analysis_models import (
    ErrorCategory,
    MasteryLevel,
)
from src.error_analysis_service import ErrorAnalysisService
from src.question_source_models import VerifiedQuestion
from src.targeted_training_models import TrainingTask
from src.training_session_models import ErrorType, QuestionAttempt, TrainingSession


@pytest.fixture
def test_db(tmp_path) -> Database:
    """Create a temporary SQLite database."""
    db_path = tmp_path / "test_knowledge.db"
    return Database(db_path)


@pytest.fixture
def error_service(test_db: Database) -> ErrorAnalysisService:
    """Instantiate ErrorAnalysisService."""
    return ErrorAnalysisService(test_db)


def _make_verified_question(
    qid: str = "q_phys_01",
    subject: str = "物理",
    exam_name: str = "2024江苏高考物理试题",
) -> VerifiedQuestion:
    return VerifiedQuestion(
        id=qid,
        question_text="测试动量定理试题",
        source_name=exam_name,
        source_url="https://example.com/phys.pdf",
        year=2024,
        exam_or_contest_name=exam_name,
        question_number="13",
        subject=subject,
        applicable_scope="基础教育 · 普通",
        question_fingerprint=f"fp_{qid}",
        reference_answer="标准答案：动量守恒方程",
        family_id=8512,
        question_item_id=101,
    )


def _make_session(
    sid: str = "sess_01",
    subject: str = "物理",
    target: str = "动量定理",
    user_id: str = "test_user_01",
) -> TrainingSession:
    task = TrainingTask(
        training_type="方法族",
        target=target,
        subject=subject,
        family_id=8512,
    )
    return TrainingSession(
        id=sid,
        user_id=user_id,
        task=task,
        questions=[_make_verified_question(subject=subject)],
    )


def test_error_category_taxonomy_and_mapping() -> None:
    """Verify 5-tier official taxonomy and compatibility mapping."""
    assert ErrorCategory.KNOWLEDGE.value == "知识点错误"
    assert ErrorCategory.METHOD.value == "方法选择错误"
    assert ErrorCategory.CALCULATION.value == "计算错误"
    assert ErrorCategory.READING.value == "审题错误"
    assert ErrorCategory.OTHER.value == "其他"

    # Descriptions
    assert "不知道相关概念" in ErrorCategory.KNOWLEDGE.description
    assert "没有选择正确方法" in ErrorCategory.METHOD.description
    assert "计算" in ErrorCategory.CALCULATION.description
    assert "条件" in ErrorCategory.READING.description

    # Mapping from Phase 5 ErrorType
    assert ErrorCategory.from_error_type(ErrorType.CONCEPT) == ErrorCategory.KNOWLEDGE
    assert ErrorCategory.from_error_type(ErrorType.METHOD) == ErrorCategory.METHOD
    assert ErrorCategory.from_error_type(ErrorType.CALCULATION) == ErrorCategory.CALCULATION
    assert ErrorCategory.from_error_type(ErrorType.READING) == ErrorCategory.READING
    assert ErrorCategory.from_error_type(ErrorType.OTHER) == ErrorCategory.OTHER

    # Mapping from strings
    assert ErrorCategory.from_error_type("概念不清") == ErrorCategory.KNOWLEDGE
    assert ErrorCategory.from_error_type("方法选择错误") == ErrorCategory.METHOD
    assert ErrorCategory.from_error_type("计算失误") == ErrorCategory.CALCULATION
    assert ErrorCategory.from_error_type("审题不清") == ErrorCategory.READING
    assert ErrorCategory.from_error_type(None) == ErrorCategory.OTHER


def test_record_attempt_analysis_and_retrieval(error_service: ErrorAnalysisService) -> None:
    """Verify recording structured attempt analysis for incorrect attempt."""
    session = _make_session()
    question = session.questions[0]
    attempt = QuestionAttempt(
        session_id=session.id,
        question_id=question.id,
        user_answer="我的错误解答",
        is_correct=False,
        error_type=ErrorType.METHOD,
        error_analysis="受力分析没有选取一维正方向",
        submission_count=1,
    )

    analysis = error_service.record_attempt_analysis(session, question, attempt)

    assert analysis.is_error is True
    assert analysis.error_type == "方法选择错误"
    assert analysis.user_note == "受力分析没有选取一维正方向"
    assert analysis.associated_target == "动量定理"
    assert analysis.subject == "物理"
    assert analysis.family_id == 8512
    assert analysis.question_item_id == 101

    # Query back
    records = error_service.get_error_analyses(subject="物理", target="动量定理")
    assert len(records) == 1
    assert records[0].id == analysis.id
    assert records[0].error_type == "方法选择错误"


def test_update_error_attribution(error_service: ErrorAnalysisService) -> None:
    """Verify updating error cause and user reflection note."""
    session = _make_session()
    question = session.questions[0]
    attempt = QuestionAttempt(
        session_id=session.id,
        question_id=question.id,
        user_answer="初步错误解答",
        is_correct=False,
        error_type=ErrorType.OTHER,
        submission_count=1,
    )
    error_service.record_attempt_analysis(session, question, attempt)

    # Update attribution
    updated = error_service.update_error_attribution(
        session_id=session.id,
        question_id=question.id,
        error_type=ErrorType.CALCULATION,
        user_note="推导过程中漏乘了摩擦系数μ",
    )

    assert updated is not None
    assert updated.error_type == "计算错误"
    assert updated.user_note == "推导过程中漏乘了摩擦系数μ"

    # Verify updated mastery state
    mastery = error_service.get_mastery_state(session.user_id, "物理", "动量定理")
    assert mastery.last_error_type == "计算错误"


def test_zero_history_protection(error_service: ErrorAnalysisService) -> None:
    """Verify no fake error profile is generated when zero history exists."""
    profile = error_service.generate_error_profile("user_blank", "物理")

    assert profile.has_sufficient_data is False
    assert profile.total_attempts == 0
    assert profile.total_errors == 0
    assert "暂无足够训练数据" in profile.message
    assert profile.primary_weakness is None
    assert len(profile.frequent_errors) == 0


def test_frequent_error_tag_and_primary_weakness(error_service: ErrorAnalysisService) -> None:
    """Verify repeated errors form frequent error tags and identify primary weakness."""
    session1 = _make_session(sid="s1")
    q = session1.questions[0]

    # Attempt 1: Method error
    att1 = QuestionAttempt(
        session_id="s1",
        question_id=q.id,
        user_answer="错误1",
        is_correct=False,
        error_type=ErrorType.METHOD,
        submission_count=1,
    )
    error_service.record_attempt_analysis(session1, q, att1)

    # After 1 error: not yet frequent
    p1 = error_service.generate_error_profile(session1.user_id, "物理")
    assert p1.has_sufficient_data is True
    assert p1.total_errors == 1
    assert len(p1.frequent_errors) == 0
    assert p1.primary_weakness == "动量定理方法选择错误"

    # Attempt 2: Same method error on same target
    session2 = _make_session(sid="s2")
    att2 = QuestionAttempt(
        session_id="s2",
        question_id=q.id,
        user_answer="错误2",
        is_correct=False,
        error_type=ErrorType.METHOD,
        submission_count=1,
    )
    error_service.record_attempt_analysis(session2, q, att2)

    # After 2 errors: frequent error tag forms!
    p2 = error_service.generate_error_profile(session1.user_id, "物理")
    assert p2.total_errors == 2
    assert len(p2.frequent_errors) == 1
    tag = p2.frequent_errors[0]
    assert tag.target == "动量定理"
    assert tag.error_type == "方法选择错误"
    assert tag.count == 2
    assert "高频错因：动量定理 - 方法选择错误（2次）" in tag.tag_label
    assert p2.primary_weakness == "动量定理方法选择错误"


def test_cross_discipline_isolation(error_service: ErrorAnalysisService) -> None:
    """Verify Geography errors never bleed into Physics profiles or mastery."""
    # Record Physics attempt
    session_phys = _make_session(sid="s_phys", subject="物理", target="动量定理")
    att_phys = QuestionAttempt(
        session_id="s_phys",
        question_id="q_phys",
        user_answer="物理正确答案",
        is_correct=True,
        submission_count=1,
    )
    error_service.record_attempt_analysis(
        session_phys,
        _make_verified_question(qid="q_phys", subject="物理"),
        att_phys,
    )

    # Record Geography attempt with error
    session_geo = _make_session(sid="s_geo", subject="地理", target="流水侵蚀地貌")
    att_geo = QuestionAttempt(
        session_id="s_geo",
        question_id="q_geo",
        user_answer="地理错误答案",
        is_correct=False,
        error_type=ErrorType.CONCEPT,
        submission_count=1,
    )
    error_service.record_attempt_analysis(
        session_geo,
        _make_verified_question(qid="q_geo", subject="地理"),
        att_geo,
    )

    # Check Physics profile: has 0 errors!
    phys_profile = error_service.generate_error_profile(session_phys.user_id, "物理")
    assert phys_profile.total_attempts == 1
    assert phys_profile.total_errors == 0
    assert "当前学科无错误记录" in phys_profile.message

    # Check Geography profile: has 1 error
    geo_profile = error_service.generate_error_profile(session_geo.user_id, "地理")
    assert geo_profile.total_attempts == 1
    assert geo_profile.total_errors == 1
    assert geo_profile.error_distribution_by_type["知识点错误"] == 1

    # Check Mastery states are isolated
    phys_mst = error_service.get_mastery_state(session_phys.user_id, "物理", "动量定理")
    geo_mst = error_service.get_mastery_state(session_geo.user_id, "地理", "流水侵蚀地貌")

    assert phys_mst.level == MasteryLevel.IN_TRAINING
    assert geo_mst.level == MasteryLevel.NEEDS_IMPROVEMENT
