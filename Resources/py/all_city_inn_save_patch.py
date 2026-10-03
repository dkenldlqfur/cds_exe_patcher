"""Remove the nationality restriction on the city's Save/Suspend menu.

The native menu uses one boolean for both its label and its dispatch. Set
that boolean, leaving normal saving (including the optional ten-slot hook),
temporary-file handling, city ownership and player nationality untouched.
"""
from __future__ import annotations

import pefile


PATCH_VA = 0x4A290C
ORIGINAL = bytes.fromhex('1B F6 F7 DE')  # sbb esi,esi; neg esi
PATCHED = bytes.fromhex('8B F5 90 90')  # mov esi,ebp; nop; nop (EBP=1)
CONTEXTS = (
    (0x4A28B9, bytes.fromhex('BD 01 00 00 00')),
    # Get the city record, compare player nationality with city ownership.
    (0x4A28F4, bytes.fromhex('E8 C7 EE FF FF 8B D8 B9 A0 60 5B 00 FF 56 14')),
    (0x4A2903, bytes.fromhex('2B 03 3B C5 B8 A8 8D 56 00')),
    # Choose Save vs Suspend text, then use the same ESI at selection time.
    (0x4A2910, bytes.fromhex('85 F6 75 05 B8 B0 8D 56 00')),
    (0x4A294C, bytes.fromhex('8B CF 85 F6 74 07 E8 A9 FE FF FF EB 17 E8 72 FE FF FF')),
)


def _offset(data: bytes | bytearray) -> int:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        if (pe.FILE_HEADER.Machine != 0x14C
                or pe.OPTIONAL_HEADER.Magic != 0x10B
                or pe.OPTIONAL_HEADER.ImageBase != 0x400000):
            raise ValueError('지원하는 32비트 CDS III 실행 파일이 아닙니다.')
        for va, expected in CONTEXTS:
            offset = pe.get_offset_from_rva(va - 0x400000)
            if data[offset:offset+len(expected)] != expected:
                raise ValueError(f'여관 저장 국가 제한 코드 0x{va:X}을(를) 검증하지 못했습니다.')
        return pe.get_offset_from_rva(PATCH_VA - 0x400000)
    finally:
        pe.close()


def read_all_city_inn_save_state(data: bytes | bytearray) -> bool:
    offset = _offset(data)
    current = data[offset:offset+len(ORIGINAL)]
    if current == ORIGINAL:
        return False
    if current == PATCHED:
        return True
    raise ValueError('여관 저장 국가 제한 패치 상태를 검증하지 못했습니다.')


def apply_all_city_inn_save_patch(data: bytearray, enabled: bool) -> bool:
    if read_all_city_inn_save_state(data) == enabled:
        return False
    offset = _offset(data)
    # All validation precedes the only write; no section allocation is needed.
    data[offset:offset+len(ORIGINAL)] = PATCHED if enabled else ORIGINAL
    return True
