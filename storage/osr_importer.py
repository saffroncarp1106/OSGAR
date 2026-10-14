# osu replay file import parser
from __future__ import annotations

import bisect
import logging
import lzma
import math
import statistics
import struct
from dataclasses import dataclass, field

from recording.model import Recording, RecordedPoint

logger = logging.getLogger(__name__)

PLAYFIELD_W = 512.0
PLAYFIELD_H = 384.0

SCREEN_LEFT = -170.7
SCREEN_RIGHT = 682.2
SCREEN_TOP = -56.0
SCREEN_BOTTOM = 423.6
SCREEN_W = SCREEN_RIGHT - SCREEN_LEFT
SCREEN_H = SCREEN_BOTTOM - SCREEN_TOP

_VALID_X_RANGE = (-300.0, 800.0)
_VALID_Y_RANGE = (-300.0, 700.0)

_FAITHFUL_OSU_PLAYBACK = True

_NOMINAL_DT_BOUNDS_MS = (4.0, 40.0)

_POLL_WINDOW_MAX_MS = 100.0

_SHORT_DT_RATIO = 0.8

_GAP_DT_RATIO = 2.5

_MAX_RETIME_RUN_INTERVALS = 4.0

_MAX_FILL_POINTS = 64
_MIN_GAP_TRAVEL = 1e-6

_RECOVER_GAP_TIME = False

_SPEED_REFERENCE_PCT = 0.85
_GAP_SPEED_MARGIN = 1.15
_MAX_GAP_STRETCH = 12.0
_ARC_SAMPLES = 64

_MAX_TOTAL_STRETCH_FRAC = 0.02

_UNIFORM_TIME_BLEND = 0.10

_CATMULL_ROM_ALPHA = 0.5

_FILL_OVERSHOOT_FRAC = 0.25


def _is_plausible_cursor_pos(x: float, y: float) -> bool:
    return _VALID_X_RANGE[0] <= x <= _VALID_X_RANGE[1] and _VALID_Y_RANGE[0] <= y <= _VALID_Y_RANGE[1]


class OsrImportError(ValueError):
    pass


@dataclass
class RepairStats:
    nominal_dt_ms: float = 0.0
    duplicate_frames_dropped: int = 0
    key_frames_dropped: int = 0
    retimed_runs: int = 0
    retimed_frames: int = 0
    retime_runs_skipped: int = 0
    retime_frames_skipped: int = 0
    gaps_found: int = 0
    gaps_retimed: int = 0
    gaps_stretch_clamped: int = 0
    points_synthesised: int = 0
    time_recovered_ms: float = 0.0
    raw_duration_ms: float = 0.0
    final_duration_ms: float = 0.0
    map_start_offset_ms: float = 0.0
    lead_in_trimmed_ms: float = 0.0
    backwards_frames_dropped: int = 0

    def summary(self) -> str:
        return (
            f"nominal frame delta {self.nominal_dt_ms:.1f} ms; "
            f"dropped {self.duplicate_frames_dropped} sub-poll duplicate frame(s) "
            f"({self.key_frames_dropped} at key events); "
            f"re-timed {self.retimed_frames} frame(s) across {self.retimed_runs} sub-poll run(s), "
            f"left {self.retime_frames_skipped} frame(s) in {self.retime_runs_skipped} "
            f"high-frame-rate run(s) as recorded; "
            f"filled {self.gaps_found} dropout gap(s) with {self.points_synthesised} interpolated point(s); "
            f"recovered {self.time_recovered_ms:.0f} ms of unrecorded time across "
            f"{self.gaps_retimed} gap(s) ({self.gaps_stretch_clamped} clamped by the duration budget); "
            f"map time 0 is {self.map_start_offset_ms:.0f} ms into the recording, "
            f"trimmed {self.lead_in_trimmed_ms:.0f} ms of lead-in; "
            f"duration {self.raw_duration_ms:.0f} ms -> {self.final_duration_ms:.0f} ms"
        )


