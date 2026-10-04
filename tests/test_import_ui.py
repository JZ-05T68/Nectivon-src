"""Focused tests for the one-click document-reading page.

The page contract (V086-101/104/105/102 fixes):

- the uploader really accepts multiple files and every file is processed;
- a duplicate upload produces explicit feedback instead of silence;
- the import status and the AI reading status are reported separately:
  a reading failure is presented as "资料已导入，AI 阅读未完成", never as
  an import failure;
- technical terms never leak into the user-visible surface.
"""

from __future__ import annotations

import runpy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import streamlit as st

import src.runtime as runtime
from src.database import Database
from src.models import PageStatus

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMPORT_PAGE = PROJECT_ROOT / "pages" / "1_导入资料.py"


class _Progress:
    def __init__(self) -> None:
        self.labels: list[str] = []

    def progress(self, *args: Any, **kwargs: Any) -> None:
        self.labels.append(str(kwargs.get("text", "")))

    def empty(self) -> None:
        return None


def _patch_streamlit_basics(
    monkeypatch: pytest.MonkeyPatch,
    *,
    uploads: list,
    click_import: bool,
    progress: _Progress,
    error_messages: list[str],
) -> dict[str, list[str]]:
    captured: dict[str, list[str]] = {"markdown": [], "subheader": [], "info": []}

    def button(label: str, **kwargs: Any) -> bool:
        del kwargs
        return click_import and label == "导入并让 Agent 逐份阅读"

    class Column:
        @staticmethod
        def button(label: str, **kwargs: Any) -> bool:
            del kwargs
            return False

    def _capture_to(target: str):
        def _append(*args: Any, **kwargs: Any) -> None:
            del kwargs
            captured[target].append(str(args[0]) if args else "")

        return _append

    monkeypatch.setattr(st, "set_page_config", lambda **kwargs: None)
    monkeypatch.setattr(st, "title", lambda *args, **kwargs: None)
    monkeypatch.setattr(st, "caption", _capture_to("markdown"))
    monkeypatch.setattr(st, "markdown", _capture_to("markdown"))
    monkeypatch.setattr(st, "subheader", _capture_to("subheader"))
    monkeypatch.setattr(st, "info", _capture_to("info"))
    monkeypatch.setattr(st, "file_uploader", lambda *args, **kwargs: uploads)
    monkeypatch.setattr(st, "button", button)
    monkeypatch.setattr(st, "columns", lambda spec: [Column() for _ in range(2)])
    monkeypatch.setattr(st, "progress", lambda *args, **kwargs: progress)
    monkeypatch.setattr(st, "error", lambda message, *a, **k: error_messages.append(str(message)))
    return captured


def test_multi_file_import_reads_each_and_reports_honestly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two new files: both imported, OCR'd, read; messages stay honest."""

    pages = tuple(
        SimpleNamespace(id=index, page_number=index, status=PageStatus.PENDING)
        for index in range(1, 4)
    )
    result = SimpleNamespace(
        document=SimpleNamespace(id=7, title="隔离资料"),
        pages=pages,
        duplicate=False,
        import_record=None,
    )

    class Upload:
        def __init__(self, name: str) -> None:
            self.name = name
            self.size = 1024

        def getvalue(self) -> bytes:
            return b"office-document"

    imported: list[dict[str, Any]] = []
    ocr_pages: list[int] = []
    read_documents: list[int] = []
    error_messages: list[str] = []
    progress = _Progress()

    class Service:
        @staticmethod
        def import_document(**kwargs: Any):
            imported.append(kwargs)
            return result

        @staticmethod
        def run_page_ocr(page_id: int) -> None:
            ocr_pages.append(page_id)

    class AgentClient:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs

        def read_document(self, document_id: int, **kwargs: Any) -> None:
            read_documents.append(document_id)
            callback = kwargs["progress_callback"]
            for position in range(1, 4):
                callback(position, 3)

    captured = _patch_streamlit_basics(
        monkeypatch,
        uploads=[Upload("隔离资料.docx"), Upload("另一份.docx")],
        click_import=True,
        progress=progress,
        error_messages=error_messages,
    )
    monkeypatch.setattr(runtime, "application_document_service", lambda: Service())
    monkeypatch.setattr(
        runtime,
        "application_settings",
        lambda: SimpleNamespace(
            agent_readings_dir=Path("readings"), ai_llm_model_hard="qwen3.8"
        ),
    )
    monkeypatch.setattr(
        runtime,
        "application_database",
        lambda: Database(tmp_path / "import-queue.db"),
    )
    monkeypatch.setattr(
        runtime,
        "application_ai_provider",
        lambda: SimpleNamespace(default_model="synthetic-test-model"),
    )
    monkeypatch.setattr(
        "src.agent.local_client.LocalDocumentAgentClient", AgentClient
    )

    runpy.run_path(str(IMPORT_PAGE), run_name="__main__")

    assert [item["filename"] for item in imported] == [
        "隔离资料.docx",
        "另一份.docx",
    ]
    assert ocr_pages == [1, 2, 3, 1, 2, 3]
    assert read_documents == [7, 7]
    assert progress.labels[-1] == "上传队列处理完成。"
    visible_text = "\n".join(progress.labels + error_messages)
    for hidden_term in ("embedding", "chunk", "RAG", "索引"):
        assert hidden_term not in visible_text
    # V086-311: the queue section must disclose the real execution model —
    # pause on leaving, resume on return, nothing lost.
    rendered = "\n".join(captured["markdown"])
    assert "离开本页会暂停" in rendered
    assert "重新选择同样的文件即可继续" in rendered
    assert "不会丢失" in rendered
    # V086-212: success text must never surface as an error_message line.
    assert "Agent 已逐页读完" not in rendered


