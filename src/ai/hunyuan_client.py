"""Tencent Hunyuan completion adapter for the TokenHub endpoint.

Official documentation verified 2026-09-21:

* Endpoint/model IDs: https://cloud.tencent.com/document/product/1823/130078
* Hunyuan capabilities: https://cloud.tencent.com/document/product/1823/130051
* Thinking controls: https://cloud.tencent.com/document/product/1823/131208

TokenHub hosts third-party models too, but this adapter positively validates
the Hunyuan namespace and therefore cannot expose those models indirectly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from src.ai.credential_store import SecretCredential
from src.ai.model_registry import (
    CapabilitySupport,
    ProviderId,
    get_model_preset,
    is_hunyuan_model_id,
)
from src.ai.openai_compatible import (
    OpenAICompatibleClient,
    OpenAICompatibleTransportError,
    Transport,
    TransportFailureKind,
    build_chat_completion_payload,
    parse_chat_completion,
)
from src.ai.provider import (
    AIUnavailableError,
    CompletionResult,
    EmbeddingResult,
    ProviderCallError,
    ProviderErrorCode,
    RerankResult,
    SafeProviderError,
)

__all__ = ["DEFAULT_BASE_URL", "HunyuanAdapter"]

DEFAULT_BASE_URL: Final[str] = "https://tokenhub.tencentmaas.com/v1"
MAX_EXTRA_ATTEMPTS: Final[int] = 2


def _unconfigured_transport(
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    timeout_seconds: float,
) -> Mapping[str, Any]:
    del url, headers, payload, timeout_seconds
    raise AIUnavailableError("Hunyuan 传输层未配置，不发起真实 API 请求。")


class HunyuanAdapter:
    """Hunyuan-only, non-streaming completion adapter over TokenHub."""

    provider_id = ProviderId.HUNYUAN.value
    supports_embedding = False

    def __init__(
        self,
        *,
        api_key: str | SecretCredential,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 30.0,
        max_extra_attempts: int = MAX_EXTRA_ATTEMPTS,
        transport: Transport = _unconfigured_transport,
    ) -> None:
        normalized_model = self._validated_model(model)
        if isinstance(api_key, SecretCredential):
            self._credential: SecretCredential | None = api_key
        else:
            self._credential = SecretCredential(api_key) if api_key.strip() else None
        self._model = normalized_model
        self._client = OpenAICompatibleClient(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_extra_attempts=max_extra_attempts,
            transport=transport,
        )

    @property
    def is_configured(self) -> bool:
        return self._credential is not None

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_completion_tokens: int | None = None,
    ) -> CompletionResult:
        """Return only the final Hunyuan answer from TokenHub."""

        credential = self._require_credential()
        chosen_model = self._validated_model(model or self._model)
        payload = build_chat_completion_payload(
            model=chosen_model,
            messages=[{"role": "user", "content": prompt}],
        )
        if max_completion_tokens is not None:
            if max_completion_tokens <= 0:
                raise ValueError("max_completion_tokens 必须为正数")
            payload["max_tokens"] = max_completion_tokens
        payload.update(self._thinking_parameters(chosen_model))
        try:
            response, retry_count = self._client.post(
                "/chat/completions",
                payload,
                credential=credential,
                provider_id=ProviderId.HUNYUAN.value,
                error_mapper=_hunyuan_error_detail,
            )
            self._validate_reasoning_content(response)
            return parse_chat_completion(
                response, chosen_model, retry_count=retry_count
            )
        except ProviderCallError as exc:
            if exc.detail.code in {
                ProviderErrorCode.INVALID_RESPONSE,
                ProviderErrorCode.MALFORMED_RESPONSE,
            }:
                raise ProviderCallError(
                    SafeProviderError(
                        code=ProviderErrorCode.MALFORMED_RESPONSE,
                        provider_id=ProviderId.HUNYUAN.value,
                    ),
                    retry_count=exc.retry_count,
                ) from exc
            raise

    def complete_vision(
        self,
        prompt: str,
        image_png_base64: str,
        *,
        model: str | None = None,
        max_completion_tokens: int | None = None,
    ) -> CompletionResult:
        del prompt, image_png_base64, model, max_completion_tokens
        raise self._unsupported_capability()

    def embed(
        self,
        texts: Sequence[str],
        *,
        model: str | None = None,
        dimensions: int | None = None,
    ) -> EmbeddingResult:
        del texts, model, dimensions
        raise self._unsupported_capability()

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        model: str | None = None,
        top_n: int | None = None,
    ) -> RerankResult:
        del query, documents, model, top_n
        raise self._unsupported_capability()

    def _require_credential(self) -> SecretCredential:
        if self._credential is None:
            raise AIUnavailableError("Hunyuan 能力不可用：未配置 API Key。")
        return self._credential

    @staticmethod
    def _validated_model(model_id: str) -> str:
        normalized = model_id.strip()
        if not normalized:
            raise ValueError("Model ID 不能为空")
        if not is_hunyuan_model_id(normalized):
            raise ValueError("Hunyuan Provider 只允许腾讯混元 Model ID")
        return normalized

    @staticmethod
    def _thinking_parameters(model_id: str) -> dict[str, Any]:
        preset = get_model_preset(ProviderId.HUNYUAN, model_id)
        if (
            preset is None
            or preset.capabilities.reasoning is not CapabilitySupport.SUPPORTED
        ):
            return {}
        # Both curated models officially accept ``thinking``.  Disable it for
        # the current final-answer-only path; later UI may expose effort safely.
        return {"thinking": {"type": "disabled"}}

    @staticmethod
    def _validate_reasoning_content(response: Mapping[str, Any]) -> None:
        try:
            choices = response["choices"]
            message = choices[0]["message"]
        except (KeyError, IndexError, TypeError):
            return
        reasoning = message.get("reasoning_content")
        if reasoning is not None and not isinstance(reasoning, str):
            raise ProviderCallError(
                SafeProviderError(
                    code=ProviderErrorCode.MALFORMED_RESPONSE,
                    provider_id=ProviderId.HUNYUAN.value,
                )
            )

    @staticmethod
    def _unsupported_capability() -> ProviderCallError:
        return ProviderCallError(
            SafeProviderError(
                code=ProviderErrorCode.UNSUPPORTED_CAPABILITY,
                provider_id=ProviderId.HUNYUAN.value,
            )
        )


def _hunyuan_error_detail(
    error: OpenAICompatibleTransportError, provider_id: str
) -> SafeProviderError:
    status = error.status_code
    if error.kind is TransportFailureKind.TIMEOUT:
        code, retryable = ProviderErrorCode.TIMEOUT, True
    elif error.kind is TransportFailureKind.NETWORK:
        code, retryable = ProviderErrorCode.NETWORK_ERROR, True
    elif status == 401:
        code, retryable = ProviderErrorCode.AUTHENTICATION_FAILED, False
    elif status == 402:
        code, retryable = ProviderErrorCode.QUOTA_EXHAUSTED, False
    elif status == 403:
        code, retryable = ProviderErrorCode.MODEL_ACCESS_DENIED, False
    elif status == 404:
        code, retryable = ProviderErrorCode.MODEL_NOT_FOUND, False
    elif status == 429:
        code, retryable = ProviderErrorCode.RATE_LIMITED, True
    elif status is not None and status >= 500:
        code, retryable = ProviderErrorCode.PROVIDER_UNAVAILABLE, True
    else:
        code, retryable = ProviderErrorCode.INVALID_REQUEST, False
    return SafeProviderError(
        code=code,
        provider_id=provider_id,
        retryable=retryable,
        http_status=status,
    )
