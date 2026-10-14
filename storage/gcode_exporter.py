# G-code path export and generation
from __future__ import annotations

import math
from dataclasses import dataclass

from config.settings import PrinterSettings
from motion.klipper_planner import Planner
from recording.model import RecordedPoint, Recording
from utils.app_info import APP_NAME

FIXED_Z_HEIGHT_MM = 15

_XY_DECIMALS = 3
_MIN_DT_S = 0.001
_MIN_DWELL_MS = 5
_DEFAULT_SIMPLIFY_MM = 0.0

_SOLVE_ITERS = 150
_SOLVE_RELAX = 0.7
_SOLVE_TOL = 1e-6
_ABSORB_ITERS = 60
_ABSORB_RELAX = 0.5
_DWELL_FLOOR_S = 0.002


class GcodeExportError(ValueError):
    pass


@dataclass
class SpeedAnalysis:
    peak_speed_mm_s: float
    printer_max_mm_s: float
    capped_segments: int
    total_segments: int
    scale_percent: float
    capped_time_lost_s: float
    accel_overhead_s: float = 0.0
    accel_affected_segments: int = 0

    @property
    def fits(self) -> bool:
        return self.peak_speed_mm_s <= self.printer_max_mm_s


def _rdp_simplify(
    pts: list[RecordedPoint], epsilon: float,
) -> list[RecordedPoint]:
    n = len(pts)
    if n < 3 or epsilon <= 0:
        return list(pts)

    def _time_error(p: RecordedPoint, a: RecordedPoint, b: RecordedPoint) -> float:
        span = b.t_s - a.t_s
        f = 0.0 if span <= 0 else (p.t_s - a.t_s) / span
        return math.hypot(
            p.x_mm - (a.x_mm + f * (b.x_mm - a.x_mm)),
            p.y_mm - (a.y_mm + f * (b.y_mm - a.y_mm)),
        )

    keep = [False] * n
    keep[0] = keep[-1] = True

    prev_xy = (round(pts[0].x_mm, _XY_DECIMALS), round(pts[0].y_mm, _XY_DECIMALS))
    run_start = 0
    for i in range(1, n):
        xy = (round(pts[i].x_mm, _XY_DECIMALS), round(pts[i].y_mm, _XY_DECIMALS))
        if xy != prev_xy:
            if i - 1 > run_start:
                keep[run_start] = True
                keep[i - 1] = True
            run_start = i
            prev_xy = xy
    if n - 1 > run_start:
        keep[run_start] = True
        keep[n - 1] = True

    stack = [(0, n - 1)]
    while stack:
        start, end = stack.pop()
        if end - start < 2:
            continue
        max_d = 0.0
        max_i = start
        for i in range(start + 1, end):
            if keep[i]:
                d = epsilon + 1.0
            else:
                d = _time_error(pts[i], pts[start], pts[end])
            if d > max_d:
                max_d = d
                max_i = i
        if max_d > epsilon:
            keep[max_i] = True
            stack.append((start, max_i))
            stack.append((max_i, end))

    return [p for p, k in zip(pts, keep) if k]


@dataclass
class TimingResult:
    end_drift_s: float = 0.0
    worst_error_s: float = 0.0
    rms_error_s: float = 0.0
    moves: int = 0
    solved: bool = False

    def summary(self) -> str:
        if not self.solved:
            return "timing compensation off"
        return (f"end drift {self.end_drift_s*1000:+.1f} ms, "
                f"RMS {self.rms_error_s*1000:.1f} ms, "
                f"worst {self.worst_error_s*1000:.1f} ms")


def _timing_error(planner: Planner, ops, targets) -> tuple[float, float, float]:
    times, _ = planner.run([_op_tuple(o) for o in ops])
    errs = [times[k] - targets[k] for k, o in enumerate(ops) if o[0] == "move"]
    if not errs:
        return 0.0, 0.0, 0.0
    rms = math.sqrt(sum(e * e for e in errs) / len(errs))
    return errs[-1], max(abs(e) for e in errs), rms


def _op_tuple(op):
    if op[0] == "dwell":
        return ("dwell", op[1])
    d, ux, uy, speed = op[1]
    return ("move", d, ux, uy, speed)


