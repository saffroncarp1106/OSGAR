# Verify tablet fit snap placement
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from PySide6.QtWidgets import QApplication

import gui.main_window as _mw

_mw.MainWindow._maybe_offer_autosave_recovery = lambda self: None

from config.settings import AppSettings
from gui.phantom_box_panel import PRESETS, TABLET_SIZES
from storage.osr_importer import (
    parse_osr, place_osr_on_bed, playfield_to_bed_xy,
)

app = QApplication([])
ok = True


def chk(label, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))


print("1. presets are grouped by active area")
sizes = [(w, h) for w, h, _ in TABLET_SIZES]
chk("no duplicate sizes", len(sizes) == len(set(sizes)), f"{len(sizes)} entries")
lbl_152 = next(n for n in PRESETS if PRESETS[n] == (152.0, 95.0))
chk("152x95 groups Intuos S + CTL-472 + CTL-480",
    all(k in lbl_152 for k in ("Intuos S", "472", "480")))
lbl_160 = next(n for n in PRESETS if PRESETS[n] == (160.0, 100.0))
chk("160x100 groups G640 + H640P + Intuos Pro S",
    all(k in lbl_160 for k in ("G640", "H640P", "PTH-460")))

settings = AppSettings.load()
win = _mw.MainWindow(settings=settings)

print("2. the guide stays visible while placing a replay")
win.phantom_box_panel.enable_check.setChecked(True)
win.phantom_box_panel.preset_combo.setCurrentText(lbl_152)
win.phantom_box_panel.center_btn.click()

doc = parse_osr("BackandForthWithClicking.osr", win._osr_params())
win._pending_osr_doc = doc
subpaths = [[playfield_to_bed_xy(x, y) for _, x, y in doc.points]]
win.canvas.start_svg_preview(subpaths, 200.0, 150.0, 0.0, 210.0, 210.0)
win.svg_placement_panel.set_values(200.0, 150.0, 0.0, 210.0, 210.0)
win._sync_fit_tablet_button()
chk("guide still drawn", win.canvas.has_phantom_box())
chk("placement preview active", win.canvas.has_svg_preview())
chk("fit button shown", win.svg_placement_panel.fit_tablet_btn.isVisibleTo(
    win.svg_placement_panel))

print("3. fit snaps the placement to the box exactly")
win._on_fit_to_tablet()
tw, th, trot, tcx, tcy = win.canvas.get_svg_transform()
pw, ph, pcx, pcy = win.canvas.get_phantom_box()
chk("width matches", abs(tw - pw) < 1e-9, f"{tw} vs {pw}")
chk("height matches", abs(th - ph) < 1e-9, f"{th} vs {ph}")
chk("centre matches", abs(tcx - pcx) < 1e-9 and abs(tcy - pcy) < 1e-9, f"({tcx}, {tcy})")
chk("panel mirrors it", abs(win.svg_placement_panel.width_spin.value() - pw) < 0.01)

print("4. the resulting path really fills the box")
rec = place_osr_on_bed(doc, tw, th, trot, tcx, tcy)
xs = [p.x_mm for p in rec.points]
ys = [p.y_mm for p in rec.points]
chk("path width == box width", abs((max(xs) - min(xs)) - pw) < 1e-6,
    f"{max(xs) - min(xs):.6f} mm")
chk("path height == box height", abs((max(ys) - min(ys)) - ph) < 1e-6,
    f"{max(ys) - min(ys):.6f} mm")
chk("path centred on the box",
    abs((max(xs) + min(xs)) / 2 - pcx) < 1e-6 and abs((max(ys) + min(ys)) / 2 - pcy) < 1e-6)

print("5. inert when no guide is shown")
win.phantom_box_panel.enable_check.setChecked(False)
chk("fit button hidden", not win.svg_placement_panel.fit_tablet_btn.isVisibleTo(
    win.svg_placement_panel))
win._on_fit_to_tablet()
chk("fit is a no-op", abs(win.canvas.get_svg_transform()[0] - tw) < 1e-9)

print("\n" + ("ALL TABLET FIT CHECKS PASSED" if ok else "TABLET FIT CHECKS FAILED"))
sys.exit(0 if ok else 1)
