"""Tests for the static four-provider model registry."""

from __future__ import annotations

import dataclasses

import pytest

from src.ai.model_registry import (
    MODEL_REGISTRY,
    CapabilitySupport,
    ProviderId,
    get_capability_profile,
    get_model_preset,
    get_provider_definition,
    is_hunyuan_model_id,
)


def test_registry_has_exactly_the_fixed_five_provider_boundary() -> None:
    """v0.8.6: GLM joins as the user-ratified fifth peer provider."""

    assert set(MODEL_REGISTRY) == set(ProviderId)
    assert {provider.value for provider in MODEL_REGISTRY} == {
        "deepseek",
        "qwen",
        "kimi",
        "hunyuan",
        "glm",
    }


def test_hunyuan_new_user_default_is_tokenhub_and_only_hunyuan_presets() -> None:
    provider = get_provider_definition(ProviderId.HUNYUAN)

    assert provider.default_base_url == "https://tokenhub.tencentmaas.com/v1"
    assert provider.default_model_id == "hy4-preview"
    assert {preset.model_id for preset in provider.presets} == {"hy4-preview", "hy3"}
    assert provider.legacy_base_urls == (
        "https://api.hunyuan.cloud.tencent.com/v1",
    )
    assert all(
        not preset.model_id.startswith(("deepseek", "kimi", "qwen"))
        for preset in provider.presets
    )


def test_capabilities_are_explicit_three_state_values() -> None:
    hy4 = get_model_preset(ProviderId.HUNYUAN, "hy4-preview")

    assert hy4 is not None
    assert hy4.capabilities.streaming is CapabilitySupport.SUPPORTED
    assert hy4.capabilities.vision is CapabilitySupport.UNSUPPORTED
    assert get_model_preset(ProviderId.HUNYUAN, "hy-future") is None


def test_kimi_current_official_model_ids_and_hunyuan_positive_namespace() -> None:
    kimi = get_provider_definition(ProviderId.KIMI)

    assert kimi.default_base_url == "https://api.moonshot.cn/v1"
    assert {preset.model_id for preset in kimi.presets} == {"kimi-k3", "kimi-k2.6"}
    assert is_hunyuan_model_id("hy-future")
    assert is_hunyuan_model_id("hunyuan-future")
    assert not is_hunyuan_model_id("deepseek/hy3")
    assert not is_hunyuan_model_id("kimi-k3")


def test_qwen_offers_max_default_flash_and_verified_vision_preset() -> None:
    qwen = get_provider_definition(ProviderId.QWEN)

    assert qwen.default_model_id == "qwen3.8-max"
    assert {preset.model_id for preset in qwen.presets} == {
        "qwen3.8-max",
        "qwen3.8-flash",
        "qwen3-vl-plus",
    }
    assert get_model_preset(ProviderId.QWEN, "qwen3.7-plus") is None
    assert get_model_preset(ProviderId.QWEN, "qwen3.8-max-0902") is None
    assert get_model_preset(ProviderId.QWEN, "qwen3.8-max-2026-09-02") is None
    assert get_capability_profile("qwen", "qwen3-vl-plus").effective.vision is (
        CapabilitySupport.SUPPORTED
    )


def test_effective_capability_is_native_intersect_adapter_implementation() -> None:
    kimi = get_capability_profile(ProviderId.KIMI, "kimi-k3")
    qwen = get_capability_profile(ProviderId.QWEN, "qwen3.8-max")
    custom = get_capability_profile(ProviderId.KIMI, "future-custom-model")

    assert kimi.native.vision is CapabilitySupport.SUPPORTED
    assert kimi.adapter.vision is CapabilitySupport.SUPPORTED
    assert kimi.effective.vision is CapabilitySupport.SUPPORTED
    assert kimi.effective.streaming is CapabilitySupport.UNSUPPORTED
    assert qwen.effective.vision is CapabilitySupport.SUPPORTED
    assert custom.native.reasoning is CapabilitySupport.UNKNOWN
    assert custom.effective.reasoning is CapabilitySupport.UNKNOWN
    assert custom.effective.vision is CapabilitySupport.UNKNOWN


def test_deepseek_offers_official_presets_and_keeps_custom_capabilities() -> None:
    definition = get_provider_definition(ProviderId.DEEPSEEK)

    assert definition.default_model_id == "deepseek-flash"
    assert [preset.model_id for preset in definition.presets] == [
        "deepseek-flash",
        "deepseek-v4-pro",
    ]
    assert [preset.display_name for preset in definition.presets] == [
        "DeepSeek V4.1 Flash",
        "DeepSeek V4 Pro",
    ]
    assert definition.allows_custom_model_id is True
    # Only documented vision support changes; request-policy fields stay neutral.
    assert all(
        capability is CapabilitySupport.UNKNOWN
        for preset in definition.presets
        for capability in (
            preset.capabilities.streaming,
            preset.capabilities.reasoning,
            preset.capabilities.tool_calling,
        )
    )
    assert definition.presets[0].capabilities.vision is CapabilitySupport.SUPPORTED
    assert definition.presets[1].capabilities.vision is CapabilitySupport.UNSUPPORTED
    assert get_model_preset(ProviderId.DEEPSEEK, "deepseek-custom") is None


def test_registry_data_is_frozen() -> None:
    preset = get_provider_definition(ProviderId.QWEN).presets[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        preset.model_id = "changed"  # type: ignore[misc]


def test_unknown_provider_has_stable_validation_error() -> None:
    with pytest.raises(ValueError, match="不支持的 Provider ID"):
        get_provider_definition("other")
