"""Guard the shared workspace visual system against accidental drift."""

from __future__ import annotations

import re
from pathlib import Path

_CSS_PATH = Path(__file__).resolve().parents[1] / "src" / "workspace.css"
_HOME_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _css() -> str:
    return _CSS_PATH.read_text(encoding="utf-8")


def _relative_luminance(hex_color: str) -> float:
    channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92
        if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(first: str, second: str) -> float:
    lighter, darker = sorted(
        (_relative_luminance(first), _relative_luminance(second)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def test_workspace_uses_eight_point_spacing_tokens() -> None:
    css = _css()

    for index, pixels in enumerate((4, 8, 16, 24, 32, 48), start=1):
        assert f"--ekb-space-{index}:{pixels}px" in css


def test_workspace_defines_complete_interaction_states() -> None:
    css = _css()

    assert ":hover:not(:disabled)" in css
    assert ":active:not(:disabled)" in css
    assert "button:disabled" in css
    assert ":focus-visible" in css
    assert "cubic-bezier(.16,1,.3,1)" in css
    assert "--ekb-duration-fast:160ms" in css
    assert "--ekb-duration-slow:260ms" in css
    assert "@media (prefers-reduced-motion:reduce)" in css


def test_workspace_keeps_readable_type_and_loading_feedback() -> None:
    css = _css()

    assert '"Segoe UI"' in css
    assert '"PingFang SC"' in css
    assert '"Microsoft YaHei"' in css
    assert "max-width:68ch" in css
    assert "line-height:1.65" in css
    assert '[data-testid="stSkeleton"]' in css
    assert "@keyframes ekb-shimmer" in css
    assert "@keyframes ekb-media-enter" in css


def test_workspace_primary_and_body_text_meet_wcag_aa() -> None:
    assert _contrast_ratio("#ffffff", "#167d65") >= 4.5
    assert _contrast_ratio("#20312f", "#f6f8f7") >= 4.5
    assert _contrast_ratio("#53665d", "#f6f8f7") >= 4.5


def test_workspace_css_has_balanced_blocks() -> None:
    css_without_comments = re.sub(r"/\*.*?\*/", "", _css(), flags=re.DOTALL)

    assert css_without_comments.count("{") == css_without_comments.count("}")


def test_home_uses_quiet_knowledge_flow_instead_of_ai_hero() -> None:
    css = _css()
    home = _HOME_PATH.read_text(encoding="utf-8")

    assert "ekb-knowledge-flow" in home
    assert all(label in home for label in ("知识", "上下文", "提问"))
    assert "ekb-hero" not in home
    assert ".ekb-knowledge-flow" in css
    assert ".ekb-file-card" in css
    assert ".ekb-hero" not in css
