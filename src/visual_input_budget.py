"""Visual input budget for stage-2 page image requests (V086-204 fix).

Wave-1 testing showed a full-page 150-dpi PNG inlined as base64 producing a
~793K-character request that failed at the transport layer on every attempt.
This module turns that unbounded inline into a measured, capped budget:

- the page image is decoded and re-encoded as JPEG with a bounded long edge,
- if the encoded payload still exceeds the char budget, the long edge and
  JPEG quality are reduced stepwise (bounded loop, readable floor),
- the caller receives the data-URL media type plus size telemetry for
  redaction-safe logging (sizes only - never image content).

The goal is legibility retention (formulas and handwriting stay readable),
not maximal compression.
"""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass
from typing import Final

from PIL import Image

LOGGER = logging.getLogger(__name__)

#: Longest allowed image side on the first encoding attempt. A 150-dpi A4
#: page is ~1240x1754; 1600 keeps formula strokes legible while cutting the
#: payload several-fold versus raw PNG.
MAX_LONG_EDGE_PX: Final[int] = 1600
#: JPEG quality on the first encoding attempt.
BASE_JPEG_QUALITY: Final[int] = 82
#: Long-edge floor; below this handwriting/formulas stop being reliable.
MIN_LONG_EDGE_PX: Final[int] = 640
#: Quality floor for the stepdown loop.
MIN_JPEG_QUALITY: Final[int] = 60
#: Maximum base64 characters accepted for one vision request. Roughly 900 KB
#: of binary; conservative against provider request-size limits and upload
#: timeouts on domestic networks.
MAX_IMAGE_BASE64_CHARS: Final[int] = 900_000
#: Hard bound for the stepdown loop (defensive; the arithmetic converges).
MAX_PREPARE_ATTEMPTS: Final[int] = 8


class VisualInputBudgetError(ValueError):
    """The page image cannot be prepared within the visual input budget."""


@dataclass(frozen=True, slots=True)
class PreparedPageImage:
    """A budget-compliant image payload plus redaction-safe telemetry."""

    data_url: str
    media_type: str
    base64_chars: int
    width: int
    height: int
    source_width: int
    source_height: int
    source_bytes: int
    jpeg_quality: int
    downscaled: bool


def prepare_page_image(
    png_bytes: bytes, *, max_long_edge: int = MAX_LONG_EDGE_PX,
    jpeg_quality: int = BASE_JPEG_QUALITY,
    max_base64_chars: int = MAX_IMAGE_BASE64_CHARS,
    preserve_dimensions: bool = False,
) -> PreparedPageImage:
    """Encode a page image within the visual input budget.

    Raises :class:`VisualInputBudgetError` when the image cannot be decoded
    or cannot fit the budget even at the readable floor.
    """

    if not png_bytes:
        raise VisualInputBudgetError("页面图片内容为空。")
    try:
        with Image.open(io.BytesIO(png_bytes)) as source:
            source.load()
            rgb = source.convert("RGB")
    except Exception as exc:
        raise VisualInputBudgetError(f"页面图片无法解码：{type(exc).__name__}") from exc
    source_width, source_height = rgb.size
    if source_width <= 0 or source_height <= 0:
        raise VisualInputBudgetError("页面图片尺寸无效。")

    long_edge = min(max_long_edge, max(source_width, source_height))
    quality = jpeg_quality
    for attempt in range(MAX_PREPARE_ATTEMPTS):
        encoded = _encode_jpeg(rgb, long_edge, quality)
        base64_chars = len(base64.b64encode(encoded))
        if base64_chars <= max_base64_chars:
            data_url = f"data:image/jpeg;base64,{base64.b64encode(encoded).decode('ascii')}"
            prepared = PreparedPageImage(
                data_url=data_url,
                media_type="image/jpeg",
                base64_chars=base64_chars,
                width=long_edge if source_width >= source_height else int(
                    long_edge * source_width / source_height
                ),
                height=long_edge if source_height >= source_width else int(
                    long_edge * source_height / source_width
                ),
                source_width=source_width,
                source_height=source_height,
                source_bytes=len(png_bytes),
                jpeg_quality=quality,
                downscaled=long_edge < max(source_width, source_height)
                or quality < BASE_JPEG_QUALITY,
            )
            LOGGER.info(
                "视觉输入预算：source=%sx%s px / %s bytes -> sent=%sx%s px "
                "jpeg q=%s base64_chars=%s attempts=%s",
                source_width,
                source_height,
                len(png_bytes),
                prepared.width,
                prepared.height,
                quality,
                base64_chars,
                attempt + 1,
            )
            return prepared
        # Still over budget: shrink the readable side stepwise first, then
        # degrade quality, never below the legibility floor.
        if not preserve_dimensions and long_edge > MIN_LONG_EDGE_PX:
            long_edge = max(MIN_LONG_EDGE_PX, int(long_edge * 0.75))
        elif quality > MIN_JPEG_QUALITY:
            quality = max(MIN_JPEG_QUALITY, quality - 8)
        else:
            break
    raise VisualInputBudgetError(
        "页面图片在可读性下限内仍超出视觉请求预算，无法发送。"
    )


def _encode_jpeg(image: Image.Image, long_edge: int, quality: int) -> bytes:
    working = image
    width, height = image.size
    current_long = max(width, height)
    if current_long > long_edge:
        scale = long_edge / current_long
        working = image.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.LANCZOS,
        )
    buffer = io.BytesIO()
    working.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()
