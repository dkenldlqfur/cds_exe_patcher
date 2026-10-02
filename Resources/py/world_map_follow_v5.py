"""Frozen v5 generators for exact validation, removal, and future migration.

Do not edit: this snapshot matches the deployed v5 payload and its hooks.
Camera, renderer, weather replay, interpolator, and modal wrappers are frozen.
"""

from __future__ import annotations

# Frozen v5 camera generator.

"""Native-movement-preserving camera and mouse hooks for world-map follow.

The visible camera and the original edge-scrolling camera are separate.  The
native mover runs with its original camera, including its wind/RNG and collision
side effects; only after it finishes do we restore the centered visible camera.
"""


import struct


ENSURE_FOLLOW_OFFSET = 0x100
PLAYER_X = 0x5B63B0
PLAYER_Y = 0x5B63B4
ORIGIN_X = 0x5B63A8
ORIGIN_Y = 0x5B63AC
VIEW = 0x61B2D0
VIEW_WIDTH = 0x61B32C
VIEW_HEIGHT = 0x61B330
NPC_RECORDS = 0x5B65D8


def build_camera(slot_va: int, state: dict[str, int]):
    """Return payload chunks and reversible (VA, original, replacement) hooks.

    ``slot+ENSURE_FOLLOW_OFFSET`` is thiscall(view), ret 0, preserving all
    registers and flags.  Call it before installing the render-only pixel
    translation. ``width`` and ``height`` are the unexpanded viewport in pixels.
    State storage is supplied by the enclosing patch; additional keys are
    allocated through its dictionary implementation.
    """
    code = bytearray()
    labels: dict[str, int] = {}
    fixups: list[tuple[int, str]] = []
    base = slot_va + ENSURE_FOLLOW_OFFSET
    fields = (
        "rx", "ry", "width", "height", "view", "valid", "legacy_x",
        "legacy_y", "follow_x", "follow_y", "npc_snapshot", "camera_busy",
        "camera_moving", "camera_tracking", "camera_x", "camera_y",
        "camera_rx", "camera_ry", "camera_last_player_x", "camera_last_player_y",
        "camera_defer", "camera_defer_started",
    )
    addresses = {name: state[name] for name in fields}

    def emit(value: str):
        code.extend(bytes.fromhex(value))

    def u32(value: int):
        code.extend(struct.pack("<I", value))

    def mem(opcode: str, address: int):
        emit(opcode)
        u32(address)

    def load(name: str):
        mem("A1", addresses[name])

    def save(name: str):
        mem("A3", addresses[name])

    def const(name: str, value: int):
        mem("C7 05", addresses[name])
        u32(value)

    def label(name: str):
        labels[name] = len(code)

    def branch(opcode: str, name: str):
        emit(opcode)
        fixups.append((len(code), name))
        u32(0)

    def call(address: int):
        emit("E8")
        code.extend(struct.pack("<i", address - (base + len(code) + 4)))

    def dimensions(invalid: str):
        mem("A1", VIEW_WIDTH)
        emit("85 C0")
        branch("0F 8E", invalid)
        emit("3D 40 9C 00 00")
        branch("0F 8F", invalid)
        save("width")
        mem("A1", VIEW_HEIGHT)
        emit("85 C0")
        branch("0F 8E", invalid)
        emit("3D 20 4E 00 00")
        branch("0F 8F", invalid)
        save("height")

    def reset_check(reset: str, good: str, check_player: bool):
        mem("83 3D", addresses["valid"])
        emit("00")
        branch("0F 84", reset)
        for world, previous in ((ORIGIN_X, "follow_x"), (ORIGIN_Y, "follow_y")):
            mem("A1", world)
            mem("3B 05", addresses[previous])
            branch("0F 85", reset)
        if check_player:
            for world, previous in ((PLAYER_X, "camera_last_player_x"),
                                    (PLAYER_Y, "camera_last_player_y")):
                mem("A1", world)
                mem("3B 05", addresses[previous])
                branch("0F 85", reset)
        branch("E9", good)

    def remember_legacy():
        mem("A1", ORIGIN_X)
        save("legacy_x")
        mem("A1", ORIGIN_Y)
        save("legacy_y")

    # Rendering entry, also used after the native mover completes.  No display
    # resource access is performed until dimensions have been validated.
    label("ensure")
    emit("9C 60")
    mem("83 3D", addresses["camera_busy"])
    emit("00")
    branch("0F 85", "ensure_return")
    const("camera_busy", 1)
    mem("89 0D", addresses["view"])
    dimensions("ensure_invalid")
    # A changed origin, or a position changed outside the mover (load/teleport),
    # invalidates the saved native camera.  The mover exit marks its own origin
    # as accepted before calling us and keeps camera_moving nonzero.
    mem("83 3D", addresses["camera_moving"])
    emit("00")
    branch("0F 85", "ensure_in_motion")
    reset_check("ensure_reset", "ensure_compute", True)
    label("ensure_in_motion")
    reset_check("ensure_reset", "ensure_compute", False)
    label("ensure_reset")
    remember_legacy()
    label("ensure_compute")
    load("width")
    emit("D1 E8 8B C8")
    mem("A1", PLAYER_X)
    emit("2B C1 85 C0")
    branch("0F 89", "ensure_x_nonnegative")
    emit("05 40 9C 00 00")
    label("ensure_x_nonnegative")
    emit("3D 40 9C 00 00")
    branch("0F 8C", "ensure_x_wrapped")
    emit("2D 40 9C 00 00")
    label("ensure_x_wrapped")
    emit("8B D0 83 E2 0F")
    mem("89 15", addresses["camera_rx"])
    emit("C1 F8 04")
    save("camera_x")
    load("height")
    emit("8B D0 D1 E8 8B C8")
    mem("A1", PLAYER_Y)
    emit("2B C1 85 C0")
    branch("0F 89", "ensure_y_nonnegative")
    emit("33 C0")
    label("ensure_y_nonnegative")
    emit("B9 20 4E 00 00 2B CA 3B C1")
    branch("0F 8E", "ensure_y_clamped")
    emit("8B C1")
    label("ensure_y_clamped")
    emit("8B D0 83 E2 0F")
    mem("89 15", addresses["camera_ry"])
    emit("C1 F8 04")
    save("camera_y")
    load("camera_x")
    mem("3B 05", ORIGIN_X)
    branch("0F 85", "ensure_refresh")
    load("camera_y")
    mem("3B 05", ORIGIN_Y)
    branch("0F 84", "ensure_commit")
    label("ensure_refresh")
    # Preserve per-NPC state/frame across a visibility-only rebuild.  Native
    # edge-scroll resets have already occurred and retain their native effect.
    emit("FC BE")
    u32(NPC_RECORDS)
    emit("BF")
    u32(addresses["npc_snapshot"])
    emit("B9 30 00 00 00 F3 A5")
    branch("E8", "rebase")
    mem("8B 0D", addresses["view"])
    call(0x426790)
    emit("BE")
    u32(NPC_RECORDS)
    emit("BB 10 00 00 00")
    label("npc_outer")
    emit("8B 06 85 C0")
    branch("0F 88", "npc_next")
    emit("BF")
    u32(addresses["npc_snapshot"])
    emit("B9 10 00 00 00")
    label("npc_find")
    emit("3B 07")
    branch("0F 84", "npc_found")
    emit("83 C7 0C 49")
    branch("0F 85", "npc_find")
    branch("E9", "npc_next")
    label("npc_found")
    emit("8B 57 04 89 56 04 8B 57 08 89 56 08")
    label("npc_next")
    emit("83 C6 0C 4B")
    branch("0F 85", "npc_outer")
    label("ensure_commit")
    for source, dest in (("camera_x", "follow_x"), ("camera_y", "follow_y"),
                         ("camera_rx", "rx"), ("camera_ry", "ry")):
        load(source)
        save(dest)
    mem("A1", PLAYER_X)
    save("camera_last_player_x")
    mem("A1", PLAYER_Y)
    save("camera_last_player_y")
    const("valid", 1)
    branch("E9", "ensure_done")
    label("ensure_invalid")
    const("valid", 0)
    const("rx", 0)
    const("ry", 0)
    label("ensure_done")
    const("camera_busy", 0)
    label("ensure_return")
    emit("61 9D C3")

    # Rebase persistent world animation points into candidate tile-camera
    # coordinates.  Fractional pixels remain a render-only transformation.
    label("rebase")
    load("camera_x")
    mem("2B 05", ORIGIN_X)
    emit("3D E2 04 00 00")
    branch("0F 8E", "rebase_x_low")
    emit("2D C4 09 00 00")
    label("rebase_x_low")
    emit("3D 1E FB FF FF")
    branch("0F 8D", "rebase_x_done")
    emit("05 C4 09 00 00")
    label("rebase_x_done")
    emit("8B D0")
    load("camera_y")
    mem("2B 05", ORIGIN_Y)
    emit("50 52 B9 40 68 5B 00")
    call(0x489360)
    load("camera_x")
    mem("A3", ORIGIN_X)
    load("camera_y")
    mem("A3", ORIGIN_Y)
    emit("C3")

    label("move_enter")
    emit("9C 60")
    mem("FF 05", addresses["camera_moving"])
    mem("83 3D", addresses["camera_moving"])
    emit("01")
    branch("0F 85", "move_enter_return")
    const("camera_tracking", 0)
    dimensions("move_enter_return")
    mem("83 3D", addresses["camera_defer"])
    emit("00")
    branch("0F 84", "move_enter_check")
    mem("83 3D", addresses["camera_defer_started"])
    emit("00")
    branch("0F 85", "move_enter_tracked")
    const("camera_defer_started", 1)
    label("move_enter_check")
    reset_check("move_enter_reset", "move_enter_ready", True)
    label("move_enter_reset")
    remember_legacy()
    label("move_enter_ready")
    for source, dest in (("legacy_x", "camera_x"), ("legacy_y", "camera_y")):
        load(source)
        save(dest)
    branch("E8", "rebase")
    label("move_enter_tracked")
    const("camera_tracking", 1)
    label("move_enter_return")
    emit("61 9D 8B 44 24 24 99")
    emit("E9")
    code.extend(struct.pack("<i", 0x47D0EA - (base + len(code) + 4)))

    label("move_exit")
    emit("9C 60")
    mem("83 3D", addresses["camera_moving"])
    emit("01")
    branch("0F 85", "move_exit_finish")
    mem("83 3D", addresses["camera_tracking"])
    emit("00")
    branch("0F 84", "move_exit_finish")
    remember_legacy()
    mem("A1", ORIGIN_X)
    save("follow_x")
    mem("A1", ORIGIN_Y)
    save("follow_y")
    const("valid", 1)
    mem("A1", PLAYER_X)
    save("camera_last_player_x")
    mem("A1", PLAYER_Y)
    save("camera_last_player_y")
    mem("83 3D", addresses["camera_defer"])
    emit("00")
    branch("0F 85", "move_exit_finish")
    mem("8B 0D", addresses["view"])
    emit("85 C9")
    branch("0F 85", "move_exit_view")
    emit("B9")
    u32(VIEW)
    label("move_exit_view")
    branch("E8", "ensure")
    label("move_exit_finish")
    mem("FF 0D", addresses["camera_moving"])
    emit("61 9D 5D 5F 5E 5B 83 C4 1C E9")
    code.extend(struct.pack("<i", 0x47D444 - (base + len(code) + 4)))

    label("mouse_sailing")
    emit("9C 50")
    load("rx")
    emit("01 45 DC")
    load("ry")
    emit("01 45 E0 58 9D B9 A0 60 5B 00 E9")
    code.extend(struct.pack("<i", 0x48ECF4 - (base + len(code) + 4)))

    label("mouse_adjacent")
    emit("9C 50")
    load("rx")
    emit("01 44 24 20")  # original ESP+18h, adjusted for pushfd/push eax
    load("ry")
    emit("01 44 24 24 58 9D 8B 44 24 18 8D 4C 24 2C E9")
    code.extend(struct.pack("<i", 0x48BAAE - (base + len(code) + 4)))

    for offset, target in fixups:
        struct.pack_into("<i", code, offset, labels[target] - offset - 4)
    if ENSURE_FOLLOW_OFFSET + len(code) > 0x1800:
        raise AssertionError("월드 지도 중앙 추적 카메라 코드가 예약 공간을 초과했습니다.")

    def hook(va: int, original: str, target: str):
        before = bytes.fromhex(original)
        after = b"\xE9" + struct.pack("<i", base + labels[target] - va - 5)
        return va, before, after + b"\x90" * (len(before) - len(after))

    hooks = [
        hook(0x47D0E5, "8B 44 24 24 99", "move_enter"),
        hook(0x47D43D, "5D 5F 5E 5B 83 C4 1C", "move_exit"),
        hook(0x48ECEF, "B9 A0 60 5B 00", "mouse_sailing"),
        hook(0x48BAA6, "8B 44 24 18 8D 4C 24 2C", "mouse_adjacent"),
    ]
    return {ENSURE_FOLLOW_OFFSET: bytes(code)}, hooks