@dataclass
class OsrDocument:
    points: list[tuple[float, float, float]]
    clicks: list[tuple[float, float, float, int]]
    releases: list[tuple[float, float, float, int]]
    synthetic_indices: set[int] = field(default_factory=set)
    raw_points: list[tuple[float, float, float]] = field(default_factory=list)
    raw_mask_changed: list[bool] = field(default_factory=list)
    map_start_offset_ms: float = 0.0
    repair: RepairStats = field(default_factory=RepairStats)


_KEY1_MASK = 0b0101
_KEY2_MASK = 0b1010


def _read_uleb128(buf: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _read_string(buf: bytes, i: int) -> tuple[str, int]:
    marker = buf[i]
    i += 1
    if marker == 0x00:
        return "", i
    length, i = _read_uleb128(buf, i)
    return buf[i:i + length].decode("utf-8"), i + length


def strip_duplicate_position_frames(
    frames: list[tuple[float, float, float]],
    mask_changed: list[bool],
    nominal_dt: float,
    stats: RepairStats | None = None,
) -> list[tuple[float, float, float]]:
    n = len(frames)
    if n < 2:
        return list(frames)

    sub_poll = nominal_dt * _SHORT_DT_RATIO
    kept: list[tuple[float, float, float]] = [frames[0]]
    for i in range(1, n):
        t, x, y = frames[i]
        if (
            i < n - 1
            and x == kept[-1][1]
            and y == kept[-1][2]
            and t - frames[i - 1][0] < sub_poll
        ):
            if stats is not None:
                stats.duplicate_frames_dropped += 1
                if i < len(mask_changed) and mask_changed[i]:
                    stats.key_frames_dropped += 1
            continue
        kept.append((t, x, y))
    return kept


def _nominal_frame_dt(frames: list[tuple[float, float, float]]) -> float:
    deltas = sorted(
        b[0] - a[0]
        for a, b in zip(frames, frames[1:])
        if 0.0 < b[0] - a[0] <= _POLL_WINDOW_MAX_MS
    )
    if not deltas:
        return 16.0

    half = sum(deltas) / 2.0
    acc = 0.0
    chosen = deltas[-1]
    for d in deltas:
        acc += d
        if acc >= half:
            chosen = d
            break

    lo, hi = _NOMINAL_DT_BOUNDS_MS
    return min(max(chosen, lo), hi)


def _repair_subpoll_timing(
    frames: list[tuple[float, float, float]],
    nominal_dt: float,
    stats: RepairStats,
) -> list[tuple[float, float, float]]:
    n = len(frames)
    if n < 3:
        return frames

    short_cut = nominal_dt * _SHORT_DT_RATIO
    gap_cut = nominal_dt * _GAP_DT_RATIO

    deltas = [frames[i][0] - frames[i - 1][0] for i in range(1, n)]
    is_short = [d < short_cut for d in deltas]
    is_gap = [d > gap_cut for d in deltas]

    runs: list[tuple[int, int]] = []
    i = 0
    while i < len(deltas):
        if not is_short[i] or is_gap[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(deltas) and is_short[j + 1] and not is_gap[j + 1]:
            j += 1
        start, end = i, j
        if start - 1 >= 0 and not is_gap[start - 1]:
            start -= 1
        if end + 1 < len(deltas) and not is_gap[end + 1]:
            end += 1
        if runs and start <= runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], max(runs[-1][1], end))
        else:
            runs.append((start, end))
        i = j + 1

    if not runs:
        return frames

    times = [f[0] for f in frames]
    max_run_span = nominal_dt * _MAX_RETIME_RUN_INTERVALS
    for seg_start, seg_end in runs:
        a = seg_start
        b = seg_end + 1
        span = times[b] - times[a]
        if span <= 0 or b - a < 2:
            continue
        if span > max_run_span:
            stats.retime_runs_skipped += 1
            stats.retime_frames_skipped += b - a - 1
            continue

        dists = [
            math.hypot(frames[k][1] - frames[k - 1][1], frames[k][2] - frames[k - 1][2])
            for k in range(a + 1, b + 1)
        ]
        total = sum(dists)
        count = len(dists)
        if count == 0:
            continue

        weights = [
            (1.0 - _UNIFORM_TIME_BLEND) * (d / total if total > 0 else 1.0 / count)
            + _UNIFORM_TIME_BLEND / count
            for d in dists
        ]
        wsum = sum(weights) or 1.0

        acc = 0.0
        for offset, w in enumerate(weights[:-1]):
            acc += w / wsum
            times[a + 1 + offset] = times[a] + span * acc
        times[b] = times[a] + span

        stats.retimed_runs += 1
        stats.retimed_frames += b - a - 1

    return [(times[k], frames[k][1], frames[k][2]) for k in range(n)]


