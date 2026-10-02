"""Execute native rain/snow and the camera's captured-command replay as x86."""

import struct
import unittest

import test_world_map_follow_render as renderer
from test_world_map_follow_render import (
    Uc, UC_HOOK_CODE, UC_HOOK_MEM_WRITE,
    UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_ESP, UC_X86_REG_EIP,
    UC_X86_REG_EFLAGS, SLOT, VIEW, STACK, STOP, SURFACE, METHOD, CACHE,
)
from world_map_follow_weather import build_weather, CHANNELS

SOURCE = 0x746000


@unittest.skipIf(Uc is None, "Install unicorn to execute weather hooks")
class WeatherExecutionTests(unittest.TestCase):
    setUpClass = classmethod(renderer.RendererExecutionTests.setUpClass.__func__)
    tearDownClass = classmethod(renderer.RendererExecutionTests.tearDownClass.__func__)
    put = renderer.RendererExecutionTests.put
    get = renderer.RendererExecutionTests.get
    set_state = renderer.RendererExecutionTests.set_state
    run_to = renderer.RendererExecutionTests.run_to
    assert_registers = renderer.RendererExecutionTests.assert_registers

    def setUp(self):
        renderer.RendererExecutionTests.setUp(self)
        chunks, hooks = build_weather(SLOT, self.state)
        self.weather_hooks = hooks
        for offset, blob in chunks.items():
            self.uc.mem_write(SLOT + offset, blob)
        for va, original, replacement in hooks:
            self.assertEqual(bytes(self.uc.mem_read(va, len(original))), original)
            self.uc.mem_write(va, replacement)
        self.uc.mem_write(METHOD, bytes.fromhex("33 C0 C2 18 00"))
        self.put(0x61B32C, 640, 320)
        self.put(0x5AA2D8, 640, 320)
        self.put(VIEW + 0xD0, CACHE)
        self.put(0x5B6840, -2, -2)
        self.set_state(active=1, smooth_render_only=0)
        self.calls = []

        def capture(uc, address, size, unused):
            if address == METHOD:
                sp = uc.reg_read(UC_X86_REG_ESP)
                self.calls.append((self.get(self.get(sp + 8), 4),
                                   self.get(self.get(sp + 16), 4)))

        self.uc.hook_add(UC_HOOK_CODE, capture)

    def prepare(self, channel, *, frame=20, edge=False):
        phase, alias, surface, capacity, size = CHANNELS[channel]
        self.put(phase, frame)
        self.put(alias, SOURCE)
        self.put(surface, SOURCE)
        self.put(0x61D780, 19)
        self.put(0x5801A8, 0x13579BDF)
        for i in range(capacity):
            x = 620 + i % 30 if edge else 80 + i % 20
            y = 305 + i % 30 if edge else 60 + i % 15
            if channel == 0:
                self.put(0x61E018 + i * 4, x)
                self.put(0x61E018 + 0x12C + i * 4, y)
            else:
                self.put(0x61DBF8 + i * 4, i)
                self.put(0x61DBF8 + 0xCC + i * 4, x)
                self.put(0x61DBF8 + 0x194 + i * 4, y)
        self.put((0x61E018 + 0x258, 0x61DBF8 + 0x25C)[channel], 0)

    def weather(self, channel, *, raw=False):
        phase = self.get(CHANNELS[channel][0])
        self.put(STACK, STOP, channel, phase, SURFACE, CACHE,
                 self.get(VIEW + 0xEC), self.get(VIEW + 0xF0))
        self.uc.reg_write(UC_X86_REG_ESP, STACK)
        if raw:
            entry = 0x49ABB0
        else:
            call = (0x48AA6E, 0x48AAC2)[channel]
            blob = bytes(self.uc.mem_read(call, 5))
            entry = call + 5 + struct.unpack_from("<i", blob, 1)[0]
        self.run_to(entry, STOP)
        self.assertEqual(self.uc.reg_read(UC_X86_REG_ESP), STACK + 4)
        return self.uc.reg_read(UC_X86_REG_EAX)

    def ready(self):
        self.put(STACK, STOP)
        self.uc.reg_write(UC_X86_REG_ESP, STACK)
        self.uc.reg_write(UC_X86_REG_ECX, VIEW)
        self.run_to(self.state["weather_ready"], STOP)
        return self.uc.reg_read(UC_X86_REG_EAX)

    def snapshot(self, channel):
        return (bytes(self.uc.mem_read((0x61E018, 0x61DBF8)[channel], 0x260)),
                self.get(0x5801A8), self.get(0x61D780),
                self.get(CHANNELS[channel][1]))

    def test_normal_native_parity_and_replay_preserve_weather_rng_and_counter(self):
        for channel in range(2):
            with self.subTest(channel=channel):
                self.setUp()
                self.prepare(channel, edge=True)
                initial = self.snapshot(channel)
                self.set_state(active=0)
                native_result = self.weather(channel, raw=True)
                native_after = self.snapshot(channel)
                # The camera clip drops native empty/inverted edge blits.
                native_calls = [call for call in self.calls
                                if call[0][0] < call[0][2] and call[0][1] < call[0][3]]
                self.uc.mem_write((0x61E018, 0x61DBF8)[channel], initial[0])
                self.put(0x5801A8, initial[1]); self.put(0x61D780, initial[2])
                self.calls.clear()
                self.set_state(active=1)
                self.assertEqual(self.weather(channel), native_result)
                self.assertEqual(self.snapshot(channel), native_after)
                self.assertEqual(self.calls, native_calls)
                self.assertEqual(self.get(self.state[f"weather_meta_{channel}_1"]), (75, 50)[channel])
                self.assertEqual(self.ready(), 1)
                captured = list(self.calls)
                self.set_state(smooth_render_only=1)
                for rx, ry in ((0, 0), (4, 9), (15, 15), (1, 0), (7, 11)):
                    self.calls.clear()
                    self.set_state(rx=rx, ry=ry)
                    self.assertEqual(self.weather(channel), 1)
                    self.assertEqual(self.calls, captured)
                    self.assertEqual(self.snapshot(channel), native_after)
                self.assertEqual(self.get(self.state["weather_busy"]), 0)
                self.assertEqual(self.get(self.state["weather_screen"]), 0)

    def test_ready_preserves_registers_flags_and_rejects_stale_lifecycle(self):
        self.prepare(0)
        self.weather(0)
        expected = dict(self.registers)
        expected[UC_X86_REG_ECX] = VIEW
        expected[UC_X86_REG_EAX] = 1
        for reg, value in self.registers.items():
            self.uc.reg_write(reg, value)
        self.uc.reg_write(UC_X86_REG_EFLAGS, 0x247)
        self.assertEqual(self.ready(), 1)
        self.assert_registers(expected)
        self.assertEqual(self.uc.reg_read(UC_X86_REG_EFLAGS), 0x247)
        for address in (0x5B6840, 0x56E2A0, 0x61E274, 0x61B32C, 0x61B330,
                        VIEW + 0xD0, VIEW + 0xEC, VIEW + 0xF0):
            old = self.get(address)
            self.put(address, old + 1)
            self.assertEqual(self.ready(), 0, hex(address))
            self.put(address, old)
        self.put(0x5B6844, 20)
        self.assertEqual(self.ready(), 0, "The other active channel has no cache")

    def test_missing_cache_or_reentry_never_runs_effect_manager_on_extra_frame(self):
        self.prepare(0)
        initial = self.snapshot(0)
        self.set_state(smooth_render_only=1)
        for busy in (0, 1):
            self.set_state(weather_busy=busy)
            self.assertEqual(self.weather(0), 1)
            self.assertEqual(self.snapshot(0), initial)
            self.assertEqual(self.calls, [])
            self.assertEqual(self.get(self.state["weather_busy"]), busy)

    def test_invalid_render_scope_never_advances_weather_on_extra_frame(self):
        self.prepare(0)
        self.weather(0)
        initial = self.snapshot(0)
        self.set_state(smooth_render_only=1)
        self.calls.clear()
        for name in ("active", "valid"):
            self.set_state(**{name: 0})
            self.assertEqual(self.weather(0), 1)
            self.assertEqual(self.snapshot(0), initial)
            self.assertEqual(self.calls, [])
            self.set_state(**{name: 1})
        for address in (0x56E2A0, 0x61E274, 0x5B6840):
            old = self.get(address)
            self.put(address, old + 1)
            stale = self.snapshot(0)
            self.assertEqual(self.weather(0), 1)
            self.assertEqual(self.snapshot(0), stale)
            self.assertEqual(self.calls, [])
            self.put(address, old)

    def test_native_resource_initialization_and_rng_run_only_on_normal_draw(self):
        loads = []

        def loader(uc, address, size, unused):
            if address == 0x49A210:
                sp = uc.reg_read(UC_X86_REG_ESP)
                loads.append(self.get(sp + 4))
                self.put(self.get(sp + 4), SOURCE)
                uc.reg_write(UC_X86_REG_EAX, 1)
                uc.reg_write(UC_X86_REG_EIP, self.get(sp))
                uc.reg_write(UC_X86_REG_ESP, sp + 24)

        self.uc.hook_add(UC_HOOK_CODE, loader)
        for channel in range(2):
            with self.subTest(channel=channel):
                self.prepare(channel)
                self.put(CHANNELS[channel][1], 0)
                self.put(CHANNELS[channel][2], 0)
                initial = self.snapshot(channel)
                self.set_state(active=0, smooth_render_only=0)
                self.weather(channel, raw=True)
                native_after = self.snapshot(channel)
                self.uc.mem_write((0x61E018, 0x61DBF8)[channel], initial[0])
                self.put(0x5801A8, initial[1]); self.put(0x61D780, initial[2])
                self.put(CHANNELS[channel][1], 0)
                self.set_state(active=1)
                self.weather(channel)
                self.assertEqual(self.snapshot(channel), native_after)
                self.assertEqual(loads.count(CHANNELS[channel][2]), 2)
                self.set_state(smooth_render_only=1)
                for _ in range(4):
                    self.weather(channel)
                    self.assertEqual(self.snapshot(channel), native_after)
                    self.assertEqual(loads.count(CHANNELS[channel][2]), 2)

    def test_screen_space_dirty_cache_adds_residue_without_moving_particles(self):
        self.prepare(0)
        for i in range(75):
            self.put(0x61E018 + i * 4, 15)
            self.put(0x61E144 + i * 4, 15)
        self.set_state(rx=1, ry=1)
        self.uc.mem_write(CACHE, bytes(40 * 20 * 2))
        self.weather(0)
        changed = {i for i in range(800) if self.get_word(CACHE + 2 * i) == 0xFFFF}
        self.assertEqual(changed, {y * 40 + x for y in (1, 2, 3) for x in (1, 2, 3)})
        self.assertEqual(self.calls[0][0], (15, 47, 47, 79))
        captured = list(self.calls)
        self.calls.clear()
        self.uc.mem_write(CACHE, bytes(40 * 20 * 2))
        # Camera crossed the dateline; particles remain fixed screen overlays.
        self.put(0x5B63A8, 2499, 10)
        self.set_state(rx=0, ry=0, smooth_render_only=1)
        self.weather(0)
        self.assertEqual(self.calls, captured)
        changed = {i for i in range(800) if self.get_word(CACHE + 2 * i) == 0xFFFF}
        self.assertEqual(changed, {y * 40 + x for y in (0, 1, 2) for x in (0, 1, 2)})

    def get_word(self, address):
        return struct.unpack("<H", self.uc.mem_read(address, 2))[0]

    def test_normal_inactive_camera_is_native_passthrough(self):
        for channel in range(2):
            self.prepare(channel)
            before = self.snapshot(channel)
            self.set_state(active=0, smooth_render_only=0)
            self.weather(channel, raw=True)
            expected = self.snapshot(channel)
            self.uc.mem_write((0x61E018, 0x61DBF8)[channel], before[0])
            self.put(0x5801A8, before[1]); self.put(0x61D780, before[2])
            self.weather(channel)
            self.assertEqual(self.snapshot(channel), expected)
            self.assertEqual(self.get(self.state[f"weather_meta_{channel}_0"]), 0)
        self.assertEqual(bytes(self.uc.mem_read(0x48AB1E, 5)),
                         self.native[0x8AB1E:0x8AB23], "Other effect manager call remains untouched")

    def test_fading_normal_frame_keeps_native_cleanup_and_invalidates_cache(self):
        release = METHOD + 0x80
        self.uc.mem_write(release, bytes.fromhex("33 C0 C2 04 00"))
        self.put(SOURCE, SOURCE + 0x100)
        self.put(SOURCE + 0x108, release)
        for channel in range(2):
            self.prepare(channel, frame=-1)
            self.put((0x61E270, 0x61DE54)[channel], (17, 14)[channel])
            self.assertEqual(self.weather(channel), 0)
            self.assertEqual(self.get(CHANNELS[channel][1]), 0)
            self.assertEqual(self.get(CHANNELS[channel][2]), 0)
            self.assertEqual(self.get(self.state[f"weather_meta_{channel}_0"]), 0)

    def test_nonmultiple_viewport_and_dirty_cache_bounds(self):
        width, height = 639, 319
        cols, rows = width // 16, height // 16
        self.put(0x61B32C, width, height)
        self.put(0x5AA2D8, width, height)
        self.put(VIEW + 0xEC, cols, rows)
        self.set_state(width=width, height=height, rx=15, ry=15)
        size = cols * rows * 2
        self.uc.mem_write(CACHE - 64, b"\xA5" * (size + 128))
        self.uc.mem_write(CACHE, bytes(size))
        writes = []

        def bounds(uc, access, address, length, value, unused):
            if CACHE - 64 <= address < CACHE + size + 64:
                writes.append((address, length))
                self.assertGreaterEqual(address, CACHE)
                self.assertLessEqual(address + length, CACHE + size)

        self.uc.hook_add(UC_HOOK_MEM_WRITE, bounds)
        self.prepare(0, edge=True)
        self.put(0x61E018, 10, -8, width - 1)
        self.put(0x61E144, 10, -8, height - 1)
        self.weather(0)
        self.set_state(smooth_render_only=1)
        self.weather(0)
        self.assertTrue(writes)
        self.assertEqual(bytes(self.uc.mem_read(CACHE - 64, 64)), b"\xA5" * 64)
        self.assertEqual(bytes(self.uc.mem_read(CACHE + size, 64)), b"\xA5" * 64)
        for dst, src in self.calls:
            self.assertTrue(0 <= dst[0] < dst[2] <= width)
            self.assertTrue(32 <= dst[1] < dst[3] <= height + 32)

    def test_invalidate_clears_both_channels_without_altering_registers(self):
        for channel in range(2):
            self.prepare(channel); self.weather(channel)
        for reg, value in self.registers.items():
            self.uc.reg_write(reg, value)
        self.uc.reg_write(UC_X86_REG_EFLAGS, 0x247)
        self.put(STACK, STOP); self.uc.reg_write(UC_X86_REG_ESP, STACK)
        self.run_to(self.state["weather_invalidate"], STOP)
        self.assert_registers()
        self.assertEqual(self.uc.reg_read(UC_X86_REG_EFLAGS), 0x247)
        for channel in range(2):
            self.assertEqual(self.get(self.state[f"weather_meta_{channel}_0"], 2), (0, 0))
        self.assertEqual(self.ready(), 0)


if __name__ == "__main__":
    unittest.main()
