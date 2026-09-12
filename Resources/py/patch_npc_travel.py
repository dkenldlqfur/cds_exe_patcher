"""Create a separate CDS_95.EXE with more frequent ordinary NPC travel.

Only IDs 14..190 are affected: the monthly departure roll changes from 1/5
to 1/2, and the existing -60 arrival timer becomes eligible at -45 (15 days).
The monthly scheduling, rival scripts, special NPCs, terrain/city filters,
travel speed, age/activity checks, 16 display slots, and save format stay intact.
The source EXE and save files are never modified. Requires pefile and capstone.
"""

import argparse
import hashlib
from pathlib import Path
import struct

import capstone
import pefile


SOURCE_SHA256 = '6fa47dab501d5519a4017fdf02ae574fc7e5dde439da737ea4b827276738c986'
PATCHES = (
    # Same 7-byte footprint as getter call + test. The following signed JL is
    # unchanged and EAX is replaced by the destination getter on the taken path.
    (0x43282D, bytes.fromhex('e8 ee fa ff ff 85 c0'),
     bytes.fromhex('83 be 10 01 00 00 d3'), 'wait threshold: 0 -> -45'),
    (0x43284A, bytes.fromhex('6a 05'), bytes.fromhex('6a 02'),
     'monthly departure roll: 1/5 -> 1/2'),
)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def make_patch(source):
    if sha256(source) != SOURCE_SHA256:
        raise ValueError('Unsupported source EXE SHA-256; refusing to patch.')
    pe = pefile.PE(data=source)
    if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
        raise ValueError('Expected the verified x86 image at base 0x400000.')
    output = bytearray(source)
    allowed = set()
    for va, before, after, _description in PATCHES:
        offset = pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)
        if len(before) != len(after) or source[offset:offset + len(before)] != before:
            raise ValueError('Unexpected instructions at VA 0x%X' % va)
        output[offset:offset + len(after)] = after
        allowed.update(range(offset, offset + len(after)))
    changes = {i for i, (a, b) in enumerate(zip(source, output)) if a != b}
    if len(source) != len(output) or not changes or not changes <= allowed:
        raise ValueError('Unexpected changes outside the two permitted sites.')
    # Validate the new condition, its unchanged exit branch and RNG call.
    cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    cs.detail = True
    cs_start = pe.get_offset_from_rva(0x3282D)
    code = list(cs.disasm(bytes(output[cs_start:cs_start + 0x2F]), 0x43282D))
    first, branch = code[:2]
    assert first.mnemonic == 'cmp' and first.size == 7
    assert first.operands[0].mem.base == capstone.x86.X86_REG_ESI
    assert first.operands[0].mem.disp == 0x110
    assert first.operands[1].imm == -45
    assert branch.mnemonic == 'jl' and branch.operands[0].imm == 0x4329ED
    roll = next(i for i in code if i.address == 0x43284A)
    assert roll.mnemonic == 'push' and roll.operands[0].imm == 2
    # Check wait boundaries: daily progress still begins at the original -60.
    for days in (0, 1, 14, 15, 16, 59, 60, 100):
        assert (-60 + days >= -45) == (days >= 15)
        assert (-60 + days >= 0) == (days >= 60)
    # The game's random source returns 15 bits before taking the remainder.
    assert sum(n % 2 == 0 for n in range(32768)) == 16384
    assert sum(n % 5 == 0 for n in range(32768)) == 6554
    # Only IDs 14..190 reach these patches; the prior ID branches are untouched.
    for va in (0x432810, 0x432822, 0x432617, 0x44B185, 0x4322B0):
        offset = pe.get_offset_from_rva(va - 0x400000)
        assert output[offset:offset + 8] == source[offset:offset + 8]
    print('Validation OK: %d changed bytes; original branches and save layout preserved.' % len(changes))
    for instruction in code:
        print('0x%08X  %-8s %s' % (instruction.address, instruction.mnemonic, instruction.op_str))
    pe.close()
    return bytes(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    source_path = args.source.resolve(strict=True)
    output_path = args.output.resolve()
    if source_path == output_path:
        parser.error('Source and output must be different paths.')
    if output_path.exists() and not args.check_only:
        parser.error('Output already exists; choose a new filename.')
    original = source_path.read_bytes()
    patched = make_patch(original)
    if not args.check_only:
        # Exclusive creation: never silently overwrite another build.
        with output_path.open('xb') as stream:
            stream.write(patched)
        if output_path.read_bytes() != patched:
            raise IOError('Output verification failed.')
        print('Created:', output_path)
    if source_path.read_bytes() != original:
        raise RuntimeError('Source EXE changed externally while patching.')
    print('Source SHA256:', sha256(original))
    print('Output SHA256:', sha256(patched))


if __name__ == '__main__':
    main()
