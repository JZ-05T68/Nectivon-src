"""Unit tests for Targeted Training models and service (Phase 2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.database import Database
from src.learning_workflow_service import QuestionOrganizationService
from src.targeted_training_models import (
    DEFAULT_SOURCE,
    STATUS_WAITING_FOR_QUESTION_GENERATION,
    TargetedTrainingError,
    TrainingTask,
)
from src.targeted_training_service import TargetedTrainingService


@pytest.fixture
def test_db(tmp_path: Path) -> Database:
    """Create a temporary initialized database for testing."""
    db_file = tmp_path / "test_knowledge.db"
    return Database(db_file)


@pytest.fixture
def service(test_db: Database) -> TargetedTrainingService:
    """Create a TargetedTrainingService backed by the temporary database."""
    return TargetedTrainingService(test_db)


def test_training_task_validation():
    """Verify TrainingTask dataclass constraints."""
    # 正常创建
    task = TrainingTask(
        training_type="题型族",
        target="自然过程分析类",
    )
    assert task.training_type == "题型族"
    assert task.target == "自然过程分析类"
    assert task.source == DEFAULT_SOURCE
    assert task.status == STATUS_WAITING_FOR_QUESTION_GENERATION
    assert task.to_contract_dict() == {
        "training_type": "题型族",
        "target": "自然过程分析类",
        "source": "第二层整理数据",
        "status": "waiting_for_question_generation",
    }

    # 非法类型
    with pytest.raises(TargetedTrainingError, match="训练类型无效"):
        TrainingTask(training_type="知识点族", target="动量定理")

    # 空目标
    with pytest.raises(TargetedTrainingError, match="训练目标名称不能为空"):
        TrainingTask(training_type="方法族", target="   ")

    # 伪造来源
    with pytest.raises(TargetedTrainingError, match="数据来源必须是"):
        TrainingTask(
            training_type="方法族",
            target="动量定理",
            source="互联网检索",
        )

    # 非法初始状态
    with pytest.raises(TargetedTrainingError, match="当前阶段初始状态必须是"):
        TrainingTask(
            training_type="方法族",
            target="动量定理",
            status="completed",
        )


def test_empty_database_handling(service: TargetedTrainingService):
    """Verify clean empty messages when no Layer 2 data exists."""
    assert service.get_available_targets("题型族") == []
    assert service.get_available_targets("方法族") == []
    assert service.get_empty_message("题型族") == "暂无可训练题型，请先完成归纳整理。"
    assert service.get_empty_message("方法族") == "暂无可训练方法，请先完成归纳整理。"


def test_abnormal_injection_rejection(service: TargetedTrainingService):
    """Verify that attempting to create a task for non-existent targets is rejected."""
    with pytest.raises(TargetedTrainingError, match="在第二层整理数据中不存在"):
        service.create_training_task("题型族", "不存在的神秘题型")

    with pytest.raises(TargetedTrainingError, match="在第二层整理数据中不存在"):
        service.create_training_task("方法族", "不存在的神秘方法")


def test_layer2_target_reading_and_task_generation(
    test_db: Database, service: TargetedTrainingService
):
    """Verify authentic Layer 2 family reading and task generation."""
    org = QuestionOrganizationService(test_db)

    # 模拟第二层归纳整理创建的族
    type_fam_id = org.create_family(
        family_kind="type",
        title="某区域分析类",
        description="区域综合分析",
    )
    method_fam_id = org.create_family(
        family_kind="method",
        title="动量定理",
        description="物理动量定理应用",
    )

    # 1. 验证题型族读取
    type_targets = service.get_available_targets("题型族")
    assert len(type_targets) == 1
    assert type_targets[0].title == "某区域分析类"
    assert type_targets[0].id == type_fam_id

    # 2. 验证方法族读取
    method_targets = service.get_available_targets("方法族")
    assert len(method_targets) == 1
    assert method_targets[0].title == "动量定理"
    assert method_targets[0].id == method_fam_id

    # 3. 验证生成任务对象
    task = service.create_training_task("方法族", "动量定理")
    assert task.training_type == "方法族"
    assert task.target == "动量定理"
    assert task.source == "第二层整理数据"
    assert task.status == "waiting_for_question_generation"
    assert task.family_id == method_fam_id

    # 4. 验证本地持久化查询
    tasks = service.list_training_tasks()
    assert len(tasks) == 1
    assert tasks[0].target == "动量定理"


def test_cross_discipline_isolation(test_db: Database, service: TargetedTrainingService):
    """Verify that Geography cannot see Physics methods and Physics cannot see Geography types."""
    org = QuestionOrganizationService(test_db)

    # 创建地理题型族与方法族
    org.create_family(family_kind="type", title="某区域分析类", description="地理区域分析")
    org.create_family(family_kind="method", title="自然地理过程判读", description="自然地理演变")

    # 创建物理方法族
    org.create_family(family_kind="method", title="动量定理", description="物理动量定理")
    org.create_family(family_kind="method", title="能量守恒", description="物理机械能守恒")

    # 1. 当学科限定为地理时
    geo_methods = service.get_available_targets("方法族", subject="地理")
    geo_method_titles = [f.title for f in geo_methods]
    assert "自然地理过程判读" in geo_method_titles
    assert "动量定理" not in geo_method_titles
    assert "能量守恒" not in geo_method_titles

    # 地理用户尝试注入物理方法族必须被拒绝
    with pytest.raises(TargetedTrainingError, match="不属于当前学科「地理」"):
        service.create_training_task("方法族", "动量定理", subject="地理")

    # 2. 当学科限定为物理时
    phy_methods = service.get_available_targets("方法族", subject="物理")
    phy_method_titles = [f.title for f in phy_methods]
    assert "动量定理" in phy_method_titles
    assert "能量守恒" in phy_method_titles
    assert "自然地理过程判读" not in phy_method_titles

    phy_types = service.get_available_targets("题型族", subject="物理")
    phy_type_titles = [f.title for f in phy_types]
    assert "某区域分析类" not in phy_type_titles

    # 物理用户尝试注入地理题型族必须被拒绝
    with pytest.raises(TargetedTrainingError, match="不属于当前学科「物理」"):
        service.create_training_task("题型族", "某区域分析类", subject="物理")
