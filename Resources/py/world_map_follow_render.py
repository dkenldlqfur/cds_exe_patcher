"""Render the fractional part of the world-map following camera.

The native tile and occlusion caches keep their original dimensions.  Only
the tile loops gain partial edge rows/columns; those cells never dereference
either cache.  DirectDraw and software drawing share the same camera offset.
Stationary frames retain native dirty-cell caching.  On compatible 8-bit DD
surfaces the background is copied from the native CPU atlas under one lock;
all actor, effect and HUD drawing runs after the surface is unlocked.
"""

from __future__ import annotations

import struct


RENDER_OFFSET = 0x1800
RENDER_LIMIT = 0x2800
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
