"""Restore missing EVSTILL displays for four geographic discoveries.

The first twelve discovery events are stored in ``DISEV.CDS``.  Eight of
them show one of the shared EVSTILL scenes after their introductory text, but
India, the Spice Islands, China, and Zipangu omit that command.  This toggle
adds the same display command at the equivalent point in those four scripts:

* India, Spice Islands, China, Zipangu: EVSTILL slot 4

The patch also inserts the ``33`` image-close command immediately before the
matching item-acquisition command, so the normal acquisition sequence can
continue after the scene.  Only the four decoded event scripts are changed.
``EVSTILL.CDS`` and the executable are left untouched because slot 4 is
already valid.
"""

from __future__ import annotations

from pathlib import Path
import struct

from kaaba_patch import _parse_ls12, _rebuild_ls12
from portrait_reader import _decode_ls12_part
from slave_patch import _back_up_and_replace


# DISEV part number: (discovery name, EVSTILL slot)
GEOGRAPHIC_DISCOVERY_STILLS = {
    2: ("인도", 4),
    4: ("향료제도", 4),
    5: ("중국", 4),
    8: ("지팡그", 4),
}

# ``00 05 [u16]`` is the item-acquisition command in these events.  Each
# target script has one discovery-related acquisition at the value below.
ITEM_ACQUISITION_IDS = {2: 0x00AE, 4: 0x00B0, 5: 0x00B1, 8: 0x00B4}

# Existing geographic events with an EVSTILL image put the command directly
# after the first 0E 03 4B dialogue-control sequence.  The surrounding bytes
# make this insertion point unambiguous in all four target scripts.
DISPLAY_ANCHOR = b"\x0E\x03\x4B\x00\x00\x0A"


class GeographicDiscoveryStillPatchError(ValueError):
    """The selected DISEV archive is not a supported patch state."""


def _display_anchor(slot: int, enabled: bool) -> bytes:
    command = b"\x00\x1F" + struct.pack("<H", slot)
    return (
        b"\x0E\x03\x4B\x00" + command + b"\x00\x0A"
        if enabled else DISPLAY_ANCHOR
    )


def _item_acquisition_anchor(part: int, closes_image: bool) -> bytes:
    """Return the target item-acquisition command, with an optional close."""
    item = b"\x00\x05" + struct.pack("<H", ITEM_ACQUISITION_IDS[part])
    return (b"\x33" if closes_image else b"") + item


def _registration_anchor(part: int, closes_image: bool) -> bytes:
    """Return the old, incorrect close position used by an earlier release."""
    registration = b"\x00\x01\x0B" + struct.pack("<H", part)
    return registration + (b"\x33" if closes_image else b"")


def _read_parts(
    disev: bytes,
) -> tuple[
    list[tuple[int, int, int]], dict[int, bytes], bool, set[int], set[int], set[int],
]:
    try:
        entries = _parse_ls12(disev, "DISEV.CDS")
    except ValueError as error:
        raise GeographicDiscoveryStillPatchError(str(error)) from error
    if len(entries) <= max(GEOGRAPHIC_DISCOVERY_STILLS):
        raise GeographicDiscoveryStillPatchError(
            "DISEV.CDS에서 지리 발견 이벤트 파트 2·4·5·8을 찾지 못했습니다."
        )

    decoded: dict[int, bytes] = {}
    states: set[bool] = set()
    legacy_slot_parts: set[int] = set()
    missing_item_close_parts: set[int] = set()
    legacy_registration_close_parts: set[int] = set()
    for part, (name, slot) in GEOGRAPHIC_DISCOVERY_STILLS.items():
        try:
            payload = _decode_ls12_part(disev, entries[part])
        except ValueError as error:
            raise GeographicDiscoveryStillPatchError(
                f"DISEV.CDS의 {name} 발견 이벤트를 압축 해제하지 못했습니다."
            ) from error

        original = _display_anchor(slot, False)
        patched = _display_anchor(slot, True)
        # Releases before slot 4 was chosen for every event used slot 5 for
        # the Spice Islands.  Treat that as enabled, then migrate it on the
        # next apply rather than rejecting an otherwise valid installed patch.
        legacy_patched = _display_anchor(5, True) if part == 4 else None
        if (
            len(payload) < 12
            or struct.unpack_from("<H", payload)[0] != part
            or payload[-2:] != b"\x4C\xFF"
        ):
            raise GeographicDiscoveryStillPatchError(
                f"DISEV.CDS의 {name} 발견 이벤트 구조가 예상과 다릅니다."
            )
        original_count = payload.count(original)
        patched_count = payload.count(patched)
        legacy_count = payload.count(legacy_patched) if legacy_patched else 0
        if original_count == 1 and patched_count == 0 and legacy_count == 0:
            image_enabled = False
        elif original_count == 0 and patched_count == 1 and legacy_count == 0:
            image_enabled = True
        elif part == 4 and original_count == 0 and patched_count == 0 and legacy_count == 1:
            image_enabled = True
            legacy_slot_parts.add(part)
        else:
            raise GeographicDiscoveryStillPatchError(
                f"DISEV.CDS의 {name} 발견 이벤트에서 정지 이미지 위치를 하나로 특정하지 못했습니다."
            )

        no_close = _item_acquisition_anchor(part, False)
        with_close = _item_acquisition_anchor(part, True)
        with_close_count = payload.count(with_close)
        # ``with_close`` has ``no_close`` as its prefix, so exclude that
        # prefix match before deciding which form the script contains.
        no_close_count = payload.count(no_close) - with_close_count
        if no_close_count == 1 and with_close_count == 0:
            closes_image = False
        elif no_close_count == 0 and with_close_count == 1:
            closes_image = True
        else:
            raise GeographicDiscoveryStillPatchError(
                f"DISEV.CDS의 {name} 발견 이벤트에서 아이템 획득 전 이미지 종료 위치를 하나로 특정하지 못했습니다."
            )

        registration = _registration_anchor(part, False)
        legacy_registration = _registration_anchor(part, True)
        legacy_registration_count = payload.count(legacy_registration)
        registration_count = payload.count(registration) - legacy_registration_count
        if registration_count == 1 and legacy_registration_count == 0:
            closes_at_registration = False
        elif registration_count == 0 and legacy_registration_count == 1:
            closes_at_registration = True
            legacy_registration_close_parts.add(part)
        else:
            raise GeographicDiscoveryStillPatchError(
                f"DISEV.CDS의 {name} 발견 이벤트에서 발견물 등록 위치를 하나로 특정하지 못했습니다."
            )

        if not image_enabled and not closes_image and not closes_at_registration:
            states.add(False)
        elif image_enabled:
            states.add(True)
            if not closes_image:
                missing_item_close_parts.add(part)
        else:
            raise GeographicDiscoveryStillPatchError(
                f"DISEV.CDS의 {name} 발견 이벤트에서 이미지 표시와 종료 상태가 맞지 않습니다."
            )
        decoded[part] = payload

    if len(states) != 1:
        raise GeographicDiscoveryStillPatchError(
            "지리 발견 정지 이미지 수정이 일부 이벤트에만 적용되어 있습니다."
        )
    return (
        entries, decoded, states.pop(), legacy_slot_parts, missing_item_close_parts,
        legacy_registration_close_parts,
    )


