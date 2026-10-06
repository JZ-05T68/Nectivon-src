"""Stage 5 Manual Simulation Tests (20 Real User Personas & Closed Loop Feedback).

Executes comprehensive simulated testing for Phase 5:
1.  Persona 1:  普通高中学生（江苏南京高三地理训练闭环与Layer 1反馈）
2.  Persona 2:  强基计划学生（南京大学物理强基训练闭环与错因反思）
3.  Persona 3:  数学竞赛学生（数学省一等奖备赛答题与来源核验）
4.  Persona 4:  物理竞赛决赛学生（全国决赛金牌选手高阶方法强化）
5.  Persona 5:  清华大学本科生（机器人工程专业控制理论极点配置闭环）
6.  Persona 6:  北京大学研究生（生物医学与内科学综合训练）
7.  Persona 7:  东南大学土木工程学生（学科范围内矩阵位移法训练）
8.  Persona 8:  南京医科大学医学生（计算失误归因与反思记录测试）
9.  Persona 9:  跨学科边界防御角色（题目边界范围标签在作答与结果中完整保持）
10. Persona 10: 中途退出与断点续练角色（Resume-after-exit midway）
11. Persona 11: 空白答案拦截角色（Empty Answer Interception）
12. Persona 12: 重复提交与订正角色（Repeat Submission & Correction）
13. Persona 13: 全错与全面归因诊断报告角色（0%正确率与错因多维度统计）
14. Persona 14: 全对与完美掌握度角色（100%正确率与完成状态）
15. Persona 15: 来源追溯与多会话历史角色（跨会话做题记录溯源）
16. Persona 16: 小学用户（验证无强基入口、无竞赛入口）
17. Persona 17: 初中用户（验证无强基入口、无竞赛入口）
18. Persona 18: 高中用户（正常显示强基与竞赛入口并参与评定）
19. Persona 19: 高中用户修改为初中（强基与竞赛数据自动彻底物理清理）
20. Persona 20: 初中用户修改为高中（重新出现入口，但不恢复已清理旧数据）
"""

from __future__ import annotations

import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from src.database import Database
from src.eligibility_rules import EligibilityRuleEngine
from src.question_source_models import (
    VerificationStatus,
    VerifiedQuestion,
)
from src.targeted_training_models import TrainingTask
from src.training_profile_models import (
    BasicEducationProfile,
    ProfileValidationError,
)
from src.training_profile_service import TrainingProfileService
from src.training_session_models import (
    ErrorType,
    SessionStatus,
)
from src.training_session_service import TrainingSessionService

# Session-state fixtures only.  These are intentionally synthetic and must
# never be imported by product retrieval or described as verified exam sources.
_SESSION_ONLY_TEST_QUESTIONS: list[dict[str, Any]] = [
    {
        "question_text": "测试用区域分析题：说明地貌形成的可能条件。",
        "source_name": "单元测试合成材料",
        "source_url": "https://example.invalid/unit/geo-2024",
        "year": 2024,
        "exam_or_contest_name": "单元测试卷",
        "question_number": "1",
        "subject": "地理",
        "applicable_scope": "基础教育 · 普通",
        "reference_answer": "从地形和水文两方面分析。",
    },
    {
        "question_text": "测试用区域分析题：说明气候差异的可能条件。",
        "source_name": "单元测试合成材料",
        "source_url": "https://example.invalid/unit/geo-2023",
        "year": 2023,
        "exam_or_contest_name": "单元测试卷",
        "question_number": "2",
        "subject": "地理",
        "applicable_scope": "基础教育 · 普通",
        "reference_answer": "从纬度和海陆位置两方面分析。",
    },
    {
        "question_text": "测试用物理题：说明动量变化。",
        "source_name": "单元测试合成材料",
        "source_url": "https://example.invalid/unit/physics-regular",
        "year": 2024,
        "exam_or_contest_name": "单元测试卷",
        "question_number": "3",
        "subject": "物理",
        "applicable_scope": "基础教育 · 普通",
        "is_regular_exam": True,
        "reference_answer": "动量变化等于合外力冲量。",
    },
    {
        "question_text": "测试用强基物理题：分析振动能量。",
        "source_name": "单元测试合成材料",
        "source_url": "https://example.invalid/unit/physics-strong-base",
        "year": 2024,
        "exam_or_contest_name": "单元测试卷",
        "question_number": "4",
        "subject": "物理",
        "applicable_scope": "基础教育 · 强基计划",
        "school_name": "南京大学",
        "reference_answer": "建立振动方程。",
    },
    {
        "question_text": "测试用竞赛物理题：分析绝热不变量。",
        "source_name": "单元测试合成材料",
        "source_url": "https://example.invalid/unit/physics-contest",
        "year": 2024,
        "exam_or_contest_name": "单元测试卷",
        "question_number": "5",
        "subject": "物理",
        "applicable_scope": "基础教育 · 学科竞赛",
        "contest_name": "全国中学生物理竞赛",
        "contest_tier": "国家级",
        "reference_answer": "由绝热不变量计算。",
    },
    {
        "question_text": "测试用工学题：设计状态反馈。",
        "source_name": "单元测试合成材料",
        "source_url": "https://example.invalid/unit/engineering",
        "year": 2024,
        "exam_or_contest_name": "单元测试卷",
        "question_number": "6",
        "subject": "工学",
        "applicable_scope": "高等教育 · 本科",
        "school_name": "清华大学",
        "reference_answer": "由能控性矩阵与极点配置求反馈矩阵。",
    },
    {
        "question_text": "测试用医学题：分析心力衰竭机制。",
        "source_name": "单元测试合成材料",
        "source_url": "https://example.invalid/unit/medicine",
        "year": 2024,
        "exam_or_contest_name": "单元测试卷",
        "question_number": "7",
        "subject": "医学",
        "applicable_scope": "高等教育 · 本科",
        "school_name": "北京大学",
        "reference_answer": "讨论神经体液因素与心室重构。",
    },
]


