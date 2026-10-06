"""Manual drawing persists across question surfaces without touching original scans."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from streamlit.testing.v1 import AppTest

from src.question_image_store import QuestionImageError, QuestionImageStore, question_display_path
from src.question_visual_regions import bind_regions, crop_region


def png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@pytest.fixture()
def scan(tmp_path: Path) -> Path:
    path = tmp_path / "page_0001.png"
    image = Image.new("RGB", (600, 800), "white")
    draw = ImageDraw.Draw(image)
    draw.line((100, 200, 500, 200), fill="black", width=3)
    draw.line((200, 180, 200, 200), fill="black", width=3)
    image.save(path)
    return path


def revised(scan: Path) -> bytes:
    with Image.open(scan) as original:
        result = original.convert("RGB")
    ImageDraw.Draw(result).line((300, 200, 300, 185), fill="red", width=4)
    return png(result)


def test_manual_edit_changes_all_shared_crops_but_not_original(scan: Path) -> None:
    original = scan.read_bytes()
    region = bind_regions(
        [{"role": "question", "bbox": [150, 210, 850, 360]}],
        page_id=1, image_bytes=original,
    )[0]
    before = crop_region(scan, region)
    store = QuestionImageStore(scan)
    state = store.save(revised(scan), expected_revision="original")
    assert state["origin"] == "manual" and scan.read_bytes() == original
    assert question_display_path(scan) == store.display_path()
    with Image.open(store.display_path()) as display:
        assert display.getpixel((300, 190)) == (255, 0, 0)
        assert display.getpixel((200, 190)) == (0, 0, 0)
    after = crop_region(scan, region)
    assert after != before and crop_region(scan, region, original=True) == before
    assert Image.open(io.BytesIO(after)).size == Image.open(io.BytesIO(before)).size


def test_restore_keeps_history_and_source_untouched(scan: Path) -> None:
    original = scan.read_bytes()
    store = QuestionImageStore(scan)
    state = store.save(revised(scan), expected_revision="original")
    restored = store.restore_original(expected_revision=state["revision"])
    assert (store.root / f"{state['revision']}.png").is_file()
    assert restored["origin"] == "manual" and restored["details"]["restored_original"]
    assert scan.read_bytes() == original
    with Image.open(store.display_path()) as display, Image.open(scan) as source:
        assert display.tobytes() == source.convert("RGB").tobytes()


def test_source_change_never_reuses_old_revision_or_old_crop(scan: Path) -> None:
    store = QuestionImageStore(scan)
    store.save(revised(scan), expected_revision="original")
    old_region = bind_regions(
        [{"role": "stem", "bbox": [100, 100, 900, 400]}],
        page_id=1, image_bytes=scan.read_bytes(),
    )[0]
    Image.new("RGB", (600, 800), "blue").save(scan)
    assert QuestionImageStore(scan).display_path() == scan.resolve()
    assert crop_region(scan, old_region) is None


@pytest.mark.parametrize("data", [b"bad", png(Image.new("RGB", (10, 10)))])
def test_invalid_or_resized_drawing_is_rejected(scan: Path, data: bytes) -> None:
    store = QuestionImageStore(scan)
    with pytest.raises(QuestionImageError):
        store.save(data, expected_revision="original")
    assert store.display_path() == scan.resolve()


def test_stale_editor_cannot_overwrite_new_revision(scan: Path) -> None:
    store = QuestionImageStore(scan)
    store.save(scan.read_bytes(), expected_revision="original")
    with pytest.raises(QuestionImageError, match="其它位置修改"):
        store.save(scan.read_bytes(), expected_revision="original")


def test_tampering_falls_back_to_original_without_hiding_question(scan: Path) -> None:
    store = QuestionImageStore(scan)
    store.save(scan.read_bytes(), expected_revision="original")
    path = store.display_path()
    path.write_bytes(b"bad")
    with pytest.raises(QuestionImageError):
        store.state()
    assert question_display_path(scan) == scan
    region = {"role": "question", "bbox": [100, 100, 900, 500]}
    assert crop_region(scan, region) == crop_region(scan, region, original=True)


def test_reopening_editor_does_not_resave_component_event(scan: Path, monkeypatch) -> None:
    import src.question_image_editor as editor
    from src.question_region_store import QuestionRegionStore

    regions = [{"role": "stem", "bbox": [100, 100, 900, 500]}]
    state = QuestionRegionStore(scan, "6", regions)
    identifier = state.state()["regions"][0]["id"]
    data = state.crop(identifier)
    event = {"request_id": "test-save", "source_hash": state.source_sha256,
             "scope": state.scope, "region_id": identifier,
             "revision": "original", "data_url": "data:image/png;base64," +
             base64.b64encode(data).decode()}
    monkeypatch.setattr(editor, "_draw_component", lambda **kwargs: event)

    app = AppTest.from_string(
        "from src.question_image_editor import render_question_image_editor\n"
        f"render_question_image_editor({str(scan)!r}, page_id=1, number='6', "
        f"regions={regions!r}, key='manual_test')",
    ).run()
    assert not app.exception
    assert not any("AI" in b.label or "去手写" in b.label for b in app.button)
    app.checkbox(key="manual_test_open").check().run()
    assert not app.exception
    stored = QuestionRegionStore(scan, "6", regions)
    revision = stored.state()["revision"]
    assert revision != "original"
    app.run()
    assert not app.exception and stored.state()["revision"] == revision
    assert len(list(stored.root.glob("*.png"))) == 1
