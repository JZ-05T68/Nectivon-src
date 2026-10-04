"""Durable import queue regression (V086-301).

The batch must be persisted before processing starts, survive navigation
(via honest interrupted markers), resume without duplicating rows, and
isolate per-file failures so one broken file never swallows the rest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.database import Database
from src.import_queue_service import (
    DEDUP_NOTICE,
    ImportQueueError,
    ImportQueueService,
    new_run_id,
)


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    return Database(tmp_path / "queue.db")


def test_enqueue_batch_persists_before_processing(database: Database) -> None:
    service = ImportQueueService(database)
    batch_id = service.enqueue_batch(["04名校数学.pdf", "07常州数学.pdf", "08无锡数学.pdf"])
    entries = service.list_batch(batch_id)
    assert [entry.filename for entry in entries] == [
        "04名校数学.pdf",
        "07常州数学.pdf",
        "08无锡数学.pdf",
    ]
    assert all(entry.status == "queued" for entry in entries)


def test_enqueue_rejects_empty_batch(database: Database) -> None:
    with pytest.raises(ImportQueueError):
        ImportQueueService(database).enqueue_batch([])


def test_full_status_lifecycle_of_one_file(database: Database) -> None:
    service = ImportQueueService(database)
    batch_id = service.enqueue_batch(["a.pdf"])
    entry = service.list_batch(batch_id)[0]
    run_id = new_run_id()
    service.start_entry(entry.id, run_id=run_id)
    assert service.get_entry(entry.id).status == "importing"
    service.mark_imported(entry.id, document_id=7, message="资料已导入，AI 阅读未完成")
    imported = service.get_entry(entry.id)
    assert imported.status == "imported"
    assert imported.document_id == 7
    assert "AI 阅读未完成" in imported.error_message
    # V086-212: success is expressed by the status alone — a successful
    # completion writes NO text into error_message.
    service.mark_complete(entry.id, document_id=7)
    completed = service.get_entry(entry.id)
    assert completed.status == "complete"
    assert completed.error_message == ""
    service.mark_complete(entry.id, document_id=7, message="")
    assert service.get_entry(entry.id).error_message == ""


def test_import_failure_does_not_swallow_later_files(database: Database) -> None:
    """File-level isolation: failed row 1, later rows still queue/process."""

    service = ImportQueueService(database)
    batch_id = service.enqueue_batch(["broken.pdf", "good.pdf"])
    first, second = service.list_batch(batch_id)
    service.start_entry(first.id, run_id="run-1")
    service.mark_failed(first.id, message="导入失败：文件损坏")
    service.start_entry(second.id, run_id="run-1")
    service.mark_complete(second.id, document_id=9)
    statuses = {entry.filename: entry.status for entry in service.list_batch(batch_id)}
    assert statuses == {"broken.pdf": "failed", "good.pdf": "complete"}
    counts = ImportQueueService.summarize(service.list_batch(batch_id))
    assert counts["failed"] == 1
    assert counts["complete"] == 1


def test_dead_run_is_marked_interrupted_and_recoverable(database: Database) -> None:
    """Navigation away mid-batch: importing rows become honestly interrupted."""

    service = ImportQueueService(database)
    batch_id = service.enqueue_batch(["first.pdf", "second.pdf", "third.pdf"])
    entries = service.list_batch(batch_id)
    dead_run = "dead-run"
    service.start_entry(entries[0].id, run_id=dead_run)
    service.mark_complete(entries[0].id, document_id=1)
    service.start_entry(entries[1].id, run_id=dead_run)  # run died here

    recovered = service.recover_stale_inflight(new_run_id())
    assert [entry.filename for entry in recovered] == ["second.pdf"]
    assert recovered[0].status == "interrupted"
    # V086-310: this row never finished importing (no document id) — the
    # message must say so, not claim「导入中途被中断」for every case.
    assert "导入尚未完成" in recovered[0].error_message
    assert "可继续" in recovered[0].error_message
    # The interrupted file can be resumed; the completed file is not re-run.
    resumable = service.queued_entries(batch_id)
    assert [entry.filename for entry in resumable] == ["second.pdf", "third.pdf"]


def test_interrupted_after_import_reports_import_done(database: Database) -> None:
    """V086-310 case B: the file imported, the reading/batch was cut off."""

    service = ImportQueueService(database)
    batch_id = service.enqueue_batch(["如皋.pdf"])
    entry = service.list_batch(batch_id)[0]
    service.start_entry(entry.id, run_id="dead-run")
    service._set_status(entry.id, status="importing", document_id=42)

    recovered = service.recover_stale_inflight(new_run_id())
    assert len(recovered) == 1
    assert "资料已导入" in recovered[0].error_message
    assert "导入中途" not in recovered[0].error_message
    assert "暂停" in recovered[0].error_message


def test_recovery_repairs_success_text_in_error_message(
    database: Database,
) -> None:
    """V086-212: historical complete rows carrying the Round-2 success text
    are cleaned; real errors and the neutral dedup note are never touched."""

    service = ImportQueueService(database)
    batch_id = service.enqueue_batch(["done.pdf", "dup.pdf", "bad.pdf"])
    done, dup, bad = service.list_batch(batch_id)
    service.mark_complete(done.id, document_id=1)
    service._set_status(done.id, status="complete", error_message="Agent 已逐页读完。")
    service.mark_complete(dup.id, document_id=2, message=DEDUP_NOTICE)
    service.mark_failed(bad.id, message="导入失败：文件损坏")

    service.recover_stale_inflight(new_run_id())

    assert service.get_entry(done.id).error_message == ""
    assert service.get_entry(dup.id).error_message == DEDUP_NOTICE
    assert service.get_entry(bad.id).error_message == "导入失败：文件损坏"
    # Idempotent: a second pass changes nothing.
    service.recover_stale_inflight(new_run_id())
    assert service.get_entry(done.id).error_message == ""


def test_current_run_is_not_marked_interrupted(database: Database) -> None:
    service = ImportQueueService(database)
    batch_id = service.enqueue_batch(["x.pdf"])
    entry = service.list_batch(batch_id)[0]
    active_run = new_run_id()
    service.start_entry(entry.id, run_id=active_run)
    assert service.recover_stale_inflight(active_run) == []
    assert service.get_entry(entry.id).status == "importing"


def test_latest_open_batch_resolution(database: Database) -> None:
    service = ImportQueueService(database)
    assert service.latest_open_batch_id() is None
    batch_id = service.enqueue_batch(["a.pdf"])
    assert service.latest_open_batch_id() == batch_id
    entry = service.list_batch(batch_id)[0]
    service.mark_complete(entry.id, document_id=1)
    assert service.latest_open_batch_id() is None
    assert service.latest_batch_id() == batch_id


def test_append_to_open_batch_for_resume(database: Database) -> None:
    service = ImportQueueService(database)
    batch_id = service.enqueue_batch(["a.pdf"])
    appended = service.enqueue_batch(["b.pdf", "c.pdf"], batch_id=batch_id)
    assert appended == batch_id
    assert [entry.filename for entry in service.list_batch(batch_id)] == [
        "a.pdf",
        "b.pdf",
        "c.pdf",
    ]
