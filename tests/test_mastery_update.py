"""Unit tests for Phase 6 Mastery State Progression and History-Aware Evolution.

Verifies:
1. Discrete stages: 未建立 -> 训练中 -> 待加强 -> 阶段掌握.
2. Invariant: 1 correct != 阶段掌握 (moves to 训练中).
3. Invariant: 1 error != 完全不会 (moves to 待加强).
4. Continuous correct progression (>= 2 consecutive correct -> 阶段掌握).
5. Error fallback from 阶段掌握 back to 待加强.
6. Recovery progression from 待加强 back to 训练中 and then 阶段掌握.
7. Accurate calculation of accuracy rate and streak metrics.
8. Cross-discipline isolation of mastery states.
9. Database persistence and reload reliability.
"""

from __future__ import annotations

import pytest

from src.database import Database
from src.error_analysis_models import MasteryLevel
from src.error_analysis_service import ErrorAnalysisService


@pytest.fixture
def test_db(tmp_path) -> Database:
    """Create a temporary SQLite database."""
    db_path = tmp_path / "test_mastery.db"
    return Database(db_path)


@pytest.fixture
def error_service(test_db: Database) -> ErrorAnalysisService:
    """Instantiate ErrorAnalysisService."""
    return ErrorAnalysisService(test_db)


def test_initial_state_unestablished(error_service: ErrorAnalysisService) -> None:
    """Verify initial state is 未建立 with 0 attempts."""
    state = error_service.get_mastery_state("user_01", "物理", "动量定理")
    assert state.level == MasteryLevel.NOT_ESTABLISHED
    assert state.total_attempts == 0
    assert state.correct_attempts == 0
    assert state.accuracy_rate == 0.0
    assert state.consecutive_correct == 0
    assert state.consecutive_errors == 0


def test_single_error_does_not_declare_hopeless(error_service: ErrorAnalysisService) -> None:
    """Verify 1st attempt wrong -> 待加强 (not completely unable)."""
    state = error_service.update_mastery_state(
        user_id="user_01",
        subject="物理",
        target="动量定理",
        is_correct=False,
        error_type="方法选择错误",
    )

    assert state.level == MasteryLevel.NEEDS_IMPROVEMENT
    assert state.total_attempts == 1
    assert state.correct_attempts == 0
    assert state.consecutive_errors == 1
    assert state.consecutive_correct == 0
    assert state.last_attempt_is_correct is False
    assert state.last_error_type == "方法选择错误"


def test_single_correct_does_not_declare_mastered(error_service: ErrorAnalysisService) -> None:
    """Verify 1st attempt correct -> 训练中 (not 阶段掌握)."""
    state = error_service.update_mastery_state(
        user_id="user_01",
        subject="物理",
        target="动量定理",
        is_correct=True,
    )

    assert state.level == MasteryLevel.IN_TRAINING
    assert state.total_attempts == 1
    assert state.correct_attempts == 1
    assert state.consecutive_correct == 1
    assert state.consecutive_errors == 0
    assert state.accuracy_rate == 100.0


def test_consecutive_correct_progresses_to_stage_mastered(
    error_service: ErrorAnalysisService,
) -> None:
    """Verify 2 consecutive correct attempts reach 阶段掌握."""
    # Attempt 1: Correct -> 训练中
    s1 = error_service.update_mastery_state("user_01", "物理", "动量定理", is_correct=True)
    assert s1.level == MasteryLevel.IN_TRAINING
    assert s1.consecutive_correct == 1

    # Attempt 2: Correct -> 阶段掌握
    s2 = error_service.update_mastery_state("user_01", "物理", "动量定理", is_correct=True)
    assert s2.level == MasteryLevel.STAGE_MASTERED
    assert s2.consecutive_correct == 2
    assert s2.total_attempts == 2
    assert s2.correct_attempts == 2

    # Attempt 3: Stays 阶段掌握
    s3 = error_service.update_mastery_state("user_01", "物理", "动量定理", is_correct=True)
    assert s3.level == MasteryLevel.STAGE_MASTERED
    assert s3.consecutive_correct == 3


