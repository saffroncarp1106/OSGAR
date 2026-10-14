# Wire protocol message type definitions
from __future__ import annotations

PROTOCOL_VERSION = 8

BAUD = 115200

PRESS = 1
RELEASE = 0

RING_SIZE = 96

REFILL_TARGET = 64


CMD_HELLO = "HELLO"
CMD_PING = "PING"
CMD_CONFIG = "CFG"
CMD_CLEAR = "CLEAR"
CMD_EVENT = "EV"
CMD_ARM = "ARM"
CMD_START = "START"
CMD_SYNC = "SYNC"
CMD_STOP = "STOP"
CMD_TAP = "TAP"
CMD_STATUS = "STATUS"
CMD_HEARTBEAT = "HB"

CMD_CALIBRATE = "CAL"
CMD_PREVIEW = "PRV"
CMD_LEDS = "LED"

CFG_KEY1 = "key1"
CFG_KEY2 = "key2"
CFG_LEAD_MS = "lead"
CFG_MIN_PULSE_MS = "minpulse"
CFG_WATCHDOG_MS = "wdog"
CFG_TICK_MS = "tick"
CFG_VERBOSE = "verbose"

CFG_KEYS = (
    CFG_KEY1, CFG_KEY2, CFG_LEAD_MS, CFG_MIN_PULSE_MS, CFG_WATCHDOG_MS,
    CFG_TICK_MS, CFG_VERBOSE,
)


RSP_IDENT = "OSGARHID"
RSP_OK = "OK"
RSP_ERR = "ERR"
RSP_PONG = "PONG"
RSP_STAT = "STAT"
RSP_BUTTON = "BTN"
RSP_TICK = "TICK"
RSP_SYNC_LOST = "SYNCLOST"
RSP_SYNC_FOUND = "SYNCFOUND"
RSP_FIRE = "FIRE"
RSP_CAL_EDGE = "CALEDGE"

USAGE_NAMES: dict[int, str] = {
    0x1D: "Z", 0x1B: "X", 0x06: "C", 0x19: "V",
    0x04: "A", 0x16: "S", 0x07: "D", 0x09: "F",
    0x2C: "Space", 0x36: ",", 0x37: ".",
    0xE1: "Left Shift", 0xE0: "Left Ctrl",
}


def usage_label(usage: int) -> str:
    return USAGE_NAMES.get(usage, f"usage 0x{usage:02X}")


def usage_supported(usage: int) -> bool:
    if 0xE0 <= usage <= 0xE7:
        return True
    return 0x04 <= usage <= 0x77


def encode_event(t_ms: int, key: int, action: int) -> str:
    return f"{CMD_EVENT} {int(t_ms)} {int(key)} {int(action)}"


def encode_config(key: str, value: int) -> str:
    return f"{CMD_CONFIG} {key} {int(value)}"


def parse_stat(line: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for token in line.split()[1:]:
        if "=" in token:
            k, v = token.split("=", 1)
            out[k] = v
    return out
