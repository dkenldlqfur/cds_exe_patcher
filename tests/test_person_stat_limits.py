"""Regression tests for the shared ability and vitality gameplay caps."""

from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from patch_cds_integrated import (  # noqa: E402
    PERSON_ABILITY_LIMIT_VA,
    PERSON_ABILITY_ORIGINAL_CODE,
    PERSON_VITALITY_LIMIT_VA,
    _read_person_stat_limits_from_data,
    apply_all,
    apply_person_stat_limits,
    read_person_stat_limits,
    read_settings,
)


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"


def _offset(data: bytes | bytearray, va: int) -> int:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)
    finally:
        pe.close()


class PersonStatLimitTests(unittest.TestCase):
    def test_integrated_apply_reads_back_selected_caps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "CDS_95.EXE"
            shutil.copyfile(FIXTURE, target)
            settings = read_settings(target)
            backup = apply_all(
                target, settings[0], True, settings[1], *settings[2:],
                person_ability_limit=255, person_vitality_limit=9999,
            )
            self.assertIsNotNone(backup)
            assert backup is not None
            self.assertTrue(backup.exists())
            self.assertEqual(read_person_stat_limits(target), (255, 9999))

    def test_round_trip_boundaries_and_restore_original_code(self) -> None:
        original = FIXTURE.read_bytes()
        self.assertEqual(_read_person_stat_limits_from_data(original), (100, 2000))
        ability_offset = _offset(original, PERSON_ABILITY_LIMIT_VA)
        vitality_offset = _offset(original, PERSON_VITALITY_LIMIT_VA)
        self.assertEqual(original[ability_offset:ability_offset + 0x30], PERSON_ABILITY_ORIGINAL_CODE)

        edited = bytearray(original)
        self.assertTrue(apply_person_stat_limits(edited, 255, 9999))
        self.assertEqual(_read_person_stat_limits_from_data(edited), (255, 9999))
        self.assertEqual(edited[ability_offset + 5], 0x68)  # PUSH imm32, not signed PUSH imm8
        self.assertEqual(struct.unpack_from("<I", edited, ability_offset + 6)[0], 255)
        self.assertEqual(struct.unpack_from("<I", edited, vitality_offset + 1)[0], 9999)
        self.assertFalse(apply_person_stat_limits(edited, 255, 9999))

        self.assertTrue(apply_person_stat_limits(edited, 0, 0))
        self.assertEqual(_read_person_stat_limits_from_data(edited), (0, 0))
        self.assertTrue(apply_person_stat_limits(edited, 100, 2000))
        self.assertEqual(bytes(edited), original)

    def test_reject_out_of_range_or_unrecognized_code(self) -> None:
        original = FIXTURE.read_bytes()
        for ability, vitality in ((-1, 2000), (256, 2000), (100, -1), (100, 10000)):
            with self.subTest(ability=ability, vitality=vitality):
                with self.assertRaises(ValueError):
                    apply_person_stat_limits(bytearray(original), ability, vitality)

        edited = bytearray(original)
        edited[_offset(edited, PERSON_ABILITY_LIMIT_VA) + 5] = 0x90
        with self.assertRaises(ValueError):
            apply_person_stat_limits(edited, 255, 9999)


if __name__ == "__main__":
    unittest.main()
