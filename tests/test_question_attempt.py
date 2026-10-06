"""Unit tests for QuestionAttempt, answering interactions, and Layer 1 feedback (Phase 5)."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.database import Database
from src.question_source_models import VerificationStatus, VerifiedQuestion
from src.targeted_training_models import TrainingTask
from src.training_session_models import ErrorType
from src.training_session_service import TrainingSessionService, _generate_method_reinforcement


@pytest.fixture
def temp_db() -> Database:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path)
    now = datetime.now(UTC).isoformat()
    with db._connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO question_families
            (id, family_kind, title, created_at, updated_at)
            VALUES (201, 'method', '动量定理', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO question_items
            (id, question_kind, stem_text, created_at, updated_at)
            VALUES (85121, 'typical', '测试题干', ?, ?)
            """,
            (now, now),
        )
    return db


@pytest.fixture
def sample_task() -> TrainingTask:
    return TrainingTask(
        training_type="方法族",
        target="动量守恒与机械能守恒",
        subject="物理",
        family_id=201,
    )


@pytest.fixture
def sample_question() -> VerifiedQuestion:
    return VerifiedQuestion(
        id="vq_phys_attempt_1",
        question_text="【2024年高考物理新课标卷·第24题】质量为 m 的滑块冲上斜面...",
        source_name="2024年普通高等学校招生全国统一考试物理试题（新课标卷）",
        source_url="https://gaokao.neea.edu.cn/archive/2024/physics_q24.pdf",
        year=2024,
        exam_or_contest_name="2024年高考物理新课标卷",
        question_number="24",
        subject="物理",
        applicable_scope="基础教育 · 普通",
        verification_status=VerificationStatus.VERIFIED,
        reference_answer="m*v0 = (M + m)*v_共",
        question_item_id=85121,
        family_id=201,
    )


def test_empty_answer_interception(
    temp_db: Database, sample_task: TrainingTask, sample_question: VerifiedQuestion
) -> None:
    """Submitting empty or whitespace-only answers must be rejected with ValueError."""
    service = TrainingSessionService(temp_db)
    session = service.create_session(sample_task, [sample_question])

    with pytest.raises(ValueError, match="请先填写答案。"):
        service.evaluate_and_record_attempt(session.id, sample_question.id, "")

    with pytest.raises(ValueError, match="请先填写答案。"):
        service.evaluate_and_record_attempt(session.id, sample_question.id, "   ")

    with pytest.raises(ValueError, match="请先填写答案。"):
        service.evaluate_and_record_attempt(session.id, sample_question.id, "\n\t  \n")


def test_repeat_submission_counter(
    temp_db: Database, sample_task: TrainingTask, sample_question: VerifiedQuestion
) -> None:
    """Multiple submissions for the same question must increment submission_count."""
    service = TrainingSessionService(temp_db)
    session = service.create_session(sample_task, [sample_question])

    # First attempt (incorrect)
    att1 = service.evaluate_and_record_attempt(
        session.id,
        sample_question.id,
        user_answer="v = sqrt(2gh)",
        is_correct=False,
        error_type=ErrorType.CALCULATION,
        error_analysis="机械能不守恒计算错误",
    )
    assert att1.submission_count == 1
    assert att1.is_correct is False
    assert att1.error_type == ErrorType.CALCULATION

    # Second attempt (revised, correct)
    att2 = service.evaluate_and_record_attempt(
        session.id,
        sample_question.id,
        user_answer="m*v0 = (M + m)*v_共",
        is_correct=True,
    )
    assert att2.submission_count == 2
    assert att2.is_correct is True

    # Third attempt
    att3 = service.evaluate_and_record_attempt(
        session.id,
        sample_question.id,
        user_answer="补充推导：E_损 = 1/2*m*v0^2 - 1/2*(M+m)*v_共^2",
        is_correct=True,
    )
    assert att3.submission_count == 3


