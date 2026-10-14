# Click to key event scheduling
from __future__ import annotations

import math
from dataclasses import dataclass, field

from config.settings import HidSettings
from recording.model import Recording
from utils.logging_setup import get_logger

logger = get_logger("hardware")

PRESS = 1
RELEASE = 0

_XY_DECIMALS = 3
_MIN_FEED_MM_S = 1.0

_RATE_WINDOW_S = 1.0


@dataclass
class KeyEvent:

    t_ms: int
    key: int
    action: int

    def __lt__(self, other: "KeyEvent") -> bool:
        return (self.t_ms, self.action) < (other.t_ms, other.action)


@dataclass
class ScheduleStats:
    presses: dict[int, int] = field(default_factory=dict)
    peak_rate_hz: dict[int, float] = field(default_factory=dict)
    shortest_hold_ms: float = math.inf
    shortest_gap_ms: float = math.inf
    holds_extended: int = 0
    zero_length_holds: int = 0
    min_pulse_ms: int = 0
    gaps_widened: int = 0
    presses_dropped: int = 0
    unmatched_presses: int = 0
    duration_ms: int = 0
    stretch: float = 1.0
    capability_hz: float = 0.0

    def total_presses(self) -> int:
        return sum(self.presses.values())

    def peak_rate(self) -> float:
        return max(self.peak_rate_hz.values(), default=0.0)

    def summary(self) -> str:
        per_key = ", ".join(
            f"K{k}: {self.presses.get(k, 0)} press(es) at up to "
            f"{self.peak_rate_hz.get(k, 0.0):.1f}/s"
            for k in sorted(self.presses)
        ) or "no clicks"
        return (
            f"{self.total_presses()} press(es) over {self.duration_ms / 1000:.1f} s "
            f"of drawing (time base x{self.stretch:.2f}); {per_key}; "
            f"shortest hold {self.shortest_hold_ms:.0f} ms, "
            f"shortest gap {self.shortest_gap_ms:.0f} ms"
        )

    def warnings(self) -> list[str]:
        out: list[str] = []
        if self.presses_dropped:
            out.append(
                f"{self.presses_dropped} press(es) dropped: they fell inside the "
                f"minimum pulse and gap of the press before them, leaving no room "
                f"for the key to come back up and go down again. At the HID "
                f"defaults this should be zero."
            )
        if self.zero_length_holds:
            out.append(
                f"{self.zero_length_holds} press(es) are recorded with zero duration "
                f"- osu! wrote the press and the release on two frames sharing a "
                f"timestamp - and were widened to the {self.min_pulse_ms} ms minimum. "
                f"No input system can reproduce a zero-length press, so this is the "
                f"smallest change that can be made rather than a setting to tune."
            )
        extended_beyond = self.holds_extended - self.zero_length_holds
        if extended_beyond > 0:
            out.append(
                f"{extended_beyond} hold(s) stretched up to the minimum pulse width. "
                f"Those keys stay down slightly longer than the replay did."
            )
        if self.gaps_widened:
            out.append(
                f"{self.gaps_widened} hold(s) cut short to keep the minimum release "
                f"gap before the next press."
            )
        if self.unmatched_presses:
            out.append(
                f"{self.unmatched_presses} press(es) had no matching release in the "
                f"replay and were given the minimum pulse width instead."
            )
        if self.capability_hz > 0 and self.peak_rate() > self.capability_hz:
            out.append(
                f"Peak demand is {self.peak_rate():.1f} presses/s but the pulse and "
                f"gap settings only allow {self.capability_hz:.1f}/s, so the fastest "
                f"passages lose notes. At the HID defaults of 1 ms this cannot "
                f"happen - if you are seeing it, min_pulse_ms/min_gap_ms have been "
                f"raised to model a mechanical presser."
            )
        return out


