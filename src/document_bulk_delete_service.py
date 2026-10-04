"""Bulk document deletion as a thin orchestration layer (overnight §16).

Product rule: there is NO second deletion engine.  Every document goes
through the existing staged :class:`src.document_deletion_service.DocumentDeletionService`
(preview → validation → quarantine manifest → transaction → residue check →
recovery) exactly once per document.  This module only:

1. aggregates the per-document previews into one batch summary;
2. executes deletions one document at a time, isolating failures so the
   37th document failing can never corrupt the state of the other 36;
3. reports per-document outcomes so failed items can be retried.

Learning assets (题目/族/二级结论/掌握记录) are NOT deleted: the single
deletion service detaches their document/page provenance while preserving
evidence relationships and confirmation state — the preview says so explicitly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.document_deletion_service import (
    DocumentDeletionError,
    DocumentDeletionService,
)
from src.models import DocumentDeletionPreview

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BulkDeleteOutcome:
    """Per-document result of one bulk deletion run."""

    document_id: int
    document_title: str
    deleted: bool
    message: str = ""


@dataclass(slots=True)
class BulkDeletePreview:
    """Aggregated read-only impact of deleting several documents."""

    previews: list[DocumentDeletionPreview] = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)

    @property
    def document_count(self) -> int:
        return len(self.previews)

    @property
    def page_count(self) -> int:
        return sum(preview.page_count for preview in self.previews)

    @property
    def total_size_bytes(self) -> int:
        return sum(preview.total_size_bytes for preview in self.previews)

    @property
    def surviving_learning_questions(self) -> int:
        """Learning question assets that stay (detached) after deletion."""

        return sum(preview.learning_question_count for preview in self.previews)

    @property
    def surviving_learning_families(self) -> int:
        return sum(preview.learning_family_count for preview in self.previews)

    def missing_or_anomalous(self) -> list[tuple[int, list[str]]]:
        return [
            (preview.document_id, list(preview.path_anomalies))
            for preview in self.previews
            if preview.path_anomalies
        ]


class DocumentBulkDeleteService:
    """One-by-one orchestration over the staged single-document deleter."""

    def __init__(self, deletion_service: DocumentDeletionService) -> None:
        self._deletion_service = deletion_service

    def preview(self, document_ids: list[int]) -> BulkDeletePreview:
        """Aggregate per-document previews; missing documents are errors."""

        aggregated = BulkDeletePreview()
        for document_id in document_ids:
            try:
                aggregated.previews.append(
                    self._deletion_service.preview_document_deletion(document_id)
                )
            except DocumentDeletionError as exc:
                aggregated.errors.append((document_id, str(exc)))
        return aggregated

    def execute(self, document_ids: list[int]) -> list[BulkDeleteOutcome]:
        """Delete each document independently; one failure never stops the rest."""

        outcomes: list[BulkDeleteOutcome] = []
        for document_id in document_ids:
            try:
                preview = self._deletion_service.preview_document_deletion(document_id)
                self._deletion_service.delete_document(
                    document_id, expected_title=preview.document_title
                )
            except DocumentDeletionError as exc:
                LOGGER.warning("批量删除单项失败：document_id=%s error=%s", document_id, exc)
                outcomes.append(
                    BulkDeleteOutcome(
                        document_id=document_id,
                        document_title="",
                        deleted=False,
                        message=str(exc),
                    )
                )
            except Exception as exc:  # noqa: BLE001 - isolation is the contract
                LOGGER.exception("批量删除单项异常：document_id=%s", document_id)
                outcomes.append(
                    BulkDeleteOutcome(
                        document_id=document_id,
                        document_title="",
                        deleted=False,
                        message=f"未预期错误：{exc}",
                    )
                )
            else:
                outcomes.append(
                    BulkDeleteOutcome(
                        document_id=document_id,
                        document_title=preview.document_title,
                        deleted=True,
                    )
                )
        return outcomes

    @staticmethod
    def failed_ids(outcomes: list[BulkDeleteOutcome]) -> list[int]:
        return [outcome.document_id for outcome in outcomes if not outcome.deleted]

    @staticmethod
    def summarize(outcomes: list[BulkDeleteOutcome]) -> str:
        succeeded = sum(1 for outcome in outcomes if outcome.deleted)
        failed = len(outcomes) - succeeded
        return f"成功 {succeeded} 份，失败 {failed} 份。"
