"""Regression tests for suppressing inspector-bribe item re-addition."""

from pathlib import Path
import sys
import unittest

import pefile
from capstone import CS_ARCH_X86, CS_MODE_32, Cs

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from patch_cds_integrated import (  # noqa: E402
    BRIBE_ITEM_DUPLICATE_FIX_SLOT_OFFSET,
    BRIBE_ITEM_DUPLICATE_FIX_SLOT_SIZE,
    BRIBE_ITEM_DUPLICATE_HOOK_VA,
    BRIBE_ITEM_DUPLICATE_ORIGINAL_CALL,
    BRIBE_ITEM_DUPLICATE_SUPPRESSED_CALL,
    BRIBE_ITEM_DUPLICATE_WRAPPER_OFFSET,
    _bribe_item_duplicate_fix_hook,
    _bribe_item_duplicate_fix_patch_info,
    _build_bribe_item_duplicate_fix_payload,
    apply_bribe_item_duplicate_fix,
)
from pe_patch_section import ensure_patch_section  # noqa: E402


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"


def _offset(data: bytes | bytearray, va: int) -> int:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)
    finally:
        pe.close()


class BribeItemDuplicateFixTests(unittest.TestCase):
    def test_patch_suppresses_readdition_call_and_restores_exactly(self) -> None:
        original = FIXTURE.read_bytes()
        hook_offset = _offset(original, BRIBE_ITEM_DUPLICATE_HOOK_VA)
        self.assertEqual(original[hook_offset:hook_offset + 5], BRIBE_ITEM_DUPLICATE_ORIGINAL_CALL)
        self.assertFalse(_bribe_item_duplicate_fix_patch_info(original))

        edited = bytearray(original)
        self.assertTrue(apply_bribe_item_duplicate_fix(edited, True))
        self.assertTrue(_bribe_item_duplicate_fix_patch_info(edited))
        self.assertFalse(apply_bribe_item_duplicate_fix(edited, True))
        self.assertEqual(edited[hook_offset:hook_offset + 5], BRIBE_ITEM_DUPLICATE_SUPPRESSED_CALL)
        instructions = tuple(Cs(CS_ARCH_X86, CS_MODE_32).disasm(
            edited[hook_offset:hook_offset + 5], BRIBE_ITEM_DUPLICATE_HOOK_VA,
        ))
        self.assertEqual([item.mnemonic for item in instructions], ["nop"] * 5)

        self.assertTrue(apply_bribe_item_duplicate_fix(edited, False))
        self.assertFalse(_bribe_item_duplicate_fix_patch_info(edited))
        self.assertEqual(edited, original)

    def test_migrates_previous_duplicate_filter_wrapper(self) -> None:
        edited = bytearray(FIXTURE.read_bytes())
        section, _created = ensure_patch_section(
            edited, BRIBE_ITEM_DUPLICATE_FIX_SLOT_OFFSET + BRIBE_ITEM_DUPLICATE_FIX_SLOT_SIZE,
        )
        slot_offset, slot_va = section.slot(
            BRIBE_ITEM_DUPLICATE_FIX_SLOT_OFFSET,
            BRIBE_ITEM_DUPLICATE_FIX_SLOT_SIZE,
        )
        edited[slot_offset:slot_offset + BRIBE_ITEM_DUPLICATE_FIX_SLOT_SIZE] = (
            _build_bribe_item_duplicate_fix_payload(slot_va)
        )
        hook_offset = _offset(edited, BRIBE_ITEM_DUPLICATE_HOOK_VA)
        edited[hook_offset:hook_offset + 5] = _bribe_item_duplicate_fix_hook(
            slot_va + BRIBE_ITEM_DUPLICATE_WRAPPER_OFFSET
        )

        self.assertTrue(_bribe_item_duplicate_fix_patch_info(edited))
        self.assertTrue(apply_bribe_item_duplicate_fix(edited, True))
        self.assertEqual(edited[hook_offset:hook_offset + 5], BRIBE_ITEM_DUPLICATE_SUPPRESSED_CALL)
        self.assertFalse(any(edited[
            slot_offset:slot_offset + BRIBE_ITEM_DUPLICATE_FIX_SLOT_SIZE
        ]))


if __name__ == "__main__":
    unittest.main()
