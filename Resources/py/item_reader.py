"""Read original 120×120 item images from a CDS III ``ITEM.CDS`` archive."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from portrait_reader import PortraitReadError, _decode_ls12_part, _parse_ls12


ITEM_IMAGE_SIZE = (120, 120)
ITEM_PIXEL_COUNT = ITEM_IMAGE_SIZE[0] * ITEM_IMAGE_SIZE[1]
LOCAL_PALETTE_START = 160
LOCAL_PALETTE_COLORS = 86


class ItemImageReadError(ValueError):
    """The selected game's ITEM.CDS image cannot be read safely."""


def read_item_image(
    executable_path: str | Path,
    *,
    image_slot: int,
    palette_path: str | Path,
) -> Image.Image:
    """Decode one original-size image slot from ITEM.CDS.

    Item master records contain the image slot directly.  The companion palette
    part supplies 86 B/R/G entries, installed at global palette indices
    160~245; the remaining common indices use the shared game palette.
    """
    archive_path = Path(executable_path).resolve().parent / "ITEM.CDS"
    try:
        archive = archive_path.read_bytes()
    except OSError as error:
        raise ItemImageReadError("게임 폴더에서 ITEM.CDS을(를) 읽을 수 없습니다.") from error
    try:
        entries = _parse_ls12(archive, "ITEM.CDS")
    except PortraitReadError as error:
        raise ItemImageReadError(str(error)) from error
    if image_slot < 0 or image_slot * 2 + 1 >= len(entries):
        raise ItemImageReadError(f"ITEM.CDS 이미지 슬롯 {image_slot}번이 없습니다.")
    try:
        pixels = _decode_ls12_part(archive, entries[image_slot * 2])
        local_palette = _decode_ls12_part(archive, entries[image_slot * 2 + 1])
    except PortraitReadError as error:
        raise ItemImageReadError(str(error)) from error
    if len(pixels) != ITEM_PIXEL_COUNT or len(local_palette) != LOCAL_PALETTE_COLORS * 3:
        raise ItemImageReadError(f"ITEM.CDS 이미지 슬롯 {image_slot}번의 형식이 올바르지 않습니다.")
    try:
        with Image.open(palette_path) as palette_image:
            palette = palette_image.getpalette()
    except OSError as error:
        raise ItemImageReadError("내장 공통 팔레트를 읽을 수 없습니다.") from error
    if palette is None or len(palette) < 768:
        raise ItemImageReadError("내장 공통 팔레트가 올바르지 않습니다.")
    merged_palette = palette[:768]
    for color_index in range(LOCAL_PALETTE_COLORS):
        blue, red, green = local_palette[color_index * 3:color_index * 3 + 3]
        start = (LOCAL_PALETTE_START + color_index) * 3
        merged_palette[start:start + 3] = (red, green, blue)
    image = Image.frombytes("P", ITEM_IMAGE_SIZE, pixels)
    image.putpalette(merged_palette)
    return image
