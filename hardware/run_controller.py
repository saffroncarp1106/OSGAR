# Print run orchestration and control
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum

from PySide6.QtCore import QObject, Signal

from config.settings import AppSettings
from hardware.key_schedule import KeySchedule
from hardware.moonraker_client import (
    MoonrakerClient,
    MoonrakerError,
    default_filename,
)
from utils.logging_setup import get_logger

logger = get_logger("hardware")

MOTION_TIMEOUT_S = 300.0
TRIGGER_RADIUS_MM = 2.0
HOME_DELAY_S = 15.0


class RunState(Enum):
    IDLE = "idle"
    READY = "ready"
    HOMING = "homing"
    PRINTING = "printing"
    WAITING = "waiting for trigger"
    RUNNING = "running"
    FINISHED = "finished"
    ABORTED = "aborted"
    FAILED = "failed"


@dataclass
class RunReport:
    state: RunState = RunState.IDLE
    path_ms: int = 0
    duration_ms: int = 0
    events_sent: int = 0
    events_total: int = 0
    printer_state: str = ""
    latency_ms: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def progress_pct(self) -> float:
        if self.duration_ms <= 0:
            return 0.0
        return min(100.0 * self.path_ms / self.duration_ms, 100.0)


class RunController(QObject):

    state_changed = Signal(object)
    progress = Signal(object)
    message = Signal(str)
    failed = Signal(str)
    finished = Signal(object)

    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.settings = settings
        self._state = RunState.IDLE
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._schedule: KeySchedule | None = None
        self._gcode: str | None = None
        self._moonraker: MoonrakerClient | None = None
        self._sent = 0
        self._report = RunReport()

    @property
    def state(self) -> RunState:
        return self._state

    def _set_state(self, state: RunState) -> None:
        if state is self._state:
            return
        self._state = state
        self._report.state = state
        logger.info("Run state: %s", state.value)
        self.state_changed.emit(state)

    @property
    def is_active(self) -> bool:
        return self._state in (
            RunState.HOMING, RunState.PRINTING,
            RunState.WAITING, RunState.RUNNING,
        )


    def prepare(self, schedule: KeySchedule, gcode: str) -> list[str]:
        if self.is_active:
            raise RuntimeError("A run is already in progress.")
        self._schedule = schedule
        self._gcode = gcode
        self._sent = 0
        self._report = RunReport(
            state=RunState.READY,
            duration_ms=schedule.duration_ms(),
            events_total=len(schedule.events),
        )
        self._set_state(RunState.READY)
        notes = list(schedule.stats.warnings())
        if schedule.is_empty():
            notes.append("No clicks — nothing will be typed.")
        return notes

    def check_printer(self) -> tuple[bool, str]:
        comms = self.settings.communication
        client = MoonrakerClient(comms.host, comms.port, comms.api_key)
        try:
            ident = client.ping()
        except MoonrakerError as exc:
            return (False, str(exc))
        finally:
            client.close()
        return (True, ident)

    def start(self) -> None:
        if self.is_active:
            return
        if self._state is not RunState.READY or self._schedule is None:
            raise RuntimeError("Nothing prepared to run.")
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="osgar-run", daemon=True)
        self._thread.start()

    def abort(self, reason: str = "aborted by the operator") -> None:
        self._stop.set()
        if self._moonraker is not None:
            try:
                self._moonraker.cancel_print()
            except Exception:
                pass
        self._set_state(RunState.ABORTED)
        self.message.emit(reason)


    def _run(self) -> None:
        from hardware.local_keys import hid_to_vk, press_key, release_key

        comms = self.settings.communication
        sol = self.settings.hid
        assert self._schedule is not None
        assert self._gcode is not None

        vk_map = {1: hid_to_vk(sol.key1_usage), 2: hid_to_vk(sol.key2_usage)}
        self._moonraker = MoonrakerClient(comms.host, comms.port, comms.api_key)
        poll_interval = 1.0 / max(comms.poll_hz, 1.0)

        try:
            self._set_state(RunState.HOMING)
            self.message.emit(f"Homing printer (G28) — waiting {HOME_DELAY_S:.0f}s...")
            try:
                self._moonraker.run_gcode("G28")
            except MoonrakerError:
                pass
            remaining = HOME_DELAY_S
            while remaining > 0 and not self._stop.is_set():
                step = min(remaining, 1.0)
                time.sleep(step)
                remaining -= step
            if self._stop.is_set():
                return
            self.message.emit("Homing delay done.")

            self._set_state(RunState.PRINTING)
            try:
                self._moonraker.cancel_print()
                time.sleep(0.5)
            except MoonrakerError:
                pass
            try:
                self._moonraker.run_gcode("SDCARD_RESET_FILE")
                time.sleep(0.3)
            except MoonrakerError:
                pass

            filename = default_filename()
            self.message.emit(f"Uploading {filename}...")
            remote_path = self._moonraker.upload_gcode(
                self._gcode, filename, comms.upload_dir,
            )
            self.message.emit("Starting print...")
            try:
                self._moonraker.start_print(remote_path)
            except MoonrakerError as exc:
                self.message.emit(f"Start print returned error (ignored): {exc}")
                logger.warning("start_print error (continuing): %s", exc)

            trigger_x = sol.trigger_x_mm
            trigger_y = sol.trigger_y_mm
            if trigger_x == 0.0 and trigger_y == 0.0:
                self._fail("No trigger point set. Place one on the canvas first.")
                return

            self._set_state(RunState.WAITING)
            self.message.emit(
                f"Print started — waiting for head to reach "
                f"({trigger_x:.1f}, {trigger_y:.1f}) mm..."
            )

            deadline = time.monotonic() + MOTION_TIMEOUT_S
            while not self._stop.is_set():
                try:
                    status = self._moonraker.status()
                except MoonrakerError:
                    time.sleep(0.5)
                    continue
                self._report.printer_state = status.state
                self._report.latency_ms = status.latency_s * 1000.0
                dist = ((status.x_mm - trigger_x) ** 2
                        + (status.y_mm - trigger_y) ** 2) ** 0.5
                if dist <= TRIGGER_RADIUS_MM:
                    break
                if time.monotonic() > deadline:
                    self._fail("Trigger point not reached in time.")
                    return
                time.sleep(poll_interval)
            if self._stop.is_set():
                return

            trigger_delay = sol.trigger_delay_ms
            if trigger_delay > 0:
                self.message.emit(
                    f"Trigger reached — waiting {trigger_delay} ms trigger delay..."
                )
                remaining = trigger_delay / 1000.0
                while remaining > 0 and not self._stop.is_set():
                    step = min(remaining, 0.05)
                    time.sleep(step)
                    remaining -= step
                if self._stop.is_set():
                    return

            map_offset = sol.map_offset_ms
            self.message.emit(
                f"Key playback started (map offset {map_offset:+d} ms)."
            )
            self._set_state(RunState.RUNNING)
            self._sent = 0
            t0 = time.monotonic()
            events = self._schedule.events

            while not self._stop.is_set():
                elapsed_ms = int((time.monotonic() - t0) * 1000.0) + map_offset
                self._report.path_ms = max(elapsed_ms, 0)

                while self._sent < len(events):
                    ev = events[self._sent]
                    if ev.t_ms > elapsed_ms:
                        break
                    vk = vk_map.get(ev.key, 0)
                    if vk:
                        (press_key if ev.action == 1 else release_key)(vk)
                    self._sent += 1
                    self._report.events_sent = self._sent

                self.progress.emit(self._report)

                if self._sent >= len(events) and elapsed_ms >= self._report.duration_ms:
                    self._set_state(RunState.FINISHED)
                    self.message.emit("Run finished.")
                    self.finished.emit(self._report)
                    return

                time.sleep(0.005)

        except MoonrakerError as exc:
            self._fail(f"Printer communication failed: {exc}")
        except Exception as exc:
            logger.exception("Run failed")
            self._fail(f"Run failed: {exc}")
        finally:
            for vk in vk_map.values():
                if vk:
                    try:
                        release_key(vk)
                    except Exception:
                        pass
            if self._moonraker is not None:
                self._moonraker.close()

    def _fail(self, why: str) -> None:
        self._stop.set()
        self._set_state(RunState.FAILED)
        logger.error("Run failed: %s", why)
        self.failed.emit(why)
