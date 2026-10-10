"""Offline component wrapper for the adapted, locally bundled star-vault renderer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import streamlit.components.v1 as components

_COMPONENT = components.declare_component(
    "nectivon_knowledge_starmap",
    path=str(Path(__file__).parent / "components" / "knowledge_starmap"),
)


def render_starmap(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Render a transient projection; return a selection, never persisted browser data."""

    revision = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return _COMPONENT(payload=payload, revision=revision, key="knowledge_starmap", default=None)
