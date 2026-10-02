"""Execute interpolation, event isolation, and batched native movement in x86."""

import struct
import unittest

from test_world_map_follow_camera import _Machine, Uc
from world_map_follow_smooth import build_smooth, FRAME_MS, MAX_SEGMENT_MS
from world_map_follow_render import build_render
from world_map_follow_weather import build_weather

if Uc is not None:
    from unicorn.x86_const import (
        UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_ESP,
        UC_X86_REG_EIP, UC_X86_REG_EBP,
    )


class _SmoothMachine(_Machine):
    def __init__(self, smooth_builder=build_smooth, **kwargs):
        self.now = 1000
        self.native_poll_result = 0
        self.wait_result = 2
        self.draws, self.poll_calls = [], []
        self.refreshes, self.updates = 0, 0
        super().__init__(**kwargs)
        self.uc.mem_map(0x900000, 0x10000)
        self.state = _RenderState(self.state)
        self.targets = {}
        for builder in (build_weather, smooth_builder):
            chunks, hooks = builder(0x800000, self.state)
            for offset, payload in chunks.items():
                self.uc.mem_write(0x800000 + offset, payload)
            for va, before, after in hooks:
                assert bytes(self.uc.mem_read(va, len(before))) == before
                self.uc.mem_write(va, after)
                self.targets[va] = va + 5 + struct.unpack_from("<i", after, 1)[0]
        self.put(0x62F448, 0x80F100)
        self.put(0x61B370, 4)  # view+A0: active sailing and no hidden-map bit
        self.put(0x61B3D8, -1, 12)  # view+108/+10C: no special effect, frame
        self.put(0x5B6840, -2, -2)  # only -2 is an idle native world effect
        self.put(0x5B3A00, 0)
        self.set("running", 1)
        self.set("view", 0x61B2D0)
        # Native draw surrogate runs the real camera helper and advances the
        # same per-draw animation counter as 48AAF1; no graphics driver needed.
        code = b"\x51\xE8" + struct.pack("<i", 0x800100 - 0x80F046)
        code += bytes.fromhex("59 FF 81 0C 01 00 00 B8 78 56 34 12 C2 04 00")
        self.uc.mem_write(0x80F040, code)
        self.ensure()

    def set(self, name, value):
        self.put(self.state["smooth_" + name], value)

    def smooth(self, name):
        return self.value("smooth_" + name)

    def intercept(self, uc, address, size, context):
        sp = uc.reg_read(UC_X86_REG_ESP)
        if address == 0x80F100:
            uc.reg_write(UC_X86_REG_EAX, self.now & 0xFFFFFFFF)
            cleanup = 0
        elif address == 0x48D0A0:
            self.updates += 1
            uc.reg_write(UC_X86_REG_EIP, 0x80F000)
            return
        elif address == 0x48A1E0:
            self.draws.append(self.player)
            uc.reg_write(UC_X86_REG_EIP, 0x80F040)
            return
        elif address == 0x4B8D3F:
            self.poll_calls.append((uc.reg_read(UC_X86_REG_ECX), self.get(sp + 4)))
            uc.reg_write(UC_X86_REG_EAX, self.native_poll_result)
            uc.reg_write(UC_X86_REG_ECX, 0x12345678)
            uc.reg_write(UC_X86_REG_EDX, 0x23456789)
            cleanup = 4
        elif address == 0x459CC0:
            assert self.smooth("waiting") == 1
            uc.reg_write(UC_X86_REG_EAX, self.wait_result)
            cleanup = 0
        else:
            if address == 0x426790:
                self.refreshes += 1
            super().intercept(uc, address, size, context)
            return
        uc.reg_write(UC_X86_REG_ESP, sp + 4 + cleanup)
        uc.reg_write(UC_X86_REG_EIP, self.get(sp))

    def update(self, dx, dy):
        # Thiscall(view), ret0 -> real native mover or high-speed wrapper.
        code = b"\x68" + struct.pack("<i", dy) + b"\x68" + struct.pack("<i", dx)
        code += bytes.fromhex("B9 A0 60 5B 00 E8")
        code += struct.pack("<i", self.move_address - (0x80F000 + len(code) + 4))
        code += b"\xC3"
        # Unicorn may keep a translated block after replacing the requested
        # movement constants.  Mixed-direction cases must execute the new stub.
        self.uc.ctl_remove_cache(0x80F000, 0x80F030)
        self.uc.mem_write(0x80F000, code)
        self.call(self.targets[0x48EF55])

    def draw(self, now, render_only=False):
        self.now = now
        self.set("render_only", int(render_only))
        self.call(self.targets[0x48EC3B], (0,))

    def poll(self, now, result=0, waiting=True, manager=0x62B368):
        self.now, self.native_poll_result = now, result
        self.set("waiting", int(waiting))
        self.put(0x61B368, 0, -1)  # view+98/+9C
        self.call(self.targets[0x4B8D27], (0x10016000,), this=manager)
        return self.uc.reg_read(UC_X86_REG_EAX), self.get(0x61B36C)


