"""Use the flagship's sprite group when drawing the ship left on the coast."""
from __future__ import annotations

import struct
import pefile

from pe_patch_section import (
    LANDING_SHIP_IMAGE_SLOT_OFFSET, LANDING_SHIP_IMAGE_SLOT_SIZE,
    PATCH_SECTION_LANDING_SHIP_IMAGE_SIZE, clear_slot,
    ensure_patch_section, find_patch_section,
)

HOOK_VA = 0x48A9C9
RESUME_VA = 0x48A9D6
ORIGINAL = bytes.fromhex('6A 02 8D 04 F6 C1 E0 0B 05 C8 68 5D 00')
PIXELS_VA = 0x5D68C8
FRAME_BYTES = 48 * 48
PARKED_DIRECTION = 2
VERSION = 3
WRAPPER_OFFSET = 0x20
CAPTURE_OFFSET = 0x80
RESET_NEW_OFFSET = 0xA0
RESET_LOAD_OFFSET = 0xC0
STATE_OFFSET = 0xF0
HEADING_VA = 0x5B63C8
# Both native sea->land paths copy the parked ship's X coordinate here.
# CALL is safe: the replaced MOV has no stack operands and the helper RETs.
EXTRA_HOOKS = (
    (0x48E76E, bytes.fromhex('A3 B8 63 5B 00'), CAPTURE_OFFSET, True),
    (0x49364C, bytes.fromhex('A3 B8 63 5B 00'), CAPTURE_OFFSET, True),
    (0x47C35D, bytes.fromhex('89 86 28 03 00 00 89 86 2C 03 00 00'), RESET_NEW_OFFSET, False),
    (0x47C654, bytes.fromhex('89 8E 2C 03 00 00'), RESET_LOAD_OFFSET, False),
)
CONTEXTS = (
    # ESI = sprite_group[get_ship_type(flagship)].
    (0x48A99B, bytes.fromhex('8B C8 E8 3E 1D FC FF 8B 34 85 D8 95 56 00')),
    (0x48A9C2, bytes.fromhex('50 8B 0D E4 9F 56 00')),
    (RESUME_VA, bytes.fromhex('50 51 8B 4C 24 30 E8 4F 02 00 00')),
)


def _jump(source: int, target: int) -> bytes:
    return b'\xE9' + struct.pack('<i', target - source - 5)


def _hook(slot_va: int) -> bytes:
    return _jump(HOOK_VA, slot_va + WRAPPER_OFFSET).ljust(len(ORIGINAL), b'\x90')


def _legacy_payload(slot_va: int) -> bytes:
    """Frozen v1 for exact recognition, migration and removal only."""
    # ESI and ECX must survive. Leave exactly one frame-index argument on
    # the stack and EAX pointing to that same frame for the native continuation.
    code = bytearray(bytes.fromhex('8D 04 F5 02 00 00 00 50'))  # eax=esi*8+2; push eax
    code += b'\x69\xC0' + struct.pack('<I', FRAME_BYTES)  # imul eax,eax,2304
    code += b'\x05' + struct.pack('<I', PIXELS_VA)
    code += _jump(slot_va + WRAPPER_OFFSET + len(code), RESUME_VA)
    header = b'CDSLSI1\0' + struct.pack('<I', 1)
    return (header.ljust(WRAPPER_OFFSET, b'\0') + code).ljust(LANDING_SHIP_IMAGE_SLOT_SIZE, b'\0')


def _v2_payload(slot_va: int) -> bytes:
    # Correct only the DirectDraw frame's missing ship-group offset. Keep the
    # original software pixels calculation byte-for-byte: it uses direction 0,
    # not DirectDraw's direction 2. Neither path samples walking direction.
    code = bytearray(bytes.fromhex('8D 04 F5 02 00 00 00 50'))  # eax=esi*8+2; push eax
    code += ORIGINAL[2:]  # eax=(esi*9)<<11; eax+=PIXELS_VA (group*8 frames)
    code += _jump(slot_va + WRAPPER_OFFSET + len(code), RESUME_VA)
    header = b'CDSLSI1\0' + struct.pack('<I', 2)
    return (header.ljust(WRAPPER_OFFSET, b'\0') + code).ljust(LANDING_SHIP_IMAGE_SLOT_SIZE, b'\0')


