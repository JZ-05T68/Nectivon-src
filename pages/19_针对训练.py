"""Targeted Training & One-to-Many Workflow - Phase 1: Profile Configuration."""

from __future__ import annotations

import logging

import streamlit as st

from src import __version__
from src.runtime import application_training_profile_service
from src.training_profile_ui import render_training_profile_page
from src.workspace_ui import render_workspace

LOGGER = logging.getLogger(__name__)

st.set_page_config(
    page_title=f"针对训练 · Nectivon v{__version__}",
    page_icon="🎯",
    layout="wide",
)
render_workspace("pages/19_针对训练.py")

try:
    service = application_training_profile_service()
except Exception as exc:
    LOGGER.exception("初始化针对训练服务失败")
    st.error(f"初始化服务失败：{exc}")
    st.stop()

render_training_profile_page(service)
