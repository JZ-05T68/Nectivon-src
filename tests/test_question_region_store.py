"""Crop re-framing and local drawings synchronize without crossing question boundaries."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image

from src.question_content_ui import _coalesce_overlapping_figures, question_regions
from src.question_image_store import QuestionImageError
from src.question_region_store import QuestionRegionStore
from src.question_visual_regions import crop_region


@pytest.fixture()
def scan(tmp_path: Path) -> Path:
    source = tmp_path / "scan.png"
    image = Image.new("RGB", (800, 600), "white")
    image.paste("blue", (100, 120, 160, 180))
    image.paste("green", (300, 120, 360, 180))
    image.save(source)
    return source


def boxes() -> list[dict]:
    return [{"id": "a" * 32, "role": "stem", "bounds": [80, 100, 180, 200]},
            {"id": "b" * 32, "role": "stem", "bounds": [280, 100, 380, 200]}]


def pixels(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


def test_boxes_sync_across_candidates_learning_training_and_rerecognition(scan: Path) -> None:
    before = scan.read_bytes()
    store = QuestionRegionStore(scan, "24(1)")
    store.save_regions(boxes(), expected_revision="original")
    changed = boxes()
    changed[0]["bounds"] = [90, 110, 170, 190]
    store.save_regions(changed, expected_revision=store.state()["revision"])
    # Different surfaces or a new recognition output still resolve the same committed bounds.
    for raw in ([], [{"role": "stem", "bbox": [0, 0, 900, 800]}], store.regions(page_id=5)):
        resolved = question_regions(scan, "24（1）", raw, page_id=5)
        assert resolved[0]["crop_bounds"] == [90, 110, 170, 190]
        assert pixels(crop_region(scan, resolved[0])).size == (80, 80)
    assert scan.read_bytes() == before
    assert question_regions(scan, "24(2)", [], page_id=5) == []


def test_drawing_then_expanding_crop_retains_original_pixel_coordinates(scan: Path) -> None:
    store = QuestionRegionStore(scan, "6")
    store.save_regions(boxes(), expected_revision="original")
    drawn = pixels(store.crop("a" * 32))
    drawn.putpixel((30, 30), (255, 0, 0))
    buffer = io.BytesIO()
    drawn.save(buffer, format="PNG")
    store.save_crop("a" * 32, buffer.getvalue(), expected_revision=store.state()["revision"])
    changed = boxes()
    changed[0]["bounds"] = [60, 80, 200, 220]
    store.save_regions(changed, expected_revision=store.state()["revision"])
    current = pixels(store.crop("a" * 32))
    assert current.size == (140, 140)
    assert current.getpixel((50, 50)) == (255, 0, 0)
    assert pixels(store.crop("a" * 32, original=True)).getpixel((50, 50)) == (0, 0, 255)
    assert pixels(store.crop("b" * 32)).getpixel((30, 30)) == (0, 128, 0)
    store.restore_crop("a" * 32, expected_revision=store.state()["revision"])
    assert store.crop("a" * 32) == store.crop("a" * 32, original=True)
    assert len(list(store.root.glob("*.png"))) == 1  # history survives restoration


def test_manual_overlapping_boxes_are_distinct_and_exact(scan: Path) -> None:
    store = QuestionRegionStore(scan, "6")
    items = boxes()
    items[1]["bounds"] = [85, 105, 185, 205]
    store.save_regions(items, expected_revision="original")
    regions = store.regions(page_id=1)
    assert len(_coalesce_overlapping_figures(regions)) == 2
    assert pixels(crop_region(scan, regions[0])).size == (100, 100)
    regions[0]["image_sha256"] = "0" * 64
    assert crop_region(scan, regions[0]) is None


def test_stale_foreign_and_invalid_changes_never_overwrite_source(scan: Path) -> None:
    original = hashlib.sha256(scan.read_bytes()).hexdigest()
    store = QuestionRegionStore(scan, "6")
    store.save_regions(boxes(), expected_revision="original")
    with pytest.raises(QuestionImageError, match="其它位置"):
        store.save_regions([], expected_revision="original")
    invalid = boxes()
    invalid[0]["bounds"] = [-1, 0, 50, 50]
    with pytest.raises(QuestionImageError):
        store.save_regions(invalid, expected_revision=store.state()["revision"])
    with pytest.raises(QuestionImageError):
        store.save_crop("a" * 32, scan.read_bytes(), expected_revision=store.state()["revision"])
    assert hashlib.sha256(scan.read_bytes()).hexdigest() == original
    old_regions = store.regions()
    store.save_regions([], expected_revision=store.state()["revision"])
    assert crop_region(scan, old_regions[0]) is None  # deleted boxes cannot revive by fallback


def test_ai_defaults_with_foreign_source_hash_are_not_seeded(scan: Path) -> None:
    defaults = [{"role": "stem", "bbox": [100, 100, 500, 500], "image_sha256": "0" * 64}]
    assert QuestionRegionStore(scan, "6", defaults).state()["regions"] == []
