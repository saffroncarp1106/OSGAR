#!/usr/bin/env python3
"""Compare two osu! replay cursor trails and estimate their relative delay."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from osrparse import Replay

try:
    from rich.console import Console
    from rich.table import Table

    HAVE_RICH = True
except ImportError:  # Rich is cosmetic; plain output remains fully functional.
    HAVE_RICH = False


CLICK_MASK = 0b11  # Per the requested convention: bit 0 = M1/K1, bit 1 = M2/K2.


@dataclass(frozen=True)
class CursorEvent:
    time_ms: float
    x: float
    y: float
    keys: int


@dataclass(frozen=True)
class DistanceStats:
    mean: float
    median: float
    p95: float
    maximum: float
    rms: float
    samples: int


@dataclass
class ReplayInfo:
    path: Path
    replay: Replay
    events: list[CursorEvent]
    clicks: list[CursorEvent]
    anchor_ms: float


def _enum_text(value: Any) -> str:
    """Return stable, readable text for osrparse enum/flag values."""
    if value is None:
        return "None"
    name = getattr(value, "name", None)
    if name:
        return str(name).replace("|", ", ")
    return str(value)


def extract_events(replay: Replay) -> list[CursorEvent]:
    """Convert replay delta timestamps into an absolute millisecond timeline."""
    events: list[CursorEvent] = []
    elapsed = 0.0
    for frame in replay.replay_data or []:
        delta = float(frame.time_delta)
        # A negative delta is a metadata/sentinel frame (commonly -12345), not motion.
        if delta < 0:
            continue
        elapsed += delta
        x, y = float(frame.x), float(frame.y)
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        events.append(CursorEvent(elapsed, x, y, int(frame.keys)))
    return events


def find_gameplay_clicks(events: Sequence[CursorEvent]) -> list[CursorEvent]:
    """Find 0->pressed transitions after the first genuine moving replay frame.

    A usable Move frame has a non-zero time delta, valid playfield coordinates, and
    a changed cursor position. The first usable frame is accepted as the start when
    no earlier valid coordinate exists; this handles replays whose first data frame
    is already inside the playfield.
    """
    if not events:
        return []

    start_index: int | None = None
    previous_valid: CursorEvent | None = None
    previous_time = 0.0
    for index, event in enumerate(events):
        valid_position = 0.0 <= event.x <= 512.0 and 0.0 <= event.y <= 384.0
        nonzero_delta = event.time_ms > previous_time
        moved = previous_valid is None or (event.x, event.y) != (
            previous_valid.x,
            previous_valid.y,
        )
        if valid_position and nonzero_delta and moved:
            start_index = index
            break
        if valid_position:
            previous_valid = event
        previous_time = event.time_ms

    if start_index is None:
        return []

    clicks: list[CursorEvent] = []
    previous_pressed = 0
    for event in events[start_index:]:
        pressed = event.keys & CLICK_MASK
        if pressed and not previous_pressed:
            clicks.append(event)
        previous_pressed = pressed
    return clicks


def load_replay(path: Path) -> ReplayInfo:
    """Parse a replay and locate its gameplay click synchronization anchor."""
    try:
        replay = Replay.from_path(path)
    except Exception as exc:
        raise ValueError(f"Could not parse {path}: {exc}") from exc
    events = extract_events(replay)
    clicks = find_gameplay_clicks(events)
    if not events:
        raise ValueError(f"{path} contains no usable cursor events.")
    if not clicks:
        raise ValueError(
            f"{path} contains no gameplay clicks (no keys & 3 transition after motion)."
        )
    return ReplayInfo(path, replay, events, clicks, clicks[0].time_ms)


def prepare_timeline(info: ReplayInfo) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return de-duplicated times relative to the anchor and matching x/y arrays."""
    times = np.asarray([event.time_ms - info.anchor_ms for event in info.events])
    x = np.asarray([event.x for event in info.events], dtype=float)
    y = np.asarray([event.y for event in info.events], dtype=float)

    # np.interp requires increasing x values. Keeping the final sample at a repeated
    # timestamp best represents the state at that instant.
    reverse_unique = np.unique(times[::-1], return_index=True)[1]
    keep = np.sort(len(times) - 1 - reverse_unique)
    return times[keep], x[keep], y[keep]


