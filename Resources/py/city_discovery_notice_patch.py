"""Centre city/current-nation labels; migrate legacy discovery popups away."""

from __future__ import annotations

import struct

import pefile

from pe_patch_section import (
    CITY_DISCOVERY_NOTICE_SLOT_OFFSET,
    CITY_DISCOVERY_NOTICE_SLOT_SIZE,
    PATCH_SECTION_CITY_DISCOVERY_NOTICE_SIZE,
    clear_slot,
    ensure_patch_section,
    find_patch_section,
)


MAGIC = b"CDSCTN1\0"
VERSION = 4
PIXEL_TEXT_OFFSET = 0x20
PIXEL_TEXT_VA = 0x406800
NATION_TABLE_VA = 0x4CA370
NATION_COUNT = 78
LEGACY_SLOT_SIZE = 0x200
SPEECH_WRAPPER_OFFSET = 0x20
MESSAGE_WRAPPER_OFFSET = 0x60
NOTICE_OFFSET = 0xA0
FORMAT_OFFSET = 0x100
MAP_WRAPPER_OFFSET = 0x140
MAP_LABELS_OFFSET = 0x180
MAP_HOOK_VA = 0x48AB38
MAP_ORIGINAL = bytes.fromhex("8B 4C 24 14 F6 81 A0 00 00 00 01")
MAP_RESUME_VA = MAP_HOOK_VA + len(MAP_ORIGINAL)
MAP_TEXT_VA = 0x426860
CITY_COUNT = 226
NOTICE_FORMAT = "%s%s 발견했다!".encode("cp949") + b"\0"
CITY_OBJECT_VA = 0x429970
CITY_MASTER_VA = 0x429980
OBJECT_PARTICLE_VA = 0x4281B0
COMMON_POPUP_VA = 0x49E3E0
SPEECH_POPUP_VA = 0x478280
# Legacy v1/v2 redirected these successful discovery calls. Keep their exact
# payload format solely to validate and restore previously patched EXEs.
HOOKS = (
    (0x48D9FE, SPEECH_POPUP_VA, SPEECH_WRAPPER_OFFSET, 4),
    (0x48DA11, COMMON_POPUP_VA, MESSAGE_WRAPPER_OFFSET, 3),
)
CONTEXTS = (
    (0x48D916, bytes.fromhex("8B E8 83 FD FF")),  # EBP is the discovered city ID
    (0x48D9FD, b"\x50"),  # original speech actor argument
    (0x48DA03, bytes.fromhex("83 C4 10 EB 11")),
    (0x48DA08, bytes.fromhex("68 50 08 57 00 6A 00 6A 00")),
    (0x48DA16, bytes.fromhex("83 C4 0C")),
    (MAP_RESUME_VA, bytes.fromhex("74 2B")),
)


def _call(source_va: int, target_va: int) -> bytes:
    return b"\xE8" + struct.pack("<i", target_va - source_va - 5)


