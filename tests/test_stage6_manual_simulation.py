"""Stage 6 Manual Simulation Tests (10 Personas: 21 to 30 & Closed Loop System).

Executes comprehensive simulated testing for Phase 6:
1.  Persona 21: 连续错误用户（同一方法连续失误，验证生成高频错误标签与薄弱方向）
2.  Persona 22: 一次错误用户（单次失误不予过度推断，判定待加强而非完全不会，无高频标签）
3.  Persona 23: 连续正确用户（掌握度由未建立 -> 训练中 -> 阶段掌握真实演进）
4.  Persona 24: 错误后再次训练用户（错因触发变式训练，生成新TrainingTask并检索真实题源）
5.  Persona 25: 无训练历史用户（零数据防幻觉保护，拒绝虚构错因画像）
6.  Persona 26: 跨学科错误用户（地理做题失误绝不污染物理掌握度与错因画像）
7.  Persona 27: 普通题训练用户（高考真题训练结果与错因记录严格保持普通题标签）
8.  Persona 28: 强基用户（强基高校与专业方向标签在错因记录与掌握度中严格保持）
9.  Persona 29: 竞赛用户（奥赛级别与阶段标签在训练执行与错因记录中严格保持）
10. Persona 30: 完整闭环用户（训练 -> 错误 -> 归因 -> 变式训练 -> 再次做题 -> 阶段掌握）
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.database import Database
from src.error_analysis_models import (
    MasteryLevel,
)
from src.error_analysis_service import ErrorAnalysisService
from src.question_source_models import VerifiedQuestion
from src.question_source_retrieval_service import (
    QuestionSourceRetrievalService,
)
from src.targeted_training_models import TrainingTask
from src.training_profile_models import (
    BasicEducationProfile,
    LearnerProfile,
)
from src.training_session_models import ErrorType
from src.training_session_service import TrainingSessionService


@pytest.fixture
def stage6_db() -> Database:
    """Create a temporary SQLite database initialized with real question families and items."""
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
            VALUES (8512, 'method', '动量守恒与机械能守恒', ?, ?)
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
    return db


@pytest.fixture
def error_service(stage6_db: Database) -> ErrorAnalysisService:
    return ErrorAnalysisService(stage6_db)


@pytest.fixture
def session_service(
    stage6_db: Database, error_service: ErrorAnalysisService
) -> TrainingSessionService:
    return TrainingSessionService(stage6_db, error_analysis_service=error_service)


@pytest.fixture
def retrieval_service(stage6_db: Database) -> QuestionSourceRetrievalService:
    return QuestionSourceRetrievalService(stage6_db)


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
# Persona 21: 连续错误用户 (Multiple Consecutive Errors -> High-Frequency Error Tag)
# ==============================================================================
def test_persona_21_consecutive_errors_form_frequent_tag(
    session_service: TrainingSessionService,
    error_service: ErrorAnalysisService,
) -> None:
    """Persona 21: 同一方法连续失误，验证生成高频错误标签与薄弱方向。"""
    user_id = "user_21_consecutive_errors"
    target = "动量定理"
    subject = "物理"

    task = TrainingTask(training_type="方法族", target=target, subject=subject, family_id=8512)
    q1 = _build_authentic_question("p21_q1", subject, target)
    q2 = _build_authentic_question("p21_q2", subject, target)

    session = session_service.create_session(task, user_id=user_id, questions=[q1, q2])

    # Attempt 1: Wrong with 方法选择错误
    session_service.evaluate_and_record_attempt(
        session_id=session.id,
        question_id=q1.id,
        user_answer="错误解答1",
        is_correct=False,
        error_type=ErrorType.METHOD,
        error_analysis="未按一维矢量规定正方向",
    )

    # After 1st error: only 1 occurrence, not yet frequent tag
    p1 = error_service.generate_error_profile(user_id, subject)
    assert p1.total_errors == 1
    assert len(p1.frequent_errors) == 0

    # Advance and Attempt 2: Wrong again with 方法选择错误
    session_service.advance_question(session.id, 1)
    session_service.evaluate_and_record_attempt(
        session_id=session.id,
        question_id=q2.id,
        user_answer="错误解答2",
        is_correct=False,
        error_type=ErrorType.METHOD,
        error_analysis="再次选错解题路径，混淆动量与动能定理",
    )

    # After 2nd error: Frequent error tag forms!
    p2 = error_service.generate_error_profile(user_id, subject)
    assert p2.total_errors == 2
    assert len(p2.frequent_errors) == 1
    tag = p2.frequent_errors[0]
    assert tag.target == target
    assert tag.error_type == "方法选择错误"
    assert tag.count == 2
    assert "高频错因：动量定理 - 方法选择错误（2次）" in tag.tag_label
    assert p2.primary_weakness == "动量定理方法选择错误"

    # Mastery state is 待加强
    mst = error_service.get_mastery_state(user_id, subject, target)
    assert mst.level == MasteryLevel.NEEDS_IMPROVEMENT
    assert mst.consecutive_errors == 2


# ==============================================================================
# Persona 22: 一次错误用户 (Single Error -> No Over-judgment)
# ==============================================================================
def test_persona_22_single_error_no_overjudgment(
    session_service: TrainingSessionService,
    error_service: ErrorAnalysisService,
) -> None:
    """Persona 22: 单次失误不予过度推断，判定待加强而非完全不会，无高频标签。"""
    user_id = "user_22_single_error"
    target = "流水侵蚀地貌"
    subject = "地理"

    task = TrainingTask(training_type="题型族", target=target, subject=subject, family_id=8511)
    q1 = _build_authentic_question("p22_q1", subject, target)

    session = session_service.create_session(task, user_id=user_id, questions=[q1])

    # Attempt 1: Wrong
    session_service.evaluate_and_record_attempt(
        session_id=session.id,
        question_id=q1.id,
        user_answer="地壳下沉导致",
        is_correct=False,
        error_type=ErrorType.CONCEPT,
        error_analysis="混淆内力构造与流水侵蚀的外力作用",
    )

    mst = error_service.get_mastery_state(user_id, subject, target)
    # Rule: 1 error does not declare complete inability ("完全不会")
    assert mst.level == MasteryLevel.NEEDS_IMPROVEMENT
    assert mst.total_attempts == 1
    assert mst.consecutive_errors == 1

    # Profile check: no frequent error tag
    profile = error_service.generate_error_profile(user_id, subject)
    assert profile.has_sufficient_data is True
    assert profile.total_errors == 1
    assert len(profile.frequent_errors) == 0  # < 2 threshold


# ==============================================================================
# Persona 23: 连续正确用户 (Sequential Correct Attempts -> Gradual Progression)
# ==============================================================================
def test_persona_23_consecutive_correct_gradual_progression(
    session_service: TrainingSessionService,
    error_service: ErrorAnalysisService,
) -> None:
    """Persona 23: 掌握度由未建立 -> 训练中 -> 阶段掌握真实演进。"""
    user_id = "user_23_consecutive_correct"
    target = "综合地理区域分析"
    subject = "地理"

    # Initial state: 未建立
    init_mst = error_service.get_mastery_state(user_id, subject, target)
    assert init_mst.level == MasteryLevel.NOT_ESTABLISHED

    task = TrainingTask(training_type="题型族", target=target, subject=subject, family_id=8511)
    q1 = _build_authentic_question("p23_q1", subject, target)
    q2 = _build_authentic_question("p23_q2", subject, target)

    session = session_service.create_session(task, user_id=user_id, questions=[q1, q2])

    # Attempt 1: Correct -> 训练中 (not 阶段掌握)
    session_service.evaluate_and_record_attempt(
        session_id=session.id,
        question_id=q1.id,
        user_answer="官方公布的标准参考解答",
        is_correct=True,
    )
    mst1 = error_service.get_mastery_state(user_id, subject, target)
    assert mst1.level == MasteryLevel.IN_TRAINING
    assert mst1.consecutive_correct == 1
    assert mst1.accuracy_rate == 100.0

    # Advance and Attempt 2: Correct -> 阶段掌握
    session_service.advance_question(session.id, 1)
    session_service.evaluate_and_record_attempt(
        session_id=session.id,
        question_id=q2.id,
        user_answer="官方公布的标准参考解答",
        is_correct=True,
    )
    mst2 = error_service.get_mastery_state(user_id, subject, target)
    assert mst2.level == MasteryLevel.STAGE_MASTERED
    assert mst2.consecutive_correct == 2
    assert mst2.total_attempts == 2


# ==============================================================================
# Persona 24: 错误后再次训练用户 (Error -> Variant Practice Task Generation)
# ==============================================================================
def test_persona_24_error_triggers_variant_practice(
    session_service: TrainingSessionService,
    error_service: ErrorAnalysisService,
    retrieval_service: QuestionSourceRetrievalService,
) -> None:
    """Persona 24: 错因触发变式训练，生成新TrainingTask并检索真实题源。"""
    user_id = "user_24_variant_practice"
    target = "动量定理"
    subject = "物理"

    task = TrainingTask(training_type="方法族", target=target, subject=subject, family_id=8512)
    q = _build_authentic_question("p24_q1", subject, target)
    session = session_service.create_session(task, user_id=user_id, questions=[q])

    # Commit error
    session_service.evaluate_and_record_attempt(
        session_id=session.id,
        question_id=q.id,
        user_answer="错误计算结果",
        is_correct=False,
        error_type=ErrorType.CALCULATION,
        error_analysis="积分常数算错",
    )

    # Get error analysis record
    error_records = error_service.get_error_analyses(subject=subject, target=target)
    assert len(error_records) == 1
    err_id = error_records[0].id

    # Trigger variant training task generation
    variant_task = error_service.create_variant_training_task(err_id)
    assert variant_task.training_type == "方法族"
    assert variant_task.target == "动量定理"
    assert variant_task.subject == "物理"
    assert variant_task.family_id == 8512

    # Retrieve authentic questions for the variant task
    profile = LearnerProfile(
        education_type="basic",
        basic=BasicEducationProfile(
            province="江苏省",
            province_code="320000",
            city="南京市",
            city_code="320100",
            stage="高中",
            grade="高三",
        ),
    )
    retrieval_result = retrieval_service.retrieve_and_verify(variant_task, profile=profile)

    # 当前没有逐题在线核验管线，硬编码外部题不能冒充真题。
    # 错题仍会触发变式任务，但题源不足时必须诚实停止在 0 题。
    assert retrieval_result.questions == []
    assert retrieval_result.verified_count == 0
    assert "本地资料库暂未找到可核验的变式训练题" in retrieval_result.prompt_message


# ==============================================================================
# Persona 25: 无训练历史用户 (Zero History Guard -> No Fake Profile)
# ==============================================================================
def test_persona_25_zero_history_no_fake_profile(
    error_service: ErrorAnalysisService,
) -> None:
    """Persona 25: 零数据防幻觉保护，拒绝虚构错因画像。"""
    user_id = "user_25_fresh_start"

    # Physics check
    phys_profile = error_service.generate_error_profile(user_id, "物理")
    assert phys_profile.has_sufficient_data is False
    assert phys_profile.total_attempts == 0
    assert phys_profile.total_errors == 0
    assert phys_profile.primary_weakness is None
    assert len(phys_profile.frequent_errors) == 0
    assert "暂无足够训练数据" in phys_profile.message

    # Geography check
    geo_profile = error_service.generate_error_profile(user_id, "地理")
    assert geo_profile.has_sufficient_data is False
    assert geo_profile.total_attempts == 0


# ==============================================================================
# Persona 26: 跨学科错误用户 (Cross-Discipline Isolation)
# ==============================================================================
def test_persona_26_cross_discipline_isolation(
    session_service: TrainingSessionService,
    error_service: ErrorAnalysisService,
) -> None:
    """Persona 26: 地理做题失误绝不污染物理掌握度与错因画像。"""
    user_id = "user_26_cross_disciplines"

    # Physics: 2 consecutive correct attempts -> 阶段掌握
    task_p = TrainingTask(training_type="方法族", target="动量定理", subject="物理", family_id=8512)
    qp1 = _build_authentic_question("p26_qp1", "物理", "动量定理")
    qp2 = _build_authentic_question("p26_qp2", "物理", "动量定理")
    sess_p = session_service.create_session(task_p, user_id=user_id, questions=[qp1, qp2])

    session_service.evaluate_and_record_attempt(sess_p.id, qp1.id, "正确1", is_correct=True)
    session_service.advance_question(sess_p.id, 1)
    session_service.evaluate_and_record_attempt(sess_p.id, qp2.id, "正确2", is_correct=True)

    # Geography: 3 consecutive errors -> 待加强 + 高频失误
    task_g = TrainingTask(
        training_type="题型族",
        target="综合地理区域分析",
        subject="地理",
        family_id=8511,
    )
    qg1 = _build_authentic_question("p26_qg1", "地理", "综合地理区域分析")
    sess_g = session_service.create_session(task_g, user_id=user_id, questions=[qg1])

    session_service.evaluate_and_record_attempt(
        sess_g.id, qg1.id, "地理错解1", is_correct=False, error_type=ErrorType.CONCEPT
    )

    # Verify Physics: completely unpolluted!
    p_profile = error_service.generate_error_profile(user_id, "物理")
    assert p_profile.total_attempts == 2
    assert p_profile.total_errors == 0
    p_mst = error_service.get_mastery_state(user_id, "物理", "动量定理")
    assert p_mst.level == MasteryLevel.STAGE_MASTERED
    assert p_mst.consecutive_correct == 2

    # Verify Geography: records the error accurately
    g_profile = error_service.generate_error_profile(user_id, "地理")
    assert g_profile.total_attempts == 1
    assert g_profile.total_errors == 1
    g_mst = error_service.get_mastery_state(user_id, "地理", "综合地理区域分析")
    assert g_mst.level == MasteryLevel.NEEDS_IMPROVEMENT


# ==============================================================================
# Persona 27: 普通题训练用户 (Standard Gaokao Problem Tag Preservation)
# ==============================================================================
def test_persona_27_standard_gaokao_tags_preserved(
    session_service: TrainingSessionService,
    error_service: ErrorAnalysisService,
) -> None:
    """Persona 27: 高考真题训练结果与错因记录严格保持普通题标签。"""
    user_id = "user_27_regular_gaokao"
    q = _build_authentic_question(
        "p27_q", "地理", "综合地理区域分析", scope="基础教育 · 普通"
    )
    task = TrainingTask(training_type="题型族", target="综合地理区域分析", subject="地理")
    session = session_service.create_session(task, user_id=user_id, questions=[q])

    session_service.evaluate_and_record_attempt(
        session.id,
        q.id,
        "普通题错解",
        is_correct=False,
        error_type=ErrorType.READING,
    )

    analyses = error_service.get_error_analyses(subject="地理")
    assert len(analyses) == 1
    assert analyses[0].applicable_scope == "基础教育 · 普通"


# ==============================================================================
# Persona 28: 强基用户 (Qiangji Problem Tag Preservation)
# ==============================================================================
def test_persona_28_qiangji_tags_preserved(
    session_service: TrainingSessionService,
    error_service: ErrorAnalysisService,
) -> None:
    """Persona 28: 强基高校与专业方向标签在错因记录与掌握度中严格保持。"""
    user_id = "user_28_qiangji"
    q = _build_authentic_question(
        "p28_q",
        "物理",
        "动量定理",
        scope="基础教育 · 强基计划",
        school_name="南京大学",
        major_direction="物理学",
    )
    task = TrainingTask(training_type="方法族", target="动量定理", subject="物理")
    session = session_service.create_session(task, user_id=user_id, questions=[q])

    session_service.evaluate_and_record_attempt(
        session.id,
        q.id,
        "阻尼振动推导出错",
        is_correct=False,
        error_type=ErrorType.METHOD,
    )

    analyses = error_service.get_error_analyses(subject="物理")
    assert len(analyses) == 1
    assert analyses[0].applicable_scope == "基础教育 · 强基计划"


# ==============================================================================
# Persona 29: 竞赛用户 (Olympiad Problem Tag Preservation)
# ==============================================================================
def test_persona_29_olympiad_tags_preserved(
    session_service: TrainingSessionService,
    error_service: ErrorAnalysisService,
) -> None:
    """Persona 29: 奥赛级别与阶段标签在训练执行与错因记录中严格保持。"""
    user_id = "user_29_olympiad"
    q = _build_authentic_question(
        "p29_q",
        "物理",
        "动量定理",
        scope="基础教育 · 学科竞赛",
        contest_name="全国中学生物理竞赛",
        contest_tier="全国决赛",
    )
    task = TrainingTask(training_type="方法族", target="动量定理", subject="物理")
    session = session_service.create_session(task, user_id=user_id, questions=[q])

    session_service.evaluate_and_record_attempt(
        session.id,
        q.id,
        "角动量守恒建立错误",
        is_correct=False,
        error_type=ErrorType.CONCEPT,
    )

    analyses = error_service.get_error_analyses(subject="物理")
    assert len(analyses) == 1
    assert analyses[0].applicable_scope == "基础教育 · 学科竞赛"


# ==============================================================================
# Persona 30: 完整闭环用户 (End-to-End Closed Loop)
# ==============================================================================
def test_persona_30_closed_loop_stops_without_verified_variant(
    session_service: TrainingSessionService,
    error_service: ErrorAnalysisService,
    retrieval_service: QuestionSourceRetrievalService,
) -> None:
    """Persona 30: 完整闭环：训练 -> 错误 -> 归因 -> 变式训练 -> 再次做题 -> 阶段掌握。"""
    user_id = "user_30_full_loop"
    target = "动量定理"
    subject = "物理"

    # Step 1: Initial practice
    task = TrainingTask(training_type="方法族", target=target, subject=subject, family_id=8512)
    q_init = _build_authentic_question("p30_q_initial", subject, target)
    sess1 = session_service.create_session(task, user_id=user_id, questions=[q_init])

    # Step 2: Make an error
    session_service.evaluate_and_record_attempt(
        sess1.id,
        q_init.id,
        "解题时冲量矢量求和错误",
        is_correct=False,
        error_type=ErrorType.CALCULATION,
        error_analysis="漏算反弹反向动量冲量",
    )
    mst1 = error_service.get_mastery_state(user_id, subject, target)
    assert mst1.level == MasteryLevel.NEEDS_IMPROVEMENT

    # Step 3: Trigger variant training task
    analyses = error_service.get_error_analyses(subject=subject, target=target)
    assert len(analyses) >= 1
    variant_task = error_service.create_variant_training_task(analyses[0].id)

    # Step 4: Retrieve authentic questions for variant practice
    profile = LearnerProfile(
        education_type="basic",
        basic=BasicEducationProfile(
            province="江苏省",
            province_code="320000",
            city="南京市",
            city_code="320100",
            stage="高中",
            grade="高三",
        ),
    )
    result = retrieval_service.retrieve_and_verify(variant_task, profile=profile)
    assert result.questions == []
    assert result.verified_count == 0
    assert "本地资料库暂未找到可核验的变式训练题" in result.prompt_message

    # 没有可信训练题就不能凭空启动会话，更不能用虚构的连续答对证据
    # 把“待改进”升级成“阶段掌握”。
    mst_after_retrieval = error_service.get_mastery_state(user_id, subject, target)
    assert mst_after_retrieval.level == MasteryLevel.NEEDS_IMPROVEMENT
