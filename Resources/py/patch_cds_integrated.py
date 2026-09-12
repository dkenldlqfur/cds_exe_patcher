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
    BARMAID_NAME_SLOT_OFFSET,
    BARMAID_NAME_SLOT_STRIDE,
    CITY_NAME_SLOT_OFFSET,
    CITY_NAME_SLOT_STRIDE,
    DISCOVERY_NAME_SLOT_OFFSET,
    DISCOVERY_NAME_SLOT_STRIDE,
    ECLIPSE_SLOT_OFFSET,
    ECLIPSE_SLOT_SIZE,
    FIGUREHEAD_EFFECT_SLOT_OFFSET,
    FIGUREHEAD_EFFECT_SLOT_SIZE,
    MISTRANSLATION_SLOT_OFFSET,
    MISTRANSLATION_SLOT_SIZE,
    PATCH_SECTION_EXPANDED_SIZE,
    PATCH_SECTION_DISCOVERY_NAMES_SIZE,
    PATCH_SECTION_CITY_NAMES_SIZE,
    PATCH_SECTION_ITEM_NAMES_SIZE,
    PATCH_SECTION_MASTER_NAMES_SIZE,
    ITEM_NAME_SLOT_OFFSET,
    ITEM_NAME_SLOT_STRIDE,
    PIRATE_SLOT_OFFSET,
    PIRATE_SLOT_SIZE,
    SHIP_TYPE_NAME_SLOT_OFFSET,
    SHIP_TYPE_NAME_SLOT_STRIDE,
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
MAX_NPC_ARRIVAL_WAIT_DAYS = 127
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


@dataclass(frozen=True)
class FigureheadEffectSettings:
    """Editable global strengths used by the fourteen figurehead effects."""

    disaster_grade1_chance: int = 11
    disaster_grade2_chance: int = 41
    disaster_grade3_chance: int = 71
    cannon_damage_reduction: int = 20
    shooting_damage_reduction: int = 50
    melee_damage_reduction: int = 70
    cannon_attack_percent: int = 120
    shooting_attack_percent: int = 150
    melee_attack_percent: int = 200
    hull_recovery: int = 5
    movement_bonus: int = 1
    movement_maximum: int = 6
    special_cannon_attack_percent: int = 200
    all_attack_percent: int = 150


@dataclass(frozen=True)
class BarmaidRecord:
    """A single immutable entry from the executable's barmaid master table."""

    identifier: int
    name: str
    face_code: int
    appearance_year: int
    city_id: int
    personality_id: int
    language_flags: int


@dataclass(frozen=True)
class BarmaidChildAptitudes:
    """The six child-stat modifiers assigned to one female face code."""

    face_code: int
    modifiers: tuple[int, int, int, int, int, int]


@dataclass(frozen=True)
class BarmaidEdit:
    """The user-editable fields for one barmaid master entry."""

    identifier: int
    name: str
    face_code: int
    appearance_year: int
    city_id: int
    personality_id: int
    language_flags: int
    child_aptitude_modifiers: tuple[int, int, int, int, int, int]


@dataclass(frozen=True)
class SponsorRecord:
    """One immutable static sponsor record embedded in the executable."""

    identifier: int
    name: str
    face_code: int
    gender: int
    nation_id: int
    job_id: int
    appearance_year: int
    power: int
    city_id: int
    building_id: int
    wealth_factor: int
    appraisal: int
    preference_flags: int
    language_flags: int


@dataclass(frozen=True)
class SponsorEdit:
    """The supported editable fields of one static sponsor record."""

    identifier: int
    face_code: int
    gender: int
    nation_id: int
    job_id: int
    appearance_year: int
    power: int
    city_id: int
    building_id: int
    wealth_factor: int
    appraisal: int
    preference_flags: int
    language_flags: int


@dataclass(frozen=True)
class PersonRecord:
    """One general-person master record embedded in the executable."""

    identifier: int
    name: str
    face_code: int
    gender: int
    age_at_1480: int
    nation_id: int
    job_id: int
    fame: int
    infamy: int
    employment_state: int
    city_id: int
    building_id: int
    blood_id: int
    vitality: int
    hire_cost: int
    abilities: tuple[int, ...]
    skills: tuple[int, ...]


@dataclass(frozen=True)
class PersonEdit:
    """The documented, fixed-width initial fields of one general person."""

    identifier: int
    face_code: int
    gender: int
    age_at_1480: int
    nation_id: int
    job_id: int
    fame: int
    infamy: int
    employment_state: int
    city_id: int
    building_id: int
    blood_id: int
    vitality: int
    hire_cost: int
    abilities: tuple[int, ...]
    skills: tuple[int, ...]


@dataclass(frozen=True)
class ShipTypeRecord:
    """One of the eight static ship-type records embedded in the executable."""

    identifier: int
    name: str
    shipyard_requirement: int
    base_power: int
    power_limit: int
    base_durability: int
    durability_limit: int
    base_weight: int
    weight_limit: int
    base_capacity: int
    capacity_limit: int
    base_cannons: int
    cannon_limit: int
    min_crew: int


@dataclass(frozen=True)
class ShipTypeEdit:
    """Editable initial values for one static ship type."""

    identifier: int
    name: str
    shipyard_requirement: int
    base_power: int
    power_limit: int
    base_durability: int
    durability_limit: int
    base_weight: int
    weight_limit: int
    base_capacity: int
    capacity_limit: int
    base_cannons: int
    cannon_limit: int
    min_crew: int


@dataclass(frozen=True)
class CityRecord:
    """Verified static city definition stored in the executable."""

    identifier: int
    name: str
    world_x: int
    world_y: int
    inland_connection_ids: tuple[int, int]
    ship_candidate_mask: int
    trade_region_id: int
    culture_id: int
    nation_id: int
    shipyard_level: int
    update_counter: int
    specialty_id: int
    specialty_price: int
    specialty_supply_index: int
    default_market_goods: tuple[int, int, int, int, int, int, int, int]
    city_status: int
    facility_flags: int
    default_flags: int


@dataclass(frozen=True)
class CityEdit:
    """Editable static city fields. Dynamic city state remains in SAVEDATA.CDS."""

    identifier: int
    name: str
    world_x: int
    world_y: int
    inland_connection_ids: tuple[int, int]
    ship_candidate_mask: int
    trade_region_id: int
    culture_id: int
    nation_id: int
    shipyard_level: int
    update_counter: int
    specialty_id: int
    specialty_price: int
    specialty_supply_index: int
    default_market_goods: tuple[int, int, int, int, int, int, int, int]
    city_status: int
    facility_flags: int
    default_flags: int


@dataclass(frozen=True)
class ItemRecord:
    """One static item definition embedded in the executable."""

    identifier: int
    name: str
    image_id: int | None
    buy_price: int
    sell_price: int
    effect_value: int
    category_id: int


@dataclass(frozen=True)
class ItemEdit:
    """Verified editable fields of one static item definition."""

    identifier: int
    name: str
    buy_price: int
    sell_price: int
    effect_value: int
    category_id: int


@dataclass(frozen=True)
class DiscoveryRecord:
    """One static discovery master entry embedded in the executable.

    The discovery progress itself lives in SAVEDATA.CDS.  This model only
    represents the EXE-side definition: where the discovery can appear and
    its category/value.  ``still_slot``, ``avi_id``, and ``animation_part``
    are the optional media links found from the corresponding original record.
    """

    identifier: int
    name: str
    category_id: int
    game_id: int
    value: int
    min_x: int | None
    min_y: int | None
    max_x: int | None
    max_y: int | None
    still_slot: int | None = None
    avi_id: int | None = None
    animation_part: int | None = None


@dataclass(frozen=True)
class DiscoveryEdit:
    """The verified editable EXE fields for one discovery definition."""

    identifier: int
    name: str
    category_id: int
    value: int
    min_x: int | None
    min_y: int | None
    max_x: int | None
    max_y: int | None
    still_slot: int | None
    avi_id: int | None
    animation_part: int | None


DEFAULT_PIRATE_VARIETY_SETTINGS = PirateVarietySettings()
DEFAULT_FIGUREHEAD_EFFECT_SETTINGS = FigureheadEffectSettings()

# The original game keeps figurehead strengths in executable instructions,
# rather than in the item records.  The item field only selects effect code
# 0..35.  These verified blocks are replaced with jumps/calls into one stable
# .patch slot whose header stores the editable values.
FIGUREHEAD_EFFECT_MAGIC = b"CDSFHE1\0"
FIGUREHEAD_EFFECT_VERSION = 1
FIGUREHEAD_EFFECT_CONFIG_OFFSET = 0x0C
FIGUREHEAD_EFFECT_CONFIG_COUNT = 14
FIGUREHEAD_EFFECT_EXPECTED_SLOT_VA = 0x64F700
FIGUREHEAD_EFFECT_STUB_LAYOUT = {
    "move": (0x80, bytes.fromhex(
        "85ff7523837c241408c706010000007d4e8b4e18e897d2dfff83f8217541"
        "a134f764000106eb388b442410b9640000008b8064080000400faf46040faf"
        "c799f7f940837c24140889067d148b4e18e85dd2dfff83f8217507a134f7"
        "64000106a138f7640039067c028906e9a954deff"
    ), ((21, 0x44CA30), (79, 0x44CA30), (107, 0x434C98))),
    "adjacent_attack": (0x100, bytes.fromhex(
        "8b8f10030000e825d2dfff83f81e740c83f8237523a140f76400eb05a128"
        "f764006a6450ffb63c080000e86783e6ff83c40c89863c080000e9346fdeff"
    ), ((7, 0x44CA30), (43, 0x4B7B96), (57, 0x436771))),
    "adjacent_defense": (0x180, bytes.fromhex(
        "8b8910030000e8a5d1dfff83f81b7522b8640000002b051cf764006a6450"
        "ffb63c080000e8ed82e6ff83c40c89863c080000e9ba6edeff"
    ), ((7, 0x44CA30), (37, 0x4B7B96), (51, 0x436771))),
    "cannon": (0x200, bytes.fromhex(
        "837c2450087d568b5424148b8a10030000e81ad1dfff83f81d741583f822"
        "741783f8230f8583000000a140f76400eb0ca124f76400eb05a13cf76400"
        "8b5424106a6450ffb23c080000e84882e6ff83c40c8b54241089823c080000"
        "eb4f8b5424108b4424108b8a280800008d14898d0c918b8c8810030000e8"
        "b3d0dfff83f81a752ab8640000002b0518f764008b5424106a6450ffb23c"
        "080000e8f781e6ff83c40c8b54241089823c080000e95677deff"
    ), ((18, 0x44CA30), (74, 0x4B7B96), (121, 0x44CA30),
        (155, 0x4B7B96), (173, 0x437107))),
    "repair": (0x300, bytes.fromhex(
        "8b8c8e10030000e874cedfff508b8f04030000ff3530f7640051e89381e6"
        "ff83c40c898704030000e97bdedeff"
    ), ((8, 0x44C880), (27, 0x4B7BB2), (41, 0x43D8A8))),
    "melee": (0x380, bytes.fromhex(
        "83fb087d508b8910030000e8a0cfdfff83f81f74248b86540800008d1480"
        "8d04908b8c8610030000e883cfdfff83f8237523a140f76400eb05a12cf7"
        "64006a6450ffb63c080000e8ca80e6ff83c40c89863c0800008b86280800"
        "0083f8087d398d0c808d04888b8c8610030000e83ecfdfff83f81c7522b8"
        "640000002b0520f764006a6450ffb63c080000e88680e6ff83c40c89863c"
        "080000e984a3deff"
    ), ((12, 0x44CA30), (41, 0x44CA30), (72, 0x4B7B96),
        (110, 0x44CA30), (140, 0x4B7B96), (154, 0x439EA2))),
    "disaster": (0x480, bytes.fromhex(
        "8b78044f83ff02760231ff8b3cbd0cf764004f6a64e87580e6ff83c40439c7c3"
    ), ((22, 0x4B7C0F),)),
}

FIGUREHEAD_EFFECT_HOOKS = (
    ("move", 0x434C38, bytes.fromhex(
        "85ff751e837c241408c706010000007d4f8b4e18e8df7d010083f8217542ff06"
        "eb3e8b442410b9640000008b8064080000400faf46040fafc799f7f940837c24"
        "140889067d0f8b4e18e8aa7d010083f8217502ff06833e067c06c70606000000"
    ), False),
    ("adjacent_attack", 0x4366AD, bytes.fromhex(
        "8b8f10030000e87863010083f81e74148b8f10030000e86863010083f8230f85"
        "a00000006a028b863c0800006a0350e8b514080083c40ce982000000"
    ), False),
    ("adjacent_defense", 0x43674F, bytes.fromhex(
        "8b8910030000e8d662010083f81b75128b863c080000992bc2c1f80189863c080000"
    ), False),
    ("cannon", 0x437065, bytes.fromhex(
        "837c2450087d558b5424148b8a10030000e8b559010083f81d75066a056a06eb64"
        "8b5424148b8a10030000e89b59010083f822750d8b542410c1a23c08000001eb60"
        "8b5424148b8a10030000e87a59010083f823754c6a036a02eb298b5424108b4424"
        "108b8a280800008d14898d0c918b8c8810030000e84f59010083f81a75216a056a"
        "048b5424188b823c08000050e89c0a08008b54241c83c40c89823c080000"
    ), False),
    ("repair", 0x43D884, bytes.fromhex(
        "8b8c8e10030000e8f0ef0000508b8f040300006a0551e813a3070083c40c898704030000"
    ), False),
    ("melee", 0x439E13, bytes.fromhex(
        "83fb087d4f8b8910030000e80d2c010083f81f7509c1a63c08000001eb368b8654"
        "0800008d14808d04908b8c8610030000e8e72b010083f82375196a028b863c0800"
        "006a0350e838dd070083c40c89863c0800008b862808000083f8087d308d0c808d"
        "04888b8c8610030000e8ac2b010083f81c75196a0a8b863c0800006a0350e8fddc"
        "070083c40c89863c080000"
    ), False),
    ("disaster", 0x47473C, bytes.fromhex("8b78046a6403ff8d047f8d3c80e8c134040083ef1483c4043bf8"), True),
    ("disaster", 0x4748B8, bytes.fromhex("8b78046a6403ff8d047f8d3c80e84533040083ef1483c4043bf8"), True),
    ("disaster", 0x474A64, bytes.fromhex("8b78046a6403ff8d047f8d3c80e89931040083ef1483c4043bf8"), True),
    ("disaster", 0x474B95, bytes.fromhex("8b78046a6403ff8d047f8d3c80e86830040083ef1483c4043bf8"), True),
    ("disaster", 0x474C7F, bytes.fromhex("8b78046a6403ff8d047f8d3c80e87e2f040083ef1483c4043bf8"), True),
)


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
# Money limits are duplicated in the global money clamp, bank deposit/
# withdrawal paths, and their numeric entry controls.  Every operand below
# must move together or the bank UI and the actual stored amount disagree.
CASH_LIMIT_OPERANDS = (
    (0x4059B5, b"\x3D"),       # global held-cash clamp comparison
    (0x4059D0, b"\xB8"),       # global held-cash clamp result
    (0x460B61, b"\x81\xFE"),   # bank withdrawal: current cash comparison
    (0x460B6A, b"\xB8"),       # bank withdrawal: remaining cash capacity
    (0x47CBC8, b"\x68"),       # held-cash numeric entry maximum
    (0x48252A, b"\x3D"),       # held-cash digit entry comparison
    (0x482541, b"\xC7\x81\xA8\x00\x00\x00"),  # digit entry clamp result
)
DEPOSIT_LIMIT_OPERANDS = (
    (0x460ACB, b"\x81\xFF"),  # bank deposit: current deposit comparison
    (0x460AD4, b"\xB8"),       # bank deposit: remaining deposit capacity
    (0x47CC08, b"\x68"),       # deposit numeric entry maximum
)
MONEY_LIMIT_MIN = 1
MONEY_LIMIT_MAX = 99_999_999
# Fame and infamy are independent player-record accumulators.  Their original
# limits are 100,000 and 10,000 respectively, enforced by these clamp calls.
FAME_LIMIT_OPERANDS = (
    (0x474188, b"\x68"),  # fame accumulator maximum
)
INFAMY_LIMIT_OPERANDS = (
    (0x4741C8, b"\x68"),  # infamy accumulator maximum
)
FAME_INFAMY_LIMIT_MIN = 1
FAME_LIMIT_MAX = 99_999_999
INFAMY_LIMIT_MAX = 99_999_999

