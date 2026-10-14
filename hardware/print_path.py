# G-code path parsing sync ticks
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from utils.logging_setup import get_logger

logger = get_logger("hardware")

SNAP_TOLERANCE_MS = 2.0

TICK_WARN_MS = 5.0

MIN_SPLIT_MM = 0.002

PREAMBLE_EDGES = 4
PREAMBLE_DWELL_MS = 250
PREAMBLE_MARK_GAP_MS = 700

_XY_DECIMALS = 3
_FEED_DECIMALS = 1

_TICK_TAG = "osgar-tick"
_PRE_TAG = "osgar-preamble"
_RESET_TAG = "osgar-sync-reset"
_RELEASE_TAG = "osgar-sync-release"

_WORD_RE = re.compile(r"([A-Za-z])\s*(-?\d*\.?\d+)")


def _words(line: str) -> dict[str, float]:
    code = line.split(";", 1)[0]
    return {m.group(1).upper(): float(m.group(2)) for m in _WORD_RE.finditer(code)}


@dataclass
class Move:

    x0: float
    y0: float
    x1: float
    y1: float
    feed_mm_s: float
    t_start_s: float
    t_end_s: float
    line_index: int
    is_travel: bool = False
    estimated: bool = False

    @property
    def length_mm(self) -> float:
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)

    @property
    def duration_s(self) -> float:
        return self.t_end_s - self.t_start_s

    def point_at(self, t_s: float) -> tuple[float, float]:
        span = self.duration_s
        f = 0.0 if span <= 0 else min(max((t_s - self.t_start_s) / span, 0.0), 1.0)
        return (self.x0 + f * (self.x1 - self.x0), self.y0 + f * (self.y1 - self.y0))


@dataclass
class PrintPath:

    moves: list[Move] = field(default_factory=list)
    first_draw_line: int = -1
    travel_moves: int = 0
    estimated_travel_s: float = 0.0

    @property
    def duration_s(self) -> float:
        return self.moves[-1].t_end_s if self.moves else 0.0

    def is_empty(self) -> bool:
        return not self.moves

    def move_at_time(self, t_s: float, hint: int = 0) -> int:
        if not self.moves:
            return -1
        n = len(self.moves)
        i = min(max(hint, 0), n - 1)
        if self.moves[i].t_start_s <= t_s <= self.moves[i].t_end_s:
            return i
        lo, hi = 0, n - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if self.moves[mid].t_end_s < t_s:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def position_at(self, t_s: float, hint: int = 0) -> tuple[float, float]:
        i = self.move_at_time(t_s, hint)
        if i < 0:
            return (0.0, 0.0)
        return self.moves[i].point_at(t_s)

    def nearest_time(
        self, x: float, y: float, t_lo_s: float, t_hi_s: float
    ) -> tuple[float, float, int]:
        if not self.moves:
            return (0.0, math.inf, -1)

        n = len(self.moves)
        if t_hi_s < t_lo_s:
            t_lo_s, t_hi_s = t_hi_s, t_lo_s
        lo = self.move_at_time(t_lo_s)
        hi = min(self.move_at_time(t_hi_s) + 1, n)
        lo = max(0, min(lo, n - 1))
        hi = max(lo + 1, hi)

        best_t = 0.0
        best_d2 = math.inf
        best_i = lo
        for i in range(lo, hi):
            mv = self.moves[i]
            if mv.t_end_s < t_lo_s or mv.t_start_s > t_hi_s:
                continue
            dx = mv.x1 - mv.x0
            dy = mv.y1 - mv.y0
            seg2 = dx * dx + dy * dy
            if seg2 <= 0.0:
                f = 0.0
            else:
                f = ((x - mv.x0) * dx + (y - mv.y0) * dy) / seg2
                f = min(max(f, 0.0), 1.0)
            px = mv.x0 + f * dx
            py = mv.y0 + f * dy
            d2 = (x - px) ** 2 + (y - py) ** 2
            if d2 < best_d2:
                best_d2 = d2
                best_t = mv.t_start_s + f * mv.duration_s
                best_i = i

        return (best_t, math.sqrt(best_d2), best_i)


