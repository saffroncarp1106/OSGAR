# Logging configuration and handler setup
from __future__ import annotations

import logging
import sys

from utils.app_info import APP_DIR, LOG_NAMESPACE

LOG_DIR = APP_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"

_CATEGORIES = ("printer", "motion", "recording", "playback", "gui", "storage")


def setup_logging(debug: bool = False) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger(LOG_NAMESPACE)
    if root.handlers:
        root.setLevel(logging.DEBUG if debug else logging.INFO)
        return

    root.setLevel(logging.DEBUG if debug else logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
        datefmt="%H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(logging.DEBUG if debug else logging.INFO)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)

    root.addHandler(console)
    root.addHandler(file_handler)


def get_logger(category: str) -> logging.Logger:
    return logging.getLogger(f"{LOG_NAMESPACE}.{category}")