class _RenderState(dict):
    """Retain camera addresses and allocate contiguous renderer descriptor data."""
    def __init__(self, existing, base=0x900000):
        super().__init__(existing)
        self.cursor = base

    def __missing__(self, name):
        address = self.cursor
        self.cursor += 4
        self[name] = address
        return address


class _CloudMachine(_SmoothMachine):
    def __init__(self, **kwargs):
        self.cloud_blits = []
        super().__init__(**kwargs)
        self.uc.mem_map(0xA00000, 0x10000)
        self.state = _RenderState(self.state, 0xA00000)
        self.state["ensure_follow"] = 0x800100
        chunks, hooks = build_render(0x800000, self.state)
        for offset, payload in chunks.items():
            self.uc.mem_write(0x800000 + offset, payload)
        for va, before, after in hooks:
            assert bytes(self.uc.mem_read(va, len(before))) == before
            self.uc.mem_write(va, after)
        self.put(0x5AA2D8, 640, 320)
        self.put(0x80E100, 0x80E000)  # DirectDraw surface and its vtable
        self.put(0x80E014, 0x80F300)
        self.put(0x569FF0, 0x80E100)
        self.put(0x5B68C0, 0x80E100)  # native cloud sprite surface

    def intercept(self, uc, address, size, context):
        if address != 0x80F300:
            super().intercept(uc, address, size, context)
            return
        sp = uc.reg_read(UC_X86_REG_ESP)
        destination, source = self.get(sp + 8), self.get(sp + 16)
        self.cloud_blits.append((tuple(self.get(destination + i * 4) for i in range(4)),
                                 tuple(self.get(source + i * 4) for i in range(4))))
        uc.reg_write(UC_X86_REG_EAX, 0)
        uc.reg_write(UC_X86_REG_ESP, sp + 28)
        uc.reg_write(UC_X86_REG_EIP, self.get(sp))

    def draw_clouds(self):
        self.cloud_blits.clear()
        self.put(self.state["active"], 1)
        try:
            # Real native cloud clipping, COM argument construction, and dirty
            # mask iteration all execute.  Only the final graphics driver is fake.
            self.call(0x489110, (0x80E100, 0x10004000, 40, 20), this=0x5B6840)
        finally:
            self.put(self.state["active"], 0)

    def advance_clouds(self):
        self.call(0x4893D0, this=0x5B6840)