def _solve_feed_rates(planner: Planner, ops, targets) -> None:
    prev_targets = [0.0] + targets[:-1]
    for _ in range(_SOLVE_ITERS):
        _, durs = planner.run([_op_tuple(o) for o in ops])
        worst = 0.0
        for k, op in enumerate(ops):
            if op[0] != "move":
                continue
            allowed = targets[k] - prev_targets[k]
            if allowed <= 1e-9 or durs[k] <= 1e-12:
                continue
            ratio = durs[k] / allowed
            worst = max(worst, abs(ratio - 1.0))
            newv = op[1][3] * (1.0 + _SOLVE_RELAX * (ratio - 1.0))
            op[1][3] = max(0.01, min(planner.max_speed, newv))
        if worst < _SOLVE_TOL:
            break


def _absorb_into_dwells(planner: Planner, ops, targets) -> None:
    for _ in range(_ABSORB_ITERS):
        times, _ = planner.run([_op_tuple(o) for o in ops])
        moved = False
        for k, op in enumerate(ops):
            if op[0] != "dwell" or op[1] <= _DWELL_FLOOR_S:
                continue
            err = times[k] - targets[k]
            if err > 1e-6:
                cut = min(_ABSORB_RELAX * err, op[1] - _DWELL_FLOOR_S)
                if cut > 1e-9:
                    op[1] -= cut
                    moved = True
        if not moved:
            break


def _accel_overhead(dist: float, speed: float, accel: float) -> float:
    if accel <= 0 or dist <= 0 or speed <= 10:
        return 0.0
    d_accel = speed * speed / accel
    if dist >= d_accel:
        return 0.0
    actual_time = 2.0 * math.sqrt(dist / accel)
    commanded_time = dist / speed
    return max(actual_time - commanded_time, 0.0)


def analyze_replay_speeds(
    recordings: list[Recording],
    printer_max_mm_s: float,
    accel_mm_s2: float = 0.0,
) -> SpeedAnalysis | None:
    if not recordings:
        return None

    peak = 0.0
    capped = 0
    total = 0
    time_lost = 0.0
    accel_overhead_total = 0.0
    accel_affected = 0

    for rec in recordings:
        if rec.is_empty():
            continue
        pts = rec.points
        prev_t = pts[0].t_s
        prev_x = pts[0].x_mm
        prev_y = pts[0].y_mm
        emitted_xy = (round(pts[0].x_mm, _XY_DECIMALS), round(pts[0].y_mm, _XY_DECIMALS))

        for i in range(1, len(pts)):
            p = pts[i]
            xy = (round(p.x_mm, _XY_DECIMALS), round(p.y_mm, _XY_DECIMALS))
            if xy == emitted_xy:
                prev_t = p.t_s
                continue

            dt = p.t_s - prev_t
            dist = math.hypot(p.x_mm - prev_x, p.y_mm - prev_y)

            if dt > _MIN_DT_S and dist > 0:
                seg_speed = dist / dt
                total += 1
                if seg_speed > peak:
                    peak = seg_speed

                effective_speed = min(seg_speed, printer_max_mm_s)
                if seg_speed > printer_max_mm_s:
                    capped += 1
                    ideal_time = dist / seg_speed
                    capped_time = dist / printer_max_mm_s
                    time_lost += capped_time - ideal_time

                if accel_mm_s2 > 0:
                    oh = _accel_overhead(dist, effective_speed, accel_mm_s2)
                    if oh > 0.001:
                        accel_overhead_total += oh
                        accel_affected += 1

            emitted_xy = xy
            prev_t = p.t_s
            prev_x = p.x_mm
            prev_y = p.y_mm

    if total == 0 or peak == 0:
        return None

    if peak > printer_max_mm_s:
        scale = (printer_max_mm_s / peak) * 100.0
    else:
        scale = 100.0

    return SpeedAnalysis(
        peak_speed_mm_s=peak,
        printer_max_mm_s=printer_max_mm_s,
        capped_segments=capped,
        total_segments=total,
        scale_percent=scale,
        capped_time_lost_s=time_lost,
        accel_overhead_s=accel_overhead_total,
        accel_affected_segments=accel_affected,
    )


