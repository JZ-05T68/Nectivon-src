"""Static model metadata for Nectivon's supported AI providers.

The registry is deliberately data-only: it performs no I/O, owns no API
credentials, and is not imported by Agent/RAG/runtime composition.  A custom
model id is supported for every provider, but custom ids do not acquire
capabilities by name inference; callers must treat those capabilities as
unknown until a provider response proves otherwise.

Official documentation was last checked on 2026-10-05.  Keep this list small
and re-check the linked vendor pages before changing a capability:

* DeepSeek: https://api-docs.deepseek.com/quick_start/pricing/
* Qwen: https://help.aliyun.com/zh/model-studio/what-is-model-studio
* Kimi models/parameters: https://platform.kimi.com/docs/models.md and
  https://platform.kimi.com/docs/api/models-overview.md
* Hunyuan models/parameters: https://cloud.tencent.com/document/product/1823/130051
  and https://cloud.tencent.com/document/product/1823/131208
* TokenHub endpoint: https://cloud.tencent.com/document/product/1823/130078
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "CapabilitySupport",
    "CapabilityProfile",
    "ADAPTER_CAPABILITIES",
    "MODEL_REGISTRY",
    "ModelCapabilities",
    "ModelPurpose",
    "ModelPreset",
    "ProviderDefinition",
    "ProviderId",
    "get_model_preset",
    "get_capability_profile",
    "get_provider_definition",
    "is_hunyuan_model_id",
    "list_model_presets",
]


class ProviderId(StrEnum):
    """The fixed product boundary; v0.8.6 adds GLM as the fifth provider."""

    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    KIMI = "kimi"
    HUNYUAN = "hunyuan"
    GLM = "glm"


class CapabilitySupport(StrEnum):
    """Three-state capability evidence; unknown is never treated as supported."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class ModelPurpose(StrEnum):
    """Independent user selections for image reading and text assistance."""

    TEXT = "text"
    IMAGE = "image"


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """Capabilities verified from official vendor documentation."""

    streaming: CapabilitySupport = CapabilitySupport.UNKNOWN
    reasoning: CapabilitySupport = CapabilitySupport.UNKNOWN
    tool_calling: CapabilitySupport = CapabilitySupport.UNKNOWN
    vision: CapabilitySupport = CapabilitySupport.UNKNOWN


@dataclass(frozen=True, slots=True)
class CapabilityProfile:
    """Native, implemented and effective capability views for one model."""

    native: ModelCapabilities
    adapter: ModelCapabilities
    effective: ModelCapabilities


@dataclass(frozen=True, slots=True)
class ModelPreset:
    """One curated model id; custom model ids remain outside this tuple."""

    model_id: str
    display_name: str
    capabilities: ModelCapabilities
    context_window_tokens: int | None = None
    recommended: bool = False
    image_selectable: bool = True


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    """Non-secret provider metadata and its deliberately small preset list."""

    provider_id: ProviderId
    display_name: str
    default_base_url: str
    presets: tuple[ModelPreset, ...]
    allows_custom_model_id: bool = True
    legacy_base_urls: tuple[str, ...] = ()

    @property
    def default_model_id(self) -> str | None:
        """Return the recommended preset, or ``None`` when only custom IDs are safe."""

        return next(
            (preset.model_id for preset in self.presets if preset.recommended),
            None,
        )


_YES = CapabilitySupport.SUPPORTED
_NO = CapabilitySupport.UNSUPPORTED
_UNKNOWN = CapabilitySupport.UNKNOWN
_HUNYUAN_MODEL_ID = re.compile(r"^(?:hy|hunyuan)[a-z0-9._:-]*$", re.IGNORECASE)


