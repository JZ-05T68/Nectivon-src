"""Resolve and build all provider adapters outside business services.

Qwen retains legacy ``EKB_AI_*`` fallback. DeepSeek, Kimi, Hunyuan and GLM
share a secure-config completion boundary. Runtime/UI product switching resolves here
so business services remain unaware of concrete providers.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import ClassVar, Protocol

from src.ai.credential_store import (
    CredentialStore,
    SecretCredential,
    build_default_credential_store,
)
from src.ai.deepseek_client import DeepSeekAdapter
from src.ai.glm_client import GlmAdapter
from src.ai.hunyuan_client import HunyuanAdapter
from src.ai.kimi_client import KimiAdapter
from src.ai.model_registry import ProviderId
from src.ai.openai_compatible import Transport
from src.ai.provider import (
    AiBudgetGuard,
    AiCallLedger,
    AIUnavailableError,
    AuditedAIProvider,
    build_production_audited_provider,
    require_production_audited_provider,
)
from src.ai.provider_config import (
    ProviderConfigStore,
    ProviderSettings,
    image_provider_settings,
)
from src.ai.qwen_client import QwenProvider
from src.config import Settings

__all__ = [
    "QwenConfigurationSource",
    "ResolvedCompletionRuntime",
    "ResolvedDeepSeekRuntime",
    "ResolvedGlmRuntime",
    "ResolvedHunyuanRuntime",
    "ResolvedKimiRuntime",
    "ResolvedQwenRuntime",
    "build_audited_deepseek_provider",
    "build_audited_glm_provider",
    "build_audited_hunyuan_provider",
    "build_audited_kimi_provider",
    "build_completion_adapter",
    "build_deepseek_adapter",
    "build_glm_adapter",
    "build_hunyuan_adapter",
    "build_kimi_adapter",
    "build_provider_adapter",
    "build_qwen_adapter",
    "resolve_completion_runtime",
    "resolve_active_provider_runtime",
    "resolve_image_provider_runtime",
    "resolve_deepseek_runtime",
    "resolve_glm_runtime",
    "resolve_hunyuan_runtime",
    "resolve_kimi_runtime",
    "resolve_provider_runtime",
    "resolved_runtime_from_settings",
    "resolve_qwen_runtime",
]


class QwenConfigurationSource(StrEnum):
    SECURE_PROVIDER_CONFIG = "secure_provider_config"
    LEGACY_ENV = "legacy_env"


@dataclass(frozen=True, slots=True)
class ResolvedQwenRuntime:
    """Fully resolved Qwen adapter inputs with a redacted credential wrapper."""

    source: QwenConfigurationSource
    credential: SecretCredential
    base_url: str
    llm_model: str
    llm_model_hard: str
    embedding_model: str
    rerank_model: str
    vision_model: str
    timeout_seconds: float
    max_extra_attempts: int

    @property
    def provider_id(self) -> ProviderId:
        return ProviderId.QWEN

    @property
    def default_model(self) -> str:
        return self.llm_model

    @property
    def default_embedding_model(self) -> str:
        return self.embedding_model


@dataclass(frozen=True, slots=True)
class ResolvedCompletionRuntime:
    """Shared secure-config inputs for one completion-only adapter."""

    credential: SecretCredential
    base_url: str
    model: str
    timeout_seconds: float
    max_extra_attempts: int

    provider_id: ClassVar[ProviderId]

    @property
    def default_model(self) -> str:
        return self.model

    @property
    def default_embedding_model(self) -> str:
        return self.model


class ResolvedDeepSeekRuntime(ResolvedCompletionRuntime):
    provider_id = ProviderId.DEEPSEEK


class ResolvedKimiRuntime(ResolvedCompletionRuntime):
    provider_id = ProviderId.KIMI


class ResolvedHunyuanRuntime(ResolvedCompletionRuntime):
    provider_id = ProviderId.HUNYUAN


class ResolvedGlmRuntime(ResolvedCompletionRuntime):
    provider_id = ProviderId.GLM


class QwenProviderConstructor(Protocol):
    def __call__(self, **kwargs: object) -> QwenProvider: ...


class DeepSeekAdapterConstructor(Protocol):
    def __call__(self, **kwargs: object) -> DeepSeekAdapter: ...


class KimiAdapterConstructor(Protocol):
    def __call__(self, **kwargs: object) -> KimiAdapter: ...


class HunyuanAdapterConstructor(Protocol):
    def __call__(self, **kwargs: object) -> HunyuanAdapter: ...


class GlmAdapterConstructor(Protocol):
    def __call__(self, **kwargs: object) -> GlmAdapter: ...


def resolve_deepseek_runtime(
    settings: Settings,
    *,
    config_store: ProviderConfigStore | None = None,
    credential_store: CredentialStore | None = None,
) -> ResolvedDeepSeekRuntime | None:
    """Resolve DeepSeek only from versioned config and credential abstraction."""

    resolved = resolve_completion_runtime(
        ProviderId.DEEPSEEK,
        settings,
        config_store=config_store,
        credential_store=credential_store,
    )
    return resolved if isinstance(resolved, ResolvedDeepSeekRuntime) else None


def resolve_kimi_runtime(
    settings: Settings,
    *,
    config_store: ProviderConfigStore | None = None,
    credential_store: CredentialStore | None = None,
) -> ResolvedKimiRuntime | None:
    """Resolve Kimi only from versioned config and credential abstraction."""

    resolved = resolve_completion_runtime(
        ProviderId.KIMI,
        settings,
        config_store=config_store,
        credential_store=credential_store,
    )
    return resolved if isinstance(resolved, ResolvedKimiRuntime) else None


def resolve_hunyuan_runtime(
    settings: Settings,
    *,
    config_store: ProviderConfigStore | None = None,
    credential_store: CredentialStore | None = None,
) -> ResolvedHunyuanRuntime | None:
    """Resolve Hunyuan only from versioned config and credential abstraction."""

    resolved = resolve_completion_runtime(
        ProviderId.HUNYUAN,
        settings,
        config_store=config_store,
        credential_store=credential_store,
    )
    return resolved if isinstance(resolved, ResolvedHunyuanRuntime) else None


def resolve_glm_runtime(
    settings: Settings,
    *,
    config_store: ProviderConfigStore | None = None,
    credential_store: CredentialStore | None = None,
) -> ResolvedGlmRuntime | None:
    """Resolve GLM only from versioned config and credential abstraction."""

    resolved = resolve_completion_runtime(
        ProviderId.GLM,
        settings,
        config_store=config_store,
        credential_store=credential_store,
    )
    return resolved if isinstance(resolved, ResolvedGlmRuntime) else None


_RUNTIME_TYPES: dict[ProviderId, type[ResolvedCompletionRuntime]] = {
    ProviderId.DEEPSEEK: ResolvedDeepSeekRuntime,
    ProviderId.KIMI: ResolvedKimiRuntime,
    ProviderId.HUNYUAN: ResolvedHunyuanRuntime,
    ProviderId.GLM: ResolvedGlmRuntime,
}


def resolve_completion_runtime(
    provider_id: ProviderId | str,
    settings: Settings,
    *,
    config_store: ProviderConfigStore | None = None,
    credential_store: CredentialStore | None = None,
) -> ResolvedCompletionRuntime | None:
    """Resolve one secure completion provider through the common boundary."""

    normalized_provider = ProviderId(provider_id)
    runtime_type = _RUNTIME_TYPES.get(normalized_provider)
    if runtime_type is None:
        # Qwen is intentionally resolved by its legacy-compatible path.
        return None

    if config_store is None:
        try:
            config_store = ProviderConfigStore()
        except RuntimeError:
            return None
    if not config_store.path.is_file():
        return None
    try:
        config = config_store.load()
    except ValueError as exc:
        raise AIUnavailableError("安全 AI Provider 配置无效。") from exc
    if config.active_provider_id is not normalized_provider:
        return None
    provider_settings = config.get(normalized_provider)
    if provider_settings is None:
        return None
    store = credential_store or build_default_credential_store()
    credential = store.get(normalized_provider)
    if credential is None:
        return None
    return runtime_type(
        credential=credential,
        base_url=provider_settings.base_url,
        model=provider_settings.model_id,
        timeout_seconds=settings.ai_timeout_seconds,
        max_extra_attempts=settings.ai_max_extra_attempts,
    )


def resolve_qwen_runtime(
    settings: Settings,
    *,
    config_store: ProviderConfigStore | None = None,
    credential_store: CredentialStore | None = None,
    allow_inactive: bool = False,
) -> ResolvedQwenRuntime | None:
    """Resolve secure config first, otherwise preserve legacy Settings behavior."""

    if config_store is None:
        try:
            config_store = ProviderConfigStore()
        except RuntimeError:
            config_store = None

    if config_store is not None and config_store.path.is_file():
        try:
            config = config_store.load()
        except ValueError as exc:
            raise AIUnavailableError("安全 AI Provider 配置无效。") from exc
        if config.active_provider_id is None or (
            config.active_provider_id is not ProviderId.QWEN and not allow_inactive
        ):
            return None
        qwen_settings = config.get(ProviderId.QWEN)
        if qwen_settings is None:
            return None
        store = credential_store or build_default_credential_store()
        credential = store.get(ProviderId.QWEN)
        if credential is None:
            return None
        return _resolved(
            settings,
            source=QwenConfigurationSource.SECURE_PROVIDER_CONFIG,
            credential=credential,
            base_url=qwen_settings.base_url,
            llm_model=qwen_settings.model_id,
        )

    if settings.ai_mode != "api" or (
        settings.ai_provider != ProviderId.QWEN.value and not allow_inactive
    ):
        return None
    legacy_key = settings.ai_api_key.get_secret_value()
    if not legacy_key:
        return None
    return _resolved(
        settings,
        source=QwenConfigurationSource.LEGACY_ENV,
        credential=SecretCredential(legacy_key),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        llm_model=settings.ai_llm_model,
    )


def resolve_provider_runtime(
    provider_id: ProviderId | str,
    settings: Settings,
    *,
    config_store: ProviderConfigStore | None = None,
    credential_store: CredentialStore | None = None,
) -> ResolvedQwenRuntime | ResolvedCompletionRuntime | None:
    """Expose one centralized four-provider resolution boundary."""

    normalized_provider = ProviderId(provider_id)
    if normalized_provider is ProviderId.QWEN:
        return resolve_qwen_runtime(
            settings,
            config_store=config_store,
            credential_store=credential_store,
        )
    return resolve_completion_runtime(
        normalized_provider,
        settings,
        config_store=config_store,
        credential_store=credential_store,
    )


def resolve_active_provider_runtime(
    settings: Settings,
    *,
    config_store: ProviderConfigStore | None = None,
    credential_store: CredentialStore | None = None,
) -> ResolvedQwenRuntime | ResolvedCompletionRuntime | None:
    """Resolve secure active selection, then legacy Qwen only if no file exists."""

    if config_store is None:
        try:
            config_store = ProviderConfigStore()
        except RuntimeError:
            config_store = None
    if config_store is not None and config_store.path.is_file():
        try:
            config = config_store.load()
        except ValueError as exc:
            raise AIUnavailableError("安全 AI Provider 配置无效。") from exc
        if config.active_provider_id is None:
            return None
        return resolve_provider_runtime(
            config.active_provider_id,
            settings,
            config_store=config_store,
            credential_store=credential_store,
        )
    return resolve_qwen_runtime(
        settings,
        config_store=config_store,
        credential_store=credential_store,
    )


def resolved_runtime_from_settings(
    provider_settings: ProviderSettings,
    credential: SecretCredential,
    settings: Settings,
) -> ResolvedQwenRuntime | ResolvedCompletionRuntime:
    """Build adapter inputs for an explicit UI connection test without I/O."""

    if provider_settings.provider_id is ProviderId.QWEN:
        return _resolved(
            settings,
            source=QwenConfigurationSource.SECURE_PROVIDER_CONFIG,
            credential=credential,
            base_url=provider_settings.base_url,
            llm_model=provider_settings.model_id,
        )
    runtime_type = _RUNTIME_TYPES[provider_settings.provider_id]
    return runtime_type(
        credential=credential,
        base_url=provider_settings.base_url,
        model=provider_settings.model_id,
        timeout_seconds=settings.ai_timeout_seconds,
        max_extra_attempts=settings.ai_max_extra_attempts,
    )


def resolve_image_provider_runtime(
    settings: Settings,
    *,
    config_store: ProviderConfigStore | None = None,
    credential_store: CredentialStore | None = None,
) -> ResolvedQwenRuntime | ResolvedCompletionRuntime | None:
    """Resolve only the selected image model; missing keys never switch vendors."""

    config_store = config_store or ProviderConfigStore()
    if config_store.path.is_file():
        try:
            selected = image_provider_settings(config_store.load())
        except (ValueError, KeyError) as exc:
            raise AIUnavailableError("安全读图模型配置无效。") from exc
        if selected is None:
            return None
        credentials = credential_store or build_default_credential_store()
        credential = credentials.get(selected.provider_id)
        if credential is None:
            return None
        return resolved_runtime_from_settings(selected, credential, settings)
    legacy = resolve_qwen_runtime(
        settings,
        config_store=config_store,
        credential_store=credential_store,
    )
    if legacy is None:
        return None
    from src.ai.model_registry import CapabilitySupport, get_capability_profile

    model = next(
        (
            model
            for model in (legacy.default_model, legacy.vision_model)
            if get_capability_profile("qwen", model).effective.vision is CapabilitySupport.SUPPORTED
        ),
        None,
    )
    return replace(legacy, llm_model=model) if model else None


def build_qwen_adapter(
    resolved: ResolvedQwenRuntime,
    *,
    transport: Transport,
    constructor: QwenProviderConstructor = QwenProvider,
) -> QwenProvider:
    """Build Qwen while keeping the credential wrapped until adapter ingress."""

    return constructor(
        api_key=resolved.credential,
        llm_model=resolved.llm_model,
        llm_model_hard=resolved.llm_model_hard,
        embedding_model=resolved.embedding_model,
        rerank_model=resolved.rerank_model,
        vision_model=resolved.vision_model,
        base_url=resolved.base_url,
        timeout_seconds=resolved.timeout_seconds,
        max_extra_attempts=resolved.max_extra_attempts,
        enable_thinking=None,
        transport=transport,
    )


def build_deepseek_adapter(
    resolved: ResolvedDeepSeekRuntime,
    *,
    transport: Transport,
    constructor: DeepSeekAdapterConstructor = DeepSeekAdapter,
) -> DeepSeekAdapter:
    """Build the DeepSeek text/image adapter with a wrapped credential."""

    return constructor(
        api_key=resolved.credential,
        model=resolved.model,
        base_url=resolved.base_url,
        timeout_seconds=resolved.timeout_seconds,
        max_extra_attempts=resolved.max_extra_attempts,
        transport=transport,
    )


def build_kimi_adapter(
    resolved: ResolvedKimiRuntime,
    *,
    transport: Transport,
    constructor: KimiAdapterConstructor = KimiAdapter,
) -> KimiAdapter:
    """Build the Kimi text/image adapter with a wrapped credential."""

    return constructor(
        api_key=resolved.credential,
        model=resolved.model,
        base_url=resolved.base_url,
        timeout_seconds=resolved.timeout_seconds,
        max_extra_attempts=resolved.max_extra_attempts,
        transport=transport,
    )


def build_hunyuan_adapter(
    resolved: ResolvedHunyuanRuntime,
    *,
    transport: Transport,
    constructor: HunyuanAdapterConstructor = HunyuanAdapter,
) -> HunyuanAdapter:
    """Build the Hunyuan-only TokenHub adapter with a wrapped credential."""

    return constructor(
        api_key=resolved.credential,
        model=resolved.model,
        base_url=resolved.base_url,
        timeout_seconds=resolved.timeout_seconds,
        max_extra_attempts=resolved.max_extra_attempts,
        transport=transport,
    )


def build_glm_adapter(
    resolved: ResolvedGlmRuntime,
    *,
    transport: Transport,
    constructor: GlmAdapterConstructor = GlmAdapter,
) -> GlmAdapter:
    """Build the GLM text/image adapter with a wrapped credential."""

    return constructor(
        api_key=resolved.credential,
        model=resolved.model,
        base_url=resolved.base_url,
        timeout_seconds=resolved.timeout_seconds,
        max_extra_attempts=resolved.max_extra_attempts,
        transport=transport,
    )


def build_completion_adapter(
    resolved: ResolvedCompletionRuntime,
    *,
    transport: Transport,
) -> DeepSeekAdapter | KimiAdapter | HunyuanAdapter | GlmAdapter:
    """Central dispatch for secure provider adapters and their capabilities."""

    constructors = {
        ProviderId.DEEPSEEK: DeepSeekAdapter,
        ProviderId.KIMI: KimiAdapter,
        ProviderId.HUNYUAN: HunyuanAdapter,
        ProviderId.GLM: GlmAdapter,
    }
    constructor = constructors[resolved.provider_id]
    return constructor(
        api_key=resolved.credential,
        model=resolved.model,
        base_url=resolved.base_url,
        timeout_seconds=resolved.timeout_seconds,
        max_extra_attempts=resolved.max_extra_attempts,
        transport=transport,
    )


def build_provider_adapter(
    resolved: ResolvedQwenRuntime | ResolvedCompletionRuntime,
    *,
    transport: Transport,
) -> QwenProvider | DeepSeekAdapter | KimiAdapter | HunyuanAdapter | GlmAdapter:
    """Expose one centralized five-provider adapter construction boundary."""

    if isinstance(resolved, ResolvedQwenRuntime):
        return build_qwen_adapter(resolved, transport=transport)
    return build_completion_adapter(resolved, transport=transport)


def build_audited_deepseek_provider(
    resolved: ResolvedDeepSeekRuntime,
    *,
    transport: Transport,
    ledger: AiCallLedger,
    budget_guard: AiBudgetGuard,
    source_feature: str,
    constructor: DeepSeekAdapterConstructor = DeepSeekAdapter,
) -> AuditedAIProvider:
    """Compose DeepSeek under the mandatory audit and token-budget boundary."""

    adapter = build_deepseek_adapter(
        resolved,
        transport=transport,
        constructor=constructor,
    )
    audited = build_production_audited_provider(
        adapter,
        default_model=resolved.model,
        # DeepSeek is completion-only here. The adapter explicitly rejects
        # embedding with ``unsupported_capability`` if accidentally called.
        default_embedding_model=resolved.model,
        source_feature=source_feature,
        ledger=ledger,
        budget_guard=budget_guard,
    )
    return require_production_audited_provider(audited)


def build_audited_kimi_provider(
    resolved: ResolvedKimiRuntime,
    *,
    transport: Transport,
    ledger: AiCallLedger,
    budget_guard: AiBudgetGuard,
    source_feature: str,
    constructor: KimiAdapterConstructor = KimiAdapter,
) -> AuditedAIProvider:
    """Compose Kimi under the mandatory audit and token-budget boundary."""

    adapter = build_kimi_adapter(
        resolved,
        transport=transport,
        constructor=constructor,
    )
    return _audited_completion_provider(
        adapter,
        resolved=resolved,
        ledger=ledger,
        budget_guard=budget_guard,
        source_feature=source_feature,
    )


def build_audited_hunyuan_provider(
    resolved: ResolvedHunyuanRuntime,
    *,
    transport: Transport,
    ledger: AiCallLedger,
    budget_guard: AiBudgetGuard,
    source_feature: str,
    constructor: HunyuanAdapterConstructor = HunyuanAdapter,
) -> AuditedAIProvider:
    """Compose Hunyuan under the mandatory audit and token-budget boundary."""

    adapter = build_hunyuan_adapter(
        resolved,
        transport=transport,
        constructor=constructor,
    )
    return _audited_completion_provider(
        adapter,
        resolved=resolved,
        ledger=ledger,
        budget_guard=budget_guard,
        source_feature=source_feature,
    )


def build_audited_glm_provider(
    resolved: ResolvedGlmRuntime,
    *,
    transport: Transport,
    ledger: AiCallLedger,
    budget_guard: AiBudgetGuard,
    source_feature: str,
    constructor: GlmAdapterConstructor = GlmAdapter,
) -> AuditedAIProvider:
    """Compose GLM under the mandatory audit and token-budget boundary."""

    adapter = build_glm_adapter(
        resolved,
        transport=transport,
        constructor=constructor,
    )
    return _audited_completion_provider(
        adapter,
        resolved=resolved,
        ledger=ledger,
        budget_guard=budget_guard,
        source_feature=source_feature,
    )


def _audited_completion_provider(
    adapter: object,
    *,
    resolved: ResolvedCompletionRuntime,
    ledger: AiCallLedger,
    budget_guard: AiBudgetGuard,
    source_feature: str,
) -> AuditedAIProvider:
    audited = build_production_audited_provider(
        adapter,
        default_model=resolved.model,
        default_embedding_model=resolved.model,
        source_feature=source_feature,
        ledger=ledger,
        budget_guard=budget_guard,
    )
    return require_production_audited_provider(audited)


def _resolved(
    settings: Settings,
    *,
    source: QwenConfigurationSource,
    credential: SecretCredential,
    base_url: str,
    llm_model: str,
) -> ResolvedQwenRuntime:
    return ResolvedQwenRuntime(
        source=source,
        credential=credential,
        base_url=base_url,
        llm_model=llm_model,
        llm_model_hard=settings.ai_llm_model_hard,
        embedding_model=settings.ai_embedding_model,
        rerank_model=settings.ai_rerank_model,
        vision_model=settings.ai_vision_model,
        timeout_seconds=settings.ai_timeout_seconds,
        max_extra_attempts=settings.ai_max_extra_attempts,
    )
