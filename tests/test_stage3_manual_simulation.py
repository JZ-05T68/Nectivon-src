"""Stage 3 Manual Simulation Tests (14+ Real User Personas).

Executes comprehensive simulated testing for Phase 3:
1. Persona 1: 基础教育高中普通生（8511 江苏地理，题型族：某区域分析类）
2. Persona 2: 基础教育高中理科生（8512 江苏物理，方法族：动量定理）
3. Persona 3: 基础教育强基计划生（江苏物理，强基目标：南京大学 · 物理学）
4. Persona 4: 基础教育物理竞赛生（全国中学生物理竞赛复赛备考）
5. Persona 5: 高等教育本科生（清华大学，物理学专业）
6. Persona 6: 高等教育研究生（北京大学，理学门类，自然地理学）
7. Persona 7: 恶意/异常用户——虚构大学（中国邮电大学）输入被拒
8. Persona 8: 恶意/异常用户——虚构比赛（不存在比赛）输入被拒
9. Persona 9: 当前 staging 真实题量场景（返回数严格等于可核验题量）
10. Persona 10: 21/5/4/2/1/0题合同场景（不限量，严禁AI凑数）
11. Persona 11: 零题目场景（无可信题源，坚守标准不降级）
12. Persona 12: 旧分页参数不能截断本地题
13. Persona 13: 来源链接失效/模糊测试（搜索引擎、根域名、模糊锚点全部拦截）
14. Persona 14: 跨学科隔离测试（地理训练任务输入物理题源，被拒并提示学科不匹配）
15. Persona 15: 普通题保护测试（竞赛生导入普通高考试卷，系统严格识别为普通题，防止虚构升级）
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.database import Database
from src.learning_workflow_service import QuestionService
from src.question_source_models import (
    QuestionSourceCandidate,
    VerificationStatus,
    VerifiedQuestion,
)
from src.question_source_retrieval_service import QuestionSourceRetrievalService
from src.question_source_verification import QuestionSourceVerifier
from src.targeted_training_models import TrainingTask
from src.training_profile_models import (
    BasicEducationProfile,
    EducationType,
    HigherEducationProfile,
    LearnerProfile,
    ProfileValidationError,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STAGING_8511 = PROJECT_ROOT / "staging-data" / "data" / "database" / "knowledge.db"
STAGING_8512 = PROJECT_ROOT / "staging-data-8512" / "data" / "database" / "knowledge.db"


def _assert_verified_provenance(question: VerifiedQuestion) -> None:
    """Assert the complete display contract for any returned verified question."""

    assert question.verification_status == VerificationStatus.VERIFIED
    assert question.question_text.strip()
    assert question.source_name.strip()
    assert question.source_url.strip()
    assert question.year is not None
    assert question.exam_or_contest_name.strip()
    assert question.question_number is not None
    assert question.subject.strip()
    assert question.applicable_scope in {
        "基础教育 · 普通",
        "基础教育 · 强基",
        "基础教育 · 竞赛",
        "高等教育 · 本科",
        "高等教育 · 研究生",
    }


def _seed_local_questions(
    tmp_path: Path,
    *,
    available_count: int,
    target: str = "某区域分析类",
    subject: str = "地理",
) -> tuple[Database, TrainingTask]:
    """Create real, traceable local test material with exactly ``available_count`` items."""

    database = Database(tmp_path / f"verified-{available_count}" / "knowledge.db")
    task = TrainingTask(training_type="题型族", target=target, subject=subject)
    if available_count == 0:
        return database, task

    raw_path = tmp_path / f"阶段3本地证据材料-{available_count}.pdf"
    raw_path.write_bytes(f"%PDF-1.7\nlocal-stage3-fixture-{available_count}".encode())
    image_path = tmp_path / f"阶段3本地证据材料-{available_count}.png"
    image_path.write_bytes(b"local-page-image")
    stems = [
        f"{target}本地原文第{index}题：根据材料分析区域差异及其形成条件。"
        for index in range(1, available_count + 1)
    ]
    document = database.create_document(
        title="2026年阶段3本地证据材料",
        filename=raw_path.name,
        source_path=raw_path,
        sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        page_count=1,
        import_status="completed",
    )
    page = database.create_page(
        document_id=document.id,
        page_number=1,
        image_path=image_path,
        extracted_text="\n".join(stems),
        status="ready",
    )
    questions = QuestionService(database)
    for index, stem in enumerate(stems, start=1):
        questions.create_question_item(
            document_id=document.id,
            page_id=page.id,
            question_kind="typical",
            question_number=str(index),
            stem_text=stem,
            stem_confidence="confirmed",
            subject=subject,
        )
    return database, task


def _local_candidate(
    tmp_path: Path,
    *,
    title: str,
    question_text: str,
    subject: str,
    applicable_scope: str,
    school_name: str | None = None,
    major_direction: str | None = None,
    contest_name: str | None = None,
    contest_tier: str | None = None,
) -> tuple[QuestionSourceVerifier, QuestionSourceCandidate]:
    """Bind a candidate to an actual temporary local document and page."""

    database = Database(tmp_path / "local-evidence" / "knowledge.db")
    raw_path = tmp_path / "范围校验材料.pdf"
    raw_path.write_bytes(f"%PDF-1.7\n{title}\n{question_text}".encode())
    image_path = tmp_path / "范围校验材料.png"
    image_path.write_bytes(b"local-page-image")
    document = database.create_document(
        title=title,
        filename=raw_path.name,
        source_path=raw_path,
        sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        page_count=1,
        import_status="completed",
    )
    page = database.create_page(
        document_id=document.id,
        page_number=1,
        image_path=image_path,
        extracted_text=question_text,
        status="ready",
    )
    question = QuestionService(database).create_question_item(
        document_id=document.id,
        page_id=page.id,
        question_kind="typical",
        question_number="1",
        stem_text=question_text,
        stem_confidence="confirmed",
    )
    candidate = QuestionSourceCandidate(
        question_text=question_text,
        source_name=f"{title} · 第1页",
        source_url=(
            f"pages/3_浏览资料.py?document={document.id}&page=1#q_{question.id}"
        ),
        year=2026,
        exam_or_contest_name=title,
        question_number="1",
        subject=subject,
        applicable_scope=applicable_scope,
        document_id=document.id,
        page_id=page.id,
        page_number=1,
        question_item_id=question.id,
        school_name=school_name,
        major_direction=major_direction,
        contest_name=contest_name,
        contest_tier=contest_tier,
    )
    return QuestionSourceVerifier(database), candidate


# ==============================================================================
# Persona 1: 基础教育高中普通生（8511 江苏地理，题型族：某区域分析类）
# ==============================================================================


def test_persona_1_basic_education_geography_student() -> None:
    """Persona 1: Regular high school student in Jiangsu practicing Geography region analysis."""
    if not STAGING_8511.is_file():
        pytest.skip("8511 database not found")

    profile = LearnerProfile(
        education_type=EducationType.BASIC,
        basic=BasicEducationProfile(
            province="江苏省",
            province_code="32",
            city="南京市",
            city_code="3201",
            stage="高中",
            grade="高三",
        ),
    )
    db = Database(STAGING_8511)
    retrieval_service = QuestionSourceRetrievalService(db)
    task = TrainingTask(
        training_type="题型族",
        target="某区域分析类",
        family_id=114,
        subject="地理",
    )

    profile.validate_isolation()
    result = retrieval_service.retrieve_local_only(task=task, profile=profile, limit=5)
    assert result.verified_count == min(result.total_available_verified, 5)
    assert len(result.questions) == result.verified_count
    assert result.subject == "地理"
    for q in result.questions:
        _assert_verified_provenance(q)
        assert q.subject == "地理"


# ==============================================================================
# Persona 2: 基础教育高中理科生（8512 江苏物理，方法族：动量定理）
# ==============================================================================


def test_persona_2_basic_education_physics_student() -> None:
    """Persona 2: Science high school student in Jiangsu practicing Physics momentum theorem."""
    if not STAGING_8512.is_file():
        pytest.skip("8512 database not found")

    profile = LearnerProfile(
        education_type=EducationType.BASIC,
        basic=BasicEducationProfile(
            province="江苏省",
            province_code="32",
            city="苏州市",
            city_code="3205",
            stage="高中",
            grade="高三",
        ),
    )
    db = Database(STAGING_8512)
    retrieval_service = QuestionSourceRetrievalService(db)
    task = TrainingTask(
        training_type="方法族",
        target="动量定理",
        family_id=1,
        subject="物理",
    )

    profile.validate_isolation()
    result = retrieval_service.retrieve_local_only(task=task, profile=profile, limit=5)
    assert result.verified_count == min(result.total_available_verified, 5)
    assert len(result.questions) == result.verified_count
    assert result.subject == "物理"
    for q in result.questions:
        _assert_verified_provenance(q)
        assert q.subject == "物理"


# ==============================================================================
# Persona 3: 基础教育强基计划生（江苏物理，强基目标：南京大学 · 物理学）
# ==============================================================================


def test_persona_3_strong_base_student(tmp_path: Path) -> None:
    """Persona 3: Qiangji plan student targeting Nanjing University Physics."""
    profile = LearnerProfile(
        education_type=EducationType.BASIC,
        basic=BasicEducationProfile(
            province="江苏省",
            province_code="32",
            city="南京市",
            city_code="3201",
            stage="高中",
            grade="高三",
            in_strong_base=True,
            strong_base_school="南京大学",
            strong_base_subject="物理学",
        ),
    )
    profile.validate_isolation()
    task = TrainingTask(training_type="方法族", target="动量定理", subject="物理")

    verifier, cand = _local_candidate(
        tmp_path,
        title="2026年南京大学强基计划物理范围校验材料",
        question_text="依据本地原文，推导变质量物体的动量定理微分形式。",
        subject="物理",
        applicable_scope="基础教育 · 强基",
        school_name="南京大学",
        major_direction="物理学",
    )
    res = verifier.verify_candidate(cand, profile=profile, task=task)
    assert res.is_verified is True
    assert res.verified_question is not None
    assert res.verified_question.applicable_scope == "基础教育 · 强基"


# ==============================================================================
# Persona 4: 基础教育物理竞赛生（全国中学生物理竞赛复赛备考）
# ==============================================================================


def test_persona_4_olympiad_competition_student(tmp_path: Path) -> None:
    """Persona 4: Physics Olympiad student preparing for national competition."""
    profile = LearnerProfile(
        education_type=EducationType.BASIC,
        basic=BasicEducationProfile(
            province="江苏省",
            province_code="32",
            city="南京市",
            city_code="3201",
            stage="高中",
            grade="高二",
            in_competition=True,
            competition_subjects={"物理": "全国中学生物理竞赛复赛备战阶段（省赛/省队选拔）"},
        ),
    )
    profile.validate_isolation()
    task = TrainingTask(training_type="方法族", target="动量定理", subject="物理")

    verifier, cand = _local_candidate(
        tmp_path,
        title="全国中学生物理竞赛复赛范围校验材料",
        question_text="依据本地原文，求双质点碰撞过程中相对质心的冲量与动量分配。",
        subject="物理",
        applicable_scope="基础教育 · 竞赛",
        contest_name="全国中学生物理竞赛",
        contest_tier="省级",
    )
    res = verifier.verify_candidate(cand, profile=profile, task=task)
    assert res.is_verified is True
    assert res.verified_question is not None
    assert res.verified_question.applicable_scope == "基础教育 · 竞赛"


# ==============================================================================
# Persona 5: 高等教育本科生（清华大学，物理学专业）
# ==============================================================================


def test_persona_5_higher_education_undergraduate(tmp_path: Path) -> None:
    """Persona 5: Undergraduate student at Tsinghua University majoring in Physics."""
    profile = LearnerProfile(
        education_type=EducationType.HIGHER,
        higher=HigherEducationProfile(
            school="清华大学",
            education_level="本科",
            major="物理学",
        ),
    )
    profile.validate_isolation()
    task = TrainingTask(training_type="方法族", target="动量定理", subject="物理")

    verifier, cand = _local_candidate(
        tmp_path,
        title="2026年清华大学物理学本科范围校验材料",
        question_text="依据本地原文，利用相对论动量分析近光速带电粒子的偏转角。",
        subject="物理",
        applicable_scope="高等教育 · 本科",
        school_name="清华大学",
    )
    res = verifier.verify_candidate(cand, profile=profile, task=task)
    assert res.is_verified is True
    assert res.verified_question is not None
    assert res.verified_question.subject == "物理"


# ==============================================================================
# Persona 6: 高等教育研究生（北京大学，理学门类，自然地理学）
# ==============================================================================


def test_persona_6_higher_education_graduate(tmp_path: Path) -> None:
    """Persona 6: Graduate student at Peking University in Earth Sciences."""
    profile = LearnerProfile(
        education_type=EducationType.HIGHER,
        higher=HigherEducationProfile(
            school="北京大学",
            education_level="研究生",
            discipline_category="07 理学",
            discipline_first_level="0705 地理学",
        ),
    )
    profile.validate_isolation()
    task = TrainingTask(training_type="题型族", target="自然过程分析类", subject="地理")

    verifier, cand = _local_candidate(
        tmp_path,
        title="2026年北京大学自然地理学研究生范围校验材料",
        question_text="依据本地原文，结合冰川消融数据分析冰前河流地貌演化过程。",
        subject="地理",
        applicable_scope="高等教育 · 研究生",
        school_name="北京大学",
    )
    res = verifier.verify_candidate(cand, profile=profile, task=task)
    assert res.is_verified is True
    assert res.verified_question is not None
    assert res.verified_question.applicable_scope == "高等教育 · 研究生"


# ==============================================================================
# Persona 7: 恶意/异常用户——虚构大学（中国邮电大学）输入被拒
# ==============================================================================


def test_persona_7_malicious_user_fake_school() -> None:
    """Persona 7: Malicious/Fake user providing non-existent '中国邮电大学'."""
    profile = LearnerProfile(
        education_type=EducationType.HIGHER,
        higher=HigherEducationProfile(
            school="中国邮电大学",
            education_level="本科",
            major="软件工程",
        ),
    )
    verifier = QuestionSourceVerifier()
    task = TrainingTask(training_type="方法族", target="图像分析", subject="物理")

    cand = QuestionSourceCandidate(
        question_text="大学物理试卷第1题：分析振动图像求周期与波长...",
        source_name="中国邮电大学期末试卷",
        source_url="https://example.com/fake_exam.pdf",
        year=2024,
        exam_or_contest_name="中国邮电大学试卷",
        question_number="1",
        subject="物理",
        school_name="中国邮电大学",
    )
    res = verifier.verify_candidate(cand, profile=profile, task=task)
    assert res.status == VerificationStatus.REJECTED
    assert "中国邮电大学" in str(res.reason)


# ==============================================================================
# Persona 8: 恶意/异常用户——虚构比赛（不存在比赛）输入被拒
# ==============================================================================


def test_persona_8_malicious_user_fake_contest() -> None:
    """Persona 8: Fake question claiming non-existent competition '不存在比赛'."""
    verifier = QuestionSourceVerifier()
    cand = QuestionSourceCandidate(
        question_text="不存在比赛高中数学邀请赛第1题：求函数极值点分布规律...",
        source_name="不存在比赛组委会官方卷",
        source_url="https://example.org/contest/fake_contest_q1.pdf",
        year=2024,
        exam_or_contest_name="不存在比赛决赛试题",
        question_number="1",
        subject="数学",
        contest_name="不存在比赛",
    )
    res = verifier.verify_candidate(cand)
    assert res.status == VerificationStatus.REJECTED
    assert "不存在比赛" in str(res.reason)


# ==============================================================================
# Persona 9: 当前 staging 真实题量（不足5题时不得补齐）
# ==============================================================================


def test_persona_9_returned_count_matches_real_staging_sources() -> None:
    """Return only locally verifiable staging questions, including a legitimate zero."""
    if not STAGING_8511.is_file():
        pytest.skip("8511 database not found")

    db = Database(STAGING_8511)
    retrieval_service = QuestionSourceRetrievalService(db)
    task = TrainingTask(
        training_type="题型族",
        target="某区域分析类",
        family_id=114,
        subject="地理",
    )

    result = retrieval_service.retrieve_local_only(task=task, limit=5)
    expected_returned = result.total_available_verified
    assert result.verified_count == expected_returned
    assert len(result.questions) == expected_returned
    for question in result.questions:
        _assert_verified_provenance(question)


# ==============================================================================
# Persona 10: 21/5/4/2/1/0题合同场景（不限量，严禁AI凑数）
# ==============================================================================


@pytest.mark.parametrize("available_count", [21, 5, 4, 2, 1, 0])
def test_persona_10_real_count_contract(
    tmp_path: Path,
    available_count: int,
) -> None:
    """Return every locally verified question, even when there are more than 20."""

    database, task = _seed_local_questions(tmp_path, available_count=available_count)
    service = QuestionSourceRetrievalService(database)
    result = service.retrieve_local_only(task=task, limit=5)

    assert result.total_available_verified == available_count
    assert result.verified_count == available_count
    assert len(result.questions) == available_count
    assert result.total_candidates == available_count
    if available_count == 0:
        assert result.can_retry is True
        assert result.prompt_message == (
            "本地资料库暂未找到可核验的变式训练题；可以先导入其他试卷，再重新本地查询。"
        )
    else:
        assert f"找到 {available_count} 道" in result.prompt_message
        assert "不足" not in result.prompt_message
        assert result.can_refresh is False
    for question in result.questions:
        _assert_verified_provenance(question)
        assert question.source_url.startswith("pages/3_浏览资料.py?")
        assert question.applicable_scope == "基础教育 · 普通"


# ==============================================================================
# Persona 11: 零题目场景（无可信题源，坚守标准不降级）
# ==============================================================================


def test_persona_11_zero_questions_scenario(tmp_path: Path) -> None:
    """Persona 11: Zero questions found; display prompt and enable retry button."""
    test_db = tmp_path / "zero_test.db"
    db = Database(test_db)
    service = QuestionSourceRetrievalService(db)

    task = TrainingTask(
        training_type="题型族",
        target="完全不存在且无题源的虚构题型",
        subject="未知学科",
    )

    result = service.retrieve_local_only(task=task, limit=5)
    expected_msg = "本地资料库暂未找到可核验的变式训练题；可以先导入其他试卷，再重新本地查询。"
    assert expected_msg == result.prompt_message
    assert result.verified_count == 0
    assert result.total_available_verified == 0
    assert result.questions == []
    assert result.can_retry is True


def test_family_seed_question_is_not_returned_as_its_own_variation(
    tmp_path: Path,
) -> None:
    """A one-paper family has zero local variations, never the original itself."""

    database = Database(tmp_path / "one-paper" / "knowledge.db")
    raw_path = tmp_path / "one-paper.pdf"
    raw_path.write_bytes(b"%PDF-1.7\ntrusted-local-source")
    image_path = tmp_path / "one-paper.png"
    image_path.write_bytes(b"local-page-image")
    document = database.create_document(
        title="2026年七年级数学练习",
        filename=raw_path.name,
        source_path=raw_path,
        sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        page_count=1,
        import_status="completed",
    )
    page = database.create_page(
        document_id=document.id,
        page_number=1,
        image_path=image_path,
        extracted_text=(
            "计算：-1^2021×[4-(-3)^2]。\n"
            "乘方底数辨析：计算 (-1)^2022 与 -1^2022 的区别。"
        ),
        status="ready",
    )
    question = QuestionService(database).create_question_item(
        document_id=document.id,
        page_id=page.id,
        question_kind="error",
        question_number="20(2)",
        stem_text="计算：-1^2021×[4-(-3)^2]。",
        stem_confidence="confirmed",
        subject="数学",
    )
    from src.learning_workflow_service import QuestionOrganizationService

    organization = QuestionOrganizationService(database)
    family_id = organization.create_family(
        family_kind="method", title="乘方底数辨析"
    )
    organization.assign_to_family(question.id, family_id)
    QuestionService(database).create_question_item(
        document_id=document.id,
        page_id=page.id,
        question_kind="typical",
        question_number="20(3)",
        stem_text="乘方底数辨析：计算 (-1)^2022 与 -1^2022 的区别。",
        stem_confidence="confirmed",
        subject="数学",
    )
    task = TrainingTask(
        training_type="方法族",
        target="乘方底数辨析",
        family_id=family_id,
        subject="数学",
    )

    result = QuestionSourceRetrievalService(database).retrieve_local_only(
        task=task, limit=5
    )

    assert result.questions == []
    assert result.verified_count == 0
    assert result.total_available_verified == 0
    assert result.prompt_message == (
        "本地资料库暂未找到可核验的变式训练题；可以先导入其他试卷，再重新本地查询。"
    )


def test_local_source_file_must_still_match_import_hash(tmp_path: Path) -> None:
    """An indexed question is not usable if its imported original changed."""
    database, task = _seed_local_questions(tmp_path, available_count=1)
    service = QuestionSourceRetrievalService(database)
    assert service.retrieve_local_only(task=task).verified_count == 1

    raw_path = tmp_path / "阶段3本地证据材料-1.pdf"
    raw_path.write_bytes(b"%PDF-1.7\nchanged-after-import")
    result = service.retrieve_local_only(task=task)
    assert result.verified_count == 0
    assert result.questions == []


# ==============================================================================
# Persona 12: 旧分页参数不应截断可核验的本地题
# ==============================================================================


def test_persona_12_refresh_questions_scenario() -> None:
    """Persona 12: Legacy pagination arguments do not cap local results."""
    if not STAGING_8511.is_file():
        pytest.skip("8511 database not found")

    db = Database(STAGING_8511)
    retrieval_service = QuestionSourceRetrievalService(db)
    task = TrainingTask(
        training_type="题型族",
        target="某区域分析类",
        family_id=114,
        subject="地理",
    )

    res1 = retrieval_service.retrieve_local_only(
        task=task,
        offset=0,
        limit=5,
    )
    assert res1.verified_count == res1.total_available_verified
    assert res1.can_refresh is False
    for question in res1.questions:
        _assert_verified_provenance(question)
    if res1.total_available_verified > 5:
        # 旧偏移量被忽略；同一批真实题不得被分页或凑数改变。
        res2 = retrieval_service.retrieve_local_only(
            task=task, offset=5, limit=5
        )
        assert [q.id for q in res2.questions] == [q.id for q in res1.questions]
        for q in res2.questions:
            assert q.verification_status == VerificationStatus.VERIFIED


# ==============================================================================
# Persona 13: 来源链接失效/模糊测试（搜索引擎、根域名、模糊锚点全部拦截）
# ==============================================================================


def test_persona_13_vague_and_search_urls_rejected() -> None:
    """Persona 13: Search engine links, root domains, and unlocatable URLs must be rejected."""
    verifier = QuestionSourceVerifier()

    test_cases = [
        ("https://www.baidu.com/s?wd=高考物理原题", "搜索引擎"),
        ("https://www.google.com/search?q=geography+exam", "搜索引擎"),
        ("https://www.bing.com/search?q=动量定理", "搜索引擎"),
        ("https://gaokao.chsi.com.cn", "根域名或首页"),
        ("http://www.moe.gov.cn/", "根域名或首页"),
    ]
    for url, expected_err in test_cases:
        cand = QuestionSourceCandidate(
            question_text="测试题目题干文本内容详情分析...",
            source_name="测试来源",
            source_url=url,
            year=2024,
            exam_or_contest_name="测试试卷",
            question_number="1",
            subject="地理",
        )
        res = verifier.verify_candidate(cand)
        assert res.status == VerificationStatus.REJECTED
        assert expected_err in str(res.reason)


# ==============================================================================
# Persona 14: 跨学科隔离测试（地理训练任务输入物理题源，被拒并提示学科不匹配）
# ==============================================================================


def test_persona_14_cross_discipline_isolation() -> None:
    """Persona 14: Geography task rejects physics questions and vice versa."""
    verifier = QuestionSourceVerifier()
    geo_task = TrainingTask(training_type="题型族", target="某区域分析类", subject="地理")

    phy_cand = QuestionSourceCandidate(
        question_text="如图所示，质量为 m 的滑块在光滑水平面上与质量为 M 的小车发生弹性碰撞...",
        source_name="高考物理新课标卷",
        source_url="https://gaokao.neea.edu.cn/archive/physics_q25.pdf",
        year=2024,
        exam_or_contest_name="2024年高考物理新课标卷",
        question_number="25",
        subject="物理",
    )
    res = verifier.verify_candidate(phy_cand, task=geo_task)
    assert res.status == VerificationStatus.REJECTED
    assert "学科不匹配" in str(res.reason)


# ==============================================================================
# Persona 15: 普通题保护测试（竞赛生导入普通高考试卷，系统严格识别为普通题，防止虚构升级）
# ==============================================================================


def test_persona_15_regular_question_protection(tmp_path: Path) -> None:
    """Persona 15: Standard exam question uploaded by contest student remains '基础教育 · 普通'."""
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
            competition_subjects={"物理": "全国中学生物理竞赛决赛备战阶段（CPhO 国决）"},
        ),
    )
    profile.validate_isolation()
    verifier, cand = _local_candidate(
        tmp_path,
        title="2026年高中物理期中范围校验材料",
        question_text="依据本地原文，求电子的初速度大小与极板间电压大小。",
        subject="物理",
        applicable_scope="基础教育 · 竞赛",
    )
    res = verifier.verify_candidate(cand, profile=profile)
    assert res.is_verified is True
    assert res.verified_question is not None
    # 严格保持并修正为普通题，防止虚构升级为竞赛题
    assert res.verified_question.applicable_scope == "基础教育 · 普通"


def test_persona_16_region_stage_and_permission_isolation() -> None:
    """Reject cross-region profiles and privileged sources for a regular student."""

    with pytest.raises(ProfileValidationError, match="不属于省份 江苏省"):
        invalid_region_profile = LearnerProfile(
            education_type=EducationType.BASIC,
            basic=BasicEducationProfile(
                province="江苏省",
                province_code="32",
                city="北京市",
                city_code="1101",
                stage="初中",
                grade="初一",
            ),
        )
        invalid_region_profile.validate_isolation()

    regular_profile = LearnerProfile(
        education_type=EducationType.BASIC,
        basic=BasicEducationProfile(
            province="江苏省",
            province_code="32",
            city="南京市",
            city_code="3201",
            stage="初中",
            grade="初一",
        ),
    )
    regular_profile.validate_isolation()
    with pytest.raises(ProfileValidationError, match="高等教育数据必须完全清空"):
        mixed_stage_profile = LearnerProfile(
            education_type=EducationType.BASIC,
            basic=regular_profile.basic,
            higher=HigherEducationProfile(
                school="清华大学",
                education_level="本科",
                major="物理学",
            ),
        )
        mixed_stage_profile.validate_isolation()

    task = TrainingTask(training_type="题型族", target="数学综合", subject="数学")
    verifier = QuestionSourceVerifier()
    privileged_candidates = (
        QuestionSourceCandidate(
            question_text="强基计划范围校验候选题，必须在普通用户边界处拒绝。",
            source_name="拒绝路径夹具",
            source_url="https://invalid.example/strong-base.pdf",
            year=2026,
            exam_or_contest_name="南京大学强基计划校考范围校验",
            question_number="1",
            subject="数学",
            applicable_scope="基础教育 · 强基",
            school_name="南京大学",
            major_direction="数学与应用数学",
        ),
        QuestionSourceCandidate(
            question_text="竞赛范围校验候选题，必须在普通用户边界处拒绝。",
            source_name="拒绝路径夹具",
            source_url="https://invalid.example/competition.pdf",
            year=2026,
            exam_or_contest_name="全国高中数学联赛范围校验",
            question_number="1",
            subject="数学",
            applicable_scope="基础教育 · 竞赛",
            contest_name="全国高中数学联赛",
            contest_tier="省级",
        ),
    )

    for candidate in privileged_candidates:
        result = verifier.verify_candidate(
            candidate,
            profile=regular_profile,
            task=task,
        )
        assert result.status == VerificationStatus.REJECTED
        assert result.verified_question is None