def _catmull_rom(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    u: float,
) -> tuple[float, float]:
    def knot(t_prev: float, a: tuple[float, float], b: tuple[float, float]) -> float:
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        return t_prev + (d ** _CATMULL_ROM_ALPHA if d > 0 else 1e-6)

    t0 = 0.0
    t1 = knot(t0, p0, p1)
    t2 = knot(t1, p1, p2)
    t3 = knot(t2, p2, p3)
    if t2 <= t1:
        return p1

    t = t1 + (t2 - t1) * u

    def mix(a, b, ta, tb):
        if tb == ta:
            return a
        wa = (tb - t) / (tb - ta)
        wb = (t - ta) / (tb - ta)
        return (a[0] * wa + b[0] * wb, a[1] * wa + b[1] * wb)

    a1 = mix(p0, p1, t0, t1)
    a2 = mix(p1, p2, t1, t2)
    a3 = mix(p2, p3, t2, t3)
    b1 = mix(a1, a2, t0, t2)
    b2 = mix(a2, a3, t1, t3)
    return mix(b1, b2, t1, t2)


def _speed_reference(
    frames: list[tuple[float, float, float]], gap_cut: float, pct: float = _SPEED_REFERENCE_PCT
) -> float:
    speeds: list[float] = []
    for k in range(1, len(frames)):
        dt = frames[k][0] - frames[k - 1][0]
        if dt <= 0 or dt > gap_cut:
            continue
        d = math.hypot(frames[k][1] - frames[k - 1][1], frames[k][2] - frames[k - 1][2])
        speeds.append(d / dt)
    if not speeds:
        return 0.0
    speeds.sort()
    return speeds[min(int(len(speeds) * pct), len(speeds) - 1)]


def _resample_by_arc_length(
    dense: list[tuple[float, float]], count: int
) -> list[tuple[float, float]]:
    if count < 1 or len(dense) < 2:
        return []

    cumulative = [0.0]
    for a, b in zip(dense, dense[1:]):
        cumulative.append(cumulative[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = cumulative[-1]
    if total <= 0:
        return [dense[0]] * count

    out: list[tuple[float, float]] = []
    for j in range(1, count + 1):
        target = total * j / (count + 1)
        k = bisect.bisect_left(cumulative, target)
        k = min(max(k, 1), len(dense) - 1)
        seg = cumulative[k] - cumulative[k - 1]
        f = (target - cumulative[k - 1]) / seg if seg > 0 else 0.0
        a, b = dense[k - 1], dense[k]
        out.append((a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])))
    return out


