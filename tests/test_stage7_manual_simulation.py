"""Stage 7 Manual Simulation Tests (10 Personas: 31 to 40).

Simulates end-to-end user workflows for Phase 7 (Learning Report & Review Queue):
1.  Persona 31: 用户导出学习报告（Markdown 正确生成，包含所有指标、题源链接与错因笔记）
2.  Persona 32: 用户查看历史报告（快照持久化并能重新完整调阅）
3.  Persona 33: 待加强用户（进入复习队列，优先级为高，1天间隔排期，提示近期失误）
4.  Persona 34: 阶段掌握用户（复习频率降低，14天排期，优先级为低）
5.  Persona 35: 新用户（零数据防幻觉保护，拒绝虚构错因画像与复习任务）
6.  Persona 36: 大量历史训练用户（多次训练记录准确聚合，统计与追溯准确，无数据丢失）
7.  Persona 37: 强基用户报告（强基高校与专业标签完整保留在报告与凭证中）
8.  Persona 38: 竞赛用户报告（竞赛名称与阶段标签完整保留在报告与凭证中）
9.  Persona 39: 大学专业用户报告（高等教育专业信息保留在报告中）
10. Persona 40: 跨学科用户（地理与物理报告及复习队列严格隔离，互不干扰）
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.database import Database
from src.error_analysis_service import ErrorAnalysisService
from src.learning_report_service import LearningReportService
from src.question_source_models import VerifiedQuestion
from src.review_queue_models import ReviewPriority
from src.review_queue_service import ReviewQueueService
from src.targeted_training_models import TrainingTask
from src.training_profile_models import (
    BasicEducationProfile,
    HigherEducationProfile,
)
from src.training_profile_service import TrainingProfileService
from src.training_session_models import ErrorType
from src.training_session_service import TrainingSessionService


@pytest.fixture
def stage7_db() -> Database:
    """Create a temporary SQLite database initialized with schemas."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path)
    now = datetime.now(UTC).isoformat()
    with db._connection() as conn:
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
            INSERT OR REPLACE INTO question_items
            (id, question_kind, stem_text, created_at, updated_at)
            VALUES (85111, 'typical', '2024年江苏高考地理第23题', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO question_items
            (id, question_kind, stem_text, created_at, updated_at)
            VALUES (85121, 'typical', '2024年高考物理新课标卷第24题', ?, ?)
            """,
            (now, now),
        )
    return db


@pytest.fixture
def profile_service(stage7_db: Database) -> TrainingProfileService:
    return TrainingProfileService(stage7_db.database_path)


@pytest.fixture
def error_service(stage7_db: Database) -> ErrorAnalysisService:
    return ErrorAnalysisService(stage7_db)


@pytest.fixture
def session_service(
    stage7_db: Database, error_service: ErrorAnalysisService
) -> TrainingSessionService:
    return TrainingSessionService(stage7_db, error_analysis_service=error_service)


@pytest.fixture
def report_service(
    stage7_db: Database,
    session_service: TrainingSessionService,
    error_service: ErrorAnalysisService,
    profile_service: TrainingProfileService,
) -> LearningReportService:
    return LearningReportService(
        stage7_db,
        session_service=session_service,
        error_service=error_service,
        profile_service=profile_service,
    )


@pytest.fixture
def queue_service(
    stage7_db: Database, error_service: ErrorAnalysisService
) -> ReviewQueueService:
    return ReviewQueueService(stage7_db, error_service=error_service)


def _build_authentic_question(
    qid: str,
    subject: str,
    target: str,
    scope: str = "基础教育 · 普通",
    school_name: str | None = None,
    major_direction: str | None = None,
    contest_name: str | None = None,
    contest_tier: str | None = None,
) -> VerifiedQuestion:
    """Build an authentic VerifiedQuestion for simulated test roles."""
    return VerifiedQuestion(
        id=qid,
        question_text=f"真实试题题干：{subject} - {target}",
        source_name=f"2024年官方考试真题（{subject}）",
        source_url=f"https://gaokao.neea.edu.cn/archive/2024/{qid}.pdf",
        year=2024,
        exam_or_contest_name=f"2024年全国统一考试（{subject}）",
        question_number="1",
        subject=subject,
        applicable_scope=scope,
        school_name=school_name,
        major_direction=major_direction,
        contest_name=contest_name,
        contest_tier=contest_tier,
        reference_answer="官方公布的标准参考解答",
        family_id=8512 if subject == "物理" else 8511,
        question_item_id=85121 if subject == "物理" else 85111,
    )


# ==============================================================================
# Persona 31: 用户导出学习报告（Markdown 正确生成，包含所有指标、题源链接与错因笔记）
# ==============================================================================


def test_persona_31_export_learning_report(
    profile_service: TrainingProfileService,
    session_service: TrainingSessionService,
    report_service: LearningReportService,
    tmp_path: Path,
) -> None:
    user_id = "p31_export_user"
    subject = "物理"
    target = "动量定理"

    # 1. Setup profile
    profile_service.save_basic_profile(
        BasicEducationProfile(
            province="江苏省",
            province_code="32",
            city="南京市",
            city_code="3201",
            stage="高中",
            grade="高三",
        )
    )

    # 2. Complete training session
    q1 = _build_authentic_question("p31_q1", subject, target)
    q2 = _build_authentic_question("p31_q2", subject, target)
    task = TrainingTask(training_type="方法族", target=target, subject=subject, family_id=8512)
    session = session_service.create_session(task, questions=[q1, q2], user_id=user_id)

    # Q1: Failed
    session_service.evaluate_and_record_attempt(
        session.id,
        q1.id,
        "速度方向选反了，结果带负号",
        is_correct=False,
        error_type=ErrorType.CALCULATION,
        error_analysis="未规定正方向导致矢量冲量符号混淆",
    )
    # Q2: Correct
    session_service.advance_question(session.id, 1)
    session_service.evaluate_and_record_attempt(
        session.id,
        q2.id,
        "取向右为正，I = m*v2 - m*v1",
        is_correct=True,
    )
    session_service.complete_session(session.id)

    # 3. Generate report
    report = report_service.generate_report(user_id, subject, target=target)
    assert report.has_sufficient_data is True
    assert report.total_sessions == 1
    assert report.total_attempts == 2
    assert report.correct_count == 1
    assert report.error_count == 1
    assert report.accuracy_rate == 50.0

    # 4. Export Markdown file
    out_file = tmp_path / "export" / "p31_physics_report.md"
    md_content = report_service.export_markdown(report, output_path=out_file)

    assert out_file.exists()
    assert "# Nectivon 针对训练学习报告" in md_content
    assert "江苏省 南京市 · 高中 高三" in md_content
    assert "50.0%" in md_content
    assert "未规定正方向导致矢量冲量符号混淆" in md_content
    assert q1.source_url in md_content
    assert q2.source_url in md_content


# ==============================================================================
# Persona 32: 用户查看历史报告（快照持久化并能重新完整调阅）
# ==============================================================================


def test_persona_32_historical_report_persistence_and_retrieval(
    session_service: TrainingSessionService,
    report_service: LearningReportService,
) -> None:
    user_id = "p32_history_user"
    subject = "地理"
    target = "综合地理区域分析"

    # Setup session & attempts
    q = _build_authentic_question("p32_q1", subject, target)
    task = TrainingTask(training_type="题型族", target=target, subject=subject, family_id=8511)
    session = session_service.create_session(task, questions=[q], user_id=user_id)
    session_service.evaluate_and_record_attempt(
        session.id, q.id, "地形对降水空间分布有显著屏障阻隔作用", is_correct=True
    )
    session_service.complete_session(session.id)

    # Generate and explicitly save snapshot
    report = report_service.generate_report(user_id, subject, target=target)
    report_id = report_service.save_report(report)
    assert report_id == report.report_id

    # Retrieve by ID
    loaded_report = report_service.get_report(report_id)
    assert loaded_report is not None
    assert loaded_report.report_id == report.report_id
    assert loaded_report.subject == "地理"
    assert loaded_report.accuracy_rate == 100.0
    assert len(loaded_report.question_evidences) == 1
    assert loaded_report.question_evidences[0].question_id == "p32_q1"

    # List historical reports
    history_list = report_service.list_historical_reports(user_id, subject)
    assert len(history_list) >= 1
    assert history_list[0].report_id == report_id


# ==============================================================================
# Persona 33: 待加强用户（进入复习队列，优先级为高，1天间隔排期，提示近期失误）
# ==============================================================================


def test_persona_33_needs_improvement_review_queue(
    error_service: ErrorAnalysisService,
    queue_service: ReviewQueueService,
) -> None:
    user_id = "p33_needs_imp_user"
    subject = "地理"
    target = "流水侵蚀地貌演化"

    # Error occurrence sets state to NEEDS_IMPROVEMENT
    error_service.update_mastery_state(
        user_id=user_id,
        subject=subject,
        target=target,
        is_correct=False,
        error_type=ErrorType.CONCEPT.value,
        family_id=8511,
    )

    items = queue_service.build_or_update_review_queue(user_id, subject)
    assert len(items) == 1
    item = items[0]
    assert item.target == target
    assert item.review_priority == ReviewPriority.HIGH
    assert item.review_interval_days == 1
    assert "近期出现失误" in item.review_reason or "针对巩固" in item.review_reason


# ==============================================================================
# Persona 34: 阶段掌握用户（复习频率降低，14天排期，优先级为低）
# ==============================================================================


def test_persona_34_stage_mastered_review_queue(
    error_service: ErrorAnalysisService,
    queue_service: ReviewQueueService,
) -> None:
    user_id = "p34_mastered_user"
    subject = "物理"
    target = "动量守恒应用"

    # Two consecutive correct answers achieve STAGE_MASTERED
    error_service.update_mastery_state(user_id, subject, target, is_correct=True, family_id=8512)
    error_service.update_mastery_state(user_id, subject, target, is_correct=True, family_id=8512)

    items = queue_service.build_or_update_review_queue(user_id, subject)
    assert len(items) == 1
    item = items[0]
    assert item.target == target
    assert item.review_priority == ReviewPriority.LOW
    assert item.review_interval_days == 14
    assert "降低复习频率" in item.review_reason or "长效巩固" in item.review_reason


# ==============================================================================
# Persona 35: 新用户（零数据防幻觉保护，拒绝虚构错因画像与复习任务）
# ==============================================================================


def test_persona_35_zero_data_protection_new_user(
    report_service: LearningReportService,
    queue_service: ReviewQueueService,
) -> None:
    new_user_id = "p35_brand_new_user"

    # Generate report for unattempted subject
    report = report_service.generate_report(new_user_id, "地理")
    assert report.has_sufficient_data is False
    assert report.total_attempts == 0
    assert report.total_sessions == 0
    assert "暂无足够学习数据" in report.message

    # Markdown export should contain caution prompt and no fake statistics
    md = report.to_markdown()
    assert "暂无足够学习数据" in md or "暂无足够训练数据" in md
    assert "薄弱知识点" not in md

    # Summary card should be empty
    summary = queue_service.get_learning_status_summary(new_user_id, "地理")
    assert summary.has_sufficient_data is False
    assert summary.total_items == 0


# ==============================================================================
# Persona 36: 大量历史训练用户（多次训练记录准确聚合，统计与追溯准确，无数据丢失）
# ==============================================================================


def test_persona_36_abundant_history_aggregation(
    session_service: TrainingSessionService,
    report_service: LearningReportService,
) -> None:
    user_id = "p36_heavy_user"
    subject = "地理"
    target = "综合地理区域分析"

    # Complete 3 sessions with 2 questions each = 6 attempts
    for i in range(3):
        q_a = _build_authentic_question(f"p36_s{i}_qa", subject, target)
        q_b = _build_authentic_question(f"p36_s{i}_qb", subject, target)
        task = TrainingTask(training_type="题型族", target=target, subject=subject, family_id=8511)
        sess = session_service.create_session(task, questions=[q_a, q_b], user_id=user_id)

        # First question correct, second question error
        session_service.evaluate_and_record_attempt(sess.id, q_a.id, "分析正确", is_correct=True)
        session_service.advance_question(sess.id, 1)
        session_service.evaluate_and_record_attempt(
            sess.id,
            q_b.id,
            "分析有疏漏",
            is_correct=False,
            error_type=ErrorType.READING,
            error_analysis="未看清图示等高线密集程度",
        )
        session_service.complete_session(sess.id)

    report = report_service.generate_report(user_id, subject)
    assert report.has_sufficient_data is True
    assert report.total_sessions == 3
    assert report.total_attempts == 6
    assert report.correct_count == 3
    assert report.error_count == 3
    assert report.accuracy_rate == 50.0
    assert len(report.question_evidences) == 6

    # Verify each evidence has complete provenance
    for ev in report.question_evidences:
        assert ev.source_name.startswith("2024年官方考试真题")
        assert ev.source_url.startswith("https://gaokao.neea.edu.cn")


# ==============================================================================
# Persona 37: 强基用户报告（强基高校与专业标签完整保留在报告与凭证中）
# ==============================================================================


def test_persona_37_strong_base_user_report(
    profile_service: TrainingProfileService,
    session_service: TrainingSessionService,
    report_service: LearningReportService,
) -> None:
    user_id = "p37_strong_base_user"
    subject = "物理"
    target = "动量守恒"

    profile_service.save_basic_profile(
        BasicEducationProfile(
            province="江苏省",
            province_code="32",
            city="南京市",
            city_code="3201",
            stage="高中",
            grade="高三",
            in_strong_base=True,
            strong_base_school="南京大学",
            strong_base_subject="物理学",
        )
    )

    q = _build_authentic_question(
        "p37_q1",
        subject,
        target,
        scope="强基计划 · 笔试深化",
        school_name="南京大学",
        major_direction="物理学",
    )
    task = TrainingTask(training_type="方法族", target=target, subject=subject, family_id=8512)
    session = session_service.create_session(task, questions=[q], user_id=user_id)
    session_service.evaluate_and_record_attempt(session.id, q.id, "强基解答", is_correct=True)
    session_service.complete_session(session.id)

    report = report_service.generate_report(user_id, subject)
    assert report.has_sufficient_data is True
    assert report.user_profile_summary.get("in_strong_base") is True
    assert report.user_profile_summary.get("strong_base_school") == "南京大学"
    assert report.user_profile_summary.get("strong_base_subject") == "物理学"

    ev = report.question_evidences[0]
    assert ev.applicable_scope == "强基计划 · 笔试深化"
    assert ev.school_name == "南京大学"
    assert ev.major_direction == "物理学"


# ==============================================================================
# Persona 38: 竞赛用户报告（竞赛名称与阶段标签完整保留在报告与凭证中）
# ==============================================================================


def test_persona_38_contest_user_report(
    profile_service: TrainingProfileService,
    session_service: TrainingSessionService,
    report_service: LearningReportService,
) -> None:
    user_id = "p38_contest_user"
    subject = "物理"
    target = "动量守恒"

    profile_service.save_basic_profile(
        BasicEducationProfile(
            province="江苏省",
            province_code="32",
            city="南京市",
            city_code="3201",
            stage="高中",
            grade="高三",
            in_competition=True,
            competition_subjects={"物理": "全国中学生物理竞赛复赛备战阶段（省赛/省队选拔）"},
        )
    )

    q = _build_authentic_question(
        "p38_q1",
        subject,
        target,
        scope="学科竞赛 · 强化提高",
        contest_name="第41届全国中学生物理竞赛预赛",
        contest_tier="省一/省队",
    )
    task = TrainingTask(training_type="方法族", target=target, subject=subject, family_id=8512)
    session = session_service.create_session(task, questions=[q], user_id=user_id)
    session_service.evaluate_and_record_attempt(session.id, q.id, "竞赛推演", is_correct=True)
    session_service.complete_session(session.id)

    report = report_service.generate_report(user_id, subject)
    assert report.has_sufficient_data is True
    assert report.user_profile_summary.get("in_competition") is True
    assert report.user_profile_summary.get("competition_subjects") == {
        "物理": "全国中学生物理竞赛复赛备战阶段（省赛/省队选拔）"
    }

    ev = report.question_evidences[0]
    assert ev.applicable_scope == "学科竞赛 · 强化提高"
    assert ev.contest_name == "第41届全国中学生物理竞赛预赛"
    assert ev.contest_tier == "省一/省队"


# ==============================================================================
# Persona 39: 大学专业用户报告（高等教育专业信息保留在报告中）
# ==============================================================================


def test_persona_39_higher_education_report(
    profile_service: TrainingProfileService,
    session_service: TrainingSessionService,
    report_service: LearningReportService,
) -> None:
    user_id = "p39_higher_user"
    subject = "工学"
    target = "状态空间极点配置"

    profile_service.save_higher_profile(
        HigherEducationProfile(
            school="东南大学",
            education_level="本科",
            major="自动化",
        )
    )

    q = _build_authentic_question(
        "p39_q1",
        subject,
        target,
        scope="高等教育 · 专业核心",
        school_name="东南大学",
        major_direction="控制科学与工程",
    )
    task = TrainingTask(training_type="方法族", target=target, subject=subject, family_id=8512)
    session = session_service.create_session(task, questions=[q], user_id=user_id)
    session_service.evaluate_and_record_attempt(
        session.id, q.id, "极点配置与Ackermann公式", is_correct=True
    )
    session_service.complete_session(session.id)

    report = report_service.generate_report(user_id, subject)
    assert report.has_sufficient_data is True
    assert report.education_type == "高等教育"
    assert report.user_profile_summary.get("school_name") == "东南大学"
    assert report.user_profile_summary.get("level") == "本科"
    assert report.user_profile_summary.get("major_name") == "自动化"

    ev = report.question_evidences[0]
    assert ev.applicable_scope == "高等教育 · 专业核心"
    assert ev.school_name == "东南大学"


# ==============================================================================
# Persona 40: 跨学科用户（地理与物理报告及复习队列严格隔离，互不干扰）
# ==============================================================================


def test_persona_40_cross_discipline_isolation(
    session_service: TrainingSessionService,
    error_service: ErrorAnalysisService,
    report_service: LearningReportService,
    queue_service: ReviewQueueService,
) -> None:
    user_id = "p40_multi_subject_user"

    # 1. Geographic training (1 failed attempt)
    q_geo = _build_authentic_question("p40_geo_1", "地理", "自然过程分析")
    task_geo = TrainingTask(
        training_type="题型族", target="自然过程分析", subject="地理", family_id=8511
    )
    sess_geo = session_service.create_session(task_geo, questions=[q_geo], user_id=user_id)
    session_service.evaluate_and_record_attempt(
        sess_geo.id,
        q_geo.id,
        "地质演化顺序判断错误",
        is_correct=False,
        error_type=ErrorType.CONCEPT,
        error_analysis="将侵蚀阶段与堆积阶段颠倒",
    )
    session_service.complete_session(sess_geo.id)

    # 2. Physics training (1 correct attempt)
    q_phy = _build_authentic_question("p40_phy_1", "物理", "动量定理")
    task_phy = TrainingTask(
        training_type="方法族", target="动量定理", subject="物理", family_id=8512
    )
    sess_phy = session_service.create_session(task_phy, questions=[q_phy], user_id=user_id)
    session_service.evaluate_and_record_attempt(
        sess_phy.id, q_phy.id, "冲量公式 I = F * t", is_correct=True
    )
    session_service.complete_session(sess_phy.id)

    # Update mastery states
    error_service.update_mastery_state(
        user_id, "地理", "自然过程分析", is_correct=False, family_id=8511
    )
    error_service.update_mastery_state(
        user_id, "物理", "动量定理", is_correct=True, family_id=8512
    )

    # 3. Check Geography report
    geo_report = report_service.generate_report(user_id, "地理")
    assert geo_report.total_attempts == 1
    assert geo_report.error_count == 1
    assert geo_report.correct_count == 0
    assert geo_report.accuracy_rate == 0.0
    assert geo_report.question_evidences[0].subject == "地理"

    # 4. Check Physics report
    phy_report = report_service.generate_report(user_id, "物理")
    assert phy_report.total_attempts == 1
    assert phy_report.error_count == 0
    assert phy_report.correct_count == 1
    assert phy_report.accuracy_rate == 100.0
    assert phy_report.question_evidences[0].subject == "物理"

    # 5. Check review queue isolation
    geo_queue = queue_service.get_review_queue(user_id, "地理")
    phy_queue = queue_service.get_review_queue(user_id, "物理")

    assert len(geo_queue) == 1
    assert geo_queue[0].subject == "地理"
    assert geo_queue[0].review_priority == ReviewPriority.HIGH

    assert len(phy_queue) == 1
    assert phy_queue[0].subject == "物理"
    assert phy_queue[0].review_priority == ReviewPriority.LOW
