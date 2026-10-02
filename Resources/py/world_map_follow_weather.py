"""Replay the last rain/snow draw without advancing native weather or RNG.

The normal 49ABB0 call retains its complete resource/simulation lifecycle.
Only its world-map call sites record raw x/y/surface/frame/size commands.
Extra camera frames replay the three native drawing/cache helpers, never the
effect manager, particle update, initializer, destructor, or random sampler.
"""

from __future__ import annotations

import struct

from world_map_follow_render import MAP_SURFACE, _Code

WEATHER_OFFSET = 0x2800
WEATHER_LIMIT = 0x4000
RECORD_WORDS = 625  # 75 rain + 50 snow, five dwords per particle
META_WORDS = 12
# valid, count, phase, view, width, height, source, dest, cache, cols, rows, records
CHANNELS = ((0x5B6840, 0x56E2A0, 0x61E274, 75, 32),
            (0x5B6844, 0x56E2A4, 0x61DCC0, 50, 16))


def build_weather(slot_va: int, state: dict[str, int]):
    """Return chunks/hooks; publish weather_ready and weather_invalidate VAs.

    ready accepts ECX=view, returns EAX=0/1, and preserves every other GPR and
    EFLAGS. invalidate preserves all registers/flags. Both use plain RET.
    Runtime allocation must give consecutive addresses to each declared block.
    """
    records = [state[f"weather_record_{i}"] for i in range(RECORD_WORDS)]
    metas = [[state[f"weather_meta_{j}_{i}"] for i in range(META_WORDS)] for j in range(2)]
    for block in (records, *metas):
        if block != list(range(block[0], block[0] + len(block) * 4, 4)):
            raise AssertionError("Weather runtime blocks must be contiguous.")
    c = _Code(slot_va + WEATHER_OFFSET)
    hooks = []

    def const(name, value):
        c.mem("C7 05", state[name]); c.u32(value)

    def hook(va, original_target, label):
        before = b"\xe8" + struct.pack("<i", original_target - va - 5)
        after = b"\xe8" + struct.pack("<i", c.va + c.labels[label] - va - 5)
        hooks.append((va, before, after))

    def compare_meta(offset, address, failure):
        c.mem("A1", address)
        c.emit(f"3B 46 {offset:02X}")
        c.branch("0F 85", failure)

    def validate(channel, failure):
        phase, alias, obj, capacity, size = CHANNELS[channel]
        c.emit("83 3E 01")
        c.branch("0F 85", failure)
        compare_meta(8, phase, failure)
        c.emit("3B 4E 0C")
        c.branch("0F 85", failure)
        for offset, address in ((0x10, 0x61B32C), (0x14, 0x61B330),
                                (0x1C, MAP_SURFACE), (0x18, alias)):
            compare_meta(offset, address, failure)
        c.emit("85 C0")
        c.branch("0F 84", failure)
        c.mem("3B 05", obj)
        c.branch("0F 85", failure)
        for offset, field in ((0x20, 0xD0), (0x24, 0xEC), (0x28, 0xF0)):
            c.emit("8B 81"); c.u32(field)
            c.emit(f"3B 46 {offset:02X}")
            c.branch("0F 85", failure)
        c.emit(f"83 7E 04 {capacity:02X}")
        c.branch("0F 87", failure)

    c.label("ready")
    c.emit("9C 60 85 C9")
    c.branch("0F 84", "not_ready")
    c.mem("83 3D", state["weather_busy"]); c.emit("00")
    c.branch("0F 85", "not_ready")
    for channel, (phase, *_unused) in enumerate(CHANNELS):
        c.mem("83 3D", phase); c.emit("FE")
        c.branch("0F 84", f"ready_next_{channel}")
        c.branch("0F 8C", "not_ready")
        c.emit("BE"); c.u32(metas[channel][0])
        validate(channel, "not_ready")
        c.label(f"ready_next_{channel}")
    c.emit("C7 44 24 1C 01 00 00 00 61 9D C3")
    c.label("not_ready")
    c.emit("C7 44 24 1C 00 00 00 00 61 9D C3")

    c.label("invalidate")
    c.emit("9C 60")
    for meta in metas:
        for address in meta[:2]:
            c.mem("C7 05", address); c.u32(0)
    for name in ("weather_screen", "weather_busy", "weather_recording", "weather_pending"):
        const(name, 0)
    c.emit("61 9D C3")

    for channel, (phase, alias, obj, capacity, size) in enumerate(CHANNELS):
        prefix = f"channel_{channel}"
        c.label(prefix)
        c.emit("55 8B EC 53 56 57")
        # Scope capture to valid active map rendering, never other effect users.
        for name in ("active", "valid"):
            c.mem("83 3D", state[name]); c.emit("00")
            c.branch("0F 84", prefix + "_fallback")
        c.emit(f"83 7D 08 {channel:02X}")
        c.branch("0F 85", prefix + "_fallback")
        c.emit("8B 45 10"); c.mem("3B 05", MAP_SURFACE)
        c.branch("0F 85", prefix + "_fallback")
        c.mem("83 3D", state["weather_busy"]); c.emit("00")
        c.branch("0F 85", prefix + "_reentry")
        c.emit("BE"); c.u32(metas[channel][0])
        c.mem("83 3D", state["smooth_render_only"]); c.emit("00")
        c.branch("0F 85", prefix + "_replay")
        const("weather_busy", 1)
        const("weather_screen", 1)
        const("weather_recording", 1)
        const("weather_pending", 0)
        const("weather_bad", 0)
        const("weather_expected_size", size)
        const("weather_capacity", capacity)
        const("weather_current_meta", metas[channel][0])
        c.emit("C7 06 00 00 00 00 C7 46 04 00 00 00 00 C7 46 18 00 00 00 00")
        for offset, arg in ((8, 0xC), (0x1C, 0x10), (0x20, 0x14), (0x24, 0x18), (0x28, 0x1C)):
            c.emit(f"8B 45 {arg:02X} 89 46 {offset:02X}")
        for offset, name in ((0xC, "view"), (0x10, "width"), (0x14, "height")):
            c.mem("A1", state[name]); c.emit(f"89 46 {offset:02X}")
        c.emit("C7 46 2C"); c.u32(records[0] + (1500 if channel else 0))
        c.emit("FF 75 1C FF 75 18 FF 75 14 FF 75 10 FF 75 0C FF 75 08")
        c.to("E8", 0x49ABB0)
        c.emit("83 C4 18 8B D8")
        const("weather_recording", 0)
        c.emit("85 DB")
        c.branch("0F 84", prefix + "_captured")
        c.mem("83 3D", state["weather_bad"]); c.emit("00")
        c.branch("0F 85", prefix + "_captured")
        c.mem("A1", alias); c.emit("85 C0")
        c.branch("0F 84", prefix + "_captured")
        c.mem("3B 05", obj)
        c.branch("0F 85", prefix + "_captured")
        c.emit("83 7E 04 00")
        c.branch("0F 84", prefix + "_empty_capture")
        c.emit("3B 46 18")
        c.branch("0F 85", prefix + "_captured")
        c.label(prefix + "_empty_capture")
        c.emit("89 46 18 C7 06 01 00 00 00")
        c.label(prefix + "_captured")
        c.emit("8B C3")
        c.branch("E9", prefix + "_finish")

        c.label(prefix + "_replay")
        c.mem("8B 0D", state["view"])
        c.emit("85 C9")
        c.branch("0F 84", prefix + "_skip")
        validate(channel, prefix + "_skip")
        # Also validate the actual call arguments, not just the cached view.
        for offset, arg in ((8, 0xC), (0x1C, 0x10), (0x20, 0x14), (0x24, 0x18), (0x28, 0x1C)):
            c.emit(f"8B 45 {arg:02X} 3B 46 {offset:02X}")
            c.branch("0F 85", prefix + "_skip")
        const("weather_busy", 1)
        const("weather_screen", 1)
        for arg, address in ((0x10, 0x61E29C), (0x14, 0x61DBDC),
                             (0x18, 0x61D27C), (0x1C, 0x61DBF0)):
            c.emit(f"8B 45 {arg:02X}"); c.mem("A3", address)
        c.emit("8B 5E 04 8B 7E 2C 85 DB")
        c.branch("0F 84", prefix + "_replayed")
        c.label(prefix + "_particle")
        c.emit("FF 77 10 FF 77 10 FF 77 04 FF 37")
        c.to("E8", 0x49A620)
        c.emit("FF 77 10 FF 77 0C FF 77 08")
        c.to("E8", 0x49A700)
        c.emit("8B 07"); c.mem("03 05", state["rx"])
        c.emit("8B 57 04"); c.mem("03 15", state["ry"])
        c.emit("FF 77 10 FF 77 10 52 50")
        c.to("E8", 0x49A050)
        c.emit("83 C7 14 4B")
        c.branch("0F 85", prefix + "_particle")
        c.label(prefix + "_replayed")
        c.emit("B8 01 00 00 00")
        c.label(prefix + "_finish")
        const("weather_screen", 0)
        const("weather_busy", 0)
        c.branch("E9", prefix + "_return")
        c.label(prefix + "_reentry")
        c.mem("83 3D", state["smooth_render_only"]); c.emit("00")
        c.branch("0F 85", prefix + "_skip")
        # A nested normal draw is still allowed to run its native lifecycle,
        # but it must not make the outer command cache appear trustworthy.
        const("weather_bad", 1)
        c.branch("E9", prefix + "_native")
        c.label(prefix + "_fallback")
        c.mem("83 3D", state["smooth_render_only"]); c.emit("00")
        c.branch("0F 84", prefix + "_native")
        c.label(prefix + "_skip")
        c.emit("B8 01 00 00 00")
        c.label(prefix + "_return")
        c.emit("5F 5E 5B 5D C3")
        c.label(prefix + "_native")
        c.emit("5F 5E 5B 5D")
        c.to("E9", 0x49ABB0)
        hook((0x48AA6E, 0x48AAC2)[channel], 0x49ABB0, prefix)

    c.label("capture_xy")
    c.emit("9C 60")
    c.mem("83 3D", state["weather_recording"]); c.emit("00")
    c.branch("0F 84", "capture_xy_done")
    c.mem("A1", state["weather_expected_size"])
    c.emit("3B 44 24 30")
    c.branch("0F 85", "capture_xy_bad")
    c.emit("3B 44 24 34")
    c.branch("0F 85", "capture_xy_bad")
    for offset, name in ((0x28, "weather_x"), (0x2C, "weather_y")):
        c.emit(f"8B 44 24 {offset:02X}"); c.mem("A3", state[name])
    const("weather_pending", 1)
    c.branch("E9", "capture_xy_done")
    c.label("capture_xy_bad")
    const("weather_bad", 1)
    const("weather_pending", 0)
    c.label("capture_xy_done")
    c.emit("61 9D"); c.to("E9", 0x49A620)
    for va in (0x496E27, 0x49712D):
        hook(va, 0x49A620, "capture_xy")

    c.label("capture_draw")
    c.emit("9C 60")
    c.mem("83 3D", state["weather_recording"]); c.emit("00")
    c.branch("0F 84", "capture_draw_done")
    c.mem("83 3D", state["weather_pending"]); c.emit("01")
    c.branch("0F 85", "capture_draw_bad")
    const("weather_pending", 0)
    c.mem("A1", state["weather_expected_size"])
    c.emit("3B 44 24 30")
    c.branch("0F 85", "capture_draw_bad")
    c.emit("8B 54 24 28 85 D2")
    c.branch("0F 84", "capture_draw_bad")
    c.mem("8B 35", state["weather_current_meta"])
    c.emit("8B 46 04"); c.mem("3B 05", state["weather_capacity"])
    c.branch("0F 83", "capture_draw_bad")
    c.emit("85 C0")
    c.branch("0F 84", "capture_first_source")
    c.emit("3B 56 18")
    c.branch("0F 85", "capture_draw_bad")
    c.label("capture_first_source")
    c.emit("89 56 18 8D 04 80 C1 E0 02 03 46 2C 8B F8")
    for offset, name in ((0, "weather_x"), (4, "weather_y")):
        c.mem("A1", state[name]); c.emit("89 07" if offset == 0 else "89 47 04")
    for offset, arg in ((8, 0x28), (0xC, 0x2C), (0x10, 0x30)):
        c.emit(f"8B 44 24 {arg:02X} 89 47 {offset:02X}")
    c.emit("FF 46 04")
    c.branch("E9", "capture_draw_done")
    c.label("capture_draw_bad")
    const("weather_bad", 1)
    c.label("capture_draw_done")
    c.emit("61 9D"); c.to("E9", 0x49A700)
    for va in (0x496E39, 0x497148):
        hook(va, 0x49A700, "capture_draw")

    c.label("dirty")
    c.emit("9C 50")
    c.mem("83 3D", state["weather_screen"]); c.emit("00")
    c.branch("0F 84", "dirty_done")
    c.mem("A1", state["rx"]); c.emit("01 44 24 0C")
    c.mem("A1", state["ry"]); c.emit("01 44 24 10")
    c.label("dirty_done")
    c.emit("58 9D"); c.to("E9", 0x49A050)
    for va in (0x496E4E, 0x49715D):
        hook(va, 0x49A050, "dirty")

    state["weather_ready"] = c.va + c.labels["ready"]
    state["weather_invalidate"] = c.va + c.labels["invalidate"]
    payload = c.finish()
    if WEATHER_OFFSET + len(payload) > WEATHER_LIMIT:
        raise AssertionError("Weather replay exceeds its reserved code region.")
    return {WEATHER_OFFSET: payload}, hooks