def _popup_payload(slot_va: int, version: int) -> bytes:
    """Legacy popup code, used only for old-patch validation and migration."""
    payload = bytearray(LEGACY_SLOT_SIZE)
    payload[:len(MAGIC)] = MAGIC
    struct.pack_into("<I", payload, len(MAGIC), version)

    for _, original_target, wrapper_offset, argument_count in HOOKS:
        wrapper_va = slot_va + wrapper_offset
        # A wrapper CALL adds its own return address. Forward the original
        # arguments by pushing the same stack offset repeatedly: each PUSH
        # brings the preceding original argument into that position.
        code = bytearray(bytes((0xFF, 0x74, 0x24, argument_count * 4)) * argument_count)
        code += _call(wrapper_va + len(code), original_target)
        code += bytes((0x83, 0xC4, argument_count * 4))
        # Keep the original popup's result, caller-saved registers and flags
        # intact while displaying the extra notice. Original caller cleanup
        # remains in .text and runs once after this wrapper returns.
        code += bytes.fromhex("9C 60")  # pushfd; pushad
        code += _call(wrapper_va + len(code), slot_va + NOTICE_OFFSET)
        code += bytes.fromhex("61 9D C3")  # popad; popfd; ret
        if wrapper_offset + len(code) > NOTICE_OFFSET:
            raise AssertionError("도시 발견 알림 래퍼가 예약 공간을 초과했습니다.")
        payload[wrapper_offset:wrapper_offset + len(code)] = code

    code = bytearray(b"\x55")  # push ebp (city ID)

    def call(target: int) -> None:
        code.extend(_call(slot_va + NOTICE_OFFSET + len(code), target))

    call(CITY_OBJECT_VA)
    code += bytes.fromhex("83 C4 04 8B C8")  # caller cleanup; mov ecx, eax
    call(CITY_MASTER_VA)
    code += bytes.fromhex("8B 30 6A 02 56")  # mov esi,[eax] (current name); push 2; push esi
    call(OBJECT_PARTICLE_VA)  # game's Korean object-particle selector (을/를)
    code += bytes.fromhex("83 C4 08 50 56")  # cleanup; push particle; push name
    code += b"\x68" + struct.pack("<I", slot_va + FORMAT_OFFSET)
    code += bytes.fromhex("6A 00 6A 00")
    call(COMMON_POPUP_VA)
    code += bytes.fromhex("83 C4 14 C3")  # five cdecl arguments; ret
    if NOTICE_OFFSET + len(code) > FORMAT_OFFSET:
        raise AssertionError("도시 발견 알림 코드가 문자열 영역을 침범했습니다.")
    payload[NOTICE_OFFSET:NOTICE_OFFSET + len(code)] = code
    payload[FORMAT_OFFSET:FORMAT_OFFSET + len(NOTICE_FORMAT)] = NOTICE_FORMAT
    return bytes(payload)


def _jump(source_va: int, target_va: int) -> bytes:
    return b"\xE9" + struct.pack("<i", target_va - source_va - 5)


def _map_hook(slot_va: int) -> bytes:
    return _jump(MAP_HOOK_VA, slot_va + MAP_WRAPPER_OFFSET) + b"\x90" * (len(MAP_ORIGINAL) - 5)


