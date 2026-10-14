# SVG file parsing and import
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from recording.model import Recording

_SVG_NS = "{http://www.w3.org/2000/svg}"
_BEZIER_STEPS = 16


@dataclass
class SvgDocument:
    subpaths: list[list[tuple[float, float]]]
    width: float
    height: float


class SvgImportError(ValueError):
    pass


def _mat_mul(a: tuple, b: tuple) -> tuple:
    a0, a1, a2, a3, a4, a5 = a
    b0, b1, b2, b3, b4, b5 = b
    return (
        a0 * b0 + a2 * b1, a1 * b0 + a3 * b1,
        a0 * b2 + a2 * b3, a1 * b2 + a3 * b3,
        a0 * b4 + a2 * b5 + a4, a1 * b4 + a3 * b5 + a5,
    )


def _parse_transform(s: str | None) -> tuple:
    ident = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    if not s:
        return ident
    m = ident
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s):
        nums = [float(v) for v in re.findall(r"-?[\d.eE+-]+", args)]
        if name == "translate":
            tx = nums[0] if nums else 0.0
            ty = nums[1] if len(nums) > 1 else 0.0
            m = _mat_mul(m, (1, 0, 0, 1, tx, ty))
        elif name == "scale":
            sx = nums[0] if nums else 1.0
            sy = nums[1] if len(nums) > 1 else sx
            m = _mat_mul(m, (sx, 0, 0, sy, 0, 0))
        elif name == "rotate" and nums:
            a = math.radians(nums[0])
            c, sn = math.cos(a), math.sin(a)
            if len(nums) >= 3:
                cx, cy = nums[1], nums[2]
                m = _mat_mul(m, (1, 0, 0, 1, cx, cy))
                m = _mat_mul(m, (c, sn, -sn, c, 0, 0))
                m = _mat_mul(m, (1, 0, 0, 1, -cx, -cy))
            else:
                m = _mat_mul(m, (c, sn, -sn, c, 0, 0))
        elif name == "matrix" and len(nums) == 6:
            m = _mat_mul(m, tuple(nums))
    return m


def _apply(m: tuple, x: float, y: float) -> tuple[float, float]:
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


_TOKEN_RE = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|-?\d*\.?\d+(?:[eE][+-]?\d+)?")


def _parse_path_d(d: str) -> list[list[tuple[float, float]]]:
    tokens = _TOKEN_RE.findall(d)
    i = 0
    subpaths: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    x = y = start_x = start_y = 0.0
    cmd = None

    def nums(n: int) -> list[float]:
        nonlocal i
        vals = [float(tokens[i + k]) for k in range(n)]
        i += n
        return vals

    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
        if cmd is None:
            break
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            nx, ny = nums(2)
            if rel:
                nx, ny = x + nx, y + ny
            if cur:
                subpaths.append(cur)
            cur = [(nx, ny)]
            x, y, start_x, start_y = nx, ny, nx, ny
            cmd = "L" if not rel else "l"
        elif c == "L":
            nx, ny = nums(2)
            if rel:
                nx, ny = x + nx, y + ny
            cur.append((nx, ny))
            x, y = nx, ny
        elif c == "H":
            (nx,) = nums(1)
            nx = x + nx if rel else nx
            cur.append((nx, y))
            x = nx
        elif c == "V":
            (ny,) = nums(1)
            ny = y + ny if rel else ny
            cur.append((x, ny))
            y = ny
        elif c in ("C", "S"):
            vals = nums(6)
            x1, y1, x2, y2, nx, ny = vals
            if rel:
                x1, y1, x2, y2, nx, ny = x1 + x, y1 + y, x2 + x, y2 + y, nx + x, ny + y
            for t in range(1, _BEZIER_STEPS + 1):
                tt = t / _BEZIER_STEPS
                mt = 1 - tt
                bx = mt**3 * x + 3 * mt**2 * tt * x1 + 3 * mt * tt**2 * x2 + tt**3 * nx
                by = mt**3 * y + 3 * mt**2 * tt * y1 + 3 * mt * tt**2 * y2 + tt**3 * ny
                cur.append((bx, by))
            x, y = nx, ny
        elif c in ("Q", "T"):
            vals = nums(4)
            x1, y1, nx, ny = vals
            if rel:
                x1, y1, nx, ny = x1 + x, y1 + y, nx + x, ny + y
            for t in range(1, _BEZIER_STEPS + 1):
                tt = t / _BEZIER_STEPS
                mt = 1 - tt
                bx = mt**2 * x + 2 * mt * tt * x1 + tt**2 * nx
                by = mt**2 * y + 2 * mt * tt * y1 + tt**2 * ny
                cur.append((bx, by))
            x, y = nx, ny
        elif c == "A":
            vals = nums(7)
            nx, ny = vals[5], vals[6]
            if rel:
                nx, ny = nx + x, ny + y
            cur.append((nx, ny))
            x, y = nx, ny
        elif c == "Z":
            if cur:
                cur.append((start_x, start_y))
            x, y = start_x, start_y
        else:
            i += 1
    if cur:
        subpaths.append(cur)
    return subpaths