def test_duplicate_upload_gets_explicit_feedback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """V086-105: an existing document must say so instead of staying silent."""

    existing = SimpleNamespace(
        document=SimpleNamespace(id=3, title="已有资料"),
        pages=(),
        duplicate=True,
        import_record=None,
    )

    class Upload:
        name = "已有资料.pdf"
        size = 1024

        def getvalue(self) -> bytes:
            return b"pdf-bytes"

    imported: list[dict[str, Any]] = []
    read_documents: list[int] = []
    markdown_messages: list[str] = []
    progress = _Progress()

    class Service:
        @staticmethod
        def import_document(**kwargs: Any):
            imported.append(kwargs)
            return existing

        @staticmethod
        def run_page_ocr(page_id: int) -> None:
            raise AssertionError("duplicate import must not re-run OCR")

    class AgentClient:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs

        def read_document(self, document_id: int, **kwargs: Any) -> None:
            read_documents.append(document_id)

    def button(label: str, **kwargs: Any) -> bool:
        del kwargs
        return label == "导入并让 Agent 逐份阅读"

    class Column:
        @staticmethod
        def button(label: str, **kwargs: Any) -> bool:
            del kwargs
            return False

    monkeypatch.setattr(st, "set_page_config", lambda **kwargs: None)
    monkeypatch.setattr(st, "title", lambda *args, **kwargs: None)
    monkeypatch.setattr(st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        st,
        "markdown",
        lambda *args, **kwargs: markdown_messages.append(str(args[0]) if args else ""),
    )
    monkeypatch.setattr(st, "subheader", lambda *args, **kwargs: None)
    monkeypatch.setattr(st, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(st, "file_uploader", lambda *args, **kwargs: [Upload()])
    monkeypatch.setattr(st, "button", button)
    monkeypatch.setattr(st, "columns", lambda spec: [Column() for _ in range(2)])
    monkeypatch.setattr(st, "progress", lambda *args, **kwargs: progress)
    monkeypatch.setattr(st, "error", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "application_document_service", lambda: Service())
    monkeypatch.setattr(
        runtime,
        "application_settings",
        lambda: SimpleNamespace(agent_readings_dir=Path("readings")),
    )
    monkeypatch.setattr(
        runtime,
        "application_database",
        lambda: Database(tmp_path / "import-queue.db"),
    )
    monkeypatch.setattr(
        runtime,
        "application_ai_provider",
        lambda: SimpleNamespace(default_model="synthetic-test-model"),
    )
    monkeypatch.setattr(
        "src.agent.local_client.LocalDocumentAgentClient", AgentClient
    )

    runpy.run_path(str(IMPORT_PAGE), run_name="__main__")

    assert len(imported) == 1  # import_document was invoked (dedup happens inside)
    assert read_documents == []  # duplicates never re-trigger paid reading
    joined = "\n".join(markdown_messages)
    assert "该资料已存在" in joined


def test_reading_failure_reports_import_and_reading_separately(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """V086-101/203: import completed + AI reading failed must be honest."""

    from src.agent_document_reader import AgentDocumentReadingError

    pages = tuple(
        SimpleNamespace(id=index, page_number=index, status=PageStatus.PENDING)
        for index in (1, 2)
    )
    result = SimpleNamespace(
        document=SimpleNamespace(id=9, title="截断资料"),
        pages=pages,
        duplicate=False,
        import_record=None,
    )

    class Upload:
        name = "截断资料.pdf"
        size = 1024

        def getvalue(self) -> bytes:
            return b"pdf-bytes"

    read_documents: list[int] = []
    error_messages: list[str] = []
    markdown_messages: list[str] = []
    progress = _Progress()

    class Service:
        @staticmethod
        def import_document(**kwargs: Any):
            del kwargs
            return result

        @staticmethod
        def run_page_ocr(page_id: int) -> None:
            del page_id

    class AgentClient:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs

        def read_document(self, document_id: int, **kwargs: Any) -> None:
            read_documents.append(document_id)
            raise AgentDocumentReadingError(
                "AI 阅读未全部完成：成功 1/2 页，失败页码：2。"
                "已成功页面的阅读结果不会丢失，可稍后重试失败页。"
            )

    def button(label: str, **kwargs: Any) -> bool:
        del kwargs
        return label == "导入并让 Agent 逐份阅读"

    class Column:
        @staticmethod
        def button(label: str, **kwargs: Any) -> bool:
            del kwargs
            return False

    monkeypatch.setattr(st, "set_page_config", lambda **kwargs: None)
    monkeypatch.setattr(st, "title", lambda *args, **kwargs: None)
    monkeypatch.setattr(st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        st,
        "markdown",
        lambda *args, **kwargs: markdown_messages.append(str(args[0]) if args else ""),
    )
    monkeypatch.setattr(st, "subheader", lambda *args, **kwargs: None)
    monkeypatch.setattr(st, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(st, "file_uploader", lambda *args, **kwargs: [Upload()])
    monkeypatch.setattr(st, "button", button)
    monkeypatch.setattr(st, "columns", lambda spec: [Column() for _ in range(2)])
    monkeypatch.setattr(st, "progress", lambda *args, **kwargs: progress)
    monkeypatch.setattr(
        st,
        "warning",
        lambda *args, **kwargs: markdown_messages.append(str(args[0]) if args else ""),
    )
    monkeypatch.setattr(st, "error", lambda message, *a, **k: error_messages.append(str(message)))
    monkeypatch.setattr(runtime, "application_document_service", lambda: Service())
    monkeypatch.setattr(
        runtime,
        "application_settings",
        lambda: SimpleNamespace(agent_readings_dir=Path("readings")),
    )
    monkeypatch.setattr(
        runtime,
        "application_database",
        lambda: Database(tmp_path / "import-queue.db"),
    )
    monkeypatch.setattr(
        runtime,
        "application_ai_provider",
        lambda: SimpleNamespace(default_model="synthetic-test-model"),
    )
    monkeypatch.setattr(
        "src.agent.local_client.LocalDocumentAgentClient", AgentClient
    )

    runpy.run_path(str(IMPORT_PAGE), run_name="__main__")

    assert read_documents == [9]
    joined = "\n".join(markdown_messages + error_messages)
    assert "资料已导入" in joined
    assert "AI 阅读未全部完成" in joined
    # The misleading wording from Wave 1 must be gone.
    assert "这份资料还没有读完" not in joined


def test_uploader_accepts_pdf_word_powerpoint_and_multiple_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploader_calls: list[dict[str, Any]] = []

    def file_uploader(*args: Any, **kwargs: Any) -> None:
        del args
        uploader_calls.append(kwargs)
        return None

    monkeypatch.setattr(st, "set_page_config", lambda **kwargs: None)
    monkeypatch.setattr(st, "title", lambda *args, **kwargs: None)
    monkeypatch.setattr(st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(st, "subheader", lambda *args, **kwargs: None)
    monkeypatch.setattr(st, "file_uploader", file_uploader)
    monkeypatch.setattr(
        st,
        "camera_input",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("导入页不得请求摄像头权限")
        ),
    )
    monkeypatch.setattr(st, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(st, "stop", lambda: (_ for _ in ()).throw(SystemExit))
    # Bare-mode Streamlit session state is a process-wide singleton that
    # earlier tests in this module may have populated; isolate it.
    monkeypatch.setattr(st, "session_state", {})

    with pytest.raises(SystemExit):
        runpy.run_path(str(IMPORT_PAGE), run_name="__main__")

    # General PDF/Office/image and dedicated image-only uploaders remain;
    # rendering either must not invoke camera_input.
    assert uploader_calls, "the page must render its uploaders before stopping"
    general = uploader_calls[0]
    assert general["type"] == [
        "pdf",
        "doc",
        "docx",
        "ppt",
        "pptx",
        "jpg",
        "jpeg",
        "png",
        "webp",
    ]
    assert general["accept_multiple_files"] is True
    image_only = [
        call for call in uploader_calls[1:] if call.get("type") == ["jpg", "jpeg", "png", "webp"]
    ]
    assert image_only, "the dedicated image-only uploader must exist"
    assert image_only[0]["accept_multiple_files"] is True
