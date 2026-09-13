"""Install converted DISCOVER animations and redirect discovery media records."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys

import pefile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_MODULES = PROJECT_ROOT / "Resources" / "py"
if str(RESOURCE_MODULES) not in sys.path:
    sys.path.insert(0, str(RESOURCE_MODULES))

from patch_cds_integrated import (  # noqa: E402
    DISCOVERY_MEDIA_ANIMATION_OFFSET,
    DISCOVERY_MEDIA_AVI_OFFSET,
    DISCOVERY_MEDIA_RECORD_RELATIVE_OFFSET,
    NO_DISCOVERY_MEDIA,
    _discovery_metadata_offset,
    read_discovery_records,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_video(source: Path, target: Path, expected_sha256: str) -> str:
    if _sha256(source) != expected_sha256:
        raise RuntimeError(f"변환 영상의 해시가 manifest와 다릅니다: {source.name}")
    if target.exists():
        if _sha256(target) != expected_sha256:
            raise RuntimeError(f"게임 AVI 폴더에 이름이 같은 다른 파일이 있습니다: {target}")
        return "existing"
    temporary = target.with_name(target.name + ".discover_avi.tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, target)
    return "copied"


def install(executable: Path, manifest_path: Path) -> None:
    executable = executable.resolve(strict=True)
    manifest_path = manifest_path.resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    parts = manifest.get("parts")
    if not isinstance(parts, list) or len(parts) != 29:
        raise RuntimeError("변환 manifest에 DISCOVER 29파트가 모두 들어 있지 않습니다.")

    video_source = manifest_path.parent / "avi"
    video_target = executable.parent / "AVI"
    video_target.resolve(strict=True)
    expected_parts = set(range(29))
    if {int(item["discover_part"]) for item in parts} != expected_parts:
        raise RuntimeError("변환 manifest의 DISCOVER 파트 대응이 0~28과 일치하지 않습니다.")

    records = {record.identifier: record for record in read_discovery_records(executable)}
    pending: list[tuple[int, int, int]] = []
    already_patched = 0
    for item in parts:
        part = int(item["discover_part"])
        avi_id = int(item["avi_id"])
        identifier = int(item["discovery_record"])
        record = records.get(identifier)
        if record is None:
            raise RuntimeError(f"발견물 레코드 {identifier}번이 없습니다.")
        if record.still_slot is not None:
            raise RuntimeError(f"발견물 {identifier}번에 예상하지 않은 DSTILL 값이 있습니다.")
        if record.animation_part == part and record.avi_id is None:
            pending.append((identifier, part, avi_id))
        elif record.animation_part is None and record.avi_id == avi_id:
            already_patched += 1
        else:
            raise RuntimeError(
                f"발견물 {identifier}번 미디어 값이 예상과 다릅니다: "
                f"AVI={record.avi_id}, DISCOVER={record.animation_part}"
            )

    copied = existing = 0
    for item in parts:
        video_name = str(item["avi_file"])
        source = video_source / video_name
        source.resolve(strict=True)
        result = _copy_video(source, video_target / video_name, str(item["avi_sha256"]))
        copied += result == "copied"
        existing += result == "existing"

    if not pending:
        print(f"EXE는 이미 변환된 상태입니다. AVI 기존 {existing}개, 신규 {copied}개")
        return
    if len(pending) != 29 or already_patched:
        raise RuntimeError("발견물 레코드가 일부만 변환된 상태이므로 안전을 위해 중단합니다.")

    original = executable.read_bytes()
    updated = bytearray(original)
    pe = pefile.PE(data=original, fast_load=False)
    try:
        for identifier, _part, avi_id in pending:
            metadata_offset = _discovery_metadata_offset(pe, identifier)
            media_offset = metadata_offset + DISCOVERY_MEDIA_RECORD_RELATIVE_OFFSET
            struct.pack_into("<I", updated, media_offset + DISCOVERY_MEDIA_AVI_OFFSET, avi_id)
            struct.pack_into(
                "<I", updated, media_offset + DISCOVERY_MEDIA_ANIMATION_OFFSET,
                NO_DISCOVERY_MEDIA,
            )
    finally:
        pe.close()

    backup = executable.with_name(executable.name + ".before_discover_avi.bak")
    if backup.exists():
        if backup.read_bytes() != original:
            raise RuntimeError(f"기존 백업이 현재 EXE와 다르므로 덮어쓰지 않습니다: {backup}")
    else:
        shutil.copy2(executable, backup)

    temporary = executable.with_name(executable.name + ".discover_avi.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, executable)
    finally:
        if temporary.exists():
            temporary.unlink()

    patched = {record.identifier: record for record in read_discovery_records(executable)}
    failures = []
    for identifier, _part, avi_id in pending:
        record = patched[identifier]
        if record.still_slot is not None or record.avi_id != avi_id or record.animation_part is not None:
            failures.append(identifier)
    if failures:
        raise RuntimeError(f"저장 후 검증에 실패한 발견물 레코드: {failures}")
    print(f"발견물 29개를 AVI I70~I98로 변경했습니다.")
    print(f"AVI 기존 {existing}개, 신규 {copied}개")
    print(f"백업: {backup}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument(
        "--manifest", type=Path,
        default=PROJECT_ROOT / "research" / "discover_export" / "manifest.json",
    )
    args = parser.parse_args()
    install(args.executable, args.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
