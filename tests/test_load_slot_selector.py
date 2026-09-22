"""Regression tests for the native ten-slot load selector."""

from pathlib import Path
import struct
import sys
import unittest

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from patch_cds_integrated import (  # noqa: E402
    LOAD_SLOT_SELECTOR_COMMAND_HOOK_ORIGINAL,
    LOAD_SLOT_SELECTOR_COMMAND_HOOK_VA,
    LOAD_SLOT_SELECTOR_COMMAND_CONTINUATION_VA,
    LOAD_SLOT_SELECTOR_COMMAND_WRAPPER_OFFSET,
    LOAD_SLOT_SELECTOR_CONFIRM_WRAPPER_OFFSET,
    LOAD_SLOT_SELECTOR_TITLE_HOOK_ORIGINAL,
    LOAD_SLOT_SELECTOR_TITLE_HOOK_VA,
    LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_HOOK_ORIGINAL,
    LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_HOOK_VA,
    LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_CONTINUATION_VA,
    LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_WRAPPER_OFFSET,
    LOAD_SLOT_SELECTOR_TITLE_WRAPPER_OFFSET,
    LOAD_SLOT_SELECTOR_TITLE_SUCCESS_VA,
    LOAD_SLOT_SELECTOR_TITLE_CANCEL_VA,
    LOAD_SLOT_SELECTOR_TITLE_CONTINUATION_VA,
    LOAD_SLOT_SELECTOR_TITLE_LOADER_VA,
    LOAD_SLOT_SELECTOR_TITLE_ORDER_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_TITLE_CITY_ORDER_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_TEMP_SLOT_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_POST_SELECTION_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_TITLE_PREPARATION_ORDER_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_TITLE_SELECTOR_CALL_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_CONFIRM_STACK_INDEX_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_TITLE_LOADER_SKIP_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_DIALOG_REGISTER_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_DIALOG_STACK_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_COMMAND_PREPARATION_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_MENU_TITLE_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_STACK_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_CONTEXT_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_DIRECT_IO_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_ALWAYS_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_EMPTY_ROWS_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_EMPTY_ROWS_FLAG_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_EMPTY_ROWS_POLARITY_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_HEADER_BUFFER_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_EXISTENCE_NAMES_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_EXISTENCE_STACK_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_EXISTENCE_PATH_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_EMPTY_ROWS_DIRECTION_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_EMPTY_SLOT_LABEL_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_ORIGINAL,
    LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_VA,
    LOAD_SLOT_SELECTOR_SESSION_FILE_WRAPPER_OFFSET,
    LOAD_SLOT_SELECTOR_SESSION_FILE_CONTINUATION_VA,
    LOAD_SLOT_SELECTOR_POST_SELECTION_WRAPPER_OFFSET,
    LOAD_SLOT_SELECTOR_SESSION_FILE_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_TITLE_CONTEXT_LEGACY_MAGIC,
    LOAD_SLOT_SELECTOR_MAGIC,
    LOAD_SLOT_SELECTOR_CONTINUATION_VA,
    LOAD_SLOT_SELECTOR_HOOK_ORIGINAL,
    LOAD_SLOT_SELECTOR_HOOK_VA,
    LOAD_SLOT_SELECTOR_LABELS_OFFSET,
    LOAD_SLOT_SELECTOR_LABEL_STRIDE,
    LOAD_SLOT_SELECTOR_DATE_FORMAT_OFFSET,
    LOAD_SLOT_SELECTOR_OUTSIDE_LABEL_OFFSET,
    LOAD_SLOT_SELECTOR_LABEL_HELPER_OFFSET,
    LOAD_SLOT_SELECTOR_TITLE_OFFSET,
    LOAD_SLOT_SELECTOR_MENU_COUNT,
    LOAD_SLOT_SELECTOR_MENU_ITEMS_OFFSET,
    LOAD_SLOT_SELECTOR_SAVE_NAME_POINTERS_OFFSET,
    LOAD_SLOT_SELECTOR_EXISTENCE_NAME_POINTERS_OFFSET,
    LOAD_SLOT_SELECTOR_EXISTENCE_NAMES_OFFSET,
    LOAD_SLOT_SELECTOR_TMP_NAME_POINTERS_OFFSET,
    SAVE_SLOT_SELECTOR_DEFAULT_TMP_NAME_VA,
    LOAD_SLOT_SELECTOR_SELECTED_INDEX_OFFSET,
    LOAD_SLOT_SELECTOR_CONFIRM_TRAMPOLINE_OFFSET,
    LOAD_SLOT_SELECTOR_SAVE_COUNT,
    LOAD_SLOT_SELECTOR_WRAPPER_OFFSET,
    _load_slot_selector_patch_info,
    apply_load_slot_selector_patch,
)
from pe_patch_section import (  # noqa: E402
    LOAD_SLOT_SELECTOR_SLOT_OFFSET,
    LOAD_SLOT_SELECTOR_SLOT_SIZE,
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


class LoadSlotSelectorTests(unittest.TestCase):
    def test_install_uses_read_only_checks_and_restores_loader_entry(self) -> None:
        original = FIXTURE.read_bytes()
        hook_offset = _offset(original, LOAD_SLOT_SELECTOR_HOOK_VA)
        command_hook_offset = _offset(original, LOAD_SLOT_SELECTOR_COMMAND_HOOK_VA)
        title_hook_offset = _offset(original, LOAD_SLOT_SELECTOR_TITLE_HOOK_VA)
        session_file_hook_offset = _offset(original, LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_VA)
        title_availability_hook_offset = _offset(
            original, LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_HOOK_VA,
        )
        self.assertEqual(
            original[hook_offset:hook_offset + len(LOAD_SLOT_SELECTOR_HOOK_ORIGINAL)],
            LOAD_SLOT_SELECTOR_HOOK_ORIGINAL,
        )
        self.assertEqual(
            original[
                command_hook_offset:
                command_hook_offset + len(LOAD_SLOT_SELECTOR_COMMAND_HOOK_ORIGINAL)
            ],
            LOAD_SLOT_SELECTOR_COMMAND_HOOK_ORIGINAL,
        )
        self.assertEqual(
            original[
                title_hook_offset:
                title_hook_offset + len(LOAD_SLOT_SELECTOR_TITLE_HOOK_ORIGINAL)
            ],
            LOAD_SLOT_SELECTOR_TITLE_HOOK_ORIGINAL,
        )
        self.assertEqual(
            original[
                session_file_hook_offset:
                session_file_hook_offset + len(LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_ORIGINAL)
            ],
            LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_ORIGINAL,
        )
        self.assertEqual(
            original[
                title_availability_hook_offset:
                title_availability_hook_offset + len(LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_HOOK_ORIGINAL)
            ],
            LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_HOOK_ORIGINAL,
        )

        edited = bytearray(original)
        self.assertTrue(apply_load_slot_selector_patch(edited, True))
        self.assertTrue(_load_slot_selector_patch_info(edited))
        section = find_patch_section(edited)
        self.assertIsNotNone(section)
        assert section is not None
        slot_offset, slot_va = section.slot(
            LOAD_SLOT_SELECTOR_SLOT_OFFSET, LOAD_SLOT_SELECTOR_SLOT_SIZE,
        )
        wrapper_va = slot_va + LOAD_SLOT_SELECTOR_WRAPPER_OFFSET
        wrapper = bytes(edited[
            slot_offset + LOAD_SLOT_SELECTOR_WRAPPER_OFFSET:
            slot_offset + LOAD_SLOT_SELECTOR_WRAPPER_OFFSET + 0x14A
        ])
        self.assertEqual(
            wrapper[0x129:0x133],
            bytes.fromhex("83 C4 68 61 B8 01 00 00 00 C3"),
        )
        self.assertEqual(wrapper[0x13E:0x141], bytes.fromhex("31 C0 C3"))
        self.assertGreaterEqual(wrapper.count(bytes.fromhex("FF 15 44 F4 62 00")), 2)
        self.assertGreaterEqual(wrapper.count(bytes.fromhex("FF 15 74 F4 62 00")), 1)
        self.assertIn(bytes.fromhex("83 EC 68"), wrapper)
        self.assertIn(bytes.fromhex("6A 5D"), wrapper)
        self.assertIn(bytes.fromhex("8D 44 24 04"), wrapper)
        self.assertIn(bytes.fromhex("83 3C 24 5D 90"), wrapper)
        # The existence check uses the same EXE-directory resolver as saving.
        # It must balance its pathname argument before the next slot.
        self.assertEqual(wrapper[0x1B], 0x50)
        self.assertEqual(wrapper[0x1C], 0xE8)
        self.assertEqual(
            _relative_target(wrapper, wrapper_va, 0x1C),
            0x425220,
        )
        self.assertEqual(wrapper[0x21:0x24], bytes.fromhex("83 C4 04"))
        self.assertEqual(
            struct.unpack_from("<I", wrapper, 0x17)[0],
            slot_va + LOAD_SLOT_SELECTOR_SAVE_NAME_POINTERS_OFFSET,
        )
        self.assertEqual(wrapper[0xD8], 0xE9)
        self.assertEqual(
            _relative_target(wrapper, wrapper_va, 0xD8),
            slot_va + LOAD_SLOT_SELECTOR_CONFIRM_TRAMPOLINE_OFFSET,
        )
        confirmation_wrapper = bytes(edited[
            slot_offset + LOAD_SLOT_SELECTOR_CONFIRM_WRAPPER_OFFSET:
            slot_offset + LOAD_SLOT_SELECTOR_CONFIRM_WRAPPER_OFFSET + 0x30
        ])
        self.assertEqual(confirmation_wrapper[0:7], bytes.fromhex("68 F8 8C 56 00 6A 02"))
        self.assertEqual(confirmation_wrapper[18:20], bytes.fromhex("75 18"))
        self.assertEqual(confirmation_wrapper[20:22], bytes.fromhex("8B 15"))
        self.assertEqual(
            struct.unpack_from("<I", confirmation_wrapper, 0x16)[0],
            slot_va + LOAD_SLOT_SELECTOR_SELECTED_INDEX_OFFSET,
        )
        self.assertEqual(
            struct.unpack_from("<I", confirmation_wrapper, 0x1D)[0],
            slot_va + LOAD_SLOT_SELECTOR_SAVE_NAME_POINTERS_OFFSET,
        )
        trampoline = bytes(edited[
            slot_offset + LOAD_SLOT_SELECTOR_CONFIRM_TRAMPOLINE_OFFSET:
            slot_offset + LOAD_SLOT_SELECTOR_CONFIRM_TRAMPOLINE_OFFSET + 10
        ])
        self.assertEqual(trampoline[0], 0xA3)
        self.assertEqual(
            struct.unpack_from("<I", trampoline, 1)[0],
            slot_va + LOAD_SLOT_SELECTOR_SELECTED_INDEX_OFFSET,
        )
        title_wrapper = bytes(edited[
            slot_offset + LOAD_SLOT_SELECTOR_TITLE_WRAPPER_OFFSET:
            slot_offset + LOAD_SLOT_SELECTOR_TITLE_WRAPPER_OFFSET + 0x20
        ])
        self.assertEqual(title_wrapper[0], 0xE8)
        title_wrapper_va = slot_va + LOAD_SLOT_SELECTOR_TITLE_WRAPPER_OFFSET
        self.assertEqual(
            _relative_target(title_wrapper, title_wrapper_va, 0), 0x478550,
        )
        self.assertEqual(title_wrapper[5], 0xE8)
        self.assertEqual(
            _relative_target(title_wrapper, title_wrapper_va, 5),
            slot_va + LOAD_SLOT_SELECTOR_WRAPPER_OFFSET,
        )
        self.assertEqual(title_wrapper[10:12], bytes.fromhex("85 C0"))
        self.assertEqual(title_wrapper[12:14], bytes.fromhex("0F 84"))
        self.assertEqual(title_wrapper[18], 0xE9)
        self.assertEqual(
            title_wrapper_va + 18 + struct.unpack_from("<i", title_wrapper, 14)[0],
            LOAD_SLOT_SELECTOR_TITLE_CANCEL_VA,
        )
        self.assertEqual(
            title_wrapper_va + 23 + struct.unpack_from("<i", title_wrapper, 19)[0],
            LOAD_SLOT_SELECTOR_TITLE_LOADER_VA,
        )
        session_file_wrapper = bytes(edited[
            slot_offset + LOAD_SLOT_SELECTOR_SESSION_FILE_WRAPPER_OFFSET:
            slot_offset + LOAD_SLOT_SELECTOR_SESSION_FILE_WRAPPER_OFFSET + 0x40
        ])
        self.assertEqual(session_file_wrapper[0:2], bytes.fromhex("FF 35"))
        self.assertEqual(session_file_wrapper[23:28], bytes.fromhex("68 44 76 53 00"))
        self.assertEqual(session_file_wrapper[45], 0xE9)
        self.assertEqual(
            _relative_target(
                session_file_wrapper,
                slot_va + LOAD_SLOT_SELECTOR_SESSION_FILE_WRAPPER_OFFSET,
                0x2D,
            ),
            LOAD_SLOT_SELECTOR_SESSION_FILE_CONTINUATION_VA,
        )
        availability_wrapper = bytes(edited[
            slot_offset + LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_WRAPPER_OFFSET:
            slot_offset + LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_WRAPPER_OFFSET + 0x20
        ])
        self.assertEqual(availability_wrapper[:4], bytes.fromhex("83 C4 04 C7"))
        self.assertEqual(availability_wrapper[:13], bytes.fromhex(
            "83 C4 04 C7 05 84 27 55 00 01 00 00 00"
        ))
        self.assertEqual(availability_wrapper[0x0D], 0xE9)
        self.assertEqual(
            _relative_target(
                availability_wrapper,
                slot_va + LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_WRAPPER_OFFSET,
                0x0D,
            ),
            LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_CONTINUATION_VA,
        )
        label_helper = bytes(edited[
            slot_offset + LOAD_SLOT_SELECTOR_LABEL_HELPER_OFFSET:
            slot_offset + LOAD_SLOT_SELECTOR_LABEL_HELPER_OFFSET + 0x60
        ])
        self.assertEqual(label_helper[1:7], bytes.fromhex("8D 04 76 C7 04 85"))
        self.assertEqual(
            struct.unpack_from("<I", label_helper, 7)[0],
            slot_va + LOAD_SLOT_SELECTOR_MENU_ITEMS_OFFSET + 8,
        )
        self.assertEqual(label_helper[11:15], bytes.fromhex("01 00 00 00"))
        self.assertEqual(
            wrapper[0xB1:0xBB],
            bytes.fromhex("46 83 FE 0A 0F 8C 4B FF FF FF"),
        )
        post_selection_wrapper = bytes(edited[
            slot_offset + LOAD_SLOT_SELECTOR_POST_SELECTION_WRAPPER_OFFSET:
            slot_offset + LOAD_SLOT_SELECTOR_POST_SELECTION_WRAPPER_OFFSET + 0x20
        ])
        self.assertEqual(post_selection_wrapper[:10], bytes.fromhex("C7 05 7C 87 56 00 44 76 53 00"))
        self.assertEqual(post_selection_wrapper[0x0A], 0xE9)
        self.assertEqual(
            _relative_target(
                post_selection_wrapper,
                slot_va + LOAD_SLOT_SELECTOR_POST_SELECTION_WRAPPER_OFFSET,
                0x0A,
            ),
            slot_va + LOAD_SLOT_SELECTOR_WRAPPER_OFFSET + 0x129,
        )
        command_wrapper = bytes(edited[
            slot_offset + LOAD_SLOT_SELECTOR_COMMAND_WRAPPER_OFFSET:
            slot_offset + LOAD_SLOT_SELECTOR_COMMAND_WRAPPER_OFFSET + 0x30
        ])
        self.assertEqual(command_wrapper[5:9], bytes.fromhex("85 C0 74 1B"))
        self.assertEqual(command_wrapper[26:31], bytes.fromhex("B9 18 4D 5A 00"))
        self.assertEqual(
            _relative_target(
                command_wrapper,
                slot_va + LOAD_SLOT_SELECTOR_COMMAND_WRAPPER_OFFSET,
                0x1F,
            ),
            LOAD_SLOT_SELECTOR_COMMAND_CONTINUATION_VA,
        )
        self.assertEqual(command_wrapper[36], 0xC3)
        self.assertNotEqual(
            edited[
                command_hook_offset:
                command_hook_offset + len(LOAD_SLOT_SELECTOR_COMMAND_HOOK_ORIGINAL)
            ],
            LOAD_SLOT_SELECTOR_COMMAND_HOOK_ORIGINAL,
        )
        self.assertNotEqual(
            edited[
                title_hook_offset:
                title_hook_offset + len(LOAD_SLOT_SELECTOR_TITLE_HOOK_ORIGINAL)
            ],
            LOAD_SLOT_SELECTOR_TITLE_HOOK_ORIGINAL,
        )
        self.assertNotEqual(
            edited[
                session_file_hook_offset:
                session_file_hook_offset + len(LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_ORIGINAL)
            ],
            LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_ORIGINAL,
        )
        self.assertNotEqual(
            edited[
                title_availability_hook_offset:
                title_availability_hook_offset + len(LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_HOOK_ORIGINAL)
            ],
            LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_HOOK_ORIGINAL,
        )

        # v8 selected the slot for the initial loader but left the later
        # title-session initializer on SAVEDATA.CDS.  Upgrade it directly.
        v8 = bytearray(edited)
        v8[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_SESSION_FILE_LEGACY_MAGIC
        )
        struct.pack_into("<I", v8, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 8)
        v8[
            session_file_hook_offset:
            session_file_hook_offset + len(LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_ORIGINAL)
        ] = LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_ORIGINAL
        self.assertFalse(_load_slot_selector_patch_info(v8))
        self.assertTrue(apply_load_slot_selector_patch(v8, True))
        self.assertTrue(_load_slot_selector_patch_info(v8))

        v9 = bytearray(edited)
        v9[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_TITLE_CONTEXT_LEGACY_MAGIC
        )
        struct.pack_into("<I", v9, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 9)
        self.assertFalse(_load_slot_selector_patch_info(v9))
        self.assertTrue(apply_load_slot_selector_patch(v9, True))
        self.assertTrue(_load_slot_selector_patch_info(v9))

        v10 = bytearray(edited)
        v10[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_TITLE_ORDER_LEGACY_MAGIC
        )
        struct.pack_into("<I", v10, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 10)
        self.assertFalse(_load_slot_selector_patch_info(v10))
        self.assertTrue(apply_load_slot_selector_patch(v10, True))
        self.assertTrue(_load_slot_selector_patch_info(v10))

        v11 = bytearray(edited)
        v11[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_TITLE_CITY_ORDER_LEGACY_MAGIC
        )
        struct.pack_into("<I", v11, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 11)
        self.assertFalse(_load_slot_selector_patch_info(v11))
        self.assertTrue(apply_load_slot_selector_patch(v11, True))
        self.assertTrue(_load_slot_selector_patch_info(v11))

        v12 = bytearray(edited)
        v12[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_TEMP_SLOT_LEGACY_MAGIC
        )
        struct.pack_into("<I", v12, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 12)
        self.assertFalse(_load_slot_selector_patch_info(v12))
        self.assertTrue(apply_load_slot_selector_patch(v12, True))
        self.assertTrue(_load_slot_selector_patch_info(v12))

        v13 = bytearray(edited)
        v13[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_POST_SELECTION_LEGACY_MAGIC
        )
        struct.pack_into("<I", v13, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 13)
        self.assertFalse(_load_slot_selector_patch_info(v13))
        self.assertTrue(apply_load_slot_selector_patch(v13, True))
        self.assertTrue(_load_slot_selector_patch_info(v13))

        v14 = bytearray(edited)
        v14[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_TITLE_PREPARATION_ORDER_LEGACY_MAGIC
        )
        struct.pack_into("<I", v14, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 14)
        self.assertFalse(_load_slot_selector_patch_info(v14))
        self.assertTrue(apply_load_slot_selector_patch(v14, True))
        self.assertTrue(_load_slot_selector_patch_info(v14))

        v15 = bytearray(edited)
        v15[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_TITLE_SELECTOR_CALL_LEGACY_MAGIC
        )
        struct.pack_into("<I", v15, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 15)
        self.assertFalse(_load_slot_selector_patch_info(v15))
        self.assertTrue(apply_load_slot_selector_patch(v15, True))
        self.assertTrue(_load_slot_selector_patch_info(v15))

        v16 = bytearray(edited)
        v16[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_CONFIRM_STACK_INDEX_LEGACY_MAGIC
        )
        struct.pack_into("<I", v16, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 16)
        self.assertFalse(_load_slot_selector_patch_info(v16))
        self.assertTrue(apply_load_slot_selector_patch(v16, True))
        self.assertTrue(_load_slot_selector_patch_info(v16))

        v17 = bytearray(edited)
        v17[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_TITLE_LOADER_SKIP_LEGACY_MAGIC
        )
        struct.pack_into("<I", v17, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 17)
        self.assertFalse(_load_slot_selector_patch_info(v17))
        self.assertTrue(apply_load_slot_selector_patch(v17, True))
        self.assertTrue(_load_slot_selector_patch_info(v17))

        v18 = bytearray(edited)
        v18[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_DIALOG_REGISTER_LEGACY_MAGIC
        )
        struct.pack_into("<I", v18, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 18)
        self.assertFalse(_load_slot_selector_patch_info(v18))
        self.assertTrue(apply_load_slot_selector_patch(v18, True))
        self.assertTrue(_load_slot_selector_patch_info(v18))

        v19 = bytearray(edited)
        v19[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_DIALOG_STACK_LEGACY_MAGIC
        )
        struct.pack_into("<I", v19, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 19)
        self.assertFalse(_load_slot_selector_patch_info(v19))
        self.assertTrue(apply_load_slot_selector_patch(v19, True))
        self.assertTrue(_load_slot_selector_patch_info(v19))

        v20 = bytearray(edited)
        v20[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_COMMAND_PREPARATION_LEGACY_MAGIC
        )
        struct.pack_into("<I", v20, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 20)
        self.assertFalse(_load_slot_selector_patch_info(v20))
        self.assertTrue(apply_load_slot_selector_patch(v20, True))
        self.assertTrue(_load_slot_selector_patch_info(v20))

        v21 = bytearray(edited)
        v21[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_MENU_TITLE_LEGACY_MAGIC
        )
        struct.pack_into("<I", v21, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 21)
        self.assertFalse(_load_slot_selector_patch_info(v21))
        self.assertTrue(apply_load_slot_selector_patch(v21, True))
        self.assertTrue(_load_slot_selector_patch_info(v21))

        # v23 had the slot selector itself, but the title menu still looked
        # only for SAVEDATA.CDS and therefore disabled Load for slot-only saves.
        v23 = bytearray(edited)
        v23[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_LEGACY_MAGIC
        )
        struct.pack_into("<I", v23, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 23)
        v23[
            title_availability_hook_offset:
            title_availability_hook_offset + len(LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_HOOK_ORIGINAL)
        ] = LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_HOOK_ORIGINAL
        self.assertFalse(_load_slot_selector_patch_info(v23))
        self.assertTrue(apply_load_slot_selector_patch(v23, True))
        self.assertTrue(_load_slot_selector_patch_info(v23))

        v24 = bytearray(edited)
        v24[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_STACK_LEGACY_MAGIC
        )
        struct.pack_into("<I", v24, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 24)
        self.assertFalse(_load_slot_selector_patch_info(v24))
        self.assertTrue(apply_load_slot_selector_patch(v24, True))
        self.assertTrue(_load_slot_selector_patch_info(v24))

        v25 = bytearray(edited)
        v25[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_CONTEXT_LEGACY_MAGIC
        )
        struct.pack_into("<I", v25, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 25)
        self.assertFalse(_load_slot_selector_patch_info(v25))
        self.assertTrue(apply_load_slot_selector_patch(v25, True))
        self.assertTrue(_load_slot_selector_patch_info(v25))

        v26 = bytearray(edited)
        v26[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_DIRECT_IO_LEGACY_MAGIC
        )
        struct.pack_into("<I", v26, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 26)
        self.assertFalse(_load_slot_selector_patch_info(v26))
        self.assertTrue(apply_load_slot_selector_patch(v26, True))
        self.assertTrue(_load_slot_selector_patch_info(v26))

        v27 = bytearray(edited)
        v27[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_ALWAYS_LEGACY_MAGIC
        )
        struct.pack_into("<I", v27, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 27)
        self.assertFalse(_load_slot_selector_patch_info(v27))
        self.assertTrue(apply_load_slot_selector_patch(v27, True))
        self.assertTrue(_load_slot_selector_patch_info(v27))

        v28 = bytearray(edited)
        v28[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_EMPTY_ROWS_LEGACY_MAGIC
        )
        struct.pack_into("<I", v28, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 28)
        self.assertFalse(_load_slot_selector_patch_info(v28))
        self.assertTrue(apply_load_slot_selector_patch(v28, True))
        self.assertTrue(_load_slot_selector_patch_info(v28))

        v29 = bytearray(edited)
        v29[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_EMPTY_ROWS_FLAG_LEGACY_MAGIC
        )
        struct.pack_into("<I", v29, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 29)
        self.assertFalse(_load_slot_selector_patch_info(v29))
        self.assertTrue(apply_load_slot_selector_patch(v29, True))
        self.assertTrue(_load_slot_selector_patch_info(v29))

        v30 = bytearray(edited)
        v30[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_EMPTY_ROWS_POLARITY_LEGACY_MAGIC
        )
        struct.pack_into("<I", v30, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 30)
        self.assertFalse(_load_slot_selector_patch_info(v30))
        self.assertTrue(apply_load_slot_selector_patch(v30, True))
        self.assertTrue(_load_slot_selector_patch_info(v30))

        v31 = bytearray(edited)
        v31[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_HEADER_BUFFER_LEGACY_MAGIC
        )
        struct.pack_into("<I", v31, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 31)
        self.assertFalse(_load_slot_selector_patch_info(v31))
        self.assertTrue(apply_load_slot_selector_patch(v31, True))
        self.assertTrue(_load_slot_selector_patch_info(v31))

        v32 = bytearray(edited)
        v32[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_EXISTENCE_NAMES_LEGACY_MAGIC
        )
        struct.pack_into("<I", v32, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 32)
        self.assertFalse(_load_slot_selector_patch_info(v32))
        self.assertTrue(apply_load_slot_selector_patch(v32, True))
        self.assertTrue(_load_slot_selector_patch_info(v32))

        v33 = bytearray(edited)
        v33[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_EXISTENCE_STACK_LEGACY_MAGIC
        )
        struct.pack_into("<I", v33, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 33)
        self.assertFalse(_load_slot_selector_patch_info(v33))
        self.assertTrue(apply_load_slot_selector_patch(v33, True))
        self.assertTrue(_load_slot_selector_patch_info(v33))

        v34 = bytearray(edited)
        v34[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_EXISTENCE_PATH_LEGACY_MAGIC
        )
        struct.pack_into("<I", v34, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 34)
        self.assertFalse(_load_slot_selector_patch_info(v34))
        self.assertTrue(apply_load_slot_selector_patch(v34, True))
        self.assertTrue(_load_slot_selector_patch_info(v34))

        v35 = bytearray(edited)
        v35[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_EMPTY_ROWS_DIRECTION_LEGACY_MAGIC
        )
        struct.pack_into("<I", v35, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 35)
        self.assertFalse(_load_slot_selector_patch_info(v35))
        self.assertTrue(apply_load_slot_selector_patch(v35, True))
        self.assertTrue(_load_slot_selector_patch_info(v35))

        v36 = bytearray(edited)
        v36[slot_offset:slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC)] = (
            LOAD_SLOT_SELECTOR_EMPTY_SLOT_LABEL_LEGACY_MAGIC
        )
        struct.pack_into("<I", v36, slot_offset + len(LOAD_SLOT_SELECTOR_MAGIC), 36)
        self.assertFalse(_load_slot_selector_patch_info(v36))
        self.assertTrue(apply_load_slot_selector_patch(v36, True))
        self.assertTrue(_load_slot_selector_patch_info(v36))

        for index in range(LOAD_SLOT_SELECTOR_MENU_COUNT):
            label_va, enabled, visible = struct.unpack_from(
                "<III", edited,
                slot_offset + LOAD_SLOT_SELECTOR_MENU_ITEMS_OFFSET + index * 12,
            )
            self.assertEqual(
                label_va,
                slot_va + LOAD_SLOT_SELECTOR_LABELS_OFFSET + index * LOAD_SLOT_SELECTOR_LABEL_STRIDE,
            )
            self.assertEqual(
                (enabled, visible),
                (1, int(index == LOAD_SLOT_SELECTOR_SAVE_COUNT)),
            )
        cancel_offset = (
            slot_offset + LOAD_SLOT_SELECTOR_LABELS_OFFSET
            + LOAD_SLOT_SELECTOR_SAVE_COUNT * LOAD_SLOT_SELECTOR_LABEL_STRIDE
        )
        self.assertEqual(
            bytes(edited[cancel_offset:cancel_offset + LOAD_SLOT_SELECTOR_LABEL_STRIDE]).split(b"\0", 1)[0],
            "취소".encode("cp949"),
        )
        for index in range(LOAD_SLOT_SELECTOR_SAVE_COUNT):
            label_offset = (
                slot_offset + LOAD_SLOT_SELECTOR_LABELS_OFFSET
                + index * LOAD_SLOT_SELECTOR_LABEL_STRIDE
            )
            self.assertEqual(
                bytes(edited[label_offset:label_offset + LOAD_SLOT_SELECTOR_LABEL_STRIDE]).split(b"\0", 1)[0],
                "빈 슬롯".encode("cp949"),
            )
        for index in range(LOAD_SLOT_SELECTOR_SAVE_COUNT):
            self.assertEqual(
                struct.unpack_from(
                    "<I", edited,
                    slot_offset + LOAD_SLOT_SELECTOR_TMP_NAME_POINTERS_OFFSET + index * 4,
                )[0],
                SAVE_SLOT_SELECTOR_DEFAULT_TMP_NAME_VA,
            )
            self.assertEqual(
                struct.unpack_from(
                    "<I", edited,
                    slot_offset + LOAD_SLOT_SELECTOR_EXISTENCE_NAME_POINTERS_OFFSET + index * 4,
                )[0],
                slot_va + LOAD_SLOT_SELECTOR_EXISTENCE_NAMES_OFFSET
                + index * LOAD_SLOT_SELECTOR_LABEL_STRIDE,
            )
        self.assertNotIn(
            b"SAVEDATA01.TMP",
            edited[slot_offset:slot_offset + LOAD_SLOT_SELECTOR_SLOT_SIZE],
        )
        self.assertEqual(
            bytes(
                edited[
                    slot_offset + LOAD_SLOT_SELECTOR_TITLE_OFFSET:
                    slot_offset + LOAD_SLOT_SELECTOR_TITLE_OFFSET + LOAD_SLOT_SELECTOR_LABEL_STRIDE
                ]
            ).split(b"\0", 1)[0],
            "불러올 데이터를 선택하세요".encode("cp949"),
        )
        self.assertEqual(
            bytes(edited[
                slot_offset + LOAD_SLOT_SELECTOR_DATE_FORMAT_OFFSET:
                slot_offset + LOAD_SLOT_SELECTOR_DATE_FORMAT_OFFSET + LOAD_SLOT_SELECTOR_LABEL_STRIDE
            ]).split(b"\0", 1)[0],
            "%04d.%02d.%02d · %s".encode("cp949"),
        )
        self.assertEqual(
            bytes(edited[
                slot_offset + LOAD_SLOT_SELECTOR_OUTSIDE_LABEL_OFFSET:
                slot_offset + LOAD_SLOT_SELECTOR_OUTSIDE_LABEL_OFFSET + LOAD_SLOT_SELECTOR_LABEL_STRIDE
            ]).split(b"\0", 1)[0],
            "해상".encode("cp949"),
        )
        self.assertIn(bytes.fromhex("0F B7 40 67"), label_helper)
        self.assertIn(bytes.fromhex("05 B0 14 4D 00"), label_helper)

        self.assertTrue(apply_load_slot_selector_patch(edited, False))
        self.assertFalse(_load_slot_selector_patch_info(edited))
        self.assertEqual(
            edited[hook_offset:hook_offset + len(LOAD_SLOT_SELECTOR_HOOK_ORIGINAL)],
            LOAD_SLOT_SELECTOR_HOOK_ORIGINAL,
        )
        self.assertEqual(
            edited[
                command_hook_offset:
                command_hook_offset + len(LOAD_SLOT_SELECTOR_COMMAND_HOOK_ORIGINAL)
            ],
            LOAD_SLOT_SELECTOR_COMMAND_HOOK_ORIGINAL,
        )
        self.assertEqual(
            edited[
                title_hook_offset:
                title_hook_offset + len(LOAD_SLOT_SELECTOR_TITLE_HOOK_ORIGINAL)
            ],
            LOAD_SLOT_SELECTOR_TITLE_HOOK_ORIGINAL,
        )
        self.assertEqual(
            edited[
                session_file_hook_offset:
                session_file_hook_offset + len(LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_ORIGINAL)
            ],
            LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_ORIGINAL,
        )
        self.assertEqual(
            edited[
                title_availability_hook_offset:
                title_availability_hook_offset + len(LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_HOOK_ORIGINAL)
            ],
            LOAD_SLOT_SELECTOR_TITLE_AVAILABILITY_HOOK_ORIGINAL,
        )
        self.assertFalse(any(edited[
            slot_offset:slot_offset + LOAD_SLOT_SELECTOR_SLOT_SIZE
        ]))


if __name__ == "__main__":
    unittest.main()
