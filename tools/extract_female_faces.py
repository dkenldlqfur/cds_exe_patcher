"""Extract every 80×96 female face in FEMALE.CDS into indexed PNG files."""

from __future__ import annotations

import argparse
from pathlib import Path
import struct

from PIL import Image


FACE_SIZE = (80, 96)
PIXEL_SIZE = FACE_SIZE[0] * FACE_SIZE[1]


def _parse_ls12(data: bytes) -> list[tuple[int, int, int]]:
    if data[:4] not in (b"Ls12", b"LS11"):
        raise ValueError("FEMALE.CDS가 LS12/LS11 아카이브가 아닙니다.")
    entries: list[tuple[int, int, int]] = []
    offset = 0x110
    while offset + 12 <= len(data):
        compressed, uncompressed, payload = struct.unpack_from(">III", data, offset)
        if compressed == 0:
            break
        if payload < 0x110 or payload + compressed > len(data):
            raise ValueError(f"얼굴 파트 {len(entries)}의 데이터 범위가 손상되었습니다.")
        entries.append((compressed, uncompressed, payload))
        offset += 12
    return entries


def _decode_part(data: bytes, entry: tuple[int, int, int], dictionary: bytes) -> bytes:
    compressed, uncompressed, payload = entry
    source = data[payload:payload + compressed]
    if compressed == uncompressed:
        return source
    output = bytearray(uncompressed)
    output_position = bit_position = distance = 0
    while output_position < uncompressed and bit_position < len(source) * 8:
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
            factor = (factor << 1) | ((source[bit_position >> 3] >> (7 - (bit_position & 7))) & 1)
            bit_position += 1
        code = ((1 << mask_length) - 2) + factor
        if distance:
            for _ in range(3 + code):
                if output_position >= uncompressed:
                    break
                output[output_position] = output[output_position - distance] if output_position >= distance else 0
                output_position += 1
            distance = 0
        elif code < 256:
            output[output_position] = dictionary[code]
            output_position += 1
        else:
            distance = code - 256
    if output_position != uncompressed:
        raise ValueError(f"LS12 압축 해제 실패: {output_position}/{uncompressed}바이트")
    return bytes(output)


def extract(source: Path, destination: Path, palette_source: Path, prefix: str) -> None:
    data = source.read_bytes()
    entries = _parse_ls12(data)
    if not entries:
        raise ValueError("얼굴 파트를 찾지 못했습니다.")
    with Image.open(palette_source) as palette_image:
        palette = palette_image.getpalette()
    if palette is None:
        raise ValueError("팔레트 원본 PNG에 인덱스 팔레트가 없습니다.")
    destination.mkdir(parents=True, exist_ok=True)
    dictionary = data[0x10:0x110]
    for code, entry in enumerate(entries):
        pixels = _decode_part(data, entry, dictionary)
        if len(pixels) != PIXEL_SIZE:
            raise ValueError(f"얼굴 코드 {code}의 픽셀 크기가 예상과 다릅니다.")
        image = Image.frombytes("P", FACE_SIZE, pixels)
        image.putpalette(palette)
        image.save(destination / f"{prefix}_{code:03d}.png")
    print(f"{len(entries)} portraits extracted to {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("palette_source", type=Path)
    parser.add_argument("--prefix", default=None, help="output filename prefix (defaults to source name)")
    args = parser.parse_args()
    extract(args.source, args.destination, args.palette_source, args.prefix or args.source.stem.lower())
