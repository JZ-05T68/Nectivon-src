"""Streamlit component for local multi-provider model configuration."""

from __future__ import annotations

import logging

import streamlit as st

from src.ai.model_registry import (
    CapabilitySupport,
    ModelPurpose,
    ProviderId,
    get_capability_profile,
    get_provider_definition,
    list_model_presets,
)
from src.ai.provider import AIUnavailableError, ProviderCallError
from src.ai.provider_config import ModelSelectionKind
from src.ai.provider_settings_service import (
    ProviderConfigurationState,
    ProviderSettingsService,
    safe_settings_error,
)

LOGGER = logging.getLogger(__name__)
_CUSTOM_MODEL = "自定义 Model ID"
_PROVIDER_LABELS = {
    ProviderId.DEEPSEEK: "DeepSeek",
    ProviderId.QWEN: "Qwen",
    ProviderId.KIMI: "Kimi",
    ProviderId.HUNYUAN: "Hunyuan / 腾讯混元",
    ProviderId.GLM: "GLM / 智谱",
}
_AI_SESSION_KEYS = (
    "agent_live_client",
    "rag_answer_output",
    "rag_answer_mock",
    "experience_output",
    "experience_is_mock",
    "experience_context_items",
)


def render_provider_settings() -> None:
    """Render settings without ever reading a stored credential value."""

    from src.runtime import application_provider_settings_service

    service = application_provider_settings_service()
    st.divider()
    st.subheader("AI / 模型服务")
    try:
        state, active_provider, active_model = service.current_state()
    except (AIUnavailableError, ProviderCallError) as exc:
        st.error(safe_settings_error(exc))
        return

    status_label = {
        ProviderConfigurationState.CONFIGURED: "已配置",
        ProviderConfigurationState.UNCONFIGURED: "未配置",
        ProviderConfigurationState.LEGACY: "legacy",
    }[state]
    current_provider = _PROVIDER_LABELS.get(active_provider, "—")
    st.markdown(
        f"**当前模型（讲题/知识问答）**　Provider：`{current_provider}`　"
        f"Model：`{active_model or '—'}`　状态：**{status_label}**"
    )
    if state is ProviderConfigurationState.LEGACY:
        st.info(
            "当前正在使用旧环境变量配置。尚未迁移到安全存储；"
            "可在下方重新输入 API Key 并保存为新配置。"
        )

    image_provider, image_model = service.current_image_state()
    st.markdown(
        f"**当前读图模型**　Provider：`{_PROVIDER_LABELS.get(image_provider, '—')}`　"
        f"Model：`{image_model or '—'}`"
    )
    image_tab, text_tab = st.tabs(["读图 / 切题", "讲题 / 知识问答"])
    with image_tab:
        st.caption("直接读取原图，生成题目、数学公式和原图裁块；无网络时提示无法识别。")
        _render_purpose_settings(service, ModelPurpose.IMAGE)
        with st.expander("其他型号的读图状态"):
            st.caption(
                "Kimi K2.6 支持图片输入，但本次试卷的图框定位明显偏移，暂不用于切题。"
                "Qwen3 VL Plus 支持图片输入，但本次试卷第三页把同一题的填空过度拆分，暂不用于切题。"
                "这两个型号仍保留在问答配置中。"
            )
    with text_tab:
        st.caption("用于讲题、知识问答和人工输入的数学内容排版。")
        _render_purpose_settings(service, ModelPurpose.TEXT)


