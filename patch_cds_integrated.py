"""Apply the supported CDS III executable tweaks as one verified update."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
import os
from pathlib import Path
import shutil
import struct
import tempfile
import ctypes
from ctypes import wintypes

import pefile

from patch_coordinate_decimal import (
    DECIMAL_FORMAT,
    KOREAN_DIRECTION_3_FORMAT,
    patch as patch_coordinate,
)
from pe_patch_section import (
    ECLIPSE_SLOT_OFFSET,
    ECLIPSE_SLOT_SIZE,
    MISTRANSLATION_SLOT_OFFSET,
    MISTRANSLATION_SLOT_SIZE,
    PIRATE_SLOT_OFFSET,
    PIRATE_SLOT_SIZE,
    clear_slot,
    ensure_patch_section,
    find_patch_section,
    write_slot,
)


# Each startup preset is a PUSH width/PUSH height pair.  Menu text has exactly
# four bytes available per number, hence the four-digit maximum below.
RESOLUTION_VALUES = (
    (0x560FF, 0x560EE),
    (0x56113, 0x56109),
    (0x56127, 0x5611D),
)
RESOLUTION_LABELS = (
    (0x147894, 0x14787C),
    (0x14788C, 0x147874),
    (0x147884, 0x14786C),
)
NPC_WAIT_VA = 0x43282D
NPC_ROLL_VA = 0x43284A
NPC_ORIGINAL_WAIT = bytes.fromhex("e8 ee fa ff ff 85 c0")
NPC_COMPARE_PREFIX = bytes.fromhex("83 be 10 01 00 00")
NPC_ACTIVITY_MIN_AGE_VA = 0x4322C8
NPC_ACTIVITY_MAX_AGE_VA = 0x4322D1

# Random naval-combat encounter denominators.  The western region produces
# pirate or pursuit-fleet encounters; the eastern region produces Islamic
# fleet encounters.  The game's RNG returns 0..32767, so larger denominators
# cannot reduce the actual probability any further.
WESTERN_COMBAT_ENCOUNTER_DENOMINATOR_VA = 0x48CAFC
ISLAMIC_COMBAT_ENCOUNTER_DENOMINATOR_VA = 0x48CB37
MAX_RANDOM_DENOMINATOR = 32768

# The Korean 1.2 pirate-selection block.  The community fix replaces this
# fixed west/east selection with a fame-tier roll and moves the overwritten
# original logic into a code cave.  This tool stores that cave in its dedicated
# executable .patch section, avoiding the very small tail padding in .text.
PIRATE_SELECTION_MAIN_VA = 0x48CBA9
PIRATE_SELECTION_MAIN_LENGTH = 59
PIRATE_SELECTION_CAVE_LENGTH = 77
PIRATE_WEIGHTED_MAGIC_V2 = b"CDSPIR2\0"
PIRATE_WEIGHTED_VERSION_V2 = 2
PIRATE_WEIGHTED_MAGIC = b"CDSPIR3\0"
PIRATE_WEIGHTED_VERSION = 3
PIRATE_WEIGHTED_CONFIG_SIZE = 0x40
PIRATE_FAME_ADDRESS = 0x5B614C
PIRATE_RANDOM_VA = 0x4B7C0F
PIRATE_RECORD_VA = 0x4AD830
PIRATE_SELECTION_RESUME_VA = 0x48CBE1
PIRATE_FAME_MIDDLE = 5000
PIRATE_FAME_HIGH = 10000
PIRATE_WESTERN_BASE_ID = 0x106
PIRATE_EASTERN_BASE_ID = 0x109
PIRATE_PURSUIT_BASE_ID = 0x10C
PIRATE_PURSUIT_MIDDLE_THRESHOLD = 0x29
PIRATE_PURSUIT_HIGH_THRESHOLD = 0x51
PIRATE_STAGE2_PROBABILITIES = (50, 50)
PIRATE_STAGE3_PROBABILITIES = (34, 33, 33)
ORIGINAL_PIRATE_SELECTION = bytes.fromhex(
    "81 BC 24 34 01 00 00 BC 02 00 00 75 24 BE 06 01 00 00 85 DB "
    "74 20 BE 0C 01 00 00 8B CB E8 65 0C 02 00 83 78 20 51 7D "
    "0E 8B CB E8 58 0C 02 00 EB 05 BE 09 01 00 00 6A 01 6A 00 56"
)


@dataclass(frozen=True)
class PirateVarietySettings:
    fame_middle: int = PIRATE_FAME_MIDDLE
    fame_high: int = PIRATE_FAME_HIGH
    western_stage2_first_probability: int = PIRATE_STAGE2_PROBABILITIES[0]
    western_stage2_second_probability: int = PIRATE_STAGE2_PROBABILITIES[1]
    western_stage3_first_probability: int = PIRATE_STAGE3_PROBABILITIES[0]
    western_stage3_second_probability: int = PIRATE_STAGE3_PROBABILITIES[1]
    western_stage3_third_probability: int = PIRATE_STAGE3_PROBABILITIES[2]
    eastern_stage2_first_probability: int = PIRATE_STAGE2_PROBABILITIES[0]
    eastern_stage2_second_probability: int = PIRATE_STAGE2_PROBABILITIES[1]
    eastern_stage3_first_probability: int = PIRATE_STAGE3_PROBABILITIES[0]
    eastern_stage3_second_probability: int = PIRATE_STAGE3_PROBABILITIES[1]
    eastern_stage3_third_probability: int = PIRATE_STAGE3_PROBABILITIES[2]
    western_base_id: int = PIRATE_WESTERN_BASE_ID
    eastern_base_id: int = PIRATE_EASTERN_BASE_ID
    pursuit_base_id: int = PIRATE_PURSUIT_BASE_ID
    pursuit_middle_threshold: int = PIRATE_PURSUIT_MIDDLE_THRESHOLD
    pursuit_high_threshold: int = PIRATE_PURSUIT_HIGH_THRESHOLD


DEFAULT_PIRATE_VARIETY_SETTINGS = PirateVarietySettings()


def validate_pirate_variety_settings(settings: PirateVarietySettings) -> None:
    if not 0 <= settings.fame_middle < settings.fame_high <= 0xFFFF:
        raise ValueError("명성 단계 기준은 0~65,535 사이이며 중간 기준이 상위 기준보다 작아야 합니다.")
    probability_groups = (
        ("서부 2단계", (settings.western_stage2_first_probability, settings.western_stage2_second_probability)),
        ("서부 3단계", (
            settings.western_stage3_first_probability,
            settings.western_stage3_second_probability,
            settings.western_stage3_third_probability,
        )),
        ("동부 2단계", (settings.eastern_stage2_first_probability, settings.eastern_stage2_second_probability)),
        ("동부 3단계", (
            settings.eastern_stage3_first_probability,
            settings.eastern_stage3_second_probability,
            settings.eastern_stage3_third_probability,
        )),
    )
    for label, probabilities in probability_groups:
        if any(not 0 <= value <= 100 for value in probabilities):
            raise ValueError(f"{label} 함대 출현 확률은 각각 0~100% 사이여야 합니다.")
        if sum(probabilities) != 100:
            raise ValueError(f"{label} 함대 출현 확률 합계는 100%여야 합니다.")
    max_variants = 3
    for label, base_id, count in (
        ("서부 기본 함대 ID", settings.western_base_id, max_variants),
        ("동부 기본 함대 ID", settings.eastern_base_id, max_variants),
        ("추격대 기본 함대 ID", settings.pursuit_base_id, 3),
    ):
        if not 0 <= base_id <= 0xFFFF or base_id + count - 1 > 0xFFFF:
            raise ValueError(f"{label}와 연속 함대 범위가 0~65,535를 벗어납니다.")
    pursuit_delta = settings.pursuit_base_id - settings.western_base_id
    if not -128 <= pursuit_delta <= 127:
        raise ValueError("추격대 기본 함대 ID는 서부 기본 함대 ID에서 ±127 범위여야 합니다.")
    if not 0 <= settings.pursuit_middle_threshold < settings.pursuit_high_threshold <= 127:
        raise ValueError("추격대 단계 경계는 0~127 사이이며 중간 경계가 상위 경계보다 작아야 합니다.")

# Gameplay option operands.  These virtual addresses point to the immediate
# value rather than the beginning of the instruction so they can be changed
# without rewriting the surrounding code.
LONG_REST_MAX_VA = 0x460783
EXPLORATION_PREPARATION_VAS = (0x468759, 0x468785, 0x4770F1, 0x477325)
# The city-gate confirmation is a literal string, rather than a formatted
# message.  Keep its displayed number in sync with the four timing operands.
EXPLORATION_PREPARATION_MESSAGE_VA = 0x551B78
EXPLORATION_PREPARATION_MESSAGE_SIZE = 0x38
EXPLORATION_PREPARATION_MESSAGE_PREFIX = "탐험을 떠납니까? 준비하는데 "
EXPLORATION_PREPARATION_MESSAGE_SUFFIX = "일 걸립니다. 좋습니까?"
SUCCESSION_MIN_AGE_VA = 0x461AF6
# The latitude coordinate grows southward: values below 10,000 are north and
# values at/above 10,000 are south.  The first conditional operand therefore
# belongs to the southern branch, and the second to the northern branch.
COLD_SOUTH_LIMIT_VA = 0x48D6CB
COLD_NORTH_LIMIT_VA = 0x48D6D2
COLD_LATITUDE_DEGREES_PER_UNIT = Decimal("0.009")
COLD_LATITUDE_MAX = Decimal("180.000")
COLD_LIMIT_DISABLED_VALUE = 10001

# The original eclipse gate is four rectangle comparisons at 0x427EAE:
# longitude lower/upper and latitude lower/upper.  Its longitude comparisons
# are reversed, so the rectangle cannot be entered.  A polar-cap patch uses
# the same 64-byte block for `latitude <= north OR latitude >= south` and keeps
# the overwritten block in .patch so disabling it restores the exact source
# executable (original Korean or community-integrated rectangle).
ECLIPSE_GATE_OFFSET = 0x272AE
ECLIPSE_GATE_VA = 0x427EAE
ECLIPSE_GATE_SIZE = 64
ECLIPSE_EVENT_VA = 0x427EEE
ECLIPSE_REJECT_VA = 0x427FCA
ECLIPSE_LATITUDE_ADDRESS = 0x5B63B4
ECLIPSE_MAGIC = b"CDSECL1\0"
ECLIPSE_COORDINATE_PER_DEGREE = Decimal(1000) / Decimal(9)


def _is_eclipse_rectangle_gate(block: bytes) -> bool:
    if len(block) != ECLIPSE_GATE_SIZE:
        return False
    addresses = (0x5B63B0, 0x5B63B0, 0x5B63B4, 0x5B63B4)
    branch_targets = (ECLIPSE_REJECT_VA,) * 4
    for index, (address, target) in enumerate(zip(addresses, branch_targets)):
        start = index * 16
        if block[start:start + 2] != b"\x81\x3D":
            return False
        if struct.unpack_from("<I", block, start + 2)[0] != address:
            return False
        if block[start + 10] != 0x0F or block[start + 11] not in (0x8C, 0x8D, 0x8E, 0x8F):
            return False
        displacement = struct.unpack_from("<i", block, start + 12)[0]
        if ECLIPSE_GATE_VA + start + 16 + displacement != target:
            return False
    return True


def _eclipse_limits(latitude: Decimal) -> tuple[int, int]:
    if not latitude.is_finite() or not Decimal("0") <= latitude <= Decimal("90"):
        raise ValueError("일식 관측 위도는 0~90도 사이여야 합니다.")
    distance = int((latitude * ECLIPSE_COORDINATE_PER_DEGREE).to_integral_value(rounding=ROUND_CEILING))
    return 10000 - distance, 10000 + distance


def _eclipse_gate(latitude: Decimal) -> bytes:
    north_limit, south_limit = _eclipse_limits(latitude)
    code = bytearray()

    def compare_and_jump(limit: int, condition: int, target: int) -> None:
        instruction_va = ECLIPSE_GATE_VA + len(code)
        code.extend(b"\x81\x3D" + struct.pack("<II", ECLIPSE_LATITUDE_ADDRESS, limit))
        jump_va = instruction_va + 10
        code.extend(b"\x0F" + bytes((condition,)) + struct.pack("<i", target - (jump_va + 6)))

    compare_and_jump(north_limit, 0x8E, ECLIPSE_EVENT_VA)  # JLE: north polar cap
    compare_and_jump(south_limit, 0x8D, ECLIPSE_EVENT_VA)  # JGE: south polar cap
    jump_va = ECLIPSE_GATE_VA + len(code)
    code.extend(b"\xE9" + struct.pack("<i", ECLIPSE_REJECT_VA - (jump_va + 5)))
    if len(code) > ECLIPSE_GATE_SIZE:
        raise AssertionError("일식 관측 패치가 원래 판정 블록보다 큽니다.")
    return bytes(code).ljust(ECLIPSE_GATE_SIZE, b"\x90")


def _parse_eclipse_latitude(value: str | int | float | Decimal) -> Decimal:
    try:
        latitude = Decimal(str(value).strip()).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError("일식 관측 위도는 숫자로 입력해야 합니다.")
    _eclipse_limits(latitude)
    return latitude


def _eclipse_patch_info(data: bytes) -> tuple[bool, Decimal, bytes | None]:
    section = find_patch_section(data)
    if section is None:
        return False, Decimal("0"), None
    slot_offset, _ = section.slot(ECLIPSE_SLOT_OFFSET, ECLIPSE_SLOT_SIZE)
    header_size = len(ECLIPSE_MAGIC) + 4
    if data[slot_offset:slot_offset + len(ECLIPSE_MAGIC)] != ECLIPSE_MAGIC:
        return False, Decimal("0"), None
    latitude_millidegrees = struct.unpack_from("<I", data, slot_offset + len(ECLIPSE_MAGIC))[0]
    if latitude_millidegrees > 90000:
        raise ValueError("일식 관측 패치의 위도 설정값이 손상되었습니다.")
    latitude = Decimal(latitude_millidegrees) / Decimal(1000)
    original_gate = data[slot_offset + header_size:slot_offset + header_size + ECLIPSE_GATE_SIZE]
    if not _is_eclipse_rectangle_gate(original_gate):
        raise ValueError("일식 관측 패치의 원본 판정 코드가 손상되었습니다.")
    current_gate = data[ECLIPSE_GATE_OFFSET:ECLIPSE_GATE_OFFSET + ECLIPSE_GATE_SIZE]
    if current_gate != _eclipse_gate(latitude):
        raise ValueError("일식 관측 패치 코드와 설정값이 일치하지 않습니다.")
    return True, latitude, original_gate


def apply_eclipse_polar_caps(
    data: bytearray,
    enabled: bool,
    latitude: str | int | float | Decimal = Decimal("67"),
) -> bool:
    """Enable eclipse observations in both polar caps at the chosen latitude."""
    current_enabled, current_latitude, original_gate = _eclipse_patch_info(bytes(data))
    if not enabled:
        if not current_enabled:
            return False
        assert original_gate is not None
        data[ECLIPSE_GATE_OFFSET:ECLIPSE_GATE_OFFSET + ECLIPSE_GATE_SIZE] = original_gate
        section = find_patch_section(data)
        assert section is not None
        clear_slot(data, section, ECLIPSE_SLOT_OFFSET, ECLIPSE_SLOT_SIZE)
        return True

    requested_latitude = _parse_eclipse_latitude(latitude)
    if current_enabled:
        assert original_gate is not None
        if current_latitude == requested_latitude:
            return False
    else:
        original_gate = bytes(data[ECLIPSE_GATE_OFFSET:ECLIPSE_GATE_OFFSET + ECLIPSE_GATE_SIZE])
        if not _is_eclipse_rectangle_gate(original_gate):
            raise ValueError("일식 발생 영역의 원본 판정 코드를 검증하지 못했습니다.")

    section, _ = ensure_patch_section(data)
    latitude_millidegrees = int(requested_latitude * 1000)
    payload = ECLIPSE_MAGIC + struct.pack("<I", latitude_millidegrees) + original_gate
    clear_slot(data, section, ECLIPSE_SLOT_OFFSET, ECLIPSE_SLOT_SIZE)
    write_slot(data, section, ECLIPSE_SLOT_OFFSET, ECLIPSE_SLOT_SIZE, payload)
    data[ECLIPSE_GATE_OFFSET:ECLIPSE_GATE_OFFSET + ECLIPSE_GATE_SIZE] = _eclipse_gate(requested_latitude)
    return True

# Korean localization corrections confirmed by comparing the original Korean
# executable with the community integrated edition.  File offsets are stable
# for the supported executable because adding .patch appends a new section and
# does not move any existing section data.
MISTRANSLATION_REPLACEMENTS = (
    *((offset, "선두상", "선수상") for offset in (
        0x12EB20, 0x12F3CE, 0x12F46B, 0x12F494, 0x12F4E0, 0x12F547,
        0x12F5D0, 0x12FA78, 0x12FB70, 0x12FB81, 0x142DB8, 0x14DCF5,
    )),
    (0x12F3D7, "놓아 가고", "놓아 두고"),
    *((offset, "르완다", "루안다") for offset in (0x13A24C, 0x162830)),
    *((offset, "군관조", "군함조") for offset in (0x13C59A, 0x1410E8, 0x155CD6, 0x17B3E8)),
    *((offset, "사교", "주교") for offset in (0x13EBA6, 0x13EBB3, 0x15FCE5, 0x161BF7, 0x165268, 0x175C99)),
    (0x141180, "식충동물", "식충식물"),
    (0x146D1C, "라이스", "레이스"),
    (0x149FE8, "예하", "성하"),
    (0x153F28, "주탄동자", "슈텐도지"),
    (0x155CD4, "큰군관조", "큰군함조"),
    (0x15E0DC, "웅변", "변론"),
    (0x1655C0, "궩갂궩귪궶갂긫긇궶갏  딲뾩궩귢귩묿궔귞빓궋궫갏갎", "그런 말도 안되는…이 자식 그거 누구한테 들었어！"),
    *((offset, "규칙", "규율") for offset in (0x166610, 0x169618, 0x1697B0, 0x16D000)),
    (0x17A214, "항주", "남경"),
    (0x17A7F9, "남안", "서안"),
    (0x17B930, "개미지옥", "파리지옥"),
    (0x17C8FA, "시에라리온", "베르데　곶"),
    *((offset, "북동쪽", "북서쪽") for offset in (0x17C96F, 0x17CE36)),
)
# Corrections removed from the current patch.  Accept the old patched form so
# executables produced by an earlier patcher can be migrated, but always write
# the original bytes on the next apply.
MISTRANSLATION_RETIRED_REPLACEMENTS = (
    (0x1664A5, "중단", "계속"),
)
MISTRANSLATION_SWORD_TEXT_OFFSET = 0x156080
MISTRANSLATION_SWORD_TEXT_CAPACITY = 16
MISTRANSLATION_SWORD_POINTER_OFFSET = 0xFC204
MISTRANSLATION_SWORD_ORIGINAL_VA = 0x558880
MISTRANSLATION_SWORD_ORIGINAL = "아이베는 안강"
MISTRANSLATION_SWORD_INTEGRATED = "도지기리 안강"
MISTRANSLATION_SWORD_CORRECTED = "도지기리 야스츠나"
MISTRANSLATION_MAGIC = b"CDSTRN1\0"


def _translation_bytes(text: str) -> bytes:
    return text.encode("cp949")


def _validate_mistranslation_layout(data: bytes | bytearray) -> None:
    for offset, original, corrected in (
        *MISTRANSLATION_REPLACEMENTS,
        *MISTRANSLATION_RETIRED_REPLACEMENTS,
    ):
        original_bytes = _translation_bytes(original)
        corrected_bytes = _translation_bytes(corrected)
        if len(original_bytes) != len(corrected_bytes):
            raise AssertionError(f"오역 수정 문자열 길이가 다릅니다: {original} / {corrected}")
        current = bytes(data[offset:offset + len(original_bytes)])
        if current not in (original_bytes, corrected_bytes):
            raise ValueError(f"오역 수정 위치 0x{offset:X}의 문자열을 검증하지 못했습니다.")

    sword_field = bytes(data[
        MISTRANSLATION_SWORD_TEXT_OFFSET:
        MISTRANSLATION_SWORD_TEXT_OFFSET + MISTRANSLATION_SWORD_TEXT_CAPACITY
    ]).rstrip(b"\0")
    allowed_sword_fields = {
        _translation_bytes(MISTRANSLATION_SWORD_ORIGINAL),
        _translation_bytes(MISTRANSLATION_SWORD_INTEGRATED),
    }
    if sword_field not in allowed_sword_fields:
        raise ValueError("도지기리 야스츠나 이름 위치를 검증하지 못했습니다.")

    pointer = struct.unpack_from("<I", data, MISTRANSLATION_SWORD_POINTER_OFFSET)[0]
    allowed_pointers = {MISTRANSLATION_SWORD_ORIGINAL_VA}
    section = find_patch_section(data)
    if section is not None:
        _, slot_va = section.slot(MISTRANSLATION_SLOT_OFFSET, MISTRANSLATION_SLOT_SIZE)
        allowed_pointers.add(slot_va + len(MISTRANSLATION_MAGIC))
    if pointer not in allowed_pointers:
        raise ValueError("도지기리 야스츠나 이름 포인터를 검증하지 못했습니다.")


def read_mistranslation_patch_state(data: bytes) -> bool:
    """Return whether every localization correction is fully applied."""
    _validate_mistranslation_layout(data)
    if any(
        data[offset:offset + len(_translation_bytes(corrected))] != _translation_bytes(corrected)
        for offset, _, corrected in MISTRANSLATION_REPLACEMENTS
    ):
        return False

    pointer = struct.unpack_from("<I", data, MISTRANSLATION_SWORD_POINTER_OFFSET)[0]
    section = find_patch_section(data)
    if section is None:
        return False
    slot_offset, slot_va = section.slot(MISTRANSLATION_SLOT_OFFSET, MISTRANSLATION_SLOT_SIZE)
    payload = MISTRANSLATION_MAGIC + _translation_bytes(MISTRANSLATION_SWORD_CORRECTED) + b"\0"
    return (
        pointer == slot_va + len(MISTRANSLATION_MAGIC)
        and data[slot_offset:slot_offset + len(payload)] == payload
    )


def apply_mistranslation_fixes(data: bytearray, enabled: bool) -> bool:
    """Apply or remove the verified Korean localization corrections."""
    _validate_mistranslation_layout(data)
    was_enabled = read_mistranslation_patch_state(bytes(data))
    changed = was_enabled != enabled

    for offset, original, corrected in MISTRANSLATION_REPLACEMENTS:
        replacement = _translation_bytes(corrected if enabled else original)
        if data[offset:offset + len(replacement)] != replacement:
            data[offset:offset + len(replacement)] = replacement
            changed = True

    for offset, original, _ in MISTRANSLATION_RETIRED_REPLACEMENTS:
        replacement = _translation_bytes(original)
        if data[offset:offset + len(replacement)] != replacement:
            data[offset:offset + len(replacement)] = replacement
            changed = True

    original_sword = _translation_bytes(MISTRANSLATION_SWORD_ORIGINAL)
    sword_field = original_sword.ljust(MISTRANSLATION_SWORD_TEXT_CAPACITY, b"\0")
    current_field = data[
        MISTRANSLATION_SWORD_TEXT_OFFSET:
        MISTRANSLATION_SWORD_TEXT_OFFSET + MISTRANSLATION_SWORD_TEXT_CAPACITY
    ]
    if current_field != sword_field:
        data[
            MISTRANSLATION_SWORD_TEXT_OFFSET:
            MISTRANSLATION_SWORD_TEXT_OFFSET + MISTRANSLATION_SWORD_TEXT_CAPACITY
        ] = sword_field
        changed = True

    if enabled:
        section, created = ensure_patch_section(data)
        payload = MISTRANSLATION_MAGIC + _translation_bytes(MISTRANSLATION_SWORD_CORRECTED) + b"\0"
        slot_offset, slot_va = section.slot(MISTRANSLATION_SLOT_OFFSET, MISTRANSLATION_SLOT_SIZE)
        target_pointer = slot_va + len(MISTRANSLATION_MAGIC)
        if data[slot_offset:slot_offset + len(payload)] != payload:
            clear_slot(data, section, MISTRANSLATION_SLOT_OFFSET, MISTRANSLATION_SLOT_SIZE)
            write_slot(data, section, MISTRANSLATION_SLOT_OFFSET, MISTRANSLATION_SLOT_SIZE, payload)
            changed = True
        if struct.unpack_from("<I", data, MISTRANSLATION_SWORD_POINTER_OFFSET)[0] != target_pointer:
            struct.pack_into("<I", data, MISTRANSLATION_SWORD_POINTER_OFFSET, target_pointer)
            changed = True
        return changed or created

    if struct.unpack_from("<I", data, MISTRANSLATION_SWORD_POINTER_OFFSET)[0] != MISTRANSLATION_SWORD_ORIGINAL_VA:
        struct.pack_into("<I", data, MISTRANSLATION_SWORD_POINTER_OFFSET, MISTRANSLATION_SWORD_ORIGINAL_VA)
        changed = True
    section = find_patch_section(data)
    if section is not None:
        slot_offset, _ = section.slot(MISTRANSLATION_SLOT_OFFSET, MISTRANSLATION_SLOT_SIZE)
        if any(data[slot_offset:slot_offset + MISTRANSLATION_SLOT_SIZE]):
            clear_slot(data, section, MISTRANSLATION_SLOT_OFFSET, MISTRANSLATION_SLOT_SIZE)
            changed = True
    return changed


def cold_limit_to_latitude(cold_limit: int) -> Decimal:
    """Convert the game's equator-distance operand to latitude degrees."""
    if not 0 <= cold_limit <= 20000:
        raise ValueError("극지방 추위 판정 내부 좌표는 0~20,000 사이여야 합니다.")
    return Decimal(cold_limit) * COLD_LATITUDE_DEGREES_PER_UNIT


