"""DeepSeek adapter tests use synthetic credentials and fake HTTP only."""

from __future__ import annotations

import urllib.request
from collections.abc import Mapping
from typing import Any

import pytest

from src.ai.completion_stage import CompletionStage, completion_stage_scope
from src.ai.credential_store import MemoryCredentialStore, SecretCredential
from src.ai.deepseek_client import DEFAULT_BASE_URL, DeepSeekAdapter
from src.ai.model_registry import ProviderId
from src.ai.openai_compatible import (
    OpenAICompatibleTransportError,
    TransportFailureKind,
    urllib_transport,
)
from src.ai.provider import (
    AiCallRecord,
    AIUnavailableError,
    AuditedAIProvider,
    ProviderCallError,
    ProviderErrorCode,
)
from src.ai.provider_config import (
    AIProviderConfig,
    ModelSelectionKind,
    ProviderConfigStore,
    ProviderSettings,
)
from src.ai.provider_factory import (
    build_audited_deepseek_provider,
    resolve_deepseek_runtime,
)
from src.config import Settings

_SYNTHETIC_KEY = "synthetic-deepseek-credential-61fd"
_CUSTOM_MODEL = "deepseek-user-model"


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


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _Ledger:
    def __init__(self) -> None:
        self.records: list[AiCallRecord] = []

    def record(self, call: AiCallRecord) -> None:
        self.records.append(call)


class _AllowBudget:
    def ensure_allowed(self, capability: str) -> None:
        assert capability == "completion"


