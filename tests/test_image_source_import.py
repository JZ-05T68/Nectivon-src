"""V086-210 regression: image sources are first-class documents.

An image upload must enter the same two-stage chain as a PDF page —
original stored verbatim, page object evidence-locatable, Stage 1 honest,
Stage 2 user-triggered, OCR/reading/learning layers reachable — without
any PDF conversion, re-encoding, or second-class shortcuts.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from src.database import Database
from src.document_service import DocumentService


class FakeOcrEngine:
    def __init__(self, result: str) -> None:
        self.result = result

    def recognize(self, image_path: Path) -> str:
        return self.result


@pytest.fixture()
def service(tmp_path: Path):
    (tmp_path / "raw").mkdir(parents=True)
    (tmp_path / "pages").mkdir(parents=True)
    (tmp_path / "markdown").mkdir(parents=True)
    from src.pdf_service import PdfService

    return DocumentService(
        database=Database(tmp_path / "image-source.db"),
        raw_dir=tmp_path / "raw",
        pages_dir=tmp_path / "pages",
        markdown_dir=tmp_path / "markdown",
        pdf_service=PdfService(minimum_text_length=20),
        ocr_engine=FakeOcrEngine("图片上的手写内容"),  # type: ignore[arg-type]
    )


def _jpg_bytes(color: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (80, 60), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_image_imports_as_one_page_source_without_conversion(
    service: DocumentService, tmp_path: Path
) -> None:
    content = _jpg_bytes()
    result = service.import_document(content, "第09章考研题14-18解析.jpg")
    assert not result.duplicate
    document = result.document
    pages = result.pages
    assert document.page_count == 1
    assert len(pages) == 1
    # The original survives byte-identical in raw/originals.
    original = Path(document.source_path)
    assert original.is_file()
    assert hashlib.sha256(original.read_bytes()).hexdigest() == hashlib.sha256(
        content
    ).hexdigest()
    # The page references a byte-identical copy of the ORIGINAL image
    # (evidence locates to the page; no PDF in the chain at all).
    page = pages[0]
    assert page.image_path.is_file()
    assert page.image_path.read_bytes() == content
    assert page.image_path.suffix == ".jpg"


def test_image_stage1_records_honest_presence_fact(
    service: DocumentService,
) -> None:
    from src.page_visual_service import PageVisualService

    result = service.import_document(_jpg_bytes(), "手写答案.png")
    state = PageVisualService(service.database).get_page_visual_state(
        result.pages[0].id
    )
    assert state["visual_detection_method"] == "image_source"
    assert state["has_visual_content"] == 1
    assert state["visual_detection_status"] == "checked"
    # Presence, never understanding: handwriting stays undetermined.
    assert state["has_handwriting"] is None


def test_image_page_is_ocr_eligible_and_reading_ready(
    service: DocumentService,
) -> None:
    result = service.import_document(_jpg_bytes((10, 90, 200)), "解析.jpg")
    page = result.pages[0]
    ocr_result = service.run_page_ocr(page.id)
    assert ocr_result.outcome is not None
    updated = service.database.get_page(page.id)
    assert updated is not None
    assert "图片上的手写内容" in (updated.ocr_text or "")


def test_duplicate_image_is_a_noop(service: DocumentService) -> None:
    content = _jpg_bytes((255, 200, 0))
    first = service.import_document(content, "答案解析A.jpg")
    second = service.import_document(content, "答案解析A.jpg")
    assert second.duplicate
    assert second.document.id == first.document.id
    assert len(second.pages) == len(first.pages)


def test_corrupt_image_is_refused_with_honest_message(
    service: DocumentService,
) -> None:
    from src.document_service import DocumentImportError

    with pytest.raises(DocumentImportError):
        service.import_document(b"not-an-image", "broken.jpg")