def test_error_attribution_update(
    temp_db: Database, sample_task: TrainingTask, sample_question: VerifiedQuestion
) -> None:
    """Updating error attribution modifies both memory state and SQLite records."""
    service = TrainingSessionService(temp_db)
    session = service.create_session(sample_task, [sample_question])

    # Attempt question without error attribution initially
    service.evaluate_and_record_attempt(
        session.id,
        sample_question.id,
        user_answer="未考虑摩擦生热",
        is_correct=False,
    )

    # Now update attribution to READING
    updated = service.update_error_attribution(
        session_id=session.id,
        question_id=sample_question.id,
        error_type=ErrorType.READING,
        error_analysis="漏看题目中'水平光滑地面'的附加条件",
    )
    assert updated.error_type == ErrorType.READING
    assert "漏看题目" in updated.error_analysis

    # Verify updated attempt loaded from DB
    loaded_session = service.get_session(session.id)
    assert loaded_session is not None
    att = loaded_session.get_attempt(sample_question.id)
    assert att is not None
    assert att.error_type == ErrorType.READING
    assert att.error_type.label == "审题不清"

    # Updating unattempted question raises ValueError
    with pytest.raises(ValueError, match="尚未作答"):
        service.update_error_attribution(
            session_id=session.id,
            question_id="non_existent_q",
            error_type=ErrorType.OTHER,
        )


def test_domain_method_reinforcement_generation(sample_task: TrainingTask) -> None:
    """Domain reinforcement tips generate specific, actionable guidance for each subject/tier."""
    geo_q = VerifiedQuestion(
        id="geo_q",
        question_text="丹霞地貌崖壁凹槽发育机制",
        source_name="2024年高考地理",
        source_url="http://example.com/geo",
        year=2024,
        exam_or_contest_name="2024高考地理",
        question_number="17",
        subject="地理",
        applicable_scope="基础教育 · 普通",
        verification_status=VerificationStatus.VERIFIED,
    )
    geo_tip = _generate_method_reinforcement(geo_q, sample_task)
    assert any(k in geo_tip for k in ("综合思维", "流水侵蚀", "地质演化", "因果链条"))

    phys_q = VerifiedQuestion(
        id="phys_q",
        question_text="双滑块相对运动与碰撞",
        source_name="2024年高考物理",
        source_url="http://example.com/phys",
        year=2024,
        exam_or_contest_name="2024高考物理",
        question_number="24",
        subject="物理",
        applicable_scope="基础教育 · 普通",
        verification_status=VerificationStatus.VERIFIED,
    )
    phys_tip = _generate_method_reinforcement(phys_q, sample_task)
    assert any(k in phys_tip for k in ("动量", "守恒", "受力分析", "过程分解"))

    cpho_q = VerifiedQuestion(
        id="cpho_q",
        question_text="第40届全国中学生物理竞赛绝热不变量分析",
        source_name="第40届全国中学生物理竞赛",
        source_url="http://example.com/cpho",
        year=2023,
        exam_or_contest_name="全国中学生物理竞赛",
        question_number="决赛1",
        subject="物理",
        applicable_scope="学科竞赛 · 决赛",
        verification_status=VerificationStatus.VERIFIED,
    )
    cpho_tip = _generate_method_reinforcement(cpho_q, sample_task)
    assert any(k in cpho_tip for k in ("竞赛", "绝热不变量", "微扰", "守恒"))

    control_q = VerifiedQuestion(
        id="control_q",
        question_text="现代控制理论状态反馈矩阵设计",
        source_name="清华大学自动化系期末考试",
        source_url="http://example.com/control",
        year=2023,
        exam_or_contest_name="期末考试",
        question_number="大题2",
        subject="自动控制原理",
        applicable_scope="高等教育 · 本科",
        verification_status=VerificationStatus.VERIFIED,
    )
    control_tip = _generate_method_reinforcement(control_q, sample_task)
    assert any(k in control_tip for k in ("高等工程", "矩阵", "状态空间", "极点配置"))


def test_grade7_math_method_reinforcement_uses_student_level_language() -> None:
    """Math fallback must not leak physics or engineering boilerplate."""

    task = TrainingTask(
        training_type="方法族",
        target="乘方底数辨析",
        subject="数学",
    )
    question = VerifiedQuestion(
        id="math_power_fallback",
        question_text="计算 -1^{2021}。",
        source_name="本地人工整理",
        source_url="pages/3_浏览资料.py?document=1&page=3",
        year=None,
        exam_or_contest_name="本地人工整理",
        question_number="20(2)",
        subject="数学",
        applicable_scope="基础教育 · 普通",
    )

    tip = _generate_method_reinforcement(question, task)

    assert "运算顺序" in tip
    assert "符号" in tip
    assert "括号" in tip
    assert "守恒定理" not in tip
    assert "演变机理" not in tip
    assert "量纲" not in tip