def resample_pair(
    a: ReplayInfo, b: ReplayInfo, fps: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Linearly interpolate both trails on the same post-anchor time grid."""
    ta, xa, ya = prepare_timeline(a)
    tb, xb, yb = prepare_timeline(b)
    duration_ms = min(float(ta[-1]), float(tb[-1]))
    if duration_ms <= 0:
        raise ValueError("The replays have no overlapping cursor data after the sync click.")
    step_ms = 1000.0 / fps
    grid = np.arange(0.0, duration_ms + step_ms * 0.25, step_ms)
    grid = grid[grid <= duration_ms]
    if grid.size < 3:
        raise ValueError("The overlapping replay duration is too short to analyze.")
    trail_a = np.column_stack((np.interp(grid, ta, xa), np.interp(grid, ta, ya)))
    trail_b = np.column_stack((np.interp(grid, tb, xb), np.interp(grid, tb, yb)))
    return grid, trail_a, trail_b


def distances(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.linalg.norm(a - b, axis=1)


def summarize(values: np.ndarray) -> DistanceStats:
    if values.size == 0:
        raise ValueError("Cannot summarize an empty distance series.")
    return DistanceStats(
        mean=float(np.mean(values)),
        median=float(np.median(values)),
        p95=float(np.percentile(values, 95)),
        maximum=float(np.max(values)),
        rms=float(np.sqrt(np.mean(np.square(values)))),
        samples=int(values.size),
    )


def aligned_slices(
    a: np.ndarray, b: np.ndarray, lag_frames: int
) -> tuple[np.ndarray, np.ndarray]:
    """Align arrays; a positive lag means B occurs later (B trails A)."""
    if lag_frames > 0:
        return a[:-lag_frames], b[lag_frames:]
    if lag_frames < 0:
        return a[-lag_frames:], b[:lag_frames]
    return a, b


def lag_correlation(
    a: np.ndarray, b: np.ndarray, fps: float, max_lag_ms: float
) -> tuple[int, np.ndarray, np.ndarray]:
    """Compute normalized 2-D cross-correlation over a bounded lag range."""
    max_frames = min(int(round(max_lag_ms * fps / 1000.0)), len(a) - 2)
    lags = np.arange(-max_frames, max_frames + 1, dtype=int)
    scores = np.empty(lags.size, dtype=float)

    # Remove each full trail's DC position so correlation responds to motion shape,
    # rather than merely rewarding both cursors for being in the same map quadrant.
    ac = a - np.mean(a, axis=0)
    bc = b - np.mean(b, axis=0)
    for index, lag in enumerate(lags):
        aa, bb = aligned_slices(ac, bc, int(lag))
        numerator = float(np.sum(aa * bb))
        denominator = math.sqrt(float(np.sum(aa * aa) * np.sum(bb * bb)))
        scores[index] = numerator / denominator if denominator else -np.inf
    best_index = int(np.argmax(scores))
    return int(lags[best_index]), lags, scores


def metadata(info: ReplayInfo) -> dict[str, Any]:
    replay = info.replay
    return {
        "file": str(info.path),
        "player": str(getattr(replay, "username", "Unknown")),
        "score": int(getattr(replay, "score", 0)),
        "mods": _enum_text(getattr(replay, "mods", None)),
        "count_300": int(getattr(replay, "count_300", 0)),
        "count_100": int(getattr(replay, "count_100", 0)),
        "count_50": int(getattr(replay, "count_50", 0)),
        "count_miss": int(getattr(replay, "count_miss", 0)),
        "max_combo": int(getattr(replay, "max_combo", 0)),
        "click_count": len(info.clicks),
        "anchor_ms": info.anchor_ms,
    }


def make_report(
    a: ReplayInfo,
    b: ReplayInfo,
    fps: float,
    grid: np.ndarray,
    trail_a: np.ndarray,
    trail_b: np.ndarray,
    best_lag: int,
    max_lag_ms: float,
) -> dict[str, Any]:
    zero_values = distances(trail_a, trail_b)
    aligned_a, aligned_b = aligned_slices(trail_a, trail_b, best_lag)
    best_values = distances(aligned_a, aligned_b)
    lag_ms = best_lag * 1000.0 / fps
    duration_a = prepare_timeline(a)[0][-1]
    duration_b = prepare_timeline(b)[0][-1]
    return {
        "replay_a": metadata(a),
        "replay_b": metadata(b),
        "sync": {
            "anchor_a_ms": a.anchor_ms,
            "anchor_b_ms": b.anchor_ms,
            "raw_delta_b_minus_a_ms": b.anchor_ms - a.anchor_ms,
        },
        "comparison": {
            "fps": fps,
            "duration_ms": float(grid[-1]),
            "samples": int(grid.size),
            "post_anchor_duration_a_ms": float(duration_a),
            "post_anchor_duration_b_ms": float(duration_b),
            "max_lag_searched_ms": max_lag_ms,
        },
        "zero_lag": asdict(summarize(zero_values)),
        "best_lag": {
            "lag_frames": best_lag,
            "lag_ms": lag_ms,
            "interpretation": (
                "B trails A" if best_lag > 0 else "B leads A" if best_lag < 0 else "no lag"
            ),
            "distance": asdict(summarize(best_values)),
        },
    }


def warnings_for(report: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    a, b = report["replay_a"], report["replay_b"]
    if a["click_count"] != b["click_count"]:
        warnings.append(
            f"Click counts differ: A has {a['click_count']}, B has {b['click_count']}."
        )
    da = report["comparison"]["post_anchor_duration_a_ms"]
    db = report["comparison"]["post_anchor_duration_b_ms"]
    if max(da, db) > 0 and abs(da - db) / max(da, db) > 0.20:
        warnings.append("Post-anchor replay lengths differ by more than 20%.")
    best = report["best_lag"]
    searched = report["comparison"]["max_lag_searched_ms"]
    if searched and abs(best["lag_ms"]) >= searched - 1000.0 / report["comparison"]["fps"]:
        warnings.append("Best lag is at the search boundary; try a larger --max-lag-ms.")
    return warnings


def _stats_row(label: str, stats: dict[str, Any]) -> list[str]:
    return [
        label,
        f"{stats['mean']:.2f}",
        f"{stats['median']:.2f}",
        f"{stats['p95']:.2f}",
        f"{stats['maximum']:.2f}",
        f"{stats['rms']:.2f}",
        str(stats["samples"]),
    ]


def print_report(report: dict[str, Any], warnings: Sequence[str]) -> None:
    a, b = report["replay_a"], report["replay_b"]
    sync, comparison, best = report["sync"], report["comparison"], report["best_lag"]
    lag_ms = float(best["lag_ms"])
    relation = "trails" if lag_ms > 0 else "leads" if lag_ms < 0 else "matches"

    if HAVE_RICH:
        console = Console()
        console.print("\n[bold cyan]osu! Replay Cursor-Delay Analysis[/bold cyan]")
        meta = Table(show_header=True, header_style="bold")
        meta.add_column("")
        meta.add_column("Replay A (reference)")
        meta.add_column("Replay B (test)")
        for key, label in (
            ("file", "File"), ("player", "Player"), ("score", "Score"),
            ("mods", "Mods"), ("count_300", "300"), ("count_100", "100"),
            ("count_50", "50"), ("count_miss", "Miss"),
            ("max_combo", "Max combo"), ("click_count", "Clicks"),
        ):
            meta.add_row(label, str(a[key]), str(b[key]))
        console.print(meta)
        console.print(
            f"Sync anchors: A [bold]{sync['anchor_a_ms']:.2f} ms[/bold], "
            f"B [bold]{sync['anchor_b_ms']:.2f} ms[/bold] "
            f"(raw B-A: {sync['raw_delta_b_minus_a_ms']:+.2f} ms)"
        )
        console.print(
            f"Resampling: {comparison['fps']:g} Hz | compared "
            f"{comparison['duration_ms'] / 1000:.3f} s ({comparison['samples']} samples)"
        )
        table = Table(title="Cursor distance (osu! pixels)", header_style="bold")
        for heading in ("Alignment", "Mean", "Median", "P95", "Max", "RMS", "N"):
            table.add_column(heading, justify="right" if heading != "Alignment" else "left")
        table.add_row(*_stats_row("Zero lag", report["zero_lag"]))
        table.add_row(*_stats_row("Best lag", best["distance"]))
        console.print(table)
        console.print(
            f"Best-fit lag: [bold]{lag_ms:+.2f} ms ({best['lag_frames']:+d} frames)[/bold]"
        )
        for warning in warnings:
            console.print(f"[yellow]Warning:[/yellow] {warning}")
        if lag_ms == 0:
            verdict = "Replay B has no measurable whole-sample lag relative to Replay A."
        else:
            verdict = (
                f"Replay B {relation} Replay A by ~{abs(lag_ms):.1f} ms "
                f"(≈{abs(best['lag_frames']):g} frames @ {comparison['fps']:g} fps)."
            )
        console.print(f"[bold green]Verdict:[/bold green] {verdict}\n")
        return

    print("\nosu! Replay Cursor-Delay Analysis")
    for label, item in (("Replay A", a), ("Replay B", b)):
        print(
            f"{label}: {item['file']} | {item['player']} | score {item['score']} | "
            f"mods {item['mods']} | 300/100/50/miss "
            f"{item['count_300']}/{item['count_100']}/{item['count_50']}/{item['count_miss']} | "
            f"combo {item['max_combo']} | clicks {item['click_count']}"
        )
    print(
        f"Sync anchors: A {sync['anchor_a_ms']:.2f} ms, B {sync['anchor_b_ms']:.2f} ms "
        f"(raw B-A: {sync['raw_delta_b_minus_a_ms']:+.2f} ms)"
    )
    print(
        f"Resampling: {comparison['fps']:g} Hz; compared "
        f"{comparison['duration_ms'] / 1000:.3f} s ({comparison['samples']} samples)"
    )
    print("Alignment     Mean   Median     P95     Max     RMS      N")
    for row in (_stats_row("Zero lag", report["zero_lag"]), _stats_row("Best lag", best["distance"])):
        print(f"{row[0]:<12} " + " ".join(f"{value:>7}" for value in row[1:]))
    print(f"Best-fit lag: {lag_ms:+.2f} ms ({best['lag_frames']:+d} frames)")
    for warning in warnings:
        print(f"WARNING: {warning}")
    print(
        "Verdict: Replay B has no measurable whole-sample lag relative to Replay A."
        if lag_ms == 0
        else f"Verdict: Replay B {relation} Replay A by ~{abs(lag_ms):.1f} ms "
        f"(≈{abs(best['lag_frames']):g} frames @ {comparison['fps']:g} fps)."
    )


def show_plot(
    grid: np.ndarray,
    a: np.ndarray,
    b: np.ndarray,
    lags: np.ndarray,
    correlations: np.ndarray,
    fps: float,
) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("Plot skipped: matplotlib is not installed. Install it with: pip install matplotlib")
        return

    seconds = grid / 1000.0
    figure, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=False)
    axes[0].plot(seconds, a[:, 0], label="Replay A")
    axes[0].plot(seconds, b[:, 0], label="Replay B", alpha=0.8)
    axes[0].set_ylabel("X (osu! px)")
    axes[0].legend()
    axes[1].plot(seconds, a[:, 1])
    axes[1].plot(seconds, b[:, 1], alpha=0.8)
    axes[1].set_ylabel("Y (osu! px)")
    axes[2].plot(seconds, distances(a, b), color="tab:red")
    axes[2].set_ylabel("Distance (px)")
    axes[2].set_xlabel("Time after sync click (s)")
    axes[3].plot(lags * 1000.0 / fps, correlations, color="tab:purple")
    axes[3].axvline(lags[int(np.argmax(correlations))] * 1000.0 / fps, color="black", ls="--")
    axes[3].set_ylabel("Correlation")
    axes[3].set_xlabel("Lag (ms; positive = B trails A)")
    axes[3].grid(alpha=0.25)
    figure.tight_layout()
    plt.show()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare two osu! replay cursor trails using their first gameplay click."
    )
    parser.add_argument("replay_a", type=Path, help="Reference .osr replay")
    parser.add_argument("replay_b", type=Path, help="Test .osr replay")
    parser.add_argument("--fps", type=float, default=60.0, help="Resample rate (default: 60)")
    parser.add_argument("--output", type=Path, help="Write the complete report as JSON")
    parser.add_argument("--plot", action="store_true", help="Show diagnostic plots if matplotlib is installed")
    parser.add_argument(
        "--max-lag-ms", type=float, default=2000.0,
        help="Maximum lead/lag searched in either direction (default: 2000)",
    )
    args = parser.parse_args(argv)
    if args.fps <= 0:
        parser.error("--fps must be greater than zero")
    if args.max_lag_ms <= 0:
        parser.error("--max-lag-ms must be greater than zero")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        a = load_replay(args.replay_a)
        b = load_replay(args.replay_b)
        grid, trail_a, trail_b = resample_pair(a, b, args.fps)
        best_lag, lags, correlations = lag_correlation(
            trail_a, trail_b, args.fps, args.max_lag_ms
        )
        report = make_report(
            a, b, args.fps, grid, trail_a, trail_b, best_lag, args.max_lag_ms
        )
        report_warnings = warnings_for(report)
        report["warnings"] = report_warnings
        print_report(report, report_warnings)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"JSON report written to: {args.output}")
        if args.plot:
            show_plot(grid, trail_a, trail_b, lags, correlations, args.fps)
        return 0
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
