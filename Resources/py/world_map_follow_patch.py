"""Reversible native world-map camera tracking, with pixel-aligned rendering.

The native movement path remains responsible for terrain, fog and time. Code
and runtime data occupy an independent slot; no original game assets change.
"""

from __future__ import annotations

from functools import lru_cache
import struct

import pefile

from pe_patch_section import (
    WORLD_MAP_FOLLOW_SLOT_OFFSET, WORLD_MAP_FOLLOW_SLOT_SIZE,
    PATCH_SECTION_WORLD_MAP_FOLLOW_SIZE, clear_slot, ensure_patch_section,
    find_patch_section,
)
from world_map_follow_camera import build_camera, ENSURE_FOLLOW_OFFSET
from world_map_follow_render import build_render
from world_map_follow_weather import build_weather
from world_map_follow_modal import build_modal
from world_map_follow_smooth import build_smooth
from world_map_follow_v1 import (
    build_camera as build_camera_v1,
    build_render as build_render_v1,
)
import world_map_follow_v2 as frozen_v2
import world_map_follow_v3 as frozen_v3
import world_map_follow_v4 as frozen_v4
import world_map_follow_v5 as frozen_v5


MAGIC = b"CDSWMC1\0"
VERSION = 6
SUPPORTED_VERSIONS = (1, 2, 3, 4, 5, VERSION)
RUNTIME_OFFSET = 0x6000


class _State(dict):
    """Assign deterministic, disjoint locations to the two code generators."""

    def __init__(self, slot_va: int, ensure_follow_offset: int = ENSURE_FOLLOW_OFFSET):
        super().__init__()
        self.cursor = slot_va + RUNTIME_OFFSET
        self.limit = slot_va + WORLD_MAP_FOLLOW_SLOT_SIZE
        for name in ("rx", "ry", "width", "height", "active", "view", "valid",
                     "legacy_x", "legacy_y", "follow_x", "follow_y"):
            self.allocate(name)
        self.allocate("npc_snapshot", 192)
        self.allocate("scratch", 512)
        self["ensure_follow"] = slot_va + ensure_follow_offset

    def allocate(self, name: str, size: int = 4) -> int:
        if name in self:
            raise AssertionError(f"Duplicate camera runtime field: {name}")
        address = self.cursor
        self.cursor += (size + 3) & ~3
        if self.cursor > self.limit:
            raise AssertionError("중앙 추적 실행 데이터가 예약 공간을 초과했습니다.")
        self[name] = address
        return address

    def __missing__(self, name: str) -> int:
        return self.allocate(name)


@lru_cache(maxsize=16)
def _layout(slot_va: int) -> tuple[bytes, tuple, dict[str, int]]:
    return _build_layout(slot_va, VERSION, (
        (build_camera, 0x100, 0x1800),
        (build_render, 0x1800, 0x2800),
        (build_weather, 0x2800, 0x4000),
        (build_smooth, 0x4000, 0x5800),
        (build_modal, 0x5800, RUNTIME_OFFSET),
    ), ENSURE_FOLLOW_OFFSET)


@lru_cache(maxsize=16)
def _legacy_layout(slot_va: int) -> tuple[bytes, tuple, dict[str, int]]:
    # The original builders and allocation order are frozen. The unchanged
    # modal builder is shared with frozen v2 because it was unchanged there;
    # hash tests pin all v1 bytes independently of newer camera/render code.
    return _build_layout(slot_va, 1, (
        (build_camera_v1, 0x100, 0x1800),
        (build_render_v1, 0x1800, 0x5800),
        (frozen_v2.build_modal, 0x5800, RUNTIME_OFFSET),
    ), 0x100)


@lru_cache(maxsize=16)
def _legacy_v2_layout(slot_va: int) -> tuple[bytes, tuple, dict[str, int]]:
    return _build_layout(slot_va, 2, (
        (frozen_v2.build_camera, 0x100, 0x1800),
        (frozen_v2.build_render, 0x1800, 0x4000),
        (frozen_v2.build_smooth, 0x4000, 0x5800),
        (frozen_v2.build_modal, 0x5800, RUNTIME_OFFSET),
    ), 0x100)


@lru_cache(maxsize=16)
def _legacy_v3_layout(slot_va: int) -> tuple[bytes, tuple, dict[str, int]]:
    return _build_layout(slot_va, 3, (
        (frozen_v3.build_camera, 0x100, 0x1800),
        (frozen_v3.build_render, 0x1800, 0x4000),
        (frozen_v3.build_smooth, 0x4000, 0x5800),
        (frozen_v3.build_modal, 0x5800, RUNTIME_OFFSET),
    ), 0x100)


