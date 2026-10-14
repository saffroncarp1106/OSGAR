# Recent files list path management
from __future__ import annotations

import json
from pathlib import Path

from utils.app_info import APP_DIR
from utils.logging_setup import get_logger

logger = get_logger("storage")

RECENT_FILES_PATH = APP_DIR / "recent_files.json"
MAX_RECENT = 10


class RecentFiles:
    def __init__(self, path: Path = RECENT_FILES_PATH, max_entries: int = MAX_RECENT) -> None:
        self._path = path
        self._max_entries = max_entries

    def get_all(self) -> list[str]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            entries = data.get("recent", [])
            return [p for p in entries if Path(p).exists()]
        except (json.JSONDecodeError, OSError, TypeError) as exc:
            logger.warning("Could not read recent files list: %s", exc)
            return []

    def add(self, path: Path) -> None:
        entries = self.get_all()
        path_str = str(path)
        entries = [p for p in entries if p != path_str]
        entries.insert(0, path_str)
        entries = entries[: self._max_entries]
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps({"recent": entries}, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not write recent files list: %s", exc)
