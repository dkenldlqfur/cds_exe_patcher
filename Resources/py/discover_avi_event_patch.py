"""Keep discovery-event playback aligned with the DISCOVER-to-AVI EXE patch.

The encyclopedia reads the media fields stored in the EXE discovery master.
Actual discovery scenes instead execute DISEV.CDS commands.  The 29 records
that use DISCOVER.CDS share fourteen event scripts; only those scripts contain
the corresponding ``00 0C`` animation command.  This reversible patch changes
each of those commands to ``00 02`` with its bundled AVI ID.
"""

from __future__ import annotations

from pathlib import Path
import struct

from kaaba_patch import _parse_ls12, _rebuild_ls12
from portrait_reader import _decode_ls12_part
from slave_patch import _back_up_and_replace


# DISEV part: (original DISCOVER.CDS part, replacement AVI ID).
# Other records in 103~131 deliberately share these event scripts and do not
# contain a media command of their own.
DISCOVER_AVI_EVENT_COMMANDS: dict[int, tuple[int, int]] = {
    103: (3, 73),
    104: (4, 74),
    107: (25, 95),
    109: (9, 79),
    112: (10, 80),
    116: (13, 83),
    117: (22, 92),
    119: (14, 84),
    120: (15, 85),
    122: (26, 96),
    125: (16, 86),
    127: (27, 97),
    128: (5, 75),
    130: (20, 90),
}


class DiscoverAviEventPatchError(ValueError):
    """The selected DISEV archive cannot safely receive this patch."""


def _command(opcode: int, media_id: int) -> bytes:
    return b"\x00" + bytes((opcode,)) + struct.pack("<H", media_id)


def _read_parts(
    disev: bytes,
) -> tuple[list[tuple[int, int, int]], dict[int, bytes], bool]:
    try:
        entries = _parse_ls12(disev, "DISEV.CDS")
    except ValueError as error:
        raise DiscoverAviEventPatchError(str(error)) from error
    if len(entries) <= max(DISCOVER_AVI_EVENT_COMMANDS):
        raise DiscoverAviEventPatchError(
            "DISEV.CDS에서 DISCOVER 발견 이벤트 파트를 찾지 못했습니다."
        )

    decoded: dict[int, bytes] = {}
    states: set[bool] = set()
    for part, (animation_part, avi_id) in DISCOVER_AVI_EVENT_COMMANDS.items():
        try:
            payload = _decode_ls12_part(disev, entries[part])
        except ValueError as error:
            raise DiscoverAviEventPatchError(
                f"DISEV.CDS의 발견 이벤트 {part}번을 압축 해제하지 못했습니다."
            ) from error
        original = _command(0x0C, animation_part)
        patched = _command(0x02, avi_id)
        original_count = payload.count(original)
        patched_count = payload.count(patched)
        if original_count == 1 and patched_count == 0:
            states.add(False)
        elif original_count == 0 and patched_count == 1:
            states.add(True)
        else:
            raise DiscoverAviEventPatchError(
                f"DISEV.CDS의 발견 이벤트 {part}번 미디어 명령을 하나로 특정하지 못했습니다."
            )
        decoded[part] = payload
    if len(states) != 1:
        raise DiscoverAviEventPatchError(
            "DISCOVER 대신 AVI 사용이 일부 발견 이벤트에만 적용되어 있습니다."
        )
    return entries, decoded, states.pop()


def is_enabled(exe_path: str | Path) -> bool:
    """Return whether every applicable DISEV discovery command uses AVI."""
    disev_path = Path(exe_path).resolve(strict=True).with_name("DISEV.CDS")
    if not disev_path.is_file():
        raise DiscoverAviEventPatchError(
            "선택한 EXE와 같은 폴더에 DISEV.CDS가 필요합니다."
        )
    _entries, _parts, enabled = _read_parts(disev_path.read_bytes())
    return enabled


def apply(
    exe_path: str | Path,
    enabled: bool,
    backed_up_paths: set[Path] | None = None,
) -> tuple[Path, ...]:
    """Switch the relevant DISEV discovery playback commands to/from AVI."""
    disev_path = Path(exe_path).resolve(strict=True).with_name("DISEV.CDS")
    if not disev_path.is_file():
        raise DiscoverAviEventPatchError(
            "선택한 EXE와 같은 폴더에 DISEV.CDS가 필요합니다."
        )
    disev = disev_path.read_bytes()
    entries, parts, was_enabled = _read_parts(disev)
    if was_enabled == bool(enabled):
        return ()

    replacements: dict[int, tuple[bytes, int]] = {}
    for part, (animation_part, avi_id) in DISCOVER_AVI_EVENT_COMMANDS.items():
        source = _command(0x02, avi_id) if was_enabled else _command(0x0C, animation_part)
        target = _command(0x02, avi_id) if enabled else _command(0x0C, animation_part)
        payload = parts[part]
        updated = payload.replace(source, target, 1)
        if updated == payload:
            raise DiscoverAviEventPatchError(
                f"DISEV.CDS의 발견 이벤트 {part}번 미디어 명령을 바꾸지 못했습니다."
            )
        replacements[part] = (updated, len(updated))

    rebuilt = _rebuild_ls12(disev, entries, replacements)
    _entries, _parts, rebuilt_enabled = _read_parts(rebuilt)
    if rebuilt_enabled != bool(enabled):
        raise DiscoverAviEventPatchError(
            "DISCOVER 발견 이벤트 AVI 전환 결과를 검증하지 못했습니다."
        )
    return _back_up_and_replace(
        {disev_path: rebuilt}, "discover_avi_events", backed_up_paths,
    )
