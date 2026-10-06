"""Unit tests for Phase 4 Eligibility Rule Engine.

Validates the core invariants:
1. '用户身份不会自动改变题目等级。'
2. Standard questions are strictly protected and never inflated to contest/Qiangji.
3. Qiangji plan boundary control: 39 pilot schools & published majors only;
   strict school/major isolation.
4. Olympiad boundary control: Provincial vs National tier isolation; prevents skipping tiers.
5. Higher ed boundary control: Major domains and unrelated discipline conflict prevention.
6. Fake inputs defense: Fake schools, fake contests, and fake stages are rejected.
"""

from __future__ import annotations

import pytest

from src.eligibility_models import CompetitionTier
from src.eligibility_rules import (
    EligibilityRuleEngine,
    classify_competition_stage,
    validate_strong_base_target,
)
from src.question_source_models import QuestionSourceCandidate
from src.targeted_training_models import TrainingTask
from src.training_profile_models import (
    BasicEducationProfile,
    EducationType,
    HigherEducationProfile,
    LearnerProfile,
)


@pytest.fixture
def engine() -> EligibilityRuleEngine:
    return EligibilityRuleEngine()


def make_basic_profile(
    *,
    stage: str = "高中",
    grade: str = "高二",
    in_strong_base: bool = False,
    strong_base_school: str | None = None,
    strong_base_subject: str | None = None,
    in_competition: bool = False,
    competition_subjects: dict[str, str] | None = None,
) -> BasicEducationProfile:
    """Helper to construct a valid BasicEducationProfile."""
    return BasicEducationProfile(
        province="江苏省",
        province_code="32",
        city="南京市",
        city_code="3201",
        stage=stage,
        grade=grade,
        in_strong_base=in_strong_base,
        strong_base_school=strong_base_school,
        strong_base_subject=strong_base_subject,
        in_competition=in_competition,
        competition_subjects=competition_subjects or {},
    )


def make_higher_profile(
    *,
    school: str = "清华大学",
    education_level: str = "本科",
    major: str | None = None,
    discipline_category: str | None = None,
    discipline_first_level: str | None = None,
) -> HigherEducationProfile:
    """Helper to construct a valid HigherEducationProfile."""
    return HigherEducationProfile(
        school=school,
        education_level=education_level,
        major=major,
        discipline_category=discipline_category,
        discipline_first_level=discipline_first_level,
    )


class TestCompetitionClassification:
    """Test official competition stage classification."""

    def test_provincial_stages(self) -> None:
        assert (
            classify_competition_stage("全国中学生物理竞赛复赛备战阶段（省赛/省队选拔）")
            == CompetitionTier.PROVINCIAL
        )
        assert (
            classify_competition_stage("全国高中数学联赛备战阶段（高联赛段/争夺省一）")
            == CompetitionTier.PROVINCIAL
        )
        assert (
            classify_competition_stage("CSP-J/S 认证备考阶段")
            == CompetitionTier.PROVINCIAL
        )
        assert (
            classify_competition_stage("全国中学生生物学联赛备战阶段（省赛/争夺省一）")
            == CompetitionTier.PROVINCIAL
        )

    def test_national_stages(self) -> None:
        assert (
            classify_competition_stage("全国中学生物理竞赛决赛备战阶段（CPhO 国决）")
            == CompetitionTier.NATIONAL
        )
        assert (
            classify_competition_stage("中国数学奥林匹克全国决赛备战阶段（CMO 冬令营）")
            == CompetitionTier.NATIONAL
        )
        assert (
            classify_competition_stage("全国青少年信息学奥林匹克竞赛备考阶段（NOI 国决）")
            == CompetitionTier.NATIONAL
        )
        assert (
            classify_competition_stage("全国高中学生化学竞赛决赛暨冬令营备战阶段（CChO 国决）")
            == CompetitionTier.NATIONAL
        )

    def test_international_stages(self) -> None:
        assert (
            classify_competition_stage("国家集训队及国际数学奥林匹克阶段（IMO 选拔）")
            == CompetitionTier.INTERNATIONAL
        )
        assert (
            classify_competition_stage("国家集训队及国际物理奥林匹克阶段（IPhO 选拔）")
            == CompetitionTier.INTERNATIONAL
        )

    def test_fake_or_unknown_stages(self) -> None:
        assert classify_competition_stage("不存在比赛阶段") is None
        assert classify_competition_stage("宇宙超级奥赛") is None
        assert classify_competition_stage("") is None


