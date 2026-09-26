"""Regression tests for the Erasmus sponsor location correction."""

from pathlib import Path
from dataclasses import replace
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from patch_cds_integrated import (  # noqa: E402
    ERASMUS_SPONSOR_ID,
    SPONSOR_BUILDING_CHURCH_ID,
    SPONSOR_BUILDING_HARBOR_ID,
    _read_sponsor_records_from_data,
    apply_erasmus_location_bug_fix,
    read_erasmus_location_bug_fix_state,
)


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"


class ErasmusLocationBugFixTests(unittest.TestCase):
    def test_moves_only_erasmus_from_harbor_to_church_and_restores(self) -> None:
        original = FIXTURE.read_bytes()
        records = _read_sponsor_records_from_data(original)
        erasmus = records[ERASMUS_SPONSOR_ID]
        self.assertIn("에라스무스", erasmus.name)
        self.assertEqual(erasmus.building_id, SPONSOR_BUILDING_HARBOR_ID)
        self.assertFalse(read_erasmus_location_bug_fix_state(original))

        patched = bytearray(original)
        self.assertTrue(apply_erasmus_location_bug_fix(patched, True))
        self.assertTrue(read_erasmus_location_bug_fix_state(patched))
        patched_records = _read_sponsor_records_from_data(bytes(patched))
        self.assertEqual(patched_records[ERASMUS_SPONSOR_ID].building_id, SPONSOR_BUILDING_CHURCH_ID)
        self.assertEqual(
            replace(patched_records[ERASMUS_SPONSOR_ID], building_id=erasmus.building_id),
            erasmus,
        )
        self.assertFalse(apply_erasmus_location_bug_fix(patched, True))

        self.assertTrue(apply_erasmus_location_bug_fix(patched, False))
        self.assertFalse(read_erasmus_location_bug_fix_state(patched))
        self.assertEqual(bytes(patched), original)
        self.assertFalse(apply_erasmus_location_bug_fix(patched, False))


if __name__ == "__main__":
    unittest.main()
