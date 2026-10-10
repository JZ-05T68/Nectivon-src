"""Scoped factory-reset behavior for failed imports and diagnostic logs."""

from __future__ import annotations

from pathlib import Path

from src.database import Database
from src.factory_reset_service import reset_failed_import_traces
from src.import_queue_service import ImportQueueService
from src.models import ImportStatus


def test_reset_clears_only_failed_import_traces_and_log_contents(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "data" / "knowledge.db")
    source_file = tmp_path / "data" / "raw" / "kept.pdf"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"preserved source")
    document = database.create_document(
        title="保留的资料",
        filename=source_file.name,
        source_path=source_file,
        sha256="a" * 64,
        import_status=ImportStatus.COMPLETED,
    )

    completed_record = database.create_import_record(
        "kept.pdf", "保留的资料", "a" * 64
    )
    database.update_import_record(
        completed_record.id,
        status=ImportStatus.COMPLETED,
        document_id=document.id,
    )
    failed_record = database.create_import_record("broken.pdf", "", "b" * 64)
    database.update_import_record(failed_record.id, status=ImportStatus.FAILED)
    pending_record = database.create_import_record("pending.pdf", "", "c" * 64)

    queue = ImportQueueService(database)
    batch_id = queue.enqueue_batch(
        ["broken.pdf", "kept.pdf", "resume.pdf", "pending.pdf"]
    )
    failed, completed, interrupted, pending = queue.list_batch(batch_id)
    queue.mark_failed(failed.id, message="文件损坏")
    queue.mark_complete(completed.id, document_id=document.id)
    queue.mark_interrupted(interrupted.id, message="可继续")

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    first_log = logs_dir / "engineering-kb.log"
    rotated_log = logs_dir / "engineering-kb.log.1"
    non_log = logs_dir / "settings.json"
    material_with_log_in_name = logs_dir / "experiment.log.pdf"
    material_with_log_in_name.write_bytes(b"preserved PDF")
    first_log.write_bytes(b"ERROR failed import\n")
    rotated_log.write_bytes(b"old log\n")
    non_log.write_text("keep config", encoding="utf-8")
    workflow_file = tmp_path / "learning" / "notes.md"
    workflow_file.parent.mkdir()
    workflow_file.write_text("preserve workflow", encoding="utf-8")

    result = reset_failed_import_traces(database, logs_dir)

    assert result.failed_import_records == 1
    assert result.failed_queue_entries == 1
    assert result.cleared_log_files == 2
    assert result.cleared_log_bytes == len("ERROR failed import\nold log\n")
    assert result.log_failures == ()
    remaining_records = database.list_import_records()
    assert {record.id for record in remaining_records} == {
        completed_record.id,
        pending_record.id,
    }
    assert database.get_document(document.id) == document
    remaining_queue = queue.list_batch(batch_id)
    assert {entry.status for entry in remaining_queue} == {
        "complete",
        "interrupted",
        "queued",
    }
    assert all(Path(path).exists() for path in (source_file, workflow_file))
    assert workflow_file.read_text(encoding="utf-8") == "preserve workflow"
    assert first_log.exists() and first_log.read_text(encoding="utf-8") == ""
    assert rotated_log.exists() and rotated_log.read_text(encoding="utf-8") == ""
    assert non_log.read_text(encoding="utf-8") == "keep config"
    assert material_with_log_in_name.read_bytes() == b"preserved PDF"


def test_reset_is_idempotent_and_preserves_non_failed_import_history(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "knowledge.db")
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    log_file = logs_dir / "engineering-kb.log"
    log_file.write_text("old log", encoding="utf-8")
    completed_record = database.create_import_record("ok.pdf", "OK", "d" * 64)
    database.update_import_record(completed_record.id, status=ImportStatus.COMPLETED)

    first = reset_failed_import_traces(database, logs_dir)
    second = reset_failed_import_traces(database, logs_dir)

    assert first.failed_import_records == 0
    assert first.failed_queue_entries == 0
    assert first.cleared_log_files == 1
    assert first.cleared_log_bytes == len("old log")
    assert second.cleared_log_files == 1
    assert second.cleared_log_bytes == 0
    assert [record.id for record in database.list_import_records()] == [
        completed_record.id
    ]