MODEL_REGISTRY: dict[ProviderId, ProviderDefinition] = {
    ProviderId.DEEPSEEK: ProviderDefinition(
        provider_id=ProviderId.DEEPSEEK,
        display_name="DeepSeek",
        default_base_url="https://api.deepseek.com",
        # Official V4.1 Flash accepts images; V4 Pro remains text-only.
        presets=(
            ModelPreset(
                model_id="deepseek-flash",
                display_name="DeepSeek V4.1 Flash",
                capabilities=ModelCapabilities(vision=_YES),
                recommended=True,
            ),
            ModelPreset(
                model_id="deepseek-v4-pro",
                display_name="DeepSeek V4 Pro",
                capabilities=ModelCapabilities(vision=_NO),
            ),
        ),
    ),
    ProviderId.QWEN: ProviderDefinition(
        provider_id=ProviderId.QWEN,
        display_name="Qwen",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        presets=(
            ModelPreset(
                model_id="qwen3.8-max",
                display_name="Qwen3.8 Max",
                capabilities=ModelCapabilities(
                    streaming=_YES,
                    reasoning=_YES,
                    tool_calling=_UNKNOWN,
                    vision=_YES,
                ),
                recommended=True,
            ),
            ModelPreset(
                # Official Qwen3.8 multimodal input support, checked 2026-10-05.
                model_id="qwen3.8-flash",
                display_name="Qwen3.8 Flash",
                capabilities=ModelCapabilities(streaming=_YES, reasoning=_YES, vision=_YES),
            ),
            ModelPreset(
                # Official image/structured-output support and original-page
                # region recognition verified 2026-10-05.
                model_id="qwen3-vl-plus",
                display_name="Qwen3 VL Plus",
                capabilities=ModelCapabilities(reasoning=_YES, vision=_YES),
                # Page 3 over-split one question's blanks in four-page acceptance.
                # Retain the known model and compatible historical settings.
                image_selectable=False,
            ),
        ),
    ),
    ProviderId.KIMI: ProviderDefinition(
        provider_id=ProviderId.KIMI,
        display_name="Kimi",
        default_base_url="https://api.moonshot.cn/v1",
        presets=(
            ModelPreset(
                model_id="kimi-k3",
                display_name="Kimi K3",
                capabilities=ModelCapabilities(
                    streaming=_YES,
                    reasoning=_YES,
                    tool_calling=_YES,
                    vision=_YES,
                ),
                context_window_tokens=1_000_000,
                recommended=True,
            ),
            ModelPreset(
                model_id="kimi-k2.6",
                display_name="Kimi K2.6",
                capabilities=ModelCapabilities(
                    streaming=_YES,
                    reasoning=_YES,
                    tool_calling=_YES,
                    vision=_YES,
                ),
                context_window_tokens=256_000,
                # Current original-page crop coordinates drift substantially.
                # Preserve native vision metadata and text configuration.
                image_selectable=False,
            ),
        ),
    ),
    ProviderId.HUNYUAN: ProviderDefinition(
        provider_id=ProviderId.HUNYUAN,
        display_name="腾讯混元",
        default_base_url="https://tokenhub.tencentmaas.com/v1",
        presets=(
            ModelPreset(
                model_id="hy4-preview",
                display_name="Hunyuan HY 4 Preview",
                capabilities=ModelCapabilities(
                    streaming=_YES,
                    reasoning=_YES,
                    tool_calling=_YES,
                    vision=_NO,
                ),
                context_window_tokens=1_000_000,
                recommended=True,
            ),
            ModelPreset(
                model_id="hy3",
                display_name="Hunyuan HY 3",
                capabilities=ModelCapabilities(
                    streaming=_YES,
                    reasoning=_YES,
                    tool_calling=_YES,
                    vision=_NO,
                ),
                context_window_tokens=256_000,
            ),
        ),
        legacy_base_urls=("https://api.hunyuan.cloud.tencent.com/v1",),
    ),
    ProviderId.GLM: ProviderDefinition(
        provider_id=ProviderId.GLM,
        display_name="智谱GLM",
        # Official China-mainland endpoint verified 2026-09-25:
        # https://docs.bigmodel.cn - chat completions live under
        # https://open.bigmodel.cn/api/paas/v4.
        default_base_url="https://open.bigmodel.cn/api/paas/v4",
        # https://docs.z.ai/guides/vlm/glm-5.3-flash: Flash accepts images;
        # GLM-5.3 is text-only. No capability is inferred for custom IDs.
        presets=(
            ModelPreset(
                model_id="glm-5.3",
                display_name="GLM-5.3",
                capabilities=ModelCapabilities(vision=_NO),
                recommended=True,
            ),
            ModelPreset(
                model_id="glm-5.3-flash",
                display_name="GLM-5.3 Flash",
                capabilities=ModelCapabilities(vision=_YES),
            ),
        ),
    ),
}


