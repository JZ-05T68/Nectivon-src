"""Round-4 replica pipeline: schema, renderer and service behaviour."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

from src.figure_replica_render import render_replica
from src.figure_replica_schema import (
    ReplicaSpecError,
    parse_replica_spec,
    port_anchor,
    verify_replica_spec,
)
from src.figure_replica_service import (
    STATUS_FAILED_PARSE,
    STATUS_FAILED_VISUAL,
    STATUS_UNSUPPORTED,
    STATUS_VERIFIED,
    generate_figure_replica,
    load_replica,
    replica_key,
    replica_png,
)


def _hydraulic_spec() -> dict:

    return {
        "schema": "nectivon-replica/1",
        "figure_type": "hydraulic",
        "canvas": {"width": 1000, "height": 700},
        "elements": [
            {"id": "tank1", "kind": "tank", "x": 120, "y": 600, "w": 90, "h": 50},
            {
                "id": "pump1",
                "kind": "pump",
                "x": 300,
                "y": 600,
                "w": 90,
                "h": 90,
                "variable": True,
                "labels": [{"text": "液压泵", "dx": 0, "dy": -70}],
            },
            {
                "id": "v1",
                "kind": "valve",
                "x": 520,
                "y": 300,
                "w": 160,
                "h": 70,
                "squares": 2,
                "detail": [{"arrows": [{"from": "left", "to": "right"}]}, {"closed": True}],
                "ports": {"left": "P", "right": "B", "top": "A"},
            },
            {
                "id": "cyl1",
                "kind": "cylinder",
                "x": 800,
                "y": 300,
                "w": 150,
                "h": 80,
                "rods": "single",
            },
        ],
        "connections": [
            {
                "id": "c1",
                "points": [[120, 600], [120, 660], [255, 660], [255, 600]],
                "line": "solid",
            },
            {
                "id": "c2",
                "points": [[345, 600], [345, 650], [520, 650], [520, 335]],
                "attach_to": "v1.bottom",
                "flow": "forward",
                "arrow": "none",
                "joints": [[400, 650]],
            },
            {
                "id": "c3",
                "points": [[600, 300], [725, 300]],
                "attach_from": "v1.right",
                "attach_to": "cyl1.left",
                "arrow": "end",
                "label": {"text": "p_{1}=2MPa", "at": [660, 270]},
            },
        ],
        "annotations": [{"x": 500, "y": 60, "text": "图 a）进口节流调速回路", "anchor": "middle"}],
        "notes": [],
    }


class TestSchema:
    def test_valid_hydraulic_spec_parses(self):

        spec = parse_replica_spec(_hydraulic_spec())

        assert spec["figure_type"] == "hydraulic"

        assert len(spec["elements"]) == 4

        assert spec["elements"][1]["variable"] is True

    def test_rejects_unknown_schema_and_type(self):

        payload = _hydraulic_spec()

        payload["schema"] = "other/9"

        with pytest.raises(ReplicaSpecError):
            parse_replica_spec(payload)

        payload = _hydraulic_spec()

        payload["figure_type"] = "photo"

        with pytest.raises(ReplicaSpecError):
            parse_replica_spec(payload)

    def test_rejects_duplicate_ids_and_dangling_attach(self):

        payload = _hydraulic_spec()

        payload["elements"][1]["id"] = payload["elements"][0]["id"]

        with pytest.raises(ReplicaSpecError, match="重复"):
            parse_replica_spec(payload)

        payload = _hydraulic_spec()

        payload["connections"][2]["attach_to"] = "ghost.right"

        with pytest.raises(ReplicaSpecError, match="不存在"):
            parse_replica_spec(payload)

    def test_rejects_empty_spec_and_bad_canvas(self):

        payload = _hydraulic_spec()

        payload["elements"] = []

        payload["connections"] = []

        with pytest.raises(ReplicaSpecError):
            parse_replica_spec(payload)

        payload = _hydraulic_spec()

        payload["canvas"] = {"width": 10, "height": 700}

        with pytest.raises(ReplicaSpecError):
            parse_replica_spec(payload)

    def test_verify_flags_far_endpoint_and_missing_arrow(self):

        spec = parse_replica_spec(_hydraulic_spec())

        check = verify_replica_spec(spec)

        assert check.ok  # endpoints near their ports, arrows declared

    def test_verify_warns_on_far_endpoint(self):

        payload = _hydraulic_spec()

        payload["connections"][2]["points"][-1] = [950, 500]  # far from cyl1.left

        spec = parse_replica_spec(payload)

        check = verify_replica_spec(spec)

        assert not check.ok

        assert any("偏离" in warning for warning in check.warnings)

    def test_port_anchor_geometry(self):

        spec = parse_replica_spec(_hydraulic_spec())

        valve = spec["elements"][2]

        anchor = port_anchor(valve, "left")

        assert anchor == (valve["x"] - valve["w"] / 2, valve["y"])


class TestRenderer:
    @pytest.mark.parametrize(
        "family,spec_builder",
        [
            ("hydraulic", _hydraulic_spec),
            (
                "block_diagram",
                lambda: {
                    "schema": "nectivon-replica/1",
                    "figure_type": "block_diagram",
                    "canvas": {"width": 1000, "height": 500},
                    "elements": [
                        {
                            "id": "b1",
                            "kind": "block",
                            "x": 200,
                            "y": 250,
                            "w": 220,
                            "h": 110,
                            "text": "比例阀 K",
                        },
                        {
                            "id": "s1",
                            "kind": "sum",
                            "x": 600,
                            "y": 250,
                            "w": 70,
                            "h": 70,
                            "signs": {"left": "+", "bottom": "-"},
                        },
                    ],
                    "connections": [
                        {
                            "id": "c1",
                            "points": [[310, 250], [565, 250]],
                            "attach_from": "b1.right",
                            "attach_to": "s1.left",
                            "arrow": "end",
                        },
                    ],
                    "annotations": [{"x": 500, "y": 80, "text": "系统框图", "anchor": "middle"}],
                    "notes": [],
                },
            ),
            (
                "circuit",
                lambda: {
                    "schema": "nectivon-replica/1",
                    "figure_type": "circuit",
                    "canvas": {"width": 1000, "height": 600},
                    "elements": [
                        {
                            "id": "u1",
                            "kind": "winding",
                            "x": 300,
                            "y": 300,
                            "w": 140,
                            "h": 120,
                            "labels": [{"text": "U_{1}", "dx": -100, "dy": 0}],
                        },
                        {"id": "w1", "kind": "winding", "x": 700, "y": 300, "w": 140, "h": 120},
                    ],
                    "connections": [
                        {
                            "id": "c1",
                            "points": [[370, 300], [630, 300]],
                            "attach_from": "u1.right",
                            "attach_to": "w1.left",
                        },
                    ],
                    "annotations": [],
                    "notes": [],
                },
            ),
            (
                "curve",
                lambda: {
                    "schema": "nectivon-replica/1",
                    "figure_type": "curve",
                    "canvas": {"width": 1000, "height": 700},
                    "axes": {
                        "x": {"label": "t/s", "range": [0, 4], "ticks": [0, 1, 2, 3, 4]},
                        "y": {"label": "u_{o}(t)", "range": [0, 10], "ticks": [0, 5, 10]},
                    },
                    "curves": [
                        {
                            "id": "c1",
                            "points": [[0, 0], [1, 6.3], [2, 8.7], [3, 9.5], [4, 9.8]],
                            "label": "一阶响应",
                        },
                    ],
                    "elements": [],
                    "connections": [],
                    "annotations": [],
                    "notes": [],
                },
            ),
        ],
    )
    def test_renders_svg_and_png(self, family, spec_builder):

        spec = parse_replica_spec(spec_builder())

        svg, png = render_replica(spec, px_width=1200)

        root = ET.fromstring(svg.decode("utf-8"))

        assert root.tag.endswith("svg")

        assert root.get("width") == "1200"

        assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000

    def test_render_is_deterministic(self):

        spec = parse_replica_spec(_hydraulic_spec())

        first = render_replica(spec)

        second = render_replica(spec)

        assert first == second

    def test_text_grammar_survives_rendering(self):

        payload = _hydraulic_spec()

        payload["annotations"] = [{"x": 500, "y": 40, "text": "q_{1} ≥ α^{2}", "anchor": "middle"}]

        spec = parse_replica_spec(payload)

        _, png = render_replica(spec)

        assert len(png) > 5000


class _Completion:
    def __init__(self, text: str):

        self.text = text

        self.model = "fake-vision"

        self.resolved_model = "fake-vision"


class _FakeProvider:
    def __init__(self, responses: list[str]):

        self.responses = list(responses)

        self.default_model = "fake-vision"

        self.calls: list[str] = []

    def complete_vision(self, prompt, image_png_base64, **kwargs):

        self.calls.append(prompt[:24])

        return _Completion(self.responses.pop(0))


class TestService:
    @staticmethod
    def _crop_bytes() -> bytes:

        import io

        from PIL import Image

        buffer = io.BytesIO()

        Image.new("RGB", (240, 160), "#f4f0e6").save(buffer, format="PNG")

        return buffer.getvalue()

    @classmethod
    def _source_file(cls, tmp_path, name: str = "page_0001.png"):

        path = tmp_path / name

        path.write_bytes(cls._crop_bytes())

        return path

    def test_verified_replica_full_flow(self, tmp_path):

        provider = _FakeProvider(
            [
                json.dumps(_hydraulic_spec(), ensure_ascii=False),
                json.dumps(
                    {"verdict": "pass", "missing": [], "extra": [], "wrong": [], "text_errors": []}
                ),
            ]
        )

        crop = self._crop_bytes()

        source = self._source_file(tmp_path)

        record = generate_figure_replica(provider, crop, source_path=source, page_ref="page:7")

        assert record["status"] == STATUS_VERIFIED

        assert record["figure_type"] == "hydraulic"

        assert len(provider.calls) == 2

        stored = load_replica(source, crop)

        assert stored is not None and stored["status"] == STATUS_VERIFIED

        assert replica_png(source, crop)[:4] == b"\x89PNG"

    def test_failed_parse_records_status(self, tmp_path):

        provider = _FakeProvider(["不是 JSON", "whatever"])

        record = generate_figure_replica(
            provider, self._crop_bytes(), source_path=self._source_file(tmp_path), page_ref="page:1"
        )

        assert record["status"] == STATUS_FAILED_PARSE

        assert len(provider.calls) == 1  # no retry on semantic failure

    def test_unsupported_figures_are_recorded(self, tmp_path):

        provider = _FakeProvider(
            [
                json.dumps({"schema": "nectivon-replica/1", "unsupported": "照片类插图"}),
            ]
        )

        record = generate_figure_replica(
            provider, self._crop_bytes(), source_path=self._source_file(tmp_path), page_ref="page:1"
        )

        assert record["status"] == STATUS_UNSUPPORTED

        assert "照片" in record["reason"]

    def test_visual_failure_blocks_publish(self, tmp_path):

        provider = _FakeProvider(
            [
                json.dumps(_hydraulic_spec(), ensure_ascii=False),
                json.dumps(
                    {
                        "verdict": "fail",
                        "missing": ["溢流阀"],
                        "extra": [],
                        "wrong": ["泵方向反了"],
                        "text_errors": [],
                    }
                ),
            ]
        )

        record = generate_figure_replica(
            provider, self._crop_bytes(), source_path=self._source_file(tmp_path),
            page_ref="page:1", allow_correction=False,
        )

        assert record["status"] == STATUS_FAILED_VISUAL

        assert record["verification"]["missing"] == ["溢流阀"]

    def test_missing_provider_is_rejected(self, tmp_path):

        from src.question_image_store import QuestionImageError

        with pytest.raises(QuestionImageError):
            generate_figure_replica(
                None, self._crop_bytes(), source_path=self._source_file(tmp_path), page_ref="page:1"
            )

    def test_content_addressing_reuses_records(self, tmp_path):

        provider = _FakeProvider(
            [
                json.dumps(_hydraulic_spec(), ensure_ascii=False),
                json.dumps(
                    {"verdict": "pass", "missing": [], "extra": [], "wrong": [], "text_errors": []}
                ),
            ]
        )

        crop = self._crop_bytes()

        source = self._source_file(tmp_path)

        first = generate_figure_replica(provider, crop, source_path=source, page_ref="page:1")

        assert first["status"] == STATUS_VERIFIED

        assert load_replica(source, crop)["replica_key"] == replica_key(crop)

        different = b"\x89PNG\r\n\x1a\nother-bytes"

        assert load_replica(source, different) is None

    def test_bounded_correction_round_can_recover(self, tmp_path):
        provider = _FakeProvider([
            json.dumps(_hydraulic_spec(), ensure_ascii=False),
            json.dumps({"verdict": "fail", "missing": ["F 方向"],
                        "extra": [], "wrong": [], "text_errors": []}),
            json.dumps(_hydraulic_spec(), ensure_ascii=False),
            json.dumps({"verdict": "pass", "missing": [], "extra": [],
                        "wrong": [], "text_errors": [], "style_notes": []}),
        ])
        record = generate_figure_replica(
            provider, self._crop_bytes(), source_path=self._source_file(tmp_path),
            page_ref="page:1")
        assert record["status"] == STATUS_VERIFIED
        assert record["corrected"] is True
        assert len(provider.calls) == 4

    def test_correction_failure_keeps_failed_visual(self, tmp_path):
        provider = _FakeProvider([
            json.dumps(_hydraulic_spec(), ensure_ascii=False),
            json.dumps({"verdict": "fail", "missing": ["x"], "extra": [],
                        "wrong": [], "text_errors": []}),
            "correction is not json",
        ])
        record = generate_figure_replica(
            provider, self._crop_bytes(), source_path=self._source_file(tmp_path),
            page_ref="page:1")
        assert record["status"] == STATUS_FAILED_VISUAL
        # spec + verify + one correction attempt whose payload was invalid
        assert len(provider.calls) == 3
