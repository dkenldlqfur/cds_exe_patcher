"""Keep shared runtime data writable while save/load selectors are rebuilt."""

import inspect
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

import patch_cds_integrated as integrated
from landing_ship_image_patch import (
    apply_landing_ship_image_fix,
    read_landing_ship_image_fix_state,
)
from pe_patch_section import (
    LANDING_SHIP_IMAGE_SLOT_OFFSET,
    LANDING_SHIP_IMAGE_SLOT_SIZE,
    LOAD_SLOT_SELECTOR_SLOT_OFFSET,
    SAVE_SLOT_SELECTOR_SLOT_OFFSET,
    WORLD_MAP_FOLLOW_SLOT_OFFSET,
    WORLD_MAP_FOLLOW_SLOT_SIZE,
    find_patch_section,
)
from world_map_follow_patch import (
    apply_world_map_follow_patch,
    read_world_map_follow_patch_state,
)


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"
SELECTORS = (
    ("save", integrated.apply_save_slot_selector_patch,
     integrated.read_save_slot_selector_patch_state),
    ("load", integrated.apply_load_slot_selector_patch,
     integrated.read_load_slot_selector_patch_state),
)


class SharedRuntimePermissionsTests(unittest.TestCase):
    def assert_writable(self, data):
        section = find_patch_section(data)
        self.assertIsNotNone(section)
        characteristics = struct.unpack_from("<I", data, section.header_offset + 36)[0]
        self.assertTrue(characteristics & 0x80000000, ".patch lost runtime WRITE permission")

    @staticmethod
    def runtime_payload(data, slot_offset, size):
        section = find_patch_section(data)
        offset, _va = section.slot(slot_offset, size)
        return bytes(data[offset:offset + size])

    def test_current_selectors_preserve_landing_when_removed_in_either_order(self):
        for order in (SELECTORS, SELECTORS[::-1]):
            with self.subTest(order=[name for name, _apply, _read in order]):
                data = bytearray(FIXTURE.read_bytes())
                apply_landing_ship_image_fix(data, True)
                for _name, apply_selector, _read in SELECTORS:
                    apply_selector(data, True)
                landing = self.runtime_payload(
                    data, LANDING_SHIP_IMAGE_SLOT_OFFSET, LANDING_SHIP_IMAGE_SLOT_SIZE,
                )
                # The last selector used to clear WRITE, despite the landing
                # heading capture still writing into this shared section.
                for enabled, sequence in ((False, order), (True, order[::-1])):
                    for _name, apply_selector, read_selector in sequence:
                        self.assertTrue(apply_selector(data, enabled))
                        self.assertEqual(read_selector(data), enabled)
                        self.assert_writable(data)
                        self.assertTrue(read_landing_ship_image_fix_state(data))
                        self.assertEqual(self.runtime_payload(
                            data, LANDING_SHIP_IMAGE_SLOT_OFFSET, LANDING_SHIP_IMAGE_SLOT_SIZE,
                        ), landing)

    def test_legacy_selector_removal_preserves_landing_runtime_permission(self):
        legacy_cases = (
            (SELECTORS[0], SAVE_SLOT_SELECTOR_SLOT_OFFSET,
             integrated.SAVE_SLOT_SELECTOR_EMPTY_SLOT_BRANCH_LEGACY_MAGIC, 10),
            (SELECTORS[1], LOAD_SLOT_SELECTOR_SLOT_OFFSET,
             integrated.LOAD_SLOT_SELECTOR_EMPTY_SLOT_LABEL_LEGACY_MAGIC, 36),
        )
        for (name, apply_selector, read_selector), slot, magic, version in legacy_cases:
            with self.subTest(selector=name):
                data = bytearray(FIXTURE.read_bytes())
                apply_landing_ship_image_fix(data, True)
                apply_selector(data, True)
                section = find_patch_section(data)
                offset = section.raw_offset + slot
                # Existing legacy migration tests use these recognized headers
                # with compatible installed hooks to exercise removal paths.
                data[offset:offset + len(magic)] = magic
                struct.pack_into("<I", data, offset + len(magic), version)
                self.assertTrue(read_selector(data))
                self.assertTrue(apply_selector(data, False))
                self.assertFalse(read_selector(data))
                self.assert_writable(data)
                self.assertTrue(read_landing_ship_image_fix_state(data))

    def test_current_selectors_preserve_camera_runtime_without_landing_patch(self):
        for order in (SELECTORS, SELECTORS[::-1]):
            with self.subTest(order=[name for name, _apply, _read in order]):
                data = bytearray(FIXTURE.read_bytes())
                apply_world_map_follow_patch(data, True)
                for _name, apply_selector, _read in SELECTORS:
                    apply_selector(data, True)
                camera = self.runtime_payload(
                    data, WORLD_MAP_FOLLOW_SLOT_OFFSET, WORLD_MAP_FOLLOW_SLOT_SIZE,
                )
                for _name, apply_selector, _read in order:
                    apply_selector(data, False)
                    self.assert_writable(data)
                    self.assertTrue(read_world_map_follow_patch_state(data))
                    self.assertEqual(self.runtime_payload(
                        data, WORLD_MAP_FOLLOW_SLOT_OFFSET, WORLD_MAP_FOLLOW_SLOT_SIZE,
                    ), camera)

    def test_integrated_reapply_and_slot_toggle_preserve_landing_across_coordinate_styles(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "CDS_95.EXE"
            data = bytearray(FIXTURE.read_bytes())
            apply_landing_ship_image_fix(data, True)
            for _name, apply_selector, _read in SELECTORS:
                apply_selector(data, True)
            target.write_bytes(data)
            for style in ("korean3", "korean2", "original"):
                for enabled in (False, True):
                    with self.subTest(coordinate_style=style, selectors_enabled=enabled):
                        settings = integrated.read_settings(target)
                        arguments = inspect.signature(integrated.apply_all).bind_partial(
                            target, settings[0], True, settings[1], *settings[2:],
                        ).arguments
                        arguments.update(
                            coordinate_style=style,
                            save_slot_selector_enabled=enabled,
                            load_slot_selector_enabled=enabled,
                        )
                        # Omit landing_ship_image_fix_enabled intentionally:
                        # apply_all must detect and preserve the installed patch.
                        integrated.apply_all(**arguments)
                        output = target.read_bytes()
                        self.assert_writable(output)
                        self.assertTrue(read_landing_ship_image_fix_state(output))
                        self.assertEqual(integrated.read_settings(target)[0], style)
                        for _name, _apply, read_selector in SELECTORS:
                            self.assertEqual(read_selector(output), enabled)


if __name__ == "__main__":
    unittest.main()
