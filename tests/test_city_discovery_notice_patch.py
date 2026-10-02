"""Verify map-only city labels and removal of legacy extra notices."""

import hashlib
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from city_discovery_notice_patch import (  # noqa: E402
    COMMON_POPUP_VA,
    COLORS_OFFSET,
    DEFAULT_LABEL_COLORS,
    DEFAULT_LABEL_OUTLINE,
    DEFAULT_SHOW_NATION,
    HOOKS,
    LEGACY_SLOT_SIZE,
    MAP_HOOK_VA,
    MAP_ORIGINAL,
    MAP_RESUME_VA,
    MAP_TEXT_VA,
    MAX_OUTLINE_WIDTH,
    MIN_OUTLINE_WIDTH,
    OUTLINE_SETTINGS_OFFSET,
    OUTLINE_TEXT_OFFSET,
    PIXEL_TEXT_VA,
    NATION_TABLE_VA,
    NOTICE_FORMAT,
    SPEECH_POPUP_VA,
    SHOW_NATION_OFFSET,
    TREATY_TEST_VA,
    _call,
    _popup_payload,
    _payload,
    _map_hook,
    apply_city_discovery_notice_patch,
    read_city_label_colors,
    read_city_label_outline,
    read_city_label_palette,
    read_city_label_show_nation,
    read_city_discovery_notice_patch_state,
)
from pe_patch_section import (  # noqa: E402
    CITY_DISCOVERY_NOTICE_SLOT_OFFSET,
    CITY_DISCOVERY_NOTICE_SLOT_SIZE,
    HIGH_SPEED_MAP_FIX_SLOT_OFFSET,
    HIGH_SPEED_MAP_FIX_SLOT_SIZE,
    PATCH_SECTION_CITY_DISCOVERY_NOTICE_SIZE,
    ensure_patch_section,
    find_patch_section,
)

try:
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE, UC_HOOK_MEM_READ
    from unicorn.x86_const import (
        UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX,
        UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP,
        UC_X86_REG_EIP, UC_X86_REG_EFLAGS,
    )
except ImportError:
    Uc = None


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"


def _offsets(data):
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return [pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase) for va, _, _, _ in HOOKS]
    finally:
        pe.close()