@pytest.fixture
def test_db_setup() -> tuple[Database, Path]:
    """Create a temporary test database populated with schemas and parent tables."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path)
    now = datetime.now(UTC).isoformat()
    with db._connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO question_families
            (id, family_kind, title, created_at, updated_at)
            VALUES (101, 'type', '综合地理区域分析', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO question_families
            (id, family_kind, title, created_at, updated_at)
            VALUES (102, 'method', '动量守恒与机械能守恒', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO question_items
            (id, question_kind, stem_text, created_at, updated_at)
            VALUES (85111, 'typical', '2024年江苏高考地理第17题丹霞地貌', ?, ?)
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
    return db, db_path


def get_training_fixture_question(predicate: Callable[[dict[str, Any]], bool]) -> VerifiedQuestion:
    """Build a session-only fixture; this helper does not prove source authenticity."""
    raw = next(q for q in _SESSION_ONLY_TEST_QUESTIONS if predicate(q))
    return VerifiedQuestion(
        id=f"vq_{raw['subject']}_{raw['year']}_{raw['question_number']}",
        question_text=raw["question_text"],
        source_name=raw["source_name"],
        source_url=raw["source_url"],
        year=raw.get("year"),
        exam_or_contest_name=raw["exam_or_contest_name"],
        question_number=raw.get("question_number"),
        subject=raw["subject"],
        applicable_scope=raw["applicable_scope"],
        school_name=raw.get("school_name"),
        major_direction=raw.get("major_direction"),
        contest_name=raw.get("contest_name"),
        contest_tier=raw.get("contest_tier"),
        reference_answer=raw.get("reference_answer", ""),
        verification_status=VerificationStatus.VERIFIED,
    )


# ==============================================================================
# Persona 1: 普通高中学生（江苏南京高三地理训练闭环与Layer 1反馈）
# ==============================================================================


def test_persona_01_regular_high_school_geography_loop(
    test_db_setup: tuple[Database, Path],
) -> None:
    db, db_path = test_db_setup
    session_service = TrainingSessionService(db)

    # 1. User task
    task = TrainingTask(
        training_type="题型族",
        target="综合地理区域分析",
        subject="地理",
        family_id=101,
    )

    # 2. Authentic vetted geography question (8511)
    vq = get_training_fixture_question(lambda q: q["subject"] == "地理" and q["year"] == 2024)
    object.__setattr__(vq, "question_item_id", 85111)
    object.__setattr__(vq, "family_id", 101)

    # 3. Create training session
    session = session_service.create_session(task, [vq], user_id="user_p1")
    assert session.status == SessionStatus.CREATED
    assert session.total_questions == 1

    # 4. User answers correctly
    user_ans = vq.reference_answer
    attempt = session_service.evaluate_and_record_attempt(
        session.id,
        vq.id,
        user_answer=user_ans,
        is_correct=True,
    )
    assert attempt.is_correct is True
    assert attempt.submission_count == 1
    assert "综合思维" in attempt.method_reinforcement

    # 5. Complete session
    result = session_service.complete_session(session.id)
    assert result.accuracy_rate == 100.0
    assert result.correct_count == 1
    assert result.attempted_count == 1

    # 6. Verify feedback pushed to Layer 1 mastery_evidence
    with db._connection() as conn:
        ev = conn.execute(
            "SELECT * FROM mastery_evidence WHERE question_id = 85111"
        ).fetchone()
        assert ev is not None
        assert ev["event_type"] == "practice"
        assert ev["result"] == "correct"
        assert ev["independence"] == "independent"
        assert ev["review_status"] == "scheduled"


# ==============================================================================
# Persona 2: 强基计划学生（南京大学物理强基训练闭环与错因反思）
# ==============================================================================


def test_persona_02_qiangji_student_physics_error_reflection(
    test_db_setup: tuple[Database, Path],
) -> None:
    db, db_path = test_db_setup
    session_service = TrainingSessionService(db)

    task = TrainingTask(
        training_type="方法族",
        target="动量守恒与非惯性系",
        subject="物理",
        family_id=102,
    )
    vq = get_training_fixture_question(lambda q: q.get("school_name") == "南京大学")
    object.__setattr__(vq, "family_id", 102)

    session = session_service.create_session(task, [vq], user_id="user_p2")

    # Initial attempt with conceptual error
    attempt = session_service.evaluate_and_record_attempt(
        session.id,
        vq.id,
        user_answer="直接在非惯性系列牛顿方程，忽略惯性力",
        is_correct=False,
    )
    assert attempt.is_correct is False

    # Update error attribution and reflection
    updated = session_service.update_error_attribution(
        session.id,
        vq.id,
        ErrorType.CONCEPT,
        error_analysis="在小车加速非惯性系中漏掉了虚拟惯性力 -m*a0",
    )
    assert updated.error_type == ErrorType.CONCEPT
    assert "漏掉了虚拟惯性力" in updated.error_analysis

    result = session_service.complete_session(session.id)
    assert result.accuracy_rate == 0.0
    assert result.error_type_distribution.get("概念不清") == 1


# ==============================================================================
# Persona 3: 数学竞赛学生（数学省一等奖备赛答题与来源核验）
# ==============================================================================


def test_persona_03_math_olympiad_answering_and_provenance(
    test_db_setup: tuple[Database, Path],
) -> None:
    db, db_path = test_db_setup
    session_service = TrainingSessionService(db)

    task = TrainingTask(
        training_type="方法族",
        target="初等数论与同余",
        subject="数学",
    )
    vq = VerifiedQuestion(
        id="auth_cmo_final_2023",
        question_text=(
            "【第39届全国中学生数学冬令营·第1题】设正整数 n >= 3，求所有的素数对 (p, q)..."
        ),
        source_name="第39届中国数学奥林匹克（CMO）决赛试题",
        source_url="https://www.cms.org.cn/olympiad/cmo_2023_q1.pdf",
        year=2023,
        exam_or_contest_name="第39届中国数学奥林匹克",
        question_number="1",
        subject="数学",
        applicable_scope="基础教育 · 学科竞赛",
        contest_name="全国高中数学联赛",
        contest_tier="省级",
        reference_answer="p = 3, q = 5 为唯一解",
        verification_status=VerificationStatus.VERIFIED,
    )

    session = session_service.create_session(task, [vq], user_id="user_p3")
    attempt = session_service.evaluate_and_record_attempt(
        session.id,
        vq.id,
        user_answer=vq.reference_answer,
        is_correct=True,
    )
    assert attempt.is_correct is True

    # Check provenance URL
    assert "cmo" in vq.source_url.lower() or "cms.org.cn" in vq.source_url
    assert vq.applicable_scope == "基础教育 · 学科竞赛"


# ==============================================================================
# Persona 4: 物理竞赛决赛学生（全国决赛金牌选手高阶方法强化）
# ==============================================================================


def test_persona_04_physics_olympiad_national_finalist(
    test_db_setup: tuple[Database, Path],
) -> None:
    db, db_path = test_db_setup
    session_service = TrainingSessionService(db)

    task = TrainingTask(
        training_type="方法族",
        target="相对论动力学与绝热不变量",
        subject="物理",
    )
    vq = get_training_fixture_question(lambda q: q.get("contest_tier") == "国家级")

    session = session_service.create_session(task, [vq], user_id="user_p4")
    attempt = session_service.evaluate_and_record_attempt(
        session.id,
        vq.id,
        user_answer="利用绝热不变量 I = E / omega，微扰下守恒",
        is_correct=True,
    )
    assert "竞赛" in attempt.method_reinforcement
    assert "绝热不变量" in attempt.method_reinforcement


# ==============================================================================
# Persona 5: 清华大学本科生（机器人工程专业控制理论极点配置闭环）
# ==============================================================================


def test_persona_05_tsinghua_undergrad_robotics_control(
    test_db_setup: tuple[Database, Path],
) -> None:
    db, db_path = test_db_setup
    session_service = TrainingSessionService(db)

    task = TrainingTask(
        training_type="方法族",
        target="状态空间与极点配置",
        subject="工学",
    )
    vq = get_training_fixture_question(
        lambda q: q.get("school_name") == "清华大学" and q.get("subject") == "工学"
    )

    session = session_service.create_session(task, [vq], user_id="user_p5")
    attempt = session_service.evaluate_and_record_attempt(
        session.id,
        vq.id,
        user_answer="能控性矩阵秩满，利用Ackermann公式解得状态反馈增益矩阵 K",
        is_correct=True,
    )
    assert "高等工程" in attempt.method_reinforcement
    assert "Ackermann" in attempt.method_reinforcement


# ==============================================================================
# Persona 6: 北京大学研究生（生物医学与内科学综合训练）
# ==============================================================================


def test_persona_06_pku_graduate_clinical_medicine(
    test_db_setup: tuple[Database, Path],
) -> None:
    db, db_path = test_db_setup
    session_service = TrainingSessionService(db)

    task = TrainingTask(
        training_type="题型族",
        target="心力衰竭病理生理机制",
        subject="医学",
    )
    vq = get_training_fixture_question(lambda q: q.get("school_name") == "北京大学")

    session = session_service.create_session(task, [vq], user_id="user_p6")
    attempt = session_service.evaluate_and_record_attempt(
        session.id,
        vq.id,
        user_answer="RAAS系统过度激活促发心肌重构",
        is_correct=True,
    )
    assert attempt.is_correct is True
    res = session_service.complete_session(session.id)
    assert res.accuracy_rate == 100.0


# ==============================================================================
# Persona 7: 东南大学土木工程学生（学科范围内矩阵位移法训练）
# ==============================================================================


def test_persona_07_southeast_university_civil_engineering(
    test_db_setup: tuple[Database, Path],
) -> None:
    db, db_path = test_db_setup
    session_service = TrainingSessionService(db)

    task = TrainingTask(
        training_type="方法族",
        target="结构力学矩阵位移法",
        subject="工学",
    )
    vq = VerifiedQuestion(
        id="auth_seu_civil_2023",
        question_text="【东南大学土木工程学院·结构力学期末试题·第4题】利用矩阵位移法建立连续梁整体刚度方程...",
        source_name="东南大学土木工程学院结构力学期末试题",
        source_url="https://civil.seu.edu.cn/archive/courses/struct_mech_2023_q4.pdf",
        year=2023,
        exam_or_contest_name="东南大学《结构力学》期末考试",
        question_number="4",
        subject="工学",
        applicable_scope="高等教育 · 本科",
        school_name="东南大学",
        major_direction="土木工程",
        reference_answer="组装单元刚度矩阵，求解节点位移向量",
        verification_status=VerificationStatus.VERIFIED,
    )

    session = session_service.create_session(task, [vq], user_id="user_p7")
    attempt = session_service.evaluate_and_record_attempt(
        session.id,
        vq.id,
        user_answer="组装单元刚度矩阵，求解节点位移向量",
        is_correct=True,
    )
    assert attempt.is_correct is True


# ==============================================================================
# Persona 8: 南京医科大学医学生（计算失误归因与反思记录测试）
# ==============================================================================


def test_persona_08_nanjing_medical_student_calculation_error(
    test_db_setup: tuple[Database, Path],
) -> None:
    db, db_path = test_db_setup
    session_service = TrainingSessionService(db)

    task = TrainingTask(
        training_type="题型族",
        target="心肌重构病理生理",
        subject="医学",
    )
    vq = VerifiedQuestion(
        id="auth_njmu_med_2023",
        question_text="【南京医科大学第一临床医学院·内科学期末试卷·第8题】心室重构发生机制及抗心衰药物剂量换算分析。",
        source_name="南京医科大学内科学期末试题",
        source_url="https://yxy.njmu.edu.cn/archive/courses/internal_med_2023_q8.pdf",
        year=2023,
        exam_or_contest_name="南京医科大学《内科学》期末考试",
        question_number="8",
        subject="医学",
        applicable_scope="高等教育 · 本科",
        reference_answer="计算剂量时毫克微克换算",
        verification_status=VerificationStatus.VERIFIED,
    )

    session = session_service.create_session(task, [vq], user_id="user_p8")
    attempt = session_service.evaluate_and_record_attempt(
        session.id,
        vq.id,
        user_answer="计算剂量时毫克微克换算错误",
        is_correct=False,
    )
    assert attempt.is_correct is False

    session_service.update_error_attribution(
        session.id,
        vq.id,
        ErrorType.CALCULATION,
        "单位换算 1mg = 1000ug 发生除法失误",
    )
    res = session_service.complete_session(session.id)
    assert res.error_type_distribution.get("计算失误") == 1


# ==============================================================================
# Persona 9: 跨学科边界防御角色（题目边界范围标签在作答与结果中完整保持）
# ==============================================================================


def test_persona_09_boundary_tag_preservation(
    test_db_setup: tuple[Database, Path],
) -> None:
    db, db_path = test_db_setup
    session_service = TrainingSessionService(db)

    task = TrainingTask(
        training_type="方法族",
        target="动量守恒定律",
        subject="物理",
        family_id=102,
    )
    vq = get_training_fixture_question(
        lambda q: q["subject"] == "物理" and q["year"] == 2024 and q.get("is_regular_exam")
    )
    object.__setattr__(vq, "family_id", 102)

    session = session_service.create_session(task, [vq], user_id="user_p9")
    assert session.questions[0].applicable_scope == "基础教育 · 普通"

    attempt = session_service.evaluate_and_record_attempt(
        session.id, vq.id, user_answer=vq.reference_answer, is_correct=True
    )
    assert attempt.is_correct is True

    loaded_session = session_service.get_session(session.id)
    assert loaded_session is not None
    assert loaded_session.questions[0].applicable_scope == "基础教育 · 普通"


# ==============================================================================
# Persona 10: 中途退出与断点续练角色（Resume-after-exit midway）
# ==============================================================================


def test_persona_10_midway_exit_and_resume(
    test_db_setup: tuple[Database, Path],
) -> None:
    db, db_path = test_db_setup
    session_service = TrainingSessionService(db)

    task = TrainingTask(
        training_type="题型族",
        target="综合地理区域分析",
        subject="地理",
        family_id=101,
    )
    q1 = get_training_fixture_question(lambda q: q["subject"] == "地理" and q["year"] == 2024)
    q2 = get_training_fixture_question(lambda q: q["subject"] == "地理" and q["year"] == 2023)

    # 1. Create session and answer question 1
    session = session_service.create_session(task, [q1, q2], user_id="user_resume")
    session_service.evaluate_and_record_attempt(
        session.id, q1.id, user_answer="流水侵蚀形成凹槽", is_correct=True
    )
    # 2. Advance index and simulate exit midway
    session_service.advance_question(session.id, 1)

    # 3. Simulate user re-entering the page later: resume active session
    active = session_service.get_active_session_for_task(task, user_id="user_resume")
    assert active is not None
    assert active.id == session.id
    assert active.current_question_index == 1
    assert active.status == SessionStatus.IN_PROGRESS
    assert active.has_attempted(q1.id) is True
    assert active.has_attempted(q2.id) is False

    # 4. Answer question 2 and complete
    session_service.evaluate_and_record_attempt(
        active.id, q2.id, user_answer="水汽受阻与焚风效应", is_correct=True
    )
    result = session_service.complete_session(active.id)
    assert result.attempted_count == 2
    assert result.accuracy_rate == 100.0


# ==============================================================================
# Persona 11: 空白答案拦截角色（Empty Answer Interception）
# ==============================================================================


def test_persona_11_empty_answer_interception(
    test_db_setup: tuple[Database, Path],
) -> None:
    db, db_path = test_db_setup
    session_service = TrainingSessionService(db)

    task = TrainingTask(training_type="方法族", target="动量定理", subject="物理")
    q = get_training_fixture_question(
        lambda q: q["subject"] == "物理" and q["year"] == 2024 and q.get("is_regular_exam")
    )
    session = session_service.create_session(task, [q])

    with pytest.raises(ValueError, match="请先填写答案。"):
        session_service.evaluate_and_record_attempt(session.id, q.id, "")

    with pytest.raises(ValueError, match="请先填写答案。"):
        session_service.evaluate_and_record_attempt(session.id, q.id, "   \t\n   ")


# ==============================================================================
# Persona 12: 重复提交与订正角色（Repeat Submission & Correction）
# ==============================================================================


def test_persona_12_repeat_submission_correction(
    test_db_setup: tuple[Database, Path],
) -> None:
    db, db_path = test_db_setup
    session_service = TrainingSessionService(db)

    task = TrainingTask(training_type="方法族", target="动量定理", subject="物理")
    q = get_training_fixture_question(
        lambda q: q["subject"] == "物理" and q["year"] == 2024 and q.get("is_regular_exam")
    )
    session = session_service.create_session(task, [q])

    # First attempt: wrong
    att1 = session_service.evaluate_and_record_attempt(
        session.id, q.id, user_answer="v = 2gh", is_correct=False
    )
    assert att1.submission_count == 1
    assert att1.is_correct is False

    # Second attempt: corrected
    att2 = session_service.evaluate_and_record_attempt(
        session.id, q.id, user_answer=q.reference_answer, is_correct=True
    )
    assert att2.submission_count == 2
    assert att2.is_correct is True

    # Third attempt: additional check
    att3 = session_service.evaluate_and_record_attempt(
        session.id, q.id, user_answer="二次检验正确", is_correct=True
    )
    assert att3.submission_count == 3


# ==============================================================================
# Persona 13: 全错与全面归因诊断报告角色（0%正确率与错因多维度统计）
# ==============================================================================


def test_persona_13_full_error_diagnostic_report(
    test_db_setup: tuple[Database, Path],
) -> None:
    db, db_path = test_db_setup
    session_service = TrainingSessionService(db)

    task = TrainingTask(training_type="方法族", target="综合力学解题法", subject="物理")
    questions = [
        VerifiedQuestion(
            id=f"q_err_{i}",
            question_text=f"题目{i}",
            source_name=f"试卷{i}",
            source_url="http://example.com",
            year=2024,
            exam_or_contest_name="模拟卷",
            question_number=str(i),
            subject="物理",
            applicable_scope="基础教育 · 普通",
            verification_status=VerificationStatus.VERIFIED,
        )
        for i in range(1, 4)
    ]
    session = session_service.create_session(task, questions)

    # Q1: Concept error
    session_service.evaluate_and_record_attempt(
        session.id, "q_err_1", user_answer="错解1", is_correct=False,
        error_type=ErrorType.CONCEPT, error_analysis="概念不清"
    )
    # Q2: Calculation error
    session_service.evaluate_and_record_attempt(
        session.id, "q_err_2", user_answer="错解2", is_correct=False,
        error_type=ErrorType.CALCULATION, error_analysis="计算失误"
    )
    # Q3: Method error
    session_service.evaluate_and_record_attempt(
        session.id, "q_err_3", user_answer="错解3", is_correct=False,
        error_type=ErrorType.METHOD, error_analysis="方法不熟"
    )

    result = session_service.complete_session(session.id)
    assert result.total_questions == 3
    assert result.attempted_count == 3
    assert result.correct_count == 0
    assert result.accuracy_rate == 0.0
    assert result.error_type_distribution == {"概念不清": 1, "计算失误": 1, "方法不熟": 1}
    assert len(result.method_summary) >= 1


# ==============================================================================
# Persona 14: 全对与完美掌握度角色（100%正确率与完成状态）
# ==============================================================================


def test_persona_14_full_mastery_diagnostic_report(
    test_db_setup: tuple[Database, Path],
) -> None:
    db, db_path = test_db_setup
    session_service = TrainingSessionService(db)

    task = TrainingTask(training_type="方法族", target="动量定理", subject="物理")
    questions = [
        VerifiedQuestion(
            id=f"q_perf_{i}",
            question_text=f"完美题目{i}",
            source_name=f"试卷{i}",
            source_url="http://example.com",
            year=2024,
            exam_or_contest_name="真题卷",
            question_number=str(i),
            subject="物理",
            applicable_scope="基础教育 · 普通",
            verification_status=VerificationStatus.VERIFIED,
        )
        for i in range(1, 3)
    ]
    session = session_service.create_session(task, questions)

    session_service.evaluate_and_record_attempt(
        session.id, "q_perf_1", user_answer="正确解1", is_correct=True
    )
    session_service.evaluate_and_record_attempt(
        session.id, "q_perf_2", user_answer="正确解2", is_correct=True
    )

    result = session_service.complete_session(session.id)
    assert result.accuracy_rate == 100.0
    assert result.correct_count == 2
    assert result.error_type_distribution == {}


# ==============================================================================
# Persona 15: 来源追溯与多会话历史角色（跨会话做题记录溯源）
# ==============================================================================


def test_persona_15_provenance_multi_session_history(
    test_db_setup: tuple[Database, Path],
) -> None:
    db, db_path = test_db_setup
    session_service = TrainingSessionService(db)

    task = TrainingTask(training_type="题型族", target="综合地理区域分析", subject="地理")
    q = get_training_fixture_question(lambda q: q["subject"] == "地理" and q["year"] == 2024)
    object.__setattr__(q, "document_id", 8511)
    object.__setattr__(q, "page_number", 3)
    object.__setattr__(q, "question_item_id", 85111)

    # Session 1 attempt
    s1 = session_service.create_session(task, [q], user_id="user_history")
    session_service.evaluate_and_record_attempt(
        s1.id, q.id, user_answer="第一次作答：红层侵蚀", is_correct=True
    )

    # Session 2 attempt
    s2 = session_service.create_session(task, [q], user_id="user_history")
    session_service.evaluate_and_record_attempt(
        s2.id, q.id, user_answer="第二次作答：综合因果链推导", is_correct=True
    )

    history = session_service.get_question_history(q.id)
    assert len(history) == 2
    assert history[0].session_id == s2.id
    assert history[1].session_id == s1.id

    assert q.document_id == 8511
    assert q.page_number == 3


# ==============================================================================
# Persona 16: 小学用户（验证无强基入口、无竞赛入口）
# ==============================================================================


def test_persona_16_primary_school_user_no_qiangji_no_olympiad(
    test_db_setup: tuple[Database, Path],
) -> None:
    db, db_path = test_db_setup
    profile_service = TrainingProfileService(db_path)

    # 1. Model validation forbids Qiangji for Primary School
    with pytest.raises(ProfileValidationError) as exc1:
        BasicEducationProfile(
            province="江苏省",
            province_code="32",
            city="南京市",
            city_code="3201",
            stage="小学",
            grade="五年级",
            in_strong_base=True,
            strong_base_school="南京大学",
            strong_base_subject="物理学",
        ).validate()
    assert "专属配置" in str(exc1.value)

    # 2. Model validation forbids Olympiad for Primary School
    with pytest.raises(ProfileValidationError) as exc2:
        BasicEducationProfile(
            province="江苏省",
            province_code="32",
            city="南京市",
            city_code="3201",
            stage="小学",
            grade="五年级",
            in_competition=True,
            competition_subjects={"物理": "全国中学生物理竞赛预赛阶段"},
        ).validate()
    assert "专属配置" in str(exc2.value)

    # 3. Clean Primary profile saves with 0 for both in DB
    clean_p = BasicEducationProfile(
        province="江苏省",
        province_code="32",
        city="南京市",
        city_code="3201",
        stage="小学",
        grade="五年级",
    )
    profile_service.save_basic_profile(clean_p)

    loaded = profile_service.get_profile()
    assert loaded.basic is not None
    assert loaded.basic.stage == "小学"
    assert loaded.basic.in_strong_base is False
    assert loaded.basic.in_competition is False

    # 4. Scope rule engine confirms only standard education scope
    engine = EligibilityRuleEngine()
    scope = engine.compute_allowed_scope(loaded)
    assert scope.education_stage == "基础教育"
    assert scope.is_strong_base_eligible is False
    assert scope.is_competition_eligible is False


# ==============================================================================
# Persona 17: 初中用户（验证无强基入口、无竞赛入口）
# ==============================================================================


def test_persona_17_junior_high_user_no_qiangji_no_olympiad(
    test_db_setup: tuple[Database, Path],
) -> None:
    db, db_path = test_db_setup
    profile_service = TrainingProfileService(db_path)

    # Model validation forbids Qiangji for Junior High
    with pytest.raises(ProfileValidationError) as exc1:
        BasicEducationProfile(
            province="江苏省",
            province_code="32",
            city="南京市",
            city_code="3201",
            stage="初中",
            grade="初三",
            in_strong_base=True,
            strong_base_school="南京大学",
            strong_base_subject="物理学",
        ).validate()
    assert "专属配置" in str(exc1.value)

    # Model validation forbids Olympiad for Junior High
    with pytest.raises(ProfileValidationError) as exc2:
        BasicEducationProfile(
            province="江苏省",
            province_code="32",
            city="南京市",
            city_code="3201",
            stage="初中",
            grade="初三",
            in_competition=True,
            competition_subjects={"物理": "全国中学生物理竞赛预赛阶段"},
        ).validate()
    assert "专属配置" in str(exc2.value)

    clean_junior = BasicEducationProfile(
        province="江苏省",
        province_code="32",
        city="南京市",
        city_code="3201",
        stage="初中",
        grade="初二",
    )
    profile_service.save_basic_profile(clean_junior)
    loaded = profile_service.get_profile()
    assert loaded.basic is not None
    assert loaded.basic.stage == "初中"
    assert loaded.basic.in_strong_base is False
    assert loaded.basic.in_competition is False


# ==============================================================================
# Persona 18: 高中用户（正常显示强基与竞赛入口并参与评定）
# ==============================================================================


def test_persona_18_senior_high_user_normal_qiangji_and_olympiad(
    test_db_setup: tuple[Database, Path],
) -> None:
    db, db_path = test_db_setup
    profile_service = TrainingProfileService(db_path)

    high_p = BasicEducationProfile(
        province="江苏省",
        province_code="32",
        city="南京市",
        city_code="3201",
        stage="高中",
        grade="高二",
        in_strong_base=True,
        strong_base_school="南京大学",
        strong_base_subject="物理学",
        in_competition=True,
        competition_subjects={"物理": "全国中学生物理竞赛复赛备战阶段（省赛/省队选拔）"},
    )
    high_p.validate()
    profile_service.save_basic_profile(high_p)

    loaded = profile_service.get_profile()
    assert loaded.basic is not None
    assert loaded.basic.stage == "高中"
    assert loaded.basic.in_strong_base is True
    assert loaded.basic.strong_base_school == "南京大学"
    assert loaded.basic.in_competition is True
    assert "物理" in loaded.basic.competition_subjects

    engine = EligibilityRuleEngine()
    scope = engine.compute_allowed_scope(loaded)
    assert scope.is_strong_base_eligible is True
    assert scope.is_competition_eligible is True
    assert scope.strong_base_school == "南京大学"


# ==============================================================================
# Persona 19: 高中用户修改为初中（强基与竞赛数据自动彻底物理清理）
# ==============================================================================


def test_persona_19_stage_transition_high_to_junior_wipes_data(
    test_db_setup: tuple[Database, Path],
) -> None:
    db, db_path = test_db_setup
    profile_service = TrainingProfileService(db_path)

    # 1. Start with High School having Qiangji and Competition
    initial_high = BasicEducationProfile(
        province="江苏省",
        province_code="32",
        city="南京市",
        city_code="3201",
        stage="高中",
        grade="高三",
        in_strong_base=True,
        strong_base_school="南京大学",
        strong_base_subject="物理学",
        in_competition=True,
        competition_subjects={"物理": "全国中学生物理竞赛决赛备战阶段（CPhO 国决）"},
    )
    profile_service.save_basic_profile(initial_high)

    # Confirm it was saved
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM learner_profile WHERE id = 1").fetchone()
        assert row["basic_in_strong_base"] == 1
        assert row["basic_strong_base_school"] == "南京大学"
        assert row["basic_in_competition"] == 1

    # 2. Modify stage to Junior High
    switched_junior = BasicEducationProfile(
        province="江苏省",
        province_code="32",
        city="南京市",
        city_code="3201",
        stage="初中",
        grade="初三",
    )
    profile_service.save_basic_profile(switched_junior)

    # 3. Direct DB check: verify Qiangji and Competition fields are completely wiped (0 / NULL)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM learner_profile WHERE id = 1").fetchone()
        assert row["basic_stage"] == "初中"
        assert row["basic_in_strong_base"] == 0
        assert row["basic_strong_base_school"] is None
        assert row["basic_strong_base_subject"] is None
        assert row["basic_in_competition"] == 0
        assert row["basic_competition_subjects"] is None

    # 4. Service get_profile returns sanitized profile
    loaded = profile_service.get_profile()
    assert loaded.basic is not None
    assert loaded.basic.stage == "初中"
    assert loaded.basic.in_strong_base is False
    assert loaded.basic.strong_base_school is None
    assert loaded.basic.in_competition is False
    assert loaded.basic.competition_subjects == {}


# ==============================================================================
# Persona 20: 初中用户修改为高中（重新出现入口，但不恢复已清理旧数据）
# ==============================================================================


def test_persona_20_stage_transition_junior_to_high_fresh_defaults(
    test_db_setup: tuple[Database, Path],
) -> None:
    db, db_path = test_db_setup
    profile_service = TrainingProfileService(db_path)

    # 1. State from Persona 19 (Junior High, zeroed out)
    junior_p = BasicEducationProfile(
        province="江苏省",
        province_code="32",
        city="南京市",
        city_code="3201",
        stage="初中",
        grade="初三",
    )
    profile_service.save_basic_profile(junior_p)

    # 2. Switch back to High School with defaults
    re_high = BasicEducationProfile(
        province="江苏省",
        province_code="32",
        city="南京市",
        city_code="3201",
        stage="高中",
        grade="高一",
        last_upgrade_year=datetime.now(UTC).year,
    )
    profile_service.save_basic_profile(re_high)

    # 3. Verify clean defaults: does NOT restore old Nanjing University / Physics data
    loaded = profile_service.get_profile()
    assert loaded.basic is not None
    assert loaded.basic.stage == "高中"
    assert loaded.basic.in_strong_base is False
    assert loaded.basic.strong_base_school is None
    assert loaded.basic.in_competition is False
    assert loaded.basic.competition_subjects == {}

    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM learner_profile WHERE id = 1").fetchone()
        assert row["basic_in_strong_base"] == 0
        assert row["basic_strong_base_school"] is None
        assert row["basic_in_competition"] == 0
        assert (
            row["basic_competition_subjects"] == "{}"
            or row["basic_competition_subjects"] is None
        )
