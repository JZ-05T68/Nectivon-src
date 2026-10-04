"""Persistence service and business logic for personal learner profiles.

Adheres strictly to the Nectivon Local-First architecture:
1. All profile data is saved locally in an ACID SQLite database.
2. Never uploaded to servers, no cloud sync, no account system.
3. Strict mutual exclusion between basic education and higher education.
4. Annual grade promotion checked on July 1st.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

from src.training_profile_models import (
    BasicEducationProfile,
    EducationType,
    HigherEducationProfile,
    LearnerProfile,
)

LOGGER = logging.getLogger(__name__)

# Grade automatic progression mapping (only within safe study stages without graduation exams)
GRADE_PROGRESSION: dict[tuple[str, str], tuple[str, str]] = {
    ("小学", "一年级"): ("小学", "二年级"),
    ("小学", "二年级"): ("小学", "三年级"),
    ("小学", "三年级"): ("小学", "四年级"),
    ("小学", "四年级"): ("小学", "五年级"),
    ("小学", "五年级"): ("小学", "六年级"),
    ("小学", "六年级"): ("初中", "初一"),
    ("初中", "初一"): ("初中", "初二"),
    ("初中", "初二"): ("初中", "初三"),
    ("高中", "高一"): ("高中", "高二"),
    ("高中", "高二"): ("高中", "高三"),
}

# Terminal grades before high-stakes admission exams (Zhongkao, Gaokao)
# where automatic upgrade is strictly forbidden
NON_UPGRADE_GRADES: set[tuple[str, str]] = {
    ("初中", "初三"),
    ("高中", "高三"),
}


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TrainingProfileService:
    """Service handling profile storage, mutual exclusion, and grade promotion."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the local profile database schema."""

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learner_profile (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    education_type TEXT,
                    basic_province TEXT,
                    basic_province_code TEXT,
                    basic_city TEXT,
                    basic_city_code TEXT,
                    basic_stage TEXT,
                    basic_grade TEXT,
                    basic_in_strong_base INTEGER DEFAULT 0,
                    basic_strong_base_school TEXT,
                    basic_strong_base_subject TEXT,
                    basic_in_competition INTEGER DEFAULT 0,
                    basic_competition_subjects TEXT,
                    basic_last_upgrade_year INTEGER,
                    higher_school TEXT,
                    higher_education_level TEXT,
                    higher_major TEXT,
                    higher_discipline_category TEXT,
                    higher_discipline_first_level TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # Ensure row with id=1 exists
            conn.execute(
                """
                INSERT OR IGNORE INTO learner_profile (id, created_at, updated_at)
                VALUES (1, ?, ?)
                """,
                (_utc_now_iso(), _utc_now_iso()),
            )
            conn.commit()

    def get_profile(
        self, check_grade_upgrade: bool = True, reference_date: date | None = None
    ) -> LearnerProfile:
        """Retrieve current learner profile, running annual grade upgrade check if applicable."""

        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM learner_profile WHERE id = 1").fetchone()
            if not row or not row["education_type"]:
                return LearnerProfile(education_type=None, basic=None, higher=None, updated_at=None)

            ed_type = EducationType(row["education_type"])
            basic_profile: BasicEducationProfile | None = None
            higher_profile: HigherEducationProfile | None = None

            if ed_type == EducationType.BASIC:
                stage = row["basic_stage"] or ""
                is_high_school = stage == "高中"
                comp_subjects = {}
                if is_high_school and row["basic_competition_subjects"]:
                    try:
                        comp_subjects = json.loads(row["basic_competition_subjects"])
                    except Exception:
                        comp_subjects = {}
                basic_profile = BasicEducationProfile(
                    province=row["basic_province"] or "",
                    province_code=row["basic_province_code"] or "",
                    city=row["basic_city"] or "",
                    city_code=row["basic_city_code"] or "",
                    stage=stage,
                    grade=row["basic_grade"] or "",
                    in_strong_base=(
                        bool(row["basic_in_strong_base"]) if is_high_school else False
                    ),
                    strong_base_school=(
                        row["basic_strong_base_school"] if is_high_school else None
                    ),
                    strong_base_subject=(
                        row["basic_strong_base_subject"] if is_high_school else None
                    ),
                    in_competition=(
                        bool(row["basic_in_competition"]) if is_high_school else False
                    ),
                    competition_subjects=comp_subjects,
                    last_upgrade_year=row["basic_last_upgrade_year"],
                )
            elif ed_type == EducationType.HIGHER:
                higher_profile = HigherEducationProfile(
                    school=row["higher_school"] or "",
                    education_level=row["higher_education_level"] or "",
                    major=row["higher_major"],
                    discipline_category=row["higher_discipline_category"],
                    discipline_first_level=row["higher_discipline_first_level"],
                )

        profile = LearnerProfile(
            education_type=ed_type,
            basic=basic_profile,
            higher=higher_profile,
            updated_at=row["updated_at"],
        )

        if check_grade_upgrade and profile.basic is not None:
            checked_profile, _was_upgraded, _ = self.check_and_apply_auto_upgrade(
                profile.basic, current_date=reference_date
            )
            if checked_profile.to_dict() != profile.basic.to_dict():
                # Persist both a real promotion and a terminal-grade check.  The
                # latter only updates ``last_upgrade_year`` so 初三/高三 are not
                # rechecked on every page load after July 1.
                self._save_basic_internal(checked_profile)
                profile.basic = checked_profile

        return profile

    def save_basic_profile(
        self,
        profile: BasicEducationProfile,
        reference_date: date | None = None,
    ) -> None:
        """Save basic education profile and strictly clear ALL higher education data.

        If stage is not high school ('高中'), automatically wipes Qiangji and Competition data.
        A manual save on or after July 1 is authoritative for the current school
        year, so it is marked as already checked.  Without this marker a freshly
        entered current grade would be promoted immediately on the next page load.
        """
        if profile.stage != "高中":
            profile.sanitize_for_stage()
        today = reference_date or date.today()
        if profile.last_upgrade_year is None and today >= date(today.year, 7, 1):
            profile.last_upgrade_year = today.year
        profile.validate()
        self._save_basic_internal(profile)

    def _save_basic_internal(self, profile: BasicEducationProfile) -> None:
        """Internal helper to write basic education and nullify higher education fields."""
        now = _utc_now_iso()
        if profile.stage != "高中":
            in_sb = 0
            sb_school = None
            sb_subject = None
            in_comp = 0
            comp_json = None
        else:
            in_sb = 1 if profile.in_strong_base else 0
            sb_school = profile.strong_base_school
            sb_subject = profile.strong_base_subject
            in_comp = 1 if profile.in_competition else 0
            comp_json = json.dumps(profile.competition_subjects, ensure_ascii=False)

        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                UPDATE learner_profile SET
                    education_type = ?,
                    basic_province = ?,
                    basic_province_code = ?,
                    basic_city = ?,
                    basic_city_code = ?,
                    basic_stage = ?,
                    basic_grade = ?,
                    basic_in_strong_base = ?,
                    basic_strong_base_school = ?,
                    basic_strong_base_subject = ?,
                    basic_in_competition = ?,
                    basic_competition_subjects = ?,
                    basic_last_upgrade_year = ?,
                    -- Clear ALL higher education fields
                    higher_school = NULL,
                    higher_education_level = NULL,
                    higher_major = NULL,
                    higher_discipline_category = NULL,
                    higher_discipline_first_level = NULL,
                    updated_at = ?
                WHERE id = 1
                """,
                (
                    EducationType.BASIC.value,
                    profile.province,
                    profile.province_code,
                    profile.city,
                    profile.city_code,
                    profile.stage,
                    profile.grade,
                    in_sb,
                    sb_school,
                    sb_subject,
                    in_comp,
                    comp_json,
                    profile.last_upgrade_year,
                    now,
                ),
            )
            conn.commit()
        LOGGER.info("已保存基础教育配置，并彻底清空高等教育数据")

    def save_higher_profile(self, profile: HigherEducationProfile) -> None:
        """Save higher education profile and strictly clear ALL basic education data."""

        profile.validate()
        now = _utc_now_iso()
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                UPDATE learner_profile SET
                    education_type = ?,
                    higher_school = ?,
                    higher_education_level = ?,
                    higher_major = ?,
                    higher_discipline_category = ?,
                    higher_discipline_first_level = ?,
                    -- Clear ALL basic education fields
                    basic_province = NULL,
                    basic_province_code = NULL,
                    basic_city = NULL,
                    basic_city_code = NULL,
                    basic_stage = NULL,
                    basic_grade = NULL,
                    basic_in_strong_base = 0,
                    basic_strong_base_school = NULL,
                    basic_strong_base_subject = NULL,
                    basic_in_competition = 0,
                    basic_competition_subjects = NULL,
                    basic_last_upgrade_year = NULL,
                    updated_at = ?
                WHERE id = 1
                """,
                (
                    EducationType.HIGHER.value,
                    profile.school,
                    profile.education_level,
                    profile.major,
                    profile.discipline_category,
                    profile.discipline_first_level,
                    now,
                ),
            )
            conn.commit()
        LOGGER.info("已保存高等教育配置，并彻底清空基础教育数据")

    def clear_all(self) -> None:
        """Completely reset all profile configurations."""

        now = _utc_now_iso()
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                UPDATE learner_profile SET
                    education_type = NULL,
                    basic_province = NULL,
                    basic_province_code = NULL,
                    basic_city = NULL,
                    basic_city_code = NULL,
                    basic_stage = NULL,
                    basic_grade = NULL,
                    basic_in_strong_base = 0,
                    basic_strong_base_school = NULL,
                    basic_strong_base_subject = NULL,
                    basic_in_competition = 0,
                    basic_competition_subjects = NULL,
                    basic_last_upgrade_year = NULL,
                    higher_school = NULL,
                    higher_education_level = NULL,
                    higher_major = NULL,
                    higher_discipline_category = NULL,
                    higher_discipline_first_level = NULL,
                    updated_at = ?
                WHERE id = 1
                """,
                (now,),
            )
            conn.commit()

    @staticmethod
    def check_and_apply_auto_upgrade(
        profile: BasicEducationProfile, current_date: date | None = None
    ) -> tuple[BasicEducationProfile, bool, str]:
        """Evaluate annual grade promotion rules as of July 1st.

        Rules:
        - Must be on or after July 1st of current year.
        - Must not have upgraded already in this year.
        - Allowed:
          小学一年级→小学二年级→...→六年级→初一
          初一→初二→初三
          高一→高二→高三
        - FORBIDDEN:
          初三→高一 (Requires Zhongkao and admission selection)
          高三→高等教育 (Requires Gaokao and admission selection)
        """

        today = current_date or date.today()
        current_year = today.year
        cutoff_date = date(current_year, 7, 1)

        # 检查是否已达到7月1日
        if today < cutoff_date:
            return profile, False, f"未到{current_year}年7月1日升级检查点，保持当前年级"

        # 检查当年是否已经升级过
        if profile.last_upgrade_year is not None and profile.last_upgrade_year >= current_year:
            return profile, False, f"{current_year}年已执行过年级检查，无需重复升级"

        current_key = (profile.stage, profile.grade)

        # 检查是否为禁止自动升级节点（初三、高三升学考试）
        if current_key in NON_UPGRADE_GRADES:
            # 记录当年已检查，避免重复提示，但不升级年级
            new_profile = BasicEducationProfile.from_dict(profile.to_dict())
            new_profile.last_upgrade_year = current_year
            msg = (
                f"{profile.grade}涉及升学考试与自主录取，禁止自动升级，"
                "保持原状态并提示用户按实际录取调整"
            )
            return new_profile, False, msg

        # 检查是否在允许升级映射表中
        if current_key in GRADE_PROGRESSION:
            new_stage, new_grade = GRADE_PROGRESSION[current_key]
            new_profile = BasicEducationProfile.from_dict(profile.to_dict())
            new_profile.stage = new_stage
            new_profile.grade = new_grade
            new_profile.last_upgrade_year = current_year
            msg = (
                f"年级已根据7月1日自动升级规则，从【{profile.stage} {profile.grade}】"
                f"升级为【{new_stage} {new_grade}】"
            )
            return new_profile, True, msg

        return profile, False, f"未匹配到升级规则，保持【{profile.stage} {profile.grade}】"