def latitude_to_cold_limit(latitude: str | int | float | Decimal) -> int:
    """Convert latitude degrees to the nearest representable game operand."""
    try:
        value = Decimal(str(latitude).strip())
    except (InvalidOperation, ValueError):
        raise ValueError("극지방 추위 판정 위도는 숫자로 입력해야 합니다.")
    if not value.is_finite() or not Decimal("0") <= value <= COLD_LATITUDE_MAX:
        raise ValueError("극지방 추위 판정 위도는 0~180도 사이여야 합니다.")
    return int((value / COLD_LATITUDE_DEGREES_PER_UNIT).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _is_resolution_label(value: bytes) -> bool:
    return len(value) == 8 and value[4:] == b"\0" * 4 and all(byte in b" 0123456789" for byte in value[:4])


def get_screen_bounds() -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    """Reproduce the display bounds used by the game's 0x456EA4 filter."""
    user32 = ctypes.windll.user32
    screen = (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
    work = wintypes.RECT()
    if user32.SystemParametersInfoW(48, 0, ctypes.byref(work), 0):  # SPI_GETWORKAREA
        work_area = (work.right - work.left, work.bottom - work.top)
    else:
        # Same fallback as the game when SPI_GETWORKAREA is unavailable.
        work_area = screen
    # 0x4B9489 obtains the work area, then subtracts SM_CYMENU (15) and
    # SM_CYCAPTION (4).  0x456EA4 rejects a candidate whose height is larger
    # than this value; the preceding 0x456E84 check does the same for width.
    menu_height = user32.GetSystemMetrics(15)
    caption_height = user32.GetSystemMetrics(4)
    game_area = (work_area[0], max(0, work_area[1] - menu_height - caption_height))
    return screen, work_area, game_area


def validate_presets(presets: tuple[tuple[int, int], ...]) -> None:
    if len(presets) != 3:
        raise ValueError("해상도는 첫 번째부터 세 번째까지 모두 입력해야 합니다.")
    for index, (width, height) in enumerate(presets, start=1):
        if not (1 <= width <= 9999 and 1 <= height <= 9999):
            raise ValueError(
                f"{index}번 해상도 {width}×{height}은(는) 지원 범위를 벗어납니다. "
                "메뉴에 표시할 수 있는 1~9999 사이여야 합니다."
            )
    _, work_area, game_area = get_screen_bounds()
    for index, (width, height) in enumerate(presets, start=1):
        if width > game_area[0] or height > game_area[1]:
            raise ValueError(
                f"{index}번 해상도 {width}×{height}은(는) 게임의 허용 크기 {game_area[0]}×{game_area[1]}보다 큽니다. "
                f"(0x456E84/0x456EA4 기준, Windows 작업 영역 {work_area[0]}×{work_area[1]})"
            )


def apply_resolution(data: bytearray, presets: tuple[tuple[int, int], ...]) -> bool:
    """Set the three startup resolution choices after validating their bounds."""
    validate_presets(presets)
    changed = False
    for (width_offset, height_offset), (label_width, label_height), (width, height) in zip(RESOLUTION_VALUES, RESOLUTION_LABELS, presets):
        for offset, target in ((width_offset, width), (height_offset, height)):
            if data[offset - 1] != 0x68:
                raise ValueError(f"해상도 설정 위치 0x{offset:X}의 명령을 검증하지 못했습니다.")
            if struct.unpack_from("<I", data, offset)[0] != target:
                struct.pack_into("<I", data, offset, target)
                changed = True
        for offset, value in ((label_width, width), (label_height, height)):
            current = bytes(data[offset:offset + 8])
            if not _is_resolution_label(current):
                raise ValueError(f"해상도 메뉴 문자열 위치 0x{offset:X}을(를) 검증하지 못했습니다.")
            target = f"{value:>4}".encode("ascii") + b"\0" * 4
            if current != target:
                data[offset:offset + 8] = target
                changed = True
    return changed


def apply_npc_travel(data: bytearray, departure_denominator: int, arrival_wait_days: int) -> bool:
    """Set ordinary-NPC departure odds and the arrival-eligibility wait."""
    if not 1 <= departure_denominator <= 127:
        raise ValueError("일반 NPC 출발 확률의 분모는 1~127 사이여야 합니다.")
    if not 0 <= arrival_wait_days <= 60:
        raise ValueError("일반 NPC 도착 대기는 0~60일 사이여야 합니다.")
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        wait_offset = pe.get_offset_from_rva(NPC_WAIT_VA - pe.OPTIONAL_HEADER.ImageBase)
        roll_offset = pe.get_offset_from_rva(NPC_ROLL_VA - pe.OPTIONAL_HEADER.ImageBase)
        current_wait = bytes(data[wait_offset:wait_offset + 7])
        if current_wait != NPC_ORIGINAL_WAIT and not (
            current_wait[:6] == NPC_COMPARE_PREFIX and -60 <= struct.unpack("b", current_wait[6:7])[0] <= 0
        ):
            raise ValueError(f"NPC 이동 루틴 0x{NPC_WAIT_VA:X}을(를) 검증하지 못했습니다.")
        current_roll = bytes(data[roll_offset:roll_offset + 2])
        if current_roll[0] != 0x6A or not 1 <= current_roll[1] <= 127:
            raise ValueError(f"NPC 이동 확률 위치 0x{NPC_ROLL_VA:X}을(를) 검증하지 못했습니다.")
        target_wait = NPC_COMPARE_PREFIX + struct.pack("b", arrival_wait_days - 60)
        target_roll = bytes((0x6A, departure_denominator))
        changed = current_wait != target_wait or current_roll != target_roll
        data[wait_offset:wait_offset + 7] = target_wait
        data[roll_offset:roll_offset + 2] = target_roll
        return changed
    finally:
        pe.close()


def _read_npc_activity_ages_from_data(data: bytes) -> tuple[int, int]:
    """Read the inclusive age range used by the NPC activity predicate."""
    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        minimum_offset = pe.get_offset_from_rva(NPC_ACTIVITY_MIN_AGE_VA - pe.OPTIONAL_HEADER.ImageBase)
        maximum_offset = pe.get_offset_from_rva(NPC_ACTIVITY_MAX_AGE_VA - pe.OPTIONAL_HEADER.ImageBase)
        if data[minimum_offset - 2:minimum_offset] != bytes.fromhex("83 f8") or data[minimum_offset + 1] != 0x7C:
            raise ValueError(f"NPC 활동 최소 나이 위치 0x{NPC_ACTIVITY_MIN_AGE_VA:X}을(를) 검증하지 못했습니다.")
        if data[maximum_offset - 2:maximum_offset] != bytes.fromhex("83 f8") or data[maximum_offset + 1] != 0x7F:
            raise ValueError(f"NPC 활동 최대 나이 위치 0x{NPC_ACTIVITY_MAX_AGE_VA:X}을(를) 검증하지 못했습니다.")
        return data[minimum_offset], data[maximum_offset]
    finally:
        pe.close()


def apply_npc_activity_ages(data: bytearray, minimum_age: int, maximum_age: int) -> bool:
    """Set the inclusive NPC activity age range."""
    if not 0 <= minimum_age <= 127 or not 0 <= maximum_age <= 127:
        raise ValueError("NPC 활동 나이는 0~127세 사이여야 합니다.")
    if minimum_age > maximum_age:
        raise ValueError("NPC 활동 최소 나이는 최대 나이보다 클 수 없습니다.")
    current = _read_npc_activity_ages_from_data(bytes(data))
    if current == (minimum_age, maximum_age):
        return False
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        data[pe.get_offset_from_rva(NPC_ACTIVITY_MIN_AGE_VA - pe.OPTIONAL_HEADER.ImageBase)] = minimum_age
        data[pe.get_offset_from_rva(NPC_ACTIVITY_MAX_AGE_VA - pe.OPTIONAL_HEADER.ImageBase)] = maximum_age
        return True
    finally:
        pe.close()


def _read_combat_encounter_denominators_from_data(data: bytes) -> tuple[int, int]:
    """Read western and Islamic naval-combat encounter denominators."""
    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        prefix = bytes.fromhex("c7 84 24 34 01 00 00")
        values: list[int] = []
        for label, va in (
            ("서부 해역 전투 인카운트", WESTERN_COMBAT_ENCOUNTER_DENOMINATOR_VA),
            ("이슬람 함대 인카운트", ISLAMIC_COMBAT_ENCOUNTER_DENOMINATOR_VA),
        ):
            option_offset = pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)
            if data[option_offset - len(prefix):option_offset] != prefix:
                raise ValueError(f"{label} 위치 0x{va:X}을(를) 검증하지 못했습니다.")
            values.append(struct.unpack_from("<I", data, option_offset)[0])
        return values[0], values[1]
    finally:
        pe.close()


def apply_combat_encounter_denominators(
    data: bytearray,
    western_denominator: int,
    islamic_denominator: int,
) -> bool:
    """Set random naval-combat encounter denominators."""
    if not 1 <= western_denominator <= MAX_RANDOM_DENOMINATOR:
        raise ValueError(f"서부 해역 전투 인카운트 분모는 1~{MAX_RANDOM_DENOMINATOR:,} 사이여야 합니다.")
    if not 1 <= islamic_denominator <= MAX_RANDOM_DENOMINATOR:
        raise ValueError(f"이슬람 함대 인카운트 분모는 1~{MAX_RANDOM_DENOMINATOR:,} 사이여야 합니다.")
    current = _read_combat_encounter_denominators_from_data(bytes(data))
    target = (western_denominator, islamic_denominator)
    if current == target:
        return False
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        struct.pack_into(
            "<I", data,
            pe.get_offset_from_rva(WESTERN_COMBAT_ENCOUNTER_DENOMINATOR_VA - pe.OPTIONAL_HEADER.ImageBase),
            western_denominator,
        )
        struct.pack_into(
            "<I", data,
            pe.get_offset_from_rva(ISLAMIC_COMBAT_ENCOUNTER_DENOMINATOR_VA - pe.OPTIONAL_HEADER.ImageBase),
            islamic_denominator,
        )
        return True
    finally:
        pe.close()


def _original_pirate_selection(western_denominator: int) -> bytes:
    code = bytearray(ORIGINAL_PIRATE_SELECTION)
    struct.pack_into("<I", code, 7, western_denominator)
    return bytes(code)


def _is_original_pirate_selection(code: bytes) -> bool:
    """Recognize the original block while allowing its west denominator."""
    return (
        len(code) == PIRATE_SELECTION_MAIN_LENGTH
        and code[:7] == ORIGINAL_PIRATE_SELECTION[:7]
        and code[11:] == ORIGINAL_PIRATE_SELECTION[11:]
    )


def _relative_displacement(source_va: int, instruction_offset: int, target_va: int) -> int:
    return target_va - (source_va + instruction_offset + 5)


def _build_legacy_pirate_selection_main(
    cave_va: int,
    fame_middle: int,
    fame_high: int,
    variant_counts: tuple[int, int, int],
) -> bytes:
    """Build the previous uniform-selection patch for compatibility checks."""
    low_count, middle_count, high_count = variant_counts
    code = bytearray(bytes.fromhex(
        "81 3D 4C 61 5B 00 10 27 00 00 7C 07 B8 03 00 00 00 EB 16 "
        "81 3D 4C 61 5B 00 88 13 00 00 B8 02 00 00 00 7D 05 B8 "
        "01 00 00 00 50 E8 00 00 00 00 83 C4 04 E9 00 00 00 00 "
        "90 6A 00 50"
    ))
    struct.pack_into("<I", code, 2, PIRATE_FAME_ADDRESS)
    struct.pack_into("<I", code, 6, fame_high)
    struct.pack_into("<I", code, 13, high_count)
    struct.pack_into("<I", code, 21, PIRATE_FAME_ADDRESS)
    struct.pack_into("<I", code, 25, fame_middle)
    struct.pack_into("<I", code, 30, middle_count)
    struct.pack_into("<I", code, 37, low_count)
    struct.pack_into("<i", code, 43, _relative_displacement(PIRATE_SELECTION_MAIN_VA, 42, PIRATE_RANDOM_VA))
    struct.pack_into("<i", code, 51, _relative_displacement(PIRATE_SELECTION_MAIN_VA, 50, cave_va))
    return bytes(code)


def _build_legacy_pirate_selection_cave(
    cave_va: int,
    western_denominator: int,
    western_base_id: int,
    eastern_base_id: int,
    pursuit_base_id: int,
    pursuit_middle_threshold: int,
    pursuit_high_threshold: int,
) -> bytes:
    code = bytearray(bytes.fromhex(
        "81 BC 24 34 01 00 00 BC 02 00 00 75 32 BE 06 01 00 00 85 "
        "DB 74 2E 83 C6 06 8B CB E8 00 00 00 00 83 78 20 51 7C "
        "05 6A 02 58 EB 19 8B CB E8 00 00 00 00 83 78 20 29 6A "
        "01 58 7D 09 33 C0 EB 05 BE 09 01 00 00 6A 01 03 C6 E9 "
        "00 00 00 00"
    ))
    struct.pack_into("<I", code, 7, western_denominator)
    struct.pack_into("<I", code, 14, western_base_id)
    struct.pack_into("<b", code, 24, pursuit_base_id - western_base_id)
    struct.pack_into("<i", code, 28, _relative_displacement(cave_va, 27, PIRATE_RECORD_VA))
    code[35] = pursuit_high_threshold
    struct.pack_into("<i", code, 46, _relative_displacement(cave_va, 45, PIRATE_RECORD_VA))
    code[53] = pursuit_middle_threshold
    struct.pack_into("<I", code, 64, eastern_base_id)
    struct.pack_into("<i", code, 73, _relative_displacement(cave_va, 72, PIRATE_SELECTION_RESUME_VA))
    return bytes(code)


def _build_pirate_selection_main(
    cave_va: int,
    settings: PirateVarietySettings = DEFAULT_PIRATE_VARIETY_SETTINGS,
) -> bytes:
    """Roll 0..99 while preserving the fame tier in EDX for weighted choice."""
    validate_pirate_variety_settings(settings)
    code = bytearray(bytes.fromhex(
        "81 3D 4C 61 5B 00 10 27 00 00 7C 07 B8 03 00 00 00 EB 16 "
        "81 3D 4C 61 5B 00 88 13 00 00 B8 02 00 00 00 7D 05 B8 "
        "01 00 00 00 50 6A 64 E8 00 00 00 00 59 5A E9 00 00 00 00 "
        "6A 00 50"
    ))
    if len(code) != PIRATE_SELECTION_MAIN_LENGTH:
        raise AssertionError("해적 선택 메인 코드 길이가 예상과 다릅니다.")
    struct.pack_into("<I", code, 2, PIRATE_FAME_ADDRESS)
    struct.pack_into("<I", code, 6, settings.fame_high)
    struct.pack_into("<I", code, 21, PIRATE_FAME_ADDRESS)
    struct.pack_into("<I", code, 25, settings.fame_middle)
    struct.pack_into("<i", code, 45, _relative_displacement(PIRATE_SELECTION_MAIN_VA, 44, PIRATE_RANDOM_VA))
    struct.pack_into("<i", code, 52, _relative_displacement(PIRATE_SELECTION_MAIN_VA, 51, cave_va))
    return bytes(code)


def _build_pirate_selection_cave_v2(
    cave_va: int,
    western_denominator: int,
    settings: PirateVarietySettings = DEFAULT_PIRATE_VARIETY_SETTINGS,
) -> bytes:
    """Rebuild the former shared west/east probability payload for migration."""
    validate_pirate_variety_settings(settings)
    code = bytearray()
    labels: dict[str, int] = {}
    short_fixups: list[tuple[int, str]] = []

    def emit(value: bytes | str) -> None:
        code.extend(bytes.fromhex(value) if isinstance(value, str) else value)

    def mark(name: str) -> None:
        labels[name] = len(code)

    def branch(opcode: int, target: str) -> None:
        code.append(opcode)
        short_fixups.append((len(code), target))
        code.append(0)

    emit("81 BC 24 34 01 00 00")
    emit(struct.pack("<I", western_denominator))
    branch(0x75, "eastern")
    emit(b"\xBE" + struct.pack("<I", settings.western_base_id))
    emit("85 DB")
    branch(0x74, "weighted")
    emit(b"\xBE" + struct.pack("<I", settings.pursuit_base_id))
    emit("8B CB E8")
    call_offset = len(code) - 1
    emit(struct.pack("<i", _relative_displacement(cave_va, call_offset, PIRATE_RECORD_VA)))
    emit(bytes.fromhex("83 78 20") + bytes((settings.pursuit_high_threshold,)))
    branch(0x7C, "pursuit_middle")
    emit("B8 02 00 00 00")
    branch(0xEB, "finish")
    mark("pursuit_middle")
    emit("8B CB E8")
    call_offset = len(code) - 1
    emit(struct.pack("<i", _relative_displacement(cave_va, call_offset, PIRATE_RECORD_VA)))
    emit(bytes.fromhex("83 78 20") + bytes((settings.pursuit_middle_threshold,)))
    branch(0x7C, "choose_zero")
    emit("B8 01 00 00 00")
    branch(0xEB, "finish")

    mark("eastern")
    emit(b"\xBE" + struct.pack("<I", settings.eastern_base_id))
    mark("weighted")
    emit("83 FA 02")
    branch(0x7C, "choose_zero")
    branch(0x7F, "stage_three")
    emit(bytes.fromhex("83 F8") + bytes((settings.western_stage2_first_probability,)))
    branch(0x7C, "choose_zero")
    emit("B8 01 00 00 00")
    branch(0xEB, "finish")

    mark("stage_three")
    emit(bytes.fromhex("83 F8") + bytes((settings.western_stage3_first_probability,)))
    branch(0x7C, "choose_zero")
    emit(bytes.fromhex("83 F8") + bytes((
        settings.western_stage3_first_probability + settings.western_stage3_second_probability,
    )))
    branch(0x7C, "choose_one")
    emit("B8 02 00 00 00")
    branch(0xEB, "finish")
    mark("choose_one")
    emit("B8 01 00 00 00")
    branch(0xEB, "finish")
    mark("choose_zero")
    emit("33 C0")

    mark("finish")
    emit("6A 01 03 C6 E9")
    jump_offset = len(code) - 1
    emit(struct.pack("<i", _relative_displacement(cave_va, jump_offset, PIRATE_SELECTION_RESUME_VA)))
    for displacement_offset, target in short_fixups:
        displacement = labels[target] - (displacement_offset + 1)
        if not -128 <= displacement <= 127:
            raise AssertionError("해적 선택 확장 코드의 짧은 분기가 범위를 벗어났습니다.")
        struct.pack_into("<b", code, displacement_offset, displacement)
    return bytes(code)


def _build_pirate_selection_cave(
    cave_va: int,
    western_denominator: int,
    settings: PirateVarietySettings = DEFAULT_PIRATE_VARIETY_SETTINGS,
) -> bytes:
    """Select west/east enemies with independent weights and preserve pursuits."""
    validate_pirate_variety_settings(settings)
    code = bytearray()
    labels: dict[str, int] = {}
    short_fixups: list[tuple[int, str]] = []

    def emit(value: bytes | str) -> None:
        code.extend(bytes.fromhex(value) if isinstance(value, str) else value)

    def mark(name: str) -> None:
        labels[name] = len(code)

    def branch(opcode: int, target: str) -> None:
        code.append(opcode)
        short_fixups.append((len(code), target))
        code.append(0)

    def weighted_choice(prefix: str, stage2_first: int, stage3_first: int, stage3_second: int) -> None:
        emit("83 FA 02")
        branch(0x7C, f"{prefix}_zero")
        branch(0x7F, f"{prefix}_stage_three")
        emit(bytes.fromhex("83 F8") + bytes((stage2_first,)))
        branch(0x7C, f"{prefix}_zero")
        emit("B8 01 00 00 00")
        branch(0xEB, "finish")

        mark(f"{prefix}_stage_three")
        emit(bytes.fromhex("83 F8") + bytes((stage3_first,)))
        branch(0x7C, f"{prefix}_zero")
        emit(bytes.fromhex("83 F8") + bytes((stage3_first + stage3_second,)))
        branch(0x7C, f"{prefix}_one")
        emit("B8 02 00 00 00")
        branch(0xEB, "finish")
        mark(f"{prefix}_one")
        emit("B8 01 00 00 00")
        branch(0xEB, "finish")
        mark(f"{prefix}_zero")
        emit("33 C0")
        branch(0xEB, "finish")

    emit("81 BC 24 34 01 00 00")
    emit(struct.pack("<I", western_denominator))
    branch(0x75, "eastern")
    emit(b"\xBE" + struct.pack("<I", settings.western_base_id))
    emit("85 DB")
    branch(0x74, "western_weighted")
    emit(b"\xBE" + struct.pack("<I", settings.pursuit_base_id))
    emit("8B CB E8")
    call_offset = len(code) - 1
    emit(struct.pack("<i", _relative_displacement(cave_va, call_offset, PIRATE_RECORD_VA)))
    emit(bytes.fromhex("83 78 20") + bytes((settings.pursuit_high_threshold,)))
    branch(0x7C, "pursuit_middle")
    emit("B8 02 00 00 00")
    branch(0xEB, "finish")
    mark("pursuit_middle")
    emit("8B CB E8")
    call_offset = len(code) - 1
    emit(struct.pack("<i", _relative_displacement(cave_va, call_offset, PIRATE_RECORD_VA)))
    emit(bytes.fromhex("83 78 20") + bytes((settings.pursuit_middle_threshold,)))
    branch(0x7C, "pursuit_zero")
    emit("B8 01 00 00 00")
    branch(0xEB, "finish")
    mark("pursuit_zero")
    emit("33 C0")
    branch(0xEB, "finish")

    mark("western_weighted")
    weighted_choice(
        "western",
        settings.western_stage2_first_probability,
        settings.western_stage3_first_probability,
        settings.western_stage3_second_probability,
    )

    mark("eastern")
    emit(b"\xBE" + struct.pack("<I", settings.eastern_base_id))
    weighted_choice(
        "eastern",
        settings.eastern_stage2_first_probability,
        settings.eastern_stage3_first_probability,
        settings.eastern_stage3_second_probability,
    )

    mark("finish")
    emit("6A 01 03 C6 E9")
    jump_offset = len(code) - 1
    emit(struct.pack("<i", _relative_displacement(cave_va, jump_offset, PIRATE_SELECTION_RESUME_VA)))
    for displacement_offset, target in short_fixups:
        displacement = labels[target] - (displacement_offset + 1)
        if not -128 <= displacement <= 127:
            raise AssertionError("해적 선택 확장 코드의 짧은 분기가 범위를 벗어났습니다.")
        struct.pack_into("<b", code, displacement_offset, displacement)
    return bytes(code)


def _build_pirate_weighted_payload(
    slot_va: int,
    western_denominator: int,
    settings: PirateVarietySettings,
) -> tuple[bytes, int]:
    """Build a self-describing configuration header followed by executable code."""
    validate_pirate_variety_settings(settings)
    header = bytearray(PIRATE_WEIGHTED_CONFIG_SIZE)
    header[:8] = PIRATE_WEIGHTED_MAGIC
    struct.pack_into("<I", header, 8, PIRATE_WEIGHTED_VERSION)
    struct.pack_into("<I", header, 12, western_denominator)
    struct.pack_into("<I", header, 16, settings.fame_middle)
    struct.pack_into("<I", header, 20, settings.fame_high)
    header[24:29] = bytes((
        settings.western_stage2_first_probability, settings.western_stage2_second_probability,
        settings.western_stage3_first_probability, settings.western_stage3_second_probability,
        settings.western_stage3_third_probability,
    ))
    header[29:34] = bytes((
        settings.eastern_stage2_first_probability, settings.eastern_stage2_second_probability,
        settings.eastern_stage3_first_probability, settings.eastern_stage3_second_probability,
        settings.eastern_stage3_third_probability,
    ))
    struct.pack_into("<III", header, 36, settings.western_base_id, settings.eastern_base_id, settings.pursuit_base_id)
    header[48] = settings.pursuit_middle_threshold
    header[49] = settings.pursuit_high_threshold
    cave_va = slot_va + PIRATE_WEIGHTED_CONFIG_SIZE
    return bytes(header) + _build_pirate_selection_cave(cave_va, western_denominator, settings), cave_va


def _build_pirate_weighted_payload_v2(
    slot_va: int,
    western_denominator: int,
    settings: PirateVarietySettings,
) -> tuple[bytes, int]:
    """Rebuild the former shared-probability payload for verification."""
    validate_pirate_variety_settings(settings)
    header = bytearray(PIRATE_WEIGHTED_CONFIG_SIZE)
    header[:8] = PIRATE_WEIGHTED_MAGIC_V2
    struct.pack_into("<I", header, 8, PIRATE_WEIGHTED_VERSION_V2)
    struct.pack_into("<I", header, 12, western_denominator)
    struct.pack_into("<I", header, 16, settings.fame_middle)
    struct.pack_into("<I", header, 20, settings.fame_high)
    header[24:29] = bytes((
        settings.western_stage2_first_probability, settings.western_stage2_second_probability,
        settings.western_stage3_first_probability, settings.western_stage3_second_probability,
        settings.western_stage3_third_probability,
    ))
    struct.pack_into("<III", header, 32, settings.western_base_id, settings.eastern_base_id, settings.pursuit_base_id)
    header[44] = settings.pursuit_middle_threshold
    header[45] = settings.pursuit_high_threshold
    cave_va = slot_va + PIRATE_WEIGHTED_CONFIG_SIZE
    return bytes(header) + _build_pirate_selection_cave_v2(cave_va, western_denominator, settings), cave_va


def _pirate_selection_patch_info(
    data: bytes,
) -> tuple[bool, int | None, int | None, PirateVarietySettings]:
    """Return enabled state, cave location and editable constants."""
    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        main_offset = pe.get_offset_from_rva(PIRATE_SELECTION_MAIN_VA - pe.OPTIONAL_HEADER.ImageBase)
        main = data[main_offset:main_offset + PIRATE_SELECTION_MAIN_LENGTH]
        if _is_original_pirate_selection(main):
            return False, None, None, DEFAULT_PIRATE_VARIETY_SETTINGS
        if len(main) != PIRATE_SELECTION_MAIN_LENGTH:
            raise ValueError(f"해적 선택 코드 위치 0x{PIRATE_SELECTION_MAIN_VA:X}을(를) 검증하지 못했습니다.")
        if main[42:44] == b"\x6A\x64" and main[51] == 0xE9:
            cave_va = PIRATE_SELECTION_MAIN_VA + 56 + struct.unpack_from("<i", main, 52)[0]
            cave_offset = pe.get_offset_from_rva(cave_va - pe.OPTIONAL_HEADER.ImageBase)
            slot_offset = cave_offset - PIRATE_WEIGHTED_CONFIG_SIZE
            header = data[slot_offset:cave_offset]
            if len(header) != PIRATE_WEIGHTED_CONFIG_SIZE:
                raise ValueError("명성별 해적 가중치 설정 헤더를 찾지 못했습니다.")
            magic = header[:8]
            version = struct.unpack_from("<I", header, 8)[0]
            if (magic, version) == (PIRATE_WEIGHTED_MAGIC, PIRATE_WEIGHTED_VERSION):
                settings = PirateVarietySettings(
                    fame_middle=struct.unpack_from("<I", header, 16)[0],
                    fame_high=struct.unpack_from("<I", header, 20)[0],
                    western_stage2_first_probability=header[24],
                    western_stage2_second_probability=header[25],
                    western_stage3_first_probability=header[26],
                    western_stage3_second_probability=header[27],
                    western_stage3_third_probability=header[28],
                    eastern_stage2_first_probability=header[29],
                    eastern_stage2_second_probability=header[30],
                    eastern_stage3_first_probability=header[31],
                    eastern_stage3_second_probability=header[32],
                    eastern_stage3_third_probability=header[33],
                    western_base_id=struct.unpack_from("<I", header, 36)[0],
                    eastern_base_id=struct.unpack_from("<I", header, 40)[0],
                    pursuit_base_id=struct.unpack_from("<I", header, 44)[0],
                    pursuit_middle_threshold=header[48],
                    pursuit_high_threshold=header[49],
                )
                payload_builder = _build_pirate_weighted_payload
            elif (magic, version) == (PIRATE_WEIGHTED_MAGIC_V2, PIRATE_WEIGHTED_VERSION_V2):
                shared = tuple(header[24:29])
                settings = PirateVarietySettings(
                    fame_middle=struct.unpack_from("<I", header, 16)[0],
                    fame_high=struct.unpack_from("<I", header, 20)[0],
                    western_stage2_first_probability=shared[0],
                    western_stage2_second_probability=shared[1],
                    western_stage3_first_probability=shared[2],
                    western_stage3_second_probability=shared[3],
                    western_stage3_third_probability=shared[4],
                    eastern_stage2_first_probability=shared[0],
                    eastern_stage2_second_probability=shared[1],
                    eastern_stage3_first_probability=shared[2],
                    eastern_stage3_second_probability=shared[3],
                    eastern_stage3_third_probability=shared[4],
                    western_base_id=struct.unpack_from("<I", header, 32)[0],
                    eastern_base_id=struct.unpack_from("<I", header, 36)[0],
                    pursuit_base_id=struct.unpack_from("<I", header, 40)[0],
                    pursuit_middle_threshold=header[44],
                    pursuit_high_threshold=header[45],
                )
                payload_builder = _build_pirate_weighted_payload_v2
            else:
                raise ValueError("지원하지 않는 명성별 해적 패치 버전입니다.")
            western_denominator = struct.unpack_from("<I", header, 12)[0]
            payload, expected_cave_va = payload_builder(
                cave_va - PIRATE_WEIGHTED_CONFIG_SIZE, western_denominator, settings,
            )
            if expected_cave_va != cave_va or main != _build_pirate_selection_main(cave_va, settings):
                raise ValueError("명성별 해적 선택 메인 코드를 검증하지 못했습니다.")
            if data[slot_offset:slot_offset + len(payload)] != payload:
                raise ValueError("명성별 해적 가중치 코드를 검증하지 못했습니다.")
            return True, slot_offset, cave_va, settings

        if main[50] != 0xE9:
            raise ValueError(f"해적 선택 코드 위치 0x{PIRATE_SELECTION_MAIN_VA:X}을(를) 검증하지 못했습니다.")
        cave_va = PIRATE_SELECTION_MAIN_VA + 55 + struct.unpack_from("<i", main, 51)[0]
        cave_offset = pe.get_offset_from_rva(cave_va - pe.OPTIONAL_HEADER.ImageBase)
        cave = data[cave_offset:cave_offset + PIRATE_SELECTION_CAVE_LENGTH]
        if len(cave) != PIRATE_SELECTION_CAVE_LENGTH:
            raise ValueError("기존 명성별 해적 선택 확장 코드가 잘려 있습니다.")
        counts = (
            struct.unpack_from("<I", main, 37)[0],
            struct.unpack_from("<I", main, 30)[0],
            struct.unpack_from("<I", main, 13)[0],
        )
        if counts != (1, 2, 3):
            raise ValueError("이전 패치의 단계별 후보 수가 기본 1/2/3이 아니어서 확률 방식으로 자동 변환할 수 없습니다.")
        western_denominator = struct.unpack_from("<I", cave, 7)[0]
        western_base_id = struct.unpack_from("<I", cave, 14)[0]
        settings = PirateVarietySettings(
            fame_middle=struct.unpack_from("<I", main, 25)[0],
            fame_high=struct.unpack_from("<I", main, 6)[0],
            western_base_id=western_base_id,
            eastern_base_id=struct.unpack_from("<I", cave, 64)[0],
            pursuit_base_id=western_base_id + struct.unpack_from("<b", cave, 24)[0],
            pursuit_middle_threshold=cave[53],
            pursuit_high_threshold=cave[35],
        )
        expected_main = _build_legacy_pirate_selection_main(
            cave_va, settings.fame_middle, settings.fame_high, counts,
        )
        expected_cave = _build_legacy_pirate_selection_cave(
            cave_va, western_denominator, settings.western_base_id,
            settings.eastern_base_id, settings.pursuit_base_id,
            settings.pursuit_middle_threshold, settings.pursuit_high_threshold,
        )
        if main != expected_main or cave != expected_cave:
            raise ValueError("기존 명성별 해적 확장 코드를 검증하지 못했습니다.")
        return True, cave_offset, cave_va, settings
    finally:
        pe.close()


def _set_text_virtual_size(data: bytearray, pe: pefile.PE, value: int) -> None:
    text_section = next((section for section in pe.sections if section.Name.rstrip(b"\0") == b".text"), None)
    if text_section is None:
        raise ValueError("EXE의 .text 섹션을 찾지 못했습니다.")
    struct.pack_into("<I", data, text_section.get_file_offset() + 8, value)


def apply_pirate_variety(
    data: bytearray,
    enabled: bool,
    settings: PirateVarietySettings = DEFAULT_PIRATE_VARIETY_SETTINGS,
) -> bool:
    """Enable or remove the Korean fame-tier six-pirate selection patch."""
    if enabled:
        validate_pirate_variety_settings(settings)
    current_enabled, cave_offset, cave_va, _ = _pirate_selection_patch_info(bytes(data))
    western_denominator, _ = _read_combat_encounter_denominators_from_data(bytes(data))
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        main_offset = pe.get_offset_from_rva(PIRATE_SELECTION_MAIN_VA - pe.OPTIONAL_HEADER.ImageBase)
        if enabled and current_enabled:
            patch_section = find_patch_section(data)
            if patch_section is not None:
                slot_offset, slot_va = patch_section.slot(PIRATE_SLOT_OFFSET, PIRATE_SLOT_SIZE)
            else:
                slot_offset, slot_va = -1, -1
            if cave_offset != slot_offset or cave_va != slot_va + PIRATE_WEIGHTED_CONFIG_SIZE:
                # One-time migration from the former uniform-selection cave.
                apply_pirate_variety(data, False)
                apply_pirate_variety(data, True, settings)
                return True
        if enabled:
            if not current_enabled:
                patch_section, _ = ensure_patch_section(data)
                cave_offset, slot_va = patch_section.slot(PIRATE_SLOT_OFFSET, PIRATE_SLOT_SIZE)
            else:
                patch_section = find_patch_section(data)
                if patch_section is None:
                    raise ValueError("명성별 해적 패치의 .patch 섹션을 찾지 못했습니다.")
                _, slot_va = patch_section.slot(PIRATE_SLOT_OFFSET, PIRATE_SLOT_SIZE)
            assert cave_offset is not None
            payload, cave_va = _build_pirate_weighted_payload(slot_va, western_denominator, settings)
            main = _build_pirate_selection_main(cave_va, settings)
            changed = (
                data[main_offset:main_offset + len(main)] != main
                or data[cave_offset:cave_offset + len(payload)] != payload
            )
            data[main_offset:main_offset + len(main)] = main
            clear_slot(data, patch_section, PIRATE_SLOT_OFFSET, PIRATE_SLOT_SIZE)
            write_slot(data, patch_section, PIRATE_SLOT_OFFSET, PIRATE_SLOT_SIZE, payload)
            return changed or not current_enabled

        original_main = _original_pirate_selection(western_denominator)
        changed = data[main_offset:main_offset + len(original_main)] != original_main
        data[main_offset:main_offset + len(original_main)] = original_main
        if current_enabled:
            assert cave_offset is not None and cave_va is not None
            patch_section = find_patch_section(data)
            if patch_section is not None:
                slot_offset, slot_va = patch_section.slot(PIRATE_SLOT_OFFSET, PIRATE_SLOT_SIZE)
            else:
                slot_offset, slot_va = -1, -1
            if cave_offset == slot_offset and cave_va in (slot_va, slot_va + PIRATE_WEIGHTED_CONFIG_SIZE):
                clear_slot(data, patch_section, PIRATE_SLOT_OFFSET, PIRATE_SLOT_SIZE)
                changed = True
                return changed

            data[cave_offset:cave_offset + PIRATE_SELECTION_CAVE_LENGTH] = b"\0" * PIRATE_SELECTION_CAVE_LENGTH
            text_section = next((section for section in pe.sections if section.Name.rstrip(b"\0") == b".text"), None)
            if text_section is None:
                raise ValueError("EXE의 .text 섹션을 찾지 못했습니다.")
            cave_rva = cave_va - pe.OPTIONAL_HEADER.ImageBase
            if cave_rva + PIRATE_SELECTION_CAVE_LENGTH == text_section.VirtualAddress + text_section.Misc_VirtualSize:
                _set_text_virtual_size(data, pe, cave_rva - text_section.VirtualAddress)
            changed = True
        return changed
    finally:
        pe.close()


def _read_gameplay_options_from_data(data: bytes) -> tuple[int, int, int, int, int]:
    """Read and validate the five editable gameplay operands."""
    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")

        def offset(va: int) -> int:
            return pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)

        rest_offset = offset(LONG_REST_MAX_VA)
        if data[rest_offset - 1] != 0x6A:
            raise ValueError(f"장기 휴양 상한 위치 0x{LONG_REST_MAX_VA:X}을(를) 검증하지 못했습니다.")
        long_rest_max = data[rest_offset]

        preparation_values: list[int] = []
        for va in EXPLORATION_PREPARATION_VAS:
            option_offset = offset(va)
            if data[option_offset - 1] != 0x6A:
                raise ValueError(f"탐험 준비 기간 위치 0x{va:X}을(를) 검증하지 못했습니다.")
            preparation_values.append(data[option_offset])
        if len(set(preparation_values)) != 1:
            raise ValueError("EXE 안의 탐험 준비 기간 네 곳이 서로 다릅니다.")

        succession_offset = offset(SUCCESSION_MIN_AGE_VA)
        if data[succession_offset - 2:succession_offset] != bytes.fromhex("83 f8"):
            raise ValueError(f"세대교체 나이 위치 0x{SUCCESSION_MIN_AGE_VA:X}을(를) 검증하지 못했습니다.")
        succession_age = data[succession_offset]

        north_offset = offset(COLD_NORTH_LIMIT_VA)
        south_offset = offset(COLD_SOUTH_LIMIT_VA)
        if data[north_offset - 1] != 0xB9 or data[south_offset - 1] != 0xB9:
            raise ValueError("극지방 추위 판정 위치를 검증하지 못했습니다.")
        cold_north_limit = struct.unpack_from("<I", data, north_offset)[0]
        cold_south_limit = struct.unpack_from("<I", data, south_offset)[0]
        return long_rest_max, preparation_values[0], succession_age, cold_north_limit, cold_south_limit
    finally:
        pe.close()