class CityDiscoveryNoticePatchTests(unittest.TestCase):
    def test_integrated_save_reload_coordinate_change_and_disable(self):
        from patch_cds_integrated import apply_all, read_settings
        from high_speed_map_patch import read_high_speed_map_fix_state

        colors = (10, 21, 32, 43, 54, 73)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "CDS_95.EXE"
            shutil.copyfile(FIXTURE, target)
            for style, enabled, requested_colors, requested_outline, expected_outline, requested_show_nation, expected_show_nation in (
                (None, True, colors, (3, 21), (1, 21), False, False),
                ("korean3", True, None, None, (1, 21), None, False),
                ("original", True, None, (0, 73), (0, 73), True, True),
                (None, False, None, None, DEFAULT_LABEL_OUTLINE, None, True),
            ):
                settings = read_settings(target)
                apply_all(
                    target, settings[0] if style is None else style,
                    True, settings[1], *settings[2:],
                    city_discovery_notice_enabled=enabled,
                    city_label_colors=requested_colors,
                    city_label_outline=requested_outline,
                    city_label_show_nation=requested_show_nation,
                    high_speed_map_fix_enabled=True,
                )
                saved = target.read_bytes()
                self.assertEqual(
                    read_city_discovery_notice_patch_state(saved), enabled,
                )
                self.assertEqual(read_city_label_colors(saved), colors if enabled else DEFAULT_LABEL_COLORS)
                self.assertEqual(read_city_label_outline(saved), expected_outline)
                self.assertIs(read_city_label_show_nation(saved), expected_show_nation)
                self.assertTrue(read_high_speed_map_fix_state(saved))

    def test_show_nation_round_trip_edit_and_omitted_settings_preservation(self):
        data = bytearray(FIXTURE.read_bytes())
        self.assertIs(DEFAULT_SHOW_NATION, True)
        self.assertEqual(SHOW_NATION_OFFSET, 0x18)
        self.assertIs(read_city_label_show_nation(data), True)
        colors, outline = (10, 21, 32, 43, 54, 73), (3, 21)
        self.assertTrue(apply_city_discovery_notice_patch(data, True, colors, outline, False))
        for show_nation in (False, True, False):
            with self.subTest(show_nation=show_nation):
                apply_city_discovery_notice_patch(data, True, show_nation=show_nation)
                saved = bytes(data)
                reloaded = bytearray(saved)
                self.assertIs(read_city_label_show_nation(reloaded), show_nation)
                self.assertEqual(read_city_label_colors(reloaded), colors)
                self.assertEqual(read_city_label_outline(reloaded), outline)
                self.assertFalse(apply_city_discovery_notice_patch(reloaded, True))
                self.assertFalse(apply_city_discovery_notice_patch(reloaded, True, show_nation=show_nation))
                self.assertEqual(bytes(reloaded), saved)
        self.assertTrue(apply_city_discovery_notice_patch(data, True, outline=(0, 42)))
        self.assertIs(read_city_label_show_nation(data), False)
        self.assertTrue(apply_city_discovery_notice_patch(data, True, tuple(reversed(colors))))
        self.assertIs(read_city_label_show_nation(data), False)
        self.assertTrue(apply_city_discovery_notice_patch(data, False))
        self.assertIs(read_city_label_show_nation(data), True)
        self.assertFalse(read_city_discovery_notice_patch_state(data))
        section = find_patch_section(data)
        off, _ = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
        self.assertFalse(any(data[off:off + CITY_DISCOVERY_NOTICE_SLOT_SIZE]))
        pe = pefile.PE(data=bytes(data), fast_load=True)
        try:
            map_offset = pe.get_offset_from_rva(MAP_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase)
        finally:
            pe.close()
        self.assertEqual(data[map_offset:map_offset + len(MAP_ORIGINAL)], MAP_ORIGINAL)
        self.assertTrue(apply_city_discovery_notice_patch(data, True))
        self.assertIs(read_city_label_show_nation(data), True)

    def test_invalid_show_nation_settings_reject_without_mutating_executable(self):
        for show_nation in (0, 1, -1, 0.0, 1.0, "true", "false", b"", [], {}):
            for already_enabled in (False, True):
                with self.subTest(show_nation=show_nation, already_enabled=already_enabled):
                    data = bytearray(FIXTURE.read_bytes())
                    if already_enabled:
                        apply_city_discovery_notice_patch(data, True, show_nation=False)
                    original = bytes(data)
                    with self.assertRaises(ValueError):
                        apply_city_discovery_notice_patch(data, True, show_nation=show_nation)
                    self.assertEqual(bytes(data), original)

    def test_integrated_coordinate_changes_preserve_hidden_nation_and_fixed_outline(self):
        from patch_cds_integrated import apply_all, read_settings

        colors = (10, 21, 32, 43, 54, 73)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "CDS_95.EXE"
            data = bytearray(FIXTURE.read_bytes())
            apply_city_discovery_notice_patch(data, True, colors, (3, 21), False)
            target.write_bytes(data)
            for style in ("korean3", "original", "korean3"):
                settings = read_settings(target)
                apply_all(
                    target, style, True, settings[1], *settings[2:],
                    city_discovery_notice_enabled=True,
                )
                saved = target.read_bytes()
                self.assertIs(read_city_label_show_nation(saved), False)
                self.assertEqual(read_city_label_colors(saved), colors)
                self.assertEqual(read_city_label_outline(saved), (1, 21))

    def test_integrated_outline_toggle_survives_reload_and_coordinate_and_nation_changes(self):
        from patch_cds_integrated import apply_all, read_settings

        colors = (10, 21, 32, 43, 54, 73)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "CDS_95.EXE"
            shutil.copyfile(FIXTURE, target)
            for style, outline_width, show_nation, expected_width, expected_show_nation in (
                ("original", 0, True, 0, True),
                ("korean3", None, None, 0, True),
                ("original", None, False, 0, False),
                ("korean3", 1, None, 1, False),
                ("original", 0, None, 0, False),
                ("korean3", None, True, 0, True),
                ("original", 2, None, 1, True),
            ):
                with self.subTest(style=style, outline_width=outline_width, show_nation=show_nation):
                    settings = read_settings(target)
                    # Re-enabling the outline uses the color saved while off.
                    outline_color = read_city_label_outline(target.read_bytes())[1]
                    requested_outline = None if outline_width is None else (
                        outline_width, 42 if outline_width == 0 else outline_color,
                    )
                    apply_all(
                        target, style, True, settings[1], *settings[2:],
                        city_discovery_notice_enabled=True,
                        city_label_colors=colors,
                        city_label_outline=requested_outline,
                        city_label_show_nation=show_nation,
                    )
                    saved = target.read_bytes()
                    self.assertTrue(read_city_discovery_notice_patch_state(saved))
                    self.assertEqual(read_city_label_colors(saved), colors)
                    self.assertEqual(read_city_label_outline(saved), (expected_width, 42))
                    self.assertIs(read_city_label_show_nation(saved), expected_show_nation)

    def test_outline_round_trip_edit_and_omitted_settings_preservation(self):
        data = bytearray(FIXTURE.read_bytes())
        self.assertEqual(DEFAULT_LABEL_OUTLINE, (1, 73))
        self.assertEqual((MIN_OUTLINE_WIDTH, MAX_OUTLINE_WIDTH), (0, 3))
        self.assertEqual(read_city_label_outline(data), DEFAULT_LABEL_OUTLINE)
        colors = (10, 21, 32, 43, 54, 73)
        self.assertTrue(apply_city_discovery_notice_patch(data, True, colors, (3, 21)))
        self.assertEqual(read_city_label_outline(bytes(data)), (3, 21))
        saved = bytes(data)
        self.assertFalse(apply_city_discovery_notice_patch(data, True))
        self.assertFalse(apply_city_discovery_notice_patch(data, True, list(colors), [3, 21]))
        self.assertEqual(bytes(data), saved)
        self.assertTrue(apply_city_discovery_notice_patch(data, True, outline=(0, 10)))
        self.assertEqual(read_city_label_colors(data), colors)
        self.assertEqual(read_city_label_outline(data), (0, 10))
        edited = tuple(reversed(colors))
        self.assertTrue(apply_city_discovery_notice_patch(data, True, edited))
        self.assertEqual(read_city_label_outline(data), (0, 10))
        self.assertTrue(apply_city_discovery_notice_patch(data, False))
        self.assertEqual(read_city_label_outline(data), DEFAULT_LABEL_OUTLINE)

    def test_invalid_outline_settings_reject_without_mutating_executable(self):
        for outline in (
            (), (1,), (1, 73, 10), (-1, 73), (4, 73), (1, 9), (1, 74),
            (True, 73), (1.0, 73), ("1", 73), (1, True), (1, 73.0),
            (1, "73"), (0, 9), (0, 74),
        ):
            for already_enabled in (False, True):
                with self.subTest(outline=outline, already_enabled=already_enabled):
                    data = bytearray(FIXTURE.read_bytes())
                    if already_enabled:
                        apply_city_discovery_notice_patch(data, True, outline=(2, 42))
                    original = bytes(data)
                    with self.assertRaises(ValueError):
                        apply_city_discovery_notice_patch(data, True, outline=outline)
                    self.assertEqual(bytes(data), original)

    def test_custom_colors_round_trip_edit_and_default_preservation(self):
        data = bytearray(FIXTURE.read_bytes())
        self.assertEqual(DEFAULT_LABEL_COLORS, (10,) * 6)
        self.assertEqual(read_city_label_colors(data), DEFAULT_LABEL_COLORS)
        colors = (10, 21, 32, 43, 54, 73)
        self.assertTrue(apply_city_discovery_notice_patch(data, True, colors))
        self.assertEqual(read_city_label_colors(bytes(data)), colors)
        saved = bytes(data)
        self.assertFalse(apply_city_discovery_notice_patch(data, True))
        self.assertFalse(apply_city_discovery_notice_patch(data, True, list(colors)))
        self.assertEqual(bytes(data), saved)
        edited = (73, 62, 51, 40, 29, 10)
        self.assertTrue(apply_city_discovery_notice_patch(data, True, edited))
        self.assertEqual(read_city_label_colors(data), edited)
        self.assertTrue(read_city_discovery_notice_patch_state(data))
        self.assertFalse(apply_city_discovery_notice_patch(data, True, edited))
        self.assertTrue(apply_city_discovery_notice_patch(data, False))
        self.assertEqual(read_city_label_colors(data), DEFAULT_LABEL_COLORS)

    def test_palette_reads_all_64_executable_brg_entries_as_rgb(self):
        data = bytearray(FIXTURE.read_bytes())
        pe = pefile.PE(data=bytes(data), fast_load=True)
        try:
            offset = pe.get_offset_from_rva(0x4FFDD8 - pe.OPTIONAL_HEADER.ImageBase)
        finally:
            pe.close()
        # Non-symmetric channels detect RGB/BGR/BRG confusion, including the
        # last selectable entry (palette index 73).
        data[offset:offset + 3] = bytes((17, 43, 89))
        data[offset + 63 * 3:offset + 64 * 3] = bytes((251, 131, 7))
        palette = read_city_label_palette(data)
        self.assertEqual(len(palette), 64)
        self.assertEqual(tuple(palette[0]), (43, 89, 17))
        self.assertEqual(tuple(palette[-1]), (131, 7, 251))
        self.assertTrue(all(len(color) == 3 for color in palette))
        apply_city_discovery_notice_patch(data, True, (10, 21, 32, 43, 54, 73))
        self.assertEqual(read_city_label_palette(data), palette)

    def test_invalid_color_settings_reject_without_mutating_executable(self):
        for colors in (
            (), (10,) * 5, (10,) * 7,
            (9, 10, 10, 10, 10, 10), (10, 10, 10, 10, 10, 74),
            (10, 10, -1, 10, 10, 10), (10, 10, 10.0, 10, 10, 10),
            (10, 10, True, 10, 10, 10), (10, 10, "10", 10, 10, 10),
        ):
            with self.subTest(colors=colors):
                data = bytearray(FIXTURE.read_bytes())
                original = bytes(data)
                with self.assertRaises(ValueError):
                    apply_city_discovery_notice_patch(data, True, colors)
                self.assertEqual(bytes(data), original)
                apply_city_discovery_notice_patch(data, True)
                enabled = bytes(data)
                with self.assertRaises(ValueError):
                    apply_city_discovery_notice_patch(data, True, colors)
                self.assertEqual(bytes(data), enabled)

    def test_applies_once_and_restores_only_discovery_and_map_hooks(self):
        original = FIXTURE.read_bytes()
        data = bytearray(original)
        self.assertFalse(read_city_discovery_notice_patch_state(data))
        self.assertTrue(apply_city_discovery_notice_patch(data, True))
        self.assertTrue(read_city_discovery_notice_patch_state(data))
        for off, (hook, target, _, _) in zip(_offsets(data), HOOKS):
            self.assertEqual(data[off:off + 5], _call(hook, target))
        enabled = bytes(data)
        self.assertFalse(apply_city_discovery_notice_patch(data, True))
        self.assertEqual(bytes(data), enabled)
        section = find_patch_section(data)
        self.assertIsNotNone(section)
        permitted = {k for off in _offsets(original) for k in range(off, off + 5)}
        pe = pefile.PE(data=original, fast_load=True)
        try:
            map_offset = pe.get_offset_from_rva(MAP_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase)
            permitted.update(range(map_offset, map_offset + len(MAP_ORIGINAL)))
            text_section = next(s for s in pe.sections if s.Name.rstrip(b"\0") == b".text")
            start = text_section.PointerToRawData
            end = start + text_section.SizeOfRawData
            changed = {k for k in range(start, end) if original[k] != data[k]}
        finally:
            pe.close()
        self.assertTrue(changed)
        self.assertTrue(changed <= permitted)
        self.assertTrue(apply_city_discovery_notice_patch(data, False))
        self.assertFalse(read_city_discovery_notice_patch_state(data))
        for off in _offsets(data):
            self.assertEqual(data[off:off + 5], original[off:off + 5])
        self.assertEqual(data[map_offset:map_offset + len(MAP_ORIGINAL)], MAP_ORIGINAL)
        off, _ = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
        self.assertFalse(any(data[off:off + CITY_DISCOVERY_NOTICE_SLOT_SIZE]))
        self.assertFalse(apply_city_discovery_notice_patch(data, False))

    def test_upgrades_and_restores_legacy_popup_only_patch(self):
        data = bytearray(FIXTURE.read_bytes())
        section, _ = ensure_patch_section(data, CITY_DISCOVERY_NOTICE_SLOT_OFFSET + LEGACY_SLOT_SIZE)
        offset, va = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, LEGACY_SLOT_SIZE)
        data[offset:offset + LEGACY_SLOT_SIZE] = _popup_payload(va, 1)
        for off, (hook, _, wrapper, _) in zip(_offsets(data), HOOKS):
            data[off:off + 5] = _call(hook, va + wrapper)
        self.assertTrue(read_city_discovery_notice_patch_state(data))
        self.assertEqual(read_city_label_outline(data), DEFAULT_LABEL_OUTLINE)
        legacy = bytes(data)
        self.assertTrue(apply_city_discovery_notice_patch(data, True))
        self.assertTrue(read_city_discovery_notice_patch_state(data))
        self.assertFalse(apply_city_discovery_notice_patch(data, True))
        self.assertTrue(apply_city_discovery_notice_patch(data, False))
        self.assertFalse(read_city_discovery_notice_patch_state(data))
        data = bytearray(legacy)
        self.assertTrue(apply_city_discovery_notice_patch(data, False))
        self.assertFalse(read_city_discovery_notice_patch_state(data))

    def test_rejects_partial_and_corrupted_patches(self):
        data = bytearray(FIXTURE.read_bytes())
        apply_city_discovery_notice_patch(data, True)
        off = _offsets(data)[0]
        va, target, _, _ = HOOKS[0]
        data[off:off + 5] = _call(va, target + 1)
        with self.assertRaises(ValueError):
            read_city_discovery_notice_patch_state(data)

    def test_migrates_v2_popup_and_map_patch_to_map_only(self):
        from high_speed_map_patch import apply_high_speed_map_fix, read_high_speed_map_fix_state
        for enable in (True, False):
            with self.subTest(enable=enable):
                data = bytearray(FIXTURE.read_bytes())
                section, _ = ensure_patch_section(data, PATCH_SECTION_CITY_DISCOVERY_NOTICE_SIZE)
                offset, va = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
                data[offset:offset + CITY_DISCOVERY_NOTICE_SLOT_SIZE] = _payload(va, 2)
                for off, (hook, _, wrapper, _) in zip(_offsets(data), HOOKS):
                    data[off:off + 5] = _call(hook, va + wrapper)
                pe = pefile.PE(data=bytes(data))
                try:
                    map_offset = pe.get_offset_from_rva(MAP_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase)
                finally:
                    pe.close()
                data[map_offset:map_offset + len(MAP_ORIGINAL)] = _map_hook(va)
                apply_high_speed_map_fix(data, True)
                self.assertTrue(read_city_discovery_notice_patch_state(data))
                self.assertEqual(read_city_label_outline(data), DEFAULT_LABEL_OUTLINE)
                self.assertTrue(apply_city_discovery_notice_patch(data, enable))
                self.assertEqual(read_city_discovery_notice_patch_state(data), enable)
                self.assertTrue(read_high_speed_map_fix_state(data))
                for off, (hook, target, _, _) in zip(_offsets(data), HOOKS):
                    self.assertEqual(data[off:off + 5], _call(hook, target))
                self.assertNotIn(NOTICE_FORMAT, data[offset:offset + CITY_DISCOVERY_NOTICE_SLOT_SIZE])
                self.assertEqual(data[map_offset:map_offset + len(MAP_ORIGINAL)], _map_hook(va) if enable else MAP_ORIGINAL)
                self.assertFalse(apply_city_discovery_notice_patch(data, enable))

    def test_upgrades_v3_map_only_patch_without_extra_popup(self):
        data = bytearray(FIXTURE.read_bytes())
        section, _ = ensure_patch_section(data, PATCH_SECTION_CITY_DISCOVERY_NOTICE_SIZE)
        off, va = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
        data[off:off + CITY_DISCOVERY_NOTICE_SLOT_SIZE] = _payload(va, 3)
        pe = pefile.PE(data=bytes(data))
        try:
            map_offset = pe.get_offset_from_rva(MAP_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase)
        finally:
            pe.close()
        data[map_offset:map_offset + len(MAP_ORIGINAL)] = _map_hook(va)
        self.assertTrue(read_city_discovery_notice_patch_state(data))
        self.assertEqual(read_city_label_outline(data), DEFAULT_LABEL_OUTLINE)
        self.assertTrue(apply_city_discovery_notice_patch(data, True))
        self.assertEqual(data[off:off + CITY_DISCOVERY_NOTICE_SLOT_SIZE], _payload(va))
        for offset, (hook, target, _, _) in zip(_offsets(data), HOOKS):
            self.assertEqual(data[offset:offset + 5], _call(hook, target))

    def test_v4_payload_remains_compatible_and_migrates_with_default_or_custom_colors(self):
        # Captured from the previously shipped v4 generator, before adding
        # configurable colors; compatibility must not drift with v5 code.
        self.assertEqual(
            hashlib.sha256(_payload(0x700000, 4)).hexdigest(),
            "110d928659c74a0b61d2663513fdb59b81e41ea11bf0076066f7d3f51838c051",
        )
        for enabled, colors in ((True, None), (True, (10, 21, 32, 43, 54, 73)), (False, None)):
            with self.subTest(enabled=enabled, colors=colors):
                data = bytearray(FIXTURE.read_bytes())
                section, _ = ensure_patch_section(data, PATCH_SECTION_CITY_DISCOVERY_NOTICE_SIZE)
                off, va = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
                data[off:off + CITY_DISCOVERY_NOTICE_SLOT_SIZE] = _payload(va, 4)
                pe = pefile.PE(data=bytes(data), fast_load=True)
                try:
                    map_offset = pe.get_offset_from_rva(MAP_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase)
                finally:
                    pe.close()
                data[map_offset:map_offset + len(MAP_ORIGINAL)] = _map_hook(va)
                self.assertTrue(read_city_discovery_notice_patch_state(data))
                self.assertEqual(read_city_label_colors(data), DEFAULT_LABEL_COLORS)
                self.assertEqual(read_city_label_outline(data), DEFAULT_LABEL_OUTLINE)
                self.assertTrue(apply_city_discovery_notice_patch(data, enabled, colors))
                self.assertEqual(read_city_discovery_notice_patch_state(data), enabled)
                self.assertEqual(read_city_label_colors(data), colors or DEFAULT_LABEL_COLORS)
                self.assertFalse(apply_city_discovery_notice_patch(data, enabled, colors))
                if enabled:
                    self.assertEqual(struct.unpack_from("<I", data, off + 8)[0], 8)
                for offset, (hook, target, _, _) in zip(_offsets(data), HOOKS):
                    self.assertEqual(data[offset:offset + 5], _call(hook, target))

    def test_v5_color_payload_is_byte_exact_and_migrates_preserving_all_six_colors(self):
        colors = (10, 21, 32, 43, 54, 73)
        # Captured before adding the v6 outline helper. Old saved EXEs must
        # remain readable with both default and individually chosen colours.
        for old_colors, digest in (
            (DEFAULT_LABEL_COLORS, "b5edc8bfabcfed01567a4b7133ec79b00ecfb14b5f66bb4e26a435667fdff574"),
            (colors, "7f4004741ac668976ff9759a68fbab1931f3447c00ca6bfe92c1786c07dd0402"),
        ):
            self.assertEqual(hashlib.sha256(_payload(0x700000, 5, old_colors)).hexdigest(), digest)
            for enabled in (True, False):
                with self.subTest(colors=old_colors, enabled=enabled):
                    data = bytearray(FIXTURE.read_bytes())
                    section, _ = ensure_patch_section(data, PATCH_SECTION_CITY_DISCOVERY_NOTICE_SIZE)
                    off, va = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
                    data[off:off + CITY_DISCOVERY_NOTICE_SLOT_SIZE] = _payload(va, 5, old_colors)
                    pe = pefile.PE(data=bytes(data), fast_load=True)
                    try:
                        map_offset = pe.get_offset_from_rva(MAP_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase)
                    finally:
                        pe.close()
                    data[map_offset:map_offset + len(MAP_ORIGINAL)] = _map_hook(va)
                    self.assertTrue(read_city_discovery_notice_patch_state(data))
                    self.assertEqual(read_city_label_colors(data), old_colors)
                    self.assertEqual(read_city_label_outline(data), DEFAULT_LABEL_OUTLINE)
                    self.assertTrue(apply_city_discovery_notice_patch(data, enabled))
                    self.assertEqual(read_city_discovery_notice_patch_state(data), enabled)
                    self.assertEqual(read_city_label_colors(data), old_colors if enabled else DEFAULT_LABEL_COLORS)
                    self.assertFalse(apply_city_discovery_notice_patch(data, enabled))
                    if enabled:
                        self.assertEqual(data[off:off + CITY_DISCOVERY_NOTICE_SLOT_SIZE], _payload(va, 8, old_colors))
                    else:
                        self.assertFalse(any(data[off:off + CITY_DISCOVERY_NOTICE_SLOT_SIZE]))
                        self.assertTrue(apply_city_discovery_notice_patch(data, True, old_colors))
                        self.assertEqual(read_city_label_colors(data), old_colors)
                    for offset, (hook, target, _, _) in zip(_offsets(data), HOOKS):
                        self.assertEqual(data[offset:offset + 5], _call(hook, target))

    def test_v6_outline_payload_is_byte_exact_and_migrates_preserving_colors(self):
        colors = (10, 21, 32, 43, 54, 73)
        # Capture the shipped v6 fixed 1px black renderer and its int32 stroke
        # table before introducing configurable width, colour, and int8 data.
        for old_colors, digest in (
            (DEFAULT_LABEL_COLORS, "3d1872e5c4a87cbdf25ddd5fdcc9122eaf8e8770346c0b2dd17896ed106f4143"),
            (colors, "c5d22f3e63322d9e4530ab706af52418569ef4571f14b1622c340139343b8461"),
        ):
            self.assertEqual(hashlib.sha256(_payload(0x700000, 6, old_colors)).hexdigest(), digest)
            for enabled, outline in ((True, None), (True, (3, 21)), (True, (0, 10)), (False, None)):
                with self.subTest(colors=old_colors, enabled=enabled, outline=outline):
                    data = bytearray(FIXTURE.read_bytes())
                    section, _ = ensure_patch_section(data, PATCH_SECTION_CITY_DISCOVERY_NOTICE_SIZE)
                    off, va = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
                    data[off:off + CITY_DISCOVERY_NOTICE_SLOT_SIZE] = _payload(va, 6, old_colors)
                    pe = pefile.PE(data=bytes(data), fast_load=True)
                    try:
                        map_offset = pe.get_offset_from_rva(MAP_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase)
                    finally:
                        pe.close()
                    data[map_offset:map_offset + len(MAP_ORIGINAL)] = _map_hook(va)
                    self.assertTrue(read_city_discovery_notice_patch_state(data))
                    self.assertEqual(read_city_label_colors(data), old_colors)
                    self.assertEqual(read_city_label_outline(data), DEFAULT_LABEL_OUTLINE)
                    self.assertTrue(apply_city_discovery_notice_patch(data, enabled, outline=outline))
                    self.assertEqual(read_city_discovery_notice_patch_state(data), enabled)
                    self.assertEqual(read_city_label_colors(data), old_colors if enabled else DEFAULT_LABEL_COLORS)
                    self.assertEqual(read_city_label_outline(data), outline or DEFAULT_LABEL_OUTLINE)
                    self.assertFalse(apply_city_discovery_notice_patch(data, enabled, outline=outline))
                    if enabled:
                        self.assertEqual(
                            data[off:off + CITY_DISCOVERY_NOTICE_SLOT_SIZE],
                            _payload(va, 8, old_colors, outline or DEFAULT_LABEL_OUTLINE),
                        )
                    else:
                        self.assertFalse(any(data[off:off + CITY_DISCOVERY_NOTICE_SLOT_SIZE]))

    def test_v6_integrated_coordinate_migration_preserves_colors_and_outline(self):
        from patch_cds_integrated import apply_all, read_settings

        colors = (10, 21, 32, 43, 54, 73)
        for outline in (None, (2, 42)):
            with self.subTest(outline=outline), tempfile.TemporaryDirectory() as directory:
                data = bytearray(FIXTURE.read_bytes())
                apply_city_discovery_notice_patch(data, True, colors)
                section = find_patch_section(data)
                off, va = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
                data[off:off + CITY_DISCOVERY_NOTICE_SLOT_SIZE] = _payload(va, 6, colors)
                target = Path(directory) / "CDS_95.EXE"
                target.write_bytes(data)
                for style in ("korean3", "original"):
                    settings = read_settings(target)
                    apply_all(
                        target, style, True, settings[1], *settings[2:],
                        city_discovery_notice_enabled=True,
                        city_label_outline=outline if style == "korean3" else None,
                    )
                    saved = target.read_bytes()
                    self.assertTrue(read_city_discovery_notice_patch_state(saved))
                    self.assertEqual(read_city_label_colors(saved), colors)
                    self.assertEqual(read_city_label_outline(saved), (1, outline[1]) if outline else DEFAULT_LABEL_OUTLINE)

    def test_v7_payload_is_byte_exact_and_migrates_preserving_colors_and_outline(self):
        for colors, outline, digest in (
            (DEFAULT_LABEL_COLORS, DEFAULT_LABEL_OUTLINE,
             "526ad2fc06f84c9ddef8cdc7bcbf4319bb174bb0414ec078e7eb9f8952be75f3"),
            ((10, 21, 32, 43, 54, 73), (3, 21),
             "0cd2024722cf82b73da0d17ff8605dbd16ae5d439240db6cb6f037115872c8b5"),
        ):
            # Captured from the shipped v7 generator before country visibility.
            self.assertEqual(hashlib.sha256(_payload(0x700000, 7, colors, outline)).hexdigest(), digest)
            for enabled, show_nation in ((True, None), (True, False), (False, None)):
                with self.subTest(colors=colors, outline=outline, enabled=enabled, show_nation=show_nation):
                    data = bytearray(FIXTURE.read_bytes())
                    apply_city_discovery_notice_patch(data, True)
                    section = find_patch_section(data)
                    off, va = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
                    data[off:off + CITY_DISCOVERY_NOTICE_SLOT_SIZE] = _payload(va, 7, colors, outline)
                    self.assertIs(read_city_label_show_nation(data), True)
                    self.assertEqual(read_city_label_colors(data), colors)
                    self.assertEqual(read_city_label_outline(data), outline)
                    self.assertTrue(apply_city_discovery_notice_patch(data, enabled, show_nation=show_nation))
                    self.assertEqual(read_city_discovery_notice_patch_state(data), enabled)
                    self.assertIs(read_city_label_show_nation(data), show_nation if enabled and show_nation is not None else True)
                    self.assertEqual(read_city_label_colors(data), colors if enabled else DEFAULT_LABEL_COLORS)
                    self.assertEqual(read_city_label_outline(data), outline if enabled else DEFAULT_LABEL_OUTLINE)
                    self.assertFalse(apply_city_discovery_notice_patch(data, enabled, show_nation=show_nation))
                    if enabled:
                        self.assertEqual(struct.unpack_from("<I", data, off + 8)[0], 8)

    def test_every_legacy_version_defaults_to_showing_nation(self):
        for version in range(1, 8):
            with self.subTest(version=version):
                data = bytearray(FIXTURE.read_bytes())
                section, _ = ensure_patch_section(data, PATCH_SECTION_CITY_DISCOVERY_NOTICE_SIZE)
                off, va = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
                payload = _popup_payload(va, 1) if version == 1 else _payload(va, version)
                data[off:off + len(payload)] = payload
                if version <= 2:
                    for hook_offset, (hook, _, wrapper, _) in zip(_offsets(data), HOOKS):
                        data[hook_offset:hook_offset + 5] = _call(hook, va + wrapper)
                if version >= 2:
                    pe = pefile.PE(data=bytes(data), fast_load=True)
                    try:
                        map_offset = pe.get_offset_from_rva(MAP_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase)
                    finally:
                        pe.close()
                    data[map_offset:map_offset + len(MAP_ORIGINAL)] = _map_hook(va)
                self.assertTrue(read_city_discovery_notice_patch_state(data))
                self.assertIs(read_city_label_show_nation(data), True)

    def test_v7_integrated_save_normalizes_outline_toggle_and_preserves_color(self):
        from patch_cds_integrated import apply_all, read_settings

        colors = (10, 21, 32, 43, 54, 73)
        for width in range(4):
            with self.subTest(width=width), tempfile.TemporaryDirectory() as directory:
                data = bytearray(FIXTURE.read_bytes())
                apply_city_discovery_notice_patch(data, True)
                section = find_patch_section(data)
                off, va = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
                data[off:off + CITY_DISCOVERY_NOTICE_SLOT_SIZE] = _payload(va, 7, colors, (width, 21))
                target = Path(directory) / "CDS_95.EXE"
                target.write_bytes(data)
                settings = read_settings(target)
                apply_all(target, "korean3", True, settings[1], *settings[2:], city_discovery_notice_enabled=True)
                saved = target.read_bytes()
                self.assertEqual(read_city_label_colors(saved), colors)
                self.assertEqual(read_city_label_outline(saved), (0 if width == 0 else 1, 21))
                self.assertIs(read_city_label_show_nation(saved), True)

    def test_all_outline_widths_and_palette_colors_preserve_shared_native_text(self):
        data = bytearray(FIXTURE.read_bytes())
        pe = pefile.PE(data=bytes(data), fast_load=True)
        try:
            regions = [
                (pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase), size)
                for va, size in ((PIXEL_TEXT_VA, 0x70), (0x52F658, 16))
            ]
        finally:
            pe.close()
        originals = [bytes(data[off:off + size]) for off, size in regions]
        colors = (10, 21, 32, 43, 54, 73)
        for width in range(4):
            for color in range(10, 74):
                with self.subTest(width=width, color=color):
                    apply_city_discovery_notice_patch(data, True, colors, (width, color))
                    self.assertEqual(read_city_label_outline(data), (width, color))
                    self.assertEqual(read_city_label_colors(data), colors)
                    for (off, size), original in zip(regions, originals):
                        self.assertEqual(bytes(data[off:off + size]), original)
        apply_city_discovery_notice_patch(data, False)
        for (off, size), original in zip(regions, originals):
            self.assertEqual(bytes(data[off:off + size]), original)

    def test_invalid_stored_color_and_corrupted_code_are_rejected_by_every_reader(self):
        for relative_offset, replacement in (
            (COLORS_OFFSET, 9), (COLORS_OFFSET + 5, 74),
            (OUTLINE_SETTINGS_OFFSET, 4), (OUTLINE_SETTINGS_OFFSET, 255),
            (OUTLINE_SETTINGS_OFFSET + 1, 9), (OUTLINE_SETTINGS_OFFSET + 1, 74),
            (OUTLINE_SETTINGS_OFFSET, 0), (OUTLINE_SETTINGS_OFFSET + 1, 10),
            (SHOW_NATION_OFFSET, 0), (SHOW_NATION_OFFSET, 2), (SHOW_NATION_OFFSET, 255),
            (0xA0, None), (OUTLINE_TEXT_OFFSET, None),
        ):
            with self.subTest(relative_offset=relative_offset, replacement=replacement):
                data = bytearray(FIXTURE.read_bytes())
                apply_city_discovery_notice_patch(data, True, (10, 21, 32, 43, 54, 73))
                section = find_patch_section(data)
                off, _ = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
                if replacement is None:
                    data[off + relative_offset] ^= 1
                else:
                    data[off + relative_offset] = replacement
                corrupt = bytes(data)
                for reader in (read_city_discovery_notice_patch_state, read_city_label_colors, read_city_label_outline, read_city_label_show_nation):
                    with self.assertRaises(ValueError):
                        reader(data)
                for enabled in (True, False):
                    with self.assertRaises(ValueError):
                        apply_city_discovery_notice_patch(data, enabled)
                    self.assertEqual(bytes(data), corrupt)

    def test_zero_width_outline_still_validates_its_stored_palette_color(self):
        for invalid_color in (9, 74):
            with self.subTest(invalid_color=invalid_color):
                data = bytearray(FIXTURE.read_bytes())
                apply_city_discovery_notice_patch(data, True, outline=(0, 42))
                section = find_patch_section(data)
                off, _ = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
                data[off + OUTLINE_SETTINGS_OFFSET + 1] = invalid_color
                corrupt = bytes(data)
                for reader in (read_city_discovery_notice_patch_state, read_city_label_colors, read_city_label_outline, read_city_label_show_nation):
                    with self.assertRaises(ValueError):
                        reader(data)
                for enabled in (False, True):
                    with self.assertRaises(ValueError):
                        apply_city_discovery_notice_patch(data, enabled)
                    self.assertEqual(bytes(data), corrupt)

    def test_color_edits_and_disable_preserve_existing_high_speed_slot(self):
        from high_speed_map_patch import apply_high_speed_map_fix, read_high_speed_map_fix_state

        data = bytearray(FIXTURE.read_bytes())
        apply_high_speed_map_fix(data, True)
        section = find_patch_section(data)
        off, _ = section.slot(HIGH_SPEED_MAP_FIX_SLOT_OFFSET, HIGH_SPEED_MAP_FIX_SLOT_SIZE)
        high_speed_payload = bytes(data[off:off + HIGH_SPEED_MAP_FIX_SLOT_SIZE])
        for enabled, colors, outline in (
            (True, (10, 21, 32, 43, 54, 73), (3, 21)),
            (True, (73, 62, 51, 40, 29, 10), (0, 42)),
            (False, None, None), (True, None, None),
        ):
            apply_city_discovery_notice_patch(data, enabled, colors, outline)
            self.assertTrue(read_high_speed_map_fix_state(data))
            self.assertEqual(bytes(data[off:off + HIGH_SPEED_MAP_FIX_SLOT_SIZE]), high_speed_payload)

    def test_altered_native_treaty_function_rejects_before_mutating(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                data = bytearray(FIXTURE.read_bytes())
                if enabled:
                    apply_city_discovery_notice_patch(data, True)
                pe = pefile.PE(data=bytes(data), fast_load=True)
                try:
                    off = pe.get_offset_from_rva(TREATY_TEST_VA - pe.OPTIONAL_HEADER.ImageBase)
                finally:
                    pe.close()
                data[off] ^= 1
                unsupported = bytes(data)
                with self.assertRaises(ValueError):
                    apply_city_discovery_notice_patch(data, True, (10, 21, 32, 43, 54, 73))
                self.assertEqual(bytes(data), unsupported)
                if enabled:
                    for reader in (read_city_discovery_notice_patch_state, read_city_label_colors, read_city_label_outline, read_city_label_show_nation):
                        with self.assertRaises(ValueError):
                            reader(data)

    def test_rejects_original_map_hook_with_payload_and_corrupted_payload(self):
        data = bytearray(FIXTURE.read_bytes())
        apply_city_discovery_notice_patch(data, True)
        pe = pefile.PE(data=bytes(data), fast_load=True)
        try:
            map_offset = pe.get_offset_from_rva(MAP_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase)
        finally:
            pe.close()
        data[map_offset:map_offset + len(MAP_ORIGINAL)] = MAP_ORIGINAL
        with self.assertRaises(ValueError):
            read_city_discovery_notice_patch_state(data)
        data = bytearray(FIXTURE.read_bytes())
        apply_city_discovery_notice_patch(data, True)
        section = find_patch_section(data)
        off, _ = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
        data[off + 0xA0] ^= 1
        with self.assertRaises(ValueError):
            read_city_discovery_notice_patch_state(data)

    def test_keeps_preexisting_patch_payloads(self):
        data = bytearray(FIXTURE.read_bytes())
        section, _ = ensure_patch_section(data, PATCH_SECTION_CITY_DISCOVERY_NOTICE_SIZE)
        marker = b"unrelated feature"
        off, _ = section.slot(0x4F200, len(marker))
        data[off:off + len(marker)] = marker
        for enabled in (True, False, True):
            apply_city_discovery_notice_patch(data, enabled)
            self.assertEqual(data[off:off + len(marker)], marker)


@unittest.skipIf(Uc is None, "Install unicorn to execute the x86 discovery branches")
class CityDiscoveryNoticeExecutionTests(unittest.TestCase):
    def _run(self, speaker, city_id=0, name=None, no_discovery=False):
        data = bytearray(FIXTURE.read_bytes())
        apply_city_discovery_notice_patch(data, True)
        pe = pefile.PE(data=bytes(data))
        try:
            mapped = pe.get_memory_mapped_image()
            base = pe.OPTIONAL_HEADER.ImageBase
            size = (pe.OPTIONAL_HEADER.SizeOfImage + 0xFFF) & ~0xFFF
        finally:
            pe.close()
        machine = Uc(UC_ARCH_X86, UC_MODE_32)
        machine.mem_map(base, size)
        machine.mem_write(base, mapped)
        stack_base = 0x10000000
        machine.mem_map(stack_base, 0x20000)
        initial_sp = stack_base + 0x18000
        if name is not None:
            name_va = stack_base + 0x100
            machine.mem_write(name_va, name.encode("cp949") + b"\0")
            machine.mem_write(0x4D14B0 + city_id * 0x88, struct.pack("<I", name_va))
        registers = {
            UC_X86_REG_EBX: 0x11112222,
            UC_X86_REG_ECX: 0x33334444,
            UC_X86_REG_EDX: 0x55556666,
            UC_X86_REG_ESI: 0x77778888,
            UC_X86_REG_EDI: 0x12345678,
            UC_X86_REG_EBP: city_id,
        }
        for reg, value in registers.items():
            machine.reg_write(reg, value)
        machine.reg_write(UC_X86_REG_ESP, initial_sp)
        machine.reg_write(UC_X86_REG_EAX, 0 if speaker else 0xFFFFFFFF)
        machine.reg_write(UC_X86_REG_EFLAGS, 0x202)
        messages = []
        original_result = 0x55667788
        actor = 0x586F00

        def words(address, count):
            return struct.unpack(f"<{count}I", machine.mem_read(address, count * 4))

        def text(address):
            result = bytearray()
            while True:
                value = machine.mem_read(address, 1)
                if value == b"\0":
                    return result.decode("cp949")
                result += value
                address += 1

        def returned(result, popped=0):
            sp = machine.reg_read(UC_X86_REG_ESP)
            return_address = words(sp, 1)[0]
            machine.reg_write(UC_X86_REG_EAX, result)
            machine.reg_write(UC_X86_REG_ESP, sp + 4 + popped)
            machine.reg_write(UC_X86_REG_EIP, return_address)

        def intercept(uc, address, size, context):
            if address == 0x48DA19:
                uc.emu_stop()
                return
            sp = uc.reg_read(UC_X86_REG_ESP)
            if address == 0x47CC60:
                self.assertEqual(words(sp + 4, 2), (0, 1))
                returned(actor, 8)
            elif address == SPEECH_POPUP_VA:
                args = words(sp + 4, 4)
                self.assertEqual(args[:3], (actor, 0, 0))
                messages.append(text(args[3]))
                returned(original_result)
            elif address == COMMON_POPUP_VA:
                args = words(sp + 4, 3)
                self.assertEqual(args[:2], (0, 0))
                fmt = text(args[2])
                self.assertEqual(fmt, "도시를 발견했습니다!")
                messages.append(fmt)
                returned(original_result)

        machine.hook_add(UC_HOOK_CODE, intercept)
        machine.emu_start(0x48D97E if no_discovery else 0x48D9E1, 0x48DA29, count=30000)
        self.assertEqual(machine.reg_read(UC_X86_REG_EIP), 0x48DA19)
        self.assertEqual(machine.reg_read(UC_X86_REG_ESP), initial_sp)
        for reg in (UC_X86_REG_EBX, UC_X86_REG_EDX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP):
            self.assertEqual(machine.reg_read(reg), registers[reg])
        if not no_discovery:
            self.assertEqual(machine.reg_read(UC_X86_REG_EAX), original_result)
        return messages

    def test_only_original_speech_is_shown(self):
        self.assertEqual(self._run(True), ["제독, 도시가 보입니다!"])

    def test_only_original_generic_notice_is_shown(self):
        self.assertEqual(self._run(False, 14), ["도시를 발견했습니다!"])

    def test_renamed_city_does_not_add_a_popup(self):
        self.assertEqual(self._run(True, name="새도시"), ["제독, 도시가 보입니다!"])

    def test_no_new_city_shows_no_popup(self):
        self.assertEqual(self._run(False, no_discovery=True), [])


@unittest.skipIf(Uc is None, "Install unicorn to execute the x86 map label hook")
class CityMapLabelExecutionTests(unittest.TestCase):
    def _run_map(
        self, cities, origin=(90, 190), dimensions=(40, 20), nations=None,
        colors=None, affiliation=0, year=1493, policies=None, include_colors=False,
        outline=None, show_nation=True,
    ):
        data = bytearray(FIXTURE.read_bytes())
        apply_city_discovery_notice_patch(data, True, colors, outline, show_nation)
        section = find_patch_section(data)
        _, slot_va = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
        outline_va = slot_va + OUTLINE_TEXT_OFFSET
        pe = pefile.PE(data=bytes(data))
        try:
            mapped = pe.get_memory_mapped_image()
            base = pe.OPTIONAL_HEADER.ImageBase
            size = (pe.OPTIONAL_HEADER.SizeOfImage + 0xFFF) & ~0xFFF
        finally:
            pe.close()
        uc = Uc(UC_ARCH_X86, UC_MODE_32)
        uc.mem_map(base, size)
        uc.mem_write(base, mapped)
        memory = 0x10000000
        uc.mem_map(memory, 0x30000)
        widget, cache, initial_sp = memory + 0x8000, memory + 0xA000, memory + 0x28000
        width, height = dimensions

        def write_words(address, *values):
            uc.mem_write(address, struct.pack(f"<{len(values)}I", *values))

        def words(address, count):
            return struct.unpack(f"<{count}I", uc.mem_read(address, count * 4))

        def text(address):
            result = bytearray()
            while uc.mem_read(address, 1) != b"\0":
                result += uc.mem_read(address, 1)
                address += 1
            return result.decode("cp949")

        write_words(widget + 0xEC, width, height)
        write_words(widget + 0x28, 12, 16)
        write_words(widget + 0x54, 4, 32)
        write_words(widget + 0xC8, width * height * 2)
        write_words(widget + 0xD0, cache)
        uc.mem_write(widget + 0xD4, b"\x04")  # byte buffer underlying u16 cache
        uc.mem_write(cache - 16, b"\xA5" * (max(width * height * 2, 0) + 32))
        write_words(0x5B63A8, *origin)
        write_words(0x62B2C8, 111, 222)  # previous global drawing origin
        write_words(0x5B394C, affiliation & 0xFFFFFFFF)
        write_words(0x5A4D20, year)
        for identifier in range(78):
            write_words(0x5859C0 + identifier * 0x10 + 0x0C, 0)
        for identifier, hostility in (policies or {}).items():
            write_words(0x5859C0 + identifier * 0x10 + 0x0C, hostility & 0xFFFFFFFF)
        for identifier in range(226):
            uc.mem_write(0x5863A8 + identifier * 0x5C + 4, b"\0\0")
        for identifier, city in enumerate(cities):
            x, y, flags, name, footprint = city[:5]
            nation_id = city[5] if len(city) > 5 else -1
            runtime = 0x5863A8 + identifier * 0x5C
            write_words(runtime, nation_id & 0xFFFFFFFF)
            uc.mem_write(runtime + 4, struct.pack("<H", flags))
            master = 0x4D14B0 + identifier * 0x88
            name_pointer = memory + 0x100 + identifier * 0x100
            uc.mem_write(name_pointer, name.encode("cp949") + b"\0")
            write_words(master, name_pointer, x, y, footprint)
            write_words(master + 0x24, 1)  # deliberately differs from current nation
        for identifier, name in (nations or {}).items():
            pointer = memory + 0x20000 + identifier * 0x100
            uc.mem_write(pointer, name.encode("cp949") + b"\0")
            write_words(NATION_TABLE_VA + identifier * 24, pointer)
        registers = {
            UC_X86_REG_EAX: 0x10203040,
            UC_X86_REG_EBX: 0x12345678,
            UC_X86_REG_EDX: 0xABCDEF00,
            UC_X86_REG_ESI: 0x33445566,
            UC_X86_REG_EDI: 0x77889900,
            UC_X86_REG_EBP: 0xDEADBEEF,
        }
        for register, value in registers.items():
            uc.reg_write(register, value)
        uc.reg_write(UC_X86_REG_ESP, initial_sp)
        uc.reg_write(UC_X86_REG_EFLAGS, 0x202)
        write_words(initial_sp + 0x14, widget)
        labels, pixels = [], []

        def returned(popped):
            sp = uc.reg_read(UC_X86_REG_ESP)
            uc.reg_write(UC_X86_REG_EIP, words(sp, 1)[0])
            uc.reg_write(UC_X86_REG_ESP, sp + 4 + popped)

        def intercept(machine, address, size, context):
            sp = machine.reg_read(UC_X86_REG_ESP)
            if address == MAP_RESUME_VA:
                machine.emu_stop()
            elif address == outline_va:
                x, y, name, color = words(sp + 4, 4)
                if colors is None:
                    self.assertEqual(color, 10)
                label = (x, y, text(name))
                labels.append((*label, color) if include_colors else label)
                # Execute the actual outline helper, native colour setter,
                # and graphics code. Only the final hardware draw is stubbed.
            elif address == PIXEL_TEXT_VA:
                self.fail("map labels must use their own outline helper")
            elif address == 0x4B6071:
                name, limit = words(sp + 4, 2)
                self.assertEqual(limit, 0x7FFFFFFF)
                pixel = (
                    *words(0x62B2D0, 2), *words(0x62B2C8, 2), text(name),
                )
                pixels.append((*pixel, words(0x580120, 1)[0]) if include_colors else pixel)
                returned(8)
            elif address == 0x4B9628:
                returned(0)  # final graphics-origin update; no hardware surface

        uc.hook_add(UC_HOOK_CODE, intercept)
        if not show_nation:
            def reject_nation_lookup(machine, access, address, size, value, context):
                self.fail("hidden country names must not read the nation-name table")

            uc.hook_add(
                UC_HOOK_MEM_READ, reject_nation_lookup,
                begin=NATION_TABLE_VA, end=NATION_TABLE_VA + 78 * 24 - 1,
            )
        uc.emu_start(MAP_HOOK_VA, MAP_RESUME_VA + 1, count=100000)
        self.assertEqual(uc.reg_read(UC_X86_REG_EIP), MAP_RESUME_VA)
        self.assertEqual(uc.reg_read(UC_X86_REG_ESP), initial_sp)
        self.assertEqual(uc.reg_read(UC_X86_REG_ECX), widget)  # displaced MOV
        for register, value in registers.items():
            self.assertEqual(uc.reg_read(register), value)
        self.assertEqual(words(0x62B2C8, 2), (111, 222))
        self.assertEqual(bytes(uc.mem_read(cache - 16, 16)), b"\xA5" * 16)
        self.assertEqual(bytes(uc.mem_read(cache + width * height * 2, 16)), b"\xA5" * 16)
        cache_data = bytes(uc.mem_read(cache, max(width * height * 2, 0)))
        return labels, pixels, cache_data

    def test_only_visible_discovered_and_not_hidden_cities_are_named(self):
        labels, pixels, cache = self._run_map([
            (100, 200, 1, "리스본", 3),
            (105, 200, 0, "미발견", 3),
            (105, 200, 5, "숨김", 3),
            (200, 200, 1, "화면 밖", 3),
            (100, 300, 1, "화면 아래", 3),
            (105, 200, 1, "", 3),
        ])
        self.assertEqual(labels, [(160, 144, "리스본")])
        self.assertEqual(len(pixels), 8)
        self.assertTrue(all(point[2:4] == (16, 48) for point in pixels))
        self.assertEqual(pixels[6][:2], (160, 144))  # unchanged foreground origin
        self.assertEqual(cache[:7 * 40 * 2], b"\xA5" * (7 * 40 * 2))
        self.assertEqual(cache[7 * 40 * 2:11 * 40 * 2], b"\xFF" * (4 * 40 * 2))
        self.assertEqual(cache[11 * 40 * 2:], b"\xA5" * (9 * 40 * 2))

    def test_scrolling_moves_labels_and_runtime_renames_are_used(self):
        cities = [(100, 200, 1, "새도시", 3)]
        self.assertEqual(self._run_map(cities)[0], [(160, 144, "새도시")])
        self.assertEqual(self._run_map(cities, origin=(92, 191))[0], [(128, 128, "새도시")])
        self.assertEqual(self._run_map(cities, origin=(110, 190))[0], [])

    def test_world_wrap_partial_cities_and_edge_clamping(self):
        self.assertEqual(
            self._run_map([(1, 10, 1, "리스본", 3)], origin=(2499, 7))[0],
            [(32, 32, "리스본")],
        )
        self.assertEqual(
            self._run_map([(99, 190, 1, "리스본", 3)], origin=(100, 190))[0],
            [(1, 19, "리스본")],
        )
        self.assertEqual(
            self._run_map([(128, 209, 1, "긴도시이름", 3)], dimensions=(40, 20))[0],
            [(558, 288, "긴도시이름")],
        )

    def test_empty_view_does_not_draw_or_write_cache(self):
        self.assertEqual(
            self._run_map([(100, 200, 1, "리스본", 3)], dimensions=(0, 0))[0], [],
        )

    def test_city_and_current_nation_share_exact_pixel_centre(self):
        labels, pixels, cache = self._run_map([(100, 200, 1, "리스본", 3, 0)])
        self.assertEqual(labels, [(160, 144, "리스본"), (124, 126, "[포르투갈 왕국]")])
        self.assertEqual(len(pixels), 16)
        for x, y, name in labels:
            self.assertEqual(x + len(name.encode("cp949")) * 4, 184)

    def test_hidden_nation_draws_only_city_and_invalidates_only_its_rows(self):
        for radius in range(4):
            with self.subTest(radius=radius):
                labels, pixels, cache = self._run_map(
                    [(100, 200, 1, "리스본", 3, 0)],
                    outline=(radius, 21), show_nation=False,
                )
                self.assertEqual(labels, [(160, 144, "리스본")])
                self.assertEqual(len(pixels), (2, 8, 18, 32)[radius])
                self.assertTrue(all(stroke[4] == "리스본" for stroke in pixels))
                first = (144 - radius) // 16
                last = (144 + 15 + radius) // 16 + 1
                expected_cache = (
                    b"\xA5" * (first * 40 * 2)
                    + b"\xFF" * ((last - first) * 40 * 2)
                    + b"\xA5" * ((20 - last) * 40 * 2)
                )
                self.assertEqual(cache, expected_cache)

    def test_hidden_nation_keeps_current_relationship_and_native_treaty_city_colors(self):
        colors = (10, 21, 32, 43, 54, 73)
        for year, affiliation, nation, policy, expected in (
            (1493, 2, 2, 1, 43),
            (1493, 2, 3, -1, 54),
            (1493, 2, 3, 0, 54),
            (1493, 2, 77, 1, 73),
            (1493, 0, 1, 0, 54),
            (1494, 0, 1, 0, 73),
            (1494, 1, 0, 0, 73),
            (1494, 2, 0, 0, 54),
            (1494, 0, 0, 1, 43),
            (1494, 78, 78, None, 54),
            (1494, -1, -1, None, 54),
        ):
            with self.subTest(year=year, affiliation=affiliation, nation=nation, policy=policy):
                labels, pixels, _ = self._run_map(
                    [(100, 200, 1, "도시", 3, nation)], colors=colors,
                    affiliation=affiliation, year=year,
                    policies={nation: policy} if policy is not None else None,
                    include_colors=True, show_nation=False,
                )
                self.assertEqual(labels, [(168, 144, "도시", expected)])
                self.assertEqual(len(pixels), 8)
                self.assertEqual([stroke[5] for stroke in pixels], [73] * 6 + [expected] * 2)

    def test_hidden_nation_top_edges_all_outline_widths_and_exact_cache_bounds(self):
        for radius in range(4):
            for x, y, dimensions, name in (
                (128, 188, (40, 20), "리스본"),
                (128, 190, (40, 20), "리스본"),
                (128, 191, (40, 20), "리스본"),
                (128, 192, (40, 20), "리스본"),
                (90, 209, (40, 20), "A가"),
                (92, 190, (4, 3), "ABC"),
                (92, 192, (4, 3), "ABC"),
            ):
                with self.subTest(radius=radius, x=x, y=y, dimensions=dimensions):
                    labels, pixels, cache = self._run_map(
                        [(x, y, 1, name, 3, 0)], dimensions=dimensions,
                        outline=(radius, 21), show_nation=False,
                    )
                    self.assertEqual(len(labels), 1)
                    self.assertEqual(len(pixels), (2, 8, 18, 32)[radius])
                    width, height = dimensions
                    label_x, label_y, _ = labels[0]
                    expected_y = min(max((y - 190 - 1) * 16, radius), height * 16 - 16 - radius)
                    self.assertEqual(label_y, expected_y)
                    glyph_width = len(name.encode("cp949")) * 8
                    self.assertEqual(min(stroke[0] for stroke in pixels), label_x - radius)
                    self.assertEqual(max(stroke[0] + glyph_width - 1 for stroke in pixels), label_x + glyph_width + radius)
                    self.assertEqual(min(stroke[1] for stroke in pixels), label_y - radius)
                    self.assertEqual(max(stroke[1] + 15 for stroke in pixels), label_y + 15 + radius)
                    for stroke_x, stroke_y, *_ in pixels:
                        self.assertGreaterEqual(stroke_x, 0)
                        self.assertGreaterEqual(stroke_y, 0)
                        self.assertLess(stroke_x + glyph_width - 1, width * 16)
                        self.assertLess(stroke_y + 15, height * 16)
                    first = (label_y - radius) // 16
                    last = (label_y + 15 + radius) // 16 + 1
                    self.assertEqual(
                        cache,
                        b"\xA5" * (first * width * 2)
                        + b"\xFF" * ((last - first) * width * 2)
                        + b"\xA5" * ((height - last) * width * 2),
                    )

    def test_hidden_nation_keeps_minimum_three_row_viewport_requirement(self):
        for height in (0, 1, 2):
            with self.subTest(height=height):
                labels, pixels, cache = self._run_map(
                    [(91, 190, 1, "ABC", 3, 0)], dimensions=(4, height),
                    show_nation=False,
                )
                self.assertEqual(labels, [])
                self.assertEqual(pixels, [])
                self.assertEqual(cache, b"\xA5" * (4 * height * 2))

    def test_all_six_colors_follow_current_city_nation_and_player_affiliation(self):
        colors = (10, 21, 32, 43, 54, 73)
        labels, pixels, _ = self._run_map(
            [(100, 200, 1, "소속", 3, 2), (105, 200, 1, "우호", 3, 3),
             (110, 200, 1, "적대", 3, 77)],
            nations={2: "소속국", 3: "우호국", 77: "적대국"},
            colors=colors, affiliation=2, policies={2: 1, 77: 1}, include_colors=True,
        )
        self.assertEqual([(label[2], label[3]) for label in labels], [
            ("소속", 43), ("[소속국]", 10),
            ("우호", 54), ("[우호국]", 21),
            ("적대", 73), ("[적대국]", 32),
        ])
        for city_label, nation_label in zip(labels[::2], labels[1::2]):
            self.assertEqual(
                city_label[0] + len(city_label[2].encode("cp949")) * 4,
                nation_label[0] + len(nation_label[2].encode("cp949")) * 4,
            )
        self.assertEqual(len(pixels), len(labels) * 8)
        for index, label in enumerate(labels):
            strokes = pixels[index * 8:index * 8 + 8]
            self.assertEqual([stroke[5] for stroke in strokes], [73] * 6 + [label[3]] * 2)
            self.assertEqual(
                [(stroke[0] - label[0], stroke[1] - label[1]) for stroke in strokes],
                [(-1, 0), (2, 0), (0, -1), (1, -1), (0, 1), (1, 1), (0, 0), (1, 0)],
            )
            self.assertTrue(all(stroke[2:4] == (16, 48) for stroke in strokes))
            self.assertTrue(all(stroke[4] == label[2] for stroke in strokes))

    def test_common_outline_width_and_color_apply_to_all_six_label_styles(self):
        colors = (10, 21, 32, 43, 54, 73)
        for width in range(4):
            for outline_color in (10, 42, 73):
                with self.subTest(width=width, outline_color=outline_color):
                    labels, pixels, _ = self._run_map(
                        [(100, 200, 1, "소속", 3, 2), (105, 200, 1, "우호", 3, 3),
                         (110, 200, 1, "적대", 3, 77)],
                        nations={2: "소속국", 3: "우호국", 77: "적대국"},
                        colors=colors, affiliation=2, policies={77: 1},
                        include_colors=True, outline=(width, outline_color),
                    )
                    self.assertEqual([label[3] for label in labels], [43, 10, 54, 21, 73, 32])
                    count = (2, 8, 18, 32)[width]
                    self.assertEqual(len(pixels), len(labels) * count)
                    # Every Manhattan-distance neighbour of the original
                    # two-pixel bold glyph must be painted once, then filled.
                    expected = {
                        (dx, dy)
                        for dx in range(-width, width + 2)
                        for dy in range(-width, width + 1)
                        if min(abs(dx), abs(dx - 1)) + abs(dy) <= width
                    }
                    for index, label in enumerate(labels):
                        strokes = pixels[index * count:(index + 1) * count]
                        offsets = [(stroke[0] - label[0], stroke[1] - label[1]) for stroke in strokes]
                        self.assertEqual(set(offsets), expected)
                        self.assertEqual(len(set(offsets)), count)
                        self.assertEqual(offsets[-2:], [(0, 0), (1, 0)])
                        self.assertEqual(
                            [stroke[5] for stroke in strokes],
                            [outline_color] * (count - 2) + [label[3]] * 2,
                        )
                        if width >= 2:
                            self.assertEqual(offsets[:-2], sorted(offsets[:-2], key=lambda point: (point[1], point[0])))
                        self.assertTrue(all(stroke[2:4] == (16, 48) for stroke in strokes))
                        self.assertTrue(all(stroke[4] == label[2] for stroke in strokes))

    def test_policy_requires_a_positive_value_and_own_nation_takes_priority(self):
        colors = (10, 21, 32, 43, 54, 73)
        for affiliation, nation, policy, expected in (
            (2, 3, -1, (54, 21)), (2, 3, 0, (54, 21)),
            (2, 3, 1, (73, 32)), (2, 3, 0x7FFFFFFF, (73, 32)),
            (2, 2, 1, (43, 10)), (0, 0, 1, (43, 10)),
        ):
            with self.subTest(affiliation=affiliation, nation=nation, policy=policy):
                labels = self._run_map(
                    [(100, 200, 1, "도시", 3, nation)], colors=colors,
                    affiliation=affiliation, year=1494, policies={nation: policy},
                    include_colors=True,
                )[0]
                self.assertEqual(tuple(label[3] for label in labels), expected)

    def test_native_treaty_hostility_depends_on_year_and_current_affiliation(self):
        colors = (10, 21, 32, 43, 54, 73)
        for year, affiliation, nation, expected in (
            (1493, 0, 1, (54, 21)), (1494, 0, 1, (73, 32)),
            (1493, 1, 0, (54, 21)), (1494, 1, 0, (73, 32)),
            (1500, 0, 1, (73, 32)), (1500, 1, 0, (73, 32)),
            (1494, 0, 0, (43, 10)), (1494, 1, 1, (43, 10)),
            (1494, 2, 0, (54, 21)), (1494, 2, 1, (54, 21)),
            (1494, 0, 2, (54, 21)), (1494, 1, 2, (54, 21)),
        ):
            with self.subTest(year=year, affiliation=affiliation, nation=nation):
                labels = self._run_map(
                    [(100, 200, 1, "도시", 3, nation)], colors=colors,
                    affiliation=affiliation, year=year, include_colors=True,
                )[0]
                self.assertEqual(tuple(label[3] for label in labels), expected)

    def test_invalid_nations_use_friendly_city_color_even_if_matching_affiliation(self):
        colors = (10, 21, 32, 43, 54, 73)
        for nation in (-1, 78, 0x7FFFFFFF):
            for affiliation in (0, nation):
                with self.subTest(nation=nation, affiliation=affiliation):
                    labels = self._run_map(
                        [(100, 200, 1, "도시", 3, nation)], colors=colors,
                        affiliation=affiliation, year=1494, include_colors=True,
                    )[0]
                    self.assertEqual(labels, [(168, 144, "도시", 54)])

    def test_missing_nation_name_keeps_city_relationship_color(self):
        colors = (10, 21, 32, 43, 54, 73)
        for name in ("", "가" * 64):
            labels = self._run_map(
                [(100, 200, 1, "도시", 3, 77)], nations={77: name},
                colors=colors, policies={77: 1}, include_colors=True,
            )[0]
            self.assertEqual(labels, [(168, 144, "도시", 73)])

    def test_runtime_nation_changes_and_last_nation_are_used(self):
        self.assertEqual(self._run_map([(100, 200, 1, "도시", 3, 77)])[0],
                         [(168, 144, "도시"), (140, 126, "[잉카 제국]")])
        self.assertEqual(self._run_map([(100, 200, 1, "도시", 3, 0)], nations={0: "새 국가"})[0],
                         [(168, 144, "도시"), (148, 126, "[새 국가]")])

    def test_ascii_mixed_text_and_two_tile_city_are_pixel_centred(self):
        for name in ("ABC", "A가", "남경"):
            labels = self._run_map([(100, 200, 1, name, 2, 0)], nations={0: "Q"})[0]
            self.assertEqual(labels[1], (164, 126, "[Q]"))
            for x, y, label in labels:
                self.assertEqual(x + len(label.encode("cp949")) * 4, 176)

    def test_country_is_bounded_and_invalid_ids_leave_city_name(self):
        for identifier in (-1, 78):
            self.assertEqual(self._run_map([(100, 200, 1, "리스본", 3, identifier)])[0],
                             [(160, 144, "리스본")])
        for name in ("", "가" * 64):
            self.assertEqual(self._run_map([(100, 200, 1, "리스본", 3, 0)], nations={0: name})[0],
                             [(160, 144, "리스본")])

    def test_both_labels_stay_inside_top_and_right_edges(self):
        labels = self._run_map([(128, 190, 1, "리스본", 3, 0)])[0]
        self.assertEqual(labels, [(590, 19, "리스본"), (518, 1, "[포르투갈 왕국]")])
        for x, y, text in labels:
            self.assertGreaterEqual(x, 1)
            self.assertGreaterEqual(y, 1)
            self.assertLessEqual(x + len(text.encode("cp949")) * 8 + 1, 639)

    def test_full_bold_outline_fits_edges_and_invalidates_every_covered_cache_row(self):
        for x, y, dimensions, name in (
            (128, 190, (40, 20), "리스본"),
            (90, 209, (40, 20), "A가"),
            (92, 192, (4, 3), "ABC"),
        ):
            with self.subTest(x=x, y=y, dimensions=dimensions, name=name):
                labels, pixels, cache = self._run_map(
                    [(x, y, 1, name, 3, 0)], dimensions=dimensions,
                    nations={0: "Q"}, include_colors=True,
                )
                self.assertEqual(len(labels), 2)
                self.assertEqual(len(pixels), 16)
                width, height = dimensions
                city, nation = labels
                # Include the 16px glyph, original extra bold column, and
                # both outline edges. Neither line can paint over the other.
                self.assertEqual(city[1] - nation[1], 18)
                self.assertLess(nation[1] + 16, city[1] - 1)
                for index, (label_x, label_y, text, color) in enumerate(labels):
                    strokes = pixels[index * 8:index * 8 + 8]
                    glyph_width = len(text.encode("cp949")) * 8
                    self.assertEqual(min(stroke[0] for stroke in strokes), label_x - 1)
                    self.assertEqual(max(stroke[0] + glyph_width - 1 for stroke in strokes), label_x + glyph_width + 1)
                    self.assertEqual(min(stroke[1] for stroke in strokes), label_y - 1)
                    self.assertEqual(max(stroke[1] + 15 for stroke in strokes), label_y + 16)
                    self.assertEqual([stroke[5] for stroke in strokes], [73] * 6 + [color] * 2)
                    for stroke_x, stroke_y, *_ in strokes:
                        self.assertGreaterEqual(stroke_x, 0)
                        self.assertGreaterEqual(stroke_y, 0)
                        self.assertLess(stroke_x + glyph_width - 1, width * 16)
                        self.assertLess(stroke_y + 15, height * 16)
                        for row in range(stroke_y // 16, (stroke_y + 15) // 16 + 1):
                            self.assertEqual(cache[row * width * 2:(row + 1) * width * 2], b"\xFF" * (width * 2))

    def test_each_outline_width_fits_viewport_and_invalidates_all_painted_rows(self):
        for radius in range(4):
            for x, y, dimensions, name in (
                (128, 190, (40, 20), "리스본"),
                (90, 209, (40, 20), "A가"),
                (92, 192, (4, 3), "ABC"),
            ):
                with self.subTest(radius=radius, x=x, y=y, dimensions=dimensions):
                    labels, pixels, cache = self._run_map(
                        [(x, y, 1, name, 3, 0)], dimensions=dimensions,
                        nations={0: "Q"}, include_colors=True, outline=(radius, 21),
                    )
                    self.assertEqual(len(labels), 2)
                    count = (2, 8, 18, 32)[radius]
                    self.assertEqual(len(pixels), count * 2)
                    width, height = dimensions
                    city, nation = labels
                    self.assertEqual(city[1] - nation[1], 16 + 2 * radius)
                    self.assertLess(nation[1] + 15 + radius, city[1] - radius)
                    for index, (label_x, label_y, name, color) in enumerate(labels):
                        strokes = pixels[index * count:(index + 1) * count]
                        glyph_width = len(name.encode("cp949")) * 8
                        self.assertEqual(min(stroke[0] for stroke in strokes), label_x - radius)
                        self.assertEqual(max(stroke[0] + glyph_width - 1 for stroke in strokes), label_x + glyph_width + radius)
                        self.assertEqual(min(stroke[1] for stroke in strokes), label_y - radius)
                        self.assertEqual(max(stroke[1] + 15 for stroke in strokes), label_y + 15 + radius)
                        for stroke_x, stroke_y, *_ in strokes:
                            self.assertGreaterEqual(stroke_x, 0)
                            self.assertGreaterEqual(stroke_y, 0)
                            self.assertLess(stroke_x + glyph_width - 1, width * 16)
                            self.assertLess(stroke_y + 15, height * 16)
                            for row in range(stroke_y // 16, (stroke_y + 15) // 16 + 1):
                                self.assertEqual(cache[row * width * 2:(row + 1) * width * 2], b"\xFF" * (width * 2))

    def test_every_outline_width_rejects_labels_wider_than_view(self):
        for radius in range(4):
            with self.subTest(radius=radius):
                labels, pixels, _ = self._run_map(
                    [(91, 191, 1, "12345678", 3)], dimensions=(4, 3), outline=(radius, 42),
                )
                self.assertEqual(labels, [])
                self.assertEqual(pixels, [])
                labels, _, _ = self._run_map(
                    [(91, 191, 1, "1234567", 3)], dimensions=(4, 3), outline=(radius, 42),
                )
                self.assertEqual(len(labels), 1)

    def test_label_wider_than_view_does_not_spill_into_other_ui(self):
        self.assertEqual(
            self._run_map(
                [(91, 191, 1, "긴도시이름", 3)], dimensions=(4, 4),
            )[0], [],
        )


if __name__ == "__main__":
    unittest.main()
