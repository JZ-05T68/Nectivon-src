"""Deterministic SVG/PNG renderer for validated replica specs.

One primitive layer feeds two backends (vector SVG for traceability, PNG for
the Streamlit display), so both outputs share exact geometry.  Symbol
libraries draw standard textbook forms (GB/ISO-style hydraulic symbols,
control block diagrams, circuit elements and data curves).  The renderer
applies only the port snaps the schema verifier recorded — it never invents
or "beautifies" structure.
"""

from __future__ import annotations

import io
import math
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from src.figure_replica_schema import SNAP_TOLERANCE, port_anchor, verify_replica_spec

_STROKE = "#1a1a1a"
_DASH: dict[str, tuple[float, ...]] = {
    "solid": (), "dashed": (14, 9), "dash_dot": (18, 6, 3, 6), "dotted": (2.5, 8),
}
_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\arial.ttf",
)
_SEGMENTS = re.compile(r"(_\{[^}]*\}|\^\{[^}]*\}|_[A-Za-z0-9ητωπα-ωΑ-Ω]|\^[A-Za-z0-9])")

#: Minimal LaTeX-command → Unicode mapping so block/label text like
#: ``\frac{1}{s^2}`` or ``\tau s`` renders as textbook glyphs instead of raw
#: source code.  Semantics are preserved; nothing is re-derived.
_LATEX_SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "Delta": "Δ",
    "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ",
    "vartheta": "ϑ", "iota": "ι", "kappa": "κ", "lambda": "λ", "Lambda": "Λ",
    "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "Pi": "Π", "rho": "ρ",
    "sigma": "σ", "Sigma": "Σ", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "varphi": "φ", "Phi": "Φ", "chi": "χ", "psi": "ψ", "Psi": "Ψ",
    "omega": "ω", "Omega": "Ω", "times": "×", "cdot": "·", "pm": "±",
    "mp": "∓", "leq": "≤", "geq": "≥", "neq": "≠", "approx": "≈",
    "equiv": "≡", "infty": "∞", "propto": "∝", "partial": "∂",
    "rightarrow": "→", "to": "→", "leftarrow": "←",
}
_FRAC_PATTERN = re.compile(r"\\?frac\{((?:[^{}]|\{[^{}]*})*)\}\{((?:[^{}]|\{[^{}]*})*)\}")
_SYMBOL_PATTERN = re.compile(r"\\([a-zA-Z]+)")


def _latex_to_text(text: str) -> str:
    """Render simple LaTeX fragments to plain Unicode without changing meaning."""

    def fraction(match: re.Match) -> str:
        numerator, denominator = match.group(1).strip(), match.group(2).strip()
        simple = re.fullmatch(r"[A-Za-z0-9.,]+", numerator) and re.fullmatch(
            r"[A-Za-z0-9.,^_{}]+|\^[A-Za-z0-9]|\s*", denominator)
        if simple:
            return f"{numerator}/{denominator}"
        return f"({numerator})/({denominator})"

    text = _FRAC_PATTERN.sub(fraction, text)
    text = _SYMBOL_PATTERN.sub(
        lambda m: _LATEX_SYMBOLS.get(m.group(1), m.group(1)), text)
    text = (text.replace("\\left", "").replace("\\right", "")
            .replace("\\,", " ").replace("\\;", " ").replace("\\ ", " "))
    return text.replace("\\", "")


def _parse_text(text: str) -> list[tuple[str, str]]:
    """Split label text into (segment, mode) with ``_{}``/``^{}`` grammar."""

    text = _latex_to_text(str(text))
    parts: list[tuple[str, str]] = []
    for chunk in _SEGMENTS.split(text):
        if not chunk:
            continue
        if chunk.startswith("_{"):
            parts.append((chunk[2:-1], "sub"))
        elif chunk.startswith("^{"):
            parts.append((chunk[2:-1], "sup"))
        else:
            parts.append((chunk, "main"))
    return parts or [(" ", "main")]


@lru_cache(maxsize=1)
def _font_path() -> str | None:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


@lru_cache(maxsize=64)
def _font(size_px: int):
    path = _font_path()
    if path is None:
        return None
    try:
        return ImageFont.truetype(path, size_px)
    except OSError:
        return None


def _char_units(text: str) -> float:
    return sum(1.0 if ord(ch) > 0x2E7F else 0.55 for ch in text)


def _wrap_text(text: str, size: float, max_width: float) -> list[str]:
    """Wrap on segment boundaries; sub/sup segments stick to their base."""

    lines: list[str] = []
    for raw_line in str(text).splitlines() or [text]:
        current = ""
        for chunk in _SEGMENTS.split(_latex_to_text(raw_line)):
            if not chunk:
                continue
            glued = chunk.startswith("_") or chunk.startswith("^")
            overflow = (_char_units(current + chunk) * size) > max_width
            if overflow and current and not glued:
                lines.append(current)
                current = chunk.lstrip()
            else:
                current += chunk
        lines.append(current)
    return lines or [""]


