"""Regression tests for patches that also repair existing save slots."""

from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from kaaba_save_patch import (  # noqa: E402
    KAABA_MARKER_OFFSET,
    MINIMUM_SAVE_SIZE,
    UNDISCOVERED_MARKER as KAABA_UNDISCOVERED_MARKER,
    game_savedata_paths,
    promote_game_savedata,
)
from slave_patch import (  # noqa: E402
    LIBRARY_BRANCH_OFFSET,
    LIBRARY_BRANCH_ORIGINAL,
    LIBRARY_VALUE_OFFSET,
    LIBRARY_VALUE_ORIGINAL,
    SAVEDATA_LIBRARY_OFFSET,
    SAVEDATA_LIBRARY_PATCHED,
    SLAVE_DISCOVERY_MARKER_OFFSET,
    SLAVE_DISCOVERY_SAVE_OFFSET,
    UNDISCOVERED_STATE,
    apply_library_hint,
)


class SaveSlotCompanionPathTests(unittest.TestCase):
    def test_slot_only_installation_is_discovered_in_numeric_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exe = root / "CDS_95.EXE"
            exe.write_bytes(b"exe")
            (root / "SAVEDATA10.CDS").write_bytes(b"ten")
            (root / "SAVEDATA02.CDS").write_bytes(b"two")

            self.assertEqual(
                (root / "SAVEDATA02.CDS", root / "SAVEDATA10.CDS"),
                game_savedata_paths(exe),
            )

    def test_slave_hint_repairs_every_existing_slot_without_base_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exe = root / "CDS_95.EXE"
            exe_data = bytearray(LIBRARY_VALUE_OFFSET + 4)
            exe_data[LIBRARY_BRANCH_OFFSET:LIBRARY_BRANCH_OFFSET + 1] = LIBRARY_BRANCH_ORIGINAL
            exe_data[LIBRARY_VALUE_OFFSET:LIBRARY_VALUE_OFFSET + 4] = LIBRARY_VALUE_ORIGINAL
            exe.write_bytes(exe_data)

            for slot in (1, 10):
                save = bytearray(SLAVE_DISCOVERY_SAVE_OFFSET + 143)
                save[SAVEDATA_LIBRARY_OFFSET:SAVEDATA_LIBRARY_OFFSET + 2] = b"\xFF\xFF"
                save[SLAVE_DISCOVERY_MARKER_OFFSET] = 0
                (root / f"SAVEDATA{slot:02d}.CDS").write_bytes(save)

            apply_library_hint(exe, True)

            for slot in (1, 10):
                data = (root / f"SAVEDATA{slot:02d}.CDS").read_bytes()
                self.assertEqual(
                    SAVEDATA_LIBRARY_PATCHED,
                    data[SAVEDATA_LIBRARY_OFFSET:SAVEDATA_LIBRARY_OFFSET + 2],
                )
                self.assertEqual(UNDISCOVERED_STATE, data[SLAVE_DISCOVERY_MARKER_OFFSET])

    def test_kaaba_repair_uses_existing_slot_files_without_base_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exe = root / "CDS_95.EXE"
            exe.write_bytes(b"exe")
            for slot in (3, 7):
                save = bytearray(MINIMUM_SAVE_SIZE)
                save[0x15:0x17] = (1494).to_bytes(2, "little")
                save[0x19:0x1B] = bytes((1, 9))
                save[KAABA_MARKER_OFFSET] = 0
                (root / f"SAVEDATA{slot:02d}.CDS").write_bytes(save)

            backups = promote_game_savedata(exe)

            self.assertEqual(2, len(backups))
            for slot in (3, 7):
                data = (root / f"SAVEDATA{slot:02d}.CDS").read_bytes()
                self.assertEqual(KAABA_UNDISCOVERED_MARKER, data[KAABA_MARKER_OFFSET])


if __name__ == "__main__":
    unittest.main()
