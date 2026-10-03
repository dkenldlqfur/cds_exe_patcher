"""Reversible inn saving and the native menu's label/dispatch decision."""
from functools import lru_cache
from pathlib import Path
import inspect
import struct
import sys
import tempfile
import unittest

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Resources' / 'py'))
import all_city_inn_save_patch as fix
from patch_cds_integrated import (
    SAVE_SLOT_SELECTOR_HOOK_VA, SAVE_SLOT_SELECTOR_WRAPPER_OFFSET,
    LOAD_SLOT_SELECTOR_COMMAND_HOOK_VA, LOAD_SLOT_SELECTOR_COMMAND_WRAPPER_OFFSET,
    apply_all, read_settings,
    apply_save_slot_selector_patch, apply_load_slot_selector_patch,
    read_save_slot_selector_patch_state, read_load_slot_selector_patch_state,
)
from pe_patch_section import (
    SAVE_SLOT_SELECTOR_SLOT_OFFSET, SAVE_SLOT_SELECTOR_SLOT_SIZE,
    LOAD_SLOT_SELECTOR_SLOT_OFFSET, LOAD_SLOT_SELECTOR_SLOT_SIZE,
    find_patch_section,
)

try:
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
    from unicorn.x86_const import (
        UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX,
        UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP,
        UC_X86_REG_EIP, UC_X86_REG_EFLAGS,
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


class AllCityInnSaveTests(unittest.TestCase):
    def test_round_trip_only_changes_four_bytes(self):
        original = FIXTURE.read_bytes()
        data = bytearray(original)
        self.assertFalse(fix.read_all_city_inn_save_state(data))
        self.assertFalse(fix.apply_all_city_inn_save_patch(data, False))
        self.assertEqual(data, original)
        self.assertTrue(fix.apply_all_city_inn_save_patch(data, True))
        self.assertTrue(fix.read_all_city_inn_save_state(bytes(data)))
        self.assertFalse(fix.apply_all_city_inn_save_patch(data, True))
        pos = offset(data, fix.PATCH_VA)
        self.assertEqual(data[pos:pos+4], bytes.fromhex('8B F5 90 90'))
        self.assertEqual(data[:pos], original[:pos])
        self.assertEqual(data[pos+4:], original[pos+4:])
        self.assertTrue(fix.apply_all_city_inn_save_patch(data, False))
        self.assertFalse(fix.read_all_city_inn_save_state(data))
        self.assertFalse(fix.apply_all_city_inn_save_patch(data, False))
        self.assertEqual(data, original)

    def test_unknown_instruction_and_contexts_reject_without_any_write(self):
        original = FIXTURE.read_bytes()
        addresses = [fix.PATCH_VA+i for i in range(4)]
        addresses.extend(va for va, _ in fix.CONTEXTS)
        for already_enabled in (False, True):
            for va in addresses:
                with self.subTest(already_enabled=already_enabled, va=hex(va)):
                    data = bytearray(original)
                    fix.apply_all_city_inn_save_patch(data, already_enabled)
                    data[offset(data, va)] ^= 0x80
                    before = bytes(data)
                    with self.assertRaises(ValueError):
                        fix.read_all_city_inn_save_state(data)
                    for enabled in (False, True):
                        with self.assertRaises(ValueError):
                            fix.apply_all_city_inn_save_patch(data, enabled)
                        self.assertEqual(data, before)

    def test_wrong_architecture_or_image_base_is_rejected(self):
        original = FIXTURE.read_bytes()
        pe = pefile.PE(data=original, fast_load=True)
        corruptions = (
            (pe.FILE_HEADER.get_field_absolute_offset('Machine'), '<H', 0x8664),
            (pe.OPTIONAL_HEADER.get_field_absolute_offset('ImageBase'), '<I', 0x500000),
        )
        pe.close()
        for pos, fmt, value in corruptions:
            data = bytearray(original)
            struct.pack_into(fmt, data, pos, value)
            before = bytes(data)
            with self.assertRaises(ValueError):
                fix.apply_all_city_inn_save_patch(data, True)
            self.assertEqual(data, before)

    def test_save_and_load_slot_hooks_are_not_overwritten(self):
        for inn_first in (False, True):
            data = bytearray(FIXTURE.read_bytes())
            if inn_first:
                fix.apply_all_city_inn_save_patch(data, True)
            apply_save_slot_selector_patch(data, True)
            apply_load_slot_selector_patch(data, True)
            before = bytes(data)
            for enabled in (True, False, True):
                fix.apply_all_city_inn_save_patch(data, enabled)
                self.assertEqual(fix.read_all_city_inn_save_state(data), enabled)
                self.assertTrue(read_save_slot_selector_patch_state(data))
                self.assertTrue(read_load_slot_selector_patch_state(data))
                pos = offset(data, fix.PATCH_VA)
                self.assertEqual(data[:pos], before[:pos])
                self.assertEqual(data[pos+4:], before[pos+4:])
            apply_save_slot_selector_patch(data, False)
            apply_load_slot_selector_patch(data, False)
            self.assertTrue(fix.read_all_city_inn_save_state(data))

    def test_integrated_apply_read_omitted_option_and_disable(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'CDS_95.EXE'
            target.write_bytes(FIXTURE.read_bytes())
            for style, option, expected in (
                ('original', None, False), ('korean3', True, True),
                ('korean2', None, True), ('original', False, False),
            ):
                with self.subTest(style=style, option=option):
                    settings = read_settings(target)
                    arguments = inspect.signature(apply_all).bind(
                        target, style, True, settings[1], *settings[2:],
                    ).arguments
                    arguments.update(save_slot_selector_enabled=True,
                                     load_slot_selector_enabled=True,
                                     all_city_inn_save_enabled=option)
                    apply_all(**arguments)
                    data = target.read_bytes()
                    self.assertEqual(fix.read_all_city_inn_save_state(data), expected)
                    self.assertTrue(read_save_slot_selector_patch_state(data))
                    self.assertTrue(read_load_slot_selector_patch_state(data))


@lru_cache(maxsize=4)
def executable_image(patched, slots):
    data = bytearray(FIXTURE.read_bytes())
    fix.apply_all_city_inn_save_patch(data, patched)
    destinations = {}
    if slots:
        apply_save_slot_selector_patch(data, True)
        apply_load_slot_selector_patch(data, True)
        section = find_patch_section(data)
        destinations['save'] = section.slot(
            SAVE_SLOT_SELECTOR_SLOT_OFFSET, SAVE_SLOT_SELECTOR_SLOT_SIZE,
        )[1] + SAVE_SLOT_SELECTOR_WRAPPER_OFFSET
        destinations['load'] = section.slot(
            LOAD_SLOT_SELECTOR_SLOT_OFFSET, LOAD_SLOT_SELECTOR_SLOT_SIZE,
        )[1] + LOAD_SLOT_SELECTOR_COMMAND_WRAPPER_OFFSET
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_memory_mapped_image(), pe.OPTIONAL_HEADER.SizeOfImage, destinations
    finally:
        pe.close()


class NativeInnHarness:
    """Run the original menu routine; stub only lookups and menu user input.

    Stop on the selected handler, or on the real ten-slot wrapper reached
    through that handler's installed JMP. No save I/O or gameplay is mocked
    into success: those operations are deliberately outside this test.
    """
    STACK = 0x800000
    SP = STACK + 0xF00
    SCRATCH = 0x900000
    VTABLE = SCRATCH
    CITY = SCRATCH + 0x100
    VIEW = SCRATCH + 0x200
    NATION_LOOKUP = SCRATCH + 0x300
    RETURN = SCRATCH + 0xF00

    def __init__(self, patched, slots=False):
        image, image_size, destinations = executable_image(patched, slots)
        self.uc = Uc(UC_ARCH_X86, UC_MODE_32)
        self.uc.mem_map(0x400000, (image_size+0xFFF) & ~0xFFF)
        self.uc.mem_write(0x400000, image)
        self.uc.mem_map(self.STACK, 0x2000)
        self.uc.mem_map(self.SCRATCH, 0x1000)
        self.uc.mem_write(self.RETURN, b'\xC3')
        self.put(0x5B60A0, self.VTABLE)
        self.put(self.VTABLE+0x14, self.NATION_LOOKUP)
        self.stops = {0x4A27D0: 'suspend', 0x4A2860: 'quit', self.RETURN: 'cancel'}
        if slots:
            self.stops.update({destinations['save']: 'save_slots', destinations['load']: 'load_slots'})
        else:
            self.stops.update({SAVE_SLOT_SELECTOR_HOOK_VA: 'save',
                               LOAD_SLOT_SELECTOR_COMMAND_HOOK_VA: 'load'})
        self.uc.hook_add(UC_HOOK_CODE, self._instruction)

    def put(self, address, value):
        self.uc.mem_write(address, struct.pack('<I', value & 0xFFFFFFFF))

    def get(self, address):
        return struct.unpack('<I', self.uc.mem_read(address, 4))[0]

    def return_value(self, value):
        sp = self.uc.reg_read(UC_X86_REG_ESP)
        self.uc.reg_write(UC_X86_REG_EAX, value)
        self.uc.reg_write(UC_X86_REG_EIP, self.get(sp))
        self.uc.reg_write(UC_X86_REG_ESP, sp+4)

    def _instruction(self, machine, address, size, user_data):
        if address == 0x4A17C0:
            self.return_value(self.CITY)
        elif address == self.NATION_LOOKUP:
            self.return_value(self.player_nation)
        elif address == 0x469A70:
            sp = machine.reg_read(UC_X86_REG_ESP)
            table = self.get(sp+4)
            self.menu = [struct.unpack('<III', machine.mem_read(table+i*12, 12)) for i in range(4)]
            self.return_value(self.selection)
        elif address in self.stops:
            self.destination = self.stops[address]
            machine.emu_stop()

    def run(self, player_nation, city_nation, selection=0):
        self.player_nation, self.selection = player_nation, selection
        self.destination, self.menu = None, None
        self.uc.mem_write(self.STACK, bytes(0x2000))
        self.put(self.SP, self.RETURN)
        self.put(self.CITY, city_nation)
        self.put(0x5A4D18, 8)
        self.registers = {
            UC_X86_REG_EAX: 0x11111111, UC_X86_REG_EBX: 0x22222222,
            UC_X86_REG_ECX: self.VIEW, UC_X86_REG_EDX: 0x33333333,
            UC_X86_REG_ESI: 0x44444444, UC_X86_REG_EDI: 0x55555555,
            UC_X86_REG_EBP: 0x66666666, UC_X86_REG_ESP: self.SP,
            UC_X86_REG_EFLAGS: 2,
        }
        for register, value in self.registers.items():
            self.uc.reg_write(register, value)
        self.uc.emu_start(0x4A28A0, self.RETURN+1, count=250)
        if self.destination is None or self.menu is None:
            raise AssertionError('Native inn menu did not reach a handler or cancellation')
        return self.destination, self.menu


@unittest.skipIf(Uc is None, 'Unicorn is needed for native inn menu regression tests')
class NativeAllCityInnSaveTests(unittest.TestCase):
    def test_all_78_city_nations_use_save_label_and_save_dispatch(self):
        # Both playable affiliations, all nation IDs, original and patched:
        # 312 native menu executions. Original foreign menus must suspend.
        for patched in (False, True):
            machine = NativeInnHarness(patched)
            for player in (0, 1):
                for city in range(78):
                    with self.subTest(patched=patched, player=player, city=city):
                        destination, menu = machine.run(player, city)
                        can_save = patched or player == city
                        self.assertEqual(destination, 'save' if can_save else 'suspend')
                        self.assertEqual(menu[0], (0x568DA8 if can_save else 0x568DB0, 1, 1))
                        self.assertEqual([row[1:] for row in menu], [(1, 1)] * 4)
                        self.assertEqual(machine.get(machine.CITY), city)
                        self.assertEqual(machine.get(0x5A4D18), 8)

    def test_other_menu_items_and_cancel_keep_native_dispatch_and_stack(self):
        for patched in (False, True):
            machine = NativeInnHarness(patched)
            for city in (0, 1, 77):
                for selection, expected in ((1, 'load'), (2, 'quit'), (3, 'cancel')):
                    with self.subTest(patched=patched, city=city, selection=selection):
                        destination, menu = machine.run(0, city, selection)
                        self.assertEqual(destination, expected)
                        self.assertEqual([row[0] for row in menu[1:]], [0x568D78, 0x568D80, 0x568D98])
                        if selection == 3:
                            self.assertEqual(machine.uc.reg_read(UC_X86_REG_ESP), machine.SP+4)
                            for register in (UC_X86_REG_EBX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP):
                                self.assertEqual(machine.uc.reg_read(register), machine.registers[register])

    def test_foreign_city_reaches_existing_ten_slot_save_and_load_wrappers(self):
        machine = NativeInnHarness(True, slots=True)
        for player in (0, 1):
            for city in (0, 1, 2, 77):
                for selection, expected in ((0, 'save_slots'), (1, 'load_slots'), (2, 'quit'), (3, 'cancel')):
                    with self.subTest(player=player, city=city, selection=selection):
                        destination, menu = machine.run(player, city, selection)
                        self.assertEqual(destination, expected)
                        self.assertEqual(menu[0], (0x568DA8, 1, 1))


if __name__ == '__main__':
    unittest.main()
