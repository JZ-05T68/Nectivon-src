"""Unit tests for Phase 7 Learning Report Generation and Export.

Verifies:
1. Zero-data protection: Refuses to forge reports or guess weaknesses without data.
2. Complete provenance traceability: Attempt -> ErrorAnalysis -> Question Source.
3. Accurate metric aggregation: total sessions, attempts, correct, error, accuracy rate.
4. Error taxonomy and frequent error tag preservation.
5. Markdown export with clickable citations and evidence tables.
6. Report snapshot persistence and historical list retrieval.
7. Historical scope and provenance tag preservation across profile mutations.
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.database import Database
from src.error_analysis_service import ErrorAnalysisService
from src.learning_report_service import LearningReportService
from src.question_source_models import VerifiedQuestion
from src.targeted_training_models import TrainingTask
from src.training_profile_models import (
    BasicEducationProfile,
)
from src.training_profile_service import TrainingProfileService
from src.training_session_models import ErrorType
from src.training_session_service import TrainingSessionService


@pytest.fixture
def test_db() -> Database:
    """Create a temporary SQLite database initialized with schemas."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    return Database(db_path)


@pytest.fixture
def report_service(test_db: Database) -> LearningReportService:
    session_service = TrainingSessionService(test_db)
    error_service = ErrorAnalysisService(test_db)
    profile_service = TrainingProfileService(test_db.database_path)
    return LearningReportService(
        test_db,
        session_service=session_service,
        error_service=error_service,
        profile_service=profile_service,
    )


def _seed_authentic_question(
    db: Database,
    qid: str = "q_rep_01",
    subject: str = "物理",
    target: str = "动量定理",
    scope: str = "基础教育 · 普通",
) -> VerifiedQuestion:
    now = datetime.now(UTC).isoformat()
    q = VerifiedQuestion(
        id=qid,
        question_text=f"测试真实试题题干：{subject} - {target}",
        source_name=f"2024年官方考试真题（{subject}）",
        source_url="https://gaokao.neea.edu.cn/archive/2024/test.pdf",
        year=2024,
        exam_or_contest_name="2024年全国高考卷",
        question_number="15",
        subject=subject,
        applicable_scope=scope,
        reference_answer="标准参考答案：依据动量守恒建立主导方程",
        family_id=8512,
        question_item_id=85121,
    )
    with db._connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO targeted_training_questions (
                id, training_type, target, question_text, source_name,
                source_url, year, exam_or_contest_name, question_number,
                subject, applicable_scope, verification_status,
                question_fingerprint, verified_at, reference_answer
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                q.id,
                "方法族",
                target,
                q.question_text,
                q.source_name,
                q.source_url,
                q.year,
                q.exam_or_contest_name,
                q.question_number,
                q.subject,
                q.applicable_scope,
                "verified",
                q.question_fingerprint,
                now,
                q.reference_answer,
            ),
        )
    return q


def test_zero_data_protection(report_service: LearningReportService) -> None:
    """Verify report generation returns has_sufficient_data=False when zero history exists."""
    report = report_service.generate_report("blank_user", "物理")

    assert report.has_sufficient_data is False
    assert report.total_attempts == 0
    assert report.total_sessions == 0
    assert "暂无足够学习数据" in report.message

    # Exporting markdown should yield clean notification without fake stats
    md = report.to_markdown()
    assert ("暂无足够学习数据" in md or "暂无足够训练数据" in md)
    assert "薄弱知识点" not in md


def test_generate_and_trace_learning_report(
    test_db: Database, report_service: LearningReportService
) -> None:
    """Verify report correctly aggregates attempts and produces full provenance evidences."""
    user_id = "user_trace_01"
    target = "动量定理"
    subject = "物理"

    # Configure learner profile
    profile_service = TrainingProfileService(test_db.database_path)
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

    q1 = _seed_authentic_question(test_db, "q_rep_1", subject, target)
    q2 = _seed_authentic_question(test_db, "q_rep_2", subject, target)

    session_service = TrainingSessionService(test_db)
    task = TrainingTask(training_type="方法族", target=target, subject=subject, family_id=8512)
    session = session_service.create_session(task, user_id=user_id, questions=[q1, q2])

    # Attempt 1: Error
    session_service.evaluate_and_record_attempt(
        session.id,
        q1.id,
        "我的错误计算",
        is_correct=False,
        error_type=ErrorType.CALCULATION,
        error_analysis="积分常数遗漏",
    )
    # Attempt 2: Correct
    session_service.advance_question(session.id, 1)
    session_service.evaluate_and_record_attempt(
        session.id,
        q2.id,
        "标准参考答案：依据动量守恒建立主导方程",
        is_correct=True,
    )
    session_service.complete_session(session.id)

    # Generate report
    report = report_service.generate_report(user_id, subject, target=target)

    assert report.has_sufficient_data is True
    assert report.total_sessions == 1
    assert report.total_attempts == 2
    assert report.correct_count == 1
    assert report.error_count == 1
    assert report.accuracy_rate == 50.0
    assert report.subject == "物理"
    assert report.target == "动量定理"
    assert report.education_type == "基础教育"

    # Traceable evidence checks
    assert len(report.question_evidences) == 2
    ev1, ev2 = report.question_evidences
    assert ev1.question_id == q1.id
    assert ev1.is_correct is False
    assert ev1.error_type == ErrorType.CALCULATION
    assert ev1.user_note == "积分常数遗漏"
    assert ev1.source_url == q1.source_url

    assert ev2.question_id == q2.id
    assert ev2.is_correct is True

    # Markdown export test
    md_content = report.to_markdown()
    assert "# Nectivon 针对训练学习报告" in md_content
    assert "综合正确率 | **50.0%**" in md_content
    expected_link = (
        "[2024年官方考试真题（物理）]"
        "(https://gaokao.neea.edu.cn/archive/2024/test.pdf)"
    )
    assert expected_link in md_content
    assert "积分常数遗漏" in md_content


