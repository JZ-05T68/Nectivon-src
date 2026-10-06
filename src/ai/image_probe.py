"""Small, locally generated image for an explicit visual API connection test."""

from __future__ import annotations

import base64
import io

from PIL import Image


def image_connection_probe() -> str:
    """Return a red/blue PNG without sending any user document or OCR text."""

    image = Image.new("RGB", (128, 64), "red")
    image.paste("blue", (64, 0, 128, 64))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")
