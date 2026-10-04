"""Safe non-streaming OpenAI-compatible HTTP and chat primitives.

This module knows only the wire contract.  It stores no credential, knows no
vendor-specific request field, and has no dependency on Agent/RAG behavior.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlsplit

from src.ai.credential_store import SecretCredential
from src.ai.provider import (
    CompletionResult,
    CompletionUsage,
    ProviderCallError,
    ProviderErrorCode,
    SafeProviderError,
)

LOGGER = logging.getLogger(__name__)

__all__ = [
    "OpenAICompatibleClient",
    "OpenAICompatibleTransportError",
    "TransportFailureKind",
    "Transport",
    "build_chat_completion_payload",
    "parse_chat_completion",
    "urllib_transport",
]


class Transport(Protocol):
    """One injected JSON POST operation; tests replace it without networking."""

    def __call__(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]: ...


class OpenAICompatibleTransportError(RuntimeError):
    """Sanitized transport signal carrying only an optional HTTP status."""

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int | None = None,
        kind: TransportFailureKind | None = None,
    ) -> None:
        # ``message`` remains accepted for compatibility with injected legacy
        # test transports, but is deliberately discarded because it may contain
        # a request header, URL, vendor body, or credential.
        del message
        super().__init__("OpenAI-compatible transport failed")
        self.status_code = status_code
        self.kind = kind or (
            TransportFailureKind.HTTP
            if status_code is not None
            else TransportFailureKind.NETWORK
        )

    def __repr__(self) -> str:
        return (
            "OpenAICompatibleTransportError("
            f"status_code={self.status_code!r}, kind={self.kind.value!r})"
        )


class TransportFailureKind(StrEnum):
    HTTP = "http"
    TIMEOUT = "timeout"
    NETWORK = "network"


def urllib_transport(
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    timeout_seconds: float,
) -> Mapping[str, Any]:
    """Send one HTTPS JSON POST without logging headers, URL, or response body."""

    if urlsplit(url).scheme != "https":
        raise OpenAICompatibleTransportError()
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=dict(headers),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise OpenAICompatibleTransportError(
            status_code=exc.code, kind=TransportFailureKind.HTTP
        ) from exc
    except TimeoutError as exc:
        raise OpenAICompatibleTransportError(kind=TransportFailureKind.TIMEOUT) from exc
    except urllib.error.URLError as exc:
        kind = (
            TransportFailureKind.TIMEOUT
            if isinstance(exc.reason, TimeoutError)
            else TransportFailureKind.NETWORK
        )
        raise OpenAICompatibleTransportError(kind=kind) from exc
    except OSError as exc:
        raise OpenAICompatibleTransportError(kind=TransportFailureKind.NETWORK) from exc
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProviderCallError(
            SafeProviderError(code=ProviderErrorCode.MALFORMED_RESPONSE)
        ) from exc
    if not isinstance(decoded, Mapping):
        raise ProviderCallError(
            SafeProviderError(code=ProviderErrorCode.MALFORMED_RESPONSE)
        )
    return decoded


class OpenAICompatibleClient:
    """Credential-free client state for URL joining, timeout and bounded retry."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        max_extra_attempts: int,
        transport: Transport,
    ) -> None:
        normalized_url = base_url.strip().rstrip("/")
        parsed = urlsplit(normalized_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("OpenAI-compatible Base URL 必须是无凭据的 HTTPS 地址")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须为正数")
        if max_extra_attempts < 0:
            raise ValueError("max_extra_attempts 不能为负数")
        self._base_url = normalized_url
        self._timeout_seconds = timeout_seconds
        self._max_extra_attempts = max_extra_attempts
        self._transport = transport

    def post(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        credential: SecretCredential,
        provider_id: str,
        error_mapper: Callable[
            [OpenAICompatibleTransportError, str], SafeProviderError
        ]
        | None = None,
        timeout_seconds: float | None = None,
    ) -> tuple[Mapping[str, Any], int]:
        """POST once plus bounded transient retries, unwrapping the key here only.

        ``timeout_seconds`` overrides the constructor timeout for this call
        only (used by vision); it must be positive.
        """

        if not path.startswith("/") or urlsplit(path).scheme or "?" in path or "#" in path:
            raise ValueError("OpenAI-compatible 请求路径无效")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须为正数")
        url = f"{self._base_url}{path}"
        headers = {
            "Authorization": f"Bearer {credential.reveal()}",
            "Content-Type": "application/json",
        }
        effective_timeout = (
            self._timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        attempts = 1 + self._max_extra_attempts
        for attempt in range(attempts):
            try:
                return self._transport(
                    url, headers, payload, effective_timeout
                ), attempt
            except OpenAICompatibleTransportError as exc:
                mapper = error_mapper or _safe_error_detail
                detail = mapper(exc, provider_id)
                if not detail.retryable or attempt == attempts - 1:
                    # Redaction-safe transport diagnostics (V086-204): failure
                    # kind and HTTP status only - never URL, headers or body.
                    LOGGER.warning(
                        "AI 传输失败（重试耗尽或不可重试）：provider=%s kind=%s "
                        "http_status=%s attempt=%s/%s timeout_seconds=%s",
                        provider_id,
                        exc.kind.value,
                        exc.status_code,
                        attempt + 1,
                        attempts,
                        effective_timeout,
                    )
                    raise ProviderCallError(detail, retry_count=attempt) from exc
        raise ProviderCallError(  # pragma: no cover - loop always returns or raises
            SafeProviderError(
                code=ProviderErrorCode.TRANSPORT,
                provider_id=provider_id,
                retryable=True,
            ),
            retry_count=self._max_extra_attempts,
        )

    def __repr__(self) -> str:
        return (
            "OpenAICompatibleClient("
            f"base_url={self._base_url!r}, timeout_seconds={self._timeout_seconds!r}, "
            f"max_extra_attempts={self._max_extra_attempts!r})"
        )


def build_chat_completion_payload(
    *,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    max_completion_tokens: int | None = None,
) -> dict[str, Any]:
    """Build only the common non-streaming chat/completions fields."""

    if not model.strip():
        raise ValueError("Model ID 不能为空")
    if max_completion_tokens is not None and max_completion_tokens <= 0:
        raise ValueError("max_completion_tokens 必须为正数")
    payload: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "stream": False,
    }
    if max_completion_tokens is not None:
        payload["max_completion_tokens"] = max_completion_tokens
    return payload


def parse_chat_completion(
    response: Mapping[str, Any],
    requested_model: str,
    *,
    retry_count: int = 0,
) -> CompletionResult:
    """Parse the common non-streaming choices/message/content response shape."""

    try:
        choices = response["choices"]
        choice = choices[0]
        text = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderCallError(
            SafeProviderError(code=ProviderErrorCode.INVALID_RESPONSE)
        ) from exc
    if not isinstance(text, str):
        raise ProviderCallError(
            SafeProviderError(code=ProviderErrorCode.INVALID_RESPONSE)
        )
    raw_model = response.get("model")
    resolved_model = (
        raw_model.strip() if isinstance(raw_model, str) and raw_model.strip() else None
    )
    return CompletionResult(
        text=text,
        model=resolved_model or requested_model,
        usage=_parse_usage(response.get("usage")),
        finish_reason=_parse_finish_reason(choice),
        retry_count=retry_count,
        resolved_model=resolved_model,
    )


def _parse_finish_reason(choice: object) -> str | None:
    if isinstance(choice, Mapping):
        reason = choice.get("finish_reason")
        if isinstance(reason, str) and reason:
            return reason
    return None


def _parse_usage(raw_usage: object) -> CompletionUsage | None:
    if not isinstance(raw_usage, Mapping):
        return None
    try:
        return CompletionUsage(
            prompt_tokens=int(raw_usage["prompt_tokens"]),
            completion_tokens=int(raw_usage["completion_tokens"]),
            total_tokens=int(raw_usage["total_tokens"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderCallError(
            SafeProviderError(code=ProviderErrorCode.INVALID_RESPONSE)
        ) from exc


def _safe_error_detail(
    error: OpenAICompatibleTransportError, provider_id: str
) -> SafeProviderError:
    status = error.status_code
    if status == 401 or status == 403:
        code = ProviderErrorCode.AUTHENTICATION
        retryable = False
    elif status == 429:
        code = ProviderErrorCode.RATE_LIMITED
        retryable = True
    elif status is not None and status >= 500:
        code = ProviderErrorCode.PROVIDER_RESPONSE
        retryable = True
    elif status is not None:
        code = ProviderErrorCode.PROVIDER_RESPONSE
        retryable = False
    else:
        code = ProviderErrorCode.TRANSPORT
        retryable = True
    return SafeProviderError(
        code=code,
        provider_id=provider_id,
        retryable=retryable,
        http_status=status,
    )
