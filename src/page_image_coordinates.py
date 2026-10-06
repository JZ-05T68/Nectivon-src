"""Visible pixel rulers for image localization; original images remain intact."""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageDraw


@dataclass(frozen=True)
class CoordinateImage:
    data: bytes
    width: int
    height: int
    content_left: int
    content_top: int
    content_width: int
    content_height: int


def coordinate_image(original: bytes) -> CoordinateImage:
    """Add external rulers, never drawing over the user-provided page."""

    with Image.open(io.BytesIO(original)) as source:
        page = source.convert("RGB")
    page.thumbnail((2800, 2800), Image.Resampling.LANCZOS)
    margin = 48
    canvas = Image.new("RGB", (page.width + 2 * margin, page.height + 2 * margin), "white")
    canvas.paste(page, (margin, margin))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(
        (margin - 1, margin - 1, margin + page.width, margin + page.height),
        outline="#1976d2",
        width=1,
    )
    for x in range(0, canvas.width, 100):
        draw.line((x, 26, x, 44), fill="#1976d2", width=2)
        draw.text((max(0, x - 10), 10), str(x), fill="#000000")
        draw.line((x, canvas.height - 44, x, canvas.height - 26), fill="#1976d2", width=2)
        draw.text((max(0, x - 10), canvas.height - 20), str(x), fill="#000000")
    for y in range(0, canvas.height, 100):
        draw.line((26, y, 44, y), fill="#1976d2", width=2)
        draw.text((2, max(0, y - 5)), str(y), fill="#000000")
        draw.line((canvas.width - 44, y, canvas.width - 26, y), fill="#1976d2", width=2)
        draw.text((canvas.width - 25, max(0, y - 5)), str(y), fill="#000000")
    output = io.BytesIO()
    canvas.save(output, format="PNG")
    return CoordinateImage(
        output.getvalue(), canvas.width, canvas.height, margin, margin, page.width, page.height
    )


def normalized_payload(payload: dict, image: CoordinateImage) -> dict:
    """Convert explicitly declared pixel boxes to original-page coordinates."""

    if payload.get("coordinate_space") != "pixels":
        raise ValueError("AI 必须明确返回带标尺图片的像素坐标，不能混用归一化坐标。")

    def convert(value: object) -> object:
        if not isinstance(value, list) or len(value) != 4:
            return value
        try:
            x1, y1, x2, y2 = (float(item) for item in value)
        except (ValueError, TypeError):
            return value
        if not (
            image.content_left <= x1 < x2 <= image.content_left + image.content_width
            and image.content_top <= y1 < y2 <= image.content_top + image.content_height
        ):
            return None
        return [
            (x1 - image.content_left) / image.content_width * 1000,
            (y1 - image.content_top) / image.content_height * 1000,
            (x2 - image.content_left) / image.content_width * 1000,
            (y2 - image.content_top) / image.content_height * 1000,
        ]

    def visit(value: object) -> object:
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, dict):
            return {
                key: convert(item)
                if key in {"bbox", "image_bbox", "question_bbox"}
                else visit(item)
                for key, item in value.items()
            }
        return value

    return visit(payload)
