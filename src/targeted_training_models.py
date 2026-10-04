"""Domain models and exceptions for Targeted Training (Phase 2).

Defines the training task configuration, target selections, and validation
rules for generating targeted training tasks from Layer 2 organized data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

TRAINING_TYPES: Final[tuple[str, str]] = ("题型族", "方法族")
TRAINING_TYPE_TO_FAMILY_KIND: Final[dict[str, str]] = {
    "题型族": "type",
    "方法族": "method",
}
FAMILY_KIND_TO_TRAINING_TYPE: Final[dict[str, str]] = {
    "type": "题型族",
    "method": "方法族",
}

STATUS_WAITING_FOR_QUESTION_GENERATION: Final[str] = (
    "waiting_for_question_generation"
)
DEFAULT_SOURCE: Final[str] = "第二层整理数据"


class TargetedTrainingError(Exception):
    """Raised when targeted training validation or task creation fails."""


@dataclass(frozen=True, slots=True)
class TrainingTask:
    """Targeted training task object generated from Layer 2 organized data.

    In Phase 2, this object represents the user's selected training target and
    establishes the downstream interface for question generation / retrieval
    in Phase 3.
    """

    training_type: str
    target: str
    source: str = DEFAULT_SOURCE
    status: str = STATUS_WAITING_FOR_QUESTION_GENERATION
    family_id: int | None = None
    subject: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    def __post_init__(self) -> None:
        if self.training_type not in TRAINING_TYPES:
            raise TargetedTrainingError(
                f"训练类型无效：{self.training_type}，必须是 '题型族' 或 '方法族'。"
            )
        if not self.target or not self.target.strip():
            raise TargetedTrainingError("训练目标名称不能为空。")
        if self.source != DEFAULT_SOURCE:
            raise TargetedTrainingError(
                f"数据来源必须是 '{DEFAULT_SOURCE}'，不可冒充或伪造来源。"
            )
        if self.status != STATUS_WAITING_FOR_QUESTION_GENERATION:
            raise TargetedTrainingError(
                f"当前阶段初始状态必须是 '{STATUS_WAITING_FOR_QUESTION_GENERATION}'。"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize into clean dictionary matching product contract."""
        return {
            "training_type": self.training_type,
            "target": self.target,
            "source": self.source,
            "status": self.status,
            "family_id": self.family_id,
            "subject": self.subject,
            "created_at": self.created_at,
        }

    def to_contract_dict(self) -> dict[str, str]:
        """Serialize strictly into the 4 required product contract fields."""
        return {
            "training_type": self.training_type,
            "target": self.target,
            "source": self.source,
            "status": self.status,
        }
