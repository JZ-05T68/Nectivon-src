"""Durable import queue for multi-file batches (V086-301, schema v21).

Wave 2 proved the failure mode: the batch loop lived inside one Streamlit
script run, so navigating away mid-batch cancelled the run, the remaining
files vanished without a trace (one file had zero DB/log records), and
returning to the page showed nothing.  The queue is now persisted in the
database **before** the first file is processed:

* every uploaded file gets one row in ``import_queue`` with an explicit
  status, so the page can always answer "共 N 份 · 已完成 X · 进行中 Y ·
  等待 Z · 失败 W";
* a run that dies mid-file leaves the row in ``importing`` with the dead
  run id; the next page load marks it ``interrupted`` honestly instead of
  pretending nothing happened;
* resuming processes the still-``queued`` rows — navigation away may pause
  a batch, it can never erase it;
* files are isolated: one file's import/OCR/reading failure is recorded on
  its own row and never swallows the rest of the batch.

Status semantics keep import and AI reading separate (V086-306):
``imported`` means "已导入，AI 阅读未完成" — the document exists and is
usable, the reading is honestly reported as not done.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import uuid4

from src.database import Database, DatabaseError

LOGGER = logging.getLogger(__name__)

_QUEUE_STATUSES: Final = (
    "queued",
    "importing",
    "imported",
    "complete",
    "failed",
    "interrupted",
)
#: Statuses that mean "this batch still has work or needs attention".
_OPEN_STATUSES: Final = ("queued", "importing", "interrupted")
#: Neutral dedup outcome note — informational, never an error (V086-105).
DEDUP_NOTICE: Final = "该资料已存在，没有重复导入，也没有重复阅读。"
#: V086-212: v21 historical rows could carry SUCCESS text in error_message.
#: Exact-match repair set — known success strings only, never real errors.
_SUCCESS_TEXT_REPAIR_SET: Final = ("Agent 已逐页读完。",)


class ImportQueueError(DatabaseError):
    """An import-queue operation was refused or could not complete."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class ImportQueueEntry:
    """One uploaded file's durable processing state."""

    id: int
    batch_id: str
    filename: str
    status: str
    document_id: int | None
    error_message: str
    run_id: str
    created_at: str
    updated_at: str


def new_run_id() -> str:
    """One id per page-run that processes the queue."""

    return uuid4().hex


