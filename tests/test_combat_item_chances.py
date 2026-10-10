"""Local probability overrides, original restoration and native roll behavior."""
from pathlib import Path
import inspect
import struct
import sys
import tempfile
import unittest

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Resources' / 'py'))
import combat_item_chance as chance
from patch_cds_integrated import apply_all, read_settings

try:
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
    from unicorn.x86_const import (
        UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX,
        UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP,
        UC_X86_REG_EIP,
    )
except ImportError:
    Uc = None

FIXTURE = Path(__file__).parent / 'fixtures' / 'coordinate_compass_test.exe'


def offset(data, va):
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_offset_from_rva(va - 0x400000)
    finally:
        pe.close()


class CombatItemChanceTests(unittest.TestCase):
    def test_all_percentages_read_back_and_restore_exactly(self):
        original = FIXTURE.read_bytes()
        data = bytearray(original)
        self.assertEqual(chance.read_combat_item_chances(data), chance.CombatItemChances())
        for percent in range(101):
            settings = chance.CombatItemChances(percent, percent, percent)
            chance.apply_combat_item_chances(data, settings)
            self.assertEqual(chance.read_combat_item_chances(data),
                             chance.CombatItemChances(percent, percent,
                                                      None if percent == 40 else percent))
            self.assertFalse(chance.apply_combat_item_chances(data, settings))
        self.assertTrue(chance.apply_combat_item_chances(data, chance.CombatItemChances()))
        self.assertEqual(data, original)

    def test_independent_settings_only_touch_local_roll_sites(self):
        original = FIXTURE.read_bytes()
        for settings, va, size in (
            (chance.CombatItemChances(submarine_bomb=100), chance.BOMB_VA, len(chance.BOMB_ORIGINAL)),
            (chance.CombatItemChances(rapid_cannon=0), chance.RAPID_VA, len(chance.RAPID_ORIGINAL)),
            (chance.CombatItemChances(explosive_shell=100), chance.SHELL_VA, 1),
        ):
            data = bytearray(original)
            chance.apply_combat_item_chances(data, settings)
            pos = offset(data, va)
            self.assertEqual(data[:pos], original[:pos])
            self.assertEqual(data[pos + size:], original[pos + size:])
            self.assertEqual(len(data), len(original))

    def test_bad_percentage_or_unknown_code_rejects_without_writing(self):
        original = FIXTURE.read_bytes()
        for value in (-1, 101, 1.5, '50', True):
            for field in ('submarine_bomb', 'rapid_cannon', 'explosive_shell'):
                data = bytearray(original)
                with self.assertRaises(ValueError):
                    chance.apply_combat_item_chances(data, chance.CombatItemChances(**{field: value}))
                self.assertEqual(data, original)
        for va in [chance.BOMB_VA, chance.RAPID_VA, chance.SHELL_VA,
                   *(va for va, _ in chance.CONTEXTS)]:
            data = bytearray(original)
            data[offset(data, va)] ^= 0x80
            before = bytes(data)
            with self.assertRaises(ValueError):
                chance.apply_combat_item_chances(data, chance.CombatItemChances(100, 100, 100))
            self.assertEqual(data, before)

    def test_integrated_apply_preserves_omitted_setting_and_restores(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'CDS_95.EXE'
            target.write_bytes(FIXTURE.read_bytes())
            custom = chance.CombatItemChances(100, 51, 99)
            for style, option, expected in (
                ('korean3', custom, custom), ('korean2', None, custom),
                ('original', chance.CombatItemChances(), chance.CombatItemChances()),
            ):
                settings = read_settings(target)
                arguments = inspect.signature(apply_all).bind(
                    target, style, True, settings[1], *settings[2:],
                ).arguments
                if option is not None:
                    arguments['combat_item_chances'] = option
                apply_all(**arguments)
                self.assertEqual(chance.read_combat_item_chances(target.read_bytes()), expected)


@unittest.skipIf(Uc is None, 'Unicorn is needed for native probability tests')
class NativeCombatItemChanceTests(unittest.TestCase):
    def setUp(self):
        self.data = bytearray(FIXTURE.read_bytes())
        self.uc = Uc(UC_ARCH_X86, UC_MODE_32)
        pe = pefile.PE(data=bytes(self.data), fast_load=True)
        self.uc.mem_map(0x400000, (pe.OPTIONAL_HEADER.SizeOfImage + 0xFFF) & ~0xFFF)
        self.uc.mem_write(0x400000, pe.get_memory_mapped_image())
        pe.close()
        self.uc.mem_map(0x800000, 0x10000)
        self.uc.hook_add(UC_HOOK_CODE, self.on_instruction)

    def on_instruction(self, machine, address, size, _user):
        if address == 0x4B7C0F:
            sp = machine.reg_read(UC_X86_REG_ESP)
            ret, bound = struct.unpack('<II', machine.mem_read(sp, 8))
            self.bounds.append(bound)
            if not self.draws:
                raise AssertionError('Unexpected extra RNG call')
            value = self.draws.pop(0)
            self.assertLess(value, max(1, bound))
            machine.reg_write(UC_X86_REG_EAX, value)
            machine.reg_write(UC_X86_REG_ESP, sp + 4)
            machine.reg_write(UC_X86_REG_EIP, ret)
        elif address in self.stops:
            self.result = self.stops[address]
            machine.emu_stop()

    def run_roll(self, kind, draws, luck=100, gunnery=10):
        start, success, fail = {
            'bomb': (chance.BOMB_VA, 0x4365CA, 0x4365C2),
            'rapid': (chance.RAPID_VA, 0x441EE3, 0x441EDB),
            'shell': (0x448DF4, 0x448E03, 0x448E2D),
        }[kind]
        self.draws, self.bounds, self.result = list(draws), [], None
        self.stops = {success: True, fail: False}
        sp, bp, battle = 0x80F000, 0x808000, 0x800000
        self.uc.mem_write(battle + 0x910, struct.pack('<II', luck, gunnery))
        self.uc.mem_write(bp - 0x9A0, struct.pack('<I', battle + 0x910))
        registers = {
            UC_X86_REG_ESP: sp, UC_X86_REG_EBP: bp, UC_X86_REG_EDI: 7,
            UC_X86_REG_ESI: battle + 0x914 if kind == 'rapid' else battle,
            UC_X86_REG_EBX: 0x1234, UC_X86_REG_ECX: 0x5678,
            UC_X86_REG_EDX: 0xABCD,
        }
        for register, value in registers.items():
            self.uc.reg_write(register, value)
        self.uc.emu_start(start, max(self.stops) + 1, count=150)
        self.assertIsNotNone(self.result)
        self.assertFalse(self.draws)
        for register in (UC_X86_REG_ESP, UC_X86_REG_EBP, UC_X86_REG_ESI, UC_X86_REG_EDI):
            self.assertEqual(self.uc.reg_read(register), registers[register])
        return self.result

    def test_fixed_roll_has_exact_threshold_and_only_one_random_call(self):
        for percent in range(101):
            chance.apply_combat_item_chances(self.data, chance.CombatItemChances(percent, percent, percent))
            for va, size in ((chance.BOMB_VA, len(chance.BOMB_ORIGINAL)),
                             (chance.RAPID_VA, len(chance.RAPID_ORIGINAL)),
                             (chance.SHELL_VA, 1)):
                pos = offset(self.data, va)
                self.uc.mem_write(va, bytes(self.data[pos:pos + size]))
                self.uc.ctl_remove_cache(va, va + size)
            for kind in ('bomb', 'rapid', 'shell'):
                successes = 0
                for draw in range(100):
                    successes += self.run_roll(kind, [draw])
                    self.assertEqual(self.bounds, [100])
                self.assertEqual(successes, percent, (kind, percent))

    def test_original_bomb_still_uses_two_draws_and_rapid_uses_stats(self):
        for first, second in ((0, 0), (49, 48), (49, 49), (99, 98), (99, 99)):
            self.assertEqual(self.run_roll('bomb', [first, second]), second < first)
            self.assertEqual(self.bounds, [100, 100])
        for skill in (0, 5, 10):
            threshold = skill * 5 + 100 // 15
            for draw in (threshold - 1, threshold):
                self.assertEqual(self.run_roll('rapid', [draw], gunnery=skill), draw < threshold)
                self.assertEqual(self.bounds, [100])


if __name__ == '__main__':
    unittest.main()
