"""Reversible Slave discovery patches for CDS III game data.

The library hint is blocked by two EXE conditions and existing SAVEDATA.CDS
templates need two companion fields adjusted.  The missing dialogue belongs to
DISEV.CDS part 229.  Each option is independent and restores only bytes that
match the patcher's own, verified values.
"""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import shutil
import tempfile

from app_update import bundled_resource_path
from kaaba_patch import _parse_ls12, _rebuild_ls12
from kaaba_save_patch import marker_from_record_dates


LIBRARY_BRANCH_OFFSET = 0x2C0A9
LIBRARY_BRANCH_ORIGINAL = b"\x75"
LIBRARY_BRANCH_PATCHED = b"\xEB"
LIBRARY_VALUE_OFFSET = 0xC50EC
LIBRARY_VALUE_ORIGINAL = b"\xFF\xFF\xFF\xFF"
LIBRARY_VALUE_PATCHED = b"\xB8\x00\x00\x00"

SAVEDATA_LIBRARY_OFFSET = 0x18CFD
SAVEDATA_LIBRARY_ORIGINAL = b"\xFF\xFF"
SAVEDATA_LIBRARY_PATCHED = b"\xB8\x00"
SAVEDATA_SLAVE_HINT_OFFSET = 0x23D44
SLAVE_DISCOVERY_SAVE_OFFSET = 0x23D4B
SLAVE_DISCOVERY_MARKER_OFFSET = SLAVE_DISCOVERY_SAVE_OFFSET - 1
UNSPAWNED_STATE = 0x00
UNDISCOVERED_STATE = 0x0C

SLAVE_EVENT_PART = 229
SLAVE_ORIGINAL_UNCOMPRESSED_SIZE = 254


class SlavePatchError(ValueError):
    """The selected CDS III game files are not safe to modify."""


def _resource(name: str) -> bytes:
    try:
        return bundled_resource_path("Resources", "bin", name).read_bytes()
    except OSError as exc:
        raise SlavePatchError(f"내장 노예 발견물 리소스를 읽지 못했습니다: {name}") from exc


def _library_state(exe: bytes) -> bool:
    branch = exe[LIBRARY_BRANCH_OFFSET:LIBRARY_BRANCH_OFFSET + len(LIBRARY_BRANCH_ORIGINAL)]
    value = exe[LIBRARY_VALUE_OFFSET:LIBRARY_VALUE_OFFSET + len(LIBRARY_VALUE_ORIGINAL)]
    original = branch == LIBRARY_BRANCH_ORIGINAL and value == LIBRARY_VALUE_ORIGINAL
    patched = branch == LIBRARY_BRANCH_PATCHED and value == LIBRARY_VALUE_PATCHED
    if not original and not patched:
        raise SlavePatchError("노예 도서관 힌트 코드가 지원하는 원본/패치 상태와 다릅니다.")
    return patched


def is_library_enabled(exe_path: Path) -> bool:
    """Return whether the two verified EXE library-hint patches are active."""
    return _library_state(exe_path.resolve(strict=True).read_bytes())


def _dialogue_state(disev: bytes) -> bool:
    entries = _parse_ls12(disev, "DISEV.CDS")
    if len(entries) <= SLAVE_EVENT_PART:
        raise SlavePatchError("DISEV.CDS에서 노예 발견물 이벤트 파트를 찾지 못했습니다.")
    compressed, uncompressed, offset = entries[SLAVE_EVENT_PART]
    current = disev[offset:offset + compressed]
    original = _resource("slave_part229_original.bin")
    patched = _resource("slave_part229.bin")
    if (compressed, uncompressed, current) == (len(original), SLAVE_ORIGINAL_UNCOMPRESSED_SIZE, original):
        return False
    if (compressed, uncompressed, current) == (len(patched), len(patched), patched):
        return True
    raise SlavePatchError("DISEV.CDS의 노예 발견물 이벤트가 지원하는 원본/패치 상태와 다릅니다.")


def is_dialogue_enabled(exe_path: Path) -> bool:
    """Return whether DISEV.CDS contains the bundled Slave dialogue event."""
    disev = exe_path.resolve(strict=True).with_name("DISEV.CDS")
    if not disev.is_file():
        raise SlavePatchError("선택한 EXE와 같은 폴더에 DISEV.CDS가 필요합니다.")
    return _dialogue_state(disev.read_bytes())


def _back_up_and_replace(
    changes: dict[Path, bytes], label: str, backed_up_paths: set[Path] | None = None,
) -> tuple[Path, ...]:
    changed = {path.resolve(): content for path, content in changes.items() if path.read_bytes() != content}
    if not changed:
        return ()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backups: list[Path] = []
    temporary: dict[Path, Path] = {}
    try:
        for path in changed:
            if backed_up_paths is not None and path in backed_up_paths:
                continue
            backup = path.with_name(f"{path.name}.before_{label}_{stamp}.bak")
            shutil.copy2(path, backup)
            if backup.read_bytes() != path.read_bytes():
                raise IOError(f"{path.name} 백업 검증에 실패했습니다.")
            backups.append(backup)
            if backed_up_paths is not None:
                backed_up_paths.add(path)
        for path, content in changed.items():
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix="cds-slave-", suffix=".tmp", delete=False) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
                temporary[path] = Path(stream.name)
        for path, temporary_path in temporary.items():
            if temporary_path.read_bytes() != changed[path]:
                raise IOError(f"{path.name} 임시 파일 검증에 실패했습니다.")
            os.replace(temporary_path, path)
        for path, content in changed.items():
            if path.read_bytes() != content:
                raise IOError(f"{path.name} 저장 후 검증에 실패했습니다.")
    finally:
        for temporary_path in temporary.values():
            temporary_path.unlink(missing_ok=True)
    return tuple(backups)


