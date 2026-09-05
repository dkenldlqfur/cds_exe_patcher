"""Patch the verified CDS_95.EXE's three startup size presets and menu labels.

Keeps desktop/work-area filtering, height adjustment, rendering logic and all
other game code unchanged. An in-place patch requires --apply and first creates
an exclusive, byte-identical backup beside the executable.
"""

import argparse
from datetime import datetime
import hashlib
import os
from pathlib import Path
import struct
import tempfile

import capstone
import pefile


SOURCE_SHA256 = '7d6225191f276283881f799a149d6a6b6393b548c1b0d29fbfbf6613d69d3b6c'
PRESETS = (
    (0x560FF, 640, 1024, 0x560EE, 480, 768),
    (0x56113, 800, 1152, 0x56109, 592, 864),
    (0x56127, 1024, 1920, 0x5611D, 768, 997),
)
LABELS = (
    (0x147894, b' 640', b'1024'),
    (0x14787C, b'480 ', b'768 '),
    (0x14788C, b' 800', b'1152'),
    (0x147874, b'600 ', b'864 '),
    (0x147884, b'1024', b'1920'),
    (0x14786C, b'768 ', b'997 '),
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def patch_bytes(original):
    if digest(original) != SOURCE_SHA256:
        raise ValueError('Unexpected source hash; refusing to patch a different executable.')
    pe = pefile.PE(data=original)
    if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
        raise ValueError('Expected x86 executable with image base 0x400000.')
    output = bytearray(original)
    permitted = set()
    disassembler = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)

    def replace(offset, expected, replacement):
        if len(expected) != len(replacement) or original[offset:offset + len(expected)] != expected:
            raise ValueError('Unexpected bytes at file offset 0x%X' % offset)
        output[offset:offset + len(expected)] = replacement
        permitted.update(range(offset, offset + len(expected)))

    for width_off, old_width, width, height_off, old_height, height in PRESETS:
        for offset, before, after in ((width_off, old_width, width), (height_off, old_height, height)):
            if original[offset - 1] != 0x68:
                raise ValueError('Expected PUSH imm32 at 0x%X' % (offset - 1))
            replace(offset, struct.pack('<I', before), struct.pack('<I', after))
            va = pe.OPTIONAL_HEADER.ImageBase + pe.get_rva_from_offset(offset - 1)
            instruction = next(disassembler.disasm(bytes(output[offset - 1:offset + 4]), va))
            if instruction.mnemonic != 'push' or instruction.size != 5 or int(instruction.op_str, 0) != after:
                raise ValueError('Patched instruction verification failed.')
        print('Preset: %dx%d -> %dx%d' % (old_width, old_height, width, height))
    for offset, before, after in LABELS:
        replace(offset, before + b'\x00' * 4, after + b'\x00' * 4)

    changed = {i for i, (before, after) in enumerate(zip(original, output)) if before != after}
    if len(output) != len(original) or not changed or not changed <= permitted:
        raise ValueError('Changes outside the permitted immediate values/menu labels.')
    pe.close()
    print('Verified: %d bytes changed; file size and unrelated code preserved.' % len(changed))
    return bytes(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    target = args.exe.resolve(strict=True)
    original = target.read_bytes()
    patched = patch_bytes(original)
    print('Patched SHA256:', digest(patched))
    if not args.apply:
        print('Check only: executable unchanged.')
        return

    backup = target.with_name(target.name + '.before_resolution_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.bak')
    with backup.open('xb') as stream:
        stream.write(original)
    if backup.read_bytes() != original:
        raise IOError('Backup verification failed; executable unchanged.')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=str(target.parent), prefix='cds-resolution-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(patched)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.read_bytes() != patched or target.read_bytes() != original:
            raise IOError('Concurrent change or staged file mismatch; refusing replacement.')
        os.replace(str(temporary), str(target))
        temporary = None
        if target.read_bytes() != patched:
            raise IOError('Executable verification failed; restore the backup.')
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    print('Patched:', target)
    print('Original backup:', backup)


if __name__ == '__main__':
    main()
