"""Execute renderer hooks, clipping and cache boundaries as real x86 code."""

from pathlib import Path
import struct
import sys
import unittest

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))
from world_map_follow_render import (  # noqa: E402
    MAP_SURFACE, SOFTWARE_CLIP, SOFTWARE_ORIGIN, build_render,
)

try:
    from unicorn import (
        Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE,
        UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE,
    )
    from unicorn.x86_const import (
        UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX,
        UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP,
        UC_X86_REG_EIP, UC_X86_REG_EFLAGS,
    )
except ImportError:
    Uc = None


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"
SLOT = 0x720000
VIEW = 0x730000
STACK = 0x8F0000
ENSURE = 0x710000
STOP = 0x710100
METHOD = 0x740000
SURFACE = 0x741000
VTABLE = 0x742000
DST = 0x743000
SRC = 0x743100
CACHE = 0x744000
MASK = 0x745000
LOCK = 0x740100
UNLOCK = 0x740200
RESTORE = 0x740300
TICK = 0x740400
PIXELS = 0x800000
ATLAS = 0x1000000


class _State(dict):
    def __missing__(self, name):
        self[name] = 0x700000 + len(self) * 4
        return self[name]


@unittest.skipIf(Uc is None, "Install unicorn to execute renderer hooks")
class RendererExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pe = pefile.PE(str(FIXTURE))
        cls.native = cls.pe.get_memory_mapped_image()

    @classmethod
    def tearDownClass(cls):
        cls.pe.close()

    def setUp(self):
        self.uc = Uc(UC_ARCH_X86, UC_MODE_32)
        self.uc.mem_map(0x400000, 0x500000)
        self.uc.mem_write(0x400000, self.native)
        self.state = _State(ensure_follow=ENSURE)
        chunks, hooks = build_render(SLOT, self.state)
        self.hooks = {va: (original, replacement) for va, original, replacement in hooks}
        for offset, blob in chunks.items():
            self.uc.mem_write(SLOT + offset, blob)
        for va, (original, replacement) in self.hooks.items():
            self.assertEqual(bytes(self.uc.mem_read(va, len(original))), original)
            self.uc.mem_write(va, replacement)
        self.uc.mem_write(ENSURE, b"\xc3")
        self.uc.mem_write(METHOD, bytes.fromhex("B8 78 56 34 12 C2 18 00"))
        self.put(SURFACE, VTABLE)
        self.put(VTABLE + 0x14, METHOD)
        self.put(MAP_SURFACE, SURFACE)
        self.put(VIEW + 0x54, 0, 32, 640, 320)
        self.put(VIEW + 0xEC, 40, 20)
        self.set_state(view=VIEW, width=640, height=320, rx=7, ry=11, active=0, valid=1)
        for address, value in zip(SOFTWARE_ORIGIN, (0, 32, 0, 32)):
            self.put(address, value)
        for address, value in zip(SOFTWARE_CLIP, (0, 0, 640, 480)):
            self.put(address, value)
        self.registers = {
            UC_X86_REG_EAX: 0x11223344, UC_X86_REG_EBX: 0x22334455,
            UC_X86_REG_ECX: 0x33445566, UC_X86_REG_EDX: 0x44556677,
            UC_X86_REG_ESI: 0x55667788, UC_X86_REG_EDI: 0x66778899,
            UC_X86_REG_EBP: 0x778899AA,
        }
        for register, value in self.registers.items():
            self.uc.reg_write(register, value)
        self.uc.reg_write(UC_X86_REG_ESP, STACK)
        self.uc.reg_write(UC_X86_REG_EFLAGS, 0x247)

    def put(self, address, *values):
        self.uc.mem_write(address, struct.pack("<" + "I" * len(values), *(x & 0xFFFFFFFF for x in values)))

    def get(self, address, count=1):
        result = struct.unpack("<" + "I" * count, self.uc.mem_read(address, count * 4))
        return result[0] if count == 1 else result

    def set_state(self, **values):
        for name, value in values.items():
            self.put(self.state[name], value)

    def run_to(self, start, *stops):
        reached = []

        def stop(uc, address, size, unused):
            if address in stops:
                reached.append(address)
                uc.emu_stop()

        token = self.uc.hook_add(UC_HOOK_CODE, stop)
        try:
            self.uc.emu_start(start, 0, count=2000000)
        finally:
            self.uc.hook_del(token)
        self.assertEqual(len(reached), 1, f"No continuation reached, EIP={self.uc.reg_read(UC_X86_REG_EIP):x}")
        return reached[0]

    def assert_registers(self, expected=None):
        for register, value in (expected or self.registers).items():
            self.assertEqual(self.uc.reg_read(register), value)

    def enter(self):
        self.put(STACK + 0x14, VIEW)
        self.put(STACK + 0x24, 123)
        self.run_to(0x48A2A9, 0x48A2B1)

    def test_entry_shifts_both_origins_and_bounds_and_preserves_registers(self):
        self.enter()
        self.assertEqual(self.get(VIEW + 0x54, 2), (0xFFFFFFF9, 21))
        self.assertEqual(tuple(self.get(x) for x in SOFTWARE_ORIGIN), (0xFFFFFFF9, 21) * 2)
        self.assertEqual(tuple(self.get(x) for x in SOFTWARE_CLIP), (0, 32, 640, 352))
        self.assertEqual(self.get(self.state["active"]), 1)
        self.assertEqual(self.get(STACK + 0x24), 0)
        self.assertEqual(self.uc.reg_read(UC_X86_REG_ESP), STACK)
        self.assertEqual(self.uc.reg_read(UC_X86_REG_EFLAGS), 0x247)
        self.assert_registers()

    def test_both_post_label_branches_restore_before_fixed_hud(self):
        for start, stop in ((0x48AB45, 0x48AB50), (0x48AB70, 0x48AB77)):
            with self.subTest(branch=hex(start)):
                self.enter()
                self.run_to(start, stop)
                self.assertEqual(self.get(VIEW + 0x54, 2), (0, 32))
                self.assertEqual(tuple(self.get(x) for x in SOFTWARE_ORIGIN), (0, 32) * 2)
                self.assertEqual(tuple(self.get(x) for x in SOFTWARE_CLIP), (0, 0, 640, 480))
                self.assertEqual(self.get(self.state["active"]), 0)
                self.assertEqual(self.uc.reg_read(UC_X86_REG_ESP), STACK)
                self.assertEqual(self.get(self.state["rx"]), 7)

    def test_invalid_dimensions_leave_origins_alone(self):
        self.set_state(width=0)
        self.enter()
        self.assertEqual(self.get(self.state["active"]), 0)
        self.assertEqual(self.get(VIEW + 0x54, 2), (0, 32))
        self.assert_registers()

    def test_invalid_camera_does_not_activate_using_stale_dimensions(self):
        self.set_state(valid=0)
        self.enter()
        self.assertEqual(self.get(self.state["active"]), 0)
        self.assertEqual(self.get(VIEW + 0x54, 2), (0, 32))
        self.assert_registers()

    def test_city_label_hook_and_validator_context_untouched(self):
        for va, (before, _) in self.hooks.items():
            self.assertFalse(va <= 0x48AB44 and va + len(before) > 0x48AB38)
        self.assertEqual(bytes(self.uc.mem_read(0x48AB43, 2)), b"\x74\x2b")

    def blit(self, destination, source, *, active=1, surface=SURFACE, method_result=0x12345678):
        self.set_state(active=active)
        self.put(DST, *destination)
        self.put(SRC, *source)
        self.put(STACK, STOP, surface, DST, 0x746000, SRC, 0x10000, 0x747000)
        self.uc.reg_write(UC_X86_REG_ESP, STACK)
        self.uc.mem_write(METHOD, b"\xb8" + struct.pack("<I", method_result) + b"\xc2\x18\x00")
        captured = []

        def capture(uc, address, size, unused):
            if address == METHOD:
                args = self.get(uc.reg_read(UC_X86_REG_ESP) + 4, 6)
                captured.append((args[0], self.get(args[1], 4), args[2], self.get(args[3], 4), args[4:]))

        token = self.uc.hook_add(UC_HOOK_CODE, capture)
        replacement = self.hooks[0x48A5F1][1]
        target = 0x48A5F6 + struct.unpack_from("<i", replacement, 1)[0]
        try:
            self.run_to(target, STOP)
        finally:
            self.uc.hook_del(token)
        self.assertEqual(self.uc.reg_read(UC_X86_REG_ESP), STACK + 28)
        self.assertEqual(self.get(DST, 4), tuple(x & 0xFFFFFFFF for x in destination))
        self.assertEqual(self.get(SRC, 4), tuple(x & 0xFFFFFFFF for x in source))
        self.assert_registers({r: self.registers[r] for r in (UC_X86_REG_EBX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP)})
        return captured, self.uc.reg_read(UC_X86_REG_EAX)

    def test_ddraw_clips_all_planes_without_mutating_retry_rectangles(self):
        self.set_state(rx=15, ry=15)
        captured, result = self.blit((-10, 22, 670, 382), (5, 9, 685, 369))
        self.assertEqual(captured, [(SURFACE, (0, 32, 640, 352), 0x746000, (30, 34, 670, 354), (0x10000, 0x747000))])
        self.assertEqual(result, 0x12345678)
        # Surface-lost HRESULT remains available to the unchanged native retry.
        captured2, result = self.blit((-10, 22, 670, 382), (5, 9, 685, 369), method_result=0x887601C2)
        self.assertEqual(captured2, captured)
        self.assertEqual(result, 0x887601C2)

    def test_partial_edges_cover_every_residual(self):
        for rx in range(16):
            for ry in range(16):
                self.set_state(rx=rx, ry=ry)
                calls, result = self.blit((0, 32, 16, 48), (0, 0, 16, 16))
                self.assertEqual(calls[0][1], (0, 32, 16 - rx, 48 - ry))
                self.assertEqual(calls[0][3], (rx, ry, 16, 16))
                self.assertEqual(result, 0x12345678)

    def test_empty_clip_never_calls_driver(self):
        calls, result = self.blit((660, 360, 676, 376), (0, 0, 16, 16))
        self.assertEqual(calls, [])
        self.assertEqual(result, 0)

    def test_failed_unlock_blocks_map_blits_only_for_the_current_active_frame(self):
        self.set_state(render_unlock_failed=1)
        calls, result = self.blit((0, 32, 16, 48), (0, 0, 16, 16))
        self.assertEqual((calls, result), ([], 0))
        calls, result = self.blit((0, 32, 16, 48), (0, 0, 16, 16), active=0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result, 0x12345678)

    def test_inactive_or_different_surface_has_original_coordinates(self):
        self.put(SURFACE + 0x100, VTABLE)
        for active, surface in ((0, SURFACE), (1, SURFACE + 0x100)):
            calls, _ = self.blit((0, 32, 16, 48), (0, 0, 16, 16), active=active, surface=surface)
            self.assertEqual(calls[0][1:4], ((0, 32, 16, 48), 0x746000, (0, 0, 16, 16)))

    def test_all_four_ddraw_adapters_preserve_argument_cleanup_and_native_hresult_register(self):
        cases = (
            (0x48A5F1, 0x48A5F8, True, UC_X86_REG_EBX),
            (0x48AD31, 0x48AD3E, False, UC_X86_REG_EBX),
            (0x49A794, 0x49A799, True, UC_X86_REG_EBP),
            (0x49A8A5, 0x49A8BC, False, UC_X86_REG_EBP),
        )
        for start, stop, self_already_pushed, result_register in cases:
            with self.subTest(site=hex(start)):
                self.set_state(active=1)
                self.put(DST, 0, 32, 16, 48)
                self.put(SRC, 0, 0, 16, 16)
                args = (DST, 0x746000, SRC, 0x10000, 0x747000)
                if self_already_pushed:
                    args = (SURFACE,) + args
                self.put(STACK, *args)
                self.uc.reg_write(UC_X86_REG_ESP, STACK)
                self.uc.reg_write(UC_X86_REG_EAX, SURFACE)
                captured = []

                def capture(uc, address, size, unused):
                    if address == METHOD:
                        arguments = self.get(uc.reg_read(UC_X86_REG_ESP) + 4, 6)
                        captured.append(self.get(arguments[1], 4))

                token = self.uc.hook_add(UC_HOOK_CODE, capture)
                try:
                    self.run_to(start, stop)
                finally:
                    self.uc.hook_del(token)
                self.assertEqual(captured, [(0, 32, 9, 37)])
                self.assertEqual(self.uc.reg_read(UC_X86_REG_ESP), STACK + 4 * len(args))
                self.assertEqual(self.uc.reg_read(result_register), 0x12345678)
                self.assertEqual(self.get(DST, 4), (0, 32, 16, 48))
                self.assertEqual(self.get(SRC, 4), (0, 0, 16, 16))

    def draw_clouds(self, points, *, width=640, height=320, rx=7, ry=11,
                    active=1, surface=SURFACE, retry=False):
        """Run the real six-cloud renderer, including clipping and cache writes."""
        self.assertEqual(len(points), 6)
        columns, rows = width // 16, height // 16
        manager, source = 0x5B6840, 0x746000
        self.set_state(active=active, width=width, height=height, rx=rx, ry=ry)
        self.put(0x5AA2D8, width, height)
        self.put(surface, VTABLE)
        self.put(manager, -2, -2)
        self.put(manager + 8, 0, 1, 2, 0, 1, 2)
        self.put(manager + 0x80, source)
        for index, point in enumerate(points):
            self.put(manager + 0x50 + index * 8, *point)
        before = bytes(self.uc.mem_read(manager, 0x84))
        cache_bytes = columns * rows * 2
        self.uc.mem_write(CACHE - 64, bytes([0xA5]) * (cache_bytes + 128))
        self.uc.mem_write(CACHE, bytes(cache_bytes))
        self.put(STACK, STOP, surface, CACHE, columns, rows)
        self.uc.reg_write(UC_X86_REG_ESP, STACK)
        self.uc.reg_write(UC_X86_REG_ECX, manager)
        saved_registers = {register: self.uc.reg_read(register) for register in
                           (UC_X86_REG_EBX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP)}
        calls, forbidden = [], []

        def capture(uc, address, size, unused):
            if address == METHOD:
                sp = uc.reg_read(UC_X86_REG_ESP)
                args = self.get(sp + 4, 6)
                calls.append((self.get(args[1], 4), self.get(args[3], 4)))
                # DDERR_WASSTILLDRAWING retries the same native local RECT.
                uc.reg_write(UC_X86_REG_EAX, 0x8876021C if retry and len(calls) == 1 else 0)
                uc.reg_write(UC_X86_REG_EIP, self.get(sp))
                uc.reg_write(UC_X86_REG_ESP, sp + 28)

        def guard(uc, access, address, size, value, unused):
            if CACHE - 64 <= address < CACHE + cache_bytes + 64:
                if address < CACHE or address + size > CACHE + cache_bytes:
                    forbidden.append((address, size))

        capture_hook = self.uc.hook_add(UC_HOOK_CODE, capture)
        guard_hook = self.uc.hook_add(UC_HOOK_MEM_WRITE, guard)
        try:
            self.run_to(0x4893A0, STOP)
        finally:
            self.uc.hook_del(capture_hook)
            self.uc.hook_del(guard_hook)
        self.assertEqual(forbidden, [])
        self.assertEqual(bytes(self.uc.mem_read(manager, 0x84)), before,
                         "Rendering must not drift persistent cloud/weather state")
        self.assertEqual(bytes(self.uc.mem_read(CACHE - 64, 64)), bytes([0xA5]) * 64)
        self.assertEqual(bytes(self.uc.mem_read(CACHE + cache_bytes, 64)), bytes([0xA5]) * 64)
        self.assertEqual(self.uc.reg_read(UC_X86_REG_ESP), STACK + 20)
        self.assert_registers(saved_registers)
        if retry:
            self.assertEqual(calls[0], calls[1], "A retry must not subtract rx/ry twice")
            calls.pop(0)
        return calls, bytes(self.uc.mem_read(CACHE, cache_bytes))

    def assert_cloud_rectangles(self, points, calls, *, width=640, height=320,
                                rx=7, ry=11):
        expected = []
        for index, (x, y) in enumerate(points):
            left, top = x - rx, y + 32 - ry
            right, bottom = left + 160, top + 120
            clipped = (max(0, left), max(32, top), min(width, right), min(height + 32, bottom))
            if clipped[0] >= clipped[2] or clipped[1] >= clipped[3]:
                continue
            frame = index % 3
            source_y = (self.get(0x519C70 + index * 4) + self.get(0x519CA0 + frame * 4)) * 120
            source = (clipped[0] - left, source_y + clipped[1] - top,
                      clipped[2] - left, source_y + clipped[3] - top)
            expected.append((clipped, source))
        self.assertEqual(calls, expected)

    def test_cloud_fractional_pixels_and_tile_crossing_are_continuous(self):
        anchors = [(200 + index * 20, 100 + index * 5) for index in range(6)]
        for delta in (0, 1, 7, 15, 16, 17, 31, 32):
            with self.subTest(delta=delta):
                tile_delta, residual = divmod(delta, 16)
                points = [(x - tile_delta * 16, y - tile_delta * 16) for x, y in anchors]
                calls, _ = self.draw_clouds(points, rx=residual, ry=residual)
                self.assert_cloud_rectangles(points, calls, rx=residual, ry=residual)
                self.assertEqual(calls[0][0][:2], (200 - delta, 132 - delta))

    def test_cloud_full_rectangles_preserve_right_bottom_slivers_and_clip_hud(self):
        width, height = 799, 533
        points = [(-8, -9), (width - 10, 90), (100, height - 10),
                  (width + 5, height + 5), (-200, 50), (60, -150)]
        calls, cache = self.draw_clouds(points, width=width, height=height, rx=15, ry=15)
        self.assert_cloud_rectangles(points, calls, width=width, height=height, rx=15, ry=15)
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[3][0], (789, 555, 799, 565))
        # Unshifted logical cloud bounds, not destination pixel bounds, own
        # cache invalidation. An edge-only cloud has no allocated cache cell.
        expected = bytearray(len(cache))
        columns, rows = width // 16, height // 16
        for x, y in points:
            left, top, right, bottom = max(0, x), max(0, y), min(width, x + 160), min(height, y + 120)
            if left >= right or top >= bottom:
                continue
            for row in range(top // 16, min(rows, (bottom + 15) // 16)):
                for col in range(left // 16, min(columns, (right + 15) // 16)):
                    index = (row * columns + col) * 2
                    expected[index:index + 2] = b"\xFF\xFF"
        self.assertEqual(cache, bytes(expected))

    def test_cloud_retries_do_not_repeat_fractional_translation(self):
        points = [(150 + index * 15, 70) for index in range(6)]
        calls, _ = self.draw_clouds(points, rx=13, ry=9, retry=True)
        self.assert_cloud_rectangles(points, calls, rx=13, ry=9)

    def test_cloud_inactive_or_other_surface_keeps_native_coordinates(self):
        points = [(150 + index * 15, 70) for index in range(6)]
        for active, surface in ((0, SURFACE), (1, SURFACE + 0x100)):
            with self.subTest(active=active, surface=hex(surface)):
                calls, _ = self.draw_clouds(points, active=active, surface=surface)
                self.assert_cloud_rectangles(points, calls, rx=0, ry=0)

    def prepare_tile(self, column, row, active=1):
        self.set_state(active=active, render_force_redraw=1)
        self.put(STACK + 0x14, VIEW, row, column)
        self.put(STACK + 0x38, CACHE + 16)
        self.put(STACK + 0x60, MASK)
        self.uc.mem_write(STACK + 0x12, b"\xAB\xCD")
        self.uc.mem_write(CACHE, bytes([0x55]) * 64)

    def test_edge_cache_reads_bypass_invalid_pointers(self):
        for column, row in ((40, 0), (0, 20), (40, 20), (41, 20)):
            with self.subTest(column=column, row=row):
                self.prepare_tile(column, row)
                self.put(STACK + 0x38, 0xDEAD0000)
                self.put(STACK + 0x60, 0xDEAD1000)
                self.assertEqual(self.run_to(0x48A556, 0x48A57F, 0x48A572), 0x48A57F)
                self.assertEqual(self.uc.reg_read(UC_X86_REG_ESP), STACK)
                self.assert_registers()

    def test_native_cells_keep_occlusion_mask_check(self):
        self.prepare_tile(39, 19)
        self.assertEqual(self.run_to(0x48A556, 0x48A57F, 0x48A572), 0x48A572)
        self.assert_registers()

    def test_stationary_camera_reuses_native_cache_and_dirty_actor_cells_still_redraw(self):
        self.enter()
        self.assertEqual(self.get(self.state["render_force_redraw"]), 1)
        self.run_to(0x48AB70, 0x48AB77)
        self.assertEqual(self.get(self.state["render_previous_valid"]), 1)
        self.enter()
        self.assertEqual(self.get(self.state["render_force_redraw"]), 0)
        self.prepare_tile(10, 10)
        self.set_state(render_force_redraw=0)
        self.put(STACK + 0x194, 0)
        self.uc.mem_write(CACHE + 16, b"\xAB\xCD")
        # An unchanged cached tile returns before dereferencing the mask.
        self.put(STACK + 0x60, 0xDEAD0000)
        self.assertEqual(self.run_to(0x48A556, 0x48A69D, 0x48A572), 0x48A69D)
        self.uc.mem_write(CACHE + 16, b"\xFF\xFF")
        self.assertEqual(self.run_to(0x48A556, 0x48A69D, 0x48A572), 0x48A572)

    def test_camera_movement_and_hidden_map_invalidate_stationary_cache(self):
        self.enter()
        self.run_to(0x48AB70, 0x48AB77)
        self.set_state(rx=8)
        self.enter()
        self.assertEqual(self.get(self.state["render_force_redraw"]), 1)
        self.run_to(0x48AB70, 0x48AB77)
        self.run_to(0x48A220, 0x48A226)
        self.assertEqual(self.get(self.state["render_previous_valid"]), 0)

    def test_edge_writes_skip_cache_but_in_bounds_write_exactly_one_word(self):
        for column, row in ((40, 0), (0, 20), (40, 20), (41, 20)):
            with self.subTest(column=column, row=row):
                self.prepare_tile(column, row)
                self.put(STACK + 0x38, 0xDEAD0000)
                self.run_to(0x48A691, 0x48A69D)
                self.assertEqual(bytes(self.uc.mem_read(CACHE, 64)), bytes([0x55]) * 64)
                self.assert_registers()
        self.prepare_tile(39, 19)
        self.run_to(0x48A691, 0x48A69D)
        self.assertEqual(bytes(self.uc.mem_read(CACHE, 64)), bytes([0x55]) * 16 + b"\xAB\xCD" + bytes([0x55]) * 46)

    def test_loop_counts_cover_non_tile_aligned_viewport(self):
        for width, height, rx, ry in ((640, 320, 0, 0), (640, 320, 15, 15), (639, 319, 15, 15), (997, 541, 0, 0)):
            self.set_state(active=1, width=width, height=height, rx=rx, ry=ry)
            self.put(VIEW + 0xEC, width // 16, height // 16)
            columns, rows = (width + rx + 15) // 16, (height + ry + 15) // 16
            for current, wanted in ((columns - 1, 0x48A3E4), (columns, 0x48A6CD)):
                self.uc.reg_write(UC_X86_REG_EAX, current)
                self.uc.reg_write(UC_X86_REG_ECX, VIEW)
                self.assertEqual(self.run_to(0x48A6C1, 0x48A3E4, 0x48A6CD), wanted)
                self.assertEqual(self.uc.reg_read(UC_X86_REG_ESP), STACK)
                self.assertEqual(self.uc.reg_read(UC_X86_REG_EDX), self.registers[UC_X86_REG_EDX])
            for current, wanted in ((rows - 1, 0x48A34E), (rows, 0x48A6ED)):
                self.uc.reg_write(UC_X86_REG_EDX, current)
                self.uc.reg_write(UC_X86_REG_ECX, VIEW)
                self.assertEqual(self.run_to(0x48A6E1, 0x48A34E, 0x48A6ED), wanted)
                self.assertEqual(self.uc.reg_read(UC_X86_REG_ESP), STACK)
            self.uc.reg_write(UC_X86_REG_EDX, self.registers[UC_X86_REG_EDX])

    def current_phase(self, world, local, *, direction, strength, tick, active=1):
        """Execute the native current tile-selection instructions and hooks."""
        self.set_state(active=active)
        self.put(STACK + 0x18, local[1], local[0])
        self.put(STACK + 0x54, world[1], world[0])
        self.put(STACK + 0x88, direction, strength)
        self.put(0x569554, tick)
        self.uc.mem_write(STACK + 0x12, b"\x80\x00")
        self.uc.reg_write(UC_X86_REG_ESP, STACK)
        self.run_to(0x48A4F7, 0x48A556)
        self.assertEqual(self.uc.reg_read(UC_X86_REG_ESP), STACK)
        self.assert_registers({r: self.registers[r] for r in
                               (UC_X86_REG_EBX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP)})
        self.assertEqual(self.get(0x569554), tick & 0xFFFFFFFF)
        self.assertEqual(self.get(STACK + 0x88, 2), (direction, strength))
        return int.from_bytes(self.uc.mem_read(STACK + 0x12, 2), "little")

    def expected_current_tile(self, coordinates, direction, strength, tick):
        dx, dy = struct.unpack("<ii", self.uc.mem_read(0x569558 + direction * 8, 8))
        value = (dx * coordinates[0] + dy * coordinates[1] - strength * tick * 16) & 0xFFFFFFFF
        if value & 0x80000000:
            value -= 0x100000000
        phase = (abs(value) // 64) * (-1 if value < 0 else 1)
        return 1408 if phase & 15 <= 7 else 128

    def test_current_phase_is_world_anchored_for_directions_strengths_and_native_ticks(self):
        world = (108, 211)
        for direction in range(16):
            for strength in (0, 1, 7, 15):
                for tick in (0, 1, 7, 1024, 0xFFFFFFFF):
                    expected = self.expected_current_tile(world, direction, strength, tick)
                    for local in ((8, 11), (7, 12), (9, 10)):
                        with self.subTest(direction=direction, strength=strength, tick=tick, local=local):
                            self.assertEqual(self.current_phase(world, local, direction=direction,
                                                               strength=strength, tick=tick), expected)

    def test_current_phase_stays_with_world_tiles_at_dateline_and_partial_edges(self):
        for world, local_positions in (
            ((0, 300), ((0, 10), (1, 10), (2, 10))),
            ((2499, 300), ((0, 10), (1, 10), (9, 10))),
            ((172, 267), ((49, 33), (50, 34), (48, 32))),
        ):
            for direction in range(16):
                expected = self.expected_current_tile(world, direction, 3, 5)
                for local in local_positions:
                    self.assertEqual(self.current_phase(world, local, direction=direction,
                                                       strength=3, tick=5), expected)

    def test_inactive_current_phase_preserves_native_viewport_coordinates(self):
        for direction in range(16):
            for local in ((8, 11), (7, 12), (49, 33)):
                expected = self.expected_current_tile(local, direction, 7, 23)
                self.assertEqual(self.current_phase((108, 211), local, direction=direction,
                                                   strength=7, tick=23, active=0), expected)

    def test_complete_native_world_renderer_covers_nonaligned_viewport_without_cache_overrun(self):
        self._exercise_complete_renderer(ddraw=True)

    def test_complete_native_software_renderer_covers_nonaligned_viewport_without_cache_overrun(self):
        self._exercise_complete_renderer(ddraw=False)

    def test_actual_camera_layout_and_native_renderer_compute_and_draw_centered_viewport(self):
        self._exercise_complete_renderer(ddraw=True, real_camera=True)

    def test_batch_pixels_match_native_atlas_with_one_lock_unlock_and_no_tile_blits(self):
        self._exercise_complete_renderer(ddraw=True, batch="ok")

    def test_stationary_complete_batch_frame_reuses_every_native_cached_tile(self):
        self._exercise_complete_renderer(ddraw=True, batch="ok", stationary_repeat=True)

    def test_batch_lock_failure_falls_back_to_original_tile_blits(self):
        self._exercise_complete_renderer(ddraw=True, batch="lock_failed")

    def test_batch_unsupported_pixel_format_unlocks_before_original_tile_blits(self):
        self._exercise_complete_renderer(ddraw=True, batch="wrong_format")

    def test_batch_surface_lost_restores_once_and_retries_lock(self):
        self._exercise_complete_renderer(ddraw=True, batch="surface_lost")

    def test_batch_invalid_pitch_falls_back_without_pixel_memory_writes(self):
        self._exercise_complete_renderer(ddraw=True, batch="bad_pitch")

    def test_batch_small_surface_falls_back_without_pixel_memory_writes(self):
        self._exercise_complete_renderer(ddraw=True, batch="small_surface")

    def test_batch_unlock_failure_restores_and_skips_actor_blits_until_next_frame(self):
        self._exercise_complete_renderer(ddraw=True, batch="unlock_failed")

    def test_batch_unsupported_format_and_unlock_failure_skip_all_map_blits(self):
        self._exercise_complete_renderer(ddraw=True, batch="format_unlock_failed")

    def test_actual_smooth_camera_and_native_batch_render_midpoint_then_restore_physics(self):
        self._exercise_complete_renderer(ddraw=True, real_camera=True, batch="ok", smooth=True)

    def test_complete_native_rain_capture_and_extra_frame_preserve_particles_rng_and_counter(self):
        self._exercise_complete_renderer(ddraw=True, real_camera=True, batch="ok",
                                         smooth=True, weather=True)

    def test_complete_native_water_tile_branch_uses_world_phase_and_batch_pixels(self):
        self._exercise_complete_renderer(ddraw=True, real_camera=True, batch="ok", currents=True)

    def _exercise_complete_renderer(self, *, ddraw, real_camera=False, batch=None,
                                    smooth=False, stationary_repeat=False, currents=False,
                                    weather=False):
        # Execute the real tile traversal, coordinate arithmetic and DD retry
        # code.  Only resource lookup, external drawing, and absent actors are
        # stubbed; protected red zones catch both cache reads and writes.
        width, height = 799, 533
        columns, rows = width // 16, height // 16
        cells = columns * rows
        pitch, surface_height = 832, height + 40
        if batch:
            self.uc.mem_map(ATLAS, 0x400000)
            self.uc.mem_write(ATLAS + 10 * 256, bytes((x * 3 + y * 5 + 7) & 255 for y in range(16) for x in range(16)))
            if currents:
                for tile, base_color in ((128, 7), (1408, 101)):
                    self.uc.mem_write(ATLAS + tile * 256,
                                      bytes((x * 3 + y * 5 + base_color) & 255
                                            for y in range(16) for x in range(16)))
            self.uc.mem_write(PIXELS, bytes([0xE7]) * (pitch * surface_height))
            self.put(VIEW + 0xB4, 0x400000)
            self.put(VTABLE + 0x64, LOCK)
            self.put(VTABLE + 0x80, UNLOCK)
            self.put(VTABLE + 0x6C, RESTORE)
            self.uc.mem_write(METHOD, bytes.fromhex("33 C0 C2 18 00"))
        if real_camera:
            from world_map_follow_patch import _layout

            # Install the production payload and its deterministic data layout,
            # including build_camera's real ensure_follow rather than a RET.
            payload, hooks, self.state = _layout(SLOT)
            self.uc.mem_write(SLOT, payload)
            for va, _original, replacement in hooks:
                self.uc.mem_write(va, replacement)
            self.set_state(width=1, height=1, rx=0, ry=0, valid=0, active=0)
            self.put(0x61B32C, width, height)
            midpoint = (123 * 16 + 15 + width // 2, 234 * 16 + 15 + height // 2)
            physical = tuple(value + (50 if smooth else 0) for value in midpoint)
            self.put(0x5B63B0, *physical)
            for npc in range(16):
                self.put(0x5B65D8 + npc * 12, -1, 0, 0)
            if smooth:
                self.set_state(smooth_running=1, smooth_active=1, smooth_view=VIEW,
                               smooth_width=width, smooth_height=height, smooth_start=1000,
                               smooth_target_x=physical[0], smooth_target_y=physical[1],
                               smooth_prev_x=physical[0] - 100, smooth_prev_y=physical[1] - 100,
                               smooth_delta_x=100, smooth_delta_y=100,
                               smooth_prev_fp_x=(physical[0] - 100) << 8,
                               smooth_prev_fp_y=(physical[1] - 100) << 8,
                               smooth_delta_fp_x=100 << 8, smooth_delta_fp_y=100 << 8,
                               smooth_duration=100, smooth_deadline=1100,
                               smooth_render_only=0 if weather else 1)
                self.put(0x62F448, TICK)
                self.uc.mem_write(TICK, b"\xB8" + struct.pack("<I", 1035 if weather else 1050) + b"\xC3")
                self.put(0x5B3A00, 0)
                self.put(0x5B6840, -2, -2)
                draw_hook = next(replacement for va, _, replacement in hooks if va == 0x48EC3B)
                draw_target = 0x48EC40 + struct.unpack_from("<i", draw_hook, 1)[0]
        else:
            self.set_state(width=width, height=height, rx=15, ry=15, active=0)
        self.put(VIEW + 0x54, 0, 32, width, height)
        self.put(VIEW + 0xEC, columns, rows)
        self.put(VIEW + 0xA0, 4 if smooth else 0)
        self.put(VIEW + 0x108, -1, 0)
        if weather:
            # Exercise the real world rain call, manager dispatch, all 75
            # particles, and the renderer's unconditional final effect call.
            # The latter uses effect=-1 but still advances shared 61D780.
            self.put(VIEW + 0x10C, 7)
            self.put(VIEW + 0xD0, CACHE)
            self.put(0x5AA2D8, width, height)
            self.put(0x5B6840, 20, -2)
            self.put(0x61D780, 19)
            self.put(0x5801A8, 0x13579BDF)
            self.put(0x56E2A0, 0x746200)
            self.put(0x61E274, 0x746200)
            self.put(0x61E270, 0)
            for particle in range(75):
                self.put(0x61E018 + particle * 4, 100 + particle % 10 * 32)
                self.put(0x61E018 + 0x12C + particle * 4,
                         height + 1 if particle == 0 else 10 + particle // 10 * 32)
        self.put(0x5B63A8, 100, 100)
        self.put(0x5B61B4, 0)
        if currents:
            self.put(0x569554, 5)
        self.uc.mem_write(0x5A4D18, b"\x18")
        self.uc.mem_write(0x5A4D1A, b"\x02")
        self.uc.mem_write(0x5A4D1D, b"\x01" if ddraw else b"\x00")
        self.put(STACK, STOP, 0)
        self.uc.reg_write(UC_X86_REG_ECX, VIEW)
        # This test isolates the renderer; city-label coexistence is checked
        # separately by the complete patch integration suite.
        self.uc.mem_write(0x48AB38, bytes.fromhex("8B 4C 24 14 F6 81 A0 00 00 00 01"))
        self.uc.mem_write(CACHE, b"\xFF" * (cells * 2))
        self.uc.mem_write(MASK, bytes(cells))
        self.put(SOFTWARE_CLIP[2], width)
        self.put(SOFTWARE_CLIP[3], height + 32)
        tiles = []
        forbidden = []
        camera_external_calls = []
        driver_calls = []
        locked = False
        actor_queries = []
        rendering_positions = []
        native_tile_draws = []
        native_frame_draw_counts = []
        weather_blits = []
        weather_world_calls = []
        weather_manager_calls = []
        weather_rng_calls = []
        calls = {
            0x4B9663: (0, 0), 0x424F70: (0, 4),
            0x426D70: (10, 8), 0x426710: (0, 8),
            0x4B5BF2: (0, 20), 0x47E470: (0x746100, 0),
            0x47E5A0: (0, 0), 0x473CD0: (0xFFFFFFFF, 0),
            0x4893A0: (0, 16), 0x488EB0: (0, 0),
            0x488EE0: (0, 0), 0x49ABB0: (0, 0),
        }
        if real_camera:
            # These external visibility/animation routines are independent of
            # camera arithmetic; native recenter, commit and snapshot run.
            calls.update({0x489360: (0, 8), 0x426790: (0, 0)})
        if currents:
            calls.update({0x426D70: (128, 8), 0x426710: (1, 8)})
        if weather:
            for native_weather in (0x488EB0, 0x488EE0, 0x49ABB0):
                del calls[native_weather]

        def return_from(uc, value, arg_bytes):
            sp = uc.reg_read(UC_X86_REG_ESP)
            target = self.get(sp)
            uc.reg_write(UC_X86_REG_EAX, value)
            uc.reg_write(UC_X86_REG_ESP, sp + 4 + arg_bytes)
            uc.reg_write(UC_X86_REG_EIP, target)

        def resources(uc, address, size, unused):
            nonlocal locked
            if smooth and address == 0x48A2A9:
                rendering_positions.append(self.get(0x5B63B0, 2))
            elif weather and address == 0x48AA6E:
                weather_world_calls.append(address)
            elif weather and address == 0x49ABB0:
                weather_manager_calls.append(self.get(uc.reg_read(UC_X86_REG_ESP) + 4))
            elif weather and address == 0x4B7C0F:
                weather_rng_calls.append(address)
            elif address == 0x48A5F1:
                native_tile_draws.append(address)
            elif batch and address == LOCK:
                args = self.get(uc.reg_read(UC_X86_REG_ESP) + 4, 5)
                self.assertEqual(args[:2], (SURFACE, 0))
                self.assertEqual(args[3:], (0, 0))
                self.assertEqual(self.get(args[2]), 0x6C)
                driver_calls.append("lock")
                if batch == "lock_failed":
                    return_from(uc, 0x80004005, 20)
                elif batch == "surface_lost" and driver_calls.count("lock") == 1:
                    return_from(uc, 0x887601C2, 20)
                else:
                    descriptor = args[2]
                    self.put(descriptor + 8, height + 31 if batch == "small_surface" else surface_height,
                             width, width - 1 if batch == "bad_pitch" else pitch)
                    self.put(descriptor + 0x24, PIXELS)
                    self.put(descriptor + 0x48, 32, 0x20, 0,
                             32 if batch in ("wrong_format", "format_unlock_failed") else 8)
                    locked = True
                    return_from(uc, 0, 20)
            elif batch and address == UNLOCK:
                args = self.get(uc.reg_read(UC_X86_REG_ESP) + 4, 2)
                self.assertEqual(args, (SURFACE, PIXELS))
                self.assertTrue(locked)
                driver_calls.append("unlock")
                locked = False
                return_from(uc, 0x80004005 if batch in ("unlock_failed", "format_unlock_failed") else 0, 8)
            elif batch and address == RESTORE:
                self.assertEqual(self.get(uc.reg_read(UC_X86_REG_ESP) + 4), SURFACE)
                driver_calls.append("restore")
                locked = False
                return_from(uc, 0, 4)
            elif address in calls:
                if currents and address == 0x424F70:
                    self.put(self.get(uc.reg_read(UC_X86_REG_ESP) + 4), 12, 3)
                if real_camera and address in (0x489360, 0x426790):
                    camera_external_calls.append(address)
                if address == 0x47E470:
                    actor_queries.append(address)
                    self.assertFalse(locked, "Map must be unlocked before actors")
                return_from(uc, *calls[address])
            elif address == 0x4B6637:
                owner = uc.reg_read(UC_X86_REG_ECX)
                offset = self.get(uc.reg_read(UC_X86_REG_ESP) + 4)
                if owner == VIEW + 0xC4:
                    pointer = CACHE + min(offset, cells * 2)
                elif owner == VIEW + 0xD8:
                    pointer = MASK + min(offset, cells)
                elif owner == VIEW + 0xB0:
                    pointer = ATLAS if batch else 0x748000
                else:
                    self.fail(f"Unexpected buffer lookup {owner:x}")
                return_from(uc, pointer, 12)
            elif address == METHOD:
                self.assertFalse(locked, "Fallback Blt must not use a locked map")
                args = self.get(uc.reg_read(UC_X86_REG_ESP) + 4, 6)
                if weather and args[2] == 0x746200:
                    weather_blits.append((self.get(args[1], 4), self.get(args[3], 4)))
                else:
                    tiles.append(self.get(args[1], 4))
            elif address == 0x4B5D09:
                rectangle = self.get(uc.reg_read(UC_X86_REG_ESP) + 4)
                x, y, w, h = self.get(rectangle, 4)
                # Native software primitive consumes x/y/width/height and
                # applies the graphics origin plus absolute clipping bounds.
                origin_x, origin_y = struct.unpack("<ii", uc.mem_read(0x62B870, 8))
                left, top = x + origin_x, y + origin_y
                right, bottom = left + w, top + h
                clip = tuple(self.get(a) for a in SOFTWARE_CLIP)
                tile = (max(left, clip[0]), max(top, clip[1]), min(right, clip[2]), min(bottom, clip[3]))
                if tile[0] < tile[2] and tile[1] < tile[3]:
                    tiles.append(tile)
                return_from(uc, 0, 8)

        def guard(uc, access, address, size, value, unused):
            for base, length in ((CACHE, cells * 2), (MASK, cells)):
                if base <= address < base + 0x1000 and address + size > base + length:
                    forbidden.append((address, size))
                    uc.emu_stop()

        resource_hook = self.uc.hook_add(UC_HOOK_CODE, resources)
        guard_hook = self.uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, guard)
        try:
            self.run_to(draw_target if smooth else 0x48A1E0, STOP)
            native_frame_draw_counts.append(len(native_tile_draws))
            if weather:
                self.assertEqual(weather_world_calls, [0x48AA6E])
                self.assertEqual(weather_manager_calls, [0, 0xFFFFFFFF])
                self.assertEqual(self.get(self.state["weather_meta_0_1"]), 75)
                self.assertEqual((self.get(0x61D780), self.get(VIEW + 0x10C)), (21, 8),
                                 "A normal frame must retain both native counter updates")
                self.assertGreater(len(weather_rng_calls), 0, "Normal particle respawn must use native RNG")
                captured = list(weather_blits)
                self.assertGreater(len(captured), 0)
                for destination, _source in captured:
                    left, top, right, bottom = destination
                    self.assertTrue(0 <= left < right <= width)
                    self.assertTrue(32 <= top < bottom <= height + 32)
                before_extra = (bytes(self.uc.mem_read(0x61E018, 0x260)),
                                self.get(0x5801A8), self.get(0x61D780),
                                self.get(VIEW + 0x10C), self.get(0x5B63B0, 2),
                                len(weather_rng_calls))
                # Readiness uses the metadata captured by actual 48AA6E,
                # not a synthetic direct call into the weather wrapper.
                self.put(STACK, STOP)
                self.uc.reg_write(UC_X86_REG_ESP, STACK)
                self.uc.reg_write(UC_X86_REG_ECX, VIEW)
                self.run_to(self.state["weather_ready"], STOP)
                self.assertEqual(self.uc.reg_read(UC_X86_REG_EAX), 1)
                self.set_state(smooth_render_only=1)
                self.uc.ctl_remove_cache(TICK, TICK + 16)
                self.uc.mem_write(TICK, bytes.fromhex("B8 1A 04 00 00 C3"))  # tick=1050
                self.put(STACK, STOP, 0)
                self.uc.reg_write(UC_X86_REG_ESP, STACK)
                self.uc.reg_write(UC_X86_REG_ECX, VIEW)
                self.run_to(draw_target, STOP)
                after_extra = (bytes(self.uc.mem_read(0x61E018, 0x260)),
                               self.get(0x5801A8), self.get(0x61D780),
                               self.get(VIEW + 0x10C), self.get(0x5B63B0, 2),
                               len(weather_rng_calls))
                self.assertEqual(after_extra, before_extra,
                                 "An extra world frame must preserve weather, shared counter, RNG and physics")
                self.assertEqual(weather_blits, captured * 2,
                                 "Screen-space rain must not follow the changing camera residue")
                self.assertEqual(weather_world_calls, [0x48AA6E] * 2)
                self.assertEqual(weather_manager_calls, [0, 0xFFFFFFFF, 0xFFFFFFFF],
                                 "Extra frames must replay rain, not run its mutable manager")
            if stationary_repeat:
                self.uc.reg_write(UC_X86_REG_ESP, STACK)
                self.uc.reg_write(UC_X86_REG_ECX, VIEW)
                self.put(STACK, STOP, 0)
                self.run_to(0x48A1E0, STOP)
                native_frame_draw_counts.append(len(native_tile_draws) - native_frame_draw_counts[0])
        finally:
            self.uc.hook_del(resource_hook)
            self.uc.hook_del(guard_hook)
        self.assertEqual(forbidden, [])
        copied = batch in ("ok", "surface_lost", "unlock_failed")
        skipped = batch == "format_unlock_failed"
        self.assertEqual(len(tiles), 0 if copied or skipped else ((width + 30) // 16) * ((height + 30) // 16))
        covered = bytearray(width * height)
        for left, top, right, bottom in tiles:
            self.assertLessEqual(0, left)
            self.assertLessEqual(32, top)
            self.assertLessEqual(right, width)
            self.assertLessEqual(bottom, height + 32)
            for row in range(top - 32, bottom - 32):
                first, last = row * width + left, row * width + right
                self.assertEqual(covered[first:last], bytes(last - first), "Tiles overlap")
                covered[first:last] = bytes([1]) * (last - first)
        if not copied and not skipped:
            self.assertEqual(covered, bytes([1]) * (width * height))
        if batch:
            self.assertFalse(locked)
            expected_calls = {
                "ok": ["lock", "unlock"],
                "lock_failed": ["lock"],
                "surface_lost": ["lock", "restore", "lock", "unlock"],
                "unlock_failed": ["lock", "unlock", "restore"],
                "format_unlock_failed": ["lock", "unlock", "restore"],
            }.get(batch, ["lock", "unlock"])
            if stationary_repeat or weather:
                expected_calls *= 2
            self.assertEqual(driver_calls, expected_calls)
            self.assertEqual(len(actor_queries), 0 if batch in ("unlock_failed", "format_unlock_failed")
                             else 32 if stationary_repeat or weather else 16)
            if stationary_repeat:
                total = ((width + 30) // 16) * ((height + 30) // 16)
                self.assertEqual(native_frame_draw_counts, [total, total - cells])
                self.assertEqual(self.get(self.state["render_force_redraw"]), 0)
            buffer = bytes(self.uc.mem_read(PIXELS, pitch * surface_height))
            if copied:
                expected_pixels = bytearray([0xE7]) * (pitch * surface_height)
                for row in range(height):
                    first = (row + 32) * pitch
                    if currents:
                        expected_pixels[first:first + width] = bytes(
                            (((col + 15) & 15) * 3 + ((row + 15) & 15) * 5
                             + (101 if self.expected_current_tile(
                                 (123 + (col + 15) // 16, 234 + (row + 15) // 16), 12, 3, 5) == 1408 else 7)) & 255
                            for col in range(width))
                    else:
                        expected_pixels[first:first + width] = bytes((((col + 15) & 15) * 3 + ((row + 15) & 15) * 5 + 7) & 255 for col in range(width))
                self.assertEqual(buffer, bytes(expected_pixels))
            else:
                self.assertEqual(buffer, bytes([0xE7]) * len(buffer))
            if batch in ("lock_failed", "unlock_failed", "format_unlock_failed"):
                self.assertEqual(self.get(self.state["render_previous_valid"]), 0)
        self.assertEqual(self.get(self.state["active"]), 0)
        self.assertEqual(self.get(VIEW + 0x54, 2), (0, 32))
        self.assertEqual(self.uc.reg_read(UC_X86_REG_ESP), STACK + 8)
        if real_camera:
            self.assertEqual(camera_external_calls, [0x489360, 0x426790])
            self.assertEqual(self.get(self.state["valid"]), 1)
            self.assertEqual(self.get(self.state["view"]), VIEW)
            self.assertEqual((self.get(self.state["width"]), self.get(self.state["height"])), (width, height))
            self.assertEqual((self.get(self.state["rx"]), self.get(self.state["ry"])), (15, 15))
            self.assertEqual(self.get(0x5B63A8, 2), (123, 234))
            self.assertEqual((self.get(self.state["legacy_x"]), self.get(self.state["legacy_y"])), (100, 100))
            self.assertEqual(midpoint[0] - self.get(0x5B63A8) * 16 - self.get(self.state["rx"]), width // 2)
            self.assertEqual(midpoint[1] - self.get(0x5B63AC) * 16 - self.get(self.state["ry"]), height // 2)
            self.assertEqual(tuple(self.get(x) for x in SOFTWARE_ORIGIN), (0, 32) * 2)
            self.assertEqual(self.get(0x5B63B0, 2), physical)
            if smooth:
                self.assertEqual(rendering_positions,
                                 [tuple(value - 15 for value in midpoint), midpoint] if weather else [midpoint])
                self.assertEqual(self.get(self.state["smooth_rendering"]), 0)
                self.assertEqual(self.get(self.state["camera_moving"]), 0)
                self.assertEqual(self.get(self.state["camera_last_player_x"]), physical[0])
                self.assertEqual(self.get(self.state["camera_last_player_y"]), physical[1])


if __name__ == "__main__":
    unittest.main()