def _completion_body(**message_extra: object) -> dict[str, Any]:
    message = {"role": "assistant", "content": "final answer", **message_extra}
    return {
        "model": "deepseek-user-model-actual",
        "choices": [{"message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
    }


def _adapter(transport: _FakeTransport, **overrides: object) -> DeepSeekAdapter:
    options: dict[str, object] = {
        "api_key": SecretCredential(_SYNTHETIC_KEY),
        "model": _CUSTOM_MODEL,
        "timeout_seconds": 9.5,
        "transport": transport,
    }
    options.update(overrides)
    return DeepSeekAdapter(**options)  # type: ignore[arg-type]


def test_request_payload_uses_common_non_streaming_codec_and_deepseek_fields() -> None:
    transport = _FakeTransport([_completion_body()])

    result = _adapter(transport).complete("question", max_completion_tokens=123)

    url, headers, payload, timeout = transport.calls[0]
    assert url == f"{DEFAULT_BASE_URL}/chat/completions"
    assert headers["Authorization"] == f"Bearer {_SYNTHETIC_KEY}"
    assert payload == {
        "model": _CUSTOM_MODEL,
        "messages": [{"role": "user", "content": "question"}],
        "stream": False,
        "max_tokens": 123,
    }
    assert "max_completion_tokens" not in payload
    assert timeout == 9.5
    assert result.text == "final answer"


def test_agent_decision_disables_thinking_with_256_output_floor() -> None:
    transport = _FakeTransport([_completion_body()])

    with completion_stage_scope(CompletionStage.AGENT_DECISION):
        _adapter(transport).complete("question", max_completion_tokens=128)

    payload = transport.calls[0][2]
    assert payload["max_tokens"] == 256
    assert payload["thinking"] == {"type": "disabled"}


def test_decision_floor_never_shrinks_larger_caller_budget() -> None:
    transport = _FakeTransport([_completion_body()])

    with completion_stage_scope(CompletionStage.AGENT_DECISION):
        _adapter(transport).complete("question", max_completion_tokens=400)

    assert transport.calls[0][2]["max_tokens"] == 400


def test_decision_stage_context_reaches_the_adapter_payload_policy() -> None:
    transport = _FakeTransport([_completion_body(), _completion_body()])

    with completion_stage_scope(CompletionStage.AGENT_DECISION):
        _adapter(transport).complete("decision", max_completion_tokens=128)
    _adapter(transport).complete("no-stage", max_completion_tokens=128)

    # With the product stage the payload carries the non-thinking policy;
    # without any stage a custom model with unknown capability stays neutral.
    assert transport.calls[0][2]["thinking"] == {"type": "disabled"}
    assert transport.calls[0][2]["max_tokens"] == 256
    assert "thinking" not in transport.calls[1][2]
    assert transport.calls[1][2]["max_tokens"] == 128


def test_final_answer_disables_thinking_and_preserves_output_budget() -> None:
    transport = _FakeTransport([_completion_body(reasoning_content="")])

    with completion_stage_scope(CompletionStage.FINAL_ANSWER):
        result = _adapter(transport).complete(
            "question", max_completion_tokens=512
        )

    payload = transport.calls[0][2]
    assert payload["max_tokens"] == 512
    assert payload["thinking"] == {"type": "disabled"}
    assert result.text == "final answer"
    assert not hasattr(result, "reasoning_content")


def test_completion_parses_usage_finish_reason_and_resolved_model() -> None:
    result = _adapter(_FakeTransport([_completion_body()])).complete("question")

    assert result.model == "deepseek-user-model-actual"
    assert result.finish_reason == "stop"
    assert result.usage is not None
    assert result.usage.prompt_tokens == 4
    assert result.usage.completion_tokens == 6
    assert result.usage.total_tokens == 10


def test_reasoning_content_is_validated_but_not_exposed_to_business_result() -> None:
    result = _adapter(
        _FakeTransport([_completion_body(reasoning_content="private reasoning")])
    ).complete("question")

    assert result.text == "final answer"
    assert not hasattr(result, "reasoning_content")


def test_malformed_reasoning_content_is_normalized() -> None:
    with pytest.raises(ProviderCallError) as captured:
        _adapter(
            _FakeTransport([_completion_body(reasoning_content={"unexpected": True})])
        ).complete("question")

    assert captured.value.detail.code is ProviderErrorCode.MALFORMED_RESPONSE


def test_custom_model_omits_thinking_when_capability_is_unknown() -> None:
    transport = _FakeTransport([_completion_body()])

    _adapter(transport).complete("question", model="deepseek-custom-future")

    payload = transport.calls[0][2]
    assert payload["model"] == "deepseek-custom-future"
    assert payload["stream"] is False
    assert "thinking" not in payload


@pytest.mark.parametrize(
    ("status", "expected_code", "retryable"),
    [
        (400, ProviderErrorCode.INVALID_REQUEST, False),
        (401, ProviderErrorCode.AUTHENTICATION_FAILED, False),
        (402, ProviderErrorCode.QUOTA_EXHAUSTED, False),
        (403, ProviderErrorCode.MODEL_ACCESS_DENIED, False),
        (404, ProviderErrorCode.MODEL_NOT_FOUND, False),
        (422, ProviderErrorCode.INVALID_REQUEST, False),
        (429, ProviderErrorCode.RATE_LIMITED, True),
        (500, ProviderErrorCode.PROVIDER_UNAVAILABLE, True),
        (503, ProviderErrorCode.PROVIDER_UNAVAILABLE, True),
    ],
)
def test_http_status_error_mapping(status, expected_code, retryable) -> None:
    count = 3 if retryable else 1
    transport = _FakeTransport(
        [OpenAICompatibleTransportError(status_code=status)] * count
    )

    with pytest.raises(ProviderCallError) as captured:
        _adapter(transport).complete("question")

    assert captured.value.detail.code is expected_code
    assert captured.value.detail.http_status == status
    assert captured.value.detail.retryable is retryable
    assert len(transport.calls) == count


@pytest.mark.parametrize(
    ("kind", "expected_code"),
    [
        (TransportFailureKind.TIMEOUT, ProviderErrorCode.TIMEOUT),
        (TransportFailureKind.NETWORK, ProviderErrorCode.NETWORK_ERROR),
    ],
)
def test_statusless_transport_error_mapping(kind, expected_code) -> None:
    transport = _FakeTransport(
        [OpenAICompatibleTransportError(kind=kind)] * 3
    )

    with pytest.raises(ProviderCallError) as captured:
        _adapter(transport).complete("question")

    assert captured.value.detail.code is expected_code
    assert captured.value.retry_count == 2


def test_malformed_json_from_shared_transport_is_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout=0: _FakeResponse(b"<not-json>"),
    )

    with pytest.raises(ProviderCallError) as captured:
        DeepSeekAdapter(
            api_key=SecretCredential(_SYNTHETIC_KEY),
            model=_CUSTOM_MODEL,
            transport=urllib_transport,
        ).complete("question")

    assert captured.value.detail.code is ProviderErrorCode.MALFORMED_RESPONSE
    assert _SYNTHETIC_KEY not in str(captured.value)


def test_secret_never_enters_url_repr_log_or_exception(caplog) -> None:
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


def test_unsupported_capabilities_fail_with_normalized_code() -> None:
    adapter = _adapter(_FakeTransport([]))

    with pytest.raises(ProviderCallError) as captured:
        adapter.embed(["text"])

    assert captured.value.detail.code is ProviderErrorCode.UNSUPPORTED_CAPABILITY


def test_missing_credential_and_default_transport_never_call_network() -> None:
    with pytest.raises(AIUnavailableError, match="API Key"):
        DeepSeekAdapter(api_key="", model=_CUSTOM_MODEL).complete("question")
    with pytest.raises(AIUnavailableError, match="不发起真实"):
        DeepSeekAdapter(
            api_key=SecretCredential(_SYNTHETIC_KEY), model=_CUSTOM_MODEL
        ).complete("question")


def test_secure_factory_to_audited_completion_chain(tmp_path) -> None:
    config_store = ProviderConfigStore(tmp_path / "ai-providers-v1.json")
    config_store.save(
        AIProviderConfig().with_provider(
            ProviderSettings(
                provider_id=ProviderId.DEEPSEEK,
                base_url=DEFAULT_BASE_URL,
                model_id=_CUSTOM_MODEL,
                model_selection=ModelSelectionKind.CUSTOM,
            ),
            make_active=True,
        )
    )
    credentials = MemoryCredentialStore()
    credentials.set(ProviderId.DEEPSEEK, SecretCredential(_SYNTHETIC_KEY))
    resolved = resolve_deepseek_runtime(
        Settings(_env_file=None),
        config_store=config_store,
        credential_store=credentials,
    )
    assert resolved is not None
    ledger = _Ledger()
    audited = build_audited_deepseek_provider(
        resolved,
        transport=_FakeTransport([_completion_body()]),
        source_feature="deepseek_adapter_test",
        ledger=ledger,
        budget_guard=_AllowBudget(),
    )

    result = audited.complete("question")

    assert isinstance(audited, AuditedAIProvider)
    assert result.text == "final answer"
    assert len(ledger.records) == 1
    assert ledger.records[0].model == _CUSTOM_MODEL
    assert ledger.records[0].status == "success"
