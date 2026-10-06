"""Stage 2 migration tests for targeted-training selection and isolation.

The historical manual suite depended on developer-machine databases. These
tests use short-lived databases populated through product APIs, so they
exercise Stage 2 contracts without pretending that historical staging data
exists in every checkout.
"""

from __future__ import annotations

from dataclasses import dataclass
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
from src.training_profile_models import (
    BasicEducationProfile,
    EducationType,
    LearnerProfile,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STAGING_DATABASE_PATHS = {
    "8511": PROJECT_ROOT / "staging-data" / "data" / "database" / "knowledge.db",
    "8512": PROJECT_ROOT / "staging-data-8512" / "data" / "database" / "knowledge.db",
}


@dataclass(frozen=True)
class Stage2Environment:
    """Independent, explicit Stage 2 test environments."""

    geography_database: Database
    physics_database: Database
    geography_profile: LearnerProfile
    physics_profile: LearnerProfile


def _create_profile(*, city: str, city_code: str) -> LearnerProfile:
    profile = LearnerProfile(
        education_type=EducationType.BASIC,
        basic=BasicEducationProfile(
            province="江苏省",
            province_code="32",
            city=city,
            city_code=city_code,
            stage="高中",
            grade="高三",
        ),
    )
    profile.validate_isolation()
    return profile


def _seed_families(
    database: Database,
    families: tuple[tuple[str, str, str], ...],
) -> None:
    organization = QuestionOrganizationService(database)
    for family_kind, title, description in families:
        organization.create_family(
            family_kind=family_kind,
            title=title,
            description=description,
        )


@pytest.fixture
def stage2_environment(tmp_path: Path) -> Stage2Environment:
    """Build Stage 2 data via public APIs, never from historical DB copies."""

    geography_database = Database(
        tmp_path / "staging-data" / "data" / "database" / "knowledge.db"
    )
    physics_database = Database(
        tmp_path / "staging-data-8512" / "data" / "database" / "knowledge.db"
    )

    _seed_families(
        geography_database,
        (
            ("type", "某区域分析类", "地理区域综合分析"),
            ("type", "自然过程分析类", "地理自然过程分析"),
            ("type", "人地关系综合分析类", "地理人地关系综合分析"),
            ("method", "自然地理过程判读", "地理自然过程判读"),
            ("method", "区位因素分析", "地理区位因素分析"),
            # Present in the same DB to prove subject filtering, rather than
            # merely relying on physical separation between database files.
            ("method", "抛体运动射程最大化：消参后用sin2a值域", "物理抛体运动"),
        ),
    )
    _seed_families(
        physics_database,
        (
            ("method", "动量定理", "物理动量定理"),
            ("method", "能量守恒", "物理机械能守恒"),
            ("method", "图像分析", "物理图像分析"),
            ("method", "递推分析", "物理递推分析"),
            ("type", "某区域分析类", "地理区域综合分析"),
        ),
    )

    return Stage2Environment(
        geography_database=geography_database,
        physics_database=physics_database,
        geography_profile=_create_profile(city="南京市", city_code="3201"),
        physics_profile=_create_profile(city="苏州市", city_code="3205"),
    )


def test_stage2_staging_paths_derive_from_repository_root() -> None:
    """Staging locations remain relocatable and independent of process CWD."""

    assert STAGING_DATABASE_PATHS == {
        "8511": PROJECT_ROOT / "staging-data" / "data" / "database" / "knowledge.db",
        "8512": PROJECT_ROOT
        / "staging-data-8512"
        / "data"
        / "database"
        / "knowledge.db",
    }
    assert all(path.is_relative_to(PROJECT_ROOT) for path in STAGING_DATABASE_PATHS.values())


class TestAntigravity8511Geography:
    """Validate the high-school Geography profile, targets, and task flow."""

    @pytest.fixture(autouse=True)
    def setup_tool(self, stage2_environment: Stage2Environment) -> None:
        self.profile = stage2_environment.geography_profile
        self.service = TargetedTrainingService(stage2_environment.geography_database)
        self.subject = "地理"

    def test_antigravity_read_8511_question_type_families(self) -> None:
        assert self.profile.basic is not None
        assert self.profile.basic.province == "江苏省"
        assert self.profile.basic.city == "南京市"
        targets = self.service.get_available_targets("题型族", subject=self.subject)
        assert {target.title for target in targets} == {
            "某区域分析类",
            "自然过程分析类",
            "人地关系综合分析类",
        }

    def test_antigravity_generate_geography_training_task(self) -> None:
        task = self.service.create_training_task(
            training_type="题型族",
            target="某区域分析类",
            subject=self.subject,
        )

        assert isinstance(task, TrainingTask)
        assert task.source == DEFAULT_SOURCE
        assert task.status == STATUS_WAITING_FOR_QUESTION_GENERATION
        assert task.subject == "地理"
        assert task.to_contract_dict() == {
            "training_type": "题型族",
            "target": "某区域分析类",
            "source": "第二层整理数据",
            "status": "waiting_for_question_generation",
        }
        persisted = self.service.list_training_tasks()
        assert len(persisted) == 1
        assert persisted[0].target == task.target
        assert persisted[0].status == STATUS_WAITING_FOR_QUESTION_GENERATION


class TestCodex8512Physics:
    """Validate the high-school Physics profile, targets, and task flow."""

    @pytest.fixture(autouse=True)
    def setup_tool(self, stage2_environment: Stage2Environment) -> None:
        self.profile = stage2_environment.physics_profile
        self.service = TargetedTrainingService(stage2_environment.physics_database)
        self.subject = "物理"

    def test_codex_read_8512_method_families(self) -> None:
        assert self.profile.basic is not None
        assert self.profile.basic.city == "苏州市"
        targets = self.service.get_available_targets("方法族", subject=self.subject)
        assert {target.title for target in targets} == {
            "动量定理",
            "能量守恒",
            "图像分析",
            "递推分析",
        }

    def test_codex_generate_physics_training_task(self) -> None:
        task = self.service.create_training_task(
            training_type="方法族",
            target="动量定理",
            subject=self.subject,
        )

        assert task.training_type == "方法族"
        assert task.target == "动量定理"
        assert task.source == DEFAULT_SOURCE
        assert task.status == STATUS_WAITING_FOR_QUESTION_GENERATION
        assert task.subject == "物理"
        assert self.service.list_training_tasks() == [task]


class TestQwenDynamicSwitch:
    """Simulate switching family types and verify dynamic target changes."""

    @pytest.fixture(autouse=True)
    def setup_tool(self, stage2_environment: Stage2Environment) -> None:
        self.service = TargetedTrainingService(stage2_environment.geography_database)

    def test_qwen_type_method_dynamic_switch(self) -> None:
        type_titles = {
            target.title
            for target in self.service.get_available_targets("题型族", subject="地理")
        }
        method_titles = {
            target.title
            for target in self.service.get_available_targets("方法族", subject="地理")
        }

        assert {"某区域分析类", "自然过程分析类"} <= type_titles
        assert type_titles.isdisjoint(method_titles)
        assert {"自然地理过程判读", "区位因素分析"} <= method_titles

    def test_qwen_switch_and_generate_task(self) -> None:
        task = self.service.create_training_task(
            training_type="方法族",
            target="自然地理过程判读",
            subject="地理",
        )
        assert task.training_type == "方法族"
        assert task.target == "自然地理过程判读"
        assert task.status == STATUS_WAITING_FOR_QUESTION_GENERATION


class TestDoubaoWorkerBoundaries:
    """Validate database isolation, subject isolation, and rejection paths."""

    def test_doubao_cross_discipline_isolation(
        self, stage2_environment: Stage2Environment
    ) -> None:
        geography_service = TargetedTrainingService(stage2_environment.geography_database)
        physics_service = TargetedTrainingService(stage2_environment.physics_database)

        geography_methods = {
            item.title
            for item in geography_service.get_available_targets("方法族", subject="地理")
        }
        physics_types = {
            item.title
            for item in physics_service.get_available_targets("题型族", subject="物理")
        }
        assert "抛体运动射程最大化：消参后用sin2a值域" not in geography_methods
        assert "某区域分析类" not in physics_types
        assert geography_service.list_training_tasks() == []
        assert physics_service.list_training_tasks() == []

    def test_doubao_empty_data_handling(self, tmp_path: Path) -> None:
        empty_service = TargetedTrainingService(Database(tmp_path / "fresh_empty_user.db"))

        assert empty_service.get_available_targets("题型族") == []
        assert empty_service.get_empty_message("题型族") == "暂无可训练题型，请先完成归纳整理。"
        assert empty_service.get_available_targets("方法族") == []
        assert empty_service.get_empty_message("方法族") == "暂无可训练方法，请先完成归纳整理。"

    def test_doubao_abnormal_injection_rejection(
        self, stage2_environment: Stage2Environment
    ) -> None:
        geography_service = TargetedTrainingService(stage2_environment.geography_database)

        with pytest.raises(TargetedTrainingError, match="在第二层整理数据中不存在"):
            geography_service.create_training_task(
                training_type="题型族",
                target="量子力学在古代农业中的应用族",
                subject="地理",
            )

        with pytest.raises(TargetedTrainingError, match="不属于当前学科「地理」"):
            geography_service.create_training_task(
                training_type="方法族",
                target="抛体运动射程最大化：消参后用sin2a值域",
                subject="地理",
            )