class ReplicaCanvas:
    """Collect geometry primitives in canvas units; emit SVG and PNG."""

    def __init__(self, width: float, height: float, *, px_width: int = 1400):
        self.width, self.height = float(width), float(height)
        self.px_width = px_width
        self.scale = px_width / self.width
        self.ops: list[tuple] = []

    def line(self, x1, y1, x2, y2, *, width=2.4, line="solid", color=_STROKE):
        self.ops.append(("line", (x1, y1, x2, y2), width, _DASH.get(line, ()), color))

    def polyline(self, points, *, width=2.4, line="solid", color=_STROKE):
        for (x1, y1), (x2, y2) in zip(points, points[1:], strict=False):
            self.line(x1, y1, x2, y2, width=width, line=line, color=color)

    def rect(self, cx, cy, w, h, *, width=2.4, fill="#ffffff", line="solid"):
        self.ops.append(("rect", (cx - w / 2, cy - h / 2, w, h), width,
                         _DASH.get(line, ()), fill))

    def circle(self, cx, cy, r, *, width=2.4, fill="#ffffff"):
        self.ops.append(("ellipse", (cx - r, cy - r, cx + r, cy + r), width, (), fill))

    def polygon(self, points, *, fill=_STROKE, outline=False, width=2.0):
        self.ops.append(("polygon", tuple(points), width if outline else 0, (), fill))

    def arc(self, cx, cy, rx, ry, a0, a1, *, width=2.4):
        self.ops.append(("arc", (cx, cy, rx, ry, a0, a1), width, (), _STROKE))

    def text(self, x, y, content, size=30, *, anchor="start", color=_STROKE):
        self.ops.append(("text", (x, y, str(content), size, anchor), 0, (), color))

    def arrowhead(self, x, y, angle, *, size=13.0, fill=_STROKE):
        rad = math.radians(angle)
        back = (x - size * math.cos(rad), y - size * math.sin(rad))
        left = (back[0] + size * .45 * math.cos(rad + math.pi / 2),
                back[1] + size * .45 * math.sin(rad + math.pi / 2))
        right = (back[0] + size * .45 * math.cos(rad - math.pi / 2),
                 back[1] + size * .45 * math.sin(rad - math.pi / 2))
        self.polygon([(x, y), left, right], fill=fill)

    def open_arrow(self, x, y, angle, *, size=15.0):
        """Open (stroke-only) arrowhead used on signal/flow directions."""
        rad = math.radians(angle)
        for sign in (math.pi / 2, -math.pi / 2):
            tip = (x - size * math.cos(rad + sign * .5),
                   y - size * math.sin(rad + sign * .5))
            self.line(x, y, *tip, width=2.2)

    def directed_segment(self, x1, y1, x2, y2, *, width=2.4, line="solid",
                         arrow="none", head_scale=1.0):
        self.line(x1, y1, x2, y2, width=width, line=line)
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
        if arrow in ("end", "both"):
            self.arrowhead(x2, y2, angle, size=13 * head_scale)
        if arrow in ("start", "both"):
            self.arrowhead(x1, y1, angle + 180, size=13 * head_scale)

    # -- backends -----------------------------------------------------------
    def to_svg(self) -> bytes:
        height = round(self.height * self.scale)
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.px_width}" '
            f'height="{height}" viewBox="0 0 {self.width:g} {self.height:g}">',
            f'<rect width="{self.width:g}" height="{self.height:g}" fill="#ffffff"/>',
            '<g stroke-linecap="round" stroke-linejoin="round" '
            'font-family="Microsoft YaHei, SimHei, Arial, sans-serif">',
        ]
        for op, coords, width, dash, color in self.ops:
            dash_attr = (
                f' stroke-dasharray="{" ".join(str(v) for v in dash)}"' if dash else "")
            if op == "line":
                x1, y1, x2, y2 = coords
                parts.append(
                    f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                    f'stroke="{color}" stroke-width="{width:.2f}"{dash_attr}/>')
            elif op == "rect":
                x1, y1, w, h = coords
                fill_attr = f' fill="{color}"' if color and color != "none" else ' fill="none"'
                parts.append(
                    f'<rect x="{x1:.2f}" y="{y1:.2f}" width="{w:.2f}" height="{h:.2f}" '
                    f'stroke="{_STROKE}" stroke-width="{width:.2f}"{dash_attr}{fill_attr}/>')
            elif op == "ellipse":
                x1, y1, x2, y2 = coords
                fill_attr = f' fill="{color}"' if color and color != "none" else ' fill="none"'
                parts.append(
                    f'<ellipse cx="{(x1 + x2) / 2:.2f}" cy="{(y1 + y2) / 2:.2f}" '
                    f'rx="{(x2 - x1) / 2:.2f}" ry="{(y2 - y1) / 2:.2f}" '
                    f'stroke="{_STROKE}" stroke-width="{width:.2f}"{fill_attr}/>')
            elif op == "polygon":
                points = " ".join(f"{x:.2f},{y:.2f}" for x, y in coords)
                outline = (f' stroke="{_STROKE}" stroke-width="{width:.2f}"'
                           if width else ' stroke="none"')
                parts.append(f'<polygon points="{points}" fill="{color}"{outline}/>')
            elif op == "arc":
                cx, cy, rx, ry, a0, a1 = coords
                start = (cx + rx * math.cos(math.radians(a0)), cy + ry * math.sin(math.radians(a0)))
                end = (cx + rx * math.cos(math.radians(a1)), cy + ry * math.sin(math.radians(a1)))
                large = 1 if (a1 - a0) % 360 > 180 else 0
                parts.append(
                    f'<path d="M {start[0]:.2f} {start[1]:.2f} A {rx:.2f} {ry:.2f} 0 '
                    f'{large} 1 {end[0]:.2f} {end[1]:.2f}" stroke="{color}" '
                    f'stroke-width="{width:.2f}" fill="none"/>')
            elif op == "text":
                x, y, content, size, anchor = coords
                anchor_attr = {"start": "start", "middle": "middle", "end": "end"}[anchor]
                cursor = x
                for segment, mode in _parse_text(content):
                    seg_size = size if mode == "main" else size * 0.62
                    dy = 0 if mode == "main" else (0.26 * size if mode == "sub" else -0.26 * size)
                    escaped = (segment.replace("&", "&amp;").replace("<", "&lt;")
                               .replace(">", "&gt;"))
                    parts.append(
                        f'<text x="{cursor:.2f}" y="{y + dy:.2f}" font-size="{seg_size:.1f}" '
                        f'fill="{color}" text-anchor="{anchor_attr}" '
                        f'dominant-baseline="middle" stroke="#ffffff" '
                        f'stroke-width="{seg_size * .28:.1f}" paint-order="stroke" '
                        f'stroke-linejoin="round">{escaped}</text>')
                    cursor += _char_units(segment) * seg_size
        parts.append("</g></svg>")
        return "\n".join(parts).encode("utf-8")

    def to_png(self) -> bytes:
        sample = 2
        scale = self.scale * sample
        width_px = round(self.width * scale)
        height_px = round(self.height * scale)
        image = Image.new("RGB", (width_px, height_px), "#ffffff")
        draw = ImageDraw.Draw(image)
        for op, coords, width, _dash, color in self.ops:
            if op == "line":
                x1, y1, x2, y2 = coords
                draw.line([x1 * scale, y1 * scale, x2 * scale, y2 * scale],
                          fill=color, width=max(1, round(width * scale)), joint="curve")
            elif op == "rect":
                x1, y1, w, h = coords
                draw.rectangle([x1 * scale, y1 * scale, (x1 + w) * scale, (y1 + h) * scale],
                               fill=color if color and color != "none" else None,
                               outline=_STROKE, width=max(1, round(width * scale)))
            elif op == "ellipse":
                x1, y1, x2, y2 = coords
                draw.ellipse([x1 * scale, y1 * scale, x2 * scale, y2 * scale],
                             fill=color if color and color != "none" else None,
                             outline=_STROKE, width=max(1, round(width * scale)))
            elif op == "polygon":
                draw.polygon([(x * scale, y * scale) for x, y in coords], fill=color,
                             outline=_STROKE if width else None,
                             width=max(1, round(width * scale)) if width else 1)
            elif op == "arc":
                cx, cy, rx, ry, a0, a1 = coords
                draw.arc([cx * scale - rx * scale, cy * scale - ry * scale,
                          cx * scale + rx * scale, cy * scale + ry * scale],
                         start=a0, end=a1, fill=color, width=max(1, round(width * scale)))
            elif op == "text":
                x, y, content, size, anchor = coords
                _draw_text(draw, x * scale, y * scale, content, size * scale,
                           anchor=anchor, color=color)
        image = image.resize((self.px_width, round(height_px / sample)), Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


def _draw_text(draw: ImageDraw.ImageDraw, x: float, y: float, content: str,
               size_px: float, *, anchor: str = "start", color: str = _STROKE) -> None:
    """Draw text with ``_{sub}``/``^{sup}`` support; anchor is the middle row."""

    segments = _parse_text(content)
    total = 0.0
    for segment, mode in segments:
        px = max(8, round(size_px * (0.62 if mode != "main" else 1.0)))
        font = _font(px)
        total += (draw.textlength(segment, font=font) if font else _char_units(segment) * px)
    cursor = {"start": x, "middle": x - total / 2, "end": x - total}[anchor]
    for segment, mode in segments:
        px = max(8, round(size_px * (0.62 if mode != "main" else 1.0)))
        font = _font(px)
        offset = 0 if mode == "main" else (
            round(0.30 * size_px) if mode == "sub" else round(-0.34 * size_px))
        if font is None:
            draw.text((cursor, y + offset - px / 2), segment, fill=color,
                      stroke_width=max(2, round(px * .14)), stroke_fill="#ffffff")
        else:
            draw.text((cursor, y + offset), segment, fill=color, font=font, anchor="lm",
                      stroke_width=max(2, round(px * .14)), stroke_fill="#ffffff")
        cursor += (draw.textlength(segment, font=font) if font else _char_units(segment) * px)


# ---------------------------------------------------------------------------
# Symbol libraries
# ---------------------------------------------------------------------------

def _quarter(rotate: float) -> int:
    return round((rotate or 0) / 90.0) % 4


class _Local:
    """Local→canvas coordinate mapping with quarter-turn rotation."""

    def __init__(self, element: dict):
        self.cx, self.cy = element["x"], element["y"]
        self.turn = _quarter(element.get("rotate", 0))

    def point(self, dx: float, dy: float) -> tuple[float, float]:
        if self.turn == 0:
            return (self.cx + dx, self.cy + dy)
        if self.turn == 1:
            return (self.cx - dy, self.cy + dx)
        if self.turn == 2:
            return (self.cx - dx, self.cy - dy)
        return (self.cx + dy, self.cy - dx)

    def line(self, canvas, d1, d2, **kwargs):
        canvas.line(*self.point(*d1), *self.point(*d2), **kwargs)


def _spring(canvas: ReplicaCanvas, x1, y1, x2, y2, *, coils=3, width=2.0):
    angle = math.atan2(y2 - y1, x2 - x1)
    length = math.hypot(x2 - x1, y2 - y1)
    nx, ny = math.cos(angle), math.sin(angle)
    px, py = -ny, nx
    amplitude = min(10.0, length / (coils * 2 + 2))
    points: list[tuple[float, float]] = [(x1, y1)]
    steps = coils * 2 + 2
    for index in range(1, steps):
        t = length * index / steps
        sign = amplitude if index % 2 else -amplitude
        points.append((x1 + nx * t + px * sign, y1 + ny * t + py * sign))
    points.append((x2, y2))
    canvas.polyline(points, width=width)


def _draw_valve(canvas: ReplicaCanvas, element: dict) -> None:
    """Directional valve: N position squares with internal path glyphs."""
    squares = element.get("squares", 2)
    w, h = element["w"], element["h"]
    vertical = _quarter(element.get("rotate", 0)) % 2 == 1
    local = _Local(element)
    size = (h if vertical else w) / squares
    detail = element.get("detail", [])
    half = size / 2
    sides = {"left": (-half, 0), "right": (half, 0), "top": (0, -half),
             "bottom": (0, half), "center": (0, 0)}

    def square_offset(index: int) -> float:
        return (index - (squares - 1) / 2) * size

    for index in range(squares):
        offset = square_offset(index)
        base = (0, offset) if vertical else (offset, 0)
        corners = [(base[0] - half, base[1] - half), (base[0] + half, base[1] - half),
                   (base[0] + half, base[1] + half), (base[0] - half, base[1] + half)]
        canvas.polygon([local.point(*c) for c in corners], fill="#ffffff", outline=True,
                       width=2.4)
        entry = detail[index] if index < len(detail) else {}
        for blocked in entry.get("blocked", []):
            bx, by = sides.get(blocked, (0, 0))
            if blocked in ("left", "right"):
                local.line(canvas, (base[0] + bx, base[1] - size * .16),
                           (base[0] + bx, base[1] + size * .16), width=2.4)
                local.line(canvas, (base[0] + bx * .55, base[1]),
                           (base[0] + bx, base[1]), width=2.4)
            else:
                local.line(canvas, (base[0] - size * .16, base[1] + by),
                           (base[0] + size * .16, base[1] + by), width=2.4)
                local.line(canvas, (base[0], base[1] + by * .55),
                           (base[0], base[1] + by), width=2.4)
        if entry.get("closed"):
            local.line(canvas, (base[0] - size * .18, base[1] - size * .22),
                       (base[0] - size * .18, base[1] + size * .22), width=2.6)
            local.line(canvas, (base[0] + size * .18, base[1] - size * .22),
                       (base[0] + size * .18, base[1] + size * .22), width=2.6)
        for arrow in entry.get("arrows", []):
            start = (base[0] + sides.get(arrow["from"], (0, 0))[0],
                     base[1] + sides.get(arrow["from"], (0, 0))[1])
            end = (base[0] + sides.get(arrow["to"], (0, 0))[0],
                   base[1] + sides.get(arrow["to"], (0, 0))[1])
            p1, p2 = local.point(*start), local.point(*end)
            mode = "both" if arrow.get("two_way") else "end"
            canvas.directed_segment(*p1, *p2, width=2.2, arrow=mode)
    for name, label in element.get("ports", {}).items():
        port = name.lower()
        if port not in ("top", "bottom", "left", "right"):
            continue
        ax, ay = port_anchor(element, port)
        if abs(ax - element["x"]) > abs(ay - element["y"]):
            lx, ly = ax + (40 if ax > element["x"] else -40), ay
        else:
            lx, ly = ax, ay + (44 if ay > element["y"] else -44)
        canvas.text(lx, ly, label, size=26, anchor="middle")


def _draw_tank(canvas: ReplicaCanvas, element: dict) -> None:
    local = _Local(element)
    w, h = element["w"], element["h"]
    local.line(canvas, (-w / 2, -h / 2), (-w / 2, h / 2))
    local.line(canvas, (-w / 2, h / 2), (w / 2, h / 2))
    local.line(canvas, (w / 2, h / 2), (w / 2, -h / 2))


def _draw_pump(canvas: ReplicaCanvas, element: dict) -> None:
    local = _Local(element)
    r = min(element["w"], element["h"]) / 2
    cx, cy = local.point(0, 0)
    canvas.circle(cx, cy, r)
    canvas.polygon([local.point(r * .82, 0), local.point(-r * .18, -r * .45),
                    local.point(-r * .18, r * .45)], fill=_STROKE)
    if element.get("variable"):
        canvas.directed_segment(*local.point(-r * .70, r * .70),
                                *local.point(r * .70, -r * .70), width=1.8,
                                arrow="end", head_scale=.7)


def _draw_motor(canvas: ReplicaCanvas, element: dict) -> None:
    local = _Local(element)
    r = min(element["w"], element["h"]) / 2
    cx, cy = local.point(0, 0)
    canvas.circle(cx, cy, r)
    canvas.polygon([local.point(-r * .82, 0), local.point(r * .18, -r * .45),
                    local.point(r * .18, r * .45)], fill=_STROKE)


def _draw_cylinder(canvas: ReplicaCanvas, element: dict) -> None:
    local = _Local(element)
    w, h = element["w"], element["h"]
    body_w = w * .74 if element.get("rods") != "double" else w * .70
    local.line(canvas, (-w / 2, -h / 2), (-w / 2, h / 2))
    local.line(canvas, (-w / 2, -h / 2), (body_w / 2, -h / 2))
    local.line(canvas, (-w / 2, h / 2), (body_w / 2, h / 2))
    local.line(canvas, (body_w / 2, -h / 2), (body_w / 2, -h * .12))
    local.line(canvas, (body_w / 2, h / 2), (body_w / 2, h * .12))
    piston = -body_w / 2 + body_w * .38
    local.line(canvas, (piston, -h * .42), (piston, h * .42), width=3.6)
    # Solid rod: heavy stroke clearly crossing the open end of the body.
    local.line(canvas, (piston, 0), (w * .56, 0), width=5.2)
    if element.get("rods") == "double":
        local.line(canvas, (piston, 0), (-w * .56, 0), width=5.2)
        local.line(canvas, (-w / 2, -h * .12), (-w / 2, h * .12), width=2.4)
    if element.get("cushion"):
        local.line(canvas, (body_w / 2 - w * .12, -h * .30),
                   (body_w / 2, -h * .12), width=1.8)
        local.line(canvas, (body_w / 2 - w * .12, h * .30),
                   (body_w / 2, h * .12), width=1.8)


def _draw_pressure_valve(canvas: ReplicaCanvas, element: dict) -> None:
    """Relief/reducing valve: shell with internal flow arrows; spring and
    its adjustable arrow sit BELOW the shell (textbook convention)."""
    local = _Local(element)
    w, h = element["w"], element["h"]
    body_h = h * .60
    local.line(canvas, (-w / 2, -body_h / 2), (w / 2, -body_h / 2))
    local.line(canvas, (-w / 2, body_h / 2), (w / 2, body_h / 2))
    local.line(canvas, (-w / 2, -body_h / 2), (-w / 2, body_h / 2))
    local.line(canvas, (w / 2, -body_h / 2), (w / 2, body_h / 2))
    canvas.directed_segment(*local.point(-w * .28, -body_h / 2),
                            *local.point(w * .02, -body_h / 2), width=2.2,
                            arrow="end", head_scale=.75)
    canvas.directed_segment(*local.point(w * .02, -body_h / 2),
                            *local.point(w / 2, -body_h / 2), width=2.2,
                            arrow="end", head_scale=.75)
    _spring(canvas, *local.point(0, body_h / 2), *local.point(0, h / 2))
    canvas.directed_segment(*local.point(-w * .45, h * .08),
                            *local.point(w * .45, h * .40), width=1.8,
                            arrow="end", head_scale=.7)


def _draw_throttle(canvas: ReplicaCanvas, element: dict) -> None:
    """Throttle: inline lens (two arcs) on the pipe; adjustable adds an arrow."""
    local = _Local(element)
    w, h = element["w"], element["h"]
    rx, ry = w * .30, h * .46
    cx, cy = local.point(0, 0)
    canvas.line(*local.point(-w / 2, 0), *local.point(-rx, 0), width=2.4)
    canvas.line(*local.point(rx, 0), *local.point(w / 2, 0), width=2.4)
    canvas.arc(cx - rx, cy, rx, ry, 90, 270, width=2.4)
    canvas.arc(cx + rx, cy, rx, ry, 270, 450, width=2.4)
    if element.get("variable"):
        canvas.directed_segment(*local.point(-w * .38, h * .38),
                                *local.point(w * .38, -h * .38), width=1.8,
                                arrow="end", head_scale=.7)


def _draw_check_valve(canvas: ReplicaCanvas, element: dict) -> None:
    local = _Local(element)
    w, h = element["w"], element["h"]
    seat_x, gap = -w * .12, h * .16
    local.line(canvas, (seat_x, -h * .42), (w * .10, -gap), width=2.4)
    local.line(canvas, (seat_x, h * .42), (w * .10, gap), width=2.4)
    ball_x = w * .18
    r = h * .20
    bx, by = local.point(ball_x, 0)
    canvas.circle(bx, by, r)
    _spring(canvas, *local.point(ball_x + r, 0), *local.point(w / 2, 0), coils=2, width=1.6)
    local.line(canvas, (-w / 2, 0), (seat_x, 0), width=2.4)
    local.line(canvas, (w / 2, 0), (w / 2 + 1, 0), width=2.4)


def _draw_gauge(canvas: ReplicaCanvas, element: dict) -> None:
    r = min(element["w"], element["h"]) / 2
    canvas.circle(element["x"], element["y"], r)
    canvas.line(element["x"], element["y"],
                element["x"] + r * .55, element["y"] - r * .55, width=2.0)
    canvas.line(element["x"], element["y"] + r, element["x"], element["y"] + r + element["h"] * .2,
                width=2.4)


def _draw_filter(canvas: ReplicaCanvas, element: dict) -> None:
    w, h = element["w"], element["h"]
    x, y = element["x"], element["y"]
    canvas.polygon([(x - w * .28, y), (x, y - h / 2), (x + w * .28, y), (x, y + h / 2)],
                   fill="#ffffff", outline=True, width=2.4)
    canvas.line(x - w * .16, y, x + w * .16, y, width=2.0, line="dashed")
    canvas.line(x - w / 2, y, x - w * .28, y, width=2.4)
    canvas.line(x + w * .28, y, x + w / 2, y, width=2.4)


def _draw_accumulator(canvas: ReplicaCanvas, element: dict) -> None:
    w, h = element["w"], element["h"]
    x, y = element["x"], element["y"]
    canvas.arc(x, y, w * .42, h * .50, 90, 270, width=2.4)
    canvas.arc(x, y, w * .42, h * .50, 270, 450, width=2.4)
    canvas.line(x - w * .42, y, x + w * .42, y, width=2.2)
    canvas.line(x, y - h * .50 - h * .16, x, y - h * .50, width=2.4)


def _draw_solenoid(canvas: ReplicaCanvas, element: dict) -> None:
    local = _Local(element)
    w, h = element["w"], element["h"]
    cx, cy = local.point(0, 0)
    canvas.rect(cx, cy, w, h, width=2.2)
    canvas.line(cx - w / 2, cy, cx - w / 2 - w * .5, cy, width=2.0)


def _draw_spring_element(canvas: ReplicaCanvas, element: dict) -> None:
    _spring(canvas, element["x"] - element["w"] / 2, element["y"] - element["h"] / 2,
            element["x"] + element["w"] / 2, element["y"] + element["h"] / 2)


def _draw_lever(canvas: ReplicaCanvas, element: dict) -> None:
    local = _Local(element)
    w, h = element["w"], element["h"]
    base = local.point(0, h * .3)
    tip = local.point(w * .34, -h * .42)
    canvas.line(*base, *tip, width=2.6)
    tx, ty = tip
    canvas.circle(tx, ty, max(4.0, w * .07), fill=_STROKE)


def _draw_plug(canvas: ReplicaCanvas, element: dict) -> None:
    local = _Local(element)
    w, h = element["w"], element["h"]
    local.line(canvas, (-w / 2, 0), (w * .1, 0), width=2.4)
    local.line(canvas, (w * .1, -h * .40), (w * .1, h * .40), width=3.0)


def _draw_load(canvas: ReplicaCanvas, element: dict) -> None:
    local = _Local(element)
    h = element["h"]
    # Textbook external force: a plain arrow plus its label, no extra body.
    # Direction follows rotation: 0=up, 90=right, 180=down, 270=left.
    start, end = local.point(0, h * .8), local.point(0, -h * .8)
    canvas.directed_segment(*start, *end, width=2.6, arrow="end")
    # The label itself is drawn by the generic element-label loop once.


def _draw_block(canvas: ReplicaCanvas, element: dict) -> None:
    w, h = element["w"], element["h"]
    canvas.rect(element["x"], element["y"], w, h, width=2.6)
    lines = _wrap_text(element.get("text", ""), 26, w * .86)
    line_h = 32
    start = element["y"] - (len(lines) - 1) * line_h / 2
    for index, line in enumerate(lines):
        canvas.text(element["x"], start + index * line_h, line, size=26, anchor="middle")


def _draw_sum(canvas: ReplicaCanvas, element: dict) -> None:
    r = min(element["w"], element["h"]) / 2
    x, y = element["x"], element["y"]
    canvas.circle(x, y, r)
    # Control textbooks draw the comparator as a crossed circle (⊗).
    k = r * .48
    canvas.line(x - k, y - k, x + k, y + k, width=2.0)
    canvas.line(x - k, y + k, x + k, y - k, width=2.0)
    offset = r + 18
    for side, sign in element.get("signs", {}).items():
        if side == "left":
            canvas.text(x - offset, y, sign, size=26, anchor="middle")
        elif side == "right":
            canvas.text(x + offset, y, sign, size=26, anchor="middle")
        elif side == "top":
            canvas.text(x, y - offset, sign, size=26, anchor="middle")
        elif side == "bottom":
            canvas.text(x, y + offset, sign, size=26, anchor="middle")


def _draw_junction(canvas: ReplicaCanvas, element: dict) -> None:
    r = max(4.0, min(element["w"], element["h"]) * .18)
    canvas.circle(element["x"], element["y"], r, fill=_STROKE)


def _draw_resistor(canvas: ReplicaCanvas, element: dict) -> None:
    local = _Local(element)
    w, h = element["w"], element["h"]
    local.line(canvas, (-w / 2, 0), (-w * .3, 0), width=2.4)
    local.line(canvas, (w * .3, 0), (w / 2, 0), width=2.4)
    zig: list[tuple[float, float]] = [(-w * .3, 0)]
    segments = 6
    for index in range(1, segments):
        dx = -w * .3 + w * .6 * index / segments
        dy = (h / 2) if index % 2 else (-h / 2)
        if index == segments - 1:
            dy = 0
        zig.append((dx, dy))
    zig.append((w * .3, 0))
    canvas.polyline([local.point(*p) for p in zig], width=2.4)


def _draw_capacitor(canvas: ReplicaCanvas, element: dict) -> None:
    local = _Local(element)
    w, h = element["w"], element["h"]
    local.line(canvas, (-w / 2, 0), (-w * .08, 0), width=2.4)
    local.line(canvas, (w * .08, 0), (w / 2, 0), width=2.4)
    local.line(canvas, (-w * .08, -h * .45), (-w * .08, h * .45), width=2.8)
    local.line(canvas, (w * .08, -h * .45), (w * .08, h * .45), width=2.8)


def _draw_inductor(canvas: ReplicaCanvas, element: dict) -> None:
    local = _Local(element)
    w, h = element["w"], element["h"]
    local.line(canvas, (-w / 2, 0), (-w * .32, 0), width=2.4)
    local.line(canvas, (w * .32, 0), (w / 2, 0), width=2.4)
    r = w * .16
    cx, cy = local.point(-w * .32 + r, 0)
    for _ in range(4):
        canvas.arc(cx, cy, r, h * .46, 180, 360, width=2.4)
        cx += 2 * r


def _draw_source(canvas: ReplicaCanvas, element: dict, *, ac: bool) -> None:
    r = min(element["w"], element["h"]) / 2
    x, y = element["x"], element["y"]
    canvas.circle(x, y, r)
    if ac:
        points = []
        for index in range(25):
            t = index / 24
            points.append((x - r * .6 + r * 1.2 * t,
                           y - math.sin(t * 2 * math.pi) * r * .45))
        canvas.polyline(points, width=2.2)
    else:
        canvas.line(x - r * .55, y - r * .22, x + r * .55, y - r * .22, width=2.4)
        canvas.line(x - r * .55, y + r * .22, x + r * .55, y + r * .22, width=1.6,
                    line="dotted")
        canvas.text(x + r * .72, y - r * .72, "+", size=26, anchor="middle")


def _draw_battery(canvas: ReplicaCanvas, element: dict) -> None:
    local = _Local(element)
    w, h = element["w"], element["h"]
    local.line(canvas, (-w / 2, 0), (-w * .18, 0), width=2.4)
    local.line(canvas, (w * .18, 0), (w / 2, 0), width=2.4)
    local.line(canvas, (-w * .18, -h * .45), (-w * .18, h * .45), width=3.2)
    local.line(canvas, (-w * .06, -h * .22), (-w * .06, h * .22), width=2.4)
    local.line(canvas, (w * .06, -h * .45), (w * .06, h * .45), width=3.2)
    local.line(canvas, (w * .18, -h * .22), (w * .18, h * .22), width=2.4)


def _draw_ground(canvas: ReplicaCanvas, element: dict) -> None:
    x, y = element["x"], element["y"]
    w = element["w"]
    canvas.line(x, y - element["h"] / 2, x, y, width=2.4)
    for index, width in enumerate((w, w * .62, w * .26)):
        canvas.line(x - width / 2, y + index * 12, x + width / 2, y + index * 12, width=2.6)


def _draw_terminal(canvas: ReplicaCanvas, element: dict) -> None:
    r = max(5.0, min(element["w"], element["h"]) * .30)
    canvas.circle(element["x"], element["y"], r, width=2.2)


def _draw_winding(canvas: ReplicaCanvas, element: dict) -> None:
    """Winding as textbook inductor coils (pure arcs), no enclosing box."""
    local = _Local(element)
    w, h = element["w"], element["h"]
    cy = local.point(0, 0)
    coils = 3
    span = w * .78
    r_x = span / (coils * 2)
    r_y = h * .42
    start = -span / 2
    for index in range(coils):
        center = start + r_x * (2 * index + 1)
        canvas.arc(cy[0] + center, cy[1], r_x, r_y, 180, 360, width=2.4)
    local.line(canvas, (-w / 2, 0), (-span / 2, 0), width=2.4)
    local.line(canvas, (span / 2, 0), (w / 2, 0), width=2.4)


def _draw_switch(canvas: ReplicaCanvas, element: dict) -> None:
    local = _Local(element)
    w, h = element["w"], element["h"]
    local.line(canvas, (-w / 2, 0), (-w * .1, 0), width=2.4)
    local.line(canvas, (-w * .1, 0), (w * .34, -h * .38), width=2.6)
    local.line(canvas, (w * .18, 0), (w / 2, 0), width=2.4)
    px, py = local.point(-w * .1, 0)
    canvas.circle(px, py, 4.5, fill=_STROKE)


def _draw_lamp(canvas: ReplicaCanvas, element: dict) -> None:
    r = min(element["w"], element["h"]) / 2
    x, y = element["x"], element["y"]
    canvas.circle(x, y, r, width=2.4)
    canvas.line(x - r * .7, y - r * .7, x + r * .7, y + r * .7, width=2.2)
    canvas.line(x - r * .7, y + r * .7, x + r * .7, y - r * .7, width=2.2)
    canvas.line(x - r * 1.4, y, x - r, y, width=2.4)
    canvas.line(x + r, y, x + r * 1.4, y, width=2.4)


_ELEMENT_DRAWERS = {
    "tank": _draw_tank, "pump": _draw_pump, "motor": _draw_motor,
    "cylinder": _draw_cylinder, "valve": _draw_valve,
    "relief_valve": _draw_pressure_valve, "reducing_valve": _draw_pressure_valve,
    "throttle": _draw_throttle, "check_valve": _draw_check_valve,
    "pressure_gauge": _draw_gauge, "filter": _draw_filter,
    "accumulator": _draw_accumulator, "solenoid": _draw_solenoid,
    "spring": _draw_spring_element, "lever": _draw_lever, "plug": _draw_plug,
    "load": _draw_load, "block": _draw_block, "node": _draw_block,
    "sum": _draw_sum, "junction": _draw_junction, "branch": _draw_junction,
    "box": _draw_block, "resistor": _draw_resistor, "capacitor": _draw_capacitor,
    "inductor": _draw_inductor, "source_dc": lambda c, e: _draw_source(c, e, ac=False),
    "source_ac": lambda c, e: _draw_source(c, e, ac=True),
    "battery": _draw_battery, "ground": _draw_ground, "terminal": _draw_terminal,
    "winding": _draw_winding, "switch": _draw_switch, "lamp": _draw_lamp,
    "motor_circuit": _draw_motor,
}


def _snap_connections(spec: dict, by_id: dict) -> list[dict]:
    """Apply recorded port snaps so lines terminate exactly on anchors.

    Snapping mirrors what :func:`verify_replica_spec` reports; it only moves
    endpoints that already sit within the tolerance of their anchor.
    """

    snapped: list[dict] = []
    for connection in spec["connections"]:
        points = [list(point) for point in connection["points"]]
        for side, index in (("attach_from", 0), ("attach_to", -1)):
            attach = connection.get(side)
            if not attach:
                continue
            element_id, port = attach.split(".")
            ax, ay = port_anchor(by_id[element_id], port)
            if math.hypot(points[index][0] - ax, points[index][1] - ay) <= SNAP_TOLERANCE:
                points[index] = [ax, ay]
        snapped.append({"id": connection["id"], "points": points,
                        "line": connection.get("line", "solid"),
                        "arrow": connection.get("arrow", "none"),
                        "flow": connection.get("flow", "none"),
                        "joints": connection.get("joints", []),
                        "label": connection.get("label")})
    return snapped


def _draw_connection(canvas: ReplicaCanvas, connection: dict, by_id: dict) -> None:
    points = [tuple(point) for point in connection["points"]]
    canvas.polyline(points, width=2.4, line=connection["line"])
    if len(points) >= 2:
        (x1, y1), (x2, y2) = points[-2], points[-1]
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
        if connection["arrow"] in ("end", "both"):
            canvas.arrowhead(x2, y2, angle)
        if connection["arrow"] in ("start", "both"):
            canvas.arrowhead(*points[0], angle + 180)
        if connection.get("flow", "none") != "none":
            total = sum(
                math.hypot(b[0] - a[0], b[1] - a[1])
                for a, b in zip(points, points[1:], strict=False)
            )
            target = total * .52
            walked = 0.0
            for (ax, ay), (bx, by) in zip(points, points[1:], strict=False):
                length = math.hypot(bx - ax, by - ay)
                if walked + length >= target:
                    t = (target - walked) / max(length, 1e-6)
                    mx, my = ax + (bx - ax) * t, ay + (by - ay) * t
                    direction = math.degrees(math.atan2(by - ay, bx - ax))
                    if connection["flow"] == "backward":
                        direction += 180
                    canvas.open_arrow(mx, my, direction)
                    break
                walked += length
    for jx, jy in connection.get("joints", []):
        canvas.circle(jx, jy, 5.0, fill=_STROKE)
    label = connection.get("label")
    if label:
        at = label.get("at")
        if at:
            canvas.text(at[0], at[1] - 18, label["text"], size=26, anchor="middle")
        elif len(points) >= 2:
            (x1, y1), (x2, y2) = points[0], points[-1]
            canvas.text((x1 + x2) / 2, (y1 + y2) / 2 - 22, label["text"], size=26,
                        anchor="middle")


def _draw_element(canvas: ReplicaCanvas, element: dict) -> None:
    kind = element["kind"]
    if kind == "path":
        canvas.polyline([tuple(p) for p in element["points"]], width=2.4,
                        line=element.get("line", "solid"))
    elif kind == "text":
        canvas.text(element["x"], element["y"], element.get("text", ""),
                    size=28 if element.get("size", "normal") == "normal" else 22,
                    anchor=element.get("anchor", "start"))
    elif kind == "circle":
        r = min(element["w"], element["h"]) / 2
        canvas.circle(element["x"], element["y"], r, width=2.4)
        canvas.text(element["x"], element["y"], element.get("text", ""), size=26,
                    anchor="middle")
    elif kind == "arrow_marker":
        canvas.open_arrow(element["x"], element["y"], element.get("rotate", 0))
    else:
        drawer = _ELEMENT_DRAWERS.get(kind)
        if drawer is not None:
            drawer(canvas, element)
    for label in element.get("labels", []):
        canvas.text(element["x"] + label["dx"], element["y"] + label["dy"],
                    label["text"], size=26, anchor=label["anchor"])


def _smooth_points(
    points: list[tuple[float, float]], *, samples: int = 8,
) -> list[tuple[float, float]]:
    """Catmull-Rom interpolation THROUGH every data point (no shape invented)."""

    if len(points) < 3:
        return points
    result: list[tuple[float, float]] = [points[0]]
    for index in range(len(points) - 1):
        p0 = points[index - 1] if index > 0 else points[index]
        p1, p2 = points[index], points[index + 1]
        p3 = points[index + 2] if index + 2 < len(points) else points[index + 1]
        for step in range(1, samples + 1):
            t = step / samples
            t2, t3 = t * t, t * t * t
            x = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            result.append((x, y))
    return result


def _draw_curves(canvas: ReplicaCanvas, spec: dict) -> None:
    axes = spec.get("axes", {})
    curves = spec.get("curves", [])
    width, height = spec["canvas"]["width"], spec["canvas"]["height"]
    left, right = width * .16, width * .94
    top, bottom = height * .08, height * .88

    def bounds(axis: str, values: list[float]) -> tuple[float, float]:
        declared = axes.get(axis, {}).get("range") or []
        if len(declared) == 2 and declared[0] != declared[1]:
            return float(declared[0]), float(declared[1])
        lo = min(values) if values else 0.0
        hi = max(values) if values else 1.0
        if lo == hi:
            lo, hi = lo - 1, hi + 1
        return lo, hi

    x_values = [p[0] for curve in curves for p in curve["points"]]
    y_values = [p[1] for curve in curves for p in curve["points"]]
    for axis, values, lo, hi in (("x", x_values, *bounds("x", x_values)),
                                 ("y", y_values, *bounds("y", y_values))):
        ticks = axes.get(axis, {}).get("ticks")
        if ticks:
            values = ticks
        if values:
            lo, hi = min(lo, min(values)), max(hi, max(values))
        if axis == "x":
            x_lo, x_hi = lo, hi
        else:
            y_lo, y_hi = lo, hi

    def map_x(value: float) -> float:
        return left + (value - x_lo) / (x_hi - x_lo) * (right - left)

    def map_y(value: float) -> float:
        return bottom - (value - y_lo) / (y_hi - y_lo) * (bottom - top)

    canvas.directed_segment(left - width * .04, bottom, right + width * .05, bottom,
                            width=2.6, arrow="end")
    canvas.directed_segment(left, bottom + height * .05, left, top - height * .04,
                            width=2.6, arrow="end")
    for tick in axes.get("x", {}).get("ticks", []):
        tx = map_x(tick)
        canvas.line(tx, bottom, tx, bottom + 10, width=2.0)
        canvas.text(tx, bottom + 26, _format_tick(tick), size=22, anchor="middle")
    for tick in axes.get("y", {}).get("ticks", []):
        ty = map_y(tick)
        canvas.line(left - 10, ty, left, ty, width=2.0)
        canvas.text(left - 18, ty, _format_tick(tick), size=22, anchor="end")
    x_label = axes.get("x", {}).get("label", "")
    if x_label:
        canvas.text(right + width * .03, bottom - 28, x_label, size=24, anchor="middle")
    y_label = axes.get("y", {}).get("label", "")
    if y_label:
        canvas.text(left + 10, top - height * .045, y_label, size=24, anchor="start")
    for index, curve in enumerate(curves):
        points = [(map_x(p[0]), map_y(p[1])) for p in curve["points"]]
        canvas.polyline(_smooth_points(points), width=2.6, line=curve.get("style", "solid"))
        if curve.get("label"):
            lx, ly = points[min(len(points) - 1, 1 + index)]
            canvas.text(lx + 16, ly - 16, curve["label"], size=24, anchor="start")


def _format_tick(value: float) -> str:
    text = f"{value:.6g}"
    return text.replace("-", "−")


def _is_stray_axis(element: dict, spec: dict) -> bool:
    """True for a solid path spanning the canvas like an axis line.

    The axes are rendered from the ``axes`` field; despite the prompt, the
    model sometimes also emits axis paths, which would duplicate the frame.
    """

    if element.get("kind") != "path" or element.get("line", "solid") != "solid":
        return False
    points = element.get("points", [])
    if len(points) < 2:
        return False
    xs = [p[0] for p in points] + [element.get("x", 0)]
    ys = [p[1] for p in points] + [element.get("y", 0)]
    width = spec["canvas"]["width"]
    height = spec["canvas"]["height"]
    return (max(xs) - min(xs) > width * .8 or max(ys) - min(ys) > height * .8)


def render_replica(spec: dict, *, px_width: int = 1400) -> tuple[bytes, bytes]:
    """Render one validated spec to ``(svg_bytes, png_bytes)``."""

    verify_replica_spec(spec)  # renderer contract: caller validated already
    canvas = ReplicaCanvas(spec["canvas"]["width"], spec["canvas"]["height"],
                           px_width=px_width)
    by_id = {element["id"]: element for element in spec["elements"]}
    curve_mode = spec.get("figure_type") == "curve"
    for connection in _snap_connections(spec, by_id):
        _draw_connection(canvas, connection, by_id)
    for element in spec["elements"]:
        if curve_mode and _is_stray_axis(element, spec):
            continue
        _draw_element(canvas, element)
    for annotation in spec.get("annotations", []):
        canvas.text(annotation["x"], annotation["y"], annotation["text"],
                    size=28 if annotation.get("size", "normal") == "normal" else 22,
                    anchor=annotation.get("anchor", "start"))
    if spec.get("figure_type") == "curve":
        _draw_curves(canvas, spec)
    return canvas.to_svg(), canvas.to_png()
