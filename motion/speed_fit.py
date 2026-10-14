# Time stretch and speed calculation
from __future__ import annotations

import math

from recording.model import Recording

SPEED_SAFETY_FACTOR = 0.8

DEFAULT_TOLERANCE = 0.02

MAX_AUTO_STRETCH = 60.0


def path_metrics(recordings: list[Recording]) -> tuple[list[float], list[float], float]:
    dists: list[float] = []
    dts: list[float] = []
    duration = 0.0
    for rec in recordings:
        pts = rec.points
        if len(pts) < 2:
            continue
        for a, b in zip(pts, pts[1:]):
            dists.append(math.hypot(b.x_mm - a.x_mm, b.y_mm - a.y_mm))
            dts.append(max(b.t_s - a.t_s, 0.0))
        duration += pts[-1].t_s - pts[0].t_s
    return dists, dts, duration


def constrained_duration(
    dists: list[float], dts: list[float], v_max_mm_s: float, stretch: float = 1.0
) -> float:
    if v_max_mm_s <= 0:
        return math.inf
    total = 0.0
    for d, dt in zip(dists, dts):
        need = d / v_max_mm_s
        scaled = dt * stretch
        total += scaled if scaled > need else need
    return total


def fit_time_stretch(
    recordings: list[Recording],
    v_max_mm_s: float,
    tolerance: float = DEFAULT_TOLERANCE,
    safety: float = SPEED_SAFETY_FACTOR,
) -> float:
    dists, dts, duration = path_metrics(recordings)
    if not dists or duration <= 0:
        return 1.0

    v_eff = max(v_max_mm_s, 1e-6) * max(safety, 1e-6)
    if constrained_duration(dists, dts, v_eff, 1.0) <= duration * (1.0 + tolerance):
        return 1.0

    lo, hi = 1.0, 2.0
    while hi < MAX_AUTO_STRETCH and constrained_duration(dists, dts, v_eff, hi) > duration * hi * (1.0 + tolerance):
        lo, hi = hi, hi * 2
    if hi >= MAX_AUTO_STRETCH:
        return MAX_AUTO_STRETCH

    for _ in range(40):
        mid = (lo + hi) / 2
        if constrained_duration(dists, dts, v_eff, mid) <= duration * mid * (1.0 + tolerance):
            hi = mid
        else:
            lo = mid
    return hi


def describe_fit(
    recordings: list[Recording], v_max_mm_s: float, safety: float = SPEED_SAFETY_FACTOR
) -> dict:
    dists, dts, duration = path_metrics(recordings)
    v_eff = max(v_max_mm_s, 1e-6) * max(safety, 1e-6)
    stretch = fit_time_stretch(recordings, v_max_mm_s, safety=safety)
    speeds = sorted(d / dt for d, dt in zip(dists, dts) if dt > 0 and d > 0)

    def pct(p: float) -> float:
        if not speeds:
            return 0.0
        return speeds[min(int(len(speeds) * p), len(speeds) - 1)]

    return {
        "stretch": stretch,
        "size_scale": 1.0 / stretch if stretch > 0 else 1.0,
        "duration_s": duration,
        "print_s": duration * stretch,
        "path_len_mm": sum(dists),
        "median_speed": pct(0.5),
        "p99_speed": pct(0.99),
        "peak_speed": speeds[-1] if speeds else 0.0,
        "usable_speed": v_eff,
        "over_limit": sum(1 for s in speeds if s > v_eff),
        "segments": len(speeds),
        "capped": stretch >= MAX_AUTO_STRETCH,
    }