class TestStrongBaseValidation:
    """Test Qiangji pilot school and published majors validation."""

    def test_valid_school_and_major(self) -> None:
        is_valid, msg = validate_strong_base_target("南京大学", "物理学")
        assert is_valid is True
        assert msg is None

        is_valid, msg = validate_strong_base_target("清华大学", "数学与应用数学")
        assert is_valid is True
        assert msg is None

        is_valid, msg = validate_strong_base_target("北京大学", "哲学")
        assert is_valid is True
        assert msg is None

    def test_fake_or_unauthorized_school(self) -> None:
        # 虚假高校（例如中国邮电大学）
        is_valid, msg = validate_strong_base_target("中国邮电大学", "物理学")
        assert is_valid is False
        assert msg is not None
        assert "39所强基计划试点高校" in msg

        # 真实非强基高校（如苏州大学）
        is_valid, msg = validate_strong_base_target("苏州大学", "物理学")
        assert is_valid is False
        assert msg is not None
        assert "39所强基计划试点高校" in msg

    def test_unauthorized_major_in_school(self) -> None:
        # 南京大学未公布该强基专业（如临床医学）
        is_valid, msg = validate_strong_base_target("南京大学", "临床医学")
        assert is_valid is False
        assert msg is not None
        assert "不属于「南京大学」官方公布的强基计划招生范围" in msg


class TestAllowedScopeComputation:
    """Test comprehensive AllowedSourceScope computation."""

    def test_regular_high_school_student(self, engine: EligibilityRuleEngine) -> None:
        profile = LearnerProfile(
            education_type=EducationType.BASIC,
            basic=make_basic_profile(
                grade="高二",
                in_strong_base=False,
                in_competition=False,
            ),
        )
        scope = engine.compute_allowed_scope(profile)
        assert scope.allow_regular is True
        assert scope.is_strong_base_eligible is False
        assert scope.is_competition_eligible is False
        assert "基础教育 · 普通题源" in scope.scope_description

    def test_qiangji_student_valid(self, engine: EligibilityRuleEngine) -> None:
        profile = LearnerProfile(
            education_type=EducationType.BASIC,
            basic=make_basic_profile(
                grade="高三",
                in_strong_base=True,
                strong_base_school="南京大学",
                strong_base_subject="物理学",
            ),
        )
        scope = engine.compute_allowed_scope(profile)
        assert scope.is_strong_base_eligible is True
        assert scope.strong_base_school == "南京大学"
        assert scope.strong_base_subject == "物理学"

    def test_qiangji_student_fake_school(self, engine: EligibilityRuleEngine) -> None:
        profile = LearnerProfile(
            education_type=EducationType.BASIC,
            basic=make_basic_profile(
                grade="高三",
                in_strong_base=True,
                strong_base_school="中国邮电大学",
                strong_base_subject="物理学",
            ),
        )
        scope = engine.compute_allowed_scope(profile)
        # 虚假高校被拒绝，强基题源资格不予开放，仅保留普通题源
        assert scope.is_strong_base_eligible is False
        assert scope.allow_regular is True

    def test_competition_student_provincial(self, engine: EligibilityRuleEngine) -> None:
        profile = LearnerProfile(
            education_type=EducationType.BASIC,
            basic=make_basic_profile(
                grade="高二",
                in_competition=True,
                competition_subjects={"物理": "全国中学生物理竞赛复赛备战阶段（省赛/省队选拔）"},
            ),
        )
        scope = engine.compute_allowed_scope(profile)
        assert scope.is_competition_eligible is True
        assert scope.competition_subject == "物理"
        assert scope.competition_tier == CompetitionTier.PROVINCIAL

    def test_higher_education_student_robotics(self, engine: EligibilityRuleEngine) -> None:
        profile = LearnerProfile(
            education_type=EducationType.HIGHER,
            higher=make_higher_profile(
                school="清华大学",
                education_level="本科",
                major="机器人工程",
            ),
        )
        scope = engine.compute_allowed_scope(profile)
        assert scope.education_stage == "高等教育"
        assert scope.higher_school == "清华大学"
        assert "自动控制原理" in scope.allowed_major_domains
        assert "机器人学" in scope.allowed_major_domains

    def test_higher_education_fake_school(self, engine: EligibilityRuleEngine) -> None:
        profile = LearnerProfile(
            education_type=EducationType.HIGHER,
            higher=make_higher_profile(
                school="中国邮电大学",
                education_level="本科",
                major="计算机科学与技术",
            ),
        )
        scope = engine.compute_allowed_scope(profile)
        assert scope.allow_regular is False
        assert "虚构高校" in scope.scope_description


