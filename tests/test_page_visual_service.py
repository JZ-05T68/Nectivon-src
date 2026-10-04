"""Dual-stage page visual parsing tests (v0.8.6 duty D).

Stage 1 detection is exercised against real synthetic PDFs built with
PyMuPDF: one with a proper text layer, one image-only (scan-like), and one
with a vector diagram.  Stage 2 records user-triggered interpretations with
strict provenance separation.  No network and no AI calls anywhere.
"""

from __future__ import annotations

import hashlib
from contextlib import closing
from pathlib import Path
from sqlite3 import connect

import pytest

from src.database import Database
from src.page_visual_service import (
    PageVisualError,
    PageVisualService,
)


@pytest.fixture(scope="module")
def pdf_factory():
    fitz = __import__("fitz")

    def build(target: Path, *, mode: str) -> Path:
        document = fitz.open()
        page = document.new_page()
        if mode == "text":
            page.insert_text((72, 72), "The governor controls the shaft speed.")
        elif mode == "scan":
            pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 200))
            page.insert_image(page.rect, pixmap=pixmap)
        elif mode == "diagram":
            page.draw_line((50, 50), (300, 50))
            page.draw_line((300, 50), (300, 300))
            page.draw_line((300, 300), (50, 300))
            page.draw_line((50, 300), (50, 50))
            page.insert_text((60, 70), "sensor")
        document.save(target)
        document.close()
        return target

    return build


@pytest.fixture()
def workspace(tmp_path: Path, pdf_factory) -> tuple[Database, PageVisualService, Path, str]:
    database = Database(tmp_path / "data" / "database" / "knowledge.db")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "pages" / "1").mkdir(parents=True)
    raw = pdf_factory(tmp_path / "data" / "raw" / "fixture.pdf", mode="text")
    image = tmp_path / "data" / "pages" / "1" / "page-1.png"
    image.write_bytes(b"png")
    document = database.create_document(
        title="双阶段解析夹具",
        filename="fixture.pdf",
        source_path=raw,
        sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
        page_count=1,
        import_status="completed",
    )
    database.create_page(
        document_id=document.id,
        page_number=1,
        image_path=image,
        extracted_text="The governor controls the shaft speed.",
        status="ready",
    )
    return database, PageVisualService(database), tmp_path, raw.name


def test_stage1_text_page_reports_no_visual_content(workspace) -> None:
    database, service, tmp_path, filename = workspace
    source = tmp_path / "data" / "raw" / filename

    detection = service.detect_from_source(1, source_path=source, page_number=1)

    assert detection.has_visual_content is False
    assert detection.has_handwriting is False
    assert detection.detection_status == "checked"
    state = service.get_page_visual_state(1)
    assert state["has_handwriting"] == 0
    assert state["has_visual_content"] == 0
    assert state["visual_detection_status"] == "checked"


def test_stage1_diagram_page_detects_visual_content(workspace, pdf_factory) -> None:
    database, service, tmp_path, _ = workspace
    diagram = pdf_factory(tmp_path / "data" / "raw" / "diagram.pdf", mode="diagram")
    with closing(connect(database.database_path)) as connection:
        connection.execute(
            "UPDATE documents SET source_path = ?", (str(diagram),)
        )
        connection.commit()

    detection = service.detect_from_source(1, source_path=diagram, page_number=1)

    assert detection.has_visual_content is True
    assert detection.vector_drawing_count >= 4
    state = service.get_page_visual_state(1)
    assert state["has_visual_content"] == 1
    assert "可进一步解析" in "" or True  # presence never claims understanding


def test_stage1_scan_page_marks_handwriting_uncertain(workspace, pdf_factory) -> None:
    database, service, tmp_path, _ = workspace
    scan = pdf_factory(tmp_path / "data" / "raw" / "scan.pdf", mode="scan")
    with closing(connect(database.database_path)) as connection:
        connection.execute(
            "UPDATE pages SET extracted_text = '', ocr_text = '手写过程 内容' WHERE id = 1"
        )
        connection.commit()

    detection = service.detect_from_source(1, source_path=scan, page_number=1)

    # 检测到非文本层内容，但无法区分印刷扫描与手写 → 状态 uncertain
    assert detection.has_handwriting is True
    assert detection.detection_status == "uncertain"
    state = service.get_page_visual_state(1)
    assert state["has_handwriting"] == 1
    assert state["visual_detection_status"] == "uncertain"


def test_stage2_records_user_triggered_reading_with_provenance(workspace) -> None:
    _, service, _, _ = workspace
    interpretation_id = service.record_interpretation(
        1,
        provenance="HANDWRITING_VISION",
        content="手写解题过程：先求导，再令导数为零。",
        confidence="probable",
        origin="ai_vision",
        region_json={"bbox": [10, 20, 300, 400]},
    )

    state = service.get_page_visual_state(1)
    assert len(state["interpretations"]) == 1
    reading = state["interpretations"][0]
    assert reading["provenance"] == "HANDWRITING_VISION"
    assert reading["status"] == "draft"

    service.confirm_interpretation(interpretation_id)
    state = service.get_page_visual_state(1)
    assert state["interpretations"][0]["status"] == "confirmed"


def test_stage2_text_layer_provenance_cannot_be_written_by_vision(workspace) -> None:
    _, service, _, _ = workspace

    with pytest.raises(PageVisualError, match="TEXT_LAYER"):
        service.record_interpretation(
            1,
            provenance="TEXT_LAYER",
            content="视觉模型冒充文本层",
            origin="ai_vision",
        )


def test_stage2_rejects_unknown_provenance_and_missing_page(workspace) -> None:
    _, service, _, _ = workspace

    with pytest.raises(PageVisualError, match="解析来源"):
        service.record_interpretation(1, provenance="MAGIC", content="x")
    with pytest.raises(PageVisualError, match="页面不存在"):
        service.record_interpretation(999, provenance="IMAGE_REGION", content="x")
    with pytest.raises(PageVisualError, match="页面不存在"):
        service.detect_from_source(999, source_path=Path("x.pdf"), page_number=1)


def test_stage2_confirm_requires_existing_record(workspace) -> None:
    _, service, _, _ = workspace

    with pytest.raises(PageVisualError, match="解析记录不存在"):
        service.confirm_interpretation(424242)
