"""Tests for the single-page OCR controls on the review page.

Pure feedback mapping is unit-tested directly; widget behavior is tested
through Streamlit AppTest against a real temporary SQLite database with
fake in-memory OCR engines only — no real OCR, network, or formal data.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

import src.runtime as runtime
from src.database import Database
from src.document_service import DocumentService, PageOcrOutcome
from src.ocr_engine import OcrExecutionError
from src.ocr_ui import page_ocr_feedback, page_ocr_unavailable_feedback


class _FakeOcrEngine:
    """Fake engine returning fixed text and recording calls."""

    def __init__(self, result: str = "识别出的泵体参数") -> None:
        self.result = result
        self.calls: list[Path] = []

    def recognize(self, image_path: Path) -> str:
        self.calls.append(image_path)
        return self.result


class _FailingOcrEngine:
    """Fake engine failing with a message containing a local path."""

    def __init__(self, message: str) -> None:
        self.message = message

    def recognize(self, image_path: Path) -> str:
        raise OcrExecutionError(self.message)


def _build_review_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    ocr_engine: object | None = None,
    ocr_text: str = "",
    extracted_text: str = "",
) -> tuple[AppTest, Database, int]:
    database = Database(tmp_path / "database" / "knowledge.db")
    document = database.create_document(
        title="单页 OCR 界面测试",
        filename="ocr-ui.pdf",
        source_path=tmp_path / "raw" / "ocr-ui.pdf",
        sha256="5" * 64,
    )
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir(parents=True)
    image_path = pages_dir / "page_0001.png"
    Image.new("RGB", (40, 20), "white").save(image_path)
    page = database.create_page(
        document_id=document.id,
        page_number=1,
        image_path=image_path,
    )
    if extracted_text or ocr_text:
        database.update_page(
            page.id,
            extracted_text=extracted_text or None,
            ocr_text=ocr_text or None,
        )
    service = DocumentService(
        database,
        tmp_path / "raw",
        pages_dir,
        tmp_path / "markdown",
        ocr_engine=ocr_engine,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(runtime, "application_database", lambda: database)
    monkeypatch.setattr(runtime, "application_document_service", lambda: service)
    app_path = next((Path(__file__).parents[1] / "pages").glob("5_*.py"))
    app = AppTest.from_file(str(app_path)).run(timeout=10)
    return app, database, page.id


# Pure feedback mapping -------------------------------------------------------


def test_completed_with_text_maps_to_success() -> None:
    assert page_ocr_feedback(PageOcrOutcome.COMPLETED, "识别文本") == (
        "success",
        "本页文字识别完成。",
    )


def test_completed_without_text_says_no_valid_text() -> None:
    level, message = page_ocr_feedback(PageOcrOutcome.COMPLETED, "  ")
    assert level == "info"
    assert "识别执行完成" in message
    assert "未识别到有效文字" in message


def test_not_eligible_maps_to_info_not_error() -> None:
    level, message = page_ocr_feedback(PageOcrOutcome.NOT_ELIGIBLE, "")
    assert level == "info"
    assert "当前页面不需要重新识别" in message


def test_failed_maps_to_error_without_paths() -> None:
    level, message = page_ocr_feedback(PageOcrOutcome.FAILED, "")
    assert level == "error"
    assert "执行失败" in message
    assert "未被修改" in message
    assert "/" not in message and "\\" not in message


def test_unavailable_maps_to_warning() -> None:
    level, message = page_ocr_unavailable_feedback()
    assert level == "warning"
    assert "不可用" in message
    assert "依赖已完整安装" in message
    assert "/" not in message and "\\" not in message


# Widget behavior --------------------------------------------------------------


@pytest.mark.parametrize("historical_ocr", ["", "历史 OCR 文字"])
def test_review_has_only_direct_image_recognition_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, historical_ocr: str,
) -> None:
    engine = _FakeOcrEngine()
    app, database, page_id = _build_review_app(
        tmp_path, monkeypatch, ocr_engine=engine, ocr_text=historical_ocr,
    )
    assert not app.exception
    labels = [button.label for button in app.button]
    assert "重新读图并切分本页" in labels
    assert "扫描整卷并统一切分题目" in labels
    assert "重新识别" not in labels
    assert "识别这一页的文字" not in labels
    assert engine.calls == []
    assert database.get_page(page_id).ocr_text == historical_ocr


def test_image_recognition_failure_keeps_review_and_originals_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, database, page_id = _build_review_app(tmp_path, monkeypatch)
    page = database.get_page(page_id)
    original = page.image_path.read_bytes()
    from types import SimpleNamespace

    def fail(_page_id):
        raise RuntimeError("模拟读图失败")

    monkeypatch.setattr(runtime, "application_page_image_reader", lambda: SimpleNamespace(
        provider=SimpleNamespace(provider_id="qwen", default_model="qwen3.8-max"),
        read_page=fail,
    ))
    next(b for b in app.button if b.key == f"review_read_image_{page_id}").click().run()
    assert not app.exception
    assert any("模拟读图失败" in item.value for item in app.error)
    assert database.get_page(page_id).image_path.read_bytes() == original
    assert database.get_page(page_id).ocr_text == ""
