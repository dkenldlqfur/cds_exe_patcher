"""Reversible DISEV fixes for the four sea-monster encounter events.

Each event already separates victory from retreat/failure.  The victory path
ends with result command ``4C`` (result 0), while the failure path currently
ends with ``4D`` (result 1).  The EXE marks both results 0 and 1 as handled,
preventing another encounter.  Replacing only the final failure command with
``4E`` (result 2) leaves the event eligible for a later encounter.
"""

from __future__ import annotations

from pathlib import Path
import struct

from kaaba_patch import _parse_ls12, _rebuild_ls12
from portrait_reader import _decode_ls12_part
from slave_patch import _back_up_and_replace


SEA_MONSTER_PARTS = {
    196: ("시서펜트", 0x0110),
    197: ("크라켄", 0x010F),
    198: ("맨터", 0x0112),
    199: ("식인상어", 0x0111),
}
ORIGINAL_FAILURE_RESULT = 0x4D
PATCHED_FAILURE_RESULT = 0x4E


class SeaMonsterPatchError(ValueError):
    """The selected DISEV.CDS is not safe to patch."""


def _read_parts(disev: bytes) -> tuple[list[tuple[int, int, int]], dict[int, bytes]]:
    try:
        entries = _parse_ls12(disev, "DISEV.CDS")
    except ValueError as error:
        raise SeaMonsterPatchError(str(error)) from error
    if len(entries) <= max(SEA_MONSTER_PARTS):
        raise SeaMonsterPatchError("DISEV.CDS에서 해상괴물 이벤트 파트 196~199를 찾지 못했습니다.")

    decoded: dict[int, bytes] = {}
    for part, (name, battle_actor) in SEA_MONSTER_PARTS.items():
        try:
            payload = _decode_ls12_part(disev, entries[part])
        except ValueError as error:
            raise SeaMonsterPatchError(
                f"DISEV.CDS의 {name} 이벤트 파트를 압축 해제하지 못했습니다."
            ) from error
        battle_command = b"\x0D\x0D" + struct.pack("<H", battle_actor)
        if (
            len(payload) < 10
            or struct.unpack_from("<H", payload)[0] != part
            or battle_command not in payload
            or b"\x4C\x43\x47" not in payload
            or payload[-1] != 0xFF
            or payload[-2] not in (ORIGINAL_FAILURE_RESULT, PATCHED_FAILURE_RESULT)
        ):
            raise SeaMonsterPatchError(
                f"DISEV.CDS의 {name} 이벤트 종료 구조가 지원하는 상태와 다릅니다."
            )
        decoded[part] = payload
    return entries, decoded


def _state(disev: bytes) -> bool:
    _entries, parts = _read_parts(disev)
    states = {payload[-2] == PATCHED_FAILURE_RESULT for payload in parts.values()}
    if len(states) != 1:
        raise SeaMonsterPatchError(
            "DISEV.CDS의 해상괴물 조우 수정이 일부 이벤트에만 적용되어 있습니다."
        )
    return states.pop()


def is_enabled(exe_path: str | Path) -> bool:
    """Return whether all four sea-monster failure paths use result 2."""
    disev_path = Path(exe_path).resolve(strict=True).with_name("DISEV.CDS")
    if not disev_path.is_file():
        raise SeaMonsterPatchError("선택한 EXE와 같은 폴더에 DISEV.CDS가 필요합니다.")
    return _state(disev_path.read_bytes())


def apply(
    exe_path: str | Path,
    enabled: bool,
    backed_up_paths: set[Path] | None = None,
) -> tuple[Path, ...]:
    """Toggle only the failure-path result byte in DISEV parts 196~199."""
    disev_path = Path(exe_path).resolve(strict=True).with_name("DISEV.CDS")
    if not disev_path.is_file():
        raise SeaMonsterPatchError("선택한 EXE와 같은 폴더에 DISEV.CDS가 필요합니다.")

    disev = disev_path.read_bytes()
    entries, parts = _read_parts(disev)
    result = PATCHED_FAILURE_RESULT if enabled else ORIGINAL_FAILURE_RESULT
    replacements: dict[int, tuple[bytes, int]] = {}
    for part, payload in parts.items():
        if payload[-2] == result:
            continue
        updated = bytearray(payload)
        updated[-2] = result
        replacements[part] = (bytes(updated), len(updated))
    if not replacements:
        return ()

    rebuilt = _rebuild_ls12(disev, entries, replacements)
    # Re-read the rebuilt archive before replacing the user's file.
    if _state(rebuilt) != bool(enabled):
        raise SeaMonsterPatchError("해상괴물 조우 수정 결과를 검증하지 못했습니다.")
    return _back_up_and_replace(
        {disev_path: rebuilt}, "sea_monster_encounter", backed_up_paths
    )
