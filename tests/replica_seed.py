"""Shared test helper: seed a verified replica for a figure region's crop.

Round 4 made the replica the default figure display, so UI tests that need a
visible figure seed a minimal verified replica record — exercising the real
display path without any cloud call.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.figure_replica_render import render_replica
from src.figure_replica_schema import parse_replica_spec
from src.figure_replica_service import replica_key, replica_store_root
from src.question_visual_regions import crop_region


def seed_verified_replica(image_path: Path, region: dict) -> None:
    data = crop_region(image_path, region)
    assert data is not None, "region crop failed; cannot seed replica"
    spec = parse_replica_spec({
        "schema": "nectivon-replica/1", "figure_type": "node_link",
        "canvas": {"width": 480, "height": 360},
        "elements": [{"id": "n1", "kind": "box", "x": 240, "y": 180,
                      "w": 260, "h": 160, "text": "复刻测试图"}],
        "connections": [], "annotations": [],
    })
    svg, png = render_replica(spec, px_width=480)
    root = replica_store_root(image_path) / replica_key(data)
    root.mkdir(parents=True, exist_ok=True)
    (root / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    (root / "replica.svg").write_bytes(svg)
    (root / "replica.png").write_bytes(png)
    (root / "status.json").write_text(json.dumps({
        "status": "verified", "figure_type": "node_link",
        "verification": {"verdict": "pass", "missing": [], "extra": [],
                         "wrong": [], "text_errors": [], "style_notes": []},
        "render_version": "replica-render-1", "model": "test-seed",
    }, ensure_ascii=False), encoding="utf-8")
