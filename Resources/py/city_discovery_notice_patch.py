"""Centre and colour city/current-nation labels; migrate legacy popups away."""

from __future__ import annotations

import struct

import pefile

from pe_patch_section import (
    CITY_DISCOVERY_NOTICE_SLOT_OFFSET,
    CITY_DISCOVERY_NOTICE_SLOT_SIZE,
    PATCH_SECTION_CITY_DISCOVERY_NOTICE_SIZE,
    clear_slot,
    ensure_patch_section,
    find_patch_section,
)


MAGIC = b"CDSCTN1\0"
VERSION = 8
COLORS_OFFSET = 0x10
OUTLINE_SETTINGS_OFFSET = 0x16
SHOW_NATION_OFFSET = 0x18
DEFAULT_SHOW_NATION = True
DEFAULT_LABEL_OUTLINE = (1, 73)  # thickness in pixels, common-palette colour
MIN_OUTLINE_WIDTH = 0
MAX_OUTLINE_WIDTH = 3
RELATION_OFFSET = 0x480
OUTLINE_STROKES_OFFSET = 0x400
OUTLINE_TEXT_OFFSET = 0x500
# A 1px four-neighbour outline around the original two-stroke bold text.
# Paint the whole border first, then restore both coloured foreground strokes.
OUTLINE_STROKES = ((-1, 0), (2, 0), (0, -1), (1, -1), (0, 1), (1, 1), (0, 0), (1, 0))
# Nation own/friendly/enemy, then city own/friendly/enemy. Keep the old white
# appearance until the user explicitly chooses other colours.
DEFAULT_LABEL_COLORS = (10, 10, 10, 10, 10, 10)
PALETTE_VA = 0x4FFDD8
TREATY_TEST_VA = 0x469880
TREATY_TEST_CODE = bytes.fromhex(
    "81 3D 20 4D 5A 00 D6 05 00 00 7C 25 8B 44 24 04 85 C0 75 09 "
    "83 3D 4C 39 5B 00 01 74 0E 83 F8 01 75 0F 83 3D 4C 39 5B 00 00 "
    "75 06 B8 01 00 00 00 C3 33 C0 C3"
)
PIXEL_TEXT_OFFSET = 0x20
PIXEL_TEXT_VA = 0x406800
NATION_TABLE_VA = 0x4CA370
NATION_COUNT = 78
LEGACY_SLOT_SIZE = 0x200
SPEECH_WRAPPER_OFFSET = 0x20
MESSAGE_WRAPPER_OFFSET = 0x60
NOTICE_OFFSET = 0xA0
FORMAT_OFFSET = 0x100
MAP_WRAPPER_OFFSET = 0x140
MAP_LABELS_OFFSET = 0x180
MAP_HOOK_VA = 0x48AB38
MAP_ORIGINAL = bytes.fromhex("8B 4C 24 14 F6 81 A0 00 00 00 01")
MAP_RESUME_VA = MAP_HOOK_VA + len(MAP_ORIGINAL)
MAP_TEXT_VA = 0x426860
CITY_COUNT = 226
NOTICE_FORMAT = "%s%s 발견했다!".encode("cp949") + b"\0"
CITY_OBJECT_VA = 0x429970
CITY_MASTER_VA = 0x429980
OBJECT_PARTICLE_VA = 0x4281B0
COMMON_POPUP_VA = 0x49E3E0
SPEECH_POPUP_VA = 0x478280
# Legacy v1/v2 redirected these successful discovery calls. Keep their exact
# payload format solely to validate and restore previously patched EXEs.
HOOKS = (
    (0x48D9FE, SPEECH_POPUP_VA, SPEECH_WRAPPER_OFFSET, 4),
    (0x48DA11, COMMON_POPUP_VA, MESSAGE_WRAPPER_OFFSET, 3),
)
CONTEXTS = (
    (0x48D916, bytes.fromhex("8B E8 83 FD FF")),  # EBP is the discovered city ID
    (0x48D9FD, b"\x50"),  # original speech actor argument
    (0x48DA03, bytes.fromhex("83 C4 10 EB 11")),
    (0x48DA08, bytes.fromhex("68 50 08 57 00 6A 00 6A 00")),
    (0x48DA16, bytes.fromhex("83 C4 0C")),
    (MAP_RESUME_VA, bytes.fromhex("74 2B")),
)


def _call(source_va: int, target_va: int) -> bytes:
    return b"\xE8" + struct.pack("<i", target_va - source_va - 5)


