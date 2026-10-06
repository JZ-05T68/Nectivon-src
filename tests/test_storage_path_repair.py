"""Moved-library recovery must restore normal deletion without widening its roots."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from src.database import Database
from src.document_deletion_service import DocumentDeletionService
from src.storage_path_repair import repair_relocated_asset_paths


def _library(tmp_path: Path):
    data = tmp_path / "current" / "data"
    old = tmp_path / "previous" / "data"
    roots = {name: data / name for name in ("raw", "pages", "markdown")}
    for root in roots.values():
        root.mkdir(parents=True)
    database = Database(data / "database" / "knowledge.db")
    content = b"locally imported PDF"
    pdf = roots["raw"] / "source.pdf"
    pdf.write_bytes(content)
    document = database.create_document(
        title="搬迁测试", filename=pdf.name, source_path=old / "raw" / pdf.name,
        sha256=hashlib.sha256(content).hexdigest(), page_count=1,
    )
    for name, suffix in (("pages", "png"), ("markdown", "md")):
        folder = roots[name] / str(document.id)
        folder.mkdir()
        (folder / f"page_0001.{suffix}").write_bytes(b"preserved page content")
    page = database.create_page(
        document_id=document.id, page_number=1,
        image_path=old / "pages" / str(document.id) / "page_0001.png",
        markdown_path=old / "markdown" / str(document.id) / "page_0001.md",
        markdown_content="用户原笔记", extracted_text="离线检索文本",
    )
    kwargs = dict(data_dir=data, raw_dir=roots["raw"], pages_dir=roots["pages"],
                  markdown_dir=roots["markdown"])
    return database, document, page, old, roots, kwargs


def test_repair_restores_reading_and_normal_deletion(tmp_path: Path) -> None:
    database, document, page, old, roots, kwargs = _library(tmp_path)
    deletion = DocumentDeletionService(database=database, **kwargs)
    assert deletion.preview_document_deletion(document.id).path_anomalies
    original_bytes = {p: p.read_bytes() for root in roots.values() for p in root.rglob("*")
                      if p.is_file()}
    assert repair_relocated_asset_paths(database, **kwargs) == 1
    updated_page = database.get_page(page.id)
    assert updated_page.image_path.is_file()
    assert updated_page.markdown_content == "用户原笔记"
    assert updated_page.extracted_text == "离线检索文本"
    assert {p: p.read_bytes() for p in original_bytes} == original_bytes
    assert not old.exists()
    snapshot, = database.database_path.parent.glob("*.paths-*.bak")
    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("SELECT source_path FROM documents").fetchone()[0] == str(
            document.source_path
        )
    assert repair_relocated_asset_paths(database, **kwargs) == 0
    assert len(list(database.database_path.parent.glob("*.paths-*.bak"))) == 1
    preview = deletion.preview_document_deletion(document.id)
    assert not preview.path_anomalies
    assert not preview.missing_files
    assert len(preview.files) == 3
    unrelated = roots["pages"] / str(document.id) / "unregistered.png"
    unrelated.write_bytes(b"unregistered user material")
    assert deletion.delete_document(document.id, expected_title=document.title).deleted
    assert database.get_document(document.id) is None
    assert unrelated.read_bytes() == b"unregistered user material"
    assert all(not p.exists() for p in original_bytes)


@pytest.mark.parametrize("problem", ["hash", "missing_page", "existing_old_root", "outside_page",
                                     "dotdot", "symlink"])
def test_ambiguous_paths_remain_blocked(tmp_path: Path, problem: str) -> None:
    database, document, page, old, roots, kwargs = _library(tmp_path)
    image = roots["pages"] / str(document.id) / "page_0001.png"
    if problem == "hash":
        (roots["raw"] / "source.pdf").write_bytes(b"different source")
    elif problem == "missing_page":
        image.unlink()
    elif problem == "existing_old_root":
        old.mkdir(parents=True)
    elif problem in ("outside_page", "dotdot"):
        path = old / "pages" / "other" / "page_0001.png"
        if problem == "dotdot":
            path = old / "pages" / str(document.id) / ".." / "page_0001.png"
        with database._connection() as connection:
            connection.execute("UPDATE pages SET image_path = ? WHERE id = ?", (str(path), page.id))
    elif problem == "symlink":
        external = tmp_path / "external.png"
        external.write_bytes(b"external user file")
        image.unlink()
        try:
            image.symlink_to(external)
        except OSError:
            pytest.skip("OS does not permit creating symlinks")
    assert repair_relocated_asset_paths(database, **kwargs) == 0
    assert database.get_document(document.id).source_path == document.source_path
    assert not list(database.database_path.parent.glob("*.paths-*.bak"))
    assert DocumentDeletionService(database=database, **kwargs).preview_document_deletion(
        document.id
    ).path_anomalies