# Frozen v5 render generator.

"""Render the fractional part of the world-map following camera.

The native tile and occlusion caches keep their original dimensions.  Only
the tile loops gain partial edge rows/columns; those cells never dereference
either cache.  DirectDraw and software drawing share the same camera offset.
Stationary frames retain native dirty-cell caching.  On compatible 8-bit DD
surfaces the background is copied from the native CPU atlas under one lock;
all actor, effect and HUD drawing runs after the surface is unlocked.
"""


import struct


RENDER_OFFSET = 0x1800
RENDER_LIMIT = 0x2800
SOFTWARE_ORIGIN = (0x62B2C8, 0x62B2CC, 0x62B870, 0x62B874)
SOFTWARE_CLIP = (0x62B828, 0x62B82C, 0x62B830, 0x62B834)
MAP_SURFACE = 0x569FF0


class _RenderCodeV5:
    def __init__(self, va: int):
        self.va = va
        self.data = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str]] = []

    def emit(self, value: str) -> None:
        self.data.extend(bytes.fromhex(value))

    def u32(self, value: int) -> None:
        self.data.extend(struct.pack("<I", value))

    def mem(self, opcode: str, address: int) -> None:
        self.emit(opcode)
        self.u32(address)

    def label(self, name: str) -> None:
        self.labels[name] = len(self.data)

    def branch(self, opcode: str, target: str) -> None:
        self.emit(opcode)
        self.fixups.append((len(self.data), target))
        self.u32(0)

    def to(self, opcode: str, address: int) -> None:
        self.emit(opcode)
        self.data.extend(struct.pack("<i", address - self.va - len(self.data) - 4))

    def finish(self) -> bytes:
        for offset, name in self.fixups:
            struct.pack_into("<i", self.data, offset, self.labels[name] - offset - 4)
        return bytes(self.data)


