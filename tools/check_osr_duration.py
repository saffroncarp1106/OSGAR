# Verify replay import timing duration
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import AppSettings
from storage.osr_importer import (
    OsrImportError, OsrImportParams, parse_osr, place_osr_on_bed,
)

TOLERANCE_MS = 1.0

STRETCH_TOLERANCE_FRAC = 0.02

MAX_LOCAL_WOBBLE_INTERVALS = 2.0


def check(path: Path, params: OsrImportParams) -> bool:
    try:
        doc = parse_osr(str(path), params)
    except OsrImportError as exc:
        print(f"  {path.name}: could not import: {exc}")
        return False

    raw_ms = doc.repair.raw_duration_ms
    trimmed_ms = doc.repair.lead_in_trimmed_ms
    expected_ms = raw_ms - trimmed_ms

    rec = place_osr_on_bed(doc, 200.0, 150.0, 0.0, 210.0, 210.0)
    span_ms = (rec.points[-1].t_s - rec.points[0].t_s) * 1000.0 if len(rec.points) > 1 else 0.0

    drift = span_ms - expected_ms
    if params.recover_gap_time:
        allowed = raw_ms * STRETCH_TOLERANCE_FRAC + TOLERANCE_MS
    else:
        allowed = TOLERANCE_MS
    ok = abs(drift) <= allowed

    print(f"  {path.name}")
    print(f"      replay          {raw_ms / 1000:8.3f} s   ({len(doc.raw_points)} recorded frames)")
    print(f"      lead-in trimmed {trimmed_ms / 1000:8.3f} s   "
          f"(map time 0 is {doc.repair.map_start_offset_ms:.0f} ms into the recording)")
    print(f"      expected span   {expected_ms / 1000:8.3f} s")
    print(f"      imported span   {span_ms / 1000:8.3f} s   ({len(doc.points)} points, "
          f"{len(doc.synthetic_indices)} synthetic)")
    print(f"      drift           {drift:8.1f} ms   {'OK' if ok else 'FAIL'} "
          f"(allowed +/- {allowed:.0f} ms)")
    print(f"      path starts at t={rec.points[0].t_s * 1000:.1f} ms "
          f"(0 = the moment the song starts)")
    print(f"      nominal frame delta {doc.repair.nominal_dt_ms:.1f} ms, "
          f"{doc.repair.gaps_found} gap(s), "
          f"{doc.repair.time_recovered_ms:.0f} ms recovered")

    if not ok:
        pct = 100.0 * drift / max(raw_ms, 1.0)
        print(f"      !! imported path is {pct:+.1f}% the length of the replay; it will not")
        print("         stay in sync with gameplay footage.")
        if doc.repair.time_recovered_ms > 0:
            print("         Gap stretching is ON and inserted "
                  f"{doc.repair.time_recovered_ms:.0f} ms. That is the only pass that")
            print("         inserts time; turn it off (Settings -> Replay import) for footage work.")
        else:
            print("         Check the nominal frame delta above against the replay's real")
            print("         polling rate. If it is far below, _nominal_frame_dt has been")
            print("         skewed by duplicate frames again.")

    kept = [t for i, (t, _, _) in enumerate(doc.points) if i not in doc.synthetic_indices]
    n_trimmed = len(doc.raw_points) - len(kept)
    if n_trimmed < 0:
        print(f"      !! FAIL: {len(kept)} recorded frames survive but the replay only had "
              f"{len(doc.raw_points)} - frames were duplicated")
        ok = False
    else:
        drifts = [k - r[0] for k, r in zip(kept, doc.raw_points[n_trimmed:])]
        worst = max(drifts, key=abs)
        worst_at = kept[drifts.index(worst)]
        wobble_allowed = doc.repair.nominal_dt_ms * MAX_LOCAL_WOBBLE_INTERVALS
        wobble_ok = abs(worst) <= wobble_allowed
        print(f"      {len(kept)} recorded frames kept, {n_trimmed} trimmed as lead-in")
        print(f"      worst frame vs map time {worst:6.1f} ms at t={worst_at:.0f} ms   "
              f"{'OK' if wobble_ok else 'FAIL'} (allowed +/- {wobble_allowed:.0f} ms, "
              "local re-timing only)")
        if not wobble_ok:
            print("         A frame sits further from its true map time than sub-poll")
            print("         re-timing can account for. That pass pins each run's endpoints,")
            print("         so its shifts always return to zero; a sustained offset means")
            print("         time was inserted or the timeline zero is wrong.")
            ok = False

        start_ms = doc.points[0][0]
        if start_ms < -TOLERANCE_MS:
            print(f"         !! FAIL: path starts at t={start_ms:.1f} ms, i.e. before the song.")
            print("            Lead-in was not trimmed, so the preview will be that far out")
            print("            of step with gameplay footage for its whole length.")
            ok = False
        elif start_ms > doc.repair.nominal_dt_ms * 2:
            print(f"         note: first cursor frame is {start_ms:.0f} ms into the song "
                  "(replay's own start, not an error)")

    ts = [p.t_s for p in rec.points]
    if any(b < a for a, b in zip(ts, ts[1:])):
        print("      !! FAIL: timestamps are not monotonic")
        ok = False

    return ok


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    if len(argv) > 1:
        paths = [Path(a) for a in argv[1:]]
    else:
        paths = sorted(root.glob("*.osr"))

    if not paths:
        print("No .osr files given and none found next to the app.")
        return 2

    settings = AppSettings.load()
    o = settings.osr
    params = OsrImportParams(
        retime_clicks=o.retime_clicks,
        recover_gap_time=o.recover_gap_time,
        gap_speed_pct=o.gap_speed_pct,
        gap_detect_ratio=o.gap_detect_ratio,
    )
    print(f"Import settings: retime_clicks={o.retime_clicks}, "
          f"recover_gap_time={o.recover_gap_time}, gap_speed_pct={o.gap_speed_pct}, "
          f"gap_detect_ratio={o.gap_detect_ratio}\n")

    results = [check(p, params) for p in paths]
    print()
    if all(results):
        print(f"PASS - {len(results)} replay(s) import at their recorded length.")
        return 0
    print(f"FAIL - {sum(1 for r in results if not r)} of {len(results)} replay(s) "
          "import at the wrong length.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
