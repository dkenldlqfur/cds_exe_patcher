"""Regression tests for moving the Tunis book event to Tunis's tavern."""

import struct
import sys
from pathlib import Path
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
        facility_count = 7 if city_id < 152 else 6
        for facility_id in range(facility_count):
            row_offset = TABLE_OFFSET + table_index * integrated.FACILITY_LAYOUT_RECORD_SIZE
            struct.pack_into("<II", data, row_offset + 0x08, city_id, facility_id)
            struct.pack_into(
                "<4i", data, row_offset + integrated.FACILITY_LAYOUT_X_OFFSET,
                0, 0, 4, 4,
            )
            struct.pack_into(
                "<I", data, row_offset + integrated.FACILITY_EVENT_PART_OFFSET,
                integrated.NO_FACILITY_EVENT,
            )
            table_index += 1
    assert table_index == integrated.FACILITY_LAYOUT_RECORD_COUNT
    casablanca_row = TABLE_OFFSET + (
        85 * 7 + integrated.TAVERN_FACILITY_ID
    ) * integrated.FACILITY_LAYOUT_RECORD_SIZE
    struct.pack_into(
        "<I", data, casablanca_row + integrated.FACILITY_EVENT_PART_OFFSET,
        integrated.TUNIS_BOOK_EVENT_PART,
    )
    return data


class TunisBookEventLocationFixTests(unittest.TestCase):
    def test_city_facility_event_editor_changes_only_selected_part_fields(self) -> None:
        original = _fixture()
        with patch.object(integrated.pefile, "PE", _FakePE):
            records = integrated._read_facility_event_records_from_data(bytes(original))
            tunis_tavern = next(
                record for record in records
                if record.city_id == integrated.TUNIS_CITY_ID
                and record.identifier == integrated.TAVERN_FACILITY_ID
            )
            edits = tuple(
                integrated.FacilityEventRecord(
                    record.identifier, record.city_id,
                    integrated.TUNIS_BOOK_EVENT_PART
                    if record.table_index == tunis_tavern.table_index
                    else record.event_part_id,
                    record.table_index,
                )
                for record in records
            )
            edited = bytearray(original)
            self.assertTrue(integrated.apply_facility_event_edits(edited, edits))
            self.assertEqual(
                integrated._read_facility_event_records_from_data(bytes(edited))[
                    tunis_tavern.table_index
                ].event_part_id,
                integrated.TUNIS_BOOK_EVENT_PART,
            )
            self.assertFalse(integrated.apply_facility_event_edits(edited, edits))

    def test_moves_event_and_restores_original_mapping(self) -> None:
        original = _fixture()
        with patch.object(integrated.pefile, "PE", _FakePE):
            tunis_offset, casablanca_offset = integrated._tunis_book_event_offsets(original)
            self.assertFalse(integrated.read_tunis_book_event_location_fix_state(original))

            edited = bytearray(original)
            self.assertTrue(integrated.apply_tunis_book_event_location_fix(edited, True))
            self.assertEqual(
                struct.unpack_from("<I", edited, tunis_offset)[0],
                integrated.TUNIS_BOOK_EVENT_PART,
            )
            self.assertEqual(
                struct.unpack_from("<I", edited, casablanca_offset)[0],
                integrated.NO_FACILITY_EVENT,
            )
            self.assertTrue(integrated.read_tunis_book_event_location_fix_state(edited))
            self.assertFalse(integrated.apply_tunis_book_event_location_fix(edited, True))

            self.assertTrue(integrated.apply_tunis_book_event_location_fix(edited, False))
            self.assertEqual(edited, original)

    def test_rejects_unrecognized_mapping_without_overwriting_it(self) -> None:
        edited = _fixture()
        with patch.object(integrated.pefile, "PE", _FakePE):
            tunis_offset, _casablanca_offset = integrated._tunis_book_event_offsets(edited)
            struct.pack_into(
                "<I", edited, tunis_offset, integrated.FACILITY_EVENT_PART_COUNT,
            )
            before = bytes(edited)
            with self.assertRaises(ValueError):
                integrated.apply_tunis_book_event_location_fix(edited, True)
        self.assertEqual(bytes(edited), before)


if __name__ == "__main__":
    unittest.main()