def export_gcode(
    recordings: list[Recording],
    printer: PrinterSettings,
    max_speed_mm_s: float,
    bed_temp_c: float = 0.0,
    nozzle_temp_c: float = 0.0,
    wait_for_temps: bool = False,
    fit_to_machine: bool = True,
    skip_home: bool = False,
    trigger_xy: tuple[float, float] | None = None,
    simplify_tolerance_mm: float = _DEFAULT_SIMPLIFY_MM,
    timing_compensation: bool = False,
    timing_out: list["TimingResult"] | None = None,
) -> str:
    if not recordings or all(r.is_empty() for r in recordings):
        raise GcodeExportError("Nothing to export — draw at least one layer.")

    printer_max = max(printer.max_speed_mm_s, 1.0)
    cap_mm_s = min(max_speed_mm_s, printer_max)
    cap_mm_min = cap_mm_s * 60.0
    accel = max(printer.max_accel_mm_s2, 0.0)
    solver_notes: list[str] = []
    timing_results: list[TimingResult] = timing_out if timing_out is not None else []

    lines: list[str] = [
        f"; Generated by {APP_NAME}",
        f"; Max feed rate = {cap_mm_s:.1f} mm/s ({cap_mm_min:.0f} mm/min)",
        f"; Fixed Z height = {FIXED_Z_HEIGHT_MM:.1f} mm",
        "G21 ; millimeter units",
        "G90 ; absolute positioning",
    ]

    if bed_temp_c > 0:
        lines.append(f"M140 S{bed_temp_c:.1f} ; set bed target temp")
        if wait_for_temps:
            lines.append(f"M190 S{bed_temp_c:.1f} ; wait for bed temp")
    if nozzle_temp_c > 0:
        lines.append(f"M104 S{nozzle_temp_c:.1f} ; set nozzle target temp")
        if wait_for_temps:
            lines.append(f"M109 S{nozzle_temp_c:.1f} ; wait for nozzle temp")

    if not skip_home:
        lines.append("G28 ; home all axes")
    lines.append(f"G0 Z{FIXED_Z_HEIGHT_MM:.3f} ; raise pen to fixed height (once)")
    if trigger_xy is not None:
        tx, ty = trigger_xy
        lines.append(f"G0 X{tx:.3f} Y{ty:.3f} F{cap_mm_min:.0f} ; move to trigger point")

    lines.append("SET_PIN PIN=caselight1 VALUE=1")

    stroke_count = 0
    prev_last_point = None
    skipped_repeats = 0
    capped_count = 0
    dwell_count = 0
    total_dwell_s = 0.0
    accel_comp_count = 0
    accel_comp_total_s = 0.0
    rdp_original_count = 0
    rdp_simplified_count = 0

    for recording in recordings:
        if recording.is_empty():
            continue
        stroke_count += 1
        pts = recording.points
        rdp_original_count += len(pts)
        if simplify_tolerance_mm > 0 and len(pts) >= 3:
            pts = _rdp_simplify(pts, simplify_tolerance_mm)
        rdp_simplified_count += len(pts)
        first = pts[0]

        if stroke_count > 1 and prev_last_point is not None:
            lines.append(f"G0 X{first.x_mm:.3f} Y{first.y_mm:.3f} F{cap_mm_min:.0f}")

        lines.append(f"; -- layer {stroke_count}: {recording.name} --")
        lines.append(f"G1 X{first.x_mm:.3f} Y{first.y_mm:.3f} F{cap_mm_min:.0f}")

        ops: list[list] = []
        targets: list[float] = []
        xys: list[tuple[float, float] | None] = []

        emitted_xy = (round(first.x_mm, _XY_DECIMALS), round(first.y_mm, _XY_DECIMALS))
        emitted_t = first.t_s
        prev_t = first.t_s
        prev_x = first.x_mm
        prev_y = first.y_mm
        accel_debt_s = 0.0

        for i in range(1, len(pts)):
            p = pts[i]
            xy = (round(p.x_mm, _XY_DECIMALS), round(p.y_mm, _XY_DECIMALS))
            if xy == emitted_xy:
                skipped_repeats += 1
                prev_t = p.t_s
                continue

            dwell_cut = 1.0 if timing_compensation else _MIN_DWELL_MS
            dwell_s = prev_t - emitted_t
            if dwell_s * 1000.0 >= dwell_cut:
                if not timing_compensation and accel_debt_s > 0 and dwell_s > 0:
                    absorbed = min(accel_debt_s, dwell_s)
                    dwell_s -= absorbed
                    accel_debt_s -= absorbed
                    accel_comp_count += 1
                    accel_comp_total_s += absorbed
                if dwell_s * 1000.0 >= dwell_cut:
                    ops.append(["dwell", dwell_s])
                    targets.append(prev_t)
                    xys.append(None)

            dt = p.t_s - prev_t
            dist = math.hypot(p.x_mm - prev_x, p.y_mm - prev_y)

            if dt > _MIN_DT_S and dist > 0:
                seg_speed = dist / dt
                if seg_speed > cap_mm_s:
                    seg_speed = cap_mm_s
                    capped_count += 1
                if not timing_compensation and accel > 0:
                    oh = _accel_overhead(dist, seg_speed, accel)
                    if oh > 0.001:
                        accel_debt_s += oh
            else:
                seg_speed = cap_mm_s

            if dist > 0:
                ops.append(["move", [dist, (p.x_mm - prev_x) / dist,
                                     (p.y_mm - prev_y) / dist, seg_speed]])
                targets.append(p.t_s)
                xys.append((p.x_mm, p.y_mm))

            emitted_xy = xy
            emitted_t = p.t_s
            prev_t = p.t_s
            prev_x = p.x_mm
            prev_y = p.y_mm

        if timing_compensation and ops and accel > 0:
            planner = Planner(accel, printer.jerk_mm_s, cap_mm_s)
            before = _timing_error(planner, ops, targets)
            _solve_feed_rates(planner, ops, targets)
            _absorb_into_dwells(planner, ops, targets)
            end, worst, rms = _timing_error(planner, ops, targets)
            timing = TimingResult(
                end_drift_s=end, worst_error_s=worst, rms_error_s=rms,
                moves=sum(1 for o in ops if o[0] == "move"), solved=True,
            )
            solver_notes.append(
                f"; layer {stroke_count} timing: {timing.summary()} "
                f"(was end drift {before[0]*1000:+.0f} ms, RMS {before[2]*1000:.0f} ms)")
            timing_results.append(timing)

        last_f_mm_min = cap_mm_min
        dwell_residual_s = 0.0
        for op, xy in zip(ops, xys):
            if op[0] == "dwell":
                want = op[1] + dwell_residual_s
                dwell_ms = int(round(want * 1000.0))
                if dwell_ms < 0:
                    dwell_ms = 0
                dwell_residual_s = want - dwell_ms / 1000.0
                if dwell_ms >= 1:
                    lines.append(f"G4 P{dwell_ms}")
                    dwell_count += 1
                    total_dwell_s += dwell_ms / 1000.0
                continue
            f_mm_min = op[1][3] * 60.0
            if abs(f_mm_min - last_f_mm_min) > 0.5:
                lines.append(f"G1 X{xy[0]:.3f} Y{xy[1]:.3f} F{f_mm_min:.0f}")
                last_f_mm_min = f_mm_min
            else:
                lines.append(f"G1 X{xy[0]:.3f} Y{xy[1]:.3f}")

        prev_last_point = pts[-1]

    lines.append("; -- end of drawing --")
    if rdp_original_count > rdp_simplified_count:
        removed = rdp_original_count - rdp_simplified_count
        pct = removed / rdp_original_count * 100
        lines.append(f"; RDP simplified {rdp_original_count} -> {rdp_simplified_count} points ({removed} removed, {pct:.1f}% reduction, tolerance {simplify_tolerance_mm:.3f}mm)")
    if skipped_repeats:
        lines.append(f"; {skipped_repeats} repeated-position move(s) deduplicated")
    if dwell_count:
        lines.append(f"; {dwell_count} dwell(s) inserted ({total_dwell_s:.3f}s total)")
    if capped_count:
        lines.append(f"; {capped_count} segment(s) capped at printer max speed ({cap_mm_s:.0f} mm/s)")
    if accel_comp_count:
        lines.append(f"; {accel_comp_count} dwell(s) shortened to compensate for accel overhead ({accel_comp_total_s*1000:.0f}ms absorbed)")
    lines.extend(solver_notes)
    if bed_temp_c > 0:
        lines.append("M140 S0 ; bed heater off")
    if nozzle_temp_c > 0:
        lines.append("M104 S0 ; nozzle heater off")
    lines.append("M84 ; disable steppers")

    return "\n".join(lines) + "\n"
