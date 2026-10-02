"""Align the native adjacent-tile chooser with fractional world-map scrolling."""

import struct


TEXT_OFFSET = 0x5800
RECT_OFFSET = 0x5900


def _call(source, target):
    return b"\xE8" + struct.pack("<i", target - source - 5)


def build_modal(slot_va, state):
    # The chooser's number labels set their own origin from the view. Change
    # that origin only for this one call, restoring it even if text is clipped.
    text = bytearray.fromhex("55 8B EC 56 53 57 8B F1 8B 5E 54 8B 7E 58")
    text += b"\xA1" + struct.pack("<I", state["rx"])
    text += bytes.fromhex("29 46 54")
    text += b"\xA1" + struct.pack("<I", state["ry"])
    text += bytes.fromhex("29 46 58 FF 75 10 FF 75 0C FF 75 08 8B CE")
    text += _call(slot_va + TEXT_OFFSET + len(text), 0x426860)
    text += bytes.fromhex("89 5E 54 89 7E 58 5F 5B 5E 5D C2 0C 00")

    # XOR selection outlines are also used to erase the previous selection.
    # Use an adjusted stack copy on every call, never modify the caller's RECT.
    rect = bytearray.fromhex("55 8B EC 83 EC 10 8B 55 08")
    for source, target, axis in ((0, 0xF0, "rx"), (4, 0xF4, "ry"),
                                  (8, 0xF8, "rx"), (12, 0xFC, "ry")):
        rect += bytes((0x8B, 0x42, source))  # eax=[edx+source]
        rect += b"\x2B\x05" + struct.pack("<I", state[axis])
        rect += bytes((0x89, 0x45, target))  # local rectangle
    rect += bytes.fromhex("FF 75 0C 8D 45 F0 50")
    rect += _call(slot_va + RECT_OFFSET + len(rect), 0x4B5C18)
    rect += bytes.fromhex("8B E5 5D C2 08 00")
    hooks = [(0x48B99A, _call(0x48B99A, 0x426860),
              _call(0x48B99A, slot_va + TEXT_OFFSET))]
    for address in (0x48BB89, 0x48BBCC, 0x48BC1A):
        hooks.append((address, _call(address, 0x4B5C18),
                      _call(address, slot_va + RECT_OFFSET)))
    return {TEXT_OFFSET: bytes(text), RECT_OFFSET: bytes(rect)}, hooks
