# Serial Pro Micro HID communication
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from hardware import protocol as P
from utils.logging_setup import get_logger

logger = get_logger("hardware")

PRO_MICRO_IDS = {
    (0x1B4F, 0x9203),
    (0x1B4F, 0x9204),
    (0x1B4F, 0x9205),
    (0x1B4F, 0x9206),
    (0x1B4F, 0x9207),
    (0x2341, 0x0036),
    (0x2341, 0x8036),
    (0x2341, 0x0037),
    (0x2341, 0x8037),
    (0x2A03, 0x0036),
    (0x2A03, 0x8036),
    (0x2A03, 0x0037),
    (0x2A03, 0x8037),
}

_PORT_HINTS = ("pro micro", "leonardo", "arduino", "usb serial", "atmega32u4", "osgar")

_BOOT_SETTLE_S = 1.2

_HANDSHAKE_TIMEOUT_S = 3.0
_READ_TIMEOUT_S = 0.1


def _pyserial():
    try:
        import serial
        import serial.tools.list_ports as list_ports
    except ImportError as exc:
        raise HidLinkError(
            "pyserial is not installed, so OSGAR cannot talk to the Arduino.\n"
            "Install it with:  pip install pyserial"
        ) from exc
    return serial, list_ports


class HidLinkError(RuntimeError):
    pass


@dataclass
class PortCandidate:
    device: str
    description: str
    vid: int | None = None
    pid: int | None = None
    likely: bool = False

    def label(self) -> str:
        ids = f" [{self.vid:04x}:{self.pid:04x}]" if self.vid and self.pid else ""
        star = "* " if self.likely else "  "
        return f"{star}{self.device} — {self.description}{ids}"


@dataclass
class BoardState:

    armed: bool = False
    running: bool = False
    path_ms: int = 0
    queued: int = 0
    free: int = P.RING_SIZE
    tick_index: int = -1
    sync_locked: bool = False
    key_down: dict[int, bool] = field(default_factory=lambda: {1: False, 2: False})
    fired: int = 0
    missed: int = 0
    protocol: int = 0
    channels: int = 2
    error: bool = False
    calibrating: bool = False
    cal_edges: int = 0
    sync_pin_level: int = -1
    preview: bool = False


def list_ports() -> list[PortCandidate]:
    _, lp = _pyserial()
    out: list[PortCandidate] = []
    for p in lp.comports():
        vid, pid = getattr(p, "vid", None), getattr(p, "pid", None)
        desc = (p.description or "").strip() or "unknown device"
        likely = (vid, pid) in PRO_MICRO_IDS or any(h in desc.lower() for h in _PORT_HINTS)
        out.append(PortCandidate(p.device, desc, vid, pid, likely))
    out.sort(key=lambda c: (not c.likely, c.device))
    return out


