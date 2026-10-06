"""Purpose separation must affect persisted choices and actual image requests."""

from __future__ import annotations

import base64
import io
from dataclasses import replace

import pytest
from PIL import Image

from src.ai.credential_store import MemoryCredentialStore, SecretCredential
from src.ai.deepseek_client import DeepSeekAdapter
from src.ai.glm_client import GlmAdapter
from src.ai.image_message import image_user_content
from src.ai.image_probe import image_connection_probe
from src.ai.kimi_client import KimiAdapter
from src.ai.model_registry import ModelPurpose, ProviderId, list_model_presets
from src.ai.provider import ProviderCallError, ProviderErrorCode
from src.ai.provider_config import AIProviderConfig, ProviderConfigStore, ProviderSettings
from src.ai.provider_factory import resolve_active_provider_runtime, resolve_image_provider_runtime
from src.ai.provider_settings_service import ProviderSettingsService
from src.config import Settings
from src.question_candidate_service import parse_candidates_payload


def test_image_choices_exclude_text_only_and_unknown_models() -> None:
    choices = {
        provider.value: [
            preset.model_id
            for preset in list_model_presets(
                provider,
                purpose=ModelPurpose.IMAGE,
            )
        ]
        for provider in ProviderId
    }
    assert choices == {
        "deepseek": ["deepseek-flash"],
        "qwen": ["qwen3.8-max", "qwen3.8-flash"],
        "kimi": ["kimi-k3"],
        "hunyuan": [],
        "glm": ["glm-5.3-flash"],
    }
    assert len(list_model_presets("hunyuan")) == 2
    assert len(list_model_presets("glm")) == 2


def test_independent_selections_round_trip_and_resolve_without_network(tmp_path) -> None:
    store = ProviderConfigStore(tmp_path / "providers.json")
    credentials = MemoryCredentialStore()
    settings = Settings(_env_file=None)
    text = ProviderSettings.default_for("deepseek")
    image = ProviderSettings.default_for("qwen")
    for provider in (ProviderId.DEEPSEEK, ProviderId.QWEN):
        credentials.set(provider, SecretCredential("synthetic-purpose-key"))
    config = AIProviderConfig().with_provider(text, make_active=True).with_image_provider(image)
    store.save(config)
    assert store.load() == config
    assert "synthetic-purpose-key" not in store.path.read_text(encoding="utf-8")
    resolved_text = resolve_active_provider_runtime(
        settings,
        config_store=store,
        credential_store=credentials,
    )
    resolved_image = resolve_image_provider_runtime(
        settings,
        config_store=store,
        credential_store=credentials,
    )
    assert resolved_text.provider_id is ProviderId.DEEPSEEK
    assert resolved_image.provider_id is ProviderId.QWEN
    store.save(config.with_image_provider(replace(image, model_id="qwen3.8-flash")))
    assert store.load().get("deepseek") == text
    assert store.load().active_provider_id is ProviderId.DEEPSEEK
    credentials.delete(ProviderId.QWEN)
    assert (
        resolve_image_provider_runtime(
            settings,
            config_store=store,
            credential_store=credentials,
        )
        is None
    )  # Never silently send the user's image to another vendor.


def test_image_only_configuration_does_not_activate_text(tmp_path) -> None:
    store = ProviderConfigStore(tmp_path / "providers.json")
    credentials = MemoryCredentialStore()
    credentials.set("kimi", SecretCredential("synthetic-purpose-key"))
    store.save(AIProviderConfig().with_image_provider(ProviderSettings.default_for("kimi")))
    assert (
        resolve_active_provider_runtime(
            Settings(_env_file=None),
            config_store=store,
            credential_store=credentials,
        )
        is None
    )
    assert (
        resolve_image_provider_runtime(
            Settings(_env_file=None),
            config_store=store,
            credential_store=credentials,
        ).default_model
        == "kimi-k3"
    )


@pytest.mark.parametrize(
    "active,text_provider,text_model,make_active",
    [
        ("qwen", "qwen", "qwen3.8-flash", True),
        ("qwen", "kimi", "kimi-k3", True),
        ("deepseek", "qwen", "qwen3.8-flash", False),
    ],
)
def test_editing_legacy_text_settings_preserves_existing_image_route(
    tmp_path, active, text_provider, text_model, make_active,
) -> None:
    store = ProviderConfigStore(tmp_path / "providers.json")
    credentials = MemoryCredentialStore()
    config = AIProviderConfig()
    for provider in (ProviderId.QWEN, ProviderId.DEEPSEEK, ProviderId.KIMI):
        credentials.set(provider, SecretCredential("synthetic-purpose-key"))
        config = config.with_provider(
            ProviderSettings.default_for(provider), make_active=provider.value == active,
        )
    assert config.image_settings is None
    store.save(config)
    service = ProviderSettingsService(
        config_store=store, credential_store=credentials,
        application_settings=Settings(_env_file=None),
        transport=lambda *args: pytest.fail("Saving settings must not call AI."),
        invalidate_runtime=lambda: None,
    )
    assert service.current_image_state() == (ProviderId.QWEN, "qwen3.8-max")
    service.save(
        replace(ProviderSettings.default_for(text_provider), model_id=text_model),
        make_active=make_active, purpose=ModelPurpose.TEXT,
    )
    saved = store.load()
    assert saved.get(text_provider).model_id == text_model
    assert saved.active_provider_id.value == (text_provider if make_active else active)
    assert saved.image_settings == ProviderSettings.default_for("qwen")
    assert resolve_image_provider_runtime(
        Settings(_env_file=None), config_store=store, credential_store=credentials,
    ).default_model == "qwen3.8-max"