@lru_cache(maxsize=16)
def _legacy_v4_layout(slot_va: int) -> tuple[bytes, tuple, dict[str, int]]:
    return _build_layout(slot_va, 4, (
        (frozen_v4.build_camera, 0x100, 0x1800),
        (frozen_v4.build_render, 0x1800, 0x4000),
        (frozen_v4.build_smooth, 0x4000, 0x5800),
        (frozen_v4.build_modal, 0x5800, RUNTIME_OFFSET),
    ), 0x100)


@lru_cache(maxsize=16)
def _legacy_v5_layout(slot_va: int) -> tuple[bytes, tuple, dict[str, int]]:
    return _build_layout(slot_va, 5, (
        (frozen_v5.build_camera, 0x100, 0x1800),
        (frozen_v5.build_render, 0x1800, 0x2800),
        (frozen_v5.build_weather, 0x2800, 0x4000),
        (frozen_v5.build_smooth, 0x4000, 0x5800),
        (frozen_v5.build_modal, 0x5800, RUNTIME_OFFSET),
    ), 0x100)


def _build_layout(slot_va: int, version: int, builders: tuple,
                  ensure_follow_offset: int) -> tuple[bytes, tuple, dict[str, int]]:
    state = _State(slot_va, ensure_follow_offset)
    payload = bytearray(WORLD_MAP_FOLLOW_SLOT_SIZE)
    payload[:len(MAGIC)] = MAGIC
    struct.pack_into("<I", payload, len(MAGIC), version)
    ranges = [(0, 0x100)]
    all_hooks = []
    for build, lower, upper in builders:
        chunks, hooks = build(slot_va, state)
        for offset, code in chunks.items():
            end = offset + len(code)
            if offset < lower or end > upper or any(offset < b and a < end for a, b in ranges):
                raise AssertionError(f"중앙 추적 코드 영역이 겹칩니다: 0x{offset:X}")
            ranges.append((offset, end))
            payload[offset:end] = code
        all_hooks.extend(hooks)
    all_hooks.sort(key=lambda hook: hook[0])
    for index, (va, original, replacement) in enumerate(all_hooks):
        if len(original) != len(replacement) or not original:
            raise AssertionError(f"잘못된 중앙 추적 훅 크기: 0x{va:X}")
        if index and all_hooks[index - 1][0] + len(all_hooks[index - 1][1]) > va:
            raise AssertionError(f"중앙 추적 훅 영역이 겹칩니다: 0x{va:X}")
    return bytes(payload), tuple(all_hooks), dict(state)


def _offsets(data: bytes | bytearray, hooks: tuple) -> list[int]:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        result = []
        for va, original, _replacement in hooks:
            offset = pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)
            if offset < 0 or offset + len(original) > len(data):
                raise ValueError(f"중앙 추적 코드 0x{va:X}이 파일 범위를 벗어납니다.")
            result.append(offset)
        return result
    finally:
        pe.close()


def _version_layout(slot_va: int, version: int) -> tuple[bytes, tuple, dict[str, int]]:
    if version == VERSION:
        return _layout(slot_va)
    if version == 1:
        return _legacy_layout(slot_va)
    if version == 2:
        return _legacy_v2_layout(slot_va)
    if version == 3:
        return _legacy_v3_layout(slot_va)
    if version == 4:
        return _legacy_v4_layout(slot_va)
    if version == 5:
        return _legacy_v5_layout(slot_va)
    raise ValueError(f"지원하지 않는 월드 지도 중앙 추적 패치 버전입니다: {version}")


def _validate_original_hooks(data: bytes | bytearray, slot_va: int) -> None:
    # A new version may add, remove, or resize hooks. Check every supported
    # version's original sites, including new sites before migrating a legacy
    # payload. Keep legacy-only sites even if a newer version retires them.
    hooks = tuple(hook for version in SUPPORTED_VERSIONS
                  for hook in _version_layout(slot_va, version)[1])
    for off, (va, original, _new) in zip(_offsets(data, hooks), hooks):
        if bytes(data[off:off + len(original)]) != original:
            raise ValueError(f"월드 지도 중앙 추적 원본 코드 0x{va:X}을 검증하지 못했습니다.")


