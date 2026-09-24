"""Regression tests for moving the legacy save into a numbered slot."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from save_slot_migration import migrate_legacy_savedata  # noqa: E402


class SaveSlotMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.exe = self.folder / "CDS_95.EXE"
        self.exe.write_bytes(b"exe")
        self.legacy = self.folder / "SAVEDATA.CDS"

    def test_uses_first_slot_when_empty(self) -> None:
        self.legacy.write_bytes(b"legacy save")
        destination = migrate_legacy_savedata(self.exe)
        self.assertEqual(destination, self.folder / "SAVEDATA01.CDS")
        self.assertEqual(destination.read_bytes(), b"legacy save")
        self.assertFalse(self.legacy.exists())
        self.assertIsNone(migrate_legacy_savedata(self.exe))

    def test_skips_existing_slots_without_overwriting_them(self) -> None:
        first = self.folder / "SAVEDATA01.CDS"
        second = self.folder / "SAVEDATA02.CDS"
        first.write_bytes(b"first save")
        second.write_bytes(b"second save")
        self.legacy.write_bytes(b"legacy save")

        destination = migrate_legacy_savedata(self.exe)

        self.assertEqual(destination, self.folder / "SAVEDATA03.CDS")
        self.assertEqual(first.read_bytes(), b"first save")
        self.assertEqual(second.read_bytes(), b"second save")
        self.assertEqual(destination.read_bytes(), b"legacy save")
        self.assertFalse(self.legacy.exists())

    def test_full_slots_leave_legacy_save_unchanged(self) -> None:
        for number in range(1, 11):
            (self.folder / f"SAVEDATA{number:02d}.CDS").write_bytes(bytes((number,)))
        self.legacy.write_bytes(b"legacy save")

        self.assertIsNone(migrate_legacy_savedata(self.exe))

        self.assertEqual(self.legacy.read_bytes(), b"legacy save")
        for number in range(1, 11):
            self.assertEqual(
                (self.folder / f"SAVEDATA{number:02d}.CDS").read_bytes(),
                bytes((number,)),
            )

    def test_no_legacy_save_does_nothing(self) -> None:
        self.assertIsNone(migrate_legacy_savedata(self.exe))
        self.assertEqual(list(self.folder.iterdir()), [self.exe])


if __name__ == "__main__":
    unittest.main()
