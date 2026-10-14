# OSGAR HID application entry point
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from config.settings import AppSettings
from gui.main_window import MainWindow
from utils.app_info import APP_DIR, APP_NAME, SIBLING_APP_DIR, seed_from_sibling
from utils.logging_setup import get_logger, setup_logging


def main() -> int:
    debug = "--debug" in sys.argv

    seeded = seed_from_sibling()

    setup_logging(debug=debug)
    logger = get_logger("app")
    if seeded:
        logger.info("Seeded settings from %s into %s", SIBLING_APP_DIR, APP_DIR)
    logger.info("Starting %s", APP_NAME)

    settings = AppSettings.load()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    window = MainWindow(settings=settings)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
