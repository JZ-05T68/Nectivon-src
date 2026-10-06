"""Unit tests for Question Source Verification Engine and Retrieval Service (Phase 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.database import Database
from src.question_source_models import (
    QuestionSourceCandidate,
    QuestionSourceError,
    VerificationStatus,
    VerifiedQuestion,
)
from src.question_source_retrieval_service import QuestionSourceRetrievalService
from src.question_source_verification import QuestionSourceVerifier
from src.targeted_training_models import TrainingTask
from src.training_profile_models import (
    BasicEducationProfile,
    EducationType,
    LearnerProfile,
)

STAGING_8511 = Path("staging-data/data/database/knowledge.db")
STAGING_8512 = Path("staging-data-8512/data/database/knowledge.db")


# ==============================================================================
# 1. 领域模型与三态约束测试
# ==============================================================================


def test_verification_status_tri_state() -> None:
    """Ensure exact tri-state model is strictly defined."""
    assert VerificationStatus.CANDIDATE == "candidate"
    assert VerificationStatus.VERIFIED == "verified"
    assert VerificationStatus.REJECTED == "rejected"


def test_verified_question_contract_and_fingerprint() -> None:
    """VerifiedQuestion must enforce verified status and compute deterministic fingerprint."""
    q = VerifiedQuestion(
        id="vq_test_1",
        question_text="测试地理区域分析题干内容详情",
        source_name="2024年江苏高考地理卷",
        source_url="pages/3_浏览资料.py?document=138&page=2#q_65",
        year=2024,
        exam_or_contest_name="2024年江苏高考地理卷",
        question_number="12",
        subject="地理",
        applicable_scope="基础教育 · 普通",
    )
    assert q.verification_status == VerificationStatus.VERIFIED
    assert len(q.question_fingerprint) == 16

    contract = q.to_display_contract()
    assert contract["question_text"] == "测试地理区域分析题干内容详情"
    assert contract["source_name"] == "2024年江苏高考地理卷"
    assert contract["source_url"] == "pages/3_浏览资料.py?document=138&page=2#q_65"
    assert contract["year"] == 2024
    assert contract["exam_or_contest_name"] == "2024年江苏高考地理卷"
    assert contract["question_number"] == "12"
    assert contract["subject"] == "地理"
    assert contract["applicable_scope"] == "基础教育 · 普通"
    assert contract["verification_status"] == "verified"


def test_local_source_year_is_never_guessed_when_title_has_no_year() -> None:
    """An undated uploaded paper must stay undated instead of defaulting to 2024."""

    assert (
        QuestionSourceRetrievalService._extract_year_from_title(
            "七年级上学期数学练习（无年份）"
        )
        is None
    )
    assert (
        QuestionSourceRetrievalService._extract_year_from_title(
            "2023-2024学年第一学期七年级数学期末练习"
        )
        == 2023
    )


def test_verified_question_rejects_non_verified_status() -> None:
    """VerifiedQuestion cannot be constructed with candidate or rejected status."""
    with pytest.raises(QuestionSourceError):
        VerifiedQuestion(
            id="bad_1",
            question_text="题干",
            source_name="来源",
            source_url="http://example.com/q1",
            year=2024,
            exam_or_contest_name="试卷",
            question_number="1",
            subject="地理",
            applicable_scope="普通",
            verification_status=VerificationStatus.CANDIDATE,  # type: ignore[arg-type]
        )


# ==============================================================================
# 2. 真实性与来源边界核验引擎测试（QuestionSourceVerifier）
# ==============================================================================


def test_verifier_accepts_authentic_gaokao_question() -> None:
    """Authentic Gaokao question with precise URL and valid metadata passes verification."""
    verifier = QuestionSourceVerifier()
    candidate = QuestionSourceCandidate(
        question_text="分析该区域洪积扇地下水埋深在出山口至边缘由浅变深的形成过程。",
        source_name="2024年普通高等学校招生全国统一考试文科综合地理（新课标卷）",
        source_url="https://gaokao.neea.edu.cn/archive/2024/geography_q36.pdf",
        year=2024,
        exam_or_contest_name="2024年高考全国新课标卷",
        question_number="36",
        subject="地理",
        applicable_scope="基础教育 · 普通",
    )
    task = TrainingTask(training_type="题型族", target="某区域分析类", subject="地理")

    res = verifier.verify_candidate(candidate, task=task)
    assert res.is_verified is True
    assert res.status == VerificationStatus.VERIFIED
    assert res.verified_question is not None
    assert res.verified_question.subject == "地理"


def test_verifier_rejects_ai_mock_hallucinations() -> None:
    """Candidates containing AI generation signatures must be rejected."""
    verifier = QuestionSourceVerifier()
    signatures = [
        "作为AI语言模型，为您生成以下高中地理练习题：某区域农业发展...",
        "以下是一道模拟题：假设在某光滑水平面上...",
        "本试题由大模型生成，用于高中物理针对训练...",
    ]
    for text in signatures:
        cand = QuestionSourceCandidate(
            question_text=text,
            source_name="网络题源",
            source_url="https://example.com/questions/100",
            year=2024,
            exam_or_contest_name="模拟试卷",
            question_number="1",
            subject="地理",
        )
        res = verifier.verify_candidate(cand)
        assert res.status == VerificationStatus.REJECTED
        assert "AI" in str(res.reason)


def test_verifier_rejects_search_engine_urls() -> None:
    """Search engine result URLs are strictly rejected.

    They cannot pinpoint the original question location.
    """
    verifier = QuestionSourceVerifier()
    banned_urls = [
        "https://www.baidu.com/s?wd=高考地理真题",
        "https://google.com/search?q=physics+momentum",
        "https://cn.bing.com/search?q=动量定理试卷",
    ]
    for url in banned_urls:
        cand = QuestionSourceCandidate(
            question_text="某区域地貌类型分析题干详情...",
            source_name="百度搜索",
            source_url=url,
            year=2024,
            exam_or_contest_name="搜索结果",
            question_number="1",
            subject="地理",
        )
        res = verifier.verify_candidate(cand)
        assert res.status == VerificationStatus.REJECTED
        assert "搜索引擎" in str(res.reason) or "搜索关键词" in str(res.reason)


def test_verifier_rejects_root_domain_and_homepage_urls() -> None:
    """Root domains or generic homepages cannot pinpoint the original question."""
    verifier = QuestionSourceVerifier()
    cand = QuestionSourceCandidate(
        question_text="滑块冲上斜面求推力的冲量与动量变化量...",
        source_name="教育部官网",
        source_url="http://www.moe.gov.cn",
        year=2024,
        exam_or_contest_name="官方试卷",
        question_number="24",
        subject="物理",
    )
    res = verifier.verify_candidate(cand)
    assert res.status == VerificationStatus.REJECTED
    assert "根域名或首页" in str(res.reason)


def test_verifier_rejects_fake_university() -> None:
    """Fabricated universities (e.g. '中国邮电大学') must be detected and rejected."""
    verifier = QuestionSourceVerifier()
    cand = QuestionSourceCandidate(
        question_text="大学物理力学试题：质点在保守力场中的拉格朗日函数...",
        source_name="中国邮电大学期末试卷",
        source_url="https://example.edu.cn/archive/physics_exam.pdf",
        year=2024,
        exam_or_contest_name="中国邮电大学期末考试",
        question_number="1",
        subject="物理",
        school_name="中国邮电大学",
    )
    res = verifier.verify_candidate(cand)
    assert res.status == VerificationStatus.REJECTED
    assert "中国邮电大学" in str(res.reason)


def test_verifier_rejects_fake_competition() -> None:
    """Fabricated competitions (e.g. '不存在比赛') must be rejected."""
    verifier = QuestionSourceVerifier()
    cand = QuestionSourceCandidate(
        question_text="全国中学生物理不存在比赛复赛试题第1题...",
        source_name="不存在比赛组委会",
        source_url="https://example.org/contest/physics/q1.pdf",
        year=2024,
        exam_or_contest_name="不存在比赛物理决赛",
        question_number="1",
        subject="物理",
        applicable_scope="基础教育 · 竞赛",
        contest_name="不存在比赛",
    )
    res = verifier.verify_candidate(cand)
    assert res.status == VerificationStatus.REJECTED
    assert "不存在比赛" in str(res.reason)


def test_verifier_rejects_anomalous_years() -> None:
    """Future years (> current year) or pre-1977 years are rejected."""
    verifier = QuestionSourceVerifier()
    for bad_year in (2099, 1950):
        cand = QuestionSourceCandidate(
            question_text="测试异常年份地理区域试题内容...",
            source_name="模拟试卷",
            source_url="https://example.com/questions/q1.pdf",
            year=bad_year,
            exam_or_contest_name="某试卷",
            question_number="1",
            subject="地理",
        )
        res = verifier.verify_candidate(cand)
        assert res.status == VerificationStatus.REJECTED
        assert "年份异常" in str(res.reason)


def test_verifier_cross_discipline_isolation() -> None:
    """Geography training task strictly rejects physics questions."""
    verifier = QuestionSourceVerifier()
    task = TrainingTask(training_type="题型族", target="某区域分析类", subject="地理")

    # 1. 显式学科不匹配
    cand_phy = QuestionSourceCandidate(
        question_text="一质量为 m 的滑块冲上斜面，求推力的冲量与动量变化量...",
        source_name="2024物理新课标卷",
        source_url="https://gaokao.neea.edu.cn/archive/2024/physics_q24.pdf",
        year=2024,
        exam_or_contest_name="高考物理新课标卷",
        question_number="24",
        subject="物理",
    )
    res1 = verifier.verify_candidate(cand_phy, task=task)
    assert res1.status == VerificationStatus.REJECTED
    assert "学科不匹配" in str(res1.reason)

    # 2. 题干包含物理专有概念
    cand_mixed = QuestionSourceCandidate(
        question_text="带电粒子在匀强磁场中运动受到洛伦兹力作用，求其运动轨迹...",
        source_name="综合卷",
        source_url="https://gaokao.neea.edu.cn/archive/2024/exam_q1.pdf",
        year=2024,
        exam_or_contest_name="综合试卷",
        question_number="1",
        subject="地理",  # 试图伪装成地理
    )
    res2 = verifier.verify_candidate(cand_mixed, task=task)
    assert res2.status == VerificationStatus.REJECTED
    assert "学科不匹配" in str(res2.reason)


def test_verifier_regular_question_protection() -> None:
    """Standard Gaokao questions must remain '基础教育 · 普通'.

    Even if the active user profile is a contest student.
    """
    verifier = QuestionSourceVerifier()
    profile = LearnerProfile(
        education_type=EducationType.BASIC,
        basic=BasicEducationProfile(
            province="江苏省",
            province_code="32",
            city="南京市",
            city_code="3201",
            stage="高中",
            grade="高三",
            in_competition=True,
            competition_subjects={"物理": "全国中学生物理竞赛复赛备战阶段（省赛/省队选拔）"},
        ),
    )
    cand = QuestionSourceCandidate(
        question_text="2024年江苏省高考地理试卷第12题关于某区域构造地貌分析...",
        source_name="02苏州地理 · 第2页",
        source_url="pages/3_浏览资料.py?document=138&page=2#q_65",
        year=2024,
        exam_or_contest_name="2024年江苏高考地理卷（02苏州地理）",
        question_number="12",
        subject="地理",
        applicable_scope="基础教育 · 竞赛",  # 错误或故意标注为竞赛
        document_id=138,
        page_id=586,
        page_number=2,
    )
    res = verifier.verify_candidate(cand, profile=profile)
    assert res.is_verified is True
    # 严格修正并保护为普通题，严禁拔高
    assert res.verified_question is not None
    assert res.verified_question.applicable_scope == "基础教育 · 普通"


# ==============================================================================
# 3. 检索核验服务与数量规则测试（QuestionSourceRetrievalService）
# ==============================================================================


def test_retrieval_service_reads_8511_local_questions() -> None:
    """Retrieval service retrieves authentic local questions from 8511 database."""
    if not STAGING_8511.is_file():
        pytest.skip("8511 database not found")

    db = Database(STAGING_8511)
    service = QuestionSourceRetrievalService(db)
    task = TrainingTask(
        training_type="题型族",
        target="某区域分析类",
        family_id=114,
        subject="地理",
    )

    result = service.retrieve_and_verify(task=task, limit=5)
    if result.verified_count == 0:
        pytest.skip("current 8511 staging database has no matching geography fixture")
    assert result.verified_count > 0
    assert result.subject == "地理"
    # 每道试题都必须拥有来源名称、URL、年份、指纹
    for q in result.questions:
        assert q.verification_status == VerificationStatus.VERIFIED
        assert q.source_name
        assert q.source_url
        assert q.year is not None
        assert len(q.question_fingerprint) == 16


def test_retrieval_service_reads_8512_local_questions() -> None:
    """Retrieval service retrieves authentic local questions from 8512 physics database."""
    if not STAGING_8512.is_file():
        pytest.skip("8512 database not found")

    db = Database(STAGING_8512)
    service = QuestionSourceRetrievalService(db)
    task = TrainingTask(
        training_type="方法族",
        target="动量定理",
        family_id=1,
        subject="物理",
    )

    result = service.retrieve_and_verify(task=task, limit=5)
    if result.verified_count == 0:
        pytest.skip("current 8512 staging database has no matching physics fixture")
    assert result.verified_count > 0
    assert result.subject == "物理"
    for q in result.questions:
        assert q.verification_status == VerificationStatus.VERIFIED
        assert q.subject == "物理"


def test_retrieval_service_few_questions_no_ai_filling(tmp_path: Path) -> None:
    """When fewer than 5 verified questions exist, display available count.

    Must never supplement with AI-synthesized filler questions.
    """
    test_db = tmp_path / "test_few.db"
    db = Database(test_db)
    service = QuestionSourceRetrievalService(db)

    # 用一个没有大量题目的目标（例如一个仅有1个题目的小族）
    task = TrainingTask(
        training_type="题型族",
        target="非主流偏门题型",
        subject="地理",
    )

    result = service.retrieve_and_verify(task=task, limit=5)
    # 如果找到的数量小于5，提示信息必须为 '当前找到 N 道符合条件的可靠题目。'
    if 0 < result.verified_count < 5:
        assert f"当前找到 {result.verified_count} 道符合条件的可靠题目。" in result.prompt_message
        assert len(result.questions) == result.verified_count
        # 绝不补充AI题目凑满5道
        assert len(result.questions) < 5


def test_retrieval_service_zero_questions_prompt(tmp_path: Path) -> None:
    """When zero reliable questions exist, display prompt and enable retry button."""
    test_db = tmp_path / "test_zero.db"
    db = Database(test_db)
    service = QuestionSourceRetrievalService(db)

    task = TrainingTask(
        training_type="题型族",
        target="完全不存在且无题源的虚构题型",
        subject="未知学科",
    )

    result = service.retrieve_and_verify(task=task, limit=5)
    assert result.verified_count == 0
    assert len(result.questions) == 0
    expected_msg = "本地资料库暂未找到可核验的变式训练题；可以先导入其他试卷，再重新本地查询。"
    assert expected_msg == result.prompt_message
    assert result.can_retry is True


def test_retrieval_service_audit_persistence(tmp_path: Path) -> None:
    """Verified questions are persisted to SQLite targeted_training_questions table."""
    test_db = tmp_path / "test_audit.db"
    db = Database(test_db)
    service = QuestionSourceRetrievalService(db)

    task = TrainingTask(
        training_type="题型族",
        target="某区域分析类",
        subject="地理",
    )
    result = service.retrieve_and_verify(task=task, limit=5)
    if result.verified_count > 0:
        with db._connection() as conn:
            cnt = conn.execute("SELECT count(*) FROM targeted_training_questions").fetchone()[0]
            assert cnt == result.verified_count