def _legacy_map_labels_code(code_va: int) -> bytes:
    """Native thiscall(view): label visible cities and invalidate covered rows.

    Reuse 0x426860, including its viewport origin and font outline. Both world
    axes are in 16-pixel tiles; X wraps at 2500. The city's actual tile footprint
    determines visibility. Text is centred above it, clamped into the viewport.
    Locals: row, column, name width, then first/end dirty row (all tile units).
    """
    code = bytearray()
    labels: dict[str, int] = {}
    fixups: list[tuple[int, str]] = []

    def emit(value: str) -> None:
        code.extend(bytes.fromhex(value))

    def label(name: str) -> None:
        labels[name] = len(code)

    def branch(opcode: str, target: str) -> None:
        emit(opcode)
        fixups.append((len(code), target))
        code.extend(b"\0" * 4)

    def call(target: int) -> None:
        code.extend(_call(code_va + len(code), target))

    emit("55 53 56 57 83 EC 14 8B E9 31 DB")  # save nonvolatile; EBP=view; EBX=id
    emit("83 BD EC 00 00 00 00")
    branch("0F 8E", "done")
    emit("83 BD F0 00 00 00 00")
    branch("0F 8E", "done")
    label("city")
    emit("53")
    call(CITY_OBJECT_VA)
    emit("83 C4 04 F6 40 04 01")
    branch("0F 84", "next")  # undiscovered
    emit("F6 40 04 04")
    branch("0F 85", "next")  # city hidden (same test as original map)
    emit("8B C8")
    call(CITY_MASTER_VA)
    emit("8B F0 8B 3E 85 FF")
    branch("0F 84", "next")
    emit("80 3F 00")
    branch("0F 84", "next")
    emit("8B 56 04 2B 15 A8 63 5B 00 85 D2")  # relative tile X
    branch("0F 89", "positive_x")
    emit("81 C2 C4 09 00 00")
    label("positive_x")
    emit("81 FA C4 09 00 00")
    branch("0F 8C", "wrapped_x")
    emit("81 EA C4 09 00 00")
    label("wrapped_x")
    emit("B8 C4 09 00 00 2B 46 0C 3B D0")
    branch("0F 8C", "visible_x")
    emit("81 EA C4 09 00 00")  # city partly beyond left edge after wrap
    label("visible_x")
    emit("3B 95 EC 00 00 00")
    branch("0F 8D", "next")
    emit("8B C2 03 46 0C 85 C0")
    branch("0F 8E", "next")
    emit("89 54 24 04 8B 46 08 2B 05 AC 63 5B 00")
    emit("3B 85 F0 00 00 00")
    branch("0F 8D", "next")
    emit("8B C8 03 4E 0C 85 C9")
    branch("0F 8E", "next")
    emit("48 85 C0")  # label one tile above the city
    branch("0F 89", "label_row")
    emit("31 C0")
    label("label_row")
    emit("89 04 24 B9 FF FF FF FF 31 C0 FC F2 AE F7 D1 49 D1 E9 41")
    # CP949 characters use 8 pixels per byte; reserve an extra tile for outline.
    emit("89 4C 24 08 3B 8D EC 00 00 00")
    branch("0F 8F", "next")  # do not spill an overlong label into adjacent UI
    emit("8B 46 0C 29 C8 D1 F8 03 44 24 04 85 C0")
    branch("0F 89", "positive_col")
    emit("31 C0")
    label("positive_col")
    emit("8B 95 EC 00 00 00 29 CA 85 D2")
    branch("0F 89", "column_limit")
    emit("31 D2")
    label("column_limit")
    emit("3B C2")
    branch("0F 8E", "label_col")
    emit("8B C2")
    label("label_col")
    emit("89 44 24 04 FF 36 FF 74 24 04 50 8B CD")  # push name,row,column
    call(MAP_TEXT_VA)  # thiscall, callee removes 12 argument bytes
    # Text crosses tile boundaries by 1px. Invalidate neighbouring cache rows
    # too so incremental terrain updates erase its outline after scrolling.
    emit("8B 04 24 48 85 C0")
    branch("0F 89", "dirty_start")
    emit("31 C0")
    label("dirty_start")
    emit("89 44 24 0C 8B 04 24 83 C0 02 3B 85 F0 00 00 00")
    branch("0F 8E", "dirty_end")
    emit("8B 85 F0 00 00 00")
    label("dirty_end")
    emit("89 44 24 10 6A 04 6A 00 6A 00 8D 8D C4 00 00 00")
    call(0x4B6637)  # first byte of the view's u16 tile cache, callee ret 12
    emit("85 C0")
    branch("0F 84", "next")
    emit("8B 4C 24 0C 0F AF 8D EC 00 00 00 8D 3C 48")
    emit("8B 4C 24 10 2B 4C 24 0C 0F AF 8D EC 00 00 00")
    emit("B8 FF FF 00 00 FC F3 66 AB")  # REP STOSW, only valid cache rows
    label("next")
    emit("43 81 FB E2 00 00 00")
    branch("0F 8C", "city")
    label("done")
    emit("83 C4 14 5F 5E 5B 5D C3")
    for offset, target in fixups:
        struct.pack_into("<i", code, offset, labels[target] - offset - 4)
    return bytes(code)


class _Code:
    def __init__(self, va: int):
        self.va = va
        self.code = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str]] = []

    def emit(self, value: str) -> None:
        self.code.extend(bytes.fromhex(value))

    def label(self, name: str) -> None:
        self.labels[name] = len(self.code)

    def branch(self, opcode: str, name: str) -> None:
        self.emit(opcode)
        self.fixups.append((len(self.code), name))
        self.code.extend(b"\0" * 4)

    def call(self, target: int) -> None:
        self.code.extend(_call(self.va + len(self.code), target))

    def finish(self) -> bytes:
        for offset, name in self.fixups:
            struct.pack_into("<i", self.code, offset, self.labels[name] - offset - 4)
        return bytes(self.code)