def _popup_payload(slot_va: int, version: int) -> bytes:
    """Legacy popup code, used only for old-patch validation and migration."""
    payload = bytearray(LEGACY_SLOT_SIZE)
    payload[:len(MAGIC)] = MAGIC
    struct.pack_into("<I", payload, len(MAGIC), version)

    for _, original_target, wrapper_offset, argument_count in HOOKS:
        wrapper_va = slot_va + wrapper_offset
        # A wrapper CALL adds its own return address. Forward the original
        # arguments by pushing the same stack offset repeatedly: each PUSH
        # brings the preceding original argument into that position.
        code = bytearray(bytes((0xFF, 0x74, 0x24, argument_count * 4)) * argument_count)
        code += _call(wrapper_va + len(code), original_target)
        code += bytes((0x83, 0xC4, argument_count * 4))
        # Keep the original popup's result, caller-saved registers and flags
        # intact while displaying the extra notice. Original caller cleanup
        # remains in .text and runs once after this wrapper returns.
        code += bytes.fromhex("9C 60")  # pushfd; pushad
        code += _call(wrapper_va + len(code), slot_va + NOTICE_OFFSET)
        code += bytes.fromhex("61 9D C3")  # popad; popfd; ret
        if wrapper_offset + len(code) > NOTICE_OFFSET:
            raise AssertionError("도시 발견 알림 래퍼가 예약 공간을 초과했습니다.")
        payload[wrapper_offset:wrapper_offset + len(code)] = code

    code = bytearray(b"\x55")  # push ebp (city ID)

    def call(target: int) -> None:
        code.extend(_call(slot_va + NOTICE_OFFSET + len(code), target))

    call(CITY_OBJECT_VA)
    code += bytes.fromhex("83 C4 04 8B C8")  # caller cleanup; mov ecx, eax
    call(CITY_MASTER_VA)
    code += bytes.fromhex("8B 30 6A 02 56")  # mov esi,[eax] (current name); push 2; push esi
    call(OBJECT_PARTICLE_VA)  # game's Korean object-particle selector (을/를)
    code += bytes.fromhex("83 C4 08 50 56")  # cleanup; push particle; push name
    code += b"\x68" + struct.pack("<I", slot_va + FORMAT_OFFSET)
    code += bytes.fromhex("6A 00 6A 00")
    call(COMMON_POPUP_VA)
    code += bytes.fromhex("83 C4 14 C3")  # five cdecl arguments; ret
    if NOTICE_OFFSET + len(code) > FORMAT_OFFSET:
        raise AssertionError("도시 발견 알림 코드가 문자열 영역을 침범했습니다.")
    payload[NOTICE_OFFSET:NOTICE_OFFSET + len(code)] = code
    payload[FORMAT_OFFSET:FORMAT_OFFSET + len(NOTICE_FORMAT)] = NOTICE_FORMAT
    return bytes(payload)


def _jump(source_va: int, target_va: int) -> bytes:
    return b"\xE9" + struct.pack("<i", target_va - source_va - 5)


def _map_hook(slot_va: int) -> bytes:
    return _jump(MAP_HOOK_VA, slot_va + MAP_WRAPPER_OFFSET) + b"\x90" * (len(MAP_ORIGINAL) - 5)


