"""Targeted Training & Goal Selection Service (Phase 2).

Manages reading Layer 2 organized data (QuestionFamily), subject/discipline
isolation, training target validation, and generation of TrainingTask objects.
Strictly offline, local SQLite persistence, with zero question generation or
external model calls in this phase.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

from src.database import Database
from src.learning_workflow_service import QuestionFamily, QuestionOrganizationService
from src.targeted_training_models import (
    DEFAULT_SOURCE,
    STATUS_WAITING_FOR_QUESTION_GENERATION,
    TRAINING_TYPE_TO_FAMILY_KIND,
    TRAINING_TYPES,
    TargetedTrainingError,
    TrainingTask,
)

LOGGER = logging.getLogger(__name__)

GEOGRAPHY_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "地理", "区域", "自然过程", "人地关系", "区位", "人口", "城市",
        "气候", "等高线", "地貌", "河流", "洋流", "沙尘", "3S", "德雷克海峡",
        "农业", "工业", "交通", "环境", "水文", "地质", "如皋", "苏州",
        "南京", "常州", "扬州", "无锡", "南通", "宿迁", "镇江", "赫拉特",
        "秦岭站", "南极", "条田", "成因分析", "空间分布", "冰进", "拉尼娜",
    }
)

PHYSICS_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "物理", "动量", "动量定理", "能量", "能量守恒", "图像分析", "递推分析",
        "抛体", "电路", "电场", "磁场", "导线", "粒子", "电子", "速度",
        "加速度", "力学", "牛顿", "欧姆", "焦耳", "法拉第", "洛伦兹",
        "光电", "多普勒", "振动", "波动", "热力学", "动能", "重力",
        "碰撞", "单摆", "平抛", "圆周", "消参", "sin2a", "KVL", "KCL",
    }
)


class TargetedTrainingService:
    """Service handling target selection and task generation for targeted training."""

    def __init__(
        self,
        database: Database | Path | str,
        organization_service: QuestionOrganizationService | None = None,
    ) -> None:
        if isinstance(database, (str, Path)):
            self._db = Database(Path(database))
        else:
            self._db = database
        self._org = organization_service or QuestionOrganizationService(self._db)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Ensure targeted training tasks table exists for local audit."""
        with self._db._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS targeted_training_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    training_type TEXT NOT NULL,
                    target TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    family_id INTEGER,
                    subject TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )

    def determine_family_subject(self, family: QuestionFamily) -> str | None:
        """Infer or retrieve the subject discipline for a given family."""
        # 1. 检查族标题和描述中的关键词
        text = f"{family.title} {family.description} {family.derivation}"
        geo_hits = sum(1 for kw in GEOGRAPHY_KEYWORDS if kw in text)
        phy_hits = sum(1 for kw in PHYSICS_KEYWORDS if kw in text)

        # 2. 检查关联成员题目的来源文档标题和题干
        try:
            members = self._org.list_family_members(family.id)
            for _, question, _ in members:
                q_text = f"{question.stem_text} {question.source_document_title_snapshot}"
                if any(kw in q_text for kw in GEOGRAPHY_KEYWORDS):
                    geo_hits += 2
                if any(kw in q_text for kw in PHYSICS_KEYWORDS):
                    phy_hits += 2
        except Exception:
            pass

        if geo_hits > phy_hits and geo_hits > 0:
            return "地理"
        if phy_hits > geo_hits and phy_hits > 0:
            return "物理"
        return None

    def get_available_targets(
        self,
        training_type: str,
        subject: str | None = None,
    ) -> list[QuestionFamily]:
        """Fetch Layer 2 families for the specified training type and subject.

        Parameters
        ----------
        training_type:
            Must be '题型族' or '方法族'.
        subject:
            Optional subject filter (e.g. '地理', '物理') to enforce cross-discipline
            isolation.

        Returns
        -------
        list[QuestionFamily]:
            List of authentic Layer 2 families matching the criteria.
        """
        if training_type not in TRAINING_TYPES:
            raise TargetedTrainingError(
                f"训练类型无效：{training_type}，必须是 '题型族' 或 '方法族'。"
            )

        family_kind = TRAINING_TYPE_TO_FAMILY_KIND[training_type]
        all_families = self._org.list_families(family_kind=family_kind)

        if not subject or not subject.strip():
            return all_families

        clean_subject = subject.strip()
        filtered: list[QuestionFamily] = []
        for fam in all_families:
            fam_subject = self.determine_family_subject(fam)
            if clean_subject == "地理":
                # 地理学科：保留明确为地理的族，严格排除物理族
                is_phy = any(kw in fam.title for kw in PHYSICS_KEYWORDS)
                if fam_subject == "地理" or (fam_subject is None and not is_phy):
                    filtered.append(fam)
            elif clean_subject == "物理":
                # 物理学科：保留明确为物理的族，严格排除地理族
                is_geo = any(kw in fam.title for kw in GEOGRAPHY_KEYWORDS)
                if fam_subject == "物理" or (fam_subject is None and not is_geo):
                    filtered.append(fam)
            else:
                # 其他学科：如未特别排除，按正常匹配
                if fam_subject == clean_subject or fam_subject is None:
                    filtered.append(fam)

        return filtered

    def create_training_task(
        self,
        training_type: str,
        target: str,
        subject: str | None = None,
    ) -> TrainingTask:
        """Create and persist a targeted training task object.

        Validates that the target actually exists in Layer 2 organized data
        for the given training type and discipline scope. Rejects abnormal /
        fabricated targets.
        """
        if training_type not in TRAINING_TYPES:
            raise TargetedTrainingError(
                f"训练类型无效：{training_type}，必须是 '题型族' 或 '方法族'。"
            )

        if not target or not target.strip():
            raise TargetedTrainingError("训练目标名称不能为空。")

        target_clean = target.strip()
        available_targets = self.get_available_targets(training_type, subject=subject)
        matched_family = next(
            (f for f in available_targets if f.title == target_clean), None
        )

        if matched_family is None:
            # 检查是否属于其他学科或根本不存在
            all_kind_families = self._org.list_families(
                family_kind=TRAINING_TYPE_TO_FAMILY_KIND[training_type]
            )
            exists_in_other_scope = any(f.title == target_clean for f in all_kind_families)
            if exists_in_other_scope:
                raise TargetedTrainingError(
                    f"训练目标「{target_clean}」不属于当前学科「{subject or '未指定'}」，"
                    "跨学科目标已被隔离并拒绝生成任务。"
                )
            raise TargetedTrainingError(
                f"训练目标「{target_clean}」在第二层整理数据中不存在，"
                "禁止使用未整理或伪造的训练目标。"
            )

        task = TrainingTask(
            training_type=training_type,
            target=target_clean,
            source=DEFAULT_SOURCE,
            status=STATUS_WAITING_FOR_QUESTION_GENERATION,
            family_id=matched_family.id,
            subject=subject,
        )

        # 记录到本地 SQLite 数据表
        with self._db._connection() as conn:
            conn.execute(
                """
                INSERT INTO targeted_training_tasks (
                    training_type, target, source, status, family_id, subject, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.training_type,
                    task.target,
                    task.source,
                    task.status,
                    task.family_id,
                    task.subject,
                    task.created_at,
                ),
            )

        LOGGER.info(
            "已生成针对训练任务：%s · %s (family_id=%s, subject=%s)",
            task.training_type,
            task.target,
            task.family_id,
            task.subject,
        )
        return task

    def list_training_tasks(self, limit: int = 50) -> list[TrainingTask]:
        """List recently created training tasks from the local database."""
        with self._db._connection() as conn:
            rows = conn.execute(
                """
                SELECT training_type, target, source, status, family_id, subject, created_at
                FROM targeted_training_tasks
                ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            TrainingTask(
                training_type=row["training_type"],
                target=row["target"],
                source=row["source"],
                status=row["status"],
                family_id=row["family_id"],
                subject=row["subject"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    @staticmethod
    def get_empty_message(training_type: str) -> str:
        """Return canonical empty prompt according to product specifications."""
        if training_type == "题型族":
            return "暂无可训练题型，请先完成归纳整理。"
        if training_type == "方法族":
            return "暂无可训练方法，请先完成归纳整理。"
        return "暂无可训练内容，请先完成归纳整理。"