@pytest.mark.parametrize(
    "provider,model",
    [
        ("deepseek", "deepseek-v4-pro"),
        ("glm", "glm-5.3"),
        ("hunyuan", "hy3"),
    ],
)
def test_text_model_cannot_be_saved_as_image_model(provider, model) -> None:
    with pytest.raises(ValueError, match="读图模型"):
        AIProviderConfig().with_image_provider(
            replace(
                ProviderSettings.default_for(provider),
                model_id=model,
            )
        )


@pytest.mark.parametrize(
    "adapter_type,model,token_field",
    [
        (DeepSeekAdapter, "deepseek-flash", "max_tokens"),
        (KimiAdapter, "kimi-k3", "max_completion_tokens"),
        (KimiAdapter, "kimi-k2.6", "max_tokens"),
        (GlmAdapter, "glm-5.3-flash", "max_tokens"),
    ],
)
def test_vendor_vision_request_contains_original_image_and_no_ocr(
    adapter_type,
    model,
    token_field,
) -> None:
    requests = []
    timeouts = []

    def transport(url, headers, payload, timeout_seconds):
        requests.append(payload)
        timeouts.append(timeout_seconds)
        return {
            "model": model,
            "choices": [{"message": {"content": "红色、蓝色"}, "finish_reason": "stop"}],
        }

    adapter = adapter_type(api_key="synthetic-purpose-key", model=model, transport=transport)
    encoded = image_connection_probe()
    result = adapter.complete_vision(
        "只读原图", f"data:image/png;base64,{encoded}", max_completion_tokens=1024
    )
    assert result.text == "红色、蓝色"
    payload = requests[0]
    assert payload["model"] == model
    assert payload[token_field] == 1024
    content = payload["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "只读原图"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == f"data:image/png;base64,{encoded}"
    assert len(requests) == 1
    assert timeouts == [120.0]
    if adapter_type is DeepSeekAdapter:
        assert payload["thinking"] == {"type": "disabled"}
    if adapter_type is GlmAdapter:
        assert payload["thinking"] == {"type": "enabled"}
        assert payload["reasoning_effort"] == "low"


def test_repeated_atomic_child_cannot_discard_options_or_figure_geometry() -> None:
    import json

    payload = {
        "candidates": [
            {
                "number": "1",
                "stem": "选择",
                "completeness": "complete",
                "has_shared_stem": False,
                "is_multiple_choice": True,
                "options": [{"label": label, "text": label + "内容"} for label in "ABCD"],
                "visual_regions": [{"role": "stem", "bbox": [100, 200, 300, 400]}],
                "children": [{"number": "1", "stem": "选择"}],
            }
        ]
    }
    candidate = parse_candidates_payload(json.dumps(payload, ensure_ascii=False))[0]
    assert candidate.children == []
    assert candidate.stem == "选择\n\nA. A内容\n\nB. B内容\n\nC. C内容\n\nD. D内容"
    assert len(candidate.visual_regions) == 1


@pytest.mark.parametrize(
    "adapter_type,model",
    [
        (DeepSeekAdapter, "deepseek-v4-pro"),
        (GlmAdapter, "glm-5.3"),
        (KimiAdapter, "future-custom"),
    ],
)
def test_unsupported_image_request_is_rejected_before_transport(adapter_type, model) -> None:
    adapter = adapter_type(
        api_key="synthetic-purpose-key",
        model=model,
        transport=lambda *args: pytest.fail("must not send a request"),
    )
    with pytest.raises(ProviderCallError) as error:
        adapter.complete_vision("read", image_connection_probe())
    assert error.value.detail.code is ProviderErrorCode.UNSUPPORTED_CAPABILITY


def test_mime_matches_jpeg_bytes_and_invalid_input_cannot_be_forwarded() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="JPEG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    assert image_user_content("read", encoded)[1]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,",
    )
    with pytest.raises(ValueError):
        image_user_content("read", "OCR text is not an image")


def test_image_connection_test_sends_pixels_and_never_saves_selection(tmp_path) -> None:
    calls = []

    def transport(url, headers, payload, timeout_seconds):
        calls.append(payload)
        return {"model": payload["model"], "choices": [{"message": {"content": "红、蓝"}}]}

    service = ProviderSettingsService(
        config_store=ProviderConfigStore(tmp_path / "providers.json"),
        credential_store=MemoryCredentialStore(),
        application_settings=Settings(_env_file=None),
        transport=transport,
        invalidate_runtime=lambda: None,
    )
    service.test_connection(
        ProviderSettings.default_for("kimi"),
        api_key_override="synthetic-purpose-key",
        purpose=ModelPurpose.IMAGE,
    )
    assert calls[0]["messages"][0]["content"][1]["type"] == "image_url"
    assert not service.config_path_exists
