"""Tests for versioned, atomic, non-secret provider configuration."""

from __future__ import annotations

import json

import pytest

from src.ai.model_registry import ProviderId
from src.ai.provider_config import (
    AI_PROVIDER_CONFIG_VERSION,
    AIProviderConfig,
    ModelSelectionKind,
    ProviderConfigStore,
    ProviderSettings,
)


def test_config_round_trip_is_versioned_and_contains_no_secret_field(tmp_path) -> None:
    path = tmp_path / "config" / "ai-providers-v1.json"
    store = ProviderConfigStore(path)
    config = AIProviderConfig().with_provider(
        ProviderSettings.default_for(ProviderId.HUNYUAN), make_active=True
    )

    store.save(config)
    loaded = store.load()
    raw_text = path.read_text(encoding="utf-8")
    raw = json.loads(raw_text)

    assert loaded == config
    assert raw["config_version"] == AI_PROVIDER_CONFIG_VERSION
    assert raw["providers"]["hunyuan"]["base_url"] == (
        "https://tokenhub.tencentmaas.com/v1"
    )
    lowered = raw_text.casefold()
    assert "api_key" not in lowered
    assert "credential" not in lowered
    assert "secret" not in lowered
    assert not list(path.parent.glob("*.tmp"))


def test_absent_file_loads_empty_version_one_document(tmp_path) -> None:
    loaded = ProviderConfigStore(tmp_path / "missing.json").load()

    assert loaded == AIProviderConfig()


def test_hunyuan_custom_model_keeps_product_boundary() -> None:
    allowed = ProviderSettings(
        provider_id=ProviderId.HUNYUAN,
        base_url="https://tokenhub.tencentmaas.com/v1",
        model_id="hunyuan-future-model",
        model_selection=ModelSelectionKind.CUSTOM,
    )

    assert allowed.model_id == "hunyuan-future-model"
    with pytest.raises(ValueError, match="腾讯混元模型"):
        ProviderSettings(
            provider_id=ProviderId.HUNYUAN,
            base_url="https://tokenhub.tencentmaas.com/v1",
            model_id="deepseek-v4-pro",
            model_selection=ModelSelectionKind.CUSTOM,
        )


def test_hunyuan_legacy_endpoint_is_allowed_but_not_default() -> None:
    settings = ProviderSettings(
        provider_id=ProviderId.HUNYUAN,
        base_url="https://api.hunyuan.cloud.tencent.com/v1",
        model_id="hy3",
        model_selection=ModelSelectionKind.PRESET,
    )

    assert settings.uses_legacy_endpoint is True
    assert ProviderSettings.default_for(ProviderId.HUNYUAN).uses_legacy_endpoint is False


def test_removing_active_provider_clears_only_that_provider() -> None:
    deepseek = ProviderSettings(
        provider_id=ProviderId.DEEPSEEK,
        base_url="https://api.deepseek.com",
        model_id="console-confirmed-custom",
        model_selection=ModelSelectionKind.CUSTOM,
    )
    config = AIProviderConfig().with_provider(
        deepseek, make_active=True
    ).with_provider(ProviderSettings.default_for(ProviderId.QWEN))

    updated = config.without_provider(ProviderId.DEEPSEEK)

    assert updated.active_provider_id is None
    assert updated.get(ProviderId.DEEPSEEK) is None
    assert updated.get(ProviderId.QWEN) is not None


def test_deepseek_defaults_to_official_flash_preset_and_accepts_custom() -> None:
    default = ProviderSettings.default_for(ProviderId.DEEPSEEK)

    assert default.model_id == "deepseek-flash"
    assert default.model_selection is ModelSelectionKind.PRESET

    settings = ProviderSettings(
        provider_id=ProviderId.DEEPSEEK,
        base_url="https://api.deepseek.com",
        model_id="console-confirmed-custom",
        model_selection=ModelSelectionKind.CUSTOM,
    )
    assert settings.model_id == "console-confirmed-custom"


def test_load_promotes_legacy_deepseek_ids_to_presets_and_migrates_qwen(tmp_path) -> None:
    path = tmp_path / "ai-providers-v1.json"
    path.write_text(
        json.dumps(
            {
                "config_version": 1,
                "active_provider_id": "deepseek",
                "providers": {
                    "deepseek": {
                        "base_url": "https://api.deepseek.com",
                        "model_id": "deepseek-flash",
                        "model_selection": "preset",
                    },
                    "qwen": {
                        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "model_id": "qwen3.7-plus",
                        "model_selection": "preset",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = ProviderConfigStore(path).load()

    # Legacy DeepSeek preset entries map onto the now-official registry preset
    # and stay active; they are never dropped.
    deepseek = loaded.get(ProviderId.DEEPSEEK)
    assert deepseek is not None
    assert deepseek.model_id == "deepseek-flash"
    assert deepseek.model_selection is ModelSelectionKind.PRESET
    assert loaded.active_provider_id is ProviderId.DEEPSEEK
    assert loaded.get(ProviderId.QWEN) == ProviderSettings.default_for(ProviderId.QWEN)


def test_load_promotes_legacy_custom_deepseek_selection_and_keeps_unknown_custom(
    tmp_path,
) -> None:
    path = tmp_path / "ai-providers-v1.json"
    path.write_text(
        json.dumps(
            {
                "config_version": 1,
                "active_provider_id": "deepseek",
                "providers": {
                    "deepseek": {
                        "base_url": "https://api.deepseek.com",
                        "model_id": "deepseek-v4-pro",
                        "model_selection": "custom",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = ProviderConfigStore(path).load()

    deepseek = loaded.get(ProviderId.DEEPSEEK)
    assert deepseek is not None
    assert deepseek.model_id == "deepseek-v4-pro"
    # A stored CUSTOM selection for a now-registered id normalizes to PRESET.
    assert deepseek.model_selection is ModelSelectionKind.PRESET
    assert loaded.active_provider_id is ProviderId.DEEPSEEK

    unknown = ProviderConfigStore(tmp_path / "unknown.json")
    unknown_path = tmp_path / "unknown.json"
    unknown_path.write_text(
        json.dumps(
            {
                "config_version": 1,
                "active_provider_id": "deepseek",
                "providers": {
                    "deepseek": {
                        "base_url": "https://api.deepseek.com",
                        "model_id": "console-confirmed-custom",
                        "model_selection": "custom",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    kept = unknown.load()

    kept_settings = kept.get(ProviderId.DEEPSEEK)
    assert kept_settings is not None
    assert kept_settings.model_id == "console-confirmed-custom"
    assert kept_settings.model_selection is ModelSelectionKind.CUSTOM
    assert kept.active_provider_id is ProviderId.DEEPSEEK


def test_config_rejects_secret_fields_and_plain_http(tmp_path) -> None:
    path = tmp_path / "ai-providers-v1.json"
    path.write_text(
        json.dumps(
            {
                "config_version": 1,
                "active_provider_id": None,
                "providers": {},
                "api_key": "test-only-sentinel",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="禁止出现凭据字段"):
        ProviderConfigStore(path).load()
    with pytest.raises(ValueError, match="HTTPS"):
        ProviderSettings(
            provider_id=ProviderId.QWEN,
            base_url="http://example.invalid/v1",
            model_id="custom-model",
            model_selection=ModelSelectionKind.CUSTOM,
        )
