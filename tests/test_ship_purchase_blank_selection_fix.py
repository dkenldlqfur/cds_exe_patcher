"""Regression tests for the ship-purchase blank-row selection guard."""

from pathlib import Path
import struct
import sys
import unittest

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from patch_cds_integrated import (  # noqa: E402
    SHIP_PURCHASE_BLANK_SELECTION_CANCEL_VA,
    SHIP_PURCHASE_BLANK_SELECTION_FIX_WRAPPER_OFFSET,
    SHIP_PURCHASE_BLANK_SELECTION_HOOK_VA,
    SHIP_PURCHASE_BLANK_SELECTION_ORIGINAL,
    SHIP_PURCHASE_BLANK_SELECTION_RESUME_VA,
    SHIP_PURCHASE_BLANK_SELECTION_RETRY_VA,
    _ship_purchase_blank_selection_fix_patch_info,
    apply_ship_purchase_blank_selection_fix,
)
from pe_patch_section import (  # noqa: E402
    SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_OFFSET,
    SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_SIZE,
    find_patch_section,
)


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"


def _offset(data: bytes | bytearray, va: int) -> int:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)
    finally:
        pe.close()


def _relative_target(code: bytes, wrapper_va: int, displacement_offset: int, end_offset: int) -> int:
    return wrapper_va + end_offset + struct.unpack_from("<i", code, displacement_offset)[0]


class ShipPurchaseBlankSelectionFixTests(unittest.TestCase):
    def test_invalid_list_rows_return_to_selector_and_restore_cleanly(self) -> None:
        original = FIXTURE.read_bytes()
        hook_offset = _offset(original, SHIP_PURCHASE_BLANK_SELECTION_HOOK_VA)
        self.assertEqual(
            original[hook_offset:hook_offset + len(SHIP_PURCHASE_BLANK_SELECTION_ORIGINAL)],
            SHIP_PURCHASE_BLANK_SELECTION_ORIGINAL,
        )
        self.assertFalse(_ship_purchase_blank_selection_fix_patch_info(original))

        edited = bytearray(original)
        self.assertTrue(apply_ship_purchase_blank_selection_fix(edited, True))
        self.assertTrue(_ship_purchase_blank_selection_fix_patch_info(edited))
        self.assertFalse(apply_ship_purchase_blank_selection_fix(edited, True))

        section = find_patch_section(edited)
        self.assertIsNotNone(section)
        assert section is not None
        slot_offset, slot_va = section.slot(
            SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_OFFSET,
            SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_SIZE,
        )
        wrapper_va = slot_va + SHIP_PURCHASE_BLANK_SELECTION_FIX_WRAPPER_OFFSET
        wrapper = edited[
            slot_offset + SHIP_PURCHASE_BLANK_SELECTION_FIX_WRAPPER_OFFSET:
            slot_offset + SHIP_PURCHASE_BLANK_SELECTION_FIX_WRAPPER_OFFSET + 31
        ]
        self.assertEqual(wrapper[:3], bytes.fromhex("83 FF FF"))
        self.assertEqual(wrapper[9:11], bytes.fromhex("85 FF"))
        self.assertEqual(wrapper[17:20], bytes.fromhex("3B 7D F0"))
        self.assertEqual(
            _relative_target(wrapper, wrapper_va, 5, 9),
            SHIP_PURCHASE_BLANK_SELECTION_CANCEL_VA,
        )
        self.assertEqual(
            _relative_target(wrapper, wrapper_va, 13, 17),
            SHIP_PURCHASE_BLANK_SELECTION_RETRY_VA,
        )
        self.assertEqual(
            _relative_target(wrapper, wrapper_va, 22, 26),
            SHIP_PURCHASE_BLANK_SELECTION_RETRY_VA,
        )
        self.assertEqual(
            _relative_target(wrapper, wrapper_va, 27, 31),
            SHIP_PURCHASE_BLANK_SELECTION_RESUME_VA,
        )

        self.assertTrue(apply_ship_purchase_blank_selection_fix(edited, False))
        self.assertFalse(_ship_purchase_blank_selection_fix_patch_info(edited))
        self.assertEqual(
            edited[hook_offset:hook_offset + len(SHIP_PURCHASE_BLANK_SELECTION_ORIGINAL)],
            SHIP_PURCHASE_BLANK_SELECTION_ORIGINAL,
        )
        self.assertFalse(any(edited[
            slot_offset:slot_offset + SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_SIZE
        ]))


if __name__ == "__main__":
    unittest.main()