# The original executable keeps all 127 tavern-maid master records in a
# contiguous 40-byte table.  Only the personality and language mask are
# exposed by the patcher; all relationship, city, birthday and portrait data
# stays untouched.
BARMAID_TABLE_VA = 0x517AF8
BARMAID_RECORD_COUNT = 127
BARMAID_RECORD_SIZE = 0x28
BARMAID_NAME_POINTER_OFFSET = 0x00
BARMAID_NAME_MAX_BYTES = 12
# +0x04 selects the female portrait and, through the table below, the six
# child-aptitude modifiers.  +0x14 is a different "fortune spouse" comparison
# code and must not be changed by the face-code control.
BARMAID_FACE_CODE_OFFSET = 0x04
BARMAID_APPEARANCE_YEAR_OFFSET = 0x08
# The game represents the year relative to 1495: stored value = 1495 - year.
# Years before the 1480 game start are indistinguishable to the player, so the
# editor deliberately keeps its selectable range within the playable period.
BARMAID_APPEARANCE_YEAR_REFERENCE = 1495
BARMAID_APPEARANCE_YEAR_MIN = 1480
BARMAID_APPEARANCE_YEAR_MAX = 1600
BARMAID_PERSONALITY_OFFSET = 0x18
BARMAID_LANGUAGE_FLAGS_OFFSET = 0x20
BARMAID_CITY_ID_OFFSET = 0x24
BARMAID_CITY_ID_MAX = 225
BARMAID_PERSONALITY_COUNT = 8
BARMAID_LANGUAGE_MASK = (1 << 14) - 1
BARMAID_FACE_CODE_MAX = 143
BARMAID_CHILD_APTITUDE_TABLE_VA = 0x51B0A0
BARMAID_CHILD_APTITUDE_RECORD_SIZE = 0x20
BARMAID_CHILD_APTITUDE_MODIFIER_COUNT = 6
# The original data only uses -5..+5, but the game consumes these as signed
# 32-bit modifiers and clamps the final child attribute to 1..100.  Permit a
# practical editor range wide enough to force either end of that final range.
BARMAID_CHILD_APTITUDE_MIN = -255
BARMAID_CHILD_APTITUDE_MAX = 255
# Static sponsor table.  The first record starts with its name pointer at
# 0x5228B8; its face code begins at 0x5228BC.  Keeping the pointer inside the
# 0x3C-byte record is essential: using the face-code address as the base shifts
# every displayed name to the following sponsor.
SPONSOR_TABLE_VA = 0x5228B8
SPONSOR_RECORD_COUNT = 81
SPONSOR_RECORD_SIZE = 0x3C
SPONSOR_NAME_POINTER_OFFSET = 0x00
SPONSOR_FACE_CODE_OFFSET = 0x04
SPONSOR_GENDER_OFFSET = 0x08
SPONSOR_NATION_ID_OFFSET = 0x0C
SPONSOR_JOB_ID_OFFSET = 0x10
SPONSOR_APPEARANCE_YEAR_OFFSET = 0x14
SPONSOR_POWER_OFFSET = 0x20
SPONSOR_CITY_ID_OFFSET = 0x24
SPONSOR_BUILDING_ID_OFFSET = 0x28
SPONSOR_WEALTH_FACTOR_OFFSET = 0x2C
SPONSOR_APPRAISAL_OFFSET = 0x30
SPONSOR_FLAGS_OFFSET = 0x38
SPONSOR_FACE_CODE_MAX = 412
SPONSOR_APPEARANCE_YEAR_REFERENCE = 1480
SPONSOR_APPEARANCE_YEAR_MIN = 1480
SPONSOR_APPEARANCE_YEAR_MAX = 1600
SPONSOR_NATION_ID_MAX = 18
SPONSOR_JOB_ID_MIN = 14
SPONSOR_JOB_ID_MAX = 21
SPONSOR_CITY_ID_MAX = 225
SPONSOR_BUILDING_ID_MAX = 15
SPONSOR_COEFFICIENT_MIN = 0
SPONSOR_COEFFICIENT_MAX = 99
SPONSOR_PREFERENCE_MASK = 0xFF
SPONSOR_LANGUAGE_MASK = (1 << 14) - 1
SPONSOR_EDITABLE_FLAGS_MASK = SPONSOR_PREFERENCE_MASK | (SPONSOR_LANGUAGE_MASK << 16)
PERSON_TABLE_FILE_OFFSET = 0x0DD9F0
PERSON_RECORD_COUNT = 205
PERSON_RECORD_SIZE = 0xCC
PERSON_FIRST_NAME_POINTER_OFFSET = 0x00
PERSON_LAST_NAME_POINTER_OFFSET = 0x04
PERSON_FACE_CODE_OFFSET = 0x08
PERSON_GENDER_OFFSET = 0x0C
PERSON_AGE_AT_1480_OFFSET = 0x10
PERSON_NATION_ID_OFFSET = 0x18
PERSON_JOB_ID_OFFSET = 0x1C
PERSON_FAME_OFFSET = 0x24
PERSON_INFAMY_OFFSET = 0x28
PERSON_EMPLOYMENT_STATE_OFFSET = 0x2C
PERSON_CITY_ID_OFFSET = 0x30
PERSON_BUILDING_ID_OFFSET = 0x34
PERSON_BLOOD_ID_OFFSET = 0x38
PERSON_VITALITY_OFFSET = 0x3C
PERSON_ABILITIES_OFFSET = 0x40
PERSON_ABILITY_COUNT = 6
PERSON_HIRE_COST_OFFSET = 0x58
PERSON_SKILLS_OFFSET = 0x60
PERSON_SKILL_COUNT = 27
PERSON_ABILITY_MAX = 255
PERSON_VITALITY_MAX = 2000
PERSON_SKILL_MAX = 3
SHIP_TYPE_TABLE_VA = 0x4FC1E0
SHIP_TYPE_RECORD_COUNT = 8
SHIP_TYPE_RECORD_SIZE = 0x40
SHIP_TYPE_NAME_POINTER_OFFSET = 0x00
SHIP_TYPE_SHIPYARD_REQUIREMENT_OFFSET = 0x08
# These fields become the save record's current/max propulsion on purchase;
# they are not naval-combat strength.
SHIP_TYPE_BASE_POWER_OFFSET = 0x0C
SHIP_TYPE_POWER_LIMIT_OFFSET = 0x10
SHIP_TYPE_BASE_DURABILITY_OFFSET = 0x14
SHIP_TYPE_DURABILITY_LIMIT_OFFSET = 0x18
SHIP_TYPE_BASE_WEIGHT_OFFSET = 0x1C
SHIP_TYPE_WEIGHT_LIMIT_OFFSET = 0x20
SHIP_TYPE_BASE_CAPACITY_OFFSET = 0x24
SHIP_TYPE_CAPACITY_LIMIT_OFFSET = 0x28
SHIP_TYPE_BASE_CANNONS_OFFSET = 0x2C
SHIP_TYPE_CANNON_LIMIT_OFFSET = 0x30
SHIP_TYPE_MIN_CREW_STORED_OFFSET = 0x34
SHIP_TYPE_MIN_CREW_DISPLAY_OFFSET = 10
# The longest original displayed type name is "대형카라벨": five Korean
# characters / ten CP949 bytes.  Keep renamed types within that game UI width.
SHIP_TYPE_NAME_MAX_CHARACTERS = 5
SHIP_TYPE_NAME_MAX_BYTES = 10
CITY_TABLE_VA = 0x4D14B0
CITY_RECORD_COUNT = 226
CITY_RECORD_SIZE = 0x88
CITY_NAME_POINTER_OFFSET = 0x00
CITY_WORLD_X_OFFSET = 0x04
CITY_WORLD_Y_OFFSET = 0x08
CITY_INLAND_CONNECTIONS_OFFSET = 0x10
CITY_SHIP_CANDIDATE_MASK_OFFSET = 0x18
CITY_TRADE_REGION_OFFSET = 0x1C
CITY_CULTURE_OFFSET = 0x20
CITY_NATION_OFFSET = 0x24
CITY_SHIPYARD_LEVEL_OFFSET = 0x28
CITY_UPDATE_COUNTER_OFFSET = 0x2C
CITY_SPECIALTY_ID_OFFSET = 0x30
CITY_SPECIALTY_PRICE_OFFSET = 0x34
CITY_SPECIALTY_SUPPLY_INDEX_OFFSET = 0x38
CITY_DEFAULT_MARKET_GOODS_OFFSET = 0x3C
CITY_DEFAULT_MARKET_GOODS_COUNT = 8
CITY_STATUS_OFFSET = 0x5C
CITY_PACKED_FLAGS_OFFSET = 0x60
CITY_WORLD_X_MIN = 0
CITY_WORLD_X_MAX = 2500
CITY_WORLD_Y_MIN = 0
CITY_WORLD_Y_MAX = 1250
CITY_TRADE_REGION_MAX = 26
CITY_CULTURE_MAX = 10
CITY_NATION_MAX = 77
CITY_SHIPYARD_LEVEL_MAX = 7
CITY_UPDATE_COUNTER_MAX = 0xFF
CITY_SPECIALTY_ID_MIN = -1
CITY_SPECIALTY_ID_MAX = 69
CITY_SPECIALTY_PRICE_MAX = 99_999_999
CITY_SPECIALTY_SUPPLY_INDEX_MAX = 7
CITY_SHIP_CANDIDATE_MASK_MAX = 0xFF
CITY_DEFAULT_MARKET_GOOD_MIN = -1
CITY_DEFAULT_MARKET_GOOD_MAX = 285
CITY_STATUS_MAX = 13
CITY_FLAGS_MAX = 0xFFFF
# The longest original city name occupies eight Korean characters / 16 bytes.
CITY_NAME_MAX_CHARACTERS = 8
CITY_NAME_MAX_BYTES = 16
TRADE_GOOD_TABLE_VA = 0x4DCBBC
TRADE_GOOD_RECORD_COUNT = 70
TRADE_GOOD_RECORD_SIZE = 0x88
TRADE_GOOD_NAME_POINTER_OFFSET = 0x7C
# The IDs used by cities start one record later than the name-pointer rows.
# ID 0 therefore reads the record immediately before TRADE_GOOD_TABLE_VA.
TRADE_GOOD_NAME_RECORD_SHIFT = -1
TRADE_REGION_GOODS_TABLE_VA = 0x4DF0E0
TRADE_REGION_GOODS_PER_REGION = 5
ITEM_TABLE_VA = 0x4FD558
ITEM_RECORD_COUNT = 286
ITEM_RECORD_SIZE = 0x1C
ITEM_NAME_POINTER_OFFSET = 0x00
ITEM_IMAGE_ID_OFFSET = 0x04
ITEM_BUY_PRICE_OFFSET = 0x08
ITEM_SELL_PRICE_OFFSET = 0x0C
ITEM_EFFECT_VALUE_OFFSET = 0x10
ITEM_CATEGORY_OFFSET = 0x14
ITEM_NO_IMAGE_ID = 0xFFFFFFFF
ITEM_CATEGORY_MAX = 8
ITEM_PRICE_MAX = 99_999_999
ITEM_EFFECT_MAX = 255
# The longest original item name is "갈라파고스 코끼리거북": 11 characters,
# 21 CP949 bytes.  Keep replacements within the existing game UI width.
ITEM_NAME_MAX_CHARACTERS = 11
ITEM_NAME_MAX_BYTES = 21
# Discovery definitions use two overlapping physical layouts.  Names,
# categories, game IDs and values have 230 contiguous metadata rows and one
# separate final row.  Map rectangles are an independent 231-row overlay
# beginning one row later.  Thus the rectangle for metadata ID 0 (Hope Cape)
# starts at 0x51C584, not at the beginning of its metadata row.
# Dynamic discovery/report progress is deliberately not adjacent to these
# records: it is in SAVEDATA.CDS.
DISCOVERY_METADATA_TABLE_VA = 0x51C528
DISCOVERY_RECORD_COUNT = 231
DISCOVERY_METADATA_TABLE_RECORD_COUNT = 230
DISCOVERY_RECORD_SIZE = 0x5C
# The final metadata entry (game ID 527) is outside the main 230-row table.
DISCOVERY_FINAL_METADATA_RECORD_VA = 0x522744
DISCOVERY_COORDINATE_TABLE_VA = 0x51C584
DISCOVERY_MIN_X_OFFSET = 0x00
DISCOVERY_MIN_Y_OFFSET = 0x04
DISCOVERY_MAX_X_OFFSET = 0x08
DISCOVERY_MAX_Y_OFFSET = 0x0C
DISCOVERY_NAME_POINTER_OFFSET = 0x18
DISCOVERY_CATEGORY_OFFSET = 0x1C
DISCOVERY_GAME_ID_OFFSET = 0x20
DISCOVERY_VALUE_OFFSET = 0x30
# The 231-row metadata view starts 0x18 bytes before the matching records in
# the contiguous 274-row discovery master table.  From that record start the
# three media fields are 0x0C/0x10/0x14.  Do not search the executable for a
# matching name: detached legacy copies use the same name and game ID but can
# carry unrelated media numbers.
DISCOVERY_MEDIA_RECORD_RELATIVE_OFFSET = DISCOVERY_NAME_POINTER_OFFSET
DISCOVERY_MEDIA_STILL_OFFSET = 0x0C
DISCOVERY_MEDIA_AVI_OFFSET = 0x10
DISCOVERY_MEDIA_ANIMATION_OFFSET = 0x14
NO_DISCOVERY_MEDIA = 0xFFFFFFFF
DISCOVER_ANIMATION_PART_COUNT = 29
DISCOVERY_CATEGORY_MAX = 7
DISCOVERY_VALUE_MAX = 99_999_999
DISCOVERY_X_MIN = -1
DISCOVERY_X_MAX = 2500
DISCOVERY_Y_MIN = -1
DISCOVERY_Y_MAX = 1250
DISCOVERY_NAME_MAX_BYTES = DISCOVERY_NAME_SLOT_STRIDE - 1
WORLD_WIDTH = Decimal("2500")
WORLD_HEIGHT = Decimal("1250")
WORLD_LONGITUDE_SPAN = Decimal("360")
WORLD_LATITUDE_SPAN = Decimal("180")
WORLD_LONGITUDE_MIN = Decimal("-180")
WORLD_LONGITUDE_MAX = Decimal("180")
WORLD_LATITUDE_MIN = Decimal("-90")
WORLD_LATITUDE_MAX = Decimal("90")


