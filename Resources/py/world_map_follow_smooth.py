"""Render-only interpolation between the original 100 ms world simulation ticks.

The shared input poll is an exact tail call outside the world loop's wait.
Inside that wait it can publish event 4; the world-loop wrapper paints again
without executing encounters, movement, weather advancement, or the calendar.
"""

from __future__ import annotations

import struct


SMOOTH_OFFSET = 0x4000
SMOOTH_LIMIT = 0x5800
# GetTickCount commonly advances in alternating 15/16 ms quanta.  Accept
# either quantum without requesting a finer system-wide timer resolution.
FRAME_MS = 15
SEGMENT_MS = 100
MAX_SEGMENT_MS = 200
PLAYER_X, PLAYER_Y = 0x5B63B0, 0x5B63B4
VIEW_WIDTH, VIEW_HEIGHT = 0x61B32C, 0x61B330
GET_TICK_COUNT = 0x62F448


class _Code:
    def __init__(self, base):
        self.base, self.code, self.labels, self.fixups = base, bytearray(), {}, []

    def emit(self, code):
        self.code.extend(bytes.fromhex(code))

    def number(self, value):
        self.code.extend(struct.pack("<I", value & 0xFFFFFFFF))

    def mem(self, opcode, address):
        self.emit(opcode)
        self.number(address)

    def label(self, name):
        self.labels[name] = len(self.code)

    def jump(self, opcode, label):
        self.emit(opcode)
        self.fixups.append((len(self.code), label))
        self.number(0)

    def target(self, opcode, target):
        self.emit(opcode)
        self.code.extend(struct.pack("<i", target - self.base - len(self.code) - 4))

    def finish(self):
        for offset, label in self.fixups:
            struct.pack_into("<i", self.code, offset, self.labels[label] - offset - 4)
        return bytes(self.code)