def _legacy_map_labels_code(code_va: int) -> bytes:
    """Native thiscall(view): label visible cities and invalidate covered rows.

    Reuse 0x426860, including its viewport origin and font outline. Both world
    axes are in 16-pixel tiles; X wraps at 2500. The city's actual tile footprint
    determines visibility. Text is centred above it, clamped into the viewport.
    Locals: row, column, name width, then first/end dirty row (all tile units).
    """
    code = bytearray()
    labels: dict[str, int] = {}
    fixups: list[tuple[int, str]] = []

    def emit(value: str) -> None:
        code.extend(bytes.fromhex(value))

    def label(name: str) -> None:
        labels[name] = len(code)

    def branch(opcode: str, target: str) -> None:
        emit(opcode)
        fixups.append((len(code), target))
        code.extend(b"\0" * 4)

    def call(target: int) -> None:
        code.extend(_call(code_va + len(code), target))

    emit("55 53 56 57 83 EC 14 8B E9 31 DB")  # save nonvolatile; EBP=view; EBX=id
    emit("83 BD EC 00 00 00 00")
    branch("0F 8E", "done")
    emit("83 BD F0 00 00 00 00")
    branch("0F 8E", "done")
    label("city")
    emit("53")
    call(CITY_OBJECT_VA)
    emit("83 C4 04 F6 40 04 01")
    branch("0F 84", "next")  # undiscovered
    emit("F6 40 04 04")
    branch("0F 85", "next")  # city hidden (same test as original map)
    emit("8B C8")
    call(CITY_MASTER_VA)
    emit("8B F0 8B 3E 85 FF")
    branch("0F 84", "next")
    emit("80 3F 00")
    branch("0F 84", "next")
    emit("8B 56 04 2B 15 A8 63 5B 00 85 D2")  # relative tile X
    branch("0F 89", "positive_x")
    emit("81 C2 C4 09 00 00")
    label("positive_x")
    emit("81 FA C4 09 00 00")
    branch("0F 8C", "wrapped_x")
    emit("81 EA C4 09 00 00")
    label("wrapped_x")
    emit("B8 C4 09 00 00 2B 46 0C 3B D0")
    branch("0F 8C", "visible_x")
    emit("81 EA C4 09 00 00")  # city partly beyond left edge after wrap
    label("visible_x")
    emit("3B 95 EC 00 00 00")
    branch("0F 8D", "next")
    emit("8B C2 03 46 0C 85 C0")
    branch("0F 8E", "next")
    emit("89 54 24 04 8B 46 08 2B 05 AC 63 5B 00")
    emit("3B 85 F0 00 00 00")
    branch("0F 8D", "next")
    emit("8B C8 03 4E 0C 85 C9")
    branch("0F 8E", "next")
    emit("48 85 C0")  # label one tile above the city
    branch("0F 89", "label_row")
    emit("31 C0")
    label("label_row")
    emit("89 04 24 B9 FF FF FF FF 31 C0 FC F2 AE F7 D1 49 D1 E9 41")
    # CP949 characters use 8 pixels per byte; reserve an extra tile for outline.
    emit("89 4C 24 08 3B 8D EC 00 00 00")
    branch("0F 8F", "next")  # do not spill an overlong label into adjacent UI
    emit("8B 46 0C 29 C8 D1 F8 03 44 24 04 85 C0")
    branch("0F 89", "positive_col")
    emit("31 C0")
    label("positive_col")
    emit("8B 95 EC 00 00 00 29 CA 85 D2")
    branch("0F 89", "column_limit")
    emit("31 D2")
    label("column_limit")
    emit("3B C2")
    branch("0F 8E", "label_col")
    emit("8B C2")
    label("label_col")
    emit("89 44 24 04 FF 36 FF 74 24 04 50 8B CD")  # push name,row,column
    call(MAP_TEXT_VA)  # thiscall, callee removes 12 argument bytes
    # Text crosses tile boundaries by 1px. Invalidate neighbouring cache rows
    # too so incremental terrain updates erase its outline after scrolling.
    emit("8B 04 24 48 85 C0")
    branch("0F 89", "dirty_start")
    emit("31 C0")
    label("dirty_start")
    emit("89 44 24 0C 8B 04 24 83 C0 02 3B 85 F0 00 00 00")
    branch("0F 8E", "dirty_end")
    emit("8B 85 F0 00 00 00")
    label("dirty_end")
    emit("89 44 24 10 6A 04 6A 00 6A 00 8D 8D C4 00 00 00")
    call(0x4B6637)  # first byte of the view's u16 tile cache, callee ret 12
    emit("85 C0")
    branch("0F 84", "next")
    emit("8B 4C 24 0C 0F AF 8D EC 00 00 00 8D 3C 48")
    emit("8B 4C 24 10 2B 4C 24 0C 0F AF 8D EC 00 00 00")
    emit("B8 FF FF 00 00 FC F3 66 AB")  # REP STOSW, only valid cache rows
    label("next")
    emit("43 81 FB E2 00 00 00")
    branch("0F 8C", "city")
    label("done")
    emit("83 C4 14 5F 5E 5B 5D C3")
    for offset, target in fixups:
        struct.pack_into("<i", code, offset, labels[target] - offset - 4)
    return bytes(code)


class _Code:
    def __init__(self, va: int):
        self.va = va
        self.code = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str]] = []

    def emit(self, value: str) -> None:
        self.code.extend(bytes.fromhex(value))

    def label(self, name: str) -> None:
        self.labels[name] = len(self.code)

    def branch(self, opcode: str, name: str) -> None:
        self.emit(opcode)
        self.fixups.append((len(self.code), name))
        self.code.extend(b"\0" * 4)

    def call(self, target: int) -> None:
        self.code.extend(_call(self.va + len(self.code), target))

    def finish(self) -> bytes:
        for offset, name in self.fixups:
            struct.pack_into("<i", self.code, offset, self.labels[name] - offset - 4)
        return bytes(self.code)


def _pixel_text_code(code_va: int, colored: bool = False,
                     outline_va: int | None = None,
                     outline_width: int | None = None) -> bytes:
    """thiscall(view, centreX, pixelY, text[, color]), exact CP949 centring.

    Preserve the viewport drawing origin like 0x426860. v6+ use a private
    outline renderer; older versions use 0x406800. v7 supplies a configurable
    outline width, including zero for no border. Reserve an
    additional pixel for the original bold text's second foreground stroke.
    Colour-aware helpers return with ret 16; byte-exact v4 keeps ret 12.
    """
    c = _Code(code_va)
    e, b, l = c.emit, c.branch, c.label
    e("56 57 53 55 83 EC 10 8B F1 8B 7C 24 2C")
    e("B9 FF FF FF FF 31 C0 FC F2 AE F7 D1 49 C1 E1 03 8B D9")
    e("8B 96 EC 00 00 00 C1 E2 04")
    if outline_width is None:
        e("83 EA 03" if outline_va is not None else "83 EA 02")
    else:
        e(f"83 EA {1 + 2 * outline_width:02X}")
    e("3B DA")
    b("0F 8F", "done")
    margin = 1 if outline_width is None else outline_width
    e(f"8B 6C 24 24 8B C3 D1 E8 2B E8 83 FD {margin:02X}")
    b("0F 8D", "left_ok")
    e("BD" + struct.pack("<I", margin).hex())
    l("left_ok")
    e("42" if outline_width is None else f"83 C2 {outline_width:02X}")
    e("2B D3 3B EA")
    b("0F 8E", "right_ok")
    e("8B EA")
    l("right_ok")
    e("A1 C8 B2 62 00 89 04 24 A1 CC B2 62 00 89 44 24 04")
    e("8B 46 28 03 46 54 89 44 24 08 8B 46 2C 03 46 58 89 44 24 0C")
    e("8D 44 24 08 50 B9 F0 B2 62 00")
    c.call(0x4B5B77)
    e("FF 74 24 30" if colored else "6A 0A")  # fourth argument or legacy white
    e("FF 74 24 30 FF 74 24 30 55")  # text,Y,X
    c.call(PIXEL_TEXT_VA if outline_va is None else outline_va)
    e("83 C4 10 8D 04 24 50 B9 F0 B2 62 00")
    c.call(0x4B5B77)
    l("done")
    e("83 C4 10 5D 5B 5F 5E")
    e("C2 10 00" if colored else "C2 0C 00")
    return c.finish()


