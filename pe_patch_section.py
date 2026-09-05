"""Create and manage the executable section reserved for CDS patches."""

from __future__ import annotations

from dataclasses import dataclass
import struct


PATCH_SECTION_NAME = b".patch"
PATCH_SECTION_MAGIC = b"CDS3PAT\0"
PATCH_SECTION_VERSION = 1
PATCH_SECTION_SIZE = 0x1000
PATCH_SECTION_CHARACTERISTICS = 0x60000020  # code | execute | read

# Keep independent patches in fixed, generously sized slots.  A stable layout
# makes detection, replacement and removal deterministic and leaves room for
# future executable tweaks without consuming more of .text.
COORDINATE_SLOT_OFFSET = 0x100
COORDINATE_SLOT_SIZE = 0x200
PIRATE_SLOT_OFFSET = 0x300
PIRATE_SLOT_SIZE = 0x100
MISTRANSLATION_SLOT_OFFSET = 0x400
MISTRANSLATION_SLOT_SIZE = 0x100
ECLIPSE_SLOT_OFFSET = 0x500
ECLIPSE_SLOT_SIZE = 0x100


@dataclass(frozen=True)
class PatchSection:
    header_offset: int
    virtual_address: int
    virtual_size: int
    raw_offset: int
    raw_size: int
    image_base: int

    def slot(self, relative_offset: int, capacity: int) -> tuple[int, int]:
        if relative_offset < 0 or capacity < 0 or relative_offset + capacity > self.raw_size:
            raise ValueError(".patch 섹션 슬롯이 섹션 범위를 벗어납니다.")
        return self.raw_offset + relative_offset, self.image_base + self.virtual_address + relative_offset


