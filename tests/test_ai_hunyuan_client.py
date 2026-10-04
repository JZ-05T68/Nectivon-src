"""Hunyuan adapter tests use only synthetic credentials and fake transport."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from src.ai.completion_stage import CompletionStage, completion_stage_scope
from src.ai.credential_store import MemoryCredentialStore, SecretCredential
from src.ai.hunyuan_client import DEFAULT_BASE_URL, HunyuanAdapter
from src.ai.model_registry import ProviderId
from src.ai.openai_compatible import (
    OpenAICompatibleTransportError,
    TransportFailureKind,
)
from src.ai.provider import (
    AiCallRecord,
    AIUnavailableError,
    AuditedAIProvider,
    ProviderCallError,
    ProviderErrorCode,
)
from src.ai.provider_config import AIProviderConfig, ProviderConfigStore, ProviderSettings
from src.ai.provider_factory import (
    build_audited_hunyuan_provider,
    resolve_hunyuan_runtime,
)
from src.config import Settings

_SYNTHETIC_KEY = "synthetic-hunyuan-credential-c91e"


class _FakeTransport:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, Mapping[str, str], Mapping[str, Any], float]] = []

    def __call__(self, url, headers, payload, timeout_seconds):
        self.calls.append((url, headers, payload, timeout_seconds))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _Ledger:
    def __init__(self) -> None:
        self.records: list[AiCallRecord] = []

    def record(self, call: AiCallRecord) -> None:
        self.records.append(call)


class _AllowBudget:
    def ensure_allowed(self, capability: str) -> None:
        assert capability == "completion"


def _body(**message_extra: object) -> dict[str, Any]:
    return {
        "model": "hy4-preview-resolved",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "hunyuan answer",
                    **message_extra,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
    }


def _adapter(transport: _FakeTransport, **overrides: object) -> HunyuanAdapter:
    options: dict[str, object] = {
        "api_key": SecretCredential(_SYNTHETIC_KEY),
        "model": "hy4-preview",
        "timeout_seconds": 9.0,
        "transport": transport,
    }
    options.update(overrides)
    return HunyuanAdapter(**options)  # type: ignore[arg-type]


@pytest.mark.parametrize("model", ["hy4-preview", "hy3"])
def test_tokenhub_payload_uses_only_hunyuan_model_and_disables_thinking(model) -> None:
    transport = _FakeTransport([_body()])

    result = _adapter(transport, model=model).complete(
        "question", max_completion_tokens=99
    )

    url, headers, payload, timeout = transport.calls[0]
    assert url == f"{DEFAULT_BASE_URL}/chat/completions"
    assert headers["Authorization"] == f"Bearer {_SYNTHETIC_KEY}"
    assert payload == {
        "model": model,
        "messages": [{"role": "user", "content": "question"}],
        "stream": False,
        "max_tokens": 99,
        "thinking": {"type": "disabled"},
    }
    assert timeout == 9.0
    assert result.text == "hunyuan answer"


def test_final_answer_stage_does_not_change_hunyuan_payload() -> None:
    transport = _FakeTransport([_body()])

    with completion_stage_scope(CompletionStage.FINAL_ANSWER):
        _adapter(transport).complete("question", max_completion_tokens=512)

    assert transport.calls[0][2]["max_tokens"] == 512
    assert transport.calls[0][2]["thinking"] == {"type": "disabled"}


def test_decision_stage_does_not_change_hunyuan_payload() -> None:
    transport = _FakeTransport([_body(), _body()])

    _adapter(transport).complete("question", max_completion_tokens=128)
    with completion_stage_scope(CompletionStage.AGENT_DECISION):
        _adapter(transport).complete("question", max_completion_tokens=128)

    assert transport.calls[1][2] == transport.calls[0][2]


def test_custom_hunyuan_id_is_allowed_but_unknown_capability_omits_thinking() -> None:
    transport = _FakeTransport([_body()])

    _adapter(transport, model="hunyuan-future").complete("question")

    assert transport.calls[0][2]["model"] == "hunyuan-future"
    assert "thinking" not in transport.calls[0][2]


@pytest.mark.parametrize("model", ["deepseek/deepseek-flash", "kimi-k3", "glm-5"])
def test_non_hunyuan_model_is_rejected_before_transport(model) -> None:
    transport = _FakeTransport([])
    with pytest.raises(ValueError, match="只允许腾讯混元"):
        _adapter(transport, model=model)
    assert transport.calls == []


def test_completion_parses_reasoning_usage_finish_reason_without_exposing_chain() -> None:
    result = _adapter(
        _FakeTransport([_body(reasoning_content="private reasoning")])
    ).complete("question")

    assert result.model == "hy4-preview-resolved"
    assert result.finish_reason == "stop"
    assert result.usage is not None and result.usage.total_tokens == 10
    assert not hasattr(result, "reasoning_content")


def test_malformed_reasoning_and_common_response_are_normalized() -> None:
    with pytest.raises(ProviderCallError) as reasoning_error:
        _adapter(_FakeTransport([_body(reasoning_content=["bad"])] )).complete(
            "question"
        )
    assert reasoning_error.value.detail.code is ProviderErrorCode.MALFORMED_RESPONSE

    with pytest.raises(ProviderCallError) as response_error:
        _adapter(_FakeTransport([{"choices": []}])).complete("question")
    assert response_error.value.detail.code is ProviderErrorCode.MALFORMED_RESPONSE


@pytest.mark.parametrize(
    ("status", "expected", "retryable"),
    [
        (400, ProviderErrorCode.INVALID_REQUEST, False),
        (401, ProviderErrorCode.AUTHENTICATION_FAILED, False),
        (402, ProviderErrorCode.QUOTA_EXHAUSTED, False),
        (403, ProviderErrorCode.MODEL_ACCESS_DENIED, False),
        (404, ProviderErrorCode.MODEL_NOT_FOUND, False),
        (429, ProviderErrorCode.RATE_LIMITED, True),
        (503, ProviderErrorCode.PROVIDER_UNAVAILABLE, True),
    ],
)
def test_http_status_mapping(status, expected, retryable) -> None:
    count = 3 if retryable else 1
    transport = _FakeTransport(
        [OpenAICompatibleTransportError(status_code=status)] * count
    )
    with pytest.raises(ProviderCallError) as captured:
        _adapter(transport).complete("question")
    assert captured.value.detail.code is expected
    assert captured.value.detail.retryable is retryable
    assert len(transport.calls) == count


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (TransportFailureKind.TIMEOUT, ProviderErrorCode.TIMEOUT),
        (TransportFailureKind.NETWORK, ProviderErrorCode.NETWORK_ERROR),
    ],
)
def test_transport_failure_is_normalized_and_bounded(kind, expected) -> None:
    transport = _FakeTransport(
        [OpenAICompatibleTransportError(kind=kind)] * 3
    )
    with pytest.raises(ProviderCallError) as captured:
        _adapter(transport).complete("question")
    assert captured.value.detail.code is expected
    assert captured.value.retry_count == 2


def test_secret_safety_unsupported_capability_and_default_transport(caplog) -> None:
    transport = _FakeTransport(
        [OpenAICompatibleTransportError(_SYNTHETIC_KEY, status_code=401)]
    )
    adapter = _adapter(transport)
    with pytest.raises(ProviderCallError) as captured:
        adapter.complete("question")
    assert _SYNTHETIC_KEY not in transport.calls[0][0]
    assert _SYNTHETIC_KEY not in repr(adapter)
    assert _SYNTHETIC_KEY not in str(captured.value)
    assert _SYNTHETIC_KEY not in repr(captured.value.__cause__)
    assert _SYNTHETIC_KEY not in caplog.text

    with pytest.raises(ProviderCallError) as unsupported:
        adapter.complete_vision("question", "synthetic-image")
    assert unsupported.value.detail.code is ProviderErrorCode.UNSUPPORTED_CAPABILITY
    with pytest.raises(AIUnavailableError, match="API Key"):
        HunyuanAdapter(api_key="", model="hy3").complete("question")
    with pytest.raises(AIUnavailableError, match="不发起真实"):
        HunyuanAdapter(
            api_key=SecretCredential(_SYNTHETIC_KEY), model="hy3"
        ).complete("question")


def test_secure_factory_keeps_audit_and_budget_outside_adapter(tmp_path) -> None:
    config_store = ProviderConfigStore(tmp_path / "ai-providers-v1.json")
    config_store.save(
        AIProviderConfig().with_provider(
            ProviderSettings.default_for(ProviderId.HUNYUAN), make_active=True
        )
    )
    credentials = MemoryCredentialStore()
    credentials.set(ProviderId.HUNYUAN, SecretCredential(_SYNTHETIC_KEY))
    resolved = resolve_hunyuan_runtime(
        Settings(_env_file=None),
        config_store=config_store,
        credential_store=credentials,
    )
    assert resolved is not None
    ledger = _Ledger()
    audited = build_audited_hunyuan_provider(
        resolved,
        transport=_FakeTransport([_body()]),
        source_feature="hunyuan_test",
        ledger=ledger,
        budget_guard=_AllowBudget(),
    )

    assert isinstance(audited, AuditedAIProvider)
    assert audited.complete("question").text == "hunyuan answer"
    assert len(ledger.records) == 1
    assert ledger.records[0].model == "hy4-preview"
