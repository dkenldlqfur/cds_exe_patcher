"""Parked ship selection, native renderer arguments and reversible installation."""
from pathlib import Path
from functools import lru_cache
import hashlib
import struct
import sys
import tempfile
import unittest

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Resources' / 'py'))
import landing_ship_image_patch as fix
from pe_patch_section import (
    LANDING_SHIP_IMAGE_SLOT_OFFSET, LANDING_SHIP_IMAGE_SLOT_SIZE,
    find_patch_section,
)
from patch_cds_integrated import apply_all, read_settings
from localization_patch import apply_extended_localization, read_extended_localization_state
from world_map_follow_patch import apply_world_map_follow_patch, read_world_map_follow_patch_state
from city_discovery_notice_patch import apply_city_discovery_notice_patch, read_city_discovery_notice_patch_state
from high_speed_map_patch import apply_high_speed_map_fix, read_high_speed_map_fix_state

try:
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32
    from unicorn.x86_const import (
        UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX,
        UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP,
        UC_X86_REG_EIP, UC_X86_REG_EFLAGS,
    )
except ImportError:
    Uc = None

FIXTURE = Path(__file__).parent / 'fixtures' / 'coordinate_compass_test.exe'


def hook_offset(data):
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_offset_from_rva(fix.HOOK_VA - 0x400000)
    finally:
        pe.close()