def build_render(
    slot_va: int, state: dict[str, int]
) -> tuple[dict[int, bytes], list[tuple[int, bytes, bytes]]]:
    """Return payload chunks and verified-site hooks without modifying an EXE.

    ``state`` maps names to writable absolute addresses, except ensure_follow,
    which is a function VA.  It may allocate missing names through __missing__.
    Required common names are rx, ry, width, height, active, valid, and view. The
    ensure_follow helper accepts ECX=view and preserves all registers/flags.
    Additional saved state uses the render_ prefix.  Persistent camera rx/ry
    remain available after rendering for inverse mouse coordinate conversion.

    Native map origin is unchanged outside the rendering interval.  In
    particular, the city-name hook and its validation bytes at 48AB38..44 are
    not touched.  Hooks restore drawing origins on both following branches.
    """
    c = _RenderCodeV5(slot_va + RENDER_OFFSET)
    hooks: list[tuple[int, bytes, bytes]] = []
    descriptor = [state[f"render_lock_desc_{i}"] for i in range(27)]
    if descriptor != list(range(descriptor[0], descriptor[0] + 0x6C, 4)):
        raise AssertionError("DirectDraw 잠금 설명자가 연속된 실행 공간에 없습니다.")
    desc = descriptor[0]
    cloud_rectangles = [state[f"render_cloud_rect_{i}"] for i in range(8)]
    if cloud_rectangles != list(range(cloud_rectangles[0], cloud_rectangles[0] + 32, 4)):
        raise AssertionError("구름 사각형이 연속된 실행 공간에 없습니다.")
    cloud_src, cloud_dst = cloud_rectangles[0], cloud_rectangles[4]

    def const(name: str, value: int) -> None:
        c.mem("C7 05", state[name])
        c.u32(value)

    # Values identifying the existing pixels, before render-only translation.
    frame_values = (
        ("view", state["view"]), ("width", state["width"]),
        ("height", state["height"]), ("rx", state["rx"]),
        ("ry", state["ry"]), ("camera_x", 0x5B63A8),
        ("camera_y", 0x5B63AC), ("surface", MAP_SURFACE),
        ("dd_mode", state["render_dd_mode"]),
    )

    def hook(va: int, original: str, label: str, *, call: bool = False) -> None:
        before = bytes.fromhex(original)
        if len(before) < 5:
            raise AssertionError("A renderer hook must cover whole instructions.")
        target = c.va + c.labels[label]
        opcode = b"\xe8" if call else b"\xe9"
        after = opcode + struct.pack("<i", target - va - 5)
        hooks.append((va, before, after + b"\x90" * (len(before) - 5)))

    # Only the visible-world branch reaches here.  Save registers while the
    # camera helper recentres and the drawing coordinate systems are adjusted.
    c.label("enter")
    c.emit("9C 60 8B 4C 24 38")  # original [esp+14] view; pushfd+pushad=36
    c.to("E8", state["ensure_follow"])
    c.branch("E8", "activate")
    c.emit("61 9D C7 44 24 24 00 00 00 00")
    c.to("E9", 0x48A2B1)
    hook(0x48A2A9, "C7 44 24 24 00 00 00 00", "enter")

    c.label("hidden_map")
    const("render_previous_valid", 0)
    c.emit("6A 00 33 F6 6A 49")
    c.to("E9", 0x48A226)
    hook(0x48A220, "6A 00 33 F6 6A 49", "hidden_map")

    # City labels are invoked at 48AB38.  Leave that hook and its following JE
    # untouched, and restore on each target before drawing fixed HUD elements.
    c.label("exit_overlay")
    c.emit("9C 60")
    c.branch("E8", "restore")
    c.emit("61 9D 8B 44 24 68 8D 8C 24 90 00 00 00")
    c.to("E9", 0x48AB50)
    hook(0x48AB45, "8B 44 24 68 8D 8C 24 90 00 00 00", "exit_overlay")
    c.label("exit_hud")
    c.emit("9C 60")
    c.branch("E8", "restore")
    c.emit("61 9D F6 05 1A 4D 5A 00 02")
    c.to("E9", 0x48AB77)
    hook(0x48AB70, "F6 05 1A 4D 5A 00 02", "exit_hud")

    # Includes the undisclosed-map early return, where active remains zero.
    c.label("exit")
    c.emit("9C 60")
    c.branch("E8", "restore")
    c.emit("61 9D 5D 5F 5E 5B 81 C4 80 01 00 00")
    c.to("E9", 0x48AC27)
    hook(0x48AC1D, "5D 5F 5E 5B 81 C4 80 01 00 00", "exit")

    c.label("activate")
    c.mem("83 3D", state["valid"])
    c.emit("00")
    c.branch("0F 84", "activate_done")
    c.mem("83 3D", state["active"])
    c.emit("00")
    c.branch("0F 85", "activate_done")
    for name in ("width", "height"):
        c.mem("83 3D", state[name])
        c.emit("00")
        c.branch("0F 8E", "activate_done")
    for name in ("rx", "ry"):
        c.mem("83 3D", state[name])
        c.emit("0F")
        c.branch("0F 87", "activate_done")
    c.mem("8B 0D", state["view"])
    c.emit("85 C9")
    c.branch("0F 84", "activate_done")
    c.mem("89 0D", state["render_view"])
    c.branch("E8", "classify_frame")
    c.mem("8B 0D", state["render_view"])
    for field, axis in ((0x54, "rx"), (0x58, "ry")):
        c.emit(f"8B 41 {field:02X}")
        c.mem("A3", state[f"render_view_{field:x}"])
        c.mem("2B 05", state[axis])
        c.emit(f"89 41 {field:02X}")
    for index, address in enumerate(SOFTWARE_ORIGIN):
        c.mem("A1", address)
        c.mem("A3", state[f"render_origin_{index}"])
        c.mem("2B 05", state["rx" if index % 2 == 0 else "ry"])
        c.mem("A3", address)
    # Software blitters use an absolute screen clip.  Intersect the caller's
    # existing clip with the unshifted map viewport, so the extra cells cannot
    # draw into the status bar or surrounding windows.
    for index, address in enumerate(SOFTWARE_CLIP):
        c.mem("A1", address)
        c.mem("A3", state[f"render_clip_{index}"])
        c.mem("8B 15", state[f"render_origin_{index % 2 + 2}"])
        if index >= 2:
            c.mem("03 15", state["width" if index == 2 else "height"])
        c.emit("3B C2")
        c.branch("0F 8D" if index < 2 else "0F 8E", f"clip_saved_{index}")
        c.mem("89 15", address)
        c.label(f"clip_saved_{index}")
    c.mem("C7 05", state["active"])
    c.u32(1)
    c.branch("E8", "batch_begin")
    c.label("activate_done")
    c.emit("C3")

    c.label("restore")
    c.mem("83 3D", state["active"])
    c.emit("00")
    c.branch("0F 84", "restore_done")
    c.branch("E8", "batch_end")
    c.branch("E8", "remember_frame")
    c.mem("8B 0D", state["render_view"])
    for field in (0x54, 0x58):
        c.mem("A1", state[f"render_view_{field:x}"])
        c.emit(f"89 41 {field:02X}")
    for index, address in enumerate(SOFTWARE_ORIGIN):
        c.mem("A1", state[f"render_origin_{index}"])
        c.mem("A3", address)
    for index, address in enumerate(SOFTWARE_CLIP):
        c.mem("A1", state[f"render_clip_{index}"])
        c.mem("A3", address)
    c.mem("C7 05", state["active"])
    c.u32(0)
    c.label("restore_done")
    c.emit("C3")

    c.label("classify_frame")
    const("render_force_redraw", 1)
    const("render_frame_failed", 0)
    const("render_unlock_failed", 0)
    const("render_dd_mode", 0)
    c.emit("F6 05 1D 4D 5A 00 01")
    c.branch("0F 84", "classify_compare")
    c.emit("F6 05 1A 4D 5A 00 02")
    c.branch("0F 84", "classify_compare")
    c.emit("F6 81 A0 00 00 00 01")
    c.branch("0F 85", "classify_compare")
    const("render_dd_mode", 1)
    c.label("classify_compare")
    c.mem("83 3D", state["render_previous_valid"])
    c.emit("00")
    c.branch("0F 84", "classify_done")
    for name, address in frame_values:
        c.mem("A1", address)
        c.mem("3B 05", state[f"render_previous_{name}"])
        c.branch("0F 85", "classify_done")
    for field in (0x54, 0x58):
        c.emit(f"8B 41 {field:02X}")
        c.mem("3B 05", state[f"render_previous_origin_{field:x}"])
        c.branch("0F 85", "classify_done")
    const("render_force_redraw", 0)
    c.label("classify_done")
    c.emit("C3")

    c.label("remember_frame")
    const("render_previous_valid", 0)
    c.mem("83 3D", state["render_frame_failed"])
    c.emit("00")
    c.branch("0F 85", "remember_done")
    for name, address in frame_values:
        c.mem("A1", address)
        c.mem("A3", state[f"render_previous_{name}"])
    for field in (0x54, 0x58):
        c.mem("A1", state[f"render_view_{field:x}"])
        c.mem("A3", state[f"render_previous_origin_{field:x}"])
    const("render_previous_valid", 1)
    c.label("remember_done")
    c.emit("C3")

    # Native 48A069 obtains the flat 16384 * 256-byte CPU tile atlas through
    # view+B0 and copies its 8-bit indices into the DD atlas.  Reusing those
    # same bytes avoids one COM Blt for every visible 16x16 background tile.
    # Only the map surface is held; the engine's software surface is distinct.
    c.label("batch_begin")
    const("render_locked", 0)
    c.mem("83 3D", state["render_dd_mode"])
    c.emit("00")
    c.branch("0F 84", "batch_begin_done")
    c.mem("8B 0D", state["view"])
    c.emit("81 B9 B4 00 00 00 00 00 40 00")
    c.branch("0F 82", "batch_begin_done")
    c.mem("A1", MAP_SURFACE)
    c.emit("85 C0")
    c.branch("0F 84", "batch_begin_done")
    c.mem("3B 05", 0x62D36C)
    c.branch("0F 84", "batch_begin_done")
    c.mem("A3", state["render_lock_surface"])
    c.emit("6A 04 6A 00 6A 00 81 C1 B0 00 00 00")
    c.to("E8", 0x4B6637)
    c.emit("85 C0")
    c.branch("0F 84", "batch_begin_done")
    c.emit("8B D0 81 C2 00 00 40 00")
    c.branch("0F 82", "batch_begin_done")
    c.mem("A3", state["render_atlas"])
    const("render_lock_attempts", 0)
    c.label("lock_try")
    c.emit("FC 33 C0 BF")
    c.u32(desc)
    c.emit("B9 1B 00 00 00 F3 AB")
    c.mem("C7 05", desc)
    c.u32(0x6C)
    # Same Lock(self,NULL,&DDSURFACEDESC,0,NULL) ABI as native 48A025.
    c.emit("6A 00 6A 00 68")
    c.u32(desc)
    c.emit("6A 00")
    c.mem("A1", state["render_lock_surface"])
    c.emit("50 8B 08 FF 51 64 85 C0")
    c.branch("0F 84", "lock_validate")
    const("render_force_redraw", 1)
    c.emit("3D C2 01 76 88")
    c.branch("0F 85", "lock_failed")
    c.mem("83 3D", state["render_lock_attempts"])
    c.emit("00")
    c.branch("0F 85", "lock_failed")
    const("render_lock_attempts", 1)
    c.mem("A1", state["render_lock_surface"])
    c.emit("50 8B 08 FF 51 6C 85 C0")
    c.branch("0F 84", "lock_try")
    c.label("lock_failed")
    const("render_frame_failed", 1)
    c.branch("E9", "batch_begin_done")
    c.label("lock_validate")
    const("render_locked", 1)
    # Fail closed for non-paletted formats, undersized surfaces or bad pitch.
    c.mem("83 3D", desc + 0x48)
    c.emit("20")
    c.branch("0F 85", "lock_unsupported")
    c.mem("F6 05", desc + 0x4C)
    c.emit("20")
    c.branch("0F 84", "lock_unsupported")
    c.mem("83 3D", desc + 0x54)
    c.emit("08")
    c.branch("0F 85", "lock_unsupported")
    c.mem("A1", desc + 0x0C)
    c.mem("3B 05", state["width"])
    c.branch("0F 82", "lock_unsupported")
    c.emit("3D 00 00 01 00")
    c.branch("0F 87", "lock_unsupported")
    c.mem("8B 15", desc + 0x10)
    c.emit("3B D0")
    c.branch("0F 82", "lock_unsupported")
    c.emit("81 FA 00 00 10 00")
    c.branch("0F 87", "lock_unsupported")
    c.mem("89 15", state["render_lock_pitch"])
    c.mem("A1", state["height"])
    c.emit("83 C0 20")
    c.mem("3B 05", desc + 8)
    c.branch("0F 87", "lock_unsupported")
    c.emit("F7 E2 85 D2")  # high dword of (height+32)*pitch must be zero
    c.branch("0F 85", "lock_unsupported")
    c.mem("03 05", desc + 0x24)
    c.branch("0F 82", "lock_unsupported")
    c.mem("A1", desc + 0x24)
    c.emit("85 C0")
    c.branch("0F 84", "lock_unsupported")
    c.mem("A3", state["render_lock_pixels"])
    c.branch("E9", "batch_begin_done")
    c.label("lock_unsupported")
    c.branch("E8", "batch_end")
    c.label("batch_begin_done")
    c.emit("C3")

    c.label("batch_end")
    c.mem("83 3D", state["render_locked"])
    c.emit("00")
    c.branch("0F 84", "batch_end_done")
    const("render_locked", 0)
    c.mem("A1", state["render_lock_surface"])
    c.mem("FF 35", desc + 0x24)
    c.emit("50 8B 08 FF 91 80 00 00 00 85 C0")
    c.branch("0F 84", "batch_end_done")
    const("render_frame_failed", 1)
    const("render_unlock_failed", 1)
    # Native graphics recovery also uses Restore(+6C).  Do not issue actor
    # blits in this frame after an unsuccessful unlock; repaint next frame.
    c.mem("A1", state["render_lock_surface"])
    c.emit("50 8B 08 FF 51 6C")
    c.label("batch_end_done")
    c.emit("C3")

    c.label("tiles_complete")
    c.emit("9C 60")
    c.branch("E8", "batch_end")
    c.emit("61 9D")
    c.mem("83 3D", state["active"])
    c.emit("00")
    c.branch("0F 84", "actors_begin")
    c.mem("83 3D", state["render_unlock_failed"])
    c.emit("00")
    c.to("0F 85", 0x48AB38)
    c.label("actors_begin")
    c.emit("33 F6")
    c.label("npc_iteration")
    c.emit("56")
    c.to("E8", 0x47E470)
    c.to("E9", 0x48A6F5)
    hook(0x48A6ED, "33 F6 56 E8 7B 3D FF FF", "tiles_complete")
    # The native NPC loop jumps back into the overwritten PUSH/CALL sequence;
    # route subsequent iterations directly to its relocated copy, not Unlock.
    c.label("npc_repeat")
    c.branch("0F 8C", "npc_iteration")
    c.to("E9", 0x48A7FF)
    hook(0x48A7F9, "0F 8C F0 FE FF FF", "npc_repeat")

    # Ocean-current animation is selected inside the tile loop, not by a
    # separate sprite. Native phase uses viewport column/row and therefore
    # changes an unchanged world tile when the camera crosses a tile boundary.
    # The loop already retains the looked-up world X (wrapped) and world Y.
    # Anchor only the visual phase to those coordinates; current direction,
    # strength, animation tick and the native signed phase arithmetic stay
    # untouched. Inactive rendering preserves the original local operands.
    for label, va, before, world_operand, continuation in (
        ("current_x", 0x48A518, "0F AF 54 24 1C", "0F AF 54 24 58", 0x48A51D),
        ("current_y", 0x48A525, "0F AF 44 24 18", "0F AF 44 24 54", 0x48A52A),
    ):
        c.label(label)
        c.mem("83 3D", state["active"])
        c.emit("00")
        c.branch("0F 84", label + "_native")
        c.emit(world_operand)
        c.to("E9", continuation)
        c.label(label + "_native")
        c.emit(before)
        c.to("E9", continuation)
        hook(va, before, label)

    # Preserve the allocated row stride.  Pointer arithmetic at an edge can
    # produce a one-past pointer, but neither cache/mask is read there.
    c.label("tile_read")
    c.emit("9C 60")
    c.mem("83 3D", state["active"])
    c.emit("00")
    c.branch("0F 84", "tile_read_native")
    c.emit("8B 4C 24 38 8B 44 24 40 3B 81 EC 00 00 00")
    c.branch("0F 8D", "tile_read_edge")
    c.emit("8B 44 24 3C 3B 81 F0 00 00 00")
    c.branch("0F 8D", "tile_read_edge")
    c.mem("83 3D", state["render_force_redraw"])
    c.emit("00")
    c.branch("0F 84", "tile_read_native")
    c.emit("61 9D")
    c.to("E9", 0x48A572)
    c.label("tile_read_edge")
    c.emit("61 9D")
    c.to("E9", 0x48A57F)
    c.label("tile_read_native")
    c.emit("61 9D 83 BC 24 94 01 00 00 00")
    c.to("E9", 0x48A55E)
    hook(0x48A556, "83 BC 24 94 01 00 00 00", "tile_read")

    c.label("tile_write")
    c.emit("9C 60")
    c.mem("83 3D", state["active"])
    c.emit("00")
    c.branch("0F 84", "tile_write_native")
    c.emit("8B 4C 24 38 8B 44 24 40 3B 81 EC 00 00 00")
    c.branch("0F 8D", "tile_write_edge")
    c.emit("8B 44 24 3C 3B 81 F0 00 00 00")
    c.branch("0F 8D", "tile_write_edge")
    c.label("tile_write_native")
    c.emit("61 9D 66 8B 4C 24 12 8B 44 24 38 66 89 08")
    c.to("E9", 0x48A69D)
    c.label("tile_write_edge")
    c.emit("61 9D")
    c.to("E9", 0x48A69D)
    hook(0x48A691, "66 8B 4C 24 12 8B 44 24 38 66 89 08", "tile_write")

    c.label("column_loop")
    c.emit("52 8B 91 EC 00 00 00")
    c.mem("83 3D", state["active"])
    c.emit("00")
    c.branch("0F 84", "column_compare")
    c.mem("8B 15", state["width"])
    c.mem("03 15", state["rx"])
    c.emit("83 C2 0F C1 EA 04")
    c.label("column_compare")
    c.emit("3B D0 5A")
    c.to("0F 8F", 0x48A3E4)
    c.to("E9", 0x48A6CD)
    hook(0x48A6C1, "39 81 EC 00 00 00 0F 8F 17 FD FF FF", "column_loop")

    c.label("row_loop")
    c.emit("50 8B 81 F0 00 00 00")
    c.mem("83 3D", state["active"])
    c.emit("00")
    c.branch("0F 84", "row_compare")
    c.mem("A1", state["height"])
    c.mem("03 05", state["ry"])
    c.emit("83 C0 0F C1 E8 04")
    c.label("row_compare")
    c.emit("3B C2 58")
    c.to("0F 8F", 0x48A34E)
    c.to("E9", 0x48A6ED)
    hook(0x48A6E1, "39 91 F0 00 00 00 0F 8F 61 FC FF FF", "row_loop")

    # These small adapters replace complete instructions around each native
    # COM call.  The shared helper consumes the original six stdcall arguments.
    c.label("ship_blt")
    c.emit("50")
    c.branch("E8", "ddraw_blt")
    c.to("E9", 0x48AD37)
    hook(0x48AD31, "8B 18 50 FF 53 14", "ship_blt")
    c.label("effect_blt")
    c.branch("E8", "ddraw_blt")
    c.emit("8B E8")
    c.to("E9", 0x49A799)
    hook(0x49A794, "FF 50 14 8B E8", "effect_blt")
    c.label("effect_row_blt")
    c.emit("50")
    c.branch("E8", "ddraw_blt")
    c.to("E9", 0x49A8AB)
    hook(0x49A8A5, "8B 28 50 FF 55 14", "effect_row_blt")

    # The six ordinary clouds have their own Blt, separate from the rain/snow
    # effect engine above.  Their POINTs already follow the integer camera;
    # 489360 rebases them by tileDelta*16, but never by fractional rx/ry.
    # Preserve both full rectangles BEFORE native 4892F0 clips them. Applying
    # the fractional translation to its already-clipped result would lose a
    # right/bottom strip, or entirely omit a cloud just outside the raw view.
    # The native rectangles stay intact for retries and dirty-cell marking.
    c.label("cloud_capture")
    c.emit("9C 60")
    const("render_cloud_captured", 0)
    c.mem("83 3D", state["active"])
    c.emit("00")
    c.branch("0F 84", "cloud_capture_done")
    c.emit("8B 84 24 DC 00 00 00")  # native cloud frame+AC: target surface
    c.mem("3B 05", MAP_SURFACE)
    c.branch("0F 85", "cloud_capture_done")
    for arg, target in ((0x28, cloud_src), (0x2C, cloud_dst)):
        c.emit(f"8B 54 24 {arg:02X}")
        for index in range(4):
            c.emit("8B 02" if index == 0 else f"8B 42 {index * 4:02X}")
            if target == cloud_dst and index % 2:
                c.emit("83 C0 20")  # native Blt adds the fixed map top
            c.mem("A3", target + index * 4)
    const("render_cloud_captured", 1)
    c.label("cloud_capture_done")
    c.emit("61 9D")
    c.to("E9", 0x4892F0)  # exact cdecl tail call, preserving both arguments
    hook(0x4891C0, "E8 2B 01 00 00", "cloud_capture", call=True)

    c.label("cloud_blt")
    c.mem("83 3D", state["active"])
    c.emit("00")
    c.branch("0F 84", "cloud_blt_native")
    c.mem("83 3D", state["render_cloud_captured"])
    c.emit("00")
    c.branch("0F 84", "cloud_blt_native")
    c.emit("8B 04 24")
    c.mem("3B 05", MAP_SURFACE)
    c.branch("0F 85", "cloud_blt_native")
    c.emit("C7 44 24 04")
    c.u32(cloud_dst)
    c.emit("C7 44 24 0C")
    c.u32(cloud_src)
    c.label("cloud_blt_native")
    c.branch("E8", "ddraw_blt")
    c.emit("8B E8")
    c.to("E9", 0x4891F9)
    hook(0x4891F3, "FF 54 24 48 8B E8", "cloud_blt")

    # Native cloud invalidation can index a partial screen cell beyond its
    # floor(width/16)*floor(height/16) allocation. Keep its logical (unshifted)
    # rectangle, since that is the coordinate system used by the tile cache,
    # and bound each write by the original row/column counts. Extra cells have
    # no cache and the renderer already repaints them on every frame.
    c.label("cloud_cache_write")
    c.emit("9C")
    c.mem("83 3D", state["active"])
    c.emit("00")
    c.branch("0F 84", "cloud_cache_native")
    c.emit("50 8B 84 24 B4 00 00 00")  # native frame+AC after two pushes
    c.mem("3B 05", MAP_SURFACE)
    c.emit("58")
    c.branch("0F 85", "cloud_cache_native")
    c.emit("85 C9")
    c.branch("0F 88", "cloud_cache_skip")
    c.emit("85 FF")
    c.branch("0F 88", "cloud_cache_skip")
    c.emit("3B 8C 24 B8 00 00 00")  # ECX column, native frame+B4 count
    c.branch("0F 8D", "cloud_cache_skip")
    c.emit("3B BC 24 BC 00 00 00")  # EDI row, native frame+B8 count
    c.branch("0F 8D", "cloud_cache_skip")
    c.label("cloud_cache_native")
    c.emit("66 C7 45 00 FF FF")
    c.label("cloud_cache_skip")
    c.emit("9D")
    c.to("E9", 0x48928B)
    hook(0x489285, "66 C7 45 00 FF FF", "cloud_cache_write")

    c.label("ddraw_blt")
    # EBP+8=self,+C=destination RECT,+10=source surface,+14=source RECT,
    # +18=flags,+1C=effects.  Locals -20..-14=destination; -10..-4=source.
    c.emit("55 8B EC 83 EC 20 53 56 57")
    c.mem("83 3D", state["active"])
    c.emit("00")
    c.branch("0F 84", "blt_native")
    c.emit("8B 45 08")
    c.mem("3B 05", MAP_SURFACE)
    c.branch("0F 85", "blt_native")
    c.mem("83 3D", state["render_unlock_failed"])
    c.emit("00")
    c.branch("0F 85", "blt_empty")
    c.emit("8B 75 0C 85 F6")
    c.branch("0F 84", "blt_native")
    c.emit("8B 5D 14 85 DB")
    c.branch("0F 84", "blt_native")
    # Copy first, because native loops retry after restoring lost surfaces.
    for index in range(4):
        src = "06" if index == 0 else f"46 {index * 4:02X}"
        c.emit(f"8B {src} 89 45 {0xE0 + index * 4:02X}")
        src = "03" if index == 0 else f"43 {index * 4:02X}"
        c.emit(f"8B {src} 89 45 {0xF0 + index * 4:02X}")
    c.mem("83 3D", state["weather_screen"])
    c.emit("00")
    c.branch("0F 85", "blt_screen_space")
    for index in range(4):
        c.mem("A1", state["rx" if index % 2 == 0 else "ry"])
        c.emit(f"29 45 {0xE0 + index * 4:02X}")
    c.label("blt_screen_space")
    # Four clipping planes.  Source and destination have matching pixel sizes
    # at all four call sites; clipping must not rescale the remaining image.
    for index in range(4):
        dst = 0xE0 + index * 4
        src = 0xF0 + index * 4
        if index == 0:
            c.emit("33 D2")
        elif index == 1:
            c.emit("BA 20 00 00 00")
        else:
            c.mem("8B 15", state["width" if index == 2 else "height"])
            if index == 3:
                c.emit("83 C2 20")
        c.emit(f"8B 45 {dst:02X} 3B C2")
        c.branch("0F 8D" if index < 2 else "0F 8E", f"blt_clip_{index}")
        c.emit(f"89 55 {dst:02X} 2B D0 01 55 {src:02X}")
        c.label(f"blt_clip_{index}")
    c.emit("8B 45 E0 3B 45 E8")
    c.branch("0F 8D", "blt_empty")
    c.emit("8B 45 E4 3B 45 EC")
    c.branch("0F 8D", "blt_empty")
    # Only the native background tile call is eligible. Ships and animations
    # use the same clip helper but must remain ordinary transparent DD blits.
    c.mem("83 3D", state["render_locked"])
    c.emit("00")
    c.branch("0F 84", "blt_clipped")
    c.emit("81 7D 04 F6 A5 48 00")
    c.branch("0F 85", "blt_clipped")
    c.emit("8B 55 E8 2B 55 E0 83 FA 10")
    c.branch("0F 87", "copy_unsupported")
    c.emit("8B 5D EC 2B 5D E4 83 FB 10")
    c.branch("0F 87", "copy_unsupported")
    c.emit("8B 45 F8 2B 45 F0 3B C2")
    c.branch("0F 85", "copy_unsupported")
    c.emit("8B 45 FC 2B 45 F4 3B C3")
    c.branch("0F 85", "copy_unsupported")
    c.emit("8B 45 F0 3D 00 02 00 00")
    c.branch("0F 83", "copy_unsupported")
    c.emit("8B C8 83 E1 0F 03 CA 83 F9 10")
    c.branch("0F 87", "copy_unsupported")
    c.emit("8B 45 F4 3D 00 20 00 00")
    c.branch("0F 83", "copy_unsupported")
    c.emit("8B C8 83 E1 0F 03 CB 83 F9 10")
    c.branch("0F 87", "copy_unsupported")
    # Flat atlas layout: tileID*256 + withinTileY*16 + withinTileX.
    c.emit("8B C8 83 E1 0F C1 E1 04 C1 E8 04 C1 E0 05 8B F0")
    c.emit("8B 45 F0 8B F8 83 E7 0F 03 CF C1 E8 04 03 F0 C1 E6 08 03 F1")
    c.mem("03 35", state["render_atlas"])
    c.emit("8B 45 E4")
    c.mem("0F AF 05", state["render_lock_pitch"])
    c.emit("03 45 E0")
    c.mem("03 05", state["render_lock_pixels"])
    c.emit("8B F8 FC")
    c.label("copy_row")
    c.emit("8B CA F3 A4 83 C6 10 2B F2")
    c.mem("03 3D", state["render_lock_pitch"])
    c.emit("2B FA 4B")
    c.branch("0F 85", "copy_row")
    c.branch("E9", "blt_empty")  # success HRESULT 0, with no COM call
    c.label("copy_unsupported")
    c.branch("E8", "batch_end")
    c.mem("83 3D", state["render_unlock_failed"])
    c.emit("00")
    c.branch("0F 85", "blt_empty")
    c.label("blt_clipped")
    c.emit("FF 75 1C FF 75 18 8D 45 F0 50 FF 75 10 8D 45 E0 50")
    c.branch("E9", "blt_call")
    c.label("blt_native")
    c.emit("FF 75 1C FF 75 18 FF 75 14 FF 75 10 FF 75 0C")
    c.label("blt_call")
    c.emit("8B 45 08 50 8B 08 FF 51 14")
    c.emit("85 C0")
    c.branch("0F 84", "blt_done")
    c.mem("83 3D", state["active"])
    c.emit("00")
    c.branch("0F 84", "blt_done")
    c.emit("8B 4D 08")
    c.mem("3B 0D", MAP_SURFACE)
    c.branch("0F 85", "blt_done")
    const("render_frame_failed", 1)
    c.branch("E9", "blt_done")
    c.label("blt_empty")
    c.emit("33 C0")
    c.label("blt_done")
    c.emit("5F 5E 5B 8B E5 5D C2 18 00")
    hook(0x48A5F1, "8B 18 FF 53 14", "ddraw_blt", call=True)

    payload = c.finish()
    if RENDER_OFFSET + len(payload) > RENDER_LIMIT:
        raise AssertionError("월드 지도 렌더링 코드가 예약 공간을 초과했습니다.")
    return {RENDER_OFFSET: payload}, hooks

