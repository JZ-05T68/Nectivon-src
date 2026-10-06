"""Vendor-neutral AI provider contracts for the optional AI layer.

This module is the single authoritative definition of the AI call boundary:

- ``CompletionProvider`` / ``EmbeddingProvider`` / ``RerankProvider`` are the
  protocols a concrete vendor adapter must satisfy, one per capability so
  callers depend only on the capability they actually use;
- ``AIError`` / ``AIUnavailableError`` / ``AIExecutionError`` express the two
  distinct failure semantics: "AI cannot be called right now" (manual mode,
  missing credential, capability not yet enabled) versus "a call was
  attempted and failed" (transport, protocol, or parsing failure);
- the frozen result dataclasses carry only the fields callers need, plus the
  model echo required for auditability.

The module intentionally performs no I/O at import time: it reads no
configuration, no API key, no disk, and no network, and imports no vendor
SDK. Vendor-specific adapters (for example the Qwen adapter) live outside
this module and implement these protocols. Business services never import
vendor modules; they depend on these contracts only.

``AIProvider`` is the legacy minimal extension point originally defined in
``src.prompt_builder``. It is kept for backward compatibility and re-exported
there; new code should depend on the capability protocols above.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

LOGGER = logging.getLogger(__name__)

__all__ = [
    "AIError",
    "AIExecutionError",
    "AIProvider",
    "AIProductionCompositionError",
    "AIBudgetExceededError",
    "AIUnavailableError",
    "AiBudgetGuard",
    "AiCallLedger",
    "AiCallRecord",
    "AiOutputRecord",
    "AuditedAIProvider",
    "build_production_audited_provider",
    "CompletionProvider",
    "CompletionResult",
    "CompletionUsage",
    "EmbeddingProvider",
    "EmbeddingResult",
    "EmbeddingUsage",
    "RerankHit",
    "RerankProvider",
    "RerankResult",
    "ProviderCallError",
    "ProviderErrorCode",
    "SafeProviderError",
    "require_ai_provider",
    "require_production_audited_provider",
]


class AIError(RuntimeError):
    """Base class of all AI boundary failures.

    Messages must never carry API keys or credentials.
    """


class AIUnavailableError(AIError):
    """AI cannot be called right now.

    Covers manual AI mode, a missing API key, a disabled capability, or an
    adapter without a configured transport. This is deliberately distinct
    from ``AIExecutionError`` so callers can tell "not configured" apart
    from "configured but the call failed".
    """


class AIProductionCompositionError(AIUnavailableError):
    """Production AI composition is missing its approved audit/budget boundary."""


class AIBudgetExceededError(AIUnavailableError):
    """The configured token budget is exhausted; the call was rejected.

    This is the typed machine signal for budget exhaustion, deliberately a
    subclass of ``AIUnavailableError`` so existing callers that catch the
    wider type keep working, while precise consumers can catch budget
    rejection first. Machine decisions must classify on this type, never on
    the human-readable message.
    """


class ProviderErrorCode(StrEnum):
    """Stable cross-vendor failure codes safe for UI and audit classification."""

    AUTHENTICATION = "authentication"
    INVALID_CONFIGURATION = "invalid_configuration"
    UNSUPPORTED_MODEL = "unsupported_model"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    PROVIDER_RESPONSE = "provider_response"
    INVALID_RESPONSE = "invalid_response"
    CREDENTIAL_STORE = "credential_store"
    CREDENTIAL_MISSING = "credential_missing"
    INVALID_BASE_URL = "invalid_base_url"
    AUTHENTICATION_FAILED = "authentication_failed"
    MODEL_NOT_FOUND = "model_not_found"
    MODEL_ACCESS_DENIED = "model_access_denied"
    QUOTA_EXHAUSTED = "quota_exhausted"
    NETWORK_ERROR = "network_error"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    INVALID_REQUEST = "invalid_request"
    MALFORMED_RESPONSE = "malformed_response"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"


_SAFE_PROVIDER_MESSAGES: dict[ProviderErrorCode, str] = {
    ProviderErrorCode.AUTHENTICATION: "Provider 身份验证失败，请检查凭据。",
    ProviderErrorCode.INVALID_CONFIGURATION: "Provider 配置无效。",
    ProviderErrorCode.UNSUPPORTED_MODEL: "Provider 不支持所选模型。",
    ProviderErrorCode.RATE_LIMITED: "Provider 请求频率受限（HTTP 429），请稍后重试。",
    ProviderErrorCode.TIMEOUT: "Provider 请求超时，请稍后重试。",
    ProviderErrorCode.TRANSPORT: "Provider 网络请求失败。",
    ProviderErrorCode.PROVIDER_RESPONSE: "Provider 返回错误。",
    ProviderErrorCode.INVALID_RESPONSE: (
        "AI 响应解析失败：JSON 对象或 choices/message/content/usage 结构无效。"
    ),
    ProviderErrorCode.CREDENTIAL_STORE: "Provider 凭据存储不可用。",
    ProviderErrorCode.CREDENTIAL_MISSING: "尚未为所选 Provider 保存 API Key。",
    ProviderErrorCode.INVALID_BASE_URL: "Base URL 必须是无凭据、无查询参数的 HTTPS 地址。",
    ProviderErrorCode.AUTHENTICATION_FAILED: "Provider 身份验证失败，请检查凭据。",
    ProviderErrorCode.MODEL_NOT_FOUND: "Provider 未找到所选模型。",
    ProviderErrorCode.MODEL_ACCESS_DENIED: "当前凭据无权访问所选模型。",
    ProviderErrorCode.QUOTA_EXHAUSTED: "Provider 账户额度已用尽。",
    ProviderErrorCode.NETWORK_ERROR: "Provider 网络连接失败。",
    ProviderErrorCode.PROVIDER_UNAVAILABLE: "Provider 服务暂时不可用。",
    ProviderErrorCode.INVALID_REQUEST: "Provider 请求参数无效。",
    ProviderErrorCode.MALFORMED_RESPONSE: "AI 响应解析失败：响应不是合法 JSON 对象。",
    ProviderErrorCode.UNSUPPORTED_CAPABILITY: "所选 Provider 不支持此能力。",
}


@dataclass(frozen=True, slots=True)
class SafeProviderError:
    """Sanitized failure data that cannot carry raw vendor response content.

    There is intentionally no free-form detail field.  Adapters may retain raw
    diagnostics only inside their local, redacted debug handling; the boundary
    exposes a stable code, a predefined message and non-secret metadata.
    """

    code: ProviderErrorCode
    provider_id: str | None = None
    retryable: bool = False
    http_status: int | None = None

    def __post_init__(self) -> None:
        if self.provider_id not in {None, "deepseek", "qwen", "kimi", "hunyuan", "glm"}:
            raise ValueError("Provider ID 无效")
        if self.http_status is not None and not 100 <= self.http_status <= 599:
            raise ValueError("Provider HTTP 状态码无效")

    @property
    def message(self) -> str:
        message = _SAFE_PROVIDER_MESSAGES[self.code]
        if self.http_status is not None and self.code is not ProviderErrorCode.RATE_LIMITED:
            return f"{message}（HTTP {self.http_status}）"
        return message


class AIExecutionError(AIError):
    """An AI call was attempted and failed.

    Covers transport failures, vendor error responses, and malformed
    response payloads after the retry policy is exhausted. ``error_class``
    carries a machine-readable failure category for the audit ledger, and
    ``retry_count`` reports how many extra attempts the adapter consumed.
    Messages must never carry API keys or credentials.
    """

    def __init__(
        self,
        message: str,
        *,
        error_class: str = "execution",
        retry_count: int = 0,
        finish_reason: str | None = None,
        output_chars: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.retry_count = retry_count
        self.finish_reason = finish_reason
        self.output_chars = output_chars
        self.output_tokens = output_tokens


class ProviderCallError(AIExecutionError):
    """An attempted provider call failed with sanitized structured details."""

    def __init__(self, detail: SafeProviderError, *, retry_count: int = 0) -> None:
        super().__init__(
            detail.message,
            error_class=detail.code.value,
            retry_count=retry_count,
        )
        self.detail = detail


@dataclass(frozen=True, slots=True)
class CompletionUsage:
    """Token accounting reported by one completion call."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class CompletionResult:
    """One completion response with effective and response-reported models.

    ``model`` preserves the established effective-model contract for business
    callers: adapters may fall back to the requested model when a response
    omits its model identifier.  ``resolved_model`` is stricter audit metadata
    and is populated only when the provider response itself contains a
    non-empty model identifier.
    """

    text: str
    model: str
    usage: CompletionUsage | None = None
    finish_reason: str | None = None
    retry_count: int = 0
    resolved_model: str | None = None