def _map_labels_code(code_va: int, pixel_text_va: int,
                     relation_va: int | None = None, colors_va: int = 0,
                     outlined: bool = False, outline_width: int | None = None,
                     show_nation: bool = DEFAULT_SHOW_NATION) -> bytes:
    """City name plus [current nation], individually centred in pixels.

    Locals: city Y, centre X, current nation ID, first/end dirty row, relation.
    +0x20 holds a bounded 128-byte nation label (including brackets and NUL).
    """
    c = _Code(code_va)
    e, b, l = c.emit, c.branch, c.label
    e("55 53 56 57 81 EC A0 00 00 00 8B E9 31 DB")
    e("83 BD EC 00 00 00 00")
    b("0F 8E", "done")
    e("83 BD F0 00 00 00 03")
    b("0F 8C", "done")
    l("city")
    e("53")
    c.call(CITY_OBJECT_VA)
    e("83 C4 04 F6 40 04 01")
    b("0F 84", "next")
    e("F6 40 04 04")
    b("0F 85", "next")
    e("8B 08 89 4C 24 08 8B C8")  # runtime city +0, not master +24
    c.call(CITY_MASTER_VA)
    e("8B F0 8B 3E 85 FF")
    b("0F 84", "next")
    e("80 3F 00")
    b("0F 84", "next")
    e("8B 56 04 2B 15 A8 63 5B 00 85 D2")
    b("0F 89", "positive_x")
    e("81 C2 C4 09 00 00")
    l("positive_x")
    e("81 FA C4 09 00 00")
    b("0F 8C", "wrapped_x")
    e("81 EA C4 09 00 00")
    l("wrapped_x")
    e("B8 C4 09 00 00 2B 46 0C 3B D0")
    b("0F 8C", "visible_x")
    e("81 EA C4 09 00 00")
    l("visible_x")
    e("3B 95 EC 00 00 00")
    b("0F 8D", "next")
    e("8B C2 03 46 0C 85 C0")
    b("0F 8E", "next")
    e("C1 E2 04 8B 46 0C C1 E0 03 03 C2 89 44 24 04")
    e("8B 46 08 2B 05 AC 63 5B 00 3B 85 F0 00 00 00")
    b("0F 8D", "next")
    e("8B C8 03 4E 0C 85 C9")
    b("0F 8E", "next")
    e("48 C1 E0 04")
    top_margin = (19 if outlined else 17) if outline_width is None else 16 + 3 * outline_width
    if not show_nation and outline_width is not None:
        top_margin = outline_width  # only the city's own upper border needs room
    bottom_margin = 17 if outline_width is None else 16 + outline_width
    line_spacing = (18 if outlined else 16) if outline_width is None else 16 + 2 * outline_width
    e(f"83 F8 {top_margin:02X}")
    b("0F 8D", "top_ok")
    e("B8" + struct.pack("<I", top_margin).hex())
    l("top_ok")
    e(f"8B 95 F0 00 00 00 C1 E2 04 83 EA {bottom_margin:02X} 3B C2")
    b("0F 8E", "bottom_ok")
    e("8B C2")
    l("bottom_ok")
    e("89 04 24")
    if relation_va is None:
        e("FF 36 FF 74 24 04 FF 74 24 0C 8B CD")
    else:
        e("8B 4C 24 08")  # ECX=current nation, not the city's initial master nation
        c.call(relation_va)
        e("89 44 24 14 0F B6 80")  # save relation; city colour table
        e(struct.pack("<I", colors_va + 3).hex())
        e("50 FF 36 FF 74 24 08 FF 74 24 10 8B CD")
    c.call(pixel_text_va)
    if not show_nation:
        b("E9", "dirty")  # preserve city relationship colours, omit only the nation row
    e("8B 44 24 08 83 F8 4E")
    b("0F 83", "dirty")  # negative or out-of-range nation: city only
    e("6B C0 18 8B 80 70 A3 4C 00 85 C0")
    b("0F 84", "dirty")
    e("80 38 00")
    b("0F 84", "dirty")
    e("8D 7C 24 20 C6 07 5B 47 B9 7D 00 00 00")
    l("copy")
    e("8A 10 84 D2")
    b("0F 84", "bracket")
    e("88 17 47 40 49")
    b("0F 85", "copy")
    b("E9", "dirty")  # overlong country name: no truncated CP949 label
    l("bracket")
    e("66 C7 07 5D 00")
    if relation_va is None:
        e("8D 44 24 20 50 8B 44 24 04 83 E8 10 50 FF 74 24 0C 8B CD")
    else:
        e("8D 54 24 20 8B 44 24 14 0F B6 80")
        e(struct.pack("<I", colors_va).hex())
        e("50 52 8B 44 24 08")
        e(f"83 E8 {line_spacing:02X}")  # 16px glyph + top/bottom border
        e("50 FF 74 24 10 8B CD")
    c.call(pixel_text_va)
    l("dirty")
    # Invalidate both label rows plus outline neighbours, clamped to cache.
    if outline_width is None:
        e("8B 04 24 C1 F8 04 83 E8 02 85 C0")
    else:
        # Exact pixel bounds, including the nation's upper and city's lower
        # outline. A 2/3px lower border can cross one more cache row than v6.
        e(f"8B 04 24 83 E8 {top_margin:02X} C1 F8 04 85 C0")
    b("0F 89", "dirty_start")
    e("31 C0")
    l("dirty_start")
    e("89 44 24 0C 8B 04 24")
    if outline_width is None:
        e("C1 F8 04 83 C0 02")
    else:
        e(f"83 C0 {15 + outline_width:02X} C1 F8 04 40")
    e("3B 85 F0 00 00 00")
    b("0F 8E", "dirty_end")
    e("8B 85 F0 00 00 00")
    l("dirty_end")
    e("89 44 24 10 6A 04 6A 00 6A 00 8D 8D C4 00 00 00")
    c.call(0x4B6637)
    e("85 C0")
    b("0F 84", "next")
    e("8B 4C 24 0C 0F AF 8D EC 00 00 00 8D 3C 48")
    e("8B 4C 24 10 2B 4C 24 0C 0F AF 8D EC 00 00 00")
    e("B8 FF FF 00 00 FC F3 66 AB")
    l("next")
    e("43 81 FB E2 00 00 00")
    b("0F 8C", "city")
    l("done")
    e("81 C4 A0 00 00 00 5F 5E 5B 5D C3")
    return c.finish()


