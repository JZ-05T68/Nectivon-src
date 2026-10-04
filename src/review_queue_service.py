"""Service for Targeted Training Review Queue & Spaced Retrieval (Phase 7).

Manages:
1. Review queue generation based on real MasteryState and error patterns.
2. Dynamic priority assignment:
   - 待加强 -> 高优先级，短间隔（1天），近期失误预警
   - 训练中 -> 中优先级，标准间隔（3天）
   - 阶段掌握 -> 低优先级，长间隔（14天）
3. Today's learning status dashboard summaries.
4. Zero-data protection: refuses to hallucinate review tasks or weaknesses without data.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.database import Database
from src.error_analysis_models import MasteryLevel
from src.error_analysis_service import ErrorAnalysisService
from src.review_queue_models import (
    ReviewPriority,
    ReviewQueueItem,
    ReviewQueueSummary,
)

LOGGER = logging.getLogger(__name__)


class ReviewQueueService:
    """Service managing targeted training review queue scheduling and summaries."""

    def __init__(
        self,
        database: Database | Path | str,
        error_service: ErrorAnalysisService | None = None,
    ) -> None:
        if isinstance(database, (str, Path)):
            self._db = Database(Path(database))
        else:
            self._db = database
        self._ensure_schema()
        self._error_service = error_service or ErrorAnalysisService(self._db)

    def _ensure_schema(self) -> None:
        """Initialize SQLite tables for targeted training review queue."""
        with self._db._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS targeted_training_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    task_id INTEGER,
                    training_type TEXT NOT NULL,
                    target TEXT NOT NULL,
                    subject TEXT,
                    questions_json TEXT NOT NULL,
                    current_question_index INTEGER DEFAULT 0,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS targeted_training_attempts (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    question_id TEXT NOT NULL,
                    user_answer TEXT NOT NULL,
                    reference_answer TEXT,
                    is_correct INTEGER NOT NULL,
                    error_type TEXT,
                    error_analysis TEXT,
                    method_reinforcement TEXT,
                    submission_count INTEGER DEFAULT 1,
                    attempted_at TEXT NOT NULL,
                    question_item_id INTEGER,
                    family_id INTEGER,
                    UNIQUE(session_id, question_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS targeted_training_review_queue (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    target TEXT NOT NULL,
                    family_id INTEGER,
                    training_type TEXT NOT NULL,
                    mastery_level TEXT NOT NULL,
                    review_priority TEXT NOT NULL,
                    review_interval_days INTEGER NOT NULL,
                    next_review_at TEXT NOT NULL,
                    review_reason TEXT NOT NULL,
                    last_trained_at TEXT,
                    last_error_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, subject, target)
                )
                """
            )

    def build_or_update_review_queue(
        self,
        user_id: str,
        subject: str | None = None,
    ) -> list[ReviewQueueItem]:
        """Build or update review queue items based on real mastery state progression."""
        # Query active mastery states where training has started
        with self._db._connection() as conn:
            query = (
                """
                SELECT * FROM targeted_training_mastery_states
                WHERE user_id = ? AND total_attempts > 0
                """
            )
            params: list[Any] = [user_id]
            if subject:
                query += " AND subject = ?"
                params.append(subject)

            mastery_rows = conn.execute(query, tuple(params)).fetchall()

        if not mastery_rows:
            return []

        items: list[ReviewQueueItem] = []
        now = datetime.now(UTC)
        now_iso = now.isoformat()

        for m in mastery_rows:
            subj = m["subject"]
            target = m["target"]
            lvl_str = m["level"]
            consec_err = m["consecutive_errors"]
            last_err_type = m["last_error_type"]
            fam_id = m["family_id"]

            # Query latest attempt and latest error timestamps
            with self._db._connection() as conn:
                last_att_row = conn.execute(
                    """
                    SELECT a.attempted_at, s.training_type
                    FROM targeted_training_attempts a
                    JOIN targeted_training_sessions s ON a.session_id = s.id
                    WHERE s.user_id = ? AND s.target = ? AND (s.subject = ? OR ? IS NULL)
                    ORDER BY a.attempted_at DESC LIMIT 1
                    """,
                    (user_id, target, subj, subj),
                ).fetchone()

                last_err_row = conn.execute(
                    """
                    SELECT created_at FROM targeted_training_error_analyses
                    WHERE associated_target = ? AND is_error = 1 AND subject = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (target, subj),
                ).fetchone()

            last_trained_at = (
                last_att_row["attempted_at"] if last_att_row else m["updated_at"]
            )
            training_type = (
                last_att_row["training_type"] if last_att_row else "方法族"
            )
            last_error_at = last_err_row["created_at"] if last_err_row else None

            # Determine scheduling parameters based on MasteryLevel
            if lvl_str == str(MasteryLevel.STAGE_MASTERED):
                interval = 14
                priority = ReviewPriority.LOW
                reason = "已达阶段掌握，进入长效巩固周期，降低复习频率"
            elif lvl_str == str(MasteryLevel.NEEDS_IMPROVEMENT):
                interval = 1
                priority = ReviewPriority.HIGH
                if consec_err >= 2:
                    reason = (
                        f"近期连续失误 {consec_err} 次"
                        f"（主因：{last_err_type or '需巩固'}），提高复习优先级"
                    )
                else:
                    reason = (
                        f"近期出现失误（主因：{last_err_type or '需巩固'}），"
                        "建议尽快进行针对巩固"
                    )
            elif lvl_str == str(MasteryLevel.IN_TRAINING):
                interval = 3
                priority = ReviewPriority.NORMAL
                reason = "处于训练积累期，保持正常频率巩固核心解题方法"
            else:
                # NOT_ESTABLISHED -> skip review queue
                continue

            # Calculate next review date
            try:
                base_dt = datetime.fromisoformat(last_trained_at)
            except Exception:
                base_dt = now
            next_review_dt = base_dt + timedelta(days=interval)
            next_review_str = next_review_dt.strftime("%Y-%m-%d")

            item_id = f"rev_{user_id}_{subj}_{target}"
            item = ReviewQueueItem(
                id=item_id,
                user_id=user_id,
                subject=subj,
                target=target,
                family_id=fam_id,
                training_type=training_type,
                mastery_level=lvl_str,
                review_priority=priority,
                review_interval_days=interval,
                next_review_at=next_review_str,
                review_reason=reason,
                last_trained_at=last_trained_at,
                last_error_at=last_error_at,
                created_at=now_iso,
                updated_at=now_iso,
            )

            with self._db._connection() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO targeted_training_review_queue (
                        id, user_id, subject, target, family_id, training_type,
                        mastery_level, review_priority, review_interval_days,
                        next_review_at, review_reason, last_trained_at,
                        last_error_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.id,
                        item.user_id,
                        item.subject,
                        item.target,
                        item.family_id,
                        item.training_type,
                        item.mastery_level,
                        str(item.review_priority),
                        item.review_interval_days,
                        item.next_review_at,
                        item.review_reason,
                        item.last_trained_at,
                        item.last_error_at,
                        item.created_at,
                        item.updated_at,
                    ),
                )
            items.append(item)

        return items

    def get_review_queue(
        self,
        user_id: str,
        subject: str | None = None,
        sync_from_mastery: bool = True,
    ) -> list[ReviewQueueItem]:
        """Fetch current review queue ordered by priority and review due date."""
        if sync_from_mastery:
            self.build_or_update_review_queue(user_id, subject)

        query = (
            "SELECT * FROM targeted_training_review_queue WHERE user_id = ?"
        )
        params: list[Any] = [user_id]
        if subject:
            query += " AND subject = ?"
            params.append(subject)

        # Ordering: HIGH ('高') first, then NORMAL ('中'), then LOW ('低')
        query += (
            " ORDER BY CASE review_priority "
            "   WHEN '高' THEN 1 WHEN '中' THEN 2 WHEN '低' THEN 3 ELSE 4 END ASC, "
            " next_review_at ASC"
        )

        with self._db._connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            return [ReviewQueueItem.from_dict(dict(r)) for r in rows]

    def get_learning_status_summary(
        self,
        user_id: str,
        subject: str | None = None,
    ) -> ReviewQueueSummary:
        """Calculate aggregated learning status and review recommendations."""
        # 1. Zero data verification: check if attempts exist
        with self._db._connection() as conn:
            query = (
                """
                SELECT COUNT(*) as count FROM targeted_training_attempts a
                JOIN targeted_training_sessions s ON a.session_id = s.id
                WHERE s.user_id = ?
                """
            )
            params: list[Any] = [user_id]
            if subject:
                query += " AND s.subject = ?"
                params.append(subject)
            cnt_row = conn.execute(query, tuple(params)).fetchone()
            total_attempts = cnt_row["count"] if cnt_row else 0

        subj_label = subject or "全学科"
        if total_attempts == 0:
            return ReviewQueueSummary(
                subject=subj_label,
                total_items=0,
                has_sufficient_data=False,
                message="暂无足够学习数据，未生成复习计划与画像",
            )

        # Ensure review queue is up to date
        self.build_or_update_review_queue(user_id, subject)
        queue_items = self.get_review_queue(user_id, subject)

        stage_mastered = sum(
            1 for item in queue_items if item.mastery_level == "阶段掌握"
        )
        needs_improvement = sum(
            1 for item in queue_items if item.mastery_level == "待加强"
        )
        in_training = sum(
            1 for item in queue_items if item.mastery_level == "训练中"
        )

        # Top recommendations: priority given to '待加强' (HIGH)
        today_recs = queue_items[:4]

        return ReviewQueueSummary(
            subject=subj_label,
            total_items=len(queue_items),
            stage_mastered_count=stage_mastered,
            needs_improvement_count=needs_improvement,
            in_training_count=in_training,
            today_recommended_items=today_recs,
            has_sufficient_data=True,
            message="复习计划已基于当前真实掌握状态生成",
        )