def _u16(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _align(value: int, alignment: int) -> int:
    if alignment <= 0 or alignment & (alignment - 1):
        raise ValueError("PE 정렬 값이 올바르지 않습니다.")
    return (value + alignment - 1) & ~(alignment - 1)


def _pe_layout(data: bytes | bytearray) -> tuple[int, int, int, int, int, int, int]:
    if data[:2] != b"MZ":
        raise ValueError("PE 실행 파일이 아닙니다.")
    pe_offset = _u32(data, 0x3C)
    if data[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise ValueError("PE 헤더를 찾지 못했습니다.")
    section_count = _u16(data, pe_offset + 6)
    optional_size = _u16(data, pe_offset + 20)
    optional = pe_offset + 24
    if _u16(data, optional) != 0x10B:
        raise ValueError("검증한 32비트 PE 형식이 아닙니다.")
    section_table = optional + optional_size
    section_alignment = _u32(data, optional + 32)
    file_alignment = _u32(data, optional + 36)
    image_base = _u32(data, optional + 28)
    return pe_offset, optional, section_count, section_table, section_alignment, file_alignment, image_base


def find_patch_section(data: bytes | bytearray) -> PatchSection | None:
    _, _, section_count, section_table, _, _, image_base = _pe_layout(data)
    for index in range(section_count):
        header = section_table + index * 40
        name = bytes(data[header:header + 8]).rstrip(b"\0")
        if name != PATCH_SECTION_NAME:
            continue
        section = PatchSection(
            header,
            _u32(data, header + 12),
            _u32(data, header + 8),
            _u32(data, header + 20),
            _u32(data, header + 16),
            image_base,
        )
        if section.raw_size < PATCH_SECTION_SIZE or section.virtual_size < PATCH_SECTION_SIZE:
            raise ValueError("기존 .patch 섹션의 크기가 지원 형식보다 작습니다.")
        if data[section.raw_offset:section.raw_offset + len(PATCH_SECTION_MAGIC)] != PATCH_SECTION_MAGIC:
            raise ValueError("기존 .patch 섹션이 CDS 패처가 만든 형식이 아닙니다.")
        if _u32(data, section.raw_offset + 8) != PATCH_SECTION_VERSION:
            raise ValueError("지원하지 않는 .patch 섹션 버전입니다.")
        return section
    return None


def ensure_patch_section(data: bytearray) -> tuple[PatchSection, bool]:
    existing = find_patch_section(data)
    if existing is not None:
        return existing, False

    pe_offset, optional, section_count, section_table, section_alignment, file_alignment, image_base = _pe_layout(data)
    new_header = section_table + section_count * 40
    size_of_headers = _u32(data, optional + 60)
    first_raw = min(
        _u32(data, section_table + index * 40 + 20)
        for index in range(section_count)
        if _u32(data, section_table + index * 40 + 16)
    )
    if new_header + 40 > min(size_of_headers, first_raw):
        raise ValueError("새 .patch 섹션 헤더를 넣을 PE 헤더 여백이 부족합니다.")
    if any(data[new_header:new_header + 40]):
        raise ValueError("새 .patch 섹션 헤더 위치가 비어 있지 않습니다.")

    sections: list[tuple[int, int, int, int]] = []
    for index in range(section_count):
        header = section_table + index * 40
        sections.append((
            _u32(data, header + 12),
            _u32(data, header + 8),
            _u32(data, header + 20),
            _u32(data, header + 16),
        ))
    max_raw_end = max(raw + raw_size for _, _, raw, raw_size in sections)
    if len(data) != max_raw_end:
        raise ValueError("파일 끝의 추가 데이터가 있어 안전하게 .patch 섹션을 붙일 수 없습니다.")

    raw_offset = _align(max_raw_end, file_alignment)
    raw_size = _align(PATCH_SECTION_SIZE, file_alignment)
    virtual_address = _align(
        max(va + max(virtual_size, raw_size_existing) for va, virtual_size, _, raw_size_existing in sections),
        section_alignment,
    )
    virtual_size = PATCH_SECTION_SIZE

    if raw_offset > len(data):
        data.extend(b"\0" * (raw_offset - len(data)))
    data.extend(b"\0" * raw_size)

    name = PATCH_SECTION_NAME.ljust(8, b"\0")
    header = struct.pack(
        "<8sIIIIIIHHI",
        name,
        virtual_size,
        virtual_address,
        raw_size,
        raw_offset,
        0,
        0,
        0,
        0,
        PATCH_SECTION_CHARACTERISTICS,
    )
    data[new_header:new_header + 40] = header
    struct.pack_into("<H", data, pe_offset + 6, section_count + 1)
    struct.pack_into("<I", data, optional + 4, _u32(data, optional + 4) + raw_size)  # SizeOfCode
    struct.pack_into(
        "<I", data, optional + 56,
        _align(virtual_address + virtual_size, section_alignment),
    )
    struct.pack_into("<I", data, optional + 64, 0)  # CheckSum

    data[raw_offset:raw_offset + len(PATCH_SECTION_MAGIC)] = PATCH_SECTION_MAGIC
    struct.pack_into("<I", data, raw_offset + 8, PATCH_SECTION_VERSION)
    section = PatchSection(
        new_header, virtual_address, virtual_size, raw_offset, raw_size, image_base,
    )
    return section, True


def write_slot(
    data: bytearray,
    section: PatchSection,
    relative_offset: int,
    capacity: int,
    payload: bytes,
) -> tuple[int, int]:
    if len(payload) > capacity:
        raise ValueError(f"패치 코드 {len(payload)}바이트가 예약 공간 {capacity}바이트를 초과합니다.")
    file_offset, virtual_address = section.slot(relative_offset, capacity)
    current = data[file_offset:file_offset + capacity]
    if any(current) and current[:len(payload)] != payload:
        raise ValueError(".patch 섹션의 예약 공간이 예상하지 못한 데이터로 사용 중입니다.")
    data[file_offset:file_offset + capacity] = b"\0" * capacity
    data[file_offset:file_offset + len(payload)] = payload
    return file_offset, virtual_address


def clear_slot(data: bytearray, section: PatchSection, relative_offset: int, capacity: int) -> None:
    file_offset, _ = section.slot(relative_offset, capacity)
    data[file_offset:file_offset + capacity] = b"\0" * capacity