class LandingShipPatchTests(unittest.TestCase):
    def legacy_data(self, version=1):
        data = bytearray(FIXTURE.read_bytes())
        fix.apply_landing_ship_image_fix(data, True)
        section = find_patch_section(data)
        off, va = section.slot(LANDING_SHIP_IMAGE_SLOT_OFFSET, LANDING_SHIP_IMAGE_SLOT_SIZE)
        builder = fix._legacy_payload if version == 1 else fix._v2_payload
        data[off:off+LANDING_SHIP_IMAGE_SLOT_SIZE] = builder(va)
        pe = pefile.PE(data=bytes(data), fast_load=True)
        for hook_va, original, _, _ in fix.EXTRA_HOOKS:
            pos = pe.get_offset_from_rva(hook_va-0x400000)
            data[pos:pos+len(original)] = original
        pe.close()
        return data

    def test_legacy_payload_is_byte_exact_and_upgrades_or_removes(self):
        self.assertEqual(hashlib.sha256(fix._legacy_payload(0x700000)).hexdigest(),
                         'a0885a9f6f2d08b7b971eca861a50876c539c6cd9c535c7670afca215f387444')
        self.assertEqual(hashlib.sha256(fix._v2_payload(0x700000)).hexdigest(),
                         '24844df01c459dbab21aaee3eec0d233edb9869276f2e1b3fdfc66e6eabdf324')
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                data = self.legacy_data()
                self.assertEqual(fix._patch_version(data), 1)
                self.assertTrue(fix.read_landing_ship_image_fix_state(data))
                self.assertTrue(fix.apply_landing_ship_image_fix(data, enabled))
                self.assertEqual(fix._patch_version(data), fix.VERSION if enabled else 0)
                self.assertFalse(fix.apply_landing_ship_image_fix(data, enabled))
        for enabled in (True, False):
            data = self.legacy_data(2)
            self.assertEqual(fix._patch_version(data), 2)
            fix.apply_landing_ship_image_fix(data, enabled)
            self.assertEqual(fix._patch_version(data), fix.VERSION if enabled else 0)

    def test_integrated_legacy_upgrade_without_unchecking(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'CDS_95.EXE'
            target.write_bytes(self.legacy_data())
            settings = read_settings(target)
            self.assertIsNotNone(apply_all(target, settings[0], True, settings[1], *settings[2:]))
            self.assertEqual(fix._patch_version(target.read_bytes()), fix.VERSION)

    def test_apply_reload_repeat_disable_and_only_hook_changes(self):
        original = FIXTURE.read_bytes()
        data = bytearray(original)
        self.assertFalse(fix.read_landing_ship_image_fix_state(data))
        self.assertFalse(fix.apply_landing_ship_image_fix(data, False))
        self.assertEqual(data, original)
        self.assertTrue(fix.apply_landing_ship_image_fix(data, True))
        self.assertTrue(fix.read_landing_ship_image_fix_state(data))
        self.assertFalse(fix.apply_landing_ship_image_fix(data, True))
        off = hook_offset(data)
        pe = pefile.PE(data=original, fast_load=True)
        try:
            for section in pe.sections:
                lo, end = section.PointerToRawData, section.PointerToRawData + section.SizeOfRawData
                expected = bytearray(original[lo:end])
                if lo <= off < end:
                    expected[off-lo:off-lo+len(fix.ORIGINAL)] = data[off:off+len(fix.ORIGINAL)]
                for va, code, _, _ in fix.EXTRA_HOOKS:
                    pos = pe.get_offset_from_rva(va-0x400000)
                    if lo <= pos < end:
                        expected[pos-lo:pos-lo+len(code)] = data[pos:pos+len(code)]
                self.assertEqual(data[lo:end], expected)
        finally:
            pe.close()
        fix.apply_landing_ship_image_fix(data, False)
        self.assertEqual(data[off:off+len(fix.ORIGINAL)], fix.ORIGINAL)
        section = find_patch_section(data)
        start = section.raw_offset + LANDING_SHIP_IMAGE_SLOT_OFFSET
        self.assertFalse(any(data[start:start+LANDING_SHIP_IMAGE_SLOT_SIZE]))
        self.assertFalse(fix.read_landing_ship_image_fix_state(data))
        self.assertFalse(fix.apply_landing_ship_image_fix(data, False))
        fix._check_extra_hooks(data)

    def test_corrupt_hook_payload_context_and_orphan_rejected_without_writes(self):
        installed = bytearray(FIXTURE.read_bytes())
        fix.apply_landing_ship_image_fix(installed, True)
        section = find_patch_section(installed)
        off = hook_offset(installed)
        for target in (off, section.raw_offset + LANDING_SHIP_IMAGE_SLOT_OFFSET + fix.WRAPPER_OFFSET,
                       off - 1, section.raw_offset + LANDING_SHIP_IMAGE_SLOT_OFFSET + 255):
            data = bytearray(installed)
            data[target] ^= 1
            for enabled in (True, False):
                before = bytes(data)
                with self.assertRaises(ValueError):
                    fix.apply_landing_ship_image_fix(data, enabled)
                self.assertEqual(data, before)
        pe = pefile.PE(data=bytes(installed), fast_load=True)
        for va, _, _, _ in fix.EXTRA_HOOKS:
            data = bytearray(installed)
            data[pe.get_offset_from_rva(va-0x400000)] ^= 1
            before = bytes(data)
            with self.assertRaises(ValueError):
                fix.apply_landing_ship_image_fix(data, False)
            self.assertEqual(data, before)
        pe.close()
        data = bytearray(installed)
        struct.pack_into('<I', data, section.header_offset+36, 0x60000020)
        with self.assertRaises(ValueError):
            fix.read_landing_ship_image_fix_state(data)
        data = bytearray(installed)
        data[off:off+len(fix.ORIGINAL)] = fix.ORIGINAL
        with self.assertRaises(ValueError):
            fix.read_landing_ship_image_fix_state(data)

    def test_other_map_and_localization_patches_are_preserved(self):
        data = bytearray(FIXTURE.read_bytes())
        apply_city_discovery_notice_patch(data, True)
        apply_high_speed_map_fix(data, True)
        apply_world_map_follow_patch(data, True)
        apply_extended_localization(data, True)
        original = bytes(data)
        for enabled in (True, False):
            fix.apply_landing_ship_image_fix(data, enabled)
            self.assertTrue(read_city_discovery_notice_patch_state(data))
            self.assertTrue(read_high_speed_map_fix_state(data))
            self.assertTrue(read_world_map_follow_patch_state(data))
            self.assertTrue(read_extended_localization_state(data))
        off = hook_offset(data)
        self.assertEqual(data[off:off+len(fix.ORIGINAL)], original[off:off+len(fix.ORIGINAL)])
        section = find_patch_section(data)
        self.assertEqual(data[section.raw_offset:section.raw_offset+LANDING_SHIP_IMAGE_SLOT_OFFSET],
                         original[section.raw_offset:section.raw_offset+LANDING_SHIP_IMAGE_SLOT_OFFSET])

    def test_integrated_coordinate_save_and_omitted_option_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'CDS_95.EXE'
            target.write_bytes(FIXTURE.read_bytes())
            for style, enabled, expected in [('original', True, True), ('korean3', None, True),
                                              ('korean2', False, False), ('original', True, True)]:
                settings = read_settings(target)
                apply_all(target, style, True, settings[1], *settings[2:],
                          landing_ship_image_fix_enabled=enabled)
                self.assertEqual(fix.read_landing_ship_image_fix_state(target.read_bytes()), expected)


@unittest.skipIf(Uc is None, 'Install unicorn to execute x86 rendering setup')
class LandingShipExecutionTests(unittest.TestCase):
    @staticmethod
    @lru_cache(maxsize=2)
    def mapped_image(patched):
        data = bytearray(FIXTURE.read_bytes())
        if patched:
            fix.apply_landing_ship_image_fix(data, True)
        pe = pefile.PE(data=bytes(data))
        image = pe.get_memory_mapped_image()
        pe.close()
        return image

    def run_setup(self, ship_type, patched=True, mode=None, heading=None,
                  capture_site=0x48E76E, reset_site=None):
        image = self.mapped_image(patched)
        group = struct.unpack_from('<I', image, 0x1695D8 + ship_type * 4)[0]
        uc = Uc(UC_ARCH_X86, UC_MODE_32)
        uc.mem_map(0x400000, (len(image) + 4095) & ~4095)
        uc.mem_write(0x400000, image)
        uc.mem_map(0x800000, 0x2000)
        uc.mem_map(0x900000, 0x1000)
        esp = 0x801000
        uc.reg_write(UC_X86_REG_ESP, esp)
        uc.reg_write(UC_X86_REG_ESI, group)
        uc.reg_write(UC_X86_REG_ECX, 0x12345000)  # source surface
        for reg, value in [(UC_X86_REG_EBX, 0x11223344), (UC_X86_REG_EDX, 0x22334455),
                           (UC_X86_REG_EDI, 0x33445566), (UC_X86_REG_EBP, 0x44556677)]:
            uc.reg_write(reg, value)
        uc.mem_write(esp, struct.pack('<4I', 100, 120, 48, 48))
        uc.mem_write(esp + 0x24, struct.pack('<I', 0x900000))
        if heading is not None:
            uc.mem_write(fix.HEADING_VA, struct.pack('<I', heading))
            uc.reg_write(UC_X86_REG_EAX, 12345)
            uc.reg_write(UC_X86_REG_EFLAGS, 0x246)
            uc.emu_start(capture_site, capture_site+5, count=100)
            self.assertEqual(uc.reg_read(UC_X86_REG_EIP), capture_site+5)
            self.assertEqual(uc.reg_read(UC_X86_REG_ESP), esp)
            self.assertEqual(uc.reg_read(UC_X86_REG_EAX), 12345)
            self.assertEqual(uc.reg_read(UC_X86_REG_ECX), 0x12345000)
            self.assertEqual(uc.reg_read(UC_X86_REG_EFLAGS), 0x246)
            self.assertEqual(struct.unpack('<I', uc.mem_read(0x5B63B8, 4))[0], 12345)
            # Walking uses the same heading field: it must NOT rotate the ship.
            uc.mem_write(fix.HEADING_VA, struct.pack('<I', (heading+8) % 16))
        if reset_site is not None:
            original = next(o for v,o,_,_ in fix.EXTRA_HOOKS if v == reset_site)
            uc.reg_write(UC_X86_REG_ESI, 0x5B60A0)
            uc.emu_start(reset_site, reset_site+len(original), count=100)
            self.assertEqual(uc.reg_read(UC_X86_REG_EIP), reset_site+len(original))
            uc.reg_write(UC_X86_REG_ESI, group)
        stop = 0x48AC30
        if mode:
            uc.mem_write(0x5A4D1D, b'\x01' if mode == 'directdraw' else b'\x00')
            uc.mem_write(0x5A4D1A, b'\x02')
            uc.mem_write(0x5AA2D0, struct.pack('<4I', 0, 0, 640, 480))
            stop = 0x48AD17 if mode == 'directdraw' else 0x48ADD0
        uc.emu_start(fix.HOOK_VA, stop, count=200)
        self.assertEqual(uc.reg_read(UC_X86_REG_EIP), stop)
        return uc, group, esp

    def test_landing_heading_retained_for_every_ship_heading_and_renderer(self):
        for ship_type in range(8):
            for heading in range(16):
                for mode in ('directdraw', 'software'):
                    with self.subTest(ship_type=ship_type, heading=heading, mode=mode):
                        uc, group, _ = self.run_setup(ship_type, mode=mode, heading=heading)
                        frame = group * 8 + heading // 2
                        if mode == 'directdraw':
                            rect = struct.unpack('<4I', uc.mem_read(uc.reg_read(UC_X86_REG_ESP)+0x28, 16))
                            self.assertEqual(rect, (0, frame*48, 48, (frame+1)*48))
                        else:
                            self.assertEqual(uc.reg_read(UC_X86_REG_EDI), fix.PIXELS_VA+frame*fix.FRAME_BYTES)

    def test_second_sea_to_land_path_and_load_new_game_invalidation(self):
        for site in (0x48E76E, 0x49364C):
            for reset in (None, 0x47C35D, 0x47C654):
                uc, group, esp = self.run_setup(6, heading=14, capture_site=site, reset_site=reset)
                args = struct.unpack('<7I', uc.mem_read(esp-12,28))
                self.assertEqual(args[2], group*8+(2 if reset else 7))
                self.assertEqual(args[1], fix.PIXELS_VA+(group*8+(0 if reset else 7))*fix.FRAME_BYTES)

    def test_next_landing_replaces_previous_heading(self):
        uc, group, esp = self.run_setup(6, heading=14)
        uc.reg_write(UC_X86_REG_ESP, esp)
        uc.mem_write(fix.HEADING_VA, struct.pack('<I', 4))
        uc.emu_start(0x48E76E, 0x48E773, count=100)
        uc.mem_write(fix.HEADING_VA, struct.pack('<I', 12))
        uc.reg_write(UC_X86_REG_ECX, 0x12345000)
        uc.emu_start(fix.HOOK_VA, 0x48AC30, count=200)
        self.assertEqual(uc.reg_read(UC_X86_REG_EIP), 0x48AC30)
        args = struct.unpack('<7I', uc.mem_read(esp-12,28))
        self.assertEqual(args[2], group*8+2)
        self.assertEqual(args[1], fix.PIXELS_VA+(group*8+2)*fix.FRAME_BYTES)

    def test_all_eight_ship_types_frame_pointer_stack_and_registers(self):
        for ship_type in range(8):
            with self.subTest(ship_type=ship_type):
                uc, group, esp = self.run_setup(ship_type)
                frame = group * 8 + fix.PARKED_DIRECTION
                self.assertEqual(uc.reg_read(UC_X86_REG_ESP), esp - 16)
                args = struct.unpack('<7I', uc.mem_read(esp - 12, 28))
                self.assertEqual(args, (0x12345000, fix.PIXELS_VA + group * 8 * fix.FRAME_BYTES,
                                        frame, 100, 120, 48, 48))
                self.assertEqual(uc.reg_read(UC_X86_REG_ESI), group)
                self.assertEqual(uc.reg_read(UC_X86_REG_ECX), 0x900000)
                for reg, value in [(UC_X86_REG_EBX, 0x11223344), (UC_X86_REG_EDX, 0x22334455),
                                   (UC_X86_REG_EDI, 0x33445566), (UC_X86_REG_EBP, 0x44556677)]:
                    self.assertEqual(uc.reg_read(reg), value)

    def test_original_galleon_reproduces_mismatched_default_frame(self):
        uc, group, esp = self.run_setup(6, patched=False)
        self.assertEqual(group, 3)
        args = struct.unpack('<7I', uc.mem_read(esp - 12, 28))
        self.assertEqual(args[2], 2)
        self.assertEqual(args[1], fix.PIXELS_VA + 24 * fix.FRAME_BYTES)

    def test_native_directdraw_corrects_group_and_software_preserves_original(self):
        for ship_type in range(8):
            with self.subTest(ship_type=ship_type):
                uc, group, _ = self.run_setup(ship_type, mode='directdraw')
                frame = group * 8 + 2
                rect = struct.unpack('<4I', uc.mem_read(uc.reg_read(UC_X86_REG_ESP) + 0x28, 16))
                self.assertEqual(rect, (0, frame * 48, 48, (frame + 1) * 48))
                uc, _, _ = self.run_setup(ship_type, mode='software')
                original, _, _ = self.run_setup(ship_type, patched=False, mode='software')
                self.assertEqual(uc.reg_read(UC_X86_REG_EDI), fix.PIXELS_VA + group * 8 * fix.FRAME_BYTES)
                self.assertEqual(uc.reg_read(UC_X86_REG_EDI), original.reg_read(UC_X86_REG_EDI))
                original, _, _ = self.run_setup(ship_type, patched=False, mode='directdraw')
                original_rect = struct.unpack('<4I', original.mem_read(original.reg_read(UC_X86_REG_ESP) + 0x28, 16))
                self.assertEqual((rect[1] // 48) % 8, (original_rect[1] // 48) % 8)


if __name__ == '__main__':
    unittest.main()
