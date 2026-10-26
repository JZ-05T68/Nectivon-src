"""Install optional browser dictation without changing existing widget contracts."""

from functools import lru_cache
from pathlib import Path

import streamlit.components.v1 as components

_ASSETS = Path(__file__).parent


@lru_cache(maxsize=4)
def _voice_assets(css_mtime: int, js_mtime: int) -> str:
    """Cache only trusted, shipped assets; never interpolate user input."""

    css = (_ASSETS / "voice_input.css").read_text(encoding="utf-8")
    script = (_ASSETS / "voice_input.js").read_text(encoding="utf-8")
    return (
        '<span hidden data-nectivon-voice-installer="true"></span>'
        f"<style>{css}</style><script>{script}</script>"
    )


def render_voice_input() -> None:
    """Attach microphone buttons to current and subsequently rendered text fields.

    ``components.html`` (not ``st.html``): the dictation script must run and
    reach the PARENT document's widgets; Streamlit 1.59 ``st.html`` renders
    the markup but does not execute the script on the main document
    (round-2 regression found by visual audit).
    """

    components.html(
        _voice_assets(
            (_ASSETS / "voice_input.css").stat().st_mtime_ns,
            (_ASSETS / "voice_input.js").stat().st_mtime_ns,
        ),
        height=0,
    )
