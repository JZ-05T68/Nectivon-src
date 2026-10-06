"""Qwen (Aliyun Bailian / DashScope) adapter for the AI provider boundary.

Vendor-neutral HTTPS, Bearer authentication, bounded retry, and the common
non-streaming chat codec live in :mod:`src.ai.openai_compatible`.  This module
retains only Qwen model policy, ``enable_thinking``, vision message shape,
embedding validation, and deferred rerank behavior.

Wire transport:

- The wire transport is an injected callable. ``urllib_transport`` is the
  minimal standard-library HTTP implementation; it is never wired in
  automatically. The default remains the unconfigured transport, which
  raises ``AIUnavailableError`` on any call, so manual mode, a missing API
  key, application startup, import, and provider construction can never
  emit network traffic. Only an explicit ``complete``/``embed`` call on a
  provider that was deliberately built with a real transport can send a
  request.
- Construction performs no I/O: it does not read the environment, touch
  the disk, or open a connection.

Retry policy (cost and loop guardrails):

- At most ``max_extra_attempts`` (default 2) extra attempts per call, a
  flat bounded loop — never recursive, never an agent loop, and callers
  can override it (a paid smoke call forces 0).
- Only transient transport failures are retried: network-level failures
  (no HTTP status), HTTP 429, and HTTP 5xx.
- Client errors (other 4xx), malformed responses, and semantic
  dissatisfaction with an answer are never retried.

The chat-completions and embeddings payloads follow the DashScope
OpenAI-compatible mode. Thinking is Qwen-specific: outside the product
Decision / Final Answer stages it is derived from the selected registry
preset's reasoning capability (or the explicit constructor override); inside
those stages the adapter forces ``enable_thinking: false`` because both
product prompts demand grounded, bounded output. Unknown/custom
models conservatively send ``enable_thinking: false`` to preserve the legacy
non-streaming behavior without guessing. An explicit constructor override is
kept for compatibility with existing hosted/tooling composition roots.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Final

from src.ai.completion_stage import CompletionStage, current_completion_stage
from src.ai.credential_store import SecretCredential
from src.ai.model_registry import (
    CapabilitySupport,
    ProviderId,
    get_model_preset,
)
from src.ai.openai_compatible import (
    OpenAICompatibleClient,
    OpenAICompatibleTransportError,
    Transport,
    build_chat_completion_payload,
    parse_chat_completion,
    urllib_transport,
)
from src.ai.provider import (
    AIExecutionError,
    AIUnavailableError,
    CompletionResult,
    EmbeddingResult,
    EmbeddingUsage,
    RerankResult,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "MAX_EXTRA_ATTEMPTS",
    "QwenProvider",
    "QwenTransportError",
    "Transport",
    "urllib_transport",
]

# Agent Decision is a short structured-output task whose prompt explicitly
# forbids reasoning. Product stages (Decision / Final Answer) therefore run
# non-thinking, and 256 is the floor for the *final* structured decision
# content — never a hidden-reasoning budget (policy fixed 2026-09-21, see
# docs/REAL_PAYLOAD_POLICY_AUDIT_2026-09-21.md).
_DECISION_MIN_OUTPUT_TOKENS: Final[int] = 256

LOGGER = logging.getLogger(__name__)

DEFAULT_BASE_URL: Final[str] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MAX_EXTRA_ATTEMPTS: Final[int] = 2
QwenTransportError = OpenAICompatibleTransportError
#: Vision calls (stage-2 handwriting/chart reading) legitimately run far
#: longer than 30 s text completions: the model transcribes dense pages. A
#: 30 s cap turned every honest vision request into a timeout-retry-fail
#: cascade (V086-204). This is a floor, never a shrink of a larger setting.
VISION_MIN_TIMEOUT_SECONDS: Final[float] = 120.0


#: Learning drafts (first layer / wings / correction / self-explanation
#: review) are long structured-JSON completions.  The overnight round
#: measured 3×30 s transport timeouts on honest bounded requests with the
#: default cap — the same failure class the vision floor fixed (V086-203
#: family, 2026-09-27).  A floor only, never a shrink of a larger setting.
LEARNING_DRAFT_MIN_TIMEOUT_SECONDS: Final[float] = 120.0


def _unconfigured_transport(
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    timeout_seconds: float,
) -> Mapping[str, Any]:
    """Default transport: guarantee this phase never emits real requests."""

    raise AIUnavailableError(
        "AI 传输层未配置：当前环境不发起真实 AI API 请求。"
    )


class QwenProvider:
    """Qwen adapter implementing the completion and embedding contracts.

    Legacy callers may still pass a plain string; the adapter immediately
    wraps it in ``SecretCredential``. New composition passes the wrapper
    directly, and the value is revealed only while building the Bearer header
    for an actual request.
    """

    provider_id = ProviderId.QWEN.value
    supports_embedding = True

    def __init__(
        self,
        *,
        api_key: str | SecretCredential,
        llm_model: str,
        llm_model_hard: str,
        embedding_model: str,
        rerank_model: str,
        vision_model: str = "",
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 30.0,
        max_extra_attempts: int = MAX_EXTRA_ATTEMPTS,
        enable_thinking: bool | None = None,
        transport: Transport = _unconfigured_transport,
    ) -> None:
        if max_extra_attempts < 0:
            raise ValueError("max_extra_attempts 不能为负数")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须为正数")
        if isinstance(api_key, SecretCredential):
            self._credential: SecretCredential | None = api_key
        else:
            self._credential = SecretCredential(api_key) if api_key.strip() else None
        self._llm_model = llm_model
        self._llm_model_hard = llm_model_hard
        self._embedding_model = embedding_model
        self._rerank_model = rerank_model
        self._vision_model = vision_model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_extra_attempts = max_extra_attempts
        self._enable_thinking = enable_thinking
        self._transport = transport
        self._client = OpenAICompatibleClient(
            base_url=self._base_url,
            timeout_seconds=timeout_seconds,
            max_extra_attempts=max_extra_attempts,
            transport=transport,
        )

    @property
    def is_configured(self) -> bool:
        """Return whether an API credential is present."""

        return self._credential is not None

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_completion_tokens: int | None = None,
    ) -> CompletionResult:
        """Return the completion for ``prompt`` via the chat endpoint.

        Every request explicitly carries ``enable_thinking`` (default off —
        thinking is a paid behavior that higher layers must opt into) and
        ``stream: false``; ``max_completion_tokens`` is included when given.
        No tools, web search, or agent fields are ever added.
        """

        self._require_credential()
        chosen_model = model or self._llm_model
        if max_completion_tokens is not None and max_completion_tokens <= 0:
            raise ValueError("max_completion_tokens 必须为正数")
        stage = current_completion_stage()
        thinking_enabled = self._thinking_enabled(chosen_model, stage=stage)
        payload = build_chat_completion_payload(
            model=chosen_model,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=self._decision_output_tokens(
                max_completion_tokens, stage=stage
            ),
        )
        payload["enable_thinking"] = thinking_enabled
        timeout_override: float | None = None
        if stage is CompletionStage.LEARNING_DRAFT:
            timeout_override = max(
                self._timeout_seconds, LEARNING_DRAFT_MIN_TIMEOUT_SECONDS
            )
        self._log_request_policy(
            stage=stage,
            model=chosen_model,
            max_tokens=payload.get("max_tokens"),
            max_completion_tokens=payload.get("max_completion_tokens"),
            thinking_enabled=thinking_enabled,
        )
        response, retry_count = self._post(
            "/chat/completions", payload, timeout_seconds=timeout_override
        )
        result = parse_chat_completion(response, chosen_model, retry_count=retry_count)
        self._log_response_outcome(stage=stage, result=result)
        return result

    def complete_vision(
        self,
        prompt: str,
        image_png_base64: str,
        *,
        model: str | None = None,
        max_completion_tokens: int | None = None,
        json_output: bool = False,
    ) -> CompletionResult:
        """Return one completion over a prompt plus a PNG image (v0.7.2).

        The image is sent inline as a base64 data URL to a vision-capable
        model. Budget/audit responsibility stays with the caller's wrapper;
        this method only shapes the vendor payload.
        """

        self._require_credential()
        if not image_png_base64.strip():
            raise ValueError("视觉调用必须提供图片内容")
        chosen_model = model or self._vision_model
        if not chosen_model:
            raise AIUnavailableError(
                "未配置视觉模型，无法读取页面图片。"
            )
        # Callers may pass a prepared data URL (visual input budget, V086-204)
        # or a bare base64 PNG payload; both are forwarded verbatim.
        if image_png_base64.startswith("data:"):
            image_url = image_png_base64
        else:
            image_url = f"data:image/png;base64,{image_png_base64}"
        payload = build_chat_completion_payload(
            model=chosen_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url},
                        },
                    ],
                }
            ],
            max_completion_tokens=max_completion_tokens,
        )
        payload["enable_thinking"] = self._thinking_enabled(
            chosen_model, stage=current_completion_stage()
        )
        if json_output:
            payload["response_format"] = {"type": "json_object"}
            payload["enable_thinking"] = False
            payload["temperature"] = 0.2
        response, retry_count = self._post(
            "/chat/completions",
            payload,
            timeout_seconds=max(self._timeout_seconds, VISION_MIN_TIMEOUT_SECONDS),
        )
        return parse_chat_completion(response, chosen_model, retry_count=retry_count)

    def embed(
        self,
        texts: Sequence[str],
        *,
        model: str | None = None,
        dimensions: int | None = None,
    ) -> EmbeddingResult:
        """Return one embedding per input text, in input order.

        The payload stays minimal (model, input, float encoding, optional
        dimensions) with no thinking-related or vendor-extra fields. The
        response is validated fail-closed: vector count must match the
        input count, indexes must be an exact permutation of the input
        positions, no vector may be empty, non-numeric values are
        rejected, and a requested ``dimensions`` value must match every
        returned vector.
        """

        self._require_credential()
        if not texts:
            raise ValueError("embedding 输入不能为空")
        if dimensions is not None and dimensions <= 0:
            raise ValueError("dimensions 必须为正数")
        chosen_model = model or self._embedding_model
        payload: dict[str, Any] = {
            "model": chosen_model,
            "input": list(texts),
            "encoding_format": "float",
        }
        if dimensions is not None:
            payload["dimensions"] = dimensions
        response, retry_count = self._post("/embeddings", payload)
        return self._parse_embeddings(
            response,
            chosen_model,
            expected_count=len(texts),
            dimensions=dimensions,
            retry_count=retry_count,
        )

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        model: str | None = None,
        top_n: int | None = None,
    ) -> RerankResult:
        """Rerank is deferred: its vendor contract is verified in a later phase."""

        raise AIUnavailableError(
            "Qwen rerank 通道尚未启用：其官方接口契约将在后续阶段核对后接入。"
        )

    def _require_credential(self) -> None:
        if self._credential is None:
            raise AIUnavailableError(
                "AI 能力不可用：未配置 API Key（当前为手动模式或未设置 EKB_AI_API_KEY）。"
            )

    def _post(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[Mapping[str, Any], int]:
        """Send one request with the bounded, flat retry policy.

        Returns ``(response_body, extra_attempts_used)`` so the adapter can
        report the consumed retry budget to the vendor-neutral audit layer.
        ``timeout_seconds`` optionally overrides the constructor timeout for
        one call (vision uses a higher floor); it never shrinks it.
        """

        credential = self._credential
        if credential is None:  # guarded by every public call
            raise AIUnavailableError("AI 能力不可用：未配置 API Key。")
        return self._client.post(
            path,
            payload,
            credential=credential,
            provider_id=ProviderId.QWEN.value,
            timeout_seconds=timeout_seconds,
        )

    def _thinking_enabled(self, model_id: str, *, stage: CompletionStage | None) -> bool:
        """Resolve Qwen thinking without guessing custom-model capabilities.

        Product stages (Agent Decision / Final Answer) always run
        non-thinking: both prompts demand grounded, bounded output, and a
        hidden reasoning stream must never compete for the output budget.
        This stage policy outranks both the registry preset and the legacy
        constructor override; outside those stages the preset/override
        behavior is preserved unchanged.
        """

        if stage in (CompletionStage.AGENT_DECISION, CompletionStage.FINAL_ANSWER):
            return False
        # Page reading is a strict bounded-JSON extraction task; a hidden
        # reasoning stream would consume the completion budget and truncate
        # the JSON payload (V086-203). Same policy rank as product stages.
        if stage is CompletionStage.PAGE_READING:
            return False
        # Learning drafts are the same bounded-JSON request class as page
        # reading (overnight round, 2026-09-27): thinking off keeps the
        # whole output budget for the JSON payload itself.
        if stage is CompletionStage.LEARNING_DRAFT:
            return False
        if self._enable_thinking is not None:
            return self._enable_thinking
        preset = get_model_preset(ProviderId.QWEN, model_id)
        if preset is None:
            # Unknown/custom model: explicit false preserves the established
            # non-streaming request behavior and avoids capability inference.
            return False
        return preset.capabilities.reasoning is CapabilitySupport.SUPPORTED

    @staticmethod
    def _decision_output_tokens(
        requested: int | None, *, stage: CompletionStage | None
    ) -> int | None:
        """Raise the Agent Decision output floor to 256 (final content only).

        The floor applies only inside the Decision stage and never shrinks a
        larger caller budget. Outside Decision the requested cap passes
        through unchanged.
        """

        if requested is None or stage is not CompletionStage.AGENT_DECISION:
            return requested
        return max(requested, _DECISION_MIN_OUTPUT_TOKENS)

    @staticmethod
    def _log_request_policy(
        *,
        stage: CompletionStage | None,
        model: str,
        max_tokens: object,
        max_completion_tokens: object,
        thinking_enabled: bool,
    ) -> None:
        """Emit a redaction-safe request-policy line for runtime verification.

        Only non-secret policy fields are logged — never the prompt, the
        credential, or any response content (audit policy 2026-09-21).
        """

        LOGGER.info(
            "AI 请求策略：provider=qwen stage=%s model=%s max_tokens=%s "
            "max_completion_tokens=%s thinking=%s",
            stage.value if stage is not None else "none",
            model,
            max_tokens,
            max_completion_tokens,
            "enabled" if thinking_enabled else "disabled",
        )

    @staticmethod
    def _log_response_outcome(
        *, stage: CompletionStage | None, result: CompletionResult
    ) -> None:
        """Emit a redaction-safe response-outcome line for the audit trail."""

        usage = result.usage
        LOGGER.info(
            "AI 响应结果：provider=qwen stage=%s finish_reason=%s "
            "completion_tokens=%s total_tokens=%s",
            stage.value if stage is not None else "none",
            result.finish_reason,
            usage.completion_tokens if usage else None,
            usage.total_tokens if usage else None,
        )

    @staticmethod
    def _parse_embeddings(
        response: Mapping[str, Any],
        requested_model: str,
        *,
        expected_count: int,
        dimensions: int | None,
        retry_count: int = 0,
    ) -> EmbeddingResult:
        """Parse and fail-closed validate one embeddings response."""

        try:
            data = response["data"]
            items = sorted(data, key=lambda item: item["index"])
        except (KeyError, TypeError) as exc:
            raise AIExecutionError(
                "AI 响应解析失败：缺少 data/embedding 结构。",
                error_class="parse",
            ) from exc
        if len(items) != expected_count:
            raise AIExecutionError(
                f"AI 响应校验失败：返回向量数 {len(items)} 与输入数 {expected_count} 不一致。",
                error_class="parse",
            )
        if [item["index"] for item in items] != list(range(expected_count)):
            raise AIExecutionError(
                "AI 响应校验失败：embedding index 与输入顺序不对应。",
                error_class="parse",
            )
        vectors: list[tuple[float, ...]] = []
        for item in items:
            raw_vector = item.get("embedding")
            if raw_vector is None:
                raise AIExecutionError(
                    "AI 响应解析失败：缺少 data/embedding 结构。",
                    error_class="parse",
                )
            if not isinstance(raw_vector, Sequence) or isinstance(raw_vector, str):
                raise AIExecutionError(
                    "AI 响应校验失败：embedding 不是数值数组。",
                    error_class="parse",
                )
            if len(raw_vector) == 0:
                raise AIExecutionError(
                    "AI 响应校验失败：存在空 embedding 向量。",
                    error_class="parse",
                )
            if dimensions is not None and len(raw_vector) != dimensions:
                raise AIExecutionError(
                    f"AI 响应校验失败：向量维度 {len(raw_vector)} 与请求值 {dimensions} 不一致。",
                    error_class="parse",
                )
            try:
                vectors.append(tuple(float(value) for value in raw_vector))
            except (TypeError, ValueError) as exc:
                raise AIExecutionError(
                    "AI 响应校验失败：embedding 含非数值元素。",
                    error_class="parse",
                ) from exc
        raw_model = response.get("model")
        resolved_model = (
            raw_model.strip()
            if isinstance(raw_model, str) and raw_model.strip()
            else None
        )
        return EmbeddingResult(
            embeddings=tuple(vectors),
            model=resolved_model or requested_model,
            usage=QwenProvider._parse_embedding_usage(response.get("usage")),
            retry_count=retry_count,
            resolved_model=resolved_model,
        )

    @staticmethod
    def _parse_embedding_usage(raw_usage: Any) -> EmbeddingUsage | None:
        """Parse optional embedding token usage; absent usage is not an error."""

        if not isinstance(raw_usage, Mapping):
            return None
        try:
            return EmbeddingUsage(
                prompt_tokens=int(raw_usage["prompt_tokens"]),
                total_tokens=int(raw_usage["total_tokens"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AIExecutionError(
                "AI 响应解析失败：usage 结构不完整。", error_class="parse"
            ) from exc
