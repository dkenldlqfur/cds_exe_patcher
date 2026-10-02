"""Native-movement-preserving camera and mouse hooks for world-map follow.

The visible camera and the original edge-scrolling camera are separate.  The
native mover runs with its original camera, including its wind/RNG and collision
side effects; only after it finishes do we restore the centered visible camera.
"""

from __future__ import annotations

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
