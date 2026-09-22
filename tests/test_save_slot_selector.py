"""Regression tests for the native ten-slot save destination selector."""

from pathlib import Path
import struct
import sys
import unittest

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from patch_cds_integrated import (  # noqa: E402
    SAVE_SLOT_SELECTOR_CANCEL_VA,
    SAVE_SLOT_SELECTOR_CONFIRM_ROUTINE_VA,
    SAVE_SLOT_SELECTOR_MENU_COUNT,
    SAVE_SLOT_SELECTOR_SAVE_COUNT,
    SAVE_SLOT_SELECTOR_HOOK_ORIGINAL,
    SAVE_SLOT_SELECTOR_HOOK_VA,
    SAVE_SLOT_SELECTOR_LABELS_OFFSET,
    SAVE_SLOT_SELECTOR_LABEL_STRIDE,
    SAVE_SLOT_SELECTOR_MENU_ITEMS_OFFSET,
    SAVE_SLOT_SELECTOR_PREVIOUS_MAGIC,
    SAVE_SLOT_SELECTOR_EMPTY_SLOT_BRANCH_LEGACY_MAGIC,
    SAVE_SLOT_SELECTOR_SAVE_TRAMPOLINE_V2_LEGACY_MAGIC,
    SAVE_SLOT_SELECTOR_HEADER_BUFFER_LEGACY_MAGIC,
    SAVE_SLOT_SELECTOR_HEADER_COUNT_LEGACY_MAGIC,
    SAVE_SLOT_SELECTOR_SAVE_NAMES_OFFSET,
    SAVE_SLOT_SELECTOR_SUCCESS_VA,
    SAVE_SLOT_SELECTOR_TMP_NAME_POINTERS_OFFSET,
    SAVE_SLOT_SELECTOR_DEFAULT_TMP_NAME_VA,
    SAVE_SLOT_SELECTOR_DATE_FORMAT_OFFSET,
    SAVE_SLOT_SELECTOR_OUTSIDE_LABEL_OFFSET,
    SAVE_SLOT_SELECTOR_LABEL_HELPER_OFFSET,
    SAVE_SLOT_SELECTOR_TITLE_OFFSET,
    SAVE_SLOT_SELECTOR_WRAPPER_OFFSET,
    _save_slot_selector_patch_info,
    apply_save_slot_selector_patch,
)
from pe_patch_section import (  # noqa: E402
    SAVE_SLOT_SELECTOR_SLOT_OFFSET,
    SAVE_SLOT_SELECTOR_SLOT_SIZE,
    find_patch_section,
)


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"


def _offset(data: bytes | bytearray, va: int) -> int:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)
    finally:
        pe.close()


def _relative_target(code: bytes, wrapper_va: int, instruction_offset: int) -> int:
    return wrapper_va + instruction_offset + 5 + struct.unpack_from(
        "<i", code, instruction_offset + 1,
    )[0]