# Frozen v5 weather generator.

"""Replay the last rain/snow draw without advancing native weather or RNG.

The normal 49ABB0 call retains its complete resource/simulation lifecycle.
Only its world-map call sites record raw x/y/surface/frame/size commands.
Extra camera frames replay the three native drawing/cache helpers, never the
effect manager, particle update, initializer, destructor, or random sampler.
"""


import struct


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
    c = _RenderCodeV5(slot_va + WEATHER_OFFSET)
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

# Frozen v5 smooth generator.

"""Render-only interpolation between the original 100 ms world simulation ticks.

The shared input poll is an exact tail call outside the world loop's wait.
Inside that wait it can publish event 4; the world-loop wrapper paints again
without executing encounters, movement, weather advancement, or the calendar.
"""


import struct


SMOOTH_OFFSET = 0x4000
SMOOTH_LIMIT = 0x5800
# GetTickCount commonly advances in alternating 15/16 ms quanta.  Accept
# either quantum without requesting a finer system-wide timer resolution.
FRAME_MS = 15
SEGMENT_MS = 100
PLAYER_X, PLAYER_Y = 0x5B63B0, 0x5B63B4
VIEW_WIDTH, VIEW_HEIGHT = 0x61B32C, 0x61B330
GET_TICK_COUNT = 0x62F448