def parse_gcode_path(text: str, travel_speed_mm_s: float = 150.0) -> PrintPath:
    path = PrintPath()
    x = y = 0.0
    have_xy = False
    feed_mm_s = 0.0
    t = 0.0
    started = False

    for index, raw in enumerate(text.splitlines()):
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        w = _words(line)
        code = ""
        if "G" in w:
            code = f"G{int(w['G'])}"

        if code == "G4":
            if started:
                t += w.get("P", 0.0) / 1000.0 + w.get("S", 0.0)
            continue

        if code not in ("G0", "G1"):
            continue

        if "F" in w:
            feed_mm_s = max(w["F"] / 60.0, 1e-6)

        nx = w.get("X", x)
        ny = w.get("Y", y)
        if "X" not in w and "Y" not in w:
            continue

        if not have_xy:
            x, y = nx, ny
            have_xy = True
            continue

        dist = math.hypot(nx - x, ny - y)
        is_travel = code == "G0"
        if dist <= 0.0:
            x, y = nx, ny
            continue

        if is_travel:
            speed = feed_mm_s if feed_mm_s > 0 else travel_speed_mm_s
            dur = dist / max(speed, 1e-6)
            estimated = True
        else:
            dur = dist / max(feed_mm_s, 1e-6)
            estimated = False
            if not started:
                started = True
                path.first_draw_line = index
                t = 0.0

        if not started:
            path.travel_moves += 1
            path.estimated_travel_s += dur
            x, y = nx, ny
            continue

        path.moves.append(
            Move(
                x0=x, y0=y, x1=nx, y1=ny,
                feed_mm_s=feed_mm_s if not is_travel else max(feed_mm_s, travel_speed_mm_s),
                t_start_s=t, t_end_s=t + dur,
                line_index=index, is_travel=is_travel, estimated=estimated,
            )
        )
        if is_travel:
            path.travel_moves += 1
            path.estimated_travel_s += dur
        t += dur
        x, y = nx, ny

    logger.info(
        "Parsed G-code path: %d move(s), %.2f s of drawing, %d travel(s) "
        "(%.2f s estimated, not measurable from G-code)",
        len(path.moves), path.duration_s, path.travel_moves, path.estimated_travel_s,
    )
    return path


@dataclass
class SyncTick:

    index: int
    grid_t_s: float
    actual_t_s: float
    split: bool

    @property
    def error_ms(self) -> float:
        return (self.actual_t_s - self.grid_t_s) * 1000.0


@dataclass
class SyncMarkResult:
    gcode: str
    ticks: list[SyncTick]
    splits: int
    snaps: int
    worst_error_ms: float
    tick_ms: int

    def summary(self) -> str:
        return (
            f"{len(self.ticks)} sync tick(s) every {self.tick_ms} ms "
            f"({self.splits} move(s) split, {self.snaps} snapped to an existing "
            f"boundary); worst tick sits {self.worst_error_ms:+.2f} ms off the grid"
        )


def _fmt_move(x: float, y: float, feed_mm_s: float) -> str:
    return (
        f"G1 X{x:.{_XY_DECIMALS}f} Y{y:.{_XY_DECIMALS}f} "
        f"F{feed_mm_s * 60:.{_FEED_DECIMALS}f}"
    )