class SaveSlotSelectorTests(unittest.TestCase):
    def test_install_creates_ten_two_digit_destinations_and_restores_cleanly(self) -> None:
        original = FIXTURE.read_bytes()
        hook_offset = _offset(original, SAVE_SLOT_SELECTOR_HOOK_VA)
        self.assertEqual(
            original[hook_offset:hook_offset + len(SAVE_SLOT_SELECTOR_HOOK_ORIGINAL)],
            SAVE_SLOT_SELECTOR_HOOK_ORIGINAL,
        )

        edited = bytearray(original)
        self.assertTrue(apply_save_slot_selector_patch(edited, True))
        self.assertTrue(_save_slot_selector_patch_info(edited))
        self.assertFalse(apply_save_slot_selector_patch(edited, True))

        section = find_patch_section(edited)
        self.assertIsNotNone(section)
        assert section is not None
        slot_offset, slot_va = section.slot(
            SAVE_SLOT_SELECTOR_SLOT_OFFSET, SAVE_SLOT_SELECTOR_SLOT_SIZE,
        )
        wrapper_va = slot_va + SAVE_SLOT_SELECTOR_WRAPPER_OFFSET
        wrapper = bytes(edited[
            slot_offset + SAVE_SLOT_SELECTOR_WRAPPER_OFFSET:
            slot_offset + SAVE_SLOT_SELECTOR_WRAPPER_OFFSET + 0x185
        ])
        self.assertEqual(_relative_target(wrapper, wrapper_va, 0x130), SAVE_SLOT_SELECTOR_CONFIRM_ROUTINE_VA)
        self.assertEqual(_relative_target(wrapper, wrapper_va, 0x145), 0x478E80)
        # A missing slot bypasses overwrite confirmation and must land on the
        # stack restore at 0x141, never inside the preceding `jne` immediate.
        self.assertEqual(wrapper[0x120:0x122], bytes.fromhex("74 1F"))
        self.assertEqual(
            wrapper_va + 0x122 + struct.unpack_from("<b", wrapper, 0x121)[0],
            wrapper_va + 0x141,
        )
        self.assertEqual(wrapper[0x141:0x145], bytes.fromhex("83 C4 68 61"))
        self.assertEqual(_relative_target(wrapper, wrapper_va, 0x15E), SAVE_SLOT_SELECTOR_SUCCESS_VA)
        self.assertEqual(_relative_target(wrapper, wrapper_va, 0x17B), SAVE_SLOT_SELECTOR_CANCEL_VA)
        self.assertEqual(
            wrapper_va + 0x141 + struct.unpack_from("<i", wrapper, 0x13D)[0],
            wrapper_va + 0xBB,
        )
        self.assertGreaterEqual(wrapper.count(bytes.fromhex("FF 15 44 F4 62 00")), 2)
        self.assertGreaterEqual(wrapper.count(bytes.fromhex("FF 15 74 F4 62 00")), 1)
        self.assertIn(bytes.fromhex("83 EC 68"), wrapper)
        self.assertIn(bytes.fromhex("6A 5D"), wrapper)
        self.assertIn(bytes.fromhex("8D 44 24 04"), wrapper)
        self.assertIn(bytes.fromhex("83 3C 24 5D 90"), wrapper)

        for index in range(SAVE_SLOT_SELECTOR_MENU_COUNT):
            item_offset = slot_offset + SAVE_SLOT_SELECTOR_MENU_ITEMS_OFFSET + index * 12
            label_va, enabled, visible = struct.unpack_from("<III", edited, item_offset)
            self.assertEqual(
                label_va,
                slot_va + SAVE_SLOT_SELECTOR_LABELS_OFFSET + index * SAVE_SLOT_SELECTOR_LABEL_STRIDE,
            )
            self.assertEqual((enabled, visible), (1, 1))
        cancel_offset = (
            slot_offset + SAVE_SLOT_SELECTOR_LABELS_OFFSET
            + SAVE_SLOT_SELECTOR_SAVE_COUNT * SAVE_SLOT_SELECTOR_LABEL_STRIDE
        )
        self.assertEqual(
            bytes(edited[cancel_offset:cancel_offset + SAVE_SLOT_SELECTOR_LABEL_STRIDE]).split(b"\0", 1)[0],
            "취소".encode("cp949"),
        )
        for index in range(SAVE_SLOT_SELECTOR_SAVE_COUNT):
            label_offset = (
                slot_offset + SAVE_SLOT_SELECTOR_LABELS_OFFSET
                + index * SAVE_SLOT_SELECTOR_LABEL_STRIDE
            )
            self.assertEqual(
                bytes(edited[label_offset:label_offset + SAVE_SLOT_SELECTOR_LABEL_STRIDE]).split(b"\0", 1)[0],
                "빈 슬롯".encode("cp949"),
            )
        self.assertEqual(
            bytes(
                edited[
                    slot_offset + SAVE_SLOT_SELECTOR_TITLE_OFFSET:
                    slot_offset + SAVE_SLOT_SELECTOR_TITLE_OFFSET + SAVE_SLOT_SELECTOR_LABEL_STRIDE
                ]
            ).split(b"\0", 1)[0],
            "저장할 데이터를 선택하세요".encode("cp949"),
        )
        self.assertEqual(
            bytes(edited[
                slot_offset + SAVE_SLOT_SELECTOR_DATE_FORMAT_OFFSET:
                slot_offset + SAVE_SLOT_SELECTOR_DATE_FORMAT_OFFSET + SAVE_SLOT_SELECTOR_LABEL_STRIDE
            ]).split(b"\0", 1)[0],
            "%04d.%02d.%02d · %s".encode("cp949"),
        )
        self.assertEqual(
            bytes(edited[
                slot_offset + SAVE_SLOT_SELECTOR_OUTSIDE_LABEL_OFFSET:
                slot_offset + SAVE_SLOT_SELECTOR_OUTSIDE_LABEL_OFFSET + SAVE_SLOT_SELECTOR_LABEL_STRIDE
            ]).split(b"\0", 1)[0],
            "해상".encode("cp949"),
        )
        label_helper = bytes(edited[
            slot_offset + SAVE_SLOT_SELECTOR_LABEL_HELPER_OFFSET:
            slot_offset + SAVE_SLOT_SELECTOR_LABEL_HELPER_OFFSET + 0x50
        ])
        self.assertIn(bytes.fromhex("0F B7 40 67"), label_helper)
        self.assertIn(bytes.fromhex("05 B0 14 4D 00"), label_helper)
        for index in range(SAVE_SLOT_SELECTOR_SAVE_COUNT):
            self.assertEqual(
                bytes(edited[
                    slot_offset + SAVE_SLOT_SELECTOR_SAVE_NAMES_OFFSET
                    + index * SAVE_SLOT_SELECTOR_LABEL_STRIDE:
                    slot_offset + SAVE_SLOT_SELECTOR_SAVE_NAMES_OFFSET
                    + (index + 1) * SAVE_SLOT_SELECTOR_LABEL_STRIDE
                ]).split(b"\0", 1)[0],
                f"C:SAVEDATA{index + 1:02d}.CDS".encode("ascii"),
            )
            self.assertEqual(
                struct.unpack_from(
                    "<I", edited,
                    slot_offset + SAVE_SLOT_SELECTOR_TMP_NAME_POINTERS_OFFSET + index * 4,
                )[0],
                SAVE_SLOT_SELECTOR_DEFAULT_TMP_NAME_VA,
            )

        self.assertNotIn(b"SAVEDATA01.TMP", edited[slot_offset:slot_offset + SAVE_SLOT_SELECTOR_SLOT_SIZE])

        v3 = bytearray(edited)
        v3[slot_offset:slot_offset + len(SAVE_SLOT_SELECTOR_PREVIOUS_MAGIC)] = (
            SAVE_SLOT_SELECTOR_PREVIOUS_MAGIC
        )
        self.assertFalse(_save_slot_selector_patch_info(v3))
        self.assertTrue(apply_save_slot_selector_patch(v3, True))
        self.assertTrue(_save_slot_selector_patch_info(v3))

        v6 = bytearray(edited)
        v6[slot_offset:slot_offset + len(SAVE_SLOT_SELECTOR_HEADER_BUFFER_LEGACY_MAGIC)] = (
            SAVE_SLOT_SELECTOR_HEADER_BUFFER_LEGACY_MAGIC
        )
        self.assertFalse(_save_slot_selector_patch_info(v6))
        self.assertTrue(apply_save_slot_selector_patch(v6, True))
        self.assertTrue(_save_slot_selector_patch_info(v6))

        v7 = bytearray(edited)
        v7[slot_offset:slot_offset + len(SAVE_SLOT_SELECTOR_HEADER_COUNT_LEGACY_MAGIC)] = (
            SAVE_SLOT_SELECTOR_HEADER_COUNT_LEGACY_MAGIC
        )
        self.assertFalse(_save_slot_selector_patch_info(v7))
        self.assertTrue(apply_save_slot_selector_patch(v7, True))
        self.assertTrue(_save_slot_selector_patch_info(v7))

        v9 = bytearray(edited)
        v9[slot_offset:slot_offset + len(SAVE_SLOT_SELECTOR_SAVE_TRAMPOLINE_V2_LEGACY_MAGIC)] = (
            SAVE_SLOT_SELECTOR_SAVE_TRAMPOLINE_V2_LEGACY_MAGIC
        )
        self.assertFalse(_save_slot_selector_patch_info(v9))
        self.assertTrue(apply_save_slot_selector_patch(v9, True))
        self.assertTrue(_save_slot_selector_patch_info(v9))

        v10 = bytearray(edited)
        v10[slot_offset:slot_offset + len(SAVE_SLOT_SELECTOR_EMPTY_SLOT_BRANCH_LEGACY_MAGIC)] = (
            SAVE_SLOT_SELECTOR_EMPTY_SLOT_BRANCH_LEGACY_MAGIC
        )
        self.assertFalse(_save_slot_selector_patch_info(v10))
        self.assertTrue(apply_save_slot_selector_patch(v10, True))
        self.assertTrue(_save_slot_selector_patch_info(v10))

        self.assertTrue(apply_save_slot_selector_patch(edited, False))
        self.assertFalse(_save_slot_selector_patch_info(edited))
        self.assertEqual(
            edited[hook_offset:hook_offset + len(SAVE_SLOT_SELECTOR_HOOK_ORIGINAL)],
            SAVE_SLOT_SELECTOR_HOOK_ORIGINAL,
        )
        self.assertFalse(any(edited[
            slot_offset:slot_offset + SAVE_SLOT_SELECTOR_SLOT_SIZE
        ]))


if __name__ == "__main__":
    unittest.main()