class HidLink(QObject):

    connected = Signal(str)
    disconnected = Signal(str)
    button_pressed = Signal()
    tick_received = Signal(int, int)
    sync_lost = Signal(int)
    sync_found = Signal()
    state_changed = Signal(object)
    board_error = Signal(str)
    line_received = Signal(str)

    calibration_edge = Signal(int, int)

    def __init__(self) -> None:
        super().__init__()
        self._serial = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._out: queue.Queue[str] = queue.Queue()
        self._state = BoardState()
        self._lock = threading.Lock()
        self._latency_s = 0.0
        self._pending_pongs: dict[str, float] = {}


    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._thread is not None and self._thread.is_alive()

    @property
    def state(self) -> BoardState:
        with self._lock:
            return self._state

    @property
    def latency_ms(self) -> float:
        return self._latency_s * 1000.0

    def open(self, port: str = "", baud: int = P.BAUD) -> str:
        if self.is_open:
            raise HidLinkError("Already connected.")

        serial_mod, _ = _pyserial()
        candidates = [port] if port else [c.device for c in list_ports() if c.likely]
        if not candidates:
            raise HidLinkError(
                "No likely Arduino found. Plug the Pro Micro in, or pick the port "
                "by hand — on Windows it appears as a COMx port."
            )

        errors: list[str] = []
        for device in candidates:
            try:
                sp = serial_mod.Serial(
                    device, baudrate=baud, timeout=_READ_TIMEOUT_S, write_timeout=2.0
                )
            except Exception as exc:
                errors.append(f"{device}: {exc}")
                continue

            try:
                time.sleep(_BOOT_SETTLE_S)
                sp.reset_input_buffer()
                ident = self._handshake(sp)
            except HidLinkError as exc:
                errors.append(f"{device}: {exc}")
                try:
                    sp.close()
                except Exception:
                    pass
                continue

            self._serial = sp
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="osgar-hid", daemon=True)
            self._thread.start()
            logger.info("HID board on %s: %s", device, ident)
            self.connected.emit(f"{device} — {ident}")
            return ident

        raise HidLinkError(
            "Could not identify an OSGAR HID board on any port.\n"
            + "\n".join(errors)
            + "\n\nIf the board is plugged in, check the firmware from "
            "firmware/osgar_hidbot is actually uploaded to it."
        )

    def _handshake(self, sp) -> str:
        sp.write((P.CMD_HELLO + "\n").encode("ascii"))
        sp.flush()
        deadline = time.monotonic() + _HANDSHAKE_TIMEOUT_S
        while time.monotonic() < deadline:
            raw = sp.readline()
            if not raw:
                continue
            line = raw.decode("ascii", "replace").strip()
            if line.startswith("OSGAR ") and not line.startswith(P.RSP_IDENT):
                raise HidLinkError(
                    "This board is running the SOLENOID firmware. Upload "
                    "firmware/osgar_hidbot to use it with the HID build."
                )
            if not line.startswith(P.RSP_IDENT):
                continue
            parts = line.split()
            try:
                proto = int(parts[1])
                channels = int(parts[2]) if len(parts) > 2 else 2
                ring = int(parts[3]) if len(parts) > 3 else P.RING_SIZE
            except (IndexError, ValueError) as exc:
                raise HidLinkError(f"Unparseable identification: {line!r}") from exc

            if proto != P.PROTOCOL_VERSION:
                raise HidLinkError(
                    f"Firmware speaks protocol {proto}, OSGAR speaks "
                    f"{P.PROTOCOL_VERSION}. Re-upload firmware/osgar_hidbot."
                )
            if ring != P.RING_SIZE:
                logger.warning(
                    "Firmware ring buffer is %d events, protocol.py says %d. Using "
                    "the firmware's value.", ring, P.RING_SIZE,
                )
            with self._lock:
                self._state.protocol = proto
                self._state.channels = channels
                self._state.free = ring
            return f"protocol {proto}, {channels} key(s), {ring}-event window"

        raise HidLinkError("No reply to HELLO within 3 s.")

    def close(self, reason: str = "") -> None:
        if self._serial is not None:
            try:
                self._serial.write((P.CMD_STOP + "\n").encode("ascii"))
                self._serial.flush()
                time.sleep(0.05)
            except Exception:
                pass

        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

        logger.info("HID board disconnected%s", f": {reason}" if reason else "")
        self.disconnected.emit(reason)


    def send(self, line: str) -> None:
        if not self.is_open:
            return
        self._out.put(line)

    def send_many(self, lines: list[str]) -> None:
        for line in lines:
            self.send(line)

    def configure(self, **values: int) -> None:
        for key, value in values.items():
            if key not in P.CFG_KEYS:
                logger.warning("Ignoring unknown firmware config key %r", key)
                continue
            self.send(P.encode_config(key, int(value)))

    def clear(self) -> None:
        self.send(P.CMD_CLEAR)

    def arm(self) -> None:
        self.send(P.CMD_ARM)

    def start(self, path_ms: int = 0) -> None:
        self.send(f"{P.CMD_START} {int(path_ms)}")

    def sync(self, path_ms: int) -> None:
        self.send(f"{P.CMD_SYNC} {int(path_ms)}")

    def stop(self) -> None:
        self.send(P.CMD_STOP)

    def heartbeat(self) -> None:
        self.send(P.CMD_HEARTBEAT)

    def request_status(self) -> None:
        self.send(P.CMD_STATUS)

    def tap(self, key: int, ms: int) -> None:
        self.send(f"{P.CMD_TAP} {int(key)} {int(ms)}")

    def set_preview(self, mode: int) -> None:
        self.send(f"{P.CMD_PREVIEW} {int(mode)}")

    def set_leds(self, k1: bool, k2: bool) -> None:
        self.send(f"{P.CMD_LEDS} {1 if k1 else 0} {1 if k2 else 0}")

    def calibrate(self) -> None:
        self.send(P.CMD_CALIBRATE)

    def ping(self) -> None:
        token = f"{int(time.monotonic() * 1000) & 0xFFFF:04x}"
        self._pending_pongs[token] = time.monotonic()
        self.send(f"{P.CMD_PING} {token}")


    def _run(self) -> None:
        buf = bytearray()
        sp = self._serial
        while not self._stop.is_set():
            try:
                sent = 0
                while sent < 64:
                    try:
                        line = self._out.get_nowait()
                    except queue.Empty:
                        break
                    sp.write((line + "\n").encode("ascii"))
                    sent += 1
                if sent:
                    sp.flush()

                chunk = sp.read(256)
                if chunk:
                    buf.extend(chunk)
                    while b"\n" in buf:
                        raw, _, rest = buf.partition(b"\n")
                        buf = bytearray(rest)
                        text = raw.decode("ascii", "replace").strip()
                        if text:
                            self._handle(text)
                elif not sent:
                    time.sleep(0.002)

            except Exception as exc:
                if not self._stop.is_set():
                    logger.error("HID link dropped: %s", exc)
                    self.disconnected.emit(str(exc))
                return

    def _handle(self, line: str) -> None:
        self.line_received.emit(line)
        parts = line.split()
        head = parts[0] if parts else ""

        if head == P.RSP_CAL_EDGE and len(parts) >= 3:
            try:
                self.calibration_edge.emit(int(parts[1]), int(parts[2]))
            except ValueError:
                logger.warning("Malformed CALEDGE line: %r", line)
            return

        if head == P.RSP_BUTTON:
            if len(parts) > 1 and parts[1] == "1":
                self.button_pressed.emit()
            return

        if head == P.RSP_TICK and len(parts) >= 3:
            try:
                self.tick_received.emit(int(parts[1]), int(parts[2]))
                with self._lock:
                    self._state.tick_index = int(parts[1])
                    self._state.sync_locked = True
            except ValueError:
                logger.warning("Malformed TICK line: %r", line)
            return

        if head == P.RSP_SYNC_FOUND:
            with self._lock:
                self._state.sync_locked = True
            self.sync_found.emit()
            return

        if head == P.RSP_SYNC_LOST:
            missed = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            with self._lock:
                self._state.sync_locked = False
                self._state.missed = missed
            self.sync_lost.emit(missed)
            return

        if head == P.RSP_STAT:
            self._apply_stat(P.parse_stat(line))
            return

        if head == P.RSP_PONG and len(parts) > 1:
            started = self._pending_pongs.pop(parts[1], None)
            if started is not None:
                self._latency_s = (time.monotonic() - started) / 2.0
            return

        if head == P.RSP_ERR:
            detail = line[len(P.RSP_ERR):].strip()
            logger.error("Board reported: %s", detail)
            self.board_error.emit(detail)
            return

    def _apply_stat(self, fields: dict[str, str]) -> None:
        def as_int(key: str, default: int = 0) -> int:
            try:
                return int(fields.get(key, default))
            except ValueError:
                return default

        with self._lock:
            s = self._state
            s.armed = as_int("armed") == 1
            s.running = as_int("running") == 1
            s.path_ms = as_int("t")
            s.queued = as_int("q")
            s.free = as_int("free", s.free)
            s.fired = as_int("fired")
            s.missed = as_int("missed")
            s.sync_locked = as_int("lock") == 1
            s.tick_index = as_int("tick", -1)
            s.error = as_int("err") == 1
            s.calibrating = as_int("cal") == 1
            s.cal_edges = as_int("edges")
            s.sync_pin_level = as_int("pin", -1)
            s.preview = as_int("prv") == 1
            s.key_down = {1: as_int("k1") == 1, 2: as_int("k2") == 1}
            snapshot = BoardState(**vars(s))
            snapshot.key_down = dict(s.key_down)

        self.state_changed.emit(snapshot)