def _exploration_preparation_message(days: int) -> bytes:
    return (
        f"{EXPLORATION_PREPARATION_MESSAGE_PREFIX}{days}"
        f"{EXPLORATION_PREPARATION_MESSAGE_SUFFIX}"
    ).encode("cp949")


def _read_exploration_preparation_message_days(data: bytes, pe: pefile.PE) -> int:
    """Read the number embedded in the city-gate exploration confirmation."""
    message_offset = pe.get_offset_from_rva(
        EXPLORATION_PREPARATION_MESSAGE_VA - pe.OPTIONAL_HEADER.ImageBase
    )
    message_block = data[message_offset:message_offset + EXPLORATION_PREPARATION_MESSAGE_SIZE]
    terminator = message_block.find(b"\0")
    if terminator < 0:
        raise ValueError("성문 탐험 안내 문구의 끝을 찾지 못했습니다.")
    try:
        message = message_block[:terminator].decode("cp949")
    except UnicodeDecodeError as error:
        raise ValueError("성문 탐험 안내 문구를 읽지 못했습니다.") from error

    if not (
        message.startswith(EXPLORATION_PREPARATION_MESSAGE_PREFIX)
        and message.endswith(EXPLORATION_PREPARATION_MESSAGE_SUFFIX)
    ):
        raise ValueError("성문 탐험 안내 문구를 검증하지 못했습니다.")
    number_text = message[
        len(EXPLORATION_PREPARATION_MESSAGE_PREFIX):
        -len(EXPLORATION_PREPARATION_MESSAGE_SUFFIX)
    ]
    if not number_text.isdecimal() or not 1 <= int(number_text) <= 127:
        raise ValueError("성문 탐험 안내 문구의 기간을 검증하지 못했습니다.")
    return int(number_text)