def test_reviewed_method_family_replaces_generic_cross_domain_tip(
    temp_db: Database,
) -> None:
    """Grade 7 math training uses the reviewed family text, never physics boilerplate."""

    now = datetime.now(UTC).isoformat()
    with temp_db._connection() as conn:
        conn.execute(
            """
            INSERT INTO question_families(
                id, family_kind, title, description, variant_pattern,
                confusion_notes, created_at, updated_at
            ) VALUES (?, 'method', ?, ?, ?, ?, ?, ?)
            """,
            (
                202,
                "乘方底数辨析",
                "先判断负号是否属于底数，再判断指数奇偶。",
                "用奇数指数与偶数指数成对比较。",
                "-1²⁰²⁴ 与 (-1)²⁰²⁴ 的值不同。",
                now,
                now,
            ),
        )
    task = TrainingTask(
        training_type="方法族", target="乘方底数辨析", subject="数学", family_id=202
    )
    question = VerifiedQuestion(
        id="math_power",
        question_text="比较 -1²⁰²⁴ 与 (-1)²⁰²⁴。",
        source_name="本地人工整理",
        source_url="pages/3_浏览资料.py?document=1&page=1",
        year=None,
        exam_or_contest_name="本地人工整理",
        question_number="训练1",
        subject="数学",
        applicable_scope="基础教育 · 普通",
        family_id=202,
    )

    tip = TrainingSessionService(temp_db)._method_reinforcement(question, task)

    assert "先判断负号是否属于底数" in tip
    assert "偶数指数" in tip
    assert "守恒定理" not in tip
    assert "量纲" not in tip


def test_layer1_mastery_evidence_feedback(
    temp_db: Database, sample_task: TrainingTask, sample_question: VerifiedQuestion
) -> None:
    """Attempts with question_item_id push practice events to Layer 1 mastery_evidence table."""
    service = TrainingSessionService(temp_db)
    session = service.create_session(sample_task, [sample_question])

    # First attempt
    service.evaluate_and_record_attempt(
        session.id,
        sample_question.id,
        user_answer="m*v0 = (M + m)*v_共",
        is_correct=True,
    )

    with temp_db._connection() as conn:
        rows = conn.execute(
            "SELECT * FROM mastery_evidence WHERE question_id = ?",
            (sample_question.question_item_id,),
        ).fetchall()
        assert len(rows) == 1
        ev = rows[0]
        assert ev["event_type"] == "practice"
        assert ev["result"] == "correct"
        assert ev["family_id"] == 201
        assert "targeted_training" in ev["provenance"]
        assert ev["idempotency_key"] == f"targeted_{session.id}_{sample_question.id}_1"

    # Second attempt (repeat submission)
    service.evaluate_and_record_attempt(
        session.id,
        sample_question.id,
        user_answer="推导更新：v = m/(M+m)*v0",
        is_correct=True,
    )

    with temp_db._connection() as conn:
        rows = conn.execute(
            "SELECT * FROM mastery_evidence WHERE question_id = ? ORDER BY id ASC",
            (sample_question.question_item_id,),
        ).fetchall()
        assert len(rows) == 2
        assert rows[1]["idempotency_key"] == f"targeted_{session.id}_{sample_question.id}_2"


def test_question_history_retrieval(
    temp_db: Database, sample_task: TrainingTask, sample_question: VerifiedQuestion
) -> None:
    """Historical attempts for a question across distinct sessions are retrieved chronologically."""
    service = TrainingSessionService(temp_db)

    # Session 1
    session1 = service.create_session(sample_task, [sample_question])
    service.evaluate_and_record_attempt(
        session1.id,
        sample_question.id,
        user_answer="第一次作答：动量守恒",
        is_correct=True,
    )

    # Session 2
    session2 = service.create_session(sample_task, [sample_question])
    service.evaluate_and_record_attempt(
        session2.id,
        sample_question.id,
        user_answer="第二次作答：完全非弹性碰撞",
        is_correct=True,
    )

    history = service.get_question_history(sample_question.id)
    assert len(history) == 2
    assert history[0].session_id == session2.id
    assert history[1].session_id == session1.id
