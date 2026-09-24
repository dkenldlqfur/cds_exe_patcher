"""Move a legacy single-slot save into the first free numbered save slot."""

from __future__ import annotations

from pathlib import Path


SAVE_SLOT_COUNT = 10


class SaveSlotMigrationError(ValueError):
    """The existing save could not be moved safely."""


def migrate_legacy_savedata(exe_path: str | Path) -> Path | None:
    """Rename SAVEDATA.CDS to the first unused SAVEDATA01~10.CDS.

    Return the destination, or ``None`` when there is no legacy save or all
    ten destinations already exist.  On Windows, Path.rename refuses to
    replace an existing destination, including one created after the check.
    """
    exe_path = Path(exe_path).resolve(strict=True)
    source = exe_path.with_name("SAVEDATA.CDS")
    if not source.is_file():
        return None

    for number in range(1, SAVE_SLOT_COUNT + 1):
        destination = exe_path.with_name(f"SAVEDATA{number:02d}.CDS")
        if destination.exists():
            continue
        try:
            source.rename(destination)
        except FileExistsError:
            # A slot can be created between the existence check and rename.
            continue
        except OSError as error:
            raise SaveSlotMigrationError(
                f"{source.name}을(를) {destination.name}(으)로 옮기지 못했습니다: {error}"
            ) from error
        return destination
    return None
