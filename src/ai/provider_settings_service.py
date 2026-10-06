"""Application service for the local multi-provider settings UI.

The service coordinates non-secret config and credential storage without ever
returning credential material to the UI.  Network I/O occurs only when the
user explicitly invokes :meth:`test_connection`; construction and status reads
are local-only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum

from src.ai.completion_stage import CompletionStage, completion_stage_scope
from src.ai.credential_store import (
    CredentialStore,
    CredentialStoreError,
    SecretCredential,
)
from src.ai.model_registry import (
    CapabilitySupport,
    ModelPurpose,
    ProviderId,
    get_capability_profile,
    get_provider_definition,
    list_model_presets,
)
from src.ai.openai_compatible import Transport
from src.ai.provider import (
    AIUnavailableError,
    ProviderCallError,
    ProviderErrorCode,
    SafeProviderError,
)
from src.ai.provider_config import (
    AIProviderConfig,
    ModelSelectionKind,
    ProviderConfigStore,
    ProviderSettings,
    image_provider_settings,
)
from src.ai.provider_factory import (
    build_provider_adapter,
    resolved_runtime_from_settings,
)
from src.config import Settings

__all__ = [
    "AI_UNCONFIGURED_MESSAGE",
    "ConnectionTestResult",
    "ProviderConfigurationState",
    "ProviderSettingsService",
    "ProviderSettingsView",
    "safe_settings_error",
]

AI_UNCONFIGURED_MESSAGE = (
    "尚未配置模型服务。\n\n"
    "Nectivon 的本地知识管理仍可使用。\n\n"
    "如需 AI 解读与问答，请先配置你自己的模型 API。"
)


class ProviderConfigurationState(StrEnum):
    CONFIGURED = "configured"
    UNCONFIGURED = "unconfigured"
    LEGACY = "legacy"


@dataclass(frozen=True, slots=True)
class ProviderSettingsView:
    provider_id: ProviderId
    settings: ProviderSettings | None
    credential_saved: bool
    active: bool


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    provider_id: ProviderId
    requested_model: str
    resolved_model: str


class ProviderSettingsService:
    """CRUD, active selection and explicit connection testing for the UI."""

    def __init__(
        self,
        *,
        config_store: ProviderConfigStore,
        credential_store: CredentialStore,
        application_settings: Settings,
        transport: Transport,
        invalidate_runtime: Callable[[], None],
    ) -> None:
        self._config_store = config_store
        self._credential_store = credential_store
        self._application_settings = application_settings
        self._transport = transport
        self._invalidate_runtime = invalidate_runtime

    @property
    def config_path_exists(self) -> bool:
        return self._config_store.path.is_file()

    def load_config(self) -> AIProviderConfig:
        try:
            return self._config_store.load()
        except ValueError as exc:
            raise AIUnavailableError("安全 AI Provider 配置无效。") from exc

    def view(self, provider_id: ProviderId | str) -> ProviderSettingsView:
        normalized = ProviderId(provider_id)
        config = self.load_config()
        settings = config.get(normalized)
        if settings is None:
            definition = get_provider_definition(normalized)
            if definition.default_model_id is not None:
                settings = ProviderSettings.default_for(normalized)
        try:
            credential_saved = self._credential_store.get(normalized) is not None
        except CredentialStoreError as exc:
            raise ProviderCallError(
                SafeProviderError(
                    code=ProviderErrorCode.CREDENTIAL_STORE,
                    provider_id=normalized.value,
                )
            ) from exc
        return ProviderSettingsView(
            provider_id=normalized,
            settings=settings,
            credential_saved=credential_saved,
            active=config.active_provider_id is normalized,
        )

    def current_state(self) -> tuple[ProviderConfigurationState, ProviderId | None, str | None]:
        config = self.load_config()
        if self.config_path_exists:
            active = config.active_provider_id
            if active is None:
                return ProviderConfigurationState.UNCONFIGURED, None, None
            view = self.view(active)
            state = (
                ProviderConfigurationState.CONFIGURED
                if view.credential_saved
                else ProviderConfigurationState.UNCONFIGURED
            )
            if view.settings is None:
                return ProviderConfigurationState.UNCONFIGURED, None, None
            return state, active, view.settings.model_id
        legacy_key = self._application_settings.ai_api_key.get_secret_value()
        if (
            self._application_settings.ai_mode == "api"
            and self._application_settings.ai_provider == ProviderId.QWEN.value
            and bool(legacy_key)
        ):
            return (
                ProviderConfigurationState.LEGACY,
                ProviderId.QWEN,
                self._application_settings.ai_llm_model,
            )
        return ProviderConfigurationState.UNCONFIGURED, None, None

    def image_view(self, provider_id: ProviderId | str) -> ProviderSettingsView:
        """Expose image settings without changing the provider's text selection."""

        normalized = ProviderId(provider_id)
        config = self.load_config()
        selected = image_provider_settings(config)
        view = self.view(normalized)
        presets = list_model_presets(normalized, purpose=ModelPurpose.IMAGE)
        if not presets:
            return replace(view, settings=None, active=False)
        settings = (
            selected
            if selected and selected.provider_id is normalized
            else replace(
                view.settings or ProviderSettings.default_for(normalized),
                model_id=presets[0].model_id,
                model_selection=ModelSelectionKind.PRESET,
            )
        )
        return replace(
            view,
            settings=settings,
            active=bool(
                selected and selected.provider_id is normalized,
            ),
        )

    def current_image_state(self) -> tuple[ProviderId | None, str | None]:
        """Report the same non-secret image selection as production routing."""

        if self.config_path_exists:
            selected = image_provider_settings(self.load_config())
            return (selected.provider_id, selected.model_id) if selected else (None, None)
        if self.current_state()[0] is ProviderConfigurationState.LEGACY:
            model = self._application_settings.ai_llm_model
            if (
                get_capability_profile("qwen", model).effective.vision
                is not CapabilitySupport.SUPPORTED
            ):
                model = self._application_settings.ai_vision_model
            return ProviderId.QWEN, model
        return None, None

    @staticmethod
    def make_settings(
        *,
        provider_id: ProviderId | str,
        base_url: str,
        model_id: str,
        model_selection: ModelSelectionKind,
    ) -> ProviderSettings:
        try:
            return ProviderSettings(
                provider_id=ProviderId(provider_id),
                base_url=base_url,
                model_id=model_id,
                model_selection=model_selection,
            )
        except ValueError as exc:
            code = (
                ProviderErrorCode.INVALID_BASE_URL
                if "Base URL" in str(exc) or "HTTPS" in str(exc)
                else ProviderErrorCode.INVALID_REQUEST
            )
            raise ProviderCallError(
                SafeProviderError(code=code, provider_id=ProviderId(provider_id).value)
            ) from exc

    def save(
        self,
        provider_settings: ProviderSettings,
        *,
        new_api_key: str = "",
        make_active: bool,
        purpose: ModelPurpose = ModelPurpose.TEXT,
    ) -> None:
        provider_id = provider_settings.provider_id
        normalized_key = new_api_key.strip()
        has_saved_key = self.view(provider_id).credential_saved
        if make_active and not normalized_key and not has_saved_key:
            raise ProviderCallError(
                SafeProviderError(
                    code=ProviderErrorCode.CREDENTIAL_MISSING,
                    provider_id=provider_id.value,
                )
            )
        config = self.load_config()
        if purpose is ModelPurpose.IMAGE:
            try:
                config = config.with_image_provider(provider_settings)
            except ValueError as exc:
                raise ProviderCallError(
                    SafeProviderError(
                        code=ProviderErrorCode.UNSUPPORTED_CAPABILITY,
                        provider_id=provider_id.value,
                    )
                ) from exc
        else:
            # The first text edit of a legacy single-purpose document must
            # freeze its existing image route before replacing a provider.
            # Otherwise changing Qwen's text model also changes image input.
            if config.image_settings is None:
                previous_image = image_provider_settings(config)
                if previous_image is not None:
                    config = config.with_image_provider(previous_image)
            config = config.with_provider(provider_settings, make_active=make_active)
        if normalized_key:
            try:
                self._credential_store.set(provider_id, SecretCredential(normalized_key))
            except CredentialStoreError as exc:
                raise ProviderCallError(
                    SafeProviderError(
                        code=ProviderErrorCode.CREDENTIAL_STORE,
                        provider_id=provider_id.value,
                    )
                ) from exc
        self._config_store.save(config)
        self._invalidate_runtime()

    def delete(self, provider_id: ProviderId | str) -> None:
        normalized = ProviderId(provider_id)
        config = self.load_config().without_provider(normalized)
        self._config_store.save(config)
        try:
            self._credential_store.delete(normalized)
        except CredentialStoreError as exc:
            raise ProviderCallError(
                SafeProviderError(
                    code=ProviderErrorCode.CREDENTIAL_STORE,
                    provider_id=normalized.value,
                )
            ) from exc
        self._invalidate_runtime()

    def test_connection(
        self,
        provider_settings: ProviderSettings,
        *,
        api_key_override: str = "",
        purpose: ModelPurpose = ModelPurpose.TEXT,
    ) -> ConnectionTestResult:
        normalized_override = api_key_override.strip()
        try:
            credential = (
                SecretCredential(normalized_override)
                if normalized_override
                else self._credential_store.get(provider_settings.provider_id)
            )
        except CredentialStoreError as exc:
            raise ProviderCallError(
                SafeProviderError(
                    code=ProviderErrorCode.CREDENTIAL_STORE,
                    provider_id=provider_settings.provider_id.value,
                )
            ) from exc
        if credential is None:
            raise ProviderCallError(
                SafeProviderError(
                    code=ProviderErrorCode.CREDENTIAL_MISSING,
                    provider_id=provider_settings.provider_id.value,
                )
            )
        resolved = resolved_runtime_from_settings(
            provider_settings, credential, self._application_settings
        )
        adapter = build_provider_adapter(resolved, transport=self._transport)
        with completion_stage_scope(CompletionStage.CONNECTION_TEST):
            if purpose is ModelPurpose.IMAGE:
                from src.ai.image_probe import image_connection_probe

                result = adapter.complete_vision(
                    "请描述这张图片中左右两侧的颜色，只回复颜色名称。",
                    image_connection_probe(),
                    model=provider_settings.model_id,
                    max_completion_tokens=256,
                )
            else:
                result = adapter.complete("请只回复 OK。", max_completion_tokens=8)
        return ConnectionTestResult(
            provider_id=provider_settings.provider_id,
            requested_model=provider_settings.model_id,
            resolved_model=result.model,
        )


#: Hunyuan 401 的静态、无凭据诊断提示（2026-09-25 复现结论：请求契约与官方
#: 文档一致、凭据往返完整，401 只能来自 Key 值本身无效——常见原因是复制了
#: 其它厂商的 Key 或旧版混元凭据，TokenHub 只认自家控制台签发的 Key）。
_HUNYUAN_AUTH_HINT = (
    "请确认 Key 来自腾讯云 TokenHub 控制台"
    "（console.cloud.tencent.com/tokenhub/apikey），"
    "重新完整复制后保存；其它厂商或旧版混元接口的 Key 无法通过 TokenHub 鉴权。"
)


def safe_settings_error(exc: BaseException) -> str:
    """Return a user-safe Chinese message without raw exception details."""

    if isinstance(exc, ProviderCallError):
        message = exc.detail.message
        if (
            exc.detail.code is ProviderErrorCode.AUTHENTICATION_FAILED
            and exc.detail.provider_id == ProviderId.HUNYUAN.value
        ):
            message += _HUNYUAN_AUTH_HINT
        return message
    if isinstance(exc, AIUnavailableError):
        return "模型服务当前不可用，请检查配置后重试。"
    return "模型服务配置操作失败，请检查输入后重试。"
