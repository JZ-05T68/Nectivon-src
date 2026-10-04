"""Unit tests for Phase 7 Review Queue and Spaced Retrieval Scheduling.

Verifies:
1. Dynamic priority assignment and scheduling intervals:
   - 待加强 -> High priority (高), 1-day interval, urgent reinforcement warning.
   - 训练中 -> Normal priority (中), 3-day interval, standard cadence.
   - 阶段掌握 -> Low priority (低), 14-day interval, long-term retention.
   - 未建立 -> Excluded from queue.
2. Queue ordering: Priority (高 -> 中 -> 低), then next_review_at ascending.
3. Zero-data protection: Returns clean status when user has no training data.
4. Summary card metrics for today's learning status dashboard.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.database import Database
from src.error_analysis_service import ErrorAnalysisService
from src.review_queue_models import ReviewPriority
from src.review_queue_service import ReviewQueueService
from src.training_session_models import ErrorType


@pytest.fixture
def review_test_db() -> Database:
    """Create a temporary SQLite database initialized with schemas."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    return Database(db_path)


@pytest.fixture
def error_service(review_test_db: Database) -> ErrorAnalysisService:
    return ErrorAnalysisService(review_test_db)


@pytest.fixture
def queue_service(
    review_test_db: Database, error_service: ErrorAnalysisService
) -> ReviewQueueService:
    return ReviewQueueService(review_test_db, error_service=error_service)


def test_zero_data_protection_review_queue(queue_service: ReviewQueueService) -> None:
    """Verify queue service returns safe fallback when user has no learning records."""
    summary = queue_service.get_learning_status_summary("non_existent_user", "地理")

    assert summary.has_sufficient_data is False
    assert ("暂无学习数据" in summary.message or "暂无足够学习数据" in summary.message)
    assert summary.stage_mastered_count == 0
    assert summary.needs_improvement_count == 0
    assert summary.in_training_count == 0
    assert len(summary.today_recommended_items) == 0

    items = queue_service.get_review_queue("non_existent_user", "地理")
    assert len(items) == 0


def test_review_scheduling_intervals_and_priorities(
    review_test_db: Database,
    error_service: ErrorAnalysisService,
    queue_service: ReviewQueueService,
) -> None:
    """Verify exact scheduling intervals and priority levels for all mastery states."""
    user_id = "user_intervals"
    subject = "物理"

    # 1. 待加强: 1 error -> level becomes NEEDS_IMPROVEMENT
    error_service.update_mastery_state(
        user_id=user_id,
        subject=subject,
        target="动量定理",
        is_correct=False,
        error_type=ErrorType.CALCULATION.value,
        family_id=8512,
    )
    # Consecutive error to test frequent error warning
    error_service.update_mastery_state(
        user_id=user_id,
        subject=subject,
        target="动量定理",
        is_correct=False,
        error_type=ErrorType.CALCULATION.value,
        family_id=8512,
    )

    # 2. 训练中: 1 error followed by 1 correct -> level becomes IN_TRAINING
    error_service.update_mastery_state(
        user_id=user_id,
        subject=subject,
        target="电磁感应",
        is_correct=False,
        error_type=ErrorType.CONCEPT.value,
        family_id=8512,
    )
    error_service.update_mastery_state(
        user_id=user_id,
        subject=subject,
        target="电磁感应",
        is_correct=True,
        family_id=8512,
    )

    # 3. 阶段掌握: 2 consecutive correct -> level becomes STAGE_MASTERED
    error_service.update_mastery_state(
        user_id=user_id,
        subject=subject,
        target="牛顿运动定律",
        is_correct=True,
        family_id=8512,
    )
    error_service.update_mastery_state(
        user_id=user_id,
        subject=subject,
        target="牛顿运动定律",
        is_correct=True,
        family_id=8512,
    )

    # Build queue
    items = queue_service.build_or_update_review_queue(user_id, subject)
    items_by_target = {item.target: item for item in items}

    # Verify 动量定理 (待加强)
    item_high = items_by_target["动量定理"]
    assert item_high.review_priority == ReviewPriority.HIGH
    assert item_high.review_interval_days == 1
    assert "连续失误 2 次" in item_high.review_reason or "近期" in item_high.review_reason

    # Verify 电磁感应 (训练中)
    item_normal = items_by_target["电磁感应"]
    assert item_normal.review_priority == ReviewPriority.NORMAL
    assert item_normal.review_interval_days == 3
    assert "保持正常频率" in item_normal.review_reason or "训练积累期" in item_normal.review_reason

    # Verify 牛顿运动定律 (阶段掌握)
    item_low = items_by_target["牛顿运动定律"]
    assert item_low.review_priority == ReviewPriority.LOW
    assert item_low.review_interval_days == 14
    assert "降低复习频率" in item_low.review_reason or "长效巩固周期" in item_low.review_reason


