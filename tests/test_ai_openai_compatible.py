"""Unit tests for the credential-free OpenAI-compatible common layer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from src.ai.credential_store import SecretCredential
from src.ai.openai_compatible import (
    OpenAICompatibleClient,
    OpenAICompatibleTransportError,
    build_chat_completion_payload,
    parse_chat_completion,
)
from src.ai.provider import ProviderCallError, ProviderErrorCode

_SYNTHETIC_KEY = "synthetic-openai-compatible-key-4d1f"


class _RecordingTransport:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, Mapping[str, str], Mapping[str, Any], float]] = []

    def __call__(self, url, headers, payload, timeout_seconds):
        self.calls.append((url, headers, payload, timeout_seconds))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _client(transport: _RecordingTransport, retries: int = 2):
    return OpenAICompatibleClient(
        base_url="https://example.invalid/v1/",
        timeout_seconds=8.5,
        max_extra_attempts=retries,
        transport=transport,
    )


def test_post_builds_bearer_header_but_never_puts_key_in_url_or_repr() -> None:
    transport = _RecordingTransport([{"ok": True}])
    client = _client(transport)

    response, retry_count = client.post(
        "/chat/completions",
        {"model": "synthetic-model"},
        credential=SecretCredential(_SYNTHETIC_KEY),
        provider_id="qwen",
    )

    url, headers, _, timeout = transport.calls[0]
    assert response == {"ok": True}
    assert retry_count == 0
    assert _SYNTHETIC_KEY not in url
    assert headers["Authorization"] == f"Bearer {_SYNTHETIC_KEY}"
    assert timeout == 8.5
    assert _SYNTHETIC_KEY not in repr(client)


def test_transport_error_discards_untrusted_message_and_maps_safe_code(caplog) -> None:
    transport = _RecordingTransport(
        [OpenAICompatibleTransportError(_SYNTHETIC_KEY, status_code=401)]
    )

    with pytest.raises(ProviderCallError) as captured:
        _client(transport).post(
            "/chat/completions",
            {},
            credential=SecretCredential(_SYNTHETIC_KEY),
            provider_id="qwen",
        )

    assert captured.value.detail.code is ProviderErrorCode.AUTHENTICATION
    assert _SYNTHETIC_KEY not in str(captured.value)
    assert _SYNTHETIC_KEY not in repr(captured.value.__cause__)
    assert _SYNTHETIC_KEY not in caplog.text


def test_transient_retry_is_bounded_and_reports_consumed_attempts() -> None:
    transport = _RecordingTransport(
        [
            OpenAICompatibleTransportError(status_code=503),
            {"choices": []},
        ]
    )

    _, retry_count = _client(transport).post(
        "/chat/completions",
        {},
        credential=SecretCredential(_SYNTHETIC_KEY),
        provider_id="qwen",
    )

    assert retry_count == 1
    assert len(transport.calls) == 2


def test_common_chat_codec_forces_non_streaming_and_parses_usage() -> None:
    payload = build_chat_completion_payload(
        model="synthetic-model",
        messages=[{"role": "user", "content": "hello"}],
        max_completion_tokens=12,
    )
    result = parse_chat_completion(
        {
            "model": "resolved-model",
            "choices": [
                {"message": {"content": "answer"}, "finish_reason": "stop"}
            ],
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 3,
                "total_tokens": 5,
            },
        },
        "synthetic-model",
    )

    assert payload["stream"] is False
    assert payload["max_completion_tokens"] == 12
    assert "enable_thinking" not in payload
    assert result.text == "answer"
    assert result.model == "resolved-model"
    assert result.usage is not None and result.usage.total_tokens == 5


def test_client_rejects_non_https_or_credential_bearing_url() -> None:
    transport = _RecordingTransport([])

    for url in ("http://example.invalid/v1", "https://user:pass@example.invalid/v1"):
        with pytest.raises(ValueError, match="HTTPS"):
            OpenAICompatibleClient(
                base_url=url,
                timeout_seconds=1,
                max_extra_attempts=0,
                transport=transport,
            )