def _pixel_text_code(code_va: int) -> bytes:
    """thiscall(view, centreX, pixelY, text), exact CP949 centring, ret 12.

    Preserve the viewport drawing origin like 0x426860, but call the same
    outlined text function with pixel coordinates instead of rounded tiles.
    Leave one pixel for the outline on each horizontal edge.
    """
    c = _Code(code_va)
    e, b, l = c.emit, c.branch, c.label
    e("56 57 53 55 83 EC 10 8B F1 8B 7C 24 2C")
    e("B9 FF FF FF FF 31 C0 FC F2 AE F7 D1 49 C1 E1 03 8B D9")
    e("8B 96 EC 00 00 00 C1 E2 04 83 EA 02 3B DA")
    b("0F 8F", "done")
    e("8B 6C 24 24 8B C3 D1 E8 2B E8 83 FD 01")
    b("0F 8D", "left_ok")
    e("BD 01 00 00 00")
    l("left_ok")
    e("42 2B D3 3B EA")
    b("0F 8E", "right_ok")
    e("8B EA")
    l("right_ok")
    e("A1 C8 B2 62 00 89 04 24 A1 CC B2 62 00 89 44 24 04")
    e("8B 46 28 03 46 54 89 44 24 08 8B 46 2C 03 46 58 89 44 24 0C")
    e("8D 44 24 08 50 B9 F0 B2 62 00")
    c.call(0x4B5B77)
    e("6A 0A FF 74 24 30 FF 74 24 30 55")  # color,text,Y,X
    c.call(PIXEL_TEXT_VA)
    e("83 C4 10 8D 04 24 50 B9 F0 B2 62 00")
    c.call(0x4B5B77)
    l("done")
    e("83 C4 10 5D 5B 5F 5E C2 0C 00")
    return c.finish()


def _map_labels_code(code_va: int, pixel_text_va: int) -> bytes:
    """City name plus [current nation], individually centred in pixels.

    Locals: city Y, centre X, current nation ID, first/end dirty row.
    +0x20 holds a bounded 128-byte nation label (including brackets and NUL).
    """
    c = _Code(code_va)
    e, b, l = c.emit, c.branch, c.label
    e("55 53 56 57 81 EC A0 00 00 00 8B E9 31 DB")
    e("83 BD EC 00 00 00 00")
    b("0F 8E", "done")
    e("83 BD F0 00 00 00 03")
    b("0F 8C", "done")
    l("city")
    e("53")
    c.call(CITY_OBJECT_VA)
    e("83 C4 04 F6 40 04 01")
    b("0F 84", "next")
    e("F6 40 04 04")
    b("0F 85", "next")
    e("8B 08 89 4C 24 08 8B C8")  # runtime city +0, not master +24
    c.call(CITY_MASTER_VA)
    e("8B F0 8B 3E 85 FF")
    b("0F 84", "next")
    e("80 3F 00")
    b("0F 84", "next")
    e("8B 56 04 2B 15 A8 63 5B 00 85 D2")
    b("0F 89", "positive_x")
    e("81 C2 C4 09 00 00")
    l("positive_x")
    e("81 FA C4 09 00 00")
    b("0F 8C", "wrapped_x")
    e("81 EA C4 09 00 00")
    l("wrapped_x")
    e("B8 C4 09 00 00 2B 46 0C 3B D0")
    b("0F 8C", "visible_x")
    e("81 EA C4 09 00 00")
    l("visible_x")
    e("3B 95 EC 00 00 00")
    b("0F 8D", "next")
    e("8B C2 03 46 0C 85 C0")
    b("0F 8E", "next")
    e("C1 E2 04 8B 46 0C C1 E0 03 03 C2 89 44 24 04")
    e("8B 46 08 2B 05 AC 63 5B 00 3B 85 F0 00 00 00")
    b("0F 8D", "next")
    e("8B C8 03 4E 0C 85 C9")
    b("0F 8E", "next")
    e("48 C1 E0 04 83 F8 11")
    b("0F 8D", "top_ok")
    e("B8 11 00 00 00")
    l("top_ok")
    e("8B 95 F0 00 00 00 C1 E2 04 83 EA 11 3B C2")
    b("0F 8E", "bottom_ok")
    e("8B C2")
    l("bottom_ok")
    e("89 04 24 FF 36 FF 74 24 04 FF 74 24 0C 8B CD")
    c.call(pixel_text_va)
    e("8B 44 24 08 83 F8 4E")
    b("0F 83", "dirty")  # negative or out-of-range nation: city only
    e("6B C0 18 8B 80 70 A3 4C 00 85 C0")
    b("0F 84", "dirty")
    e("80 38 00")
    b("0F 84", "dirty")
    e("8D 7C 24 20 C6 07 5B 47 B9 7D 00 00 00")
    l("copy")
    e("8A 10 84 D2")
    b("0F 84", "bracket")
    e("88 17 47 40 49")
    b("0F 85", "copy")
    b("E9", "dirty")  # overlong country name: no truncated CP949 label
    l("bracket")
    e("66 C7 07 5D 00 8D 44 24 20 50 8B 44 24 04 83 E8 10 50")
    e("FF 74 24 0C 8B CD")
    c.call(pixel_text_va)
    l("dirty")
    # Invalidate both label rows plus outline neighbours, clamped to cache.
    e("8B 04 24 C1 F8 04 83 E8 02 85 C0")
    b("0F 89", "dirty_start")
    e("31 C0")
    l("dirty_start")
    e("89 44 24 0C 8B 04 24 C1 F8 04 83 C0 02 3B 85 F0 00 00 00")
    b("0F 8E", "dirty_end")
    e("8B 85 F0 00 00 00")
    l("dirty_end")
    e("89 44 24 10 6A 04 6A 00 6A 00 8D 8D C4 00 00 00")
    c.call(0x4B6637)
    e("85 C0")
    b("0F 84", "next")
    e("8B 4C 24 0C 0F AF 8D EC 00 00 00 8D 3C 48")
    e("8B 4C 24 10 2B 4C 24 0C 0F AF 8D EC 00 00 00")
    e("B8 FF FF 00 00 FC F3 66 AB")
    l("next")
    e("43 81 FB E2 00 00 00")
    b("0F 8C", "city")
    l("done")
    e("81 C4 A0 00 00 00 5F 5E 5B 5D C3")
    return c.finish()