def _render_purpose_settings(service: ProviderSettingsService, purpose: ModelPurpose) -> None:
    """Render independent selections while sharing each provider's saved key."""

    image_mode = purpose is ModelPurpose.IMAGE
    key_prefix = "ai_image_settings" if image_mode else "ai_settings"
    provider_ids = [item for item in ProviderId if list_model_presets(item, purpose=purpose)]
    current_provider = (
        service.current_image_state()[0] if image_mode else service.current_state()[1]
    )
    preferred = current_provider or (ProviderId.QWEN if image_mode else ProviderId.DEEPSEEK)
    selected_provider = st.selectbox(
        "读图服务商" if image_mode else "服务商",
        options=provider_ids,
        format_func=lambda item: _PROVIDER_LABELS[item],
        index=provider_ids.index(preferred) if preferred in provider_ids else 0,
        key=f"{key_prefix}_provider",
    )
    try:
        view = (
            service.image_view(selected_provider) if image_mode else service.view(selected_provider)
        )
    except (AIUnavailableError, ProviderCallError) as exc:
        st.error(safe_settings_error(exc))
        return
    definition = get_provider_definition(selected_provider)
    preset_ids = [
        preset.model_id for preset in list_model_presets(selected_provider, purpose=purpose)
    ]
    current_model_id = view.settings.model_id if view.settings is not None else ""
    current_is_preset = current_model_id in preset_ids
    model_choice = st.selectbox(
        "读图模型" if image_mode else "模型",
        options=preset_ids if image_mode else [*preset_ids, _CUSTOM_MODEL],
        index=(
            preset_ids.index(current_model_id)
            if current_is_preset
            else 0
            if image_mode
            else len(preset_ids)
        ),
        key=f"{key_prefix}_model_{selected_provider.value}",
    )
    if model_choice == _CUSTOM_MODEL:
        model_id = st.text_input(
            "自定义 Model ID",
            value=current_model_id if not current_is_preset else "",
            key=f"{key_prefix}_custom_model_{selected_provider.value}",
        ).strip()
        model_selection = ModelSelectionKind.CUSTOM
    else:
        model_id = model_choice
        model_selection = ModelSelectionKind.PRESET

    profile = get_capability_profile(selected_provider, model_id)
    st.caption(
        "支持图片输入和文字问答。"
        if profile.effective.vision is CapabilitySupport.SUPPORTED
        else "用于文字问答；读图请在「读图 / 切题」中单独选择视觉模型。"
    )
    if view.credential_saved:
        st.success("API Key 已安全保存")
    else:
        st.caption("尚未保存 API Key")

    form_key = f"ai_provider_form_{purpose.value}_{selected_provider.value}"
    with st.form(form_key, clear_on_submit=True):
        api_key = st.text_input(
            "API Key",
            value="",
            type="password",
            help="已保存的 Key 永不回填；留空会保留现有 Key。",
        )
        with st.expander("高级设置"):
            base_url = st.text_input(
                "Base URL",
                value=(
                    view.settings.base_url
                    if view.settings is not None
                    else definition.default_base_url
                ),
            )
        st.caption(
            "测试图片连接会发送一张本地生成的小图，可能产生少量 API 费用。"
            if image_mode
            else "测试连接会发送一次极小的模型请求，可能产生少量 API 费用。"
        )
        buttons = st.columns(2 if image_mode else 3)
        save = (
            False
            if image_mode
            else buttons[0].form_submit_button("保存配置", use_container_width=True)
        )
        save_active = buttons[0 if image_mode else 1].form_submit_button(
            "保存并用于读图" if image_mode else "保存并设为当前",
            type="primary",
            use_container_width=True,
        )
        test = buttons[1 if image_mode else 2].form_submit_button(
            "测试图片连接" if image_mode else "测试连接", use_container_width=True
        )

    if save or save_active or test:
        try:
            provider_settings = service.make_settings(
                provider_id=selected_provider,
                base_url=base_url,
                model_id=model_id,
                model_selection=model_selection,
            )
            if test:
                result = service.test_connection(
                    provider_settings, api_key_override=api_key, purpose=purpose
                )
                st.success(
                    (
                        "✓ 图片请求可以调用\n\n"
                        if image_mode
                        else "✓ API Key 有效　✓ 服务可访问　✓ 当前模型可以调用\n\n"
                    )
                    + f"响应模型：{result.resolved_model}"
                )
            else:
                service.save(
                    provider_settings,
                    new_api_key=api_key,
                    make_active=save_active,
                    purpose=purpose,
                )
                _clear_model_session_state()
                st.success("模型服务配置已安全保存。")
        except (AIUnavailableError, ProviderCallError) as exc:
            st.error(safe_settings_error(exc))
        except Exception as exc:  # UI boundary: never expose raw diagnostics
            LOGGER.error("模型服务设置操作失败：error_type=%s", type(exc).__name__)
            st.error(safe_settings_error(exc))

    if not image_mode and st.button(
        "清除该 Provider 配置",
        key=f"ai_settings_delete_{selected_provider.value}",
        use_container_width=True,
    ):
        try:
            service.delete(selected_provider)
            _clear_model_session_state()
            st.success("该 Provider 的配置与凭据已清除。")
        except (AIUnavailableError, ProviderCallError) as exc:
            st.error(safe_settings_error(exc))
        except Exception as exc:
            LOGGER.error("清除模型服务配置失败：error_type=%s", type(exc).__name__)
            st.error(safe_settings_error(exc))


def _effective_capability_caption(capabilities: object) -> str:
    labels = (
        ("流式", capabilities.streaming),
        ("推理参数", capabilities.reasoning),
        ("原生工具", capabilities.tool_calling),
        ("视觉", capabilities.vision),
    )
    rendered = []
    for label, support in labels:
        value = {
            CapabilitySupport.SUPPORTED: "可用",
            CapabilitySupport.UNSUPPORTED: "当前未实现",
            CapabilitySupport.UNKNOWN: "未知",
        }[support]
        rendered.append(f"{label}：{value}")
    return "Nectivon 当前有效能力｜" + "　".join(rendered)


def _clear_model_session_state() -> None:
    for key in _AI_SESSION_KEYS:
        st.session_state.pop(key, None)