class ImportQueueService:
    """Persist and drive the per-file import state machine."""

    def __init__(self, database: Database) -> None:
        self._database = database

    # ------------------------------------------------------------ commands
    def enqueue_batch(self, filenames: list[str], *, batch_id: str | None = None) -> str:
        """Persist one row per file (into ``batch_id`` or a new batch).

        Passing an existing open ``batch_id`` appends files to that batch,
        which is how a resumed upload continues the same durable queue.
        """

        cleaned = [name.strip() for name in filenames if name and name.strip()]
        if not cleaned:
            raise ImportQueueError("导入队列至少需要一个文件名。")
        batch_id = batch_id or uuid4().hex
        timestamp = _utc_now()
        with self._database._connection() as connection:
            for filename in cleaned:
                connection.execute(
                    """
                    INSERT INTO import_queue(
                        batch_id, filename, status, document_id,
                        error_message, run_id, created_at, updated_at
                    ) VALUES (?, ?, 'queued', NULL, '', '', ?, ?)
                    """,
                    (batch_id, filename, timestamp, timestamp),
                )
        return batch_id

    def _set_status(
        self,
        entry_id: int,
        *,
        status: str,
        document_id: int | None = None,
        error_message: str | None = None,
        run_id: str | None = None,
    ) -> None:
        if status not in _QUEUE_STATUSES:
            raise ImportQueueError(f"导入队列状态无效：{status}")
        assignments = ["status = ?", "updated_at = ?"]
        values: list[object] = [status, _utc_now()]
        if document_id is not None:
            assignments.append("document_id = ?")
            values.append(document_id)
        if error_message is not None:
            assignments.append("error_message = ?")
            values.append(error_message[:2000])
        if run_id is not None:
            assignments.append("run_id = ?")
            values.append(run_id)
        values.append(entry_id)
        with self._database._connection() as connection:
            cursor = connection.execute(
                f"UPDATE import_queue SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            if cursor.rowcount == 0:
                raise ImportQueueError(f"导入队列条目不存在：{entry_id}")

    def start_entry(self, entry_id: int, *, run_id: str) -> None:
        self._set_status(entry_id, status="importing", run_id=run_id, error_message="")

    def mark_imported(self, entry_id: int, *, document_id: int, message: str = "") -> None:
        """Import (and OCR) done; AI reading pending or failed — honest state."""

        self._set_status(
            entry_id,
            status="imported",
            document_id=document_id,
            error_message=message,
        )

    def mark_complete(self, entry_id: int, *, document_id: int, message: str = "") -> None:
        self._set_status(
            entry_id,
            status="complete",
            document_id=document_id,
            error_message=message,
        )

    def mark_failed(self, entry_id: int, *, message: str) -> None:
        self._set_status(entry_id, status="failed", error_message=message)

    def mark_interrupted(self, entry_id: int, *, message: str) -> None:
        self._set_status(entry_id, status="interrupted", error_message=message)

    # ------------------------------------------------------------- queries
    def list_batch(self, batch_id: str) -> list[ImportQueueEntry]:
        with self._database._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM import_queue WHERE batch_id = ? ORDER BY id",
                (batch_id,),
            ).fetchall()
        return [self._entry_from_row(row) for row in rows]

    def get_entry(self, entry_id: int) -> ImportQueueEntry:
        with self._database._connection() as connection:
            row = connection.execute(
                "SELECT * FROM import_queue WHERE id = ?", (entry_id,)
            ).fetchone()
        if row is None:
            raise ImportQueueError(f"导入队列条目不存在：{entry_id}")
        return self._entry_from_row(row)

    def latest_open_batch_id(self) -> str | None:
        """The most recent batch that still has open work."""

        with self._database._connection() as connection:
            row = connection.execute(
                """
                SELECT batch_id FROM import_queue
                WHERE status IN ('queued', 'importing', 'interrupted')
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        return str(row[0]) if row is not None else None

    def latest_batch_id(self) -> str | None:
        with self._database._connection() as connection:
            row = connection.execute(
                "SELECT batch_id FROM import_queue ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return str(row[0]) if row is not None else None

    def recover_stale_inflight(self, active_run_id: str) -> list[ImportQueueEntry]:
        """Mark dead-run ``importing`` rows as ``interrupted``; return them.

        A row is stale when its run id differs from the currently active
        run: Nectivon is a local single-user product bound to 127.0.0.1, so
        a new run starting proves the previous run is no longer processing.
        Recovered rows keep their place in the batch and can be resumed.

        V086-310: "interrupted" covers different realities — the import
        itself may have finished (document id present) while only the AI
        reading / batch advance was cut. The recovery message states which
        one happened instead of always claiming「导入中途被中断」.

        V086-212: the recovery pass also strips historical SUCCESS text out
        of ``complete`` rows' error_message (exact-match, never real errors).
        """

        self._repair_success_message_rows()
        with self._database._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM import_queue WHERE status = 'importing' AND run_id != ?",
                (active_run_id,),
            ).fetchall()
        stale = [self._entry_from_row(row) for row in rows]
        recovered: list[ImportQueueEntry] = []
        for entry in stale:
            if entry.document_id is not None:
                message = (
                    "资料已导入，后续 AI 阅读被暂停（离开页面或关闭浏览器）；"
                    "重新选择同一文件可继续。"
                )
            else:
                message = (
                    "这份资料的导入尚未完成（离开页面或关闭浏览器）；"
                    "重新选择同一文件可继续，已完成内容不会丢失。"
                )
            self._set_status(entry.id, status="interrupted", error_message=message)
            # Report the post-recovery state, not the stale pre-read row.
            recovered.append(self.get_entry(entry.id))
        if recovered:
            LOGGER.info(
                "导入队列恢复：标记 %s 个中断条目（dead run）", len(recovered)
            )
        return recovered

    def _repair_success_message_rows(self) -> None:
        """V086-212: remove historical success text from complete rows.

        ``error_message`` must only ever carry error/failure semantics. The
        only v21 success string written by Round 2 code is cleared by exact
        match on ``complete`` rows; failed/imported rows and the neutral
        dedup notice are never touched. Idempotent, additive, no migration.
        """

        placeholders = ", ".join("?" for _ in _SUCCESS_TEXT_REPAIR_SET)
        with self._database._connection() as connection:
            cursor = connection.execute(
                f"""
                UPDATE import_queue
                SET error_message = '', updated_at = ?
                WHERE status = 'complete' AND error_message IN ({placeholders})
                """,
                (_utc_now(), *_SUCCESS_TEXT_REPAIR_SET),
            )
        if cursor.rowcount:
            LOGGER.info(
                "导入队列清理：移除 %s 行成功文案（V086-212）", cursor.rowcount
            )

    def queued_entries(self, batch_id: str) -> list[ImportQueueEntry]:
        """Entries still waiting to be processed (queued or interrupted)."""

        return [
            entry
            for entry in self.list_batch(batch_id)
            if entry.status in ("queued", "interrupted")
        ]

    @staticmethod
    def summarize(entries: list[ImportQueueEntry]) -> dict[str, int]:
        counts = {status: 0 for status in _QUEUE_STATUSES}
        for entry in entries:
            counts[entry.status] = counts.get(entry.status, 0) + 1
        return counts

    def _entry_from_row(self, row) -> ImportQueueEntry:
        return ImportQueueEntry(
            id=int(row["id"]),
            batch_id=str(row["batch_id"]),
            filename=str(row["filename"]),
            status=str(row["status"]),
            document_id=int(row["document_id"]) if row["document_id"] is not None else None,
            error_message=str(row["error_message"] or ""),
            run_id=str(row["run_id"] or ""),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