def _fill_dropout_gaps(
    frames: list[tuple[float, float, float]],
    nominal_dt: float,
    stats: RepairStats,
    recover_time: bool = _RECOVER_GAP_TIME,
    v_ref: float | None = None,
    gap_dt_ratio: float = _GAP_DT_RATIO,
) -> tuple[list[tuple[float, float, float]], set[int], list[tuple[float, float]]]:
    n = len(frames)
    if n < 2:
        return frames, set(), []

    gap_cut = nominal_dt * gap_dt_ratio
    if v_ref is None:
        v_ref = _speed_reference(frames, gap_cut)
    stretch_budget = (frames[-1][0] - frames[0][0]) * _MAX_TOTAL_STRETCH_FRAC
    out: list[tuple[float, float, float]] = [frames[0]]
    synthetic: set[int] = set()
    shifts: list[tuple[float, float]] = []
    shift = 0.0

    for i in range(1, n):
        t_prev, x_prev, y_prev = frames[i - 1]
        t_cur, x_cur, y_cur = frames[i]
        dt = t_cur - t_prev
        chord = math.hypot(x_cur - x_prev, y_cur - y_prev)

        if dt > gap_cut and chord > _MIN_GAP_TRAVEL:
            stats.gaps_found += 1

            span = dt
            implied_speed = chord / dt if dt > 0 else 0.0
            if recover_time and v_ref > 0 and implied_speed > v_ref * _GAP_SPEED_MARGIN:
                span = max(min(chord / v_ref, dt * _MAX_GAP_STRETCH), dt)
                remaining = stretch_budget - shift
                if span - dt > remaining:
                    span = dt + max(remaining, 0.0)
                    stats.gaps_stretch_clamped += 1

            missing = min(max(int(round(span / nominal_dt)) - 1, 1), _MAX_FILL_POINTS)
            base = t_prev + shift
            for j in range(1, missing + 1):
                f = j / (missing + 1)
                fx = x_prev + f * (x_cur - x_prev)
                fy = y_prev + f * (y_cur - y_prev)
                synthetic.add(len(out))
                out.append((base + span * f, fx, fy))
                stats.points_synthesised += 1

            logger.info(
                "OSR gap @ %.0f ms: %.0f px in recorded %.0f ms (%.2f px/ms) -> "
                "%s over %.0f ms with %d straight-line point(s) [v_ref=%.2f]",
                t_prev, chord, dt, implied_speed,
                "re-timed" if span > dt else "kept as recorded", span, missing, v_ref,
            )

            if span > dt:
                stats.gaps_retimed += 1
                stats.time_recovered_ms += span - dt
                shift += span - dt
                shifts.append((t_cur, shift))

        out.append((t_cur + shift, x_cur, y_cur))

    return out, synthetic, shifts


def _trim_to_map_start(
    points: list[tuple[float, float, float]],
    clicks: list[tuple[float, float, float, int]],
    releases: list[tuple[float, float, float, int]],
    synthetic: set[int],
) -> tuple[
    list[tuple[float, float, float]],
    list[tuple[float, float, float, int]],
    list[tuple[float, float, float, int]],
    set[int],
    float,
]:
    if not points or points[0][0] >= 0.0:
        return points, clicks, releases, synthetic, 0.0

    first = next((i for i, p in enumerate(points) if p[0] >= 0.0), len(points))
    if first >= len(points):
        return points, clicks, releases, synthetic, 0.0

    trimmed_ms = -points[0][0]
    head: list[tuple[float, float, float]] = []
    if first > 0:
        a, b = points[first - 1], points[first]
        span = b[0] - a[0]
        f = (0.0 - a[0]) / span if span > 0 else 0.0
        head.append((0.0, a[1] + f * (b[1] - a[1]), a[2] + f * (b[2] - a[2])))

    kept = head + points[first:]
    new_synth = {0} if head else set()
    for old_i in synthetic:
        if old_i >= first:
            new_synth.add(old_i - first + len(head))

    clicks = [c for c in clicks if c[0] >= 0.0]
    releases = [r for r in releases if r[0] >= 0.0]
    return kept, clicks, releases, new_synth, trimmed_ms


def _shift_for(shift_times: list[float], shift_values: list[float], t: float) -> float:
    if not shift_times:
        return 0.0
    i = bisect.bisect_right(shift_times, t)
    return shift_values[i - 1] if i > 0 else 0.0


@dataclass
class OsrImportParams:
    osu_faithful: bool = True

    faithful: bool = _FAITHFUL_OSU_PLAYBACK
    retime_clicks: bool = True
    recover_gap_time: bool = _RECOVER_GAP_TIME
    gap_speed_pct: float = _SPEED_REFERENCE_PCT
    gap_detect_ratio: float = _GAP_DT_RATIO
    align_to_map_start: bool = True


