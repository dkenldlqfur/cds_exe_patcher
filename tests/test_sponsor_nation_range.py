"""Regression tests for all 78 sponsor nationality IDs."""

from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from patch_cds_integrated import (  # noqa: E402
    SPONSOR_NATION_ID_MAX,
    SPONSOR_NATION_ID_OFFSET,
    SPONSOR_TABLE_VA,
    SponsorEdit,
    _read_sponsor_records_from_data,
    apply_sponsor_edit,
)

import pefile  # noqa: E402


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"


def _sponsor_table_offset(data: bytes | bytearray) -> int:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_offset_from_rva(SPONSOR_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase)
    finally:
        pe.close()


class SponsorNationRangeTests(unittest.TestCase):
    def test_sponsor_nation_id_77_round_trips(self) -> None:
        self.assertEqual(SPONSOR_NATION_ID_MAX, 77)
        data = bytearray(FIXTURE.read_bytes())
        record = _read_sponsor_records_from_data(bytes(data))[0]
        edit = SponsorEdit(
            record.identifier,
            record.face_code,
            record.gender,
            77,
            record.job_id,
            record.appearance_year,
            record.power,
            record.city_id,
            record.building_id,
            record.wealth_factor,
            record.appraisal,
            record.preference_flags,
            record.language_flags,
        )

        self.assertTrue(apply_sponsor_edit(data, edit))
        self.assertEqual(_read_sponsor_records_from_data(bytes(data))[0].nation_id, 77)

    def test_sponsor_reader_rejects_nation_id_outside_78_entry_table(self) -> None:
        data = bytearray(FIXTURE.read_bytes())
        table_offset = _sponsor_table_offset(data)
        struct.pack_into(
            "<i",
            data,
            table_offset + SPONSOR_NATION_ID_OFFSET,
            78,
        )
        with self.assertRaisesRegex(ValueError, "후원자 0번 국가"):
            _read_sponsor_records_from_data(bytes(data))

    def test_sponsor_edit_rejects_nation_id_78(self) -> None:
        data = bytearray(FIXTURE.read_bytes())
        record = _read_sponsor_records_from_data(bytes(data))[0]
        edit = SponsorEdit(
            record.identifier,
            record.face_code,
            record.gender,
            78,
            record.job_id,
            record.appearance_year,
            record.power,
            record.city_id,
            record.building_id,
            record.wealth_factor,
            record.appraisal,
            record.preference_flags,
            record.language_flags,
        )
        with self.assertRaisesRegex(ValueError, "후원자 국가는 목록에서 선택"):
            apply_sponsor_edit(data, edit)


if __name__ == "__main__":
    unittest.main()
