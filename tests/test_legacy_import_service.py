"""Legacy import service tests (v0.8.6 duty A foundation).

The source fixture is a real workspace built through the project's own
``Database`` layer, then shaped back to a v0.8.5 (schema v17) database by
dropping the v18 table — faithful because migration v18 only adds the
import ledger.  All imports run into throwaway temp roots; the source is
asserted byte-identical after every scenario.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from src.database import Database
from src.legacy_import_service import (
    LegacyImportError,
    LegacyImportService,
)
from src.migrations import SCHEMA_VERSION


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


#: Objects introduced after schema v17 (v18 ledger + v19 learning layer).
_POST_V17_OBJECTS = (
    "question_items_fts_insert",
    "question_items_fts_delete",
    "question_items_fts_update",
    "question_search",
    "question_items",
    "question_item_evidence",
    "question_families",
    "question_family_members",
    "conclusion_revisions",
    "mastery_records",
    "mastery_profiles",
    "output_collections",
    "output_collection_items",
    "two_wings_links",
    "legacy_imports",
)


def _shape_to_v085(database_path: Path) -> None:
    """Re-shape a freshly migrated database into a v0.8.5 (schema v17) one.

    Faithful because migrations v18/v19 only ADD tables: dropping them and
    their ledger rows yields exactly what the v0.8.5 release creates.
    """

    with closing(sqlite3.connect(database_path, timeout=30.0)) as connection:
        for name in _POST_V17_OBJECTS:
            connection.execute(f"DROP TRIGGER IF EXISTS {name}")
            connection.execute(f"DROP TABLE IF EXISTS {name}")
        connection.execute("DELETE FROM schema_migrations WHERE version > 17")
        connection.commit()


@pytest.fixture()
def v085_workspace(tmp_path: Path) -> Path:
    """A real v0.8.5-shaped workspace with one document, pages, and assets."""

    root = tmp_path / "旧版 工作区"
    data = root / "data"
    (data / "raw").mkdir(parents=True)
    (data / "pages" / "1").mkdir(parents=True)
    (data / "markdown" / "1").mkdir(parents=True)

    database = Database(data / "database" / "knowledge.db")
    raw_pdf = data / "raw" / "试卷A.pdf"
    raw_pdf.write_bytes(b"%PDF-1.7 fake paper A")
    document = database.create_document(
        title="高三数学 试卷A",
        filename="试卷A.pdf",
        source_path=raw_pdf,
        sha256=hashlib.sha256(raw_pdf.read_bytes()).hexdigest(),
        page_count=2,
        import_status="completed",
    )
    for page_number in (1, 2):
        image = data / "pages" / "1" / f"page-{page_number}.png"
        image.write_bytes(f"png-{page_number}".encode())
        markdown = data / "markdown" / "1" / f"page-{page_number}.md"
        markdown.write_text(
            f"# 第 {page_number} 页\n\n设函数 f(x) 求导数。",
            encoding="utf-8",
        )
        database.create_page(
            document_id=document.id,
            page_number=page_number,
            image_path=image,
            extracted_text=f"第{page_number}题 已知 f(x)=x^2 求导。",
            markdown_content=markdown.read_text(encoding="utf-8"),
            markdown_path=markdown,
            status="ready",
        )
    _shape_to_v085(database.database_path)
    return root


def _search_page_ids(database_path: Path, term: str) -> list[int]:
    with closing(sqlite3.connect(database_path, timeout=30.0)) as connection:
        return [
            int(row[0])
            for row in connection.execute(
                "SELECT rowid FROM page_search WHERE page_search MATCH ?",
                (term,),
            ).fetchall()
        ]


def test_inspect_reports_supported_v085_workspace(v085_workspace: Path) -> None:
    inspection = LegacyImportService().inspect(v085_workspace)

    assert inspection.verdict == "supported"
    assert inspection.source_schema_version == 17
    assert inspection.source_kb_uuid is not None
    assert inspection.counts["documents"] == 1
    assert inspection.counts["pages"] == 2
    assert inspection.asset_file_counts["raw"] == 1
    assert inspection.asset_file_counts["pages"] == 2


def test_import_completes_and_preserves_everything(v085_workspace: Path, tmp_path: Path) -> None:
    source_db = v085_workspace / "data" / "database" / "knowledge.db"
    source_sha = _file_sha256(source_db)
    target = tmp_path / "目标 工作区"

    report = LegacyImportService().execute_import(v085_workspace, target)

    assert report.status == "completed"
    assert report.final_schema_version == SCHEMA_VERSION
    target_db = target / "data" / "database" / "knowledge.db"
    assert target_db.is_file()
    with closing(sqlite3.connect(target_db)) as connection:
        version = int(
            connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()[0]
        )
        ledger = connection.execute(
            "SELECT source_sha256, status, document_count FROM legacy_imports"
        ).fetchall()
        document_count = int(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
    assert version == SCHEMA_VERSION
    assert ledger and ledger[0][0] == report.source_sha256 and ledger[0][1] == "completed"
    assert document_count == 1
    assert report.counts["pages"] == 2
    # 全文检索在导入后的工作区照常工作（无幽灵、无漏收录）
    assert _search_page_ids(target_db, "导数")
    # 源工作区字节级未改动
    assert _file_sha256(source_db) == source_sha
    assert (target / "legacy_import_report.json").is_file()


def test_import_rebases_recorded_paths_to_target(v085_workspace: Path, tmp_path: Path) -> None:
    target = tmp_path / "rebase 目标"

    LegacyImportService().execute_import(v085_workspace, target)

    target_db = target / "data" / "database" / "knowledge.db"
    with closing(sqlite3.connect(target_db)) as connection:
        source_paths = [
            Path(row[0])
            for row in connection.execute("SELECT source_path FROM documents").fetchall()
        ]
        image_paths = [
            Path(row[0])
            for row in connection.execute("SELECT image_path FROM pages").fetchall()
        ]
    for path in source_paths + image_paths:
        assert path.is_relative_to(target / "data"), path
        assert path.is_file(), path


def test_import_dry_run_leaves_target_empty(v085_workspace: Path, tmp_path: Path) -> None:
    target = tmp_path / "dry 目标"

    report = LegacyImportService().execute_import(v085_workspace, target, dry_run=True)

    assert report.status == "dry_run"
    assert report.final_schema_version == SCHEMA_VERSION
    assert not target.exists()


def test_import_refuses_nonempty_target(v085_workspace: Path, tmp_path: Path) -> None:
    target = tmp_path / "已占用"
    (target / "data").mkdir(parents=True)

    with pytest.raises(LegacyImportError, match="拒绝覆盖"):
        LegacyImportService().execute_import(v085_workspace, target)
    assert not (target / "data" / "database").exists()


def test_import_refuses_overlapping_roots(v085_workspace: Path, tmp_path: Path) -> None:
    service = LegacyImportService()
    with pytest.raises(LegacyImportError, match="同一个目录|互相包含"):
        service.execute_import(v085_workspace, v085_workspace)
    with pytest.raises(LegacyImportError, match="互相包含"):
        service.execute_import(v085_workspace, v085_workspace / "内嵌目标")


def test_import_refuses_path_escape(v085_workspace: Path, tmp_path: Path) -> None:
    """A record pointing outside the source data dir must abort the import."""

    outside = tmp_path / "outside-secret.txt"
    outside.write_text("不属于工作区的文件", encoding="utf-8")
    with closing(
        sqlite3.connect(v085_workspace / "data" / "database" / "knowledge.db")
    ) as connection:
        connection.execute(
            "UPDATE documents SET source_path = ? WHERE id = 1", (str(outside),)
        )
        connection.commit()

    target = tmp_path / "安全目标"
    with pytest.raises(LegacyImportError, match="超出源数据目录"):
        LegacyImportService().execute_import(v085_workspace, target)
    assert not target.exists()


def test_import_accepts_current_format_snapshot(v085_workspace: Path, tmp_path: Path) -> None:
    """A source already at the current schema imports as a snapshot copy."""

    current_source = tmp_path / "当前格式源"
    current_source_data = current_source / "data"
    current_source_data.mkdir(parents=True)
    Database(current_source_data / "database" / "knowledge.db")
    (current_source_data / "raw").mkdir()

    report = LegacyImportService().execute_import(current_source, tmp_path / "快照目标")

    assert report.status == "completed"
    assert report.counts["documents"] == 0


def test_import_refuses_future_schema(tmp_path: Path) -> None:
    future = tmp_path / "未来版本"
    database = Database(future / "data" / "database" / "knowledge.db")
    with closing(sqlite3.connect(database.database_path)) as connection:
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (9999, 'x')"
        )
        connection.commit()

    inspection = LegacyImportService().inspect(future)
    assert inspection.verdict == "too_new"
    with pytest.raises(LegacyImportError, match="高于程序支持"):
        LegacyImportService().execute_import(future, tmp_path / "拒绝目标")