class _SmoothCodeV5:
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
    c = _SmoothCodeV5(slot_va + SMOOTH_OFFSET)
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
    load("update_time")
    c.mem("2B 05", field("start"))
    c.emit(f"83 F8 {SEGMENT_MS:02X}")
    c.jump("0F 82", "update_elapsed_ready")
    c.emit("B8")
    c.number(SEGMENT_MS)
    c.label("update_elapsed_ready")
    c.emit("8B D8")
    for axis in ("x", "y"):
        load("delta_" + axis)
        c.emit("0F AF C3 99 B9")
        c.number(SEGMENT_MS)
        c.emit("F7 F9")
        c.mem("03 05", field("prev_" + axis))
        if axis == "x":
            c.emit("85 C0")
            c.jump("0F 89", "update_display_x_positive")
            c.emit("05 40 9C 00 00")
            c.label("update_display_x_positive")
            c.emit("3D 40 9C 00 00")
            c.jump("0F 8C", "update_display_x_wrapped")
            c.emit("2D 40 9C 00 00")
            c.label("update_display_x_wrapped")
        save("prev_" + axis)
    c.jump("E9", "update_new_segment")
    c.label("update_from_physical")
    for axis in ("x", "y"):
        load("update_phys_" + axis)
        save("prev_" + axis)
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
    c.mem("0B 05", field("delta_x"))
    c.jump("0F 84", "update_done")
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
    c.emit(f"83 FB {SEGMENT_MS:02X}")
    c.jump("0F 83", "draw_complete")
    for physical, name in ((PLAYER_X, "saved_phys_x"), (PLAYER_Y, "saved_phys_y")):
        c.mem("A1", physical)
        save(name)
    c.mem("A1", state["camera_moving"])
    save("saved_moving")
    c.mem("FF 05", state["camera_moving"])
    set_field("rendering", 1)
    for axis, physical in (("x", PLAYER_X), ("y", PLAYER_Y)):
        load("delta_" + axis)
        c.emit("0F AF C3 99 B9")
        c.number(SEGMENT_MS)
        c.emit("F7 F9")
        c.mem("03 05", field("prev_" + axis))
        if axis == "x":
            c.emit("85 C0")
            c.jump("0F 89", "draw_x_positive")
            c.emit("05 40 9C 00 00")
            c.label("draw_x_positive")
            c.emit("3D 40 9C 00 00")
            c.jump("0F 8C", "draw_x_wrapped")
            c.emit("2D 40 9C 00 00")
            c.label("draw_x_wrapped")
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

    payload = c.finish()
    if SMOOTH_OFFSET + len(payload) > SMOOTH_LIMIT:
        raise AssertionError("부드러운 월드 지도 추적 코드가 예약 공간을 초과했습니다.")
    return {SMOOTH_OFFSET: payload}, hooks

