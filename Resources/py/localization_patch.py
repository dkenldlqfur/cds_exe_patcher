"""Reversible, reference-checked localization of the supported Korean EXE.

Original string bytes are never overwritten. Only manifest-listed pointers
are redirected. A journal preserves the actual previous pointer of each owned
reference; custom names/descriptions are not translated by substring matching.
"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import struct
import sys

import pefile

from pe_patch_section import (
    LOCALIZATION_SLOT_OFFSET, LOCALIZATION_SLOT_SIZE,
    PATCH_SECTION_LOCALIZATION_SIZE, ensure_patch_section, find_patch_section,
)

MAGIC = b"CDSLOC1\0"
HEADER = struct.Struct("<8sII")
ROW = struct.Struct("<III")
POOL_OFFSET = 0x4000


@lru_cache(maxsize=1)
def manifest() -> tuple[dict, ...]:
    root = (Path(sys._MEIPASS) / "Resources" if hasattr(sys, "_MEIPASS")
            else Path(__file__).resolve().parents[1])
    doc = json.loads((root / "data/localization_manifest.json").read_text(encoding="utf8"))
    if doc["version"] != 1:
        raise ValueError("지원하지 않는 오역 수정 목록입니다.")
    return tuple(doc["entries"])


@lru_cache(maxsize=1)
def _pool() -> tuple[bytes, dict[int, tuple[dict, int, str]]]:
    pool = bytearray()
    refs = {}
    for entry in manifest():
        relative = POOL_OFFSET + len(pool)
        pool.extend(entry["corrected"].encode("cp949") + b"\0")
        for offset, prefix in entry["refs"]:
            if offset in refs:
                raise ValueError("오역 수정 참조가 중복되었습니다.")
            refs[offset] = (entry, relative, prefix)
    if POOL_OFFSET + len(pool) > LOCALIZATION_SLOT_SIZE:
        raise ValueError("오역 수정 문자열 공간이 부족합니다.")
    return bytes(pool), refs


def _read_text(data: bytes | bytearray, pe: pefile.PE, va: int) -> str | None:
    rva = va - pe.OPTIONAL_HEADER.ImageBase
    if not any(s.VirtualAddress <= rva < s.VirtualAddress + s.SizeOfRawData
               for s in pe.sections):
        return None
    try:
        offset = pe.get_offset_from_rva(rva)
        end = data.find(b"\0", offset, min(offset + 2048, len(data)))
        if end < 0:
            return None
        return bytes(data[offset:end]).decode("cp949")
    except (pefile.PEFormatError, UnicodeDecodeError):
        return None


def _state(data: bytes | bytearray) -> tuple[dict[int, tuple[int, int]], bool]:
    section = find_patch_section(data)
    if section is None or section.raw_size < PATCH_SECTION_LOCALIZATION_SIZE:
        return {}, False
    offset, va = section.slot(LOCALIZATION_SLOT_OFFSET, LOCALIZATION_SLOT_SIZE)
    header = bytes(data[offset:offset + HEADER.size])
    if not any(header):
        # Disabled patches retain only their known immutable string pool.
        slot = bytes(data[offset:offset + LOCALIZATION_SLOT_SIZE])
        if any(slot):
            pool, _ = _pool()
            expected = (b"\0" * POOL_OFFSET + pool).ljust(LOCALIZATION_SLOT_SIZE, b"\0")
            if slot != expected:
                raise ValueError("현지어 오역 수정 예약 공간이 다른 데이터로 사용 중입니다.")
        return {}, False
    magic, version, count = HEADER.unpack(header)
    if magic != MAGIC or version != 1 or HEADER.size + count * ROW.size > POOL_OFFSET:
        raise ValueError("현지어 오역 수정 복원 정보가 손상되었습니다.")
    pool, allowed = _pool()
    if data[offset + POOL_OFFSET:offset + POOL_OFFSET + len(pool)] != pool:
        raise ValueError("현지어 오역 수정 문자열이 손상되었습니다.")
    state = {}
    for index in range(count):
        ref, original, patched = ROW.unpack_from(data, offset + HEADER.size + index * ROW.size)
        if ref not in allowed or ref in state or patched != va + allowed[ref][1]:
            raise ValueError("현지어 오역 수정 참조 정보가 손상되었습니다.")
        if original == patched or ref + 4 > len(data):
            raise ValueError("현지어 오역 수정 원본 주소가 올바르지 않습니다.")
        state[ref] = (original, patched)
    return state, True


def read_extended_localization_state(data: bytes | bytearray) -> bool:
    return _state(data)[1]


def apply_extended_localization(data: bytearray, enabled: bool) -> bool:
    owned, was_enabled = _state(data)
    if not enabled and not was_enabled:
        return False
    pool, references = _pool()
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("현지어 오역 수정은 검증된 한국어판 EXE만 지원합니다.")
        selected: dict[int, tuple[int, int]] = {}
        restore = {}
        for ref, (entry, relative, prefix_hex) in references.items():
            if ref + 4 > len(data):
                raise ValueError("현지어 오역 수정 참조 범위를 벗어났습니다.")
            prefix = bytes.fromhex(prefix_hex)
            # A separately patched instruction must not be treated as a pointer.
            if prefix and data[ref - len(prefix):ref] != prefix:
                if ref in owned:
                    raise ValueError(f"오역 수정 명령 0x{ref:X}가 변경되었습니다.")
                continue
            current = struct.unpack_from("<I", data, ref)[0]
            text = _read_text(data, pe, current)
            if ref in owned:
                original, patched = owned[ref]
                # A tab may have copied the unchanged translated text into its
                # own editable slot. Restore that copy too, but not custom text.
                if current == patched or text == entry["corrected"]:
                    if enabled:
                        selected[ref] = (original, relative)
                    else:
                        if _read_text(data, pe, original) != entry["original"]:
                            # An editor may reuse the prior name slot. Fall
                            # back only to the verified, untouched master text.
                            original = entry["va"]
                            if _read_text(data, pe, original) != entry["original"]:
                                raise ValueError("오역 수정 원본 문자열이 변경되어 복원할 수 없습니다.")
                        restore[ref] = original
                    continue
            if enabled and text == entry["original"]:
                selected[ref] = (current, relative)
    finally:
        pe.close()

    # Every validation above finishes before changing the caller's bytes.
    before = bytes(data)
    if enabled:
        section, _ = ensure_patch_section(data, PATCH_SECTION_LOCALIZATION_SIZE)
        offset, va = section.slot(LOCALIZATION_SLOT_OFFSET, LOCALIZATION_SLOT_SIZE)
        journal = bytearray(HEADER.pack(MAGIC, 1, len(selected)))
        for ref, (original, relative) in sorted(selected.items()):
            patched = va + relative
            journal.extend(ROW.pack(ref, original, patched))
        if len(journal) > POOL_OFFSET:
            raise ValueError("오역 수정 복원 공간이 부족합니다.")
        data[offset:offset + POOL_OFFSET] = journal.ljust(POOL_OFFSET, b"\0")
        data[offset + POOL_OFFSET:offset + POOL_OFFSET + len(pool)] = pool
        for ref, (_original, relative) in selected.items():
            struct.pack_into("<I", data, ref, va + relative)
    else:
        for ref, original in restore.items():
            struct.pack_into("<I", data, ref, original)
        section = find_patch_section(data)
        assert section is not None
        offset, _ = section.slot(LOCALIZATION_SLOT_OFFSET, LOCALIZATION_SLOT_SIZE)
        data[offset:offset + POOL_OFFSET] = b"\0" * POOL_OFFSET
        # Retain immutable strings: other patch snapshots can legitimately hold
        # copies of these pointers. They must never become dangling references.
    return bytes(data) != before
