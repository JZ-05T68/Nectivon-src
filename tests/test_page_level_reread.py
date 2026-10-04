"""V086-207/206 regression: page-level reread with fresh results.

「让 Agent 重读这一页」 must read exactly one page (not the whole
document), always perform a fresh AI call for that page, and refresh the
document-level progress honestly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.agent_document_reader import (
    AgentDocumentReader,
    AgentReadingStore,
    document_ai_reading_label,
    page_reread_availability,
)
from src.ai.provider import CompletionResult
from src.database import Database


class FakeProvider:
    def __init__(self, payloads: list[str]) -> None:
        self._payloads = list(payloads)
        self.calls: list[dict[str, Any]] = []

    def complete(self, prompt: str, *, model=None, max_completion_tokens=None):
        self.calls.append({"prompt": prompt})
        payload = self._payloads.pop(0)
        return CompletionResult(text=payload, model="fake", finish_reason="stop")


def _database(tmp_path: Path, page_count: int = 2) -> Database:
    database = Database(tmp_path / "reread.db")
    document = database.create_document(
        title="第2章复习题",
        filename="review.pdf",
        source_path=tmp_path / "review.pdf",
        sha256="c" * 64,
        import_status="completed",
        page_count=page_count,
    )
    for number in range(1, page_count + 1):
        database.create_page(
            document_id=document.id,
            page_number=number,
            image_path=tmp_path / f"page_{number}.png",
            extracted_text=f"第 {number} 页的内容，包含公式 x^{number}。",
            status="ready",
        )
    return database


def _payload(summary: str) -> str:
    return json.dumps(
        {"summary": summary, "keywords": ["关键词"], "key_facts": ["事实"]},
        ensure_ascii=False,
    )


def test_read_page_reads_exactly_one_page(tmp_path: Path) -> None:
    database = _database(tmp_path, page_count=3)
    provider = FakeProvider([_payload("第 2 页的新摘要")])
    reader = AgentDocumentReader(
        database=database,
        provider=provider,
        store=AgentReadingStore(tmp_path / "readings"),
        model="fake-model",
    )
    pages = database.list_pages(1)
    target = next(page for page in pages if page.page_number == 2)
    reading = reader.read_page(target.id)
    assert reading.page_id == target.id
    assert reading.page_number == 2
    assert len(provider.calls) == 1  # one page, one AI call — not the doc


def test_read_page_always_performs_fresh_call(tmp_path: Path) -> None:
    """A re-read is explicit: the freshness reuse check is bypassed."""

    database = _database(tmp_path)
    provider = FakeProvider([_payload("第一次"), _payload("第二次")])
    reader = AgentDocumentReader(
        database=database,
        provider=provider,
        store=AgentReadingStore(tmp_path / "readings"),
        model="fake-model",
    )
    page = database.list_pages(1)[0]
    reader.read_page(page.id)
    reading = reader.read_page(page.id)
    assert reading.summary == "第二次"
    assert len(provider.calls) == 2


def test_document_label_reports_progress_honestly(tmp_path: Path) -> None:
    """V086-306: unread documents can never be labelled as read."""

    database = _database(tmp_path, page_count=2)
    store = AgentReadingStore(tmp_path / "readings")
    document = database.get_document(1)
    assert document is not None
    assert document_ai_reading_label(store, document) == "AI 未阅读"

    provider = FakeProvider([_payload("第 1 页摘要")])
    reader = AgentDocumentReader(
        database=database,
        provider=provider,
        store=store,
        model="fake-model",
    )
    target = next(page for page in database.list_pages(1) if page.page_number == 1)
    reader.read_page(target.id)
    label = document_ai_reading_label(store, document)
    assert label.startswith("AI 已读 1/2")
    assert "未完成" in label


def test_read_page_refreshes_document_state_to_completed(tmp_path: Path) -> None:
    database = _database(tmp_path, page_count=1)
    provider = FakeProvider([_payload("唯一一页")])
    store = AgentReadingStore(tmp_path / "readings")
    reader = AgentDocumentReader(
        database=database,
        provider=provider,
        store=store,
        model="fake-model",
    )
    page = database.list_pages(1)[0]
    reader.read_page(page.id)
    state = store.document_state(1)
    assert state is not None
    assert state.status == "completed"
    assert state.read_pages == 1 == state.total_pages


def test_read_page_rejects_page_without_text(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.update_page(1, extracted_text="", ocr_text="")
    reader = AgentDocumentReader(
        database=database,
        provider=FakeProvider([]),
        store=AgentReadingStore(tmp_path / "readings"),
        model="fake-model",
    )
    from src.agent_document_reader import AgentDocumentReadingError

    with pytest.raises(AgentDocumentReadingError):
        reader.read_page(1)


# ------------------------------------------- V086-211 availability contract
def test_reread_available_without_manual_correction(tmp_path: Path) -> None:
    """V086-211: no manual correction can never disable the reread button."""
    database = _database(tmp_path)
    page = database.list_pages(1)[0]
    assert not page.markdown_content.strip()
    available, reason = page_reread_availability(page)
    assert available is True
    assert reason == ""


def test_reread_available_with_manual_correction(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.update_page(1, markdown_content="16√3+12√7")
    page = database.get_page(1)
    assert page is not None
    available, reason = page_reread_availability(page)
    assert available is True
    assert reason == ""


def test_reread_unavailable_only_for_textless_page_with_reason(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    database.update_page(1, extracted_text="", ocr_text="")
    page = database.get_page(1)
    assert page is not None
    available, reason = page_reread_availability(page)
    assert available is False
    assert reason.strip()
    assert "可读文字" in reason


def test_reread_without_manual_correction_reads_and_never_touches_markdown(
    tmp_path: Path,
) -> None:
    """A re-read of a correction-free page succeeds and stays page-scoped."""
    database = _database(tmp_path)
    provider = FakeProvider([_payload("无修正页摘要")])
    reader = AgentDocumentReader(
        database=database,
        provider=provider,
        store=AgentReadingStore(tmp_path / "readings"),
        model="fake-model",
    )
    page = database.list_pages(1)[0]
    reading = reader.read_page(page.id)
    assert reading.summary == "无修正页摘要"
    assert len(provider.calls) == 1
    stored = database.get_page(page.id)
    assert stored is not None
    assert stored.markdown_content == page.markdown_content


def test_reread_never_overwrites_user_correction(tmp_path: Path) -> None:
    """USER CONFIRMED CONTENT > AI RERUN: the reading store is the only sink."""
    database = _database(tmp_path)
    correction = "16√3+12√7"
    database.update_page(1, markdown_content=correction)
    provider = FakeProvider([_payload("摘要内容")])
    reader = AgentDocumentReader(
        database=database,
        provider=provider,
        store=AgentReadingStore(tmp_path / "readings"),
        model="fake-model",
    )
    page = database.get_page(1)
    assert page is not None
    reader.read_page(page.id)
    stored = database.get_page(page.id)
    assert stored is not None
    assert stored.markdown_content == correction