def parse_osr(path: str, params: OsrImportParams | None = None) -> OsrDocument:
    if params is None:
        params = OsrImportParams()
    try:
        data = open(path, "rb").read()
    except OSError as exc:
        raise OsrImportError(f"Could not read .osr file: {exc}") from exc

    try:
        i = 0
        i += 1
        i += 4
        _, i = _read_string(data, i)
        _, i = _read_string(data, i)
        _, i = _read_string(data, i)
        i += 2 * 6
        i += 4
        i += 2
        i += 1
        i += 4
        _, i = _read_string(data, i)
        i += 8
        (blob_len,) = struct.unpack_from("<i", data, i)
        i += 4
        blob = data[i:i + blob_len]
    except (IndexError, struct.error) as exc:
        raise OsrImportError(f"Malformed .osr header: {exc}") from exc

    if not blob:
        raise OsrImportError("Replay contains no cursor data (autoplay/empty replay?).")

    try:
        raw = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(blob)
        text = raw.decode("ascii", errors="ignore")
    except lzma.LZMAError as exc:
        raise OsrImportError(f"Could not decompress replay data: {exc}") from exc

    frames: list[tuple[float, float, float]] = []
    mask_changed: list[bool] = []
    press_events: list[tuple[int, int]] = []
    release_events: list[tuple[int, int]] = []
    stats_backwards_dropped = [0]

    t_ms = 0.0
    first_true_t: float | None = None
    last_kept_t: float | None = None
    prev_mask = 0
    seen_plausible_point = False
    for frame in text.split(","):
        if not frame:
            continue
        parts = frame.split("|")
        if len(parts) < 4:
            continue
        try:
            dt, x, y = float(parts[0]), float(parts[1]), float(parts[2])
            mask = int(float(parts[3])) & 0b1111
        except ValueError:
            continue
        t_ms += dt
        if dt == -12345:
            continue

        if not seen_plausible_point and not _is_plausible_cursor_pos(x, y):
            prev_mask = mask
            continue
        seen_plausible_point = True

        if last_kept_t is not None and t_ms < last_kept_t:
            stats_backwards_dropped[0] += 1
            prev_mask = mask
            continue

        if first_true_t is None:
            first_true_t = t_ms
        last_kept_t = t_ms

        frames.append((t_ms, x, y))
        mask_changed.append(mask != prev_mask)
        idx = len(frames) - 1
        new_bits = mask & ~prev_mask
        if new_bits & _KEY1_MASK:
            press_events.append((idx, 1))
        if new_bits & _KEY2_MASK:
            press_events.append((idx, 2))
        cleared_bits = prev_mask & ~mask
        if cleared_bits & _KEY1_MASK:
            release_events.append((idx, 1))
        if cleared_bits & _KEY2_MASK:
            release_events.append((idx, 2))
        prev_mask = mask

    if not frames:
        raise OsrImportError("No cursor movement frames found in this replay.")

    raw_frames = list(frames)

    stats = RepairStats()

    clicks = [(frames[idx][0], frames[idx][1], frames[idx][2], key) for idx, key in press_events]
    releases = [(frames[idx][0], frames[idx][1], frames[idx][2], key) for idx, key in release_events]

    synthetic: set[int] = set()
    shifts: list[tuple[float, float]] = []

    if params.osu_faithful:
        stats.nominal_dt_ms = _nominal_frame_dt(frames)
    elif params.faithful:
        nominal_dt = _nominal_frame_dt(frames)
        stats.nominal_dt_ms = nominal_dt

        if params.retime_clicks:
            frames = _repair_subpoll_timing(frames, nominal_dt, stats)
            clicks = [(frames[idx][0], frames[idx][1], frames[idx][2], key) for idx, key in press_events]
            releases = [(frames[idx][0], frames[idx][1], frames[idx][2], key) for idx, key in release_events]
            ref_frames = frames
        else:
            ref_frames = strip_duplicate_position_frames(
                list(frames), mask_changed, nominal_dt, RepairStats()
            )

        gap_ratio = params.gap_detect_ratio
        v_ref = _speed_reference(ref_frames, nominal_dt * gap_ratio, params.gap_speed_pct)
        frames, synthetic, shifts = _fill_dropout_gaps(
            frames, nominal_dt, stats,
            recover_time=params.recover_gap_time, v_ref=v_ref, gap_dt_ratio=gap_ratio,
        )
    else:
        nominal_seed = _nominal_frame_dt(frames)
        frames = strip_duplicate_position_frames(frames, mask_changed, nominal_seed, stats)

        nominal_dt = _nominal_frame_dt(frames)
        stats.nominal_dt_ms = nominal_dt

        frames = _repair_subpoll_timing(frames, nominal_dt, stats)

        frames, synthetic, shifts = _fill_dropout_gaps(frames, nominal_dt, stats)

    shift_times = [s[0] for s in shifts]
    shift_values = [s[1] for s in shifts]
    clicks = [(t + _shift_for(shift_times, shift_values, t), x, y, k) for t, x, y, k in clicks]
    releases = [(t + _shift_for(shift_times, shift_values, t), x, y, k) for t, x, y, k in releases]

    points = list(frames)
    raw_points = list(raw_frames)

    if params.align_to_map_start:
        points, clicks, releases, synthetic, trimmed_ms = _trim_to_map_start(
            points, clicks, releases, synthetic
        )
        stats.lead_in_trimmed_ms = trimmed_ms
    stats.map_start_offset_ms = -(first_true_t or 0.0)
    stats.backwards_frames_dropped = stats_backwards_dropped[0]

    stats.raw_duration_ms = (raw_points[-1][0] - raw_points[0][0]) if len(raw_points) > 1 else 0.0
    stats.final_duration_ms = (points[-1][0] - points[0][0]) if len(points) > 1 else 0.0

    logger.info("OSR repair: %s", stats.summary())
    if params.align_to_map_start:
        logger.info(
            "OSR sync: t=0 is osu! map time 0 (the audio start). Trimmed %.0f ms of "
            "pre-song lead-in; start playback and footage together at the first beat.",
            stats.lead_in_trimmed_ms,
        )
    else:
        logger.warning(
            "OSR sync: align_to_map_start is OFF, so t=0 is the first recorded frame, "
            "%.0f ms before the song starts. The preview will be that far out of step "
            "with gameplay footage for its whole length.",
            stats.map_start_offset_ms,
        )

    expected = stats.raw_duration_ms - stats.lead_in_trimmed_ms
    drift = stats.final_duration_ms - expected
    allowed = stats.raw_duration_ms * _MAX_TOTAL_STRETCH_FRAC + 1.0
    if abs(drift) > allowed:
        logger.warning(
            "OSR repair changed the path's span by %.0f ms (%.1f%%): expected %.0f ms "
            "-> got %.0f ms. The imported path will not stay in sync with gameplay "
            "footage. nominal frame delta was measured as %.1f ms.",
            drift, 100.0 * drift / max(expected, 1.0),
            expected, stats.final_duration_ms, stats.nominal_dt_ms,
        )

    return OsrDocument(
        points=points,
        clicks=clicks,
        releases=releases,
        synthetic_indices=synthetic,
        raw_points=raw_points,
        raw_mask_changed=list(mask_changed),
        map_start_offset_ms=-(first_true_t or 0.0),
        repair=stats,
    )