class _RainMachine(_CloudMachine):
    """Run native rain advancement/capture with a fake graphics driver only."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.put(0x80E200, 0x80E000)
        self.put(0x56E2A0, 0x80E200)  # initialized native rain sprite
        self.put(0x61E274, 0x80E200)
        self.put(0x61E270, 0)
        self.put(0x61B3A0, 0x10004000)  # view+D0: native dirty-mask storage
        for index in range(75):
            self.put(0x61E018 + index * 4, 100 + index % 10 * 32)
            self.put(0x61E144 + index * 4, 10 + index // 10 * 32)
        self.put(0x5B6840, 1)
        # Native world draw surrogate: real camera + real rain dispatch at its
        # patched world call site.  It deliberately does not stub 49ABB0/RNG.
        code = bytearray(b"\x51\xE8" + struct.pack("<i", 0x800100 - 0x80F046))
        code += bytes.fromhex("59 51 C7 05") + struct.pack("<II", self.state["active"], 1)
        for argument in (20, 40, 0x10004000, 0x80E100):
            code += b"\x68" + struct.pack("<I", argument)
        code += bytes.fromhex("FF 35 40 68 5B 00 6A 00 E8")
        code += struct.pack("<i", self.targets[0x48AA6E] - (0x80F040 + len(code) + 4))
        code += bytes.fromhex("83 C4 18 C7 05") + struct.pack("<II", self.state["active"], 0)
        code += bytes.fromhex("59 FF 81 0C 01 00 00 B8 78 56 34 12 C2 04 00")
        self.uc.ctl_remove_cache(0x80F040, 0x80F100)
        self.uc.mem_write(0x80F040, bytes(code))

    def rain_state(self):
        return bytes(self.uc.mem_read(0x61E018, 0x260))


@unittest.skipIf(Uc is None, "Install unicorn to execute native smooth-camera code")
class WorldMapSmoothTests(unittest.TestCase):
    @staticmethod
    def rounded_motion(delta, elapsed, duration=100):
        """Nearest absolute-pixel rounding after signed Q8 interpolation."""
        product = delta * 256 * min(elapsed, duration)
        quotient = product // duration if product >= 0 else -(-product // duration)
        return (quotient + 128) // 256

    def trace_cadence(self, speed, *, jitter=True, quantized_timer=False,
                      smooth_builder=build_smooth):
        """Real timer, native mover and smooth x86; only paint resources fake."""
        m = _SmoothMachine(high_speed=abs(speed) > 32, smooth_builder=smooth_builder)
        native = _SmoothMachine(follow=False, high_speed=abs(speed) > 32)
        for machine in (m, native):
            machine.put(0x5697D4, 1000)
        initial, samples, ticks = m.player[0], [], []

        def advance(callback, completed):
            for machine in (native, m):
                machine.now = callback
                machine.put(0x61B368, 0, -1)
                machine.call(0x48B120, (0x10016000,))
            self.assertEqual(m.get(0x61B36C), native.get(0x61B36C))
            if m.get(0x61B36C) != 2:
                return False
            native.move(speed, 0)
            m.now = completed
            m.update(speed, 0)
            m.draw(completed)
            ticks.append(completed)
            samples.append((completed, m.draws[-1][0] - initial))
            self.assertEqual(m.player, native.player)
            self.assertEqual(m.wind_calls, native.wind_calls)
            self.assertEqual(m.fog_calls, native.fog_calls)
            self.assertEqual(m.rng_calls, native.rng_calls)
            self.assertEqual(m.get(0x5697D4), native.get(0x5697D4))
            return True

        if quantized_timer:
            # Real 50ms callback requests, with GetTickCount quantized to
            # alternating 15/16ms values. This yields 94/110/93/94/109ms ticks.
            last_clock = None
            for wall in range(1000, 3451):
                now = (wall * 64 // 1000) * 1000 // 64
                advanced = (wall - 1001) % 50 == 0 and advance(now, now)
                if not advanced and now != last_clock and m.poll(now)[1] == 4:
                    m.draw(now, True)
                    samples.append((now, m.draws[-1][0] - initial))
                last_clock = now
        else:
            # Callback cadence remains exactly native100ms. Variable work
            # completion adds15ms on alternate ticks, producing85/115ms gaps.
            completions = {1001 + i * 100 + (15 if jitter and i % 2 == 0 else 0):
                           1001 + i * 100 for i in range(24)}
            frame_times = {1000 + i * 1000 // 64 for i in range(160)}
            for now in sorted(set(completions) | frame_times):
                if now in completions:
                    self.assertTrue(advance(completions[now], now))
                elif m.poll(now)[1] == 4:
                    m.draw(now, True)
                    samples.append((now, m.draws[-1][0] - initial))
        self.assertEqual(len(ticks), 24)
        return m, samples, ticks

    def test_q8_rolling_deadline_removes_repeated_clumps_and_speed_ripple(self):
        for speed in (1, -1, 3, -3, 21, -21, 96, -96):
            for jitter in (False, True):
                with self.subTest(speed=speed, jitter=jitter):
                    m, samples, ticks = self.trace_cadence(speed, jitter=jitter)
                    for now, position in samples:
                        ideal = speed * min(max(now - ticks[0], 0), 2400) / 100
                        self.assertLessEqual(abs(position - ideal), 0.6)
                    if abs(speed) == 1:
                        changes = [b[0] for a, b in zip(samples, samples[1:]) if a[1] != b[1]]
                        gaps = [b - a for a, b in zip(changes, changes[1:])]
                        self.assertGreaterEqual(min(gaps), 80)
                        self.assertLessEqual(max(gaps), 120)
                    self.assertEqual(m.player[0] - 16320, speed * 24)

        # Keep the deployed v5 machine code as a regression control, rather
        # than merely asserting our Python reference matches its own formulas.
        from world_map_follow_v5 import build_smooth as build_v5
        _m, old_samples, old_ticks = self.trace_cadence(1, smooth_builder=build_v5)
        changes = [b[0] for a, b in zip(old_samples, old_samples[1:]) if a[1] != b[1]]
        old_gaps = [b - a for a, b in zip(changes, changes[1:])]
        self.assertLess(min(old_gaps), 65)
        self.assertGreater(max(old_gaps), 135)
        self.assertGreater(max(abs(position - min(now - old_ticks[0], 2400) / 100)
                               for now, position in old_samples), 0.8)

    def test_native_quantized_timer_keeps_direction_symmetric_steady_motion(self):
        for speed in (1, -1, 21, -21, 96, -96):
            with self.subTest(speed=speed):
                m, samples, ticks = self.trace_cadence(speed, quantized_timer=True)
                self.assertEqual(ticks[:6], [1046, 1140, 1250, 1343, 1437, 1546])
                # A first late tick shifts the endpoint once by4ms. Later
                # 15/16ms timer quantization must not restart that speed ripple.
                phase = m.smooth("deadline") - len(ticks) * 100
                self.assertEqual(phase, 1050)
                for now, position in samples:
                    if now >= 1600:
                        ideal = speed * min(now - phase, 2400) / 100
                        self.assertLessEqual(abs(position - ideal), 0.6)

    def test_visual_backlog_is_bounded_and_stalls_rebase_without_physics_catchup(self):
        m = _SmoothMachine()
        initial = m.player[0]
        for now, expected_duration in ((1000, 100), (1010, 190), (1020, MAX_SEGMENT_MS)):
            m.now = now
            m.update(32, 0)
            self.assertEqual(m.smooth("duration"), expected_duration)
            self.assertEqual(m.smooth("deadline"), now + expected_duration)
        m.now = 1030
        m.update(0, 0)
        self.assertEqual(m.smooth("deadline"), 1220)
        m.draw(1220, True)
        self.assertEqual(m.draws[-1][0], initial + 96)
        self.assertEqual(m.smooth("active"), 0)
        m.now = 5000
        m.update(32, 0)
        self.assertEqual(m.smooth("duration"), 100)
        self.assertEqual(m.smooth("deadline"), 5100)
        self.assertEqual(m.player[0], initial + 128)
        m.draw(5050, True)
        self.assertEqual(m.draws[-1][0], initial + 112)

    def test_q8_sampler_extreme_world_delta_and_clock_wrap_fit_signed_product(self):
        for direction in (-1, 1):
            m = _SmoothMachine()
            previous = 39990 if direction < 0 else 19990
            target = (previous + direction * 20000) % 40000
            m.put(0x5B63B0, target, 19999)
            for name, value in dict(active=1, width=640, height=320,
                                    target_x=target, target_y=19999,
                                    prev_fp_x=previous * 256, prev_fp_y=0,
                                    delta_fp_x=direction * 20000 * 256,
                                    delta_fp_y=19999 * 256, duration=200,
                                    start=-64, deadline=136).items():
                m.set(name, value)
            for elapsed in (1, 99, 199, 200):
                m.draw((-64 + elapsed) & 0xFFFFFFFF, True)
                self.assertEqual(m.draws[-1],
                                 ((previous + self.rounded_motion(direction * 20000, elapsed, 200)) % 40000,
                                  self.rounded_motion(19999, elapsed, 200)))
                self.assertEqual(m.player, (target, 19999))

    def test_intermediate_draws_preserve_physics_and_hidden_camera(self):
        m = _SmoothMachine()
        initial = m.player
        m.update(96, 48)
        physical, legacy = m.player, (m.value("legacy_x"), m.value("legacy_y"))
        wind, fog = list(m.wind_calls), list(m.fog_calls)
        m.draw(1000)
        initial_animation = m.get(0x61B3DC)
        for elapsed in (16, 32, 48, 64, 80, 96, 100):
            m.draw(1000 + elapsed, True)
            self.assertEqual(m.draws[-1], (initial[0] + self.rounded_motion(96, elapsed),
                                           initial[1] + self.rounded_motion(48, elapsed)))
            self.assertEqual(m.player, physical)
            self.assertEqual((m.value("camera_last_player_x"), m.value("camera_last_player_y")), physical)
            self.assertEqual((m.value("legacy_x"), m.value("legacy_y")), legacy)
            self.assertEqual(m.value("camera_moving"), 0)
            self.assertEqual(m.smooth("rendering"), 0)
            self.assertEqual(m.get(0x61B3DC), initial_animation)
        self.assertEqual(m.updates, 1)
        self.assertEqual(m.wind_calls, wind)
        self.assertEqual(m.fog_calls, fog)
        self.assertEqual(m.smooth("active"), 0)

    def test_poll_gates_real_event_priority_and_non_world_passthrough(self):
        m = _SmoothMachine()
        m.update(96, 0)
        m.draw(1000)
        self.assertEqual(m.poll(1000 + FRAME_MS - 1), (0, -1))
        self.assertEqual(m.poll(1000 + FRAME_MS), (1, 4))
        self.assertEqual(m.poll(1032, result=1), (1, -1))
        self.assertEqual(m.poll(1048, waiting=False), (0, -1))
        self.assertEqual(m.poll(1064, manager=0x62B380), (0, -1))
        self.assertEqual(m.uc.reg_read(UC_X86_REG_ECX), 0x12345678)
        self.assertEqual(m.uc.reg_read(UC_X86_REG_EDX), 0x23456789)
        self.assertEqual(m.poll_calls[-1], (0x62B380, 0x10016000))
        self.assertEqual(m.updates, 1)

    def test_wait_render_event_resumes_paint_setup_without_game_tick(self):
        m = _SmoothMachine()
        for result, destination in ((4, 0x48EC04), (2, 0x48EC89), (1, 0x48EC89)):
            with self.subTest(result=result):
                m.wait_result = result
                sp = 0x10018000
                m.uc.reg_write(UC_X86_REG_ESP, sp)
                m.uc.reg_write(UC_X86_REG_ECX, 0x61B2D0)
                m.uc.emu_start(0x48EC84, destination, count=10000)
                self.assertEqual(m.uc.reg_read(UC_X86_REG_EIP), destination)
                self.assertEqual(m.uc.reg_read(UC_X86_REG_ESP), sp)
                self.assertEqual(m.smooth("waiting"), 0)
                self.assertEqual(m.smooth("render_only"), int(result == 4))
                self.assertEqual(m.updates, 0)

    def test_world_wrap_clock_wrap_and_negative_displacement(self):
        m = _SmoothMachine(origin=(0, 300), relative=(8, 160))
        m.now = 0xFFFFFFF0
        m.update(-32, -32)
        physical = m.player
        m.draw(0xFFFFFFF0)
        m.draw(0x22, True)  # elapsed=50 across GetTickCount wrap
        self.assertEqual(m.draws[-1], (39992, physical[1] + 16))
        self.assertEqual(m.player, physical)
        self.assertEqual(m.poll(0x32), (1, 4))

    def test_native_timer_deadline_and_simulation_match_with_extra_frames(self):
        baseline = _SmoothMachine(follow=False)
        smooth = _SmoothMachine()
        for m in (baseline, smooth):
            m.put(0x5697D4, 1000)
        baseline_ticks, smooth_ticks, extra_frames = [], [], []
        # The native pump invokes 48B120 on its unchanged 50 ms timer.  Run
        # that real callback, then exercise our poll at 1 ms resolution.
        for now in range(1000, 2001):
            for m, ticks in ((baseline, baseline_ticks), (smooth, smooth_ticks)):
                m.now = now
                m.put(0x61B368, 0, -1)
                if now % 50 == 0:
                    m.call(0x48B120, (0x10016000,))
                real_event = m.get(0x61B36C)
                if m is smooth:
                    m.native_poll_result = int(m.get(0x61B368) != 0)
                    m.set("waiting", 1)
                    m.call(m.targets[0x4B8D27], (0x10016000,), this=0x62B368)
                    event = m.get(0x61B36C)
                    if event == 4:
                        self.assertNotEqual(real_event, 2)
                        extra_frames.append(now)
                        m.draw(now, True)
                else:
                    event = real_event
                if event == 2:
                    ticks.append(now)
                    m.update(32, 16)
                    if m is smooth:
                        m.draw(now)
            self.assertEqual(smooth.get(0x5697D4), baseline.get(0x5697D4))
            self.assertEqual(smooth.player, baseline.player)
            self.assertEqual(smooth.wind_calls, baseline.wind_calls)
            self.assertEqual(smooth.fog_calls, baseline.fog_calls)
        self.assertEqual(smooth_ticks, baseline_ticks)
        self.assertEqual(smooth_ticks, list(range(1050, 2001, 100)))
        self.assertGreaterEqual(len(extra_frames), 50)
        self.assertTrue(all(b - a >= FRAME_MS for a, b in zip(extra_frames, extra_frames[1:])))
        self.assertEqual(smooth.updates, 10)

    def test_native_timer_jitter_slow_updates_and_skipped_intervals_are_continuous(self):
        native = _SmoothMachine(follow=False)
        m = _SmoothMachine()
        initial = m.player[0]
        for machine in (native, m):
            machine.put(0x5697D4, 1000)

        def update(callback_time, completed_time, expected_start):
            for machine in (native, m):
                machine.now = callback_time
                machine.put(0x61B368, 0, -1)
                machine.call(0x48B120, (0x10016000,))
                self.assertEqual(machine.get(0x61B36C), 2)
            native.move(96, 0)
            m.now = completed_time
            m.update(96, 0)
            m.draw(completed_time)
            self.assertEqual(m.draws[-1][0] - initial, expected_start)
            self.assertEqual(m.smooth("prev_x") - initial, expected_start)
            self.assertEqual(m.player, native.player)
            self.assertEqual(m.wind_calls, native.wind_calls)
            self.assertEqual(m.fog_calls, native.fog_calls)
            self.assertEqual(m.rng_calls, native.rng_calls)
            self.assertEqual(m.get(0x5697D4), native.get(0x5697D4))

        update(1051, 1051, 0)
        m.draw(1096, True)
        self.assertEqual(m.draws[-1][0] - initial, 43)
        update(1101, 1101, 48)  # was 96: a 53 px jump in just 5 ms
        m.draw(1191, True)
        self.assertEqual(m.draws[-1][0] - initial, 134)
        self.assertEqual(m.smooth("duration"), 150)
        update(1201, 1261, 192)  # this native update takes 60 ms to finish
        m.draw(1291, True)
        self.assertEqual(m.draws[-1][0] - initial, 221)
        update(1301, 1301, 230)  # was 288: another jump after a shortened gap
        m.draw(1391, True)
        self.assertEqual(m.draws[-1][0] - initial, 317)
        self.assertEqual(m.smooth("duration"), 160)
        update(1501, 1501, 384)  # missed native intervals do not invent moves
        self.assertEqual(m.get(0x5697D4), 1600)
        self.assertEqual(m.player[0] - initial, 480)
        self.assertEqual(m.updates, 5)

    def test_zero_motion_and_collision_finish_existing_visual_segment(self):
        for collision in (False, True):
            with self.subTest(collision=collision):
                native = _Machine(False)
                m = _SmoothMachine()
                initial = m.player[0]
                native.move(96, 0)
                m.update(96, 0)
                m.draw(1045, True)
                self.assertEqual(m.draws[-1][0] - initial, 43)
                if collision:
                    native.blocked_x = m.blocked_x = m.player[0] + 1
                movement = (32, 0) if collision else (0, 0)
                native.move(*movement)
                m.now = 1050
                m.update(*movement)
                self.assertEqual(m.player[0] - initial, 96)
                self.assertEqual(m.smooth("start"), 1000)
                self.assertEqual(m.smooth("prev_x"), initial)
                m.draw(1050)
                self.assertEqual(m.draws[-1][0] - initial, 48)
                m.draw(1100, True)
                self.assertEqual(m.draws[-1][0] - initial, 96)
                self.assertEqual(m.player, native.player)
                self.assertEqual(m.wind_calls, native.wind_calls)
                self.assertEqual(m.fog_calls, native.fog_calls)
                self.assertEqual(m.rng_calls, native.rng_calls)

    def test_continuous_reversal_wraps_world_and_clock_without_changing_physics(self):
        native = _Machine(False, origin=(0, 300), relative=(8, 160), high_speed=True)
        m = _SmoothMachine(origin=(0, 300), relative=(8, 160), high_speed=True)
        m.now = 0xFFFFFFE0
        native.move(-96, -32)
        m.update(-96, -32)
        m.draw(0xD, True)  # elapsed45, before the shortened next interval
        self.assertEqual(m.draws[-1], (39965, 4946))
        m.now = 0x12  # elapsed50, across the clock rollover
        native.move(192, 64)
        m.update(192, 64)
        m.draw(0x12)
        self.assertEqual(m.draws[-1], (39960, 4944))
        self.assertEqual(m.smooth("delta_x"), 144)
        self.assertEqual(m.smooth("duration"), 150)
        for now, expected in ((0x2B, (39984, 4952)), (0x44, (8, 4960)),
                              (0x76, (56, 4976)), (0xA8, (104, 4992))):
            m.draw(now, True)
            self.assertEqual(m.draws[-1], expected)
            self.assertEqual(m.player, native.player)
            self.assertEqual(m.wind_calls, native.wind_calls)
            self.assertEqual(m.fog_calls, native.fog_calls)
            self.assertEqual(m.rng_calls, native.rng_calls)

    def test_external_load_breaks_visual_chain_before_the_next_update(self):
        m = _SmoothMachine()
        m.update(96, 48)
        m.draw(1030, True)
        m.put(0x5B63B0, 29000, 12000)
        m.now = 1050
        m.update(16, 8)
        m.draw(1050)
        self.assertEqual(m.draws[-1], (29000, 12000))
        self.assertEqual(m.player, (29016, 12008))
        self.assertEqual(m.smooth("continuing"), 0)

    def test_nested_draw_tail_calls_native_without_overwriting_outer_state(self):
        m = _SmoothMachine()
        m.update(96, 48)
        m.set("rendering", 1)
        m.set("render_only", 1)
        fields = ("draw_view", "saved_animation", "saved_phys_x", "saved_phys_y",
                  "saved_moving", "last_draw")
        for index, field in enumerate(fields):
            m.set(field, 0x12340000 + index)
        before = {field: m.smooth(field) for field in fields}
        physical = m.player
        animation = m.get(0x61B3DC)
        m.draw(1050, True)
        self.assertEqual({field: m.smooth(field) for field in fields}, before)
        self.assertEqual(m.smooth("rendering"), 1)
        self.assertEqual(m.draws[-1], physical)
        self.assertEqual(m.player, physical)
        self.assertEqual(m.get(0x61B3DC), animation + 1)
        self.assertEqual(m.uc.reg_read(UC_X86_REG_EAX), 0x12345678)

    def test_pause_special_effect_load_hidden_and_resize_cancel_interpolation(self):
        for condition in ("pause", "effect", "load", "resize", "hidden", "inactive"):
            with self.subTest(condition=condition):
                m = _SmoothMachine()
                m.update(96, 48)
                if condition == "pause":
                    m.put(0x5B3A00, 1)
                elif condition == "effect":
                    m.put(0x61B3D8, 7)
                elif condition == "load":
                    m.put(0x5B63B0, 29000, 12000)
                elif condition == "resize":
                    m.put(0x61B32C, 800, 480)
                elif condition == "hidden":
                    m.put(0x61B370, 5)
                else:
                    m.put(0x61B370, 0)
                physical = m.player
                self.assertEqual(m.poll(1020), (0, -1))
                self.assertEqual(m.smooth("active"), 0)
                m.draw(1050)
                self.assertEqual(m.draws[-1], physical)
                self.assertEqual(m.player, physical)

    def test_pending_weather_cache_skips_extra_draw_without_canceling_camera(self):
        for address, phase in ((0x5B6840, 1), (0x5B6840, -1), (0x5B6844, 1)):
            with self.subTest(address=hex(address), phase=phase):
                m = _SmoothMachine()
                initial = m.player
                m.put(address, phase)
                m.update(8, 0)
                physical = m.player
                m.draw(1000)  # normal capture is allowed, even without a cache
                self.assertEqual(m.draws[-1], initial)
                self.assertEqual(m.poll(1015), (0, -1))
                self.assertEqual(m.smooth("active"), 1)
                draw_count = len(m.draws)
                m.draw(1015, True)
                self.assertEqual(len(m.draws), draw_count)
                self.assertEqual(m.smooth("active"), 1)
                self.assertEqual(m.player, physical)
                self.assertEqual(m.poll(1030, result=1), (1, -1))

    def test_native_rain_keeps_dry_camera_cadence_without_extra_weather_updates(self):
        native = _RainMachine()
        rain = _RainMachine()
        dry = _SmoothMachine()
        for machine in (native, rain, dry):
            machine.put(0x5697D4, 1000)
        extras = 0
        for index in range(3):
            now = 1005 + index * 100
            for machine in (native, rain, dry):
                machine.now = now
                machine.put(0x61B368, 0, -1)
                machine.call(0x48B120, (0x10016000,))
                self.assertEqual(machine.get(0x61B36C), 2)
                machine.update(8, 0)
                if machine is not dry:
                    # The normal loop's 4893D0 advances this phase before draw.
                    machine.put(0x5B6840, index + 1)
                machine.draw(now)
            self.assertEqual(rain.rain_state(), native.rain_state())
            weather = rain.rain_state()
            weather_phase = rain.get(0x61D780)
            rng = rain.rng_calls
            for elapsed in (15, 30, 45, 60, 75, 90):
                self.assertEqual(rain.poll(now + elapsed), (1, 4))
                self.assertEqual(dry.poll(now + elapsed), (1, 4))
                rain.draw(now + elapsed, True)
                dry.draw(now + elapsed, True)
                extras += 1
                self.assertEqual(rain.draws[-1], dry.draws[-1])
                self.assertEqual(rain.rain_state(), weather)
                self.assertEqual(rain.get(0x61D780), weather_phase)
                self.assertEqual(rain.rng_calls, rng)
                self.assertEqual(rain.player, native.player)
            self.assertEqual(rain.rng_calls, native.rng_calls)
            self.assertEqual(rain.wind_calls, native.wind_calls)
            self.assertEqual(rain.fog_calls, native.fog_calls)
            self.assertEqual(rain.get(0x5697D4), native.get(0x5697D4))
        self.assertEqual(extras, 18)
        self.assertGreater(rain.rng_calls, 0)  # actual native rain respawns ran
        self.assertEqual(rain.updates, 3)

    def test_world_entry_and_exit_invalidate_captured_weather(self):
        for entry, stop in ((0x48E99E, 0x48E9A3), (0x48EF95, 0x48EF9F)):
            with self.subTest(entry=hex(entry)):
                m = _RainMachine()
                m.update(8, 0)
                m.draw(1000)
                self.assertEqual(m.poll(1015), (1, 4))
                sp, frame = 0x10018000, 0x10017000
                m.put(frame - 0x10, 0x61B2D0)
                m.uc.reg_write(UC_X86_REG_ECX, 0x61B2D0)
                m.uc.reg_write(UC_X86_REG_ESP, sp)
                m.uc.reg_write(UC_X86_REG_EBP, frame)
                m.uc.emu_start(entry, stop, count=100000)
                self.assertEqual(m.uc.reg_read(UC_X86_REG_EIP), stop)
                m.call(m.state["weather_ready"])
                self.assertEqual(m.uc.reg_read(UC_X86_REG_EAX), 0)

    def test_high_speed_batch_defers_added_visibility_work_until_paint(self):
        native = _Machine(False, high_speed=True)
        m = _SmoothMachine(high_speed=True)
        m.refreshes = 0
        native.move(640, 33)
        m.update(640, 33)
        self.assertEqual(m.player, native.player)
        self.assertEqual(m.wind_calls, native.wind_calls)
        self.assertEqual(m.fog_calls, native.fog_calls)
        self.assertEqual(m.refreshes, 2)  # only the native two edge transitions
        m.draw(1000)
        self.assertEqual(m.refreshes, 3)  # one visible-camera rebuild at paint
        self.assertEqual(m.value("camera_defer"), 0)
        self.assertEqual(m.value("camera_defer_started"), 0)

    def test_native_cloud_draw_follows_fractional_camera_without_tile_snap(self):
        for dx, dy in ((32, 16), (-32, -16)):
            m = _CloudMachine()
            for index in range(6):
                m.put(0x5B6890 + index * 8, 400 + index * 16, 100 + index * 16)
            m.update(dx, dy)
            m.put(0x586168, 0)  # calm wind isolates camera motion from cloud drift
            m.advance_clouds()
            physical, rng = m.player, m.rng_calls
            previous = None
            for elapsed in (0, 15, 30, 45, 60, 75, 90, 100):
                with self.subTest(movement=(dx, dy), elapsed=elapsed):
                    m.draw(1000 + elapsed, elapsed != 0)
                    persistent = bytes(m.uc.mem_read(0x5B6840, 0x80))
                    m.draw_clouds()
                    destination, _ = m.cloud_blits[0]
                    expected = (400 - self.rounded_motion(dx, elapsed),
                                132 - self.rounded_motion(dy, elapsed))
                    self.assertEqual(destination[:2], expected)
                    if previous is not None:
                        self.assertLessEqual(abs(destination[0] - previous[0]), 5)
                        self.assertLessEqual(abs(destination[1] - previous[1]), 3)
                    previous = destination
                    self.assertEqual(bytes(m.uc.mem_read(0x5B6840, 0x80)), persistent)
                    self.assertEqual(m.get(0x5B6890) + m.origin[0] * 16, 16400)
                    self.assertEqual(m.get(0x5B6894) + m.origin[1] * 16, 4900)
                    self.assertEqual(m.player, physical)
                    self.assertEqual(m.rng_calls, rng)

    def test_native_cloud_update_matches_legacy_across_reverse_and_high_speed_moves(self):
        native = _Machine(False, high_speed=True)
        m = _CloudMachine(high_speed=True)
        for machine in (native, m):
            machine.put(0x5AA2D8, 640, 320)
            machine.put(0x5B6840, -2, -2)
            for index in range(6):
                machine.put(0x5B6890 + index * 8, 200 + index * 16, 100 + index * 16)
        movements = ((32, 0), (32, 0), (32, 0), (-32, 0), (-32, 0),
                     (96, 16), (-96, -16), (640, 33))
        for index, movement in enumerate(movements):
            with self.subTest(index=index, movement=movement):
                native.move(*movement)
                m.now = 1000 + index * 100
                m.update(*movement)
                native.call(0x4893D0, this=0x5B6840)
                m.advance_clouds()
                self.assertEqual(m.player, native.player)
                self.assertEqual((m.value("legacy_x"), m.value("legacy_y")), native.origin)
                self.assertEqual(bytes(m.uc.mem_read(0x5B6840, 0x80)),
                                 bytes(native.uc.mem_read(0x5B6840, 0x80)))
                for elapsed in (0, 15, 30, 45, 60, 75, 90):
                    m.draw(1000 + index * 100 + elapsed, elapsed != 0)
                    persistent = bytes(m.uc.mem_read(0x5B6840, 0x80))
                    m.draw_clouds()
                    self.assertEqual(bytes(m.uc.mem_read(0x5B6840, 0x80)), persistent)
                    for cloud in range(6):
                        x, y = 0x5B6890 + cloud * 8, 0x5B6894 + cloud * 8
                        self.assertEqual((m.get(x) + m.origin[0] * 16) % 40000,
                                         (native.get(x) + native.origin[0] * 16) % 40000)
                        self.assertEqual(m.get(y) + m.origin[1] * 16,
                                         native.get(y) + native.origin[1] * 16)
                self.assertEqual(m.wind_calls, native.wind_calls)
                self.assertEqual(m.fog_calls, native.fog_calls)
                self.assertEqual(m.rng_calls, native.rng_calls)


if __name__ == "__main__":
    unittest.main()