def insert_sync_marks(
    text: str,
    tick_ms: int = 200,
    pin_name: str = "osgar_sync",
    travel_speed_mm_s: float = 150.0,
) -> SyncMarkResult:
    path = parse_gcode_path(text, travel_speed_mm_s=travel_speed_mm_s)
    lines = text.splitlines()
    if path.is_empty():
        logger.warning("No cutting moves in this G-code; emitting it unmarked.")
        return SyncMarkResult(text, [], 0, 0, 0.0, tick_ms)

    tick_s = max(tick_ms, 1) / 1000.0
    snap_s = SNAP_TOLERANCE_MS / 1000.0

    per_move: dict[int, list[tuple[int, float]]] = {}
    ticks: list[SyncTick] = []
    k = 0
    hint = 0
    while True:
        grid_t = k * tick_s
        if grid_t > path.duration_s:
            break
        if k == 0:
            ticks.append(SyncTick(0, 0.0, 0.0, split=False))
            k += 1
            continue
        i = path.move_at_time(grid_t, hint)
        hint = i
        mv = path.moves[i]
        if grid_t - mv.t_start_s <= snap_s or mv.t_end_s - grid_t <= snap_s or mv.duration_s <= 0:
            at_end = mv.t_end_s - grid_t < grid_t - mv.t_start_s
            actual = mv.t_end_s if at_end else mv.t_start_s
            per_move.setdefault(i, []).append((k, math.inf if at_end else -math.inf))
            ticks.append(SyncTick(k, grid_t, actual, split=False))
        else:
            per_move.setdefault(i, []).append((k, grid_t))
            ticks.append(SyncTick(k, grid_t, grid_t, split=True))
        k += 1

    move_by_line = {mv.line_index: i for i, mv in enumerate(path.moves)}
    splits = snaps = 0
    value = 0
    out: list[str] = []

    def emit_tick(k: int, note: str) -> None:
        nonlocal value
        value ^= 1
        out.append(f"SET_PIN PIN={pin_name} VALUE={value} ; {_TICK_TAG} {k} {note}")

    for index, raw in enumerate(lines):
        if index == path.first_draw_line:
            out.append(
                f"; -- OSGAR sync preamble: {PREAMBLE_EDGES} edges "
                f"{PREAMBLE_DWELL_MS} ms apart, then a {PREAMBLE_MARK_GAP_MS} ms gap, "
                f"then path time 0 --"
            )
            out.append(f"SET_PIN PIN={pin_name} VALUE=0 ; {_RESET_TAG}")
            for _ in range(PREAMBLE_EDGES):
                out.append(f"G4 P{PREAMBLE_DWELL_MS}")
                value ^= 1
                out.append(f"SET_PIN PIN={pin_name} VALUE={value} ; {_PRE_TAG}")
            out.append(f"G4 P{PREAMBLE_MARK_GAP_MS} ; the gap that identifies what follows")
            emit_tick(0, "(path time 0)")
            out.append(
                f"; -- path time 0; ticks follow every {tick_ms} ms of drawing --"
            )

        i = move_by_line.get(index)
        if i is None or i not in per_move:
            out.append(raw)
            continue

        mv = path.moves[i]
        entries = per_move[i]
        entries.sort(key=lambda e: e[1])

        before: list[int] = []
        after: list[int] = []
        splits_here: list[tuple[int, float, float]] = []
        cursor_x, cursor_y = mv.x0, mv.y0

        for _k, at in entries:
            if at == -math.inf:
                before.append(_k)
                snaps += 1
                continue
            if at == math.inf:
                after.append(_k)
                snaps += 1
                continue

            px, py = mv.point_at(at)
            px = round(px, _XY_DECIMALS)
            py = round(py, _XY_DECIMALS)
            head = math.hypot(px - cursor_x, py - cursor_y)
            tail = math.hypot(mv.x1 - px, mv.y1 - py)

            if head >= MIN_SPLIT_MM and tail >= MIN_SPLIT_MM:
                splits_here.append((_k, px, py))
                cursor_x, cursor_y = px, py
                splits += 1
                continue

            snaps += 1
            if (at - mv.t_start_s) <= (mv.t_end_s - at):
                before.append(_k)
            else:
                after.append(_k)

        for _k in before:
            emit_tick(_k, "(snapped to move start)")

        if splits_here:
            for _k, px, py in splits_here:
                out.append(_fmt_move(px, py, mv.feed_mm_s))
                emit_tick(_k, "")
            out.append(_fmt_move(mv.x1, mv.y1, mv.feed_mm_s))
        else:
            out.append(raw)

        for _k in after:
            emit_tick(_k, "(snapped to move end)")

    out.append(f"SET_PIN PIN={pin_name} VALUE=0 ; {_RELEASE_TAG}")

    marked = "\n".join(out) + "\n"
    verified = _verify_tick_times(marked, ticks, pin_name, travel_speed_mm_s)
    worst = max((abs(t.error_ms) for t in verified), default=0.0)
    signed = max(verified, key=lambda t: abs(t.error_ms)).error_ms if verified else 0.0

    result = SyncMarkResult(marked, verified, splits, snaps, signed, tick_ms)
    logger.info("Sync marks: %s", result.summary())
    if worst > TICK_WARN_MS:
        logger.warning(
            "A sync tick sits %.2f ms off its grid time. The firmware assumes ticks "
            "are exactly on the grid, so that much error goes straight into key "
            "timing. Check for moves that are very short in distance but long in "
            "duration - a split cannot be placed finely inside those.",
            worst,
        )
    return result


def _verify_tick_times(
    marked: str, ticks: list[SyncTick], pin_name: str, travel_speed_mm_s: float
) -> list[SyncTick]:
    x = y = 0.0
    have_xy = False
    feed = 0.0
    t = 0.0
    started = False
    seen: dict[int, float] = {}

    for raw in marked.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if _TICK_TAG in line:
            parts = line.split(_TICK_TAG, 1)[1].split()
            if parts:
                try:
                    seen[int(parts[0])] = t
                except ValueError:
                    logger.warning("Unparseable sync tick tag: %s", line)
            continue
        w = _words(line)
        if "G" not in w:
            continue
        g = int(w["G"])
        if g == 4:
            if started:
                t += w.get("P", 0.0) / 1000.0 + w.get("S", 0.0)
            continue
        if g not in (0, 1):
            continue
        if "F" in w:
            feed = max(w["F"] / 60.0, 1e-6)
        nx, ny = w.get("X", x), w.get("Y", y)
        if "X" not in w and "Y" not in w:
            continue
        if not have_xy:
            x, y = nx, ny
            have_xy = True
            continue
        dist = math.hypot(nx - x, ny - y)
        if dist > 0:
            if g == 1:
                started = True
                t += dist / max(feed, 1e-6)
            elif started:
                t += dist / max(max(feed, travel_speed_mm_s), 1e-6)
        x, y = nx, ny

    missing = [tk.index for tk in ticks if tk.index not in seen]
    if missing:
        logger.warning(
            "%d sync tick(s) went missing from the marked G-code (first: %s).",
            len(missing), missing[0],
        )
    return [
        SyncTick(tk.index, tk.grid_t_s, seen.get(tk.index, tk.actual_t_s), tk.split)
        for tk in ticks
    ]
