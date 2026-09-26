"""Tests for editable hard-coded land-unit combat formulas."""

from pathlib import Path
import struct
import sys
import unittest

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from patch_cds_integrated import (  # noqa: E402
    TROOP_ATTACK_FORMULA_COUNT,
    TROOP_ATTACK_FORMULA_TABLE_VA,
    TROOP_COMBAT_COUNT,
    TROOP_DEFENSE_FORMULA_TABLE_VA,
    TroopCombatEdit,
    _read_troop_combat_from_data,
    apply_troop_combat_edit,
)
from pe_patch_section import (  # noqa: E402
    TROOP_COMBAT_SLOT_OFFSET,
    TROOP_COMBAT_SLOT_SIZE,
    find_patch_section,
)


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"


def _offset(data: bytes | bytearray, va: int) -> int:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)
    finally:
        pe.close()


class TroopCombatFormulaTests(unittest.TestCase):
    def test_formulas_read_patch_and_restore_dispatch_tables(self) -> None:
        original = FIXTURE.read_bytes()
        records = _read_troop_combat_from_data(original)
        self.assertEqual(len(records), TROOP_COMBAT_COUNT)
        self.assertEqual((records[0].attack_technology_coefficient,
                          records[0].attack_stat_coefficient,
                          records[0].attack_flat_bonus), (9, 8, 0))
        self.assertEqual((records[21].attack_technology_coefficient,
                          records[21].defense_technology), (0, 11))

        attack_offset = _offset(original, TROOP_ATTACK_FORMULA_TABLE_VA)
        defense_offset = _offset(original, TROOP_DEFENSE_FORMULA_TABLE_VA)
        original_attack_table = original[attack_offset:attack_offset + TROOP_ATTACK_FORMULA_COUNT * 4]
        original_defense_table = original[defense_offset:defense_offset + TROOP_COMBAT_COUNT * 4]
        edited = bytearray(original)
        changed = TroopCombatEdit(0, 2, 10, 8, 1, 2, 5, 4, 0)
        self.assertTrue(apply_troop_combat_edit(edited, changed))
        self.assertEqual(_read_troop_combat_from_data(bytes(edited))[0].attack_flat_bonus, 1)
        self.assertNotEqual(edited[attack_offset:attack_offset + len(original_attack_table)], original_attack_table)
        self.assertNotEqual(edited[defense_offset:defense_offset + len(original_defense_table)], original_defense_table)
        self.assertFalse(apply_troop_combat_edit(edited, changed))

        defaults = records[0]
        restore = TroopCombatEdit(
            0, defaults.attack_technology, defaults.attack_technology_coefficient,
            defaults.attack_stat_coefficient, defaults.attack_flat_bonus,
            defaults.defense_technology, defaults.defense_technology_coefficient,
            defaults.defense_stat_coefficient, defaults.defense_flat_bonus,
        )
        self.assertTrue(apply_troop_combat_edit(edited, restore))
        self.assertEqual(edited[attack_offset:attack_offset + len(original_attack_table)], original_attack_table)
        self.assertEqual(edited[defense_offset:defense_offset + len(original_defense_table)], original_defense_table)
        section = find_patch_section(edited)
        self.assertIsNotNone(section)
        assert section is not None
        slot_offset, _ = section.slot(TROOP_COMBAT_SLOT_OFFSET, TROOP_COMBAT_SLOT_SIZE)
        self.assertFalse(any(edited[slot_offset:slot_offset + TROOP_COMBAT_SLOT_SIZE]))

    def test_classes_without_original_attack_branch_cannot_gain_one(self) -> None:
        edited = bytearray(FIXTURE.read_bytes())
        with self.assertRaisesRegex(ValueError, "공격 계산 분기"):
            apply_troop_combat_edit(edited, TroopCombatEdit(21, 2, 1, 1, 0, 11, 2, 3, 0))


if __name__ == "__main__":
    unittest.main()
