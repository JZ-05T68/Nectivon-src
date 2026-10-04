"""Data models and validation rules for learner profile configuration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from src.training_profile_data import (
    CITIES_BY_PROVINCE,
    COMPETITION_STAGES_BY_SUBJECT,
    COMPETITION_SUBJECTS,
    EDUCATION_STAGES,
    GRADES_BY_STAGE,
    GRADUATE_DISCIPLINE_CATEGORIES,
    HIGHER_EDUCATION_LEVELS,
    PROVINCES,
    QIANGJI_UNIVERSITIES_AND_MAJORS,
    is_valid_school_name,
    is_valid_undergraduate_major,
)


class EducationType(StrEnum):
    """Top-level education domain selection."""

    BASIC = "basic"
    HIGHER = "higher"

    @property
    def label(self) -> str:
        return "基础教育" if self == EducationType.BASIC else "高等教育"


class ProfileValidationError(ValueError):
    """Raised when profile configuration fails business or boundary validation."""


@dataclass
class BasicEducationProfile:
    """Configuration state for primary and secondary education."""

    province: str
    province_code: str
    city: str
    city_code: str
    stage: str
    grade: str
    in_strong_base: bool = False
    strong_base_school: str | None = None
    strong_base_subject: str | None = None
    in_competition: bool = False
    competition_subjects: dict[str, str] = field(default_factory=dict)
    last_upgrade_year: int | None = None

    def validate(self) -> None:
        """Strictly validate fields against official reference datasets."""

        # 1. 验证省份
        province_map = dict(PROVINCES)
        if self.province_code not in province_map:
            raise ProfileValidationError(f"无效的省份行政区划代码：{self.province_code}")
        if self.province != province_map[self.province_code]:
            expected = province_map[self.province_code]
            raise ProfileValidationError(f"省份名称与代码不匹配：{self.province} != {expected}")

        # 2. 验证城市级联
        valid_cities = dict(CITIES_BY_PROVINCE.get(self.province_code, []))
        if self.city_code not in valid_cities:
            raise ProfileValidationError(f"城市代码 {self.city_code} 不属于省份 {self.province}")
        if self.city != valid_cities[self.city_code]:
            expected_city = valid_cities[self.city_code]
            raise ProfileValidationError(f"城市名称与代码不匹配：{self.city} != {expected_city}")

        # 3. 验证教育阶段与年级
        if self.stage not in EDUCATION_STAGES:
            raise ProfileValidationError(f"无效的教育阶段：{self.stage}")
        valid_grades = GRADES_BY_STAGE.get(self.stage, [])
        if self.grade not in valid_grades:
            raise ProfileValidationError(f"年级“{self.grade}”不属于教育阶段“{self.stage}”")

        # 3.1 强基计划与学科竞赛学段限制（属于高中阶段专属配置）
        if self.stage != "高中":
            if self.in_strong_base or self.strong_base_school or self.strong_base_subject:
                raise ProfileValidationError(
                    "强基计划属于高中阶段专属配置，小学和初中阶段不得配置强基数据"
                )
            if self.in_competition or self.competition_subjects:
                raise ProfileValidationError(
                    "学科竞赛属于高中阶段专属配置，小学和初中阶段不得配置竞赛数据"
                )

        # 4. 验证强基计划
        if self.in_strong_base:
            if not self.strong_base_school:
                raise ProfileValidationError("已勾选参与强基计划，必须选择目标学校")
            if self.strong_base_school not in QIANGJI_UNIVERSITIES_AND_MAJORS:
                raise ProfileValidationError(
                    f"学校“{self.strong_base_school}”不是真实的39所强基试点高校"
                )
            if not self.strong_base_subject:
                raise ProfileValidationError("已勾选参与强基计划，必须选择目标学科")
            allowed_majors = QIANGJI_UNIVERSITIES_AND_MAJORS[self.strong_base_school]
            if self.strong_base_subject not in allowed_majors:
                raise ProfileValidationError(
                    f"学科“{self.strong_base_subject}”不属于{self.strong_base_school}公布的真实强基招生方向"
                )

        # 5. 验证学科竞赛
        if self.in_competition:
            if not self.competition_subjects:
                raise ProfileValidationError("已勾选参与学科竞赛，必须至少选择一个竞赛学科")
            for sub, stage_name in self.competition_subjects.items():
                if sub not in COMPETITION_SUBJECTS:
                    raise ProfileValidationError(f"“{sub}”不是规范的高中五大学科竞赛学科")
                valid_stages = COMPETITION_STAGES_BY_SUBJECT.get(sub, [])
                if not stage_name or stage_name not in valid_stages:
                    raise ProfileValidationError(
                        f"学科“{sub}”的备赛阶段“{stage_name}”不是官方真实赛段"
                    )

    def sanitize_for_stage(self) -> None:
        """Clear Qiangji and Competition fields if not in high school stage."""
        if self.stage != "高中":
            self.in_strong_base = False
            self.strong_base_school = None
            self.strong_base_subject = None
            self.in_competition = False
            self.competition_subjects = {}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BasicEducationProfile:
        stage = str(data.get("stage", ""))
        is_high = stage == "高中"
        return cls(
            province=str(data.get("province", "")),
            province_code=str(data.get("province_code", "")),
            city=str(data.get("city", "")),
            city_code=str(data.get("city_code", "")),
            stage=stage,
            grade=str(data.get("grade", "")),
            in_strong_base=bool(data.get("in_strong_base", False)) if is_high else False,
            strong_base_school=data.get("strong_base_school") if is_high else None,
            strong_base_subject=data.get("strong_base_subject") if is_high else None,
            in_competition=bool(data.get("in_competition", False)) if is_high else False,
            competition_subjects=dict(data.get("competition_subjects") or {}) if is_high else {},
            last_upgrade_year=data.get("last_upgrade_year"),
        )


@dataclass
class HigherEducationProfile:
    """Configuration state for higher education (undergraduate and graduate)."""

    school: str
    education_level: str
    major: str | None = None
    discipline_category: str | None = None
    discipline_first_level: str | None = None

    def validate(self) -> None:
        """Strictly validate higher education against official national datasets."""

        # 1. 验证真实高校
        if not self.school or not self.school.strip():
            raise ProfileValidationError("请填写就读学校")
        if not is_valid_school_name(self.school):
            raise ProfileValidationError(
                f"学校“{self.school}”不存在于教育部全国普通高等学校名单中。禁止使用未收录或虚构高校。"
            )

        # 2. 验证学历
        if self.education_level not in HIGHER_EDUCATION_LEVELS:
            raise ProfileValidationError(f"无效的学历：{self.education_level}")

        # 3. 本科路径验证
        if self.education_level == "本科":
            if not self.major or not self.major.strip():
                raise ProfileValidationError("本科学历必须选择专业")
            if not is_valid_undergraduate_major(self.major):
                raise ProfileValidationError(
                    f"专业“{self.major}”不存在于教育部本科专业目录中。禁止自由输入未备案专业。"
                )

        # 4. 研究生路径验证
        if self.education_level == "研究生":
            if not self.discipline_category or not self.discipline_category.strip():
                raise ProfileValidationError("研究生学历必须选择学科门类")
            if self.discipline_category not in GRADUATE_DISCIPLINE_CATEGORIES:
                raise ProfileValidationError(
                    f"学科门类“{self.discipline_category}”不属于教育部现行14项研究生学科门类"
                )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HigherEducationProfile:
        return cls(
            school=str(data.get("school", "")),
            education_level=str(data.get("education_level", "")),
            major=data.get("major"),
            discipline_category=data.get("discipline_category"),
            discipline_first_level=data.get("discipline_first_level"),
        )


@dataclass
class LearnerProfile:
    """Unified personal profile entity adhering to strict mutual exclusion."""

    education_type: EducationType | None = None
    basic: BasicEducationProfile | None = None
    higher: HigherEducationProfile | None = None
    updated_at: str | None = None

    @property
    def is_empty(self) -> bool:
        """Return True if no profile has been saved yet."""

        return self.education_type is None or (self.basic is None and self.higher is None)

    def validate_isolation(self) -> None:
        """Ensure strict isolation: only one educational system can be active."""

        if self.education_type == EducationType.BASIC:
            if self.basic is None:
                raise ProfileValidationError("当前类型为基础教育，但基础教育数据为空")
            if self.higher is not None:
                raise ProfileValidationError(
                    "数据隔离违规：保存基础教育时，高等教育数据必须完全清空"
                )
            self.basic.validate()
        elif self.education_type == EducationType.HIGHER:
            if self.higher is None:
                raise ProfileValidationError("当前类型为高等教育，但高等教育数据为空")
            if self.basic is not None:
                raise ProfileValidationError(
                    "数据隔离违规：保存高等教育时，基础教育数据必须完全清空"
                )
            self.higher.validate()
