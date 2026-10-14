# Win32 keyboard key input emulation
import ctypes

_KEYEVENTF_KEYUP = 0x0002

_HID_TO_VK: dict[int, int] = {
    0x04: 0x41,
    0x06: 0x43,
    0x07: 0x44,
    0x09: 0x46,
    0x16: 0x53,
    0x19: 0x56,
    0x1B: 0x58,
    0x1D: 0x5A,
    0x2C: 0x20,
    0x36: 0xBC,
    0x37: 0xBE,
    0xE0: 0xA2,
    0xE1: 0xA0,
}


def hid_to_vk(usage: int) -> int:
    return _HID_TO_VK.get(usage, 0)


def press_key(vk: int) -> None:
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)


def release_key(vk: int) -> None:
    ctypes.windll.user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
