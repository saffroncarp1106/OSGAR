# OSGAR

**OSGAR (OSR and SVG G-code Assembler and Generator)** is a desktop application for generating G-code from `.osr` and `.svg` files.

The project was built around the **Elegoo Neptune 4 Max** and generates G-code intended for Klipper firmware. While it should work with other Klipper-based or Klipper-compatible printers, this has not been extensively tested.

You need to edit your max printer speed for your `printer.cfg` file.

The original goal of this project is to allow for extensive testing with drawing tablets.

---

# General Features

- GUI function
- Import `.svg` files.
- Import `.osr` project files.
- Generate G-code for drawing, plotting, or similar motion-based operations.
- Layer and stroke controls. *(Has Smoothing Function)*
- Timeline-based editing and project management.
- Positioning and placement tools for SVG artwork.
- Export generated G-code.
- Static `Z=20mm` offset.

Future update to add an option to change.

To change, review:

```python
storage/gcode-exporter.py
```

Look for:

```python
FIXED_Z_HEIGHT_MM = 20.0   # static pen height (raised once at start)
```

---

# Printer Compatibility

Personally tested with:

- Elegoo Neptune 4 Max

Personally ran at Klipper Mesh Bed calibration with an extra Z offset of around **20mm**, plus the Z offset in the program.

If you test OSGAR on another printer and it works, feel free to share your results.

---

# Supported File Types

## `.osr`

- Osu Replay File
- Any Osu replay should work, even Centipede…

## `.svg`

Scalable Vector Graphics files can be imported directly and converted into toolpaths for G-code generation.

---

# Output

OSGAR generates standard G-code intended for motion control on Klipper systems.

The generated output is designed around the motion characteristics of the Neptune 4 Max, so results on other machines may vary depending on firmware configuration, build volume, acceleration limits, and printer setup.

When creating a new layer, such as a new stroke, I had added a handle to provide a function for G-code to also travel to the start of the new layer stroke. If this is left out, the printer will move incredibly slowly.

---

# Current Status

OSGAR is a baby, and is still being developed. If you are interested in helping, please reach out!

---

# Notes

- Always preview generated G-code before running it on your machine. I use NCViewer.
- Test new projects at reduced speed before committing to a full run, from… personal experience.
- Use at your own risk. You are responsible for ensuring generated G-code is safe for your printer and hardware.