# Frozen v5 modal generator.

"""Align the native adjacent-tile chooser with fractional world-map scrolling."""

import struct


TEXT_OFFSET = 0x5800
RECT_OFFSET = 0x5900


def _call(source, target):
    return b"\xE8" + struct.pack("<i", target - source - 5)


def build_modal(slot_va, state):
    # The chooser's number labels set their own origin from the view. Change
    # that origin only for this one call, restoring it even if text is clipped.
    text = bytearray.fromhex("55 8B EC 56 53 57 8B F1 8B 5E 54 8B 7E 58")
    text += b"\xA1" + struct.pack("<I", state["rx"])
    text += bytes.fromhex("29 46 54")
    text += b"\xA1" + struct.pack("<I", state["ry"])
    text += bytes.fromhex("29 46 58 FF 75 10 FF 75 0C FF 75 08 8B CE")
    text += _call(slot_va + TEXT_OFFSET + len(text), 0x426860)
    text += bytes.fromhex("89 5E 54 89 7E 58 5F 5B 5E 5D C2 0C 00")

    # XOR selection outlines are also used to erase the previous selection.
    # Use an adjusted stack copy on every call, never modify the caller's RECT.
    rect = bytearray.fromhex("55 8B EC 83 EC 10 8B 55 08")
    for source, target, axis in ((0, 0xF0, "rx"), (4, 0xF4, "ry"),
                                  (8, 0xF8, "rx"), (12, 0xFC, "ry")):
        rect += bytes((0x8B, 0x42, source))  # eax=[edx+source]
        rect += b"\x2B\x05" + struct.pack("<I", state[axis])
        rect += bytes((0x89, 0x45, target))  # local rectangle
    rect += bytes.fromhex("FF 75 0C 8D 45 F0 50")
    rect += _call(slot_va + RECT_OFFSET + len(rect), 0x4B5C18)
    rect += bytes.fromhex("8B E5 5D C2 08 00")
    hooks = [(0x48B99A, _call(0x48B99A, 0x426860),
              _call(0x48B99A, slot_va + TEXT_OFFSET))]
    for address in (0x48BB89, 0x48BBCC, 0x48BC1A):
        hooks.append((address, _call(address, 0x4B5C18),
                      _call(address, slot_va + RECT_OFFSET)))
    return {TEXT_OFFSET: bytes(text), RECT_OFFSET: bytes(rect)}, hooks

