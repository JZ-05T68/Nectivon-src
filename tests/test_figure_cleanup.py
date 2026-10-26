"""Preserve sparse engineering structures while removing photographed paper."""

import io

import numpy as np
from PIL import Image, ImageDraw

from src.figure_scan_service import enhance_figure_scan


def test_cleanup_preserves_fine_lines_dots_arrows_gaps_and_subscripts() -> None:
    background = np.tile(np.linspace(125, 245, 600), (300, 1)).astype(np.uint8)
    photo = Image.fromarray(background).convert("RGB")
    draw = ImageDraw.Draw(photo)
    draw.line((30, 80, 560, 80), fill=(25, 25, 25), width=1)
    draw.line((300, 20, 300, 180), fill=(25, 25, 25), width=1)
    draw.line((560, 80, 548, 74), fill="black", width=1)
    draw.line((560, 80, 548, 86), fill="black", width=1)
    draw.line((40, 170, 120, 170), fill="black", width=1)
    draw.line((130, 170, 210, 170), fill="black", width=1)
    draw.text((350, 100), "p_1 10^-4", fill="black")
    draw.point((260, 210), fill="black")
    stream = io.BytesIO()
    photo.save(stream, format="PNG")
    original = stream.getvalue()
    cleaned = np.array(Image.open(io.BytesIO(enhance_figure_scan(original))))
    assert set(np.unique(cleaned)) == {0, 255}
    assert np.all(cleaned[80, 30:561] == 0)
    assert np.all(cleaned[20:181, 300] == 0)
    assert cleaned[210, 260] == 0  # Dark isolated mathematical dots survive.
    assert np.all(cleaned[170, 121:130] == 255)  # Never invent a connection.
    assert np.all(cleaned[240:, :] == 255)
    raw = np.asarray(photo.convert("L"))
    assert np.all(cleaned[100:125, 350:420][raw[100:125, 350:420] < 30] == 0)
    assert original == stream.getvalue()


def test_cleanup_keeps_faint_long_stroke_and_clears_weak_paper_islands() -> None:
    photo = Image.new("L", (240, 150), 220)
    draw = ImageDraw.Draw(photo)
    draw.line((10, 60, 225, 60), fill=105, width=1)
    for x in range(15, 220, 10):
        draw.point((x, 115), fill=155)
    out = io.BytesIO()
    photo.save(out, format="PNG")
    cleaned = np.asarray(Image.open(io.BytesIO(enhance_figure_scan(out.getvalue()))))
    assert np.all(cleaned[60, 10:226] == 0)
    assert np.all(cleaned[100:, :] == 255)


def test_uniform_screenshot_preserves_pale_wires_labels_and_tiny_dots() -> None:
    photo = Image.new("L", (500, 300), 255)
    draw = ImageDraw.Draw(photo)
    draw.line((20, 180, 450, 180), fill=225, width=1)
    draw.line((100, 70, 400, 240), fill=235, width=1)
    draw.text((220, 70), "A B C X Y Z", fill=200)
    draw.point((180, 200), fill=230)
    raw = np.asarray(photo)
    out = io.BytesIO()
    photo.save(out, format="PNG")
    cleaned = np.asarray(Image.open(io.BytesIO(enhance_figure_scan(out.getvalue()))))
    assert np.all(cleaned[raw < 240] == 0)
    assert np.all(cleaned[raw == 255] == 255)
