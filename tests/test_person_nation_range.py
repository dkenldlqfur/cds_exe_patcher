"""Regression tests for all 78 person nationality IDs."""

from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from patch_cds_integrated import (  # noqa: E402
    PERSON_NATION_ID_MAX,
    PERSON_NATION_ID_OFFSET,
    PERSON_TABLE_FILE_OFFSET,
    PersonEdit,
    _read_person_records_from_data,
    apply_person_edit,
)


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"


class PersonNationRangeTests(unittest.TestCase):
    def test_person_nation_id_77_round_trips(self) -> None:
        self.assertEqual(PERSON_NATION_ID_MAX, 77)
        data = bytearray(FIXTURE.read_bytes())
        record = _read_person_records_from_data(bytes(data))[0]
        edit = PersonEdit(
            record.identifier,
            record.face_code,
            record.gender,
            record.age_at_1480,
            77,
            record.job_id,
            record.fame,
            record.infamy,
            record.employment_state,
            record.city_id,
            record.building_id,
            record.blood_id,
            record.vitality,
            record.hire_cost_coefficient,
            record.abilities,
            record.skills,
        )

        self.assertTrue(apply_person_edit(data, edit))
        self.assertEqual(_read_person_records_from_data(bytes(data))[0].nation_id, 77)

    def test_person_reader_rejects_nation_id_outside_78_entry_table(self) -> None:
        data = bytearray(FIXTURE.read_bytes())
        struct.pack_into(
            "<i",
            data,
            PERSON_TABLE_FILE_OFFSET + PERSON_NATION_ID_OFFSET,
            78,
        )
        with self.assertRaisesRegex(ValueError, "인물 0번 마스터"):
            _read_person_records_from_data(bytes(data))


if __name__ == "__main__":
    unittest.main()
