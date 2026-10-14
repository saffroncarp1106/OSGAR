# Inspect replay repair pass results
from __future__ import annotations

import csv
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage import osr_importer as osr


Frames = list[tuple[float, float, float]]


def frames_from_csv(path: Path) -> tuple[Frames, list[bool]]:
    frames: Frames = []
    mask_changed: list[bool] = []
    t = 0.0
    prev_mask = 0
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if not row or not row[0].lstrip("-").replace(".", "", 1).isdigit():
                continue
            dt, x, y = float(row[0]), float(row[1]), float(row[2])
            mask = int(float(row[3])) & 0b1111 if len(row) > 3 else 0
            if dt == -12345:
                break
            t += max(dt, 0.0)
            frames.append((t, x, y))
            mask_changed.append(mask != prev_mask)
            prev_mask = mask
    return frames, mask_changed


def frames_from_osr(path: Path) -> tuple[Frames, list[bool]]:
    doc = osr.parse_osr(str(path))
    return [(t, x, y) for t, x, y in doc.raw_points], list(doc.raw_mask_changed)


def speeds(frames: list[tuple[float, float, float]]) -> list[float]:
    out = []
    for (t0, x0, y0), (t1, x1, y1) in zip(frames, frames[1:]):
        dt = t1 - t0
        if dt <= 0:
            continue
        out.append(math.hypot(x1 - x0, y1 - y0) / dt)
    return out


def describe(label: str, frames: list[tuple[float, float, float]]) -> None:
    v = speeds(frames)
    if not v:
        print(f"{label}: no measurable segments")
        return
    med = statistics.median(v) or 1e-9
    worst = max(v)
    over_3x = sum(1 for s in v if s > med * 3)
    over_5x = sum(1 for s in v if s > med * 5)
    print(
        f"{label}\n"
        f"    samples            {len(frames)}\n"
        f"    duration           {frames[-1][0] - frames[0][0]:.3f} ms\n"
        f"    median speed       {med:.3f} px/ms\n"
        f"    worst speed        {worst:.3f} px/ms  ({worst / med:.1f}x median)\n"
        f"    segments >3x med   {over_3x}\n"
        f"    segments >5x med   {over_5x}"
    )


def worst_offenders(
    frames: list[tuple[float, float, float]],
    synthetic: set[int] | None = None,
    top: int = 12,
) -> None:
    synthetic = synthetic or set()
    v = speeds(frames)
    if not v:
        return
    med = statistics.median(v) or 1e-9
    scored = []
    for i in range(1, len(frames)):
        dt = frames[i][0] - frames[i - 1][0]
        if dt <= 0:
            continue
        d = math.hypot(frames[i][1] - frames[i - 1][1], frames[i][2] - frames[i - 1][2])
        scored.append((d / dt / med, i, d, dt))
    scored.sort(reverse=True)

    print(f"\nworst remaining segments (median = {med:.3f} px/ms)")
    for ratio, i, d, dt in scored[:top]:
        tag = "synth" if i in synthetic else "     "
        print(
            f"    {ratio:6.1f}x  idx {i:6d} {tag}  {d:8.2f} px in {dt:7.2f} ms"
            f"   ({frames[i-1][1]:7.2f},{frames[i-1][2]:6.2f}) -> "
            f"({frames[i][1]:7.2f},{frames[i][2]:6.2f})"
        )


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"No such file: {path}")
        return 1

    loader = frames_from_osr if path.suffix.lower() == ".osr" else frames_from_csv
    raw, mask_changed = loader(path)
    if len(raw) < 3:
        print("Not enough frames to analyse.")
        return 1

    stats = osr.RepairStats()
    seed = osr._nominal_frame_dt(raw)
    stripped = osr.strip_duplicate_position_frames(list(raw), mask_changed, seed, stats)
    nominal = osr._nominal_frame_dt(stripped)
    stats.nominal_dt_ms = nominal
    retimed = osr._repair_subpoll_timing(stripped, nominal, stats)
    filled, synthetic, shifts = osr._fill_dropout_gaps(retimed, nominal, stats)

    print(f"file: {path.name}")
    print(f"repair: {stats.summary()}\n")
    describe("BEFORE (raw replay frames)", raw)
    print()
    describe("AFTER  (repaired + gap-filled)", filled)

    print("\nchecks")
    dur_raw = raw[-1][0] - raw[0][0]
    dur_new = filled[-1][0] - filled[0][0]
    recovered = stats.time_recovered_ms
    drift = abs((dur_new - dur_raw) - recovered)
    print(f"    duration accounted for     {'OK' if drift < 1e-6 else 'FAIL'}"
          f"  ({dur_raw:.1f} -> {dur_new:.1f} ms, {recovered:.1f} ms re-inserted at gaps)")

    kept = [p for i, p in enumerate(filled) if i not in synthetic]
    expected = len(raw) - stats.duplicate_frames_dropped
    same_count = len(kept) == expected
    same_coords = same_count and all(
        a[1] == b[1] and a[2] == b[2] for a, b in zip(stripped, kept)
    )
    print(f"    frame accounting           {'OK' if same_count else 'FAIL'}"
          f"  ({len(raw)} raw - {stats.duplicate_frames_dropped} dupes = {expected};"
          f" got {len(kept)} recorded + {len(synthetic)} synthetic)")
    print(f"    surviving coords untouched {'OK' if same_coords else 'FAIL'}")

    lost = 0
    kept_set = {(round(x, 6), round(y, 6)) for _, x, y in stripped}
    for _, x, y in raw:
        if (round(x, 6), round(y, 6)) not in kept_set:
            lost += 1
    print(f"    no position lost           {'OK' if lost == 0 else 'FAIL'}  ({lost} orphaned)")

    monotonic = all(b[0] >= a[0] for a, b in zip(filled, filled[1:]))
    zeros_raw = sum(1 for a, b in zip(raw, raw[1:]) if b[0] <= a[0])
    zeros_new = sum(1 for a, b in zip(filled, filled[1:]) if b[0] <= a[0])
    print(f"    timestamps non-decreasing  {'OK' if monotonic else 'FAIL'}")
    print(f"    zero-length time steps     {'OK' if zeros_new <= zeros_raw else 'FAIL'}"
          f"  ({zeros_raw} raw -> {zeros_new} repaired)")

    worst_offenders(filled, synthetic)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
