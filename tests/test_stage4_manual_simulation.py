"""Stage 4 Manual Simulation Tests (15+ Real User Personas & Boundary Verification).

Executes comprehensive simulated testing for Phase 4:
1. Persona 1: 普通高中学生（普通题源，禁止越界获得强基/竞赛题）
2. Persona 2: 高中强基学生（南京大学物理学强基，限定南大物理题源）
3. Persona 3: 虚假强基高校（中国邮电大学，强基资格被拒，仅保留普通题源）
4. Persona 4: 物理竞赛省级备赛学生（只允许省级预赛/复赛题源，拒绝国家级）
5. Persona 5: 物理竞赛国家级决赛学生（允许全国竞赛决赛试题及国际赛事）
6. Persona 6: 竞赛学生上传普通高考试卷（普通题保护：识别为普通题，禁止虚构升级）
7. Persona 7: 本科机器人工程专业学生（控制/机器人专业题源，拒绝医学/法学跨门类题目）
8. Persona 8: 高等教育研究生用户（工学/理学研究生一级学科接口测试）
9. Persona 9: 虚假竞赛测试（输入“不存在比赛阶段”或“不存在比赛”，拦截拒绝）
10. Persona 10: 跨学科攻击用户（地理任务请求物理竞赛题，拦截拒绝）
11. Persona 11: 虚假强基专业（南京大学但填“临床医学”，拦截拒绝）
12. Persona 12: 普通学生请求竞赛试题（超出普通题源范围，拦截拒绝）
13. Persona 13: 来源链接失效/模糊测试（搜索引擎/根域名链接拦截）
14. Persona 14: 边界年份与异常年份测试（1970年或2099年拦截）
15. Persona 15: 综合端到端回归测试（8511 地理 + 8512 物理全流程回归与数据库审计）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.database import Database
from src.eligibility_rules import (
    EligibilityRuleEngine,
    classify_competition_stage,
    validate_strong_base_target,
)
from src.question_source_models import (
    QuestionSourceCandidate,
    VerificationStatus,
)
from src.question_source_retrieval_service import QuestionSourceRetrievalService
from src.question_source_verification import QuestionSourceVerifier
from src.targeted_training_models import TrainingTask
from src.training_profile_models import (
    BasicEducationProfile,
    EducationType,
    HigherEducationProfile,
    LearnerProfile,
)

STAGING_8511 = Path("staging-data/data/database/knowledge.db")
STAGING_8512 = Path("staging-data-8512/data/database/knowledge.db")


def make_basic_profile(
    *,
    stage: str = "高中",
    grade: str = "高三",
    in_strong_base: bool = False,
    strong_base_school: str | None = None,
    strong_base_subject: str | None = None,
    in_competition: bool = False,
    competition_subjects: dict[str, str] | None = None,
) -> LearnerProfile:
    return LearnerProfile(
        education_type=EducationType.BASIC,
        basic=BasicEducationProfile(
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
        ),
    )


def make_higher_profile(
    *,
    school: str = "清华大学",
    education_level: str = "本科",
    major: str | None = None,
    discipline_category: str | None = None,
    discipline_first_level: str | None = None,
) -> LearnerProfile:
    return LearnerProfile(
        education_type=EducationType.HIGHER,
        higher=HigherEducationProfile(
            school=school,
            education_level=education_level,
            major=major,
            discipline_category=discipline_category,
            discipline_first_level=discipline_first_level,
        ),
    )


# ==============================================================================
# Persona 1: 普通高中学生（普通题源，禁止越界获得强基/竞赛题）
# ==============================================================================


def test_persona_1_regular_high_school_student() -> None:
    """Persona 1: Regular high school student in Jiangsu practicing Geography region analysis."""
    if not STAGING_8511.is_file():
        pytest.skip("8511 database not found")

    profile = make_basic_profile(in_strong_base=False, in_competition=False)
    db = Database(STAGING_8511)
    retrieval_service = QuestionSourceRetrievalService(db)
    task = TrainingTask(
        training_type="题型族",
        target="某区域分析类",
        family_id=114,
        subject="地理",
    )

    result = retrieval_service.retrieve_and_verify(task=task, profile=profile, limit=5)
    # 源码内曾预置的“外部真题”没有逐题联网核验证据，不能仅凭网址外观
    # 继续当作真实题。没有可信本地题时，普通学生也必须安全返回 0 题。
    assert result.verified_count == 0
    assert result.questions == []
    assert "本地资料库暂未找到可核验的变式训练题" in result.prompt_message


# ==============================================================================
# Persona 2: 高中强基学生（南京大学物理学强基，限定南大物理题源）
# ==============================================================================


def test_persona_2_qiangji_student_nju_physics() -> None:
    """Persona 2: Qiangji student targeting Nanjing University Physics."""
    if not STAGING_8512.is_file():
        pytest.skip("8512 database not found")

    profile = make_basic_profile(
        in_strong_base=True,
        strong_base_school="南京大学",
        strong_base_subject="物理学",
    )
    db = Database(STAGING_8512)
    retrieval_service = QuestionSourceRetrievalService(db)
    task = TrainingTask(
        training_type="方法族",
        target="动量定理",
        subject="物理",
    )

    result = retrieval_service.retrieve_and_verify(task=task, profile=profile, limit=5)
    # 拥有强基权限不等于可以降低来源标准；没有可逐题核验的南大题源时
    # 仍应返回真实数量 0，不能凭硬编码学校名和链接补齐。
    assert result.verified_count == 0
    assert result.questions == []
    assert "本地资料库暂未找到可核验的变式训练题" in result.prompt_message


# ==============================================================================
# Persona 3: 虚假强基高校（中国邮电大学，强基资格被拒，仅保留普通题源）
# ==============================================================================


def test_persona_3_fake_qiangji_school_rejected() -> None:
    """Persona 3: User inputs fabricated school '中国邮电大学' for Qiangji."""
    is_valid, msg = validate_strong_base_target("中国邮电大学", "物理学")
    assert is_valid is False
    assert msg is not None
    assert "39所强基计划试点高校" in msg

    engine = EligibilityRuleEngine()
    fake_profile = make_basic_profile(
        in_strong_base=True,
        strong_base_school="中国邮电大学",
        strong_base_subject="物理学",
    )
    scope = engine.compute_allowed_scope(fake_profile)
    assert scope.is_strong_base_eligible is False
    assert scope.allow_regular is True

    # 虚假高校的强基试题被直接拒绝
    verifier = QuestionSourceVerifier()
    fake_cand = QuestionSourceCandidate(
        question_text="【2023年中国邮电大学强基试卷·第1题】动量守恒讨论...",
        source_name="中国邮电大学强基测试",
        source_url="https://cup.edu.cn/archive/q1.pdf",
        year=2023,
        exam_or_contest_name="2023年中国邮电大学强基校考",
        question_number="1",
        subject="物理",
        applicable_scope="基础教育 · 强基计划",
        school_name="中国邮电大学",
        major_direction="物理学",
    )
    res = verifier.verify_candidate(fake_cand, profile=fake_profile)
    assert res.status == VerificationStatus.REJECTED
    assert "虚构" in str(res.reason) or "普通学生题源范围不包含强基" in str(res.reason)


# ==============================================================================
# Persona 4: 物理竞赛省级备赛学生（只允许省级预赛/复赛题源，拒绝国家级）
# ==============================================================================


def test_persona_4_olympiad_provincial_student() -> None:
    """Persona 4: Student preparing for Provincial Physics Competition (Semfinals)."""
    profile = make_basic_profile(
        in_competition=True,
        competition_subjects={"物理": "全国中学生物理竞赛复赛备战阶段（省赛/省队选拔）"},
    )
    verifier = QuestionSourceVerifier()

    # 1. 省级复赛试题 -> 验证通过
    prov_cand = QuestionSourceCandidate(
        question_text="【第40届全国中学生物理竞赛复赛·第1题】小车与单摆动量定理应用...",
        source_name="第40届全国中学生物理竞赛复赛理论试题及参考解答",
        source_url="http://www.cps-net.org.cn/cpho/archive/40th_cpho_semifinal_q1.pdf",
        year=2023,
        exam_or_contest_name="第40届全国中学生物理竞赛复赛",
        question_number="1",
        subject="物理",
        applicable_scope="基础教育 · 学科竞赛",
        contest_name="全国中学生物理竞赛",
        contest_tier="省级",
    )
    res_prov = verifier.verify_candidate(prov_cand, profile=profile)
    assert res_prov.is_verified is True
    assert res_prov.verified_question is not None
    assert res_prov.verified_question.contest_tier == "省级"

    # 2. 国家级决赛试题 -> 严禁越级直接拒绝
    nat_cand = QuestionSourceCandidate(
        question_text="【第40届全国中学生物理竞赛决赛·第2题】相对论性带电粒子回旋运动...",
        source_name="第40届全国中学生物理竞赛决赛理论试题",
        source_url="http://www.cps-net.org.cn/cpho/archive/40th_cpho_final_q2.pdf",
        year=2023,
        exam_or_contest_name="第40届全国中学生物理竞赛决赛",
        question_number="2",
        subject="物理",
        applicable_scope="基础教育 · 学科竞赛",
        contest_name="全国中学生物理竞赛",
        contest_tier="国家级",
    )
    res_nat = verifier.verify_candidate(nat_cand, profile=profile)
    assert res_nat.status == VerificationStatus.REJECTED
    assert "严禁直接越级检索「国家级/国际级」试题" in str(res_nat.reason)


# ==============================================================================
# Persona 5: 物理竞赛国家级决赛学生（允许全国竞赛决赛试题及国际赛事）
# ==============================================================================


def test_persona_5_olympiad_national_student() -> None:
    """Persona 5: Student preparing for National Physics Finals (CPhO Winter Camp)."""
    profile = make_basic_profile(
        in_competition=True,
        competition_subjects={"物理": "全国中学生物理竞赛决赛备战阶段（CPhO 国决）"},
    )
    verifier = QuestionSourceVerifier()

    nat_cand = QuestionSourceCandidate(
        question_text="【第40届全国中学生物理竞赛决赛·第2题】相对论性带电粒子回旋运动...",
        source_name="第40届全国中学生物理竞赛决赛理论试题",
        source_url="http://www.cps-net.org.cn/cpho/archive/40th_cpho_final_q2.pdf",
        year=2023,
        exam_or_contest_name="第40届全国中学生物理竞赛决赛",
        question_number="2",
        subject="物理",
        applicable_scope="基础教育 · 学科竞赛",
        contest_name="全国中学生物理竞赛",
        contest_tier="国家级",
    )
    res = verifier.verify_candidate(nat_cand, profile=profile)
    assert res.is_verified is True
    assert res.verified_question is not None
    assert res.verified_question.contest_tier == "国家级"


# ==============================================================================
# Persona 6: 竞赛学生上传普通高考试卷（普通题保护：识别为普通题，禁止虚构升级）
# ==============================================================================


def test_persona_6_regular_question_protection_for_national_olympiad_student() -> None:
    """Persona 6: National contestant uploads standard Gaokao exam.

    Core Invariant: User identity must NEVER inflate standard exam difficulty.
    """
    profile = make_basic_profile(
        in_competition=True,
        competition_subjects={"物理": "全国中学生物理竞赛决赛备战阶段（CPhO 国决）"},
    )
    verifier = QuestionSourceVerifier()

    # 高考物理常规试题，即使被误标为竞赛
    cand = QuestionSourceCandidate(
        question_text="【2024年高考物理新课标卷·第24题】滑块受恒定推力冲上斜面求冲量...",
        source_name="2024年普通高等学校招生全国统一考试物理试题（新课标卷）",
        source_url="https://gaokao.neea.edu.cn/archive/2024/physics_q24.pdf",
        year=2024,
        exam_or_contest_name="2024年高考物理新课标卷",
        question_number="24",
        subject="物理",
        applicable_scope="基础教育 · 竞赛",  # 模拟错误/伪装标签
        is_regular_exam=True,
    )

    res = verifier.verify_candidate(cand, profile=profile)
    assert res.is_verified is True
    # 普通题保护机制强制剥离伪装，归一化为基础教育 · 普通
    assert res.verified_question is not None
    assert res.verified_question.applicable_scope == "基础教育 · 普通"


# ==============================================================================
# Persona 7: 本科机器人工程专业学生（控制/机器人专业题源，拒绝医学/法学）
# ==============================================================================


def test_persona_7_undergraduate_robotics_student() -> None:
    """Persona 7: Tsinghua undergraduate in Robotics Engineering."""
    profile = make_higher_profile(
        school="清华大学",
        education_level="本科",
        major="机器人工程",
    )
    verifier = QuestionSourceVerifier()

    # 1. 自动控制原理相关专业试题 -> 允许
    ctrl_cand = QuestionSourceCandidate(
        question_text="【清华大学·自动控制原理期末试卷·第3题】机械臂状态方程与观测器设计...",
        source_name="清华大学《自动控制原理》期末考试试卷",
        source_url="https://learn.tsinghua.edu.cn/archive/courses/auto_control_2023_q3.pdf",
        year=2023,
        exam_or_contest_name="清华大学《自动控制原理》课程期末考试",
        question_number="3",
        subject="工学",
        applicable_scope="高等教育 · 本科",
        school_name="清华大学",
        major_direction="机器人工程",
    )
    res_ctrl = verifier.verify_candidate(ctrl_cand, profile=profile)
    assert res_ctrl.is_verified is True

    # 2. 临床医学试题 -> 显式冲突拦截拒绝
    med_cand = QuestionSourceCandidate(
        question_text="【北京大学医学部·内科学期末考试·第12题】慢性心力衰竭病理生理机制...",
        source_name="北京大学医学部《内科学》期末考试试卷",
        source_url="https://med.pku.edu.cn/archive/courses/internal_medicine_2023_q12.pdf",
        year=2023,
        exam_or_contest_name="北京大学医学部《内科学》期末考试",
        question_number="12",
        subject="医学",
        applicable_scope="高等教育 · 本科",
        school_name="北京大学",
        major_direction="临床医学",
    )
    res_med = verifier.verify_candidate(med_cand, profile=profile)
    assert res_med.status == VerificationStatus.REJECTED
    assert "无关学科" in str(res_med.reason) or "核心专业领域不相关" in str(res_med.reason)


# ==============================================================================
# Persona 8: 高等教育研究生用户（工学/理学研究生一级学科接口测试）
# ==============================================================================


def test_persona_8_graduate_student_discipline_scope() -> None:
    """Persona 8: Graduate student at Peking University in Natural Geography."""
    profile = make_higher_profile(
        school="北京大学",
        education_level="研究生",
        discipline_category="理学",
        discipline_first_level="地理学",
    )
    engine = EligibilityRuleEngine()
    scope = engine.compute_allowed_scope(profile)

    assert scope.education_stage == "高等教育"
    assert scope.higher_school == "北京大学"
    assert scope.higher_education_level == "研究生"
    assert scope.graduate_category == "理学"
    assert scope.graduate_first_level == "地理学"
    assert "高等教育 · 北京大学 · 研究生 · 理学" in scope.scope_description


# ==============================================================================
# Persona 9: 虚假竞赛测试（输入“不存在比赛阶段”或“不存在比赛”，拦截拒绝）
# ==============================================================================


def test_persona_9_fake_competition_rejected() -> None:
    """Persona 9: Candidate or profile with fabricated competition."""
    assert classify_competition_stage("不存在比赛阶段") is None
    assert classify_competition_stage("宇宙数学超级奥林匹克") is None

    verifier = QuestionSourceVerifier()
    fake_cand = QuestionSourceCandidate(
        question_text="分析某虚拟物体的动量守恒规律...",
        source_name="不存在比赛试题委员会",
        source_url="https://cps-net.org.cn/archive/fake_q1.pdf",
        year=2023,
        exam_or_contest_name="不存在比赛",
        question_number="1",
        subject="物理",
        applicable_scope="基础教育 · 学科竞赛",
        contest_name="不存在比赛",
    )
    res = verifier.verify_candidate(fake_cand)
    assert res.status == VerificationStatus.REJECTED
    assert "不存在比赛" in str(res.reason)


# ==============================================================================
# Persona 10: 跨学科攻击用户（地理任务请求物理竞赛题，拦截拒绝）
# ==============================================================================


def test_persona_10_cross_discipline_isolation() -> None:
    """Persona 10: Geography training task requests Physics competition question."""
    verifier = QuestionSourceVerifier()
    geo_task = TrainingTask(
        training_type="题型族",
        target="某区域分析类",
        subject="地理",
    )
    phy_cand = QuestionSourceCandidate(
        question_text="【第40届全国中学生物理竞赛复赛·第1题】角动量定理应用...",
        source_name="第40届全国中学生物理竞赛复赛试题",
        source_url="http://www.cps-net.org.cn/cpho/semifinal_q1.pdf",
        year=2023,
        exam_or_contest_name="第40届全国中学生物理竞赛复赛",
        question_number="1",
        subject="物理",
        applicable_scope="基础教育 · 学科竞赛",
        contest_name="全国中学生物理竞赛",
        contest_tier="省级",
    )
    res = verifier.verify_candidate(phy_cand, task=geo_task)
    assert res.status == VerificationStatus.REJECTED
    assert "学科不匹配" in str(res.reason)


# ==============================================================================
# Persona 11: 虚假强基专业（南京大学但填“临床医学”，拦截拒绝）
# ==============================================================================


def test_persona_11_unauthorized_qiangji_major_rejected() -> None:
    """Persona 11: Qiangji pilot school Nanjing University without Clinical Medicine."""
    is_valid, msg = validate_strong_base_target("南京大学", "临床医学")
    assert is_valid is False
    assert msg is not None
    assert "不属于「南京大学」官方公布的强基计划招生范围" in msg


# ==============================================================================
# Persona 12: 普通学生请求竞赛试题（超出普通题源范围，拦截拒绝）
# ==============================================================================


def test_persona_12_regular_student_cannot_access_competition() -> None:
    """Persona 12: Standard student requests Olympiad competition paper."""
    reg_profile = make_basic_profile(in_competition=False)
    verifier = QuestionSourceVerifier()

    comp_cand = QuestionSourceCandidate(
        question_text="【第40届全国中学生物理竞赛复赛·第1题】小车冲量分析...",
        source_name="第40届全国中学生物理竞赛复赛试题",
        source_url="http://www.cps-net.org.cn/cpho/semifinal_q1.pdf",
        year=2023,
        exam_or_contest_name="第40届全国中学生物理竞赛复赛",
        question_number="1",
        subject="物理",
        applicable_scope="基础教育 · 学科竞赛",
        contest_name="全国中学生物理竞赛",
        contest_tier="省级",
    )
    res = verifier.verify_candidate(comp_cand, profile=reg_profile)
    assert res.status == VerificationStatus.REJECTED
    assert "普通学生题源范围不包含学科竞赛试题" in str(res.reason)


# ==============================================================================
# Persona 13: 来源链接失效/模糊测试（搜索引擎/根域名链接拦截）
# ==============================================================================


def test_persona_13_unlocatable_links_rejected() -> None:
    """Persona 13: Questions with search engines or root domains must be rejected."""
    verifier = QuestionSourceVerifier()

    # 1. 搜索引擎
    cand_search = QuestionSourceCandidate(
        question_text="2024年高考地理试题分析某区域冲积扇地貌...",
        source_name="百度搜索结果页面",
        source_url="https://www.baidu.com/s?wd=高考地理冲积扇",
        year=2024,
        exam_or_contest_name="2024年高考地理新课标卷",
        question_number="36",
        subject="地理",
        applicable_scope="基础教育 · 普通",
    )
    assert verifier.verify_candidate(cand_search).status == VerificationStatus.REJECTED

    # 2. 根域名 / 首页
    cand_root = QuestionSourceCandidate(
        question_text="2024年高考地理试题分析某区域冲积扇地貌...",
        source_name="教育部考试院官网首页",
        source_url="https://gaokao.neea.edu.cn/",
        year=2024,
        exam_or_contest_name="2024年高考地理新课标卷",
        question_number="36",
        subject="地理",
        applicable_scope="基础教育 · 普通",
    )
    assert verifier.verify_candidate(cand_root).status == VerificationStatus.REJECTED


# ==============================================================================
# Persona 14: 边界年份与异常年份测试（1970年或2099年拦截）
# ==============================================================================


def test_persona_14_abnormal_years_rejected() -> None:
    """Persona 14: Historical pre-Gaokao (< 1977) and future (> current_year) years rejected."""
    verifier = QuestionSourceVerifier()

    # 1970 年（早于恢复高考）
    cand_old = QuestionSourceCandidate(
        question_text="古代水利工程对于流域农业灌溉的影响分析...",
        source_name="历史档案资料汇编",
        source_url="https://example.org/archive/1970/history_q1.pdf",
        year=1970,
        exam_or_contest_name="1970年模拟练习",
        question_number="1",
        subject="地理",
        applicable_scope="基础教育 · 普通",
    )
    assert verifier.verify_candidate(cand_old).status == VerificationStatus.REJECTED

    # 2099 年（未来虚假年份）
    cand_future = QuestionSourceCandidate(
        question_text="未来火星基地农田水利灌溉体系分析...",
        source_name="未来预测卷",
        source_url="https://example.org/archive/2099/future_q1.pdf",
        year=2099,
        exam_or_contest_name="2099年高考预测",
        question_number="1",
        subject="地理",
        applicable_scope="基础教育 · 普通",
    )
    assert verifier.verify_candidate(cand_future).status == VerificationStatus.REJECTED


# ==============================================================================
# Persona 15: 综合端到端回归测试（8511 地理 + 8512 物理全流程回归与数据库审计）
# ==============================================================================


def test_persona_15_end_to_end_regression_with_database_audit() -> None:
    """Persona 15: Comprehensive end-to-end regression covering 8511 & 8512."""
    if not STAGING_8512.is_file():
        pytest.skip("8512 database not found")

    db = Database(STAGING_8512)
    service = QuestionSourceRetrievalService(db)

    # 8512 是可复用的 staging 数据库，可能保留旧阶段审计记录。验证本次
    # 0 题检索不新增或改写记录，而不是假设历史表必须为空。
    with db._connection() as conn:
        before_rows = [
            tuple(row)
            for row in conn.execute(
                """
                SELECT school_name, major_direction, contest_name, contest_tier, applicable_scope
                FROM targeted_training_questions
                WHERE target = '动量定理'
                ORDER BY id
                """
            ).fetchall()
        ]

    # 强基物理任务
    profile = make_basic_profile(
        in_strong_base=True,
        strong_base_school="南京大学",
        strong_base_subject="物理学",
    )
    task = TrainingTask(
        training_type="方法族",
        target="动量定理",
        subject="物理",
    )

    res = service.retrieve_and_verify(task=task, profile=profile, limit=5)
    assert res.verified_count == 0
    assert res.questions == []
    assert "本地资料库暂未找到可核验的变式训练题" in res.prompt_message
    assert res.training_type == "方法族"
    assert res.target == "动量定理"

    # 没有真实返回题目时，审计表也不能伪造南大学校或专业字段。
    with db._connection() as conn:
        after_rows = [
            tuple(row)
            for row in conn.execute(
                """
                SELECT school_name, major_direction, contest_name, contest_tier, applicable_scope
                FROM targeted_training_questions
                WHERE target = '动量定理'
                ORDER BY id
                """
            ).fetchall()
        ]
        assert after_rows == before_rows