def _validate_retired_hooks(data: bytes | bytearray, slot_va: int, hooks: tuple,
                            version: int) -> None:
    # Hooks that a newer version removes (or shortens) must have been restored.
    # Ignore bytes owned by a current hook, whose full replacement was checked.
    active_ranges = tuple((va, va + len(original)) for va, original, _new in hooks)
    # Validate only earlier versions here: reading an old version must not
    # depend on future sites. Check those before enabling or upgrading instead.
    old_hooks = tuple(hook for old_version in SUPPORTED_VERSIONS if old_version < version
                      for hook in _version_layout(slot_va, old_version)[1])
    if not old_hooks:
        return
    for off, (va, original, _new) in zip(_offsets(data, old_hooks), old_hooks):
        for index, value in enumerate(original):
            if not any(start <= va + index < end for start, end in active_ranges):
                if data[off + index] != value:
                    raise ValueError(f"이전 중앙 추적 훅 0x{va:X}이 복원되지 않았습니다.")


def _patch_version(data: bytes | bytearray) -> int:
    section = find_patch_section(data)
    slot = None
    if section is not None and min(section.virtual_size, section.raw_size) >= PATCH_SECTION_WORLD_MAP_FOLLOW_SIZE:
        slot = section.slot(WORLD_MAP_FOLLOW_SLOT_OFFSET, WORLD_MAP_FOLLOW_SLOT_SIZE)
    if slot is None or not any(data[slot[0]:slot[0] + WORLD_MAP_FOLLOW_SLOT_SIZE]):
        _validate_original_hooks(data, slot[1] if slot else 0x700000)
        return 0
    actual = bytes(data[slot[0]:slot[0] + WORLD_MAP_FOLLOW_SLOT_SIZE])
    if actual[:len(MAGIC)] != MAGIC:
        raise ValueError("월드 지도 중앙 추적 패치 식별자를 검증하지 못했습니다.")
    version = struct.unpack_from("<I", actual, len(MAGIC))[0]
    payload, hooks, _state = _version_layout(slot[1], version)
    if actual != payload:
        raise ValueError("월드 지도 중앙 추적 패치 데이터를 검증하지 못했습니다.")
    for off, (va, _old, new) in zip(_offsets(data, hooks), hooks):
        if bytes(data[off:off + len(new)]) != new:
            raise ValueError(f"월드 지도 중앙 추적 훅 0x{va:X}을 검증하지 못했습니다.")
    _validate_retired_hooks(data, slot[1], hooks, version)
    if not struct.unpack_from("<I", data, section.header_offset + 36)[0] & 0x80000000:
        raise ValueError("중앙 추적 실행 데이터의 쓰기 권한이 없습니다.")
    return version


def read_world_map_follow_patch_state(data: bytes | bytearray) -> bool:
    return _patch_version(data) != 0


def apply_world_map_follow_patch(data: bytearray, enabled: bool) -> bool:
    current = _patch_version(data)
    if current == (VERSION if enabled else 0):
        return False
    # Work on a copy so a failed validation never leaves a partially edited EXE.
    updated = bytearray(data)
    if current:
        section = find_patch_section(updated)
        assert section is not None
        _, va = section.slot(WORLD_MAP_FOLLOW_SLOT_OFFSET, WORLD_MAP_FOLLOW_SLOT_SIZE)
        _payload, old_hooks, _state = _version_layout(va, current)
        for off, (_va, original, _replacement) in zip(_offsets(updated, old_hooks), old_hooks):
            updated[off:off + len(original)] = original
        clear_slot(updated, section, WORLD_MAP_FOLLOW_SLOT_OFFSET, WORLD_MAP_FOLLOW_SLOT_SIZE)
    if enabled:
        section, _ = ensure_patch_section(updated, PATCH_SECTION_WORLD_MAP_FOLLOW_SIZE)
        offset, va = section.slot(WORLD_MAP_FOLLOW_SLOT_OFFSET, WORLD_MAP_FOLLOW_SLOT_SIZE)
        if any(updated[offset:offset + WORLD_MAP_FOLLOW_SLOT_SIZE]):
            raise ValueError("중앙 추적용 .patch 슬롯이 다른 데이터로 사용 중입니다.")
        _validate_original_hooks(updated, va)
        payload, hooks, _state = _layout(va)
        updated[offset:offset + len(payload)] = payload
        characteristics = struct.unpack_from("<I", updated, section.header_offset + 36)[0]
        struct.pack_into("<I", updated, section.header_offset + 36, characteristics | 0x80000000)
        for off, (_va, original, replacement) in zip(_offsets(updated, hooks), hooks):
            updated[off:off + len(original)] = replacement
    if _patch_version(updated) != (VERSION if enabled else 0):
        raise ValueError("월드 지도 중앙 추적 적용 결과를 검증하지 못했습니다.")
    data[:] = updated
    return True
