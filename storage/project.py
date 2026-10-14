# Project data model and container
from __future__ import annotations

from dataclasses import dataclass, field

from config.settings import AppSettings
from recording.model import Recording

PROJECT_FORMAT_VERSION = 1


@dataclass
class Project:
    settings: AppSettings = field(default_factory=AppSettings)
    recordings: list[Recording] = field(default_factory=list)
    active_recording_index: int | None = None
    playback_position_ms: int = 0
    version: int = PROJECT_FORMAT_VERSION

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "settings": self.settings.to_dict(),
            "recordings": [r.to_dict() for r in self.recordings],
            "active_recording_index": self.active_recording_index,
            "playback_position_ms": self.playback_position_ms,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        return cls(
            version=data.get("version", PROJECT_FORMAT_VERSION),
            settings=AppSettings.from_dict(data.get("settings", {})),
            recordings=[Recording.from_dict(r) for r in data.get("recordings", [])],
            active_recording_index=data.get("active_recording_index"),
            playback_position_ms=data.get("playback_position_ms", 0),
        )
