"""Reversible combat item activation chances; never alter the shared RNG."""
from __future__ import annotations

from dataclasses import dataclass
import struct

import pefile


@dataclass(frozen=True)
class CombatItemChances:
    # None preserves the original formula. Shell's original formula is 40%.
    submarine_bomb: int | None = None
    rapid_cannon: int | None = None
    explosive_shell: int | None = None


BOMB_VA = 0x4365A6
BOMB_ORIGINAL = bytes.fromhex('8B861009000050E85D16080083C40450E8A716080083C404')
RAPID_VA = 0x441EB7
RAPID_ORIGINAL = bytes.fromhex(
    '8B0EBB0F0000008B8560F6FFFF8D0C898B0099F7FB03C851E88E5D070083C404'
)
SHELL_VA = 0x448E00
CONTEXTS = (
    (0x436597, bytes.fromhex('57B9A0605B00E82E68040085C0751C')),
    (0x4365BE, bytes.fromhex('85C075084783FF107CCFEB53')),
    (0x441EA7, bytes.fromhex('57B9A0605B00E81EAF030083F8017524')),
    (0x441ED7, bytes.fromhex('85C075084783FF107CC6EB0D')),
    (0x448DD4, bytes.fromhex('6A006A02E8A3E7FFFF85C0744C6A02B9A0605B00E83340030083F8FF8BF87439')),
    (0x448DF4, bytes.fromhex('6A64E814EE060083C40483F8')),
    (0x448E01, bytes.fromhex('732A')),
)


def _fixed_roll(va: int, size: int, percent: int) -> bytes:
    # Replace only local threshold calculation. Caller still performs its
    # original test/branch and all eligibility, inventory and effect handling.
    code = b'\x6A' + bytes([percent]) + b'\xE8'
    code += struct.pack('<i', 0x4B7C62 - (va + 7))
    code += b'\x83\xC4\x04'
    return code.ljust(size, b'\x90')


def _offsets(data: bytes | bytearray) -> tuple[int, int, int]:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        if (pe.FILE_HEADER.Machine != 0x14C
                or pe.OPTIONAL_HEADER.Magic != 0x10B
                or pe.OPTIONAL_HEADER.ImageBase != 0x400000):
            raise ValueError('지원하는 32비트 CDS III 실행 파일이 아닙니다.')
        for va, expected in CONTEXTS:
            pos = pe.get_offset_from_rva(va - 0x400000)
            if data[pos:pos + len(expected)] != expected:
                raise ValueError(f'전투 아이템 확률 코드 0x{va:X}을(를) 검증하지 못했습니다.')
        return tuple(pe.get_offset_from_rva(va - 0x400000)
                     for va in (BOMB_VA, RAPID_VA, SHELL_VA))
    finally:
        pe.close()


def read_combat_item_chances(data: bytes | bytearray) -> CombatItemChances:
    bomb_pos, rapid_pos, shell_pos = _offsets(data)
    values = []
    for pos, va, original in (
        (bomb_pos, BOMB_VA, BOMB_ORIGINAL),
        (rapid_pos, RAPID_VA, RAPID_ORIGINAL),
    ):
        current = bytes(data[pos:pos + len(original)])
        if current == original:
            values.append(None)
        elif (len(current) == len(original) and current[1] <= 100
              and current == _fixed_roll(va, len(original), current[1])):
            values.append(current[1])
        else:
            raise ValueError(f'전투 아이템 확률 패치 0x{va:X}의 상태를 검증하지 못했습니다.')
    shell = data[shell_pos]
    if shell > 100:
        raise ValueError('작렬탄 발동 확률이 0~100 범위가 아닙니다.')
    # Fixed 40% and original 40% are byte-identical; use original on readback.
    return CombatItemChances(*values, None if shell == 40 else shell)


def apply_combat_item_chances(data: bytearray, settings: CombatItemChances) -> bool:
    for value in (settings.submarine_bomb, settings.rapid_cannon, settings.explosive_shell):
        if value is not None and (type(value) is not int or not 0 <= value <= 100):
            raise ValueError('발동 확률은 0~100 사이의 정수여야 합니다.')
    read_combat_item_chances(data)  # Validate all sites before the first write.
    bomb_pos, rapid_pos, shell_pos = _offsets(data)
    writes = (
        (bomb_pos, BOMB_ORIGINAL if settings.submarine_bomb is None else
         _fixed_roll(BOMB_VA, len(BOMB_ORIGINAL), settings.submarine_bomb)),
        (rapid_pos, RAPID_ORIGINAL if settings.rapid_cannon is None else
         _fixed_roll(RAPID_VA, len(RAPID_ORIGINAL), settings.rapid_cannon)),
        (shell_pos, bytes([40 if settings.explosive_shell is None else settings.explosive_shell])),
    )
    changed = False
    for pos, payload in writes:
        if data[pos:pos + len(payload)] != payload:
            data[pos:pos + len(payload)] = payload
            changed = True
    return changed