def is_enabled(exe_path: str | Path) -> bool:
    """Return whether all four target events display their assigned slot."""
    disev_path = Path(exe_path).resolve(strict=True).with_name("DISEV.CDS")
    if not disev_path.is_file():
        raise GeographicDiscoveryStillPatchError(
            "선택한 EXE와 같은 폴더에 DISEV.CDS가 필요합니다."
        )
    (
        _entries, _parts, enabled, _legacy_slot_parts, _missing_item_close_parts,
        _legacy_registration_close_parts,
    ) = _read_parts(disev_path.read_bytes())
    return enabled


def apply(
    exe_path: str | Path,
    enabled: bool,
    backed_up_paths: set[Path] | None = None,
) -> tuple[Path, ...]:
    """Toggle EVSTILL displays in DISEV parts 2, 4, 5, and 8."""
    disev_path = Path(exe_path).resolve(strict=True).with_name("DISEV.CDS")
    if not disev_path.is_file():
        raise GeographicDiscoveryStillPatchError(
            "선택한 EXE와 같은 폴더에 DISEV.CDS가 필요합니다."
        )

    disev = disev_path.read_bytes()
    (
        entries, parts, was_enabled, legacy_slot_parts, missing_item_close_parts,
        legacy_registration_close_parts,
    ) = _read_parts(disev)
    if (
        was_enabled == bool(enabled)
        and not legacy_slot_parts
        and not missing_item_close_parts
        and not legacy_registration_close_parts
    ):
        return ()

    replacements: dict[int, tuple[bytes, int]] = {}
    for part, (_name, slot) in GEOGRAPHIC_DISCOVERY_STILLS.items():
        source = (
            _display_anchor(5, True)
            if part in legacy_slot_parts
            else _display_anchor(slot, was_enabled)
        )
        target = _display_anchor(slot, bool(enabled))
        payload = parts[part]
        updated = payload.replace(source, target, 1)
        source = _item_acquisition_anchor(
            part, was_enabled and part not in missing_item_close_parts,
        )
        target = _item_acquisition_anchor(part, bool(enabled))
        updated = updated.replace(source, target, 1)
        source = _registration_anchor(part, part in legacy_registration_close_parts)
        target = _registration_anchor(part, False)
        updated = updated.replace(source, target, 1)
        if updated == payload:
            raise GeographicDiscoveryStillPatchError(
                "지리 발견 정지 이미지 명령을 바꾸지 못했습니다."
            )
        replacements[part] = (updated, len(updated))

    rebuilt = _rebuild_ls12(disev, entries, replacements)
    (
        _entries, _parts, rebuilt_enabled, rebuilt_legacy_slot_parts,
        rebuilt_missing_item_close_parts, rebuilt_legacy_registration_close_parts,
    ) = _read_parts(rebuilt)
    if (
        rebuilt_enabled != bool(enabled)
        or rebuilt_legacy_slot_parts
        or rebuilt_missing_item_close_parts
        or rebuilt_legacy_registration_close_parts
    ):
        raise GeographicDiscoveryStillPatchError(
            "지리 발견 정지 이미지 수정 결과를 검증하지 못했습니다."
        )
    return _back_up_and_replace(
        {disev_path: rebuilt}, "geographic_discovery_stills", backed_up_paths,
    )
