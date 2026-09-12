"""Reversible Mughal Empire event-condition patch for CDS III.

Only DISEV.CDS part 92 is replaced.  The source reference archive also
contains unrelated Kaaba and Slave patches, so those parts are deliberately
not bundled or touched here.
"""

from __future__ import annotations

from pathlib import Path

from app_update import bundled_resource_path
from kaaba_patch import KaabaPatchError, _parse_ls12, _rebuild_ls12
from slave_patch import _back_up_and_replace


MUGHAL_EVENT_PART = 92
MUGHAL_ORIGINAL_COMPRESSED_SIZE = 1378
MUGHAL_UNCOMPRESSED_SIZE = 2211


class MughalPatchError(ValueError):
    """The selected DISEV.CDS event is not a verified supported state."""


def _resource(name: str) -> bytes:
    try:
        return bundled_resource_path("Resources", "bin", name).read_bytes()
    except OSError as exc:
        raise MughalPatchError(f"내장 무제국 이벤트 리소스를 읽지 못했습니다: {name}") from exc


def _event_state(disev: bytes) -> bool:
    try:
        entries = _parse_ls12(disev, "DISEV.CDS")
    except KaabaPatchError as exc:
        raise MughalPatchError(str(exc)) from exc
    if len(entries) <= MUGHAL_EVENT_PART:
        raise MughalPatchError("DISEV.CDS에서 무제국 이벤트 파트 92를 찾지 못했습니다.")
    compressed, uncompressed, offset = entries[MUGHAL_EVENT_PART]
    current = disev[offset:offset + compressed]
    original = _resource("mughal_part92_original.bin")
    patched = _resource("mughal_part92.bin")
    if (
        len(original) != MUGHAL_ORIGINAL_COMPRESSED_SIZE
        or len(patched) != MUGHAL_UNCOMPRESSED_SIZE
    ):
        raise MughalPatchError("내장 무제국 이벤트 리소스의 크기가 예상과 다릅니다.")
    if (
        compressed == MUGHAL_ORIGINAL_COMPRESSED_SIZE
        and uncompressed == MUGHAL_UNCOMPRESSED_SIZE
        and current == original
    ):
        return False
    if compressed == MUGHAL_UNCOMPRESSED_SIZE and uncompressed == MUGHAL_UNCOMPRESSED_SIZE and current == patched:
        return True
    raise MughalPatchError(
        "DISEV.CDS의 무제국 이벤트가 지원하는 원본/패치 상태와 다릅니다."
    )


def is_enabled(exe_path: Path) -> bool:
    """Return whether DISEV.CDS contains the bundled Mughal condition event."""
    disev_path = exe_path.resolve(strict=True).with_name("DISEV.CDS")
    if not disev_path.is_file():
        raise MughalPatchError("선택한 EXE와 같은 폴더에 DISEV.CDS가 필요합니다.")
    return _event_state(disev_path.read_bytes())


def apply(
    exe_path: Path, enabled: bool, backed_up_paths: set[Path] | None = None,
) -> tuple[Path, ...]:
    """Toggle only DISEV.CDS part 92 and preserve every other event part."""
    exe_path = exe_path.resolve(strict=True)
    disev_path = exe_path.with_name("DISEV.CDS")
    if not disev_path.is_file():
        raise MughalPatchError("선택한 EXE와 같은 폴더에 DISEV.CDS가 필요합니다.")
    disev = disev_path.read_bytes()
    if _event_state(disev) == enabled:
        return ()
    try:
        entries = _parse_ls12(disev, "DISEV.CDS")
    except KaabaPatchError as exc:
        raise MughalPatchError(str(exc)) from exc
    replacement = _resource("mughal_part92.bin") if enabled else _resource("mughal_part92_original.bin")
    updated = _rebuild_ls12(
        disev, entries, {MUGHAL_EVENT_PART: (replacement, MUGHAL_UNCOMPRESSED_SIZE)}
    )
    return _back_up_and_replace({disev_path: updated}, "mughal_hint", backed_up_paths)
