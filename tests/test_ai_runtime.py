"""Tests for the optional AI provider factory in the application runtime."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

import src.runtime as runtime
from src.ai.credential_store import MemoryCredentialStore, SecretCredential
from src.ai.model_registry import ProviderId
from src.ai.provider import (
    AIBudgetExceededError,
    AIExecutionError,
    AIProductionCompositionError,
    AIUnavailableError,
    AuditedAIProvider,
)
from src.ai.provider_config import (
    AIProviderConfig,
    ModelSelectionKind,
    ProviderConfigStore,
    ProviderSettings,
)
from src.ai.qwen_client import QwenTransportError
from src.config import Settings


@pytest.fixture(autouse=True)
def _clear_ai_provider_cache(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # Never inspect a developer's real provider config or OS credential store.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    def clear() -> None:
        for target in (
            runtime.application_ai_provider,
            runtime.application_credential_store,
            runtime.application_experience_model_service,
            runtime.application_hybrid_search_service,
            runtime.application_provider_settings_service,
        ):
            if hasattr(target, "cache_clear"):
                target.cache_clear()

    clear()
    yield
    clear()


def _stub_settings(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> Settings:
    settings = Settings(_env_file=None, **overrides)  # type: ignore[arg-type]
    monkeypatch.setattr(runtime, "application_settings", lambda: settings)
    return settings


def test_manual_mode_disables_ai_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_settings(monkeypatch, ai_api_key="synthetic-manual-credential")
    builder = Mock(side_effect=AssertionError("manual mode must not build an adapter"))
    monkeypatch.setattr(runtime, "build_provider_adapter", builder)

    assert runtime.application_ai_provider() is None
    builder.assert_not_called()


def test_api_mode_without_key_disables_ai_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_settings(monkeypatch, ai_mode="api")
    builder = Mock(side_effect=AssertionError("missing key must not build an adapter"))
    monkeypatch.setattr(runtime, "build_provider_adapter", builder)

    assert runtime.application_ai_provider() is None
    builder.assert_not_called()


def test_api_mode_with_key_builds_audited_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_settings(monkeypatch, ai_mode="api", ai_api_key="sk-runtime-test")
    builder = Mock(wraps=runtime.build_provider_adapter)
    monkeypatch.setattr(runtime, "build_provider_adapter", builder)

    provider = runtime.application_ai_provider()

    assert isinstance(provider, AuditedAIProvider)
    assert provider.is_configured
    assert not hasattr(provider, "wrapped")
    builder.assert_called_once()
    assert isinstance(provider._ledger, runtime._LazyDatabaseAiCallLedger)
    assert isinstance(provider._budget_guard, runtime._LazyTokenBudgetGuard)
    assert isinstance(builder.call_args.args[0].credential, SecretCredential)


def test_secure_provider_config_precedes_legacy_runtime_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    settings = _stub_settings(
        monkeypatch,
        ai_mode="manual",
        ai_api_key="synthetic-legacy-key-must-not-win",
    )
    config_store = ProviderConfigStore()
    config_store.save(
        AIProviderConfig().with_provider(
            ProviderSettings.default_for(ProviderId.QWEN), make_active=True
        )
    )
    credentials = MemoryCredentialStore()
    credentials.set(
        ProviderId.QWEN, SecretCredential("synthetic-secure-runtime-key")
    )
    monkeypatch.setattr(runtime, "application_credential_store", lambda: credentials)
    builder = Mock(wraps=runtime.build_provider_adapter)
    monkeypatch.setattr(runtime, "build_provider_adapter", builder)

    provider = runtime.application_ai_provider()

    assert isinstance(provider, AuditedAIProvider)
    resolved = builder.call_args.args[0]
    assert resolved.llm_model == "qwen3.8-max"
    assert resolved.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert isinstance(resolved.credential, SecretCredential)
    assert resolved.credential.reveal() == "synthetic-secure-runtime-key"
    assert provider._default_model == "qwen3.8-max"
    assert settings.ai_mode == "manual"


def test_application_provider_uses_configured_retry_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry policy comes from Settings.ai_max_extra_attempts, never a hard-coded 0."""

    _stub_settings(
        monkeypatch,
        ai_mode="api",
        ai_api_key="sk-runtime-test",
        ai_max_extra_attempts=0,
    )

    calls: list[int] = []

    def _failing_transport(url, headers, payload, timeout_seconds):
        calls.append(1)
        raise QwenTransportError("transient", status_code=503)

    database = Mock()
    monkeypatch.setattr(runtime, "urllib_transport", _failing_transport)
    monkeypatch.setattr(runtime, "application_database", lambda: database)
    provider = runtime.application_ai_provider()
    assert isinstance(provider, AuditedAIProvider)
    with pytest.raises(AIExecutionError):
        provider.embed(("query",), model="qwen3.7-text-embedding", dimensions=1024)

    assert len(calls) == 1
    database.insert_ai_call.assert_called_once()


def test_application_provider_default_retry_policy_is_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_settings(monkeypatch, ai_mode="api", ai_api_key="sk-runtime-test")
    builder = Mock(wraps=runtime.build_provider_adapter)
    monkeypatch.setattr(runtime, "build_provider_adapter", builder)

    provider = runtime.application_ai_provider()
    assert isinstance(provider, AuditedAIProvider)
    assert builder.call_args.args[0].max_extra_attempts == 2


def test_provider_factory_is_cached_per_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_settings(monkeypatch, ai_mode="api", ai_api_key="sk-runtime-test")

    assert runtime.application_ai_provider() is runtime.application_ai_provider()