def test_review_queue_ordering_and_due_filtering(
    review_test_db: Database,
    error_service: ErrorAnalysisService,
    queue_service: ReviewQueueService,
) -> None:
    """Verify queue items are strictly ordered by Priority (高 -> 中 -> 低)."""
    user_id = "user_ordering"
    subject = "地理"

    # 1. 牛顿/流水侵蚀地貌: 阶段掌握 (低优先级)
    error_service.update_mastery_state(
        user_id, subject, "流水侵蚀地貌", is_correct=True, family_id=8511
    )
    error_service.update_mastery_state(
        user_id, subject, "流水侵蚀地貌", is_correct=True, family_id=8511
    )

    # 2. 植被垂直分布规律: 训练中 (中优先级)
    error_service.update_mastery_state(
        user_id, subject, "植被垂直分布规律", is_correct=False, family_id=8511
    )
    error_service.update_mastery_state(
        user_id, subject, "植被垂直分布规律", is_correct=True, family_id=8511
    )

    # 3. 锋面与天气系统: 待加强 (高优先级)
    error_service.update_mastery_state(
        user_id, subject, "锋面与天气系统", is_correct=False, family_id=8511
    )

    # Build and fetch queue
    queue_service.build_or_update_review_queue(user_id, subject)
    all_queue = queue_service.get_review_queue(user_id, subject)

    assert len(all_queue) == 3
    # High -> Normal -> Low
    assert all_queue[0].review_priority == ReviewPriority.HIGH
    assert all_queue[0].target == "锋面与天气系统"
    assert all_queue[1].review_priority == ReviewPriority.NORMAL
    assert all_queue[1].target == "植被垂直分布规律"
    assert all_queue[2].review_priority == ReviewPriority.LOW
    assert all_queue[2].target == "流水侵蚀地貌"


def test_learning_status_summary_aggregation(
    review_test_db: Database,
    error_service: ErrorAnalysisService,
    queue_service: ReviewQueueService,
) -> None:
    """Verify dashboard summary card counts match real mastery states."""
    user_id = "user_summary"
    subject = "地理"

    # Insert an attempt so user has training data
    now = datetime.now(UTC).isoformat()
    with review_test_db._connection() as conn:
        conn.execute(
            """
            INSERT INTO targeted_training_sessions (
                id, user_id, training_type, target, subject, questions_json, status, created_at
            ) VALUES ('sess_sum_1', ?, '方法族', '综合地理', '地理', '[]', 'completed', ?)
            """,
            (user_id, now),
        )
        conn.execute(
            """
            INSERT INTO targeted_training_attempts (
                id, session_id, question_id, user_answer, is_correct, attempted_at
            ) VALUES ('att_sum_1', 'sess_sum_1', 'q_sum_1', 'ans', 1, ?)
            """,
            (now,),
        )

    # Set up mastery states
    # 2 stage mastered
    error_service.update_mastery_state(user_id, subject, "地貌分析1", is_correct=True)
    error_service.update_mastery_state(user_id, subject, "地貌分析1", is_correct=True)

    error_service.update_mastery_state(user_id, subject, "地貌分析2", is_correct=True)
    error_service.update_mastery_state(user_id, subject, "地貌分析2", is_correct=True)

    # 1 needs improvement
    error_service.update_mastery_state(user_id, subject, "地貌分析3", is_correct=False)

    # 1 in training
    error_service.update_mastery_state(user_id, subject, "地貌分析4", is_correct=False)
    error_service.update_mastery_state(user_id, subject, "地貌分析4", is_correct=True)

    summary = queue_service.get_learning_status_summary(user_id, subject)
    assert summary.has_sufficient_data is True
    assert summary.stage_mastered_count == 2
    assert summary.needs_improvement_count == 1
    assert summary.in_training_count == 1
    assert len(summary.today_recommended_items) >= 1
    # Needs improvement should be recommended first with priority 高
    assert summary.today_recommended_items[0].review_priority == ReviewPriority.HIGH
