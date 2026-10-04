"""Shared user-facing states for optional local AI features."""

from __future__ import annotations

import streamlit as st

from src.ai.provider_settings_service import AI_UNCONFIGURED_MESSAGE

__all__ = ["render_ai_unconfigured_notice"]


def render_ai_unconfigured_notice() -> None:
    """Render the single product message used by all unconfigured AI entries."""

    st.warning(AI_UNCONFIGURED_MESSAGE)
    if st.button("⚙️ 前往设置", use_container_width=True):
        st.switch_page("pages/13_运行说明.py")