def _outline_text_code(code_va: int, strokes_va: int, stroke_count: int = 8,
                       outline_color: int = 73, compact: bool = False) -> bytes:
    """cdecl(x, y, text, color); a private renderer, leaving other game text alone.

    Mirror 0x406800's native graphics calls and text cursor updates. Its
    horizontal-only offsets (-1,2,0,1) become border strokes followed by two
    foreground strokes. Defaults reproduce v6's fixed 1px black outline;
    v7 uses compact signed-byte offsets and configurable colour/count. No
    palette, font, or shared offset table is modified.
    """
    c = _Code(code_va)
    e, b, l = c.emit, c.branch, c.label
    e("53 56 57 55 31 F6 8B 7C 24 14 8B 5C 24 18 8B 6C 24 1C")
    l("stroke")
    e("0F BE 04 75" if compact else "8B 04 F5")
    e(struct.pack("<I", strokes_va).hex())
    e("0F BE 14 75" if compact else "8B 14 F5")
    e(struct.pack("<I", strokes_va + (1 if compact else 4)).hex())
    e("03 C7 03 D3 A3 18 01 58 00 A3 D0 B2 62 00 89 15 D4 B2 62 00")
    e("B8" + struct.pack("<I", outline_color).hex())
    e(f"83 FE {stroke_count - 2:02X}")
    b("0F 82", "color")
    e("8B 44 24 20")
    l("color")
    e("6A 04 6A 0F 50 B9 F0 B2 62 00")
    c.call(0x4B5DEA)
    e("68 FF FF FF 7F 55 B9 F0 B2 62 00")
    c.call(0x4B6071)
    e(f"46 83 FE {stroke_count:02X}")
    b("0F 82", "stroke")
    e("5D 5F 5E 5B C3")
    return c.finish()


def _outline_strokes(width: int) -> tuple[tuple[int, int], ...]:
    """Four-neighbour dilation of the original two-stroke foreground.

    Include inner offsets too, so thicker outlines have no gaps around thin
    glyphs. Width zero draws only the unchanged two foreground strokes.
    """
    if type(width) is not int or not MIN_OUTLINE_WIDTH <= width <= MAX_OUTLINE_WIDTH:
        raise ValueError("테두리 굵기는 0~3픽셀로 지정해야 합니다.")
    if width == 1:
        return OUTLINE_STROKES
    foreground = ((0, 0), (1, 0))
    border = {
        (x + dx, dy)
        for x, _ in foreground
        for dx in range(-width, width + 1)
        for dy in range(-width, width + 1)
        if abs(dx) + abs(dy) <= width
    } - set(foreground)
    return tuple(sorted(border, key=lambda point: (point[1], point[0]))) + foreground