@dataclass(frozen=True, slots=True)
class EmbeddingUsage:
    """Token accounting reported by one embedding call.

    Embeddings only consume input tokens; there is no completion side,
    so this deliberately does not reuse ``CompletionUsage``.
    """

    prompt_tokens: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """Embeddings for one batch of input texts, in input order."""

    embeddings: tuple[tuple[float, ...], ...]
    model: str
    usage: EmbeddingUsage | None = None
    retry_count: int = 0
    resolved_model: str | None = None


@dataclass(frozen=True, slots=True)
class RerankHit:
    """One reranked document: its index in the input batch and its score."""

    index: int
    relevance_score: float


@dataclass(frozen=True, slots=True)
class RerankResult:
    """Reranked hits ordered by descending relevance."""

    hits: tuple[RerankHit, ...]
    model: str


@runtime_checkable
class CompletionProvider(Protocol):
    """Minimal contract of a text completion provider."""

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_completion_tokens: int | None = None,
    ) -> CompletionResult:
        """Return the completion for ``prompt``.

        ``model`` overrides the provider's default model for this call.
        ``max_completion_tokens`` caps the generated tokens for this call;
        ``None`` leaves the provider default. Implementations raise
        ``AIUnavailableError`` when the provider cannot be called at all,
        and ``AIExecutionError`` when an attempted call fails. Semantic
        dissatisfaction with an answer is never a reason for the client
        layer to retry automatically.
        """
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Minimal contract of a text embedding provider."""

    def embed(
        self,
        texts: Sequence[str],
        *,
        model: str | None = None,
        dimensions: int | None = None,
    ) -> EmbeddingResult:
        """Return one embedding per input text, in input order.

        ``dimensions`` requests a specific vector size; implementations
        fail closed when the returned vectors do not match it. A missing
        usage report is expressed as ``usage=None``, never fabricated.
        """
        ...


@runtime_checkable
class RerankProvider(Protocol):
    """Minimal contract of a document rerank provider."""

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        model: str | None = None,
        top_n: int | None = None,
    ) -> RerankResult:
        """Return input document indexes ordered by relevance to ``query``."""
        ...


@runtime_checkable
class AIProvider(Protocol):
    """Legacy minimal extension point kept for backward compatibility.

    Originally defined in ``src.prompt_builder`` for the manual AI mode.
    New code should depend on ``CompletionProvider`` instead; this protocol
    remains the authoritative definition of the legacy ``generate``
    signature so existing imports keep working unchanged.
    """

    def generate(self, prompt: str) -> str:
        """Generate a response for ``prompt`` when a provider is enabled."""
        ...


def require_ai_provider(provider: CompletionProvider | None) -> CompletionProvider:
    """Return the given provider, or raise ``AIUnavailableError`` when absent.

    Callers that receive an optional provider use this to convert the
    disabled state into one explicit, typed failure instead of an
    ``AttributeError`` on ``None``. A real provider instance is returned
    unchanged — never wrapped or replaced.
    """

    if provider is None:
        raise AIUnavailableError(
            "AI 能力不可用：当前为手动模式，或尚未配置 API Key。"
        )
    return provider


# --------------------------------------------------------------------------
# AI call audit ledger (vendor-neutral, contract layer)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AiCallRecord:
    """One immutable audit row describing a single AI call attempt cycle.

    The record deliberately carries no prompt text, no response text and no
    credential: ``prompt_sha256`` is the only content-derived value stored,
    and ``target_refs`` holds stable-id references to local knowledge anchors
    when the caller supplies them.
    """

    call_uuid: str
    capability: str
    model: str
    prompt_sha256: str
    input_chars: int
    status: str
    source_feature: str
    target_refs: tuple[str, ...] = ()
    error_class: str | None = None
    retry_count: int = 0
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    finish_reason: str | None = None
    created_at: str = ""
    provider: str = "qwen"
    resolved_model: str | None = None

    @property
    def requested_model(self) -> str:
        """Return the model sent in the request.

        The schema-v12 physical column remains named ``model`` to avoid a
        no-value data copy.  At the application boundary its meaning is
        explicitly ``requested_model``.
        """

        return self.model


@dataclass(frozen=True, slots=True)
class AiOutputRecord:
    """One immutable audit anchor for a user-imported or exported AI output.

    The record is an audit anchor only: it stores hashes and stable-id
    references, never the output text itself, and it never mutates any
    knowledge asset. Knowledge writes remain an explicit user action.
    """

    output_uuid: str
    model: str
    output_sha256: str
    output_kind: str
    source_feature: str
    call_uuid: str | None = None
    context_package_sha256: str | None = None
    target_refs: tuple[str, ...] = ()
    recheck_path: str | None = None
    created_at: str = ""


class AiCallLedger(Protocol):
    """Append-only sink for :class:`AiCallRecord` rows."""

    def record(self, call: AiCallRecord) -> None:
        """Persist one call record; must never raise into the AI call path."""
        ...


class AiBudgetGuard(Protocol):
    """Pre-call budget gate evaluated before any network request is sent."""

    def ensure_allowed(self, capability: str) -> None:
        """Raise ``AIBudgetExceededError`` when the budget is exhausted."""
        ...


class _NullAiCallLedger:
    """Default no-op ledger used when no durable ledger is configured."""

    def record(self, call: AiCallRecord) -> None:
        return None


class _NullAiBudgetGuard:
    """Default no-op budget gate that always allows the call."""

    def ensure_allowed(self, capability: str) -> None:
        return None


_PRODUCTION_COMPOSITION_MARKER = object()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_texts(texts: Sequence[str]) -> str:
    payload = json.dumps(list(texts), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class AuditedAIProvider:
    """Vendor-neutral call-lifecycle wrapper around one AI provider.

    The wrapper owns everything the audit ledger needs — call uuid, prompt
    hashing, input sizing, latency, token accounting, error classification and
    retry reporting — so replacing the wrapped vendor adapter never changes
    ledger behaviour. It implements the completion and embedding capability
    protocols and can be used anywhere those protocols are accepted.

    The budget guard is evaluated before the wrapped provider is invoked, so
    an over-budget call performs no network I/O and is recorded with
    ``status="rejected"``. Ledger write failures never break the caller's AI
    result or exception: they are logged with traceback and swallowed only for
    the audit side effect.
    """

    def __init__(
        self,
        provider: object,
        *,
        default_model: str,
        default_embedding_model: str,
        source_feature: str,
        target_refs: Sequence[str] = (),
        ledger: AiCallLedger | None = None,
        budget_guard: AiBudgetGuard | None = None,
        _production_marker: object | None = None,
    ) -> None:
        if not default_model.strip():
            raise ValueError("default_model 不能为空")
        if not default_embedding_model.strip():
            raise ValueError("default_embedding_model 不能为空")
        if not source_feature.strip():
            raise ValueError("source_feature 不能为空")
        self._wrapped = provider
        self._default_model = default_model
        self._default_embedding_model = default_embedding_model
        self._source_feature = source_feature
        self._target_refs = tuple(target_refs)
        self._ledger = ledger or _NullAiCallLedger()
        self._budget_guard = budget_guard or _NullAiBudgetGuard()
        self._production_marker = _production_marker

    @property
    def is_configured(self) -> bool:
        """Mirror the wrapped provider's credential state when it exists."""
        return bool(getattr(self._wrapped, "is_configured", False))

    @property
    def provider_id(self) -> str | None:
        """Expose only the wrapped provider identity, never its credential."""

        value = getattr(self._wrapped, "provider_id", None)
        return value if isinstance(value, str) else None

    @property
    def default_model(self) -> str:
        """Return the configured completion model used by composition roots."""

        return self._default_model

    @property
    def supports_embedding(self) -> bool:
        """Report adapter implementation support, independent of Protocol shape."""

        declared = getattr(self._wrapped, "supports_embedding", None)
        if declared is not None:
            return bool(declared)
        # Preserve the structural protocol contract for injected/test providers.
        # Product adapters declare this flag explicitly, so completion-only
        # vendors cannot appear embedding-capable merely because the audited
        # wrapper itself implements ``embed``.
        return isinstance(self._wrapped, EmbeddingProvider)

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_completion_tokens: int | None = None,
        source_feature: str | None = None,
        target_refs: Sequence[str] | None = None,
    ) -> CompletionResult:
        """Return the wrapped completion with a full audit record.

        ``source_feature`` / ``target_refs`` are optional per-call overrides
        for the audit ledger; when omitted, the wrapper's construction-time
        values are used. Callers that depend only on the ``CompletionProvider``
        protocol never pass them.
        """

        chosen_model = model or self._default_model
        base: dict[str, object] = {
            "call_uuid": str(uuid.uuid4()),
            "capability": "completion",
            "requested_model": chosen_model,
            "prompt_sha256": _sha256_text(prompt),
            "input_chars": len(prompt),
            "source_feature": source_feature or self._source_feature,
            "target_refs": (
                tuple(target_refs) if target_refs is not None else self._target_refs
            ),
            "created_at": _utc_timestamp(),
        }
        self._ensure_allowed("completion", base)
        started = time.monotonic()
        try:
            result = self._wrapped.complete(  # type: ignore[attr-defined]
                prompt, model=model, max_completion_tokens=max_completion_tokens
            )
        except AIUnavailableError:
            self._record(
                base,
                status="error",
                error_class="unavailable",
                latency_ms=_elapsed_ms(started),
            )
            raise
        except AIExecutionError as exc:
            self._record(
                base,
                status="error",
                error_class=exc.error_class,
                retry_count=exc.retry_count,
                latency_ms=_elapsed_ms(started),
            )
            raise
        usage = result.usage
        self._record(
            base,
            status="success",
            retry_count=result.retry_count,
            latency_ms=_elapsed_ms(started),
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            finish_reason=result.finish_reason,
            resolved_model=result.resolved_model,
        )
        return result

    def complete_vision(
        self,
        prompt: str,
        image_png_base64: str,
        *,
        model: str | None = None,
        max_completion_tokens: int | None = None,
        json_output: bool = False,
        source_feature: str | None = None,
        target_refs: Sequence[str] | None = None,
    ) -> CompletionResult:
        """Return one vision completion with a full audit record (v0.7.2).

        Mirrors :meth:`complete` for image-bearing calls: same ledger fields,
        capability ``vision``, prompt hash over text plus image payload.
        """

        chosen_model = model or self._default_model
        # A vision call is a multimodal completion; the ledger schema pins
        # capability to completion/embedding/rerank, so it is recorded as a
        # completion and distinguished by source_feature.
        base: dict[str, object] = {
            "call_uuid": str(uuid.uuid4()),
            "capability": "completion",
            "requested_model": chosen_model,
            "prompt_sha256": _sha256_text(prompt + "\n" + image_png_base64),
            "input_chars": len(prompt) + len(image_png_base64),
            "source_feature": source_feature or self._source_feature,
            "target_refs": (
                tuple(target_refs) if target_refs is not None else self._target_refs
            ),
            "created_at": _utc_timestamp(),
        }
        self._ensure_allowed("vision", base)
        started = time.monotonic()
        try:
            vision_options: dict[str, object] = {
                "model": model, "max_completion_tokens": max_completion_tokens,
            }
            if json_output:
                vision_options["json_output"] = True
            result = self._wrapped.complete_vision(  # type: ignore[attr-defined]
                prompt,
                image_png_base64,
                **vision_options,
            )
        except AIUnavailableError:
            self._record(
                base,
                status="error",
                error_class="unavailable",
                latency_ms=_elapsed_ms(started),
            )
            raise
        except AIExecutionError as exc:
            self._record(
                base,
                status="error",
                error_class=exc.error_class,
                retry_count=exc.retry_count,
                latency_ms=_elapsed_ms(started),
            )
            raise
        usage = result.usage
        self._record(
            base,
            status="success",
            retry_count=result.retry_count,
            latency_ms=_elapsed_ms(started),
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            finish_reason=result.finish_reason,
            resolved_model=result.resolved_model,
        )
        return result

    def embed(
        self,
        texts: Sequence[str],
        *,
        model: str | None = None,
        dimensions: int | None = None,
        source_feature: str | None = None,
        target_refs: Sequence[str] | None = None,
    ) -> EmbeddingResult:
        """Return the wrapped embeddings with a full audit record.

        ``source_feature`` / ``target_refs`` are optional per-call overrides
        for the audit ledger, mirroring :meth:`complete`.
        """

        chosen_model = model or self._default_embedding_model
        base: dict[str, object] = {
            "call_uuid": str(uuid.uuid4()),
            "capability": "embedding",
            "requested_model": chosen_model,
            "prompt_sha256": _sha256_texts(texts),
            "input_chars": sum(len(text) for text in texts),
            "source_feature": source_feature or self._source_feature,
            "target_refs": (
                tuple(target_refs) if target_refs is not None else self._target_refs
            ),
            "created_at": _utc_timestamp(),
        }
        self._ensure_allowed("embedding", base)
        started = time.monotonic()
        try:
            result = self._wrapped.embed(  # type: ignore[attr-defined]
                texts, model=model, dimensions=dimensions
            )
        except AIUnavailableError:
            self._record(
                base,
                status="error",
                error_class="unavailable",
                latency_ms=_elapsed_ms(started),
            )
            raise
        except AIExecutionError as exc:
            self._record(
                base,
                status="error",
                error_class=exc.error_class,
                retry_count=exc.retry_count,
                latency_ms=_elapsed_ms(started),
            )
            raise
        usage = result.usage
        self._record(
            base,
            status="success",
            retry_count=result.retry_count,
            latency_ms=_elapsed_ms(started),
            prompt_tokens=usage.prompt_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            resolved_model=result.resolved_model,
        )
        return result

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        model: str | None = None,
        top_n: int | None = None,
    ) -> RerankResult:
        """Delegate rerank directly; the Qwen adapter defers it for now."""
        rerank = getattr(self._wrapped, "rerank", None)
        if rerank is None:
            raise AIUnavailableError("AI 能力不可用：rerank 通道未配置。")
        return rerank(query, documents, model=model, top_n=top_n)

    def _ensure_allowed(
        self, capability: str, base: dict[str, object]
    ) -> None:
        try:
            self._budget_guard.ensure_allowed(capability)
        except AIUnavailableError:
            self._record(
                base,
                status="rejected",
                error_class="budget",
                latency_ms=0,
            )
            raise

    def _record(
        self,
        base: dict[str, object],
        *,
        status: str,
        error_class: str | None = None,
        retry_count: int = 0,
        latency_ms: int | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        finish_reason: str | None = None,
        resolved_model: str | None = None,
    ) -> None:
        record = AiCallRecord(
            call_uuid=str(base["call_uuid"]),
            capability=str(base["capability"]),
            model=str(base["requested_model"]),
            prompt_sha256=str(base["prompt_sha256"]),
            input_chars=int(base["input_chars"]),
            status=status,
            source_feature=str(base["source_feature"]),
            target_refs=tuple(base["target_refs"]),  # type: ignore[arg-type]
            error_class=error_class,
            retry_count=retry_count,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
            created_at=str(base["created_at"]),
            provider=self.provider_id or "qwen",
            resolved_model=resolved_model,
        )
        try:
            self._ledger.record(record)
        except Exception as exc:
            LOGGER.error(
                "AI 调用台账写入失败（不影响本次调用结果）：error_type=%s",
                type(exc).__name__,
            )


