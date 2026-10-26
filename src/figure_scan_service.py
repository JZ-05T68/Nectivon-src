"""Deterministic white-paper/black-ink cleanup and non-OCR prose detection.

Illumination estimation, local binarization and conservative paper-grain
rejection retain native pixel coordinates. Nothing generates, bridges or
redraws engineering structures; original photographs remain immutable.
"""

from __future__ import annotations

import io
import logging
from functools import lru_cache

import numpy as np
from PIL import Image, ImageFilter

#: Rows of ink whose profile repeats with this regularity → prose, not a
#: diagram.  Diagrams (circuits, curves) show irregular row energy.
_TEXT_LINE_REGULARITY = 0.62
_MIN_TEXT_LINES = 4
CLEANUP_VERSION = "engineering-bw-v3.1"


def _otsu_threshold(array: np.ndarray) -> float:
    """Separate faint screenshot strokes from an already uniform white paper."""

    histogram = np.bincount(array.astype(np.uint8).ravel(), minlength=256).astype(np.float64)
    weights = histogram.cumsum()
    sums = (histogram * np.arange(256)).cumsum()
    denominator = weights * (weights[-1] - weights)
    variance = np.divide((sums[-1] * weights - sums * weights[-1])**2, denominator,
                         out=np.zeros(256), where=denominator > 0)
    return float(variance.argmax())


def _box_mean(array: np.ndarray, radius: int) -> np.ndarray:
    """Compute native-resolution local statistics without an OCR dependency."""

    width = radius * 2 + 1
    padded = np.pad(array, radius, mode="edge")
    integral = np.pad(padded, ((1, 0), (1, 0))).cumsum(0, dtype=np.float64).cumsum(1)
    return ((integral[width:, width:] - integral[:-width, width:]
             - integral[width:, :-width] + integral[:-width, :-width]) / width**2)


def _strip_weak_grain(ink: np.ndarray, normalized: np.ndarray) -> np.ndarray:
    """Drop only tiny, low-contrast paper islands; retain dark dots and thin lines.

    Run-length components avoid an extra CV dependency. A one-pixel black
    mathematical point survives; long one-pixel strokes survive regardless of
    area. No surviving component is eroded, dilated, joined or redrawn.
    """

    parents: list[int] = []
    runs: list[tuple[int, int, int, int]] = []
    previous: list[tuple[int, int, int]] = []

    def root(identifier: int) -> int:
        while parents[identifier] != identifier:
            parents[identifier] = parents[parents[identifier]]
            identifier = parents[identifier]
        return identifier

    for y, row in enumerate(ink):
        edges = np.flatnonzero(np.diff(np.pad(row.astype(np.int8), (1, 1))))
        current = []
        cursor = 0
        for left, right in edges.reshape(-1, 2):
            left, right = int(left), int(right)
            identifier = len(parents)
            parents.append(identifier)
            while cursor < len(previous) and previous[cursor][1] < left:
                cursor += 1
            for a, _b, earlier in previous[cursor:]:
                if a > right:
                    break
                parents[root(earlier)] = root(identifier)
            current.append((left, right, identifier))
            runs.append((y, left, right, identifier))
        previous = current
    stats: dict[int, list[float]] = {}
    for y, left, right, identifier in runs:
        key = root(identifier)
        dark = float(normalized[y, left:right].min())
        if key not in stats:
            stats[key] = [0, left, right, y, y, dark]
        item = stats[key]
        item[0] += right - left
        item[1:] = [min(item[1], left), max(item[2], right), min(item[3], y),
                    max(item[4], y), min(item[5], dark)]
    weak = {key for key, (area, x1, x2, y1, y2, dark) in stats.items()
            if area < 14 and x2 - x1 < 9 and y2 - y1 < 9 and dark > 100}
    result = ink.copy()
    for y, left, right, identifier in runs:
        if root(identifier) in weak:
            result[y, left:right] = False
    return result


