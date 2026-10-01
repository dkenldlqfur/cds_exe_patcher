"""Reversible patch and execution tests against the native camera/mover."""

from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from high_speed_map_patch import (
    HOOK_VA, MAX_STEP, MOVE_VA, ORIGINAL,
    apply_high_speed_map_fix, read_high_speed_map_fix_state,
)
from pe_patch_section import (
    HIGH_SPEED_MAP_FIX_SLOT_OFFSET, HIGH_SPEED_MAP_FIX_SLOT_SIZE,
    find_patch_section,
)

try:
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
    from unicorn.x86_const import (
        UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX,
        UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP,
        UC_X86_REG_ESP, UC_X86_REG_EIP,
    )
except ImportError:
    Uc = None


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"


def _offset(data, va):
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)
    finally:
        pe.close()


class HighSpeedMapPatchTests(unittest.TestCase):
    def test_apply_reload_idempotence_restore_and_text_isolation(self):
        original = FIXTURE.read_bytes()
        data = bytearray(original)
        self.assertFalse(read_high_speed_map_fix_state(data))
        self.assertTrue(apply_high_speed_map_fix(data, True))
        self.assertTrue(read_high_speed_map_fix_state(bytes(data)))
        snapshot = bytes(data)
        self.assertFalse(apply_high_speed_map_fix(data, True))
        self.assertEqual(bytes(data), snapshot)
        hook = _offset(original, HOOK_VA)
        pe = pefile.PE(data=original)
        try:
            text = next(s for s in pe.sections if s.Name.rstrip(b"\0") == b".text")
            changed = {i for i in range(text.PointerToRawData, text.PointerToRawData + text.SizeOfRawData) if original[i] != data[i]}
        finally:
            pe.close()
        self.assertTrue(changed)
        self.assertTrue(changed <= set(range(hook, hook + 5)))
        section = find_patch_section(data)
        off, _ = section.slot(HIGH_SPEED_MAP_FIX_SLOT_OFFSET, HIGH_SPEED_MAP_FIX_SLOT_SIZE)
        self.assertTrue(apply_high_speed_map_fix(data, False))
        self.assertFalse(read_high_speed_map_fix_state(data))
        self.assertEqual(data[hook:hook + 5], ORIGINAL)
        self.assertFalse(any(data[off:off + HIGH_SPEED_MAP_FIX_SLOT_SIZE]))
        self.assertFalse(apply_high_speed_map_fix(data, False))

    def test_rejects_altered_context_hook_and_payload(self):
        for corrupt in ("context", "hook", "payload", "orphan"):
            with self.subTest(corrupt=corrupt):
                data = bytearray(FIXTURE.read_bytes())
                if corrupt == "context":
                    data[_offset(data, MOVE_VA)] ^= 1
                else:
                    apply_high_speed_map_fix(data, True)
                    if corrupt == "hook":
                        data[_offset(data, HOOK_VA) + 1] ^= 1
                    elif corrupt == "orphan":
                        off = _offset(data, HOOK_VA)
                        data[off:off + 5] = ORIGINAL
                    else:
                        section = find_patch_section(data)
                        off, _ = section.slot(HIGH_SPEED_MAP_FIX_SLOT_OFFSET, HIGH_SPEED_MAP_FIX_SLOT_SIZE)
                        data[off + 0x30] ^= 1
                unchanged = bytes(data)
                with self.assertRaises(ValueError):
                    apply_high_speed_map_fix(data, True)
                self.assertEqual(bytes(data), unchanged)

    def test_integrated_save_coordinate_changes_and_city_labels_coexist(self):
        from patch_cds_integrated import apply_all, read_settings
        from city_discovery_notice_patch import read_city_discovery_notice_patch_state
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "CDS_95.EXE"
            shutil.copyfile(FIXTURE, target)
            for style, high, city in ((None, True, True), ("korean3", True, True), ("original", False, True), (None, True, False), (None, False, False)):
                settings = read_settings(target)
                apply_all(target, settings[0] if style is None else style, True,
                          settings[1], *settings[2:], high_speed_map_fix_enabled=high,
                          city_discovery_notice_enabled=city)
                data = target.read_bytes()
                self.assertEqual(read_high_speed_map_fix_state(data), high)
                self.assertEqual(read_city_discovery_notice_patch_state(data), city)