def apply_library_hint(
    exe_path: Path, enabled: bool, backed_up_paths: set[Path] | None = None,
) -> tuple[Path, ...]:
    """Toggle library-hint access and its SAVEDATA.CDS companion fields."""
    exe_path = exe_path.resolve(strict=True)
    savedata_path = exe_path.with_name("SAVEDATA.CDS")
    if not savedata_path.is_file():
        raise SlavePatchError("선택한 EXE와 같은 폴더에 SAVEDATA.CDS가 필요합니다.")
    exe, savedata = exe_path.read_bytes(), savedata_path.read_bytes()
    if len(savedata) <= SLAVE_DISCOVERY_SAVE_OFFSET + 142:
        raise SlavePatchError("SAVEDATA.CDS가 노예 발견물 상태를 포함하지 않습니다.")
    was_enabled = _library_state(exe)
    updated_exe = bytearray(exe)
    updated_savedata = bytearray(savedata)
    if was_enabled != enabled:
        updated_exe[LIBRARY_BRANCH_OFFSET:LIBRARY_BRANCH_OFFSET + 1] = (
            LIBRARY_BRANCH_PATCHED if enabled else LIBRARY_BRANCH_ORIGINAL
        )
        updated_exe[LIBRARY_VALUE_OFFSET:LIBRARY_VALUE_OFFSET + 4] = (
            LIBRARY_VALUE_PATCHED if enabled else LIBRARY_VALUE_ORIGINAL
        )

    library_value = bytes(savedata[SAVEDATA_LIBRARY_OFFSET:SAVEDATA_LIBRARY_OFFSET + 2])
    if library_value not in (SAVEDATA_LIBRARY_ORIGINAL, SAVEDATA_LIBRARY_PATCHED):
        raise SlavePatchError("SAVEDATA.CDS의 노예 도서관 힌트 상태가 예상과 다릅니다.")
    updated_savedata[SAVEDATA_LIBRARY_OFFSET:SAVEDATA_LIBRARY_OFFSET + 2] = (
        SAVEDATA_LIBRARY_PATCHED if enabled else SAVEDATA_LIBRARY_ORIGINAL
    )

    hint_state = savedata[SAVEDATA_SLAVE_HINT_OFFSET]
    if hint_state == UNSPAWNED_STATE and enabled:
        updated_savedata[SAVEDATA_SLAVE_HINT_OFFSET] = UNDISCOVERED_STATE
    elif hint_state == UNDISCOVERED_STATE and not enabled:
        updated_savedata[SAVEDATA_SLAVE_HINT_OFFSET] = UNSPAWNED_STATE

    discovery_state = savedata[SLAVE_DISCOVERY_MARKER_OFFSET]
    if discovery_state == UNSPAWNED_STATE and enabled:
        updated_savedata[SLAVE_DISCOVERY_MARKER_OFFSET] = marker_from_record_dates(
            savedata, SLAVE_DISCOVERY_SAVE_OFFSET
        )
    elif discovery_state == UNDISCOVERED_STATE and not enabled:
        updated_savedata[SLAVE_DISCOVERY_MARKER_OFFSET] = UNSPAWNED_STATE

    return _back_up_and_replace(
        {exe_path: bytes(updated_exe), savedata_path: bytes(updated_savedata)}, "slave_library", backed_up_paths
    )


def apply_dialogue(
    exe_path: Path, enabled: bool, backed_up_paths: set[Path] | None = None,
) -> tuple[Path, ...]:
    """Toggle only DISEV.CDS part 229, preserving every other event part."""
    exe_path = exe_path.resolve(strict=True)
    disev_path = exe_path.with_name("DISEV.CDS")
    if not disev_path.is_file():
        raise SlavePatchError("선택한 EXE와 같은 폴더에 DISEV.CDS가 필요합니다.")
    disev = disev_path.read_bytes()
    was_enabled = _dialogue_state(disev)
    if was_enabled == enabled:
        return ()
    entries = _parse_ls12(disev, "DISEV.CDS")
    replacement = _resource("slave_part229.bin") if enabled else _resource("slave_part229_original.bin")
    replacement_size = len(replacement) if enabled else SLAVE_ORIGINAL_UNCOMPRESSED_SIZE
    updated = _rebuild_ls12(disev, entries, {SLAVE_EVENT_PART: (replacement, replacement_size)})
    return _back_up_and_replace({disev_path: updated}, "slave_dialogue", backed_up_paths)
