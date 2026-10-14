# Moonraker printer HTTP API client
from __future__ import annotations

import http.client
import json
import socket
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field

from utils.logging_setup import get_logger

logger = get_logger("hardware")

DEFAULT_TIMEOUT_S = 5.0

_POLL_OBJECTS = "motion_report&print_stats&toolhead&idle_timeout"

ACTIVE_STATES = frozenset({"printing"})
DONE_STATES = frozenset({"complete", "cancelled", "error", "standby"})


class MoonrakerError(RuntimeError):
    pass


@dataclass
class PrinterStatus:

    state: str = "unknown"
    filename: str = ""
    print_duration: float = 0.0
    x_mm: float = 0.0
    y_mm: float = 0.0
    z_mm: float = 0.0
    homed_axes: str = ""
    latency_s: float = 0.0
    round_trip_s: float = 0.0
    raw: dict = field(default_factory=dict)

    @property
    def printing(self) -> bool:
        return self.state in ACTIVE_STATES

    @property
    def finished(self) -> bool:
        return self.state in DONE_STATES

    @property
    def is_homed(self) -> bool:
        return "x" in self.homed_axes and "y" in self.homed_axes


class MoonrakerClient:

    def __init__(
        self,
        host: str,
        port: int = 7125,
        api_key: str = "",
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.host = host.strip()
        self.port = int(port)
        self.api_key = api_key.strip()
        self.timeout_s = timeout_s
        self._conn: http.client.HTTPConnection | None = None
        self._failures = 0


    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"Connection": "keep-alive", "Accept": "application/json"}
        if self.api_key:
            h["X-Api-Key"] = self.api_key
        if extra:
            h.update(extra)
        return h

    def _connect(self) -> http.client.HTTPConnection:
        if self._conn is None:
            self._conn = http.client.HTTPConnection(
                self.host, self.port, timeout=self.timeout_s
            )
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None

    def _request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        retry: bool = True,
    ) -> tuple[dict, float]:
        started = time.monotonic()
        try:
            conn = self._connect()
            conn.request(method, path, body=body, headers=self._headers(headers))
            resp = conn.getresponse()
            payload = resp.read()
            rtt = time.monotonic() - started
        except (http.client.HTTPException, OSError) as exc:
            self.close()
            if retry:
                return self._request(method, path, body, headers, retry=False)
            self._failures += 1
            raise MoonrakerError(f"{method} {path} failed: {exc}") from exc

        if resp.status >= 400:
            detail = payload.decode("utf-8", "replace")[:400]
            if resp.status in (401, 403):
                raise MoonrakerError(
                    f"Moonraker refused the request ({resp.status}). If it is set up "
                    f"to require one, put its API key in Settings. Detail: {detail}"
                )
            raise MoonrakerError(f"{method} {path} -> HTTP {resp.status}: {detail}")

        self._failures = 0
        if not payload:
            return ({}, rtt)
        try:
            return (json.loads(payload.decode("utf-8")), rtt)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MoonrakerError(f"{method} {path} returned unparseable JSON: {exc}") from exc


    def ping(self) -> str:
        data, rtt = self._request("GET", "/printer/info")
        info = data.get("result", {})
        state = info.get("state", "?")
        name = info.get("hostname", self.host)
        klipper = info.get("software_version", "?")
        logger.info(
            "Moonraker at %s:%s reachable in %.0f ms - %s, Klipper %s, state %s",
            self.host, self.port, rtt * 1000, name, klipper, state,
        )
        return f"{name} — Klipper {klipper}, {state}"

    def status(self) -> PrinterStatus:
        data, rtt = self._request(
            "GET", f"/printer/objects/query?{_POLL_OBJECTS}"
        )
        status = data.get("result", {}).get("status", {})
        motion = status.get("motion_report", {}) or {}
        stats = status.get("print_stats", {}) or {}
        toolhead = status.get("toolhead", {}) or {}

        pos = motion.get("live_position") or toolhead.get("position") or [0.0, 0.0, 0.0, 0.0]
        while len(pos) < 3:
            pos = list(pos) + [0.0]

        return PrinterStatus(
            state=str(stats.get("state", "unknown")),
            filename=str(stats.get("filename", "") or ""),
            print_duration=float(stats.get("print_duration", 0.0) or 0.0),
            x_mm=float(pos[0]), y_mm=float(pos[1]), z_mm=float(pos[2]),
            homed_axes=str(toolhead.get("homed_axes", "") or ""),
            latency_s=rtt / 2.0,
            round_trip_s=rtt,
            raw=status,
        )


    def upload_gcode(self, text: str, filename: str, subdir: str = "osgar") -> str:
        if not filename.lower().endswith((".gcode", ".g")):
            filename += ".gcode"
        boundary = f"----osgar{uuid.uuid4().hex}"
        subdir = subdir.strip("/")

        parts: list[bytes] = []

        def field(name: str, value: str) -> None:
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n".encode("utf-8")
            )

        field("root", "gcodes")
        if subdir:
            field("path", subdir)
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\nContent-Type: text/plain\r\n\r\n'.encode("utf-8")
        )
        parts.append(text.encode("utf-8"))
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)

        data, rtt = self._request(
            "POST", "/server/files/upload", body=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
        )
        item = data.get("result", {}).get("item", {})
        remote = item.get("path") or (f"{subdir}/{filename}" if subdir else filename)
        logger.info(
            "Uploaded %.1f KiB of G-code to Moonraker as %s in %.0f ms",
            len(body) / 1024, remote, rtt * 1000,
        )
        return remote

    def clear_sd(self) -> None:
        self.run_gcode("SDCARD_RESET_FILE")
        logger.info("SD card state cleared")

    def start_print(self, remote_path: str) -> None:
        q = urllib.parse.urlencode({"filename": remote_path})
        self._request("POST", f"/printer/print/start?{q}")
        logger.info("Print started: %s", remote_path)

    def cancel_print(self) -> None:
        self._request("POST", "/printer/print/cancel")
        logger.info("Print cancelled")

    def emergency_stop(self) -> None:
        self._request("POST", "/printer/emergency_stop")
        logger.warning("Emergency stop sent to Klipper")

    def run_gcode(self, script: str) -> None:
        q = urllib.parse.urlencode({"script": script})
        self._request("POST", f"/printer/gcode/script?{q}")
        logger.info("Ran G-code: %s", script)


    def has_output_pin(self, name: str) -> bool:
        try:
            data, _ = self._request("GET", "/printer/objects/list")
        except MoonrakerError:
            return False
        objects = data.get("result", {}).get("objects", [])
        return f"output_pin {name}" in objects


def default_filename(stem: str = "osgar_replay") -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)[:48]
    return f"{safe}_{time.strftime('%Y%m%d_%H%M%S')}.gcode"


def resolve_host(host: str, port: int, timeout_s: float = 2.0) -> str:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return ""
    except socket.gaierror:
        return f"Cannot resolve '{host}'. Use the printer's IP address or hostname."
    except (OSError, socket.timeout) as exc:
        return (
            f"Nothing answering on {host}:{port} ({exc}). Check the printer is on "
            f"and that Moonraker's port is 7125 unless you changed it."
        )