def test_ai_factory_never_touches_existing_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AI is optional: building it must not initialize the database or OCR."""

    _stub_settings(monkeypatch, ai_mode="api", ai_api_key="sk-runtime-test")
    monkeypatch.setattr(
        runtime,
        "application_database",
        lambda: pytest.fail("AI 工厂不应触碰数据库"),
    )
    monkeypatch.setattr(
        runtime,
        "application_document_service",
        lambda: pytest.fail("AI 工厂不应触碰文档服务"),
    )

    assert isinstance(runtime.application_ai_provider(), AuditedAIProvider)


def test_local_factory_fails_closed_if_production_builder_returns_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_settings(monkeypatch, ai_mode="api", ai_api_key="sk-runtime-test")
    raw = Mock()
    monkeypatch.setattr(
        runtime, "build_production_audited_provider", lambda *args, **kwargs: raw
    )

    with pytest.raises(AIProductionCompositionError):
        runtime.application_ai_provider()

    raw.complete.assert_not_called()


def test_experience_production_composition_rejects_raw_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = Mock()
    monkeypatch.setattr(runtime, "application_ai_provider", lambda: raw)

    with pytest.raises(AIProductionCompositionError):
        runtime.application_experience_model_service()

    raw.complete.assert_not_called()


@pytest.mark.parametrize("provider_id", list(ProviderId))
def test_runtime_routes_all_four_active_providers(
    monkeypatch: pytest.MonkeyPatch, provider_id: ProviderId
) -> None:
    _stub_settings(
        monkeypatch,
        ai_mode="api",
        ai_api_key="synthetic-legacy-must-not-win",
    )
    config_store = ProviderConfigStore()
    configured = (
        ProviderSettings(
            provider_id=ProviderId.DEEPSEEK,
            base_url="https://api.deepseek.com",
            model_id="console-confirmed-custom",
            model_selection=ModelSelectionKind.CUSTOM,
        )
        if provider_id is ProviderId.DEEPSEEK
        else ProviderSettings.default_for(provider_id)
    )
    config_store.save(
        AIProviderConfig().with_provider(configured, make_active=True)
    )
    credentials = MemoryCredentialStore()
    credentials.set(provider_id, SecretCredential(f"synthetic-{provider_id.value}-key"))
    monkeypatch.setattr(runtime, "application_credential_store", lambda: credentials)

    provider = runtime.application_ai_provider()

    assert isinstance(provider, AuditedAIProvider)
    assert provider.provider_id == provider_id.value
    assert provider.default_model == configured.model_id
    assert provider.is_configured


def test_ai_cache_invalidation_is_narrow(monkeypatch: pytest.MonkeyPatch) -> None:
    ai_provider = Mock()
    experience = Mock()
    hybrid = Mock()
    database = Mock()
    ocr = Mock()
    monkeypatch.setattr(runtime, "application_ai_provider", ai_provider)
    monkeypatch.setattr(runtime, "application_experience_model_service", experience)
    monkeypatch.setattr(runtime, "application_hybrid_search_service", hybrid)
    monkeypatch.setattr(runtime, "application_database", database)
    monkeypatch.setattr(runtime, "application_document_service", ocr)

    runtime.invalidate_ai_runtime_cache()

    ai_provider.cache_clear.assert_called_once()
    experience.cache_clear.assert_called_once()
    hybrid.cache_clear.assert_called_once()
    database.cache_clear.assert_not_called()
    ocr.cache_clear.assert_not_called()


# ---------------------------------------------------------------------------
# TD-06: typed local budget guard semantics
# ---------------------------------------------------------------------------


def _budget_guard(monkeypatch: pytest.MonkeyPatch, **overrides: object):
    from src.runtime import _LazyTokenBudgetGuard

    kwargs: dict[str, object] = {
        "ai_mode": "api",
        "ai_api_key": "sk-runtime-test",
        "ai_daily_token_budget": 100,
        "ai_monthly_token_budget": 1000,
    }
    kwargs.update(overrides)
    settings = _stub_settings(monkeypatch, **kwargs)  # type: ignore[arg-type]
    return settings, _LazyTokenBudgetGuard(settings)


def test_local_daily_budget_exhaustion_raises_typed_budget_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B5: local daily exhaustion -> AIBudgetExceededError, transport untouched."""
    _, guard = _budget_guard(monkeypatch)
    database = Mock()
    database.total_ai_tokens_since.return_value = 100
    monkeypatch.setattr(runtime, "application_database", lambda: database)

    with pytest.raises(AIBudgetExceededError) as excinfo:
        guard.ensure_allowed("completion")

    assert isinstance(excinfo.value, AIUnavailableError)
    assert "日预算" in str(excinfo.value)


def test_local_monthly_budget_exhaustion_raises_typed_budget_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B6: local monthly exhaustion -> AIBudgetExceededError."""
    _, guard = _budget_guard(
        monkeypatch, ai_daily_token_budget=0, ai_monthly_token_budget=500
    )
    database = Mock()
    database.total_ai_tokens_since.return_value = 500
    monkeypatch.setattr(runtime, "application_database", lambda: database)

    with pytest.raises(AIBudgetExceededError) as excinfo:
        guard.ensure_allowed("embedding")

    assert isinstance(excinfo.value, AIUnavailableError)
    assert "月预算" in str(excinfo.value)
    assert database.total_ai_tokens_since.call_args is not None


def test_local_zero_budget_means_unlimited_and_never_touches_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, guard = _budget_guard(
        monkeypatch, ai_daily_token_budget=0, ai_monthly_token_budget=0
    )
    database = Mock()
    monkeypatch.setattr(runtime, "application_database", lambda: database)

    guard.ensure_allowed("completion")

    database.total_ai_tokens_since.assert_not_called()
