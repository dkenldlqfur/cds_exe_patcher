"""Regression tests for the consolidated tavern-hint corrections."""

from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from patch_cds_integrated import (  # noqa: E402
    CACAO_HINT_CITY_ORIGINAL,
    CACAO_HINT_CITY_PATCHED,
    CACAO_HINT_ID,
    KNOSSOS_HINT_ID,
    KNOSSOS_HINT_TARGET_ORIGINAL,
    KNOSSOS_HINT_TARGET_PATCHED,
    _read_hint_records_from_data,
    _tavern_hint_bug_fix_offsets,
    apply_tavern_hint_bug_fix,
    read_tavern_hint_bug_fix_state,
)


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"


class TavernHintBugFixTests(unittest.TestCase):
    def test_applies_and_restores_knossos_and_cacao_together(self) -> None:
        original = FIXTURE.read_bytes()
        hints = _read_hint_records_from_data(original)
        self.assertEqual(hints[KNOSSOS_HINT_ID].target_code, KNOSSOS_HINT_TARGET_ORIGINAL)
        self.assertEqual(hints[CACAO_HINT_ID].city_ids[0], CACAO_HINT_CITY_ORIGINAL)
        self.assertFalse(read_tavern_hint_bug_fix_state(original))

        edited = bytearray(original)
        self.assertTrue(apply_tavern_hint_bug_fix(edited, True))
        hints = _read_hint_records_from_data(bytes(edited))
        self.assertEqual(hints[KNOSSOS_HINT_ID].target_code, KNOSSOS_HINT_TARGET_PATCHED)
        self.assertEqual(hints[CACAO_HINT_ID].city_ids[0], CACAO_HINT_CITY_PATCHED)
        self.assertTrue(read_tavern_hint_bug_fix_state(bytes(edited)))
        self.assertFalse(apply_tavern_hint_bug_fix(edited, True))

        self.assertTrue(apply_tavern_hint_bug_fix(edited, False))
        hints = _read_hint_records_from_data(bytes(edited))
        self.assertEqual(hints[KNOSSOS_HINT_ID].target_code, KNOSSOS_HINT_TARGET_ORIGINAL)
        self.assertEqual(hints[CACAO_HINT_ID].city_ids[0], CACAO_HINT_CITY_ORIGINAL)

    def test_recognizes_the_previous_knossos_only_patch_for_upgrade(self) -> None:
        edited = bytearray(FIXTURE.read_bytes())
        knossos_offset, cacao_city_offset = _tavern_hint_bug_fix_offsets(edited)
        struct.pack_into("<I", edited, knossos_offset, KNOSSOS_HINT_TARGET_PATCHED)
        self.assertEqual(struct.unpack_from("<i", edited, cacao_city_offset)[0], CACAO_HINT_CITY_ORIGINAL)
        self.assertTrue(read_tavern_hint_bug_fix_state(bytes(edited)))

        self.assertTrue(apply_tavern_hint_bug_fix(edited, True))
        self.assertEqual(struct.unpack_from("<i", edited, cacao_city_offset)[0], CACAO_HINT_CITY_PATCHED)


if __name__ == "__main__":
    unittest.main()
