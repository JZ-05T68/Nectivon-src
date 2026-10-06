"""Validated image content for OpenAI-compatible vendor adapters.

This codec operates on supplied image bytes only; it never extracts text.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any


def image_user_content(prompt: str, image_base64: str) -> list[dict[str, Any]]:
    """Build text plus a correctly typed PNG/JPEG Data URL, never OCR text."""

    if image_base64.startswith("data:"):
        header, separator, image_base64 = image_base64.partition(",")
        if not separator or header not in {"data:image/png;base64", "data:image/jpeg;base64"}:
            raise ValueError("图片格式必须为 PNG 或 JPEG Data URL")
    try:
        raw = base64.b64decode(image_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("图片内容必须为有效的 Base64 PNG/JPEG") from exc
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif raw.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    else:
        raise ValueError("图片格式必须为 PNG 或 JPEG")
    return [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_base64}"}},
    ]