@dataclass
class KeySchedule:
    events: list[KeyEvent] = field(default_factory=list)
    stats: ScheduleStats = field(default_factory=ScheduleStats)

    def is_empty(self) -> bool:
        return not self.events

    def duration_ms(self) -> int:
        return self.events[-1].t_ms if self.events else 0


def build_time_map(
    recording: Recording, max_speed_mm_s: float, stretch: float
) -> tuple[list[float], list[float]]:
    pts = recording.points
    if len(pts) < 2:
        return ([0.0], [0.0])

    limit = max(max_speed_mm_s, _MIN_FEED_MM_S)
    t_rec = [pts[0].t_s]
    t_path = [0.0]

    emitted = pts[0]
    emitted_xy = (round(pts[0].x_mm, _XY_DECIMALS), round(pts[0].y_mm, _XY_DECIMALS))
    acc = 0.0
    for i in range(1, len(pts)):
        p = pts[i]
        xy = (round(p.x_mm, _XY_DECIMALS), round(p.y_mm, _XY_DECIMALS))
        if xy == emitted_xy:
            continue
        dist = math.hypot(p.x_mm - emitted.x_mm, p.y_mm - emitted.y_mm)
        dt = p.t_s - emitted.t_s
        if dt <= 0 or dist <= 0:
            feed = limit
        else:
            feed = max(dist / (dt * stretch), _MIN_FEED_MM_S)
        feed_written = round(feed * 60.0, 1) / 60.0
        travelled = math.hypot(xy[0] - emitted_xy[0], xy[1] - emitted_xy[1])
        acc += travelled / max(feed_written, 1e-9)
        t_rec.append(p.t_s)
        t_path.append(acc)
        emitted = p
        emitted_xy = xy

    return (t_rec, t_path)


