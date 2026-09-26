"""Regression tests for the duplicate Nestorian Cross discovery reward."""

from pathlib import Path
import sys
import unittest

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from patch_cds_integrated import (  # noqa: E402
    NESTORIAN_CROSS_DUPLICATE_REWARD_ITEM,
    NESTORIAN_CROSS_DUPLICATE_REWARD_VA,
    NO_DISCOVERY_REWARD_ITEM,
    apply_nestorian_cross_duplicate_reward_fix,
    read_nestorian_cross_duplicate_reward_fix,
)


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"


def _reward_offset(data: bytes | bytearray) -> int:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_offset_from_rva(
            NESTORIAN_CROSS_DUPLICATE_REWARD_VA - pe.OPTIONAL_HEADER.ImageBase
        )
    finally:
        pe.close()


class NestorianCrossDuplicateRewardFixTests(unittest.TestCase):
    def test_disables_only_uguksa_reward_and_restores_it(self) -> None:
        original = FIXTURE.read_bytes()
        edited = bytearray(original)
        offset = _reward_offset(original)
        self.assertEqual(
            int.from_bytes(original[offset:offset + 4], "little"),
            NESTORIAN_CROSS_DUPLICATE_REWARD_ITEM,
        )
        self.assertFalse(read_nestorian_cross_duplicate_reward_fix(edited))

        self.assertTrue(apply_nestorian_cross_duplicate_reward_fix(edited, True))
        self.assertEqual(
            int.from_bytes(edited[offset:offset + 4], "little"),
            NO_DISCOVERY_REWARD_ITEM,
        )
        self.assertTrue(read_nestorian_cross_duplicate_reward_fix(edited))
        self.assertFalse(apply_nestorian_cross_duplicate_reward_fix(edited, True))

        self.assertTrue(apply_nestorian_cross_duplicate_reward_fix(edited, False))
        self.assertFalse(read_nestorian_cross_duplicate_reward_fix(edited))
        self.assertEqual(edited, original)


if __name__ == "__main__":
    unittest.main()
