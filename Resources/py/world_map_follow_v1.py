"""Frozen v1 generators, used only to validate and migrate installed patches.

Do not edit: existing EXEs must match their complete original payload before
any code is restored or upgraded.
"""

from __future__ import annotations

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
    reset_check("move_enter_reset", "move_enter_ready", True)
    label("move_enter_reset")
    remember_legacy()
    label("move_enter_ready")
    for source, dest in (("legacy_x", "camera_x"), ("legacy_y", "camera_y")):
        load(source)
        save(dest)
    branch("E8", "rebase")
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


"""Render the fractional part of the world-map following camera.

The native tile and occlusion caches keep their original dimensions.  Only
the tile loops gain a partial edge row/column; those cells never dereference
either cache.  DirectDraw and software drawing share the same camera offset.
"""


import struct


RENDER_OFFSET = 0x1800
RENDER_LIMIT = 0x5800
SOFTWARE_ORIGIN = (0x62B2C8, 0x62B2CC, 0x62B870, 0x62B874)
SOFTWARE_CLIP = (0x62B828, 0x62B82C, 0x62B830, 0x62B834)
MAP_SURFACE = 0x569FF0


class _Code:
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
    c = _Code(slot_va + RENDER_OFFSET)
    hooks: list[tuple[int, bytes, bytes]] = []

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
    c.label("activate_done")
    c.emit("C3")

    c.label("restore")
    c.mem("83 3D", state["active"])
    c.emit("00")
    c.branch("0F 84", "restore_done")
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
    c.emit("61 9D")
    c.to("E9", 0x48A572)  # always redraw but keep native in-range occlusion
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
    for index in range(4):
        c.mem("A1", state["rx" if index % 2 == 0 else "ry"])
        c.emit(f"29 45 {0xE0 + index * 4:02X}")
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
    c.emit("FF 75 1C FF 75 18 8D 45 F0 50 FF 75 10 8D 45 E0 50")
    c.branch("E9", "blt_call")
    c.label("blt_native")
    c.emit("FF 75 1C FF 75 18 FF 75 14 FF 75 10 FF 75 0C")
    c.label("blt_call")
    c.emit("8B 45 08 50 8B 08 FF 51 14")
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