def _payload(slot_va: int, version: int = VERSION) -> bytes:
    payload = bytearray(CITY_DISCOVERY_NOTICE_SLOT_SIZE)
    if version == 2:
        payload[:LEGACY_SLOT_SIZE] = _popup_payload(slot_va, version)
    elif version in (3, VERSION):
        payload[:len(MAGIC)] = MAGIC
        struct.pack_into("<I", payload, len(MAGIC), version)
    else:
        raise ValueError("지원하지 않는 도시 지도 이름 패치 버전입니다.")
    wrapper_va = slot_va + MAP_WRAPPER_OFFSET
    # Original ESP + 0x14 contains the map widget; PUSHFD/PUSHAD adds 36 bytes.
    code = bytearray(bytes.fromhex("9C 60 8B 4C 24 38"))
    code += _call(wrapper_va + len(code), slot_va + MAP_LABELS_OFFSET)
    code += bytes.fromhex("61 9D") + MAP_ORIGINAL
    code += _jump(wrapper_va + len(code), MAP_RESUME_VA)
    payload[MAP_WRAPPER_OFFSET:MAP_WRAPPER_OFFSET + len(code)] = code
    if version in (2, 3):
        code = _legacy_map_labels_code(slot_va + MAP_LABELS_OFFSET)
    else:
        helper = _pixel_text_code(slot_va + PIXEL_TEXT_OFFSET)
        if PIXEL_TEXT_OFFSET + len(helper) > MAP_WRAPPER_OFFSET:
            raise AssertionError("도시 이름 가운데 정렬 코드가 예약 공간을 초과했습니다.")
        payload[PIXEL_TEXT_OFFSET:PIXEL_TEXT_OFFSET + len(helper)] = helper
        code = _map_labels_code(slot_va + MAP_LABELS_OFFSET, slot_va + PIXEL_TEXT_OFFSET)
    if MAP_LABELS_OFFSET + len(code) > len(payload):
        raise AssertionError("도시 지도 이름 표시 코드가 예약 공간을 초과했습니다.")
    payload[MAP_LABELS_OFFSET:MAP_LABELS_OFFSET + len(code)] = code
    return bytes(payload)


def _hook_offsets(data: bytes | bytearray) -> list[int]:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        for va, expected in CONTEXTS:
            offset = pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)
            if bytes(data[offset:offset + len(expected)]) != expected:
                raise ValueError(f"도시 발견 알림 코드 0x{va:X}을(를) 검증하지 못했습니다.")
        return [
            pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)
            for va, _, _, _ in HOOKS
        ]
    finally:
        pe.close()


def _map_offset(data: bytes | bytearray) -> int:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_offset_from_rva(MAP_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase)
    finally:
        pe.close()