class TestCandidateEligibilityEvaluation:
    """Test candidate boundary control and regular question protection."""

    def test_regular_question_protection_for_olympiad_student(
        self, engine: EligibilityRuleEngine
    ) -> None:
        """核心不变量：普通试卷绝不因用户是竞赛国决生而升级为竞赛题。"""
        profile = LearnerProfile(
            education_type=EducationType.BASIC,
            basic=make_basic_profile(
                grade="高三",
                in_competition=True,
                competition_subjects={"物理": "全国中学生物理竞赛决赛备战阶段（CPhO 国决）"},
            ),
        )
        scope = engine.compute_allowed_scope(profile)

        # 真实普通高考试题（如 8512 高中物理回归测试或全国卷高考）
        candidate = QuestionSourceCandidate(
            question_text="一物块在水平面上受恒力作用运动，求动量变化...",
            source_name="2024年普通高等学校招生全国统一考试物理试题（新课标卷）",
            source_url="https://gaokao.neea.edu.cn/archive/2024/physics_q24.pdf",
            year=2024,
            exam_or_contest_name="2024年高考物理新课标卷",
            question_number="24",
            subject="物理",
            applicable_scope="基础教育 · 普通",
            is_regular_exam=True,
        )

        decision = engine.evaluate_candidate_eligibility(candidate, scope)
        assert decision.is_eligible is True
        # 强制界定为基础教育普通题，绝不升级为学科竞赛
        assert decision.normalized_scope == "基础教育 · 普通"

    def test_qiangji_boundary_isolation(self, engine: EligibilityRuleEngine) -> None:
        # 南京大学物理学强基生
        nju_profile = LearnerProfile(
            education_type=EducationType.BASIC,
            basic=make_basic_profile(
                grade="高三",
                in_strong_base=True,
                strong_base_school="南京大学",
                strong_base_subject="物理学",
            ),
        )
        nju_scope = engine.compute_allowed_scope(nju_profile)

        # 1. 匹配的南京大学物理强基题 -> 通过
        nju_cand = QuestionSourceCandidate(
            question_text="简谐振动微扰阻尼衰减规律分析...",
            source_name="2023年南京大学强基计划物理校考试题",
            source_url="https://bkzs.nju.edu.cn/archive/2023/qiangji_q1.pdf",
            year=2023,
            exam_or_contest_name="2023年南京大学强基计划校考",
            question_number="1",
            subject="物理",
            applicable_scope="基础教育 · 强基计划",
            school_name="南京大学",
            major_direction="物理学",
        )
        assert engine.evaluate_candidate_eligibility(nju_cand, nju_scope).is_eligible is True

        # 2. 跨校清华大学强基题 -> 拒绝
        thu_cand = QuestionSourceCandidate(
            question_text="微分中值定理分析零点分布...",
            source_name="2023年清华大学强基计划校测试卷",
            source_url="https://join-tsinghua.edu.cn/archive/2023/qiangji_q2.pdf",
            year=2023,
            exam_or_contest_name="2023年清华大学强基计划校测",
            question_number="2",
            subject="物理",
            applicable_scope="基础教育 · 强基计划",
            school_name="清华大学",
            major_direction="物理学",
        )
        res = engine.evaluate_candidate_eligibility(thu_cand, nju_scope)
        assert res.is_eligible is False
        assert "强基高校不匹配" in res.reason

        # 3. 普通学生请求强基题 -> 拒绝越界升级
        reg_profile = LearnerProfile(
            education_type=EducationType.BASIC,
            basic=make_basic_profile(in_strong_base=False),
        )
        reg_scope = engine.compute_allowed_scope(reg_profile)
        res_reg = engine.evaluate_candidate_eligibility(nju_cand, reg_scope)
        assert res_reg.is_eligible is False
        assert "普通学生题源范围不包含强基计划校测试卷" in res_reg.reason

    def test_competition_tier_boundary(self, engine: EligibilityRuleEngine) -> None:
        # 省级备赛学生
        prov_profile = LearnerProfile(
            education_type=EducationType.BASIC,
            basic=make_basic_profile(
                in_competition=True,
                competition_subjects={"物理": "全国中学生物理竞赛复赛备战阶段（省赛/省队选拔）"},
            ),
        )
        prov_scope = engine.compute_allowed_scope(prov_profile)

        # 省级竞赛题 -> 通过
        prov_cand = QuestionSourceCandidate(
            question_text="小车与单摆系统的角动量定理应用...",
            source_name="第40届全国中学生物理竞赛复赛理论试题",
            source_url="http://www.cps-net.org.cn/cpho/semifinal_q1.pdf",
            year=2023,
            exam_or_contest_name="第40届全国中学生物理竞赛复赛",
            question_number="1",
            subject="物理",
            applicable_scope="基础教育 · 学科竞赛",
            contest_name="全国中学生物理竞赛",
            contest_tier="省级",
        )
        assert engine.evaluate_candidate_eligibility(prov_cand, prov_scope).is_eligible is True

        # 国家级决赛题 -> 越级拦截拒绝
        nat_cand = QuestionSourceCandidate(
            question_text="强非均匀磁场中相对论性带电粒子回旋运动...",
            source_name="第40届全国中学生物理竞赛决赛理论试题",
            source_url="http://www.cps-net.org.cn/cpho/final_q2.pdf",
            year=2023,
            exam_or_contest_name="第40届全国中学生物理竞赛决赛",
            question_number="2",
            subject="物理",
            applicable_scope="基础教育 · 学科竞赛",
            contest_name="全国中学生物理竞赛",
            contest_tier="国家级",
        )
        res = engine.evaluate_candidate_eligibility(nat_cand, prov_scope)
        assert res.is_eligible is False
        assert "严禁直接越级检索「国家级/国际级」试题" in res.reason

        # 国家级决赛学生 -> 允许检索决赛试题
        nat_profile = LearnerProfile(
            education_type=EducationType.BASIC,
            basic=make_basic_profile(
                in_competition=True,
                competition_subjects={"物理": "全国中学生物理竞赛决赛备战阶段（CPhO 国决）"},
            ),
        )
        nat_scope = engine.compute_allowed_scope(nat_profile)
        assert engine.evaluate_candidate_eligibility(nat_cand, nat_scope).is_eligible is True

    def test_higher_ed_major_conflict(self, engine: EligibilityRuleEngine) -> None:
        robotics_profile = LearnerProfile(
            education_type=EducationType.HIGHER,
            higher=make_higher_profile(
                school="清华大学",
                education_level="本科",
                major="机器人工程",
            ),
        )
        scope = engine.compute_allowed_scope(robotics_profile)

        # 1. 自动控制原理试题 -> 通过
        ctrl_cand = QuestionSourceCandidate(
            question_text="考虑机械臂状态方程，利用极点配置设计状态观测器...",
            source_name="清华大学《自动控制原理》期末考试试卷",
            source_url="https://learn.tsinghua.edu.cn/archive/courses/auto_control_q3.pdf",
            year=2023,
            exam_or_contest_name="清华大学《自动控制原理》课程期末考试",
            question_number="3",
            subject="工学",
            applicable_scope="高等教育 · 本科",
            school_name="清华大学",
            major_direction="机器人工程",
        )
        assert engine.evaluate_candidate_eligibility(ctrl_cand, scope).is_eligible is True

        # 2. 临床医学试题 -> 显式无关学科冲突拦截
        med_cand = QuestionSourceCandidate(
            question_text="简述慢性心力衰竭的神经体液激活机制及其对左心室重构的影响...",
            source_name="北京大学医学部《内科学》期末考试试卷",
            source_url="https://med.pku.edu.cn/archive/courses/internal_medicine_q12.pdf",
            year=2023,
            exam_or_contest_name="北京大学医学部《内科学》期末考试",
            question_number="12",
            subject="医学",
            applicable_scope="高等教育 · 本科",
            school_name="北京大学",
            major_direction="临床医学",
        )
        res = engine.evaluate_candidate_eligibility(med_cand, scope)
        assert res.is_eligible is False
        assert "无关学科" in res.reason or "核心专业领域不相关" in res.reason

    def test_cross_discipline_isolation(self, engine: EligibilityRuleEngine) -> None:
        """任务学科为地理，题源为物理，必须严格隔离拒绝。"""
        profile = LearnerProfile(
            education_type=EducationType.BASIC,
            basic=make_basic_profile(),
        )
        scope = engine.compute_allowed_scope(profile)

        geo_task = TrainingTask(
            training_type="题型族",
            target="某区域分析类",
            subject="地理",
        )

        physics_cand = QuestionSourceCandidate(
            question_text="分析滑块冲上斜面过程中的动量变化量...",
            source_name="2024年高考物理新课标卷",
            source_url="https://gaokao.neea.edu.cn/archive/physics_q24.pdf",
            year=2024,
            exam_or_contest_name="2024年高考物理新课标卷",
            question_number="24",
            subject="物理",
            applicable_scope="基础教育 · 普通",
        )

        res = engine.evaluate_candidate_eligibility(physics_cand, scope, task=geo_task)
        assert res.is_eligible is False
        assert "学科不匹配" in res.reason
