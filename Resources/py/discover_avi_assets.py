"""Install the bundled DISCOVER-to-AVI replacement movies safely."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import struct
import tempfile

from app_update import bundled_resource_path


DISCOVER_AVI_FIRST_ID = 70
DISCOVER_AVI_LAST_ID = 98
DISCOVER_AVI_WIDTH = 320
DISCOVER_AVI_HEIGHT = 240
DISCOVER_AVI_FPS = 15


class DiscoverAviAssetError(ValueError):
    """The bundled or destination AVI set is incomplete or unsafe to use."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_avi(path: Path) -> None:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise DiscoverAviAssetError(f"내장 발견물 AVI를 읽지 못했습니다: {path.name}") from error
    if data[:4] != b"RIFF" or data[8:12] != b"AVI ":
        raise DiscoverAviAssetError(f"내장 발견물 AVI 형식이 올바르지 않습니다: {path.name}")
    avih = data.find(b"avih")
    strh = data.find(b"strh")
    strf = data.find(b"strf")
    if min(avih, strh, strf) < 0:
        raise DiscoverAviAssetError(f"내장 발견물 AVI 헤더가 없습니다: {path.name}")
    try:
        main_header = struct.unpack_from("<10I", data, avih + 8)
        scale, rate = struct.unpack_from("<II", data, strh + 8 + 20)
        compression = data[strf + 8 + 16:strf + 8 + 20]
    except struct.error as error:
        raise DiscoverAviAssetError(f"내장 발견물 AVI 헤더가 잘렸습니다: {path.name}") from error
    width, height = main_header[8], main_header[9]
    if (width, height) != (DISCOVER_AVI_WIDTH, DISCOVER_AVI_HEIGHT):
        raise DiscoverAviAssetError(
            f"내장 발견물 AVI 해상도가 올바르지 않습니다: {path.name} ({width}x{height})"
        )
    if compression != b"cvid":
        raise DiscoverAviAssetError(f"내장 발견물 AVI가 Cinepak(cvid)이 아닙니다: {path.name}")
    if not scale or rate / scale != DISCOVER_AVI_FPS:
        raise DiscoverAviAssetError(f"내장 발견물 AVI가 15fps가 아닙니다: {path.name}")


def _bundled_movies() -> tuple[Path, ...]:
    source = bundled_resource_path("Resources", "avi")
    expected = tuple(
        source / f"I{avi_id:02d}_0000.AVI"
        for avi_id in range(DISCOVER_AVI_FIRST_ID, DISCOVER_AVI_LAST_ID + 1)
    )
    for path in expected:
        if not path.is_file():
            raise DiscoverAviAssetError(f"내장 발견물 AVI가 없습니다: {path.name}")
        _validate_avi(path)
    return expected


def install_discover_avi_assets(executable: Path) -> tuple[Path, ...]:
    """Copy missing bundled movies beside the selected game without overwriting files."""
    executable = executable.resolve(strict=True)
    destination = executable.parent / "AVI"
    if not destination.is_dir():
        raise DiscoverAviAssetError("선택한 EXE와 같은 폴더에서 AVI 폴더를 찾지 못했습니다.")

    sources = _bundled_movies()
    source_hashes = {source.name: _sha256(source) for source in sources}
    for source in sources:
        target = destination / source.name
        if target.exists() and (
            not target.is_file() or _sha256(target) != source_hashes[source.name]
        ):
            raise DiscoverAviAssetError(
                f"게임 AVI 폴더에 이름이 같은 다른 파일이 있어 덮어쓸 수 없습니다: {target.name}"
            )

    installed: list[Path] = []
    temporary_paths: list[Path] = []
    try:
        for source in sources:
            target = destination / source.name
            if target.exists():
                continue
            with tempfile.NamedTemporaryFile(
                dir=destination, prefix="cds-discover-avi-", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
            temporary_paths.append(temporary)
            shutil.copy2(source, temporary)
            if _sha256(temporary) != source_hashes[source.name]:
                raise IOError(f"발견물 AVI 임시 복사본 검증에 실패했습니다: {source.name}")
            os.replace(temporary, target)
            temporary_paths.remove(temporary)
            installed.append(target)
        for source in sources:
            target = destination / source.name
            if _sha256(target) != source_hashes[source.name]:
                raise IOError(f"발견물 AVI 저장 후 검증에 실패했습니다: {source.name}")
    finally:
        for temporary in temporary_paths:
            temporary.unlink(missing_ok=True)
    return tuple(installed)
