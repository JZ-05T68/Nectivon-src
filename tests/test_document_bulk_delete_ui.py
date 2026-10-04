"""Bulk-delete UI contract: select-all reflects into the multiselect (§16).

AppTest drives the real 删除文件 page against a stubbed runtime so the
「全选当前筛选结果」→ multiselect reflection is verified without a browser
(agent-browser cannot drive BaseWeb multiselect options — same test-infra
limitation class as the file_uploader findings).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture()
def three_documents(tmp_path: Path):
    from src.database import Database

    db = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "pages").mkdir(parents=True)
    (tmp_path / "data" / "markdown").mkdir(parents=True)
    ids = []
    for index in (1, 2, 3):
        raw = tmp_path / "data" / "raw" / f"卷{index}.pdf"
        raw.write_bytes(b"%PDF-1.7 fixture " + bytes([index]) * 16)
        image_dir = tmp_path / "data" / "pages" / str(index)
        image_dir.mkdir(parents=True, exist_ok=True)
        image = image_dir / "page-1.png"
        image.write_bytes(b"png")
        document = db.create_document(
            title=f"ZZZ_bulk_ui_test_{index}",
            filename=f"卷{index}.pdf",
            source_path=raw,
            sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
            page_count=1,
            import_status="completed",
        )
        db.create_page(
            document_id=document.id,
            page_number=1,
            image_path=image,
            extracted_text=f"第{index}题",
            status="ready",
        )
        ids.append(document.id)
    return db, tmp_path, ids


def _run_page(monkeypatch: pytest.MonkeyPatch, database, tmp_path: Path) -> AppTest:
    import src.runtime as runtime_module

    page_path = Path(__file__).resolve().parents[1] / "pages" / "11_文档管理.py"
    at = AppTest.from_file(str(page_path), default_timeout=30)
    monkeypatch.setattr(
        runtime_module, "application_database", lambda: database, raising=False
    )
    from src.document_deletion_service import DocumentDeletionService

    deletion = DocumentDeletionService(
        database=database,
        raw_dir=tmp_path / "data" / "raw",
        pages_dir=tmp_path / "data" / "pages",
        markdown_dir=tmp_path / "data" / "markdown",
        data_dir=tmp_path / "data",
    )
    monkeypatch.setattr(
        runtime_module,
        "application_document_deletion_service",
        lambda: deletion,
        raising=False,
    )
    at.run()
    return at


def test_select_all_reflects_into_multiselect(
    monkeypatch: pytest.MonkeyPatch, three_documents
) -> None:
    db, tmp_path, ids = three_documents
    at = _run_page(monkeypatch, db, tmp_path)
    assert not at.exception

    filter_input = at.text_input(key="bulk_filter")
    filter_input.set_value("ZZZ_bulk_ui_test").run()
    at.button(key="bulk_select_all").click().run()
    selected = at.multiselect(key="bulk_selected_ids").value
    assert sorted(selected) == sorted(ids), (
        "全选当前筛选结果 must fill the multiselect with every filtered document"
    )


def test_bulk_preview_then_execute_removes_documents(
    monkeypatch: pytest.MonkeyPatch, three_documents
) -> None:
    db, tmp_path, ids = three_documents
    at = _run_page(monkeypatch, db, tmp_path)
    assert not at.exception

    at.multiselect(key="bulk_selected_ids").set_value(ids).run()
    at.button(key="bulk_preview").click().run()
    body = " ".join(str(block.value) for block in at.markdown)
    assert "准备删除 3 份资料" in body
    confirm = at.checkbox(key="bulk_confirm")
    confirm.check().run()
    at.button(key="bulk_execute").click().run()
    for document_id in ids:
        assert db.get_document(document_id) is None
