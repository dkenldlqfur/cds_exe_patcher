"""Regression tests for the telescope city/port discovery-radius modifier."""

from pathlib import Path
import struct
import sys
import unittest

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from patch_cds_integrated import (  # noqa: E402
    TELESCOPE_CITY_DISCOVERY_BONUS_MAX,
    TELESCOPE_CITY_DISCOVERY_BONUS_ORIGINAL,
    TELESCOPE_CITY_DISCOVERY_BONUS_VA,
    _read_gameplay_options_from_data,
    apply_gameplay_options,
)


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"


def _offset(data: bytes | bytearray, va: int) -> int:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)
    finally:
        pe.close()


class TelescopeCityDiscoveryBonusTests(unittest.TestCase):
    def test_reads_and_writes_the_verified_add_immediate(self) -> None:
        original = FIXTURE.read_bytes()
        self.assertEqual(_read_gameplay_options_from_data(original)[2], TELESCOPE_CITY_DISCOVERY_BONUS_ORIGINAL)

        edited = bytearray(original)
        settings = list(_read_gameplay_options_from_data(original))
        settings[2] = 5
        self.assertTrue(apply_gameplay_options(edited, *settings))
        bonus_offset = _offset(edited, TELESCOPE_CITY_DISCOVERY_BONUS_VA)
        self.assertEqual(edited[bonus_offset - 4:bonus_offset], bytes.fromhex("83 44 24 14"))
        self.assertEqual(struct.unpack_from("b", edited, bonus_offset)[0], 5)
        self.assertEqual(_read_gameplay_options_from_data(edited)[2], 5)
        self.assertFalse(apply_gameplay_options(edited, *settings))

    def test_rejects_an_impractical_bonus(self) -> None:
        settings = list(_read_gameplay_options_from_data(FIXTURE.read_bytes()))
        settings[2] = TELESCOPE_CITY_DISCOVERY_BONUS_MAX + 1
        with self.assertRaises(ValueError):
            apply_gameplay_options(bytearray(FIXTURE.read_bytes()), *settings)


if __name__ == "__main__":
    unittest.main()