def _rect_points(x, y, w, h) -> list[tuple[float, float]]:
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]


def _ellipse_points(cx, cy, rx, ry, steps: int = 48) -> list[tuple[float, float]]:
    return [
        (cx + rx * math.cos(2 * math.pi * k / steps), cy + ry * math.sin(2 * math.pi * k / steps))
        for k in range(steps + 1)
    ]


def _f(el, name, default=0.0) -> float:
    v = el.get(name)
    return float(v) if v else default


def _strip_units(v: str | None, default: float) -> float:
    if not v:
        return default
    m = re.match(r"-?[\d.]+", v.strip())
    return float(m.group()) if m else default


def parse_svg(path: str) -> SvgDocument:
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise SvgImportError(f"Could not parse SVG: {exc}") from exc
    root = tree.getroot()

    width = _strip_units(root.get("width"), 0.0)
    height = _strip_units(root.get("height"), 0.0)
    viewbox = root.get("viewBox")
    if viewbox:
        parts = [float(v) for v in re.findall(r"-?[\d.]+", viewbox)]
        if len(parts) == 4:
            if not width:
                width = parts[2]
            if not height:
                height = parts[3]
    width = width or 100.0
    height = height or 100.0

    subpaths: list[list[tuple[float, float]]] = []

    def walk(el, parent_m: tuple) -> None:
        m = _mat_mul(parent_m, _parse_transform(el.get("transform")))
        tag = el.tag.replace(_SVG_NS, "")
        local_paths: list[list[tuple[float, float]]] = []
        if tag == "path" and el.get("d"):
            local_paths = _parse_path_d(el.get("d"))
        elif tag in ("line",):
            local_paths = [[(_f(el, "x1"), _f(el, "y1")), (_f(el, "x2"), _f(el, "y2"))]]
        elif tag in ("polyline", "polygon"):
            pts = [float(v) for v in re.findall(r"-?[\d.]+", el.get("points", ""))]
            pairs = list(zip(pts[0::2], pts[1::2]))
            if tag == "polygon" and pairs:
                pairs.append(pairs[0])
            local_paths = [pairs] if pairs else []
        elif tag == "rect":
            local_paths = [_rect_points(_f(el, "x"), _f(el, "y"), _f(el, "width"), _f(el, "height"))]
        elif tag == "circle":
            local_paths = [_ellipse_points(_f(el, "cx"), _f(el, "cy"), _f(el, "r"), _f(el, "r"))]
        elif tag == "ellipse":
            local_paths = [_ellipse_points(_f(el, "cx"), _f(el, "cy"), _f(el, "rx"), _f(el, "ry"))]

        for sp in local_paths:
            if len(sp) >= 2:
                subpaths.append([_apply(m, px, py) for px, py in sp])

        for child in el:
            walk(child, m)

    walk(root, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))

    if not subpaths:
        raise SvgImportError("No drawable paths/shapes found in this SVG.")
    return SvgDocument(subpaths=subpaths, width=width, height=height)


def place_on_bed(
    doc: SvgDocument,
    target_width_mm: float,
    target_height_mm: float,
    rotation_deg: float,
    center_x_mm: float,
    center_y_mm: float,
    travel_speed_mm_s: float,
) -> list[Recording]:
    xs = [p[0] for sp in doc.subpaths for p in sp]
    ys = [p[1] for sp in doc.subpaths for p in sp]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    src_w = max(max_x - min_x, 1e-6)
    src_h = max(max_y - min_y, 1e-6)

    sx = target_width_mm / src_w
    sy = target_height_mm / src_h
    ang = math.radians(rotation_deg)
    cos_a, sin_a = math.cos(ang), math.sin(ang)
    speed = max(travel_speed_mm_s, 1.0)

    recordings: list[Recording] = []
    for idx, sp in enumerate(doc.subpaths):
        rec = Recording()
        rec.name = f"SVG Path {idx + 1}"
        t_s = 0.0
        prev = None
        for (px, py) in sp:
            lx = (px - min_x) * sx - target_width_mm / 2
            ly = (py - min_y) * sy - target_height_mm / 2
            rx = lx * cos_a - ly * sin_a
            ry = lx * sin_a + ly * cos_a
            x_mm = center_x_mm + rx
            y_mm = center_y_mm + ry
            if prev is not None:
                dist = math.hypot(x_mm - prev[0], y_mm - prev[1])
                t_s += dist / speed
            rec.append(x_mm, y_mm, t_s, speed)
            prev = (x_mm, y_mm)
        if not rec.is_empty():
            recordings.append(rec)
    return recordings
