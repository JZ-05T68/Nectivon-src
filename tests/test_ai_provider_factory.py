"""Four-provider resolution with secure config and legacy-Qwen compatibility."""

from __future__ import annotations

from unittest.mock import Mock

from src.ai.credential_store import MemoryCredentialStore, SecretCredential
from src.ai.model_registry import ProviderId
from src.ai.provider_config import (
    AIProviderConfig,
    ModelSelectionKind,
    ProviderConfigStore,
    ProviderSettings,
)
from src.ai.provider_factory import (
    QwenConfigurationSource,
    build_provider_adapter,
    build_qwen_adapter,
    resolve_provider_runtime,
    resolve_qwen_runtime,
)
from src.config import Settings

_LEGACY_KEY = "synthetic-legacy-key-37ac"
_SECURE_KEY = "synthetic-secure-key-82bf"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "ai_mode": "api",
        "ai_api_key": _LEGACY_KEY,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def _secure_config(path, *, active: ProviderId = ProviderId.QWEN) -> ProviderConfigStore:
    store = ProviderConfigStore(path)
    settings = (
        ProviderSettings(
            provider_id=ProviderId.DEEPSEEK,
            base_url="https://api.deepseek.com",
            model_id="console-confirmed-custom",
            model_selection=ModelSelectionKind.CUSTOM,
        )
        if active is ProviderId.DEEPSEEK
        else ProviderSettings.default_for(active)
    )
    config = AIProviderConfig().with_provider(
        settings, make_active=True
    )
    store.save(config)
    return store


def test_absent_new_config_preserves_legacy_qwen_resolution(tmp_path) -> None:
    resolved = resolve_qwen_runtime(
        _settings(ai_llm_model="qwen-legacy-model"),
        config_store=ProviderConfigStore(tmp_path / "missing.json"),
    )

    assert resolved is not None
    assert resolved.source is QwenConfigurationSource.LEGACY_ENV
    assert resolved.llm_model == "qwen-legacy-model"
    assert resolved.credential.reveal() == _LEGACY_KEY
    assert _LEGACY_KEY not in repr(resolved)


def test_secure_config_and_credential_take_priority_over_legacy(tmp_path) -> None:
    config_store = _secure_config(tmp_path / "ai-providers-v1.json")
    credentials = MemoryCredentialStore()
    credentials.set(ProviderId.QWEN, SecretCredential(_SECURE_KEY))

    resolved = resolve_qwen_runtime(
        _settings(ai_mode="manual", ai_api_key=_LEGACY_KEY),
        config_store=config_store,
        credential_store=credentials,
    )

    assert resolved is not None
    assert resolved.source is QwenConfigurationSource.SECURE_PROVIDER_CONFIG
    assert resolved.llm_model == "qwen3.8-max"
    assert resolved.credential.reveal() == _SECURE_KEY
    assert _LEGACY_KEY not in repr(resolved)
    assert _SECURE_KEY not in repr(resolved)


def test_secure_config_without_secure_credential_does_not_fall_back(tmp_path) -> None:
    resolved = resolve_qwen_runtime(
        _settings(ai_api_key=_LEGACY_KEY),
        config_store=_secure_config(tmp_path / "ai-providers-v1.json"),
        credential_store=MemoryCredentialStore(),
    )

    assert resolved is None


def test_non_qwen_secure_selection_does_not_construct_other_adapter(tmp_path) -> None:
    resolved = resolve_qwen_runtime(
        _settings(),
        config_store=_secure_config(
            tmp_path / "ai-providers-v1.json", active=ProviderId.DEEPSEEK
        ),
        credential_store=MemoryCredentialStore(),
    )

    assert resolved is None


def test_no_legacy_ai_configuration_remains_optional(tmp_path) -> None:
    assert (
        resolve_qwen_runtime(
            _settings(ai_mode="manual", ai_api_key=""),
            config_store=ProviderConfigStore(tmp_path / "missing.json"),
        )
        is None
    )


def test_adapter_builder_passes_wrapped_secret_and_capability_mode(tmp_path) -> None:
    resolved = resolve_qwen_runtime(
        _settings(), config_store=ProviderConfigStore(tmp_path / "missing.json")
    )
    assert resolved is not None
    constructor = Mock(return_value=Mock())
    transport = Mock()

    build_qwen_adapter(resolved, transport=transport, constructor=constructor)

    kwargs = constructor.call_args.kwargs
    assert isinstance(kwargs["api_key"], SecretCredential)
    assert kwargs["api_key"].reveal() == _LEGACY_KEY
    assert kwargs["enable_thinking"] is None
    assert kwargs["transport"] is transport


def test_central_four_provider_boundary_keeps_qwen_legacy_compatible(tmp_path) -> None:
    resolved = resolve_provider_runtime(
        ProviderId.QWEN,
        _settings(),
        config_store=ProviderConfigStore(tmp_path / "missing.json"),
    )
    assert resolved is not None

    adapter = build_provider_adapter(resolved, transport=Mock())

    assert adapter.is_configured is True