def _parse_world_degree(value: str | int | float | Decimal, minimum: Decimal, maximum: Decimal, label: str) -> Decimal:
    try:
        degree = Decimal(str(value).strip()).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}는 숫자로 입력해 주세요.")
    if not degree.is_finite() or not minimum <= degree <= maximum:
        raise ValueError(f"{label}는 {minimum}~{maximum}도 사이여야 합니다.")
    return degree


def world_x_to_longitude(world_x: int) -> Decimal:
    """Convert the 0..2500 world X grid to longitude in degrees.

    longitude = world_x * 360 / 2500 - 180
    """
    if not 0 <= world_x <= DISCOVERY_X_MAX:
        raise ValueError("내부 경도 좌표가 올바르지 않습니다.")
    return (Decimal(world_x) * WORLD_LONGITUDE_SPAN / WORLD_WIDTH + WORLD_LONGITUDE_MIN).quantize(
        Decimal("0.001")
    )


def world_y_to_latitude(world_y: int) -> Decimal:
    """Convert the 0..1250 world Y grid to latitude in degrees.

    latitude = 90 - world_y * 180 / 1250
    """
    if not 0 <= world_y <= DISCOVERY_Y_MAX:
        raise ValueError("내부 위도 좌표가 올바르지 않습니다.")
    return (WORLD_LATITUDE_MAX - Decimal(world_y) * WORLD_LATITUDE_SPAN / WORLD_HEIGHT).quantize(
        Decimal("0.001")
    )


def longitude_to_world_x(longitude: str | int | float | Decimal) -> int:
    value = _parse_world_degree(longitude, WORLD_LONGITUDE_MIN, WORLD_LONGITUDE_MAX, "경도")
    return int(((value - WORLD_LONGITUDE_MIN) * WORLD_WIDTH / WORLD_LONGITUDE_SPAN).to_integral_value(
        rounding=ROUND_HALF_UP
    ))


def latitude_to_world_y(latitude: str | int | float | Decimal) -> int:
    value = _parse_world_degree(latitude, WORLD_LATITUDE_MIN, WORLD_LATITUDE_MAX, "위도")
    return int(((WORLD_LATITUDE_MAX - value) * WORLD_HEIGHT / WORLD_LATITUDE_SPAN).to_integral_value(
        rounding=ROUND_HALF_UP
    ))
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
    if not 0 <= arrival_wait_days <= MAX_NPC_ARRIVAL_WAIT_DAYS:
        raise ValueError(f"일반 NPC 도착 대기는 0~{MAX_NPC_ARRIVAL_WAIT_DAYS}일 사이여야 합니다.")
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        wait_offset = pe.get_offset_from_rva(NPC_WAIT_VA - pe.OPTIONAL_HEADER.ImageBase)
        roll_offset = pe.get_offset_from_rva(NPC_ROLL_VA - pe.OPTIONAL_HEADER.ImageBase)
        current_wait = bytes(data[wait_offset:wait_offset + 7])
        if current_wait != NPC_ORIGINAL_WAIT and not (
            current_wait[:6] == NPC_COMPARE_PREFIX
            and -60 <= struct.unpack("b", current_wait[6:7])[0] <= MAX_NPC_ARRIVAL_WAIT_DAYS - 60
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


def _read_limit_operands(
    data: bytes,
    pe: pefile.PE,
    operands: tuple[tuple[int, bytes], ...],
    label: str,
) -> tuple[int, ...]:
    """Read the matching immediate operands used by one money-limit setting."""
    values: list[int] = []
    for value_va, instruction_prefix in operands:
        value_offset = pe.get_offset_from_rva(value_va - pe.OPTIONAL_HEADER.ImageBase)
        prefix_offset = value_offset - len(instruction_prefix)
        if data[prefix_offset:value_offset] != instruction_prefix:
            raise ValueError(f"{label} 위치 0x{value_va:X}을(를) 검증하지 못했습니다.")
        value = struct.unpack_from("<I", data, value_offset)[0]
        # The game compares signed 32-bit values.  Older community patches may
        # use a larger value, so accept them while reading and allow this tool
        # to bring the setting back into its supported 99,999,999 range.
        if not 1 <= value <= 0x7FFF_FFFF:
            raise ValueError(f"{label} 값 0x{value_va:X}을(를) 읽지 못했습니다.")
        values.append(value)
    return tuple(values)


def _write_limit_operands(
    data: bytearray,
    pe: pefile.PE,
    operands: tuple[tuple[int, bytes], ...],
    label: str,
    value: int,
) -> None:
    """Verify and update every code path that uses one money limit."""
    _read_limit_operands(bytes(data), pe, operands, label)
    for value_va, _ in operands:
        value_offset = pe.get_offset_from_rva(value_va - pe.OPTIONAL_HEADER.ImageBase)
        struct.pack_into("<I", data, value_offset, value)


def _read_gameplay_options_from_data(data: bytes) -> tuple[int, int, int, int, int, int, int, int, int]:
    """Read and validate the editable gameplay operands."""
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
        cash_limit = _read_limit_operands(data, pe, CASH_LIMIT_OPERANDS, "소지금 상한")[0]
        deposit_limit = _read_limit_operands(data, pe, DEPOSIT_LIMIT_OPERANDS, "저금 상한")[0]
        fame_limit = _read_limit_operands(data, pe, FAME_LIMIT_OPERANDS, "명성 상한")[0]
        infamy_limit = _read_limit_operands(data, pe, INFAMY_LIMIT_OPERANDS, "악명 상한")[0]
        return (
            long_rest_max,
            preparation_values[0],
            succession_age,
            cold_north_limit,
            cold_south_limit,
            cash_limit,
            deposit_limit,
            fame_limit,
            infamy_limit,
        )
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
    cash_limit: int,
    deposit_limit: int,
    fame_limit: int,
    infamy_limit: int,
) -> bool:
    """Set rest, exploration, money and polar-cold parameters."""
    if not 1 <= long_rest_max <= 127:
        raise ValueError("장기 휴양 최대 기간은 1~127개월 사이여야 합니다.")
    if not 1 <= exploration_preparation_days <= 127:
        raise ValueError("탐험 준비 기간은 1~127일 사이여야 합니다.")
    if not 1 <= succession_min_age <= 127:
        raise ValueError("세대교체 가능 나이는 1~127세 사이여야 합니다.")
    if not 0 <= cold_north_limit <= 20000 or not 0 <= cold_south_limit <= 20000:
        raise ValueError("극지방 추위 판정 좌표는 0~20,000 사이여야 합니다.")
    if not MONEY_LIMIT_MIN <= cash_limit <= MONEY_LIMIT_MAX:
        raise ValueError("소지금 상한은 1~99,999,999 두캇 사이여야 합니다.")
    if not MONEY_LIMIT_MIN <= deposit_limit <= MONEY_LIMIT_MAX:
        raise ValueError("저금 상한은 1~99,999,999 두캇 사이여야 합니다.")
    if not FAME_INFAMY_LIMIT_MIN <= fame_limit <= FAME_LIMIT_MAX:
        raise ValueError("명성 상한은 1~99,999,999 사이여야 합니다.")
    if not FAME_INFAMY_LIMIT_MIN <= infamy_limit <= INFAMY_LIMIT_MAX:
        raise ValueError("악명 상한은 1~99,999,999 사이여야 합니다.")

    current = _read_gameplay_options_from_data(bytes(data))
    target = (
        long_rest_max,
        exploration_preparation_days,
        succession_min_age,
        cold_north_limit,
        cold_south_limit,
        cash_limit,
        deposit_limit,
        fame_limit,
        infamy_limit,
    )
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        displayed_days = _read_exploration_preparation_message_days(bytes(data), pe)
        cash_values = _read_limit_operands(bytes(data), pe, CASH_LIMIT_OPERANDS, "소지금 상한")
        deposit_values = _read_limit_operands(bytes(data), pe, DEPOSIT_LIMIT_OPERANDS, "저금 상한")
        fame_values = _read_limit_operands(bytes(data), pe, FAME_LIMIT_OPERANDS, "명성 상한")
        infamy_values = _read_limit_operands(bytes(data), pe, INFAMY_LIMIT_OPERANDS, "악명 상한")
        if (
            current == target
            and displayed_days == exploration_preparation_days
            and all(value == cash_limit for value in cash_values)
            and all(value == deposit_limit for value in deposit_values)
            and all(value == fame_limit for value in fame_values)
            and all(value == infamy_limit for value in infamy_values)
        ):
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
        _write_limit_operands(data, pe, CASH_LIMIT_OPERANDS, "소지금 상한", cash_limit)
        _write_limit_operands(data, pe, DEPOSIT_LIMIT_OPERANDS, "저금 상한", deposit_limit)
        _write_limit_operands(data, pe, FAME_LIMIT_OPERANDS, "명성 상한", fame_limit)
        _write_limit_operands(data, pe, INFAMY_LIMIT_OPERANDS, "악명 상한", infamy_limit)
        return True
    finally:
        pe.close()


def _read_barmaid_records_from_data(data: bytes) -> tuple[BarmaidRecord, ...]:
    """Read and validate the immutable barmaid table embedded in the EXE."""
    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        table_offset = pe.get_offset_from_rva(BARMAID_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase)
        table_size = BARMAID_RECORD_COUNT * BARMAID_RECORD_SIZE
        if table_offset < 0 or table_offset + table_size > len(data):
            raise ValueError("여급 마스터 테이블의 범위를 검증하지 못했습니다.")

        records: list[BarmaidRecord] = []
        for identifier in range(BARMAID_RECORD_COUNT):
            record_offset = table_offset + identifier * BARMAID_RECORD_SIZE
            name_va = struct.unpack_from("<I", data, record_offset + BARMAID_NAME_POINTER_OFFSET)[0]
            try:
                name_offset = pe.get_offset_from_rva(name_va - pe.OPTIONAL_HEADER.ImageBase)
            except pefile.PEFormatError as error:
                raise ValueError(f"여급 {identifier}번 이름 주소를 검증하지 못했습니다.") from error
            if not 0 <= name_offset < len(data):
                raise ValueError(f"여급 {identifier}번 이름 주소를 검증하지 못했습니다.")
            name_end = data.find(b"\0", name_offset, min(name_offset + 64, len(data)))
            if name_end < 0:
                raise ValueError(f"여급 {identifier}번 이름의 끝을 찾지 못했습니다.")
            try:
                name = data[name_offset:name_end].decode("cp949")
            except UnicodeDecodeError as error:
                raise ValueError(f"여급 {identifier}번 이름을 읽지 못했습니다.") from error
            face_code = struct.unpack_from("<I", data, record_offset + BARMAID_FACE_CODE_OFFSET)[0]
            # The game cannot distinguish an earlier date from its 1480 start,
            # so preserve such original offsets while presenting them as 1480.
            appearance_year = max(
                BARMAID_APPEARANCE_YEAR_MIN,
                BARMAID_APPEARANCE_YEAR_REFERENCE
                - struct.unpack_from("<i", data, record_offset + BARMAID_APPEARANCE_YEAR_OFFSET)[0],
            )
            city_id = struct.unpack_from("<I", data, record_offset + BARMAID_CITY_ID_OFFSET)[0]
            personality_id = struct.unpack_from("<I", data, record_offset + BARMAID_PERSONALITY_OFFSET)[0]
            language_flags = struct.unpack_from("<I", data, record_offset + BARMAID_LANGUAGE_FLAGS_OFFSET)[0]
            if not name or not 0 <= face_code <= BARMAID_FACE_CODE_MAX:
                raise ValueError(f"여급 {identifier}번 레코드의 얼굴 코드 값을 검증하지 못했습니다.")
            if not BARMAID_APPEARANCE_YEAR_MIN <= appearance_year <= BARMAID_APPEARANCE_YEAR_MAX:
                raise ValueError(f"여급 {identifier}번 레코드의 출현 연도 값을 검증하지 못했습니다.")
            if not 0 <= city_id <= BARMAID_CITY_ID_MAX:
                raise ValueError(f"여급 {identifier}번 레코드의 출현 도시 값을 검증하지 못했습니다.")
            if not 0 <= personality_id < BARMAID_PERSONALITY_COUNT:
                raise ValueError(f"여급 {identifier}번 레코드의 성격 값을 검증하지 못했습니다.")
            if language_flags & ~BARMAID_LANGUAGE_MASK:
                raise ValueError(f"여급 {identifier}번 레코드의 언어 값을 검증하지 못했습니다.")
            records.append(
                BarmaidRecord(identifier, name, face_code, appearance_year, city_id, personality_id, language_flags)
            )
        return tuple(records)
    finally:
        pe.close()


def read_barmaid_records(target: Path) -> tuple[BarmaidRecord, ...]:
    """Read the selectable barmaid entries from a supported executable."""
    target = target.resolve(strict=True)
    return _read_barmaid_records_from_data(target.read_bytes())


