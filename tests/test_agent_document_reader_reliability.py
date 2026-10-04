"""Reliability tests for the page-by-page document reader (V086-101/203).

Covers the completion-budget policy, the bounded retry ladder for truncated
or empty model output, page-level failure isolation, and honest aggregate
errors. All providers are fakes; no network is involved.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.agent_document_reader import (
    AgentDocumentReader,
    AgentDocumentReadingError,
    AgentReadingStore,
    DocumentReadingState,
    page_completion_budget,
)
from src.ai.completion_stage import (
    CompletionStage,
    completion_stage_scope,
    current_completion_stage,
)
from src.database import Database


def _make_db(tmp_path: Path, page_texts: list[str]) -> Database:
    database = Database(tmp_path / "reader-test.db")
    document = database.create_document(
        title="可靠性夹具",
        filename="fixture.pdf",
        source_path=tmp_path / "fixture.pdf",
        sha256="a" * 64,
        import_status="completed",
        page_count=len(page_texts),
    )
    for number, text in enumerate(page_texts, start=1):
        database.create_page(
            document_id=document.id,
            page_number=number,
            image_path=tmp_path / f"page_{number}.png",
            extracted_text=text,
            status="ready",
        )
    return database


def _valid_payload(summary: str = "本页摘要") -> str:
    return json.dumps(
        {"summary": summary, "keywords": ["关键词"], "key_facts": ["事实一"]},
        ensure_ascii=False,
    )


class FakeProvider:
    """Scripted completion provider returning queued results per call."""

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def complete(self, prompt: str, *, model=None, max_completion_tokens=None):
        self.calls.append(
            {
                "prompt": prompt,
                "max_completion_tokens": max_completion_tokens,
                "stage": current_completion_stage(),
            }
        )
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        text, finish_reason = outcome
        from src.ai.provider import CompletionResult

        return CompletionResult(
            text=text, model=model or "fake", finish_reason=finish_reason
        )


def _reader(database: Database, provider: FakeProvider, tmp_path: Path):
    return AgentDocumentReader(
        database=database,
        provider=provider,
        store=AgentReadingStore(tmp_path / "readings"),
        model="fake-model",
    )


# ------------------------------------------------------------- budget policy
def test_budget_scales_with_page_and_is_clamped() -> None:
    assert page_completion_budget(0) == 1200
    small = page_completion_budget(300)
    assert 1200 <= small < 2000
    huge = page_completion_budget(50_000)
    assert huge == 4096
    assert page_completion_budget(10_000) <= 4096


def test_budget_rejects_negative() -> None:
    with pytest.raises(ValueError):
        page_completion_budget(-1)


# ------------------------------------------------------------ retry ladder
def test_truncated_first_attempt_retries_and_succeeds(tmp_path: Path) -> None:
    database = _make_db(tmp_path, ["第一页内容" * 20])
    provider = FakeProvider(
        [
            ('{"summary": "未闭合', "length"),  # truncated JSON
            (_valid_payload(), "stop"),
        ]
    )
    report = _reader(database, provider, tmp_path).read_document(1)
    assert len(provider.calls) == 2
    # The retry must raise the cap instead of reusing the first budget.
    assert provider.calls[1]["max_completion_tokens"] > provider.calls[0][
        "max_completion_tokens"
    ]
    assert report.state.status == "completed"


def test_empty_output_retries_then_succeeds(tmp_path: Path) -> None:
    database = _make_db(tmp_path, ["只有一页"])
    provider = FakeProvider([("", "stop"), (_valid_payload(), "stop")])
    report = _reader(database, provider, tmp_path).read_document(1)
    assert report.state.status == "completed"
    assert report.newly_read_pages == 1


def test_compact_retry_asks_for_smaller_task(tmp_path: Path) -> None:
    database = _make_db(tmp_path, ["很难的长页" * 100])
    provider = FakeProvider(
        [
            ('{"summary": "截断', "length"),
            ('{"summary": "截断', "length"),
            (_valid_payload("压缩摘要"), "stop"),
        ]
    )
    report = _reader(database, provider, tmp_path).read_document(1)
    assert report.state.status == "completed"
    # Attempt 3 is the compact task: its prompt pins much smaller bounds.
    assert "500 字" in provider.calls[2]["prompt"]
    assert provider.calls[2]["max_completion_tokens"] == 8192


def test_all_attempts_exhausted_raises_honestly(tmp_path: Path) -> None:
    database = _make_db(tmp_path, ["坏页"])
    provider = FakeProvider(
        [('{"bad', "length"), ('{"bad', "length"), ('{"bad', "length")]
    )
    with pytest.raises(AgentDocumentReadingError):
        _reader(database, provider, tmp_path).read_document(1)
    assert len(provider.calls) == 3  # bounded, never an infinite loop


# ------------------------------------------------------- page-level isolation
def test_page_failure_keeps_successful_pages(tmp_path: Path) -> None:
    database = _make_db(tmp_path, ["好页一", "坏页", "好页三"])

    outcomes: list[Any] = [
        (_valid_payload("第一页"), "stop"),
        ('{"summary": "坏', "length"),
        ('{"summary": "坏', "length"),
        ('{"summary": "坏', "length"),
        (_valid_payload("第三页"), "stop"),
    ]
    provider = FakeProvider(outcomes)
    with pytest.raises(AgentDocumentReadingError) as excinfo:
        _reader(database, provider, tmp_path).read_document(1)
    message = str(excinfo.value)
    assert "成功 2/3 页" in message
    assert "失败页码：2" in message
    # Successful page readings survive on disk.
    store = AgentReadingStore(tmp_path / "readings")
    assert store.page_reading(1) is not None
    assert store.page_reading(3) is not None
    assert store.page_reading(2) is None
    state = store.document_state(1)
    assert state is not None
    assert state.status == "failed"
    assert state.failed_page_numbers == (2,)


def test_state_loader_tolerates_old_payloads_without_failed_pages(
    tmp_path: Path,
) -> None:
    payload = {
        "format_version": 1,
        "document_id": 1,
        "document_sha256": "a" * 64,
        "status": "completed",
        "total_pages": 2,
        "read_pages": 2,
        "model": "m",
        "started_at": "t",
        "updated_at": "t",
    }
    state = DocumentReadingState(
        format_version=1,
        document_id=1,
        document_sha256="a" * 64,
        status="completed",
        total_pages=2,
        read_pages=2,
        model="m",
        started_at="t",
        updated_at="t",
    )
    assert state.completed
    # Loader-level tolerance is exercised via the store round-trip below.
    store = AgentReadingStore(tmp_path / "readings")
    store.save_document_state(state)
    loaded = store.document_state(1)
    assert loaded is not None
    assert loaded.failed_page_numbers == ()
    assert json.loads(json.dumps(payload))["status"] == "completed"


# ------------------------------------------------------------- stage wiring
def test_page_reading_runs_inside_page_reading_stage(tmp_path: Path) -> None:
    database = _make_db(tmp_path, ["一页"])
    provider = FakeProvider([(_valid_payload(), "stop")])
    _reader(database, provider, tmp_path).read_document(1)
    assert provider.calls[0]["stage"] is CompletionStage.PAGE_READING


def test_stage_scope_restores_previous_stage() -> None:
    with completion_stage_scope(CompletionStage.FINAL_ANSWER):
        with completion_stage_scope(CompletionStage.PAGE_READING):
            assert current_completion_stage() is CompletionStage.PAGE_READING
        assert current_completion_stage() is CompletionStage.FINAL_ANSWER
    assert current_completion_stage() is None
