# Verify HID sync timeline agreement
from __future__ import annotations

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import AppSettings
from hardware.key_schedule import build_key_schedule, build_time_map
from hardware.print_path import (
    PREAMBLE_DWELL_MS,
    PREAMBLE_EDGES,
    PREAMBLE_MARK_GAP_MS,
    TICK_WARN_MS,
    insert_sync_marks,
    parse_gcode_path,
)
from motion.speed_fit import describe_fit
from storage.gcode_exporter import export_gcode
from storage.osr_importer import (
    OsrImportError, OsrImportParams, parse_osr, place_osr_on_bed,
)

TIME_MAP_TOLERANCE_MS = 1.0

DURATION_TOLERANCE_MS = 5.0
LENGTH_TOLERANCE_MM = 0.01

TICK_TOLERANCE_MS = TICK_WARN_MS

TABLET_W_MM, TABLET_H_MM = 152.0, 95.0
BED_CENTRE = (117.5, 117.5)


def simulate_firmware_decode(tick_times_s: list[float], tick_ms: int) -> tuple[list[float], list[str]]:
    problems: list[str] = []
    if not tick_times_s:
        return ([], ["no ticks to decode"])

    edges_ms: list[float] = []
    t = -(PREAMBLE_EDGES * PREAMBLE_DWELL_MS + PREAMBLE_MARK_GAP_MS)
    for _ in range(PREAMBLE_EDGES):
        t += PREAMBLE_DWELL_MS
        edges_ms.append(t)
    edges_ms.extend(tk * 1000.0 for tk in tick_times_s)

    believed: list[float] = []
    fast_run = 0
    locked = False
    index = -1
    prev = edges_ms[0] - PREAMBLE_DWELL_MS
    missed = 0

    for edge in edges_ms:
        gap = edge - prev
        prev = edge

        if 15 <= gap <= 70:
            fast_run += 1
            continue

        if fast_run >= 3 and 90 <= gap <= 165:
            locked = True
            index = 0
            fast_run = 0
            believed.append(0.0)
            continue

        if locked:
            steps = int((gap + tick_ms / 2) // tick_ms)
            if steps < 1:
                steps = 1
            if steps > 1:
                missed += steps - 1
            index += steps
            believed.append(float(index * tick_ms))
            continue

        fast_run = 0

    if not locked:
        problems.append(
            "the firmware would never have recognised the preamble, so the run "
            "would never start"
        )
    if missed:
        problems.append(f"decoder inferred {missed} missed tick(s) where none were dropped")
    if len(believed) != len(tick_times_s):
        problems.append(
            f"decoded {len(believed)} tick(s) from {len(tick_times_s)} emitted"
        )
    return (believed, problems)


def check(path: Path, settings: AppSettings) -> bool:
    speed = settings.motion.speed_limit_mm_s
    printer = settings.printer
    sol = settings.hid
    tick_ms = sol.sync_tick_ms

    try:
        doc = parse_osr(str(path), OsrImportParams())
    except OsrImportError as exc:
        print(f"  {path.name}: could not import: {exc}")
        return False

    rec = place_osr_on_bed(doc, TABLET_W_MM, TABLET_H_MM, 0.0, *BED_CENTRE)
    limit = min(speed, printer.max_speed_mm_s)
    stretch = describe_fit([rec], limit)["stretch"]

    gcode = export_gcode([rec], printer, max_speed_mm_s=speed)
    plain = parse_gcode_path(gcode, travel_speed_mm_s=limit)

    print(f"  {path.name}")
    print(f"      replay {rec.duration_s:8.2f} s, {len(rec.click_points)} click(s); "
          f"time base x{stretch:.3f}")
    print(f"      drawing {plain.duration_s:8.2f} s over {len(plain.moves)} move(s)")

    ok = True

    _, t_path = build_time_map(rec, limit, stretch)
    drift_ms = (t_path[-1] - plain.duration_s) * 1000.0
    good = abs(drift_ms) <= TIME_MAP_TOLERANCE_MS
    ok &= good
    print(f"      time map vs G-code      {drift_ms:+8.3f} ms   "
          f"{'OK' if good else 'FAIL'} (allowed +/- {TIME_MAP_TOLERANCE_MS:.1f} ms)")
    if not good:
        print("         !! build_time_map no longer reproduces the exporter's feed rule,")
        print("            so every key fires against a timeline the printer is not running.")

    res = insert_sync_marks(gcode, tick_ms=tick_ms, travel_speed_mm_s=limit)
    marked = parse_gcode_path(res.gcode, travel_speed_mm_s=limit)

    d_dur_ms = (marked.duration_s - plain.duration_s) * 1000.0
    len_plain = sum(m.length_mm for m in plain.moves)
    len_marked = sum(m.length_mm for m in marked.moves)
    d_len = len_marked - len_plain

    dur_ok = abs(d_dur_ms) <= DURATION_TOLERANCE_MS
    len_ok = abs(d_len) <= LENGTH_TOLERANCE_MM
    ok &= dur_ok and len_ok
    print(f"      {res.summary()}")
    print(f"      duration after marking  {d_dur_ms:+8.3f} ms   "
          f"{'OK' if dur_ok else 'FAIL'} (allowed +/- {DURATION_TOLERANCE_MS:.1f} ms)")
    print(f"      path length after mark  {d_len:+8.5f} mm   "
          f"{'OK' if len_ok else 'FAIL'} (allowed +/- {LENGTH_TOLERANCE_MM:.3f} mm)")
    if not len_ok:
        print("         !! splits are not collinear; Klipper will decelerate into a")
        print("            corner that is not in the drawing.")

    errs = [abs(t.error_ms) for t in res.ticks]
    if errs:
        worst = max(errs)
        tick_ok = worst <= TICK_TOLERANCE_MS
        ok &= tick_ok
        over = sum(1 for e in errs if e > TICK_TOLERANCE_MS)
        print(f"      tick placement worst   {worst:8.2f} ms   "
              f"{'OK' if tick_ok else 'FAIL'} (mean {statistics.mean(errs):.3f}, "
              f"{over} over {TICK_TOLERANCE_MS:.1f} ms)")
        if not tick_ok:
            bad = max(res.ticks, key=lambda t: abs(t.error_ms))
            print(f"         !! tick {bad.index} wanted {bad.grid_t_s:.4f} s, "
                  f"landed at {bad.actual_t_s:.4f} s")

    believed, problems = simulate_firmware_decode(
        [t.actual_t_s for t in res.ticks], tick_ms
    )
    if problems:
        ok = False
        for p in problems:
            print(f"      firmware decode         FAIL - {p}")
    else:
        deltas = [
            (b - a * 1000.0) for b, a in zip(believed, (t.actual_t_s for t in res.ticks))
        ]
        worst = max(abs(d) for d in deltas) if deltas else 0.0
        decode_ok = worst <= TICK_TOLERANCE_MS
        ok &= decode_ok
        print(f"      firmware decode worst  {worst:8.2f} ms   "
              f"{'OK' if decode_ok else 'FAIL'} over {len(believed)} tick(s)")

    sched = build_key_schedule([rec], limit, stretch, sol, limit)
    st = sched.stats
    print(f"      schedule: {len(sched.events)} event(s), peak "
          f"{st.peak_rate():.1f} press/s vs {st.capability_hz:.1f}/s capability")
    last = sched.events[-1].t_ms if sched.events else 0
    within = last <= marked.duration_s * 1000.0 + 1.0
    ok &= within
    print(f"      last event {last / 1000:8.2f} s within drawing {marked.duration_s:8.2f} s   "
          f"{'OK' if within else 'FAIL'}")
    for w in st.warnings():
        print(f"         note: {w}")

    return ok


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    paths = [Path(a) for a in argv[1:]] if len(argv) > 1 else sorted(root.glob("*.osr"))
    if not paths:
        print("No .osr files given and none found next to the app.")
        print(f"Usage: python {Path(argv[0]).name} <replay.osr> [...]")
        return 2

    settings = AppSettings.load()
    s = settings.hid
    print(f"Tick spacing {s.sync_tick_ms} ms; min pulse {s.min_pulse_ms} ms, "
          f"min gap {s.min_gap_ms} ms, lead {s.lead_ms} ms")
    print(f"Speed limit {settings.motion.speed_limit_mm_s:.0f} mm/s, "
          f"printer max {settings.printer.max_speed_mm_s:.0f} mm/s\n")

    results = [check(p, settings) for p in paths]
    print()
    if all(results):
        print(f"PASS - {len(results)} replay(s): schedule, sync ticks and G-code agree.")
        return 0
    print(f"FAIL - {sum(1 for r in results if not r)} of {len(results)} replay(s) "
          "disagree somewhere in the chain.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
