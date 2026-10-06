"""Validated references and local crops of figures in original question pages."""

from __future__ import annotations

import hashlib
import io
import logging
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image

REGION_ROLES = frozenset({"stem", "shared", "option", "question"})


def normalize_region(value: object) -> dict | None:
    """Accept only finite, ordered 0..1000 page coordinates and known roles."""

    if not isinstance(value, dict):
        return None
    role = value.get("role", "stem")
    bbox = value.get("bbox")
    if not isinstance(role, str) or role not in REGION_ROLES:
        return None
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    if any(type(item) not in (int, float) or not math.isfinite(item) for item in bbox):
        return None
    x1, y1, x2, y2 = bbox
    if not (0 <= x1 < x2 <= 1000 and 0 <= y1 < y2 <= 1000):
        return None
    scope, identifier = value.get("crop_scope"), value.get("region_id")
    committed = all(isinstance(item, str) and len(item) == 32
                    and all(c in "0123456789abcdef" for c in item)
                    for item in (scope, identifier))
    # Human bounds are validated in original pixels by the per-question store.
    if not committed and (x2 - x1 < 3 or y2 - y1 < 3):
        return None
    label = str(value.get("option_label", "")).strip()
    if role == "option" and (len(label) != 1 or label not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        return None
    result = {
        "role": role, "bbox": [float(item) for item in bbox],
        "option_label": label if role == "option" else "",
        "description": str(value.get("description", "")).strip()[:240],
    }
    clip = value.get("clip_bbox")
    if (isinstance(clip, list) and len(clip) == 4
            and all(type(v) in (int, float) and math.isfinite(v) for v in clip)
            and 0 <= clip[0] <= x1 < x2 <= clip[2] <= 1000
            and 0 <= clip[1] <= y1 < y2 <= clip[3] <= 1000):
        result["clip_bbox"] = [float(v) for v in clip]
    # Provenance is assigned by the caller after recognition, not by the model.
    if type(value.get("page_id")) is int:
        result["page_id"] = value["page_id"]
    digest = value.get("image_sha256")
    if (isinstance(digest, str) and len(digest) == 64
            and all(c in "0123456789abcdef" for c in digest)):
        result["image_sha256"] = digest
    if committed:
        result.update(crop_scope=scope, region_id=identifier)
        bounds = value.get("crop_bounds")
        if isinstance(bounds, list) and len(bounds) == 4 and all(type(v) is int for v in bounds):
            result["crop_bounds"] = bounds.copy()
    return result


def normalize_regions(values: object) -> list[dict]:
    """Drop unusable/duplicate region proposals without losing question text."""

    if not isinstance(values, list):
        return []
    result: list[dict] = []
    seen: set[tuple] = set()
    for value in values[:40]:
        region = normalize_region(value)
        if region is None:
            continue
        key = (region["role"], tuple(region["bbox"]), region["option_label"],
               region.get("page_id"), region.get("image_sha256"), region.get("region_id"))
        if key not in seen:
            seen.add(key)
            result.append(region)
    # Some models also propose an overview of the option grid as a stem
    # figure. Do not display those same options twice or associate the grid
    # with the common question stem.
    options = [r for r in result if r["role"] == "option"]
    # The whitespace halfway to another option is a hard crop boundary. This
    # prevents stroke completion from absorbing a diagram in the next row.
    for option in options:
        x1, y1, x2, y2 = option["bbox"]
        clip = list(option.get("clip_bbox", [0., 0., 1000., 1000.]))
        for other in options:
            if (other is option or other.get("page_id") != option.get("page_id")
                    or other.get("image_sha256") != option.get("image_sha256")):
                continue
            a1, b1, a2, b2 = other["bbox"]
            row_overlap = max(0, min(y2, b2) - max(y1, b1))
            col_overlap = max(0, min(x2, a2) - max(x1, a1))
            if row_overlap >= .5 * min(y2 - y1, b2 - b1):
                if a1 >= x2:
                    clip[2] = min(clip[2], (x2 + a1) / 2)
                elif a2 <= x1:
                    clip[0] = max(clip[0], (a2 + x1) / 2)
            if col_overlap >= .5 * min(x2 - x1, a2 - a1):
                if b1 >= y2:
                    clip[3] = min(clip[3], (y2 + b1) / 2)
                elif b2 <= y1:
                    clip[1] = max(clip[1], (b2 + y1) / 2)
        option["clip_bbox"] = clip
    def includes_option(region: dict, option: dict) -> bool:
        if (region.get("page_id") != option.get("page_id")
                or region.get("image_sha256") != option.get("image_sha256")):
            return False
        x1, y1, x2, y2 = region["bbox"]
        a1, b1, a2, b2 = option["bbox"]
        overlap = max(0, min(x2, a2) - max(x1, a1)) * max(0, min(y2, b2) - max(y1, b1))
        return overlap >= 0.9 * (a2 - a1) * (b2 - b1)

    return [r for r in result if not (
        r["role"] in ("stem", "shared")
        and not r.get("region_id")
        and sum(includes_option(r, option) for option in options) >= 2
    )]


def bind_regions(values: list[dict], *, page_id: int, image_bytes: bytes) -> list[dict]:
    """Pin proposed geometry to the actual caller-supplied page and image."""

    digest = hashlib.sha256(image_bytes).hexdigest()
    return [
        {**{k: v for k, v in region.items() if k not in ("crop_scope", "region_id", "crop_bounds")},
         "page_id": page_id, "image_sha256": digest}
        for region in normalize_regions(values)
    ]


def crop_region(
    image_path: Path | str, region: dict, *, original: bool = False,
) -> bytes | None:
    """Crop the shared display revision using bounds verified on original pixels."""

    normalized = normalize_region(region)
    if normalized is None:
        return None
    path = Path(image_path)
    try:
        if normalized.get("crop_scope"):
            from src.question_region_store import QuestionRegionStore

            store = QuestionRegionStore(path, scope=normalized["crop_scope"])
            return store.crop(normalized["region_id"], original=original, fallback=normalized)
        stat = path.stat()
        display = path
        if not original:
            from src.question_image_store import QuestionImageError, question_display_path

            try:
                display = question_display_path(path)
            except QuestionImageError:
                logging.getLogger(__name__).warning("题目显示图不可用，改用原图：%s", path)
        return _cached_crop(
            str(path.resolve()), stat.st_mtime_ns, stat.st_size,
            tuple(normalized["bbox"]), str(normalized.get("image_sha256", "")),
            normalized["role"],
            tuple(normalized.get("clip_bbox", [])),
            str(display.resolve()),
        )
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=128)
def _cached_crop(
    path: str, modified_ns: int, size: int, bbox: tuple[float, ...], digest: str, role: str,
    clip_bbox: tuple[float, ...], display_path: str,
) -> bytes | None:
    bounds = _cached_bounds(path, modified_ns, size, bbox, digest, role, clip_bbox)
    if bounds is None:
        return None
    with Image.open(path) as source, Image.open(display_path) as display:
        if display.size != source.size:
            return None
        cropped = display.crop(bounds).convert("RGB")
    output = io.BytesIO()
    cropped.save(output, format="PNG")
    return output.getvalue()


