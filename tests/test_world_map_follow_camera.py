"""Execute the follow helper and the original mover, including native wind."""

from pathlib import Path
import struct
import sys
import unittest

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))
from world_map_follow_camera import build_camera, ENSURE_FOLLOW_OFFSET
from high_speed_map_patch import apply_high_speed_map_fix

try:
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
    from unicorn.x86_const import (
        UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX,
        UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP,
        UC_X86_REG_ESP, UC_X86_REG_EIP,
    )
except ImportError:
    Uc = None


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"


class _State(dict):
    def __missing__(self, key):
        value = 0x80A000 + len(self) * 0x100
        self[key] = value
        return value


class _Machine:
    def __init__(self, follow=True, origin=(1000, 300), relative=(320, 160),
                 dimensions=(640, 320), blocked_x=None, high_speed=False):
        data = bytearray(FIXTURE.read_bytes())
        if high_speed:
            apply_high_speed_map_fix(data, True)
        pe = pefile.PE(data=bytes(data))
        move_call = pe.get_offset_from_rva(0x48D356 - pe.OPTIONAL_HEADER.ImageBase)
        self.move_address = 0x48D35B + struct.unpack_from("<i", data, move_call + 1)[0]
        self.uc = Uc(UC_ARCH_X86, UC_MODE_32)
        self.uc.mem_map(0x400000, (pe.OPTIONAL_HEADER.SizeOfImage + 0xFFF) & ~0xFFF)
        self.uc.mem_write(0x400000, pe.get_memory_mapped_image())
        pe.close()
        self.uc.mem_map(0x800000, 0x10000)
        self.uc.mem_map(0x10000000, 0x20000)
        self.state = _State()
        chunks, hooks = build_camera(0x800000, self.state)
        for offset, payload in chunks.items():
            self.uc.mem_write(0x800000 + offset, payload)
        if follow:
            for va, before, after in hooks:
                assert bytes(self.uc.mem_read(va, len(before))) == before
                self.uc.mem_write(va, after)
        self.put(0x61B32C, *dimensions)
        self.put(0x61B3BC, dimensions[0] // 16, dimensions[1] // 16)
        self.put(0x5B63A8, *origin,
                 (origin[0] * 16 + relative[0]) % 40000,
                 origin[1] * 16 + relative[1])
        self.put(0x5B61B4, 0)
        self.uc.mem_write(0x5A4D1D, b"\x01")
        self.uc.mem_write(0x5A4D1A, b"\x02")
        for i in range(16):
            self.put(0x5B65D8 + i * 12, -1, 0, 0)
        self.put(0x5B65D8, 3, 27, 5, 9, 19, 7)
        for i in range(6):
            self.put(0x5B6890 + i * 8, 80 + i * 16, 100 + i * 16)
        self.wind_calls = []
        self.fog_calls = []
        self.animation_calls = []
        self.rng_calls = 0
        self.blocked_x = blocked_x
        self.uc.hook_add(UC_HOOK_CODE, self.intercept)

    def put(self, address, *values):
        self.uc.mem_write(address, struct.pack("<" + "i" * len(values), *values))

    def get(self, address):
        return struct.unpack("<i", self.uc.mem_read(address, 4))[0]

    def value(self, name):
        return self.get(self.state[name])

    @property
    def player(self):
        return self.get(0x5B63B0), self.get(0x5B63B4)

    @property
    def origin(self):
        return self.get(0x5B63A8), self.get(0x5B63AC)

    def intercept(self, uc, address, size, context):
        sp = uc.reg_read(UC_X86_REG_ESP)
        if address == 0x424E50:
            self.wind_calls.append(self.player)
            return  # Execute the original table lookup and RNG call.
        if address == 0x489360:
            self.animation_calls.append((self.get(sp + 4), self.get(sp + 8)))
            return  # Execute the original animation coordinate adjustment.
        if address == 0x4B7C0F:
            self.rng_calls += 1
            result, cleanup = 1, 0
        elif address == 0x426790:
            # A visibility rebuild may reorder NPC IDs and resets animation.
            self.put(0x5B65D8, 9, 0, 0, 3, 0, 0)
            for i in range(2, 16):
                self.put(0x5B65D8 + i * 12, -1, 0, 0)
            result, cleanup = 0, 0
        elif address == 0x4257E0:
            result, cleanup = 0, 4
        elif address == 0x425BA0:
            result = int(self.blocked_x is None or self.get(sp + 4) < self.blocked_x)
            cleanup = 8
        elif address == 0x468D90:
            self.fog_calls.append((self.get(sp + 4), self.get(sp + 8)))
            result, cleanup = 0, 8
        else:
            return
        uc.reg_write(UC_X86_REG_EAX, result)
        uc.reg_write(UC_X86_REG_ESP, sp + 4 + cleanup)
        uc.reg_write(UC_X86_REG_EIP, self.get(sp))

    def call(self, address, args=(), this=0x61B2D0, preserve_all=False):
        sp, stop = 0x10018000, 0x80FFF0
        self.put(sp, stop, *args)
        registers = {
            UC_X86_REG_EBX: 0x11112222, UC_X86_REG_ESI: 0x33334444,
            UC_X86_REG_EDI: 0x55556666, UC_X86_REG_EBP: 0x77778888,
        }
        if preserve_all:
            registers.update({UC_X86_REG_EAX: 0x12345678,
                              UC_X86_REG_ECX: this, UC_X86_REG_EDX: 0x23456789})
        for reg, value in registers.items():
            self.uc.reg_write(reg, value)
        self.uc.reg_write(UC_X86_REG_ECX, this)
        self.uc.reg_write(UC_X86_REG_ESP, sp)
        self.uc.emu_start(address, stop, count=500000)
        assert self.uc.reg_read(UC_X86_REG_EIP) == stop
        assert self.uc.reg_read(UC_X86_REG_ESP) == sp + 4 + len(args) * 4
        for reg, value in registers.items():
            assert self.uc.reg_read(reg) == value, (reg, self.uc.reg_read(reg), value)

    def ensure(self):
        self.call(0x800000 + ENSURE_FOLLOW_OFFSET, preserve_all=True)

    def move(self, dx, dy):
        self.call(self.move_address, (dx, dy), this=0x5B60A0)


@unittest.skipIf(Uc is None, "Install unicorn to execute the native camera")
class FollowCameraExecutionTests(unittest.TestCase):
    def assert_centered(self, machine):
        width, height = machine.value("width"), machine.value("height")
        x, y = machine.player
        ox, oy = machine.origin
        self.assertEqual((x - ox * 16 - machine.value("rx")) % 40000, width // 2)
        expected_y = min(max(y - height // 2, 0), 20000 - height)
        self.assertEqual(oy * 16 + machine.value("ry"), expected_y)
        self.assertTrue(0 <= machine.value("rx") < 16)
        self.assertTrue(0 <= machine.value("ry") < 16)

    def test_initial_center_pixel_residue_and_npc_state_preservation(self):
        machine = _Machine(relative=(319, 159))
        machine.ensure()
        self.assert_centered(machine)
        self.assertEqual(machine.origin, (999, 299))
        self.assertEqual((machine.value("rx"), machine.value("ry")), (15, 15))
        self.assertEqual(tuple(machine.get(0x5B65D8 + i * 4) for i in range(6)),
                         (9, 19, 7, 3, 27, 5))
        self.assertEqual(machine.rng_calls, 0)
        self.assertEqual(machine.animation_calls, [(-1, -1)])

    def test_repeated_native_movement_wind_rng_and_fog_are_unchanged(self):
        native = _Machine(False, relative=(119, 137))
        follow = _Machine(True, relative=(119, 137))
        follow.ensure()
        movements = [(17, -11)] * 24 + [(-31, 19)] * 29 + [(16, 0)] * 33
        for step in movements:
            with self.subTest(step=step):
                native.move(*step)
                follow.move(*step)
                self.assertEqual(follow.player, native.player)
                self.assertEqual((follow.value("legacy_x"), follow.value("legacy_y")), native.origin)
                self.assertEqual(follow.wind_calls, native.wind_calls)
                self.assertEqual(follow.rng_calls, native.rng_calls)
                self.assertEqual(follow.fog_calls, native.fog_calls)
                self.assertEqual(bytes(follow.uc.mem_read(0x586168, 4)),
                                 bytes(native.uc.mem_read(0x586168, 4)))
                self.assert_centered(follow)
        self.assertGreater(follow.rng_calls, 0)

    def test_world_wrap_large_motion_border_clamp_and_terrain(self):
        cases = [
            ((0, 300), (-640, 77), None),
            ((2480, 300), (640, -77), None),
            ((0, 0), (0, -640), None),
            ((0, 1230), (0, 640), None),
            ((1000, 300), (640, 51), 16400),
        ]
        for origin, step, blocked_x in cases:
            with self.subTest(origin=origin, step=step):
                native = _Machine(False, origin=origin, blocked_x=blocked_x)
                follow = _Machine(True, origin=origin, blocked_x=blocked_x)
                follow.ensure()
                native.move(*step)
                follow.move(*step)
                self.assertEqual(follow.player, native.player)
                self.assertEqual(follow.wind_calls, native.wind_calls)
                self.assertEqual(follow.fog_calls, native.fog_calls)
                self.assert_centered(follow)

    def test_high_speed_wrapper_and_follow_preserve_native_movement_together(self):
        cases = [
            ((0, 300), (89, 160), ((-640, 33), (640, -33)), None),
            ((0, 0), (320, 160), ((33, -640), (-33, 33)), None),
            ((0, 1230), (320, 160), ((-33, 640), (33, -33)), None),
            ((1000, 300), (320, 160), ((640, 33), (-33, -33)), 16400),
        ]
        for origin, relative, steps, blocked_x in cases:
            native = _Machine(False, origin=origin, relative=relative,
                              blocked_x=blocked_x, high_speed=True)
            follow = _Machine(True, origin=origin, relative=relative,
                              blocked_x=blocked_x, high_speed=True)
            self.assertNotEqual(native.move_address, 0x47D0C0)
            self.assertEqual(native.move_address, follow.move_address)
            follow.ensure()
            for step in steps:
                with self.subTest(origin=origin, step=step, blocked_x=blocked_x):
                    native.move(*step)
                    follow.move(*step)
                    self.assertEqual(follow.player, native.player)
                    self.assertEqual((follow.value("legacy_x"), follow.value("legacy_y")),
                                     native.origin)
                    self.assertEqual(follow.wind_calls, native.wind_calls)
                    self.assertEqual(follow.rng_calls, native.rng_calls)
                    self.assertEqual(follow.fog_calls, native.fog_calls)
                    self.assertEqual(bytes(follow.uc.mem_read(0x586168, 4)),
                                     bytes(native.uc.mem_read(0x586168, 4)))
                    self.assert_centered(follow)

    def test_external_load_resets_hidden_camera_without_wind_tick(self):
        machine = _Machine()
        machine.ensure()
        machine.move(11, 7)
        old_calls = list(machine.wind_calls)
        machine.put(0x5B63A8, 2470, 540, 39817, 8819)
        machine.ensure()
        self.assertEqual((machine.value("legacy_x"), machine.value("legacy_y")), (2470, 540))
        self.assert_centered(machine)
        self.assertEqual(machine.wind_calls, old_calls)
        # Loading a different position can also retain the same tile origin.
        before = machine.origin
        machine.put(0x5B63B0, 39999, 8991)
        machine.ensure()
        self.assertEqual((machine.value("legacy_x"), machine.value("legacy_y")), before)
        self.assert_centered(machine)

    def test_zero_movement_various_dimensions_and_world_animation_anchor(self):
        for dimensions in ((640, 320), (799, 533), (800, 480), (1920, 1088)):
            machine = _Machine(dimensions=dimensions, relative=(17, 35))
            initial = [(machine.get(0x5B6890 + i * 8) + 16000,
                        machine.get(0x5B6894 + i * 8) + 4800) for i in range(6)]
            machine.ensure()
            machine.move(0, 0)
            self.assert_centered(machine)
            ox, oy = machine.origin
            actual = [((machine.get(0x5B6890 + i * 8) + ox * 16) % 40000,
                       machine.get(0x5B6894 + i * 8) + oy * 16) for i in range(6)]
            self.assertEqual(actual, initial)

    def test_non_tile_aligned_dimensions_clamp_pixels_at_south_edge(self):
        machine = _Machine(dimensions=(799, 533), origin=(2480, 1217),
                           relative=(399, 475))
        machine.ensure()
        self.assert_centered(machine)
        self.assertEqual(machine.origin[1], 1216)
        self.assertEqual(machine.value("ry"), 11)
        self.assertEqual(machine.origin[1] * 16 + machine.value("ry") + 533, 20000)

    def test_sailing_and_adjacent_mouse_hooks_use_same_fractional_pixels(self):
        machine = _Machine(relative=(319, 153))
        machine.ensure()
        self.assertEqual((machine.value("rx"), machine.value("ry")), (15, 9))
        uc, sp, frame = machine.uc, 0x10018000, 0x10017000
        machine.put(frame - 0x24, 101, 203)
        uc.reg_write(UC_X86_REG_EBP, frame)
        uc.reg_write(UC_X86_REG_ESP, sp)
        uc.emu_start(0x48ECEF, 0x48ECF4, count=1000)
        self.assertEqual((machine.get(frame - 0x24), machine.get(frame - 0x20)), (116, 212))
        self.assertEqual(uc.reg_read(UC_X86_REG_ESP), sp)
        self.assertEqual(uc.reg_read(UC_X86_REG_ECX), 0x5B60A0)
        machine.put(sp + 0x18, 101, 203)
        uc.emu_start(0x48BAA6, 0x48BAAE, count=1000)
        self.assertEqual((machine.get(sp + 0x18), machine.get(sp + 0x1C)), (116, 212))
        self.assertEqual(uc.reg_read(UC_X86_REG_ESP), sp)
        self.assertEqual(uc.reg_read(UC_X86_REG_EAX), 116)
        self.assertEqual(uc.reg_read(UC_X86_REG_ECX), sp + 0x2C)

    def test_invalid_dimensions_do_not_call_render_resources(self):
        machine = _Machine(dimensions=(0, 0))
        origin = machine.origin
        machine.ensure()
        self.assertEqual(machine.origin, origin)
        self.assertEqual(machine.value("valid"), 0)
        self.assertEqual(machine.animation_calls, [])


if __name__ == "__main__":
    unittest.main()
