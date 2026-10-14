# Verify osu decoder frame accuracy
from __future__ import annotations

import lzma
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage.osr_importer import (
    OsrImportParams, parse_osr, place_osr_on_bed, _read_string,
)

SENTINEL = (256.0, -500.0)


def reference_frames(path: Path) -> list[tuple[float, float, float]]:
    data = path.read_bytes()
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
    text = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(blob).decode(
        "ascii", errors="ignore"
    )

    last_time = 0.0
    kept: list[tuple[float, float, float]] = []
    seen_real = False
    for raw in text.split(","):
        if not raw:
            continue
        parts = raw.split("|")
        if len(parts) < 4:
            continue
        try:
            diff, x, y = float(parts[0]), float(parts[1]), float(parts[2])
        except ValueError:
            continue

        last_time += diff
        if diff == -12345:
            continue
        if not seen_real and (x, y) == SENTINEL:
            continue
        seen_real = True
        if kept and last_time < kept[-1][0]:
            continue
        kept.append((last_time, x, y))
    return kept


def check(path: Path) -> bool:
    ref = reference_frames(path)
    doc = parse_osr(str(path), OsrImportParams(align_to_map_start=False))
    got = doc.points

    print(f"  {path.name}")
    print(f"      osu! reference : {len(ref)} frames, "
          f"t {ref[0][0]:.0f} .. {ref[-1][0]:.0f} ms")
    print(f"      this importer  : {len(got)} frames, "
          f"t {got[0][0]:.0f} .. {got[-1][0]:.0f} ms")

    if len(ref) != len(got):
        print(f"      !! FAIL: frame count differs by {len(got) - len(ref)}")
        return False

    worst_t = worst_xy = 0.0
    worst_at = 0.0
    for (rt, rx, ry), (gt, gx, gy) in zip(ref, got):
        if abs(gt - rt) > worst_t:
            worst_t, worst_at = abs(gt - rt), rt
        worst_xy = max(worst_xy, abs(gx - rx), abs(gy - ry))

    ok = worst_t <= 1e-6 and worst_xy <= 1e-6
    print(f"      worst time difference : {worst_t:.6f} ms"
          + (f" (at t={worst_at:.0f})" if worst_t else ""))
    print(f"      worst position diff   : {worst_xy:.6f} px")
    print(f"      -> {'identical to osu!' if ok else 'MISMATCH'}")
    if not ok:
        print("         The importer is not reproducing osu!'s decoding. Any preview")
        print("         built on this cannot line up with gameplay footage.")
    return ok and check_orientation(path)


def check_orientation(path: Path) -> bool:
    bed = 420.0
    doc = parse_osr(str(path))
    rec = place_osr_on_bed(doc, 300.0, 250.0, 0.0, bed / 2, bed / 2)

    osu_xy = [(x, y) for _, x, y in doc.points]
    bed_xy = [(p.x_mm, bed - p.y_mm) for p in rec.points]
    if len(osu_xy) != len(bed_xy):
        print(f"      !! FAIL: {len(bed_xy)} placed points vs {len(osu_xy)} path points")
        return False

    def unit(pts):
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        mnx, mxx, mny, mxy = min(xs), max(xs), min(ys), max(ys)
        return [((x - mnx) / max(mxx - mnx, 1e-9), (y - mny) / max(mxy - mny, 1e-9))
                for x, y in pts]

    a, b = unit(osu_xy), unit(bed_xy)
    worst = max(max(abs(p[0] - q[0]), abs(p[1] - q[1])) for p, q in zip(a, b))
    ok = worst <= 1e-6
    print(f"      orientation vs osu!   : {worst:.6f} "
          f"({'same picture' if ok else 'MIRRORED OR ROTATED'})")
    if not ok:
        print("         The bed path is not the drawing osu! shows. Check")
        print("         playfield_to_bed_xy in storage/osr_importer.py.")
    return ok


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    paths = [Path(a) for a in argv[1:]] or sorted(root.glob("*.osr"))
    if not paths:
        print("No .osr files given and none found next to the app.")
        return 2
    print("Comparing against a standalone transcription of ppy/osu's "
          "LegacyScoreDecoder\n")
    results = [check(p) for p in paths]
    print()
    if all(results):
        print(f"PASS - {len(results)} replay(s) decode identically to osu!.")
        return 0
    print(f"FAIL - {sum(1 for r in results if not r)} of {len(results)} replay(s) differ.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
