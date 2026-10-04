"""Visual input budget tests (V086-204).

Synthetic Pillow images only - no network, no real page images.
"""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from src.visual_input_budget import (
    MAX_IMAGE_BASE64_CHARS,
    MAX_LONG_EDGE_PX,
    VisualInputBudgetError,
    prepare_page_image,
)


def _png_bytes(width: int, height: int, color=(200, 200, 200)) -> bytes:
    image = Image.new("RGB", (width, height), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_full_page_png_is_downscaled_into_budget() -> None:
    # Noisy content: PNG stays large, JPEG gets small - a realistic scan.
    import random

    random.seed(7)
    width, height = 1240, 1754
    image = Image.new("RGB", (width, height))
    image.putdata(
        [
            (
                random.randint(180, 255),
                random.randint(180, 255),
                random.randint(180, 255),
            )
            for _ in range(width * height)
        ]
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    raw = buffer.getvalue()
    prepared = prepare_page_image(raw)
    assert prepared.media_type == "image/jpeg"
    assert prepared.data_url.startswith("data:image/jpeg;base64,")
    assert prepared.base64_chars <= MAX_IMAGE_BASE64_CHARS
    assert prepared.downscaled
    assert max(prepared.width, prepared.height) <= MAX_LONG_EDGE_PX
    assert prepared.source_width == width
    assert prepared.source_height == height
    # Payload must shrink dramatically versus the raw PNG inline.
    raw_chars = len(base64.b64encode(raw))
    assert prepared.base64_chars < raw_chars * 0.5


def test_small_image_stays_within_budget_without_downscale() -> None:
    raw = _png_bytes(600, 400)
    prepared = prepare_page_image(raw)
    assert prepared.base64_chars <= MAX_IMAGE_BASE64_CHARS
    assert prepared.downscaled is False
    assert prepared.source_bytes == len(raw)


def test_extremely_noisy_page_reaches_budget_via_stepdown() -> None:
    # Random noise compresses badly in JPEG; force the stepdown loop.
    import random

    random.seed(42)
    image = Image.new("RGB", (2400, 3200))
    image.putdata(
        [
            (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
            for _ in range(2400 * 3200)
        ]
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    prepared = prepare_page_image(buffer.getvalue())
    assert prepared.base64_chars <= MAX_IMAGE_BASE64_CHARS
    assert prepared.downscaled


def test_invalid_image_raises_budget_error() -> None:
    with pytest.raises(VisualInputBudgetError):
        prepare_page_image(b"not-an-image")
    with pytest.raises(VisualInputBudgetError):
        prepare_page_image(b"")
