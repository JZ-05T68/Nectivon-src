"""GLM adapter tests use only synthetic credentials and fake transport.

The real model ids (``glm-5.3`` / ``glm-5.3-flash``) were verified against
the official Zhipu documentation on 2026-09-25; every request here is fake.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from src.ai.credential_store import MemoryCredentialStore, SecretCredential
from src.ai.glm_client import DEFAULT_BASE_URL, GlmAdapter
from src.ai.model_registry import ProviderId, get_model_preset
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
from src.ai.provider_config import (
    AIProviderConfig,
    ProviderConfigStore,
    ProviderSettings,
)
from src.ai.provider_factory import build_audited_glm_provider, resolve_glm_runtime
from src.config import Settings

_SYNTHETIC_KEY = "synthetic-glm-credential-b73d"


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
        "model": "glm-5.3-actual",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "glm answer",
                    **message_extra,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
    }


def _adapter(transport: _FakeTransport, **overrides: object) -> GlmAdapter:
    options: dict[str, object] = {
        "api_key": SecretCredential(_SYNTHETIC_KEY),
        "model": "glm-5.3",
        "timeout_seconds": 8.5,
        "transport": transport,
    }
    options.update(overrides)
    return GlmAdapter(**options)  # type: ignore[arg-type]


def test_request_payload_hits_official_endpoint_without_vendor_fields() -> None:
    transport = _FakeTransport([_body()])

    result = _adapter(transport).complete("question", max_completion_tokens=77)

    url, headers, payload, timeout = transport.calls[0]
    assert url == f"{DEFAULT_BASE_URL}/chat/completions"
    assert DEFAULT_BASE_URL == "https://open.bigmodel.cn/api/paas/v4"
    assert headers["Authorization"] == f"Bearer {_SYNTHETIC_KEY}"
    assert payload == {
        "model": "glm-5.3",
        "messages": [{"role": "user", "content": "question"}],
        "stream": False,
        "max_tokens": 77,
    }
    assert timeout == 8.5
    assert result.text == "glm answer"


def test_preset_ids_match_official_documentation() -> None:
    """UI display names map to verified API ids - never the reverse."""

    preset_53 = get_model_preset(ProviderId.GLM, "glm-5.3")
    preset_flash = get_model_preset(ProviderId.GLM, "glm-5.3-flash")
    assert preset_53 is not None and preset_53.display_name == "GLM-5.3"
    assert preset_53.recommended is True
    assert preset_flash is not None and preset_flash.display_name == "GLM-5.3flash"


def test_flash_preset_uses_exact_official_model_id() -> None:
    transport = _FakeTransport([_body()])

    _adapter(transport, model="glm-5.3-flash").complete("question")

    assert transport.calls[0][2]["model"] == "glm-5.3-flash"


def test_completion_parses_reasoning_usage_finish_reason_without_exposing_chain() -> None:
    result = _adapter(
        _FakeTransport([_body(reasoning_content="private glm reasoning")])
    ).complete("question")

    assert result.model == "glm-5.3-actual"
    assert result.finish_reason == "stop"
    assert result.usage is not None and result.usage.total_tokens == 10
    assert not hasattr(result, "reasoning_content")


def test_malformed_reasoning_and_common_response_are_normalized() -> None:
    with pytest.raises(ProviderCallError) as reasoning_error:
        _adapter(_FakeTransport([_body(reasoning_content={"bad": True})])).complete(
            "question"
        )
    assert reasoning_error.value.detail.code is ProviderErrorCode.MALFORMED_RESPONSE

    with pytest.raises(ProviderCallError) as response_error:
        _adapter(_FakeTransport([{"choices": []}])).complete("question")
    assert response_error.value.detail.code is ProviderErrorCode.MALFORMED_RESPONSE


def test_custom_model_sends_no_vendor_fields() -> None:
    transport = _FakeTransport([_body()])

    _adapter(transport).complete("question", model="future-custom-glm")

    payload = transport.calls[0][2]
    assert payload["model"] == "future-custom-glm"
    assert payload["stream"] is False
    assert "thinking" not in payload
    assert "reasoning_effort" not in payload


@pytest.mark.parametrize(
    ("status", "expected", "retryable"),
    [
        (400, ProviderErrorCode.INVALID_REQUEST, False),
        (401, ProviderErrorCode.AUTHENTICATION_FAILED, False),
        (402, ProviderErrorCode.QUOTA_EXHAUSTED, False),
        (403, ProviderErrorCode.MODEL_ACCESS_DENIED, False),
        (404, ProviderErrorCode.MODEL_NOT_FOUND, False),
        (429, ProviderErrorCode.RATE_LIMITED, True),
        (500, ProviderErrorCode.PROVIDER_UNAVAILABLE, True),
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
        adapter.embed(["text"])
    assert unsupported.value.detail.code is ProviderErrorCode.UNSUPPORTED_CAPABILITY
    with pytest.raises(AIUnavailableError, match="API Key"):
        GlmAdapter(api_key="", model="glm-5.3").complete("question")
    with pytest.raises(AIUnavailableError, match="不发起真实"):
        GlmAdapter(
            api_key=SecretCredential(_SYNTHETIC_KEY), model="glm-5.3"
        ).complete("question")


def test_secure_factory_keeps_audit_and_budget_outside_adapter(tmp_path) -> None:
    config_store = ProviderConfigStore(tmp_path / "ai-providers-v1.json")
    config_store.save(
        AIProviderConfig().with_provider(
            ProviderSettings.default_for(ProviderId.GLM), make_active=True
        )
    )
    credentials = MemoryCredentialStore()
    credentials.set(ProviderId.GLM, SecretCredential(_SYNTHETIC_KEY))
    resolved = resolve_glm_runtime(
        Settings(_env_file=None),
        config_store=config_store,
        credential_store=credentials,
    )
    assert resolved is not None
    assert resolved.base_url == "https://open.bigmodel.cn/api/paas/v4"
    assert resolved.model == "glm-5.3"
    ledger = _Ledger()
    audited = build_audited_glm_provider(
        resolved,
        transport=_FakeTransport([_body()]),
        source_feature="glm_test",
        ledger=ledger,
        budget_guard=_AllowBudget(),
    )

    assert isinstance(audited, AuditedAIProvider)
    assert audited.complete("question").text == "glm answer"
    assert len(ledger.records) == 1
    assert ledger.records[0].model == "glm-5.3"