def _interp(xs: list[float], ys: list[float], x: float) -> float:
    if not xs:
        return 0.0
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    lo, hi = 0, len(xs) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if xs[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    span = xs[lo] - xs[lo - 1]
    f = (x - xs[lo - 1]) / span if span > 0 else 0.0
    return ys[lo - 1] + f * (ys[lo] - ys[lo - 1])


@dataclass
class _Hold:
    key: int
    press_ms: float
    release_ms: float
    matched: bool


def _pair_holds(recording: Recording, to_path_ms) -> tuple[list[_Hold], int]:
    holds: list[_Hold] = []
    unmatched = 0
    for key in (1, 2):
        presses = sorted(p.t_s for p in recording.click_points if p.key == key)
        releases = sorted(p.t_s for p in recording.key_release_points if p.key == key)
        ri = 0
        for pt in presses:
            while ri < len(releases) and releases[ri] < pt:
                ri += 1
            if ri < len(releases):
                holds.append(_Hold(key, to_path_ms(pt), to_path_ms(releases[ri]), True))
                ri += 1
            else:
                holds.append(_Hold(key, to_path_ms(pt), to_path_ms(pt), False))
                unmatched += 1
    holds.sort(key=lambda h: (h.press_ms, h.key))
    return holds, unmatched


def build_key_schedule(
    recordings: list[Recording],
    max_speed_mm_s: float,
    stretch: float,
    hid: HidSettings,
    printer_travel_mm_s: float = 150.0,
) -> KeySchedule:
    stats = ScheduleStats(stretch=stretch, min_pulse_ms=hid.min_pulse_ms)
    stats.capability_hz = 1000.0 / max(
        hid.min_pulse_ms + hid.min_gap_ms, 1
    )

    layer_offset_ms = 0.0
    all_holds: list[_Hold] = []
    layers_with_clicks = 0

    for index, rec in enumerate(recordings):
        if rec.is_empty():
            continue
        t_rec, t_path = build_time_map(rec, max_speed_mm_s, stretch)
        layer_span_ms = t_path[-1] * 1000.0
        offset = layer_offset_ms

        if rec.click_points:
            layers_with_clicks += 1
            if index > 0:
                logger.warning(
                    "Layer %d ('%s') carries clicks but is not the first layer. The "
                    "rapid travel moves before it have no stated feed rate, so its "
                    "start time on the path clock is an estimate; sync ticks will "
                    "pull the keys back into line but the first few may be early or "
                    "late.", index + 1, rec.name,
                )

            def to_path_ms(t_s: float, _t_rec=t_rec, _t_path=t_path, _off=offset) -> float:
                return _off + _interp(_t_rec, _t_path, t_s) * 1000.0

            holds, unmatched = _pair_holds(rec, to_path_ms)
            stats.unmatched_presses += unmatched
            all_holds.extend(holds)

        layer_offset_ms += layer_span_ms
        if index + 1 < len(recordings):
            nxt = recordings[index + 1]
            if not nxt.is_empty() and rec.points:
                d = math.hypot(
                    nxt.points[0].x_mm - rec.points[-1].x_mm,
                    nxt.points[0].y_mm - rec.points[-1].y_mm,
                )
                layer_offset_ms += 1000.0 * d / max(printer_travel_mm_s, 1.0)

    stats.duration_ms = int(layer_offset_ms)

    if not all_holds:
        logger.info("No clicks in this project; there is nothing to type.")
        return KeySchedule([], stats)

    all_holds.sort(key=lambda h: (h.press_ms, h.key))
    events = _apply_hardware_limits(all_holds, hid, stats)
    events.sort()

    _measure(events, stats)
    logger.info("Key schedule: %s", stats.summary())
    for w in stats.warnings():
        logger.warning("Key schedule: %s", w)
    return KeySchedule(events, stats)


def _apply_hardware_limits(
    holds: list[_Hold], hid: HidSettings, stats: ScheduleStats
) -> list[KeyEvent]:
    min_pulse = float(hid.min_pulse_ms)
    min_gap = float(hid.min_gap_ms)
    events: list[KeyEvent] = []

    for key in (1, 2):
        mine = [h for h in holds if h.key == key]
        last_release: float | None = None
        for i, h in enumerate(mine):
            press = h.press_ms
            if last_release is not None and press < last_release + min_gap:
                stats.presses_dropped += 1
                continue

            release = max(h.release_ms, press + min_pulse)
            if release > h.release_ms + 1e-9:
                stats.holds_extended += 1
                if h.release_ms - h.press_ms <= 1e-9:
                    stats.zero_length_holds += 1

            nxt = mine[i + 1].press_ms if i + 1 < len(mine) else math.inf
            if release + min_gap > nxt:
                trimmed = nxt - min_gap
                if trimmed >= press + min_pulse:
                    release = trimmed
                    stats.gaps_widened += 1

            events.append(KeyEvent(int(round(press)), key, PRESS))
            events.append(KeyEvent(int(round(release)), key, RELEASE))
            last_release = release

    return events


def _measure(events: list[KeyEvent], stats: ScheduleStats) -> None:
    for key in (1, 2):
        presses = [e.t_ms for e in events if e.key == key and e.action == PRESS]
        releases = [e.t_ms for e in events if e.key == key and e.action == RELEASE]
        if not presses:
            continue
        stats.presses[key] = len(presses)

        for p, r in zip(presses, releases):
            stats.shortest_hold_ms = min(stats.shortest_hold_ms, r - p)
        for r, p in zip(releases, presses[1:]):
            stats.shortest_gap_ms = min(stats.shortest_gap_ms, p - r)

        window_ms = _RATE_WINDOW_S * 1000.0
        peak = 0
        start = 0
        for end in range(len(presses)):
            while presses[end] - presses[start] > window_ms:
                start += 1
            peak = max(peak, end - start + 1)
        stats.peak_rate_hz[key] = peak / _RATE_WINDOW_S

    if stats.shortest_hold_ms is math.inf:
        stats.shortest_hold_ms = 0.0
    if stats.shortest_gap_ms is math.inf:
        stats.shortest_gap_ms = 0.0