def build_production_audited_provider(
    provider: object,
    *,
    default_model: str,
    default_embedding_model: str,
    source_feature: str,
    ledger: AiCallLedger,
    budget_guard: AiBudgetGuard,
    target_refs: Sequence[str] = (),
) -> AuditedAIProvider:
    """Build the only wrapper shape approved for production composition roots."""

    if ledger is None or budget_guard is None:
        raise AIProductionCompositionError(
            "AI 生产组合无效：必须配置审计台账与预算门禁。"
        )
    audited = AuditedAIProvider(
        provider,
        default_model=default_model,
        default_embedding_model=default_embedding_model,
        source_feature=source_feature,
        target_refs=target_refs,
        ledger=ledger,
        budget_guard=budget_guard,
        _production_marker=_PRODUCTION_COMPOSITION_MARKER,
    )
    return require_production_audited_provider(audited)


def require_production_audited_provider(provider: object) -> AuditedAIProvider:
    """Fail closed unless ``provider`` is the approved production wrapper."""

    if (
        not isinstance(provider, AuditedAIProvider)
        or provider._production_marker is not _PRODUCTION_COMPOSITION_MARKER
        or isinstance(provider._ledger, _NullAiCallLedger)
        or isinstance(provider._budget_guard, _NullAiBudgetGuard)
    ):
        raise AIProductionCompositionError(
            "AI 生产组合无效：必须使用经批准的审计与预算 provider。"
        )
    return provider


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
