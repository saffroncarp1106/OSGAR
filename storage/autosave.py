# Autosave path and file location
from __future__ import annotations

from storage.serializer import PROJECT_FILE_EXTENSION
from utils.app_info import APP_DIR

AUTOSAVE_PATH = APP_DIR / f"autosave{PROJECT_FILE_EXTENSION}"
