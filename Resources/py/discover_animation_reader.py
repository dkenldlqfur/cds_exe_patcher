"""Decode original discovery animations from ``DISCOVER.CDS``."""

from __future__ import annotations

from pathlib import Path

import pefile
from PIL import Image

from portrait_reader import PortraitReadError, _decode_ls12_part, _parse_ls12


# ``240 * 176`` and ``220 * 192`` both equal 42,240.  The archived frame
# byte count alone cannot distinguish them; the original conversion and the
# game's output confirm that DISCOVER frames are 240×176.
FRAME_WIDTH = 240
FRAME_HEIGHT = 176
FRAME_SIZE = FRAME_WIDTH * FRAME_HEIGHT
LOCAL_PALETTE_START = 160
LOCAL_PALETTE_COLORS = 86
PALETTE_SIZE = LOCAL_PALETTE_COLORS * 3
# The game's 64-colour common CG palette is embedded in the executable.  It
# occupies indices 10..73; every other non-local index is intentionally kept
# as a grayscale fallback because its source is not established yet.
EXE_COMMON_PALETTE_VA = 0x4FFDD8
EXE_COMMON_PALETTE_START = 10
EXE_COMMON_PALETTE_COLORS = 64
EXE_COMMON_PALETTE_SIZE = EXE_COMMON_PALETTE_COLORS * 3


class DiscoverAnimationReadError(ValueError):
    """The selected game's DISCOVER animation cannot be decoded safely."""


def discover_animation_count(executable_path: str | Path) -> int:
    """Return the number of original DISCOVER.CDS animation parts."""
    archive, entries = _load_archive(executable_path)
    del archive
    return len(entries)


def read_discover_animation(
    executable_path: str | Path,
    *,
    animation_part: int,
) -> tuple[Image.Image, ...]:
    """Decode every indexed 240×176 frame in one DISCOVER animation part."""
    archive, entries = _load_archive(executable_path)
    if not 0 <= animation_part < len(entries):
        raise DiscoverAnimationReadError(f"DISCOVER.CDS 애니메이션 파트 {animation_part}번이 없습니다.")
    try:
        payload = _decode_ls12_part(archive, entries[animation_part])
    except PortraitReadError as error:
        raise DiscoverAnimationReadError(str(error)) from error
    if len(payload) <= PALETTE_SIZE or (len(payload) - PALETTE_SIZE) % FRAME_SIZE:
        raise DiscoverAnimationReadError(
            f"DISCOVER.CDS 애니메이션 파트 {animation_part}번의 프레임 구성이 올바르지 않습니다."
        )
    palette = _merged_palette(payload[:PALETTE_SIZE], executable_path)
    frames: list[Image.Image] = []
    for offset in range(PALETTE_SIZE, len(payload), FRAME_SIZE):
        frame = Image.frombytes("P", (FRAME_WIDTH, FRAME_HEIGHT), payload[offset:offset + FRAME_SIZE])
        frame.putpalette(palette)
        frames.append(frame)
    return tuple(frames)


def _load_archive(executable_path: str | Path) -> tuple[bytes, tuple]:
    archive_path = Path(executable_path).resolve().parent / "DISCOVER.CDS"
    try:
        archive = archive_path.read_bytes()
    except OSError as error:
        raise DiscoverAnimationReadError("게임 폴더에서 DISCOVER.CDS을(를) 읽을 수 없습니다.") from error
    try:
        return archive, _parse_ls12(archive, "DISCOVER.CDS")
    except PortraitReadError as error:
        raise DiscoverAnimationReadError(str(error)) from error


def _merged_palette(local_palette: bytes, executable_path: str | Path) -> list[int]:
    if len(local_palette) != PALETTE_SIZE:
        raise DiscoverAnimationReadError("DISCOVER.CDS 팔레트 크기가 올바르지 않습니다.")
    try:
        executable = Path(executable_path).resolve(strict=True).read_bytes()
        pe = pefile.PE(data=executable, fast_load=True)
        try:
            palette_offset = pe.get_offset_from_rva(EXE_COMMON_PALETTE_VA - pe.OPTIONAL_HEADER.ImageBase)
        finally:
            pe.close()
    except (OSError, pefile.PEFormatError) as error:
        raise DiscoverAnimationReadError("EXE 공용 CG 팔레트를 읽을 수 없습니다.") from error
    common_palette = executable[palette_offset:palette_offset + EXE_COMMON_PALETTE_SIZE]
    if len(common_palette) != EXE_COMMON_PALETTE_SIZE:
        raise DiscoverAnimationReadError("EXE 공용 CG 팔레트의 범위가 올바르지 않습니다.")
    # Unknown common slots use their index value, as in the prior converter.
    merged = [value for index in range(256) for value in (index, index, index)]
    for index in range(EXE_COMMON_PALETTE_COLORS):
        blue, red, green = common_palette[index * 3:index * 3 + 3]
        start = (EXE_COMMON_PALETTE_START + index) * 3
        merged[start:start + 3] = (red, green, blue)
    for index in range(LOCAL_PALETTE_COLORS):
        # DISCOVER's embedded palette, like the EXE common palette, is B/R/G.
        blue, red, green = local_palette[index * 3:index * 3 + 3]
        start = (LOCAL_PALETTE_START + index) * 3
        merged[start:start + 3] = (red, green, blue)
    return merged
