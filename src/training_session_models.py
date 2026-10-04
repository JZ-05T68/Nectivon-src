"""Domain models for Targeted Training Session Execution and Result Feedback (Phase 5).

Establishes:
1. TrainingSession lifecycle state machine: created -> in_progress -> completed.
2. QuestionAttempt recording: user answer, reference answer, correctness, error attribution,
   method reinforcement, submission count.
3. TrainingResult summary: total questions, correct rate, error distribution, method summary.
4. Strict zero-hallucination invariant: session strictly wraps verified questions from Phase 3/4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from src.question_source_models import VerificationStatus, VerifiedQuestion
from src.targeted_training_models import TrainingTask


class SessionStatus(StrEnum):
    """Lifecycle state machine for a targeted training session."""

    CREATED = "created"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"

    @property
    def label(self) -> str:
        match self:
            case SessionStatus.CREATED:
                return "已创建"
            case SessionStatus.IN_PROGRESS:
                return "作答中"
            case SessionStatus.COMPLETED:
                return "已完成"


class ErrorType(StrEnum):
    """Standard error cause taxonomy for diagnostic reflection."""

    CONCEPT = "concept_misunderstanding"
    CALCULATION = "calculation_error"
    METHOD = "method_unfamiliarity"
    READING = "reading_comprehension"
    OTHER = "other"

    @property
    def label(self) -> str:
        match self:
            case ErrorType.CONCEPT:
                return "概念不清"
            case ErrorType.CALCULATION:
                return "计算失误"
            case ErrorType.METHOD:
                return "方法不熟"
            case ErrorType.READING:
                return "审题不清"
            case ErrorType.OTHER:
                return "其他"

    @classmethod
    def from_label(cls, label: str) -> ErrorType:
        """Map Chinese label to ErrorType enum."""
        mapping = {
            "概念不清": cls.CONCEPT,
            "计算失误": cls.CALCULATION,
            "方法不熟": cls.METHOD,
            "审题不清": cls.READING,
            "其他": cls.OTHER,
        }
        return mapping.get(label, cls.OTHER)


@dataclass
class QuestionAttempt:
    """Individual attempt record on a verified training question."""

    session_id: str
    question_id: str
    user_answer: str
    reference_answer: str = ""
    is_correct: bool = False
    error_type: ErrorType | None = None
    error_analysis: str = ""
    method_reinforcement: str = ""
    submission_count: int = 1
    attempted_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    question_item_id: int | None = None
    family_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize attempt into dictionary."""
        return {
            "session_id": self.session_id,
            "question_id": self.question_id,
            "user_answer": self.user_answer,
            "reference_answer": self.reference_answer,
            "is_correct": self.is_correct,
            "error_type": str(self.error_type) if self.error_type else None,
            "error_analysis": self.error_analysis,
            "method_reinforcement": self.method_reinforcement,
            "submission_count": self.submission_count,
            "attempted_at": self.attempted_at,
            "question_item_id": self.question_item_id,
            "family_id": self.family_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QuestionAttempt:
        """Instantiate attempt from dictionary."""
        err_raw = data.get("error_type")
        err_type = ErrorType(err_raw) if err_raw else None
        return cls(
            session_id=str(data["session_id"]),
            question_id=str(data["question_id"]),
            user_answer=str(data.get("user_answer", "")),
            reference_answer=str(data.get("reference_answer", "")),
            is_correct=bool(data.get("is_correct", False)),
            error_type=err_type,
            error_analysis=str(data.get("error_analysis", "")),
            method_reinforcement=str(data.get("method_reinforcement", "")),
            submission_count=int(data.get("submission_count", 1)),
            attempted_at=str(
                data.get("attempted_at") or datetime.now(UTC).isoformat()
            ),
            question_item_id=data.get("question_item_id"),
            family_id=data.get("family_id"),
        )


@dataclass
class TrainingSession:
    """An execution session containing verified questions and attempts."""

    id: str
    task: TrainingTask
    questions: list[VerifiedQuestion]
    user_id: str = "default_local_user"
    current_question_index: int = 0
    status: SessionStatus = SessionStatus.CREATED
    attempts: dict[str, QuestionAttempt] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    completed_at: str | None = None

    @property
    def current_question(self) -> VerifiedQuestion | None:
        """Return the current active question to display or answer."""
        if 0 <= self.current_question_index < len(self.questions):
            return self.questions[self.current_question_index]
        return None

    @property
    def is_completed(self) -> bool:
        """Check if all questions have been answered or session marked completed."""
        return self.status == SessionStatus.COMPLETED

    @property
    def total_questions(self) -> int:
        return len(self.questions)

    def get_attempt(self, question_id: str) -> QuestionAttempt | None:
        return self.attempts.get(question_id)

    def has_attempted(self, question_id: str) -> bool:
        """Check if question has at least one recorded attempt."""
        return question_id in self.attempts

    def to_dict(self) -> dict[str, Any]:
        """Serialize training session into dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "task": self.task.to_dict(),
            "questions": [q.to_dict() for q in self.questions],
            "current_question_index": self.current_question_index,
            "status": str(self.status),
            "attempts": {qid: att.to_dict() for qid, att in self.attempts.items()},
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrainingSession:
        """Reconstruct TrainingSession from dictionary."""
        task_data = data["task"]
        task = TrainingTask(
            training_type=str(task_data["training_type"]),
            target=str(task_data["target"]),
            subject=task_data.get("subject"),
            family_id=task_data.get("family_id"),
        )
        questions: list[VerifiedQuestion] = []
        for q_data in data.get("questions", []):
            questions.append(
                VerifiedQuestion(
                    id=q_data["id"],
                    question_text=q_data["question_text"],
                    source_name=q_data["source_name"],
                    source_url=q_data["source_url"],
                    year=q_data.get("year"),
                    exam_or_contest_name=q_data["exam_or_contest_name"],
                    question_number=q_data.get("question_number"),
                    subject=q_data["subject"],
                    applicable_scope=q_data["applicable_scope"],
                    verification_status=VerificationStatus(
                        q_data.get("verification_status", "verified")
                    ),
                    rejection_reason=q_data.get("rejection_reason"),
                    difficulty_level=q_data.get("difficulty_level", "中等"),
                    question_fingerprint=q_data.get("question_fingerprint", ""),
                    document_id=q_data.get("document_id"),
                    page_id=q_data.get("page_id"),
                    page_number=q_data.get("page_number"),
                    question_item_id=q_data.get("question_item_id"),
                    family_id=q_data.get("family_id"),
                    school_name=q_data.get("school_name"),
                    major_direction=q_data.get("major_direction"),
                    contest_name=q_data.get("contest_name"),
                    contest_tier=q_data.get("contest_tier"),
                    reference_answer=q_data.get("reference_answer", ""),
                    verified_at=q_data.get("verified_at", ""),
                )
            )

        attempts = {
            qid: QuestionAttempt.from_dict(att_data)
            for qid, att_data in data.get("attempts", {}).items()
        }

        return cls(
            id=str(data["id"]),
            task=task,
            questions=questions,
            user_id=str(data.get("user_id", "default_local_user")),
            current_question_index=int(data.get("current_question_index", 0)),
            status=SessionStatus(data.get("status", "created")),
            attempts=attempts,
            created_at=str(data.get("created_at", "")),
            completed_at=data.get("completed_at"),
        )


@dataclass(frozen=True, slots=True)
class TrainingResult:
    """Summary result of a completed training session."""

    session_id: str
    task_target: str
    training_type: str
    subject: str | None
    total_questions: int
    attempted_count: int
    correct_count: int
    accuracy_rate: float
    error_type_distribution: dict[str, int]
    method_summary: list[str]
    completed_at: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize result into dictionary."""
        return {
            "session_id": self.session_id,
            "task_target": self.task_target,
            "training_type": self.training_type,
            "subject": self.subject,
            "total_questions": self.total_questions,
            "attempted_count": self.attempted_count,
            "correct_count": self.correct_count,
            "accuracy_rate": self.accuracy_rate,
            "error_type_distribution": self.error_type_distribution,
            "method_summary": self.method_summary,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrainingResult:
        """Reconstruct TrainingResult from dictionary."""
        return cls(
            session_id=str(data["session_id"]),
            task_target=str(data["task_target"]),
            training_type=str(data["training_type"]),
            subject=data.get("subject"),
            total_questions=int(data["total_questions"]),
            attempted_count=int(data["attempted_count"]),
            correct_count=int(data["correct_count"]),
            accuracy_rate=float(data["accuracy_rate"]),
            error_type_distribution=dict(data.get("error_type_distribution", {})),
            method_summary=list(data.get("method_summary", [])),
            completed_at=str(data["completed_at"]),
        )