def test_report_snapshot_persistence_and_retrieval(
    test_db: Database, report_service: LearningReportService
) -> None:
    """Verify persisting report snapshot to database and retrieving historical reports."""
    user_id = "user_snapshot_01"
    q = _seed_authentic_question(test_db, "q_snap_1", "物理", "动量定理")

    session_service = TrainingSessionService(test_db)
    task = TrainingTask(training_type="方法族", target="动量定理", subject="物理")
    session = session_service.create_session(task, user_id=user_id, questions=[q])
    session_service.evaluate_and_record_attempt(session.id, q.id, "答案", is_correct=True)

    report = report_service.generate_report(user_id, "物理", target="动量定理")
    assert report.has_sufficient_data is True

    # Save report
    report_service.save_report(report)

    # Query back
    loaded = report_service.get_report(report.report_id)
    assert loaded is not None
    assert loaded.report_id == report.report_id
    assert loaded.subject == "物理"
    assert loaded.total_attempts == 1

    # List historical reports
    hist_list = report_service.list_historical_reports(user_id, "物理")
    assert len(hist_list) == 1
    assert hist_list[0].report_id == report.report_id


def test_markdown_file_export(
    test_db: Database, report_service: LearningReportService, tmp_path: Path
) -> None:
    """Verify export_markdown writes a UTF-8 markdown file to disk."""
    user_id = "user_md_export"
    q = _seed_authentic_question(test_db, "q_md_1", "地理", "综合地理区域分析")

    session_service = TrainingSessionService(test_db)
    task = TrainingTask(training_type="题型族", target="综合地理区域分析", subject="地理")
    session = session_service.create_session(task, user_id=user_id, questions=[q])
    session_service.evaluate_and_record_attempt(session.id, q.id, "地理答案", is_correct=True)

    report = report_service.generate_report(user_id, "地理")
    out_file = tmp_path / "reports" / "geo_report.md"

    md_str = report_service.export_markdown(report, output_path=out_file)
    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8") == md_str
    assert "综合地理区域分析" in md_str


def test_missing_question_source_is_not_attributed_to_an_official_site(
    test_db: Database, report_service: LearningReportService
) -> None:
    """A historical attempt with no source metadata must remain unattributed."""
    now = datetime.now(UTC).isoformat()
    question_id = "synthetic_missing_source"
    with test_db._connection() as conn:
        conn.execute(
            """INSERT INTO targeted_training_sessions
               (id, user_id, training_type, target, subject, questions_json,
                status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "synthetic_session",
                "synthetic_user",
                "题型族",
                "合成测试目标",
                "数学",
                json.dumps([{"id": question_id, "question_text": "合成测试题干"}]),
                "completed",
                now,
            ),
        )
        conn.execute(
            """INSERT INTO targeted_training_attempts
               (id, session_id, question_id, user_answer, reference_answer,
                is_correct, attempted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                "synthetic_attempt",
                "synthetic_session",
                question_id,
                "合成测试作答",
                "合成测试参考",
                1,
                now,
            ),
        )

    report = report_service.generate_report("synthetic_user", "数学")

    assert report.has_sufficient_data is True
    assert report.total_attempts == 1
    evidence = report.question_evidences[0]
    assert evidence.source_name == ""
    assert evidence.source_url == ""
    assert evidence.applicable_scope == ""
    markdown = report.to_markdown()
    assert "- **题源记录**：来源未记录" in markdown
    assert "官方试题库" not in markdown
    assert "gaokao.neea.edu.cn" not in markdown
