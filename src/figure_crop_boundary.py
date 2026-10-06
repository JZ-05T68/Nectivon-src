"""Bounded pixel-only completion of clipped diagram strokes and nearby labels."""

from __future__ import annotations

from collections import deque

from PIL import Image


def has_sparse_bottom_gutter(
    image: Image.Image, bounds: tuple[int, int, int, int], clip_bottom: int,
) -> bool:
    """Find a blank/thin-pen gutter near a tall figure's lower row boundary.

    Frame borders spanning a row and short number-line proposals must keep
    growing to recover labels. A narrow handwritten connector across a
    gutter must not join the diagram to prose in the following question.
    This inspects ink positions only; it never identifies any glyph.
    """

    left, top, right, bottom = bounds
    if bottom - top < image.height * .03:
        return False
    end = min(image.height, clip_bottom)
    start = max(top, end - max(10, round(image.height * .015)))
    pixels = image.crop((left, start, right, end)).convert("L")
    width = pixels.width
    data = pixels.tobytes()
    sparse_rows = 0
    for y in range(pixels.height):
        positions = [x for x, value in enumerate(data[y * width:(y + 1) * width]) if value < 150]
        span = positions[-1] - positions[0] + 1 if positions else 0
        if len(positions) <= max(4, image.width * .006) and span <= max(4, image.width * .012):
            sparse_rows += 1
            if sparse_rows >= 5:
                return True
        else:
            sparse_rows = 0
    return False


def figure_bounds(
    image: Image.Image, bounds: tuple[int, int, int, int],
    *, anchor_bounds: tuple[int, int, int, int] | None = None,
    horizontal_margin: int | None = None,
) -> tuple[int, int, int, int]:
    """Locate line/frame components, retaining adjacent labels in original pixels.

    No glyphs are identified, no text is transcribed, and no AI request is made.
    Dense photographs and large regions keep their proposed bounds unchanged.
    """

    left, top, right, bottom = bounds
    anchor_left, anchor_top, anchor_right, anchor_bottom = anchor_bounds or bounds
    margin = max(12, round(image.height * 0.02))
    margin_x = margin if horizontal_margin is None else horizontal_margin
    roi = (
        max(0, left - margin_x),
        max(0, top - margin),
        min(image.width, right + margin_x),
        min(image.height, bottom + margin),
    )
    width, height = roi[2] - roi[0], roi[3] - roi[1]
    if width * height > 1_000_000:
        return bounds
    mask = bytearray(image.crop(roi).convert("L").point(lambda p: 1 if p < 150 else 0).tobytes())
    components: list[tuple[int, int, int, int, int]] = []
    for position, ink in enumerate(mask):
        if not ink:
            continue
        queue = deque([position])
        mask[position] = 0
        x, y = position % width, position // width
        x1 = x2 = x
        y1 = y2 = y
        count = 0
        while queue:
            current = queue.popleft()
            x, y = current % width, current // width
            x1, x2, y1, y2 = min(x1, x), max(x2, x), min(y1, y), max(y2, y)
            count += 1
            for ny in range(max(0, y - 1), min(height, y + 2)):
                for nx in range(max(0, x - 1), min(width, x + 2)):
                    neighbor = ny * width + nx
                    if mask[neighbor]:
                        mask[neighbor] = 0
                        queue.append(neighbor)
        if count >= 4:
            components.append((x1, y1, x2 + 1, y2 + 1, count))
    anchors = [
        c
        for c in components
        if c[2] - c[0] >= min(width * 0.12, max(20, image.width * 0.04))
        and c[3] - c[1] >= 2
        and (c[3] - c[1] <= 4 or c[4] / ((c[2] - c[0]) * (c[3] - c[1])) < 0.55)
        and c[0] + roi[0] < anchor_right
        and c[2] + roi[0] > anchor_left
        and c[1] + roi[1] < anchor_bottom
        and c[3] + roi[1] > anchor_top
    ]
    if not anchors:
        return bounds
    near = max(12, round(image.height * 0.009))
    primary = max(anchors, key=lambda c: c[2] - c[0])
    if primary[3] - primary[1] > image.height * .012:
        # Tall frames need their own labels, not prose in the next row.
        near = min(near, max(6, round((primary[3] - primary[1]) * .16)))

    def same_band(component, anchor) -> bool:
        return max(0, component[1] - anchor[3], anchor[1] - component[3]) <= near

    # Disconnected arrows in a flow chart share a row. A blank answer line
    # in the following row must not become a second figure anchor.
    anchors = [c for c in anchors if same_band(c, primary)]
    selected = [c for c in components if any(same_band(c, anchor) for anchor in anchors)]
    # Follow nearby disconnected frames/arrows across the same row. A wide
    # column gap is a boundary, even when another figure shares that row.
    group = [primary]
    remaining = [c for c in selected if c != primary]
    gap = max(12, round(image.width * .055))
    while remaining:
        adjacent = [c for c in remaining if any(
            max(0, c[0] - other[2], other[0] - c[2]) <= gap for other in group
        )]
        if not adjacent:
            break
        group.extend(adjacent)
        remaining = [c for c in remaining if c not in adjacent]
    selected = group
    return (
        max(0, roi[0] + min(c[0] for c in selected) - 6),
        max(0, roi[1] + min(c[1] for c in selected) - 6),
        min(image.width, roi[0] + max(c[2] for c in selected) + 6),
        min(image.height, roi[1] + max(c[3] for c in selected) + 6),
    )