def _relation_code(code_va: int) -> bytes:
    """ECX=nation ID, EAX=own(0)/friendly(1)/enemy(2); nonvolatile preserved.

    This is a map display classification, not a new diplomacy mechanic.
    Own affiliation takes precedence. Other nations use the live approach
    gate's entry policy (0x429D90) and its treaty test (0x46AB90/0x469880).
    Religion and nation+8 (treaty violation history) are not hostility tests.
    """
    c = _Code(code_va)
    e, b, l = c.emit, c.branch, c.label
    e("83 F9 4E")
    b("0F 83", "friendly")  # includes negative IDs: do not index the nation table
    e("3B 0D 4C 39 5B 00")
    b("0F 84", "own")
    e("8B C1 C1 E0 04 83 B8 CC 59 58 00 00")
    b("0F 8F", "enemy")  # live nation +0x0C > 0, same as 0x4687F4 approach
    e("51")
    c.call(TREATY_TEST_VA)  # cdecl; checks year and current affiliation itself
    e("83 C4 04 85 C0")
    b("0F 85", "enemy")
    l("friendly")
    e("B8 01 00 00 00 C3")
    l("own")
    e("31 C0 C3")
    l("enemy")
    e("B8 02 00 00 00 C3")
    return c.finish()


def _validated_colors(colors) -> tuple[int, ...]:
    result = tuple(colors)
    if len(result) != 6 or any(type(color) is not int or not 10 <= color <= 73 for color in result):
        raise ValueError("도시 이름 색상은 공용 팔레트 10~73 중 6개를 지정해야 합니다.")
    return result


def _validated_outline(outline) -> tuple[int, int]:
    result = tuple(outline)
    if (len(result) != 2 or any(type(value) is not int for value in result)
            or not MIN_OUTLINE_WIDTH <= result[0] <= MAX_OUTLINE_WIDTH
            or not 10 <= result[1] <= 73):
        raise ValueError("테두리 굵기는 0~3픽셀, 색상은 공용 팔레트 10~73으로 지정해야 합니다.")
    return result


def _validated_show_nation(show_nation: bool) -> bool:
    if type(show_nation) is not bool:
        raise ValueError("국가 이름 표시 여부는 켜기/끄기로 지정해야 합니다.")
    return show_nation


def read_city_label_palette(data: bytes | bytearray) -> tuple[tuple[int, int, int], ...]:
    """The game's fixed 64-colour palette, slots 10..73, stored as B-R-G."""
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        offset = pe.get_offset_from_rva(PALETTE_VA - pe.OPTIONAL_HEADER.ImageBase)
        raw = data[offset:offset + 192]
        if len(raw) != 192:
            raise ValueError("게임 공용 팔레트를 읽지 못했습니다.")
        return tuple((raw[i + 1], raw[i + 2], raw[i]) for i in range(0, 192, 3))
    finally:
        pe.close()


def _validate_relation_code(data: bytes | bytearray) -> None:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        offset = pe.get_offset_from_rva(TREATY_TEST_VA - pe.OPTIONAL_HEADER.ImageBase)
        if data[offset:offset + len(TREATY_TEST_CODE)] != TREATY_TEST_CODE:
            raise ValueError("도시 이름 색상에 사용하는 조약 판정 코드를 검증하지 못했습니다.")
    finally:
        pe.close()


