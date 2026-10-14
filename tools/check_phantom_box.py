# Verify tablet guide inert behavior
import sys, os, math
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.chdir(Path(__file__).resolve().parent.parent)
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPointF
import gui.main_window as _mw
_mw.MainWindow._maybe_offer_autosave_recovery = lambda self: None
from config.settings import AppSettings
from storage.osr_importer import parse_osr, place_osr_on_bed
from storage.gcode_exporter import export_gcode
from gui.phantom_box_panel import PRESETS

LBL_311 = next(n for n, v in PRESETS.items() if v == (311.0, 216.0))

app = QApplication([]); ok = True
def chk(label, cond, detail=""):
    global ok; ok &= bool(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))

s = AppSettings.load()
w = _mw.MainWindow(settings=s)
print("1. defaults")
chk("box off by default", not w.canvas.has_phantom_box())
chk("not editable", not w.canvas._phantom_editable)

print("2. enable via panel")
w.phantom_box_panel.enable_check.setChecked(True)
chk("canvas has box", w.canvas.has_phantom_box())
bw, bh, bcx, bcy = w.canvas.get_phantom_box()
chk("uses stored size", (bw, bh) == (152.0, 95.0), f"{bw}x{bh}")
chk("settings updated", s.phantom_box.enabled)

print("3. preset")
w.phantom_box_panel.preset_combo.setCurrentText(LBL_311)
bw, bh, _, _ = w.canvas.get_phantom_box()
chk("preset applied", (bw, bh) == (311.0, 216.0), f"{bw}x{bh}")

print("4. center on bed")
w.phantom_box_panel.center_btn.click()
_, _, bcx, bcy = w.canvas.get_phantom_box()
chk("centered", (bcx, bcy) == (s.printer.bed_width_mm/2, s.printer.bed_height_mm/2), f"({bcx},{bcy})")

print("5. does NOT intercept clicks while not editable")
w.canvas.resize(600, 600)
center_px = w.canvas._mm_to_px(bcx, bcy)
pressed = []
w.canvas.mouse_pressed.connect(lambda x, y: pressed.append((x, y)))
from PySide6.QtGui import QMouseEvent
from PySide6.QtCore import QEvent, Qt
ev = QMouseEvent(QEvent.Type.MouseButtonPress, center_px, Qt.MouseButton.LeftButton,
                 Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
w.canvas.mousePressEvent(ev)
chk("click passed through to drawing", len(pressed) == 1, f"{pressed}")
chk("no phantom drag started", w.canvas._phantom_drag is None)

print("6. DOES respond when 'Adjust on canvas' is on")
w.phantom_box_panel.editable_check.setChecked(True)
chk("canvas editable", w.canvas._phantom_editable)
pressed.clear()
w.canvas.mousePressEvent(ev)
chk("click consumed by box", len(pressed) == 0)
chk("move drag started", w.canvas._phantom_drag and w.canvas._phantom_drag["mode"] == "move")
target = w.canvas._mm_to_px(bcx + 20.0, bcy)
w.canvas._phantom_mouse_move(target)
nw, nh, ncx, ncy = w.canvas.get_phantom_box()
chk("box moved ~20mm", abs(ncx - bcx - 20.0) < 0.6, f"delta {ncx-bcx:.2f} mm")
chk("size unchanged by move", (nw, nh) == (311.0, 216.0))
chk("panel mirrors the drag", abs(w.phantom_box_panel.cx_spin.value() - ncx) < 0.11)
w.canvas._phantom_drag = None

print("7. never reaches the exported G-code")
doc = parse_osr("BackandForthWithClicking.osr", w._osr_params())
rec = place_osr_on_bed(doc, 200, 150, 0, 210, 210)
w._add_recording(rec, normalized=False)
g_with = export_gcode(w._recordings, s.printer, s.motion.speed_limit_mm_s)
w.phantom_box_panel.enable_check.setChecked(False)
g_without = export_gcode(w._recordings, s.printer, s.motion.speed_limit_mm_s)
chk("identical gcode with box on and off", g_with == g_without,
    f"{len(g_with)} vs {len(g_without)} bytes")
chk("no layer created", len(w._recordings) == 1)
chk("box cleared from canvas", not w.canvas.has_phantom_box())

print("8. persists through settings round-trip")
s.phantom_box.enabled = True
s.phantom_box.width_mm = 224.0
d = AppSettings.from_dict(s.to_dict())
chk("survives to_dict/from_dict", d.phantom_box.enabled and d.phantom_box.width_mm == 224.0)

print("\n" + ("ALL PHANTOM BOX CHECKS PASSED" if ok else "PHANTOM BOX CHECKS FAILED"))
sys.exit(0 if ok else 1)
