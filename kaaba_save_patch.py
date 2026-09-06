"""Minimal, backup-first Kaaba Temple state repair for CDS III SAVEDATA.CDS.

The Kaaba event can be injected into game data after a save already exists.
The built-in SAVEDATA.CDS template can retain the old ``unspawned`` marker,
so the discovery cannot be found.  This module changes only that marker to
``undiscovered``.  It never downgrades it when the game-data patch is removed.
"""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import tempfile


KAABA_DISCOVERY_NUMBER = 63
KAABA_GAME_ID = 672
KAABA_SAVE_OFFSET = 119_539
KAABA_MARKER_OFFSET = KAABA_SAVE_OFFSET - 1
UNSPAWNED_MARKER = 0x00
UNDISCOVERED_MARKER = 0x0C
DISCOVERED_MARKER = 0x4C
REPORTED_MARKER = 0xCC
DISCOVERY_DATE_OFFSET = 0x28
REPORT_DATE_OFFSET = 0x86
DATE_FIELD_SIZE = 8
MINIMUM_SAVE_SIZE = KAABA_SAVE_OFFSET + 164


class KaabaSavePatchError(ValueError):
    """The game-folder SAVEDATA.CDS cannot safely be repaired."""


def _validate_save(data: bytes, path: Path) -> None:
    if len(data) < MINIMUM_SAVE_SIZE:
        raise KaabaSavePatchError(f"{path.name}: CDS III 세이브 파일 크기가 아닙니다.")
    # CDS III stores the in-game date in this fixed header.  This inexpensive
    # check prevents ordinary small/large CDS resource archives from being
    # accepted merely because they happen to cover the discovery offset.
    year = int.from_bytes(data[21:23], "little")
    month, day = data[25], data[26]
    if not 1400 <= year <= 1900 or not 1 <= month <= 12 or not 1 <= day <= 31:
        raise KaabaSavePatchError(f"{path.name}: CDS III 세이브 헤더를 검증하지 못했습니다.")


def marker_from_record_dates(data: bytes | bytearray, record_offset: int) -> int:
    """Infer the least destructive discovery marker from saved date fields."""
    discovery_date = data[record_offset + DISCOVERY_DATE_OFFSET:record_offset + DISCOVERY_DATE_OFFSET + DATE_FIELD_SIZE]
    report_date = data[record_offset + REPORT_DATE_OFFSET:record_offset + REPORT_DATE_OFFSET + DATE_FIELD_SIZE]
    unset_values = (b"\xFF" * DATE_FIELD_SIZE, b"\0" * DATE_FIELD_SIZE)
    if report_date not in unset_values:
        return REPORTED_MARKER
    if discovery_date not in unset_values:
        return DISCOVERED_MARKER
    return UNDISCOVERED_MARKER


def state_marker(path: Path) -> int:
    """Return the raw Kaaba discovery marker after validating SAVEDATA.CDS."""
    path = path.resolve(strict=True)
    data = path.read_bytes()
    _validate_save(data, path)
    return data[KAABA_MARKER_OFFSET]


def promote_unspawned_to_undiscovered(
    path: Path, backed_up_paths: set[Path] | None = None,
) -> Path | None:
    """Promote the Kaaba entry only if it is unspawned, returning its backup.

    ``None`` means the save was already undiscovered, discovered, or reported.
    """
    path = path.resolve(strict=True)
    original = path.read_bytes()
    _validate_save(original, path)
    if original[KAABA_MARKER_OFFSET] != UNSPAWNED_MARKER:
        return None

    updated = bytearray(original)
    updated[KAABA_MARKER_OFFSET] = marker_from_record_dates(original, KAABA_SAVE_OFFSET)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup: Path | None = None
    if backed_up_paths is None or path not in backed_up_paths:
        backup = path.with_name(f"{path.name}.before_kaaba_{stamp}.bak")
        with backup.open("xb") as stream:
            stream.write(original)
        if backup.read_bytes() != original:
            raise IOError(f"{path.name}: 세이브 백업 검증에 실패했습니다.")
        if backed_up_paths is not None:
            backed_up_paths.add(path)

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix="cds-kaaba-save-", suffix=".tmp", delete=False
        ) as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
            temporary_name = stream.name
        temporary = Path(temporary_name)
        if temporary.read_bytes() != updated:
            raise IOError(f"{path.name}: 세이브 임시 파일 검증에 실패했습니다.")
        os.replace(temporary, path)
        if path.read_bytes() != updated:
            raise IOError(f"{path.name}: 세이브 저장 후 검증에 실패했습니다.")
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
    return backup


def promote_game_savedata(
    exe_path: Path, backed_up_paths: set[Path] | None = None,
) -> Path | None:
    """Promote the game folder's SAVEDATA.CDS if its Kaaba entry is unspawned."""
    savedata = exe_path.resolve(strict=True).with_name("SAVEDATA.CDS")
    if not savedata.is_file():
        raise KaabaSavePatchError("선택한 EXE와 같은 폴더에 SAVEDATA.CDS가 필요합니다.")
    return promote_unspawned_to_undiscovered(savedata, backed_up_paths)