# Adapter implementation scope is deliberately separate from vendor-native
# model metadata. Non-streaming vision uses image-bearing vendor requests.
# Reasoning here means the adapter safely
# handles the provider's reasoning request/response fields, not that chain of
# thought is exposed to callers.
ADAPTER_CAPABILITIES: dict[ProviderId, ModelCapabilities] = {
    ProviderId.DEEPSEEK: ModelCapabilities(
        streaming=_NO, reasoning=_YES, tool_calling=_NO, vision=_YES
    ),
    ProviderId.QWEN: ModelCapabilities(
        streaming=_NO, reasoning=_YES, tool_calling=_NO, vision=_YES
    ),
    ProviderId.KIMI: ModelCapabilities(
        streaming=_NO, reasoning=_YES, tool_calling=_NO, vision=_YES
    ),
    ProviderId.HUNYUAN: ModelCapabilities(
        streaming=_NO, reasoning=_YES, tool_calling=_NO, vision=_NO
    ),
    ProviderId.GLM: ModelCapabilities(streaming=_NO, reasoning=_YES, tool_calling=_NO, vision=_YES),
}


def get_provider_definition(provider_id: ProviderId | str) -> ProviderDefinition:
    """Return one provider definition or raise a stable ``ValueError``."""

    try:
        normalized = ProviderId(provider_id)
    except ValueError as exc:
        raise ValueError(f"不支持的 Provider ID：{provider_id!r}") from exc
    return MODEL_REGISTRY[normalized]


def list_model_presets(
    provider_id: ProviderId | str,
    *,
    purpose: ModelPurpose = ModelPurpose.TEXT,
) -> tuple[ModelPreset, ...]:
    """Offer only implemented, documented image models for image reading."""

    presets = get_provider_definition(provider_id).presets
    if purpose is ModelPurpose.IMAGE:
        return tuple(
            preset
            for preset in presets
            if preset.image_selectable
            and get_capability_profile(
                provider_id,
                preset.model_id,
            ).effective.vision
            is CapabilitySupport.SUPPORTED
        )
    return presets


def get_model_preset(provider_id: ProviderId | str, model_id: str) -> ModelPreset | None:
    """Return a curated preset, or ``None`` for a valid custom-model path."""

    normalized_model_id = model_id.strip()
    for preset in list_model_presets(provider_id):
        if preset.model_id == normalized_model_id:
            return preset
    return None


def is_hunyuan_model_id(model_id: str) -> bool:
    """Accept only Tencent Hunyuan namespaces, never TokenHub's other vendors."""

    return _HUNYUAN_MODEL_ID.fullmatch(model_id.strip()) is not None


def get_capability_profile(provider_id: ProviderId | str, model_id: str) -> CapabilityProfile:
    """Return native, adapter and conservative effective capabilities."""

    normalized_provider = ProviderId(provider_id)
    preset = get_model_preset(normalized_provider, model_id)
    native = preset.capabilities if preset is not None else ModelCapabilities()
    adapter = ADAPTER_CAPABILITIES[normalized_provider]
    return CapabilityProfile(
        native=native,
        adapter=adapter,
        effective=ModelCapabilities(
            streaming=_intersect(native.streaming, adapter.streaming),
            reasoning=_intersect(native.reasoning, adapter.reasoning),
            tool_calling=_intersect(native.tool_calling, adapter.tool_calling),
            vision=_intersect(native.vision, adapter.vision),
        ),
    )


def _intersect(native: CapabilitySupport, adapter: CapabilitySupport) -> CapabilitySupport:
    if CapabilitySupport.UNSUPPORTED in {native, adapter}:
        return CapabilitySupport.UNSUPPORTED
    if native is CapabilitySupport.SUPPORTED and adapter is CapabilitySupport.SUPPORTED:
        return CapabilitySupport.SUPPORTED
    return CapabilitySupport.UNKNOWN


def _validate_registry() -> None:
    if set(MODEL_REGISTRY) != set(ProviderId):
        raise RuntimeError("Model registry 必须且只能包含五个固定 Provider")
    if set(ADAPTER_CAPABILITIES) != set(ProviderId):
        raise RuntimeError("Adapter capability registry 必须且只能包含五个固定 Provider")
    for provider_id, definition in MODEL_REGISTRY.items():
        if definition.provider_id is not provider_id:
            raise RuntimeError(f"Provider registry key 不匹配：{provider_id.value}")
        model_ids = [preset.model_id for preset in definition.presets]
        if len(model_ids) != len(set(model_ids)):
            raise RuntimeError(f"Model preset 重复：{provider_id.value}")
        recommended_count = sum(preset.recommended for preset in definition.presets)
        expected_count = 1 if definition.presets else 0
        if recommended_count != expected_count:
            raise RuntimeError(
                f"有 preset 的 Provider 必须有且仅有一个默认模型：{provider_id.value}"
            )


_validate_registry()
