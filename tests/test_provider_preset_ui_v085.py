"""v0.8.5 provider preset / settings-UI regression tests (zero-batch fix).

Covers the acceptance items for the Qwen/DeepSeek preset fix:
registry presets, custom-model conditional visibility, legacy config
migration, current-model display legality, unchanged Kimi/Hunyuan presets
and offline (no-API-key) knowledge-base independence.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.ai.credential_store import MemoryCredentialStore
from src.ai.model_registry import (
    CapabilitySupport,
    ProviderId,
    get_model_preset,
    get_provider_definition,
)
from src.ai.provider_config import ModelSelectionKind, ProviderConfigStore, ProviderSettings
from src.ai.provider_settings_service import (
    ProviderConfigurationState,
    ProviderSettingsService,
)
from src.config import Settings
from src.database import Database
from src.search_service import SearchService

_CUSTOM = "自定义 Model ID"


def _service(tmp_path: Path) -> ProviderSettingsService:
    return ProviderSettingsService(
        config_store=ProviderConfigStore(tmp_path / "ai-providers-v1.json"),
        credential_store=MemoryCredentialStore(),
        application_settings=Settings(_env_file=None),
        transport=lambda *args: pytest.fail("render/save must not call network"),
        invalidate_runtime=lambda: None,
    )


@pytest.fixture
def settings_ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated application settings with no AI key and an empty database."""
    data = tmp_path / "data"
    settings = Settings(
        data_dir=data,
        raw_dir=data / "raw",
        pages_dir=data / "pages",
        markdown_dir=data / "markdown",
        database_dir=data / "database",
        database_path=data / "database" / "knowledge.db",
        backups_dir=tmp_path / "backups",
        logs_dir=tmp_path / "logs",
        log_path=tmp_path / "logs" / "engineering-kb.log",
        runtime_dir=tmp_path / "runtime",
        pid_path=tmp_path / "runtime" / "engineering-kb.pid.json",
        port=49342,
        _env_file=None,
    )
    settings.ensure_directories()
    database = Database(settings.database_path)

    import src.runtime as runtime

    monkeypatch.setattr(runtime, "application_settings", lambda: settings)
    monkeypatch.setattr(runtime, "application_database", lambda: database)
    provider_settings_service = _service(tmp_path)
    monkeypatch.setattr(
        runtime,
        "application_provider_settings_service",
        lambda: provider_settings_service,
    )
    return settings, database, provider_settings_service


def _render_page() -> AppTest:
    return AppTest.from_file("pages/13_运行说明.py").run(timeout=10)


def _model_select(app: AppTest):
    return next(item for item in app.selectbox if item.label == "模型")


def _select_provider(app: AppTest, provider: ProviderId) -> AppTest:
    provider_select = next(item for item in app.selectbox if item.label == "服务商")
    return provider_select.set_value(provider).run(timeout=10)


def _current_model_markdown(app: AppTest) -> str:
    return next(
        item.value for item in app.markdown if "当前模型" in item.value
    )


# --- Qwen (items 1-6) ---------------------------------------------------------


def test_qwen_default_preset_is_qwen38_max() -> None:
    definition = get_provider_definition(ProviderId.QWEN)

    assert definition.default_model_id == "qwen3.8-max"
    preset = get_model_preset(ProviderId.QWEN, "qwen3.8-max")
    assert preset is not None
    assert preset.display_name == "Qwen3.8 Max"
    assert preset.recommended is True


def test_qwen_dropdown_contains_both_official_presets_and_custom(
    settings_ui,
) -> None:
    _settings, _database, _service = settings_ui
    app = _select_provider(_render_page(), ProviderId.QWEN)

    options = _model_select(app).options
    assert "qwen3.8-max" in options
    assert "qwen3.8-flash" in options
    assert _CUSTOM in options


def test_qwen_preset_selection_hides_custom_input(settings_ui) -> None:
    _settings, _database, _service = settings_ui
    app = _select_provider(_render_page(), ProviderId.QWEN)

    assert _model_select(app).value == "qwen3.8-max"
    assert not any(item.label == "自定义 Model ID" for item in app.text_input)


