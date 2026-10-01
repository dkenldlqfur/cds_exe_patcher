"""Regression tests for the separate final discovery coordinate overlay."""

from pathlib import Path
import struct
import sys
import unittest

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from patch_cds_integrated import (  # noqa: E402
    DISCOVERY_COORDINATE_TABLE_VA,
    DISCOVERY_FINAL_COORDINATE_RECORD_VA,
    DISCOVERY_GAME_ID_OFFSET,
    DISCOVERY_RECORD_SIZE,
    DiscoveryEdit,
    _discovery_coordinate_offset,
    _discovery_metadata_offset,
    _read_discovery_records_from_data,
    apply_discovery_edit,
)


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"


class DiscoveryCoordinateTests(unittest.TestCase):
    def test_coordinates_can_be_added_and_cleared_with_sentinel(self) -> None:
        original = FIXTURE.read_bytes()
        records = _read_discovery_records_from_data(original)
        discovery_with_coords = next(record for record in records if record.min_x is not None)
        discovery_without_coords = next(record for record in records if record.min_x is None)

        def make_edit(record, coordinates):
            return DiscoveryEdit(
                record.identifier, record.name, record.category_id, record.game_id,
                record.value, *coordinates, record.still_slot, record.avi_id,
                record.animation_part, record.description,
            )

        cleared = bytearray(original)
        self.assertTrue(apply_discovery_edit(
            cleared, make_edit(discovery_with_coords, (None, None, None, None)),
        ))
        pe = pefile.PE(data=bytes(cleared), fast_load=True)
        try:
            cleared_offset = _discovery_coordinate_offset(pe, discovery_with_coords.identifier)
        finally:
            pe.close()
        self.assertEqual(struct.unpack_from("<iiii", cleared, cleared_offset), (-1, -1, -1, -1))
        self.assertIsNone(
            _read_discovery_records_from_data(bytes(cleared))[discovery_with_coords.identifier].min_x,
        )

        restored = bytearray(original)
        coordinates = (1000, 300, 1001, 301)
        self.assertTrue(apply_discovery_edit(
            restored, make_edit(discovery_without_coords, coordinates),
        ))
        self.assertEqual(
            _read_discovery_records_from_data(bytes(restored))[discovery_without_coords.identifier].min_x,
            coordinates[0],
        )

    def test_group_id_edit_updates_only_selected_discovery_record(self) -> None:
        original = FIXTURE.read_bytes()
        discovery = _read_discovery_records_from_data(original)[26]
        pe = pefile.PE(data=original, fast_load=True)
        try:
            metadata_offset = _discovery_metadata_offset(pe, discovery.identifier)
        finally:
            pe.close()

        new_game_id = discovery.game_id + 1
        edit = DiscoveryEdit(
            discovery.identifier, discovery.name, discovery.category_id, new_game_id,
            discovery.value, discovery.min_x, discovery.min_y, discovery.max_x,
            discovery.max_y, discovery.still_slot, discovery.avi_id,
            discovery.animation_part, discovery.description,
        )
        edited = bytearray(original)
        self.assertTrue(apply_discovery_edit(edited, edit))
        expected = bytearray(original)
        struct.pack_into(
            "<I", expected, metadata_offset + DISCOVERY_GAME_ID_OFFSET, new_game_id,
        )
        self.assertEqual(edited, expected)
        self.assertEqual(
            _read_discovery_records_from_data(bytes(edited))[26].game_id,
            new_game_id,
        )
        self.assertFalse(apply_discovery_edit(edited, edit))

    def test_bulguksa_uses_separate_final_coordinate_record(self) -> None:
        original = FIXTURE.read_bytes()
        discovery = _read_discovery_records_from_data(original)[230]
        self.assertEqual((discovery.min_x, discovery.min_y, discovery.max_x, discovery.max_y),
                         (2144, 375, 2145, 375))

        pe = pefile.PE(data=original, fast_load=True)
        try:
            coordinate_offset = _discovery_coordinate_offset(pe, discovery.identifier)
            self.assertEqual(
                coordinate_offset,
                pe.get_offset_from_rva(
                    DISCOVERY_FINAL_COORDINATE_RECORD_VA - pe.OPTIONAL_HEADER.ImageBase
                ),
            )
            former_offset = pe.get_offset_from_rva(
                DISCOVERY_COORDINATE_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
            ) + discovery.identifier * DISCOVERY_RECORD_SIZE
        finally:
            pe.close()

        self.assertNotEqual(coordinate_offset, former_offset)
        edited = bytearray(original)
        edit = DiscoveryEdit(
            discovery.identifier, discovery.name, discovery.category_id, discovery.game_id,
            discovery.value,
            discovery.min_x, discovery.min_y, discovery.max_x + 1, discovery.max_y,
            discovery.still_slot, discovery.avi_id, discovery.animation_part,
            discovery.description,
        )
        self.assertTrue(apply_discovery_edit(edited, edit))
        self.assertEqual(edited[former_offset:former_offset + 16],
                         original[former_offset:former_offset + 16])
        self.assertEqual(edited[coordinate_offset:coordinate_offset + 16],
                         struct.pack("<iiii", 2144, 375, 2146, 375))
        self.assertEqual(_read_discovery_records_from_data(bytes(edited))[230].max_x, 2146)


if __name__ == "__main__":
    unittest.main()
