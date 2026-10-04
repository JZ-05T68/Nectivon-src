"""Persistence and execution service for Targeted Training Sessions (Phase 5).

Manages:
1. TrainingSession lifecycle and SQLite persistence.
2. QuestionAttempt recording, repeat submission tracking, and empty-answer rejection.
3. Diagnostic error attribution and method reinforcement consolidation.
4. Session result calculation and feedback into Layer 1 Three-Tier Organization (mastery_evidence).
5. Resume-after-exit midway without losing progress.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.database import Database
from src.error_analysis_service import ErrorAnalysisService
from src.question_source_models import VerifiedQuestion
from src.targeted_training_models import TrainingTask
from src.training_session_models import (
    ErrorType,
    QuestionAttempt,
    SessionStatus,
    TrainingResult,
    TrainingSession,
)

LOGGER = logging.getLogger(__name__)


def _generate_method_reinforcement(
    question: VerifiedQuestion, task: TrainingTask
) -> str:
    """Generate targeted method reinforcement tip based on task and question attributes."""
    target = task.target.strip()
    subject = question.subject or task.subject or ""
    scope = question.applicable_scope or ""

    if "竞赛" in scope or question.contest_tier or question.contest_name:
        return (
            "【方法强化 · 学科竞赛高阶方法】"
            "1. 识别守恒量与绝热不变量，利用微扰理论分析临界对称破缺；"
            "2. 建立广义坐标与拉格朗日量，避免繁杂直角坐标受力分解；"
            "3. 检验极限情形与渐近行为，快速验证解析解自洽性。"
        )

    if subject == "地理" or "地理" in target or "地貌" in target or "区域" in target:
        return (
            "【方法强化 · 综合地理区域分析】"
            "1. 运用综合思维，遵循'位置-地貌-水文-生物-土壤'自然地理要素链式推导；"
            "2. 分析侵蚀沉积（如流水侵蚀、红层构造、崖壁凹槽）与地质演化过程时，抓住主导动力；"
            "3. 答题表述注重完整因果链条，避免跳跃性定论。"
        )

    if "控制" in target or "机器人" in target or subject in ("工学", "自动控制原理"):
        return (
            "【方法强化 · 高等工程与现代控制系统设计】"
            "1. 首先验算能控性矩阵与能观性矩阵秩 rank[B AB ... A^(n-1)B]；"
            "2. 建立状态空间方程，明确期望极点位置，利用 Ackermann 公式构造状态反馈增益矩阵 K；"
            "3. 注意状态观测器极点衰减速率应快于控制器极点2-5倍以确保估计精度。"
        )

    if "动量" in target or subject == "物理":
        return (
            "【方法强化 · 动量定理与守恒】"
            "1. 明确选定系统（物块与小车或单个质点），严格规定一维正方向与受力分析；"
            "2. 抓住过程分解与冲量矢量性：I_合 = Δp = m*v_终 - m*v_初；"
            "3. 作用过程中若水平方向不受外力，优先利用动量守恒建立初末状态关联。"
        )

    if subject == "数学":
        return (
            f"【方法强化 · {target}】"
            "1. 先圈出题目里的运算符号、括号和已知条件；"
            "2. 按初中数学的运算顺序逐步写清，每一步只做一件事；"
            "3. 做完后把结果代回题意，重点检查符号、括号和单位是否遗漏。"
        )

    return (
        f"【方法强化 · {target}】"
        "1. 审题时标记已知边界条件与物理/学科量符号；"
        "2. 依据基本守恒定理或演变机理列出核心主导方程；"
        "3. 求解后检验极值条件与量纲一致性。"
    )


class TrainingSessionService:
    """Service handling training execution sessions, attempts, and feedback."""

    def __init__(
        self,
        database: Database | Path | str,
        error_analysis_service: ErrorAnalysisService | None = None,
    ) -> None:
        if isinstance(database, (str, Path)):
            self._db = Database(Path(database))
        else:
            self._db = database
        self._ensure_schema()
        self._error_service = error_analysis_service or ErrorAnalysisService(self._db)

    def _method_reinforcement(
        self, question: VerifiedQuestion, task: TrainingTask
    ) -> str:
        """Prefer the user's reviewed method-family notes over generic advice."""

        family_id = question.family_id or task.family_id
        if family_id is not None:
            with self._db._connection() as conn:
                row = conn.execute(
                    "SELECT family_kind, title, description, variant_pattern, "
                    "confusion_notes FROM question_families "
                    "WHERE id = ? AND status != 'retired'",
                    (family_id,),
                ).fetchone()
            if row is not None and str(row["family_kind"]) == "method":
                sections = [str(row["description"] or "").strip()]
                variant = str(row["variant_pattern"] or "").strip()
                confusion = str(row["confusion_notes"] or "").strip()
                if variant:
                    sections.append(f"练习提醒：{variant}")
                if confusion:
                    sections.append(f"易混淆：{confusion}")
                content = " ".join(section for section in sections if section)
                if content:
                    return f"【方法强化 · {row['title']}】{content}"
        return _generate_method_reinforcement(question, task)

    @property
    def error_service(self) -> ErrorAnalysisService:
        """Return the wired ErrorAnalysisService."""
        return self._error_service

    def _ensure_schema(self) -> None:
        """Initialize SQLite tables for sessions, attempts, and results."""
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
                CREATE TABLE IF NOT EXISTS targeted_training_results (
                    session_id TEXT PRIMARY KEY,
                    task_target TEXT NOT NULL,
                    training_type TEXT NOT NULL,
                    subject TEXT,
                    total_questions INTEGER NOT NULL,
                    attempted_count INTEGER NOT NULL,
                    correct_count INTEGER NOT NULL,
                    accuracy_rate REAL NOT NULL,
                    error_distribution_json TEXT NOT NULL,
                    method_summary_json TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                )
                """
            )

    def create_session(
        self,
        task: TrainingTask,
        questions: list[VerifiedQuestion],
        user_id: str = "default_local_user",
    ) -> TrainingSession:
        """Create a new training session with verified questions."""
        if not questions:
            raise ValueError("无法为没有任何已验证题目的任务创建训练计划")

        session_id = f"ts_{uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()
        session = TrainingSession(
            id=session_id,
            task=task,
            questions=questions,
            user_id=user_id,
            current_question_index=0,
            status=SessionStatus.CREATED,
            attempts={},
            created_at=now,
        )

        with self._db._connection() as conn:
            conn.execute(
                """
                INSERT INTO targeted_training_sessions (
                    id, user_id, task_id, training_type, target, subject,
                    questions_json, current_question_index, status, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    session.id,
                    session.user_id,
                    task.family_id,
                    task.training_type,
                    task.target,
                    task.subject,
                    json.dumps([q.to_dict() for q in questions], ensure_ascii=False),
                    0,
                    str(SessionStatus.CREATED),
                    now,
                ),
            )

        LOGGER.info("已创建针对训练 Session: %s，题目数量: %d", session_id, len(questions))
        return session

    def get_session(self, session_id: str) -> TrainingSession | None:
        """Retrieve a session by its ID with all recorded attempts."""
        with self._db._connection() as conn:
            row = conn.execute(
                "SELECT * FROM targeted_training_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                return None

            questions_raw = json.loads(row["questions_json"])
            task = TrainingTask(
                training_type=row["training_type"],
                target=row["target"],
                subject=row["subject"],
                family_id=row["task_id"],
            )

            # Load attempts
            att_rows = conn.execute(
                "SELECT * FROM targeted_training_attempts WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            attempts: dict[str, QuestionAttempt] = {}
            for ar in att_rows:
                err_raw = ar["error_type"]
                err_type = ErrorType(err_raw) if err_raw else None
                attempts[ar["question_id"]] = QuestionAttempt(
                    session_id=ar["session_id"],
                    question_id=ar["question_id"],
                    user_answer=ar["user_answer"],
                    reference_answer=ar["reference_answer"] or "",
                    is_correct=bool(ar["is_correct"]),
                    error_type=err_type,
                    error_analysis=ar["error_analysis"] or "",
                    method_reinforcement=ar["method_reinforcement"] or "",
                    submission_count=int(ar["submission_count"]),
                    attempted_at=ar["attempted_at"],
                    question_item_id=ar["question_item_id"],
                    family_id=ar["family_id"],
                )

            session_data = {
                "id": row["id"],
                "user_id": row["user_id"],
                "task": task.to_dict(),
                "questions": questions_raw,
                "current_question_index": row["current_question_index"],
                "status": row["status"],
                "attempts": {qid: a.to_dict() for qid, a in attempts.items()},
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
            }
            return TrainingSession.from_dict(session_data)

    def get_active_session_for_task(
        self, task: TrainingTask, user_id: str = "default_local_user"
    ) -> TrainingSession | None:
        """Find an uncompleted session for this task to resume midway."""
        with self._db._connection() as conn:
            row = conn.execute(
                """
                SELECT id FROM targeted_training_sessions
                WHERE user_id = ? AND training_type = ? AND target = ?
                  AND status IN (?, ?)
                ORDER BY created_at DESC LIMIT 1
                """,
                (
                    user_id,
                    task.training_type,
                    task.target,
                    str(SessionStatus.CREATED),
                    str(SessionStatus.IN_PROGRESS),
                ),
            ).fetchone()
            if row:
                return self.get_session(row["id"])
        return None

    def evaluate_and_record_attempt(
        self,
        session_id: str,
        question_id: str,
        user_answer: str,
        is_correct: bool | None = None,
        error_type: ErrorType | None = None,
        error_analysis: str = "",
    ) -> QuestionAttempt:
        """Evaluate and record an attempt.

        Rejects empty answer submissions. Increments submission count on repeat attempts.
        Provides method reinforcement and pushes evidence to Layer 1 organization.
        """
        clean_answer = user_answer.strip()
        if not clean_answer:
            raise ValueError("请先填写答案。")

        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"未找到训练会话: {session_id}")

        # Locate question
        target_q = next((q for q in session.questions if q.id == question_id), None)
        if not target_q:
            raise ValueError(f"题目 {question_id} 不属于当前训练会话 {session_id}")

        # Determine correctness
        ref_ans = target_q.reference_answer.strip()
        if is_correct is None:
            # Automated comparison if short match, otherwise default to False pending confirmation
            if ref_ans and (clean_answer == ref_ans or clean_answer in ref_ans):
                final_correct = True
            else:
                final_correct = False
        else:
            final_correct = is_correct

        now = datetime.now(UTC).isoformat()
        method_tip = self._method_reinforcement(target_q, session.task)

        # Check existing attempt for repeat submission
        existing_attempt = session.get_attempt(question_id)
        if existing_attempt:
            submission_count = existing_attempt.submission_count + 1
        else:
            submission_count = 1

        attempt = QuestionAttempt(
            session_id=session_id,
            question_id=question_id,
            user_answer=clean_answer,
            reference_answer=ref_ans,
            is_correct=final_correct,
            error_type=error_type,
            error_analysis=error_analysis,
            method_reinforcement=method_tip,
            submission_count=submission_count,
            attempted_at=now,
            question_item_id=target_q.question_item_id,
            family_id=target_q.family_id or session.task.family_id,
        )

        with self._db._connection() as conn:
            # 1. Save or update attempt
            att_id = f"att_{session_id}_{question_id}"
            conn.execute(
                """
                INSERT OR REPLACE INTO targeted_training_attempts (
                    id, session_id, question_id, user_answer, reference_answer,
                    is_correct, error_type, error_analysis, method_reinforcement,
                    submission_count, attempted_at, question_item_id, family_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    att_id,
                    session_id,
                    question_id,
                    clean_answer,
                    ref_ans,
                    1 if final_correct else 0,
                    str(error_type) if error_type else None,
                    error_analysis,
                    method_tip,
                    submission_count,
                    now,
                    target_q.question_item_id,
                    attempt.family_id,
                ),
            )

            # 2. Update session status to in_progress if currently created
            conn.execute(
                """
                UPDATE targeted_training_sessions
                SET status = ?
                WHERE id = ? AND status = ?
                """,
                (
                    str(SessionStatus.IN_PROGRESS),
                    session_id,
                    str(SessionStatus.CREATED),
                ),
            )

            # 3. Layer 1 Three-Tier Organization Feedback (mastery_evidence)
            if target_q.question_item_id:
                self._record_mastery_evidence(conn, session, target_q, attempt)

        # Phase 6: Record ErrorAnalysis and update MasteryState
        try:
            self._error_service.record_attempt_analysis(session, target_q, attempt)
        except Exception as e:
            LOGGER.warning("记录错因分析与掌握度失败（不阻断主流程）：%s", e)

        LOGGER.info(
            "已记录作答 Session: %s, Question: %s, 正确: %s, 提交次数: %d",
            session_id,
            question_id,
            final_correct,
            submission_count,
        )
        return attempt

    def _record_mastery_evidence(
        self,
        conn: Any,
        session: TrainingSession,
        question: VerifiedQuestion,
        attempt: QuestionAttempt,
    ) -> None:
        """Push practice attempt into Layer 1 mastery_evidence table safely."""
        try:
            # Check if mastery_evidence table exists
            table_check = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='mastery_evidence'"
            ).fetchone()
            if not table_check:
                return

            idempotency_key = (
                f"targeted_{session.id}_{question.id}_{attempt.submission_count}"
            )
            result_str = "correct" if attempt.is_correct else "incorrect"
            err_text = attempt.error_type.label if attempt.error_type else "无"
            note = (
                f"【针对训练-{session.task.target}】作答："
                f"{attempt.user_answer[:40]} | 错因：{err_text}"
            )
            now_iso = datetime.now(UTC).isoformat()

            conn.execute(
                """
                INSERT OR REPLACE INTO mastery_evidence (
                    question_id, family_id, event_type, result, independence,
                    explanation_state, hint_used, source, provenance, user_note,
                    review_status, idempotency_key, created_at, updated_at
                ) VALUES (
                    ?, ?, 'practice', ?, 'independent', 'unverified',
                    0, 'system_training', 'targeted_training', ?, 'scheduled', ?, ?, ?
                )
                """,
                (
                    question.question_item_id,
                    attempt.family_id,
                    result_str,
                    note,
                    idempotency_key,
                    now_iso,
                    now_iso,
                ),
            )
        except Exception as e:
            LOGGER.warning("记录掌握度证据到 mastery_evidence 失败（不阻断主流程）：%s", e)

    def update_error_attribution(
        self,
        session_id: str,
        question_id: str,
        error_type: ErrorType,
        error_analysis: str = "",
    ) -> QuestionAttempt:
        """Update error cause classification and reflection note for an existing attempt."""
        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"未找到训练会话: {session_id}")
        attempt = session.get_attempt(question_id)
        if not attempt:
            raise ValueError(f"题目 {question_id} 尚未作答，无法归因错因")

        attempt.error_type = error_type
        attempt.error_analysis = error_analysis

        with self._db._connection() as conn:
            conn.execute(
                """
                UPDATE targeted_training_attempts
                SET error_type = ?, error_analysis = ?
                WHERE session_id = ? AND question_id = ?
                """,
                (str(error_type), error_analysis, session_id, question_id),
            )

        # Phase 6: Sync to ErrorAnalysis and MasteryState
        try:
            self._error_service.update_error_attribution(
                session_id=session_id,
                question_id=question_id,
                error_type=error_type,
                user_note=error_analysis,
            )
        except Exception as e:
            LOGGER.warning("更新错因归因记录失败（不阻断主流程）：%s", e)

        return attempt

    def advance_question(self, session_id: str, next_index: int) -> TrainingSession:
        """Update the active question index for the session."""
        with self._db._connection() as conn:
            conn.execute(
                """
                UPDATE targeted_training_sessions
                SET current_question_index = ?
                WHERE id = ?
                """,
                (next_index, session_id),
            )
        updated = self.get_session(session_id)
        if not updated:
            raise ValueError(f"未找到会话 {session_id}")
        return updated

    def complete_session(self, session_id: str) -> TrainingResult:
        """Complete the session, calculate summary analytics, and save result."""
        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"未找到训练会话: {session_id}")

        total_questions = len(session.questions)
        attempted_count = len(session.attempts)
        correct_count = sum(1 for a in session.attempts.values() if a.is_correct)
        accuracy_rate = (
            round((correct_count / attempted_count) * 100.0, 1)
            if attempted_count > 0
            else 0.0
        )

        # Error type distribution
        error_distribution: dict[str, int] = {}
        for att in session.attempts.values():
            if not att.is_correct and att.error_type:
                label = att.error_type.label
                error_distribution[label] = error_distribution.get(label, 0) + 1

        # Consolidate method summary
        method_set: list[str] = []
        for att in session.attempts.values():
            if att.method_reinforcement and att.method_reinforcement not in method_set:
                method_set.append(att.method_reinforcement)
        if not method_set:
            method_set.append(
                self._method_reinforcement(session.questions[0], session.task)
            )

        now = datetime.now(UTC).isoformat()
        result = TrainingResult(
            session_id=session_id,
            task_target=session.task.target,
            training_type=session.task.training_type,
            subject=session.task.subject,
            total_questions=total_questions,
            attempted_count=attempted_count,
            correct_count=correct_count,
            accuracy_rate=accuracy_rate,
            error_type_distribution=error_distribution,
            method_summary=method_set,
            completed_at=now,
        )

        with self._db._connection() as conn:
            # 1. Update session status
            conn.execute(
                """
                UPDATE targeted_training_sessions
                SET status = ?, completed_at = ?
                WHERE id = ?
                """,
                (str(SessionStatus.COMPLETED), now, session_id),
            )
            # 2. Persist result
            conn.execute(
                """
                INSERT OR REPLACE INTO targeted_training_results (
                    session_id, task_target, training_type, subject,
                    total_questions, attempted_count, correct_count,
                    accuracy_rate, error_distribution_json, method_summary_json,
                    completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    result.task_target,
                    result.training_type,
                    result.subject,
                    result.total_questions,
                    result.attempted_count,
                    result.correct_count,
                    result.accuracy_rate,
                    json.dumps(result.error_type_distribution, ensure_ascii=False),
                    json.dumps(result.method_summary, ensure_ascii=False),
                    now,
                ),
            )

        LOGGER.info(
            "针对训练会话 %s 已完成：总题数 %d, 正确 %d, 正确率 %.1f%%",
            session_id,
            total_questions,
            correct_count,
            accuracy_rate,
        )
        return result

    def get_result(self, session_id: str) -> TrainingResult | None:
        """Retrieve stored training result for a session."""
        with self._db._connection() as conn:
            row = conn.execute(
                "SELECT * FROM targeted_training_results WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                return None
            return TrainingResult(
                session_id=row["session_id"],
                task_target=row["task_target"],
                training_type=row["training_type"],
                subject=row["subject"],
                total_questions=row["total_questions"],
                attempted_count=row["attempted_count"],
                correct_count=row["correct_count"],
                accuracy_rate=row["accuracy_rate"],
                error_type_distribution=json.loads(row["error_distribution_json"]),
                method_summary=json.loads(row["method_summary_json"]),
                completed_at=row["completed_at"],
            )

    def get_question_history(self, question_id: str) -> list[QuestionAttempt]:
        """Retrieve historical attempts across sessions for a specific question."""
        with self._db._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM targeted_training_attempts
                WHERE question_id = ?
                ORDER BY attempted_at DESC
                """,
                (question_id,),
            ).fetchall()
            history: list[QuestionAttempt] = []
            for r in rows:
                err_raw = r["error_type"]
                err_type = ErrorType(err_raw) if err_raw else None
                history.append(
                    QuestionAttempt(
                        session_id=r["session_id"],
                        question_id=r["question_id"],
                        user_answer=r["user_answer"],
                        reference_answer=r["reference_answer"] or "",
                        is_correct=bool(r["is_correct"]),
                        error_type=err_type,
                        error_analysis=r["error_analysis"] or "",
                        method_reinforcement=r["method_reinforcement"] or "",
                        submission_count=int(r["submission_count"]),
                        attempted_at=r["attempted_at"],
                        question_item_id=r["question_item_id"],
                        family_id=r["family_id"],
                    )
                )
            return history
