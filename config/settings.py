# Application settings and persistence layer
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from utils.app_info import APP_DIR
from utils.logging_setup import get_logger

logger = get_logger("config")

SETTINGS_DIR = APP_DIR
SETTINGS_FILE = SETTINGS_DIR / "settings.json"


def _known_fields(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    allowed = {f.name for f in fields(cls)}
    unknown = set(data) - allowed
    if unknown:
        logger.info("Ignoring unknown %s keys: %s", cls.__name__, ", ".join(sorted(unknown)))
    return {k: v for k, v in data.items() if k in allowed}


@dataclass
class PrinterSettings:
    bed_width_mm: float = 235.0
    bed_height_mm: float = 235.0
    origin_x_mm: float = 0.0
    origin_y_mm: float = 0.0
    max_speed_mm_s: float = 200.0
    max_accel_mm_s2: float = 2000.0
    jerk_mm_s: float = 8.0


@dataclass
class MotionSettings:
    scale: float = 1.0
    update_rate_hz: float = 30.0
    smoothing_factor: float = 0.35
    deadzone_px: float = 2.0


@dataclass
class CommunicationSettings:
    mode: str = "moonraker"
    host: str = "192.168.0.137"
    port: int = 80
    api_key: str = ""

    poll_hz: float = 20.0

    upload_dir: str = "osgar"

    start_print: bool = True

    local_only: bool = True


@dataclass
class UISettings:
    theme: str = "dark"
    target_fps: int = 60


@dataclass
class OsrSettings:
    playback_tick_ms: int = 16
    osu_faithful: bool = True
    align_to_map_start: bool = True
    retime_clicks: bool = True
    recover_gap_time: bool = False
    gap_speed_pct: float = 0.85
    gap_detect_ratio: float = 2.5


@dataclass
class PhantomBoxSettings:
    enabled: bool = False
    width_mm: float = 152.0
    height_mm: float = 95.0
    center_x_mm: float = 117.5
    center_y_mm: float = 117.5
    editable: bool = False
    lock_aspect: bool = True


@dataclass
class HidSettings:

    enabled: bool = False

    port: str = ""
    baud: int = 115200

    key1_usage: int = 0x1D
    key2_usage: int = 0x1B

    min_pulse_ms: int = 1
    min_gap_ms: int = 1

    lead_ms: int = 0

    trigger_x_mm: float = 0.0
    trigger_y_mm: float = 0.0
    trigger_delay_ms: int = 0
    map_offset_ms: int = 0

    sync_source: str = "pin"
    sync_tick_ms: int = 200
    sync_missed_tick_limit: int = 3
    sync_gain: float = 0.25
    sync_reanchor_ms: float = 250.0
    position_tolerance_mm: float = 5.0
    extra_latency_ms: float = 15.0

    watchdog_ms: int = 500


@dataclass
class AppSettings:
    printer: PrinterSettings = field(default_factory=PrinterSettings)
    motion: MotionSettings = field(default_factory=MotionSettings)
    communication: CommunicationSettings = field(default_factory=CommunicationSettings)
    ui: UISettings = field(default_factory=UISettings)
    osr: OsrSettings = field(default_factory=OsrSettings)
    phantom_box: PhantomBoxSettings = field(default_factory=PhantomBoxSettings)
    hid: HidSettings = field(default_factory=HidSettings)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppSettings":
        def section(name: str, kls: type):
            return kls(**_known_fields(kls, data.get(name, {})))

        return cls(
            printer=section("printer", PrinterSettings),
            motion=section("motion", MotionSettings),
            communication=section("communication", CommunicationSettings),
            ui=section("ui", UISettings),
            osr=section("osr", OsrSettings),
            phantom_box=section("phantom_box", PhantomBoxSettings),
            hid=section("hid", HidSettings),
        )

    def save(self, path: Path = SETTINGS_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        logger.info("Settings saved to %s", path)

    @classmethod
    def load(cls, path: Path = SETTINGS_FILE) -> "AppSettings":
        if not path.exists():
            logger.info("No settings file found, using defaults")
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("Failed to load settings (%s), using defaults", exc)
            return cls()