def region_pixel_bounds(image_path: Path | str, region: dict) -> tuple[int, int, int, int] | None:
    """Expose exactly the bounds used by an original figure crop for human editing."""

    normalized = normalize_region(region)
    if normalized is None:
        return None
    path = Path(image_path)
    try:
        stat = path.stat()
        return _cached_bounds(
            str(path.resolve()), stat.st_mtime_ns, stat.st_size,
            tuple(normalized["bbox"]), str(normalized.get("image_sha256", "")),
            normalized["role"], tuple(normalized.get("clip_bbox", [])),
        )
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=128)
def _cached_bounds(
    path: str, modified_ns: int, size: int, bbox: tuple[float, ...], digest: str, role: str,
    clip_bbox: tuple[float, ...],
) -> tuple[int, int, int, int] | None:
    source = Path(path).read_bytes()
    if digest and hashlib.sha256(source).hexdigest() != digest:
        return None
    with Image.open(io.BytesIO(source)) as image:
        width, height = image.size
        # Small outward padding preserves strokes touching the predicted box.
        left = max(0, math.floor((bbox[0] - 2) * width / 1000))
        top = max(0, math.floor((bbox[1] - 2) * height / 1000))
        right = min(width, math.ceil((bbox[2] + 2) * width / 1000))
        bottom = min(height, math.ceil((bbox[3] + 2) * height / 1000))
        if right - left < 4 or bottom - top < 4:
            return None
        proposed_bounds = (left, top, right, bottom)
        isolated_figure = role in ("stem", "shared")
        left, top, right, bottom = _complete_ink_boundary(
            image, proposed_bounds, horizontal_fraction=.35 if isolated_figure else .08,
        )
        if role != "question":
            from src.figure_crop_boundary import figure_bounds

            left, top, right, bottom = figure_bounds(
                image, (left, top, right, bottom), anchor_bounds=proposed_bounds,
                horizontal_margin=round(width * .35) if isolated_figure else None,
            )
        if clip_bbox and not isolated_figure:
            # Boundary completion may recover labels, but must not reach the
            # following question even when handwritten strokes connect them.
            left = max(left, math.floor(clip_bbox[0] * width / 1000))
            top = max(top, math.floor(clip_bbox[1] * height / 1000))
            right = min(right, math.ceil(clip_bbox[2] * width / 1000))
            bottom = min(bottom, math.ceil(clip_bbox[3] * height / 1000))
        if isolated_figure:
            # Follow horizontal axes/frames, but bound vertical growth so a
            # student's connecting pen stroke cannot pull in the next row.
            margin_y = round(height * .02)
            top = max(top, math.floor(bbox[1] * height / 1000) - margin_y)
            bottom = min(bottom, math.ceil(bbox[3] * height / 1000) + margin_y)
            if clip_bbox:
                from src.figure_crop_boundary import has_sparse_bottom_gutter

                clip_bottom = math.ceil(clip_bbox[3] * height / 1000)
                if has_sparse_bottom_gutter(image, proposed_bounds, clip_bottom):
                    bottom = min(bottom, clip_bottom)
        return (left, top, right, bottom)


