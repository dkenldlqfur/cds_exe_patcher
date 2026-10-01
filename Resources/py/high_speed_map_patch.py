"""Keep native world-map tracking valid during oversized sailing steps."""

from __future__ import annotations

import struct

import pefile

from pe_patch_section import (
    HIGH_SPEED_MAP_FIX_SLOT_OFFSET,
    HIGH_SPEED_MAP_FIX_SLOT_SIZE,
    PATCH_SECTION_HIGH_SPEED_MAP_FIX_SIZE,
    clear_slot,
    ensure_patch_section,
    find_patch_section,
)


MAGIC = b"CDSHSM1\0"
VERSION = 1
WRAPPER_OFFSET = 0x20
HOOK_VA = 0x48D356
MOVE_VA = 0x47D0C0
MAX_STEP = 32  # below the native camera's left/top tracking margin
CONTEXTS = (
    (0x48D34F, bytes.fromhex("57 B9 A0 60 5B 00 56")),
    (0x48D35B, bytes.fromhex("8B 44 24 20 C7 80 00 01 00 00 00 00 00 00")),
    (MOVE_VA, bytes.fromhex("83 EC 1C 53 56 57 8B F1")),
    (0x47D031, bytes.fromhex("79 06 05 28 9C 00 00 C3")),
    (0x47D172, bytes.fromhex("83 F8 04")),
    (0x47D444, bytes.fromhex("C2 08 00")),
)


def _call(source: int, target: int) -> bytes:
    return b"\xE8" + struct.pack("<i", target - source - 5)


ORIGINAL = _call(HOOK_VA, MOVE_VA)


def _payload(slot_va: int) -> bytes:
    """thiscall(player, dx, dy), preserving nonvolatile registers and ret 8.

    N=ceil(max(abs(dx),abs(dy))/32). Each substep divides the remaining
    displacement by the remaining count: signed truncation and the final step
    preserve both totals exactly, including negative and diagonal movement.
    Only movement is repeated; no extra frame, day, discovery or encounter tick.
    """
    code = bytearray()
    labels: dict[str, int] = {}
    fixups: list[tuple[int, str]] = []

    def emit(value: str) -> None:
        code.extend(bytes.fromhex(value))

    def label(name: str) -> None:
        labels[name] = len(code)

    def branch(opcode: str, name: str) -> None:
        emit(opcode)
        fixups.append((len(code), name))
        code.extend(b"\0" * 4)

    def move() -> None:
        emit("57 56 8B CB")  # push dy; push dx; this=player
        code.extend(_call(slot_va + WRAPPER_OFFSET + len(code), MOVE_VA))

    emit("53 56 57 55 83 EC 0C 8B D9")
    emit("8B 74 24 20 8B 7C 24 24")  # original dx/dy
    emit("8B C6 99 33 C2 2B C2")  # unsigned abs(dx), even INT_MIN
    emit("8B CF 8B D1 C1 FA 1F 33 CA 2B CA")  # abs(dy)
    emit("3B C1")
    branch("0F 83", "maximum")
    emit("8B C1")
    label("maximum")
    emit("83 F8 20")
    branch("0F 86", "direct")  # ordinary/zero step stays native
    emit("83 C0 1F C1 E8 05 89 44 24 08")
    emit("89 34 24 89 7C 24 04")  # remaining dx/dy
    label("step")
    emit("8B 04 24 99 F7 7C 24 08 8B F0 29 34 24")
    emit("8B 44 24 04 99 F7 7C 24 08 8B F8 29 7C 24 04")
    move()
    emit("FF 4C 24 08")
    branch("0F 85", "step")
    branch("E9", "done")
    label("direct")
    move()
    label("done")
    emit("83 C4 0C 5D 5F 5E 5B C2 08 00")
    for offset, name in fixups:
        struct.pack_into("<i", code, offset, labels[name] - offset - 4)
    if WRAPPER_OFFSET + len(code) > HIGH_SPEED_MAP_FIX_SLOT_SIZE:
        raise AssertionError("고속 항해 지도 갱신 코드가 예약 공간을 초과했습니다.")
    payload = bytearray(HIGH_SPEED_MAP_FIX_SLOT_SIZE)
    payload[:len(MAGIC)] = MAGIC
    struct.pack_into("<I", payload, len(MAGIC), VERSION)
    payload[WRAPPER_OFFSET:WRAPPER_OFFSET + len(code)] = code
    return bytes(payload)


def _hook_offset(data: bytes | bytearray) -> int:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        for va, expected in CONTEXTS:
            offset = pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)
            if bytes(data[offset:offset + len(expected)]) != expected:
                raise ValueError(f"고속 항해 지도 코드 0x{va:X}을(를) 검증하지 못했습니다.")
        return pe.get_offset_from_rva(HOOK_VA - pe.OPTIONAL_HEADER.ImageBase)
    finally:
        pe.close()


def read_high_speed_map_fix_state(data: bytes | bytearray) -> bool:
    offset = _hook_offset(data)
    current = bytes(data[offset:offset + 5])
    section = find_patch_section(data)
    slot = None
    if section is not None and min(section.raw_size, section.virtual_size) >= PATCH_SECTION_HIGH_SPEED_MAP_FIX_SIZE:
        slot = section.slot(HIGH_SPEED_MAP_FIX_SLOT_OFFSET, HIGH_SPEED_MAP_FIX_SLOT_SIZE)
    if current == ORIGINAL:
        if slot is not None and any(data[slot[0]:slot[0] + HIGH_SPEED_MAP_FIX_SLOT_SIZE]):
            raise ValueError("고속 항해 지도 코드가 남아 있지만 호출부가 원본 상태입니다.")
        return False
    if slot is None:
        raise ValueError("고속 항해 지도 호출에 대응하는 .patch 데이터를 찾지 못했습니다.")
    start, va = slot
    if current != _call(HOOK_VA, va + WRAPPER_OFFSET) or bytes(data[start:start + HIGH_SPEED_MAP_FIX_SLOT_SIZE]) != _payload(va):
        raise ValueError("고속 항해 지도 갱신 패치 상태를 검증하지 못했습니다.")
    return True


def apply_high_speed_map_fix(data: bytearray, enabled: bool) -> bool:
    current = read_high_speed_map_fix_state(data)
    if current == enabled:
        return False
    if enabled:
        section, _ = ensure_patch_section(data, PATCH_SECTION_HIGH_SPEED_MAP_FIX_SIZE)
        start, va = section.slot(HIGH_SPEED_MAP_FIX_SLOT_OFFSET, HIGH_SPEED_MAP_FIX_SLOT_SIZE)
        if any(data[start:start + HIGH_SPEED_MAP_FIX_SLOT_SIZE]):
            raise ValueError("고속 항해 지도용 .patch 슬롯이 다른 데이터로 사용 중입니다.")
        data[start:start + HIGH_SPEED_MAP_FIX_SLOT_SIZE] = _payload(va)
        hook = _call(HOOK_VA, va + WRAPPER_OFFSET)
    else:
        section = find_patch_section(data)
        assert section is not None
        clear_slot(data, section, HIGH_SPEED_MAP_FIX_SLOT_OFFSET, HIGH_SPEED_MAP_FIX_SLOT_SIZE)
        hook = ORIGINAL
    offset = _hook_offset(data)
    data[offset:offset + 5] = hook
    if read_high_speed_map_fix_state(data) != enabled:
        raise ValueError("고속 항해 지도 갱신 적용 결과를 검증하지 못했습니다.")
    return True
