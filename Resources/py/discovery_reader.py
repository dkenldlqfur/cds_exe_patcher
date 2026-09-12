"""Read original static discovery images from ``DSTILL.CDS``."""

from __future__ import annotations

from pathlib import Path
import struct

from PIL import Image

from portrait_reader import PortraitReadError, _decode_ls12_part, _parse_ls12


LOCAL_PALETTE_START = 160
LOCAL_PALETTE_COLORS = 86


class DiscoveryImageReadError(ValueError):
    """The selected game's DSTILL image cannot be read safely."""


def discovery_still_count(executable_path: str | Path) -> int:
    """Return the number of complete three-part DSTILL image slots."""
    archive_path = Path(executable_path).resolve().parent / "DSTILL.CDS"
    try:
        archive = archive_path.read_bytes()
    except OSError as error:
        raise DiscoveryImageReadError("게임 폴더에서 DSTILL.CDS을(를) 읽을 수 없습니다.") from error
    try:
        entries = _parse_ls12(archive, "DSTILL.CDS")
    except PortraitReadError as error:
        raise DiscoveryImageReadError(str(error)) from error
    if len(entries) % 3:
        raise DiscoveryImageReadError("DSTILL.CDS의 이미지 파트 구성이 올바르지 않습니다.")
    return len(entries) // 3


def read_discovery_still(
    executable_path: str | Path,
    *,
    image_slot: int,
    palette_path: str | Path,
) -> Image.Image:
    """Decode a single original-size pixel/palette/size DSTILL slot."""
    archive_path = Path(executable_path).resolve().parent / "DSTILL.CDS"
    try:
        archive = archive_path.read_bytes()
    except OSError as error:
        raise DiscoveryImageReadError("게임 폴더에서 DSTILL.CDS을(를) 읽을 수 없습니다.") from error
    try:
        entries = _parse_ls12(archive, "DSTILL.CDS")
    except PortraitReadError as error:
        raise DiscoveryImageReadError(str(error)) from error
    base_part = image_slot * 3
    if image_slot < 0 or base_part + 2 >= len(entries):
        raise DiscoveryImageReadError(f"DSTILL.CDS 이미지 슬롯 {image_slot}번이 없습니다.")
    try:
        pixels = _decode_ls12_part(archive, entries[base_part])
        local_palette = _decode_ls12_part(archive, entries[base_part + 1])
        size = _decode_ls12_part(archive, entries[base_part + 2])
    except PortraitReadError as error:
        raise DiscoveryImageReadError(str(error)) from error
    if len(local_palette) != LOCAL_PALETTE_COLORS * 3 or len(size) != 8:
        raise DiscoveryImageReadError(f"DSTILL.CDS 이미지 슬롯 {image_slot}번의 형식이 올바르지 않습니다.")
    width, height = struct.unpack("<II", size)
    if not width or not height or len(pixels) != width * height:
        raise DiscoveryImageReadError(f"DSTILL.CDS 이미지 슬롯 {image_slot}번의 크기가 올바르지 않습니다.")
    try:
        with Image.open(palette_path) as palette_image:
            palette = palette_image.getpalette()
    except OSError as error:
        raise DiscoveryImageReadError("내장 공통 팔레트를 읽을 수 없습니다.") from error
    if palette is None or len(palette) < 768:
        raise DiscoveryImageReadError("내장 공통 팔레트가 올바르지 않습니다.")
    merged_palette = palette[:768]
    for color_index in range(LOCAL_PALETTE_COLORS):
        blue, red, green = local_palette[color_index * 3:color_index * 3 + 3]
        start = (LOCAL_PALETTE_START + color_index) * 3
        merged_palette[start:start + 3] = (red, green, blue)
    image = Image.frombytes("P", (width, height), pixels)
    image.putpalette(merged_palette)
    return image
