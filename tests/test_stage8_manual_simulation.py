"""Stage 8 Manual Simulation Tests (15 Personas: 1 to 15).

Validates Phase 8: Layer 3 Mastery Training and local-only retrieval:
1.  Persona 1: 高中普通学生（无强基/竞赛入口，仅限普通真实题源）
2.  Persona 2: 高中强基学生（个人信息开启强基，未勾选不查强基，勾选后查询对应高校专业题源）
3.  Persona 3: 高中竞赛学生（个人信息开启竞赛，未勾选不查竞赛，勾选后按备赛层级合规查询）
4.  Persona 4: 初中学生（完全无强基/竞赛入口，高级勾选框彻底隐藏且后端不生效）
5.  Persona 5: 小学学生（完全无高级题源，仅限基础教育普通规范试题）
6.  Persona 6: 本地查询找到 5 题（全部可训练，不联网）
7.  Persona 7: 本地查询找到不足 5 题（照实返回，不补齐）
8.  Persona 8: 本地查询 0 题（不给题，不生成）
9.  Persona 9: 旧联网入口拒绝调用，已有本地题保持原样
10. Persona 10: 虚假学校拦截测试（「中国邮电大学」等虚构大学被严格拦截）
11. Persona 11: 虚假比赛拦截测试（「虚构测试比赛」等非五大学科奥赛规范赛事被拦截）
12. Persona 12: 普通题保护机制（竞赛生训练普通高考卷不被升级为竞赛题）
13. Persona 13: 跨学科隔离机制（物理题绝不进入地理训练）
14. Persona 14: 题目来源按钮定位（每道题准确追溯文档、页码、年份、题号）
15. Persona 15: 弟弟初一数学真实材料测试接口（初中数学基础教育普通试题核验）
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.database import Database
from src.eligibility_models import CompetitionTier
from src.eligibility_rules import EligibilityRuleEngine
from src.question_source_models import (
    QuestionSourceCandidate,
    QuestionSourceError,
    VerificationStatus,
)
from src.question_source_retrieval_service import QuestionSourceRetrievalService
from src.question_source_verification import QuestionSourceVerifier
from src.targeted_training_models import TrainingTask
from src.training_profile_models import (
    BasicEducationProfile,
    EducationType,
    LearnerProfile,
)


def make_basic_profile(
    *,
    stage: str = "高中",
    grade: str = "高二",
    in_strong_base: bool = False,
    strong_base_school: str | None = None,
    strong_base_subject: str | None = None,
    in_competition: bool = False,
    competition_subjects: dict[str, str] | None = None,
) -> LearnerProfile:
    """Helper to create a validated BasicEducationProfile inside a LearnerProfile."""
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


@pytest.fixture
def stage8_db(tmp_path: Path) -> Database:
    """Create a temporary SQLite database initialized with test materials for Stage 8."""
    geo_pdf = tmp_path / "stage8-geography.pdf"
    geo_pdf.write_bytes(b"%PDF-1.7\nStage 8 synthetic geography fixture")
    geo_image = tmp_path / "stage8-geography.png"
    geo_image.write_bytes(b"stage8-geography-page")
    phy_pdf = tmp_path / "stage8-physics.pdf"
    phy_pdf.write_bytes(b"%PDF-1.7\nStage 8 synthetic physics fixture")
    phy_image = tmp_path / "stage8-physics.png"
    phy_image.write_bytes(b"stage8-physics-page")
    db = Database(tmp_path / "stage8.db")
    now = datetime.now(UTC).isoformat()

    with db._connection() as conn:
        # 1. 题型族与方法族定义
        conn.execute(
            """
            INSERT OR REPLACE INTO question_families
            (id, family_kind, title, created_at, updated_at)
            VALUES (8511, 'type', '综合地理区域分析', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO question_families
            (id, family_kind, title, created_at, updated_at)
            VALUES (8512, 'method', '动量定理与守恒方法', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO question_families
            (id, family_kind, title, created_at, updated_at)
            VALUES (8515, 'type', '初中数学绝对值与方程', ?, ?)
            """,
            (now, now),
        )

        # 2. 插入文档与页面（8511 地理：5道题 -> 用于分支 A 测试）
        conn.execute(
            """
            INSERT OR REPLACE INTO documents
            (id, title, filename, source_path, sha256, created_at, updated_at)
            VALUES (101, '2024江苏高考地理 Final Boss', 'stage8-geography.pdf',
                    ?, ?, ?, ?)
            """,
            (str(geo_pdf), hashlib.sha256(geo_pdf.read_bytes()).hexdigest(), now, now),
        )
        for p in range(1, 6):
            conn.execute(
                """
                INSERT OR REPLACE INTO pages
                (id, document_id, page_number, image_path, extracted_text,
                 status, review_status, created_at, updated_at)
                VALUES (?, 101, ?, ?,
                        '贺兰山东麓冲积扇地下水埋深演化过程分析与区域生态环境特征。',
                        'text_extracted', 'reviewed', ?, ?)
                """,
                (1000 + p, p, str(geo_image), now, now),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO question_items
                (id, document_id, page_id, question_number, question_kind, subject, stem_text,
                 source_document_title_snapshot, correction_note, teacher_comment,
                 stem_confidence, created_at, updated_at)
                VALUES (?, 101, ?, ?, 'typical', '地理', ?, '2024江苏高考地理 Final Boss',
                        '地下水自出山口至扇缘埋深由浅变深', '区域综合分析典例',
                        'confirmed', ?, ?)
                """,
                (
                    2000 + p,
                    1000 + p,
                    str(p),
                    f"综合地理区域分析变式{p}：分析贺兰山第{p}号冲积扇地下水埋深特征。",
                    now,
                    now,
                ),
            )

        # 3. 插入文档与页面（8512 物理：仅2道题 -> 用于分支 B 测试）
        conn.execute(
            """
            INSERT OR REPLACE INTO documents
            (id, title, filename, source_path, sha256, created_at, updated_at)
            VALUES (102, '2024江苏高中物理简单回归', 'stage8-physics.pdf',
                    ?, ?, ?, ?)
            """,
            (str(phy_pdf), hashlib.sha256(phy_pdf.read_bytes()).hexdigest(), now, now),
        )
        for p in range(1, 3):
            conn.execute(
                """
                INSERT OR REPLACE INTO pages
                (id, document_id, page_number, image_path, extracted_text,
                 status, review_status, created_at, updated_at)
                VALUES (?, 102, ?, ?,
                        '滑块在光滑斜面上的动量定理与动量守恒分析过程。',
                        'text_extracted', 'reviewed', ?, ?)
                """,
                (2000 + p, p, str(phy_image), now, now),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO question_items
                (id, document_id, page_id, question_number, question_kind, subject, stem_text,
                 source_document_title_snapshot, correction_note, teacher_comment,
                 stem_confidence, created_at, updated_at)
                VALUES (?, 102, ?, ?, 'typical', '物理', ?, '2024江苏高中物理简单回归',
                        '动量定理 F*t = Δp', '动量守恒经典方法',
                        'confirmed', ?, ?)
                """,
                (
                    3000 + p,
                    2000 + p,
                    str(p),
                    f"动量定理与守恒方法变式{p}：分析第{p}阶段小球受力冲量。",
                    now,
                    now,
                ),
            )

    return db


# ======================================================================
# Persona 1: 高中普通学生（无强基/竞赛入口）
# ======================================================================
def test_persona_1_high_school_regular_student(stage8_db: Database) -> None:
    """Persona 1: Regular High School student has no Qiangji/Competition scope."""
    engine = EligibilityRuleEngine()
    profile = make_basic_profile(
        stage="高中",
        grade="高二",
        in_strong_base=False,
        in_competition=False,
    )
    # Checkbox visibility logic
    is_high_school = profile.basic and profile.basic.stage == "高中"
    show_sb_checkbox = bool(is_high_school and profile.basic.in_strong_base)
    show_comp_checkbox = bool(is_high_school and profile.basic.in_competition)

    assert show_sb_checkbox is False
    assert show_comp_checkbox is False

    # Scope evaluation
    scope = engine.compute_allowed_scope(profile)
    assert scope.allow_regular is True
    assert scope.is_strong_base_eligible is False
    assert scope.is_competition_eligible is False


# ======================================================================
# Persona 2: 高中强基学生（未勾选不查强基，勾选后查询对应题源）
# ======================================================================
def test_persona_2_high_school_strong_base_student(stage8_db: Database) -> None:
    """Persona 2: High School Qiangji student checkbox gating."""
    engine = EligibilityRuleEngine()
    profile = make_basic_profile(
        stage="高中",
        grade="高三",
        in_strong_base=True,
        strong_base_school="南京大学",
        strong_base_subject="物理学",
    )
    # Checkbox visibility
    assert profile.basic is not None
    assert profile.basic.in_strong_base is True

    # 1. 默认状态：用户未勾选强基多选框 (include_strong_base=False)
    scope_unchecked = engine.compute_allowed_scope(
        profile, include_strong_base=False
    )
    assert scope_unchecked.is_strong_base_eligible is False

    # 2. 用户主动勾选强基多选框 (include_strong_base=True)
    scope_checked = engine.compute_allowed_scope(
        profile, include_strong_base=True
    )
    assert scope_checked.is_strong_base_eligible is True
    assert scope_checked.strong_base_school == "南京大学"
    assert scope_checked.strong_base_subject == "物理学"

    # 3. 校验题目合格性：南京大学强基试题合格，清华大学强基试题被拦截
    cand_nju = QuestionSourceCandidate(
        question_text="南京大学强基校测物理试题：分析粒子势阱束缚态",
        source_name="2023年南京大学强基计划校测试卷",
        source_url="https://bkzs.nju.edu.cn/archive/2023/qiangji_q1.pdf",
        year=2023,
        exam_or_contest_name="2023年南京大学强基计划校测",
        question_number="1",
        subject="物理",
        applicable_scope="基础教育 · 强基计划",
        school_name="南京大学",
        major_direction="物理学",
    )
    cand_thu = QuestionSourceCandidate(
        question_text="清华大学强基校测数学试题：二阶导数零点分析",
        source_name="2023年清华大学强基计划数学试卷",
        source_url="https://join-tsinghua.edu.cn/archive/2023/qiangji_math_q2.pdf",
        year=2023,
        exam_or_contest_name="2023年清华大学强基计划校测",
        question_number="2",
        subject="数学",
        applicable_scope="基础教育 · 强基计划",
        school_name="清华大学",
        major_direction="数学与应用数学",
    )
    dec_nju = engine.evaluate_candidate_eligibility(cand_nju, scope_checked)
    dec_thu = engine.evaluate_candidate_eligibility(cand_thu, scope_checked)
    assert dec_nju.is_eligible is True
    assert dec_thu.is_eligible is False
    assert "强基高校不匹配" in (dec_thu.reason or "")


# ======================================================================
# Persona 3: 高中竞赛学生（未勾选不查竞赛，勾选后按层级查询）
# ======================================================================
def test_persona_3_high_school_competition_student(stage8_db: Database) -> None:
    """Persona 3: High School Competition student checkbox gating and tier boundaries."""
    engine = EligibilityRuleEngine()
    profile = make_basic_profile(
        stage="高中",
        grade="高二",
        in_competition=True,
        competition_subjects={"物理": "全国物理竞赛复赛（省一）"},
    )
    # 1. 用户未勾选竞赛多选框 (include_competition=False)
    scope_unchecked = engine.compute_allowed_scope(
        profile, include_competition=False
    )
    assert scope_unchecked.is_competition_eligible is False

    # 2. 用户主动勾选竞赛多选框 (include_competition=True)
    scope_checked = engine.compute_allowed_scope(
        profile, include_competition=True
    )
    assert scope_checked.is_competition_eligible is True
    assert scope_checked.competition_subject == "物理"
    assert scope_checked.competition_tier == CompetitionTier.PROVINCIAL

    # 3. 校验题目：复赛（省级）试题通过，决赛（国家级）试题被阻断
    cand_prov = QuestionSourceCandidate(
        question_text="第40届全国中学生物理竞赛复赛试题：小球碰撞动量定理",
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
    cand_nat = QuestionSourceCandidate(
        question_text="第40届全国中学生物理竞赛决赛理论试题：狭义相对论动量演化",
        source_name="第40届全国中学生物理竞赛决赛试题",
        source_url="http://www.cps-net.org.cn/cpho/final_q2.pdf",
        year=2023,
        exam_or_contest_name="第40届全国中学生物理竞赛决赛",
        question_number="2",
        subject="物理",
        applicable_scope="基础教育 · 学科竞赛",
        contest_name="全国中学生物理竞赛",
        contest_tier="国家级",
    )
    dec_prov = engine.evaluate_candidate_eligibility(cand_prov, scope_checked)
    dec_nat = engine.evaluate_candidate_eligibility(cand_nat, scope_checked)
    assert dec_prov.is_eligible is True
    assert dec_nat.is_eligible is False
    assert "越级" in (dec_nat.reason or "") or "阶段不匹配" in (dec_nat.reason or "")


# ======================================================================
# Persona 4: 初中学生（完全无强基/竞赛入口）
# ======================================================================
def test_persona_4_junior_high_student_no_advanced_entries(stage8_db: Database) -> None:
    """Persona 4: Junior high student has zero access to Qiangji or Competition."""
    engine = EligibilityRuleEngine()
    profile = make_basic_profile(
        stage="初中",
        grade="初二",
    )
    # Checkbox visibility logic
    is_high_school = profile.basic and profile.basic.stage == "高中"
    assert is_high_school is False

    # Scope evaluation
    scope = engine.compute_allowed_scope(
        profile, include_strong_base=True, include_competition=True
    )
    assert scope.education_stage == "基础教育"
    assert scope.is_strong_base_eligible is False
    assert scope.is_competition_eligible is False


# ======================================================================
# Persona 5: 小学学生（完全无高级题源）
# ======================================================================
def test_persona_5_elementary_student_no_advanced_entries(stage8_db: Database) -> None:
    """Persona 5: Elementary school student has zero access to advanced sources."""
    engine = EligibilityRuleEngine()
    profile = make_basic_profile(
        stage="小学",
        grade="五年级",
    )
    scope = engine.compute_allowed_scope(
        profile, include_strong_base=True, include_competition=True
    )
    assert scope.is_strong_base_eligible is False
    assert scope.is_competition_eligible is False
    assert scope.allow_regular is True


# ======================================================================
# Persona 6: 本地查询找到 >= 5 题（分支 A：直接出现【开始训练】）
# ======================================================================
def test_persona_6_local_search_branch_a_five_items(stage8_db: Database) -> None:
    """Persona 6: Local search finds >= 5 questions, activates Branch A."""
    retrieval_service = QuestionSourceRetrievalService(stage8_db)
    task = TrainingTask(
        training_type="题型族",
        target="综合地理区域分析",
        family_id=8511,
        subject="地理",
    )
    result = retrieval_service.retrieve_local_only(task=task, limit=5)

    assert result.verified_count == 5
    assert "已从本地资料库找到 5 道符合条件的训练题" in result.prompt_message
    assert result.can_retry is False
    # All 5 questions are from local database
    for q in result.questions:
        assert q.document_id == 101
        assert q.page_number is not None
        assert "2024江苏高考地理" in q.exam_or_contest_name


# ======================================================================
# Persona 7: 本地查询找到不足 5 题（照实返回）
# ======================================================================
def test_persona_7_local_search_branch_b_insufficient_items(stage8_db: Database) -> None:
    """Persona 7: Local search returns exactly two verified items."""
    retrieval_service = QuestionSourceRetrievalService(stage8_db)
    task = TrainingTask(
        training_type="方法族",
        target="动量定理与守恒方法",
        family_id=8512,
        subject="物理",
    )
    result = retrieval_service.retrieve_local_only(task=task, limit=5)

    assert result.verified_count == 2
    assert "已从本地资料库找到 2 道符合条件的训练题" in result.prompt_message
    assert "不足" not in result.prompt_message
    assert len(result.questions) == 2


# ======================================================================
# Persona 8: 本地查询 0 题（不给题，严禁 AI 出题）
# ======================================================================
def test_persona_8_local_search_branch_c_zero_items(stage8_db: Database) -> None:
    """Persona 8: Local search finds 0 items, activates Branch C, zero AI synthesis."""
    retrieval_service = QuestionSourceRetrievalService(stage8_db)
    task = TrainingTask(
        training_type="题型族",
        target="不存在的本地题型",
        family_id=9999,
        subject="化学",
    )
    result = retrieval_service.retrieve_local_only(task=task, limit=5)

    assert result.verified_count == 0
    assert result.prompt_message == (
        "本地资料库暂未找到可核验的变式训练题；可以先导入其他试卷，再重新本地查询。"
    )
    assert len(result.questions) == 0
    assert result.can_retry is True


# ======================================================================
# Persona 9: 旧联网接口明确拒绝
# ======================================================================
def test_persona_9_external_supplement_merging(stage8_db: Database) -> None:
    """Persona 9: Legacy external entry is disabled and cannot alter local items."""
    retrieval_service = QuestionSourceRetrievalService(stage8_db)
    task = TrainingTask(
        training_type="方法族",
        target="动量定理与守恒方法",
        family_id=8512,
        subject="物理",
    )
    # 1. 本地查询得到 2 道题
    local_res = retrieval_service.retrieve_local_only(task=task, limit=5)
    assert local_res.verified_count == 2

    # 旧联网方法即使被直接调用，也必须在入口拒绝。
    with pytest.raises(QuestionSourceError, match="联网题源检索已停用"):
        retrieval_service.retrieve_external_supplement(
            task=task,
            existing_questions=local_res.questions,
        )
    assert [q.document_id for q in local_res.questions] == [102, 102]


# ======================================================================
# Persona 10: 虚假学校拦截测试（中国邮电大学）
# ======================================================================
def test_persona_10_fake_university_rejection(stage8_db: Database) -> None:
    """Persona 10: Fake university '中国邮电大学' rejected by rule engine."""
    engine = EligibilityRuleEngine()
    cand = QuestionSourceCandidate(
        question_text="中国邮电大学期末试卷：自动化控制系统极点配置",
        source_name="中国邮电大学2023年期末考试试卷",
        source_url="https://fake-cput.edu.cn/exam.pdf",
        year=2023,
        exam_or_contest_name="中国邮电大学期末考试",
        question_number="3",
        subject="工学",
        applicable_scope="高等教育 · 本科",
        school_name="中国邮电大学",
        major_direction="机器人工程",
    )
    verifier = QuestionSourceVerifier(stage8_db, eligibility_engine=engine)
    res = verifier.verify_candidate(cand)
    assert res.status == VerificationStatus.REJECTED
    assert "高校" in (res.reason or "") or "不属于" in (res.reason or "")


# ======================================================================
# Persona 11: 虚假比赛拦截测试（虚构测试比赛）
# ======================================================================
def test_persona_11_fake_competition_rejection(stage8_db: Database) -> None:
    """Persona 11: Fabricated competition rejected."""
    engine = EligibilityRuleEngine()
    cand = QuestionSourceCandidate(
        question_text="2024不存在全国中学生物理测试比赛试题：测试题干内容",
        source_name="不存在比赛试题集",
        source_url="http://fake-contest.org.cn/exam.pdf",
        year=2024,
        exam_or_contest_name="2024不存在全国中学生物理测试比赛",
        question_number="1",
        subject="物理",
        applicable_scope="基础教育 · 学科竞赛",
        contest_name="不存在全国中学生物理测试比赛",
        contest_tier="省级",
    )
    verifier = QuestionSourceVerifier(stage8_db, eligibility_engine=engine)
    res = verifier.verify_candidate(cand)
    assert res.status == VerificationStatus.REJECTED
    assert "不属于全国五大学科奥赛规范赛事" in (res.reason or "")


# ======================================================================
# Persona 12: 普通题保护机制（竞赛生训练普通高考卷不升级）
# ======================================================================
def test_persona_12_regular_exam_protection_for_competition_user(stage8_db: Database) -> None:
    """Persona 12: Standard Gaokao exam is protected and never inflated."""
    engine = EligibilityRuleEngine()
    comp_profile = make_basic_profile(
        stage="高中",
        grade="高三",
        in_competition=True,
        competition_subjects={"物理": "全国中学生物理竞赛集训队（国家队选拔）"},
    )
    cand_regular = QuestionSourceCandidate(
        question_text="【2024年高考物理新课标卷·第24题】恒定推力冲量与动量变化量计算",
        source_name="2024年普通高等学校招生全国统一考试物理试题",
        source_url="https://gaokao.neea.edu.cn/archive/2024/physics_q24.pdf",
        year=2024,
        exam_or_contest_name="2024年高考物理新课标卷",
        question_number="24",
        subject="物理",
        applicable_scope="基础教育 · 普通",
        is_regular_exam=True,
    )
    scope = engine.compute_allowed_scope(comp_profile)
    decision = engine.evaluate_candidate_eligibility(cand_regular, scope)
    assert decision.is_eligible is True
    # 强制界定为普通题，禁止升级为竞赛
    assert decision.normalized_scope == "基础教育 · 普通"


# ======================================================================
# Persona 13: 跨学科隔离机制（物理题绝不进入地理训练）
# ======================================================================
def test_persona_13_cross_discipline_isolation(stage8_db: Database) -> None:
    """Persona 13: Physics question strictly rejected in Geography training task."""
    engine = EligibilityRuleEngine()
    geo_task = TrainingTask(
        training_type="题型族",
        target="综合地理区域分析",
        family_id=8511,
        subject="地理",
    )
    cand_physics = QuestionSourceCandidate(
        question_text="利用动量定理计算滑块在光滑斜面上的动量变化量",
        source_name="2024江苏高中物理简单回归",
        source_url="pages/3_浏览资料.py?document=102&page=1",
        year=2024,
        exam_or_contest_name="2024江苏高中物理简单回归",
        question_number="1",
        subject="物理",
        applicable_scope="基础教育 · 普通",
    )
    verifier = QuestionSourceVerifier(stage8_db, eligibility_engine=engine)
    res = verifier.verify_candidate(cand_physics, task=geo_task)
    assert res.status == VerificationStatus.REJECTED
    assert "学科不匹配" in (res.reason or "")


# ======================================================================
# Persona 14: 题目来源按钮定位（每道题准确追溯文档、页码、年份、题号）
# ======================================================================
def test_persona_14_question_provenance_button_details(stage8_db: Database) -> None:
    """Persona 14: Provenance traceability and button link data integrity."""
    retrieval_service = QuestionSourceRetrievalService(stage8_db)
    task = TrainingTask(
        training_type="题型族",
        target="综合地理区域分析",
        family_id=8511,
        subject="地理",
    )
    res = retrieval_service.retrieve_local_only(task=task, limit=5)
    for q in res.questions:
        assert q.document_id == 101
        assert q.page_number in (1, 2, 3, 4, 5)
        assert q.question_number in ("1", "2", "3", "4", "5")
        assert q.year == 2024  # the fixture document title explicitly contains 2024
        assert q.source_url.startswith("pages/3_浏览资料.py")
        assert f"page={q.page_number}" in q.source_url


# ======================================================================
# Persona 15: 弟弟初一数学真实材料测试接口（初中数学基础教育普通试题核验）
# ======================================================================
def test_persona_15_junior_high_math_external_search_fails_closed(
    stage8_db: Database,
) -> None:
    """Persona 15: Grade 7 search returns no question without source evidence."""
    retrieval_service = QuestionSourceRetrievalService(stage8_db)
    math_profile = make_basic_profile(
        stage="初中",
        grade="初一",
    )
    task_math = TrainingTask(
        training_type="题型族",
        target="绝对值问题",
        subject="数学",
    )
    # 旧联网方法不再返回任何题，即使调用者拥有完整个人信息。
    with pytest.raises(QuestionSourceError, match="联网题源检索已停用"):
        retrieval_service.retrieve_external_only(task=task_math, profile=math_profile)
    local = retrieval_service.retrieve_local_only(task=task_math, profile=math_profile)
    assert local.questions == []
    assert local.verified_count == 0
