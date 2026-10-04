"""Eligibility models and boundary definitions for Targeted Training (Phase 4).

Governs the permission envelope derived from the learner's profile:
User Identity -> Allowed Source Scope -> Retrieval -> Verification -> Display.
Enforces the fundamental law:
'用户身份不会自动改变题目等级。题源范围必须由用户填写信息与真实规则共同决定。'
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class CompetitionTier(StrEnum):
    """Competition tier classification reflecting authentic national competition structures.

    - PROVINCIAL: Provincial preliminary/semifinal/league stages (预赛, 初赛, 复赛, 联赛, 省赛).
    - NATIONAL: National final competitions (全国决赛, 国决, 冬令营, CMO, CPhO, CChO, CBO, NOI).
    - INTERNATIONAL: National team & Olympiads (国家集训队, IPhO, IMO, IChO, IBO, IOI).
    """

    PROVINCIAL = "省级"
    NATIONAL = "国家级"
    INTERNATIONAL = "国际级"


@dataclass(frozen=True, slots=True)
class AllowedSourceScope:
    """The computed authoritative permission envelope for question source retrieval.

    Determined jointly by the user's verified profile and active training task.
    Prevents scope inflation, fake institutions, fake competitions, and cross-discipline leakage.
    """

    education_stage: str = "基础教育"
    allow_regular: bool = True
    is_strong_base_eligible: bool = False
    strong_base_school: str | None = None
    strong_base_subject: str | None = None
    is_competition_eligible: bool = False
    competition_subject: str | None = None
    competition_stage: str | None = None
    competition_tier: CompetitionTier | None = None
    higher_school: str | None = None
    higher_education_level: str | None = None
    higher_major: str | None = None
    allowed_major_domains: tuple[str, ...] = field(default_factory=tuple)
    graduate_category: str | None = None
    graduate_first_level: str | None = None
    scope_description: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize allowed scope into structured dictionary."""
        return {
            "education_stage": self.education_stage,
            "allow_regular": self.allow_regular,
            "is_strong_base_eligible": self.is_strong_base_eligible,
            "strong_base_school": self.strong_base_school,
            "strong_base_subject": self.strong_base_subject,
            "is_competition_eligible": self.is_competition_eligible,
            "competition_subject": self.competition_subject,
            "competition_stage": self.competition_stage,
            "competition_tier": str(self.competition_tier) if self.competition_tier else None,
            "higher_school": self.higher_school,
            "higher_education_level": self.higher_education_level,
            "higher_major": self.higher_major,
            "allowed_major_domains": list(self.allowed_major_domains),
            "graduate_category": self.graduate_category,
            "graduate_first_level": self.graduate_first_level,
            "scope_description": self.scope_description,
        }


@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    """Outcome of evaluating a candidate question against the AllowedSourceScope."""

    is_eligible: bool
    reason: str | None = None
    normalized_scope: str = "基础教育 · 普通"
