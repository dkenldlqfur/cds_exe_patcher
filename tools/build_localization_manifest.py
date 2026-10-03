"""Regenerate reviewed EXE string references from the checked-in test fixture.

Development-only: Capstone identifies instruction operand boundaries. No
runtime scanning of arbitrary pointer-looking bytes is used by the patcher.
"""
from pathlib import Path
import json
import struct
import sys

import capstone
import pefile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Resources" / "py"))
from localization_catalog import translate_text


def build():
    data = (ROOT / "tests/fixtures/coordinate_compass_test.exe").read_bytes()
    pe = pefile.PE(data=data, fast_load=True)
    refs = {}
    for section in pe.sections:
        name = section.Name.rstrip(b"\0")
        lo = section.PointerToRawData
        hi = lo + section.SizeOfRawData
        if name in (b".data", b".rdata"):
            for offset in range(lo, hi - 3, 4):
                value = struct.unpack_from("<I", data, offset)[0]
                refs.setdefault(value, []).append([offset, ""])
        elif name == b".text":
            decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
            decoder.detail = True
            decoder.skipdata = True
            base = pe.OPTIONAL_HEADER.ImageBase + section.VirtualAddress
            for ins in decoder.disasm(data[lo:hi], base):
                if not ins.id:
                    continue
                file_offset = lo + ins.address - base
                for relative, size in ((ins.imm_offset, ins.imm_size),
                                       (ins.disp_offset, ins.disp_size)):
                    if size != 4:
                        continue
                    offset = file_offset + relative
                    value = struct.unpack_from("<I", data, offset)[0]
                    # Prefix from the instruction start, checked before writes.
                    refs.setdefault(value, []).append(
                        [offset, data[file_offset:offset].hex()])

    # Linear disassembly can lose synchronization after inline switch tables.
    # Independently recognize literal PUSH imm32 references to reviewed strings.
    text_section = next(s for s in pe.sections if s.Name.rstrip(b'\0') == b'.text')
    tlo = text_section.PointerToRawData
    thi = tlo + text_section.SizeOfRawData
    for offset in range(tlo + 1, thi - 3):
        if data[offset - 1] == 0x68:
            value = struct.unpack_from('<I', data, offset)[0]
            existing = refs.setdefault(value, [])
            if not any(ref[0] == offset for ref in existing):
                existing.append([offset, '68'])
    entries = []
    # Use pointer targets rather than heuristically decoded binary runs. Only
    # the verified Korean string pool belongs to this version of the game.
    for va, locations in sorted(refs.items()):
        try:
            offset = pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)
        except (pefile.PEFormatError, OverflowError):
            continue
        if not 0x12CFD8 <= offset < 0x17D900:
            continue
        end = data.find(b"\0", offset, offset + 2048)
        if end < 0:
            continue
        try:
            original = data[offset:end].decode("cp949")
        except UnicodeDecodeError:
            continue
        corrected = translate_text(original)
        if original == corrected:
            continue
        entries.append({"offset": offset, "va": va, "original": original,
                        "corrected": corrected, "refs": sorted(locations)})
    # Generated data: keep it readable for review and deterministic in Git.
    output = ROOT / "Resources/data/localization_manifest.json"
    output.write_text(json.dumps({"version": 1, "entries": entries},
                                ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(f"{len(entries)} strings, {sum(len(e['refs']) for e in entries)} references")
    print(f"String pool bytes: {sum(len(e['corrected'].encode('cp949')) + 1 for e in entries)}")


if __name__ == "__main__":
    build()
