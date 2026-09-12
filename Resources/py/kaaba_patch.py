"""Reversible embedded Kaaba Temple patch for CDS III game data.

Unlike ordinary EXE-only tweaks, this feature changes CDS_95.EXE, DSTILL.CDS
and DISEV.CDS together.  The EXE's private .patch slot stores the exact
discovery record and DISEV part that existed before injection, so unchecking
the option can safely restore the three files without relying on loose backups.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import os
from pathlib import Path
import shutil
import struct
import tempfile

import pefile

from app_update import bundled_resource_path
from pe_patch_section import (
    KAABA_SLOT_OFFSET,
    KAABA_SLOT_SIZE,
    clear_slot,
    ensure_patch_section,
    find_patch_section,
    write_slot,
)


KAABA_MAGIC = b"CDSKAB1\0"
KAABA_IMAGE_MAGIC = b"CDSKABIMG1\0"
KAABA_DISCOVERY_NAME = "카바신전"
KAABA_GAME_ID = 672
KAABA_EVENT_PART = 63
STILL_PARTS_PER_SLOT = 3
RECORD_SIZE = 0x5C
RECORD_GAME_ID_OFFSET = 8
RECORD_STILL_OFFSET = 0x0C
RECORD_AVI_OFFSET = 0x10
RECORD_CG_OFFSET = 0x14
RECORD_AVAILABILITY_FLAG_OFFSETS = (0x20, 0x2C)
NO_MEDIA = 0xFFFFFFFF
STATE_HEADER = struct.Struct("<8sIIIIII32s")
STATE_RECORD_OFFSET = STATE_HEADER.size
STATE_DESCRIPTION_OFFSET = 0x880
STATE_DESCRIPTION_CAPACITY = KAABA_SLOT_SIZE - STATE_DESCRIPTION_OFFSET

KAABA_DESCRIPTION = (
    "메카 성역의 정육면체 석조 신전. 검은 천으로 덮인 외관을 지녔으며 "
    "이슬람의 제1성지다. 전승에 따르면 신이 천사들을 보내 아브라함과 "
    "이스마엘의 카바 건설을 도왔고, 완공 뒤 신전을 축복했다. 이스마엘의 "
    "후손들은 해마다 이곳을 순례했다."
).encode("cp949") + b"\0"
PREVIOUS_DESCRIPTION = (
    "12세기 독일 로마네스크 건축. 캐롤링 왕조시대의 건축양식이 남아 있다."
).encode("cp949") + b"\0"
NEXT_DESCRIPTION = (
    "노틀담·라·그랜드라 한다. 높이보다도 수평방향의 넓이가 크고 평면은 "
    "긴 십자형이며 벽면크기에 비해 창은 작다. 이 특징은 로마네스크건축에 "
    "공통된 것이다. 12세기의 장중하고 중후한 건축."
).encode("cp949") + b"\0"


class KaabaPatchError(ValueError):
    """The selected game files cannot safely receive or remove this patch."""


def _resource(name: str) -> bytes:
    try:
        return bundled_resource_path("Resources", "bin", name).read_bytes()
    except OSError as exc:
        raise KaabaPatchError(f"내장 카바신전 리소스를 읽지 못했습니다: {name}") from exc


def _image_assets() -> tuple[bytes, bytes, bytes]:
    raw = _resource("kaaba_still.bin")
    if not raw.startswith(KAABA_IMAGE_MAGIC):
        raise KaabaPatchError("내장 카바신전 이미지의 형식을 검증하지 못했습니다.")
    payload = raw[len(KAABA_IMAGE_MAGIC):]
    pixels, palette, size = payload[:76800], payload[76800:76800 + 258], payload[76800 + 258:]
    if len(pixels) != 76800 or len(palette) != 258 or size != struct.pack("<II", 320, 240):
        raise KaabaPatchError("내장 카바신전 이미지 데이터가 손상되었습니다.")
    return pixels, palette, size


def _event_template() -> bytes:
    template = _resource("kaaba_part63.bin")
    if len(template) < 9 or struct.unpack_from("<H", template)[0] != KAABA_EVENT_PART:
        raise KaabaPatchError("내장 카바신전 이벤트 데이터가 손상되었습니다.")
    return template


def _parse_ls12(data: bytes, label: str) -> list[tuple[int, int, int]]:
    if data[:4] not in (b"Ls12", b"LS11"):
        raise KaabaPatchError(f"{label}가 LS12 아카이브가 아닙니다.")
    entries: list[tuple[int, int, int]] = []
    offset = 0x110
    while offset + 12 <= len(data):
        compressed, uncompressed, payload = struct.unpack_from(">III", data, offset)
        if compressed == 0:
            break
        if payload + compressed > len(data):
            raise KaabaPatchError(f"{label}의 파트 범위가 손상되었습니다.")
        entries.append((compressed, uncompressed, payload))
        offset += 12
    if not entries:
        raise KaabaPatchError(f"{label}에 파트가 없습니다.")
    return entries


def _rebuild_ls12(data: bytes, entries: list[tuple[int, int, int]], replacements: dict[int, tuple[bytes, int]]) -> bytes:
    blobs: list[bytes] = []
    metadata: list[tuple[int, int]] = []
    for index, (compressed, uncompressed, payload) in enumerate(entries):
        if index in replacements:
            blob, original_size = replacements[index]
            blobs.append(blob)
            metadata.append((len(blob), original_size))
        else:
            blobs.append(data[payload:payload + compressed])
            metadata.append((compressed, uncompressed))
    table_end = 0x110 + len(blobs) * 12 + 4
    output = bytearray(data[:0x110])
    payload_offset = table_end
    for (compressed, uncompressed), blob in zip(metadata, blobs):
        output.extend(struct.pack(">III", compressed, uncompressed, payload_offset))
        payload_offset += compressed
    output.extend(b"\0\0\0\0")
    output.extend(b"".join(blobs))
    return bytes(output)


def _append_still(data: bytes) -> tuple[bytes, int]:
    entries = _parse_ls12(data, "DSTILL.CDS")
    if len(entries) % STILL_PARTS_PER_SLOT:
        raise KaabaPatchError("DSTILL.CDS의 이미지 파트 수가 슬롯 단위와 맞지 않습니다.")
    still_slot = len(entries) // STILL_PARTS_PER_SLOT
    blobs = [data[offset:offset + compressed] for compressed, _uncompressed, offset in entries]
    metadata = [(compressed, uncompressed) for compressed, uncompressed, _offset in entries]
    assets = _image_assets()
    blobs.extend(assets)
    metadata.extend((len(asset), len(asset)) for asset in assets)
    table_end = 0x110 + len(blobs) * 12 + 4
    output = bytearray(data[:0x110])
    payload_offset = table_end
    for (compressed, uncompressed), blob in zip(metadata, blobs):
        output.extend(struct.pack(">III", compressed, uncompressed, payload_offset))
        payload_offset += compressed
    output.extend(b"\0\0\0\0")
    output.extend(b"".join(blobs))
    return bytes(output), still_slot


def _remove_still(data: bytes, original_slots: int) -> bytes:
    entries = _parse_ls12(data, "DSTILL.CDS")
    if original_slots < 1 or len(entries) != (original_slots + 1) * STILL_PARTS_PER_SLOT:
        raise KaabaPatchError("카바신전 주입 뒤의 DSTILL 슬롯 구성을 검증하지 못했습니다.")
    assets = _image_assets()
    for entry, expected in zip(entries[-3:], assets):
        compressed, uncompressed, payload = entry
        if (compressed, uncompressed) != (len(expected), len(expected)) or data[payload:payload + compressed] != expected:
            raise KaabaPatchError("DSTILL.CDS의 카바신전 내장 이미지가 예상과 다릅니다.")
    return _rebuild_ls12(data, entries[:-3], {})


def _unique_offset(data: bytes | bytearray, needle: bytes, label: str) -> int:
    matches: list[int] = []
    cursor = 0
    while True:
        offset = data.find(needle, cursor)
        if offset < 0:
            break
        matches.append(offset)
        cursor = offset + 1
    if len(matches) != 1:
        raise KaabaPatchError(f"{label}을(를) 하나로 특정하지 못했습니다.")
    return matches[0]


def _file_offset_to_va(data: bytes | bytearray, offset: int) -> int:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.OPTIONAL_HEADER.ImageBase + pe.get_rva_from_offset(offset)
    finally:
        pe.close()


def _locate_record(data: bytes | bytearray) -> int:
    name_offset = _unique_offset(data, KAABA_DISCOVERY_NAME.encode("cp949"), "카바신전 이름")
    pointer = struct.pack("<I", _file_offset_to_va(data, name_offset))
    candidates: list[int] = []
    cursor = 0
    while True:
        offset = data.find(pointer, cursor)
        if offset < 0:
            break
        if offset + RECORD_SIZE <= len(data):
            game_id = struct.unpack_from("<I", data, offset + RECORD_GAME_ID_OFFSET)[0]
            flags = tuple(struct.unpack_from("<I", data, offset + relative)[0] for relative in RECORD_AVAILABILITY_FLAG_OFFSETS)
            if game_id == KAABA_GAME_ID and all(flag in (0, 1) for flag in flags):
                candidates.append(offset)
        cursor = offset + 1
    if len(candidates) != 1:
        raise KaabaPatchError("카바신전 발견물 레코드를 하나로 특정하지 못했습니다.")
    return candidates[0]


def _locate_description_pointer(data: bytes | bytearray) -> int:
    previous_va = _file_offset_to_va(data, _unique_offset(data, PREVIOUS_DESCRIPTION, "앞 발견물 설명"))
    next_va = _file_offset_to_va(data, _unique_offset(data, NEXT_DESCRIPTION, "뒤 발견물 설명"))
    previous_pointer, next_pointer = struct.pack("<I", previous_va), struct.pack("<I", next_va)
    candidates: list[int] = []
    cursor = 0
    while True:
        offset = data.find(previous_pointer, cursor)
        if offset < 0:
            break
        if data[offset + 8:offset + 12] == next_pointer:
            candidates.append(offset + 4)
        cursor = offset + 1
    if len(candidates) != 1:
        raise KaabaPatchError("카바신전 설명 포인터를 하나로 특정하지 못했습니다.")
    return candidates[0]


def _read_state(data: bytes | bytearray) -> tuple[int, int, int, bytes, int, int, bytes] | None:
    section = find_patch_section(data)
    if section is None:
        return None
    slot_offset, slot_va = section.slot(KAABA_SLOT_OFFSET, KAABA_SLOT_SIZE)
    header = bytes(data[slot_offset:slot_offset + STATE_HEADER.size])
    magic, record_offset, description_pointer, slots, compressed, uncompressed, blob_length, still_digest = STATE_HEADER.unpack(header)
    if magic != KAABA_MAGIC:
        return None
    blob_start = slot_offset + STATE_RECORD_OFFSET + RECORD_SIZE
    blob_end = blob_start + blob_length
    if slots < 1 or blob_end > slot_offset + STATE_DESCRIPTION_OFFSET:
        raise KaabaPatchError("카바신전 복원 정보가 손상되었습니다.")
    record = bytes(data[slot_offset + STATE_RECORD_OFFSET:blob_start])
    if len(record) != RECORD_SIZE or hashlib.sha256(b"".join(_image_assets())).digest() != still_digest:
        raise KaabaPatchError("카바신전 복원 정보의 무결성을 검증하지 못했습니다.")
    expected_description_pointer = slot_va + STATE_DESCRIPTION_OFFSET
    if description_pointer == expected_description_pointer:
        raise KaabaPatchError("카바신전 원본 설명 포인터가 손상되었습니다.")
    return record_offset, description_pointer, slots, record, compressed, uncompressed, bytes(data[blob_start:blob_end])


def _looks_like_injected_record(exe: bytes) -> bool:
    try:
        record_offset = _locate_record(exe)
    except KaabaPatchError:
        return False
    slot = struct.unpack_from("<I", exe, record_offset + RECORD_STILL_OFFSET)[0]
    avi = struct.unpack_from("<I", exe, record_offset + RECORD_AVI_OFFSET)[0]
    cg = struct.unpack_from("<I", exe, record_offset + RECORD_CG_OFFSET)[0]
    flags = tuple(
        struct.unpack_from("<I", exe, record_offset + relative)[0]
        for relative in RECORD_AVAILABILITY_FLAG_OFFSETS
    )
    return slot != NO_MEDIA and avi == NO_MEDIA and cg == NO_MEDIA and flags == (0, 0)


def _looks_like_injected_still(dstill: bytes) -> bool:
    try:
        entries = _parse_ls12(dstill, "DSTILL.CDS")
    except KaabaPatchError:
        return False
    if len(entries) % STILL_PARTS_PER_SLOT:
        return False
    try:
        assets = _image_assets()
    except KaabaPatchError:
        return False
    return all(
        (compressed, uncompressed) == (len(expected), len(expected))
        and dstill[payload:payload + compressed] == expected
        for (compressed, uncompressed, payload), expected in zip(entries[-3:], assets)
    )


def _looks_like_injected_event(disev: bytes) -> bool:
    try:
        entries = _parse_ls12(disev, "DISEV.CDS")
        compressed, uncompressed, payload = entries[KAABA_EVENT_PART]
        template = _event_template()
    except (KaabaPatchError, IndexError):
        return False
    return (
        compressed == len(template) and uncompressed == len(template)
        and disev[payload:payload + compressed] == template
    )


def is_enabled(exe_path: Path) -> bool:
    """Return managed state, rejecting unsafe legacy or partial injection sets."""
    exe_path = exe_path.resolve(strict=True)
    exe = exe_path.read_bytes()
    if _read_state(exe) is not None:
        return True
    dstill_path, disev_path = exe_path.with_name("DSTILL.CDS"), exe_path.with_name("DISEV.CDS")
    if not dstill_path.is_file() or not disev_path.is_file():
        return False
    signatures = (
        _looks_like_injected_record(exe),
        _looks_like_injected_still(dstill_path.read_bytes()),
        _looks_like_injected_event(disev_path.read_bytes()),
    )
    if any(signatures):
        raise KaabaPatchError(
            "카바신전 파일이 기존 또는 부분 주입 상태입니다. "
            "패처가 만든 복원 정보가 없어 자동 상태 설정과 안전한 해제를 할 수 없습니다."
        )
    return False


def _state_payload(
    record_offset: int,
    original_description_pointer: int,
    original_slots: int,
    record: bytes,
    original_part: tuple[int, int, bytes],
) -> bytes:
    compressed, uncompressed, blob = original_part
    blob_end = STATE_RECORD_OFFSET + RECORD_SIZE + len(blob)
    if blob_end > STATE_DESCRIPTION_OFFSET or len(KAABA_DESCRIPTION) > STATE_DESCRIPTION_CAPACITY:
        raise KaabaPatchError("카바신전 복원 정보가 .patch 예약 공간을 초과합니다.")
    payload = bytearray(KAABA_SLOT_SIZE)
    STATE_HEADER.pack_into(
        payload, 0, KAABA_MAGIC, record_offset, original_description_pointer,
        original_slots, compressed, uncompressed, len(blob),
        hashlib.sha256(b"".join(_image_assets())).digest(),
    )
    payload[STATE_RECORD_OFFSET:STATE_RECORD_OFFSET + RECORD_SIZE] = record
    payload[STATE_RECORD_OFFSET + RECORD_SIZE:blob_end] = blob
    payload[STATE_DESCRIPTION_OFFSET:STATE_DESCRIPTION_OFFSET + len(KAABA_DESCRIPTION)] = KAABA_DESCRIPTION
    return bytes(payload)


def _back_up_and_replace(
    changes: dict[Path, bytes], backed_up_paths: set[Path] | None = None,
) -> tuple[Path, ...]:
    changed = {path.resolve(): content for path, content in changes.items() if path.read_bytes() != content}
    if not changed:
        return ()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backups: list[Path] = []
    temporary: dict[Path, Path] = {}
    try:
        for path in changed:
            if backed_up_paths is not None and path in backed_up_paths:
                continue
            backup = path.with_name(f"{path.name}.before_kaaba_{stamp}.bak")
            shutil.copy2(path, backup)
            if backup.read_bytes() != path.read_bytes():
                raise IOError(f"{path.name} 백업 검증에 실패했습니다.")
            backups.append(backup)
            if backed_up_paths is not None:
                backed_up_paths.add(path)
        for path, content in changed.items():
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix="cds-kaaba-", suffix=".tmp", delete=False) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
                temporary[path] = Path(stream.name)
        for path, temporary_path in temporary.items():
            if temporary_path.read_bytes() != changed[path]:
                raise IOError(f"{path.name} 임시 파일 검증에 실패했습니다.")
            os.replace(temporary_path, path)
        for path, content in changed.items():
            if path.read_bytes() != content:
                raise IOError(f"{path.name} 저장 후 검증에 실패했습니다.")
    finally:
        for temporary_path in temporary.values():
            temporary_path.unlink(missing_ok=True)
    return tuple(backups)


def apply(
    exe_path: Path, enabled: bool, backed_up_paths: set[Path] | None = None,
) -> tuple[Path, ...]:
    """Enable or remove the complete Kaaba Temple patch and return backups."""
    exe_path = exe_path.resolve(strict=True)
    dstill_path, disev_path = exe_path.with_name("DSTILL.CDS"), exe_path.with_name("DISEV.CDS")
    if not dstill_path.is_file() or not disev_path.is_file():
        raise KaabaPatchError("선택한 EXE와 같은 폴더에 DSTILL.CDS와 DISEV.CDS가 필요합니다.")
    exe, dstill, disev = exe_path.read_bytes(), dstill_path.read_bytes(), disev_path.read_bytes()
    state = _read_state(exe)
    if enabled:
        if state is not None:
            return ()

        # 1. Reserve the first free DSTILL slot.  The record only receives
        # this number after the image archive has been rebuilt.
        updated_dstill, still_slot = _append_still(dstill)

        # 2. Prepare the Kaaba event replacement.
        entries = _parse_ls12(disev, "DISEV.CDS")
        if len(entries) <= KAABA_EVENT_PART:
            raise KaabaPatchError("DISEV.CDS에서 카바신전 이벤트 파트를 찾지 못했습니다.")
        compressed, uncompressed, payload = entries[KAABA_EVENT_PART]
        original_part = (compressed, uncompressed, disev[payload:payload + compressed])
        updated_disev = _rebuild_ls12(disev, entries, {KAABA_EVENT_PART: (_event_template(), len(_event_template()))})

        # 3. Activate the existing Kaaba discovery record and point it at the
        # slot chosen from DSTILL.CDS.  An original NO_MEDIA value is valid.
        record_offset = _locate_record(exe)
        description_pointer_offset = _locate_description_pointer(exe)
        original_record = exe[record_offset:record_offset + RECORD_SIZE]
        original_description_pointer = struct.unpack_from("<I", exe, description_pointer_offset)[0]
        updated_exe = bytearray(exe)
        section, _created = ensure_patch_section(updated_exe)
        slot_offset, slot_va = section.slot(KAABA_SLOT_OFFSET, KAABA_SLOT_SIZE)
        struct.pack_into("<I", updated_exe, record_offset + RECORD_STILL_OFFSET, still_slot)
        struct.pack_into("<I", updated_exe, record_offset + RECORD_AVI_OFFSET, NO_MEDIA)
        struct.pack_into("<I", updated_exe, record_offset + RECORD_CG_OFFSET, NO_MEDIA)
        for relative in RECORD_AVAILABILITY_FLAG_OFFSETS:
            struct.pack_into("<I", updated_exe, record_offset + relative, 0)
        struct.pack_into("<I", updated_exe, description_pointer_offset, slot_va + STATE_DESCRIPTION_OFFSET)
        clear_slot(updated_exe, section, KAABA_SLOT_OFFSET, KAABA_SLOT_SIZE)
        write_slot(updated_exe, section, KAABA_SLOT_OFFSET, KAABA_SLOT_SIZE, _state_payload(
            record_offset, original_description_pointer, still_slot, original_record, original_part,
        ))
        return _back_up_and_replace(
            {dstill_path: updated_dstill, disev_path: updated_disev, exe_path: bytes(updated_exe)},
            backed_up_paths,
        )

    if state is None:
        return ()
    record_offset, original_description_pointer, original_slots, original_record, compressed, uncompressed, original_blob = state
    section = find_patch_section(exe)
    assert section is not None
    slot_offset, slot_va = section.slot(KAABA_SLOT_OFFSET, KAABA_SLOT_SIZE)
    if exe[record_offset:record_offset + 4] != original_record[:4]:
        raise KaabaPatchError("카바신전 발견물 레코드가 예상과 달라 복원을 중단했습니다.")
    description_pointer_offset = _locate_description_pointer(exe)
    if struct.unpack_from("<I", exe, description_pointer_offset)[0] != slot_va + STATE_DESCRIPTION_OFFSET:
        raise KaabaPatchError("카바신전 설명 포인터가 예상과 달라 복원을 중단했습니다.")
    updated_dstill = _remove_still(dstill, original_slots)
    entries = _parse_ls12(disev, "DISEV.CDS")
    if len(entries) <= KAABA_EVENT_PART:
        raise KaabaPatchError("DISEV.CDS에서 카바신전 이벤트 파트를 찾지 못했습니다.")
    current = entries[KAABA_EVENT_PART]
    template = _event_template()
    if current[0] != len(template) or current[1] != len(template) or disev[current[2]:current[2] + current[0]] != template:
        raise KaabaPatchError("DISEV.CDS의 카바신전 이벤트가 예상과 달라 복원을 중단했습니다.")
    updated_disev = _rebuild_ls12(disev, entries, {KAABA_EVENT_PART: (original_blob, uncompressed)})
    updated_exe = bytearray(exe)
    updated_exe[record_offset:record_offset + RECORD_SIZE] = original_record
    struct.pack_into("<I", updated_exe, description_pointer_offset, original_description_pointer)
    clear_slot(updated_exe, section, KAABA_SLOT_OFFSET, KAABA_SLOT_SIZE)
    return _back_up_and_replace(
        {exe_path: bytes(updated_exe), dstill_path: updated_dstill, disev_path: updated_disev},
        backed_up_paths,
    )