def playfield_to_bed_xy(x: float, y: float) -> tuple[float, float]:
    return x, -y


def place_osr_on_bed(
    doc: OsrDocument,
    target_width_mm: float,
    target_height_mm: float,
    rotation_deg: float,
    center_x_mm: float,
    center_y_mm: float,
) -> Recording:
    pf = [playfield_to_bed_xy(p[1], p[2]) for p in doc.points]
    xs = [p[0] for p in pf]
    ys = [p[1] for p in pf]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    src_w = max(max_x - min_x, 1e-6)
    src_h = max(max_y - min_y, 1e-6)
    sx = target_width_mm / src_w
    sy = target_height_mm / src_h
    ang = math.radians(rotation_deg)
    cos_a, sin_a = math.cos(ang), math.sin(ang)

    def to_bed(x: float, y: float) -> tuple[float, float]:
        x, y = playfield_to_bed_xy(x, y)
        lx = (x - min_x) * sx - target_width_mm / 2
        ly = (y - min_y) * sy - target_height_mm / 2
        return (center_x_mm + lx * cos_a - ly * sin_a,
                center_y_mm + lx * sin_a + ly * cos_a)

    rec = Recording()
    rec.name = "OSR Replay"
    prev = None
    for idx, (t_ms, x, y) in enumerate(doc.points):
        x_mm, y_mm = to_bed(x, y)
        t_s = t_ms / 1000.0
        vel = 0.0
        if prev is not None and t_s > prev[2]:
            vel = math.hypot(x_mm - prev[0], y_mm - prev[1]) / (t_s - prev[2])
        rec.append(x_mm, y_mm, t_s, vel)
        if idx in doc.synthetic_indices:
            rec.points[-1].is_synthetic = True
        prev = (x_mm, y_mm, t_s)

    for t_ms, x, y, key in doc.clicks:
        x_mm, y_mm = to_bed(x, y)
        rec.click_points.append(RecordedPoint(x_mm, y_mm, t_ms / 1000.0, key=key))

    for t_ms, x, y, key in doc.releases:
        x_mm, y_mm = to_bed(x, y)
        rec.key_release_points.append(RecordedPoint(x_mm, y_mm, t_ms / 1000.0, key=key))

    _flag_click_spikes(rec, doc)
    _flag_initial_outliers(rec)
    return rec


