"""Stage-1 visual presence detection must run inside the product flow (V086-107).

Uses a real temporary SQLite database, a real synthetic PDF (PyMuPDF) and a
fake OCR engine: no AI, no network.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from src.database import Database
from src.document_service import DocumentService
from src.pdf_service import PdfService

LONG_TEXT = "工程正文内容，用于构造较厚的文本层。" * 10


class FakeOcrEngine:
    def __init__(self, result: str) -> None:
        self.result = result

    def recognize(self, image_path: Path) -> str:
        return self.result


def _synthetic_pdf(path: Path, *, with_text: bool) -> None:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    if with_text:
        page.insert_text((72, 100), LONG_TEXT, fontname="helv", fontsize=10)
    document.save(path)
    document.close()


def _make_document_with_page(tmp_path: Path, *, with_text: bool):
    database = Database(tmp_path / "stage1.db")
    pdf_path = tmp_path / "raw" / "manual.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    _synthetic_pdf(pdf_path, with_text=with_text)
    document = database.create_document(
        title="检测夹具",
        filename="manual.pdf",
        source_path=pdf_path,
        sha256=uuid4().hex * 2,
        import_status="completed",
        page_count=1,
    )
    image_path = tmp_path / "pages" / "1.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"fake-page-png")  # presence is enough for eligibility
    page = database.create_page(
        document_id=document.id,
        page_number=1,
        image_path=image_path,
        extracted_text=LONG_TEXT if with_text else "",
        status="ready",
    )
    return database, document, page


def _service(database: Database, tmp_path: Path) -> DocumentService:
    return DocumentService(
        database=database,
        raw_dir=tmp_path / "raw",
        pages_dir=tmp_path / "pages",
        markdown_dir=tmp_path / "markdown",
        pdf_service=PdfService(minimum_text_length=20),
        ocr_engine=FakeOcrEngine("手写识别出的文字内容" * 5),  # type: ignore[arg-type]
    )


def test_run_page_ocr_records_stage1_detection_for_thin_text_page(
    tmp_path: Path,
) -> None:
    """Scanned page: OCR text + thin text layer => uncertain handwriting flag."""

    database, document, page = _make_document_with_page(tmp_path, with_text=False)
    service = _service(database, tmp_path)
    result = service.run_page_ocr(page.id)
    assert result.outcome is not None
    state = database.get_page(page.id)
    assert state is not None
    assert state.ocr_text  # OCR ran
    # The detection columns are no longer left at not_checked (V086-107).
    with database._connection() as connection:
        row = connection.execute(
            "SELECT has_handwriting, visual_detection_status FROM pages WHERE id = ?",
            (page.id,),
        ).fetchone()
    assert row["visual_detection_status"] != "not_checked"


def test_text_page_not_ocr_eligible_keeps_import_time_detection_contract(
    tmp_path: Path,
) -> None:
    """Thick text pages skip OCR entirely; they are detected at import time."""

    database, document, page = _make_document_with_page(tmp_path, with_text=True)
    service = _service(database, tmp_path)
    result = service.run_page_ocr(page.id)
    assert result.outcome.value == "not_eligible"
    # run_page_ocr must not touch a text-layer page's detection state; the
    # import-time hook (test below) is the owner of that first detection.
    with database._connection() as connection:
        row = connection.execute(
            "SELECT visual_detection_status FROM pages WHERE id = ?",
            (page.id,),
        ).fetchone()
    assert row["visual_detection_status"] == "not_checked"


def test_detection_failure_never_breaks_ocr(tmp_path: Path, monkeypatch) -> None:
    """Stage-1 is auxiliary: a detection crash must not fail the OCR result."""

    database, document, page = _make_document_with_page(tmp_path, with_text=False)
    service = _service(database, tmp_path)

    def broken_detect(*args, **kwargs):
        raise RuntimeError("synthetic detection failure")

    monkeypatch.setattr(
        "src.page_visual_service.PageVisualService.detect_from_source",
        broken_detect,
    )
    result = service.run_page_ocr(page.id)
    assert result.page is not None
    assert result.page.ocr_text


def test_import_flow_records_stage1_detection(tmp_path: Path) -> None:
    """_process_document runs detection for freshly imported pages."""

    pytest.importorskip("fitz")
    fitz = __import__("fitz")
    pdf_path = tmp_path / "import.pdf"
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 100), LONG_TEXT, fontname="helv", fontsize=10)
    document.save(pdf_path)
    document.close()

    database = Database(tmp_path / "import.db")
    service = DocumentService(
        database=database,
        raw_dir=tmp_path / "raw",
        pages_dir=tmp_path / "pages",
        markdown_dir=tmp_path / "markdown",
        pdf_service=PdfService(minimum_text_length=20),
        ocr_engine=None,  # type: ignore[arg-type]
    )
    result = service.import_document(
        file_content=pdf_path.read_bytes(), filename="import.pdf"
    )
    with database._connection() as connection:
        statuses = connection.execute(
            "SELECT visual_detection_status FROM pages WHERE document_id = ?",
            (result.document.id,),
        ).fetchall()
    assert statuses
    assert all(row["visual_detection_status"] != "not_checked" for row in statuses)