def test_qwen_custom_selection_reveals_model_id_input(settings_ui) -> None:
    _settings, _database, _service = settings_ui
    app = _select_provider(_render_page(), ProviderId.QWEN)

    app = _model_select(app).set_value(_CUSTOM).run(timeout=10)

    assert any(item.label == "自定义 Model ID" for item in app.text_input)
    assert not app.exception


# --- DeepSeek (items 7-12; legacy 13-15 live in test_ai_provider_config) ------


def test_deepseek_default_preset_is_v41_flash() -> None:
    definition = get_provider_definition(ProviderId.DEEPSEEK)

    assert definition.default_model_id == "deepseek-flash"
    preset = get_model_preset(ProviderId.DEEPSEEK, "deepseek-flash")
    assert preset is not None
    assert preset.display_name == "DeepSeek V4.1 Flash"
    assert preset.recommended is True


def test_deepseek_dropdown_contains_both_official_presets_and_custom(
    settings_ui,
) -> None:
    _settings, _database, _service = settings_ui
    app = _select_provider(_render_page(), ProviderId.DEEPSEEK)

    options = _model_select(app).options
    assert "deepseek-flash" in options
    assert "deepseek-v4-pro" in options
    assert _CUSTOM in options


def test_deepseek_preset_selection_hides_custom_input(settings_ui) -> None:
    _settings, _database, _service = settings_ui
    app = _select_provider(_render_page(), ProviderId.DEEPSEEK)

    assert _model_select(app).value == "deepseek-flash"
    assert not any(item.label == "自定义 Model ID" for item in app.text_input)


def test_deepseek_custom_selection_reveals_model_id_input(settings_ui) -> None:
    _settings, _database, _service = settings_ui
    app = _select_provider(_render_page(), ProviderId.DEEPSEEK)

    app = _model_select(app).set_value(_CUSTOM).run(timeout=10)

    assert any(item.label == "自定义 Model ID" for item in app.text_input)
    assert not app.exception


def test_deepseek_saved_custom_input_survives_preset_toggling(settings_ui) -> None:
    _settings, _database, service = settings_ui
    service.save(
        ProviderSettings(
            provider_id=ProviderId.DEEPSEEK,
            base_url="https://api.deepseek.com",
            model_id="console-confirmed-custom",
            model_selection=ModelSelectionKind.CUSTOM,
        ),
        new_api_key="synthetic-deepseek-ui-key-9d21",
        make_active=True,
    )

    app = _select_provider(_render_page(), ProviderId.DEEPSEEK)
    # A saved custom selection keeps the dropdown on the custom entry with the
    # stored id prefilled.
    assert _model_select(app).value == _CUSTOM
    text_input = next(item for item in app.text_input if item.label == "自定义 Model ID")
    assert text_input.value == "console-confirmed-custom"

    # Switching to an official preset hides the input and never rewrites the
    # stored custom configuration by itself.
    app = _model_select(app).set_value("deepseek-flash").run(timeout=10)
    assert not any(item.label == "自定义 Model ID" for item in app.text_input)
    stored = service.load_config().get(ProviderId.DEEPSEEK)
    assert stored is not None
    assert stored.model_id == "console-confirmed-custom"
    assert stored.model_selection is ModelSelectionKind.CUSTOM

    # Switching back to custom restores the saved id from storage, not a blank.
    app = _model_select(app).set_value(_CUSTOM).run(timeout=10)
    text_input = next(item for item in app.text_input if item.label == "自定义 Model ID")
    assert text_input.value == "console-confirmed-custom"


# --- Kimi / Hunyuan unchanged (items 16-17) -----------------------------------


def test_kimi_presets_remain_unchanged() -> None:
    definition = get_provider_definition(ProviderId.KIMI)

    assert [preset.model_id for preset in definition.presets] == [
        "kimi-k3",
        "kimi-k2.6",
    ]
    assert [preset.display_name for preset in definition.presets] == [
        "Kimi K3",
        "Kimi K2.6",
    ]
    assert definition.default_model_id == "kimi-k3"
    assert definition.presets[0].capabilities.streaming is CapabilitySupport.SUPPORTED
    assert definition.presets[0].context_window_tokens == 1_000_000