def _extra_hook(va: int, original: bytes, destination: int, call: bool) -> bytes:
    return ((b'\xE8' if call else b'\xE9') + struct.pack('<i', destination-va-5)).ljust(len(original), b'\x90')


def _payload(slot_va: int) -> bytes:
    payload = bytearray(LANDING_SHIP_IMAGE_SLOT_SIZE)
    payload[:12] = b'CDSLSI1\0' + struct.pack('<I', VERSION)
    state = struct.pack('<I', slot_va + STATE_OFFSET)
    # If the save was loaded while already on land, its pre-landing heading
    # does not exist in the save. Use v2's native-direction fallback, never
    # a previous game's capture or the walking heading.
    code = bytearray(b'\xA1' + state + b'\x83\xF8\x08')
    branch = len(code)
    code += b'\x73\0'  # jae fallback
    code += bytes.fromhex('8D 04 F0 50')  # eax=esi*8+saved direction; push eax
    code += b'\x69\xC0' + struct.pack('<I', FRAME_BYTES)
    code += b'\x05' + struct.pack('<I', PIXELS_VA)
    code += _jump(slot_va + WRAPPER_OFFSET + len(code), RESUME_VA)
    code[branch+1] = len(code) - branch - 2
    code += bytes.fromhex('8D 04 F5 02 00 00 00 50') + ORIGINAL[2:]
    code += _jump(slot_va + WRAPPER_OFFSET + len(code), RESUME_VA)
    assert WRAPPER_OFFSET + len(code) <= CAPTURE_OFFSET
    payload[WRAPPER_OFFSET:WRAPPER_OFFSET+len(code)] = code
    # Replay original MOV, then snapshot the same heading/2 as the sailing
    # renderer. Preserve EAX and flags; ECX is still the parked Y coordinate.
    capture = (EXTRA_HOOKS[0][1] + b'\x9C\x50\xA1' + struct.pack('<I', HEADING_VA)
               + b'\xD1\xE8\xA3' + state + b'\x58\x9D\xC3')
    assert CAPTURE_OFFSET + len(capture) <= RESET_NEW_OFFSET
    payload[CAPTURE_OFFSET:CAPTURE_OFFSET+len(capture)] = capture
    for va, original, offset, call in EXTRA_HOOKS[2:]:
        reset = bytearray(b'\xC7\x05' + state + b'\xFF'*4 + original)
        reset += _jump(slot_va + offset + len(reset), va + len(original))
        assert offset + len(reset) < STATE_OFFSET
        payload[offset:offset+len(reset)] = reset
    payload[STATE_OFFSET:STATE_OFFSET+4] = b'\xFF'*4
    return bytes(payload)


def _check_extra_hooks(data: bytes | bytearray, slot_va: int | None = None) -> None:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        for va, original, offset, call in EXTRA_HOOKS:
            expected = original if slot_va is None else _extra_hook(va, original, slot_va+offset, call)
            pos = pe.get_offset_from_rva(va - 0x400000)
            if data[pos:pos+len(expected)] != expected:
                raise ValueError(f'상륙 선박 방향 코드 0x{va:X}을(를) 검증하지 못했습니다.')
    finally:
        pe.close()


def _offset(data: bytes | bytearray) -> int:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError('지원하는 32비트 CDS III 실행 파일이 아닙니다.')
        for va, expected in CONTEXTS:
            offset = pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)
            if bytes(data[offset:offset + len(expected)]) != expected:
                raise ValueError(f'상륙 선박 이미지 코드 0x{va:X}을(를) 검증하지 못했습니다.')
        return pe.get_offset_from_rva(HOOK_VA - pe.OPTIONAL_HEADER.ImageBase)
    finally:
        pe.close()


