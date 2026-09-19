"""Regression tests for the player fame and infamy limit paths."""

from pathlib import Path
import struct
import sys
import unittest

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from patch_cds_integrated import (  # noqa: E402
    PLAYER_FAME_LIMIT_CODE_OFFSET,
    PLAYER_FAME_LIMIT_HOOK_VA,
    _read_gameplay_options_from_data,
    apply_gameplay_options,
)
from pe_patch_section import (  # noqa: E402
    PLAYER_FAME_LIMIT_SLOT_OFFSET,
    PLAYER_FAME_LIMIT_SLOT_SIZE,
    find_patch_section,
)


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"


def _offset(data: bytes | bytearray, va: int) -> int:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)
    finally:
        pe.close()


class PlayerFameLimitTests(unittest.TestCase):
    def test_independent_limits_change_player_clamp_not_provisions(self) -> None:
        original = FIXTURE.read_bytes()
        original_options = _read_gameplay_options_from_data(original)
        self.assertEqual(original_options[8], original_options[9])
        food_offset = _offset(original, 0x474188)
        unrelated_offset = _offset(original, 0x4741C8)
        original_other_limits = (
            original[food_offset:food_offset + 4],
            original[unrelated_offset:unrelated_offset + 4],
        )
        edited = bytearray(original)
        desired = (*original_options[:8], 1_234_567, 765_432)

        self.assertTrue(apply_gameplay_options(edited, *desired))
        self.assertEqual(_read_gameplay_options_from_data(bytes(edited)), desired)
        self.assertEqual(edited[food_offset:food_offset + 4], original_other_limits[0])
        self.assertEqual(edited[unrelated_offset:unrelated_offset + 4], original_other_limits[1])
        self.assertFalse(apply_gameplay_options(edited, *desired))

        section = find_patch_section(edited)
        self.assertIsNotNone(section)
        assert section is not None
        slot_offset, slot_va = section.slot(
            PLAYER_FAME_LIMIT_SLOT_OFFSET, PLAYER_FAME_LIMIT_SLOT_SIZE,
        )
        hook_offset = _offset(edited, PLAYER_FAME_LIMIT_HOOK_VA)
        jump = struct.unpack_from("<i", edited, hook_offset + 1)[0]
        self.assertEqual(
            PLAYER_FAME_LIMIT_HOOK_VA + 5 + jump,
            slot_va + PLAYER_FAME_LIMIT_CODE_OFFSET,
        )
        code = edited[
            slot_offset + PLAYER_FAME_LIMIT_CODE_OFFSET:
            slot_offset + PLAYER_FAME_LIMIT_CODE_OFFSET + 21
        ]
        self.assertEqual(code[:5], b"\x85\xC0\x75\x07\x68")
        self.assertEqual(struct.unpack_from("<I", code, 5)[0], desired[8])
        self.assertEqual(code[9:12], b"\xEB\x05\x68")
        self.assertEqual(struct.unpack_from("<I", code, 12)[0], desired[9])
        self.assertEqual(code[16], 0xE9)
        self.assertEqual(
            slot_va + PLAYER_FAME_LIMIT_CODE_OFFSET + 21
            + struct.unpack_from("<i", code, 17)[0],
            PLAYER_FAME_LIMIT_HOOK_VA + 5,
        )

        revised = (*desired[:8], 2_000_000, 500_000)
        self.assertTrue(apply_gameplay_options(edited, *revised))
        self.assertEqual(_read_gameplay_options_from_data(bytes(edited)), revised)

        shared = (*desired[:8], 800_000, 800_000)
        self.assertTrue(apply_gameplay_options(edited, *shared))
        self.assertEqual(_read_gameplay_options_from_data(bytes(edited)), shared)
        self.assertEqual(edited[hook_offset:hook_offset + 5],
                         b"\x68" + struct.pack("<I", 800_000))
        self.assertFalse(any(edited[slot_offset:slot_offset + PLAYER_FAME_LIMIT_SLOT_SIZE]))
        self.assertEqual(edited[food_offset:food_offset + 4], original_other_limits[0])
        self.assertEqual(edited[unrelated_offset:unrelated_offset + 4], original_other_limits[1])


if __name__ == "__main__":
    unittest.main()