def test_hunyuan_presets_remain_unchanged() -> None:
    definition = get_provider_definition(ProviderId.HUNYUAN)

    assert [preset.model_id for preset in definition.presets] == [
        "hy4-preview",
        "hy3",
    ]
    assert [preset.display_name for preset in definition.presets] == [
        "Hunyuan HY 4 Preview",
        "Hunyuan HY 3",
    ]
    assert definition.default_model_id == "hy4-preview"
    assert definition.legacy_base_urls == (
        "https://api.hunyuan.cloud.tencent.com/v1",
    )


# --- Current-model display (item 18) ------------------------------------------


def test_current_model_display_follows_active_provider_legally(settings_ui) -> None:
    _settings, _database, service = settings_ui
    service.save(
        ProviderSettings.default_for(ProviderId.DEEPSEEK),
        new_api_key="synthetic-deepseek-ui-key-1f3a",
        make_active=True,
    )

    app = _render_page()
    rendered = _current_model_markdown(app)

    assert "Provider：`DeepSeek`" in rendered
    assert "Model：`deepseek-flash`" in rendered
    # Illegal states must never render: an active provider with an empty model
    # or a bare custom-model placeholder instead of a concrete id.
    assert "Model：`—`" not in rendered
    assert "Model：`自定义" not in rendered

    # Switching the edit-target provider must not change the active model.
    app = _select_provider(app, ProviderId.QWEN)
    still_active = _current_model_markdown(app)
    assert "Provider：`DeepSeek`" in still_active
    assert "Model：`deepseek-flash`" in still_active


def test_current_model_display_without_any_configuration_has_no_provider_pair(
    settings_ui,
) -> None:
    _settings, _database, _service = settings_ui

    state, active_provider, active_model = _service_state(settings_ui)

    assert state is ProviderConfigurationState.UNCONFIGURED
    assert active_provider is None
    assert active_model is None
    app = _render_page()
    rendered = _current_model_markdown(app)
    # Unconfigured: both fields degrade together; no half-populated pair.
    assert "Provider：`—`" in rendered
    assert "Model：`—`" in rendered


def _service_state(settings_ui):
    _settings, _database, service = settings_ui
    return service.current_state()


# --- No-API-key independence (item 19) ----------------------------------------


def test_local_knowledge_base_works_without_any_api_key(settings_ui) -> None:
    settings, database, service = settings_ui

    state, _provider, _model = service.current_state()
    assert state is ProviderConfigurationState.UNCONFIGURED

    image_path = settings.pages_dir / "smoke-page.png"
    image_path.write_bytes(b"\x89PNG-not-a-real-image")
    document = database.create_document(
        title="离线冒烟手册",
        filename="offline.pdf",
        source_path="data/raw/offline.pdf",
        sha256="e" * 64,
        page_count=1,
    )
    database.create_page(
        document_id=document.id,
        page_number=1,
        image_path=image_path,
        extracted_text="时间继电器分为通电延时型和断电延时型。",
    )

    hits = SearchService(database).search("时间继电器", limit=5)
    assert hits
    assert hits[0].document_id == document.id


# --- saved custom model id survives switching back to a preset ----------------


def test_saving_preset_after_custom_keeps_stored_custom_until_explicit_save(
    settings_ui,
) -> None:
    _settings, _database, service = settings_ui
    custom = ProviderSettings(
        provider_id=ProviderId.DEEPSEEK,
        base_url="https://api.deepseek.com",
        model_id="console-confirmed-custom",
        model_selection=ModelSelectionKind.CUSTOM,
    )
    service.save(custom, new_api_key="synthetic-deepseek-ui-key-2b7c", make_active=True)

    service.save(
        ProviderSettings(
            provider_id=ProviderId.DEEPSEEK,
            base_url="https://api.deepseek.com",
            model_id="deepseek-flash",
            model_selection=ModelSelectionKind.PRESET,
        ),
        new_api_key="",
        make_active=True,
    )

    stored = service.load_config().get(ProviderId.DEEPSEEK)
    assert stored is not None
    assert stored.model_id == "deepseek-flash"
    assert stored.model_selection is ModelSelectionKind.PRESET
