"""Domain models for Targeted Training Review Queue (Phase 7).

Provides:
1. ReviewPriority: Priority levels (高 / 中 / 低) driven by mastery status and errors.
2. ReviewQueueItem: Scheduled review target with next review date and rationale.
3. ReviewQueueSummary: Aggregated dashboard summary for today's learning status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ReviewPriority(StrEnum):
    """Review priority levels determining dispatch urgency."""

    HIGH = "高"
    NORMAL = "中"
    LOW = "低"


@dataclass
class ReviewQueueItem:
    """Scheduled review queue item derived from real mastery states and attempt history."""

    id: str
    user_id: str
    subject: str
    target: str
    training_type: str = "方法族"
    mastery_level: str = "待加强"
    review_priority: ReviewPriority = ReviewPriority.NORMAL
    review_interval_days: int = 3
    next_review_at: str = field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%d")
    )
    review_reason: str = ""
    family_id: int | None = None
    last_trained_at: str | None = None
    last_error_at: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize review item into dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "subject": self.subject,
            "target": self.target,
            "training_type": self.training_type,
            "mastery_level": self.mastery_level,
            "review_priority": str(self.review_priority),
            "review_interval_days": self.review_interval_days,
            "next_review_at": self.next_review_at,
            "review_reason": self.review_reason,
            "family_id": self.family_id,
            "last_trained_at": self.last_trained_at,
            "last_error_at": self.last_error_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewQueueItem:
        """Reconstruct review item from dictionary."""
        prio_raw = data.get("review_priority", "中")
        prio = (
            ReviewPriority(prio_raw)
            if prio_raw in list(ReviewPriority)
            else ReviewPriority.NORMAL
        )
        return cls(
            id=str(data["id"]),
            user_id=str(data["user_id"]),
            subject=str(data["subject"]),
            target=str(data["target"]),
            training_type=str(data.get("training_type", "方法族")),
            mastery_level=str(data.get("mastery_level", "待加强")),
            review_priority=prio,
            review_interval_days=int(data.get("review_interval_days", 3)),
            next_review_at=str(data.get("next_review_at", "")),
            review_reason=str(data.get("review_reason", "")),
            family_id=data.get("family_id"),
            last_trained_at=data.get("last_trained_at"),
            last_error_at=data.get("last_error_at"),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


@dataclass
class ReviewQueueSummary:
    """Aggregated status summary for today's review dashboard."""

    subject: str
    total_items: int = 0
    stage_mastered_count: int = 0
    needs_improvement_count: int = 0
    in_training_count: int = 0
    today_recommended_items: list[ReviewQueueItem] = field(default_factory=list)
    has_sufficient_data: bool = True
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize status summary into dictionary."""
        return {
            "subject": self.subject,
            "total_items": self.total_items,
            "stage_mastered_count": self.stage_mastered_count,
            "needs_improvement_count": self.needs_improvement_count,
            "in_training_count": self.in_training_count,
            "today_recommended_items": [
                item.to_dict() for item in self.today_recommended_items
            ],
            "has_sufficient_data": self.has_sufficient_data,
            "message": self.message,
        }
