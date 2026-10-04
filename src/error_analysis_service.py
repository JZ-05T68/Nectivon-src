"""Service for Error Analysis, Variant Practice, and Mastery State (Phase 6).

Governs:
1. ErrorAnalysis persistence and retrieval.
2. ErrorProfile generation based strictly on authentic learner records (zero fake profiles).
3. Frequent error trend identification (multiple errors on same target/method).
4. MasteryState history-aware evolution: 未建立 -> 训练中 -> 待加强 -> 阶段掌握.
5. Variant training task generation from error causes with authentic question matching.
6. Strict cross-discipline data isolation.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.database import Database
from src.error_analysis_models import (
    ErrorAnalysis,
    ErrorCategory,
    ErrorProfile,
    FrequentErrorTag,
    MasteryLevel,
    MasteryState,
)
from src.question_source_models import VerifiedQuestion
from src.targeted_training_models import TrainingTask
from src.training_session_models import ErrorType, QuestionAttempt, TrainingSession

LOGGER = logging.getLogger(__name__)

FREQUENT_ERROR_THRESHOLD = 2


class ErrorAnalysisService:
    """Service handling error diagnostics, tendency analysis, and mastery updates."""

    def __init__(self, database: Database | Path | str) -> None:
        if isinstance(database, (str, Path)):
            self._db = Database(Path(database))
        else:
            self._db = database
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Initialize SQLite tables for error analysis and mastery states."""
        with self._db._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS targeted_training_error_analyses (
                    id TEXT PRIMARY KEY,
                    question_id TEXT NOT NULL,
                    attempt_id TEXT,
                    session_id TEXT NOT NULL,
                    is_error INTEGER NOT NULL,
                    error_type TEXT,
                    user_note TEXT,
                    associated_target TEXT NOT NULL,
                    training_type TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    family_id INTEGER,
                    question_item_id INTEGER,
                    applicable_scope TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS targeted_training_mastery_states (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    target TEXT NOT NULL,
                    family_id INTEGER,
                    level TEXT NOT NULL,
                    total_attempts INTEGER DEFAULT 0,
                    correct_attempts INTEGER DEFAULT 0,
                    consecutive_correct INTEGER DEFAULT 0,
                    consecutive_errors INTEGER DEFAULT 0,
                    last_attempt_is_correct INTEGER,
                    last_error_type TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, subject, target)
                )
                """
            )

    def record_attempt_analysis(
        self,
        session: TrainingSession,
        question: VerifiedQuestion,
        attempt: QuestionAttempt,
    ) -> ErrorAnalysis:
        """Record structured error analysis and update target mastery state."""
        is_error = not attempt.is_correct

        err_cat_label: str | None = None
        if attempt.error_type:
            err_cat_label = ErrorCategory.from_error_type(attempt.error_type).value
        elif is_error:
            err_cat_label = ErrorCategory.OTHER.value

        target = session.task.target
        subject = question.subject or session.task.subject or "未知"
        family_id = question.family_id or session.task.family_id
        now = datetime.now(UTC).isoformat()
        analysis_id = f"err_{session.id}_{question.id}_{attempt.submission_count}"

        analysis = ErrorAnalysis(
            id=analysis_id,
            question_id=question.id,
            attempt_id=f"att_{session.id}_{question.id}",
            session_id=session.id,
            is_error=is_error,
            error_type=err_cat_label if is_error else None,
            user_note=attempt.error_analysis,
            associated_target=target,
            training_type=session.task.training_type,
            subject=subject,
            family_id=family_id,
            question_item_id=question.question_item_id,
            applicable_scope=question.applicable_scope,
            created_at=now,
        )

        with self._db._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO targeted_training_error_analyses (
                    id, question_id, attempt_id, session_id, is_error,
                    error_type, user_note, associated_target, training_type,
                    subject, family_id, question_item_id, applicable_scope, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis.id,
                    analysis.question_id,
                    analysis.attempt_id,
                    analysis.session_id,
                    1 if analysis.is_error else 0,
                    analysis.error_type,
                    analysis.user_note,
                    analysis.associated_target,
                    analysis.training_type,
                    analysis.subject,
                    analysis.family_id,
                    analysis.question_item_id,
                    analysis.applicable_scope,
                    analysis.created_at,
                ),
            )

        # Update mastery state
        self.update_mastery_state(
            user_id=session.user_id,
            subject=subject,
            target=target,
            is_correct=attempt.is_correct,
            error_type=err_cat_label,
            family_id=family_id,
        )

        return analysis

    def update_error_attribution(
        self,
        session_id: str,
        question_id: str,
        error_type: ErrorType | str,
        user_note: str = "",
    ) -> ErrorAnalysis | None:
        """Update error type and user note for an existing attempt analysis."""
        err_cat = ErrorCategory.from_error_type(error_type).value
        with self._db._connection() as conn:
            row = conn.execute(
                """
                SELECT id FROM targeted_training_error_analyses
                WHERE session_id = ? AND question_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (session_id, question_id),
            ).fetchone()
            if not row:
                return None
            record_id = row["id"]
            conn.execute(
                """
                UPDATE targeted_training_error_analyses
                SET error_type = ?, user_note = ?
                WHERE id = ?
                """,
                (err_cat, user_note, record_id),
            )
            # Refetch updated
            updated_row = conn.execute(
                "SELECT * FROM targeted_training_error_analyses WHERE id = ?",
                (record_id,),
            ).fetchone()
            if not updated_row:
                return None

            # Sync last_error_type to mastery state if matching
            conn.execute(
                """
                UPDATE targeted_training_mastery_states
                SET last_error_type = ?
                WHERE subject = ? AND target = ?
                """,
                (err_cat, updated_row["subject"], updated_row["associated_target"]),
            )
            return self._row_to_error_analysis(updated_row)

    def get_error_analyses(
        self,
        subject: str | None = None,
        target: str | None = None,
        is_error_only: bool = True,
    ) -> list[ErrorAnalysis]:
        """Retrieve error analyses with optional discipline or target filtering."""
        query = "SELECT * FROM targeted_training_error_analyses WHERE 1=1"
        params: list[Any] = []
        if is_error_only:
            query += " AND is_error = 1"
        if subject:
            query += " AND subject = ?"
            params.append(subject)
        if target:
            query += " AND associated_target = ?"
            params.append(target)
        query += " ORDER BY created_at DESC"

        with self._db._connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            return [self._row_to_error_analysis(r) for r in rows]

    def generate_error_profile(
        self, user_id: str, subject: str
    ) -> ErrorProfile:
        """Generate structured error profile strictly from authentic history.

        Zero-hallucination invariant:
        Returns has_sufficient_data=False when no training history exists.
        Strictly isolates subject data (e.g. Geography errors never bleed into Physics).
        """
        with self._db._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM targeted_training_error_analyses
                WHERE subject = ?
                ORDER BY created_at ASC
                """,
                (subject,),
            ).fetchall()

        total_attempts = len(rows)
        if total_attempts == 0:
            return ErrorProfile(
                subject=subject,
                has_sufficient_data=False,
                message="暂无足够训练数据，无法生成错因画像",
            )

        error_rows = [r for r in rows if r["is_error"] == 1]
        total_errors = len(error_rows)
        if total_errors == 0:
            return ErrorProfile(
                subject=subject,
                has_sufficient_data=True,
                total_attempts=total_attempts,
                total_errors=0,
                message="当前学科无错误记录，掌握情况良好",
            )

        distribution_by_type: dict[str, int] = {}
        distribution_by_target: dict[str, dict[str, int]] = {}

        for er in error_rows:
            etype = er["error_type"] or ErrorCategory.OTHER.value
            target = er["associated_target"] or "通用"

            distribution_by_type[etype] = distribution_by_type.get(etype, 0) + 1

            if target not in distribution_by_target:
                distribution_by_target[target] = {}
            target_dict = distribution_by_target[target]
            target_dict[etype] = target_dict.get(etype, 0) + 1

        # Identify frequent errors (>= threshold)
        frequent_errors: list[FrequentErrorTag] = []
        for target, type_counts in distribution_by_target.items():
            for etype, count in type_counts.items():
                if count >= FREQUENT_ERROR_THRESHOLD:
                    frequent_errors.append(
                        FrequentErrorTag(
                            target=target,
                            error_type=etype,
                            count=count,
                            subject=subject,
                        )
                    )

        # Primary weakness determination
        primary_weakness: str | None = None
        if frequent_errors:
            top_tag = max(frequent_errors, key=lambda t: t.count)
            primary_weakness = f"{top_tag.target}{top_tag.error_type}"
        elif total_errors > 0:
            # Single error or multiple distinct errors without reaching frequency threshold
            top_type = max(distribution_by_type.items(), key=lambda kv: kv[1])[0]
            top_target = max(
                distribution_by_target.items(),
                key=lambda kv: sum(kv[1].values()),
            )[0]
            primary_weakness = f"{top_target}{top_type}"

        return ErrorProfile(
            subject=subject,
            has_sufficient_data=True,
            total_attempts=total_attempts,
            total_errors=total_errors,
            error_distribution_by_type=distribution_by_type,
            error_distribution_by_target=distribution_by_target,
            frequent_errors=frequent_errors,
            primary_weakness=primary_weakness,
            message=f"已基于 {total_attempts} 次真实训练记录完成错因画像分析",
        )

    def update_mastery_state(
        self,
        user_id: str,
        subject: str,
        target: str,
        is_correct: bool,
        error_type: str | None = None,
        family_id: int | None = None,
    ) -> MasteryState:
        """Update mastery progression based on sequential historical evidence.

        Principles:
        - 1 correct does not declare stage mastery.
        - 1 error does not declare complete inability.
        - Progression: 未建立 -> 训练中 -> 待加强 -> 阶段掌握.
        """
        current = self.get_mastery_state(user_id, subject, target)
        now = datetime.now(UTC).isoformat()

        new_total = current.total_attempts + 1
        family_id_to_store = family_id or current.family_id

        if is_correct:
            new_correct = current.correct_attempts + 1
            new_consec_correct = current.consecutive_correct + 1
            new_consec_errors = 0
            new_last_correct = True
            new_last_error = current.last_error_type

            # Mastery state progression
            if new_consec_correct >= 2:
                new_level = MasteryLevel.STAGE_MASTERED
            elif current.level == MasteryLevel.NOT_ESTABLISHED:
                new_level = MasteryLevel.IN_TRAINING
            elif current.level == MasteryLevel.NEEDS_IMPROVEMENT:
                new_level = MasteryLevel.IN_TRAINING
            else:
                new_level = current.level
        else:
            new_correct = current.correct_attempts
            new_consec_correct = 0
            new_consec_errors = current.consecutive_errors + 1
            new_last_correct = False
            new_last_error = error_type

            # Error causes fallback to needs_improvement
            new_level = MasteryLevel.NEEDS_IMPROVEMENT

        state_id = f"mst_{user_id}_{subject}_{target}"
        updated_state = MasteryState(
            user_id=user_id,
            subject=subject,
            target=target,
            level=new_level,
            total_attempts=new_total,
            correct_attempts=new_correct,
            consecutive_correct=new_consec_correct,
            consecutive_errors=new_consec_errors,
            last_attempt_is_correct=new_last_correct,
            last_error_type=new_last_error,
            family_id=family_id_to_store,
            updated_at=now,
        )

        with self._db._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO targeted_training_mastery_states (
                    id, user_id, subject, target, family_id, level,
                    total_attempts, correct_attempts, consecutive_correct,
                    consecutive_errors, last_attempt_is_correct, last_error_type, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state_id,
                    user_id,
                    subject,
                    target,
                    family_id_to_store,
                    str(new_level),
                    new_total,
                    new_correct,
                    new_consec_correct,
                    new_consec_errors,
                    1 if new_last_correct else 0,
                    new_last_error,
                    now,
                ),
            )

        LOGGER.info(
            "掌握度更新 [%s - %s]: %s -> %s (连续正确: %d, 连续错误: %d)",
            subject,
            target,
            current.level,
            new_level,
            new_consec_correct,
            new_consec_errors,
        )
        return updated_state

    def get_mastery_state(
        self, user_id: str, subject: str, target: str
    ) -> MasteryState:
        """Fetch mastery state for user/subject/target, returning default if unestablished."""
        with self._db._connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM targeted_training_mastery_states
                WHERE user_id = ? AND subject = ? AND target = ?
                """,
                (user_id, subject, target),
            ).fetchone()
            if not row:
                return MasteryState(
                    user_id=user_id,
                    subject=subject,
                    target=target,
                    level=MasteryLevel.NOT_ESTABLISHED,
                )
            return self._row_to_mastery_state(row)

    def list_mastery_states_by_subject(
        self, user_id: str, subject: str
    ) -> list[MasteryState]:
        """List all mastery states for a user in a given discipline."""
        with self._db._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM targeted_training_mastery_states
                WHERE user_id = ? AND subject = ?
                ORDER BY updated_at DESC
                """,
                (user_id, subject),
            ).fetchall()
            return [self._row_to_mastery_state(r) for r in rows]

    def create_variant_training_task(self, error_id: str) -> TrainingTask:
        """Create a new TrainingTask for variant practice targeting an error record."""
        with self._db._connection() as conn:
            row = conn.execute(
                "SELECT * FROM targeted_training_error_analyses WHERE id = ?",
                (error_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"未找到错因分析记录：{error_id}")

        analysis = self._row_to_error_analysis(row)
        return TrainingTask(
            training_type=analysis.training_type,
            target=analysis.associated_target,
            subject=analysis.subject,
            family_id=analysis.family_id,
        )

    @staticmethod
    def _row_to_error_analysis(row: Any) -> ErrorAnalysis:
        return ErrorAnalysis(
            id=row["id"],
            question_id=row["question_id"],
            attempt_id=row["attempt_id"],
            session_id=row["session_id"],
            is_error=bool(row["is_error"]),
            error_type=row["error_type"],
            user_note=row["user_note"] or "",
            associated_target=row["associated_target"],
            training_type=row["training_type"],
            subject=row["subject"],
            family_id=row["family_id"],
            question_item_id=row["question_item_id"],
            applicable_scope=row["applicable_scope"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_mastery_state(row: Any) -> MasteryState:
        lvl_raw = row["level"]
        level = (
            MasteryLevel(lvl_raw)
            if lvl_raw in list(MasteryLevel)
            else MasteryLevel.NOT_ESTABLISHED
        )
        last_correct = (
            bool(row["last_attempt_is_correct"])
            if row["last_attempt_is_correct"] is not None
            else None
        )
        return MasteryState(
            user_id=row["user_id"],
            subject=row["subject"],
            target=row["target"],
            level=level,
            total_attempts=row["total_attempts"],
            correct_attempts=row["correct_attempts"],
            consecutive_correct=row["consecutive_correct"],
            consecutive_errors=row["consecutive_errors"],
            last_attempt_is_correct=last_correct,
            last_error_type=row["last_error_type"],
            family_id=row["family_id"],
            updated_at=row["updated_at"],
        )
