"""Shared, ordinary-language page-number jump controls."""

from __future__ import annotations

import html
import json
import re

import streamlit as st

_INTEGER_PATTERN = re.compile(r"[0-9]+")

# Keep the page-jump copy together so a future locale switch has one source.
_PAGE_JUMP_LABEL = "跳转到页码"
_PAGE_JUMP_INPUT_LABEL = "输入页码"
_PAGE_JUMP_PLACEHOLDER = "输入需要跳转的页码"
_PAGE_JUMP_SUBMIT_LABEL = "跳转"
_PAGE_JUMP_SUBMIT_HINT = "按回车键提交"


def _page_jump_styles(input_key: str) -> str:
    """Return styles scoped to one keyed page-jump input."""

    input_class = json.dumps(f"st-key-{input_key}", ensure_ascii=False)
    submit_hint = json.dumps(_PAGE_JUMP_SUBMIT_HINT, ensure_ascii=False)
    return f"""
    <style>
    .ekb-page-jump-text {{
        align-items: center;
        display: flex;
        font-size: 1rem;
        justify-content: center;
        min-height: 2.5rem;
        text-align: center;
    }}

    [class~={input_class}] input,
    [data-testid="stForm"]:has([class~={input_class}]) button,
    [data-testid="stForm"]:has([class~={input_class}]) button p {{
        font-size: 1rem;
    }}

    [class~={input_class}] [data-testid="InputInstructions"] > span {{
        display: none;
    }}

    [class~={input_class}] [data-testid="InputInstructions"]::after {{
        content: {submit_hint};
    }}
    </style>
    """


def _centered_text(value: str) -> str:
    """Build the small centered text block used beside the jump input."""

    return f'<div class="ekb-page-jump-text">{html.escape(value)}</div>'


def parse_page_jump(raw_value: str, total_pages: int) -> tuple[int | None, str | None]:
    """Validate one action-only page number without changing navigation state."""

    value = raw_value.strip()
    if not value:
        return None, f"请输入页码。可以输入 1 到 {total_pages} 之间的整数。"
    if _INTEGER_PATTERN.fullmatch(value) is None:
        return None, f"页码只能输入整数。请输入 1 到 {total_pages} 之间的页码。"
    page_number = int(value)
    if page_number < 1 or page_number > total_pages:
        return None, f"没有这一页。请输入 1 到 {total_pages} 之间的页码。"
    return page_number, None


def render_page_jump(*, total_pages: int, key_prefix: str) -> int | None:
    """Render an inline jump action and return a valid submitted page number."""

    form_key = f"{key_prefix}_form"
    input_key = f"{key_prefix}_input"
    st.html(_page_jump_styles(input_key))
    with st.form(form_key, border=False):
        label_column, input_column, total_column, button_column = st.columns(
            [1.15, 2, 1, 0.8], vertical_alignment="bottom"
        )
        label_column.markdown(
            _centered_text(_PAGE_JUMP_LABEL), unsafe_allow_html=True
        )
        raw_value = input_column.text_input(
            _PAGE_JUMP_INPUT_LABEL,
            key=input_key,
            placeholder=_PAGE_JUMP_PLACEHOLDER,
            label_visibility="collapsed",
        )
        total_column.markdown(
            _centered_text(f"共 {total_pages} 页"), unsafe_allow_html=True
        )
        submitted = button_column.form_submit_button(
            _PAGE_JUMP_SUBMIT_LABEL, type="primary", use_container_width=True
        )
    if not submitted:
        return None
    page_number, error = parse_page_jump(raw_value, total_pages)
    if error is not None:
        st.warning(error)
        return None
    return page_number


__all__ = ["parse_page_jump", "render_page_jump"]
