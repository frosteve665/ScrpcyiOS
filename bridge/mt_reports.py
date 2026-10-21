"""HID touchscreen report builders for the multitouch experiment (pure, no pymobiledevice3).

Proven (from pymobiledevice3 captures): 58-byte report, id 0x09, one contact at bytes 1..7,
32 reserved zero bytes at 8..39. HYPOTHESIS (unproven): extra contacts live in that reserved area.
Each variant below is one guess at how a second contact is laid out there.
"""
from __future__ import annotations

import struct

TAIL = b"\x02\x00\x00\x00"


def _tail(ts: int) -> bytes:
    return TAIL + (ts & ((1 << 48) - 1)).to_bytes(6, "little") + b"\x00" * 8


def _head(contact) -> bytes:
    state, x, y = contact
    return bytes([0x09, 0x01, 0x05, state]) + struct.pack("<HH", x & 0xFFFF, y & 0xFFFF)


def build_single(state: int, x: int, y: int, ts: int) -> bytes:
    return _head((state, x, y)) + b"\x00" * 32 + _tail(ts)


def _xy(c) -> bytes:
    return struct.pack("<HH", c[1] & 0xFFFF, c[2] & 0xFFFF)


# second-contact block, placed at the start of the reserved area (offset 8)
_VARIANTS = {
    "block7": lambda c: b"\x01\x05" + bytes([c[0]]) + _xy(c),        # same 7-byte shape as contact 1 (minus id byte 0x09)
    "block5": lambda c: bytes([c[0]]) + _xy(c),                       # bare state+x+y
    "idblock": lambda c: b"\x01" + bytes([c[0]]) + _xy(c),            # contact id 1, then state+x+y
    "stride8": lambda c: b"\x00\x01\x05" + bytes([c[0]]) + _xy(c),    # 8-byte stride from offset 8
}
VARIANTS = tuple(_VARIANTS)


def build_two_contact(variant: str, first, second, ts: int) -> bytes:
    if variant not in _VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; choose from {VARIANTS}")
    block = _VARIANTS[variant](second)
    return _head(first) + block.ljust(32, b"\x00") + _tail(ts)