def _complete_ink_boundary(
    image: Image.Image, bounds: tuple[int, int, int, int],
    *, horizontal_fraction: float = .08,
) -> tuple[int, int, int, int]:
    """Follow clipped strokes to nearby white margins, without reading text.

    This bounded pixel check preserves arrowheads, triangle vertices and frame
    borders touching a predicted box. It does no character recognition.
    """

    gray = image.convert("L")
    left, top, right, bottom = bounds
    initial = bounds
    limit_x = max(4, int(image.width * horizontal_fraction))
    limit_y = max(4, int(image.height * .035))

    def dark(strip: tuple[int, int, int, int]) -> bool:
        histogram = gray.crop(strip).histogram()
        return sum(histogram[:160]) >= 2

    for _ in range(max(limit_x, limit_y)):
        changed = False
        if left > 0 and initial[0] - left < limit_x and dark((left, top, left + 1, bottom)):
            left -= 1
            changed = True
        if top > 0 and initial[1] - top < limit_y and dark((left, top, right, top + 1)):
            top -= 1
            changed = True
        if (right < image.width and right - initial[2] < limit_x
                and dark((right - 1, top, right, bottom))):
            right += 1
            changed = True
        if (bottom < image.height and bottom - initial[3] < limit_y
                and dark((left, bottom - 1, right, bottom))):
            bottom += 1
            changed = True
        if not changed:
            break
    return (max(0, left - 3), max(0, top - 3),
            min(image.width, right + 3), min(image.height, bottom + 3))