def apply_gameplay_options(
    data: bytearray,
    long_rest_max: int,
    exploration_preparation_days: int,
    succession_min_age: int,
    cold_north_limit: int,
    cold_south_limit: int,
) -> bool:
    """Set rest, exploration, succession and polar-cold parameters."""
    if not 1 <= long_rest_max <= 127:
        raise ValueError("장기 휴양 최대 기간은 1~127개월 사이여야 합니다.")
    if not 1 <= exploration_preparation_days <= 127:
        raise ValueError("탐험 준비 기간은 1~127일 사이여야 합니다.")
    if not 1 <= succession_min_age <= 127:
        raise ValueError("세대교체 가능 나이는 1~127세 사이여야 합니다.")
    if not 0 <= cold_north_limit <= 20000 or not 0 <= cold_south_limit <= 20000:
        raise ValueError("극지방 추위 판정 좌표는 0~20,000 사이여야 합니다.")

    current = _read_gameplay_options_from_data(bytes(data))
    target = (
        long_rest_max,
        exploration_preparation_days,
        succession_min_age,
        cold_north_limit,
        cold_south_limit,
    )
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        displayed_days = _read_exploration_preparation_message_days(bytes(data), pe)
        if current == target and displayed_days == exploration_preparation_days:
            return False

        def offset(va: int) -> int:
            return pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)

        data[offset(LONG_REST_MAX_VA)] = long_rest_max
        for va in EXPLORATION_PREPARATION_VAS:
            data[offset(va)] = exploration_preparation_days
        message_offset = offset(EXPLORATION_PREPARATION_MESSAGE_VA)
        message = _exploration_preparation_message(exploration_preparation_days)
        if len(message) + 1 > EXPLORATION_PREPARATION_MESSAGE_SIZE:
            raise ValueError("성문 탐험 안내 문구 공간이 부족합니다.")
        data[message_offset:message_offset + EXPLORATION_PREPARATION_MESSAGE_SIZE] = (
            message + b"\0" * (EXPLORATION_PREPARATION_MESSAGE_SIZE - len(message))
        )
        data[offset(SUCCESSION_MIN_AGE_VA)] = succession_min_age
        struct.pack_into("<I", data, offset(COLD_NORTH_LIMIT_VA), cold_north_limit)
        struct.pack_into("<I", data, offset(COLD_SOUTH_LIMIT_VA), cold_south_limit)
        return True
    finally:
        pe.close()


