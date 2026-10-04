"""Schema v16 and four-provider AI ledger attribution tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

import src.migrations as migrations_module
from src.ai.deepseek_client import DeepSeekAdapter
from src.ai.hunyuan_client import HunyuanAdapter
from src.ai.kimi_client import KimiAdapter
from src.ai.provider import AiCallRecord, AuditedAIProvider
from src.ai.qwen_client import QwenProvider
from src.database import Database
from src.migrations import SCHEMA_VERSION, MigrationError, _read_schema_version, migrate_database

_SYNTHETIC_KEY = "synthetic-ledger-attribution-key-7d45"


class _FakeTransport:
    def __init__(self, response: Mapping[str, Any]) -> None:
        self._response = response
        self.calls: list[tuple[str, Mapping[str, str], Mapping[str, Any], float]] = []

    def __call__(self, url, headers, payload, timeout_seconds):
        self.calls.append((url, headers, payload, timeout_seconds))
        return self._response


class _Ledger:
    def __init__(self) -> None:
        self.records: list[AiCallRecord] = []

    def record(self, call: AiCallRecord) -> None:
        self.records.append(call)


def _response(model: str | None) -> dict[str, Any]:
    response: dict[str, Any] = {
        "choices": [
            {
                "message": {"role": "assistant", "content": "safe answer"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 3,
            "completion_tokens": 2,
            "total_tokens": 5,
        },
    }
    if model is not None:
        response["model"] = model
    return response


def _adapter(provider: str, model: str, transport: _FakeTransport) -> object:
    if provider == "deepseek":
        return DeepSeekAdapter(
            api_key=_SYNTHETIC_KEY,
            model=model,
            max_extra_attempts=0,
            transport=transport,
        )
    if provider == "kimi":
        return KimiAdapter(
            api_key=_SYNTHETIC_KEY,
            model=model,
            max_extra_attempts=0,
            transport=transport,
        )
    if provider == "hunyuan":
        return HunyuanAdapter(
            api_key=_SYNTHETIC_KEY,
            model=model,
            max_extra_attempts=0,
            transport=transport,
        )
    return QwenProvider(
        api_key=_SYNTHETIC_KEY,
        llm_model=model,
        llm_model_hard=model,
        embedding_model="text-embedding-v4",
        rerank_model="gte-rerank-v2",
        max_extra_attempts=0,
        transport=transport,
    )


@pytest.mark.parametrize(
    ("provider", "requested_model", "response_model"),
    [
        ("deepseek", "deepseek-flash", "deepseek-flash-202609"),
        ("qwen", "qwen3.7-plus", "qwen3.7-plus-202609"),
        ("kimi", "kimi-k3", "kimi-k3-202609"),
        ("hunyuan", "hy3", "hy3-202609"),
    ],
)
def test_real_adapter_metadata_reaches_audited_ledger(
    provider: str, requested_model: str, response_model: str
) -> None:
    transport = _FakeTransport(_response(response_model))
    ledger = _Ledger()
    audited = AuditedAIProvider(
        _adapter(provider, requested_model, transport),
        default_model=requested_model,
        default_embedding_model=requested_model,
        source_feature="attribution_test",
        ledger=ledger,
    )

    result = audited.complete("synthetic prompt")

    assert result.model == response_model
    assert result.resolved_model == response_model
    assert len(ledger.records) == 1
    record = ledger.records[0]
    assert record.provider == provider
    assert record.requested_model == requested_model
    assert record.resolved_model == response_model
    assert _SYNTHETIC_KEY not in repr(record)
    assert _SYNTHETIC_KEY not in repr(result)


def test_missing_response_model_is_never_inferred() -> None:
    requested_model = "deepseek-custom-model"
    transport = _FakeTransport(_response(None))
    ledger = _Ledger()
    audited = AuditedAIProvider(
        _adapter("deepseek", requested_model, transport),
        default_model=requested_model,
        default_embedding_model=requested_model,
        source_feature="attribution_test",
        ledger=ledger,
    )

    result = audited.complete("synthetic prompt")

    assert result.model == requested_model
    assert result.resolved_model is None
    assert ledger.records[0].requested_model == requested_model
    assert ledger.records[0].resolved_model is None


def test_v15_history_migrates_to_qwen_without_resolved_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "knowledge.db"
    monkeypatch.setattr(
        migrations_module, "_V16_INJECTION_POINT", "v16_provider_column"
    )
    with pytest.raises(MigrationError, match="v16 迁移失败注入点"):
        migrate_database(database_path)
    assert _read_schema_version(database_path) == 15

    historical = (
        "legacy-call",
        "completion",
        "qwen3.7-plus",
        "a" * 64,
        12,
        "success",
        0,
        41,
        8,
        4,
        12,
        "stop",
        "rag_answer",
        "[]",
        "2026-09-20T00:00:00+00:00",
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO ai_calls(
                call_uuid, capability, model, prompt_sha256, input_chars,
                status, retry_count, latency_ms, prompt_tokens,
                completion_tokens, total_tokens, finish_reason,
                source_feature, target_refs, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            historical,
        )
        connection.commit()

    monkeypatch.setattr(migrations_module, "_V16_INJECTION_POINT", None)
    backup = migrate_database(database_path)

    assert backup is not None
    assert _read_schema_version(database_path) == SCHEMA_VERSION
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT call_uuid, model, provider, resolved_model, total_tokens, "
            "finish_reason FROM ai_calls WHERE call_uuid = 'legacy-call'"
        ).fetchone()
    assert row == ("legacy-call", "qwen3.7-plus", "qwen", None, 12, "stop")

    record = Database(database_path).list_ai_calls()[0]
    assert record.requested_model == "qwen3.7-plus"
    assert record.provider == "qwen"
    assert record.resolved_model is None


def test_schema_rejects_unknown_provider(tmp_path: Path) -> None:
    database = Database(tmp_path / "knowledge.db")
    with sqlite3.connect(database.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO ai_calls(
                    call_uuid, capability, model, prompt_sha256, input_chars,
                    status, source_feature, created_at, provider
                ) VALUES ('bad-provider', 'completion', 'm', ?, 1,
                          'success', 'test', 'now', 'other')
                """,
                ("b" * 64,),
            )
