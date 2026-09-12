#!/usr/bin/env python3
"""Patch CDS_95.EXE to show navigation latitude/longitude to 2 decimals.

The original navigation-status routine at RVA 0x7DD0A converts the internal
0.009-degree world coordinates but keeps only the integer quotient.  This
patch replaces that branch with code in a dedicated executable `.patch`
section that rounds to centidegrees and renders ``북위 12.34  동경 56.78``.

The executable is backed up before modification.  It is deliberately limited
to the verified CDS_95.EXE layout and refuses unknown code or occupied slots.
"""

from __future__ import annotations

import argparse
import shutil
import struct
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

from pe_patch_section import (
    COORDINATE_SLOT_OFFSET,
    COORDINATE_SLOT_SIZE,
    clear_slot,
    ensure_patch_section,
    find_patch_section,
    write_slot,
)


HOOK_RVA = 0x7DD0A
CALL_FORMAT = 0x4B8521
FORMAT_BUFFER = 0x5B64D8
ORIGINAL_HOOK = bytes.fromhex("8B 0D B0 63 5B 00")


@dataclass(frozen=True)
class CoordinateLayout:
    hook_offset: int
    hook_va: int
    cleanup_va: int
    coordinate_a: int       # longitude (centre 20000)
    coordinate_b: int       # latitude (centre 10000)
    latitude_normal: int
    latitude_alternate: int
    longitude_normal: int
    longitude_alternate: int
    format_buffer: int
    format_call: int

# x86 code for the original coordinate branch, expanded to calculate rounded
# centidegrees, then degrees/remainder and call the game's formatter.  The
# DEADBEEF immediate and the two relative branches are fixed up per EXE.
CODE_TEMPLATE = bytes.fromhex(
    "55 8B 0D B0 63 5B 00 81 F9 20 4E 00 00 7C 14 "
    "8D 04 4D C0 63 FF FF 69 C0 84 03 00 00 05 E8 03 00 00 EB 12 "
    "B8 20 4E 00 00 29 C8 69 C0 08 07 00 00 05 E8 03 00 00 "
    "99 B9 D0 07 00 00 F7 F9 89 C7 "
    "A1 B4 63 5B 00 3D 10 27 00 00 7C 12 "
    "69 C0 84 03 00 00 2D 40 54 89 00 05 F4 01 00 00 EB 15 "
    "B9 10 27 00 00 29 C1 69 C9 84 03 00 00 81 C1 F4 01 00 00 89 C8 "
    "99 B9 E8 03 00 00 F7 F9 89 C6 "
    "89 F8 31 D2 B9 64 00 00 00 F7 F1 89 C7 89 D5 "
    "89 F0 31 D2 F7 F1 89 C6 89 D3 "
    # The game stores longitude at 5B63B0 (centre 20000) and latitude at
    # 5B63B4 (centre 10000).  Arguments are pushed right-to-left, therefore
    # longitude is pushed first and latitude last, yielding 위도 then 경도.
    "B8 FC BE 56 00 81 3D B0 63 5B 00 20 4E 00 00 7D 05 B8 F8 BE 56 00 "
    "55 57 50 "
    "B8 F0 BE 56 00 81 3D B4 63 5B 00 10 27 00 00 7D 05 B8 F4 BE 56 00 "
    "53 56 50 68 EF BE AD DE 68 D8 64 5B 00 E8 19 64 FF FF 83 C4 20 5D E9 D0 BE FB FF"
)
FORMAT_MARKER = bytes.fromhex("68 EF BE AD DE")
CALL_MARKER = bytes.fromhex("68 D8 64 5B 00 E8")
# The coordinate buffer was sized for the original maximum 20-byte string.
# Keep that exact maximum while adding decimals: ``북위90.00 동경180.00``.
# (The previous spaced format was six bytes longer and overwrote the buffer.)
DECIMAL_FORMAT = "%s위%2d.%02d %s경%3d.%02d".encode("cp949") + b"\0"
COMPASS_FORMAT = b"%s %2d.%02d %s %3d.%02d\0"
KOREAN_DIRECTION_3_FORMAT = "%s %2d.%03d %s %3d.%03d".encode("cp949") + b"\0"
COMPASS_3_FORMAT = b"%s %2d.%03d %s %3d.%03d\0"
LEGACY_DECIMAL_FORMAT = "%s위 %3d.%02d  %s경 %3d.%02d  ".encode("cp949") + b"\0"
ORIGINAL_COORDINATE_FORMAT = "%s위 %3d  %s경 %3d  ".encode("cp949") + b"\0"
MAX_COORDINATE_PATCH_SIZE = len(CODE_TEMPLATE) + max(
    len(DECIMAL_FORMAT), len(COMPASS_FORMAT), len(KOREAN_DIRECTION_3_FORMAT),
    len(COMPASS_3_FORMAT), len(LEGACY_DECIMAL_FORMAT),
) + len("북\0남\0동\0서\0".encode("cp949"))
ORIGINAL_BRANCH_TEMPLATE = bytes.fromhex(
    "8b0db0635b008b35b4635b0081f9204e00007c0c8d144dc063ffff8d04d2eb10"
    "b8204e00002b05b0635b0003c08d04c099bfd0070000f7ff813db4635b0010270000"
    "8bf87c0ea1b4635b008d84c070a0feffeb0eb8102700002b05b4635b008d04c0"
    "bbe80300005799f7fb81f9204e0000b9fcbe56007d05b9f8be56005181fe10270000"
    "50b8f0be56007d05b8f4be56005068d8be560068d8645b00e876a7030083c418e92e020000"
)


