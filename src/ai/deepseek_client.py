"""DeepSeek completion adapter over the shared OpenAI-compatible layer.

Official documentation verified 2026-09-20:

* Models/base URL: https://api-docs.deepseek.com/quick_start/pricing/
* Chat and thinking fields: https://api-docs.deepseek.com/api/create-chat-completion/
* Status semantics: https://api-docs.deepseek.com/quick_start/error_codes/

The adapter is completion-only in this phase.  It does not expose native tool
calling, vision, streaming, or reasoning content to Agent/RAG contracts.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Final

from src.ai.completion_stage import CompletionStage, current_completion_stage
from src.ai.credential_store import SecretCredential
from src.ai.model_registry import CapabilitySupport, ProviderId, get_model_preset
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

__all__ = ["DEFAULT_BASE_URL", "DeepSeekAdapter"]

DEFAULT_BASE_URL: Final[str] = "https://api.deepseek.com"
MAX_EXTRA_ATTEMPTS: Final[int] = 2
# Agent Decision runs non-thinking (its prompt forbids reasoning), so 256 is
# the floor for the final structured decision content only (policy fixed
# 2026-09-21, see docs/REAL_PAYLOAD_POLICY_AUDIT_2026-09-21.md).
_DECISION_MIN_OUTPUT_TOKENS: Final[int] = 256

LOGGER = logging.getLogger(__name__)


def _unconfigured_transport(
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    timeout_seconds: float,
) -> Mapping[str, Any]:
    del url, headers, payload, timeout_seconds
    raise AIUnavailableError("DeepSeek 传输层未配置，不发起真实 API 请求。")


class DeepSeekAdapter:
    """DeepSeek non-streaming adapter implementing the CompletionProvider boundary."""

    provider_id = ProviderId.DEEPSEEK.value
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
        normalized_model = model.strip()
        if not normalized_model:
            raise ValueError("Model ID 不能为空")
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
        """Return only final answer content; reasoning content remains internal."""

        credential = self._require_credential()
        chosen_model = (model or self._model).strip()
        stage = current_completion_stage()
        payload = build_chat_completion_payload(
            model=chosen_model,
            messages=[{"role": "user", "content": prompt}],
        )
        thinking = self._thinking_parameter(chosen_model, stage=stage)
        if max_completion_tokens is not None:
            if max_completion_tokens <= 0:
                raise ValueError("max_completion_tokens 必须为正数")
            # DeepSeek's current Chat Completions schema names this field
            # ``max_tokens``; this translation is vendor-specific.
            payload["max_tokens"] = self._completion_token_limit(
                max_completion_tokens, stage=stage
            )
        if thinking is not None:
            payload["thinking"] = thinking
        self._log_request_policy(
            stage=stage,
            model=chosen_model,
            max_tokens=payload.get("max_tokens"),
            thinking_type=thinking.get("type") if thinking else None,
        )
        try:
            response, retry_count = self._client.post(
                "/chat/completions",
                payload,
                credential=credential,
                provider_id=ProviderId.DEEPSEEK.value,
                error_mapper=_deepseek_error_detail,
            )
            self._validate_reasoning_content(response)
            result = parse_chat_completion(
                response, chosen_model, retry_count=retry_count
            )
            self._log_response_outcome(stage=stage, result=result)
            return result
        except ProviderCallError as exc:
            if exc.detail.code in {
                ProviderErrorCode.INVALID_RESPONSE,
                ProviderErrorCode.MALFORMED_RESPONSE,
            }:
                raise ProviderCallError(
                    SafeProviderError(
                        code=ProviderErrorCode.MALFORMED_RESPONSE,
                        provider_id=ProviderId.DEEPSEEK.value,
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
        """Fail explicitly: no DeepSeek vision business flow is added in this phase."""

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
            raise AIUnavailableError("DeepSeek 能力不可用：未配置 API Key。")
        return self._credential

    @staticmethod
    def _thinking_parameter(
        model_id: str, *, stage: CompletionStage | None
    ) -> dict[str, str] | None:
        # DeepSeek documents ``thinking.type=disabled``.  Product stages
        # (Agent Decision / Final Answer) always run non-thinking: both
        # prompts demand grounded, bounded output, so a hidden reasoning
        # stream must never compete for the output budget.
        if stage in (CompletionStage.AGENT_DECISION, CompletionStage.FINAL_ANSWER):
            return {"type": "disabled"}
        preset = get_model_preset(ProviderId.DEEPSEEK, model_id)
        if preset is None:
            # A custom id has unknown capability. Omitting the vendor field is
            # deliberate: do not infer support from the model name.
            return None
        support = preset.capabilities.reasoning
        if support is CapabilitySupport.SUPPORTED:
            return {"type": "enabled"}
        if support is CapabilitySupport.UNSUPPORTED:
            return {"type": "disabled"}
        return None

    @staticmethod
    def _completion_token_limit(
        requested: int, *, stage: CompletionStage | None
    ) -> int:
        """Raise the Agent Decision output floor to 256 (final content only).

        The floor applies only inside the Decision stage and never shrinks a
        larger caller budget; every other stage passes the requested cap
        through unchanged.
        """

        if stage is CompletionStage.AGENT_DECISION:
            return max(requested, _DECISION_MIN_OUTPUT_TOKENS)
        return requested

    @staticmethod
    def _log_request_policy(
        *,
        stage: CompletionStage | None,
        model: str,
        max_tokens: object,
        thinking_type: str | None,
    ) -> None:
        """Emit a redaction-safe request-policy line for runtime verification.

        Only non-secret policy fields are logged — never the prompt, the
        credential, or any response content (audit policy 2026-09-21).
        """

        LOGGER.info(
            "AI 请求策略：provider=deepseek stage=%s model=%s max_tokens=%s "
            "thinking=%s",
            stage.value if stage is not None else "none",
            model,
            max_tokens,
            thinking_type or "omitted",
        )

    @staticmethod
    def _log_response_outcome(
        *, stage: CompletionStage | None, result: CompletionResult
    ) -> None:
        """Emit a redaction-safe response-outcome line for the audit trail."""

        usage = result.usage
        LOGGER.info(
            "AI 响应结果：provider=deepseek stage=%s finish_reason=%s "
            "completion_tokens=%s total_tokens=%s",
            stage.value if stage is not None else "none",
            result.finish_reason,
            usage.completion_tokens if usage else None,
            usage.total_tokens if usage else None,
        )

    @staticmethod
    def _validate_reasoning_content(response: Mapping[str, Any]) -> None:
        try:
            choices = response["choices"]
            message = choices[0]["message"]
        except (KeyError, IndexError, TypeError):
            return  # The common codec emits the normalized malformed error.
        reasoning = message.get("reasoning_content")
        if reasoning is not None and not isinstance(reasoning, str):
            raise ProviderCallError(
                SafeProviderError(
                    code=ProviderErrorCode.MALFORMED_RESPONSE,
                    provider_id=ProviderId.DEEPSEEK.value,
                )
            )

    @staticmethod
    def _unsupported_capability() -> ProviderCallError:
        return ProviderCallError(
            SafeProviderError(
                code=ProviderErrorCode.UNSUPPORTED_CAPABILITY,
                provider_id=ProviderId.DEEPSEEK.value,
            )
        )


def _deepseek_error_detail(
    error: OpenAICompatibleTransportError, provider_id: str
) -> SafeProviderError:
    status = error.status_code
    if error.kind is TransportFailureKind.TIMEOUT:
        code = ProviderErrorCode.TIMEOUT
        retryable = True
    elif error.kind is TransportFailureKind.NETWORK:
        code = ProviderErrorCode.NETWORK_ERROR
        retryable = True
    elif status == 401:
        code = ProviderErrorCode.AUTHENTICATION_FAILED
        retryable = False
    elif status == 402:
        code = ProviderErrorCode.QUOTA_EXHAUSTED
        retryable = False
    elif status == 403:
        code = ProviderErrorCode.MODEL_ACCESS_DENIED
        retryable = False
    elif status == 404:
        code = ProviderErrorCode.MODEL_NOT_FOUND
        retryable = False
    elif status == 429:
        code = ProviderErrorCode.RATE_LIMITED
        retryable = True
    elif status is not None and status >= 500:
        code = ProviderErrorCode.PROVIDER_UNAVAILABLE
        retryable = True
    else:
        code = ProviderErrorCode.INVALID_REQUEST
        retryable = False
    return SafeProviderError(
        code=code,
        provider_id=provider_id,
        retryable=retryable,
        http_status=status,
    )
