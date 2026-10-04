"""Settings service tests never access real credentials or provider networks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from src.ai.credential_store import MemoryCredentialStore
from src.ai.model_registry import ProviderId
from src.ai.provider import ProviderCallError, ProviderErrorCode
from src.ai.provider_config import (
    ModelSelectionKind,
    ProviderConfigStore,
    ProviderSettings,
)
from src.ai.provider_settings_service import (
    ProviderConfigurationState,
    ProviderSettingsService,
    safe_settings_error,
)
from src.config import Settings

_SYNTHETIC_KEY = "synthetic-settings-credential-72ac"


class _FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, str], Mapping[str, Any], float]] = []

    def __call__(self, url, headers, payload, timeout_seconds):
        self.calls.append((url, headers, payload, timeout_seconds))
        return {
            "model": payload["model"],
            "choices": [
                {"message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }


def _service(tmp_path, *, settings: Settings | None = None):
    credentials = MemoryCredentialStore()
    transport = _FakeTransport()
    invalidations: list[int] = []
    service = ProviderSettingsService(
        config_store=ProviderConfigStore(tmp_path / "ai-providers-v1.json"),
        credential_store=credentials,
        application_settings=settings or Settings(_env_file=None),
        transport=transport,
        invalidate_runtime=lambda: invalidations.append(1),
    )
    return service, credentials, transport, invalidations


def _deepseek_custom() -> ProviderSettings:
    return ProviderSettings(
        provider_id=ProviderId.DEEPSEEK,
        base_url="https://api.deepseek.com",
        model_id="console-confirmed-custom",
        model_selection=ModelSelectionKind.CUSTOM,
    )


def test_provider_configs_and_credentials_remain_independent(tmp_path) -> None:
    service, credentials, _, invalidations = _service(tmp_path)
    deepseek = _deepseek_custom()
    qwen = ProviderSettings.default_for(ProviderId.QWEN)

    service.save(deepseek, new_api_key=_SYNTHETIC_KEY, make_active=True)
    service.save(qwen, new_api_key="synthetic-qwen-credential-65bd", make_active=False)
    service.save(qwen, new_api_key="", make_active=True)

    config = service.load_config()
    assert config.active_provider_id is ProviderId.QWEN
    assert config.get(ProviderId.DEEPSEEK) == deepseek
    assert config.get(ProviderId.QWEN) == qwen
    assert credentials.get(ProviderId.DEEPSEEK) is not None
    assert credentials.get(ProviderId.QWEN) is not None
    assert len(invalidations) == 3


def test_blank_key_preserves_saved_credential(tmp_path) -> None:
    service, credentials, _, _ = _service(tmp_path)
    settings = ProviderSettings.default_for(ProviderId.KIMI)
    service.save(settings, new_api_key=_SYNTHETIC_KEY, make_active=True)

    service.save(settings, new_api_key="   ", make_active=True)

    saved = credentials.get(ProviderId.KIMI)
    assert saved is not None
    assert saved.reveal() == _SYNTHETIC_KEY


def test_delete_active_provider_does_not_delete_other_provider(tmp_path) -> None:
    service, credentials, _, invalidations = _service(tmp_path)
    service.save(
        _deepseek_custom(),
        new_api_key=_SYNTHETIC_KEY,
        make_active=True,
    )
    service.save(
        ProviderSettings.default_for(ProviderId.HUNYUAN),
        new_api_key="synthetic-hunyuan-settings-key",
        make_active=False,
    )

    service.delete(ProviderId.DEEPSEEK)

    config = service.load_config()
    assert config.active_provider_id is None
    assert config.get(ProviderId.DEEPSEEK) is None
    assert config.get(ProviderId.HUNYUAN) is not None
    assert credentials.get(ProviderId.DEEPSEEK) is None
    assert credentials.get(ProviderId.HUNYUAN) is not None
    assert len(invalidations) == 3


def test_connection_uses_same_adapter_with_override_without_saving_key(tmp_path) -> None:
    service, credentials, transport, invalidations = _service(tmp_path)
    settings = ProviderSettings.default_for(ProviderId.KIMI)

    result = service.test_connection(
        settings, api_key_override=_SYNTHETIC_KEY
    )

    assert result.provider_id is ProviderId.KIMI
    assert result.requested_model == "kimi-k3"
    assert result.resolved_model == "kimi-k3"
    assert transport.calls[0][2]["stream"] is False
    assert transport.calls[0][2]["max_completion_tokens"] == 8
    assert credentials.get(ProviderId.KIMI) is None
    assert invalidations == []
    assert _SYNTHETIC_KEY not in transport.calls[0][0]


def test_make_active_and_connection_require_credential(tmp_path) -> None:
    service, _, _, _ = _service(tmp_path)
    settings = _deepseek_custom()

    with pytest.raises(ProviderCallError) as save_error:
        service.save(settings, make_active=True)
    with pytest.raises(ProviderCallError) as test_error:
        service.test_connection(settings)

    assert save_error.value.detail.code is ProviderErrorCode.CREDENTIAL_MISSING
    assert test_error.value.detail.code is ProviderErrorCode.CREDENTIAL_MISSING


def test_invalid_base_url_maps_to_safe_code_without_echo(tmp_path) -> None:
    service, _, _, _ = _service(tmp_path)
    unsafe = "http://synthetic-secret@example.invalid/v1?key=synthetic"

    with pytest.raises(ProviderCallError) as captured:
        service.make_settings(
            provider_id=ProviderId.QWEN,
            base_url=unsafe,
            model_id="qwen3.8-max",
            model_selection=ModelSelectionKind.PRESET,
        )

    assert captured.value.detail.code is ProviderErrorCode.INVALID_BASE_URL
    assert unsafe not in str(captured.value)
    assert "HTTPS" in safe_settings_error(captured.value)


def test_legacy_state_is_reported_but_never_migrated(tmp_path) -> None:
    legacy = Settings(
        _env_file=None,
        ai_mode="api",
        ai_provider="qwen",
        ai_api_key=_SYNTHETIC_KEY,
        ai_llm_model="qwen-legacy-model",
    )
    service, credentials, _, _ = _service(tmp_path, settings=legacy)

    state, provider_id, model = service.current_state()

    assert state is ProviderConfigurationState.LEGACY
    assert provider_id is ProviderId.QWEN
    assert model == "qwen-legacy-model"
    assert credentials.get(ProviderId.QWEN) is None
    assert not service.config_path_exists


def test_unconfigured_deepseek_view_defaults_to_official_flash_preset(tmp_path) -> None:
    service, _, _, _ = _service(tmp_path)

    view = service.view(ProviderId.DEEPSEEK)

    assert view.provider_id is ProviderId.DEEPSEEK
    assert view.settings is not None
    assert view.settings.model_id == "deepseek-flash"
    assert view.settings.model_selection is ModelSelectionKind.PRESET
    assert view.credential_saved is False
