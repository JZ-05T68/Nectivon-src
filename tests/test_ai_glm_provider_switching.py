"""Provider switching regression: GLM must never cross-contaminate.

User-ratified v0.8.6 addition: GLM joins as the fifth peer provider.  Each
scenario switches active provider through the secure config boundary and
asserts the resolved runtime carries exactly that provider's base URL,
model id and credential - never a mix from a previous selection.
"""

from __future__ import annotations

from src.ai.credential_store import MemoryCredentialStore, SecretCredential
from src.ai.model_registry import ProviderId, get_provider_definition
from src.ai.provider_config import (
    AIProviderConfig,
    ModelSelectionKind,
    ProviderConfigStore,
    ProviderSettings,
)
from src.ai.provider_factory import (
    build_provider_adapter,
    resolve_provider_runtime,
)
from src.config import Settings

_SYNTHETIC_KEYS = {
    ProviderId.QWEN: "synthetic-qwen-key",
    ProviderId.DEEPSEEK: "synthetic-deepseek-key",
    ProviderId.KIMI: "synthetic-kimi-key",
    ProviderId.HUNYUAN: "synthetic-hunyuan-key",
    ProviderId.GLM: "synthetic-glm-key",
}


def _runtime_for(provider_id: ProviderId, tmp_path):
    config_store = ProviderConfigStore(tmp_path / "ai-providers-v1.json")
    config = AIProviderConfig()
    for current in ProviderId:
        definition = get_provider_definition(current)
        config = config.with_provider(
            ProviderSettings(
                provider_id=current,
                base_url=definition.default_base_url,
                model_id=definition.default_model_id or "",
                model_selection=__import__(
                    "src.ai.provider_config", fromlist=["ModelSelectionKind"]
                ).ModelSelectionKind.PRESET,
            )
        )
    config = config.with_provider(
        ProviderSettings(
            provider_id=provider_id,
            base_url=get_provider_definition(provider_id).default_base_url,
            model_id=get_provider_definition(provider_id).default_model_id or "",
            model_selection=ModelSelectionKind.PRESET,
        ),
        make_active=True,
    )
    config_store.save(config)
    credentials = MemoryCredentialStore()
    for current, key in _SYNTHETIC_KEYS.items():
        credentials.set(current, SecretCredential(key))
    return resolve_provider_runtime(
        provider_id,
        Settings(_env_file=None),
        config_store=config_store,
        credential_store=credentials,
    )


def _assert_runtime_identity(resolved, provider_id: ProviderId) -> None:
    definition = get_provider_definition(provider_id)
    assert resolved is not None
    assert resolved.provider_id is provider_id
    assert resolved.base_url == definition.default_base_url
    assert resolved.default_model == definition.default_model_id
    assert resolved.credential.reveal() == _SYNTHETIC_KEYS[provider_id]


def test_switch_each_provider_to_glm_and_back(tmp_path) -> None:
    """Qwen/DeepSeek/Kimi/Hunyuan -> GLM -> back must never leak state."""

    for provider_id in (
        ProviderId.QWEN,
        ProviderId.DEEPSEEK,
        ProviderId.KIMI,
        ProviderId.HUNYUAN,
    ):
        original = _runtime_for(provider_id, tmp_path)
        _assert_runtime_identity(original, provider_id)

        glm = _runtime_for(ProviderId.GLM, tmp_path)
        _assert_runtime_identity(glm, ProviderId.GLM)
        assert glm.base_url == "https://open.bigmodel.cn/api/paas/v4"
        assert glm.default_model == "glm-5.3"

        restored = _runtime_for(provider_id, tmp_path)
        _assert_runtime_identity(restored, provider_id)


def test_glm_adapter_build_keeps_credential_and_model_separate(tmp_path) -> None:
    from unittest.mock import Mock

    glm = _runtime_for(ProviderId.GLM, tmp_path)
    adapter = build_provider_adapter(glm, transport=Mock())

    assert adapter.provider_id == ProviderId.GLM.value
    assert adapter.is_configured is True
    # Adapter keeps the wrapped credential, never a plain-key copy.
    assert "synthetic-glm-key" not in repr(adapter)


def test_glm_and_qwen_runtimes_never_share_endpoint_or_model(tmp_path) -> None:
    qwen = _runtime_for(ProviderId.QWEN, tmp_path)
    glm = _runtime_for(ProviderId.GLM, tmp_path)

    assert qwen.base_url != glm.base_url
    assert qwen.default_model != glm.default_model
    assert qwen.default_model.startswith("qwen")
    assert glm.default_model.startswith("glm")