def build_smooth(slot_va: int, state: dict[str, int]):
    """Return independent smooth-render hooks and their executable payload."""
    c = _Code(slot_va + SMOOTH_OFFSET)
    hooks = []

    def field(name):
        return state["smooth_" + name]

    def load(name):
        c.mem("A1", field(name))

    def save(name):
        c.mem("A3", field(name))

    def set_field(name, value):
        c.mem("C7 05", field(name))
        c.number(value)

    def set_shared(name, value):
        c.mem("C7 05", state[name])
        c.number(value)

    def cmp_field(name, value):
        c.mem("83 3D", field(name))
        c.emit(f"{value & 255:02X}")

    def tick():
        c.mem("FF 15", GET_TICK_COUNT)

    def guard_segment(failure):
        """Registers are scratch here; EBX is preserved for elapsed time."""
        cmp_field("running", 1)
        c.jump("0F 85", failure)
        cmp_field("active", 1)
        c.jump("0F 85", failure)
        c.mem("8B 0D", field("view"))
        c.emit("85 C9")
        c.jump("0F 84", failure)
        c.emit("F6 81 A0 00 00 00 01")
        c.jump("0F 85", failure)
        c.emit("F6 81 A0 00 00 00 04")
        c.jump("0F 84", failure)
        c.emit("83 B9 08 01 00 00 FF")
        c.jump("0F 85", failure)
        c.mem("83 3D", 0x5B3A00)
        c.emit("00")
        c.jump("0F 85", failure)
        for physical, name in ((PLAYER_X, "target_x"), (PLAYER_Y, "target_y"),
                               (VIEW_WIDTH, "width"), (VIEW_HEIGHT, "height")):
            c.mem("A1", physical)
            c.mem("3B 05", field(name))
            c.jump("0F 85", failure)
        for name in ("width", "height"):
            cmp_field(name, 0)
            c.jump("0F 8E", failure)

    def weather_ready(failure):
        # Normal simulation draws create the next weather cache.  Only extra
        # paints need a matching captured frame; a pending cache must not tear
        # down the camera's still-valid interpolation segment.
        c.mem("8B 0D", field("view"))
        c.target("E8", state["weather_ready"])
        c.emit("85 C0")
        c.jump("0F 84", failure)

    def hook(va, original, label, is_call=False):
        before = bytes.fromhex(original)
        opcode = b"\xE8" if is_call else b"\xE9"
        after = opcode + struct.pack("<i", c.base + c.labels[label] - va - 5)
        hooks.append((va, before, after + b"\x90" * (len(before) - 5)))

    c.label("enter")
    c.emit("9C 60")
    c.mem("89 0D", field("view"))
    set_field("running", 1)
    for name in ("active", "waiting", "render_only", "rendering"):
        set_field(name, 0)
    set_shared("camera_defer", 0)
    set_shared("camera_defer_started", 0)
    c.target("E8", state["weather_invalidate"])
    c.emit("61 9D 89 4D F0 53 56")
    c.target("E9", 0x48E9A3)
    hook(0x48E99E, "89 4D F0 53 56", "enter")

    c.label("exit")
    c.emit("9C 60")
    for name in ("running", "active", "waiting", "render_only"):
        set_field(name, 0)
    set_shared("camera_defer", 0)
    set_shared("camera_defer_started", 0)
    c.target("E8", state["weather_invalidate"])
    c.emit("61 9D 8B 4D F0 83 A1 A0 00 00 00 FB")
    c.target("E9", 0x48EF9F)
    hook(0x48EF95, "8B 4D F0 83 A1 A0 00 00 00 FB", "exit")

    c.label("wait")
    # This flag scopes the global polling extension to this exact blocking
    # world-map wait.  The native pump continues to own all real input events.
    set_field("waiting", 1)
    c.target("E8", 0x459CC0)
    set_field("waiting", 0)
    c.emit("83 F8 04")
    c.jump("0F 85", "wait_normal")
    set_field("render_only", 1)
    c.emit("83 C4 04")  # discard this hook CALL's return, not the game frame
    c.target("E9", 0x48EC04)
    c.label("wait_normal")
    set_field("render_only", 0)
    c.emit("83 F8 02")
    c.jump("0F 84", "wait_return")
    set_field("active", 0)  # menu, cancel, focus/state changes
    c.label("wait_return")
    c.emit("C3")
    hook(0x48EC84, "E8 37 B0 FC FF", "wait", True)

    c.label("poll")
    # Preserve the native callee's complete outputs for every non-world call.
    cmp_field("waiting", 1)
    c.jump("0F 85", "poll_native")
    c.emit("81 F9 68 B3 62 00")
    c.jump("0F 85", "poll_native")
    c.emit("FF 74 24 04")
    c.target("E8", 0x4B8D3F)
    c.emit("9C 60 85 C0")
    c.jump("0F 85", "poll_return")  # real events always take priority
    guard_segment("poll_cancel")
    weather_ready("poll_return")
    c.emit("83 B9 98 00 00 00 00")
    c.jump("0F 85", "poll_return")
    tick()
    c.mem("2B 05", field("last_draw"))
    c.emit(f"83 F8 {FRAME_MS:02X}")
    c.jump("0F 82", "poll_return")
    c.mem("8B 0D", field("view"))
    c.emit("C7 81 98 00 00 00 01 00 00 00 C7 81 9C 00 00 00 04 00 00 00")
    c.emit("C7 44 24 1C 01 00 00 00")  # native EAX becomes poll-success
    c.jump("E9", "poll_return")
    c.label("poll_cancel")
    set_field("active", 0)
    c.label("poll_return")
    c.emit("61 9D C2 04 00")
    c.label("poll_native")
    c.target("E9", 0x4B8D3F)
    hook(0x4B8D27, "E8 13 00 00 00", "poll", True)

    c.label("update")
    c.emit("9C 60")
    c.mem("89 0D", field("view"))
    for physical, name in ((PLAYER_X, "update_phys_x"), (PLAYER_Y, "update_phys_y")):
        c.mem("A1", physical)
        save(name)
    # A native timer event can arrive before the previous visual segment is
    # complete (50 ms polling jitter, or a slow preceding update).  Retarget
    # from that segment's displayed position, never its unseen physical end.
    set_field("continuing", 0)
    guard_segment("update_captured")
    set_field("continuing", 1)
    c.label("update_captured")
    set_shared("camera_defer", 1)
    set_shared("camera_defer_started", 0)
    c.emit("61 9D")
    c.target("E8", 0x48D0A0)
    c.emit("9C 60")
    set_shared("camera_defer", 0)
    set_shared("camera_defer_started", 0)
    tick()
    save("update_time")
    cmp_field("continuing", 1)
    c.jump("0F 85", "update_from_physical")
    # Zero-displacement/collision ticks retain the old segment and deadline.
    # Restarting it here would delay its arrival, while canceling would snap.
    for physical, name in ((PLAYER_X, "update_phys_x"), (PLAYER_Y, "update_phys_y")):
        c.mem("A1", physical)
        c.mem("3B 05", field(name))
        c.jump("0F 85", "update_from_display")
    c.jump("E9", "update_done")
    c.label("update_from_display")
    # Keep the visual endpoint on a rolling simulation-sized timeline.
    # An early tick extends the remaining interval instead of speeding up
    # toward its new target; a late tick rebases once. Never accumulate an
    # unbounded display backlog after a burst of updates.
    load("deadline")
    c.mem("2B 05", field("update_time"))
    c.emit("85 C0")
    c.jump("0F 8F", "update_deadline_pending")
    c.emit("33 C0")
    c.label("update_deadline_pending")
    c.emit("05")
    c.number(SEGMENT_MS)
    c.emit("3D")
    c.number(MAX_SEGMENT_MS)
    c.jump("0F 86", "update_duration_ready")
    c.emit("B8")
    c.number(MAX_SEGMENT_MS)
    c.label("update_duration_ready")
    save("next_duration")
    load("update_time")
    c.mem("2B 05", field("start"))
    c.emit("8B D8")
    for axis in ("x", "y"):
        c.jump("E8", "sample_" + axis)
        save("prev_fp_" + axis)
        c.jump("E8", "round_" + axis)
        save("prev_" + axis)
    c.jump("E9", "update_new_segment")
    c.label("update_from_physical")
    set_field("next_duration", SEGMENT_MS)
    for axis in ("x", "y"):
        load("update_phys_" + axis)
        save("prev_" + axis)
        c.emit("C1 E0 08")
        save("prev_fp_" + axis)
    c.label("update_new_segment")
    set_field("active", 0)
    for physical, name in ((PLAYER_X, "target_x"), (PLAYER_Y, "target_y"),
                           (VIEW_WIDTH, "width"), (VIEW_HEIGHT, "height")):
        c.mem("A1", physical)
        save(name)
    # Form the shortest wrapped X displacement, never interpolating across
    # the entire 40,000-pixel map when crossing the date line.
    load("target_x")
    c.mem("2B 05", field("prev_x"))
    c.emit("3D 20 4E 00 00")
    c.jump("0F 8E", "update_x_low")
    c.emit("2D 40 9C 00 00")
    c.label("update_x_low")
    c.emit("3D E0 B1 FF FF")
    c.jump("0F 8D", "update_x_ready")
    c.emit("05 40 9C 00 00")
    c.label("update_x_ready")
    save("delta_x")
    load("target_y")
    c.mem("2B 05", field("prev_y"))
    save("delta_y")
    for axis in ("x", "y"):
        load("target_" + axis)
        c.emit("C1 E0 08")
        c.mem("2B 05", field("prev_fp_" + axis))
        if axis == "x":
            c.emit("3D 00 20 4E 00")
            c.jump("0F 8E", "update_fp_x_low")
            c.emit("2D 00 40 9C 00")
            c.label("update_fp_x_low")
            c.emit("3D 00 E0 B1 FF")
            c.jump("0F 8D", "update_fp_x_ready")
            c.emit("05 00 40 9C 00")
            c.label("update_fp_x_ready")
        save("delta_fp_" + axis)
    c.mem("0B 05", field("delta_fp_x"))
    c.jump("0F 84", "update_done")
    load("next_duration")
    save("duration")
    c.mem("03 05", field("update_time"))
    save("deadline")
    load("update_time")
    save("start")
    set_field("active", 1)
    c.label("update_done")
    set_field("render_only", 0)
    c.emit("61 9D C3")
    hook(0x48EF55, "E8 46 E1 FF FF", "update", True)

    c.label("draw")
    cmp_field("rendering", 0)
    c.jump("0F 85", "draw_reentrant")
    c.emit("9C 60")
    c.mem("89 0D", field("draw_view"))
    c.emit("8B 81 0C 01 00 00")
    save("saved_animation")
    # Native 48AB1E calls 49ABB0 even for effect=-1. Its 49A010
    # bookkeeping advances the shared weather frame before dispatching.
    # Extra draws must restore this too, or later rain/snow ticks speed up.
    c.mem("A1", 0x61D780)
    save("saved_weather_frame")
    tick()
    save("last_draw")
    c.mem("2B 05", field("start"))
    c.emit("8B D8")  # EBX=unsigned elapsed; 32-bit subtraction handles wrap
    guard_segment("draw_cancel")
    cmp_field("render_only", 1)
    c.jump("0F 85", "draw_weather_ready")
    weather_ready("draw_return")
    c.label("draw_weather_ready")
    c.mem("3B 1D", field("duration"))
    c.jump("0F 83", "draw_complete")
    for physical, name in ((PLAYER_X, "saved_phys_x"), (PLAYER_Y, "saved_phys_y")):
        c.mem("A1", physical)
        save(name)
    c.mem("A1", state["camera_moving"])
    save("saved_moving")
    c.mem("FF 05", state["camera_moving"])
    set_field("rendering", 1)
    for axis, physical in (("x", PLAYER_X), ("y", PLAYER_Y)):
        c.jump("E8", "sample_" + axis)
        c.jump("E8", "round_" + axis)
        c.mem("A3", physical)
    c.jump("E9", "draw_native")
    c.label("draw_cancel")
    set_field("active", 0)
    cmp_field("render_only", 1)
    c.jump("0F 84", "draw_return")
    c.jump("E9", "draw_native")
    c.label("draw_complete")
    set_field("active", 0)
    c.label("draw_native")
    c.mem("8B 0D", field("draw_view"))
    c.emit("FF 74 24 28")  # original thiscall argument, after flags/pushad
    c.target("E8", 0x48A1E0)
    c.emit("89 44 24 1C")  # retain the native return EAX
    cmp_field("rendering", 1)
    c.jump("0F 85", "draw_animation")
    for name, physical, last in (("saved_phys_x", PLAYER_X, "camera_last_player_x"),
                                 ("saved_phys_y", PLAYER_Y, "camera_last_player_y")):
        load(name)
        c.mem("A3", physical)
        c.mem("A3", state[last])
    load("saved_moving")
    c.mem("A3", state["camera_moving"])
    set_field("rendering", 0)
    c.label("draw_animation")
    cmp_field("render_only", 1)
    c.jump("0F 85", "draw_return")
    c.mem("8B 0D", field("draw_view"))
    load("saved_animation")
    c.emit("89 81 0C 01 00 00")
    load("saved_weather_frame")
    c.mem("A3", 0x61D780)
    c.label("draw_return")
    c.emit("61 9D C2 04 00")
    c.label("draw_reentrant")
    c.target("E9", 0x48A1E0)
    hook(0x48EC3B, "E8 A0 B5 FF FF", "draw", True)

    # Both retargeting and painting sample exactly the same Q8 curve. The
    # largest shortest-world delta is 20,000*256; clamped elapsed <=200
    # makes the signed product <=1,024,000,000, safely below INT32_MAX.
    # Keep subpixels here and round absolute coordinates only for display.
    for axis in ("x", "y"):
        c.label("sample_" + axis)
        load("delta_fp_" + axis)
        c.mem("8B 35", field("prev_fp_" + axis))
        c.jump("E8", "sample_value")
        if axis == "x":
            c.emit("85 C0")
            c.jump("0F 89", "sample_x_positive")
            c.emit("05 00 40 9C 00")
            c.label("sample_x_positive")
            c.emit("3D 00 40 9C 00")
            c.jump("0F 8C", "sample_x_wrapped")
            c.emit("2D 00 40 9C 00")
            c.label("sample_x_wrapped")
        c.emit("C3")
    c.label("sample_value")
    c.mem("8B 0D", field("duration"))
    c.emit("8B FB 3B F9")
    c.jump("0F 86", "sample_elapsed_ready")
    c.emit("8B F9")
    c.label("sample_elapsed_ready")
    c.emit("0F AF C7 99 F7 F9 03 C6 C3")
    for axis in ("x", "y"):
        c.label("round_" + axis)
        c.emit("05 80 00 00 00 C1 F8 08")
        if axis == "x":
            c.emit("3D 40 9C 00 00")
            c.jump("0F 8C", "round_x_ready")
            c.emit("2D 40 9C 00 00")
            c.label("round_x_ready")
        c.emit("C3")

    payload = c.finish()
    if SMOOTH_OFFSET + len(payload) > SMOOTH_LIMIT:
        raise AssertionError("부드러운 월드 지도 추적 코드가 예약 공간을 초과했습니다.")
    return {SMOOTH_OFFSET: payload}, hooks