def _patch_version(data: bytes | bytearray) -> int:
    offsets = _hook_offsets(data)
    current = [bytes(data[offset:offset + 5]) for offset in offsets]
    original = [_call(va, target) for va, target, _, _ in HOOKS]
    map_offset = _map_offset(data)
    map_code = bytes(data[map_offset:map_offset + len(MAP_ORIGINAL)])
    section = find_patch_section(data)
    if current == original and map_code == MAP_ORIGINAL:
        if section is not None and min(section.raw_size, section.virtual_size) >= CITY_DISCOVERY_NOTICE_SLOT_OFFSET + LEGACY_SLOT_SIZE:
            offset, _ = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, LEGACY_SLOT_SIZE)
            if bytes(data[offset:offset + len(MAGIC)]) == MAGIC:
                raise ValueError("도시 발견 알림 코드가 남아 있지만 호출부가 원본 상태입니다.")
        return 0
    if section is None or min(section.raw_size, section.virtual_size) < CITY_DISCOVERY_NOTICE_SLOT_OFFSET + LEGACY_SLOT_SIZE:
        raise ValueError("도시 발견 알림 호출에 대응하는 .patch 데이터를 찾지 못했습니다.")
    offset, va = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, LEGACY_SLOT_SIZE)
    expected = [_call(hook, va + wrapper) for hook, _, wrapper, _ in HOOKS]
    if (
        current == expected and map_code == MAP_ORIGINAL
        and bytes(data[offset:offset + LEGACY_SLOT_SIZE]) == _popup_payload(va, 1)
    ):
        return 1
    if min(section.raw_size, section.virtual_size) >= PATCH_SECTION_CITY_DISCOVERY_NOTICE_SIZE and map_code == _map_hook(va):
        payload = bytes(data[offset:offset + CITY_DISCOVERY_NOTICE_SLOT_SIZE])
        if current == expected and payload == _payload(va, 2):
            return 2
        if current == original and payload == _payload(va, 3):
            return 3
        if current == original and payload == _payload(va):
            return VERSION
    raise ValueError("도시 지도 이름 표시 패치 상태를 검증하지 못했습니다.")


def read_city_discovery_notice_patch_state(data: bytes | bytearray) -> bool:
    return _patch_version(data) != 0


def apply_city_discovery_notice_patch(data: bytearray, enabled: bool) -> bool:
    version = _patch_version(data)
    if (version == VERSION and enabled) or (version == 0 and not enabled):
        return False
    if version in (1, 2, 3) and enabled:
        # Restore both old popup CALLs and erase their code before installing
        # current map labels, without changing the user's checkbox state.
        apply_city_discovery_notice_patch(data, False)
    if enabled:
        section, _ = ensure_patch_section(data, PATCH_SECTION_CITY_DISCOVERY_NOTICE_SIZE)
        offset, va = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
        payload = _payload(va)
        present = bytes(data[offset:offset + CITY_DISCOVERY_NOTICE_SLOT_SIZE])
        if any(present) and present != payload:
            raise ValueError("도시 발견 알림용 .patch 슬롯이 다른 데이터로 사용 중입니다.")
        data[offset:offset + CITY_DISCOVERY_NOTICE_SLOT_SIZE] = payload
        targets = [target for _, target, _, _ in HOOKS]
    else:
        section = find_patch_section(data)
        if section is None:
            raise ValueError("도시 발견 알림의 복원 데이터를 찾지 못했습니다.")
        clear_slot(
            data, section, CITY_DISCOVERY_NOTICE_SLOT_OFFSET,
            LEGACY_SLOT_SIZE if version == 1 else CITY_DISCOVERY_NOTICE_SLOT_SIZE,
        )
        targets = [target for _, target, _, _ in HOOKS]
    for offset, (hook, _, _, _), target in zip(_hook_offsets(data), HOOKS, targets):
        data[offset:offset + 5] = _call(hook, target)
    map_offset = _map_offset(data)
    data[map_offset:map_offset + len(MAP_ORIGINAL)] = _map_hook(va) if enabled else MAP_ORIGINAL
    if read_city_discovery_notice_patch_state(data) != enabled:
        raise ValueError("도시 발견 알림 적용 결과를 검증하지 못했습니다.")
    return True
