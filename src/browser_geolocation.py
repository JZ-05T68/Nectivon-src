"""Local browser geolocation component for city-level profile assistance.

The component asks the browser for permission and resolves coordinates against
the bundled city-centroid dataset entirely inside the browser.  Its return
value contains only province and city names; raw coordinates never cross the
component boundary and are never stored by Nectivon.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from streamlit.components.v1 import declare_component

from src.training_profile_data import get_city_centroids_for_browser

_COMPONENT_DIR = Path(__file__).resolve().parent / "components" / "browser_geolocation"
_browser_geolocation = declare_component("browser_geolocation", path=_COMPONENT_DIR)


def render_city_geolocation_button(*, key: str) -> dict[str, Any] | None:
    """Render one direct geolocation button and return a city-only result."""

    value = _browser_geolocation(
        cities=get_city_centroids_for_browser(),
        button_label="定位当前城市 📍",
        pending_label="正在定位…",
        key=key,
        default=None,
        tab_index=0,
    )
    return value if isinstance(value, dict) else None