def _payload(slot_va: int, version: int = VERSION,
             colors: tuple[int, ...] = DEFAULT_LABEL_COLORS,
             outline: tuple[int, int] = DEFAULT_LABEL_OUTLINE,
             show_nation: bool = DEFAULT_SHOW_NATION) -> bytes:
    width, outline_color = _validated_outline(outline) if version >= 7 else DEFAULT_LABEL_OUTLINE
    show_nation = _validated_show_nation(show_nation) if version >= 8 else DEFAULT_SHOW_NATION
    payload = bytearray(CITY_DISCOVERY_NOTICE_SLOT_SIZE)
    if version == 2:
        payload[:LEGACY_SLOT_SIZE] = _popup_payload(slot_va, version)
    elif version in (3, 4, 5, 6, 7, VERSION):
        payload[:len(MAGIC)] = MAGIC
        struct.pack_into("<I", payload, len(MAGIC), version)
    else:
        raise ValueError("지원하지 않는 도시 지도 이름 패치 버전입니다.")
    wrapper_va = slot_va + MAP_WRAPPER_OFFSET
    # Original ESP + 0x14 contains the map widget; PUSHFD/PUSHAD adds 36 bytes.
    code = bytearray(bytes.fromhex("9C 60 8B 4C 24 38"))
    code += _call(wrapper_va + len(code), slot_va + MAP_LABELS_OFFSET)
    code += bytes.fromhex("61 9D") + MAP_ORIGINAL
    code += _jump(wrapper_va + len(code), MAP_RESUME_VA)
    payload[MAP_WRAPPER_OFFSET:MAP_WRAPPER_OFFSET + len(code)] = code
    if version in (2, 3):
        code = _legacy_map_labels_code(slot_va + MAP_LABELS_OFFSET)
    else:
        helper = _pixel_text_code(
            slot_va + PIXEL_TEXT_OFFSET, colored=version >= 5,
            outline_va=slot_va + OUTLINE_TEXT_OFFSET if version >= 6 else None,
            outline_width=width if version >= 7 else None,
        )
        if PIXEL_TEXT_OFFSET + len(helper) > MAP_WRAPPER_OFFSET:
            raise AssertionError("도시 이름 가운데 정렬 코드가 예약 공간을 초과했습니다.")
        payload[PIXEL_TEXT_OFFSET:PIXEL_TEXT_OFFSET + len(helper)] = helper
        code = _map_labels_code(
            slot_va + MAP_LABELS_OFFSET, slot_va + PIXEL_TEXT_OFFSET,
            slot_va + RELATION_OFFSET if version >= 5 else None, slot_va + COLORS_OFFSET,
            outlined=version >= 6,
            outline_width=width if version >= 7 else None,
            show_nation=show_nation,
        )
    labels_end = OUTLINE_STROKES_OFFSET if version >= 6 else RELATION_OFFSET if version >= 5 else len(payload)
    if MAP_LABELS_OFFSET + len(code) > labels_end:
        raise AssertionError("도시 지도 이름 표시 코드가 예약 공간을 초과했습니다.")
    payload[MAP_LABELS_OFFSET:MAP_LABELS_OFFSET + len(code)] = code
    if version >= 5:
        payload[COLORS_OFFSET:COLORS_OFFSET + 6] = bytes(_validated_colors(colors))
        relation = _relation_code(slot_va + RELATION_OFFSET)
        if RELATION_OFFSET + len(relation) > (OUTLINE_TEXT_OFFSET if version >= 6 else len(payload)):
            raise AssertionError("도시 국가 관계 판정 코드가 예약 공간을 초과했습니다.")
        payload[RELATION_OFFSET:RELATION_OFFSET + len(relation)] = relation
    if version >= 6:
        offsets = _outline_strokes(width) if version >= 7 else OUTLINE_STROKES
        strokes = b"".join(struct.pack("<bb" if version >= 7 else "<ii", *stroke) for stroke in offsets)
        if OUTLINE_STROKES_OFFSET + len(strokes) > RELATION_OFFSET:
            raise AssertionError("도시 이름 테두리 좌표가 예약 공간을 초과했습니다.")
        payload[OUTLINE_STROKES_OFFSET:OUTLINE_STROKES_OFFSET + len(strokes)] = strokes
        outline_code = _outline_text_code(
            slot_va + OUTLINE_TEXT_OFFSET, slot_va + OUTLINE_STROKES_OFFSET,
            stroke_count=len(offsets), outline_color=outline_color, compact=version >= 7,
        )
        if OUTLINE_TEXT_OFFSET + len(outline_code) > len(payload):
            raise AssertionError("도시 이름 테두리 코드가 예약 공간을 초과했습니다.")
        payload[OUTLINE_TEXT_OFFSET:OUTLINE_TEXT_OFFSET + len(outline_code)] = outline_code
    if version >= 7:
        payload[OUTLINE_SETTINGS_OFFSET:OUTLINE_SETTINGS_OFFSET + 2] = bytes((width, outline_color))
    if version >= 8:
        payload[SHOW_NATION_OFFSET] = int(show_nation)
    return bytes(payload)


def _hook_offsets(data: bytes | bytearray) -> list[int]:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        for va, expected in CONTEXTS:
            offset = pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)
            if bytes(data[offset:offset + len(expected)]) != expected:
                raise ValueError(f"도시 발견 알림 코드 0x{va:X}을(를) 검증하지 못했습니다.")
        return [
            pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)
            for va, _, _, _ in HOOKS
        ]
    finally:
        pe.close()


def _map_offset(data: bytes | bytearray) -> int:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_offset_from_rva(MAP_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase)
    finally:
        pe.close()


def _patch_version(data: bytes | bytearray) -> int:
    offsets = _hook_offsets(data)
    current = [bytes(data[offset:offset + 5]) for offset in offsets]
    original = [_call(va, target) for va, target, _, _ in HOOKS]
    map_offset = _map_offset(data)
    map_code = bytes(data[map_offset:map_offset + len(MAP_ORIGINAL)])
    section = find_patch_section(data)
    if current == original and map_code == MAP_ORIGINAL:
        if section is not None and min(section.raw_size, section.virtual_size) >= CITY_DISCOVERY_NOTICE_SLOT_OFFSET + LEGACY_SLOT_SIZE:
            offset, _ = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, LEGACY_SLOT_SIZE)
            if bytes(data[offset:offset + len(MAGIC)]) == MAGIC:
                raise ValueError("도시 발견 알림 코드가 남아 있지만 호출부가 원본 상태입니다.")
        return 0
    if section is None or min(section.raw_size, section.virtual_size) < CITY_DISCOVERY_NOTICE_SLOT_OFFSET + LEGACY_SLOT_SIZE:
        raise ValueError("도시 발견 알림 호출에 대응하는 .patch 데이터를 찾지 못했습니다.")
    offset, va = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, LEGACY_SLOT_SIZE)
    expected = [_call(hook, va + wrapper) for hook, _, wrapper, _ in HOOKS]
    if (
        current == expected and map_code == MAP_ORIGINAL
        and bytes(data[offset:offset + LEGACY_SLOT_SIZE]) == _popup_payload(va, 1)
    ):
        return 1
    if min(section.raw_size, section.virtual_size) >= PATCH_SECTION_CITY_DISCOVERY_NOTICE_SIZE and map_code == _map_hook(va):
        payload = bytes(data[offset:offset + CITY_DISCOVERY_NOTICE_SLOT_SIZE])
        if current == expected and payload == _payload(va, 2):
            return 2
        if current == original and payload == _payload(va, 3):
            return 3
        if current == original and payload == _payload(va, 4):
            return 4
        colors = tuple(payload[COLORS_OFFSET:COLORS_OFFSET + 6])
        version = struct.unpack_from("<I", payload, len(MAGIC))[0]
        outline = tuple(payload[OUTLINE_SETTINGS_OFFSET:OUTLINE_SETTINGS_OFFSET + 2]) if version >= 7 else DEFAULT_LABEL_OUTLINE
        show_nation = DEFAULT_SHOW_NATION
        if version == VERSION:
            if payload[SHOW_NATION_OFFSET] not in (0, 1):
                raise ValueError("저장된 국가 이름 표시 설정이 올바르지 않습니다.")
            show_nation = bool(payload[SHOW_NATION_OFFSET])
        if (current == original and version in (5, 6, 7, VERSION)
                and payload == _payload(va, version, colors=colors, outline=outline, show_nation=show_nation)):
            _validate_relation_code(data)
            return version
    raise ValueError("도시 지도 이름 표시 패치 상태를 검증하지 못했습니다.")