def _u16(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _file_offset_to_va(data: bytes | bytearray, file_offset: int) -> int:
    pe_offset = _u32(data, 0x3C)
    optional_size = _u16(data, pe_offset + 20)
    image_base = _u32(data, pe_offset + 24 + 28)
    section_count = _u16(data, pe_offset + 6)
    first_section = pe_offset + 24 + optional_size
    for index in range(section_count):
        header = first_section + index * 40
        raw = _u32(data, header + 20)
        raw_size = _u32(data, header + 16)
        if raw <= file_offset < raw + raw_size:
            return image_base + _u32(data, header + 12) + file_offset - raw
    raise ValueError("파일 오프셋의 가상 주소를 찾지 못했습니다.")


def _va_to_file_offset(data: bytes | bytearray, virtual_address: int) -> int:
    pe_offset = _u32(data, 0x3C)
    optional_size = _u16(data, pe_offset + 20)
    optional = pe_offset + 24
    image_base = _u32(data, optional + 28)
    section_count = _u16(data, pe_offset + 6)
    first_section = optional + optional_size
    rva = virtual_address - image_base
    for index in range(section_count):
        header = first_section + index * 40
        section_rva = _u32(data, header + 12)
        raw_size = _u32(data, header + 16)
        if section_rva <= rva < section_rva + raw_size:
            return _u32(data, header + 20) + rva - section_rva
    raise ValueError("가상 주소의 파일 위치를 찾지 못했습니다.")


def _text_section(data: bytes | bytearray) -> tuple[int, int, int, int, int]:
    if data[:2] != b"MZ":
        raise ValueError("PE 실행 파일이 아닙니다.")
    pe_offset = _u32(data, 0x3C)
    if data[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise ValueError("PE 헤더를 찾지 못했습니다.")
    section_count = _u16(data, pe_offset + 6)
    optional_size = _u16(data, pe_offset + 20)
    optional = pe_offset + 24
    if _u16(data, optional) != 0x10B:
        raise ValueError("검증한 32비트 PE 형식이 아닙니다.")
    image_base = _u32(data, optional + 28)
    first_section = optional + optional_size
    for index in range(section_count):
        header = first_section + index * 40
        if data[header : header + 8].rstrip(b"\0") == b".text":
            return (
                image_base,
                header,
                _u32(data, header + 8),
                _u32(data, header + 12),
                _u32(data, header + 16),
            )
    raise ValueError(".text 섹션을 찾지 못했습니다.")


def _replace_u32(code: bytearray, old: int, new: int) -> None:
    old_bytes = struct.pack("<I", old)
    new_bytes = struct.pack("<I", new)
    start = 0
    while (offset := code.find(old_bytes, start)) != -1:
        code[offset : offset + 4] = new_bytes
        start = offset + 4


def _build_code(
    cave_va: int,
    layout: CoordinateLayout,
    decimal_format: bytes = DECIMAL_FORMAT,
    compass: bool = False,
    precision: int = 2,
    korean_directions: bool = False,
) -> bytes:
    code = bytearray(CODE_TEMPLATE)
    _replace_u32(code, 0x5B63B0, layout.coordinate_a)
    _replace_u32(code, 0x5B63B4, layout.coordinate_b)
    if precision == 3:
        # Convert the original world units to rounded milli-degrees, then
        # split each value by 1000 instead of 100.  The southern-latitude
        # branch subtracts ``10000 * scale`` to turn the value into an
        # absolute distance from the equator, so its origin must be scaled
        # together with the multiplier as well.
        _replace_u32(code, 0x384, 0x2328)   # 900 -> 9000
        _replace_u32(code, 0x708, 0x4650)   # 1800 -> 18000
        _replace_u32(code, 0x895440, 0x55D4A80)  # 9,000,000 -> 90,000,000
        _replace_u32(code, 0x64, 0x3E8)     # 100 -> 1000
    suffix = b""
    if compass or korean_directions:
        # ASCII N/S/E/W strings are appended after the format text.
        string_va = cave_va + len(code) + len(decimal_format)
        if compass:
            suffix = b"N\0S\0E\0W\0"
            north, south, east, west = string_va, string_va + 2, string_va + 4, string_va + 6
        else:
            suffix = "북\0남\0동\0서\0".encode("cp949")
            north, south, east, west = string_va, string_va + 3, string_va + 6, string_va + 9
        _replace_u32(code, 0x56BEFC, east)
        _replace_u32(code, 0x56BEF8, west)
        _replace_u32(code, 0x56BEF0, south)
        _replace_u32(code, 0x56BEF4, north)
    else:
        _replace_u32(code, 0x56BEFC, layout.longitude_normal)
        _replace_u32(code, 0x56BEF8, layout.longitude_alternate)
        _replace_u32(code, 0x56BEF0, layout.latitude_normal)
        _replace_u32(code, 0x56BEF4, layout.latitude_alternate)
    _replace_u32(code, FORMAT_BUFFER, layout.format_buffer)
    format_offset = code.index(FORMAT_MARKER) + 1
    struct.pack_into("<I", code, format_offset, cave_va + len(code))

    call_offset = code.index(CALL_MARKER) + len(CALL_MARKER) - 1
    struct.pack_into("<i", code, call_offset + 1, layout.format_call - (cave_va + call_offset + 5))

    jump_offset = len(code) - 5
    if code[jump_offset] != 0xE9:
        raise AssertionError("코드 템플릿의 복귀 점프를 찾지 못했습니다.")
    struct.pack_into("<i", code, jump_offset + 1, layout.cleanup_va - (cave_va + jump_offset + 5))
    return bytes(code) + decimal_format + suffix


def _find_coordinate_layout(data: bytes | bytearray, image_base: int, section_header: int, section_rva: int) -> CoordinateLayout:
    """Find the unpatched coordinate branch by its instruction structure.

    Absolute addresses are intentionally wildcards: translation/re-release
    builds can move data and code while retaining this coordinate algorithm.
    """
    raw = _u32(data, section_header + 20)
    text_size = _u32(data, section_header + 16)
    text = data[raw : raw + text_size]
    # Fixed operations around the two scale conversions.  The wildcard fields
    # are absolute addresses or the relative formatter call.
    signature = bytes.fromhex("81 F9 20 4E 00 00 7C 0C 8D 14 4D C0 63 FF FF 8D 04 D2 EB 10 B8 20 4E 00 00")
    matches: list[int] = []
    start = 0
    while (index := text.find(signature, start)) != -1:
        hook = index - 12
        if hook >= 0 and text[hook : hook + 2] == b"\x8B\x0D" and text[hook + 6 : hook + 8] == b"\x8B\x35":
            matches.append(hook)
        start = index + 1
    if len(matches) != 1:
        raise ValueError(f"좌표 표시 루틴을 하나로 식별하지 못했습니다. (후보 {len(matches)}개)")
    offset = matches[0]
    branch = text[offset : offset + 0xA9]
    if len(branch) < 0xA9 or branch[0xA1:0xA4] != b"\x83\xC4\x18" or branch[0xA4] != 0xE9:
        raise ValueError("찾은 좌표 루틴의 끝 구조가 예상과 다릅니다.")
    a = _u32(branch, 2)
    b = _u32(branch, 8)
    if _u32(branch, 0x27) != a or _u32(branch, 0x3A) != b or _u32(branch, 0x47) != b or _u32(branch, 0x5B) != b:
        raise ValueError("좌표 루틴의 전역 좌표 참조가 일관되지 않습니다.")
    hook_va = image_base + section_rva + offset
    call_offset = offset + 0x9C
    call_va = image_base + section_rva + call_offset
    cleanup_va = image_base + section_rva + offset + 0xA9 + struct.unpack_from("<i", branch, 0xA5)[0]
    return CoordinateLayout(
        raw + offset, hook_va, cleanup_va, a, b,
        _u32(branch, 0x72), _u32(branch, 0x79),
        _u32(branch, 0x86), _u32(branch, 0x8D),
        _u32(branch, 0x98), call_va + 5 + struct.unpack_from("<i", branch, 0x9D)[0],
    )


def _find_existing_patch_layout(data: bytes | bytearray, image_base: int, section_header: int, section_rva: int) -> tuple[CoordinateLayout, int, int] | None:
    """Recover enough metadata to replace one of this tool's prior patches."""
    raw = _u32(data, section_header + 20)
    raw_size = _u32(data, section_header + 16)
    text = data[raw : raw + raw_size]
    for index in range(len(text) - 6):
        if text[index] != 0xE9 or text[index + 5] != 0x90:
            continue
        hook_va = image_base + section_rva + index
        cave_va = hook_va + 5 + struct.unpack_from("<i", text, index + 1)[0]
        try:
            cave_offset = _va_to_file_offset(data, cave_va)
        except ValueError:
            continue
        code = data[cave_offset : cave_offset + len(CODE_TEMPLATE)]
        if len(code) != len(CODE_TEMPLATE) or code[:3] != b"\x55\x8B\x0D":
            continue
        if code[-5] != 0xE9 or code[1:3] != b"\x8B\x0D":
            continue
        try:
            call_offset = code.index(CALL_MARKER) + len(CALL_MARKER) - 1
        except ValueError:
            continue
        jump_offset = len(CODE_TEMPLATE) - 5
        format_file_offset = data.find(ORIGINAL_COORDINATE_FORMAT)
        if format_file_offset < 0:
            continue
        original_format_va = _file_offset_to_va(data, format_file_offset)
        layout = CoordinateLayout(
            raw + index, hook_va, cave_va + jump_offset + 5 + struct.unpack_from("<i", code, jump_offset + 1)[0],
            _u32(code, 3), _u32(code, code.index(b"\xA1") + 1),
            original_format_va + 0x18, original_format_va + 0x1C,
            original_format_va + 0x24, original_format_va + 0x20,
            _u32(code, code.index(CALL_MARKER) + 1),
            cave_va + call_offset + 5 + struct.unpack_from("<i", code, call_offset + 1)[0],
        )
        return layout, cave_offset, cave_va
    return None


def _original_branch(layout: CoordinateLayout, data: bytes | bytearray) -> bytes:
    code = bytearray(ORIGINAL_BRANCH_TEMPLATE)
    _replace_u32(code, 0x5B63B0, layout.coordinate_a)
    _replace_u32(code, 0x5B63B4, layout.coordinate_b)
    _replace_u32(code, 0x56BEFC, layout.longitude_normal)
    _replace_u32(code, 0x56BEF8, layout.longitude_alternate)
    _replace_u32(code, 0x56BEF0, layout.latitude_normal)
    _replace_u32(code, 0x56BEF4, layout.latitude_alternate)
    original_format_offset = code.index(bytes.fromhex("68 D8 BE 56 00")) + 1
    format_file_offset = data.find(ORIGINAL_COORDINATE_FORMAT)
    if format_file_offset < 0:
        raise ValueError("원본 좌표 출력 문자열을 찾지 못했습니다.")
    struct.pack_into("<I", code, original_format_offset, _file_offset_to_va(data, format_file_offset))
    _replace_u32(code, FORMAT_BUFFER, layout.format_buffer)
    struct.pack_into("<i", code, 0x9D, layout.format_call - (layout.hook_va + 0x9C + 5))
    struct.pack_into("<i", code, 0xA5, layout.cleanup_va - (layout.hook_va + 0xA4 + 5))
    return bytes(code)


def _restore_existing_patch_data(
    data: bytearray,
    image_base: int,
    section_header: int,
    section_rva: int,
    found: tuple[CoordinateLayout, int, int],
) -> None:
    layout, cave_offset, cave_va = found
    data[layout.hook_offset : layout.hook_offset + len(ORIGINAL_BRANCH_TEMPLATE)] = _original_branch(layout, data)

    patch_section = find_patch_section(data)
    if patch_section is not None:
        slot_offset, slot_va = patch_section.slot(COORDINATE_SLOT_OFFSET, COORDINATE_SLOT_SIZE)
        if cave_offset == slot_offset and cave_va == slot_va:
            clear_slot(data, patch_section, COORDINATE_SLOT_OFFSET, COORDINATE_SLOT_SIZE)
            return

    raw = _u32(data, section_header + 20)
    raw_size = _u32(data, section_header + 16)
    virtual_size = _u32(data, section_header + 8)
    original_virtual_size = cave_va - (image_base + section_rva)
    if raw <= cave_offset < raw + raw_size and original_virtual_size < virtual_size:
        clear_end = min(raw + raw_size, max(raw + virtual_size, cave_offset + MAX_COORDINATE_PATCH_SIZE))
        data[cave_offset:clear_end] = b"\0" * (clear_end - cave_offset)
        struct.pack_into("<I", data, section_header + 8, original_virtual_size)
    else:
        data[cave_offset:cave_offset + MAX_COORDINATE_PATCH_SIZE] = b"\0" * MAX_COORDINATE_PATCH_SIZE


def restore_original(exe_path: Path) -> Path | None:
    data = bytearray(exe_path.read_bytes())
    image_base, section_header, virtual_size, section_rva, raw_size = _text_section(data)
    found = _find_existing_patch_layout(data, image_base, section_header, section_rva)
    if found is None:
        return None
    backup = exe_path.with_name(f"{exe_path.name}.before_coordinate_restore_{datetime.now():%Y%m%d_%H%M%S}.bak")
    shutil.copy2(exe_path, backup)
    _restore_existing_patch_data(data, image_base, section_header, section_rva, found)
    temp = exe_path.with_name(exe_path.name + ".coordinate_decimal.tmp")
    temp.write_bytes(data)
    temp.replace(exe_path)
    return backup


def patch(exe_path: Path, style: str = "korean2") -> Path | None:
    if style == "original":
        return restore_original(exe_path)
    styles = {
        "korean2": (DECIMAL_FORMAT, False, 2),
        "korean3": (KOREAN_DIRECTION_3_FORMAT, False, 3, True),
        "compass2": (COMPASS_FORMAT, True, 2),
        "compass3": (COMPASS_3_FORMAT, True, 3, False),
    }
    if style not in styles:
        raise ValueError("지원하지 않는 좌표 표시 형식입니다.")
    if style == "korean2":
        decimal_format, compass, precision, korean_directions = DECIMAL_FORMAT, False, 2, False
    elif style == "compass2":
        decimal_format, compass, precision, korean_directions = COMPASS_FORMAT, True, 2, False
    else:
        decimal_format, compass, precision, korean_directions = styles[style]
    data = bytearray(exe_path.read_bytes())
    image_base, section_header, virtual_size, section_rva, raw_size = _text_section(data)
    existing_patch = None
    try:
        layout = _find_coordinate_layout(data, image_base, section_header, section_rva)
    except ValueError:
        existing_patch = _find_existing_patch_layout(data, image_base, section_header, section_rva)
        if existing_patch is None:
            raise
        layout, cave_offset, cave_va = existing_patch
        patch_section = find_patch_section(data)
        if patch_section is not None:
            slot_offset, slot_va = patch_section.slot(COORDINATE_SLOT_OFFSET, COORDINATE_SLOT_SIZE)
            if cave_offset == slot_offset and cave_va == slot_va:
                blob = _build_code(cave_va, layout, decimal_format, compass, precision, korean_directions)
                if data[cave_offset:cave_offset + len(blob)] == blob:
                    return None
                backup_path = exe_path.with_name(f"{exe_path.name}.before_coordinate_style_{datetime.now():%Y%m%d_%H%M%S}.bak")
                shutil.copy2(exe_path, backup_path)
                clear_slot(data, patch_section, COORDINATE_SLOT_OFFSET, COORDINATE_SLOT_SIZE)
                write_slot(data, patch_section, COORDINATE_SLOT_OFFSET, COORDINATE_SLOT_SIZE, blob)
                temporary = exe_path.with_name(exe_path.name + ".coordinate_decimal.tmp")
                temporary.write_bytes(data)
                temporary.replace(exe_path)
                return backup_path

        # Migrate the former .text-tail code cave into the dedicated section.
        _restore_existing_patch_data(data, image_base, section_header, section_rva, existing_patch)
        image_base, section_header, virtual_size, section_rva, raw_size = _text_section(data)
        layout = _find_coordinate_layout(data, image_base, section_header, section_rva)
    hook_offset = layout.hook_offset
    hook_va = layout.hook_va
    patch_section, _ = ensure_patch_section(data)
    cave_offset, cave_va = patch_section.slot(COORDINATE_SLOT_OFFSET, COORDINATE_SLOT_SIZE)
    blob = _build_code(cave_va, layout, decimal_format, compass, precision, korean_directions)
    expected_jump = b"\xE9" + struct.pack("<i", cave_va - (hook_va + 5)) + b"\x90"
    if data[hook_offset : hook_offset + 2] != b"\x8B\x0D" or _u32(data, hook_offset + 2) != layout.coordinate_a:
        raise ValueError("좌표 표시 루틴이 검증한 원본 코드와 다릅니다.")
    if any(data[cave_offset:cave_offset + COORDINATE_SLOT_SIZE]):
        raise ValueError(".patch 좌표 코드 예약 공간이 비어 있지 않습니다.")

    backup_path = exe_path.with_name(
        f"{exe_path.name}.before_coordinate_decimal_{datetime.now():%Y%m%d_%H%M%S}.bak"
    )
    shutil.copy2(exe_path, backup_path)
    write_slot(data, patch_section, COORDINATE_SLOT_OFFSET, COORDINATE_SLOT_SIZE, blob)
    data[hook_offset : hook_offset + 6] = expected_jump

    # Check the finished hook and payload before replacing the original file.
    if data[hook_offset : hook_offset + 6] != expected_jump:
        raise ValueError("좌표 표시 분기 패치 검증에 실패했습니다.")
    if data[cave_offset : cave_offset + len(blob)] != blob:
        raise ValueError("좌표 표시 확장 코드 검증에 실패했습니다.")
    temporary = exe_path.with_name(exe_path.name + ".coordinate_decimal.tmp")
    temporary.write_bytes(data)
    temporary.replace(exe_path)
    return backup_path


def main() -> int:
    parser = argparse.ArgumentParser(description="CDS_95.EXE 위도/경도 소수 둘째 자리 표시 패치")
    parser.add_argument("exe", type=Path, help="패치할 CDS_95.EXE 경로")
    args = parser.parse_args()
    backup_path = patch(args.exe.resolve())
    if backup_path is None:
        print("이미 위도/경도 소수 둘째 자리 표시 패치가 적용되어 있습니다.")
    else:
        print("적용 완료: 위도/경도를 소수 둘째 자리까지 표시합니다.")
        print(f"백업: {backup_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, struct.error) as exc:
        raise SystemExit(f"오류: {exc}")
