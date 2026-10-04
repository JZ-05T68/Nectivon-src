"""Domain models for Error Analysis, Variant Practice, and Mastery State (Phase 6).

Implements:
1. ErrorAnalysis: Structured error cause records linked to QuestionAttempt.
2. ErrorCategory: Standard 5-tier error taxonomy
   (知识点错误, 方法选择错误, 计算错误, 审题错误, 其他).
3. FrequentErrorTag: Tendency identification for repeated error patterns (>= 2 occurrences).
4. ErrorProfile: Discipline-isolated learner error profiling based strictly on real records.
5. MasteryLevel: Discrete progression stages (未建立 -> 训练中 -> 待加强 -> 阶段掌握).
6. MasteryState: History-aware competency state tracking with zero artificial leaps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from src.training_session_models import ErrorType


class ErrorCategory(StrEnum):
    """Phase 6 official error category taxonomy."""

    KNOWLEDGE = "知识点错误"
    METHOD = "方法选择错误"
    CALCULATION = "计算错误"
    READING = "审题错误"
    OTHER = "其他"

    @property
    def description(self) -> str:
        match self:
            case ErrorCategory.KNOWLEDGE:
                return "不知道相关概念或理论基础。"
            case ErrorCategory.METHOD:
                return "知道知识，但没有选择正确方法或解题路径。"
            case ErrorCategory.CALCULATION:
                return "方法正确，但在计算或代数推导过程中出现失误。"
            case ErrorCategory.READING:
                return "没有准确识别题目已知条件、约束或设问要求。"
            case ErrorCategory.OTHER:
                return "无法归入上述类别的偶发或其他错误。"

    @classmethod
    def from_error_type(cls, err: ErrorType | str | None) -> ErrorCategory:
        """Map Phase 5 ErrorType or string label to ErrorCategory."""
        if err is None:
            return cls.OTHER
        if isinstance(err, ErrorType):
            raw = str(err)
            lbl = err.label
        else:
            raw = str(err).strip()
            lbl = raw

        if raw == ErrorType.CONCEPT or lbl in ("概念不清", "知识点错误"):
            return cls.KNOWLEDGE
        if raw == ErrorType.METHOD or lbl in ("方法不熟", "方法选择错误"):
            return cls.METHOD
        if raw == ErrorType.CALCULATION or lbl in ("计算失误", "计算错误"):
            return cls.CALCULATION
        if raw == ErrorType.READING or lbl in ("审题不清", "审题错误"):
            return cls.READING
        return cls.OTHER


@dataclass
class ErrorAnalysis:
    """Structured error analysis object derived from an authentic question attempt."""

    id: str
    question_id: str
    session_id: str
    is_error: bool
    attempt_id: str | None = None
    error_type: str | None = None
    user_note: str = ""
    associated_target: str = ""
    training_type: str = "方法族"
    subject: str = "物理"
    family_id: int | None = None
    question_item_id: int | None = None
    applicable_scope: str = "基础教育 · 普通"
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize error analysis into clean dictionary."""
        return {
            "id": self.id,
            "question_id": self.question_id,
            "attempt_id": self.attempt_id,
            "session_id": self.session_id,
            "is_error": self.is_error,
            "error_type": self.error_type,
            "user_note": self.user_note,
            "associated_target": self.associated_target,
            "training_type": self.training_type,
            "subject": self.subject,
            "family_id": self.family_id,
            "question_item_id": self.question_item_id,
            "applicable_scope": self.applicable_scope,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ErrorAnalysis:
        """Reconstruct ErrorAnalysis from dictionary."""
        return cls(
            id=str(data["id"]),
            question_id=str(data["question_id"]),
            attempt_id=data.get("attempt_id"),
            session_id=str(data["session_id"]),
            is_error=bool(data["is_error"]),
            error_type=data.get("error_type"),
            user_note=str(data.get("user_note", "")),
            associated_target=str(data.get("associated_target", "")),
            training_type=str(data.get("training_type", "方法族")),
            subject=str(data.get("subject", "物理")),
            family_id=data.get("family_id"),
            question_item_id=data.get("question_item_id"),
            applicable_scope=str(data.get("applicable_scope", "基础教育 · 普通")),
            created_at=str(data.get("created_at", datetime.now(UTC).isoformat())),
        )


@dataclass(frozen=True, slots=True)
class FrequentErrorTag:
    """Identified trend tag for recurring error patterns."""

    target: str
    error_type: str
    count: int
    subject: str

    @property
    def tag_label(self) -> str:
        return f"高频错因：{self.target} - {self.error_type}（{self.count}次）"


@dataclass
class ErrorProfile:
    """Discipline-isolated diagnostic error profile calculated from real learning history."""

    subject: str
    has_sufficient_data: bool
    total_attempts: int = 0
    total_errors: int = 0
    error_distribution_by_type: dict[str, int] = field(default_factory=dict)
    error_distribution_by_target: dict[str, dict[str, int]] = field(
        default_factory=dict
    )
    frequent_errors: list[FrequentErrorTag] = field(default_factory=list)
    primary_weakness: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize error profile into structured dictionary."""
        return {
            "subject": self.subject,
            "has_sufficient_data": self.has_sufficient_data,
            "total_attempts": self.total_attempts,
            "total_errors": self.total_errors,
            "error_distribution_by_type": self.error_distribution_by_type,
            "error_distribution_by_target": self.error_distribution_by_target,
            "frequent_errors": [
                {
                    "target": t.target,
                    "error_type": t.error_type,
                    "count": t.count,
                    "subject": t.subject,
                    "tag_label": t.tag_label,
                }
                for t in self.frequent_errors
            ],
            "primary_weakness": self.primary_weakness,
            "message": self.message,
        }


class MasteryLevel(StrEnum):
    """Discrete mastery progression stages reflecting authentic learning evolution."""

    NOT_ESTABLISHED = "未建立"
    IN_TRAINING = "训练中"
    NEEDS_IMPROVEMENT = "待加强"
    STAGE_MASTERED = "阶段掌握"


@dataclass
class MasteryState:
    """Mastery evaluation state for a specific subject target."""

    user_id: str
    subject: str
    target: str
    level: MasteryLevel = MasteryLevel.NOT_ESTABLISHED
    total_attempts: int = 0
    correct_attempts: int = 0
    consecutive_correct: int = 0
    consecutive_errors: int = 0
    last_attempt_is_correct: bool | None = None
    last_error_type: str | None = None
    family_id: int | None = None
    updated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    @property
    def accuracy_rate(self) -> float:
        if self.total_attempts <= 0:
            return 0.0
        return round((self.correct_attempts / self.total_attempts) * 100.0, 1)

    def to_dict(self) -> dict[str, Any]:
        """Serialize mastery state into dictionary."""
        return {
            "user_id": self.user_id,
            "subject": self.subject,
            "target": self.target,
            "level": str(self.level),
            "total_attempts": self.total_attempts,
            "correct_attempts": self.correct_attempts,
            "consecutive_correct": self.consecutive_correct,
            "consecutive_errors": self.consecutive_errors,
            "last_attempt_is_correct": self.last_attempt_is_correct,
            "last_error_type": self.last_error_type,
            "family_id": self.family_id,
            "accuracy_rate": self.accuracy_rate,
            "updated_at": self.updated_at,
        }
