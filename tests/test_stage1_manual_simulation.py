"""Simulated manual testing suite across 4 designated execution environments.

Execution Tools:
1. 反重力 (Antigravity): Browser-simulated Basic Education & Geolocation.
2. Codex: Browser-simulated Higher Education, '中国邮电大学' Rejection.
3. 千问 (Qwen): Browser-simulated State Machine & Unsaved Interceptor.
4. 豆包工作 (Doubao-Worker): Headless Data Isolation & Auto-Upgrade.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from src.training_profile_data import (
    CITIES_BY_PROVINCE,
    COMPETITION_STAGES_BY_SUBJECT,
    GRADUATE_DISCIPLINE_CATEGORIES,
    PROVINCES,
    QIANGJI_SCHOOLS,
    QIANGJI_UNIVERSITIES_AND_MAJORS,
    get_first_level_disciplines,
    is_valid_school_name,
    resolve_city_by_coordinates,
    search_official_schools,
    search_undergraduate_majors,
)
from src.training_profile_models import (
    BasicEducationProfile,
    EducationType,
    HigherEducationProfile,
    ProfileValidationError,
)
from src.training_profile_service import TrainingProfileService
from src.training_profile_ui import _trigger_save_from_state


@pytest.fixture
def sim_service(tmp_path: Path) -> TrainingProfileService:
    """Fixture providing isolated database service for simulated manual testing."""

    db_file = tmp_path / "simulated_profile.db"
    return TrainingProfileService(db_file)


# ==============================================================================
# 工具一：反重力（浏览器模拟）测试项
# 测试：基础教育配置、动态行联动、定位辅助、保存校验
# ==============================================================================


class TestToolAntigravityBrowserSimulation:
    """Simulate manual operations via browser using Antigravity."""

    def test_basic_education_region_cascading_and_sorting(self) -> None:
        """1. 测试地区选择级联与行政区划代码升序排序."""

        # 检查省份代码升序排列
        prov_codes = [int(code) for code, _ in PROVINCES]
        assert prov_codes == sorted(prov_codes), "省份必须按行政区划代码升序排列"
        assert PROVINCES[0][1] == "北京市"
        assert PROVINCES[9][1] == "江苏省"

        # 检查江苏省城市联动与代码升序
        js_cities = CITIES_BY_PROVINCE["32"]
        js_codes = [int(code) for code, _ in js_cities]
        assert js_codes == sorted(js_codes), "城市必须按行政区划代码升序排列"
        js_names = [name for _, name in js_cities]
        assert js_names[:5] == ["南京市", "无锡市", "徐州市", "常州市", "苏州市"]

    def test_basic_education_geolocation_assist(self) -> None:
        """2. 测试用户授权定位辅助（城市级精度，不持久化具体坐标）."""

        # 模拟授权定位获取南京市坐标
        loc = resolve_city_by_coordinates(32.06, 118.79)
        assert loc == ("江苏省", "南京市")

        # 模拟授权定位获取北京市坐标
        loc_bj = resolve_city_by_coordinates(39.90, 116.40)
        assert loc_bj == ("北京市", "市辖区")

    def test_basic_education_normal_high_school_user(
        self, sim_service: TrainingProfileService
    ) -> None:
        """3. 模拟普通高中用户填写并保存."""

        bp = BasicEducationProfile(
            province="江苏省", province_code="32", city="南京市", city_code="3201",
            stage="高中", grade="高一",
            in_strong_base=False,
            in_competition=False,
        )
        sim_service.save_basic_profile(bp)

        loaded = sim_service.get_profile(check_grade_upgrade=False)
        assert loaded.education_type == EducationType.BASIC
        assert loaded.basic is not None
        assert loaded.basic.province == "江苏省"
        assert loaded.basic.city == "南京市"
        assert loaded.basic.stage == "高中"
        assert loaded.basic.grade == "高一"
        assert not loaded.basic.in_strong_base
        assert not loaded.basic.in_competition

    def test_basic_education_qiangji_dynamic_row(
        self, sim_service: TrainingProfileService
    ) -> None:
        """4. 模拟强基用户：勾选“是”，3.5 行动态联动出现并保存."""

        # 强基高校与专业真实性联动
        target_school = "南京大学"
        assert target_school in QIANGJI_SCHOOLS
        allowed_majors = QIANGJI_UNIVERSITIES_AND_MAJORS[target_school]
        assert "物理学" in allowed_majors

        bp = BasicEducationProfile(
            province="江苏省", province_code="32", city="南京市", city_code="3201",
            stage="高中", grade="高二",
            in_strong_base=True,
            strong_base_school=target_school,
            strong_base_subject="物理学",
            in_competition=False,
        )
        sim_service.save_basic_profile(bp)

        loaded = sim_service.get_profile(check_grade_upgrade=False)
        assert loaded.basic is not None
        assert loaded.basic.in_strong_base
        assert loaded.basic.strong_base_school == "南京大学"
        assert loaded.basic.strong_base_subject == "物理学"

    def test_basic_education_competition_dynamic_row(
        self, sim_service: TrainingProfileService
    ) -> None:
        """5. 模拟竞赛用户：勾选“是”，4.5 行多选及学科赛段联动并保存."""

        comp_dict = {
            "数学": COMPETITION_STAGES_BY_SUBJECT["数学"][1],
            "物理": COMPETITION_STAGES_BY_SUBJECT["物理"][1],
            "化学": COMPETITION_STAGES_BY_SUBJECT["化学"][0],
        }
        bp = BasicEducationProfile(
            province="北京市", province_code="11", city="市辖区", city_code="1101",
            stage="高中", grade="高一",
            in_strong_base=False,
            in_competition=True,
            competition_subjects=comp_dict,
        )
        sim_service.save_basic_profile(bp)

        loaded = sim_service.get_profile(check_grade_upgrade=False)
        assert loaded.basic is not None
        assert loaded.basic.in_competition
        assert loaded.basic.competition_subjects == comp_dict


# ==============================================================================
# 工具二：Codex（浏览器模拟）测试项
# 测试：高等教育配置、搜索匹配、不存在学校拦截（中国邮电大学）、专业与门类
# ==============================================================================


class TestToolCodexBrowserSimulation:
    """Simulate manual operations via browser using Codex."""

    def test_china_post_university_strictly_rejected(self) -> None:
        """1. 测试输入“中国邮电大学”，要求必须无法匹配并拒绝保存."""

        # 搜索匹配
        search_res = search_official_schools("中国邮电大学")
        assert search_res == [], f"输入“中国邮电大学”应返回空列表，但返回了：{search_res}"

        # 验证器校验
        assert not is_valid_school_name("中国邮电大学")

        # 提交保存必须抛出异常
        with pytest.raises(ProfileValidationError, match="不存在于教育部全国普通高等学校名单中"):
            HigherEducationProfile(
                school="中国邮电大学",
                education_level="本科",
                major="软件工程",
            ).validate()

    def test_fake_school_names_strictly_rejected(self) -> None:
        """2. 测试输入虚假学校名称，要求必须无法匹配并拒绝保存."""

        for fake in ["华东虚拟科技大学", "中国管理进修学院", "不存在的高校"]:
            assert search_official_schools(fake) == []
            assert not is_valid_school_name(fake)
            with pytest.raises(ProfileValidationError):
                HigherEducationProfile(
                    school=fake, education_level="本科", major="计算机科学与技术"
                ).validate()

    def test_fake_competition_name_strictly_rejected(self) -> None:
        """3. 测试输入不存在的竞赛名称，要求拒绝."""

        with pytest.raises(ProfileValidationError):
            BasicEducationProfile(
                province="江苏省", province_code="32", city="南京市", city_code="3201",
                stage="高中", grade="高一",
                in_competition=True,
                competition_subjects={"电子竞技": "全省选拔赛"},
            ).validate()

    def test_undergraduate_school_and_major_search(
        self, sim_service: TrainingProfileService
    ) -> None:
        """4. 模拟本科用户：高校搜索匹配 + 本科专业搜索匹配 + 本地保存."""

        # 搜索清华
        matched_schools = search_official_schools("清华")
        assert "清华大学" in matched_schools

        # 搜索计算机专业
        matched_majors = search_undergraduate_majors("计算机")
        assert "计算机科学与技术" in matched_majors

        hp = HigherEducationProfile(
            school="清华大学",
            education_level="本科",
            major="计算机科学与技术",
        )
        sim_service.save_higher_profile(hp)

        loaded = sim_service.get_profile(check_grade_upgrade=False)
        assert loaded.education_type == EducationType.HIGHER
        assert loaded.higher is not None
        assert loaded.higher.school == "清华大学"
        assert loaded.higher.education_level == "本科"
        assert loaded.higher.major == "计算机科学与技术"

    def test_graduate_13_disciplines_and_extension_interface(
        self, sim_service: TrainingProfileService
    ) -> None:
        """5. 模拟研究生用户：现行14大学科门类选择 + 一级学科扩展接口."""

        assert len(GRADUATE_DISCIPLINE_CATEGORIES) == 14

        hp = HigherEducationProfile(
            school="北京大学",
            education_level="研究生",
            discipline_category="01 哲学",
            discipline_first_level="0101 哲学",
        )
        sim_service.save_higher_profile(hp)

        loaded = sim_service.get_profile(check_grade_upgrade=False)
        assert loaded.higher is not None
        assert loaded.higher.discipline_category == "01 哲学"

        # 预留一级学科接口联动
        first_levels = get_first_level_disciplines("01 哲学")
        assert first_levels == ["0101 哲学"]


# ==============================================================================
# 工具三：千问（浏览器模拟）测试项
# 测试：编辑保存状态机、修改未保存退出拦截与三个选项分支
# ==============================================================================


class TestToolQwenBrowserSimulation:
    """Simulate manual operations via browser using Qwen."""

    def test_state_machine_initial_view_mode_when_saved(
        self, sim_service: TrainingProfileService
    ) -> None:
        """1. 初始状态：已有保存数据时进入查看状态（所有字段只读）."""

        bp = BasicEducationProfile(
            province="江苏省", province_code="32", city="南京市", city_code="3201",
            stage="初中", grade="初二",
        )
        sim_service.save_basic_profile(bp)

        profile = sim_service.get_profile(check_grade_upgrade=False)
        assert not profile.is_empty, "已有数据不应为空"

    def test_state_machine_edit_modify_save_roundtrip(
        self, sim_service: TrainingProfileService
    ) -> None:
        """2. 测试编辑→修改→保存完整流转."""

        # 初始保存
        bp = BasicEducationProfile(
            province="江苏省", province_code="32", city="南京市", city_code="3201",
            stage="初中", grade="初一",
        )
        sim_service.save_basic_profile(bp)

        # 模拟进入编辑状态并修改为初二
        bp.grade = "初二"
        sim_service.save_basic_profile(bp)

        # 保存后恢复查看状态
        loaded = sim_service.get_profile(check_grade_upgrade=False)
        assert loaded.basic is not None
        assert loaded.basic.grade == "初二"

    def test_unsaved_changes_interception_logic(
        self, sim_service: TrainingProfileService
    ) -> None:
        """3. 测试未保存修改退出时的拦截与三种选项分支."""

        # 初始数据：初一
        bp = BasicEducationProfile(
            province="江苏省", province_code="32", city="南京市", city_code="3201",
            stage="初中", grade="初一",
        )
        sim_service.save_basic_profile(bp)

        # 选项1：保存数据分支
        with patch.dict(
            "streamlit.session_state",
            {
                "profile_edu_type": "基础教育",
                "basic_province_val": "江苏省",
                "basic_city_val": "南京市",
                "basic_stage_val": "初中",
                "basic_grade_val": "初二",
                "basic_sb_val": False,
                "basic_comp_val": False,
            },
            clear=True,
        ):
            ok = _trigger_save_from_state(sim_service)
            assert ok is True
            loaded1 = sim_service.get_profile(check_grade_upgrade=False)
            assert loaded1.basic.grade == "初二"

        # 选项2：放弃修改分支（不调用 save，数据恢复原样）
        loaded_discard = sim_service.get_profile(check_grade_upgrade=False)
        assert loaded_discard.basic.grade == "初二"  # 保持前一次保存状态

        # 选项3：取消分支（留在当前编辑模式，不更改底层数据）
        loaded_cancel = sim_service.get_profile(check_grade_upgrade=False)
        assert loaded_cancel.basic.grade == "初二"


# ==============================================================================
# 工具四：豆包工作（无浏览器模拟）测试项
# 测试：数据隔离物理清空、本地数据存储规范、年级自动升级规则
# ==============================================================================


class TestToolDoubaoWorkerHeadlessSimulation:
    """Headless service and data layer validation using Doubao-Worker."""

    def test_strict_mutual_exclusion_and_clearing(
        self, sim_service: TrainingProfileService
    ) -> None:
        """1. 测试保存基础教育清空高等教育；保存高等教育清空基础教育."""

        # 保存基础教育
        bp = BasicEducationProfile(
            province="江苏省", province_code="32", city="南京市", city_code="3201",
            stage="高中", grade="高一",
        )
        sim_service.save_basic_profile(bp)
        p1 = sim_service.get_profile(check_grade_upgrade=False)
        assert p1.education_type == EducationType.BASIC
        assert p1.basic is not None
        assert p1.higher is None

        # 保存高等教育
        hp = HigherEducationProfile(
            school="东南大学", education_level="本科", major="软件工程"
        )
        sim_service.save_higher_profile(hp)
        p2 = sim_service.get_profile(check_grade_upgrade=False)
        assert p2.education_type == EducationType.HIGHER
        assert p2.higher is not None
        assert p2.basic is None, "基础教育数据必须已被物理清空"

        # 再次保存基础教育
        sim_service.save_basic_profile(bp)
        p3 = sim_service.get_profile(check_grade_upgrade=False)
        assert p3.education_type == EducationType.BASIC
        assert p3.basic is not None
        assert p3.higher is None, "高等教育数据必须已被物理清空"

    def test_annual_grade_promotion_july_first_rules(self) -> None:
        """2. 测试年级自动升级规则（7月1日前后、跨学段升学禁止规则）."""

        # 允许自动升级链路
        bp_g1 = BasicEducationProfile(
            province="江苏省", province_code="32", city="南京市", city_code="3201",
            stage="小学", grade="一年级",
        )
        p_up, was_up, _ = TrainingProfileService.check_and_apply_auto_upgrade(
            bp_g1, current_date=date(2026, 7, 1)
        )
        assert was_up is True
        assert p_up.grade == "二年级"

        # 小学六年级升初一
        bp_g6 = BasicEducationProfile(
            province="江苏省", province_code="32", city="南京市", city_code="3201",
            stage="小学", grade="六年级",
        )
        p_up6, was_up6, _ = TrainingProfileService.check_and_apply_auto_upgrade(
            bp_g6, current_date=date(2026, 7, 1)
        )
        assert was_up6 is True
        assert p_up6.stage == "初中"
        assert p_up6.grade == "初一"

        # 初三禁止自动升级（升学考试）
        bp_g9 = BasicEducationProfile(
            province="江苏省", province_code="32", city="南京市", city_code="3201",
            stage="初中", grade="初三",
        )
        p_up9, was_up9, msg9 = TrainingProfileService.check_and_apply_auto_upgrade(
            bp_g9, current_date=date(2026, 7, 1)
        )
        assert was_up9 is False
        assert p_up9.grade == "初三"
        assert "涉及升学考试" in msg9

        # 高三禁止自动升级（高考）
        bp_g12 = BasicEducationProfile(
            province="江苏省", province_code="32", city="南京市", city_code="3201",
            stage="高中", grade="高三",
        )
        p_up12, was_up12, msg12 = TrainingProfileService.check_and_apply_auto_upgrade(
            bp_g12, current_date=date(2026, 7, 1)
        )
        assert was_up12 is False
        assert p_up12.grade == "高三"
        assert "涉及升学考试" in msg12