_INITIAL_OUTLIER_SPEED_MULT = 10.0
_INITIAL_OUTLIER_MAX_SCAN = 20

_CLICK_SPIKE_WINDOW_S = 0.09


def _flag_click_spikes(rec: Recording, doc: OsrDocument) -> None:
    pts = rec.points
    if len(pts) < 5:
        return

    event_times = sorted(t_ms / 1000.0 for t_ms, *_ in (*doc.clicks, *doc.releases))
    if not event_times:
        return

    velocities = [p.velocity_mm_s for p in pts[1:]]
    if not velocities:
        return
    median_v = statistics.median(velocities)
    mad_v = statistics.median(abs(v - median_v) for v in velocities) or 1e-6

    for p in pts:
        if p.is_synthetic:
            continue
        if p.velocity_mm_s <= median_v * 4 or p.velocity_mm_s <= median_v + 5 * mad_v:
            continue
        i = bisect.bisect_left(event_times, p.t_s)
        near = (
            (i < len(event_times) and event_times[i] - p.t_s <= _CLICK_SPIKE_WINDOW_S)
            or (i > 0 and p.t_s - event_times[i - 1] <= _CLICK_SPIKE_WINDOW_S)
        )
        if near:
            p.is_click_spike = True


def _flag_initial_outliers(rec: Recording) -> None:
    pts = rec.points
    if len(pts) < 4:
        return

    speeds: list[float] = []
    for i in range(1, len(pts)):
        if pts[i].velocity_mm_s > 0:
            speeds.append(pts[i].velocity_mm_s)
    if not speeds:
        return

    speeds.sort()
    median_v = speeds[len(speeds) // 2]
    threshold = median_v * _INITIAL_OUTLIER_SPEED_MULT

    for i in range(min(_INITIAL_OUTLIER_MAX_SCAN, len(pts) - 1)):
        p = pts[i]
        p_next = pts[i + 1]
        dt = p_next.t_s - p.t_s
        dist = math.hypot(p_next.x_mm - p.x_mm, p_next.y_mm - p.y_mm)
        seg_speed = dist / dt if dt > 0 else 0.0
        if seg_speed > threshold or (dt > 1.0 and i == 0):
            p.is_initial_outlier = True
            logger.info(
                "Flagged initial outlier point %d: speed %.0f mm/s "
                "(threshold %.0f mm/s, median %.0f mm/s)",
                i, seg_speed, threshold, median_v,
            )
        else:
            break