def _patch_version(data: bytes | bytearray) -> int:
    offset = _offset(data)
    current = bytes(data[offset:offset + len(ORIGINAL)])
    section = find_patch_section(data)
    slot = None
    if section and min(section.raw_size, section.virtual_size) >= PATCH_SECTION_LANDING_SHIP_IMAGE_SIZE:
        slot = section.slot(LANDING_SHIP_IMAGE_SLOT_OFFSET, LANDING_SHIP_IMAGE_SLOT_SIZE)
    if current == ORIGINAL:
        _check_extra_hooks(data)
        if slot and any(data[slot[0]:slot[0] + LANDING_SHIP_IMAGE_SLOT_SIZE]):
            raise ValueError('상륙 선박 이미지 패치가 남아 있지만 호출부는 원본 상태입니다.')
        return 0
    if slot:
        offset, va = slot
        if current == _hook(va):
            payload = bytes(data[offset:offset + LANDING_SHIP_IMAGE_SLOT_SIZE])
            if payload == _payload(va):
                _check_extra_hooks(data, va)
                if not struct.unpack_from('<I', data, section.header_offset+36)[0] & 0x80000000:
                    raise ValueError('상륙 방향 저장 영역에 쓰기 권한이 없습니다.')
                return VERSION
            if payload == _v2_payload(va):
                _check_extra_hooks(data)
                return 2
            if payload == _legacy_payload(va):
                _check_extra_hooks(data)
                return 1
    raise ValueError('상륙 선박 이미지 패치 상태를 검증하지 못했습니다.')


def read_landing_ship_image_fix_state(data: bytes | bytearray) -> bool:
    return bool(_patch_version(data))


def apply_landing_ship_image_fix(data: bytearray, enabled: bool) -> bool:
    version = _patch_version(data)
    if (not enabled and version == 0) or (enabled and version == VERSION):
        return False
    # Work on a copy so unknown occupied slots or final validation cannot leave
    # the caller with a partially expanded executable.
    updated = bytearray(data)
    if enabled:
        section, _ = ensure_patch_section(updated, PATCH_SECTION_LANDING_SHIP_IMAGE_SIZE)
        offset, va = section.slot(LANDING_SHIP_IMAGE_SLOT_OFFSET, LANDING_SHIP_IMAGE_SLOT_SIZE)
        if version == 0 and any(updated[offset:offset + LANDING_SHIP_IMAGE_SLOT_SIZE]):
            raise ValueError('상륙 선박 이미지 예약 공간이 다른 데이터로 사용 중입니다.')
        updated[offset:offset + LANDING_SHIP_IMAGE_SLOT_SIZE] = _payload(va)
        characteristics = struct.unpack_from('<I', updated, section.header_offset+36)[0]
        struct.pack_into('<I', updated, section.header_offset+36, characteristics | 0x80000000)
        replacement = _hook(va)
    else:
        section = find_patch_section(updated)
        assert section is not None
        clear_slot(updated, section, LANDING_SHIP_IMAGE_SLOT_OFFSET, LANDING_SHIP_IMAGE_SLOT_SIZE)
        replacement = ORIGINAL
    offset = _offset(updated)
    updated[offset:offset + len(ORIGINAL)] = replacement
    pe = pefile.PE(data=bytes(updated), fast_load=True)
    try:
        for hook_va, original, wrapper_offset, call in EXTRA_HOOKS:
            pos = pe.get_offset_from_rva(hook_va-0x400000)
            patched = _extra_hook(hook_va, original, va+wrapper_offset, call) if enabled else original
            updated[pos:pos+len(original)] = patched
    finally:
        pe.close()
    if read_landing_ship_image_fix_state(updated) != enabled:
        raise ValueError('상륙 선박 이미지 적용 결과를 검증하지 못했습니다.')
    data[:] = updated
    return True
