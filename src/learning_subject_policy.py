"""Subject selection follows the user's saved education stage and grade."""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from src.training_profile_data import FIRST_LEVEL_DISCIPLINES
from src.training_profile_models import (
    EducationType,
    HigherEducationProfile,
    LearnerProfile,
)

PRIMARY_SUBJECTS = ("语文", "数学", "英语")
JUNIOR_ONE_SUBJECTS = (*PRIMARY_SUBJECTS, "历史", "政治", "地理", "生物")
JUNIOR_TWO_SUBJECTS = (*JUNIOR_ONE_SUBJECTS, "物理")
JUNIOR_THREE_SUBJECTS = (*PRIMARY_SUBJECTS, "物理", "化学", "历史", "政治")
HIGH_SCHOOL_SUBJECTS = (*JUNIOR_THREE_SUBJECTS, "地理", "生物")
ADVANCED_SUBJECT = "强基/竞赛"
MANUAL_SUBJECT = "手动填写…"


def _discipline_display_name(entry: str) -> str:
    """Strip the official code prefix from a first-level discipline entry."""

    return entry.split(" ", 1)[1] if " " in entry else entry


# 大学学科分类：国务院学位委员会/教育部现行《研究生教育学科专业目录》一级学科
# 名称（去编号、去重、官方目录顺序），覆盖计算机、心理学等大学全部学科。
# 课程级名称（如高等数学）不属于学科分类，仍通过“手动填写”入口录入。
UNIVERSITY_SUBJECTS: tuple[str, ...] = tuple(
    dict.fromkeys(
        _discipline_display_name(entry)
        for entries in FIRST_LEVEL_DISCIPLINES.values()
        for entry in entries
    )
)


@dataclass(frozen=True, slots=True)
class SubjectSelectionPolicy:
    """Allowed regular subjects or an explicitly confirmed manual subject."""

    label: str
    subjects: tuple[str, ...] = ()
    manual_only: bool = False
    allows_advanced: bool = False
    manual_choice: str | None = None

    @property
    def choices(self) -> tuple[str, ...]:
        choices = (*self.subjects, ADVANCED_SUBJECT) if self.allows_advanced else self.subjects
        return (*choices, self.manual_choice) if self.manual_choice else choices

    @property
    def signature(self) -> str:
        return f"{self.label}:{int(self.allows_advanced)}"


def higher_subject_choices(higher: HigherEducationProfile | None) -> tuple[str, ...]:
    """University disciplines with the saved major/discipline ranked first."""

    preferred = ""
    if higher is not None:
        if higher.discipline_first_level:
            name = _discipline_display_name(higher.discipline_first_level.strip())
            if name in UNIVERSITY_SUBJECTS:
                preferred = name
        if not preferred and (higher.major or "").strip() in UNIVERSITY_SUBJECTS:
            preferred = (higher.major or "").strip()
    return (preferred, *UNIVERSITY_SUBJECTS) if preferred else UNIVERSITY_SUBJECTS


def subject_selection_policy(profile: LearnerProfile | None) -> SubjectSelectionPolicy:
    """Map saved configuration to the exact stage/grade subject list."""

    if profile is not None and profile.education_type == EducationType.HIGHER:
        return SubjectSelectionPolicy(
            "高等教育",
            higher_subject_choices(profile.higher),
            manual_choice=MANUAL_SUBJECT,
        )
    basic = profile.basic if profile is not None else None
    if profile is not None and profile.education_type == EducationType.BASIC and basic is not None:
        if basic.stage == "小学":
            return SubjectSelectionPolicy(
                f"小学 · {basic.grade}", PRIMARY_SUBJECTS, manual_choice=MANUAL_SUBJECT,
            )
        if basic.stage == "初中":
            subjects = {
                "初一": JUNIOR_ONE_SUBJECTS,
                "初二": JUNIOR_TWO_SUBJECTS,
                "初三": JUNIOR_THREE_SUBJECTS,
            }.get(basic.grade)
            if subjects is not None:
                return SubjectSelectionPolicy(
                    f"初中 · {basic.grade}", subjects, manual_choice=MANUAL_SUBJECT,
                )
        if basic.stage == "高中":
            return SubjectSelectionPolicy(
                f"高中 · {basic.grade}", HIGH_SCHOOL_SUBJECTS,
                allows_advanced=basic.in_strong_base or basic.in_competition,
                manual_choice=MANUAL_SUBJECT,
            )
    # Organization remains usable offline without a training configuration.
    return SubjectSelectionPolicy("尚未保存训练配置", manual_only=True)


def normalize_subject_name(value: str) -> str:
    """Offer existing political-subject labels under the requested common name."""

    subject = value.strip()
    return "政治" if subject in ("道德与法治", "思想政治") else subject


def is_foreign_language_subject(value: str) -> bool:
    """Route only the user's saved course name, without inspecting questions or calling AI."""

    name = re.sub(r"^\d+\s+", "", value.strip()).casefold()
    name = re.sub(r"^(?:(?:小学|初中|高中|大学|高考|考研|基础|商务|专业|公共)\s*)+", "", name)
    return name.startswith((
        "英语", "英文", "日语", "日文", "德语", "法语", "俄语", "韩语", "朝鲜语",
        "西班牙语", "葡萄牙语", "意大利语", "阿拉伯语", "外语", "外国语言文学",
    )) or bool(re.match(
        r"(?:english|japanese|german|french|russian|korean|spanish|portuguese|italian|arabic)\b",
        name,
    ))


def read_saved_subject(database_path: Path) -> str:
    """Read the last explicitly saved subject from local application metadata."""

    path = database_path.parent / "learning_subject.json"
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("subject"), str):
        raise ValueError("已保存的学科配置格式无效。")
    return data["subject"].strip()


def save_subject(database_path: Path, subject: str) -> None:
    """Persist an explicit subject confirmation atomically, apart from source materials."""

    if not subject.strip():
        raise ValueError("请先填写学科。")
    path = database_path.parent / "learning_subject.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temp:
        json.dump({"subject": subject.strip()}, temp, ensure_ascii=False)
        temp.flush()
        os.fsync(temp.fileno())
        temporary_path = Path(temp.name)
    try:
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
