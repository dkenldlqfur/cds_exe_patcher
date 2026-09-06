"""Extract the verified Kaaba Temple still from an injected DSTILL.CDS.

The resulting resource is the game's native pixel, palette and size triplet,
not a re-encoded image.  It is used directly by the integrated patcher.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import struct


KAABA_SLOT = 84
MAGIC = b"CDSKABIMG1\0"


def read_parts(archive: bytes) -> list[tuple[int, int, int]]:
    if archive[:4] not in (b"Ls12", b"LS11"):
        raise ValueError("DSTILL.CDS가 LS12 아카이브가 아닙니다.")
    entries: list[tuple[int, int, int]] = []
    offset = 0x110
    while offset + 12 <= len(archive):
        compressed, uncompressed, payload = struct.unpack_from(">III", archive, offset)
        if compressed == 0:
            break
        if payload + compressed > len(archive):
            raise ValueError("DSTILL.CDS 파트 범위가 손상되었습니다.")
        entries.append((compressed, uncompressed, payload))
        offset += 12
    return entries


def extract(source: Path, destination: Path) -> None:
    archive = source.read_bytes()
    entries = read_parts(archive)
    if len(entries) != (KAABA_SLOT + 1) * 3:
        raise ValueError(f"카바신전이 주입된 85개 슬롯 DSTILL이 아닙니다: {len(entries) // 3}개")
    selected = entries[KAABA_SLOT * 3:KAABA_SLOT * 3 + 3]
    blobs = tuple(archive[offset:offset + compressed] for compressed, _uncompressed, offset in selected)
    if (len(blobs[0]), len(blobs[1]), blobs[2]) != (320 * 240, 86 * 3, struct.pack("<II", 320, 240)):
        raise ValueError("카바신전 슬롯 84의 이미지 형식을 검증하지 못했습니다.")
    payload = MAGIC + b"".join(blobs)
    destination.write_bytes(payload)
    print(f"{destination}: {len(payload)} bytes, sha256={hashlib.sha256(payload).hexdigest()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    extract(args.source, args.destination)
