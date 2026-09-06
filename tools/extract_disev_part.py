"""Extract a raw LS12 DISEV.CDS part for auditable bundled patch resources."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kaaba_patch import _parse_ls12


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("part", type=int)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    data = args.source.read_bytes()
    entries = _parse_ls12(data, args.source.name)
    if not 0 <= args.part < len(entries):
        raise SystemExit(f"part must be between 0 and {len(entries) - 1}")
    compressed, _uncompressed, offset = entries[args.part]
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_bytes(data[offset:offset + compressed])


if __name__ == "__main__":
    main()