def test_error_fallback_and_recovery_cycle(error_service: ErrorAnalysisService) -> None:
    """Verify: 阶段掌握 -> 错误 (待加强) -> 再次训练正确 (训练中) -> 连续正确 (阶段掌握)."""
    user = "user_loop"
    subject = "物理"
    target = "动量定理"

    # Step 1: Reach 阶段掌握
    error_service.update_mastery_state(user, subject, target, is_correct=True)
    s_master = error_service.update_mastery_state(user, subject, target, is_correct=True)
    assert s_master.level == MasteryLevel.STAGE_MASTERED

    # Step 2: Make an error -> falls back to 待加强
    s_err = error_service.update_mastery_state(
        user, subject, target, is_correct=False, error_type="计算错误"
    )
    assert s_err.level == MasteryLevel.NEEDS_IMPROVEMENT
    assert s_err.consecutive_correct == 0
    assert s_err.consecutive_errors == 1
    assert s_err.last_error_type == "计算错误"

    # Step 3: Variant practice 1st attempt correct -> 训练中 (recovering)
    s_rec1 = error_service.update_mastery_state(user, subject, target, is_correct=True)
    assert s_rec1.level == MasteryLevel.IN_TRAINING
    assert s_rec1.consecutive_correct == 1
    assert s_rec1.consecutive_errors == 0

    # Step 4: Variant practice 2nd attempt correct -> 阶段掌握 again!
    s_rec2 = error_service.update_mastery_state(user, subject, target, is_correct=True)
    assert s_rec2.level == MasteryLevel.STAGE_MASTERED
    assert s_rec2.consecutive_correct == 2
    assert s_rec2.total_attempts == 5
    assert s_rec2.correct_attempts == 4
    assert s_rec2.accuracy_rate == 80.0


def test_mastery_cross_discipline_isolation(error_service: ErrorAnalysisService) -> None:
    """Verify Geography mastery state is completely isolated from Physics."""
    user = "cross_user"

    # Physics: 2 correct -> 阶段掌握
    error_service.update_mastery_state(user, "物理", "动量定理", is_correct=True)
    p_state = error_service.update_mastery_state(user, "物理", "动量定理", is_correct=True)
    assert p_state.level == MasteryLevel.STAGE_MASTERED

    # Geography: 1 error -> 待加强
    g_state = error_service.update_mastery_state(
        user, "地理", "流水侵蚀地貌", is_correct=False, error_type="知识点错误"
    )
    assert g_state.level == MasteryLevel.NEEDS_IMPROVEMENT

    # Query lists
    phys_list = error_service.list_mastery_states_by_subject(user, "物理")
    geo_list = error_service.list_mastery_states_by_subject(user, "地理")

    assert len(phys_list) == 1
    assert phys_list[0].target == "动量定理"
    assert phys_list[0].level == MasteryLevel.STAGE_MASTERED

    assert len(geo_list) == 1
    assert geo_list[0].target == "流水侵蚀地貌"
    assert geo_list[0].level == MasteryLevel.NEEDS_IMPROVEMENT


def test_mastery_persistence_reload(test_db: Database) -> None:
    """Verify mastery state persists across service instances with exact metrics."""
    service1 = ErrorAnalysisService(test_db)
    service1.update_mastery_state("p_user", "物理", "动量定理", is_correct=True)
    service1.update_mastery_state("p_user", "物理", "动量定理", is_correct=True)

    # Reconnect with a new instance
    service2 = ErrorAnalysisService(test_db)
    reloaded = service2.get_mastery_state("p_user", "物理", "动量定理")

    assert reloaded.level == MasteryLevel.STAGE_MASTERED
    assert reloaded.total_attempts == 2
    assert reloaded.correct_attempts == 2
    assert reloaded.consecutive_correct == 2
    assert reloaded.accuracy_rate == 100.0