def _read_coordinate_style(data: bytes) -> str:
    if KOREAN_DIRECTION_3_FORMAT in data:
        return "korean3"
    if DECIMAL_FORMAT in data:
        return "korean2"
    return "original"


def read_settings(
    target: Path,
) -> tuple[
    str, tuple[tuple[int, int], ...], int, int, int, int, int, int, int,
    int, int, int, int, int, int, bool, PirateVarietySettings, bool, Decimal, bool,
]:
    """Read the settings currently encoded in a selected executable."""
    target = target.resolve(strict=True)
    data = target.read_bytes()
    presets: list[tuple[int, int]] = []
    for width_offset, height_offset in RESOLUTION_VALUES:
        if data[width_offset - 1] != 0x68 or data[height_offset - 1] != 0x68:
            raise ValueError("해상도 선택지 명령을 검증하지 못했습니다.")
        presets.append((struct.unpack_from("<I", data, width_offset)[0], struct.unpack_from("<I", data, height_offset)[0]))

    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        wait_offset = pe.get_offset_from_rva(NPC_WAIT_VA - pe.OPTIONAL_HEADER.ImageBase)
        roll_offset = pe.get_offset_from_rva(NPC_ROLL_VA - pe.OPTIONAL_HEADER.ImageBase)
        wait_code = data[wait_offset:wait_offset + 7]
        roll_code = data[roll_offset:roll_offset + 2]
        if roll_code[0] != 0x6A or not 1 <= roll_code[1] <= 127:
            raise ValueError("NPC 이동 확률 명령을 검증하지 못했습니다.")
        if wait_code == NPC_ORIGINAL_WAIT:
            wait_days = 60
        elif wait_code[:6] == NPC_COMPARE_PREFIX and -60 <= struct.unpack("b", wait_code[6:7])[0] <= 0:
            wait_days = struct.unpack("b", wait_code[6:7])[0] + 60
        else:
            raise ValueError("NPC 이동 대기 명령을 검증하지 못했습니다.")
        gameplay = _read_gameplay_options_from_data(data)
        activity_ages = _read_npc_activity_ages_from_data(data)
        encounters = _read_combat_encounter_denominators_from_data(data)
        pirate_variety, _, _, pirate_settings = _pirate_selection_patch_info(data)
        eclipse_enabled, eclipse_latitude, _ = _eclipse_patch_info(data)
        return (
            _read_coordinate_style(data), tuple(presets), roll_code[1], wait_days,
            *gameplay, *activity_ages, *encounters, pirate_variety, pirate_settings,
            eclipse_enabled, eclipse_latitude,
            read_mistranslation_patch_state(data),
        )
    finally:
        pe.close()


