"""Comprehensive test suite for Phase 1: Targeted Training Profile Configuration.

Validates:
1. Data authenticity (fake universities like '中国邮电大学', fake competitions rejected).
2. Basic education profile (standard high school, Qiangji plan, Olympiad competition).
3. Higher education profile (undergraduate, current 14 graduate disciplines and extensions).
4. Edit/save state machine and dirty state protection.
5. Strict mutual exclusion & data isolation between basic and higher education.
6. Annual grade promotion rules as of July 1st (allowed paths vs. forbidden exams).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
import streamlit

from src.training_profile_data import (
    CITIES_BY_PROVINCE,
    GRADUATE_DISCIPLINE_CATEGORIES,
    PROVINCES,
    QIANGJI_UNIVERSITIES_AND_MAJORS,
    get_first_level_disciplines,
    is_valid_school_name,
    resolve_city_by_coordinates,
    search_official_schools,
)
from src.training_profile_models import (
    BasicEducationProfile,
    EducationType,
    HigherEducationProfile,
    ProfileValidationError,
)
from src.training_profile_service import TrainingProfileService
from src.training_profile_ui import (
    _apply_pending_location_state,
    _clear_draft_states,
    _handle_browser_location_result,
)


@pytest.fixture
def temp_service(tmp_path: Path) -> TrainingProfileService:
    """Provide a fresh isolated TrainingProfileService for testing."""

    db_path = tmp_path / "test_profile.db"
    return TrainingProfileService(db_path)


def test_browser_location_result_updates_city_only_without_coordinates() -> None:
    """Browser geolocation may return only province/city to Python state."""

    with patch.dict("streamlit.session_state", {}, clear=True):
        should_rerun = _handle_browser_location_result(
            {
                "status": "success",
                "request_id": "location-1",
                "province": "江苏省",
                "city": "南京市",
            }
        )
        assert should_rerun is True
        assert streamlit.session_state["profile_pending_location"] == ("江苏省", "南京市")
        assert "latitude" not in streamlit.session_state
        assert "longitude" not in streamlit.session_state

        _apply_pending_location_state()
        assert streamlit.session_state["basic_province_select"] == "江苏省"
        assert streamlit.session_state["basic_city_select"] == "南京市"
        assert "profile_pending_location" not in streamlit.session_state


def test_browser_location_result_is_idempotent() -> None:
    """A persisted component result must not cause an infinite rerun loop."""

    result = {
        "status": "success",
        "request_id": "location-2",
        "province": "江苏省",
        "city": "南京市",
    }
    with patch.dict("streamlit.session_state", {}, clear=True):
        assert _handle_browser_location_result(result) is True
        assert _handle_browser_location_result(result) is False


def test_discard_clears_actual_streamlit_widget_drafts() -> None:
    """Discard must remove keyed widget values, not only legacy mirrors."""

    draft_state = {
        "basic_province_select": "江苏省",
        "basic_city_select": "南京市",
        "basic_stage_select": "初中",
        "basic_grade_select": "初二",
        "edu_type_radio_input": "高等教育",
        "higher_school_dropdown": "南京邮电大学",
        "basic_comp_check_数学": True,
        "basic_comp_stage_数学": "省级竞赛",
        "unrelated_state": "keep-me",
    }
    with patch.dict("streamlit.session_state", draft_state, clear=True):
        _clear_draft_states()
        assert streamlit.session_state == {"unrelated_state": "keep-me"}


# ==============================================================================
# 1. 数据真实性与边界测试
# ==============================================================================


def test_fake_university_china_post_rejected() -> None:
    """Verify that '中国邮电大学' is strictly rejected and cannot be matched."""

    assert not is_valid_school_name("中国邮电大学"), "中国邮电大学 must not be a valid school"
    matches = search_official_schools("中国邮电大学")
    assert matches == [], f"Search for 中国邮电大学 must be empty, got {matches}"

    # Creating higher education profile with 中国邮电大学 must raise validation error
    with pytest.raises(ProfileValidationError, match="不存在于教育部全国普通高等学校名单中"):
        profile = HigherEducationProfile(
            school="中国邮电大学",
            education_level="本科",
            major="计算机科学与技术",
        )
        profile.validate()


def test_real_postal_universities_accepted() -> None:
    """Verify real postal universities (北京邮电大学, 南京邮电大学) are accepted."""

    for real_school in ["北京邮电大学", "南京邮电大学", "重庆邮电大学", "西安邮电大学"]:
        assert is_valid_school_name(real_school), f"{real_school} should be recognized as valid"
        matches = search_official_schools(real_school[:4])
        assert real_school in matches


def test_nonexistent_school_rejected() -> None:
    """Verify fictitious school names cannot be saved."""

    for fake in ["宇宙理工大学", "魔法师范学院", "火星工程大学"]:
        assert not is_valid_school_name(fake)
        with pytest.raises(ProfileValidationError):
            HigherEducationProfile(
                school=fake, education_level="本科", major="软件工程"
            ).validate()


def test_fake_competition_rejected() -> None:
    """Verify fictitious competitions or stages are rejected."""

    # 1. 非法学科
    with pytest.raises(ProfileValidationError, match="不是规范的高中五大学科竞赛学科"):
        BasicEducationProfile(
            province="江苏省", province_code="32", city="南京市", city_code="3201",
            stage="高中", grade="高一",
            in_competition=True,
            competition_subjects={"电子竞技": "全国总决赛"},
        ).validate()

    # 2. 真实学科但虚构赛段
    with pytest.raises(ProfileValidationError, match="不是官方真实赛段"):
        BasicEducationProfile(
            province="江苏省", province_code="32", city="南京市", city_code="3201",
            stage="高中", grade="高一",
            in_competition=True,
            competition_subjects={"数学": "全国数学王者争霸段位赛"},
        ).validate()


def test_qiangji_university_and_majors_authenticity() -> None:
    """Verify Qiangji 39 pilot universities and strict major mapping."""

    assert len(QIANGJI_UNIVERSITIES_AND_MAJORS) == 39, "Must have exactly 39 Qiangji universities"

    # 非39所高校参与强基报错
    with pytest.raises(ProfileValidationError, match="不是真实的39所强基试点高校"):
        BasicEducationProfile(
            province="江苏省", province_code="32", city="南京市", city_code="3201",
            stage="高中", grade="高一",
            in_strong_base=True,
            strong_base_school="苏州大学",
            strong_base_subject="物理学",
        ).validate()

    # 39所高校但非该校真实招生方向报错
    with pytest.raises(ProfileValidationError, match="不属于东北大学公布的真实强基招生方向"):
        BasicEducationProfile(
            province="江苏省", province_code="32", city="南京市", city_code="3201",
            stage="高中", grade="高一",
            in_strong_base=True,
            strong_base_school="东北大学",
            strong_base_subject="中国语言文学类（古文字学方向）",  # 东北大学强基仅招自动化
        ).validate()


# ==============================================================================
# 2. 基础教育测试
# ==============================================================================


def test_administrative_divisions_order() -> None:
    """Verify provinces and cities are strictly ordered ascending by code."""

    # Provinces
    prov_codes = [int(code) for code, _ in PROVINCES]
    assert prov_codes == sorted(prov_codes), "Provinces must be strictly sorted by code"
    assert PROVINCES[0] == ("11", "北京市")
    assert PROVINCES[1] == ("12", "天津市")
    assert PROVINCES[2] == ("13", "河北省")
    assert PROVINCES[3] == ("14", "山西省")
    assert PROVINCES[4] == ("15", "内蒙古自治区")

    # Cities for each province
    for prov_code, cities in CITIES_BY_PROVINCE.items():
        city_codes = [int(code) for code, _ in cities]
        assert city_codes == sorted(city_codes), f"Cities for {prov_code} must be sorted by code"

    # Jiangsu province verification
    js_cities = [name for _, name in CITIES_BY_PROVINCE["32"]]
    assert js_cities[:5] == ["南京市", "无锡市", "徐州市", "常州市", "苏州市"]


def test_offline_city_location_resolution() -> None:
    """Verify city-level offline location resolves province and city without saving coords."""

    res = resolve_city_by_coordinates(32.06, 118.79)
    assert res == ("江苏省", "南京市")

    res_bj = resolve_city_by_coordinates(39.90, 116.40)
    assert res_bj == ("北京市", "市辖区")


def test_standard_high_school_user(temp_service: TrainingProfileService) -> None:
    """Test standard high school student without Qiangji or Olympiads."""

    bp = BasicEducationProfile(
        province="江苏省", province_code="32", city="南京市", city_code="3201",
        stage="高中", grade="高一",
        in_strong_base=False,
        in_competition=False,
    )
    temp_service.save_basic_profile(bp)

    loaded = temp_service.get_profile(check_grade_upgrade=False)
    assert loaded.education_type == EducationType.BASIC
    assert loaded.basic is not None
    assert loaded.basic.stage == "高中"
    assert loaded.basic.grade == "高一"
    assert not loaded.basic.in_strong_base
    assert not loaded.basic.in_competition
    assert loaded.higher is None


def test_qiangji_user(temp_service: TrainingProfileService) -> None:
    """Test high school student enrolled in Qiangji plan."""

    bp = BasicEducationProfile(
        province="浙江省", province_code="33", city="杭州市", city_code="3301",
        stage="高中", grade="高二",
        in_strong_base=True,
        strong_base_school="清华大学",
        strong_base_subject="数理基础科学",
        in_competition=False,
    )
    temp_service.save_basic_profile(bp)

    loaded = temp_service.get_profile(check_grade_upgrade=False)
    assert loaded.basic is not None
    assert loaded.basic.in_strong_base
    assert loaded.basic.strong_base_school == "清华大学"
    assert loaded.basic.strong_base_subject == "数理基础科学"


def test_competition_user(temp_service: TrainingProfileService) -> None:
    """Test high school student with multiple Olympiad subjects."""

    comp_dict = {
        "数学": "全国高中数学联赛备战阶段（高联赛段/争夺省一）",
        "物理": "全国中学生物理竞赛复赛备战阶段（省赛/省队选拔）",
    }
    bp = BasicEducationProfile(
        province="北京市", province_code="11", city="市辖区", city_code="1101",
        stage="高中", grade="高一",
        in_strong_base=False,
        in_competition=True,
        competition_subjects=comp_dict,
    )
    temp_service.save_basic_profile(bp)

    loaded = temp_service.get_profile(check_grade_upgrade=False)
    assert loaded.basic is not None
    assert loaded.basic.in_competition
    assert loaded.basic.competition_subjects == comp_dict


# ==============================================================================
# 3. 高等教育测试
# ==============================================================================


def test_undergraduate_user(temp_service: TrainingProfileService) -> None:
    """Test undergraduate student configuration."""

    hp = HigherEducationProfile(
        school="北京大学",
        education_level="本科",
        major="软件工程",
    )
    temp_service.save_higher_profile(hp)

    loaded = temp_service.get_profile(check_grade_upgrade=False)
    assert loaded.education_type == EducationType.HIGHER
    assert loaded.higher is not None
    assert loaded.higher.school == "北京大学"
    assert loaded.higher.education_level == "本科"
    assert loaded.higher.major == "软件工程"
    assert loaded.basic is None


def test_graduate_user_with_14_disciplines(temp_service: TrainingProfileService) -> None:
    """Test graduate configuration and the 14 current discipline categories."""

    assert len(GRADUATE_DISCIPLINE_CATEGORIES) == 14

    hp = HigherEducationProfile(
        school="清华大学",
        education_level="研究生",
        discipline_category="08 工学",
        discipline_first_level="0812 计算机科学与技术",
    )
    temp_service.save_higher_profile(hp)

    loaded = temp_service.get_profile(check_grade_upgrade=False)
    assert loaded.higher is not None
    assert loaded.higher.education_level == "研究生"
    assert loaded.higher.discipline_category == "08 工学"
    assert loaded.higher.discipline_first_level == "0812 计算机科学与技术"

    # Test extension interface
    first_levels = get_first_level_disciplines("08 工学")
    assert "0812 计算机科学与技术" in first_levels
    assert "0835 软件工程" in first_levels


# ==============================================================================
# 4. 数据隔离与互斥测试
# ==============================================================================


def test_strict_data_isolation_between_systems(temp_service: TrainingProfileService) -> None:
    """Verify saving basic education clears higher education, and vice versa."""

    # 1. 保存基础教育
    bp = BasicEducationProfile(
        province="江苏省", province_code="32", city="南京市", city_code="3201",
        stage="小学", grade="三年级",
    )
    temp_service.save_basic_profile(bp)
    p1 = temp_service.get_profile(check_grade_upgrade=False)
    assert p1.education_type == EducationType.BASIC
    assert p1.basic is not None
    assert p1.higher is None

    # 2. 保存高等教育 -> 基础教育必须彻底清空
    hp = HigherEducationProfile(
        school="复旦大学",
        education_level="本科",
        major="哲学",
    )
    temp_service.save_higher_profile(hp)
    p2 = temp_service.get_profile(check_grade_upgrade=False)
    assert p2.education_type == EducationType.HIGHER
    assert p2.higher is not None
    assert p2.basic is None, (
        "Basic education data MUST be completely cleared when saving higher education"
    )

    # 3. 再次保存基础教育 -> 高等教育必须彻底清空
    temp_service.save_basic_profile(bp)
    p3 = temp_service.get_profile(check_grade_upgrade=False)
    assert p3.education_type == EducationType.BASIC
    assert p3.basic is not None
    assert p3.higher is None, (
        "Higher education data MUST be completely cleared when saving basic education"
    )


# ==============================================================================
# 5. 年级自动升级规则测试
# ==============================================================================


@pytest.mark.parametrize(
    ("stage", "grade", "expected_stage", "expected_grade", "should_upgrade"),
    [
        # 小学阶段
        ("小学", "一年级", "小学", "二年级", True),
        ("小学", "二年级", "小学", "三年级", True),
        ("小学", "三年级", "小学", "四年级", True),
        ("小学", "四年级", "小学", "五年级", True),
        ("小学", "五年级", "小学", "六年级", True),
        ("小学", "六年级", "初中", "初一", True),
        # 初中阶段
        ("初中", "初一", "初中", "初二", True),
        ("初中", "初二", "初中", "初三", True),
        ("初中", "初三", "初中", "初三", False),  # 初三禁止升级（中考升学）
        # 高中阶段
        ("高中", "高一", "高中", "高二", True),
        ("高中", "高二", "高中", "高三", True),
        ("高中", "高三", "高中", "高三", False),  # 高三禁止升级（高考升学）
    ],
)
def test_annual_grade_promotion_rules(
    stage: str,
    grade: str,
    expected_stage: str,
    expected_grade: str,
    should_upgrade: bool,
) -> None:
    """Test annual grade promotion on July 1st across all school tiers."""

    bp = BasicEducationProfile(
        province="江苏省", province_code="32", city="南京市", city_code="3201",
        stage=stage, grade=grade, last_upgrade_year=None,
    )

    # 7月1日之前不升级
    p_before, was_upgraded_before, _ = TrainingProfileService.check_and_apply_auto_upgrade(
        bp, current_date=date(2026, 6, 30)
    )
    assert not was_upgraded_before
    assert p_before.stage == stage
    assert p_before.grade == grade

    # 7月1日触发检查
    p_after, was_upgraded_after, msg = TrainingProfileService.check_and_apply_auto_upgrade(
        bp, current_date=date(2026, 7, 1)
    )
    assert was_upgraded_after == should_upgrade
    assert p_after.stage == expected_stage
    assert p_after.grade == expected_grade

    # 当年已升级，再次检查不会重复升级
    if should_upgrade:
        p_repeat, was_upgraded_repeat, _ = TrainingProfileService.check_and_apply_auto_upgrade(
            p_after, current_date=date(2026, 9, 1)
        )
        assert not was_upgraded_repeat
        assert p_repeat.grade == expected_grade


def test_manual_save_after_july_is_not_immediately_promoted(
    temp_service: TrainingProfileService,
) -> None:
    """A current grade entered by the user after July is authoritative."""

    profile = BasicEducationProfile(
        province="江苏省",
        province_code="32",
        city="南京市",
        city_code="3201",
        stage="初中",
        grade="初一",
    )
    temp_service.save_basic_profile(profile, reference_date=date(2026, 10, 2))

    loaded = temp_service.get_profile(
        check_grade_upgrade=True,
        reference_date=date(2026, 10, 2),
    )
    assert loaded.basic is not None
    assert loaded.basic.grade == "初一"
    assert loaded.basic.last_upgrade_year == 2026


def test_terminal_grade_check_year_is_persisted(
    temp_service: TrainingProfileService,
) -> None:
    """初三/高三 remain unchanged but do not repeat the annual check forever."""

    profile = BasicEducationProfile(
        province="江苏省",
        province_code="32",
        city="南京市",
        city_code="3201",
        stage="初中",
        grade="初三",
    )
    temp_service.save_basic_profile(profile, reference_date=date(2026, 6, 30))

    first = temp_service.get_profile(
        check_grade_upgrade=True,
        reference_date=date(2026, 7, 1),
    )
    assert first.basic is not None
    assert first.basic.grade == "初三"
    assert first.basic.last_upgrade_year == 2026

    persisted = temp_service.get_profile(check_grade_upgrade=False)
    assert persisted.basic is not None
    assert persisted.basic.last_upgrade_year == 2026