@unittest.skipIf(Uc is None, "Install unicorn to execute the native movement/camera")
class HighSpeedMapExecutionTests(unittest.TestCase):
    def _run(self, dx, dy, enabled=True, relative=(320, 160), origin=(1000, 300), dimensions=(640, 320), blocked_x=None):
        data = bytearray(FIXTURE.read_bytes())
        apply_high_speed_map_fix(data, enabled)
        pe = pefile.PE(data=bytes(data))
        try:
            mapped = pe.get_memory_mapped_image()
            image_size = (pe.OPTIONAL_HEADER.SizeOfImage + 0xFFF) & ~0xFFF
        finally:
            pe.close()
        uc = Uc(UC_ARCH_X86, UC_MODE_32)
        uc.mem_map(0x400000, image_size)
        uc.mem_write(0x400000, mapped)
        uc.mem_map(0x10000000, 0x20000)
        sp = 0x10018000

        def put(address, *values):
            uc.mem_write(address, struct.pack("<" + "i" * len(values), *values))

        def get(address):
            return struct.unpack("<i", uc.mem_read(address, 4))[0]

        initial = ((origin[0] * 16 + relative[0]) % 40000, origin[1] * 16 + relative[1])
        put(0x61B32C, *dimensions)
        put(0x5B63A8, *origin, *initial)
        put(0x5B61B4, 0)
        put(sp, dx, dy)
        registers = {UC_X86_REG_EBX: 0x11112222, UC_X86_REG_ESI: 0x33334444,
                     UC_X86_REG_EDI: 0x55556666, UC_X86_REG_EBP: 0x77778888}
        for reg, value in registers.items():
            uc.reg_write(reg, value)
        uc.reg_write(UC_X86_REG_ESP, sp)
        uc.reg_write(UC_X86_REG_ECX, 0x5B60A0)
        moves, shifts = [], []
        # Execute the real 47D0C0, coordinate getters, point-copy helper and
        # world-wrap counter. Stub only wind/NPC refresh, cache invalidation,
        # fog and terrain resource access; no replacement camera algorithm.
        stubs = {0x489360: 8, 0x424E50: 0, 0x426790: 0,
                 0x4257E0: 4, 0x425BA0: 8, 0x468D90: 8}

        def intercept(machine, address, size, context):
            stack = machine.reg_read(UC_X86_REG_ESP)
            if address == MOVE_VA:
                self.assertEqual(machine.reg_read(UC_X86_REG_ECX), 0x5B60A0)
                moves.append((get(stack + 4), get(stack + 8)))
            elif address in stubs:
                if address == 0x489360:
                    shifts.append((get(stack + 4), get(stack + 8)))
                result = 0
                if address == 0x425BA0:
                    result = int(blocked_x is None or get(stack + 4) < blocked_x)
                machine.reg_write(UC_X86_REG_EAX, result)
                machine.reg_write(UC_X86_REG_ESP, stack + 4 + stubs[address])
                machine.reg_write(UC_X86_REG_EIP, get(stack))

        uc.hook_add(UC_HOOK_CODE, intercept)
        uc.emu_start(HOOK_VA, HOOK_VA + 5, count=300000)
        self.assertEqual(uc.reg_read(UC_X86_REG_EIP), HOOK_VA + 5)
        self.assertEqual(uc.reg_read(UC_X86_REG_ESP), sp + 8)
        for reg, value in registers.items():
            self.assertEqual(uc.reg_read(reg), value)
        final = (get(0x5B63B0), get(0x5B63B4))
        camera = (get(0x5B63A8), get(0x5B63AC))
        sprite = ((final[0] - camera[0] * 16) % 40000 - 24,
                  final[1] - camera[1] * 16 - 24)
        return dict(initial=initial, final=final, camera=camera, sprite=sprite, moves=moves, shifts=shifts)

    def test_reproduces_then_fixes_westward_wrong_direction(self):
        broken = self._run(-90, 0, enabled=False, relative=(89, 160))
        self.assertEqual(broken["shifts"], [(20, 0)])
        self.assertGreater(broken["sprite"][0], 39000)
        fixed = self._run(-90, 0, relative=(89, 160))
        self.assertEqual(fixed["shifts"], [(-20, 0)])
        self.assertEqual(fixed["sprite"], (295, 136))
        self.assertEqual(fixed["final"], broken["final"])

    def test_normal_and_zero_steps_are_identical(self):
        for dx, dy in ((0, 0), (1, -1), (-32, 32), (32, -32), (16, 0)):
            with self.subTest(step=(dx, dy)):
                self.assertEqual(self._run(dx, dy), self._run(dx, dy, enabled=False))

    def test_large_steps_all_directions_preserve_distance_and_track_ship(self):
        for dx, dy in ((640, 0), (-640, 0), (0, 640), (0, -640),
                       (1280, 777), (-1280, -777), (999, -333), (-999, 333)):
            with self.subTest(step=(dx, dy)):
                result = self._run(dx, dy)
                self.assertEqual(result["final"], ((result["initial"][0] + dx) % 40000, result["initial"][1] + dy))
                self.assertEqual(sum(x for x, y in result["moves"]), dx)
                self.assertEqual(sum(y for x, y in result["moves"]), dy)
                self.assertTrue(all(max(abs(x), abs(y)) <= MAX_STEP for x, y in result["moves"]))
                x, y = result["sprite"]
                self.assertTrue(0 <= x and x + 48 < 640 and 0 <= y and y + 48 < 320)

    def test_world_wrap_in_both_directions_and_viewport_sizes(self):
        for dimensions in ((640, 320), (800, 480), (1920, 1088)):
            for origin, dx in (((0, 300), -1280), ((2480, 300), 1280)):
                with self.subTest(dimensions=dimensions, origin=origin):
                    w, h = dimensions
                    result = self._run(dx, 333, dimensions=dimensions, origin=origin, relative=(w // 2, h // 2))
                    self.assertEqual(result["final"][0], (result["initial"][0] + dx) % 40000)
                    self.assertTrue(0 <= result["camera"][0] < 2500)
                    x, y = result["sprite"]
                    self.assertTrue(0 <= x and x + 48 < w and 0 <= y and y + 48 < h)

    def test_world_north_south_limits_remain_native(self):
        north = self._run(0, -640, origin=(0, 0), relative=(320, 160))
        south = self._run(0, 640, origin=(0, 1230), relative=(320, 160))
        self.assertEqual(north["camera"][1], 0)
        self.assertEqual(north["final"][1], 0)
        self.assertEqual(south["camera"][1], 1230)
        self.assertEqual(south["final"][1], 0x4DF0)

    def test_native_terrain_checks_still_block_motion(self):
        result = self._run(640, 0, blocked_x=16400)
        self.assertLess(result["final"][0], 16400)
        self.assertGreaterEqual(result["final"][0], result["initial"][0])


if __name__ == "__main__":
    unittest.main()
