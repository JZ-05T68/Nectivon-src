"""Constrained intermediate representation for faithful textbook figure replicas.

This module defines and validates ``nectivon-replica/1`` — a strict,
fail-closed JSON schema for structured figure replication (Route A of the
round-4 direction).  The cloud vision model only ever proposes this IR;
rendering and verification are deterministic local code.  Anything the model
cannot confirm is either rejected here or carried as an explicit
``notes``/warning, never silently "improved".
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

SCHEMA_VERSION = "nectivon-replica/1"
REPLICA_VERSION = "replica-render-1"

FIGURE_TYPES = frozenset({"hydraulic", "block_diagram", "circuit", "curve", "node_link"})

LINE_STYLES = frozenset({"solid", "dashed", "dash_dot", "dotted"})
ARROW_MODES = frozenset({"none", "start", "end", "both"})
ANCHORS = frozenset({"start", "middle", "end"})

#: Generic kinds allowed in every figure family (escape hatches stay visible
#: as "unverifiable geometry" in the verification report).
COMMON_KINDS = frozenset({
    "box", "circle", "path", "junction", "terminal", "text", "arrow_marker",
})

KINDS_BY_TYPE: dict[str, frozenset[str]] = {
    "hydraulic": frozenset({
        "tank", "pump", "motor", "cylinder", "valve", "relief_valve",
        "reducing_valve", "throttle", "check_valve", "pressure_gauge", "filter",
        "accumulator", "solenoid", "spring", "lever", "plug", "load",
    }),
    "block_diagram": frozenset({"block", "sum", "branch", "signal_out"}),
    "circuit": frozenset({
        "resistor", "capacitor", "inductor", "source_dc", "source_ac", "battery",
        "ground", "terminal", "winding", "switch", "lamp", "motor",
    }),
    "curve": frozenset({"axis_label_marker"}),
    "node_link": frozenset({"node"}),
}

_PORT_NAMES = frozenset({
    "top", "bottom", "left", "right", "center", "a", "b", "p", "t",
    "t1", "t2", "in1", "in2", "out", "out1", "out2", "plus", "minus", "rodl", "rodr",
})

_ID_PATTERN = re.compile(r"[0-9A-Za-z_.-]{1,40}")

#: Distance (canvas units) within which a connection endpoint is snapped to
#: the referenced port anchor; beyond it the mismatch becomes a warning.
SNAP_TOLERANCE = 42.0


class ReplicaSpecError(ValueError):
    """Raised when a model-proposed replica spec violates the schema."""


@dataclass(frozen=True)
class ReplicaCheck:
    """Deterministic verification outcome for one replica spec."""

    ok: bool
    element_count: int = 0
    connection_count: int = 0
    label_count: int = 0
    snapped: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unverifiable: list[str] = field(default_factory=list)


def _number(value: object, *, lo: float, hi: float, what: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ReplicaSpecError(f"{what} 必须是有限数字。")
    number = float(value)
    if not lo <= number <= hi:
        raise ReplicaSpecError(f"{what} 超出范围 [{lo:g}, {hi:g}]。")
    return number


def _text(value: object, what: str, *, limit: int = 120) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReplicaSpecError(f"{what} 必须是非空字符串。")
    return value.strip()[:limit]


def _enum(value: object, allowed: frozenset[str], what: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ReplicaSpecError(f"{what} 必须是 {sorted(allowed)} 之一。")
    return value


def _label(value: object, what: str) -> dict:
    if not isinstance(value, dict):
        raise ReplicaSpecError(f"{what} 必须是对象。")
    return {
        "text": _text(value.get("text"), f"{what}.text", limit=80),
        "dx": _number(value.get("dx", 0), lo=-1000, hi=1000, what=f"{what}.dx"),
        "dy": _number(value.get("dy", 0), lo=-1000, hi=1000, what=f"{what}.dy"),
        "anchor": _enum(value.get("anchor", "start"), ANCHORS, f"{what}.anchor"),
    }


def _allowed_kinds(figure_type: str) -> frozenset[str]:
    return COMMON_KINDS | KINDS_BY_TYPE.get(figure_type, frozenset())


def _validate_element(element: object, figure_type: str, canvas: dict, ids: set[str]) -> dict:
    if not isinstance(element, dict):
        raise ReplicaSpecError("elements 列表项必须是对象。")
    identifier = element.get("id")
    if not isinstance(identifier, str) or not _ID_PATTERN.fullmatch(identifier):
        raise ReplicaSpecError(f"元素 id 非法：{identifier!r}")
    if identifier in ids:
        raise ReplicaSpecError(f"元素 id 重复：{identifier}")
    kind = _enum(element.get("kind"), _allowed_kinds(figure_type), f"元素 {identifier} 的 kind")
    # Some kinds place themselves by coordinates alone (paths, standalone
    # text) or need only a default dot size; the model may omit w/h for them.
    point_kinds = ("path", "text", "arrow_marker", "axis_label_marker")
    dot_kinds = ("junction", "branch", "terminal")
    size_optional = kind in point_kinds or kind in dot_kinds
    default_size = 0 if kind in point_kinds else 26 if size_optional else None

    def _size(value: object, what: str) -> float:
        if value is None and default_size is not None:
            return default_size
        return _number(value, lo=0 if kind in point_kinds else 4,
                       hi=max(canvas["width"], canvas["height"]) * 1.5, what=what)

    width = _size(element.get("w"), f"元素 {identifier} 的 w")
    height = _size(element.get("h"), f"元素 {identifier} 的 h")

    def _coordinate(value: object, limit: float, what: str) -> float:
        if value is None and kind in point_kinds:
            return 0.0  # paths/text place themselves via points/x,y only
        return _number(value, lo=-limit, hi=limit, what=what)

    result = {
        "id": identifier, "kind": kind,
        "x": _coordinate(element.get("x"), canvas["width"] * 1.3,
                         f"元素 {identifier} 的 x"),
        "y": _coordinate(element.get("y"), canvas["height"] * 1.3,
                         f"元素 {identifier} 的 y"),
        "w": width, "h": height,
        "rotate": _number(element.get("rotate", 0), lo=-360, hi=360,
                          what=f"元素 {identifier} 的 rotate"),
        "labels": [_label(item, f"元素 {identifier} 的标签")
                   for item in element.get("labels", []) if isinstance(item, dict)],
    }
    ids.add(identifier)
    _validate_kind_fields(element, result, identifier)
    return result


def _validate_kind_fields(element: dict, result: dict, identifier: str) -> None:
    kind = result["kind"]
    if kind == "valve":
        squares = element.get("squares", 2)
        if type(squares) is not int or not 1 <= squares <= 4:
            raise ReplicaSpecError(f"换向阀 {identifier} 的 squares 必须是 1..4。")
        result["squares"] = squares
        detail = element.get("detail", [])
        if not isinstance(detail, list) or len(detail) > squares:
            raise ReplicaSpecError(f"换向阀 {identifier} 的 detail 数量超过位数。")
        cleaned = []
        for square in detail:
            if not isinstance(square, dict):
                raise ReplicaSpecError(f"换向阀 {identifier} 的 detail 项必须是对象。")
            item: dict = {"closed": bool(square.get("closed", False))}
            arrows = square.get("arrows", [])
            if not isinstance(arrows, list):
                raise ReplicaSpecError(f"换向阀 {identifier} 的 arrows 必须是列表。")
            cleaned_arrows = []
            for arrow in arrows:
                if not isinstance(arrow, dict):
                    raise ReplicaSpecError(f"换向阀 {identifier} 的 arrow 项必须是对象。")
                cleaned_arrows.append({
                    "from": _enum(arrow.get("from"), frozenset(
                        {"left", "right", "top", "bottom", "center"}), "arrow.from"),
                    "to": _enum(arrow.get("to"), frozenset(
                        {"left", "right", "top", "bottom", "center"}), "arrow.to"),
                    "two_way": bool(arrow.get("two_way", False)),
                })
            item["arrows"] = cleaned_arrows
            blocked = square.get("blocked", [])
            if not isinstance(blocked, list):
                raise ReplicaSpecError(f"换向阀 {identifier} 的 blocked 必须是列表。")
            item["blocked"] = [
                _enum(side, frozenset({"left", "right", "top", "bottom", "center"}), "blocked")
                for side in blocked if isinstance(side, str)
            ]
            cleaned.append(item)
        result["detail"] = cleaned
        ports = element.get("ports", {})
        if not isinstance(ports, dict):
            raise ReplicaSpecError(f"换向阀 {identifier} 的 ports 必须是对象。")
        result["ports"] = {
            str(name).lower(): _text(value, f"端口 {name} 标注", limit=16)
            for name, value in ports.items() if isinstance(value, str) and value.strip()
        }
    if kind == "cylinder":
        rods = _enum(element.get("rods", "single"), frozenset({"single", "double"}),
                     f"液压缸 {identifier} 的 rods")
        result["rods"] = rods
        result["cushion"] = bool(element.get("cushion", False))
    if kind in ("pump", "motor"):
        result["variable"] = bool(element.get("variable", False))
    if kind == "sum":
        signs = element.get("signs", {})
        if not isinstance(signs, dict):
            raise ReplicaSpecError(f"比较点 {identifier} 的 signs 必须是对象。")
        cleaned_signs = {}
        for side, sign in signs.items():
            if side not in ("left", "right", "top", "bottom"):
                raise ReplicaSpecError(f"比较点 {identifier} 的 signs 方向非法：{side}")
            cleaned_signs[side] = "+" if str(sign).strip().startswith("+") else "-"
        result["signs"] = cleaned_signs
    if kind == "block" or kind == "node" or kind == "box" or kind == "circle":
        text = element.get("text", "")
        if text is not None and not isinstance(text, str):
            raise ReplicaSpecError(f"元素 {identifier} 的 text 必须是字符串。")
        result["text"] = (text or "").strip()[:160]
    if kind == "path":
        points = element.get("points", [])
        if (not isinstance(points, list) or len(points) < 2
                or any(not isinstance(point, list) or len(point) != 2 for point in points)):
            raise ReplicaSpecError(f"自由路径 {identifier} 至少需要 2 个坐标点。")
        result["points"] = [
            [_number(point[0], lo=-1e5, hi=1e5, what="path.x"),
             _number(point[1], lo=-1e5, hi=1e5, what="path.y")]
            for point in points
        ]
        result["line"] = _enum(element.get("line", "solid"), LINE_STYLES,
                               f"自由路径 {identifier} 的 line")
    if kind == "text":
        result["text"] = _text(element.get("text"), f"文本 {identifier} 的 text", limit=200)
        result["size"] = _enum(element.get("size", "normal"), frozenset(
            {"normal", "small"}), f"文本 {identifier} 的 size")
        result["anchor"] = _enum(element.get("anchor", "start"), ANCHORS, "anchor")


def _validate_connection(connection: object, canvas: dict, ids: set[str]) -> dict:
    if not isinstance(connection, dict):
        raise ReplicaSpecError("connections 列表项必须是对象。")
    identifier = connection.get("id")
    if not isinstance(identifier, str) or not _ID_PATTERN.fullmatch(identifier):
        raise ReplicaSpecError(f"连线 id 非法：{identifier!r}")
    points = connection.get("points", [])
    if (not isinstance(points, list) or len(points) < 2
            or any(not isinstance(point, list) or len(point) != 2 for point in points)):
        raise ReplicaSpecError(f"连线 {identifier} 至少需要 2 个坐标点。")
    limit = max(canvas["width"], canvas["height"]) * 1.4
    cleaned_points: list[list[float]] = []
    for point in points:
        x = _number(point[0], lo=-limit, hi=limit, what=f"连线 {identifier} 的 x")
        y = _number(point[1], lo=-limit, hi=limit, what=f"连线 {identifier} 的 y")
        if cleaned_points:
            previous = cleaned_points[-1]
            if abs(previous[0] - x) < .5 and abs(previous[1] - y) < .5:
                raise ReplicaSpecError(f"连线 {identifier} 含重复相邻坐标点。")
        cleaned_points.append([x, y])
    result: dict = {"id": identifier, "points": cleaned_points}
    for key in ("attach_from", "attach_to"):
        attach = connection.get(key)
        if attach is None:
            result[key] = None
            continue
        if not isinstance(attach, str) or attach.count(".") != 1:
            raise ReplicaSpecError(f"连线 {identifier} 的 {key} 必须形如 元素id.端口。")
        element_id, port = attach.split(".")
        if element_id not in ids:
            raise ReplicaSpecError(f"连线 {identifier} 引用了不存在的元素 {element_id}。")
        if port.lower() not in _PORT_NAMES:
            raise ReplicaSpecError(f"连线 {identifier} 的端口名 {port} 不受支持。")
        result[key] = f"{element_id}.{port.lower()}"
    result["line"] = _enum(connection.get("line", "solid"), LINE_STYLES,
                           f"连线 {identifier} 的 line")
    result["arrow"] = _enum(connection.get("arrow", "none"), ARROW_MODES,
                            f"连线 {identifier} 的 arrow")
    result["flow"] = _enum(connection.get("flow", "none"), frozenset(
        {"none", "forward", "backward"}), f"连线 {identifier} 的 flow")
    for key in ("joints", "crossings"):
        raw = connection.get(key, [])
        if not isinstance(raw, list):
            raise ReplicaSpecError(f"连线 {identifier} 的 {key} 必须是列表。")
        cleaned = []
        for point in raw:
            if not isinstance(point, list) or len(point) != 2:
                raise ReplicaSpecError(f"连线 {identifier} 的 {key} 坐标非法。")
            cleaned.append([
                _number(point[0], lo=-limit, hi=limit, what=f"{key}.x"),
                _number(point[1], lo=-limit, hi=limit, what=f"{key}.y"),
            ])
        result[key] = cleaned
    label = connection.get("label")
    if label is not None:
        if isinstance(label, str):
            result["label"] = {"text": label.strip()[:80]}
        elif isinstance(label, dict):
            entry = {"text": _text(label.get("text"), f"连线 {identifier} 的 label.text", limit=80)}
            at = label.get("at")
            if isinstance(at, list) and len(at) == 2:
                entry["at"] = [
                    _number(at[0], lo=-limit, hi=limit, what="label.at.x"),
                    _number(at[1], lo=-limit, hi=limit, what="label.at.y"),
                ]
            result["label"] = entry
    return result


def _validate_curve_axes(axes: object) -> dict:
    if not isinstance(axes, dict):
        raise ReplicaSpecError("curve 图的 axes 必须是对象。")
    result = {}
    for axis in ("x", "y"):
        spec = axes.get(axis, {})
        if not isinstance(spec, dict):
            raise ReplicaSpecError(f"axes.{axis} 必须是对象。")
        entry: dict = {"label": str(spec.get("label", "")).strip()[:60]}
        for key in ("range", "ticks"):
            values = spec.get(key, [])
            if not isinstance(values, list) or not all(
                    type(v) in (int, float) and math.isfinite(v) for v in values
            ):
                raise ReplicaSpecError(f"axes.{axis}.{key} 必须是数字列表。")
            entry[key] = [float(v) for v in values]
        result[axis] = entry
    return result


def _validate_curves(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        raise ReplicaSpecError("curves 必须是列表。")
    result = []
    for curve in raw:
        if not isinstance(curve, dict):
            raise ReplicaSpecError("curves 列表项必须是对象。")
        points = curve.get("points", [])
        if (not isinstance(points, list) or len(points) < 2
                or any(not isinstance(point, list) or len(point) != 2 for point in points)):
            raise ReplicaSpecError("曲线至少需要 2 个数据点（物理坐标）。")
        result.append({
            "id": _text(curve.get("id", "curve"), "curves.id", limit=32),
            "points": [[float(p[0]), float(p[1])] for p in points],
            "style": _enum(curve.get("style", "solid"), LINE_STYLES, "curves.style"),
            "label": str(curve.get("label", "")).strip()[:60],
        })
    return result


def port_anchor(element: dict, port: str) -> tuple[float, float]:
    """Return the canvas-space anchor of one named port for an element."""

    x, y, w, h = element["x"], element["y"], element["w"], element["h"]
    port = port.lower()
    if port in ("left", "t1", "in1", "rodl"):
        return (x - w / 2, y)
    if port in ("right", "t2", "in2", "rodr", "out", "out1", "out2"):
        return (x + w / 2, y)
    if port == "top":
        return (x, y - h / 2)
    if port == "bottom":
        return (x, y + h / 2)
    if port == "a":
        return (x, y - h / 2)
    if port == "b":
        return (x + w / 2, y)
    if port == "p":
        return (x - w / 2, y)
    if port == "t":
        return (x, y + h / 2)
    return (x, y)


def parse_replica_spec(payload: object) -> dict:
    """Validate and normalize one model-proposed replica spec (fail-closed)."""

    if not isinstance(payload, dict):
        raise ReplicaSpecError("复刻结构必须是 JSON 对象。")
    if payload.get("schema") != SCHEMA_VERSION:
        raise ReplicaSpecError(f"schema 必须是 {SCHEMA_VERSION}。")
    figure_type = _enum(payload.get("figure_type"), FIGURE_TYPES, "figure_type")
    canvas_raw = payload.get("canvas")
    width = canvas_raw.get("width") if isinstance(canvas_raw, dict) else None
    if type(width) is not int or not 120 <= width <= 4000:
        raise ReplicaSpecError("canvas.width 必须是 120..4000 的整数。")
    height = payload["canvas"].get("height")
    if type(height) is not int or not 120 <= height <= 4000:
        raise ReplicaSpecError("canvas.height 必须是 120..4000 的整数。")
    canvas = {"width": float(width), "height": float(height)}
    ids: set[str] = set()
    elements = [_validate_element(item, figure_type, canvas, ids)
                for item in payload.get("elements", [])]
    raw_connections = payload.get("connections", [])
    if not isinstance(raw_connections, list):
        raise ReplicaSpecError("connections 必须是列表。")
    # A connection with no usable polyline is dropped and recorded as a note
    # instead of failing the whole figure (dense schematics occasionally
    # produce one empty connection).
    dropped: list[str] = []
    usable: list[object] = []
    for item in raw_connections:
        points = item.get("points") if isinstance(item, dict) else None
        if isinstance(points, list) and len(points) >= 2:
            usable.append(item)
        elif isinstance(item, dict) and isinstance(item.get("id"), str):
            dropped.append(item["id"])
    connections = [_validate_connection(item, canvas, ids) for item in usable]
    dropped_notes = [f"连线 {name} 坐标不足，已忽略。" for name in dropped]
    conn_ids = {c["id"] for c in connections}
    if len(conn_ids) != len(connections):
        raise ReplicaSpecError("connections 的 id 重复。")
    annotations = []
    for annotation in payload.get("annotations", []):
        if not isinstance(annotation, dict):
            raise ReplicaSpecError("annotations 列表项必须是对象。")
        annotations.append({
            "x": _number(annotation.get("x"), lo=-1e5, hi=1e5, what="annotation.x"),
            "y": _number(annotation.get("y"), lo=-1e5, hi=1e5, what="annotation.y"),
            "text": _text(annotation.get("text"), "annotation.text", limit=200),
            "anchor": _enum(annotation.get("anchor", "start"), ANCHORS, "annotation.anchor"),
            "size": _enum(annotation.get("size", "normal"), frozenset(
                {"normal", "small"}), "annotation.size"),
        })
    notes_raw = payload.get("notes", [])
    if isinstance(notes_raw, str):
        notes_raw = [notes_raw]
    notes = [str(note).strip()[:200] for note in notes_raw
             if isinstance(note, (str, int, float)) and str(note).strip()]
    notes = dropped_notes + notes
    result = {
        "schema": SCHEMA_VERSION, "figure_type": figure_type, "canvas": canvas,
        "elements": elements, "connections": connections,
        "annotations": annotations, "notes": notes,
    }
    if figure_type == "curve":
        result["axes"] = _validate_curve_axes(payload.get("axes", {}))
        result["curves"] = _validate_curves(payload.get("curves", []))
    if not elements and not connections and not result.get("curves"):
        raise ReplicaSpecError("复刻结构为空：没有任何元素或连线。")
    return result


def verify_replica_spec(spec: dict) -> ReplicaCheck:
    """Deterministic topology/geometry checks; never silently mutates the spec.

    Port snaps (endpoint pulled onto the referenced anchor) are recorded and
    applied by the renderer only, so the stored spec stays the model's own
    proposal.  Free ``path`` elements and unused attachments surface as
    warnings so the visual check knows where to look.
    """

    warnings: list[str] = []
    snapped: list[dict] = []
    unverifiable: list[str] = []
    by_id = {element["id"]: element for element in spec["elements"]}
    for element in spec["elements"]:
        if element["kind"] == "path":
            unverifiable.append(f"自由路径 {element['id']} 无法做拓扑核验")
    for connection in spec["connections"]:
        for side, points_index in (("attach_from", 0), ("attach_to", -1)):
            attach = connection.get(side)
            if not attach:
                continue
            element_id, port = attach.split(".")
            anchor = port_anchor(by_id[element_id], port)
            endpoint = connection["points"][points_index]
            distance = math.hypot(endpoint[0] - anchor[0], endpoint[1] - anchor[1])
            if distance > SNAP_TOLERANCE:
                warnings.append(
                    f"连线 {connection['id']} 的 {side} 端点偏离 {element_id}.{port} "
                    f"锚点 {distance:.0f} 单位（阈值 {SNAP_TOLERANCE:g}）。"
                )
            elif distance > 1.0:
                snapped.append({"connection": connection["id"], "port": attach,
                                "moved_units": round(distance, 1)})
        if connection.get("arrow", "none") != "none" and not (
                connection.get("attach_from") or connection.get("attach_to")):
            unverifiable.append(f"连线 {connection['id']} 的箭头端没有锚定的元素。")
    label_count = sum(len(element.get("labels", [])) for element in spec["elements"])
    label_count += sum(1 for c in spec["connections"] if c.get("label"))
    label_count += len(spec.get("annotations", []))
    ok = not warnings
    return ReplicaCheck(
        ok=ok, element_count=len(spec["elements"]),
        connection_count=len(spec["connections"]), label_count=label_count,
        snapped=snapped, warnings=warnings, unverifiable=unverifiable,
    )
