"""Subject selection follows the user's saved education stage and grade."""

from dataclasses import dataclass

from src.training_profile_models import EducationType, LearnerProfile

PRIMARY_SUBJECTS = ("语文", "数学", "英语")
JUNIOR_ONE_SUBJECTS = (*PRIMARY_SUBJECTS, "历史", "政治", "地理", "生物")
JUNIOR_TWO_SUBJECTS = (*JUNIOR_ONE_SUBJECTS, "物理")
JUNIOR_THREE_SUBJECTS = (*PRIMARY_SUBJECTS, "物理", "化学", "历史", "政治")
HIGH_SCHOOL_SUBJECTS = (*JUNIOR_THREE_SUBJECTS, "地理", "生物")
ADVANCED_SUBJECT = "强基/竞赛"


@dataclass(frozen=True, slots=True)
class SubjectSelectionPolicy:
    """Allowed regular subjects or an explicitly confirmed manual subject."""

    label: str
    subjects: tuple[str, ...] = ()
    manual_only: bool = False
    allows_advanced: bool = False

    @property
    def choices(self) -> tuple[str, ...]:
        return (*self.subjects, ADVANCED_SUBJECT) if self.allows_advanced else self.subjects

    @property
    def signature(self) -> str:
        return f"{self.label}:{int(self.allows_advanced)}"


def subject_selection_policy(profile: LearnerProfile | None) -> SubjectSelectionPolicy:
    """Map saved configuration to the exact stage/grade subject list."""

    if profile is not None and profile.education_type == EducationType.HIGHER:
        return SubjectSelectionPolicy("高等教育", manual_only=True)
    basic = profile.basic if profile is not None else None
    if profile is not None and profile.education_type == EducationType.BASIC and basic is not None:
        if basic.stage == "小学":
            return SubjectSelectionPolicy(f"小学 · {basic.grade}", PRIMARY_SUBJECTS)
        if basic.stage == "初中":
            subjects = {
                "初一": JUNIOR_ONE_SUBJECTS,
                "初二": JUNIOR_TWO_SUBJECTS,
                "初三": JUNIOR_THREE_SUBJECTS,
            }.get(basic.grade)
            if subjects is not None:
                return SubjectSelectionPolicy(f"初中 · {basic.grade}", subjects)
        if basic.stage == "高中":
            return SubjectSelectionPolicy(
                f"高中 · {basic.grade}", HIGH_SCHOOL_SUBJECTS,
                allows_advanced=basic.in_strong_base or basic.in_competition,
            )
    # Organization remains usable offline without a training configuration.
    return SubjectSelectionPolicy("尚未保存训练配置", manual_only=True)


def normalize_subject_name(value: str) -> str:
    """Offer existing political-subject labels under the requested common name."""

    subject = value.strip()
    return "政治" if subject in ("道德与法治", "思想政治") else subject
