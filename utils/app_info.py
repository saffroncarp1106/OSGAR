# Application name and directory paths
from __future__ import annotations

import shutil
from pathlib import Path

APP_NAME = "OSGAR HID"
LOG_NAMESPACE = "osgar_hid"

APP_DIR = Path.home() / ".osgar-hid"

SIBLING_APP_DIR = Path.home() / ".osgar"

_SEEDED_FILES = ("settings.json",)


def seed_from_sibling() -> bool:
    if APP_DIR.exists() or not SIBLING_APP_DIR.is_dir():
        return False

    copied = False
    APP_DIR.mkdir(parents=True, exist_ok=True)
    for name in _SEEDED_FILES:
        source = SIBLING_APP_DIR / name
        if not source.is_file():
            continue
        try:
            shutil.copy2(source, APP_DIR / name)
            copied = True
        except OSError:
            return copied
    return copied
