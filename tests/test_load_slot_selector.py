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
    LOAD_SLOT_SELECTOR_MENU_COUNT,
    LOAD_SLOT_SELECTOR_MENU_ITEMS_OFFSET,
    LOAD_SLOT_SELECTOR_SAVE_NAME_POINTERS_OFFSET,
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
            wrapper[0x126:0x130],
            bytes.fromhex("83 C4 20 61 B8 01 00 00 00 C3"),
        )
        self.assertEqual(wrapper[0x13B:0x13E], bytes.fromhex("31 C0 C3"))
        self.assertGreaterEqual(wrapper.count(bytes.fromhex("FF 15 44 F4 62 00")), 2)
        self.assertGreaterEqual(wrapper.count(bytes.fromhex("FF 15 74 F4 62 00")), 1)
        self.assertEqual(wrapper[0xD5], 0xE9)
        self.assertEqual(
            _relative_target(wrapper, wrapper_va, 0xD5),
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
            slot_va + LOAD_SLOT_SELECTOR_WRAPPER_OFFSET + 0x126,
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

        for index in range(LOAD_SLOT_SELECTOR_MENU_COUNT):
            label_va, enabled, visible = struct.unpack_from(
                "<III", edited,
                slot_offset + LOAD_SLOT_SELECTOR_MENU_ITEMS_OFFSET + index * 12,
            )
            self.assertEqual(
                label_va,
                slot_va + LOAD_SLOT_SELECTOR_LABELS_OFFSET + index * LOAD_SLOT_SELECTOR_LABEL_STRIDE,
            )
            self.assertEqual((enabled, visible), (1, 1))
        cancel_offset = (
            slot_offset + LOAD_SLOT_SELECTOR_LABELS_OFFSET
            + LOAD_SLOT_SELECTOR_SAVE_COUNT * LOAD_SLOT_SELECTOR_LABEL_STRIDE
        )
        self.assertEqual(
            bytes(edited[cancel_offset:cancel_offset + LOAD_SLOT_SELECTOR_LABEL_STRIDE]).split(b"\0", 1)[0],
            "취소".encode("cp949"),
        )

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
        self.assertFalse(any(edited[
            slot_offset:slot_offset + LOAD_SLOT_SELECTOR_SLOT_SIZE
        ]))


if __name__ == "__main__":
    unittest.main()