def _load(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as image:
        return image.convert("L")


def looks_like_text_block(data: bytes) -> bool:
    """True when the crop's ink layout reads as stacked text lines.

    Heuristic: binarize with a DARK-ink threshold (printed strokes; photo
    bleed-through stays above it), take the row-ink profile, find line
    bands, and check whether >=4 bands repeat with a similar pitch.  A
    hydraulic circuit or response curve produces bands of wildly different
    heights (or one tall connected band) and fails the regularity test.
    Calibrated on round-2 real crops: prose bands give pitch CV <= 0.40
    while figures give >= 0.77 or a single tall band.
    """

    gray = _load(data)
    gray.thumbnail((900, 900))
    array = np.asarray(gray, dtype=np.uint8)
    ink = array < 120
    if ink.sum() < 40:
        return False
    row_ink = ink.sum(axis=1).astype(np.float32)
    columns = float(array.shape[1])
    threshold = max(1.5, columns * 0.004)
    bands: list[tuple[int, int]] = []
    start = None
    for index, value in enumerate(row_ink):
        if value >= threshold and start is None:
            start = index
        elif value < threshold and start is not None:
            bands.append((start, index))
            start = None
    if start is not None:
        bands.append((start, len(row_ink)))
    merged: list[tuple[int, int]] = []
    for begin, end in bands:
        if merged and begin - merged[-1][1] <= 3:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((begin, end))
    bands = merged
    if len(bands) < _MIN_TEXT_LINES:
        return False
    heights = np.asarray([end - begin for begin, end in bands], dtype=np.float32)
    gaps = np.asarray(
        [bands[i + 1][0] - bands[i][1] for i in range(len(bands) - 1)], dtype=np.float32,
    )
    if heights.max() > max(24.0, array.shape[0] * 0.5):
        return False
    pitch = heights + np.concatenate([gaps, gaps[-1:]]).astype(np.float32)
    pitch_cv = float(pitch.std() / (pitch.mean() + 1e-6))
    height_cv = float(heights.std() / (heights.mean() + 1e-6))
    return pitch_cv < 0.6 and height_cv < 1.1


def _band_regularity(heights: np.ndarray, gaps: np.ndarray) -> bool:
    """Lines of near-equal pitch (height+gap) across the block → prose."""

    pitch = heights + (gaps.tolist() + [float(gaps[-1]) if len(gaps) else 0.0])[: len(heights)]
    pitch = pitch[pitch > 0]
    if len(pitch) < _MIN_TEXT_LINES:
        return False
    cv = float(pitch.std() / (pitch.mean() + 1e-6))
    return cv < _TEXT_LINE_REGULARITY + 0.35


def enhance_figure_scan(data: bytes) -> bytes:
    """Strip the illumination field and produce an exact black/white line image.

    Estimate paper at reduced resolution, then apply Sauvola local thresholds
    to the ORIGINAL-resolution normalized pixels. No resampling of strokes,
    erosion, closing, vectorization or generative reconstruction is used.
    Only weak paper-grain islands below a bounded size are removed; dark
    isolated dots and long fine strokes are protected. Source bytes stay immutable.
    """

    gray = _load(data)
    array = np.asarray(gray, dtype=np.float32)
    if float(np.percentile(array, 90)) < 32:
        raise ValueError("选区过暗，无法可靠生成白底黑线图，请修改图片范围。")
    median, high = np.percentile(array, [50, 90])
    if median >= 250 and high - median <= 2 and float((array >= 250).mean()) > .65:
        # Digital screenshots often contain pale antialiased grey circuits.
        # Paper removal would erase them. On uniform white, use a conservative
        # global separation and keep every component, including light dots.
        threshold = min(250., max(220., _otsu_threshold(array) + 12.))
        ink = array < threshold
    else:
        small = gray.copy()
        small.thumbnail((512, 512), Image.Resampling.BILINEAR)
        paper = small.filter(ImageFilter.MaxFilter(15)).filter(ImageFilter.GaussianBlur(5))
        background = np.asarray(
            paper.resize(gray.size, Image.Resampling.BILINEAR), dtype=np.float32,
        )
        normalized = np.minimum(255., array * 255. / np.maximum(background, 16.))
        radius = max(8, min(36, round(min(gray.size) * .018)))
        mean = _box_mean(normalized, radius)
        deviation = np.sqrt(np.maximum(0., _box_mean(normalized**2, radius) - mean**2))
        threshold = mean * (1 + .30 * (deviation / 128. - 1))
        ink = _strip_weak_grain(normalized < threshold, normalized)
    if float(ink.mean()) > .65:
        raise ValueError("选区过暗，无法可靠生成白底黑线图，请修改图片范围。")
    out = Image.fromarray(np.where(ink, 0, 255).astype(np.uint8))
    buffer = io.BytesIO()
    out.save(buffer, format="PNG")
    return buffer.getvalue()


@lru_cache(maxsize=256)
def _cached_enhance(key: str, data: bytes) -> bytes:
    return enhance_figure_scan(data)


def enhanced_crop(data: bytes, *, source_key: str) -> bytes:
    """Cache by algorithm version and pixels; never silently show a failed photo."""

    try:
        return _cached_enhance(f"{CLEANUP_VERSION}:{source_key}", data)
    except (OSError, ValueError):
        logging.getLogger(__name__).exception("白底黑线清稿失败")
        raise
