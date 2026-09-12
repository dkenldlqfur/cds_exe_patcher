"""Read CDS III portrait pixels directly from the target game archives.

``FEMALE.CDS`` and ``MALE.CDS`` contain one LS12-compressed, indexed 80×96
image per face code.  Their common display palette is retained separately in
``Resources/face_palette.png``; it is not copied from the selected game
installation.
"""

from __future__ import annotations

from pathlib import Path
import struct

from PIL import Image


PORTRAIT_SIZE = (80, 96)
PORTRAIT_PIXEL_COUNT = PORTRAIT_SIZE[0] * PORTRAIT_SIZE[1]


class PortraitReadError(ValueError):
    """The game portrait archive cannot be read safely."""


def _parse_ls12(data: bytes, archive_name: str) -> list[tuple[int, int, int]]:
    if data[:4] not in (b"Ls12", b"LS11"):
        raise PortraitReadError(f"{archive_name}이(가) LS12 아카이브가 아닙니다.")
    entries: list[tuple[int, int, int]] = []
    table_offset = 0x110
    while table_offset + 12 <= len(data):
        compressed_size, uncompressed_size, payload_offset = struct.unpack_from(
            ">III", data, table_offset,
        )
        if compressed_size == 0:
            break
        if payload_offset < 0x110 or payload_offset + compressed_size > len(data):
            raise PortraitReadError(f"{archive_name}의 얼굴 파트 범위가 손상되었습니다.")
        entries.append((compressed_size, uncompressed_size, payload_offset))
        table_offset += 12
    if not entries:
        raise PortraitReadError(f"{archive_name}에서 얼굴 파트를 찾지 못했습니다.")
    return entries


def _decode_ls12_part(data: bytes, entry: tuple[int, int, int]) -> bytes:
    compressed_size, uncompressed_size, payload_offset = entry
    source = data[payload_offset:payload_offset + compressed_size]
    if compressed_size == uncompressed_size:
        return source
    dictionary = data[0x10:0x110]
    if len(dictionary) != 0x100:
        raise PortraitReadError("LS12 사전 영역을 읽지 못했습니다.")
    output = bytearray(uncompressed_size)
    output_position = bit_position = distance = 0
    while output_position < uncompressed_size and bit_position < len(source) * 8:
        mask_length = 0
        while True:
            bit = (source[bit_position >> 3] >> (7 - (bit_position & 7))) & 1
            bit_position += 1
            mask_length += 1
            if bit == 0 or bit_position >= len(source) * 8 or mask_length >= 31:
                break
        if mask_length >= 31:
            break
        factor = 0
        for _ in range(mask_length):
            if bit_position >= len(source) * 8:
                break
            factor = (factor << 1) | (
                (source[bit_position >> 3] >> (7 - (bit_position & 7))) & 1
            )
            bit_position += 1
        code = ((1 << mask_length) - 2) + factor
        if distance:
            for _ in range(3 + code):
                if output_position >= uncompressed_size:
                    break
                output[output_position] = (
                    output[output_position - distance] if output_position >= distance else 0
                )
                output_position += 1
            distance = 0
        elif code < 256:
            output[output_position] = dictionary[code]
            output_position += 1
        else:
            distance = code - 256
    if output_position != uncompressed_size:
        raise PortraitReadError(
            f"LS12 압축 해제 실패: {output_position}/{uncompressed_size}바이트"
        )
    return bytes(output)


def portrait_count(executable_path: str | Path, *, female: bool) -> int:
    """Return the number of usable face codes in the selected game archive."""
    archive_name = "FEMALE.CDS" if female else "MALE.CDS"
    archive_path = Path(executable_path).resolve().parent / archive_name
    try:
        data = archive_path.read_bytes()
    except OSError as error:
        raise PortraitReadError(f"게임 폴더에서 {archive_name}을(를) 읽을 수 없습니다.") from error
    return len(_parse_ls12(data, archive_name))


def read_portrait(
    executable_path: str | Path,
    *,
    female: bool,
    face_code: int,
    palette_path: str | Path,
) -> Image.Image:
    """Return one indexed portrait decoded from the selected game's CDS file."""
    archive_name = "FEMALE.CDS" if female else "MALE.CDS"
    archive_path = Path(executable_path).resolve().parent / archive_name
    try:
        data = archive_path.read_bytes()
    except OSError as error:
        raise PortraitReadError(f"게임 폴더에서 {archive_name}을(를) 읽을 수 없습니다.") from error
    entries = _parse_ls12(data, archive_name)
    if not 0 <= face_code < len(entries):
        raise PortraitReadError(f"{archive_name}의 얼굴 코드는 0~{len(entries) - 1} 범위입니다.")
    pixels = _decode_ls12_part(data, entries[face_code])
    if len(pixels) != PORTRAIT_PIXEL_COUNT:
        raise PortraitReadError(f"얼굴 코드 {face_code}의 픽셀 크기가 올바르지 않습니다.")
    try:
        with Image.open(palette_path) as palette_image:
            palette = palette_image.getpalette()
    except OSError as error:
        raise PortraitReadError("내장 얼굴 팔레트를 읽을 수 없습니다.") from error
    if palette is None or len(palette) < 768:
        raise PortraitReadError("내장 얼굴 팔레트가 올바르지 않습니다.")
    portrait = Image.frombytes("P", PORTRAIT_SIZE, pixels)
    portrait.putpalette(palette[:768])
    return portrait