def _coordinate_bytes(original: bytes, target: Path, style: str) -> bytes:
    """Reuse the coordinate patcher on a disposable sibling file."""
    temporary = target.with_name(target.name + ".integrated-coordinate.tmp")
    if temporary.exists():
        raise FileExistsError(f"임시 파일이 남아 있습니다: {temporary}")
    temporary.write_bytes(original)
    backup: Path | None = None
    try:
        backup = patch_coordinate(temporary, style)
        return temporary.read_bytes()
    finally:
        if temporary.exists():
            temporary.unlink()
        if backup is not None and backup.exists():
            backup.unlink()


def apply_all(
    target: Path,
    coordinate_style: str,
    set_resolution: bool,
    presets: tuple[tuple[int, int], ...],
    departure_denominator: int,
    arrival_wait_days: int,
    long_rest_max: int,
    exploration_preparation_days: int,
    succession_min_age: int,
    cold_north_limit: int,
    cold_south_limit: int,
    npc_activity_min_age: int,
    npc_activity_max_age: int,
    western_encounter_denominator: int,
    islamic_encounter_denominator: int,
    pirate_variety_enabled: bool,
    pirate_variety_settings: PirateVarietySettings = DEFAULT_PIRATE_VARIETY_SETTINGS,
    eclipse_enabled: bool = False,
    eclipse_latitude: str | int | float | Decimal = Decimal("67"),
    mistranslation_fixes_enabled: bool = False,
) -> Path | None:
    """Apply all selected settings atomically and create one original backup."""
    target = target.resolve(strict=True)
    original = target.read_bytes()
    before_coordinate = bytearray(original)
    # Coordinate-style restoration may clear extensions after its own payload.
    # Temporarily remove relocatable patches, apply the requested coordinate
    # style, then recreate all selected payloads in their reserved slots.
    if _pirate_selection_patch_info(original)[0]:
        apply_pirate_variety(before_coordinate, False)
    if read_mistranslation_patch_state(bytes(before_coordinate)):
        apply_mistranslation_fixes(before_coordinate, False)
    if _eclipse_patch_info(bytes(before_coordinate))[0]:
        apply_eclipse_polar_caps(before_coordinate, False)
    updated = bytearray(_coordinate_bytes(bytes(before_coordinate), target, coordinate_style))
    if set_resolution:
        apply_resolution(updated, presets)
    apply_npc_travel(updated, departure_denominator, arrival_wait_days)
    apply_gameplay_options(
        updated,
        long_rest_max,
        exploration_preparation_days,
        succession_min_age,
        cold_north_limit,
        cold_south_limit,
    )
    apply_npc_activity_ages(updated, npc_activity_min_age, npc_activity_max_age)
    apply_combat_encounter_denominators(
        updated,
        western_encounter_denominator,
        islamic_encounter_denominator,
    )
    apply_pirate_variety(updated, pirate_variety_enabled, pirate_variety_settings)
    apply_mistranslation_fixes(updated, mistranslation_fixes_enabled)
    apply_eclipse_polar_caps(updated, eclipse_enabled, eclipse_latitude)
    if bytes(updated) == original:
        return None

    backup = target.with_name(f"{target.name}.before_integrated_patch_{datetime.now():%Y%m%d_%H%M%S_%f}.bak")
    with backup.open("xb") as stream:
        stream.write(original)
    if backup.read_bytes() != original:
        raise IOError("백업 검증에 실패하여 실행 파일을 변경하지 않았습니다.")

    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix="cds-integrated-", suffix=".tmp", delete=False) as stream:
            temp_name = stream.name
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        if Path(temp_name).read_bytes() != updated or target.read_bytes() != original:
            raise IOError("저장 도중 대상 파일이 변경되어 패치를 중단했습니다.")
        os.replace(temp_name, target)
        temp_name = None
        if target.read_bytes() != updated:
            raise IOError("패치 후 실행 파일 검증에 실패했습니다. 백업을 복원해 주세요.")
    finally:
        if temp_name and Path(temp_name).exists():
            Path(temp_name).unlink()
    return backup
