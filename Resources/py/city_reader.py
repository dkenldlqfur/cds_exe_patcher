"""Decode original-size CDS III city artwork from ``CITYCG.CDS``.

Each city owns a pair of LS12 parts: its 400×320 indexed pixels followed by
an 86-colour local palette.  The remaining common palette entries come from
the selected game's executable, so the preview always follows that install.
"""

from __future__ import annotations

from pathlib import Path

import pefile
from PIL import Image

from portrait_reader import PortraitReadError, _decode_ls12_part, _parse_ls12


CITY_IMAGE_SIZE = (400, 320)
EXE_COMMON_PALETTE_VA = 0x4FFDD8
EXE_COMMON_PALETTE_START = 10
EXE_COMMON_PALETTE_COLORS = 64
LOCAL_PALETTE_START = 74
LOCAL_PALETTE_COLORS = 86


class CityImageReadError(ValueError):
    """The selected game's city artwork cannot be decoded safely."""


def _read_common_palette(executable_path: Path) -> list[int]:
    try:
        data = executable_path.read_bytes()
    except OSError as error:
        raise CityImageReadError("대상 실행 파일의 공용 팔레트를 읽을 수 없습니다.") from error
    pe = pefile.PE(data=data, fast_load=True)
    try:
        offset = pe.get_offset_from_rva(EXE_COMMON_PALETTE_VA - pe.OPTIONAL_HEADER.ImageBase)
    except pefile.PEFormatError as error:
        raise CityImageReadError("실행 파일의 공용 팔레트 주소를 검증하지 못했습니다.") from error
    finally:
        pe.close()
    raw_size = EXE_COMMON_PALETTE_COLORS * 3
    raw = data[offset:offset + raw_size]
    if len(raw) != raw_size:
        raise CityImageReadError("실행 파일의 공용 팔레트 크기가 올바르지 않습니다.")
    palette = [component for index in range(256) for component in (index, index, index)]
    for color_index in range(EXE_COMMON_PALETTE_COLORS):
        blue, red, green = raw[color_index * 3:color_index * 3 + 3]
        start = (EXE_COMMON_PALETTE_START + color_index) * 3
        palette[start:start + 3] = (red, green, blue)
    return palette


def read_city_image(executable_path: str | Path, *, city_id: int) -> Image.Image:
    """Return the selected city's unscaled 400×320 indexed artwork."""
    executable = Path(executable_path).resolve()
    archive_path = executable.parent / "CITYCG.CDS"
    try:
        archive = archive_path.read_bytes()
    except OSError as error:
        raise CityImageReadError("게임 폴더에서 CITYCG.CDS을(를) 읽을 수 없습니다.") from error
    try:
        entries = _parse_ls12(archive, "CITYCG.CDS")
    except PortraitReadError as error:
        raise CityImageReadError(str(error)) from error
    part_index = city_id * 2
    if city_id < 0 or part_index + 1 >= len(entries):
        raise CityImageReadError(f"CITYCG.CDS에 도시 {city_id}번 이미지가 없습니다.")
    try:
        pixels = _decode_ls12_part(archive, entries[part_index])
        local_palette = _decode_ls12_part(archive, entries[part_index + 1])
    except PortraitReadError as error:
        raise CityImageReadError(str(error)) from error
    if len(pixels) != CITY_IMAGE_SIZE[0] * CITY_IMAGE_SIZE[1]:
        raise CityImageReadError(f"도시 {city_id}번 이미지의 크기가 올바르지 않습니다.")
    if len(local_palette) != LOCAL_PALETTE_COLORS * 3:
        raise CityImageReadError(f"도시 {city_id}번 이미지 팔레트의 크기가 올바르지 않습니다.")
    palette = _read_common_palette(executable)
    for color_index in range(LOCAL_PALETTE_COLORS):
        blue, red, green = local_palette[color_index * 3:color_index * 3 + 3]
        start = (LOCAL_PALETTE_START + color_index) * 3
        palette[start:start + 3] = (red, green, blue)
    image = Image.frombytes("P", CITY_IMAGE_SIZE, pixels)
    image.putpalette(palette)
    return image
