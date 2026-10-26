"""Low-typing math correction widgets for question editing (P1-3B / P0-2).

One shared editor, used by the candidate review cards and the first-layer
question cards:

- ``render_math_snippet_editor`` — the student types a small fragment such
  as ``omega_n``; it is converted to LaTeX locally, previewed with the
  existing KaTeX read renderer, and can be appended to the target text.
- ``render_math_find_replace`` — fix one wrong symbol via 查找/替换; the
  replacement goes through the same converter so ``Delta p`` becomes
  ``$\\Delta p$`` without retyping the formula.
- ``render_stem_live_preview`` — live KaTeX preview of the whole editing
  stem so a correction is visible before saving.

Streamlit forbids modifying a widget's session state after the widget is
instantiated in the same run, so every mutation is staged through a
``__pending_new`` key and applied by :func:`apply_pending_text` BEFORE the
target text widget is created on the next run.

Ambiguous input never resolves silently: the editor surfaces every defensible
reading and lets the user choose (P1-3C).
"""

from __future__ import annotations

import logging

import streamlit as st

from src.math_display import normalize_math_delimiters
from src.math_input_service import (
    SNIPPET_SYMBOLS,
    MathConversion,
    plain_math_to_latex,
    wrap_inline,
)

LOGGER = logging.getLogger(__name__)

PENDING_SUFFIX = "__pending_new"


def apply_pending_text(widget_key: str, default: str) -> None:
    """Stage-and-apply pattern: call right BEFORE creating a text widget.

    Widgets produced by the snippet editor / find-replace never touch a
    live widget's state; they park the next value here instead.
    """

    pending = st.session_state.pop(f"{widget_key}{PENDING_SUFFIX}", None)
    if pending is not None:
        st.session_state[widget_key] = str(pending)


def render_stem_live_preview(text: str, *, caption: str = "实时预览（KaTeX）") -> None:
    """Render the in-progress text exactly as the read surface will show it."""

    value = str(text or "")
    if not value.strip():
        return
    try:
        st.caption(caption)
        st.markdown(normalize_math_delimiters(value))
    except Exception:  # noqa: BLE001 - preview must never block editing
        LOGGER.warning("数学实时预览失败，显示原文", exc_info=True)
        st.text(value)


def _render_symbol_picker(key: str) -> None:
    """Append LaTeX snippets for common STEM notation without typing."""

    with st.expander("符号选择器（点按插入，无需输入 LaTeX）"):
        st.caption("点击符号会追加到当前数学片段输入框的末尾。")
        for row_start in range(0, len(SNIPPET_SYMBOLS), 8):
            row = SNIPPET_SYMBOLS[row_start : row_start + 8]
            columns = st.columns(len(row))
            pending_key = f"{key}_snippet{PENDING_SUFFIX}"
            for column, (label, latex) in zip(columns, row, strict=True):
                if column.button(label, key=f"{key}_sym_{row_start}_{label}"):
                    current = str(st.session_state.get(f"{key}_snippet", "") or "")
                    st.session_state[pending_key] = current + latex
                    st.rerun()


def _choose_conversion(conversion: MathConversion, key: str) -> str:
    """Return the confirmed LaTeX, asking the user when it is ambiguous."""

    if not conversion.ambiguous:
        return conversion.latex
    return str(
        st.radio(
            "该输入存在两种下标读法，请确认数学含义：",
            conversion.candidates,
            index=0,
            key=f"{key}_ambiguity",
        )
    )


def render_math_snippet_editor(key: str) -> str | None:
    """One math fragment editor; return wrapped inline LaTeX on demand.

    Returns ``$...$``-wrapped LaTeX when the user clicks 插入, otherwise
    ``None``.  The caller stages the insertion into its own target widget
    through :func:`apply_pending_text` (see module docstring).
    """

    snippet_key = f"{key}_snippet"
    apply_pending_text(snippet_key, "")
    if snippet_key not in st.session_state:
        st.session_state[snippet_key] = ""
    snippet = st.text_input(
        "数学片段（输入后按 Enter 生效；如 omega_n、K_p、3.2e-3、sqrt(p1-p2)；也可直接粘贴 LaTeX）",
        key=snippet_key,
        placeholder="例：omega_n 或 \eta_1 或 3.2×10^-3 m^2",
    )
    _render_symbol_picker(key)
    if not snippet.strip():
        return None
    conversion = plain_math_to_latex(snippet)
    for note in conversion.notes:
        st.caption(f"ℹ️ {note}")
    latex = _choose_conversion(conversion, key)
    left, right = st.columns([3, 2])
    with left:
        st.caption("转换结果（自动生成，可复制）")
        st.code(latex, language="latex")
    with right:
        st.caption("渲染预览")
        st.markdown(wrap_inline(latex))
    if st.button("插入到编辑框末尾", key=f"{key}_insert", type="primary"):
        return wrap_inline(latex)
    return None


def render_math_find_replace(
    key: str,
    *,
    apply_to: str,
    widget_key: str,
) -> None:
    """Symbol-level fix: find one wrong piece, replace with converted math.

    ``apply_to`` is the current editable text; ``widget_key`` is the
    ``st.text_area`` it lives in.  A confirmed replacement is parked in
    session state and applied by :func:`apply_pending_text` on the next
    run — the existing 保存 button stays authoritative.
    """

    if not apply_to.strip():
        return
    with st.expander("局部替换（只改错的符号，不重打公式）", expanded=False):
        find_value = st.text_input(
            "查找（原样文字，如 wn、3.2x10-3；输入后按 Enter）",
            key=f"{key}_find",
        )
        replace_value = st.text_input(
            "替换为（可输 omega_n，会自动转 LaTeX；也可直接输 LaTeX；按 Enter）",
            key=f"{key}_replace",
        )
        if not find_value.strip() or not replace_value.strip():
            return
        conversion = plain_math_to_latex(replace_value)
        for note in conversion.notes:
            st.caption(f"ℹ️ {note}")
        latex = _choose_conversion(conversion, f"{key}_fr")
        replacement = wrap_inline(latex)
        occurrences = apply_to.count(find_value)
        if occurrences == 0:
            st.caption("编辑框当前内容中没有找到要查找的文字。")
            return
        st.caption(f"找到 {occurrences} 处；替换后：")
        preview = apply_to.replace(find_value, replacement, 1)
        st.markdown(normalize_math_delimiters(preview))
        col_one, col_all = st.columns(2)
        pending_key = f"{widget_key}{PENDING_SUFFIX}"
        if col_one.button("替换第 1 处", key=f"{key}_replace_first", type="primary"):
            st.session_state[pending_key] = preview
        elif col_all.button(
            f"全部替换（{occurrences} 处）", key=f"{key}_replace_all"
        ):
            st.session_state[pending_key] = apply_to.replace(find_value, replacement)
        if pending_key in st.session_state:
            st.toast("已应用替换，请核对后保存。")
