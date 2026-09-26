"""Tests for reading and editing the executable's per-city facility hit areas."""

from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

import patch_cds_integrated as integrated  # noqa: E402


TABLE_OFFSET = 0x100


class _FakePE:
    FILE_HEADER = type("Header", (), {"Machine": 0x14C})()
    OPTIONAL_HEADER = type("OptionalHeader", (), {"ImageBase": 0x400000})()

    def __init__(self, **_kwargs) -> None:
        pass

    def get_offset_from_rva(self, _rva: int) -> int:
        return TABLE_OFFSET

    def close(self) -> None:
        pass


def _fixture() -> bytearray:
    data = bytearray(
        TABLE_OFFSET
        + integrated.FACILITY_LAYOUT_RECORD_COUNT * integrated.FACILITY_LAYOUT_RECORD_SIZE
    )
    table_index = 0
    for city_id in range(integrated.CITY_RECORD_COUNT):
        count = 7 if city_id < 152 else 6
        for facility_id in range(count):
            offset = TABLE_OFFSET + table_index * integrated.FACILITY_LAYOUT_RECORD_SIZE
            struct.pack_into("<II", data, offset + 0x08, city_id, facility_id)
            struct.pack_into(
                "<4i", data, offset + integrated.FACILITY_LAYOUT_X_OFFSET,
                10 + (city_id % 10) * 10, 5 + facility_id * 5, 48, 40,
            )
            table_index += 1
    assert table_index == integrated.FACILITY_LAYOUT_RECORD_COUNT
    return data


class FacilityAreaLayoutTests(unittest.TestCase):
    def test_reads_per_city_regions_and_derives_effective_rectangles(self) -> None:
        with patch.object(integrated.pefile, "PE", _FakePE):
            records = integrated._read_facility_area_records_from_data(bytes(_fixture()))

        self.assertEqual(len(records), 1508)
        city_zero = [record for record in records if record.city_id == 0]
        city_one = [record for record in records if record.city_id == 1]
        self.assertEqual(len(city_zero), 7)
        self.assertEqual(len(city_one), 7)
        self.assertEqual(city_zero[0].hit_rect, (22, 15, 24, 20))
        self.assertEqual(city_one[0].hit_rect, (32, 15, 24, 20))

    def test_edits_only_the_selected_city_facility_geometry(self) -> None:
        original = _fixture()
        with patch.object(integrated.pefile, "PE", _FakePE):
            edits = list(integrated._read_facility_area_records_from_data(bytes(original)))
            record = edits[3]
            edits[3] = integrated.FacilityAreaRecord(
                record.identifier, record.x + 6, record.y, record.width, record.height,
                record.city_id, record.table_index,
            )
            edited = bytearray(original)
            self.assertTrue(integrated.apply_facility_area_edits(edited, tuple(edits)))
            reread = integrated._read_facility_area_records_from_data(bytes(edited))
            self.assertFalse(integrated.apply_facility_area_edits(edited, tuple(edits)))

        changed = [index for index, (left, right) in enumerate(zip(original, edited)) if left != right]
        expected_offset = TABLE_OFFSET + 3 * integrated.FACILITY_LAYOUT_RECORD_SIZE + integrated.FACILITY_LAYOUT_X_OFFSET
        self.assertEqual(changed, [expected_offset])
        self.assertEqual(reread[3].hit_rect, (28, 30, 24, 20))
        self.assertEqual(reread[10].hit_rect, (32, 30, 24, 20))

    def test_rejects_areas_outside_the_city_artwork(self) -> None:
        with patch.object(integrated.pefile, "PE", _FakePE):
            edits = list(integrated._read_facility_area_records_from_data(bytes(_fixture())))
            record = edits[0]
            edits[0] = integrated.FacilityAreaRecord(
                record.identifier, 390, 5, 48, 40, record.city_id, record.table_index,
            )
            with self.assertRaises(ValueError):
                integrated.apply_facility_area_edits(_fixture(), tuple(edits))


if __name__ == "__main__":
    unittest.main()
