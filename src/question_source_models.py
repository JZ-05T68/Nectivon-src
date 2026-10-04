"""Domain models for Question Source Retrieval, Verification, and Provenance (Phase 3).

Establishes the tri-state verification model (candidate -> verified / rejected),
provenance binding structures, and product contracts.
Highest priority invariant: Better to show no questions than unverified/fabricated questions.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class VerificationStatus(StrEnum):
    """Tri-state question status model as defined in Phase 3 specifications.

    - CANDIDATE: Discovered, awaiting verification.
    - VERIFIED: Passed all authenticity and locatability checks. Allowed to display.
    - REJECTED: Failed verification (non-existent, unlocatable, AI-synthesized, etc.).
    """

    CANDIDATE = "candidate"
    VERIFIED = "verified"
    REJECTED = "rejected"


class QuestionSourceError(Exception):
    """Raised when question source verification or retrieval fails."""


@dataclass(frozen=True, slots=True)
class QuestionSourceCandidate:
    """Raw question candidate discovered before verification.

    Contains all candidate provenance fields to be strictly audited by
    QuestionSourceVerifier.
    """

    question_text: str
    source_name: str
    source_url: str
    year: int | None
    exam_or_contest_name: str
    question_number: str | None
    subject: str
    applicable_scope: str = "基础教育 · 普通"
    difficulty_level: str = "中等"
    document_id: int | None = None
    page_id: int | None = None
    page_number: int | None = None
    question_item_id: int | None = None
    family_id: int | None = None
    raw_source_text: str | None = None
    school_name: str | None = None
    major_direction: str | None = None
    contest_name: str | None = None
    contest_tier: str | None = None
    contest_stage: str | None = None
    is_regular_exam: bool = False
    reference_answer: str = ""


@dataclass(frozen=True, slots=True)
class VerifiedQuestion:
    """Verified training question approved for display to the learner.

    Guarantees:
    1. Question truly exists.
    2. Source can be manually queried.
    3. Source can locate the exact original question.
    4. Question content matches source.
    """

    id: str
    question_text: str
    source_name: str
    source_url: str
    year: int | None
    exam_or_contest_name: str
    question_number: str | None
    subject: str
    applicable_scope: str
    verification_status: VerificationStatus = VerificationStatus.VERIFIED
    rejection_reason: str | None = None
    difficulty_level: str = "中等"
    question_fingerprint: str = ""
    document_id: int | None = None
    page_id: int | None = None
    page_number: int | None = None
    question_item_id: int | None = None
    family_id: int | None = None
    school_name: str | None = None
    major_direction: str | None = None
    contest_name: str | None = None
    contest_tier: str | None = None
    reference_answer: str = ""
    verified_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    def __post_init__(self) -> None:
        if self.verification_status != VerificationStatus.VERIFIED:
            msg = (
                f"只有 verified 状态的题目才能创建为 VerifiedQuestion，"
                f"当前状态：{self.verification_status}"
            )
            raise QuestionSourceError(msg)
        if not self.question_fingerprint:
            # Generate deterministic SHA-256 fingerprint from content & source
            fp_content = (
                f"{self.question_text.strip()}::{self.source_name}::"
                f"{self.year}::{self.question_number}"
            )
            computed = hashlib.sha256(fp_content.encode("utf-8")).hexdigest()[:16]
            object.__setattr__(self, "question_fingerprint", computed)

    def to_dict(self) -> dict[str, Any]:
        """Serialize full verified question into dictionary."""
        return {
            "id": self.id,
            "question_text": self.question_text,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "year": self.year,
            "exam_or_contest_name": self.exam_or_contest_name,
            "question_number": self.question_number,
            "subject": self.subject,
            "applicable_scope": self.applicable_scope,
            "verification_status": str(self.verification_status),
            "rejection_reason": self.rejection_reason,
            "difficulty_level": self.difficulty_level,
            "question_fingerprint": self.question_fingerprint,
            "document_id": self.document_id,
            "page_id": self.page_id,
            "page_number": self.page_number,
            "question_item_id": self.question_item_id,
            "family_id": self.family_id,
            "school_name": self.school_name,
            "major_direction": self.major_direction,
            "contest_name": self.contest_name,
            "contest_tier": self.contest_tier,
            "reference_answer": self.reference_answer,
            "verified_at": self.verified_at,
        }

    def to_display_contract(self) -> dict[str, Any]:
        """Contract matching product specifications for UI display."""
        return {
            "question_text": self.question_text,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "year": self.year,
            "exam_or_contest_name": self.exam_or_contest_name,
            "question_number": self.question_number,
            "subject": self.subject,
            "applicable_scope": self.applicable_scope,
            "verification_status": str(self.verification_status),
            "school_name": self.school_name,
            "major_direction": self.major_direction,
            "contest_name": self.contest_name,
            "contest_tier": self.contest_tier,
            "reference_answer": self.reference_answer,
        }


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Outcome of verifying a question candidate."""

    status: VerificationStatus
    reason: str | None = None
    verified_question: VerifiedQuestion | None = None
    candidate: QuestionSourceCandidate | None = None

    @property
    def is_verified(self) -> bool:
        return self.status == VerificationStatus.VERIFIED


@dataclass(frozen=True, slots=True)
class QuestionRetrievalResult:
    """Result package returned by question retrieval and verification pipeline."""

    training_type: str
    target: str
    subject: str | None
    questions: list[VerifiedQuestion]
    total_candidates: int
    verified_count: int
    rejected_count: int
    rejected_reasons: list[str] = field(default_factory=list)
    prompt_message: str = ""
    can_refresh: bool = False
    can_retry: bool = False
    page_offset: int = 0
    total_available_verified: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "training_type": self.training_type,
            "target": self.target,
            "subject": self.subject,
            "questions": [q.to_dict() for q in self.questions],
            "total_candidates": self.total_candidates,
            "verified_count": self.verified_count,
            "rejected_count": self.rejected_count,
            "rejected_reasons": self.rejected_reasons,
            "prompt_message": self.prompt_message,
            "can_refresh": self.can_refresh,
            "can_retry": self.can_retry,
            "page_offset": self.page_offset,
            "total_available_verified": self.total_available_verified,
        }