def read_city_discovery_notice_patch_state(data: bytes | bytearray) -> bool:
    return _patch_version(data) != 0


def read_city_label_colors(data: bytes | bytearray) -> tuple[int, ...]:
    if _patch_version(data) < 5:
        return DEFAULT_LABEL_COLORS
    section = find_patch_section(data)
    offset, _ = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
    return tuple(data[offset + COLORS_OFFSET:offset + COLORS_OFFSET + 6])


def read_city_label_outline(data: bytes | bytearray) -> tuple[int, int]:
    if _patch_version(data) < 7:
        return DEFAULT_LABEL_OUTLINE
    section = find_patch_section(data)
    offset, _ = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
    return tuple(data[offset + OUTLINE_SETTINGS_OFFSET:offset + OUTLINE_SETTINGS_OFFSET + 2])


def read_city_label_show_nation(data: bytes | bytearray) -> bool:
    if _patch_version(data) < 8:
        return DEFAULT_SHOW_NATION
    section = find_patch_section(data)
    offset, _ = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
    return bool(data[offset + SHOW_NATION_OFFSET])


def apply_city_discovery_notice_patch(data: bytearray, enabled: bool,
                                     colors: tuple[int, ...] | None = None,
                                     outline: tuple[int, int] | None = None,
                                     show_nation: bool | None = None) -> bool:
    version = _patch_version(data)
    previous_colors = read_city_label_colors(data)
    previous_outline = read_city_label_outline(data)
    previous_show_nation = read_city_label_show_nation(data)
    colors = previous_colors if colors is None else _validated_colors(colors)
    outline = previous_outline if outline is None else _validated_outline(outline)
    show_nation = previous_show_nation if show_nation is None else _validated_show_nation(show_nation)
    if (version == VERSION and enabled and colors == previous_colors and outline == previous_outline
            and show_nation == previous_show_nation) or (version == 0 and not enabled):
        return False
    if enabled:
        _validate_relation_code(data)
    if version and enabled:
        # Restore both old popup CALLs and erase their code before installing
        # current map labels, without changing the user's checkbox state.
        apply_city_discovery_notice_patch(data, False)
    if enabled:
        section, _ = ensure_patch_section(data, PATCH_SECTION_CITY_DISCOVERY_NOTICE_SIZE)
        offset, va = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
        payload = _payload(va, colors=colors, outline=outline, show_nation=show_nation)
        present = bytes(data[offset:offset + CITY_DISCOVERY_NOTICE_SLOT_SIZE])
        if any(present) and present != payload:
            raise ValueError("도시 발견 알림용 .patch 슬롯이 다른 데이터로 사용 중입니다.")
        data[offset:offset + CITY_DISCOVERY_NOTICE_SLOT_SIZE] = payload
        targets = [target for _, target, _, _ in HOOKS]
    else:
        section = find_patch_section(data)
        if section is None:
            raise ValueError("도시 발견 알림의 복원 데이터를 찾지 못했습니다.")
        clear_slot(
            data, section, CITY_DISCOVERY_NOTICE_SLOT_OFFSET,
            LEGACY_SLOT_SIZE if version == 1 else CITY_DISCOVERY_NOTICE_SLOT_SIZE,
        )
        targets = [target for _, target, _, _ in HOOKS]
    for offset, (hook, _, _, _), target in zip(_hook_offsets(data), HOOKS, targets):
        data[offset:offset + 5] = _call(hook, target)
    map_offset = _map_offset(data)
    data[map_offset:map_offset + len(MAP_ORIGINAL)] = _map_hook(va) if enabled else MAP_ORIGINAL
    if read_city_discovery_notice_patch_state(data) != enabled:
        raise ValueError("도시 발견 알림 적용 결과를 검증하지 못했습니다.")
    return True