def _read_barmaid_child_aptitudes_from_data(data: bytes) -> tuple[BarmaidChildAptitudes, ...]:
    """Read the face-code-indexed child aptitude table from the EXE."""
    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        table_offset = pe.get_offset_from_rva(
            BARMAID_CHILD_APTITUDE_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
        table_size = (BARMAID_FACE_CODE_MAX + 1) * BARMAID_CHILD_APTITUDE_RECORD_SIZE
        if table_offset < 0 or table_offset + table_size > len(data):
            raise ValueError("여급 자녀 능력치 보정 테이블의 범위를 검증하지 못했습니다.")
        records: list[BarmaidChildAptitudes] = []
        for face_code in range(BARMAID_FACE_CODE_MAX + 1):
            record_offset = table_offset + face_code * BARMAID_CHILD_APTITUDE_RECORD_SIZE
            modifiers = struct.unpack_from("<6i", data, record_offset)
            if any(not BARMAID_CHILD_APTITUDE_MIN <= value <= BARMAID_CHILD_APTITUDE_MAX for value in modifiers):
                raise ValueError(f"얼굴 코드 {face_code}의 자녀 능력치 보정값을 검증하지 못했습니다.")
            records.append(BarmaidChildAptitudes(face_code, modifiers))
        return tuple(records)
    finally:
        pe.close()


def read_barmaid_child_aptitudes(target: Path) -> tuple[BarmaidChildAptitudes, ...]:
    """Read all female-face child aptitude modifiers from a supported EXE."""
    target = target.resolve(strict=True)
    return _read_barmaid_child_aptitudes_from_data(target.read_bytes())


def apply_barmaid_edit(data: bytearray, edit: BarmaidEdit | None) -> bool:
    """Write one selected barmaid's static master settings."""
    if edit is None:
        return False
    if not 0 <= edit.identifier < BARMAID_RECORD_COUNT:
        raise ValueError("수정할 여급 번호가 올바르지 않습니다.")
    name = edit.name.strip()
    if not name:
        raise ValueError("여급 이름을 입력해 주세요.")
    try:
        name_bytes = name.encode("cp949")
    except UnicodeEncodeError as error:
        raise ValueError("여급 이름은 CP949에서 사용할 수 있는 문자만 입력할 수 있습니다.") from error
    if len(name_bytes) > BARMAID_NAME_MAX_BYTES:
        raise ValueError("여급 이름은 한글 기준 최대 6자(12바이트)까지 입력할 수 있습니다.")
    if not 0 <= edit.face_code <= BARMAID_FACE_CODE_MAX:
        raise ValueError("여급 얼굴 코드는 0~143 사이여야 합니다.")
    if not BARMAID_APPEARANCE_YEAR_MIN <= edit.appearance_year <= BARMAID_APPEARANCE_YEAR_MAX:
        raise ValueError("여급 출현 연도는 1480~1600 사이여야 합니다.")
    if not 0 <= edit.city_id <= BARMAID_CITY_ID_MAX:
        raise ValueError("여급 출현 도시 ID는 0~225 사이여야 합니다.")
    if not 0 <= edit.personality_id < BARMAID_PERSONALITY_COUNT:
        raise ValueError("여급 성격이 올바르지 않습니다.")
    if not 0 <= edit.language_flags <= BARMAID_LANGUAGE_MASK:
        raise ValueError("여급 전수 언어 값이 올바르지 않습니다.")
    if len(edit.child_aptitude_modifiers) != BARMAID_CHILD_APTITUDE_MODIFIER_COUNT:
        raise ValueError("자녀 능력치 보정값은 6개여야 합니다.")
    if any(
        not BARMAID_CHILD_APTITUDE_MIN <= value <= BARMAID_CHILD_APTITUDE_MAX
        for value in edit.child_aptitude_modifiers
    ):
        raise ValueError("자녀 능력치 보정값은 각각 -255~+255 사이여야 합니다.")

    records = _read_barmaid_records_from_data(bytes(data))
    _read_barmaid_child_aptitudes_from_data(bytes(data))
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        table_offset = pe.get_offset_from_rva(BARMAID_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase)
        record_offset = table_offset + edit.identifier * BARMAID_RECORD_SIZE
        current_name = records[edit.identifier].name
        current_face_code = struct.unpack_from("<I", data, record_offset + BARMAID_FACE_CODE_OFFSET)[0]
        current_appearance_year_offset = struct.unpack_from(
            "<i", data, record_offset + BARMAID_APPEARANCE_YEAR_OFFSET
        )[0]
        current_appearance_year = max(
            BARMAID_APPEARANCE_YEAR_MIN,
            BARMAID_APPEARANCE_YEAR_REFERENCE - current_appearance_year_offset,
        )
        current_city_id = struct.unpack_from("<I", data, record_offset + BARMAID_CITY_ID_OFFSET)[0]
        current_personality = struct.unpack_from("<I", data, record_offset + BARMAID_PERSONALITY_OFFSET)[0]
        current_languages = struct.unpack_from("<I", data, record_offset + BARMAID_LANGUAGE_FLAGS_OFFSET)[0]
        aptitude_table_offset = pe.get_offset_from_rva(
            BARMAID_CHILD_APTITUDE_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
        aptitude_offset = aptitude_table_offset + edit.face_code * BARMAID_CHILD_APTITUDE_RECORD_SIZE
        current_aptitudes = struct.unpack_from("<6i", data, aptitude_offset)
        if (
            current_name == name
            and current_face_code == edit.face_code
            and current_appearance_year == edit.appearance_year
            and current_city_id == edit.city_id
            and current_personality == edit.personality_id
            and current_languages == edit.language_flags
            and current_aptitudes == edit.child_aptitude_modifiers
        ):
            return False
        if current_name != name:
            section, _created = ensure_patch_section(data, PATCH_SECTION_EXPANDED_SIZE)
            slot_offset, slot_va = section.slot(
                BARMAID_NAME_SLOT_OFFSET + edit.identifier * BARMAID_NAME_SLOT_STRIDE,
                BARMAID_NAME_SLOT_STRIDE,
            )
            name_payload = name_bytes + b"\0"
            if len(name_payload) > BARMAID_NAME_SLOT_STRIDE:
                raise ValueError("여급 이름 저장 공간이 부족합니다.")
            data[slot_offset:slot_offset + BARMAID_NAME_SLOT_STRIDE] = b"\0" * BARMAID_NAME_SLOT_STRIDE
            data[slot_offset:slot_offset + len(name_payload)] = name_payload
            struct.pack_into("<I", data, record_offset + BARMAID_NAME_POINTER_OFFSET, slot_va)
        struct.pack_into("<I", data, record_offset + BARMAID_FACE_CODE_OFFSET, edit.face_code)
        if current_appearance_year != edit.appearance_year:
            struct.pack_into(
                "<i", data, record_offset + BARMAID_APPEARANCE_YEAR_OFFSET,
                BARMAID_APPEARANCE_YEAR_REFERENCE - edit.appearance_year,
            )
        struct.pack_into("<I", data, record_offset + BARMAID_CITY_ID_OFFSET, edit.city_id)
        struct.pack_into("<I", data, record_offset + BARMAID_PERSONALITY_OFFSET, edit.personality_id)
        struct.pack_into("<I", data, record_offset + BARMAID_LANGUAGE_FLAGS_OFFSET, edit.language_flags)
        struct.pack_into("<6i", data, aptitude_offset, *edit.child_aptitude_modifiers)
        return True
    finally:
        pe.close()


def _read_person_records_from_data(data: bytes) -> tuple[PersonRecord, ...]:
    """Read the 205 general-person master records without touching dynamic saves."""
    if PERSON_TABLE_FILE_OFFSET + PERSON_RECORD_COUNT * PERSON_RECORD_SIZE > len(data):
        raise ValueError("인물 마스터 테이블의 범위를 검증하지 못했습니다.")
    pe = pefile.PE(data=data, fast_load=True)
    try:
        records: list[PersonRecord] = []
        for identifier in range(PERSON_RECORD_COUNT):
            offset = PERSON_TABLE_FILE_OFFSET + identifier * PERSON_RECORD_SIZE
            def string_at(field: int) -> str:
                pointer = struct.unpack_from("<I", data, offset + field)[0]
                try:
                    text_offset = pe.get_offset_from_rva(pointer - pe.OPTIONAL_HEADER.ImageBase)
                except pefile.PEFormatError as error:
                    raise ValueError(f"인물 {identifier}번 이름 주소를 검증하지 못했습니다.") from error
                end = data.find(b"\0", text_offset, min(text_offset + 64, len(data)))
                if end < 0:
                    raise ValueError(f"인물 {identifier}번 이름의 끝을 찾지 못했습니다.")
                return data[text_offset:end].decode("cp949")
            first, last = string_at(PERSON_FIRST_NAME_POINTER_OFFSET), string_at(PERSON_LAST_NAME_POINTER_OFFSET)
            face, gender = struct.unpack_from("<II", data, offset + PERSON_FACE_CODE_OFFSET)
            age, nation, job = struct.unpack_from("<iii", data, offset + PERSON_AGE_AT_1480_OFFSET)[0], struct.unpack_from("<i", data, offset + PERSON_NATION_ID_OFFSET)[0], struct.unpack_from("<i", data, offset + PERSON_JOB_ID_OFFSET)[0]
            fame, infamy = struct.unpack_from("<II", data, offset + PERSON_FAME_OFFSET)
            employment_state = struct.unpack_from("<i", data, offset + PERSON_EMPLOYMENT_STATE_OFFSET)[0]
            city, building, blood = struct.unpack_from("<iii", data, offset + PERSON_CITY_ID_OFFSET)
            vitality = struct.unpack_from("<I", data, offset + PERSON_VITALITY_OFFSET)[0]
            hire_cost = struct.unpack_from("<I", data, offset + PERSON_HIRE_COST_OFFSET)[0]
            abilities = struct.unpack_from(f"<{PERSON_ABILITY_COUNT}I", data, offset + PERSON_ABILITIES_OFFSET)
            skills = struct.unpack_from(f"<{PERSON_SKILL_COUNT}I", data, offset + PERSON_SKILLS_OFFSET)
            if gender not in (0, 1) or not (0 <= face <= (143 if gender else 413)) or not -100 <= age <= 100 or not 0 <= nation <= 18 or not 0 <= job <= 3 or not 0 <= fame <= 65535 or not 0 <= infamy <= 65535 or employment_state not in (0, 1, 2) or not -1 <= city <= 225 or not 0 <= building <= 15 or not 0 <= blood <= 3 or not 0 <= vitality <= PERSON_VITALITY_MAX or not 0 <= hire_cost <= 2000 or any(not 0 <= value <= PERSON_ABILITY_MAX for value in abilities) or any(not 0 <= value <= PERSON_SKILL_MAX for value in skills):
                raise ValueError(f"인물 {identifier}번 마스터 값을 검증하지 못했습니다.")
            records.append(PersonRecord(identifier, f"{first} {last}".strip(), face, gender, age, nation, job, fame, infamy, employment_state, city, building, blood, vitality, hire_cost, abilities, skills))
        return tuple(records)
    finally:
        pe.close()


def read_person_records(target: Path) -> tuple[PersonRecord, ...]:
    return _read_person_records_from_data(target.resolve(strict=True).read_bytes())


def apply_person_edit(data: bytearray, edit: PersonEdit | None) -> bool:
    if edit is None:
        return False
    if not 0 <= edit.identifier < PERSON_RECORD_COUNT or edit.gender not in (0, 1) or not 0 <= edit.face_code <= (143 if edit.gender else 413) or not -100 <= edit.age_at_1480 <= 100 or not 0 <= edit.nation_id <= 18 or not 0 <= edit.job_id <= 3 or not 0 <= edit.fame <= 65535 or not 0 <= edit.infamy <= 65535 or edit.employment_state not in (0, 1, 2) or not -1 <= edit.city_id <= 225 or not 0 <= edit.building_id <= 15 or not 0 <= edit.blood_id <= 3 or not 0 <= edit.vitality <= PERSON_VITALITY_MAX or not 0 <= edit.hire_cost <= 2000 or len(edit.abilities) != PERSON_ABILITY_COUNT or any(not 0 <= value <= PERSON_ABILITY_MAX for value in edit.abilities) or len(edit.skills) != PERSON_SKILL_COUNT or any(not 0 <= value <= PERSON_SKILL_MAX for value in edit.skills):
        raise ValueError("인물 입력값을 확인해 주세요.")
    current = _read_person_records_from_data(bytes(data))[edit.identifier]
    if (current.face_code, current.gender, current.age_at_1480, current.nation_id, current.job_id, current.fame, current.infamy, current.employment_state, current.city_id, current.building_id, current.blood_id, current.vitality, current.hire_cost, current.abilities, current.skills) == (edit.face_code, edit.gender, edit.age_at_1480, edit.nation_id, edit.job_id, edit.fame, edit.infamy, edit.employment_state, edit.city_id, edit.building_id, edit.blood_id, edit.vitality, edit.hire_cost, edit.abilities, edit.skills):
        return False
    offset = PERSON_TABLE_FILE_OFFSET + edit.identifier * PERSON_RECORD_SIZE
    struct.pack_into("<IIi", data, offset + PERSON_FACE_CODE_OFFSET, edit.face_code, edit.gender, edit.age_at_1480)
    struct.pack_into("<i", data, offset + PERSON_NATION_ID_OFFSET, edit.nation_id)
    struct.pack_into("<i", data, offset + PERSON_JOB_ID_OFFSET, edit.job_id)
    struct.pack_into("<II", data, offset + PERSON_FAME_OFFSET, edit.fame, edit.infamy)
    struct.pack_into("<i", data, offset + PERSON_EMPLOYMENT_STATE_OFFSET, edit.employment_state)
    struct.pack_into("<iii", data, offset + PERSON_CITY_ID_OFFSET, edit.city_id, edit.building_id, edit.blood_id)
    struct.pack_into("<I", data, offset + PERSON_VITALITY_OFFSET, edit.vitality)
    struct.pack_into(f"<{PERSON_ABILITY_COUNT}I", data, offset + PERSON_ABILITIES_OFFSET, *edit.abilities)
    struct.pack_into("<I", data, offset + PERSON_HIRE_COST_OFFSET, edit.hire_cost)
    struct.pack_into(f"<{PERSON_SKILL_COUNT}I", data, offset + PERSON_SKILLS_OFFSET, *edit.skills)
    return True


def _read_ship_type_records_from_data(data: bytes) -> tuple[ShipTypeRecord, ...]:
    """Read the fixed-width static ship-type master table from a supported EXE."""
    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        table_offset = pe.get_offset_from_rva(SHIP_TYPE_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase)
        table_size = SHIP_TYPE_RECORD_COUNT * SHIP_TYPE_RECORD_SIZE
        if table_offset < 0 or table_offset + table_size > len(data):
            raise ValueError("선종 마스터 테이블의 범위를 검증하지 못했습니다.")
        records: list[ShipTypeRecord] = []
        for identifier in range(SHIP_TYPE_RECORD_COUNT):
            offset = table_offset + identifier * SHIP_TYPE_RECORD_SIZE
            name_va = struct.unpack_from("<I", data, offset + SHIP_TYPE_NAME_POINTER_OFFSET)[0]
            try:
                name_offset = pe.get_offset_from_rva(name_va - pe.OPTIONAL_HEADER.ImageBase)
            except pefile.PEFormatError as error:
                raise ValueError(f"선종 {identifier}번 이름 주소를 검증하지 못했습니다.") from error
            name_end = data.find(b"\0", name_offset, min(name_offset + 64, len(data)))
            if not 0 <= name_offset < len(data) or name_end < 0:
                raise ValueError(f"선종 {identifier}번 이름 주소를 검증하지 못했습니다.")
            try:
                name = data[name_offset:name_end].decode("cp949")
            except UnicodeDecodeError as error:
                raise ValueError(f"선종 {identifier}번 이름을 읽지 못했습니다.") from error
            values = struct.unpack_from("<16I", data, offset)
            (_, _, shipyard_requirement, base_power, power_limit, base_durability,
             durability_limit, base_weight, weight_limit, base_capacity,
             capacity_limit, base_cannons, cannon_limit, min_crew_stored, _, _) = values
            min_crew = min_crew_stored + SHIP_TYPE_MIN_CREW_DISPLAY_OFFSET
            if (shipyard_requirement > 127 or base_power > power_limit or power_limit > 255
                    or base_durability > durability_limit or base_cannons > cannon_limit
                    or cannon_limit > 255 or base_weight > weight_limit
                    or base_capacity > capacity_limit or min_crew > 265):
                raise ValueError(f"선종 {identifier}번 마스터 값을 검증하지 못했습니다.")
            records.append(ShipTypeRecord(
                identifier, name, shipyard_requirement, base_power, power_limit,
                base_durability, durability_limit, base_weight, weight_limit,
                base_capacity, capacity_limit, base_cannons, cannon_limit, min_crew,
            ))
        return tuple(records)
    finally:
        pe.close()


def read_ship_type_records(target: Path) -> tuple[ShipTypeRecord, ...]:
    return _read_ship_type_records_from_data(target.resolve(strict=True).read_bytes())


def apply_ship_type_edit(data: bytearray, edit: ShipTypeEdit | None) -> bool:
    """Apply one ship-type edit without changing any existing save-game ship."""
    if edit is None:
        return False
    name = edit.name.strip()
    if not name:
        raise ValueError("선종 이름을 입력해 주세요.")
    try:
        name_bytes = name.encode("cp949")
    except UnicodeEncodeError as error:
        raise ValueError("선종 이름은 CP949에서 사용할 수 있는 문자만 입력할 수 있습니다.") from error
    if len(name) > SHIP_TYPE_NAME_MAX_CHARACTERS or len(name_bytes) > SHIP_TYPE_NAME_MAX_BYTES:
        raise ValueError("선종 이름은 한글 최대 5자(10바이트)까지 입력할 수 있습니다.")
    if (not 0 <= edit.identifier < SHIP_TYPE_RECORD_COUNT
            or not 0 <= edit.shipyard_requirement <= 127
            or not 0 <= edit.base_power <= edit.power_limit <= 255
            or not 0 <= edit.base_durability <= edit.durability_limit <= 0x7FFFFFFF
            or not 0 <= edit.base_weight <= edit.weight_limit <= 0xFFFFFFFF
            or not 0 <= edit.base_capacity <= edit.capacity_limit <= 0xFFFFFFFF
            or not 0 <= edit.base_cannons <= edit.cannon_limit <= 255
            or not SHIP_TYPE_MIN_CREW_DISPLAY_OFFSET <= edit.min_crew <= 265):
        raise ValueError("선종 입력값을 확인해 주세요.")
    current = _read_ship_type_records_from_data(bytes(data))[edit.identifier]
    if current == ShipTypeRecord(
        edit.identifier, name, edit.shipyard_requirement, edit.base_power,
        edit.power_limit, edit.base_durability, edit.durability_limit,
        edit.base_weight, edit.weight_limit, edit.base_capacity, edit.capacity_limit,
        edit.base_cannons, edit.cannon_limit, edit.min_crew,
    ):
        return False
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        offset = pe.get_offset_from_rva(SHIP_TYPE_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase) + edit.identifier * SHIP_TYPE_RECORD_SIZE
    finally:
        pe.close()
    if current.name != name:
        section, _created = ensure_patch_section(data, PATCH_SECTION_MASTER_NAMES_SIZE)
        slot_offset, slot_va = section.slot(
            SHIP_TYPE_NAME_SLOT_OFFSET + edit.identifier * SHIP_TYPE_NAME_SLOT_STRIDE,
            SHIP_TYPE_NAME_SLOT_STRIDE,
        )
        data[slot_offset:slot_offset + SHIP_TYPE_NAME_SLOT_STRIDE] = b"\0" * SHIP_TYPE_NAME_SLOT_STRIDE
        data[slot_offset:slot_offset + len(name_bytes) + 1] = name_bytes + b"\0"
        struct.pack_into("<I", data, offset + SHIP_TYPE_NAME_POINTER_OFFSET, slot_va)
    struct.pack_into("<I", data, offset + SHIP_TYPE_SHIPYARD_REQUIREMENT_OFFSET, edit.shipyard_requirement)
    struct.pack_into("<II", data, offset + SHIP_TYPE_BASE_POWER_OFFSET, edit.base_power, edit.power_limit)
    struct.pack_into("<II", data, offset + SHIP_TYPE_BASE_DURABILITY_OFFSET, edit.base_durability, edit.durability_limit)
    struct.pack_into("<II", data, offset + SHIP_TYPE_BASE_WEIGHT_OFFSET, edit.base_weight, edit.weight_limit)
    struct.pack_into("<II", data, offset + SHIP_TYPE_BASE_CAPACITY_OFFSET, edit.base_capacity, edit.capacity_limit)
    struct.pack_into("<II", data, offset + SHIP_TYPE_BASE_CANNONS_OFFSET, edit.base_cannons, edit.cannon_limit)
    struct.pack_into("<I", data, offset + SHIP_TYPE_MIN_CREW_STORED_OFFSET, edit.min_crew - SHIP_TYPE_MIN_CREW_DISPLAY_OFFSET)
    return True


def _read_city_records_from_data(data: bytes) -> tuple[CityRecord, ...]:
    """Read the 226-row static city table from a supported executable."""
    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        table_offset = pe.get_offset_from_rva(CITY_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase)
        table_size = CITY_RECORD_COUNT * CITY_RECORD_SIZE
        if table_offset < 0 or table_offset + table_size > len(data):
            raise ValueError("도시 마스터 테이블의 범위를 검증하지 못했습니다.")
        records: list[CityRecord] = []
        for identifier in range(CITY_RECORD_COUNT):
            offset = table_offset + identifier * CITY_RECORD_SIZE
            name_va = struct.unpack_from("<I", data, offset + CITY_NAME_POINTER_OFFSET)[0]
            try:
                name_offset = pe.get_offset_from_rva(name_va - pe.OPTIONAL_HEADER.ImageBase)
            except pefile.PEFormatError as error:
                raise ValueError(f"도시 {identifier}번 이름 주소를 검증하지 못했습니다.") from error
            name_end = data.find(b"\0", name_offset, min(name_offset + 64, len(data)))
            if not 0 <= name_offset < len(data) or name_end < 0:
                raise ValueError(f"도시 {identifier}번 이름 주소를 검증하지 못했습니다.")
            try:
                name = data[name_offset:name_end].decode("cp949")
            except UnicodeDecodeError as error:
                raise ValueError(f"도시 {identifier}번 이름을 읽지 못했습니다.") from error
            world_x, world_y = struct.unpack_from("<ii", data, offset + CITY_WORLD_X_OFFSET)
            inland_connection_ids = struct.unpack_from(
                "<ii", data, offset + CITY_INLAND_CONNECTIONS_OFFSET,
            )
            ship_candidate_mask = struct.unpack_from(
                "<I", data, offset + CITY_SHIP_CANDIDATE_MASK_OFFSET,
            )[0]
            trade_region_id = struct.unpack_from("<i", data, offset + CITY_TRADE_REGION_OFFSET)[0]
            culture_id = struct.unpack_from("<i", data, offset + CITY_CULTURE_OFFSET)[0]
            nation_id = struct.unpack_from("<i", data, offset + CITY_NATION_OFFSET)[0]
            shipyard_level = struct.unpack_from("<i", data, offset + CITY_SHIPYARD_LEVEL_OFFSET)[0]
            update_counter = struct.unpack_from("<i", data, offset + CITY_UPDATE_COUNTER_OFFSET)[0]
            specialty_id = struct.unpack_from("<i", data, offset + CITY_SPECIALTY_ID_OFFSET)[0]
            specialty_price = struct.unpack_from("<i", data, offset + CITY_SPECIALTY_PRICE_OFFSET)[0]
            specialty_supply_index = struct.unpack_from(
                "<i", data, offset + CITY_SPECIALTY_SUPPLY_INDEX_OFFSET,
            )[0]
            default_market_goods = struct.unpack_from(
                f"<{CITY_DEFAULT_MARKET_GOODS_COUNT}i", data,
                offset + CITY_DEFAULT_MARKET_GOODS_OFFSET,
            )
            city_status = struct.unpack_from("<i", data, offset + CITY_STATUS_OFFSET)[0]
            packed_flags = struct.unpack_from("<I", data, offset + CITY_PACKED_FLAGS_OFFSET)[0]
            facility_flags = packed_flags & CITY_FLAGS_MAX
            default_flags = packed_flags >> 16
            if (
                not name
                or not CITY_WORLD_X_MIN <= world_x <= CITY_WORLD_X_MAX
                or not CITY_WORLD_Y_MIN <= world_y <= CITY_WORLD_Y_MAX
                or any(not -1 <= value < CITY_RECORD_COUNT for value in inland_connection_ids)
                or not 0 <= ship_candidate_mask <= CITY_SHIP_CANDIDATE_MASK_MAX
                or not 0 <= trade_region_id <= CITY_TRADE_REGION_MAX
                or not 0 <= culture_id <= CITY_CULTURE_MAX
                or not 0 <= nation_id <= CITY_NATION_MAX
                or not 0 <= shipyard_level <= CITY_SHIPYARD_LEVEL_MAX
                or not 0 <= update_counter <= CITY_UPDATE_COUNTER_MAX
                or not CITY_SPECIALTY_ID_MIN <= specialty_id <= CITY_SPECIALTY_ID_MAX
                or not 0 <= specialty_price <= CITY_SPECIALTY_PRICE_MAX
                or not 0 <= specialty_supply_index <= CITY_SPECIALTY_SUPPLY_INDEX_MAX
                or any(
                    not CITY_DEFAULT_MARKET_GOOD_MIN <= value <= CITY_DEFAULT_MARKET_GOOD_MAX
                    for value in default_market_goods
                )
                or not 0 <= city_status <= CITY_STATUS_MAX
            ):
                raise ValueError(f"도시 {identifier}번 마스터 값을 검증하지 못했습니다.")
            records.append(CityRecord(
                identifier, name, world_x, world_y, inland_connection_ids,
                ship_candidate_mask, trade_region_id, culture_id, nation_id,
                shipyard_level, update_counter, specialty_id, specialty_price,
                specialty_supply_index, default_market_goods, city_status,
                facility_flags, default_flags,
            ))
        return tuple(records)
    finally:
        pe.close()


def read_city_records(target: Path) -> tuple[CityRecord, ...]:
    """Read EXE-side city definitions without touching saved game state."""
    return _read_city_records_from_data(target.resolve(strict=True).read_bytes())


def read_trade_good_names(target: Path) -> tuple[str, ...]:
    """Read the 70 EXE-side trade-good names used by city specialties."""
    data = target.resolve(strict=True).read_bytes()
    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        table_offset = pe.get_offset_from_rva(TRADE_GOOD_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase)
        first_name_offset = table_offset + TRADE_GOOD_NAME_RECORD_SHIFT * TRADE_GOOD_RECORD_SIZE
        last_name_end = (
            table_offset
            + (TRADE_GOOD_RECORD_COUNT - 1 + TRADE_GOOD_NAME_RECORD_SHIFT) * TRADE_GOOD_RECORD_SIZE
            + TRADE_GOOD_NAME_POINTER_OFFSET
            + 4
        )
        if first_name_offset < 0 or last_name_end > len(data):
            raise ValueError("교역품 마스터 테이블의 범위를 검증하지 못했습니다.")
        names: list[str] = []
        for identifier in range(TRADE_GOOD_RECORD_COUNT):
            offset = table_offset + (
                identifier + TRADE_GOOD_NAME_RECORD_SHIFT
            ) * TRADE_GOOD_RECORD_SIZE
            name_va = struct.unpack_from("<I", data, offset + TRADE_GOOD_NAME_POINTER_OFFSET)[0]
            try:
                name_offset = pe.get_offset_from_rva(name_va - pe.OPTIONAL_HEADER.ImageBase)
            except pefile.PEFormatError as error:
                raise ValueError(f"교역품 {identifier}번 이름 주소를 검증하지 못했습니다.") from error
            name_end = data.find(b"\0", name_offset, min(name_offset + 96, len(data)))
            if not 0 <= name_offset < len(data) or name_end < 0:
                raise ValueError(f"교역품 {identifier}번 이름 주소를 검증하지 못했습니다.")
            try:
                name = data[name_offset:name_end].decode("cp949")
            except UnicodeDecodeError as error:
                raise ValueError(f"교역품 {identifier}번 이름을 읽지 못했습니다.") from error
            if not name:
                raise ValueError(f"교역품 {identifier}번 이름을 읽지 못했습니다.")
            names.append(name)
        return tuple(names)
    finally:
        pe.close()


def _read_trade_region_goods_from_data(data: bytes) -> tuple[tuple[int, ...], ...]:
    """Read the complete 27×5 trade-region table from EXE bytes."""
    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        offset = pe.get_offset_from_rva(TRADE_REGION_GOODS_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase)
        size = (CITY_TRADE_REGION_MAX + 1) * TRADE_REGION_GOODS_PER_REGION * 4
        if offset < 0 or offset + size > len(data):
            raise ValueError("교역권 공통 품목표의 범위를 검증하지 못했습니다.")
        regions = tuple(
            struct.unpack_from(
                f"<{TRADE_REGION_GOODS_PER_REGION}i", data,
                offset + identifier * TRADE_REGION_GOODS_PER_REGION * 4,
            )
            for identifier in range(CITY_TRADE_REGION_MAX + 1)
        )
        if any(any(not -1 <= good_id <= CITY_SPECIALTY_ID_MAX for good_id in goods) for goods in regions):
            raise ValueError("교역권 공통 품목표의 값을 검증하지 못했습니다.")
        return regions
    finally:
        pe.close()


def read_trade_region_goods(target: Path) -> tuple[tuple[int, ...], ...]:
    """Read the five common trade-good IDs assigned to each trade region."""
    return _read_trade_region_goods_from_data(target.resolve(strict=True).read_bytes())


def apply_trade_region_goods(
    data: bytearray, regions: tuple[tuple[int, ...], ...] | None,
) -> bool:
    """Replace the global 27×5 trade-region goods table in place."""
    if regions is None:
        return False
    if (
        len(regions) != CITY_TRADE_REGION_MAX + 1
        or any(len(goods) != TRADE_REGION_GOODS_PER_REGION for goods in regions)
        or any(
            not -1 <= good_id <= CITY_SPECIALTY_ID_MAX
            for goods in regions for good_id in goods
        )
    ):
        raise ValueError("교역권 품목 입력값을 확인해 주세요.")
    if _read_trade_region_goods_from_data(bytes(data)) == regions:
        return False
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        table_offset = pe.get_offset_from_rva(
            TRADE_REGION_GOODS_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
    finally:
        pe.close()
    for identifier, goods in enumerate(regions):
        struct.pack_into(
            f"<{TRADE_REGION_GOODS_PER_REGION}i", data,
            table_offset + identifier * TRADE_REGION_GOODS_PER_REGION * 4,
            *goods,
        )
    return True


def apply_city_edit(data: bytearray, edit: CityEdit | None) -> bool:
    """Apply one static city edit without changing SAVEDATA.CDS city state."""
    if edit is None:
        return False
    name = edit.name.strip()
    if not name:
        raise ValueError("도시 이름을 입력해 주세요.")
    try:
        name_bytes = name.encode("cp949")
    except UnicodeEncodeError as error:
        raise ValueError("도시 이름은 CP949에서 사용할 수 있는 문자만 입력할 수 있습니다.") from error
    if len(name) > CITY_NAME_MAX_CHARACTERS or len(name_bytes) > CITY_NAME_MAX_BYTES:
        raise ValueError("도시 이름은 최대 8자(16바이트)까지 입력할 수 있습니다.")
    if (
        not 0 <= edit.identifier < CITY_RECORD_COUNT
        or not CITY_WORLD_X_MIN <= edit.world_x <= CITY_WORLD_X_MAX
        or not CITY_WORLD_Y_MIN <= edit.world_y <= CITY_WORLD_Y_MAX
        or len(edit.inland_connection_ids) != 2
        or any(not -1 <= value < CITY_RECORD_COUNT for value in edit.inland_connection_ids)
        or not 0 <= edit.ship_candidate_mask <= CITY_SHIP_CANDIDATE_MASK_MAX
        or not 0 <= edit.trade_region_id <= CITY_TRADE_REGION_MAX
        or not 0 <= edit.culture_id <= CITY_CULTURE_MAX
        or not 0 <= edit.nation_id <= CITY_NATION_MAX
        or not 0 <= edit.shipyard_level <= CITY_SHIPYARD_LEVEL_MAX
        or not 0 <= edit.update_counter <= CITY_UPDATE_COUNTER_MAX
        or not CITY_SPECIALTY_ID_MIN <= edit.specialty_id <= CITY_SPECIALTY_ID_MAX
        or not 0 <= edit.specialty_price <= CITY_SPECIALTY_PRICE_MAX
        or not 0 <= edit.specialty_supply_index <= CITY_SPECIALTY_SUPPLY_INDEX_MAX
        or len(edit.default_market_goods) != CITY_DEFAULT_MARKET_GOODS_COUNT
        or any(
            not CITY_DEFAULT_MARKET_GOOD_MIN <= value <= CITY_DEFAULT_MARKET_GOOD_MAX
            for value in edit.default_market_goods
        )
        or not 0 <= edit.city_status <= CITY_STATUS_MAX
        or not 0 <= edit.facility_flags <= CITY_FLAGS_MAX
        or not 0 <= edit.default_flags <= CITY_FLAGS_MAX
    ):
        raise ValueError("도시 입력값을 확인해 주세요.")
    current = _read_city_records_from_data(bytes(data))[edit.identifier]
    if current == CityRecord(
        edit.identifier, name, edit.world_x, edit.world_y, edit.inland_connection_ids,
        edit.ship_candidate_mask, edit.trade_region_id, edit.culture_id, edit.nation_id,
        edit.shipyard_level, edit.update_counter, edit.specialty_id, edit.specialty_price,
        edit.specialty_supply_index, edit.default_market_goods, edit.city_status,
        edit.facility_flags, edit.default_flags,
    ):
        return False
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        offset = pe.get_offset_from_rva(CITY_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase) + edit.identifier * CITY_RECORD_SIZE
    finally:
        pe.close()
    if current.name != name:
        section, _created = ensure_patch_section(data, PATCH_SECTION_CITY_NAMES_SIZE)
        slot_offset, slot_va = section.slot(
            CITY_NAME_SLOT_OFFSET + edit.identifier * CITY_NAME_SLOT_STRIDE,
            CITY_NAME_SLOT_STRIDE,
        )
        data[slot_offset:slot_offset + CITY_NAME_SLOT_STRIDE] = b"\0" * CITY_NAME_SLOT_STRIDE
        data[slot_offset:slot_offset + len(name_bytes) + 1] = name_bytes + b"\0"
        struct.pack_into("<I", data, offset + CITY_NAME_POINTER_OFFSET, slot_va)
    struct.pack_into("<ii", data, offset + CITY_WORLD_X_OFFSET, edit.world_x, edit.world_y)
    struct.pack_into("<ii", data, offset + CITY_INLAND_CONNECTIONS_OFFSET, *edit.inland_connection_ids)
    struct.pack_into("<I", data, offset + CITY_SHIP_CANDIDATE_MASK_OFFSET, edit.ship_candidate_mask)
    struct.pack_into("<i", data, offset + CITY_TRADE_REGION_OFFSET, edit.trade_region_id)
    struct.pack_into("<i", data, offset + CITY_CULTURE_OFFSET, edit.culture_id)
    struct.pack_into("<i", data, offset + CITY_NATION_OFFSET, edit.nation_id)
    struct.pack_into("<i", data, offset + CITY_SHIPYARD_LEVEL_OFFSET, edit.shipyard_level)
    struct.pack_into("<i", data, offset + CITY_UPDATE_COUNTER_OFFSET, edit.update_counter)
    struct.pack_into("<i", data, offset + CITY_SPECIALTY_ID_OFFSET, edit.specialty_id)
    struct.pack_into("<i", data, offset + CITY_SPECIALTY_PRICE_OFFSET, edit.specialty_price)
    struct.pack_into("<i", data, offset + CITY_SPECIALTY_SUPPLY_INDEX_OFFSET, edit.specialty_supply_index)
    struct.pack_into(
        f"<{CITY_DEFAULT_MARKET_GOODS_COUNT}i", data,
        offset + CITY_DEFAULT_MARKET_GOODS_OFFSET, *edit.default_market_goods,
    )
    struct.pack_into("<i", data, offset + CITY_STATUS_OFFSET, edit.city_status)
    struct.pack_into(
        "<I", data, offset + CITY_PACKED_FLAGS_OFFSET,
        edit.facility_flags | (edit.default_flags << 16),
    )
    return True


def _read_item_records_from_data(data: bytes) -> tuple[ItemRecord, ...]:
    """Read the fixed-width 286-row item table from a supported EXE."""
    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        table_offset = pe.get_offset_from_rva(ITEM_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase)
        table_size = ITEM_RECORD_COUNT * ITEM_RECORD_SIZE
        if table_offset < 0 or table_offset + table_size > len(data):
            raise ValueError("아이템 마스터 테이블의 범위를 검증하지 못했습니다.")
        records: list[ItemRecord] = []
        for identifier in range(ITEM_RECORD_COUNT):
            offset = table_offset + identifier * ITEM_RECORD_SIZE
            name_va = struct.unpack_from("<I", data, offset + ITEM_NAME_POINTER_OFFSET)[0]
            try:
                name_offset = pe.get_offset_from_rva(name_va - pe.OPTIONAL_HEADER.ImageBase)
            except pefile.PEFormatError as error:
                raise ValueError(f"아이템 {identifier}번 이름 주소를 검증하지 못했습니다.") from error
            name_end = data.find(b"\0", name_offset, min(name_offset + 96, len(data)))
            if not 0 <= name_offset < len(data) or name_end < 0:
                raise ValueError(f"아이템 {identifier}번 이름 주소를 검증하지 못했습니다.")
            try:
                name = data[name_offset:name_end].decode("cp949")
            except UnicodeDecodeError as error:
                raise ValueError(f"아이템 {identifier}번 이름을 읽지 못했습니다.") from error
            image_raw, buy_price, sell_price, effect_value, category_id = struct.unpack_from(
                "<IIIII", data, offset + ITEM_IMAGE_ID_OFFSET,
            )
            if (
                not name
                or not 0 <= buy_price <= ITEM_PRICE_MAX
                or not 0 <= sell_price <= ITEM_PRICE_MAX
                or not 0 <= effect_value <= ITEM_EFFECT_MAX
                or not 0 <= category_id <= ITEM_CATEGORY_MAX
            ):
                raise ValueError(f"아이템 {identifier}번 마스터 값을 검증하지 못했습니다.")
            records.append(ItemRecord(
                identifier, name,
                None if image_raw == ITEM_NO_IMAGE_ID else image_raw,
                buy_price, sell_price, effect_value, category_id,
            ))
        return tuple(records)
    finally:
        pe.close()


def read_item_records(target: Path) -> tuple[ItemRecord, ...]:
    """Read EXE item definitions without opening or changing save data."""
    return _read_item_records_from_data(target.resolve(strict=True).read_bytes())


def apply_item_edit(data: bytearray, edit: ItemEdit | None) -> bool:
    """Apply one item-master edit without changing inventories in save data."""
    if edit is None:
        return False
    name = edit.name.strip()
    if not name:
        raise ValueError("아이템 이름을 입력해 주세요.")
    try:
        name_bytes = name.encode("cp949")
    except UnicodeEncodeError as error:
        raise ValueError("아이템 이름은 CP949에서 사용할 수 있는 문자만 입력할 수 있습니다.") from error
    if len(name) > ITEM_NAME_MAX_CHARACTERS or len(name_bytes) > ITEM_NAME_MAX_BYTES:
        raise ValueError("아이템 이름은 최대 11자(21바이트)까지 입력할 수 있습니다.")
    if (
        not 0 <= edit.identifier < ITEM_RECORD_COUNT
        or not 0 <= edit.buy_price <= ITEM_PRICE_MAX
        or not 0 <= edit.sell_price <= ITEM_PRICE_MAX
        or not 0 <= edit.effect_value <= ITEM_EFFECT_MAX
        or not 0 <= edit.category_id <= ITEM_CATEGORY_MAX
    ):
        raise ValueError("아이템 입력값을 확인해 주세요.")
    current = _read_item_records_from_data(bytes(data))[edit.identifier]
    if (
        current.name, current.buy_price, current.sell_price,
        current.effect_value, current.category_id,
    ) == (
        name, edit.buy_price, edit.sell_price,
        edit.effect_value, edit.category_id,
    ):
        return False
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        offset = pe.get_offset_from_rva(ITEM_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase) + edit.identifier * ITEM_RECORD_SIZE
    finally:
        pe.close()
    if current.name != name:
        section, _created = ensure_patch_section(data, PATCH_SECTION_ITEM_NAMES_SIZE)
        slot_offset, slot_va = section.slot(
            ITEM_NAME_SLOT_OFFSET + edit.identifier * ITEM_NAME_SLOT_STRIDE,
            ITEM_NAME_SLOT_STRIDE,
        )
        data[slot_offset:slot_offset + ITEM_NAME_SLOT_STRIDE] = b"\0" * ITEM_NAME_SLOT_STRIDE
        data[slot_offset:slot_offset + len(name_bytes) + 1] = name_bytes + b"\0"
        struct.pack_into("<I", data, offset + ITEM_NAME_POINTER_OFFSET, slot_va)
    struct.pack_into("<I", data, offset + ITEM_BUY_PRICE_OFFSET, edit.buy_price)
    struct.pack_into("<I", data, offset + ITEM_SELL_PRICE_OFFSET, edit.sell_price)
    struct.pack_into("<I", data, offset + ITEM_EFFECT_VALUE_OFFSET, edit.effect_value)
    struct.pack_into("<I", data, offset + ITEM_CATEGORY_OFFSET, edit.category_id)
    return True


def _discovery_metadata_offset(pe: pefile.PE, identifier: int) -> int:
    """Return the metadata row offset for an editor discovery number."""
    if not 0 <= identifier < DISCOVERY_RECORD_COUNT:
        raise ValueError("발견물 번호가 올바르지 않습니다.")
    if identifier < DISCOVERY_METADATA_TABLE_RECORD_COUNT:
        return pe.get_offset_from_rva(
            DISCOVERY_METADATA_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        ) + identifier * DISCOVERY_RECORD_SIZE
    return pe.get_offset_from_rva(
        DISCOVERY_FINAL_METADATA_RECORD_VA - pe.OPTIONAL_HEADER.ImageBase
    )


def _discovery_coordinate_offset(pe: pefile.PE, identifier: int) -> int:
    """Return the map-rectangle overlay offset for an editor discovery number."""
    if not 0 <= identifier < DISCOVERY_RECORD_COUNT:
        raise ValueError("발견물 번호가 올바르지 않습니다.")
    return pe.get_offset_from_rva(
        DISCOVERY_COORDINATE_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
    ) + identifier * DISCOVERY_RECORD_SIZE


def _read_discovery_records_from_data(data: bytes) -> tuple[DiscoveryRecord, ...]:
    """Read discovery metadata and its separately overlaid map rectangles.

    In particular, the rectangle for discovery number 0 begins at VA
    ``0x51C584``.  ``+0x0C`` in that overlay is its maximum Y coordinate,
    never a DSTILL/media slot.
    """
    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        metadata_table_offset = pe.get_offset_from_rva(
            DISCOVERY_METADATA_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
        metadata_table_size = DISCOVERY_METADATA_TABLE_RECORD_COUNT * DISCOVERY_RECORD_SIZE
        if metadata_table_offset < 0 or metadata_table_offset + metadata_table_size > len(data):
            raise ValueError("발견물 마스터 테이블의 범위를 검증하지 못했습니다.")
        final_metadata_offset = pe.get_offset_from_rva(
            DISCOVERY_FINAL_METADATA_RECORD_VA - pe.OPTIONAL_HEADER.ImageBase
        )
        if final_metadata_offset < 0 or final_metadata_offset + DISCOVERY_RECORD_SIZE > len(data):
            raise ValueError("마지막 발견물 마스터 레코드의 범위를 검증하지 못했습니다.")
        coordinate_table_offset = pe.get_offset_from_rva(
            DISCOVERY_COORDINATE_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
        coordinate_table_size = DISCOVERY_RECORD_COUNT * DISCOVERY_RECORD_SIZE
        if coordinate_table_offset < 0 or coordinate_table_offset + coordinate_table_size > len(data):
            raise ValueError("발견물 좌표 테이블의 범위를 검증하지 못했습니다.")
        records: list[DiscoveryRecord] = []
        for identifier in range(DISCOVERY_RECORD_COUNT):
            metadata_offset = (
                metadata_table_offset + identifier * DISCOVERY_RECORD_SIZE
                if identifier < DISCOVERY_METADATA_TABLE_RECORD_COUNT else final_metadata_offset
            )
            coordinate_offset = coordinate_table_offset + identifier * DISCOVERY_RECORD_SIZE
            name_va = struct.unpack_from("<I", data, metadata_offset + DISCOVERY_NAME_POINTER_OFFSET)[0]
            try:
                name_offset = pe.get_offset_from_rva(name_va - pe.OPTIONAL_HEADER.ImageBase)
            except pefile.PEFormatError as error:
                raise ValueError(f"발견물 {identifier}번 이름 주소를 검증하지 못했습니다.") from error
            name_end = data.find(b"\0", name_offset, min(name_offset + 80, len(data)))
            if not 0 <= name_offset < len(data) or name_end < 0:
                raise ValueError(f"발견물 {identifier}번 이름 주소를 검증하지 못했습니다.")
            try:
                name = data[name_offset:name_end].decode("cp949")
            except UnicodeDecodeError as error:
                raise ValueError(f"발견물 {identifier}번 이름을 읽지 못했습니다.") from error
            raw_coordinates = struct.unpack_from("<iiii", data, coordinate_offset)
            coordinates: tuple[int | None, int | None, int | None, int | None] = (
                (None, None, None, None)
                if raw_coordinates == (-1, -1, -1, -1) else raw_coordinates
            )
            min_x, min_y, max_x, max_y = coordinates
            category_id = struct.unpack_from("<I", data, metadata_offset + DISCOVERY_CATEGORY_OFFSET)[0]
            game_id = struct.unpack_from("<I", data, metadata_offset + DISCOVERY_GAME_ID_OFFSET)[0]
            value = struct.unpack_from("<I", data, metadata_offset + DISCOVERY_VALUE_OFFSET)[0]
            media_record_offset = metadata_offset + DISCOVERY_MEDIA_RECORD_RELATIVE_OFFSET
            still_value, avi_value, animation_value = struct.unpack_from(
                "<III", data, media_record_offset + DISCOVERY_MEDIA_STILL_OFFSET,
            )
            still_slot = None if still_value == NO_DISCOVERY_MEDIA else still_value
            avi_id = None if avi_value == NO_DISCOVERY_MEDIA else avi_value
            animation_part = (
                None if animation_value == NO_DISCOVERY_MEDIA else animation_value
            )
            if (
                not name
                or not 0 <= category_id <= DISCOVERY_CATEGORY_MAX
                or not 0 <= game_id <= 0xFFFF
                or not 0 <= value <= DISCOVERY_VALUE_MAX
                or (
                    min_x is not None and (
                        not DISCOVERY_X_MIN <= min_x <= max_x <= DISCOVERY_X_MAX
                        or not DISCOVERY_Y_MIN <= min_y <= max_y <= DISCOVERY_Y_MAX
                    )
                )
            ):
                raise ValueError(f"발견물 {identifier}번 마스터 값을 검증하지 못했습니다.")
            records.append(DiscoveryRecord(
                identifier, name, category_id, game_id, value, min_x, min_y, max_x, max_y,
                still_slot, avi_id, animation_part,
            ))
        return tuple(records)
    finally:
        pe.close()


def read_discovery_records(target: Path) -> tuple[DiscoveryRecord, ...]:
    """Read EXE discovery definitions; no SAVEDATA.CDS is opened or changed."""
    return _read_discovery_records_from_data(target.resolve(strict=True).read_bytes())


def apply_discovery_edit(data: bytearray, edit: DiscoveryEdit | None) -> bool:
    """Update one ordinary discovery definition without touching save progress."""
    if edit is None:
        return False
    name = edit.name.strip()
    if not name:
        raise ValueError("발견물 이름을 입력해 주세요.")
    try:
        name_bytes = name.encode("cp949")
    except UnicodeEncodeError as error:
        raise ValueError("발견물 이름은 CP949에서 사용할 수 있는 문자만 입력할 수 있습니다.") from error
    if len(name_bytes) > DISCOVERY_NAME_MAX_BYTES:
        raise ValueError("발견물 이름은 최대 31바이트까지 입력할 수 있습니다.")
    coordinates = (edit.min_x, edit.min_y, edit.max_x, edit.max_y)
    if (
        not 0 <= edit.identifier < DISCOVERY_RECORD_COUNT
        or not 0 <= edit.category_id <= DISCOVERY_CATEGORY_MAX
        or not 0 <= edit.value <= DISCOVERY_VALUE_MAX
        or (any(value is None for value in coordinates) and any(value is not None for value in coordinates))
        or (
            all(value is not None for value in coordinates) and (
                not DISCOVERY_X_MIN <= edit.min_x <= edit.max_x <= DISCOVERY_X_MAX
                or not DISCOVERY_Y_MIN <= edit.min_y <= edit.max_y <= DISCOVERY_Y_MAX
            )
        )
    ):
        raise ValueError("발견물 입력값을 확인해 주세요.")
    current = _read_discovery_records_from_data(bytes(data))[edit.identifier]
    if (
        current.name, current.category_id, current.value, current.min_x, current.min_y,
        current.max_x, current.max_y, current.still_slot, current.avi_id, current.animation_part,
    ) == (
        name, edit.category_id, edit.value, edit.min_x, edit.min_y, edit.max_x, edit.max_y,
        edit.still_slot, edit.avi_id, edit.animation_part,
    ):
        return False
    if edit.still_slot is not None and not 0 <= edit.still_slot < NO_DISCOVERY_MEDIA:
        raise ValueError("발견물 이미지 번호가 올바르지 않습니다.")
    if current.still_slot is None and edit.still_slot is not None:
        raise ValueError("이 발견물의 원본 이미지 레코드는 확인되지 않아 이미지 번호를 변경할 수 없습니다.")
    if current.still_slot is not None and edit.still_slot is None:
        raise ValueError("발견물 이미지 번호는 비워 둘 수 없습니다.")
    if current.avi_id is None and edit.avi_id is not None:
        raise ValueError("이 발견물은 AVI 미디어를 사용하지 않습니다.")
    if current.avi_id is not None and (edit.avi_id is None or not 0 <= edit.avi_id <= 69):
        raise ValueError("발견물 AVI 번호는 0~69 사이여야 합니다.")
    if current.animation_part is None and edit.animation_part is not None:
        raise ValueError("이 발견물은 DISCOVER 애니메이션을 사용하지 않습니다.")
    if current.animation_part is not None and (
        edit.animation_part is None or not 0 <= edit.animation_part < DISCOVER_ANIMATION_PART_COUNT
    ):
        raise ValueError("발견물 DISCOVER 파트 번호는 0~28 사이여야 합니다.")
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        metadata_offset = _discovery_metadata_offset(pe, edit.identifier)
        coordinate_offset = _discovery_coordinate_offset(pe, edit.identifier)
    finally:
        pe.close()
    media_record_offset = metadata_offset + DISCOVERY_MEDIA_RECORD_RELATIVE_OFFSET
    image_record_offset = (
        media_record_offset
        if current.still_slot is not None and edit.still_slot != current.still_slot
        else None
    )
    if current.name != name:
        section, _created = ensure_patch_section(data, PATCH_SECTION_DISCOVERY_NAMES_SIZE)
        slot_offset, slot_va = section.slot(
            DISCOVERY_NAME_SLOT_OFFSET + edit.identifier * DISCOVERY_NAME_SLOT_STRIDE,
            DISCOVERY_NAME_SLOT_STRIDE,
        )
        data[slot_offset:slot_offset + DISCOVERY_NAME_SLOT_STRIDE] = b"\0" * DISCOVERY_NAME_SLOT_STRIDE
        data[slot_offset:slot_offset + len(name_bytes) + 1] = name_bytes + b"\0"
        struct.pack_into("<I", data, metadata_offset + DISCOVERY_NAME_POINTER_OFFSET, slot_va)
    if edit.min_x is not None:
        struct.pack_into("<iiii", data, coordinate_offset, edit.min_x, edit.min_y, edit.max_x, edit.max_y)
    struct.pack_into("<I", data, metadata_offset + DISCOVERY_CATEGORY_OFFSET, edit.category_id)
    struct.pack_into("<I", data, metadata_offset + DISCOVERY_VALUE_OFFSET, edit.value)
    if image_record_offset is not None:
        struct.pack_into("<I", data, image_record_offset + DISCOVERY_MEDIA_STILL_OFFSET, edit.still_slot)
    if current.avi_id is not None and edit.avi_id != current.avi_id:
        struct.pack_into("<I", data, media_record_offset + DISCOVERY_MEDIA_AVI_OFFSET, edit.avi_id)
    if current.animation_part is not None and edit.animation_part != current.animation_part:
        struct.pack_into(
            "<I", data, media_record_offset + DISCOVERY_MEDIA_ANIMATION_OFFSET, edit.animation_part,
        )
    return True


def _read_sponsor_records_from_data(data: bytes) -> tuple[SponsorRecord, ...]:
    """Read and validate the fixed-width static sponsor master table."""
    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        table_offset = pe.get_offset_from_rva(SPONSOR_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase)
        table_size = SPONSOR_RECORD_COUNT * SPONSOR_RECORD_SIZE
        if table_offset < 0 or table_offset + table_size > len(data):
            raise ValueError("후원자 마스터 테이블의 범위를 검증하지 못했습니다.")
        records: list[SponsorRecord] = []
        for identifier in range(SPONSOR_RECORD_COUNT):
            record_offset = table_offset + identifier * SPONSOR_RECORD_SIZE
            name_va = struct.unpack_from("<I", data, record_offset + SPONSOR_NAME_POINTER_OFFSET)[0]
            if name_va == 0:
                raise ValueError(f"후원자 {identifier}번 이름 주소를 검증하지 못했습니다.")
            try:
                name_offset = pe.get_offset_from_rva(name_va - pe.OPTIONAL_HEADER.ImageBase)
            except pefile.PEFormatError as error:
                raise ValueError(f"후원자 {identifier}번 이름 주소를 검증하지 못했습니다.") from error
            if not 0 <= name_offset < len(data):
                raise ValueError(f"후원자 {identifier}번 이름 주소를 검증하지 못했습니다.")
            name_end = data.find(b"\0", name_offset, min(name_offset + 64, len(data)))
            if name_end < 0:
                raise ValueError(f"후원자 {identifier}번 이름의 끝을 찾지 못했습니다.")
            try:
                name = data[name_offset:name_end].decode("cp949")
            except UnicodeDecodeError as error:
                raise ValueError(f"후원자 {identifier}번 이름을 읽지 못했습니다.") from error
            face_code = struct.unpack_from("<I", data, record_offset + SPONSOR_FACE_CODE_OFFSET)[0]
            gender = struct.unpack_from("<I", data, record_offset + SPONSOR_GENDER_OFFSET)[0]
            nation_id = struct.unpack_from("<i", data, record_offset + SPONSOR_NATION_ID_OFFSET)[0]
            job_id = struct.unpack_from("<i", data, record_offset + SPONSOR_JOB_ID_OFFSET)[0]
            appearance_year = SPONSOR_APPEARANCE_YEAR_REFERENCE + struct.unpack_from(
                "<i", data, record_offset + SPONSOR_APPEARANCE_YEAR_OFFSET
            )[0]
            power = struct.unpack_from("<I", data, record_offset + SPONSOR_POWER_OFFSET)[0]
            city_id = struct.unpack_from("<i", data, record_offset + SPONSOR_CITY_ID_OFFSET)[0]
            building_id = struct.unpack_from("<i", data, record_offset + SPONSOR_BUILDING_ID_OFFSET)[0]
            wealth_factor = struct.unpack_from("<I", data, record_offset + SPONSOR_WEALTH_FACTOR_OFFSET)[0]
            appraisal = struct.unpack_from("<I", data, record_offset + SPONSOR_APPRAISAL_OFFSET)[0]
            packed_flags = struct.unpack_from("<I", data, record_offset + SPONSOR_FLAGS_OFFSET)[0]
            preference_flags = packed_flags & SPONSOR_PREFERENCE_MASK
            language_flags = (packed_flags >> 16) & SPONSOR_LANGUAGE_MASK
            if not name or not 0 <= face_code <= SPONSOR_FACE_CODE_MAX:
                raise ValueError(f"후원자 {identifier}번 얼굴 코드 값을 검증하지 못했습니다.")
            if gender not in (0, 1):
                raise ValueError(f"후원자 {identifier}번 성별 값을 검증하지 못했습니다.")
            if not 0 <= nation_id <= SPONSOR_NATION_ID_MAX:
                raise ValueError(f"후원자 {identifier}번 국가 값을 검증하지 못했습니다.")
            if not SPONSOR_JOB_ID_MIN <= job_id <= SPONSOR_JOB_ID_MAX:
                raise ValueError(f"후원자 {identifier}번 직업 값을 검증하지 못했습니다.")
            if not SPONSOR_APPEARANCE_YEAR_MIN <= appearance_year <= SPONSOR_APPEARANCE_YEAR_MAX:
                raise ValueError(f"후원자 {identifier}번 등장 연도 값을 검증하지 못했습니다.")
            if not SPONSOR_COEFFICIENT_MIN <= power <= SPONSOR_COEFFICIENT_MAX:
                raise ValueError(f"후원자 {identifier}번 권력 값을 검증하지 못했습니다.")
            if not 0 <= city_id <= SPONSOR_CITY_ID_MAX:
                raise ValueError(f"후원자 {identifier}번 소재 도시 값을 검증하지 못했습니다.")
            if not 0 <= building_id <= SPONSOR_BUILDING_ID_MAX:
                raise ValueError(f"후원자 {identifier}번 소재 시설 값을 검증하지 못했습니다.")
            if not SPONSOR_COEFFICIENT_MIN <= wealth_factor <= SPONSOR_COEFFICIENT_MAX:
                raise ValueError(f"후원자 {identifier}번 재산 계수 값을 검증하지 못했습니다.")
            if not SPONSOR_COEFFICIENT_MIN <= appraisal <= SPONSOR_COEFFICIENT_MAX:
                raise ValueError(f"후원자 {identifier}번 계약금 평가 값을 검증하지 못했습니다.")
            records.append(SponsorRecord(
                identifier, name, face_code, gender, nation_id, job_id, appearance_year,
                power, city_id, building_id, wealth_factor, appraisal, preference_flags, language_flags,
            ))
        return tuple(records)
    finally:
        pe.close()


def read_sponsor_records(target: Path) -> tuple[SponsorRecord, ...]:
    """Read selectable sponsor entries from a supported executable."""
    target = target.resolve(strict=True)
    return _read_sponsor_records_from_data(target.read_bytes())


def apply_sponsor_edit(data: bytearray, edit: SponsorEdit | None) -> bool:
    """Write one selected sponsor's supported static master settings."""
    if edit is None:
        return False
    if not 0 <= edit.identifier < SPONSOR_RECORD_COUNT:
        raise ValueError("수정할 후원자 번호가 올바르지 않습니다.")
    if not 0 <= edit.face_code <= SPONSOR_FACE_CODE_MAX:
        raise ValueError("후원자 얼굴 코드는 0~412 사이여야 합니다.")
    if edit.gender not in (0, 1):
        raise ValueError("후원자 성별이 올바르지 않습니다.")
    if not 0 <= edit.nation_id <= SPONSOR_NATION_ID_MAX:
        raise ValueError("후원자 국가는 목록에서 선택해 주세요.")
    if not SPONSOR_JOB_ID_MIN <= edit.job_id <= SPONSOR_JOB_ID_MAX:
        raise ValueError("후원자 직업은 목록에서 선택해 주세요.")
    if not SPONSOR_APPEARANCE_YEAR_MIN <= edit.appearance_year <= SPONSOR_APPEARANCE_YEAR_MAX:
        raise ValueError("후원자 등장 연도는 1480~1600 사이여야 합니다.")
    if any(
        not SPONSOR_COEFFICIENT_MIN <= value <= SPONSOR_COEFFICIENT_MAX
        for value in (edit.power, edit.wealth_factor, edit.appraisal)
    ):
        raise ValueError("후원자 권력·재산·계약금 평가는 0~99 사이여야 합니다.")
    if not 0 <= edit.city_id <= SPONSOR_CITY_ID_MAX:
        raise ValueError("후원자 소재 도시는 목록에서 선택해 주세요.")
    if not 0 <= edit.building_id <= SPONSOR_BUILDING_ID_MAX:
        raise ValueError("후원자 소재 시설은 목록에서 선택해 주세요.")
    if not 0 <= edit.preference_flags <= SPONSOR_PREFERENCE_MASK:
        raise ValueError("후원자 취향 값이 올바르지 않습니다.")
    if not 0 <= edit.language_flags <= SPONSOR_LANGUAGE_MASK:
        raise ValueError("후원자 언어 값이 올바르지 않습니다.")

    records = _read_sponsor_records_from_data(bytes(data))
    current = records[edit.identifier]
    if (
        current.face_code == edit.face_code
        and current.gender == edit.gender
        and current.nation_id == edit.nation_id
        and current.job_id == edit.job_id
        and current.appearance_year == edit.appearance_year
        and current.power == edit.power
        and current.city_id == edit.city_id
        and current.building_id == edit.building_id
        and current.wealth_factor == edit.wealth_factor
        and current.appraisal == edit.appraisal
        and current.preference_flags == edit.preference_flags
        and current.language_flags == edit.language_flags
    ):
        return False
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        table_offset = pe.get_offset_from_rva(SPONSOR_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase)
        record_offset = table_offset + edit.identifier * SPONSOR_RECORD_SIZE
        current_flags = struct.unpack_from("<I", data, record_offset + SPONSOR_FLAGS_OFFSET)[0]
        flags = (
            (current_flags & ~SPONSOR_EDITABLE_FLAGS_MASK)
            | edit.preference_flags
            | (edit.language_flags << 16)
        )
        struct.pack_into("<I", data, record_offset + SPONSOR_FACE_CODE_OFFSET, edit.face_code)
        struct.pack_into("<I", data, record_offset + SPONSOR_GENDER_OFFSET, edit.gender)
        struct.pack_into("<i", data, record_offset + SPONSOR_NATION_ID_OFFSET, edit.nation_id)
        struct.pack_into("<i", data, record_offset + SPONSOR_JOB_ID_OFFSET, edit.job_id)
        struct.pack_into(
            "<i", data, record_offset + SPONSOR_APPEARANCE_YEAR_OFFSET,
            edit.appearance_year - SPONSOR_APPEARANCE_YEAR_REFERENCE,
        )
        struct.pack_into("<I", data, record_offset + SPONSOR_POWER_OFFSET, edit.power)
        struct.pack_into("<i", data, record_offset + SPONSOR_CITY_ID_OFFSET, edit.city_id)
        struct.pack_into("<i", data, record_offset + SPONSOR_BUILDING_ID_OFFSET, edit.building_id)
        struct.pack_into("<I", data, record_offset + SPONSOR_WEALTH_FACTOR_OFFSET, edit.wealth_factor)
        struct.pack_into("<I", data, record_offset + SPONSOR_APPRAISAL_OFFSET, edit.appraisal)
        struct.pack_into("<I", data, record_offset + SPONSOR_FLAGS_OFFSET, flags)
        return True
    finally:
        pe.close()


def _read_coordinate_style(data: bytes) -> str:
    if KOREAN_DIRECTION_3_FORMAT in data:
        return "korean3"
    if DECIMAL_FORMAT in data:
        return "korean2"
    return "original"


def _figurehead_effect_values(settings: FigureheadEffectSettings) -> tuple[int, ...]:
    return (
        settings.disaster_grade1_chance,
        settings.disaster_grade2_chance,
        settings.disaster_grade3_chance,
        settings.cannon_damage_reduction,
        settings.shooting_damage_reduction,
        settings.melee_damage_reduction,
        settings.cannon_attack_percent,
        settings.shooting_attack_percent,
        settings.melee_attack_percent,
        settings.hull_recovery,
        settings.movement_bonus,
        settings.movement_maximum,
        settings.special_cannon_attack_percent,
        settings.all_attack_percent,
    )


def _validate_figurehead_effect_settings(settings: FigureheadEffectSettings) -> None:
    chances = _figurehead_effect_values(settings)[:6]
    if any(not 0 <= value <= 100 for value in chances):
        raise ValueError("선수상 재해 방지율과 피해 감소율은 0~100% 사이여야 합니다.")
    attack_values = (
        settings.cannon_attack_percent,
        settings.shooting_attack_percent,
        settings.melee_attack_percent,
        settings.special_cannon_attack_percent,
        settings.all_attack_percent,
    )
    if any(not 0 <= value <= 1000 for value in attack_values):
        raise ValueError("선수상 공격 배율은 0~1,000% 사이여야 합니다.")
    if not 0 <= settings.hull_recovery <= 9999:
        raise ValueError("선수상 내구 회복량은 0~9,999 사이여야 합니다.")
    if not 0 <= settings.movement_bonus <= 127:
        raise ValueError("선수상 이동 보너스는 0~127 사이여야 합니다.")
    if not 1 <= settings.movement_maximum <= 127:
        raise ValueError("선수상 이동 최대값은 1~127 사이여야 합니다.")


def _rebase_figurehead_stub(
    template: bytes,
    template_relative_offset: int,
    relocation_targets: tuple[tuple[int, int], ...],
    slot_va: int,
) -> bytes:
    """Rebase one preassembled x86 stub to the selected EXE's .patch VA."""
    code = bytearray(template)
    expected_stub_va = FIGUREHEAD_EFFECT_EXPECTED_SLOT_VA + template_relative_offset
    actual_stub_va = slot_va + template_relative_offset
    for operand_offset, target_va in relocation_targets:
        struct.pack_into(
            "<i", code, operand_offset,
            target_va - (actual_stub_va + operand_offset + 4),
        )
    for config_relative_offset in range(
        FIGUREHEAD_EFFECT_CONFIG_OFFSET,
        FIGUREHEAD_EFFECT_CONFIG_OFFSET + FIGUREHEAD_EFFECT_CONFIG_COUNT * 4,
        4,
    ):
        expected_address = struct.pack(
            "<I", FIGUREHEAD_EFFECT_EXPECTED_SLOT_VA + config_relative_offset,
        )
        actual_address = struct.pack("<I", slot_va + config_relative_offset)
        start = 0
        while True:
            index = code.find(expected_address, start)
            if index < 0:
                break
            code[index:index + 4] = actual_address
            start = index + 4
    # The old base is intentionally only used to document the relocation
    # origin and catch a malformed template during maintenance.
    if expected_stub_va <= 0:
        raise AssertionError("선수상 패치 코드 기준 주소가 올바르지 않습니다.")
    return bytes(code)


def _build_figurehead_effect_payload(
    slot_va: int,
    settings: FigureheadEffectSettings,
) -> bytes:
    _validate_figurehead_effect_settings(settings)
    payload = bytearray(FIGUREHEAD_EFFECT_SLOT_SIZE)
    payload[:len(FIGUREHEAD_EFFECT_MAGIC)] = FIGUREHEAD_EFFECT_MAGIC
    struct.pack_into("<I", payload, 8, FIGUREHEAD_EFFECT_VERSION)
    struct.pack_into(
        f"<{FIGUREHEAD_EFFECT_CONFIG_COUNT}I", payload,
        FIGUREHEAD_EFFECT_CONFIG_OFFSET, *_figurehead_effect_values(settings),
    )
    for relative_offset, template, relocations in FIGUREHEAD_EFFECT_STUB_LAYOUT.values():
        code = _rebase_figurehead_stub(
            template, relative_offset, relocations, slot_va,
        )
        end = relative_offset + len(code)
        if end > len(payload):
            raise ValueError("선수상 패치 코드가 예약 공간을 벗어납니다.")
        payload[relative_offset:end] = code
    return bytes(payload)


def _figurehead_hook_patch(
    hook_va: int,
    size: int,
    target_va: int,
    use_call: bool,
) -> bytes:
    if size < 5:
        raise ValueError("선수상 패치 지점의 길이가 부족합니다.")
    opcode = 0xE8 if use_call else 0xE9
    return bytes((opcode,)) + struct.pack("<i", target_va - (hook_va + 5)) + b"\x90" * (size - 5)


def _read_figurehead_effect_settings_from_data(data: bytes) -> FigureheadEffectSettings:
    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        patch_section = find_patch_section(data)
        if (
            patch_section is None
            or patch_section.raw_size < FIGUREHEAD_EFFECT_SLOT_OFFSET + FIGUREHEAD_EFFECT_SLOT_SIZE
            or patch_section.virtual_size < FIGUREHEAD_EFFECT_SLOT_OFFSET + FIGUREHEAD_EFFECT_SLOT_SIZE
        ):
            for _name, hook_va, original, _use_call in FIGUREHEAD_EFFECT_HOOKS:
                offset = pe.get_offset_from_rva(hook_va - pe.OPTIONAL_HEADER.ImageBase)
                if data[offset:offset + len(original)] != original:
                    raise ValueError(f"선수상 효과 위치 0x{hook_va:X}을(를) 검증하지 못했습니다.")
            return DEFAULT_FIGUREHEAD_EFFECT_SETTINGS

        slot_offset, slot_va = patch_section.slot(
            FIGUREHEAD_EFFECT_SLOT_OFFSET, FIGUREHEAD_EFFECT_SLOT_SIZE,
        )
        if data[slot_offset:slot_offset + len(FIGUREHEAD_EFFECT_MAGIC)] != FIGUREHEAD_EFFECT_MAGIC:
            if any(data[slot_offset:slot_offset + FIGUREHEAD_EFFECT_SLOT_SIZE]):
                raise ValueError(".patch의 선수상 효과 슬롯이 예상하지 못한 데이터로 사용 중입니다.")
            for _name, hook_va, original, _use_call in FIGUREHEAD_EFFECT_HOOKS:
                offset = pe.get_offset_from_rva(hook_va - pe.OPTIONAL_HEADER.ImageBase)
                if data[offset:offset + len(original)] != original:
                    raise ValueError(f"선수상 효과 위치 0x{hook_va:X}을(를) 검증하지 못했습니다.")
            return DEFAULT_FIGUREHEAD_EFFECT_SETTINGS
        version = struct.unpack_from("<I", data, slot_offset + 8)[0]
        if version != FIGUREHEAD_EFFECT_VERSION:
            raise ValueError("지원하지 않는 선수상 효과 패치 버전입니다.")
        values = struct.unpack_from(
            f"<{FIGUREHEAD_EFFECT_CONFIG_COUNT}I", data,
            slot_offset + FIGUREHEAD_EFFECT_CONFIG_OFFSET,
        )
        settings = FigureheadEffectSettings(*values)
        _validate_figurehead_effect_settings(settings)
        expected_payload = _build_figurehead_effect_payload(slot_va, settings)
        if data[slot_offset:slot_offset + FIGUREHEAD_EFFECT_SLOT_SIZE] != expected_payload:
            raise ValueError("선수상 효과 패치 코드를 검증하지 못했습니다.")
        for name, hook_va, original, use_call in FIGUREHEAD_EFFECT_HOOKS:
            offset = pe.get_offset_from_rva(hook_va - pe.OPTIONAL_HEADER.ImageBase)
            stub_va = slot_va + FIGUREHEAD_EFFECT_STUB_LAYOUT[name][0]
            expected = _figurehead_hook_patch(hook_va, len(original), stub_va, use_call)
            if data[offset:offset + len(original)] != expected:
                raise ValueError(f"선수상 효과 연결 위치 0x{hook_va:X}을(를) 검증하지 못했습니다.")
        return settings
    finally:
        pe.close()


def read_figurehead_effect_settings(target: Path) -> FigureheadEffectSettings:
    """Read editable figurehead strengths from an executable."""
    return _read_figurehead_effect_settings_from_data(target.resolve(strict=True).read_bytes())


def apply_figurehead_effect_settings(
    data: bytearray,
    settings: FigureheadEffectSettings,
) -> bool:
    """Inject verified figurehead-strength code and its editable constants."""
    _validate_figurehead_effect_settings(settings)
    current = _read_figurehead_effect_settings_from_data(bytes(data))
    if current == settings:
        return False
    patch_section, _created = ensure_patch_section(data, PATCH_SECTION_CITY_NAMES_SIZE)
    slot_offset, slot_va = patch_section.slot(
        FIGUREHEAD_EFFECT_SLOT_OFFSET, FIGUREHEAD_EFFECT_SLOT_SIZE,
    )
    existing = bytes(data[slot_offset:slot_offset + FIGUREHEAD_EFFECT_SLOT_SIZE])
    if any(existing) and not existing.startswith(FIGUREHEAD_EFFECT_MAGIC):
        raise ValueError(".patch의 선수상 효과 슬롯이 예상하지 못한 데이터로 사용 중입니다.")
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        for name, hook_va, original, use_call in FIGUREHEAD_EFFECT_HOOKS:
            offset = pe.get_offset_from_rva(hook_va - pe.OPTIONAL_HEADER.ImageBase)
            stub_va = slot_va + FIGUREHEAD_EFFECT_STUB_LAYOUT[name][0]
            patched = _figurehead_hook_patch(hook_va, len(original), stub_va, use_call)
            present = bytes(data[offset:offset + len(original)])
            if present not in (original, patched):
                raise ValueError(f"선수상 효과 위치 0x{hook_va:X}을(를) 검증하지 못했습니다.")
            data[offset:offset + len(original)] = patched
    finally:
        pe.close()
    data[slot_offset:slot_offset + FIGUREHEAD_EFFECT_SLOT_SIZE] = (
        _build_figurehead_effect_payload(slot_va, settings)
    )
    return True


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
        elif (
            wait_code[:6] == NPC_COMPARE_PREFIX
            and -60 <= struct.unpack("b", wait_code[6:7])[0] <= MAX_NPC_ARRIVAL_WAIT_DAYS - 60
        ):
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
    cash_limit: int,
    deposit_limit: int,
    fame_limit: int,
    infamy_limit: int,
    npc_activity_min_age: int,
    npc_activity_max_age: int,
    western_encounter_denominator: int,
    islamic_encounter_denominator: int,
    pirate_variety_enabled: bool,
    pirate_variety_settings: PirateVarietySettings = DEFAULT_PIRATE_VARIETY_SETTINGS,
    eclipse_enabled: bool = False,
    eclipse_latitude: str | int | float | Decimal = Decimal("67"),
    mistranslation_fixes_enabled: bool = False,
    figurehead_effect_settings: FigureheadEffectSettings = DEFAULT_FIGUREHEAD_EFFECT_SETTINGS,
    barmaid_edit: BarmaidEdit | None = None,
    sponsor_edit: SponsorEdit | None = None,
    person_edit: PersonEdit | None = None,
    ship_type_edit: ShipTypeEdit | None = None,
    city_edit: CityEdit | None = None,
    trade_region_goods: tuple[tuple[int, ...], ...] | None = None,
    item_edit: ItemEdit | None = None,
    discovery_edit: DiscoveryEdit | None = None,
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
        cash_limit,
        deposit_limit,
        fame_limit,
        infamy_limit,
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
    apply_figurehead_effect_settings(updated, figurehead_effect_settings)
    apply_barmaid_edit(updated, barmaid_edit)
    apply_sponsor_edit(updated, sponsor_edit)
    apply_person_edit(updated, person_edit)
    apply_ship_type_edit(updated, ship_type_edit)
    apply_city_edit(updated, city_edit)
    apply_trade_region_goods(updated, trade_region_goods)
    apply_item_edit(updated, item_edit)
    apply_discovery_edit(updated, discovery_edit)
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
