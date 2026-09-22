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
    DISCOVERY_DESCRIPTION_SLOT_OFFSET,
    DISCOVERY_DESCRIPTION_SLOT_STRIDE,
    DISCOVERY_NAME_SLOT_OFFSET,
    DISCOVERY_NAME_SLOT_STRIDE,
    ECLIPSE_SLOT_OFFSET,
    ECLIPSE_SLOT_SIZE,
    FIGUREHEAD_EFFECT_SLOT_OFFSET,
    FIGUREHEAD_EFFECT_SLOT_SIZE,
    HINT_TEXT_SLOT_OFFSET,
    HINT_TEXT_SLOT_STRIDE,
    HISTORY_ELAPSED_YEARS_FIX_SLOT_OFFSET,
    HISTORY_ELAPSED_YEARS_FIX_SLOT_SIZE,
    MISTRANSLATION_SLOT_OFFSET,
    MISTRANSLATION_SLOT_SIZE,
    NPC_DAILY_DEPARTURE_SLOT_OFFSET,
    NPC_DAILY_DEPARTURE_SLOT_SIZE,
    PATCH_SECTION_EXPANDED_SIZE,
    PATCH_SECTION_DISCOVERY_NAMES_SIZE,
    PATCH_SECTION_DISCOVERY_DESCRIPTIONS_SIZE,
    PATCH_SECTION_CITY_NAMES_SIZE,
    PATCH_SECTION_ITEM_NAMES_SIZE,
    PATCH_SECTION_MASTER_NAMES_SIZE,
    PATCH_SECTION_HINT_TEXTS_SIZE,
    PATCH_SECTION_HISTORY_ELAPSED_YEARS_FIX_SIZE,
    PATCH_SECTION_JUDGMENT_FIX_SIZE,
    PATCH_SECTION_PLAYER_FAME_LIMIT_SIZE,
    PATCH_SECTION_SHIP_PURCHASE_BLANK_SELECTION_FIX_SIZE,
    PATCH_SECTION_SHIP_REUSE_FIX_SIZE,
    PATCH_SECTION_NPC_DAILY_DEPARTURE_SIZE,
    ITEM_NAME_SLOT_OFFSET,
    ITEM_NAME_SLOT_STRIDE,
    JUDGMENT_FIX_SLOT_OFFSET,
    JUDGMENT_FIX_SLOT_SIZE,
    LIBRARY_BOOK_AUTHOR_SLOT_OFFSET,
    LIBRARY_BOOK_AUTHOR_SLOT_STRIDE,
    LIBRARY_BOOK_TITLE_SLOT_OFFSET,
    LIBRARY_BOOK_TITLE_SLOT_STRIDE,
    LIBRARY_HINT_NAME_SLOT_OFFSET,
    LIBRARY_HINT_NAME_SLOT_STRIDE,
    LIBRARY_HINT_TEXT_SLOT_OFFSET,
    LIBRARY_HINT_TEXT_SLOT_STRIDE,
    PATCH_SECTION_LIBRARY_BOOKS_SIZE,
    PATCH_SECTION_LIBRARY_HINT_NAMES_SIZE,
    PATCH_SECTION_LIBRARY_HINT_TEXTS_SIZE,
    PATCH_SECTION_SAVE_SLOT_SELECTOR_SIZE,
    SAVE_SLOT_SELECTOR_SLOT_OFFSET,
    SAVE_SLOT_SELECTOR_SLOT_SIZE,
    PATCH_SECTION_LOAD_SLOT_SELECTOR_SIZE,
    LOAD_SLOT_SELECTOR_SLOT_OFFSET,
    LOAD_SLOT_SELECTOR_SLOT_SIZE,
    PIRATE_SLOT_OFFSET,
    PIRATE_SLOT_SIZE,
    PLAYER_FAME_LIMIT_SLOT_OFFSET,
    PLAYER_FAME_LIMIT_SLOT_SIZE,
    SHIP_TYPE_NAME_SLOT_OFFSET,
    SHIP_TYPE_NAME_SLOT_STRIDE,
    SHIP_REUSE_FIX_SLOT_OFFSET,
    SHIP_REUSE_FIX_SLOT_SIZE,
    SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_OFFSET,
    SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_SIZE,
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
NPC_DAILY_UPDATE_VA = 0x432740
NPC_MONTHLY_DEPARTURE_VA = 0x4327F0
NPC_CHARACTER_ID_VA = 0x432080
NPC_DAILY_UPDATE_VTABLE_VA = 0x4FB40C
NPC_MONTHLY_DEPARTURE_VTABLE_VA = 0x4FB410
NPC_DAILY_DEPARTURE_MAGIC = b"CDSNPD1\0"
NPC_DAILY_DEPARTURE_VERSION = 1
NPC_DAILY_DEPARTURE_WRAPPER_OFFSET = 0x20
NPC_DAILY_DEPARTURE_MONTHLY_GATE_OFFSET = 0x80

# The Judgment command removes a dead target but, unlike ordinary attacks and
# Assassinate, never normalizes a side whose entire front rank was eliminated.
# Redirect the existing death-cleanup call through a wrapper that performs the
# omitted side lookup and formation normalization before returning.
JUDGMENT_CLEANUP_HOOK_VA = 0x449046
JUDGMENT_DEAD_UNIT_CLEANUP_VA = 0x448510
JUDGMENT_UNIT_SIDE_VA = 0x446200
JUDGMENT_FORMATION_NORMALIZE_VA = 0x4484F0
JUDGMENT_ORIGINAL_HOOK = bytes.fromhex("E8 C5 F4 FF FF")
JUDGMENT_FIX_MAGIC = b"CDSJDG1\0"
JUDGMENT_FIX_VERSION = 1
JUDGMENT_FIX_WRAPPER_OFFSET = 0x20

# The cannon hit-rate formula can become negative with low gunnery and very
# few cannons.  The original uses unsigned JB for its minimum-one clamp, so a
# negative percentage reaches the unsigned random comparison as a huge value
# and succeeds 100% of the time.  Signed JL correctly clamps it to 1%.
CANNON_ACCURACY_CLAMP_BRANCH_VA = 0x436E59
CANNON_ACCURACY_ORIGINAL_BRANCH = bytes.fromhex("72 30")  # JB +30h
CANNON_ACCURACY_FIXED_BRANCH = bytes.fromhex("7C 30")     # JL +30h

# DISEV numeric lookup ID 15 calls the player's generic language getter with
# a hard-coded array index.  The Korean executable passes 10 (African native)
# even though the Monument Valley negotiation needs index 11 (Central/South
# American native).
DISEV_LANGUAGE_LOOKUP_INSTRUCTION_VA = 0x4070CA
DISEV_LANGUAGE_LOOKUP_ORIGINAL = bytes.fromhex("6A 0A")  # push 10
DISEV_LANGUAGE_LOOKUP_FIXED = bytes.fromhex("6A 0B")     # push 11

# HIST_EV condition ``1B 0B [discovery] 16 [years]`` computes the year
# difference backwards.  Redirect only subcondition 16 through a wrapper;
# subcondition 17 and every other 1B path remain byte-for-byte unchanged.
HISTORY_ELAPSED_COMPARE_VA = 0x407709
HISTORY_ELAPSED_COMPARE_ORIGINAL = bytes.fromhex(
    "66 3B F8 1B F6 46 E9 99 07 00 00"
)
HISTORY_ELAPSED_RESULT_VA = 0x407EAD
HISTORY_ELAPSED_DISCOVERY_TABLE_VA = 0x61E4C8
HISTORY_ELAPSED_DISCOVERY_LOOKUP_VA = 0x4AAE40
HISTORY_ELAPSED_YEARS_FIX_MAGIC = b"HISTYR1\0"
HISTORY_ELAPSED_YEARS_FIX_VERSION = 1
HISTORY_ELAPSED_YEARS_FIX_WRAPPER_OFFSET = 0x10

# The original save handler asks for confirmation before it writes.  Replace
# its first instruction so a slot is selected first and overwrite confirmation
# is asked only for an existing destination.
SAVE_SLOT_SELECTOR_HOOK_VA = 0x4A2800
SAVE_SLOT_SELECTOR_HOOK_ORIGINAL = bytes.fromhex("68 B8 8C 56 00")
SAVE_SLOT_SELECTOR_SAVE_ROUTINE_VA = 0x478E80
SAVE_SLOT_SELECTOR_SUCCESS_VA = 0x4A2819
SAVE_SLOT_SELECTOR_CANCEL_VA = 0x4A2828
SAVE_SLOT_SELECTOR_MENU_ROUTINE_VA = 0x469A70
SAVE_SLOT_SELECTOR_CONFIRM_ROUTINE_VA = 0x469060
SAVE_SLOT_SELECTOR_OVERWRITE_PROMPT_VA = 0x568CB8
SAVE_SLOT_SELECTOR_PATH_ROUTINE_VA = 0x425220
SAVE_SLOT_SELECTOR_CREATE_FILE_IAT_VA = 0x62F444
SAVE_SLOT_SELECTOR_READ_FILE_IAT_VA = 0x62F474
SAVE_SLOT_SELECTOR_CLOSE_HANDLE_IAT_VA = 0x62F424
SAVE_SLOT_SELECTOR_WSPRINTF_IAT_VA = 0x62F594
SAVE_SLOT_SELECTOR_SAVE_NAME_POINTER_VA = 0x568778
SAVE_SLOT_SELECTOR_TMP_NAME_POINTER_VA = 0x56877C
SAVE_SLOT_SELECTOR_DEFAULT_SAVE_NAME_VA = 0x537654
SAVE_SLOT_SELECTOR_DEFAULT_TMP_NAME_VA = 0x537644
# Version 2 used the game's create/truncate helper while reading labels.
# Version 3 used direct read-only Win32 calls, but incorrectly created one TMP
# file per slot.  The game has one shared temporary file, SAVEDATA.TMP.
SAVE_SLOT_SELECTOR_LEGACY_MAGIC = b"SAVESLT2"
SAVE_SLOT_SELECTOR_PREVIOUS_MAGIC = b"SAVESLT3"
SAVE_SLOT_SELECTOR_MAGIC = b"SAVESLT4"
SAVE_SLOT_SELECTOR_VERSION = 4
SAVE_SLOT_SELECTOR_WRAPPER_OFFSET = 0x20
# Keep all data after the wrapper.  The direct read-only file calls make the
# wrapper larger than the old 0x180 menu-table position.
SAVE_SLOT_SELECTOR_MENU_ITEMS_OFFSET = 0x1C0
SAVE_SLOT_SELECTOR_SAVE_NAME_POINTERS_OFFSET = 0x250
SAVE_SLOT_SELECTOR_TMP_NAME_POINTERS_OFFSET = 0x280
SAVE_SLOT_SELECTOR_LABELS_OFFSET = 0x300
SAVE_SLOT_SELECTOR_SAVE_NAMES_OFFSET = 0x500
SAVE_SLOT_SELECTOR_TMP_NAMES_OFFSET = 0x640
SAVE_SLOT_SELECTOR_DATE_FORMAT_OFFSET = 0x780
SAVE_SLOT_SELECTOR_SAVE_COUNT = 10
SAVE_SLOT_SELECTOR_MENU_COUNT = 11
SAVE_SLOT_SELECTOR_LABEL_STRIDE = 0x20

# The original loader remains unmodified.  Both game command paths select a
# slot first, then call the original loader.  The title-session initializer is
# redirected separately because it otherwise reopens SAVEDATA.CDS by name.
LOAD_SLOT_SELECTOR_HOOK_VA = 0x478A60
LOAD_SLOT_SELECTOR_HOOK_ORIGINAL = bytes.fromhex("81 EC 08 01 00 00")
LOAD_SLOT_SELECTOR_CONTINUATION_VA = 0x478A66
LOAD_SLOT_SELECTOR_COMMAND_HOOK_VA = 0x4A2830
LOAD_SLOT_SELECTOR_COMMAND_HOOK_ORIGINAL = bytes.fromhex("68 F8 8C 56 00")
LOAD_SLOT_SELECTOR_COMMAND_CONTINUATION_VA = 0x44AF70
# The title screen uses a separate load callback and must remain on its own
# callback path; it does not use the in-game command transition.
LOAD_SLOT_SELECTOR_TITLE_HOOK_VA = 0x45ED1F
LOAD_SLOT_SELECTOR_TITLE_HOOK_ORIGINAL = bytes.fromhex("68 88 1A 57 00")
LOAD_SLOT_SELECTOR_TITLE_SUCCESS_VA = 0x45ED3B
LOAD_SLOT_SELECTOR_TITLE_CANCEL_VA = 0x45ED65
# The title wrapper prepares its state itself, so success must resume at the
# original loader call, not at the instruction after that call.
LOAD_SLOT_SELECTOR_TITLE_LOADER_VA = 0x45ED40
LOAD_SLOT_SELECTOR_TITLE_CONTINUATION_VA = 0x45ED45
LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_VA = 0x41ABE2
LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_ORIGINAL = bytes.fromhex("68 54 76 53 00")
LOAD_SLOT_SELECTOR_SESSION_FILE_CONTINUATION_VA = 0x41AC0E
LOAD_SLOT_SELECTOR_MENU_ROUTINE_VA = 0x469A70
LOAD_SLOT_SELECTOR_PATH_ROUTINE_VA = 0x425220
LOAD_SLOT_SELECTOR_LEGACY_MAGIC = b"LOADSLT1"
LOAD_SLOT_SELECTOR_INTERMEDIATE_MAGIC = b"LOADSLT2"
LOAD_SLOT_SELECTOR_OLDER_MAGIC = b"LOADSLT3"
LOAD_SLOT_SELECTOR_PREVIOUS_MAGIC = b"LOADSLT4"
LOAD_SLOT_SELECTOR_LATEST_LEGACY_MAGIC = b"LOADSLT5"
LOAD_SLOT_SELECTOR_NEWEST_LEGACY_MAGIC = b"LOADSLT6"
LOAD_SLOT_SELECTOR_FINAL_LEGACY_MAGIC = b"LOADSLT7"
LOAD_SLOT_SELECTOR_SESSION_FILE_LEGACY_MAGIC = b"LOADSLT8"
LOAD_SLOT_SELECTOR_TITLE_CONTEXT_LEGACY_MAGIC = b"LOADSLT9"
LOAD_SLOT_SELECTOR_TITLE_ORDER_LEGACY_MAGIC = b"LOADSL10"
LOAD_SLOT_SELECTOR_TITLE_CITY_ORDER_LEGACY_MAGIC = b"LOADSL11"
LOAD_SLOT_SELECTOR_TEMP_SLOT_LEGACY_MAGIC = b"LOADSL12"
# v13 overwrote the post-selection wrapper's ``jmp`` opcode while writing its
# relative operand.  The selector then fell through into empty .patch data as
# soon as a slot was chosen.  v14 writes the operand after the opcode.
LOAD_SLOT_SELECTOR_POST_SELECTION_LEGACY_MAGIC = b"LOADSL13"
# The title path originally selected a slot before game-state preparation,
# unlike the working in-game path.  v15 prepares first, then selects, before
# rejoining the title callback at its original loader call.  v16 corrects the
# second call's instruction-end address (v15 entered five bytes into selector).
LOAD_SLOT_SELECTOR_TITLE_PREPARATION_ORDER_LEGACY_MAGIC = b"LOADSL14"
LOAD_SLOT_SELECTOR_TITLE_SELECTOR_CALL_LEGACY_MAGIC = b"LOADSL15"
# v16 fixes the title selector call target.  v17 also stops storing the
# selected row in an unprotected stack scratch slot across the confirmation
# dialog; use EBP, which that dialog preserves by calling convention.
LOAD_SLOT_SELECTOR_CONFIRM_STACK_INDEX_LEGACY_MAGIC = b"LOADSL16"
# v17 accidentally rejoined after the title loader call.  v18 re-enters at
# the native loader call itself, so the selected data is actually deserialized.
LOAD_SLOT_SELECTOR_TITLE_LOADER_SKIP_LEGACY_MAGIC = b"LOADSL17"
# The game's confirmation dialog clobbers EBP and its caller-stack scratch
# area.  v19 keeps the row on the stack only for the dialog call, then writes
# the resolved selected path directly to the normal save-name pointer.
LOAD_SLOT_SELECTOR_DIALOG_REGISTER_LEGACY_MAGIC = b"LOADSL18"
# v19 still depends on the confirmation dialog's stack layout.  v20 stores
# the chosen row in dedicated .patch data before showing that dialog.
LOAD_SLOT_SELECTOR_DIALOG_STACK_LEGACY_MAGIC = b"LOADSL19"
# v20 ran the in-game load preparation before the slot menu.  That preparation
# changes the active UI state, so cancelling the menu could return to the
# title screen.  v21 selects first and preserves the original order: prepare
# the load only after a slot was actually chosen.
LOAD_SLOT_SELECTOR_COMMAND_PREPARATION_LEGACY_MAGIC = b"LOADSL20"
LOAD_SLOT_SELECTOR_MAGIC = b"LOADSL21"
LOAD_SLOT_SELECTOR_VERSION = 21
LOAD_SLOT_SELECTOR_WRAPPER_OFFSET = 0x20
LOAD_SLOT_SELECTOR_MENU_ITEMS_OFFSET = 0x1C0
LOAD_SLOT_SELECTOR_SAVE_NAME_POINTERS_OFFSET = 0x250
LOAD_SLOT_SELECTOR_TMP_NAME_POINTERS_OFFSET = 0x280
LOAD_SLOT_SELECTOR_LABELS_OFFSET = 0x300
LOAD_SLOT_SELECTOR_SAVE_NAMES_OFFSET = 0x500
LOAD_SLOT_SELECTOR_TMP_NAMES_OFFSET = 0x640
LOAD_SLOT_SELECTOR_DATE_FORMAT_OFFSET = 0x780
LOAD_SLOT_SELECTOR_SELECTED_INDEX_OFFSET = 0x7C0
LOAD_SLOT_SELECTOR_COMMAND_WRAPPER_OFFSET = 0x800
LOAD_SLOT_SELECTOR_CONFIRM_WRAPPER_OFFSET = 0x840
LOAD_SLOT_SELECTOR_TITLE_WRAPPER_OFFSET = 0x880
LOAD_SLOT_SELECTOR_POST_SELECTION_WRAPPER_OFFSET = 0x8C0
LOAD_SLOT_SELECTOR_SESSION_FILE_WRAPPER_OFFSET = 0x900
LOAD_SLOT_SELECTOR_CONFIRM_TRAMPOLINE_OFFSET = 0x940
LOAD_SLOT_SELECTOR_CONFIRM_PROMPT_VA = 0x568CF8
LOAD_SLOT_SELECTOR_SAVE_COUNT = 10
LOAD_SLOT_SELECTOR_MENU_COUNT = 11
LOAD_SLOT_SELECTOR_LABEL_STRIDE = 0x20

# Recycled ship slots retain their previous cannon type/count/capacity.  The
# constructor calls the maximum-weight setter before clearing those fields,
# and that setter adds the stale cannon weight to the new ship.  Redirect that
# call through a wrapper which establishes the cannon-free constructor state
# first.  Clearing +54 also prevents the adjacent capacity setter from using
# the previous ship's maximum cannon count.
SHIP_REUSE_WEIGHT_SETTER_CALL_VA = 0x423231
SHIP_WEIGHT_SETTER_VA = 0x44C890
SHIP_REUSE_ORIGINAL_CALL = bytes.fromhex("E8 5A 96 02 00")
SHIP_REUSE_FIX_MAGIC = b"CDSSRF1\0"
SHIP_REUSE_FIX_VERSION = 1
SHIP_REUSE_FIX_WRAPPER_OFFSET = 0x20

# The ship-purchase selector returns the clicked visual row.  After docking,
# the number of selectable ship types can shrink while the list still has
# blank rows; clicking one of those rows used to index beyond the temporary
# candidate array at 0x44B65D.  Retain the explicit cancel (-1), but send any
# other invalid selection back to the existing selector loop.
SHIP_PURCHASE_BLANK_SELECTION_HOOK_VA = 0x44B628
SHIP_PURCHASE_BLANK_SELECTION_ORIGINAL = bytes.fromhex("83 FF FF 0F 84 50 01 00 00")
SHIP_PURCHASE_BLANK_SELECTION_RETRY_VA = 0x44B616
SHIP_PURCHASE_BLANK_SELECTION_CANCEL_VA = 0x44B781
SHIP_PURCHASE_BLANK_SELECTION_RESUME_VA = 0x44B631
SHIP_PURCHASE_BLANK_SELECTION_FIX_MAGIC = b"CDSSBS1\0"
SHIP_PURCHASE_BLANK_SELECTION_FIX_VERSION = 1
SHIP_PURCHASE_BLANK_SELECTION_FIX_WRAPPER_OFFSET = 0x20

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
    """One person or event-actor master record embedded in the executable."""

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
    hire_cost_coefficient: int
    abilities: tuple[int, ...]
    skills: tuple[int, ...]


@dataclass(frozen=True)
class PersonEdit:
    """The documented, fixed-width initial fields of one person or actor."""

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
    hire_cost_coefficient: int
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
    hint_id: int


@dataclass(frozen=True)
class ItemEdit:
    """Verified editable fields of one static item definition."""

    identifier: int
    name: str
    buy_price: int
    sell_price: int
    effect_value: int
    category_id: int
    hint_id: int


@dataclass(frozen=True)
class FakeItemRecord:
    """One of the 28 market fake-item records in the discovery master table."""

    identifier: int
    name: str
    category_id: int
    target_code: int
    value: int
    item_id: int
    city_id: int


@dataclass(frozen=True)
class FakeItemEdit:
    """Editable fields used when a fake item is offered by a city market."""

    identifier: int
    category_id: int
    target_code: int
    value: int
    item_id: int
    city_id: int


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
    description: str = ""


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
    description: str


@dataclass(frozen=True)
class HintRecord:
    """One EXE-side tavern hint and the cities where it can be heard."""

    identifier: int
    target_code: int
    city_ids: tuple[int, int, int, int]
    text: str


@dataclass(frozen=True)
class HintEdit:
    """Editable fields of one tavern-hint table row."""

    identifier: int
    target_code: int
    city_ids: tuple[int, int, int, int]
    text: str


@dataclass(frozen=True)
class DiscoveryHintEdit:
    """Editable metadata, requirements, and reading-page text of one library hint."""

    hint_id: int
    name: str
    target_id: int
    required_skill_id: int
    required_language_id: int
    required_level: int
    prerequisite_discovery_ids: tuple[int, ...]
    text: str


@dataclass(frozen=True)
class LibraryBookEdit:
    """Editable fields of one EXE library-book record."""

    record_number: int
    title: str
    author: str
    city_ids: tuple[int, ...]
    appearance_year: int


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
# Both sea travel and land exploration use this immediate when the telescope
# (item 35) is held: it is added to the city/port discovery radius in map
# cells.  The scan is quadratic in this value, so retain a practical cap.
TELESCOPE_CITY_DISCOVERY_BONUS_VA = 0x48D84E
TELESCOPE_CITY_DISCOVERY_BONUS_ORIGINAL = 2
TELESCOPE_CITY_DISCOVERY_BONUS_MAX = 32
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
# The player fame and infamy fields at 0x5B614C/0x5B6150 both pass through
# 0x4800E0.  Its PUSH at 0x4800E5 supplies the shared original maximum.
# 0x474188 belongs to voyage food, not fame; never edit it here.
PLAYER_FAME_LIMIT_HOOK_VA = 0x4800E5
PLAYER_FAME_LIMIT_HOOK_END_VA = PLAYER_FAME_LIMIT_HOOK_VA + 5
PLAYER_FAME_LIMIT_CODE_OFFSET = 0x20
PLAYER_FAME_LIMIT_MAGIC = b"FAMELIM1"
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
# The EXE owns 0x119 (281) static person records.  IDs 0~204 are the
# persistent people mirrored by SAVEDATA.CDS, 205~275 are event/battle
# actors, and 276~280 are internal accumulator rows rather than people.
PERSON_MASTER_RECORD_COUNT = 281
PERSON_RECORD_COUNT = 276
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
PERSON_HIRE_COST_COEFFICIENT_OFFSET = 0x3C
PERSON_ABILITIES_OFFSET = 0x40
PERSON_ABILITY_COUNT = 6
PERSON_VITALITY_OFFSET = 0x58
PERSON_SKILLS_OFFSET = 0x60
PERSON_SKILL_COUNT = 27
PERSON_ABILITY_MAX = 255
PERSON_VITALITY_MAX = 9999
PERSON_ABILITY_LIMIT_VA = 0x432C50
PERSON_ABILITY_LIMIT_BLOCK_SIZE = 0x30
PERSON_VITALITY_LIMIT_VA = 0x432C87
PERSON_ABILITY_ORIGINAL_CODE = bytes.fromhex(
    "8b 44 24 04 56 6a 64 6a 01 8d 34 81 8b 4c 24 14 51 "
    "8b 06 40 50 e8 f6 b8 06 00 83 c4 10 48 89 06 5e c2 08 00"
).ljust(PERSON_ABILITY_LIMIT_BLOCK_SIZE, b"\xCC")
PERSON_VITALITY_ORIGINAL_PREFIX = bytes.fromhex("8b 44 24 04 56 8b f1 68")
PERSON_VITALITY_ORIGINAL_SUFFIX = bytes.fromhex(
    "6a 00 50 8b 4e 18 51 e8 c8 b8 06 00 83 c4 10 89 46 18 5e c2 04 00"
)
PERSON_HIRE_COST_COEFFICIENT_MAX = 255
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
ITEM_HINT_ID_OFFSET = 0x18
ITEM_NO_IMAGE_ID = 0xFFFFFFFF
ITEM_CATEGORY_MAX = 8
ITEM_BOOK_CATEGORY = 7
ITEM_HINT_ID_MIN = -1
ITEM_HINT_ID_MAX = 185
ITEM_PRICE_MAX = 99_999_999
ITEM_EFFECT_MAX = 255
# The longest original item name is "갈라파고스 코끼리거북": 11 characters,
# 21 CP949 bytes.  Keep replacements within the existing game UI width.
ITEM_NAME_MAX_CHARACTERS = 11
ITEM_NAME_MAX_BYTES = 21
# Discovery definitions use two overlapping physical layouts.  Names,
# categories, game IDs and values have 230 contiguous metadata rows and one
# separate final row.  Map rectangles begin one row after their metadata:
# 230 contiguous overlays plus one overlay after the separate final row.
# Thus the rectangle for metadata ID 0 (Hope Cape) starts at 0x51C584.
# Dynamic discovery/report progress is deliberately not adjacent to these
# records: it is in SAVEDATA.CDS.
DISCOVERY_METADATA_TABLE_VA = 0x51C528
DISCOVERY_RECORD_COUNT = 231
DISCOVERY_METADATA_TABLE_RECORD_COUNT = 230
DISCOVERY_RECORD_SIZE = 0x5C
FAKE_ITEM_FIRST_RECORD_ID = 230
FAKE_ITEM_RECORD_COUNT = 28
FAKE_ITEM_LAST_RECORD_ID = FAKE_ITEM_FIRST_RECORD_ID + FAKE_ITEM_RECORD_COUNT - 1
FAKE_ITEM_ITEM_ID_OFFSET = 0x48
FAKE_ITEM_CITY_ID_OFFSET = 0x4C
# The final metadata entry (game ID 527) is outside the main 230-row table.
DISCOVERY_FINAL_METADATA_RECORD_VA = 0x522744
DISCOVERY_COORDINATE_TABLE_VA = 0x51C584
DISCOVERY_FINAL_COORDINATE_RECORD_VA = DISCOVERY_FINAL_METADATA_RECORD_VA + DISCOVERY_RECORD_SIZE
DISCOVERY_DESCRIPTION_POINTER_TABLE_VA = 0x57AA78
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
# The reward/item-image association is stored in the same physical master
# record, not in the overlapping coordinate view.
DISCOVERY_REWARD_ITEM_OFFSET = 0x30
NO_DISCOVERY_MEDIA = 0xFFFFFFFF
DISCOVER_ANIMATION_PART_COUNT = 29
# Discovery records 103~131 use every DISCOVER.CDS part exactly once, in
# this record order.  The AVI replacement keeps that original association
# and reserves I70_0000.AVI through I98_0000.AVI.
DISCOVER_AVI_FIRST_ID = 70
DISCOVER_AVI_RECORD_PARTS = (
    3, 4, 6, 0, 25, 7, 9, 8, 1, 10,
    11, 28, 12, 13, 22, 21, 14, 15, 23, 26,
    24, 2, 16, 17, 27, 5, 19, 20, 18,
)
DISCOVER_AVI_FIRST_RECORD_ID = 103
DISCOVERY_AVI_MAX = 98
DISCOVERY_CATEGORY_MAX = 7
LIBRARY_HINT_TABLE_VA = 0x4D8E88
LIBRARY_HINT_RECORD_COUNT = 186
LIBRARY_HINT_RECORD_SIZE = 0x50
LIBRARY_HINT_NAME_POINTER_OFFSET = -0x08
LIBRARY_HINT_TARGET_ID_OFFSET = 0x00
LIBRARY_HINT_REQUIRED_SKILL_OFFSET = 0x18
LIBRARY_HINT_REQUIRED_LANGUAGE_OFFSET = 0x1C
LIBRARY_HINT_REQUIRED_LEVEL_OFFSET = 0x20
LIBRARY_HINT_PREREQUISITE_LIST_OFFSET = 0x28
LIBRARY_HINT_PREREQUISITE_CAPACITY = 8
LIBRARY_HINT_TEXT_ID_OFFSET = 0x14
LIBRARY_HINT_TEXT_POINTER_TABLE_VA = 0x543FA0
LIBRARY_HINT_TEXT_POINTER_COUNT = 186
LIBRARY_HINT_TEXT_MAX_BYTES = LIBRARY_HINT_TEXT_SLOT_STRIDE - 1
LIBRARY_HINT_SKILL_MIN = -1
LIBRARY_HINT_SKILL_MAX = 12
LIBRARY_HINT_LANGUAGE_MIN = -1
LIBRARY_HINT_LANGUAGE_MAX = 13
LIBRARY_HINT_LEVEL_MIN = 0
LIBRARY_HINT_LEVEL_MAX = 3
LIBRARY_HINT_DISCOVERY_MIN = 0
LIBRARY_HINT_DISCOVERY_MAX = 273
LIBRARY_HINT_NAME_MAX_CHARACTERS = 18
LIBRARY_HINT_NAME_MAX_BYTES = 36
LIBRARY_BOOK_TABLE_VA = 0x4C4748
LIBRARY_BOOK_RECORD_COUNT = 257
LIBRARY_BOOK_RECORD_SIZE = 0x58
LIBRARY_BOOK_TITLE_POINTER_OFFSET = 0x00
LIBRARY_BOOK_AUTHOR_POINTER_OFFSET = 0x04
LIBRARY_BOOK_APPEARANCE_YEAR_OFFSET = 0x10
LIBRARY_BOOK_YEAR_BASE = 1480
LIBRARY_BOOK_YEAR_MAX = 1600
LIBRARY_BOOK_CITY_LIST_OFFSET = 0x18
LIBRARY_BOOK_CITY_CAPACITY = 8
LIBRARY_BOOK_TITLE_MAX_CHARACTERS = 18
LIBRARY_BOOK_TITLE_MAX_BYTES = 36
LIBRARY_BOOK_AUTHOR_MAX_CHARACTERS = 18
LIBRARY_BOOK_AUTHOR_MAX_BYTES = 36
DISCOVERY_VALUE_MAX = 99_999_999
DISCOVERY_DESCRIPTION_MAX_BYTES = DISCOVERY_DESCRIPTION_SLOT_STRIDE - 1
DISCOVERY_X_MIN = -1
DISCOVERY_X_MAX = 2500
DISCOVERY_Y_MIN = -1
DISCOVERY_Y_MAX = 1250
DISCOVERY_NAME_MAX_BYTES = DISCOVERY_NAME_SLOT_STRIDE - 1
HINT_TABLE_VA = 0x525078
HINT_RECORD_COUNT = 191
HINT_RECORD_SIZE = 0x18
HINT_TARGET_CODE_OFFSET = 0x00
HINT_CITY_IDS_OFFSET = 0x04
HINT_CITY_COUNT = 4
HINT_TEXT_POINTER_OFFSET = 0x14
HINT_TEXT_MAX_BYTES = HINT_TEXT_SLOT_STRIDE - 1
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
    (0x16E7F8, "빚    /", "계약금/"),
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
    (0x17A214, "항주", "남경"),
)
# `EXE_LOCALIZATION_ANALYSIS.md` verifies that these CP932 strings reach live
# dialogue/UI output paths.  Each Korean replacement fits in the original
# NUL-terminated byte field, so no code pointer or adjacent string moves.
MISTRANSLATION_UNTRANSLATED_REPLACEMENTS = (
    (
        0x1655F4,
        "おい、あんた聞いたかい？  %sが滅ぼされたって噂だぜ".encode("cp932"),
        "이봐, 들었어?  %s가 멸망했다는 소문이야.",
    ),
    (
        0x165584,
        "あんた達と同じ船乗りさ、ウソだと思うなら自分で確かめな！".encode("cp932"),
        "당신들과 같은 뱃사람이야. 거짓말 같으면 직접 확인해 봐!",
    ),
    (
        0x165558,
        "なんてこった。これじゃ、契約はご破算ですね".encode("cp932"),
        "이런, 이러면 계약은 파기군요.",
    ),
    (
        0x165538,
        "%sとの契約は破棄されました！".encode("cp932"),
        "%s와의 계약이 파기됐습니다!",
    ),
    (0x16690C, "危険なエラー".encode("cp932"), "치명적 오류"),
    (
        0x16689C,
        (
            "サーフェスの固定に失敗しました。\n"
            "システムの異常だと思われます。直ちにゲームを終了して、ＯＳを再起動してください"
        ).encode("cp932"),
        (
            "서피스 고정에 실패했습니다.\n"
            "시스템에 이상이 있습니다. 즉시 게임을 종료하고 운영 체제를 다시 시작하십시오."
        ),
    ),
    (0x166940, "エラー".encode("cp932"), "오류"),
    (0x166920, "ファイルの作成に失敗しました".encode("cp932"), "파일 생성에 실패했습니다"),
)
MISTRANSLATION_CONTRACT_LABEL_OFFSET = 0x16E7F8
MISTRANSLATION_SWORD_TEXT_OFFSET = 0x156080
MISTRANSLATION_SWORD_TEXT_CAPACITY = 16
MISTRANSLATION_SWORD_POINTER_OFFSET = 0xFC204
MISTRANSLATION_SWORD_ORIGINAL_VA = 0x558880
MISTRANSLATION_SWORD_ORIGINAL = "아이베는 안강"
MISTRANSLATION_SWORD_INTEGRATED = "도지기리 안강"
MISTRANSLATION_SWORD_CORRECTED = "도지기리 야스츠나"
MISTRANSLATION_MAGIC = b"CDSTRN1\0"

# The failed-pottery market appearance is a coordinated patch.  The tavern
# sentence, hint target, and fake-item sale city must agree or the hint can
# point the player to a city where the item is never offered.
FAILED_POTTERY_TEXT_OFFSET = 0x17A214
FAILED_POTTERY_TEXT_ORIGINAL = "항주"
FAILED_POTTERY_TEXT_PATCHED = "남경"
FAILED_POTTERY_HINT_ID = 182
FAILED_POTTERY_HINT_TARGET_ORIGINAL = 218
FAILED_POTTERY_HINT_TARGET_PATCHED = 122
FAILED_POTTERY_FAKE_RECORD_ID = 247
FAILED_POTTERY_CITY_ORIGINAL = 177
FAILED_POTTERY_CITY_PATCHED = 178

# Tavern-hint corrections are applied together so the user can enable one
# coherent repair option.  Existing releases could have only the Knossos
# target correction applied; that legacy state is detected and upgraded on
# the next save.
KNOSSOS_HINT_ID = 88
KNOSSOS_HINT_TARGET_ORIGINAL = 302
KNOSSOS_HINT_TARGET_PATCHED = 112
CACAO_HINT_ID = 152
CACAO_HINT_CITY_ORIGINAL = 206  # 메리다
CACAO_HINT_CITY_PATCHED = 204   # 미틀라


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

    for offset, original_bytes, corrected in MISTRANSLATION_UNTRANSLATED_REPLACEMENTS:
        corrected_bytes = _translation_bytes(corrected)
        if len(corrected_bytes) > len(original_bytes):
            raise AssertionError(f"미번역 수정 문자열이 원본 영역을 초과합니다: 0x{offset:X}")
        corrected_field = corrected_bytes.ljust(len(original_bytes), b"\0")
        current = bytes(data[offset:offset + len(original_bytes)])
        if current not in (original_bytes, corrected_field):
            raise ValueError(f"미번역 수정 위치 0x{offset:X}의 문자열을 검증하지 못했습니다.")

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
    """Recognize the current or previous localization patch for migration."""
    _validate_mistranslation_layout(data)
    if any(
        data[offset:offset + len(_translation_bytes(corrected))] != _translation_bytes(corrected)
        for offset, _, corrected in MISTRANSLATION_REPLACEMENTS
        if offset != MISTRANSLATION_CONTRACT_LABEL_OFFSET
    ):
        return False
    if any(
        data[offset:offset + len(original_bytes)]
        != _translation_bytes(corrected).ljust(len(original_bytes), b"\0")
        for offset, original_bytes, corrected in MISTRANSLATION_UNTRANSLATED_REPLACEMENTS
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

    for offset, original_bytes, corrected in MISTRANSLATION_UNTRANSLATED_REPLACEMENTS:
        replacement = (
            _translation_bytes(corrected).ljust(len(original_bytes), b"\0")
            if enabled else original_bytes
        )
        if data[offset:offset + len(original_bytes)] != replacement:
            data[offset:offset + len(original_bytes)] = replacement
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


def _failed_pottery_offsets(data: bytes | bytearray) -> tuple[int, int]:
    """Return the hint-target and fake-item-city offsets for this EXE."""
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        hint_offset = pe.get_offset_from_rva(HINT_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase)
        fake_table_offset = pe.get_offset_from_rva(
            DISCOVERY_METADATA_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
        return (
            hint_offset + FAILED_POTTERY_HINT_ID * HINT_RECORD_SIZE + HINT_TARGET_CODE_OFFSET,
            fake_table_offset
            + FAILED_POTTERY_FAKE_RECORD_ID * DISCOVERY_RECORD_SIZE
            + FAKE_ITEM_CITY_ID_OFFSET,
        )
    finally:
        pe.close()


def _validate_failed_pottery_layout(data: bytes | bytearray) -> tuple[int, int]:
    original_text = _translation_bytes(FAILED_POTTERY_TEXT_ORIGINAL)
    patched_text = _translation_bytes(FAILED_POTTERY_TEXT_PATCHED)
    current_text = bytes(data[
        FAILED_POTTERY_TEXT_OFFSET:FAILED_POTTERY_TEXT_OFFSET + len(original_text)
    ])
    if current_text not in (original_text, patched_text):
        raise ValueError("실패작 도자기 주점 힌트 문자열을 검증하지 못했습니다.")
    hint_offset, city_offset = _failed_pottery_offsets(data)
    return hint_offset, city_offset


def read_failed_pottery_patch_state(data: bytes) -> bool:
    """Return True only when all three failed-pottery changes are present."""
    hint_offset, city_offset = _validate_failed_pottery_layout(data)
    patched_text = _translation_bytes(FAILED_POTTERY_TEXT_PATCHED)
    return (
        data[FAILED_POTTERY_TEXT_OFFSET:FAILED_POTTERY_TEXT_OFFSET + len(patched_text)]
        == patched_text
        and struct.unpack_from("<I", data, hint_offset)[0]
        == FAILED_POTTERY_HINT_TARGET_PATCHED
        and struct.unpack_from("<I", data, city_offset)[0] == FAILED_POTTERY_CITY_PATCHED
    )


def apply_failed_pottery_patch(data: bytearray, enabled: bool) -> bool:
    """Apply or restore the coordinated failed-pottery market appearance."""
    hint_offset, city_offset = _validate_failed_pottery_layout(data)
    text = _translation_bytes(
        FAILED_POTTERY_TEXT_PATCHED if enabled else FAILED_POTTERY_TEXT_ORIGINAL
    )
    target = (
        FAILED_POTTERY_HINT_TARGET_PATCHED
        if enabled else FAILED_POTTERY_HINT_TARGET_ORIGINAL
    )
    city = FAILED_POTTERY_CITY_PATCHED if enabled else FAILED_POTTERY_CITY_ORIGINAL
    changed = False
    if data[FAILED_POTTERY_TEXT_OFFSET:FAILED_POTTERY_TEXT_OFFSET + len(text)] != text:
        data[FAILED_POTTERY_TEXT_OFFSET:FAILED_POTTERY_TEXT_OFFSET + len(text)] = text
        changed = True
    if struct.unpack_from("<I", data, hint_offset)[0] != target:
        struct.pack_into("<I", data, hint_offset, target)
        changed = True
    if struct.unpack_from("<I", data, city_offset)[0] != city:
        struct.pack_into("<I", data, city_offset, city)
        changed = True
    return changed


def _tavern_hint_bug_fix_offsets(data: bytes | bytearray) -> tuple[int, int]:
    """Return the Knossos target and Kakao first-city fields."""
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        table_offset = pe.get_offset_from_rva(
            HINT_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
        return (
            table_offset
            + KNOSSOS_HINT_ID * HINT_RECORD_SIZE
            + HINT_TARGET_CODE_OFFSET,
            table_offset
            + CACAO_HINT_ID * HINT_RECORD_SIZE
            + HINT_CITY_IDS_OFFSET,
        )
    finally:
        pe.close()


def read_tavern_hint_bug_fix_state(data: bytes) -> bool:
    """Recognize the full fix and the legacy Knossos-only patch state."""
    knossos_offset, cacao_city_offset = _tavern_hint_bug_fix_offsets(data)
    knossos_target = struct.unpack_from("<I", data, knossos_offset)[0]
    # The tavern-hint editor intentionally allows arbitrary target and city
    # edits, so only the exact legacy correction acts as an enabled marker.
    # Do not reject a user-authored value in either field while loading EXE.
    # Older versions contained the first correction only.  Keep it selected
    # in the consolidated UI so a normal save upgrades it with the Kakao city.
    cacao_city = struct.unpack_from("<i", data, cacao_city_offset)[0]
    return (
        knossos_target == KNOSSOS_HINT_TARGET_PATCHED
        and cacao_city in (CACAO_HINT_CITY_ORIGINAL, CACAO_HINT_CITY_PATCHED)
    )


def apply_tavern_hint_bug_fix(data: bytearray, enabled: bool) -> bool:
    """Correct or restore the Knossos target and Kakao hint city together."""
    knossos_offset, cacao_city_offset = _tavern_hint_bug_fix_offsets(data)
    target = KNOSSOS_HINT_TARGET_PATCHED if enabled else KNOSSOS_HINT_TARGET_ORIGINAL
    city = CACAO_HINT_CITY_PATCHED if enabled else CACAO_HINT_CITY_ORIGINAL
    changed = False
    if struct.unpack_from("<I", data, knossos_offset)[0] != target:
        struct.pack_into("<I", data, knossos_offset, target)
        changed = True
    if struct.unpack_from("<i", data, cacao_city_offset)[0] != city:
        struct.pack_into("<i", data, cacao_city_offset, city)
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


def _build_npc_daily_departure_payload(slot_va: int) -> bytes:
    """Build callbacks that check ordinary NPCs daily and keep special NPCs monthly."""
    wrapper_va = slot_va + NPC_DAILY_DEPARTURE_WRAPPER_OFFSET
    wrapper = bytearray(bytes.fromhex(
        "56 "                 # push esi
        "53 "                 # push ebx
        "8B F1 "              # mov esi, ecx
        "8B 5C 24 0C "        # mov ebx, [esp+0Ch] (elapsed days)
        "85 DB "              # test ebx, ebx
        "7E 00 "              # jle fallback
    ))
    fallback_jump_byte = len(wrapper) - 1
    loop_offset = len(wrapper)
    wrapper.extend(bytes.fromhex("6A 01 8B CE E8 00 00 00 00"))
    daily_call_offset = loop_offset + 4
    wrapper.extend(bytes.fromhex("8B CE E8 00 00 00 00"))
    character_id_call_offset = loop_offset + 11
    wrapper.extend(bytes.fromhex("83 F8 0E 7C 00"))  # ID < 14: special NPC
    special_npc_jump_byte = len(wrapper) - 1
    wrapper.extend(bytes.fromhex("3D BF 00 00 00 7D 00"))  # ID >= 191: non-NPC
    non_npc_jump_byte = len(wrapper) - 1
    wrapper.extend(bytes.fromhex("8B CE E8 00 00 00 00"))
    monthly_call_offset = len(wrapper) - 5
    skip_departure_offset = len(wrapper)
    wrapper.extend(bytes.fromhex("4B 75 00 EB 00"))
    loop_jump_byte = len(wrapper) - 3
    done_jump_byte = len(wrapper) - 1
    fallback_offset = len(wrapper)
    wrapper.extend(bytes.fromhex("53 8B CE E8 00 00 00 00"))
    fallback_daily_call_offset = fallback_offset + 3
    done_offset = len(wrapper)
    wrapper.extend(bytes.fromhex("5B 5E C2 04 00"))

    def patch_rel32(call_offset: int, target_va: int) -> None:
        struct.pack_into(
            "<i", wrapper, call_offset + 1,
            target_va - (wrapper_va + call_offset + 5),
        )

    wrapper[fallback_jump_byte] = fallback_offset - (fallback_jump_byte + 1)
    wrapper[special_npc_jump_byte] = skip_departure_offset - (special_npc_jump_byte + 1)
    wrapper[non_npc_jump_byte] = skip_departure_offset - (non_npc_jump_byte + 1)
    wrapper[loop_jump_byte] = (loop_offset - (loop_jump_byte + 1)) & 0xFF
    wrapper[done_jump_byte] = done_offset - (done_jump_byte + 1)
    patch_rel32(daily_call_offset, NPC_DAILY_UPDATE_VA)
    patch_rel32(character_id_call_offset, NPC_CHARACTER_ID_VA)
    patch_rel32(monthly_call_offset, NPC_MONTHLY_DEPARTURE_VA)
    patch_rel32(fallback_daily_call_offset, NPC_DAILY_UPDATE_VA)

    if len(wrapper) >= (
        NPC_DAILY_DEPARTURE_MONTHLY_GATE_OFFSET - NPC_DAILY_DEPARTURE_WRAPPER_OFFSET
    ):
        raise AssertionError("NPC 일일 이동 래퍼가 예약 공간을 초과했습니다.")

    # The original monthly callback also handles special/history NPCs (IDs 0-13).
    # Keep that path monthly while suppressing the duplicated ordinary-NPC check.
    monthly_gate_va = slot_va + NPC_DAILY_DEPARTURE_MONTHLY_GATE_OFFSET
    monthly_gate = bytearray(bytes.fromhex(
        "56 "                 # push esi
        "8B F1 "              # mov esi, ecx
        "E8 00 00 00 00 "     # call character ID getter
        "83 F8 0E "           # cmp eax, 14
        "7D 00 "              # jge done
        "8B CE "              # mov ecx, esi
        "E8 00 00 00 00 "     # call original monthly callback
        "5E "                 # pop esi
        "C3"                  # ret
    ))
    gate_id_call_offset = 3
    gate_skip_jump_byte = 12
    gate_monthly_call_offset = 15
    gate_done_offset = 20

    def patch_gate_rel32(call_offset: int, target_va: int) -> None:
        struct.pack_into(
            "<i", monthly_gate, call_offset + 1,
            target_va - (monthly_gate_va + call_offset + 5),
        )

    monthly_gate[gate_skip_jump_byte] = gate_done_offset - (gate_skip_jump_byte + 1)
    patch_gate_rel32(gate_id_call_offset, NPC_CHARACTER_ID_VA)
    patch_gate_rel32(gate_monthly_call_offset, NPC_MONTHLY_DEPARTURE_VA)

    payload = bytearray(NPC_DAILY_DEPARTURE_SLOT_SIZE)
    payload[:8] = NPC_DAILY_DEPARTURE_MAGIC
    struct.pack_into(
        "<III", payload, 8, NPC_DAILY_DEPARTURE_VERSION,
        NPC_DAILY_UPDATE_VA, NPC_MONTHLY_DEPARTURE_VA,
    )
    payload[
        NPC_DAILY_DEPARTURE_WRAPPER_OFFSET:
        NPC_DAILY_DEPARTURE_WRAPPER_OFFSET + len(wrapper)
    ] = wrapper
    payload[
        NPC_DAILY_DEPARTURE_MONTHLY_GATE_OFFSET:
        NPC_DAILY_DEPARTURE_MONTHLY_GATE_OFFSET + len(monthly_gate)
    ] = monthly_gate
    return bytes(payload)


def _npc_daily_departure_patch_info(data: bytes | bytearray) -> bool:
    """Return whether the verified daily-departure callback patch is active."""
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        daily_pointer_offset = pe.get_offset_from_rva(
            NPC_DAILY_UPDATE_VTABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
        monthly_pointer_offset = pe.get_offset_from_rva(
            NPC_MONTHLY_DEPARTURE_VTABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
    finally:
        pe.close()
    pointers = struct.unpack_from("<II", data, daily_pointer_offset)
    if pointers == (NPC_DAILY_UPDATE_VA, NPC_MONTHLY_DEPARTURE_VA):
        return False
    section = find_patch_section(data)
    if (
        section is None
        or section.raw_size < NPC_DAILY_DEPARTURE_SLOT_OFFSET + NPC_DAILY_DEPARTURE_SLOT_SIZE
        or section.virtual_size < NPC_DAILY_DEPARTURE_SLOT_OFFSET + NPC_DAILY_DEPARTURE_SLOT_SIZE
    ):
        raise ValueError("NPC 일일 이동 패치 포인터가 있으나 .patch 데이터를 찾지 못했습니다.")
    slot_offset, slot_va = section.slot(
        NPC_DAILY_DEPARTURE_SLOT_OFFSET, NPC_DAILY_DEPARTURE_SLOT_SIZE,
    )
    expected_pointers = (
        slot_va + NPC_DAILY_DEPARTURE_WRAPPER_OFFSET,
        slot_va + NPC_DAILY_DEPARTURE_MONTHLY_GATE_OFFSET,
    )
    expected_payload = _build_npc_daily_departure_payload(slot_va)
    if (
        pointers != expected_pointers
        or bytes(data[slot_offset:slot_offset + NPC_DAILY_DEPARTURE_SLOT_SIZE]) != expected_payload
    ):
        raise ValueError("NPC 일일 이동 패치 상태를 검증하지 못했습니다.")
    return True


def read_npc_daily_departure_enabled(target: Path) -> bool:
    """Read whether ordinary NPC departure is checked for every elapsed day."""
    return _npc_daily_departure_patch_info(target.resolve(strict=True).read_bytes())


def apply_npc_daily_departure(data: bytearray, enabled: bool) -> bool:
    """Move ordinary-NPC departure checks between monthly and daily callbacks."""
    current_enabled = _npc_daily_departure_patch_info(data)
    if current_enabled == enabled:
        return False
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        daily_pointer_offset = pe.get_offset_from_rva(
            NPC_DAILY_UPDATE_VTABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
        monthly_pointer_offset = pe.get_offset_from_rva(
            NPC_MONTHLY_DEPARTURE_VTABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
    finally:
        pe.close()
    if enabled:
        section, _created = ensure_patch_section(
            data, PATCH_SECTION_NPC_DAILY_DEPARTURE_SIZE,
        )
        slot_offset, slot_va = section.slot(
            NPC_DAILY_DEPARTURE_SLOT_OFFSET, NPC_DAILY_DEPARTURE_SLOT_SIZE,
        )
        payload = _build_npc_daily_departure_payload(slot_va)
        current_payload = bytes(data[slot_offset:slot_offset + NPC_DAILY_DEPARTURE_SLOT_SIZE])
        if any(current_payload) and current_payload != payload:
            raise ValueError("NPC 일일 이동용 .patch 슬롯이 다른 데이터로 사용 중입니다.")
        data[slot_offset:slot_offset + NPC_DAILY_DEPARTURE_SLOT_SIZE] = payload
        struct.pack_into(
            "<II", data, daily_pointer_offset,
            slot_va + NPC_DAILY_DEPARTURE_WRAPPER_OFFSET,
            slot_va + NPC_DAILY_DEPARTURE_MONTHLY_GATE_OFFSET,
        )
    else:
        section = find_patch_section(data)
        if section is None:
            raise ValueError("NPC 일일 이동 패치의 복원 데이터를 찾지 못했습니다.")
        struct.pack_into(
            "<II", data, daily_pointer_offset,
            NPC_DAILY_UPDATE_VA, NPC_MONTHLY_DEPARTURE_VA,
        )
        clear_slot(
            data, section, NPC_DAILY_DEPARTURE_SLOT_OFFSET,
            NPC_DAILY_DEPARTURE_SLOT_SIZE,
        )
    return True


def _build_judgment_fix_payload(slot_va: int) -> bytes:
    """Build the verified wrapper called in place of Judgment's cleanup call."""
    wrapper_va = slot_va + JUDGMENT_FIX_WRAPPER_OFFSET
    wrapper = bytearray(bytes.fromhex(
        "56 "                 # push esi
        "8B F1 "              # mov esi, ecx (battle object)
        "8B 44 24 08 "        # mov eax, [esp+8] (original target index)
        "50 "                 # push eax
        "E8 00 00 00 00 "     # call dead-unit cleanup (callee pops duplicate arg)
        "8B 44 24 08 "        # mov eax, [esp+8] (original target index)
        "50 "                 # push eax
        "E8 00 00 00 00 "     # call unit-index-to-side
        "83 C4 04 "           # add esp, 4
        "50 "                 # push eax (side)
        "8B CE "              # mov ecx, esi
        "E8 00 00 00 00 "     # call formation normalization (callee pops side)
        "5E "                 # pop esi
        "C2 04 00"            # ret 4 (pop original target index)
    ))
    cleanup_call_offset = 8
    side_call_offset = 18
    normalize_call_offset = 29

    def patch_rel32(call_offset: int, target_va: int) -> None:
        if wrapper[call_offset] != 0xE8:
            raise AssertionError("심판 버그 수정 래퍼의 CALL 위치가 올바르지 않습니다.")
        struct.pack_into(
            "<i", wrapper, call_offset + 1,
            target_va - (wrapper_va + call_offset + 5),
        )

    patch_rel32(cleanup_call_offset, JUDGMENT_DEAD_UNIT_CLEANUP_VA)
    patch_rel32(side_call_offset, JUDGMENT_UNIT_SIDE_VA)
    patch_rel32(normalize_call_offset, JUDGMENT_FORMATION_NORMALIZE_VA)
    if JUDGMENT_FIX_WRAPPER_OFFSET + len(wrapper) > JUDGMENT_FIX_SLOT_SIZE:
        raise AssertionError("심판 버그 수정 래퍼가 예약 공간을 초과했습니다.")

    payload = bytearray(JUDGMENT_FIX_SLOT_SIZE)
    payload[:len(JUDGMENT_FIX_MAGIC)] = JUDGMENT_FIX_MAGIC
    struct.pack_into("<I", payload, len(JUDGMENT_FIX_MAGIC), JUDGMENT_FIX_VERSION)
    payload[
        JUDGMENT_FIX_WRAPPER_OFFSET:
        JUDGMENT_FIX_WRAPPER_OFFSET + len(wrapper)
    ] = wrapper
    return bytes(payload)


def _judgment_fix_hook(wrapper_va: int) -> bytes:
    return b"\xE8" + struct.pack(
        "<i", wrapper_va - (JUDGMENT_CLEANUP_HOOK_VA + 5),
    )


def _judgment_fix_patch_info(data: bytes | bytearray) -> bool:
    """Return whether the verified Judgment front-rank fix is active."""
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        hook_offset = pe.get_offset_from_rva(
            JUDGMENT_CLEANUP_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase
        )
    finally:
        pe.close()
    current_hook = bytes(data[hook_offset:hook_offset + len(JUDGMENT_ORIGINAL_HOOK)])
    section = find_patch_section(data)
    if current_hook == JUDGMENT_ORIGINAL_HOOK:
        if (
            section is not None
            and section.raw_size >= JUDGMENT_FIX_SLOT_OFFSET + JUDGMENT_FIX_SLOT_SIZE
            and bytes(data[
                section.raw_offset + JUDGMENT_FIX_SLOT_OFFSET:
                section.raw_offset + JUDGMENT_FIX_SLOT_OFFSET + len(JUDGMENT_FIX_MAGIC)
            ]) == JUDGMENT_FIX_MAGIC
        ):
            raise ValueError("심판 버그 수정 코드가 남아 있지만 호출부가 원본 상태입니다.")
        return False
    if (
        section is None
        or section.raw_size < JUDGMENT_FIX_SLOT_OFFSET + JUDGMENT_FIX_SLOT_SIZE
        or section.virtual_size < JUDGMENT_FIX_SLOT_OFFSET + JUDGMENT_FIX_SLOT_SIZE
    ):
        raise ValueError("심판 버그 수정 호출이 있으나 .patch 데이터를 찾지 못했습니다.")
    slot_offset, slot_va = section.slot(JUDGMENT_FIX_SLOT_OFFSET, JUDGMENT_FIX_SLOT_SIZE)
    expected_payload = _build_judgment_fix_payload(slot_va)
    expected_hook = _judgment_fix_hook(slot_va + JUDGMENT_FIX_WRAPPER_OFFSET)
    if (
        current_hook != expected_hook
        or bytes(data[slot_offset:slot_offset + JUDGMENT_FIX_SLOT_SIZE]) != expected_payload
    ):
        raise ValueError("심판 버그 수정 패치 상태를 검증하지 못했습니다.")
    return True


def apply_judgment_fix(data: bytearray, enabled: bool) -> bool:
    """Fix Judgment freezing after it eliminates every unit in a front rank."""
    current_enabled = _judgment_fix_patch_info(data)
    if current_enabled == enabled:
        return False
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        hook_offset = pe.get_offset_from_rva(
            JUDGMENT_CLEANUP_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase
        )
    finally:
        pe.close()
    if enabled:
        section, _created = ensure_patch_section(data, PATCH_SECTION_JUDGMENT_FIX_SIZE)
        slot_offset, slot_va = section.slot(JUDGMENT_FIX_SLOT_OFFSET, JUDGMENT_FIX_SLOT_SIZE)
        payload = _build_judgment_fix_payload(slot_va)
        current_payload = bytes(data[slot_offset:slot_offset + JUDGMENT_FIX_SLOT_SIZE])
        if any(current_payload) and current_payload != payload:
            raise ValueError("심판 버그 수정용 .patch 슬롯이 다른 데이터로 사용 중입니다.")
        data[slot_offset:slot_offset + JUDGMENT_FIX_SLOT_SIZE] = payload
        data[hook_offset:hook_offset + len(JUDGMENT_ORIGINAL_HOOK)] = _judgment_fix_hook(
            slot_va + JUDGMENT_FIX_WRAPPER_OFFSET
        )
    else:
        section = find_patch_section(data)
        if section is None:
            raise ValueError("심판 버그 수정의 복원 데이터를 찾지 못했습니다.")
        data[hook_offset:hook_offset + len(JUDGMENT_ORIGINAL_HOOK)] = JUDGMENT_ORIGINAL_HOOK
        clear_slot(data, section, JUDGMENT_FIX_SLOT_OFFSET, JUDGMENT_FIX_SLOT_SIZE)
    return True


def _cannon_accuracy_fix_patch_info(data: bytes | bytearray) -> bool:
    """Return whether the signed cannon-accuracy minimum clamp is active."""
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        branch_offset = pe.get_offset_from_rva(
            CANNON_ACCURACY_CLAMP_BRANCH_VA - pe.OPTIONAL_HEADER.ImageBase
        )
    finally:
        pe.close()
    branch = bytes(data[
        branch_offset:branch_offset + len(CANNON_ACCURACY_ORIGINAL_BRANCH)
    ])
    if branch == CANNON_ACCURACY_ORIGINAL_BRANCH:
        return False
    if branch == CANNON_ACCURACY_FIXED_BRANCH:
        return True
    raise ValueError("포격 명중률 최솟값 판정 코드를 검증하지 못했습니다.")


def apply_cannon_accuracy_fix(data: bytearray, enabled: bool) -> bool:
    """Clamp negative cannon hit rates to 1% instead of treating them as 100%."""
    current_enabled = _cannon_accuracy_fix_patch_info(data)
    if current_enabled == enabled:
        return False
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        branch_offset = pe.get_offset_from_rva(
            CANNON_ACCURACY_CLAMP_BRANCH_VA - pe.OPTIONAL_HEADER.ImageBase
        )
    finally:
        pe.close()
    target = (
        CANNON_ACCURACY_FIXED_BRANCH
        if enabled else CANNON_ACCURACY_ORIGINAL_BRANCH
    )
    data[branch_offset:branch_offset + len(target)] = target
    return True


def _disev_language_fix_patch_info(data: bytes | bytearray) -> bool:
    """Return whether DISEV lookup ID 15 uses language array index 11."""
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        instruction_offset = pe.get_offset_from_rva(
            DISEV_LANGUAGE_LOOKUP_INSTRUCTION_VA - pe.OPTIONAL_HEADER.ImageBase
        )
    finally:
        pe.close()
    instruction = bytes(data[
        instruction_offset:instruction_offset + len(DISEV_LANGUAGE_LOOKUP_ORIGINAL)
    ])
    if instruction == DISEV_LANGUAGE_LOOKUP_ORIGINAL:
        return False
    if instruction == DISEV_LANGUAGE_LOOKUP_FIXED:
        return True
    raise ValueError("DISEV 언어 숙련도 조회 명령을 검증하지 못했습니다.")


def apply_disev_language_fix(data: bytearray, enabled: bool) -> bool:
    """Use Central/South American instead of African for lookup ID 15."""
    current_enabled = _disev_language_fix_patch_info(data)
    if current_enabled == enabled:
        return False
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        instruction_offset = pe.get_offset_from_rva(
            DISEV_LANGUAGE_LOOKUP_INSTRUCTION_VA - pe.OPTIONAL_HEADER.ImageBase
        )
    finally:
        pe.close()
    target = (
        DISEV_LANGUAGE_LOOKUP_FIXED
        if enabled else DISEV_LANGUAGE_LOOKUP_ORIGINAL
    )
    data[instruction_offset:instruction_offset + len(target)] = target
    return True


def _history_elapsed_years_fix_compare_offset(data: bytes | bytearray) -> int:
    """Return the file offset of the verified subcondition-16 comparison."""
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        return pe.get_offset_from_rva(
            HISTORY_ELAPSED_COMPARE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
    finally:
        pe.close()


def _build_history_elapsed_years_fix_payload(slot_va: int) -> bytes:
    """Build the isolated HIST_EV subcondition-16 comparison wrapper."""
    wrapper_va = slot_va + HISTORY_ELAPSED_YEARS_FIX_WRAPPER_OFFSET
    wrapper = bytearray(bytes.fromhex(
        "50 "                    # push eax (positive u16 threshold)
        "8B 44 24 18 "           # mov eax, [esp+18h] (condition stream pointer)
        "0F B7 40 FB "           # movzx eax, word ptr [eax-5] (discovery ID)
        "8D 14 80 "              # lea edx, [eax+eax*4]
        "8D 04 90 "              # lea eax, [eax+edx*4] (ID * 21)
        "8D 0C C5 00 00 00 00 "  # lea ecx, [eax*8+discovery master table]
        "E8 00 00 00 00 "        # call discovery-record lookup
        "85 C0 "                 # test eax, eax
        "74 15 "                 # je missing_record
        "8B 3D 20 4D 5A 00 "     # mov edi, [005A4D20h] (current year)
        "2B 78 28 "              # sub edi, [eax+28h] (discovery year)
        "58 "                    # pop eax (restore threshold)
        "66 3B F8 "              # cmp di, ax
        "1B F6 "                 # sbb esi, esi
        "46 "                    # inc esi (unsigned elapsed >= threshold)
        "E9 00 00 00 00 "        # jmp common result continuation
        "58 "                    # missing_record: pop eax (balance threshold)
        "33 F6 "                 # xor esi, esi
        "E9 00 00 00 00"         # jmp common result continuation
    ))
    lookup_call_offset = 22
    true_jump_offset = 47
    false_jump_offset = 55
    struct.pack_into("<I", wrapper, 18, HISTORY_ELAPSED_DISCOVERY_TABLE_VA)
    struct.pack_into(
        "<i", wrapper, lookup_call_offset + 1,
        HISTORY_ELAPSED_DISCOVERY_LOOKUP_VA
        - (wrapper_va + lookup_call_offset + 5),
    )
    for jump_offset in (true_jump_offset, false_jump_offset):
        struct.pack_into(
            "<i", wrapper, jump_offset + 1,
            HISTORY_ELAPSED_RESULT_VA - (wrapper_va + jump_offset + 5),
        )
    if (
        HISTORY_ELAPSED_YEARS_FIX_WRAPPER_OFFSET + len(wrapper)
        > HISTORY_ELAPSED_YEARS_FIX_SLOT_SIZE
    ):
        raise AssertionError("발견 후 경과 연수 수정 래퍼가 예약 공간을 초과했습니다.")

    payload = bytearray(HISTORY_ELAPSED_YEARS_FIX_SLOT_SIZE)
    payload[:len(HISTORY_ELAPSED_YEARS_FIX_MAGIC)] = HISTORY_ELAPSED_YEARS_FIX_MAGIC
    struct.pack_into(
        "<I", payload, len(HISTORY_ELAPSED_YEARS_FIX_MAGIC),
        HISTORY_ELAPSED_YEARS_FIX_VERSION,
    )
    payload[
        HISTORY_ELAPSED_YEARS_FIX_WRAPPER_OFFSET:
        HISTORY_ELAPSED_YEARS_FIX_WRAPPER_OFFSET + len(wrapper)
    ] = wrapper
    return bytes(payload)


def _history_elapsed_years_fix_hook(wrapper_va: int) -> bytes:
    """Return the near jump replacing only subcondition 16's comparison."""
    return (
        b"\xE9"
        + struct.pack("<i", wrapper_va - (HISTORY_ELAPSED_COMPARE_VA + 5))
        + b"\x90" * (len(HISTORY_ELAPSED_COMPARE_ORIGINAL) - 5)
    )


def _history_elapsed_years_fix_patch_info(data: bytes | bytearray) -> bool:
    """Return whether only HIST_EV subcondition 16 uses corrected elapsed years."""
    compare_offset = _history_elapsed_years_fix_compare_offset(data)
    current_hook = bytes(data[
        compare_offset:compare_offset + len(HISTORY_ELAPSED_COMPARE_ORIGINAL)
    ])
    section = find_patch_section(data)
    if current_hook == HISTORY_ELAPSED_COMPARE_ORIGINAL:
        if (
            section is not None
            and section.raw_size
            >= HISTORY_ELAPSED_YEARS_FIX_SLOT_OFFSET + HISTORY_ELAPSED_YEARS_FIX_SLOT_SIZE
            and bytes(data[
                section.raw_offset + HISTORY_ELAPSED_YEARS_FIX_SLOT_OFFSET:
                section.raw_offset + HISTORY_ELAPSED_YEARS_FIX_SLOT_OFFSET
                + len(HISTORY_ELAPSED_YEARS_FIX_MAGIC)
            ]) == HISTORY_ELAPSED_YEARS_FIX_MAGIC
        ):
            raise ValueError("발견 후 경과 연수 수정 코드가 남아 있지만 분기부가 원본 상태입니다.")
        return False
    if (
        section is None
        or section.raw_size
        < HISTORY_ELAPSED_YEARS_FIX_SLOT_OFFSET + HISTORY_ELAPSED_YEARS_FIX_SLOT_SIZE
        or section.virtual_size
        < HISTORY_ELAPSED_YEARS_FIX_SLOT_OFFSET + HISTORY_ELAPSED_YEARS_FIX_SLOT_SIZE
    ):
        raise ValueError("발견 후 경과 연수 수정 분기가 있으나 .patch 데이터를 찾지 못했습니다.")
    slot_offset, slot_va = section.slot(
        HISTORY_ELAPSED_YEARS_FIX_SLOT_OFFSET,
        HISTORY_ELAPSED_YEARS_FIX_SLOT_SIZE,
    )
    expected_payload = _build_history_elapsed_years_fix_payload(slot_va)
    expected_hook = _history_elapsed_years_fix_hook(
        slot_va + HISTORY_ELAPSED_YEARS_FIX_WRAPPER_OFFSET
    )
    if (
        current_hook != expected_hook
        or bytes(data[
            slot_offset:slot_offset + HISTORY_ELAPSED_YEARS_FIX_SLOT_SIZE
        ]) != expected_payload
    ):
        raise ValueError("HIST_EV 발견 후 경과 연수 수정 상태를 검증하지 못했습니다.")
    return True


def apply_history_elapsed_years_fix(data: bytearray, enabled: bool) -> bool:
    """Correct only HIST_EV subcondition 16, including its missing-record result."""
    current_enabled = _history_elapsed_years_fix_patch_info(data)
    if current_enabled == enabled:
        return False
    compare_offset = _history_elapsed_years_fix_compare_offset(data)
    if enabled:
        section, _created = ensure_patch_section(
            data, PATCH_SECTION_HISTORY_ELAPSED_YEARS_FIX_SIZE,
        )
        slot_offset, slot_va = section.slot(
            HISTORY_ELAPSED_YEARS_FIX_SLOT_OFFSET,
            HISTORY_ELAPSED_YEARS_FIX_SLOT_SIZE,
        )
        payload = _build_history_elapsed_years_fix_payload(slot_va)
        current_payload = bytes(data[
            slot_offset:slot_offset + HISTORY_ELAPSED_YEARS_FIX_SLOT_SIZE
        ])
        if any(current_payload) and current_payload != payload:
            raise ValueError("발견 후 경과 연수 수정용 .patch 슬롯이 다른 데이터로 사용 중입니다.")
        data[slot_offset:slot_offset + HISTORY_ELAPSED_YEARS_FIX_SLOT_SIZE] = payload
        data[
            compare_offset:compare_offset + len(HISTORY_ELAPSED_COMPARE_ORIGINAL)
        ] = _history_elapsed_years_fix_hook(
            slot_va + HISTORY_ELAPSED_YEARS_FIX_WRAPPER_OFFSET
        )
    else:
        section = find_patch_section(data)
        if section is None:
            raise ValueError("발견 후 경과 연수 수정의 복원 데이터를 찾지 못했습니다.")
        data[
            compare_offset:compare_offset + len(HISTORY_ELAPSED_COMPARE_ORIGINAL)
        ] = HISTORY_ELAPSED_COMPARE_ORIGINAL
        clear_slot(
            data, section,
            HISTORY_ELAPSED_YEARS_FIX_SLOT_OFFSET,
            HISTORY_ELAPSED_YEARS_FIX_SLOT_SIZE,
        )
    return True


def _save_slot_selector_hook(wrapper_va: int) -> bytes:
    """Return the save-command branch redirected to the selector wrapper."""
    return b"\xE9" + struct.pack(
        "<i", wrapper_va - (SAVE_SLOT_SELECTOR_HOOK_VA + 5),
    )


def _build_save_slot_selector_payload(slot_va: int) -> bytes:
    """Build the save-only ten-slot selector and its mutable date labels."""
    wrapper_va = slot_va + SAVE_SLOT_SELECTOR_WRAPPER_OFFSET
    # The wrapper obtains a full path via the game's helper and uses only
    # CreateFileA(GENERIC_READ, OPEN_EXISTING) + ReadFile for date labels.
    # The game's writer helper must not be called here because it can truncate
    # an existing save before returning a handle.
    wrapper = bytearray(bytes.fromhex(
        "60 83 EC 20 "              # pushad; reserve 32-byte read buffer
        "31 F6 "                    # xor esi, esi (slot index)
        "89 F7 C1 E7 05 "           # mov edi, esi; shl edi, 5
        "81 C7 11 11 11 11 "        # add edi, labels
        "C6 07 00 "                 # empty label by default
        "8B 04 B5 22 22 22 22 "     # mov eax, [save-name pointers + esi*4]
        "50 E8 00 00 00 00 83 C4 04 "  # full path helper
        "6A 00 68 80 00 00 00 6A 03 6A 00 6A 03 "
        "68 00 00 00 80 50 FF 15 44 F4 62 00 "  # CreateFileA read-only
        "83 F8 FF 74 6F "           # missing / inaccessible -> next slot
        "89 C3 8D 0C 24 "           # handle; local buffer starts at ESP
        "6A 00 8D 44 24 20 50 6A 1B 51 53 "
        "FF 15 74 F4 62 00 "        # ReadFile(handle, buffer, 0x1b, &count, 0)
        "85 C0 0F 84 19 01 00 00 "  # failed read -> close, then next slot
        "53 FF 15 24 F4 62 00 "     # CloseHandle
        "83 7C 24 1C 1B 75 43 "     # require full 0x1b-byte header
        "0F B7 44 24 15 "           # year at SAVEDATA + 0x15
        "3D 78 05 00 00 72 37 "     # 1400 <= year
        "3D 6C 07 00 00 77 30 "     # year <= 1900
        "0F B6 4C 24 19 83 F9 01 72 26 83 F9 0C 77 21 "
        "0F B6 54 24 1A 83 FA 01 72 17 83 FA 1F 77 12 "
        "52 51 50 "                 # day, month, year
        "68 33 33 33 33 57 "        # format, label
        "FF 15 94 F5 62 00 83 C4 14 "  # wsprintfA(label, format, ...)
        "46 83 FE 0A 0F 8C 4B FF FF FF "  # next slot
        "6A 00 6A 00 6A 01 6A 0B "
        "68 44 44 44 44 "           # ten saves plus an explicit cancel row
        "E8 00 00 00 00 83 C4 14 "
        "83 F8 0A 0F 83 83 00 00 00 "  # explicit cancel row / invalid selection
        "8B 14 85 22 22 22 22 "     # save filename by selection
        "89 15 78 87 56 00 "
        "8B 14 85 55 55 55 55 "     # temp filename by selection
        "89 15 7C 87 56 00 "
        "FF 35 78 87 56 00 "        # push current save filename
        "E8 00 00 00 00 83 C4 04 "  # make full path
        "6A 00 68 80 00 00 00 6A 03 6A 00 6A 03 "
        "68 00 00 00 80 50 FF 15 44 F4 62 00 "  # CreateFileA read-only
        "83 F8 FF 74 1B "           # absent -> save without overwrite prompt
        "50 FF 15 24 F4 62 00 "     # CloseHandle
        "68 B8 8C 56 00 6A 02 "     # existing game overwrite confirmation
        "E8 00 00 00 00 83 C4 08 "
        "83 F8 02 75 81 "           # no -> show slot list again
        "83 C4 20 61 "
        "E8 00 00 00 00 "           # original save serializer
        "C7 05 78 87 56 00 54 76 53 00 "  # restore default save path
        "C7 05 7C 87 56 00 44 76 53 00 "  # restore default temporary path
        "E9 00 00 00 00 "           # original success message
        "C7 05 78 87 56 00 54 76 53 00 "  # cancel also leaves load path unchanged
        "C7 05 7C 87 56 00 44 76 53 00 "
        "83 C4 20 61 "
        "E9 00 00 00 00"            # original cancel return
        "53 FF 15 24 F4 62 00 "     # failed ReadFile: close handle
        "E9 2C FF FF FF"            # then continue with next slot
    ))
    # These offsets are instruction-relative and intentionally kept explicit
    # so the generated payload can be regression-tested without an assembler.
    for offset, target in (
        (0x1C, SAVE_SLOT_SELECTOR_PATH_ROUTINE_VA),
        (0xC8, SAVE_SLOT_SELECTOR_MENU_ROUTINE_VA),
        (0xF9, SAVE_SLOT_SELECTOR_PATH_ROUTINE_VA),
        (0x12D, SAVE_SLOT_SELECTOR_CONFIRM_ROUTINE_VA),
        (0x13E, SAVE_SLOT_SELECTOR_SAVE_ROUTINE_VA),
        (0x157, SAVE_SLOT_SELECTOR_SUCCESS_VA),
        (0x174, SAVE_SLOT_SELECTOR_CANCEL_VA),
    ):
        struct.pack_into("<i", wrapper, offset + 1, target - (wrapper_va + offset + 5))
    for offset, value in (
        (0x0D, slot_va + SAVE_SLOT_SELECTOR_LABELS_OFFSET),
        (0x17, slot_va + SAVE_SLOT_SELECTOR_SAVE_NAME_POINTERS_OFFSET),
        (0xA3, slot_va + SAVE_SLOT_SELECTOR_DATE_FORMAT_OFFSET),
        (0xC4, slot_va + SAVE_SLOT_SELECTOR_MENU_ITEMS_OFFSET),
        (0xDC, slot_va + SAVE_SLOT_SELECTOR_SAVE_NAME_POINTERS_OFFSET),
        (0xE9, slot_va + SAVE_SLOT_SELECTOR_TMP_NAME_POINTERS_OFFSET),
    ):
        struct.pack_into("<I", wrapper, offset, value)
    if SAVE_SLOT_SELECTOR_WRAPPER_OFFSET + len(wrapper) > SAVE_SLOT_SELECTOR_SLOT_SIZE:
        raise AssertionError("저장 슬롯 선택 래퍼가 예약 공간을 초과했습니다.")

    payload = bytearray(SAVE_SLOT_SELECTOR_SLOT_SIZE)
    payload[:len(SAVE_SLOT_SELECTOR_MAGIC)] = SAVE_SLOT_SELECTOR_MAGIC
    struct.pack_into("<I", payload, len(SAVE_SLOT_SELECTOR_MAGIC), SAVE_SLOT_SELECTOR_VERSION)
    payload[
        SAVE_SLOT_SELECTOR_WRAPPER_OFFSET:
        SAVE_SLOT_SELECTOR_WRAPPER_OFFSET + len(wrapper)
    ] = wrapper
    for index in range(SAVE_SLOT_SELECTOR_MENU_COUNT):
        label_va = (
            slot_va + SAVE_SLOT_SELECTOR_LABELS_OFFSET
            + index * SAVE_SLOT_SELECTOR_LABEL_STRIDE
        )
        struct.pack_into(
            "<III", payload,
            SAVE_SLOT_SELECTOR_MENU_ITEMS_OFFSET + index * 12,
            label_va, 1, 1,
        )
    for index in range(SAVE_SLOT_SELECTOR_SAVE_COUNT):
        name_offset = (
            SAVE_SLOT_SELECTOR_SAVE_NAMES_OFFSET
            + index * SAVE_SLOT_SELECTOR_LABEL_STRIDE
        )
        struct.pack_into(
            "<I", payload,
            SAVE_SLOT_SELECTOR_SAVE_NAME_POINTERS_OFFSET + index * 4,
            slot_va + name_offset,
        )
        struct.pack_into(
            "<I", payload,
            SAVE_SLOT_SELECTOR_TMP_NAME_POINTERS_OFFSET + index * 4,
            SAVE_SLOT_SELECTOR_DEFAULT_TMP_NAME_VA,
        )
        slot_number = index + 1
        payload[name_offset:name_offset + SAVE_SLOT_SELECTOR_LABEL_STRIDE] = (
            f"C:SAVEDATA{slot_number:02d}.CDS".encode("ascii") + b"\0"
        ).ljust(SAVE_SLOT_SELECTOR_LABEL_STRIDE, b"\0")
    cancel_label_offset = (
        SAVE_SLOT_SELECTOR_LABELS_OFFSET
        + SAVE_SLOT_SELECTOR_SAVE_COUNT * SAVE_SLOT_SELECTOR_LABEL_STRIDE
    )
    payload[cancel_label_offset:cancel_label_offset + SAVE_SLOT_SELECTOR_LABEL_STRIDE] = (
        "취소".encode("cp949") + b"\0"
    ).ljust(SAVE_SLOT_SELECTOR_LABEL_STRIDE, b"\0")
    date_format = "%d년 %d월 %d일".encode("cp949") + b"\0"
    payload[
        SAVE_SLOT_SELECTOR_DATE_FORMAT_OFFSET:
        SAVE_SLOT_SELECTOR_DATE_FORMAT_OFFSET + len(date_format)
    ] = date_format
    return bytes(payload)


def _save_slot_selector_hook_offset(data: bytes | bytearray) -> int:
    """Return the file offset of the original save-handler entry point."""
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_offset_from_rva(
            SAVE_SLOT_SELECTOR_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase
        )
    finally:
        pe.close()


def _is_legacy_save_slot_selector_patch(
    data: bytes | bytearray,
    section=None,
    hook_offset: int | None = None,
) -> bool:
    """Recognize earlier selector payloads so they can be upgraded."""
    if section is None:
        section = find_patch_section(data)
    if section is None or section.raw_size < (
        SAVE_SLOT_SELECTOR_SLOT_OFFSET + SAVE_SLOT_SELECTOR_SLOT_SIZE
    ):
        return False
    magic = bytes(data[
        section.raw_offset + SAVE_SLOT_SELECTOR_SLOT_OFFSET:
        section.raw_offset + SAVE_SLOT_SELECTOR_SLOT_OFFSET + len(SAVE_SLOT_SELECTOR_LEGACY_MAGIC)
    ])
    if magic not in (
        SAVE_SLOT_SELECTOR_LEGACY_MAGIC,
        SAVE_SLOT_SELECTOR_PREVIOUS_MAGIC,
    ):
        return False
    if hook_offset is None:
        hook_offset = _save_slot_selector_hook_offset(data)
    hook = bytes(data[hook_offset:hook_offset + len(SAVE_SLOT_SELECTOR_HOOK_ORIGINAL)])
    if hook[:1] != b"\xE9":
        return False
    destination = SAVE_SLOT_SELECTOR_HOOK_VA + 5 + struct.unpack_from("<i", hook, 1)[0]
    return destination == section.slot(
        SAVE_SLOT_SELECTOR_SLOT_OFFSET, SAVE_SLOT_SELECTOR_SLOT_SIZE,
    )[1] + SAVE_SLOT_SELECTOR_WRAPPER_OFFSET


def _save_slot_selector_patch_info(data: bytes | bytearray) -> bool:
    """Return whether the verified v3 save-only selector is installed."""
    hook_offset = _save_slot_selector_hook_offset(data)
    current_hook = bytes(data[hook_offset:hook_offset + len(SAVE_SLOT_SELECTOR_HOOK_ORIGINAL)])
    section = find_patch_section(data)
    if _is_legacy_save_slot_selector_patch(data, section, hook_offset):
        return False
    if current_hook == SAVE_SLOT_SELECTOR_HOOK_ORIGINAL:
        if section is not None and section.raw_size >= (
            SAVE_SLOT_SELECTOR_SLOT_OFFSET + SAVE_SLOT_SELECTOR_SLOT_SIZE
        ) and bytes(data[
            section.raw_offset + SAVE_SLOT_SELECTOR_SLOT_OFFSET:
            section.raw_offset + SAVE_SLOT_SELECTOR_SLOT_OFFSET + len(SAVE_SLOT_SELECTOR_MAGIC)
        ]) == SAVE_SLOT_SELECTOR_MAGIC:
            raise ValueError("저장 슬롯 선택 코드가 남아 있지만 저장 분기부가 원본 상태입니다.")
        return False
    if section is None or section.raw_size < (
        SAVE_SLOT_SELECTOR_SLOT_OFFSET + SAVE_SLOT_SELECTOR_SLOT_SIZE
    ) or section.virtual_size < (
        SAVE_SLOT_SELECTOR_SLOT_OFFSET + SAVE_SLOT_SELECTOR_SLOT_SIZE
    ):
        raise ValueError("저장 슬롯 선택 분기가 있으나 .patch 데이터를 찾지 못했습니다.")
    slot_offset, slot_va = section.slot(
        SAVE_SLOT_SELECTOR_SLOT_OFFSET, SAVE_SLOT_SELECTOR_SLOT_SIZE,
    )
    expected_payload = _build_save_slot_selector_payload(slot_va)
    if (
        current_hook != _save_slot_selector_hook(
            slot_va + SAVE_SLOT_SELECTOR_WRAPPER_OFFSET,
        )
        or bytes(data[slot_offset:slot_offset + SAVE_SLOT_SELECTOR_SLOT_SIZE])
        != expected_payload
    ):
        raise ValueError("저장 슬롯 선택 패치 상태를 검증하지 못했습니다.")
    return True


def apply_save_slot_selector_patch(data: bytearray, enabled: bool) -> bool:
    """Install or remove the native ten-slot save destination selector."""
    hook_offset = _save_slot_selector_hook_offset(data)
    section = find_patch_section(data)
    if _is_legacy_save_slot_selector_patch(data, section, hook_offset):
        assert section is not None
        data[hook_offset:hook_offset + len(SAVE_SLOT_SELECTOR_HOOK_ORIGINAL)] = (
            SAVE_SLOT_SELECTOR_HOOK_ORIGINAL
        )
        clear_slot(
            data, section, SAVE_SLOT_SELECTOR_SLOT_OFFSET, SAVE_SLOT_SELECTOR_SLOT_SIZE,
        )
        if not read_load_slot_selector_patch_state(data):
            characteristics = struct.unpack_from("<I", data, section.header_offset + 36)[0]
            struct.pack_into("<I", data, section.header_offset + 36, characteristics & ~0x80000000)
        if not enabled:
            return True
    current_enabled = _save_slot_selector_patch_info(data)
    if current_enabled == enabled:
        return False
    if enabled:
        section, _created = ensure_patch_section(
            data, PATCH_SECTION_SAVE_SLOT_SELECTOR_SIZE,
        )
        slot_offset, slot_va = section.slot(
            SAVE_SLOT_SELECTOR_SLOT_OFFSET, SAVE_SLOT_SELECTOR_SLOT_SIZE,
        )
        payload = _build_save_slot_selector_payload(slot_va)
        existing = bytes(data[slot_offset:slot_offset + SAVE_SLOT_SELECTOR_SLOT_SIZE])
        if any(existing) and existing != payload:
            raise ValueError("저장 슬롯 선택용 .patch 슬롯이 다른 데이터로 사용 중입니다.")
        # This payload refreshes the date labels at runtime.
        characteristics = struct.unpack_from("<I", data, section.header_offset + 36)[0]
        struct.pack_into("<I", data, section.header_offset + 36, characteristics | 0x80000000)
        data[slot_offset:slot_offset + SAVE_SLOT_SELECTOR_SLOT_SIZE] = payload
        data[hook_offset:hook_offset + len(SAVE_SLOT_SELECTOR_HOOK_ORIGINAL)] = (
            _save_slot_selector_hook(slot_va + SAVE_SLOT_SELECTOR_WRAPPER_OFFSET)
        )
    else:
        section = find_patch_section(data)
        if section is None:
            raise ValueError("저장 슬롯 선택의 복원 데이터를 찾지 못했습니다.")
        data[hook_offset:hook_offset + len(SAVE_SLOT_SELECTOR_HOOK_ORIGINAL)] = (
            SAVE_SLOT_SELECTOR_HOOK_ORIGINAL
        )
        clear_slot(
            data, section, SAVE_SLOT_SELECTOR_SLOT_OFFSET, SAVE_SLOT_SELECTOR_SLOT_SIZE,
        )
        if not read_load_slot_selector_patch_state(data):
            characteristics = struct.unpack_from("<I", data, section.header_offset + 36)[0]
            struct.pack_into("<I", data, section.header_offset + 36, characteristics & ~0x80000000)
    return True


def read_save_slot_selector_patch_state(data: bytes | bytearray) -> bool:
    """Public state reader for the GUI's ten-slot save checkbox."""
    return _save_slot_selector_patch_info(data) or _is_legacy_save_slot_selector_patch(data)


def _load_slot_selector_hook(wrapper_va: int) -> bytes:
    """Return a six-byte entry hook which preserves the original prologue size."""
    return b"\xE9" + struct.pack(
        "<i", wrapper_va - (LOAD_SLOT_SELECTOR_HOOK_VA + 5),
    ) + b"\x90"


def _load_slot_selector_command_hook(wrapper_va: int) -> bytes:
    """Return the five-byte main-menu load-command redirection."""
    return b"\xE9" + struct.pack(
        "<i", wrapper_va - (LOAD_SLOT_SELECTOR_COMMAND_HOOK_VA + 5),
    )


def _load_slot_selector_title_hook(wrapper_va: int) -> bytes:
    """Return the title-screen load-command redirection."""
    return b"\xE9" + struct.pack(
        "<i", wrapper_va - (LOAD_SLOT_SELECTOR_TITLE_HOOK_VA + 5),
    )


def _load_slot_selector_session_file_hook(wrapper_va: int) -> bytes:
    """Return the title-session file initializer redirection."""
    return b"\xE9" + struct.pack(
        "<i", wrapper_va - (LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_VA + 5),
    )


def _load_slot_selector_hook_offset(data: bytes | bytearray) -> int:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_offset_from_rva(
            LOAD_SLOT_SELECTOR_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase,
        )
    finally:
        pe.close()


def _load_slot_selector_command_hook_offset(data: bytes | bytearray) -> int:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_offset_from_rva(
            LOAD_SLOT_SELECTOR_COMMAND_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase,
        )
    finally:
        pe.close()


def _load_slot_selector_title_hook_offset(data: bytes | bytearray) -> int:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_offset_from_rva(
            LOAD_SLOT_SELECTOR_TITLE_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase,
        )
    finally:
        pe.close()


def _load_slot_selector_session_file_hook_offset(data: bytes | bytearray) -> int:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_offset_from_rva(
            LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase,
        )
    finally:
        pe.close()


def _build_load_slot_selector_command_wrapper(slot_va: int) -> bytes:
    """Open the selector directly from the in-game load command.

    The original command prepares the load only after its confirmation.
    Select first for the same cancellation behavior; a cancelled selection
    simply returns to the active in-game menu without changing UI state.
    """
    wrapper_va = slot_va + LOAD_SLOT_SELECTOR_COMMAND_WRAPPER_OFFSET
    wrapper = bytearray(bytes.fromhex(
        "E8 00 00 00 00 "  # selector
        "85 C0 74 1B "     # cancellation: return to the active menu
        "E8 00 00 00 00 "  # prepare save-directory path
        "E8 00 00 00 00 "  # original loader
        "80 0D 18 4D 5A 00 10 "
        "B9 18 4D 5A 00 "
        "E9 00 00 00 00 "
        "C3"
    ))
    for offset, target in (
        (0x00, slot_va + LOAD_SLOT_SELECTOR_WRAPPER_OFFSET),
        (0x09, 0x478550),
        (0x0E, LOAD_SLOT_SELECTOR_HOOK_VA),
        (0x1F, LOAD_SLOT_SELECTOR_COMMAND_CONTINUATION_VA),
    ):
        struct.pack_into("<i", wrapper, offset + 1, target - (wrapper_va + offset + 5))
    return bytes(wrapper)


def _build_load_slot_selector_confirmation_wrapper(slot_va: int) -> bytes:
    """Confirm a selected load slot and return to its menu on ``No``.

    The selected row is kept in dedicated .patch data before this function is
    entered.  It never returns: ``Yes`` stores the selected path and resumes
    the ordinary file check; ``No`` returns to the selector.
    """
    wrapper_va = slot_va + LOAD_SLOT_SELECTOR_CONFIRM_WRAPPER_OFFSET
    wrapper = bytearray(bytes.fromhex(
        "68 11 11 11 11 "  # original \"load data?\" prompt
        "6A 02 E8 00 00 00 00 83 C4 08 83 F8 02 75 18 "
        "8B 15 11 11 11 11 8B 14 95 22 22 22 22 "
        "89 15 78 87 56 00 E9 00 00 00 00 "
        "E9 00 00 00 00"
    ))
    for offset, target in (
        (0x07, SAVE_SLOT_SELECTOR_CONFIRM_ROUTINE_VA),
        (0x27, slot_va + LOAD_SLOT_SELECTOR_WRAPPER_OFFSET + 0xDE),
        (0x2C, slot_va + LOAD_SLOT_SELECTOR_WRAPPER_OFFSET + 0xBB),
    ):
        struct.pack_into("<i", wrapper, offset + 1, target - (wrapper_va + offset + 5))
    struct.pack_into("<I", wrapper, 1, LOAD_SLOT_SELECTOR_CONFIRM_PROMPT_VA)
    struct.pack_into(
        "<I", wrapper, 0x16,
        slot_va + LOAD_SLOT_SELECTOR_SELECTED_INDEX_OFFSET,
    )
    struct.pack_into(
        "<I", wrapper, 0x1D,
        slot_va + LOAD_SLOT_SELECTOR_SAVE_NAME_POINTERS_OFFSET,
    )
    return bytes(wrapper)


def _build_load_slot_selector_confirmation_trampoline(slot_va: int) -> bytes:
    """Persist the selected row before the game replaces its dialog stack."""
    trampoline_va = slot_va + LOAD_SLOT_SELECTOR_CONFIRM_TRAMPOLINE_OFFSET
    wrapper_va = slot_va + LOAD_SLOT_SELECTOR_WRAPPER_OFFSET
    trampoline = bytearray(bytes.fromhex("A3 11 11 11 11 E9 00 00 00 00"))
    struct.pack_into("<I", trampoline, 1, slot_va + LOAD_SLOT_SELECTOR_SELECTED_INDEX_OFFSET)
    struct.pack_into(
        "<i", trampoline, 6,
        (slot_va + LOAD_SLOT_SELECTOR_CONFIRM_WRAPPER_OFFSET) - (trampoline_va + 10),
    )
    return bytes(trampoline)


def _build_load_slot_selector_title_wrapper(slot_va: int) -> bytes:
    """Prepare title state, select a slot, then run its native loader call."""
    wrapper_va = slot_va + LOAD_SLOT_SELECTOR_TITLE_WRAPPER_OFFSET
    wrapper = bytearray(bytes.fromhex(
        "E8 00 00 00 00 "  # original title game-state preparation
        "E8 00 00 00 00 "  # selector
        "85 C0 "           # selected?
        "0F 84 00 00 00 00 "  # no: original callback cancellation path
        "E9 00 00 00 00"   # yes: native title loader call at 45ED40
    ))
    struct.pack_into(
        "<i", wrapper, 1,
        0x478550 - (wrapper_va + 5),
    )
    struct.pack_into(
        "<i", wrapper, 6,
        slot_va + LOAD_SLOT_SELECTOR_WRAPPER_OFFSET - (wrapper_va + 10),
    )
    struct.pack_into(
        "<i", wrapper, 14,
        LOAD_SLOT_SELECTOR_TITLE_CANCEL_VA - (wrapper_va + 18),
    )
    struct.pack_into(
        "<i", wrapper, 19,
        LOAD_SLOT_SELECTOR_TITLE_LOADER_VA - (wrapper_va + 23),
    )
    return bytes(wrapper)


def _build_load_slot_selector_session_file_wrapper(slot_va: int) -> bytes:
    """Open the selected save while retaining the game's shared temp file."""
    wrapper_va = slot_va + LOAD_SLOT_SELECTOR_SESSION_FILE_WRAPPER_OFFSET
    wrapper = bytearray(bytes.fromhex(
        "FF 35 11 11 11 11 E8 00 00 00 00 83 C4 04 50 "
        "E8 00 00 00 00 83 C4 04 "
        "68 44 76 53 00 E8 00 00 00 00 83 C4 04 50 "
        "E8 00 00 00 00 83 C4 04 E9 00 00 00 00"
    ))
    struct.pack_into("<I", wrapper, 2, SAVE_SLOT_SELECTOR_SAVE_NAME_POINTER_VA)
    for offset, target in (
        (0x06, LOAD_SLOT_SELECTOR_PATH_ROUTINE_VA),
        (0x0F, 0x4B7B15),
        (0x1C, LOAD_SLOT_SELECTOR_PATH_ROUTINE_VA),
        (0x25, 0x4B7B15),
        (0x2D, LOAD_SLOT_SELECTOR_SESSION_FILE_CONTINUATION_VA),
    ):
        struct.pack_into("<i", wrapper, offset + 1, target - (wrapper_va + offset + 5))
    return bytes(wrapper)


def _build_load_slot_selector_post_selection_wrapper(slot_va: int) -> bytes:
    """Retain the game's temporary file after the selected path is installed."""
    wrapper_va = slot_va + LOAD_SLOT_SELECTOR_POST_SELECTION_WRAPPER_OFFSET
    wrapper = bytearray(bytes.fromhex(
        "C7 05 7C 87 56 00 44 76 53 00 "
        "E9 00 00 00 00"
    ))
    struct.pack_into(
        "<i", wrapper, 0x0B,
        (slot_va + LOAD_SLOT_SELECTOR_WRAPPER_OFFSET + 0x126) - (wrapper_va + 0x0F),
    )
    return bytes(wrapper)


def _build_load_slot_selector_payload(slot_va: int) -> bytes:
    """Build the load selector with read-only date and existence checks."""
    wrapper_va = slot_va + LOAD_SLOT_SELECTOR_WRAPPER_OFFSET
    wrapper = bytearray(bytes.fromhex(
        "60 83 EC 20 31 F6 "
        "89 F7 C1 E7 05 81 C7 11 11 11 11 C6 07 00 "
        "8B 04 B5 22 22 22 22 50 E8 00 00 00 00 83 C4 04 "
        "6A 00 68 80 00 00 00 6A 03 6A 00 6A 03 "
        "68 00 00 00 80 50 FF 15 44 F4 62 00 "
        "83 F8 FF 74 6F 89 C3 8D 0C 24 "
        "6A 00 8D 44 24 20 50 6A 1B 51 53 FF 15 74 F4 62 00 "
        "85 C0 0F 84 DE 00 00 00 53 FF 15 24 F4 62 00 "
        "83 7C 24 1C 1B 75 43 "
        "0F B7 44 24 15 3D 78 05 00 00 72 37 3D 6C 07 00 00 77 30 "
        "0F B6 4C 24 19 83 F9 01 72 26 83 F9 0C 77 21 "
        "0F B6 54 24 1A 83 FA 01 72 17 83 FA 1F 77 12 "
        "52 51 50 68 33 33 33 33 57 FF 15 94 F5 62 00 83 C4 14 "
        "46 83 FE 0A 0F 8C 4B FF FF FF "
        "6A 00 6A 00 6A 01 6A 0B 68 44 44 44 44 E8 00 00 00 00 83 C4 14 "
        "83 F8 0A 73 60 89 C5 8B 14 AD 22 22 22 22 52 "
        "E8 00 00 00 00 83 C4 04 "
        "6A 00 68 80 00 00 00 6A 03 6A 00 6A 03 "
        "68 00 00 00 80 50 FF 15 44 F4 62 00 "
        "83 F8 FF 74 B6 50 FF 15 24 F4 62 00 "
        "8B 14 AD 22 22 22 22 89 15 78 87 56 00 "
        "8B 14 AD 55 55 55 55 89 15 7C 87 56 00 "
        "83 C4 20 61 81 EC 08 01 00 00 E9 00 00 00 00 "
        "31 C0 83 C4 20 61 31 C0 C3 "
        "53 FF 15 24 F4 62 00 E9 67 FF FF FF"
    ))
    for offset, target in (
        (0x1C, LOAD_SLOT_SELECTOR_PATH_ROUTINE_VA),
        (0xC8, LOAD_SLOT_SELECTOR_MENU_ROUTINE_VA),
        (0xDF, LOAD_SLOT_SELECTOR_PATH_ROUTINE_VA),
    ):
        struct.pack_into("<i", wrapper, offset + 1, target - (wrapper_va + offset + 5))
    for offset, value in (
        (0x0D, slot_va + LOAD_SLOT_SELECTOR_LABELS_OFFSET),
        (0x17, slot_va + LOAD_SLOT_SELECTOR_SAVE_NAME_POINTERS_OFFSET),
        (0xA3, slot_va + LOAD_SLOT_SELECTOR_DATE_FORMAT_OFFSET),
        (0xC4, slot_va + LOAD_SLOT_SELECTOR_MENU_ITEMS_OFFSET),
        (0xDA, slot_va + LOAD_SLOT_SELECTOR_SAVE_NAME_POINTERS_OFFSET),
        (0x10F, slot_va + LOAD_SLOT_SELECTOR_SAVE_NAME_POINTERS_OFFSET),
        (0x11C, slot_va + LOAD_SLOT_SELECTOR_TMP_NAME_POINTERS_OFFSET),
    ):
        struct.pack_into("<I", wrapper, offset, value)
    confirmation_wrapper_va = slot_va + LOAD_SLOT_SELECTOR_CONFIRM_WRAPPER_OFFSET
    # The confirmation dialog clobbers both EBP and stack locals.  Save the
    # row through a trampoline in .patch data, then enter that dialog wrapper.
    trampoline_va = slot_va + LOAD_SLOT_SELECTOR_CONFIRM_TRAMPOLINE_OFFSET
    wrapper[0xD5] = 0xE9
    struct.pack_into(
        "<i", wrapper, 0xD6,
        trampoline_va - (wrapper_va + 0xDA),
    )
    post_selection_wrapper_va = slot_va + LOAD_SLOT_SELECTOR_POST_SELECTION_WRAPPER_OFFSET
    wrapper[0x10C] = 0xE9
    struct.pack_into(
        "<i", wrapper, 0x10D,
        post_selection_wrapper_va - (wrapper_va + 0x111),
    )
    # A chosen slot now returns success to its specific caller.  The title and
    # in-game command wrappers then invoke the untouched original loader.
    wrapper[0x126:0x135] = bytes.fromhex(
        "83 C4 20 61 B8 01 00 00 00 C3 90 90 90 90 90"
    )
    if LOAD_SLOT_SELECTOR_WRAPPER_OFFSET + len(wrapper) > LOAD_SLOT_SELECTOR_SLOT_SIZE:
        raise AssertionError("불러오기 슬롯 선택 래퍼가 예약 공간을 초과했습니다.")

    payload = bytearray(LOAD_SLOT_SELECTOR_SLOT_SIZE)
    payload[:len(LOAD_SLOT_SELECTOR_MAGIC)] = LOAD_SLOT_SELECTOR_MAGIC
    struct.pack_into("<I", payload, len(LOAD_SLOT_SELECTOR_MAGIC), LOAD_SLOT_SELECTOR_VERSION)
    payload[
        LOAD_SLOT_SELECTOR_WRAPPER_OFFSET:
        LOAD_SLOT_SELECTOR_WRAPPER_OFFSET + len(wrapper)
    ] = wrapper
    for index in range(LOAD_SLOT_SELECTOR_MENU_COUNT):
        label_va = (
            slot_va + LOAD_SLOT_SELECTOR_LABELS_OFFSET
            + index * LOAD_SLOT_SELECTOR_LABEL_STRIDE
        )
        struct.pack_into(
            "<III", payload,
            LOAD_SLOT_SELECTOR_MENU_ITEMS_OFFSET + index * 12,
            label_va, 1, 1,
        )
    for index in range(LOAD_SLOT_SELECTOR_SAVE_COUNT):
        name_offset = (
            LOAD_SLOT_SELECTOR_SAVE_NAMES_OFFSET
            + index * LOAD_SLOT_SELECTOR_LABEL_STRIDE
        )
        tmp_offset = (
            LOAD_SLOT_SELECTOR_TMP_NAMES_OFFSET
            + index * LOAD_SLOT_SELECTOR_LABEL_STRIDE
        )
        struct.pack_into(
            "<I", payload,
            LOAD_SLOT_SELECTOR_SAVE_NAME_POINTERS_OFFSET + index * 4,
            slot_va + name_offset,
        )
        struct.pack_into(
            "<I", payload,
            LOAD_SLOT_SELECTOR_TMP_NAME_POINTERS_OFFSET + index * 4,
            slot_va + tmp_offset,
        )
        slot_number = index + 1
        payload[name_offset:name_offset + LOAD_SLOT_SELECTOR_LABEL_STRIDE] = (
            f"C:SAVEDATA{slot_number:02d}.CDS".encode("ascii") + b"\0"
        ).ljust(LOAD_SLOT_SELECTOR_LABEL_STRIDE, b"\0")
        payload[tmp_offset:tmp_offset + LOAD_SLOT_SELECTOR_LABEL_STRIDE] = (
            f"C:SAVEDATA{slot_number:02d}.TMP".encode("ascii") + b"\0"
        ).ljust(LOAD_SLOT_SELECTOR_LABEL_STRIDE, b"\0")
    cancel_offset = (
        LOAD_SLOT_SELECTOR_LABELS_OFFSET
        + LOAD_SLOT_SELECTOR_SAVE_COUNT * LOAD_SLOT_SELECTOR_LABEL_STRIDE
    )
    payload[cancel_offset:cancel_offset + LOAD_SLOT_SELECTOR_LABEL_STRIDE] = (
        "취소".encode("cp949") + b"\0"
    ).ljust(LOAD_SLOT_SELECTOR_LABEL_STRIDE, b"\0")
    date_format = "%d년 %d월 %d일".encode("cp949") + b"\0"
    payload[
        LOAD_SLOT_SELECTOR_DATE_FORMAT_OFFSET:
        LOAD_SLOT_SELECTOR_DATE_FORMAT_OFFSET + len(date_format)
    ] = date_format
    command_wrapper = _build_load_slot_selector_command_wrapper(slot_va)
    command_wrapper_offset = LOAD_SLOT_SELECTOR_COMMAND_WRAPPER_OFFSET
    payload[
        command_wrapper_offset:command_wrapper_offset + len(command_wrapper)
    ] = command_wrapper
    confirmation_wrapper = _build_load_slot_selector_confirmation_wrapper(slot_va)
    confirmation_wrapper_offset = LOAD_SLOT_SELECTOR_CONFIRM_WRAPPER_OFFSET
    payload[
        confirmation_wrapper_offset:
        confirmation_wrapper_offset + len(confirmation_wrapper)
    ] = confirmation_wrapper
    title_wrapper = _build_load_slot_selector_title_wrapper(slot_va)
    title_wrapper_offset = LOAD_SLOT_SELECTOR_TITLE_WRAPPER_OFFSET
    payload[title_wrapper_offset:title_wrapper_offset + len(title_wrapper)] = title_wrapper
    session_file_wrapper = _build_load_slot_selector_session_file_wrapper(slot_va)
    session_file_wrapper_offset = LOAD_SLOT_SELECTOR_SESSION_FILE_WRAPPER_OFFSET
    payload[
        session_file_wrapper_offset:
        session_file_wrapper_offset + len(session_file_wrapper)
    ] = session_file_wrapper
    confirmation_trampoline = _build_load_slot_selector_confirmation_trampoline(slot_va)
    payload[
        LOAD_SLOT_SELECTOR_CONFIRM_TRAMPOLINE_OFFSET:
        LOAD_SLOT_SELECTOR_CONFIRM_TRAMPOLINE_OFFSET + len(confirmation_trampoline)
    ] = confirmation_trampoline
    post_selection_wrapper = _build_load_slot_selector_post_selection_wrapper(slot_va)
    post_selection_wrapper_offset = LOAD_SLOT_SELECTOR_POST_SELECTION_WRAPPER_OFFSET
    payload[
        post_selection_wrapper_offset:
        post_selection_wrapper_offset + len(post_selection_wrapper)
    ] = post_selection_wrapper
    return bytes(payload)


def _is_legacy_load_slot_selector_patch(data: bytes | bytearray) -> bool:
    """Recognize v1 so an existing selector upgrades without manual removal."""
    section = find_patch_section(data)
    if section is None or section.raw_size < (
        LOAD_SLOT_SELECTOR_SLOT_OFFSET + LOAD_SLOT_SELECTOR_SLOT_SIZE
    ):
        return False
    hook_offset = _load_slot_selector_hook_offset(data)
    command_hook_offset = _load_slot_selector_command_hook_offset(data)
    title_hook_offset = _load_slot_selector_title_hook_offset(data)
    session_file_hook_offset = _load_slot_selector_session_file_hook_offset(data)
    hook = bytes(data[hook_offset:hook_offset + len(LOAD_SLOT_SELECTOR_HOOK_ORIGINAL)])
    command_hook = bytes(data[
        command_hook_offset:
        command_hook_offset + len(LOAD_SLOT_SELECTOR_COMMAND_HOOK_ORIGINAL)
    ])
    title_hook = bytes(data[
        title_hook_offset:
        title_hook_offset + len(LOAD_SLOT_SELECTOR_TITLE_HOOK_ORIGINAL)
    ])
    session_file_hook = bytes(data[
        session_file_hook_offset:
        session_file_hook_offset + len(LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_ORIGINAL)
    ])
    magic_offset = section.raw_offset + LOAD_SLOT_SELECTOR_SLOT_OFFSET
    magic = bytes(data[magic_offset:magic_offset + len(LOAD_SLOT_SELECTOR_MAGIC)])
    version = struct.unpack_from("<I", data, magic_offset + len(LOAD_SLOT_SELECTOR_MAGIC))[0]
    if (magic, version) in (
        (LOAD_SLOT_SELECTOR_LEGACY_MAGIC, 1),
        (LOAD_SLOT_SELECTOR_INTERMEDIATE_MAGIC, 2),
        (LOAD_SLOT_SELECTOR_OLDER_MAGIC, 3),
        (LOAD_SLOT_SELECTOR_PREVIOUS_MAGIC, 4),
        (LOAD_SLOT_SELECTOR_LATEST_LEGACY_MAGIC, 5),
        (LOAD_SLOT_SELECTOR_NEWEST_LEGACY_MAGIC, 6),
        (LOAD_SLOT_SELECTOR_FINAL_LEGACY_MAGIC, 7),
        (LOAD_SLOT_SELECTOR_SESSION_FILE_LEGACY_MAGIC, 8),
        (LOAD_SLOT_SELECTOR_TITLE_CONTEXT_LEGACY_MAGIC, 9),
        (LOAD_SLOT_SELECTOR_TITLE_ORDER_LEGACY_MAGIC, 10),
        (LOAD_SLOT_SELECTOR_TITLE_CITY_ORDER_LEGACY_MAGIC, 11),
        (LOAD_SLOT_SELECTOR_TEMP_SLOT_LEGACY_MAGIC, 12),
        (LOAD_SLOT_SELECTOR_POST_SELECTION_LEGACY_MAGIC, 13),
        (LOAD_SLOT_SELECTOR_TITLE_PREPARATION_ORDER_LEGACY_MAGIC, 14),
        (LOAD_SLOT_SELECTOR_TITLE_SELECTOR_CALL_LEGACY_MAGIC, 15),
        (LOAD_SLOT_SELECTOR_CONFIRM_STACK_INDEX_LEGACY_MAGIC, 16),
        (LOAD_SLOT_SELECTOR_TITLE_LOADER_SKIP_LEGACY_MAGIC, 17),
        (LOAD_SLOT_SELECTOR_DIALOG_REGISTER_LEGACY_MAGIC, 18),
        (LOAD_SLOT_SELECTOR_DIALOG_STACK_LEGACY_MAGIC, 19),
        (LOAD_SLOT_SELECTOR_COMMAND_PREPARATION_LEGACY_MAGIC, 20),
    ):
        return (
            hook != LOAD_SLOT_SELECTOR_HOOK_ORIGINAL
            or command_hook != LOAD_SLOT_SELECTOR_COMMAND_HOOK_ORIGINAL
            or title_hook != LOAD_SLOT_SELECTOR_TITLE_HOOK_ORIGINAL
            or session_file_hook != LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_ORIGINAL
        )
    return False


def _load_slot_selector_patch_info(data: bytes | bytearray) -> bool:
    hook_offset = _load_slot_selector_hook_offset(data)
    command_hook_offset = _load_slot_selector_command_hook_offset(data)
    title_hook_offset = _load_slot_selector_title_hook_offset(data)
    session_file_hook_offset = _load_slot_selector_session_file_hook_offset(data)
    hook = bytes(data[hook_offset:hook_offset + len(LOAD_SLOT_SELECTOR_HOOK_ORIGINAL)])
    command_hook = bytes(data[
        command_hook_offset:
        command_hook_offset + len(LOAD_SLOT_SELECTOR_COMMAND_HOOK_ORIGINAL)
    ])
    title_hook = bytes(data[
        title_hook_offset:
        title_hook_offset + len(LOAD_SLOT_SELECTOR_TITLE_HOOK_ORIGINAL)
    ])
    session_file_hook = bytes(data[
        session_file_hook_offset:
        session_file_hook_offset + len(LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_ORIGINAL)
    ])
    section = find_patch_section(data)
    if (
        hook == LOAD_SLOT_SELECTOR_HOOK_ORIGINAL
        and command_hook == LOAD_SLOT_SELECTOR_COMMAND_HOOK_ORIGINAL
        and title_hook == LOAD_SLOT_SELECTOR_TITLE_HOOK_ORIGINAL
        and session_file_hook == LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_ORIGINAL
    ):
        if section is not None and section.raw_size >= (
            LOAD_SLOT_SELECTOR_SLOT_OFFSET + LOAD_SLOT_SELECTOR_SLOT_SIZE
        ) and bytes(data[
            section.raw_offset + LOAD_SLOT_SELECTOR_SLOT_OFFSET:
            section.raw_offset + LOAD_SLOT_SELECTOR_SLOT_OFFSET + len(LOAD_SLOT_SELECTOR_MAGIC)
        ]) == LOAD_SLOT_SELECTOR_MAGIC:
            raise ValueError("불러오기 슬롯 선택 코드가 남아 있지만 진입 분기부가 원본 상태입니다.")
        return False
    if _is_legacy_load_slot_selector_patch(data):
        return False
    if section is None or section.raw_size < (
        LOAD_SLOT_SELECTOR_SLOT_OFFSET + LOAD_SLOT_SELECTOR_SLOT_SIZE
    ) or section.virtual_size < (
        LOAD_SLOT_SELECTOR_SLOT_OFFSET + LOAD_SLOT_SELECTOR_SLOT_SIZE
    ):
        raise ValueError("불러오기 슬롯 선택 분기가 있으나 .patch 데이터를 찾지 못했습니다.")
    slot_offset, slot_va = section.slot(
        LOAD_SLOT_SELECTOR_SLOT_OFFSET, LOAD_SLOT_SELECTOR_SLOT_SIZE,
    )
    if (
        hook != LOAD_SLOT_SELECTOR_HOOK_ORIGINAL
        or command_hook != _load_slot_selector_command_hook(
            slot_va + LOAD_SLOT_SELECTOR_COMMAND_WRAPPER_OFFSET,
        )
        or title_hook != _load_slot_selector_title_hook(
            slot_va + LOAD_SLOT_SELECTOR_TITLE_WRAPPER_OFFSET,
        )
        or session_file_hook != _load_slot_selector_session_file_hook(
            slot_va + LOAD_SLOT_SELECTOR_SESSION_FILE_WRAPPER_OFFSET,
        )
        or bytes(data[slot_offset:slot_offset + LOAD_SLOT_SELECTOR_SLOT_SIZE])
        != _build_load_slot_selector_payload(slot_va)
    ):
        raise ValueError("불러오기 슬롯 선택 패치 상태를 검증하지 못했습니다.")
    return True


def apply_load_slot_selector_patch(data: bytearray, enabled: bool) -> bool:
    """Install or remove the native ten-slot load selector."""
    legacy_enabled = _is_legacy_load_slot_selector_patch(data)
    current_enabled = _load_slot_selector_patch_info(data)
    if current_enabled == enabled and not legacy_enabled:
        return False
    hook_offset = _load_slot_selector_hook_offset(data)
    command_hook_offset = _load_slot_selector_command_hook_offset(data)
    title_hook_offset = _load_slot_selector_title_hook_offset(data)
    session_file_hook_offset = _load_slot_selector_session_file_hook_offset(data)
    if current_enabled or legacy_enabled:
        section = find_patch_section(data)
        if section is None:
            raise ValueError("불러오기 슬롯 선택의 복원 데이터를 찾지 못했습니다.")
        data[hook_offset:hook_offset + len(LOAD_SLOT_SELECTOR_HOOK_ORIGINAL)] = (
            LOAD_SLOT_SELECTOR_HOOK_ORIGINAL
        )
        data[
            command_hook_offset:
            command_hook_offset + len(LOAD_SLOT_SELECTOR_COMMAND_HOOK_ORIGINAL)
        ] = LOAD_SLOT_SELECTOR_COMMAND_HOOK_ORIGINAL
        data[
            title_hook_offset:
            title_hook_offset + len(LOAD_SLOT_SELECTOR_TITLE_HOOK_ORIGINAL)
        ] = LOAD_SLOT_SELECTOR_TITLE_HOOK_ORIGINAL
        data[
            session_file_hook_offset:
            session_file_hook_offset + len(LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_ORIGINAL)
        ] = LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_ORIGINAL
        clear_slot(
            data, section, LOAD_SLOT_SELECTOR_SLOT_OFFSET, LOAD_SLOT_SELECTOR_SLOT_SIZE,
        )
        if not enabled:
            if not read_save_slot_selector_patch_state(data):
                characteristics = struct.unpack_from("<I", data, section.header_offset + 36)[0]
                struct.pack_into("<I", data, section.header_offset + 36, characteristics & ~0x80000000)
            return True
    if enabled:
        section, _created = ensure_patch_section(
            data, PATCH_SECTION_LOAD_SLOT_SELECTOR_SIZE,
        )
        slot_offset, slot_va = section.slot(
            LOAD_SLOT_SELECTOR_SLOT_OFFSET, LOAD_SLOT_SELECTOR_SLOT_SIZE,
        )
        payload = _build_load_slot_selector_payload(slot_va)
        existing = bytes(data[slot_offset:slot_offset + LOAD_SLOT_SELECTOR_SLOT_SIZE])
        if any(existing) and existing != payload:
            raise ValueError("불러오기 슬롯 선택용 .patch 슬롯이 다른 데이터로 사용 중입니다.")
        characteristics = struct.unpack_from("<I", data, section.header_offset + 36)[0]
        struct.pack_into("<I", data, section.header_offset + 36, characteristics | 0x80000000)
        data[slot_offset:slot_offset + LOAD_SLOT_SELECTOR_SLOT_SIZE] = payload
        data[hook_offset:hook_offset + len(LOAD_SLOT_SELECTOR_HOOK_ORIGINAL)] = (
            LOAD_SLOT_SELECTOR_HOOK_ORIGINAL
        )
        data[
            command_hook_offset:
            command_hook_offset + len(LOAD_SLOT_SELECTOR_COMMAND_HOOK_ORIGINAL)
        ] = _load_slot_selector_command_hook(
            slot_va + LOAD_SLOT_SELECTOR_COMMAND_WRAPPER_OFFSET,
        )
        data[
            title_hook_offset:
            title_hook_offset + len(LOAD_SLOT_SELECTOR_TITLE_HOOK_ORIGINAL)
        ] = _load_slot_selector_title_hook(
            slot_va + LOAD_SLOT_SELECTOR_TITLE_WRAPPER_OFFSET,
        )
        data[
            session_file_hook_offset:
            session_file_hook_offset + len(LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_ORIGINAL)
        ] = _load_slot_selector_session_file_hook(
            slot_va + LOAD_SLOT_SELECTOR_SESSION_FILE_WRAPPER_OFFSET,
        )
    else:
        section = find_patch_section(data)
        if section is None:
            raise ValueError("불러오기 슬롯 선택의 복원 데이터를 찾지 못했습니다.")
        data[hook_offset:hook_offset + len(LOAD_SLOT_SELECTOR_HOOK_ORIGINAL)] = (
            LOAD_SLOT_SELECTOR_HOOK_ORIGINAL
        )
        data[
            command_hook_offset:
            command_hook_offset + len(LOAD_SLOT_SELECTOR_COMMAND_HOOK_ORIGINAL)
        ] = LOAD_SLOT_SELECTOR_COMMAND_HOOK_ORIGINAL
        data[
            title_hook_offset:
            title_hook_offset + len(LOAD_SLOT_SELECTOR_TITLE_HOOK_ORIGINAL)
        ] = LOAD_SLOT_SELECTOR_TITLE_HOOK_ORIGINAL
        data[
            session_file_hook_offset:
            session_file_hook_offset + len(LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_ORIGINAL)
        ] = LOAD_SLOT_SELECTOR_SESSION_FILE_HOOK_ORIGINAL
        clear_slot(
            data, section, LOAD_SLOT_SELECTOR_SLOT_OFFSET, LOAD_SLOT_SELECTOR_SLOT_SIZE,
        )
        if not read_save_slot_selector_patch_state(data):
            characteristics = struct.unpack_from("<I", data, section.header_offset + 36)[0]
            struct.pack_into("<I", data, section.header_offset + 36, characteristics & ~0x80000000)
    return True


def read_load_slot_selector_patch_state(data: bytes | bytearray) -> bool:
    """Public state reader for the GUI's unified slot save/load checkbox."""
    return _load_slot_selector_patch_info(data) or _is_legacy_load_slot_selector_patch(data)


def _build_ship_reuse_fix_payload(slot_va: int) -> bytes:
    """Build the wrapper which clears stale cannon fields before ship weight setup."""
    wrapper_va = slot_va + SHIP_REUSE_FIX_WRAPPER_OFFSET
    wrapper = bytearray(bytes.fromhex(
        "31 C0 "                    # xor eax, eax
        "89 41 50 "                 # mov [ecx+50h], eax (current cannon count)
        "89 41 54 "                 # mov [ecx+54h], eax (maximum cannon count)
        "C7 41 58 FF FF FF FF "     # mov dword ptr [ecx+58h], -1 (no cannon)
        "E9 00 00 00 00"            # jmp original maximum-weight setter
    ))
    jump_offset = len(wrapper) - 5
    struct.pack_into(
        "<i", wrapper, jump_offset + 1,
        SHIP_WEIGHT_SETTER_VA - (wrapper_va + jump_offset + 5),
    )
    if SHIP_REUSE_FIX_WRAPPER_OFFSET + len(wrapper) > SHIP_REUSE_FIX_SLOT_SIZE:
        raise AssertionError("선박 슬롯 재사용 버그 수정 래퍼가 예약 공간을 초과했습니다.")

    payload = bytearray(SHIP_REUSE_FIX_SLOT_SIZE)
    payload[:len(SHIP_REUSE_FIX_MAGIC)] = SHIP_REUSE_FIX_MAGIC
    struct.pack_into("<I", payload, len(SHIP_REUSE_FIX_MAGIC), SHIP_REUSE_FIX_VERSION)
    payload[
        SHIP_REUSE_FIX_WRAPPER_OFFSET:
        SHIP_REUSE_FIX_WRAPPER_OFFSET + len(wrapper)
    ] = wrapper
    return bytes(payload)


def _ship_reuse_fix_hook(wrapper_va: int) -> bytes:
    return b"\xE8" + struct.pack(
        "<i", wrapper_va - (SHIP_REUSE_WEIGHT_SETTER_CALL_VA + 5),
    )


def _ship_reuse_fix_patch_info(data: bytes | bytearray) -> bool:
    """Return whether recycled ship slots are cleared before derived setup."""
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        hook_offset = pe.get_offset_from_rva(
            SHIP_REUSE_WEIGHT_SETTER_CALL_VA - pe.OPTIONAL_HEADER.ImageBase
        )
    finally:
        pe.close()

    current_hook = bytes(data[
        hook_offset:hook_offset + len(SHIP_REUSE_ORIGINAL_CALL)
    ])
    section = find_patch_section(data)
    if current_hook == SHIP_REUSE_ORIGINAL_CALL:
        if (
            section is not None
            and section.raw_size >= SHIP_REUSE_FIX_SLOT_OFFSET + SHIP_REUSE_FIX_SLOT_SIZE
            and bytes(data[
                section.raw_offset + SHIP_REUSE_FIX_SLOT_OFFSET:
                section.raw_offset + SHIP_REUSE_FIX_SLOT_OFFSET + len(SHIP_REUSE_FIX_MAGIC)
            ]) == SHIP_REUSE_FIX_MAGIC
        ):
            raise ValueError("선박 슬롯 재사용 버그 수정 코드가 남아 있지만 호출부가 원본 상태입니다.")
        return False
    if (
        section is None
        or section.raw_size < SHIP_REUSE_FIX_SLOT_OFFSET + SHIP_REUSE_FIX_SLOT_SIZE
        or section.virtual_size < SHIP_REUSE_FIX_SLOT_OFFSET + SHIP_REUSE_FIX_SLOT_SIZE
    ):
        raise ValueError("선박 슬롯 재사용 버그 수정 호출이 있으나 .patch 데이터를 찾지 못했습니다.")
    slot_offset, slot_va = section.slot(
        SHIP_REUSE_FIX_SLOT_OFFSET, SHIP_REUSE_FIX_SLOT_SIZE,
    )
    expected_payload = _build_ship_reuse_fix_payload(slot_va)
    expected_hook = _ship_reuse_fix_hook(slot_va + SHIP_REUSE_FIX_WRAPPER_OFFSET)
    if (
        current_hook != expected_hook
        or bytes(data[slot_offset:slot_offset + SHIP_REUSE_FIX_SLOT_SIZE]) != expected_payload
    ):
        raise ValueError("선박 슬롯 재사용 버그 수정 상태를 검증하지 못했습니다.")
    return True


def apply_ship_reuse_fix(data: bytearray, enabled: bool) -> bool:
    """Clear stale cannon fields before constructing a ship in a reused slot."""
    current_enabled = _ship_reuse_fix_patch_info(data)
    if current_enabled == enabled:
        return False
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        hook_offset = pe.get_offset_from_rva(
            SHIP_REUSE_WEIGHT_SETTER_CALL_VA - pe.OPTIONAL_HEADER.ImageBase
        )
    finally:
        pe.close()

    if enabled:
        section, _created = ensure_patch_section(data, PATCH_SECTION_SHIP_REUSE_FIX_SIZE)
        slot_offset, slot_va = section.slot(
            SHIP_REUSE_FIX_SLOT_OFFSET, SHIP_REUSE_FIX_SLOT_SIZE,
        )
        payload = _build_ship_reuse_fix_payload(slot_va)
        current_payload = bytes(data[slot_offset:slot_offset + SHIP_REUSE_FIX_SLOT_SIZE])
        if any(current_payload) and current_payload != payload:
            raise ValueError("선박 슬롯 재사용 버그 수정용 .patch 슬롯이 다른 데이터로 사용 중입니다.")
        data[slot_offset:slot_offset + SHIP_REUSE_FIX_SLOT_SIZE] = payload
        data[hook_offset:hook_offset + len(SHIP_REUSE_ORIGINAL_CALL)] = (
            _ship_reuse_fix_hook(slot_va + SHIP_REUSE_FIX_WRAPPER_OFFSET)
        )
    else:
        section = find_patch_section(data)
        if section is None:
            raise ValueError("선박 슬롯 재사용 버그 수정의 복원 데이터를 찾지 못했습니다.")
        data[hook_offset:hook_offset + len(SHIP_REUSE_ORIGINAL_CALL)] = SHIP_REUSE_ORIGINAL_CALL
        clear_slot(data, section, SHIP_REUSE_FIX_SLOT_OFFSET, SHIP_REUSE_FIX_SLOT_SIZE)
    return True


def _build_ship_purchase_blank_selection_fix_payload(slot_va: int) -> bytes:
    """Build the selector-index guard used by the ship-purchase screen."""
    wrapper_va = slot_va + SHIP_PURCHASE_BLANK_SELECTION_FIX_WRAPPER_OFFSET
    wrapper = bytearray(bytes.fromhex(
        "83 FF FF "                # cmp edi, -1 (explicit cancel)
        "0F 84 00 00 00 00 "        # je  purchase cancel path
        "85 FF "                    # test edi, edi
        "0F 88 00 00 00 00 "        # js  selector loop
        "3B 7D F0 "                 # cmp edi, [ebp-10h] (candidate count)
        "0F 8D 00 00 00 00 "        # jge selector loop
        "E9 00 00 00 00"            # jmp original valid-selection path
    ))
    for displacement_offset, instruction_end, target_va in (
        (5, 9, SHIP_PURCHASE_BLANK_SELECTION_CANCEL_VA),
        (13, 17, SHIP_PURCHASE_BLANK_SELECTION_RETRY_VA),
        (22, 26, SHIP_PURCHASE_BLANK_SELECTION_RETRY_VA),
        (27, 31, SHIP_PURCHASE_BLANK_SELECTION_RESUME_VA),
    ):
        struct.pack_into(
            "<i", wrapper, displacement_offset,
            target_va - (wrapper_va + instruction_end),
        )
    if (
        SHIP_PURCHASE_BLANK_SELECTION_FIX_WRAPPER_OFFSET + len(wrapper)
        > SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_SIZE
    ):
        raise AssertionError("선박 구입 빈 슬롯 수정 래퍼가 예약 공간을 초과했습니다.")

    payload = bytearray(SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_SIZE)
    payload[:len(SHIP_PURCHASE_BLANK_SELECTION_FIX_MAGIC)] = (
        SHIP_PURCHASE_BLANK_SELECTION_FIX_MAGIC
    )
    struct.pack_into(
        "<I", payload, len(SHIP_PURCHASE_BLANK_SELECTION_FIX_MAGIC),
        SHIP_PURCHASE_BLANK_SELECTION_FIX_VERSION,
    )
    payload[
        SHIP_PURCHASE_BLANK_SELECTION_FIX_WRAPPER_OFFSET:
        SHIP_PURCHASE_BLANK_SELECTION_FIX_WRAPPER_OFFSET + len(wrapper)
    ] = wrapper
    return bytes(payload)


def _ship_purchase_blank_selection_fix_hook(wrapper_va: int) -> bytes:
    return b"\xE9" + struct.pack(
        "<i", wrapper_va - (SHIP_PURCHASE_BLANK_SELECTION_HOOK_VA + 5),
    ) + b"\x90" * 4


def _ship_purchase_blank_selection_fix_patch_info(data: bytes | bytearray) -> bool:
    """Return whether invalid ship-purchase rows are guarded before indexing."""
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        hook_offset = pe.get_offset_from_rva(
            SHIP_PURCHASE_BLANK_SELECTION_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase
        )
    finally:
        pe.close()

    current_hook = bytes(data[
        hook_offset:hook_offset + len(SHIP_PURCHASE_BLANK_SELECTION_ORIGINAL)
    ])
    section = find_patch_section(data)
    if current_hook == SHIP_PURCHASE_BLANK_SELECTION_ORIGINAL:
        if (
            section is not None
            and section.raw_size >= (
                SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_OFFSET
                + SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_SIZE
            )
            and bytes(data[
                section.raw_offset + SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_OFFSET:
                section.raw_offset + SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_OFFSET
                + len(SHIP_PURCHASE_BLANK_SELECTION_FIX_MAGIC)
            ]) == SHIP_PURCHASE_BLANK_SELECTION_FIX_MAGIC
        ):
            raise ValueError("선박 구입 빈 슬롯 수정 코드가 남아 있지만 호출부가 원본 상태입니다.")
        return False
    if (
        section is None
        or section.raw_size < (
            SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_OFFSET
            + SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_SIZE
        )
        or section.virtual_size < (
            SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_OFFSET
            + SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_SIZE
        )
    ):
        raise ValueError("선박 구입 빈 슬롯 수정 호출이 있으나 .patch 데이터를 찾지 못했습니다.")
    slot_offset, slot_va = section.slot(
        SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_OFFSET,
        SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_SIZE,
    )
    expected_payload = _build_ship_purchase_blank_selection_fix_payload(slot_va)
    expected_hook = _ship_purchase_blank_selection_fix_hook(
        slot_va + SHIP_PURCHASE_BLANK_SELECTION_FIX_WRAPPER_OFFSET
    )
    if (
        current_hook != expected_hook
        or bytes(data[
            slot_offset:slot_offset + SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_SIZE
        ]) != expected_payload
    ):
        raise ValueError("선박 구입 빈 슬롯 수정 상태를 검증하지 못했습니다.")
    return True


def apply_ship_purchase_blank_selection_fix(data: bytearray, enabled: bool) -> bool:
    """Prevent a blank row in the ship-purchase list from indexing past candidates."""
    current_enabled = _ship_purchase_blank_selection_fix_patch_info(data)
    if current_enabled == enabled:
        return False
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        hook_offset = pe.get_offset_from_rva(
            SHIP_PURCHASE_BLANK_SELECTION_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase
        )
    finally:
        pe.close()

    if enabled:
        section, _created = ensure_patch_section(
            data, PATCH_SECTION_SHIP_PURCHASE_BLANK_SELECTION_FIX_SIZE,
        )
        slot_offset, slot_va = section.slot(
            SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_OFFSET,
            SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_SIZE,
        )
        payload = _build_ship_purchase_blank_selection_fix_payload(slot_va)
        current_payload = bytes(data[
            slot_offset:slot_offset + SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_SIZE
        ])
        if any(current_payload) and current_payload != payload:
            raise ValueError("선박 구입 빈 슬롯 수정용 .patch 슬롯이 다른 데이터로 사용 중입니다.")
        data[slot_offset:slot_offset + SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_SIZE] = payload
        data[
            hook_offset:hook_offset + len(SHIP_PURCHASE_BLANK_SELECTION_ORIGINAL)
        ] = _ship_purchase_blank_selection_fix_hook(
            slot_va + SHIP_PURCHASE_BLANK_SELECTION_FIX_WRAPPER_OFFSET
        )
    else:
        section = find_patch_section(data)
        if section is None:
            raise ValueError("선박 구입 빈 슬롯 수정의 복원 데이터를 찾지 못했습니다.")
        data[
            hook_offset:hook_offset + len(SHIP_PURCHASE_BLANK_SELECTION_ORIGINAL)
        ] = SHIP_PURCHASE_BLANK_SELECTION_ORIGINAL
        clear_slot(
            data, section,
            SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_OFFSET,
            SHIP_PURCHASE_BLANK_SELECTION_FIX_SLOT_SIZE,
        )
    return True


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


def _person_ability_limit_code(limit: int) -> bytes:
    """Encode the shared six-ability clamp with an unsigned 32-bit maximum."""
    code = bytearray(bytes.fromhex("8b 44 24 04 56 68"))
    code += struct.pack("<I", limit)
    code += bytes.fromhex("6a 00 8d 34 81 8b 4c 24 14 51 8b 06 40 50")
    call_va = PERSON_ABILITY_LIMIT_VA + len(code)
    code += b"\xE8" + struct.pack("<i", 0x49E560 - (call_va + 5))
    code += bytes.fromhex("83 c4 10 48 89 06 5e c2 08 00")
    if len(code) > PERSON_ABILITY_LIMIT_BLOCK_SIZE:
        raise AssertionError("능력치 상한 코드가 원래 함수 공간을 초과합니다.")
    return bytes(code).ljust(PERSON_ABILITY_LIMIT_BLOCK_SIZE, b"\xCC")


def _read_person_stat_limits_from_data(data: bytes | bytearray) -> tuple[int, int]:
    """Read the live game clamps, validating their surrounding machine code."""
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")

        def offset(va: int) -> int:
            return pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)

        ability_offset = offset(PERSON_ABILITY_LIMIT_VA)
        ability_code = bytes(data[ability_offset:ability_offset + PERSON_ABILITY_LIMIT_BLOCK_SIZE])
        if ability_code == PERSON_ABILITY_ORIGINAL_CODE:
            ability_limit = 100
        elif ability_code[:6] == bytes.fromhex("8b 44 24 04 56 68"):
            ability_limit = struct.unpack_from("<I", ability_code, 6)[0]
            if ability_limit > PERSON_ABILITY_MAX or ability_code != _person_ability_limit_code(ability_limit):
                raise ValueError("능력치 상한 함수의 코드를 검증하지 못했습니다.")
        else:
            raise ValueError("능력치 상한 함수의 코드를 검증하지 못했습니다.")

        vitality_offset = offset(PERSON_VITALITY_LIMIT_VA)
        if (
            bytes(data[vitality_offset - 7:vitality_offset + 1])
            != PERSON_VITALITY_ORIGINAL_PREFIX
            or bytes(data[vitality_offset + 5:vitality_offset + 5 + len(PERSON_VITALITY_ORIGINAL_SUFFIX)])
            != PERSON_VITALITY_ORIGINAL_SUFFIX
        ):
            raise ValueError("생명력 상한 함수의 코드를 검증하지 못했습니다.")
        vitality_limit = struct.unpack_from("<I", data, vitality_offset + 1)[0]
        if vitality_limit > PERSON_VITALITY_MAX:
            raise ValueError("생명력 상한값이 0~9999 범위를 벗어납니다.")
        return ability_limit, vitality_limit
    finally:
        pe.close()


def read_person_stat_limits(target: Path) -> tuple[int, int]:
    return _read_person_stat_limits_from_data(target.resolve(strict=True).read_bytes())


def apply_person_stat_limits(data: bytearray, ability_limit: int, vitality_limit: int) -> bool:
    if not 0 <= ability_limit <= PERSON_ABILITY_MAX:
        raise ValueError("체력·지력·무력·매력·운·신앙심 공통 상한은 0~255 사이여야 합니다.")
    if not 0 <= vitality_limit <= PERSON_VITALITY_MAX:
        raise ValueError("생명력 상한은 0~9999 사이여야 합니다.")
    current = _read_person_stat_limits_from_data(data)
    if current == (ability_limit, vitality_limit):
        return False
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        ability_offset = pe.get_offset_from_rva(PERSON_ABILITY_LIMIT_VA - pe.OPTIONAL_HEADER.ImageBase)
        vitality_offset = pe.get_offset_from_rva(PERSON_VITALITY_LIMIT_VA - pe.OPTIONAL_HEADER.ImageBase)
    finally:
        pe.close()
    if current[0] != ability_limit:
        data[ability_offset:ability_offset + PERSON_ABILITY_LIMIT_BLOCK_SIZE] = (
            PERSON_ABILITY_ORIGINAL_CODE if ability_limit == 100
            else _person_ability_limit_code(ability_limit)
        )
    if current[1] != vitality_limit:
        struct.pack_into("<I", data, vitality_offset + 1, vitality_limit)
    return True


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


def _player_fame_limit_payload(slot_va: int, fame_limit: int, infamy_limit: int) -> bytes:
    """Route the original shared clamp to the correct player-field maximum."""
    payload = bytearray(PLAYER_FAME_LIMIT_SLOT_SIZE)
    payload[:len(PLAYER_FAME_LIMIT_MAGIC)] = PLAYER_FAME_LIMIT_MAGIC
    struct.pack_into("<II", payload, 8, fame_limit, infamy_limit)
    code_va = slot_va + PLAYER_FAME_LIMIT_CODE_OFFSET
    # EAX is the field index: 0 is fame and 1 is infamy.  The overwritten
    # instruction only pushed a maximum; keep all registers and the stack as
    # they were when execution resumes at 0x4800EA.
    code = bytearray(b"\x85\xC0\x75\x07\x68")
    code += struct.pack("<I", fame_limit)
    code += b"\xEB\x05\x68" + struct.pack("<I", infamy_limit)
    code += b"\xE9" + struct.pack("<i", PLAYER_FAME_LIMIT_HOOK_END_VA - (code_va + 21))
    if len(code) != 21:
        raise AssertionError("명성·악명 상한 분기 크기가 올바르지 않습니다.")
    payload[PLAYER_FAME_LIMIT_CODE_OFFSET:PLAYER_FAME_LIMIT_CODE_OFFSET + len(code)] = code
    return bytes(payload)


def _player_fame_limit_state(data: bytes | bytearray, pe: pefile.PE) -> tuple[int, int, bool]:
    """Read the actual player clamp, including the independent-limit wrapper."""
    hook_offset = pe.get_offset_from_rva(
        PLAYER_FAME_LIMIT_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase
    )
    if (
        data[hook_offset - 5:hook_offset] != b"\x8B\x44\x24\x04\x56"
        or data[hook_offset + 5:hook_offset + 14]
        != b"\x6A\x00\x8D\xB4\x81\xAC\x00\x00\x00"
    ):
        raise ValueError("주인공 명성·악명 상한 함수 위치를 검증하지 못했습니다.")
    hook = bytes(data[hook_offset:hook_offset + 5])
    if hook[0] == 0x68:
        shared_limit = struct.unpack_from("<I", hook, 1)[0]
        if not FAME_INFAMY_LIMIT_MIN <= shared_limit <= 0x7FFF_FFFF:
            raise ValueError("주인공 명성·악명 공통 상한값이 올바르지 않습니다.")
        return shared_limit, shared_limit, False
    if hook[0] != 0xE9:
        raise ValueError("주인공 명성·악명 상한 명령이 예상한 형식이 아닙니다.")
    section = find_patch_section(data)
    if section is None:
        raise ValueError("명성·악명 분기 코드의 .patch 섹션을 찾지 못했습니다.")
    slot_offset, slot_va = section.slot(
        PLAYER_FAME_LIMIT_SLOT_OFFSET, PLAYER_FAME_LIMIT_SLOT_SIZE,
    )
    payload = bytes(data[slot_offset:slot_offset + PLAYER_FAME_LIMIT_SLOT_SIZE])
    if payload[:len(PLAYER_FAME_LIMIT_MAGIC)] != PLAYER_FAME_LIMIT_MAGIC:
        raise ValueError("명성·악명 분기 코드의 식별자를 검증하지 못했습니다.")
    fame_limit, infamy_limit = struct.unpack_from("<II", payload, 8)
    if not (
        FAME_INFAMY_LIMIT_MIN <= fame_limit <= FAME_LIMIT_MAX
        and FAME_INFAMY_LIMIT_MIN <= infamy_limit <= INFAMY_LIMIT_MAX
    ):
        raise ValueError("명성·악명 분기 코드의 상한값이 범위를 벗어납니다.")
    expected_hook = b"\xE9" + struct.pack(
        "<i", slot_va + PLAYER_FAME_LIMIT_CODE_OFFSET - PLAYER_FAME_LIMIT_HOOK_END_VA,
    )
    if (
        hook != expected_hook
        or payload != _player_fame_limit_payload(slot_va, fame_limit, infamy_limit)
    ):
        raise ValueError("명성·악명 분기 코드가 검증한 형식과 다릅니다.")
    return fame_limit, infamy_limit, True


def _apply_player_fame_limits(data: bytearray, fame_limit: int, infamy_limit: int) -> None:
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        current_fame, current_infamy, installed = _player_fame_limit_state(data, pe)
        if (current_fame, current_infamy) == (fame_limit, infamy_limit):
            return
        hook_offset = pe.get_offset_from_rva(
            PLAYER_FAME_LIMIT_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase
        )
    finally:
        pe.close()
    if fame_limit == infamy_limit:
        if installed:
            section = find_patch_section(data)
            if section is None:
                raise ValueError("명성·악명 분기 코드의 .patch 섹션을 찾지 못했습니다.")
            clear_slot(data, section, PLAYER_FAME_LIMIT_SLOT_OFFSET, PLAYER_FAME_LIMIT_SLOT_SIZE)
        data[hook_offset:hook_offset + 5] = b"\x68" + struct.pack("<I", fame_limit)
        return
    section, _ = ensure_patch_section(data, PATCH_SECTION_PLAYER_FAME_LIMIT_SIZE)
    slot_offset, slot_va = section.slot(
        PLAYER_FAME_LIMIT_SLOT_OFFSET, PLAYER_FAME_LIMIT_SLOT_SIZE,
    )
    current_slot = bytes(data[slot_offset:slot_offset + PLAYER_FAME_LIMIT_SLOT_SIZE])
    if not installed and any(current_slot):
        raise ValueError("명성·악명 분기 코드의 예약 공간이 이미 사용 중입니다.")
    data[slot_offset:slot_offset + PLAYER_FAME_LIMIT_SLOT_SIZE] = (
        _player_fame_limit_payload(slot_va, fame_limit, infamy_limit)
    )
    data[hook_offset:hook_offset + 5] = b"\xE9" + struct.pack(
        "<i", slot_va + PLAYER_FAME_LIMIT_CODE_OFFSET - PLAYER_FAME_LIMIT_HOOK_END_VA,
    )


def _read_gameplay_options_from_data(data: bytes) -> tuple[int, int, int, int, int, int, int, int, int, int]:
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

        telescope_bonus_offset = offset(TELESCOPE_CITY_DISCOVERY_BONUS_VA)
        if data[telescope_bonus_offset - 4:telescope_bonus_offset] != bytes.fromhex("83 44 24 14"):
            raise ValueError("망원경 도시 발견 보정 위치를 검증하지 못했습니다.")
        telescope_city_discovery_bonus = struct.unpack_from("b", data, telescope_bonus_offset)[0]
        if not 0 <= telescope_city_discovery_bonus <= TELESCOPE_CITY_DISCOVERY_BONUS_MAX:
            raise ValueError("망원경 도시 발견 보정값이 지원 범위를 벗어납니다.")

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
        fame_limit, infamy_limit, _ = _player_fame_limit_state(data, pe)
        return (
            long_rest_max,
            preparation_values[0],
            telescope_city_discovery_bonus,
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
    telescope_city_discovery_bonus: int,
    succession_min_age: int,
    cold_north_limit: int,
    cold_south_limit: int,
    cash_limit: int,
    deposit_limit: int,
    fame_limit: int,
    infamy_limit: int,
) -> bool:
    """Set rest, exploration, city-sight, money and polar-cold parameters."""
    if not 1 <= long_rest_max <= 127:
        raise ValueError("장기 휴양 최대 기간은 1~127개월 사이여야 합니다.")
    if not 1 <= exploration_preparation_days <= 127:
        raise ValueError("탐험 준비 기간은 1~127일 사이여야 합니다.")
    if not 0 <= telescope_city_discovery_bonus <= TELESCOPE_CITY_DISCOVERY_BONUS_MAX:
        raise ValueError(
            f"망원경 도시 발견 보정은 0~{TELESCOPE_CITY_DISCOVERY_BONUS_MAX}칸 사이여야 합니다."
        )
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
        telescope_city_discovery_bonus,
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
        if (
            current == target
            and displayed_days == exploration_preparation_days
            and all(value == cash_limit for value in cash_values)
            and all(value == deposit_limit for value in deposit_values)
        ):
            return False

        def offset(va: int) -> int:
            return pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)

        data[offset(LONG_REST_MAX_VA)] = long_rest_max
        for va in EXPLORATION_PREPARATION_VAS:
            data[offset(va)] = exploration_preparation_days
        data[offset(TELESCOPE_CITY_DISCOVERY_BONUS_VA)] = telescope_city_discovery_bonus
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
        _apply_player_fame_limits(data, fame_limit, infamy_limit)
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
    """Read editable person and event-actor records without touching saves."""
    if PERSON_TABLE_FILE_OFFSET + PERSON_MASTER_RECORD_COUNT * PERSON_RECORD_SIZE > len(data):
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
            face_raw, gender = struct.unpack_from("<II", data, offset + PERSON_FACE_CODE_OFFSET)
            # Sea monsters use 0xFFFFFFFF to mean that no MALE/FEMALE portrait
            # is associated with the actor.
            face = -1 if face_raw == 0xFFFFFFFF else face_raw
            age, nation, job = struct.unpack_from("<iii", data, offset + PERSON_AGE_AT_1480_OFFSET)[0], struct.unpack_from("<i", data, offset + PERSON_NATION_ID_OFFSET)[0], struct.unpack_from("<i", data, offset + PERSON_JOB_ID_OFFSET)[0]
            fame, infamy = struct.unpack_from("<II", data, offset + PERSON_FAME_OFFSET)
            employment_state = struct.unpack_from("<i", data, offset + PERSON_EMPLOYMENT_STATE_OFFSET)[0]
            city, building, blood = struct.unpack_from("<iii", data, offset + PERSON_CITY_ID_OFFSET)
            vitality = struct.unpack_from("<I", data, offset + PERSON_VITALITY_OFFSET)[0]
            hire_cost_coefficient = struct.unpack_from("<I", data, offset + PERSON_HIRE_COST_COEFFICIENT_OFFSET)[0]
            abilities = struct.unpack_from(f"<{PERSON_ABILITY_COUNT}I", data, offset + PERSON_ABILITIES_OFFSET)
            skills = struct.unpack_from(f"<{PERSON_SKILL_COUNT}I", data, offset + PERSON_SKILLS_OFFSET)
            if gender not in (0, 1) or not (-1 <= face <= (143 if gender else 413)) or not -100 <= age <= 100 or not 0 <= nation <= 18 or not 0 <= job <= 3 or not 0 <= fame <= 65535 or not 0 <= infamy <= 65535 or employment_state not in (0, 1, 2) or not -1 <= city <= 225 or not 0 <= building <= 15 or not 0 <= blood <= 3 or not 0 <= vitality <= PERSON_VITALITY_MAX or not 0 <= hire_cost_coefficient <= PERSON_HIRE_COST_COEFFICIENT_MAX or any(not 0 <= value <= PERSON_ABILITY_MAX for value in abilities) or any(not 0 <= value <= PERSON_SKILL_MAX for value in skills):
                raise ValueError(f"인물 {identifier}번 마스터 값을 검증하지 못했습니다.")
            records.append(PersonRecord(identifier, f"{first} {last}".strip(), face, gender, age, nation, job, fame, infamy, employment_state, city, building, blood, vitality, hire_cost_coefficient, abilities, skills))
        return tuple(records)
    finally:
        pe.close()


def read_person_records(target: Path) -> tuple[PersonRecord, ...]:
    return _read_person_records_from_data(target.resolve(strict=True).read_bytes())


def apply_person_edit(data: bytearray, edit: PersonEdit | None) -> bool:
    if edit is None:
        return False
    if not 0 <= edit.identifier < PERSON_RECORD_COUNT or edit.gender not in (0, 1) or not -1 <= edit.face_code <= (143 if edit.gender else 413) or not -100 <= edit.age_at_1480 <= 100 or not 0 <= edit.nation_id <= 18 or not 0 <= edit.job_id <= 3 or not 0 <= edit.fame <= 65535 or not 0 <= edit.infamy <= 65535 or edit.employment_state not in (0, 1, 2) or not -1 <= edit.city_id <= 225 or not 0 <= edit.building_id <= 15 or not 0 <= edit.blood_id <= 3 or not 0 <= edit.vitality <= PERSON_VITALITY_MAX or not 0 <= edit.hire_cost_coefficient <= PERSON_HIRE_COST_COEFFICIENT_MAX or len(edit.abilities) != PERSON_ABILITY_COUNT or any(not 0 <= value <= PERSON_ABILITY_MAX for value in edit.abilities) or len(edit.skills) != PERSON_SKILL_COUNT or any(not 0 <= value <= PERSON_SKILL_MAX for value in edit.skills):
        raise ValueError("인물 입력값을 확인해 주세요.")
    current = _read_person_records_from_data(bytes(data))[edit.identifier]
    if (current.face_code, current.gender, current.age_at_1480, current.nation_id, current.job_id, current.fame, current.infamy, current.employment_state, current.city_id, current.building_id, current.blood_id, current.vitality, current.hire_cost_coefficient, current.abilities, current.skills) == (edit.face_code, edit.gender, edit.age_at_1480, edit.nation_id, edit.job_id, edit.fame, edit.infamy, edit.employment_state, edit.city_id, edit.building_id, edit.blood_id, edit.vitality, edit.hire_cost_coefficient, edit.abilities, edit.skills):
        return False
    offset = PERSON_TABLE_FILE_OFFSET + edit.identifier * PERSON_RECORD_SIZE
    face_raw = 0xFFFFFFFF if edit.face_code < 0 else edit.face_code
    struct.pack_into("<IIi", data, offset + PERSON_FACE_CODE_OFFSET, face_raw, edit.gender, edit.age_at_1480)
    struct.pack_into("<i", data, offset + PERSON_NATION_ID_OFFSET, edit.nation_id)
    struct.pack_into("<i", data, offset + PERSON_JOB_ID_OFFSET, edit.job_id)
    struct.pack_into("<II", data, offset + PERSON_FAME_OFFSET, edit.fame, edit.infamy)
    struct.pack_into("<i", data, offset + PERSON_EMPLOYMENT_STATE_OFFSET, edit.employment_state)
    struct.pack_into("<iii", data, offset + PERSON_CITY_ID_OFFSET, edit.city_id, edit.building_id, edit.blood_id)
    struct.pack_into("<I", data, offset + PERSON_VITALITY_OFFSET, edit.vitality)
    struct.pack_into(f"<{PERSON_ABILITY_COUNT}I", data, offset + PERSON_ABILITIES_OFFSET, *edit.abilities)
    struct.pack_into("<I", data, offset + PERSON_HIRE_COST_COEFFICIENT_OFFSET, edit.hire_cost_coefficient)
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
            image_raw, buy_price, sell_price, effect_value, category_id, hint_id = struct.unpack_from(
                "<IIIIIi", data, offset + ITEM_IMAGE_ID_OFFSET,
            )
            if (
                not name
                or not 0 <= buy_price <= ITEM_PRICE_MAX
                or not 0 <= sell_price <= ITEM_PRICE_MAX
                or not 0 <= effect_value <= ITEM_EFFECT_MAX
                or not 0 <= category_id <= ITEM_CATEGORY_MAX
                or not ITEM_HINT_ID_MIN <= hint_id <= ITEM_HINT_ID_MAX
            ):
                raise ValueError(f"아이템 {identifier}번 마스터 값을 검증하지 못했습니다.")
            records.append(ItemRecord(
                identifier, name,
                None if image_raw == ITEM_NO_IMAGE_ID else image_raw,
                buy_price, sell_price, effect_value, category_id, hint_id,
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
        or not ITEM_HINT_ID_MIN <= edit.hint_id <= ITEM_HINT_ID_MAX
    ):
        raise ValueError("아이템 입력값을 확인해 주세요.")
    current = _read_item_records_from_data(bytes(data))[edit.identifier]
    if (
        current.name, current.buy_price, current.sell_price,
        current.effect_value, current.category_id, current.hint_id,
    ) == (
        name, edit.buy_price, edit.sell_price,
        edit.effect_value, edit.category_id, edit.hint_id,
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
    struct.pack_into("<i", data, offset + ITEM_HINT_ID_OFFSET, edit.hint_id)
    return True


def _fake_item_record_offset(pe: pefile.PE, identifier: int) -> int:
    if not FAKE_ITEM_FIRST_RECORD_ID <= identifier <= FAKE_ITEM_LAST_RECORD_ID:
        raise ValueError("모조품 레코드 번호가 올바르지 않습니다.")
    return (
        pe.get_offset_from_rva(DISCOVERY_METADATA_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase)
        + identifier * DISCOVERY_RECORD_SIZE
    )


def _read_fake_item_records_from_data(data: bytes) -> tuple[FakeItemRecord, ...]:
    """Read the 28 fake-item market rows embedded after normal discoveries."""
    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        item_records = _read_item_records_from_data(data)
        records: list[FakeItemRecord] = []
        for identifier in range(FAKE_ITEM_FIRST_RECORD_ID, FAKE_ITEM_LAST_RECORD_ID + 1):
            offset = _fake_item_record_offset(pe, identifier)
            category_id = struct.unpack_from("<I", data, offset + DISCOVERY_CATEGORY_OFFSET)[0]
            target_code = struct.unpack_from("<I", data, offset + DISCOVERY_GAME_ID_OFFSET)[0]
            value = struct.unpack_from("<I", data, offset + DISCOVERY_VALUE_OFFSET)[0]
            item_id = struct.unpack_from("<I", data, offset + FAKE_ITEM_ITEM_ID_OFFSET)[0]
            city_id = struct.unpack_from("<I", data, offset + FAKE_ITEM_CITY_ID_OFFSET)[0]
            if (
                not 0 <= category_id <= DISCOVERY_CATEGORY_MAX
                or not 0 <= target_code <= 0xFFFF
                or not 0 <= value <= DISCOVERY_VALUE_MAX
                or not 0 <= item_id < ITEM_RECORD_COUNT
                or not 0 <= city_id < CITY_RECORD_COUNT
            ):
                raise ValueError(f"모조품 {identifier}번 레코드를 검증하지 못했습니다.")
            records.append(FakeItemRecord(
                identifier, item_records[item_id].name, category_id,
                target_code, value, item_id, city_id,
            ))
        return tuple(records)
    finally:
        pe.close()


def read_fake_item_records(target: Path) -> tuple[FakeItemRecord, ...]:
    """Read the EXE-side fake-item market definitions."""
    return _read_fake_item_records_from_data(target.resolve(strict=True).read_bytes())


def apply_fake_item_edit(data: bytearray, edit: FakeItemEdit | None) -> bool:
    """Update one fake-item row without changing hint text or save data."""
    if edit is None:
        return False
    if (
        not FAKE_ITEM_FIRST_RECORD_ID <= edit.identifier <= FAKE_ITEM_LAST_RECORD_ID
        or not 0 <= edit.category_id <= DISCOVERY_CATEGORY_MAX
        or not 0 <= edit.target_code <= 0xFFFF
        or not 0 <= edit.value <= DISCOVERY_VALUE_MAX
        or not 0 <= edit.item_id < ITEM_RECORD_COUNT
        or not 0 <= edit.city_id < CITY_RECORD_COUNT
    ):
        raise ValueError("모조품 입력값을 확인해 주세요.")
    current = next(
        record for record in _read_fake_item_records_from_data(bytes(data))
        if record.identifier == edit.identifier
    )
    if (
        current.category_id, current.target_code, current.value,
        current.item_id, current.city_id,
    ) == (
        edit.category_id, edit.target_code, edit.value, edit.item_id, edit.city_id,
    ):
        return False
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        offset = _fake_item_record_offset(pe, edit.identifier)
        item_offset = (
            pe.get_offset_from_rva(ITEM_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase)
            + edit.item_id * ITEM_RECORD_SIZE
        )
        item_name_pointer = struct.unpack_from(
            "<I", data, item_offset + ITEM_NAME_POINTER_OFFSET
        )[0]
    finally:
        pe.close()
    # Keep the market label synchronized with the item actually sold.
    struct.pack_into("<I", data, offset + DISCOVERY_NAME_POINTER_OFFSET, item_name_pointer)
    struct.pack_into("<I", data, offset + DISCOVERY_CATEGORY_OFFSET, edit.category_id)
    struct.pack_into("<I", data, offset + DISCOVERY_GAME_ID_OFFSET, edit.target_code)
    struct.pack_into("<I", data, offset + DISCOVERY_VALUE_OFFSET, edit.value)
    struct.pack_into("<I", data, offset + FAKE_ITEM_ITEM_ID_OFFSET, edit.item_id)
    struct.pack_into("<I", data, offset + FAKE_ITEM_CITY_ID_OFFSET, edit.city_id)
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
    if identifier == DISCOVERY_METADATA_TABLE_RECORD_COUNT:
        return pe.get_offset_from_rva(
            DISCOVERY_FINAL_COORDINATE_RECORD_VA - pe.OPTIONAL_HEADER.ImageBase
        )
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
        coordinate_table_size = DISCOVERY_METADATA_TABLE_RECORD_COUNT * DISCOVERY_RECORD_SIZE
        if coordinate_table_offset < 0 or coordinate_table_offset + coordinate_table_size > len(data):
            raise ValueError("발견물 좌표 테이블의 범위를 검증하지 못했습니다.")
        final_coordinate_offset = pe.get_offset_from_rva(
            DISCOVERY_FINAL_COORDINATE_RECORD_VA - pe.OPTIONAL_HEADER.ImageBase
        )
        if final_coordinate_offset < 0 or final_coordinate_offset + 16 > len(data):
            raise ValueError("마지막 발견물 좌표 레코드의 범위를 검증하지 못했습니다.")
        description_table_offset = pe.get_offset_from_rva(
            DISCOVERY_DESCRIPTION_POINTER_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
        description_table_size = DISCOVERY_RECORD_COUNT * 4
        if (
            description_table_offset < 0
            or description_table_offset + description_table_size > len(data)
        ):
            raise ValueError("발견물 설명 포인터 테이블의 범위를 검증하지 못했습니다.")
        records: list[DiscoveryRecord] = []
        for identifier in range(DISCOVERY_RECORD_COUNT):
            metadata_offset = (
                metadata_table_offset + identifier * DISCOVERY_RECORD_SIZE
                if identifier < DISCOVERY_METADATA_TABLE_RECORD_COUNT else final_metadata_offset
            )
            coordinate_offset = (
                coordinate_table_offset + identifier * DISCOVERY_RECORD_SIZE
                if identifier < DISCOVERY_METADATA_TABLE_RECORD_COUNT else final_coordinate_offset
            )
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
            description_va = struct.unpack_from(
                "<I", data, description_table_offset + identifier * 4,
            )[0]
            try:
                description_offset = pe.get_offset_from_rva(
                    description_va - pe.OPTIONAL_HEADER.ImageBase
                )
            except pefile.PEFormatError as error:
                raise ValueError(
                    f"발견물 {identifier}번 설명 주소를 검증하지 못했습니다."
                ) from error
            description_end = data.find(
                b"\0", description_offset,
                min(description_offset + DISCOVERY_DESCRIPTION_SLOT_STRIDE, len(data)),
            )
            if not 0 <= description_offset < len(data) or description_end < 0:
                raise ValueError(f"발견물 {identifier}번 설명 주소를 검증하지 못했습니다.")
            try:
                description = data[description_offset:description_end].decode("cp949")
            except UnicodeDecodeError as error:
                raise ValueError(f"발견물 {identifier}번 설명을 읽지 못했습니다.") from error
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
                still_slot, avi_id, animation_part, description,
            ))
        return tuple(records)
    finally:
        pe.close()


def read_discovery_records(target: Path) -> tuple[DiscoveryRecord, ...]:
    """Read EXE discovery definitions; no SAVEDATA.CDS is opened or changed."""
    return _read_discovery_records_from_data(target.resolve(strict=True).read_bytes())


def read_item_discovery_media_links(target: Path) -> dict[int, int]:
    """Return item ID -> discovery number links used for editor previews.

    Normal reward items point back to a discovery through ``record + 0x30``.
    Fake market items store a discovery target code instead, so resolve those
    codes while preferring the verified treasure-media range 103~131 when a
    game code is shared by more than one discovery.
    """
    data = target.resolve(strict=True).read_bytes()
    discoveries = _read_discovery_records_from_data(data)
    pe = pefile.PE(data=data, fast_load=True)
    try:
        links: dict[int, int] = {}
        for discovery in discoveries:
            record_offset = (
                _discovery_metadata_offset(pe, discovery.identifier)
                + DISCOVERY_MEDIA_RECORD_RELATIVE_OFFSET
            )
            reward_item_id = struct.unpack_from(
                "<i", data, record_offset + DISCOVERY_REWARD_ITEM_OFFSET,
            )[0]
            if 0 <= reward_item_id < ITEM_RECORD_COUNT:
                # Preserve the game's later-row precedence for repeated
                # reward item IDs, matching the extracted master mapping.
                links[reward_item_id] = discovery.identifier
    finally:
        pe.close()

    discoveries_by_code: dict[int, DiscoveryRecord] = {}
    for discovery in discoveries:
        current = discoveries_by_code.get(discovery.game_id)
        if current is None or (
            103 <= discovery.identifier <= 131
            and not 103 <= current.identifier <= 131
        ):
            discoveries_by_code[discovery.game_id] = discovery
    for fake_item in _read_fake_item_records_from_data(data):
        discovery = discoveries_by_code.get(fake_item.target_code)
        if discovery is not None:
            links[fake_item.item_id] = discovery.identifier
    return dict(sorted(links.items()))


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
    try:
        description_bytes = edit.description.encode("cp949")
    except UnicodeEncodeError as error:
        raise ValueError("발견물 설명은 CP949에서 사용할 수 있는 문자만 입력할 수 있습니다.") from error
    if len(description_bytes) > DISCOVERY_DESCRIPTION_MAX_BYTES:
        raise ValueError(
            f"발견물 설명은 최대 {DISCOVERY_DESCRIPTION_MAX_BYTES}바이트까지 입력할 수 있습니다."
        )
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
        current.max_x, current.max_y, current.still_slot, current.avi_id,
        current.animation_part, current.description,
    ) == (
        name, edit.category_id, edit.value, edit.min_x, edit.min_y, edit.max_x, edit.max_y,
        edit.still_slot, edit.avi_id, edit.animation_part, edit.description,
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
    if current.avi_id is not None and (
        edit.avi_id is None or not 0 <= edit.avi_id <= DISCOVERY_AVI_MAX
    ):
        raise ValueError(f"발견물 AVI 번호는 0~{DISCOVERY_AVI_MAX} 사이여야 합니다.")
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
        description_table_offset = pe.get_offset_from_rva(
            DISCOVERY_DESCRIPTION_POINTER_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
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
    if current.description != edit.description:
        section, _created = ensure_patch_section(
            data, PATCH_SECTION_DISCOVERY_DESCRIPTIONS_SIZE,
        )
        slot_offset, slot_va = section.slot(
            DISCOVERY_DESCRIPTION_SLOT_OFFSET
            + edit.identifier * DISCOVERY_DESCRIPTION_SLOT_STRIDE,
            DISCOVERY_DESCRIPTION_SLOT_STRIDE,
        )
        data[slot_offset:slot_offset + DISCOVERY_DESCRIPTION_SLOT_STRIDE] = (
            b"\0" * DISCOVERY_DESCRIPTION_SLOT_STRIDE
        )
        data[slot_offset:slot_offset + len(description_bytes) + 1] = (
            description_bytes + b"\0"
        )
        struct.pack_into(
            "<I", data, description_table_offset + edit.identifier * 4, slot_va,
        )
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


def read_discover_avi_patch_state(data: bytes) -> bool:
    """Return whether all 29 target discoveries point to their bundled AVIs.

    Other per-discovery media edits are valid editor data, not a corrupt
    partial patch, so they simply report the combined option as disabled.
    """
    records = _read_discovery_records_from_data(data)
    states: list[bool] = []
    for index, animation_part in enumerate(DISCOVER_AVI_RECORD_PARTS):
        identifier = DISCOVER_AVI_FIRST_RECORD_ID + index
        record = records[identifier]
        avi_id = DISCOVER_AVI_FIRST_ID + animation_part
        patched = (
            record.still_slot is None
            and record.avi_id == avi_id
            and record.animation_part is None
        )
        states.append(patched)
    return all(states)


def apply_discover_avi_patch(data: bytearray, enabled: bool) -> bool:
    """Switch the 29 original DISCOVER media links to I70~I98 or restore them."""
    if read_discover_avi_patch_state(bytes(data)) == enabled:
        return False
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        for index, animation_part in enumerate(DISCOVER_AVI_RECORD_PARTS):
            identifier = DISCOVER_AVI_FIRST_RECORD_ID + index
            metadata_offset = _discovery_metadata_offset(pe, identifier)
            media_offset = metadata_offset + DISCOVERY_MEDIA_RECORD_RELATIVE_OFFSET
            avi_value = DISCOVER_AVI_FIRST_ID + animation_part if enabled else NO_DISCOVERY_MEDIA
            animation_value = NO_DISCOVERY_MEDIA if enabled else animation_part
            struct.pack_into(
                "<I", data, media_offset + DISCOVERY_MEDIA_AVI_OFFSET, avi_value,
            )
            struct.pack_into(
                "<I", data, media_offset + DISCOVERY_MEDIA_ANIMATION_OFFSET, animation_value,
            )
    finally:
        pe.close()
    if read_discover_avi_patch_state(bytes(data)) != enabled:
        raise ValueError("DISCOVER 대신 AVI 사용 패치의 저장 후 검증에 실패했습니다.")
    return True


def _library_hint_condition_offset(
    pe: pefile.PE,
    hint_id: int,
    data_size: int,
) -> int:
    if not 0 <= hint_id < LIBRARY_HINT_RECORD_COUNT:
        raise ValueError("도서관 힌트 ID가 범위를 벗어났습니다.")
    table_offset = pe.get_offset_from_rva(
        LIBRARY_HINT_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
    )
    table_size = LIBRARY_HINT_RECORD_COUNT * LIBRARY_HINT_RECORD_SIZE
    if table_offset < 0 or table_offset + table_size > data_size:
        raise ValueError("도서관 힌트 마스터 테이블의 범위를 검증하지 못했습니다.")
    return table_offset + hint_id * LIBRARY_HINT_RECORD_SIZE


def _read_discovery_hint_edit_from_data(
    data: bytes,
    hint_id: int,
) -> DiscoveryHintEdit:
    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        offset = _library_hint_condition_offset(pe, hint_id, len(data))
        text_pointer_table_offset = pe.get_offset_from_rva(
            LIBRARY_HINT_TEXT_POINTER_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
        text_pointer_table_size = LIBRARY_HINT_TEXT_POINTER_COUNT * 4
        if (
            text_pointer_table_offset < 0
            or text_pointer_table_offset + text_pointer_table_size > len(data)
        ):
            raise ValueError("도서관 힌트 본문 포인터 테이블의 범위를 검증하지 못했습니다.")
        name_va = struct.unpack_from(
            "<I", data, offset + LIBRARY_HINT_NAME_POINTER_OFFSET,
        )[0]
        try:
            name_offset = pe.get_offset_from_rva(name_va - pe.OPTIONAL_HEADER.ImageBase)
        except pefile.PEFormatError as error:
            raise ValueError(f"도서관 힌트 {hint_id}번 이름 주소를 검증하지 못했습니다.") from error
        name_end = data.find(b"\0", name_offset, min(name_offset + 80, len(data)))
        if not 0 <= name_offset < len(data) or name_end < 0:
            raise ValueError(f"도서관 힌트 {hint_id}번 이름 주소를 검증하지 못했습니다.")
        try:
            name = data[name_offset:name_end].decode("cp949")
        except UnicodeDecodeError as error:
            raise ValueError(f"도서관 힌트 {hint_id}번 이름을 읽지 못했습니다.") from error
        target_id = struct.unpack_from(
            "<I", data, offset + LIBRARY_HINT_TARGET_ID_OFFSET,
        )[0]
        required_skill_id = struct.unpack_from(
            "<i", data, offset + LIBRARY_HINT_REQUIRED_SKILL_OFFSET,
        )[0]
        required_language_id = struct.unpack_from(
            "<i", data, offset + LIBRARY_HINT_REQUIRED_LANGUAGE_OFFSET,
        )[0]
        required_level = struct.unpack_from(
            "<i", data, offset + LIBRARY_HINT_REQUIRED_LEVEL_OFFSET,
        )[0]
        prerequisite_discovery_ids = tuple(
            prerequisite_id
            for prerequisite_id in struct.unpack_from(
                f"<{LIBRARY_HINT_PREREQUISITE_CAPACITY}i",
                data,
                offset + LIBRARY_HINT_PREREQUISITE_LIST_OFFSET,
            )
            if prerequisite_id >= 0
        )
        text_id = struct.unpack_from(
            "<i", data, offset + LIBRARY_HINT_TEXT_ID_OFFSET,
        )[0]
        if not 0 <= text_id < LIBRARY_HINT_TEXT_POINTER_COUNT:
            raise ValueError(f"도서관 힌트 {hint_id}번 본문 ID를 검증하지 못했습니다.")
        text_va = struct.unpack_from(
            "<I", data, text_pointer_table_offset + text_id * 4,
        )[0]
        try:
            text_offset = pe.get_offset_from_rva(text_va - pe.OPTIONAL_HEADER.ImageBase)
        except pefile.PEFormatError as error:
            raise ValueError(f"도서관 힌트 {hint_id}번 본문 주소를 검증하지 못했습니다.") from error
        text_end = data.find(
            b"\0", text_offset,
            min(text_offset + LIBRARY_HINT_TEXT_SLOT_STRIDE, len(data)),
        )
        if not 0 <= text_offset < len(data) or text_end < 0:
            raise ValueError(f"도서관 힌트 {hint_id}번 본문 주소를 검증하지 못했습니다.")
        try:
            text = data[text_offset:text_end].decode("cp949")
        except UnicodeDecodeError as error:
            raise ValueError(f"도서관 힌트 {hint_id}번 본문을 읽지 못했습니다.") from error
        return DiscoveryHintEdit(
            hint_id,
            name,
            target_id,
            required_skill_id,
            required_language_id,
            required_level,
            prerequisite_discovery_ids,
            text,
        )
    finally:
        pe.close()


def apply_discovery_hint_edit(
    data: bytearray,
    edit: DiscoveryHintEdit | None,
) -> bool:
    """Update one library hint without changing its sources or target rows."""
    if edit is None:
        return False
    name = edit.name.strip()
    if not name:
        raise ValueError("힌트 이름을 입력해 주세요.")
    try:
        name_bytes = name.encode("cp949")
    except UnicodeEncodeError as error:
        raise ValueError("힌트 이름은 CP949에서 사용할 수 있는 문자만 입력할 수 있습니다.") from error
    if (
        len(name) > LIBRARY_HINT_NAME_MAX_CHARACTERS
        or len(name_bytes) > LIBRARY_HINT_NAME_MAX_BYTES
    ):
        raise ValueError(
            "힌트 이름은 최대 18자·CP949 36바이트까지 입력할 수 있습니다."
        )
    text = edit.text.strip()
    if not text:
        raise ValueError("힌트 본문을 입력해 주세요.")
    try:
        text_bytes = text.encode("cp949")
    except UnicodeEncodeError as error:
        raise ValueError("힌트 본문은 CP949에서 사용할 수 있는 문자만 입력할 수 있습니다.") from error
    if len(text_bytes) > LIBRARY_HINT_TEXT_MAX_BYTES:
        raise ValueError(
            f"힌트 본문은 최대 {LIBRARY_HINT_TEXT_MAX_BYTES}바이트까지 입력할 수 있습니다."
        )
    prerequisites = tuple(edit.prerequisite_discovery_ids)
    if (
        not 0 <= edit.hint_id < LIBRARY_HINT_RECORD_COUNT
        or not 0 <= edit.target_id <= 0xFFFFFFFF
        or not LIBRARY_HINT_SKILL_MIN <= edit.required_skill_id <= LIBRARY_HINT_SKILL_MAX
        or not LIBRARY_HINT_LANGUAGE_MIN <= edit.required_language_id <= LIBRARY_HINT_LANGUAGE_MAX
        or not LIBRARY_HINT_LEVEL_MIN <= edit.required_level <= LIBRARY_HINT_LEVEL_MAX
        or len(prerequisites) > LIBRARY_HINT_PREREQUISITE_CAPACITY
        or len(set(prerequisites)) != len(prerequisites)
        or any(
            not LIBRARY_HINT_DISCOVERY_MIN <= prerequisite_id <= LIBRARY_HINT_DISCOVERY_MAX
            for prerequisite_id in prerequisites
        )
    ):
        raise ValueError("힌트 입력값을 확인해 주세요.")
    normalized = DiscoveryHintEdit(
        edit.hint_id,
        name,
        edit.target_id,
        edit.required_skill_id,
        edit.required_language_id,
        edit.required_level,
        prerequisites,
        text,
    )
    current = _read_discovery_hint_edit_from_data(bytes(data), edit.hint_id)
    if current == normalized:
        return False
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        offset = _library_hint_condition_offset(pe, edit.hint_id, len(data))
        text_pointer_table_offset = pe.get_offset_from_rva(
            LIBRARY_HINT_TEXT_POINTER_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
        text_id = struct.unpack_from(
            "<i", data, offset + LIBRARY_HINT_TEXT_ID_OFFSET,
        )[0]
        if not 0 <= text_id < LIBRARY_HINT_TEXT_POINTER_COUNT:
            raise ValueError("도서관 힌트 본문 ID를 검증하지 못했습니다.")
    finally:
        pe.close()
    if current.name != name:
        section, _created = ensure_patch_section(
            data, PATCH_SECTION_LIBRARY_HINT_NAMES_SIZE,
        )
        slot_offset, slot_va = section.slot(
            LIBRARY_HINT_NAME_SLOT_OFFSET
            + edit.hint_id * LIBRARY_HINT_NAME_SLOT_STRIDE,
            LIBRARY_HINT_NAME_SLOT_STRIDE,
        )
        data[slot_offset:slot_offset + LIBRARY_HINT_NAME_SLOT_STRIDE] = (
            b"\0" * LIBRARY_HINT_NAME_SLOT_STRIDE
        )
        data[slot_offset:slot_offset + len(name_bytes) + 1] = name_bytes + b"\0"
        struct.pack_into(
            "<I", data, offset + LIBRARY_HINT_NAME_POINTER_OFFSET, slot_va,
        )
    if current.text != text:
        section, _created = ensure_patch_section(
            data, PATCH_SECTION_LIBRARY_HINT_TEXTS_SIZE,
        )
        slot_offset, slot_va = section.slot(
            LIBRARY_HINT_TEXT_SLOT_OFFSET + text_id * LIBRARY_HINT_TEXT_SLOT_STRIDE,
            LIBRARY_HINT_TEXT_SLOT_STRIDE,
        )
        data[slot_offset:slot_offset + LIBRARY_HINT_TEXT_SLOT_STRIDE] = (
            b"\0" * LIBRARY_HINT_TEXT_SLOT_STRIDE
        )
        data[slot_offset:slot_offset + len(text_bytes) + 1] = text_bytes + b"\0"
        struct.pack_into(
            "<I", data, text_pointer_table_offset + text_id * 4, slot_va,
        )
    struct.pack_into(
        "<I", data, offset + LIBRARY_HINT_TARGET_ID_OFFSET, edit.target_id,
    )
    struct.pack_into(
        "<i", data, offset + LIBRARY_HINT_REQUIRED_SKILL_OFFSET,
        edit.required_skill_id,
    )
    struct.pack_into(
        "<i", data, offset + LIBRARY_HINT_REQUIRED_LANGUAGE_OFFSET,
        edit.required_language_id,
    )
    struct.pack_into(
        "<i", data, offset + LIBRARY_HINT_REQUIRED_LEVEL_OFFSET,
        edit.required_level,
    )
    padded_prerequisites = prerequisites + (-1,) * (
        LIBRARY_HINT_PREREQUISITE_CAPACITY - len(prerequisites)
    )
    struct.pack_into(
        f"<{LIBRARY_HINT_PREREQUISITE_CAPACITY}i",
        data,
        offset + LIBRARY_HINT_PREREQUISITE_LIST_OFFSET,
        *padded_prerequisites,
    )
    if _read_discovery_hint_edit_from_data(bytes(data), edit.hint_id) != normalized:
        raise ValueError("힌트 정보의 저장 후 검증에 실패했습니다.")
    return True


def _read_library_book_edit_from_data(
    data: bytes,
    record_number: int,
) -> LibraryBookEdit:
    if not 0 <= record_number < LIBRARY_BOOK_RECORD_COUNT:
        raise ValueError("도서관 책 레코드 번호가 범위를 벗어났습니다.")
    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        table_offset = pe.get_offset_from_rva(
            LIBRARY_BOOK_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
        table_size = LIBRARY_BOOK_RECORD_COUNT * LIBRARY_BOOK_RECORD_SIZE
        if table_offset < 0 or table_offset + table_size > len(data):
            raise ValueError("도서관 책 테이블의 범위를 검증하지 못했습니다.")
        record_offset = table_offset + record_number * LIBRARY_BOOK_RECORD_SIZE
        def read_text(pointer_field: int, field_name: str) -> str:
            text_va = struct.unpack_from("<I", data, pointer_field)[0]
            try:
                text_offset = pe.get_offset_from_rva(
                    text_va - pe.OPTIONAL_HEADER.ImageBase
                )
            except pefile.PEFormatError as error:
                raise ValueError(
                    f"도서관 책 {record_number}번 {field_name} 주소를 검증하지 못했습니다."
                ) from error
            text_end = data.find(
                b"\0", text_offset, min(text_offset + 80, len(data)),
            )
            if not 0 <= text_offset < len(data) or text_end < 0:
                raise ValueError(
                    f"도서관 책 {record_number}번 {field_name} 주소를 검증하지 못했습니다."
                )
            try:
                text = data[text_offset:text_end].decode("cp949")
            except UnicodeDecodeError as error:
                raise ValueError(
                    f"도서관 책 {record_number}번 {field_name}을 읽지 못했습니다."
                ) from error
            if not text:
                raise ValueError(
                    f"도서관 책 {record_number}번 {field_name}이 비어 있습니다."
                )
            return text

        title = read_text(
            record_offset + LIBRARY_BOOK_TITLE_POINTER_OFFSET, "제목",
        )
        author = read_text(
            record_offset + LIBRARY_BOOK_AUTHOR_POINTER_OFFSET, "저자",
        )
        appearance_year = LIBRARY_BOOK_YEAR_BASE + struct.unpack_from(
            "<i", data, record_offset + LIBRARY_BOOK_APPEARANCE_YEAR_OFFSET,
        )[0]
        raw_city_ids = struct.unpack_from(
            f"<{LIBRARY_BOOK_CITY_CAPACITY}i",
            data,
            record_offset + LIBRARY_BOOK_CITY_LIST_OFFSET,
        )
        if any(not -1 <= city_id < CITY_RECORD_COUNT for city_id in raw_city_ids):
            raise ValueError(
                f"도서관 책 {record_number}번 출현 도시를 검증하지 못했습니다."
            )
        city_ids = tuple(city_id for city_id in raw_city_ids if city_id >= 0)
        if len(set(city_ids)) != len(city_ids):
            raise ValueError(
                f"도서관 책 {record_number}번 출현 도시에 중복값이 있습니다."
            )
        return LibraryBookEdit(record_number, title, author, city_ids, appearance_year)
    finally:
        pe.close()


def _read_library_book_title_from_data(data: bytes, record_number: int) -> str:
    """Compatibility helper used by earlier title-only callers and tests."""
    return _read_library_book_edit_from_data(data, record_number).title


def apply_library_book_edits(
    data: bytearray,
    edits: tuple[LibraryBookEdit, ...],
) -> bool:
    """Update library-book title, author, appearance year and eight city slots."""
    if not edits:
        return False
    if len({edit.record_number for edit in edits}) != len(edits):
        raise ValueError("같은 도서관 책의 수정이 중복되었습니다.")
    normalized: list[tuple[LibraryBookEdit, bytes, bytes, LibraryBookEdit]] = []
    for edit in edits:
        title = edit.title.strip()
        author = edit.author.strip()
        if not title:
            raise ValueError("책 제목을 입력해 주세요.")
        if not author:
            raise ValueError("책 저자를 입력해 주세요.")
        try:
            title_bytes = title.encode("cp949")
        except UnicodeEncodeError as error:
            raise ValueError("책 제목은 CP949에서 사용할 수 있는 문자만 입력할 수 있습니다.") from error
        try:
            author_bytes = author.encode("cp949")
        except UnicodeEncodeError as error:
            raise ValueError("책 저자는 CP949에서 사용할 수 있는 문자만 입력할 수 있습니다.") from error
        if (
            not 0 <= edit.record_number < LIBRARY_BOOK_RECORD_COUNT
            or len(title) > LIBRARY_BOOK_TITLE_MAX_CHARACTERS
            or len(title_bytes) > LIBRARY_BOOK_TITLE_MAX_BYTES
        ):
            raise ValueError("책 제목은 최대 18자·CP949 36바이트까지 입력할 수 있습니다.")
        if (
            len(author) > LIBRARY_BOOK_AUTHOR_MAX_CHARACTERS
            or len(author_bytes) > LIBRARY_BOOK_AUTHOR_MAX_BYTES
        ):
            raise ValueError("책 저자는 최대 18자·CP949 36바이트까지 입력할 수 있습니다.")
        city_ids = tuple(edit.city_ids)
        if (
            len(city_ids) > LIBRARY_BOOK_CITY_CAPACITY
            or len(set(city_ids)) != len(city_ids)
            or any(not 0 <= city_id < CITY_RECORD_COUNT for city_id in city_ids)
        ):
            raise ValueError("책 출현 도시는 중복 없이 최대 8곳까지 설정할 수 있습니다.")
        if not LIBRARY_BOOK_YEAR_BASE <= edit.appearance_year <= LIBRARY_BOOK_YEAR_MAX:
            raise ValueError("책 출현 연도는 1480~1600 사이여야 합니다.")
        normalized_edit = LibraryBookEdit(
            edit.record_number, title, author, city_ids, edit.appearance_year,
        )
        current = _read_library_book_edit_from_data(bytes(data), edit.record_number)
        if current != normalized_edit:
            normalized.append(
                (normalized_edit, title_bytes, author_bytes, current)
            )
    if not normalized:
        return False

    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        table_offset = pe.get_offset_from_rva(
            LIBRARY_BOOK_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
    finally:
        pe.close()
    string_changes = any(
        edit.title != current.title or edit.author != current.author
        for edit, _title_bytes, _author_bytes, current in normalized
    )
    section = None
    if string_changes:
        section, _created = ensure_patch_section(
            data, PATCH_SECTION_LIBRARY_BOOKS_SIZE,
        )
    for edit, title_bytes, author_bytes, current in normalized:
        record_offset = table_offset + edit.record_number * LIBRARY_BOOK_RECORD_SIZE
        if edit.title != current.title:
            assert section is not None
            slot_offset, slot_va = section.slot(
                LIBRARY_BOOK_TITLE_SLOT_OFFSET
                + edit.record_number * LIBRARY_BOOK_TITLE_SLOT_STRIDE,
                LIBRARY_BOOK_TITLE_SLOT_STRIDE,
            )
            data[slot_offset:slot_offset + LIBRARY_BOOK_TITLE_SLOT_STRIDE] = (
                b"\0" * LIBRARY_BOOK_TITLE_SLOT_STRIDE
            )
            data[slot_offset:slot_offset + len(title_bytes) + 1] = title_bytes + b"\0"
            struct.pack_into(
                "<I", data,
                record_offset + LIBRARY_BOOK_TITLE_POINTER_OFFSET,
                slot_va,
            )
        if edit.author != current.author:
            assert section is not None
            slot_offset, slot_va = section.slot(
                LIBRARY_BOOK_AUTHOR_SLOT_OFFSET
                + edit.record_number * LIBRARY_BOOK_AUTHOR_SLOT_STRIDE,
                LIBRARY_BOOK_AUTHOR_SLOT_STRIDE,
            )
            data[slot_offset:slot_offset + LIBRARY_BOOK_AUTHOR_SLOT_STRIDE] = (
                b"\0" * LIBRARY_BOOK_AUTHOR_SLOT_STRIDE
            )
            data[slot_offset:slot_offset + len(author_bytes) + 1] = author_bytes + b"\0"
            struct.pack_into(
                "<I", data,
                record_offset + LIBRARY_BOOK_AUTHOR_POINTER_OFFSET,
                slot_va,
            )
        if edit.appearance_year != current.appearance_year:
            struct.pack_into(
                "<i", data,
                record_offset + LIBRARY_BOOK_APPEARANCE_YEAR_OFFSET,
                edit.appearance_year - LIBRARY_BOOK_YEAR_BASE,
            )
        if edit.city_ids != current.city_ids:
            padded_city_ids = edit.city_ids + (-1,) * (
                LIBRARY_BOOK_CITY_CAPACITY - len(edit.city_ids)
            )
            struct.pack_into(
                f"<{LIBRARY_BOOK_CITY_CAPACITY}i",
                data,
                record_offset + LIBRARY_BOOK_CITY_LIST_OFFSET,
                *padded_city_ids,
            )
    for edit, _title_bytes, _author_bytes, _current in normalized:
        if _read_library_book_edit_from_data(bytes(data), edit.record_number) != edit:
            raise ValueError("도서관 책 정보의 저장 후 검증에 실패했습니다.")
    return True


def _read_hint_records_from_data(data: bytes) -> tuple[HintRecord, ...]:
    """Read the 191-row EXE tavern-hint table.

    Each row stores a discovery/event target code, four signed city IDs and a
    pointer to a CP949 string.  ``-1`` marks an unused city slot.
    """
    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        table_offset = pe.get_offset_from_rva(HINT_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase)
        table_size = HINT_RECORD_COUNT * HINT_RECORD_SIZE
        if table_offset < 0 or table_offset + table_size > len(data):
            raise ValueError("힌트 마스터 테이블의 범위를 검증하지 못했습니다.")
        records: list[HintRecord] = []
        for identifier in range(HINT_RECORD_COUNT):
            offset = table_offset + identifier * HINT_RECORD_SIZE
            target_code = struct.unpack_from("<I", data, offset + HINT_TARGET_CODE_OFFSET)[0]
            city_ids = struct.unpack_from(
                f"<{HINT_CITY_COUNT}i", data, offset + HINT_CITY_IDS_OFFSET,
            )
            text_va = struct.unpack_from("<I", data, offset + HINT_TEXT_POINTER_OFFSET)[0]
            try:
                text_offset = pe.get_offset_from_rva(text_va - pe.OPTIONAL_HEADER.ImageBase)
            except pefile.PEFormatError as error:
                raise ValueError(f"힌트 {identifier}번 본문 주소를 검증하지 못했습니다.") from error
            text_end = data.find(
                b"\0", text_offset, min(text_offset + HINT_TEXT_SLOT_STRIDE, len(data)),
            )
            if not 0 <= text_offset < len(data) or text_end < 0:
                raise ValueError(f"힌트 {identifier}번 본문 주소를 검증하지 못했습니다.")
            try:
                text = data[text_offset:text_end].decode("cp949")
            except UnicodeDecodeError as error:
                raise ValueError(f"힌트 {identifier}번 본문을 읽지 못했습니다.") from error
            if (
                not text
                or any(not -1 <= city_id < CITY_RECORD_COUNT for city_id in city_ids)
            ):
                raise ValueError(f"힌트 {identifier}번 마스터 값을 검증하지 못했습니다.")
            records.append(HintRecord(identifier, target_code, city_ids, text))
        return tuple(records)
    finally:
        pe.close()


def read_hint_records(target: Path) -> tuple[HintRecord, ...]:
    """Read EXE-side tavern hints without touching SAVEDATA.CDS."""
    return _read_hint_records_from_data(target.resolve(strict=True).read_bytes())


def apply_hint_edit(data: bytearray, edit: HintEdit | None) -> bool:
    """Update one tavern hint and redirect changed text into ``.patch``."""
    if edit is None:
        return False
    text = edit.text.strip()
    if not text:
        raise ValueError("힌트 본문을 입력해 주세요.")
    try:
        text_bytes = text.encode("cp949")
    except UnicodeEncodeError as error:
        raise ValueError("힌트 본문은 CP949에서 사용할 수 있는 문자만 입력할 수 있습니다.") from error
    if len(text_bytes) > HINT_TEXT_MAX_BYTES:
        raise ValueError(f"힌트 본문은 최대 {HINT_TEXT_MAX_BYTES}바이트까지 입력할 수 있습니다.")
    if (
        not 0 <= edit.identifier < HINT_RECORD_COUNT
        or not 0 <= edit.target_code <= 0xFFFFFFFF
        or len(edit.city_ids) != HINT_CITY_COUNT
        or any(not -1 <= city_id < CITY_RECORD_COUNT for city_id in edit.city_ids)
    ):
        raise ValueError("힌트 입력값을 확인해 주세요.")
    current = _read_hint_records_from_data(bytes(data))[edit.identifier]
    expected = HintRecord(edit.identifier, edit.target_code, edit.city_ids, text)
    if current == expected:
        return False
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        offset = (
            pe.get_offset_from_rva(HINT_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase)
            + edit.identifier * HINT_RECORD_SIZE
        )
    finally:
        pe.close()
    struct.pack_into("<I", data, offset + HINT_TARGET_CODE_OFFSET, edit.target_code)
    struct.pack_into(
        f"<{HINT_CITY_COUNT}i", data, offset + HINT_CITY_IDS_OFFSET, *edit.city_ids,
    )
    if current.text != text:
        section, _created = ensure_patch_section(data, PATCH_SECTION_HINT_TEXTS_SIZE)
        slot_offset, slot_va = section.slot(
            HINT_TEXT_SLOT_OFFSET + edit.identifier * HINT_TEXT_SLOT_STRIDE,
            HINT_TEXT_SLOT_STRIDE,
        )
        data[slot_offset:slot_offset + HINT_TEXT_SLOT_STRIDE] = b"\0" * HINT_TEXT_SLOT_STRIDE
        data[slot_offset:slot_offset + len(text_bytes) + 1] = text_bytes + b"\0"
        struct.pack_into("<I", data, offset + HINT_TEXT_POINTER_OFFSET, slot_va)
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
    str, tuple[tuple[int, int], ...], int, int, bool, int, int, int, int, int,
    int, int, int, int, int, int, int, bool, PirateVarietySettings, bool, Decimal, bool,
    bool, bool, bool, bool, bool, bool, bool, bool, bool,
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
            _npc_daily_departure_patch_info(data),
            *gameplay, *activity_ages, *encounters, pirate_variety, pirate_settings,
            eclipse_enabled, eclipse_latitude,
            read_mistranslation_patch_state(data),
            read_failed_pottery_patch_state(data),
            _judgment_fix_patch_info(data),
            _cannon_accuracy_fix_patch_info(data),
            _ship_reuse_fix_patch_info(data),
            _ship_purchase_blank_selection_fix_patch_info(data),
            read_tavern_hint_bug_fix_state(data),
            _disev_language_fix_patch_info(data),
            _history_elapsed_years_fix_patch_info(data),
            read_discover_avi_patch_state(data),
            read_save_slot_selector_patch_state(data),
            read_load_slot_selector_patch_state(data),
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
    npc_daily_departure_enabled: bool,
    long_rest_max: int,
    exploration_preparation_days: int,
    telescope_city_discovery_bonus: int,
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
    failed_pottery_enabled: bool = False,
    judgment_fix_enabled: bool = False,
    cannon_accuracy_fix_enabled: bool = False,
    ship_reuse_fix_enabled: bool = False,
    ship_purchase_blank_selection_fix_enabled: bool = False,
    tavern_hint_bug_fix_enabled: bool = False,
    disev_language_fix_enabled: bool = False,
    history_elapsed_years_fix_enabled: bool = False,
    discover_avi_enabled: bool = False,
    save_slot_selector_enabled: bool = False,
    load_slot_selector_enabled: bool = False,
    figurehead_effect_settings: FigureheadEffectSettings = DEFAULT_FIGUREHEAD_EFFECT_SETTINGS,
    barmaid_edit: BarmaidEdit | None = None,
    sponsor_edit: SponsorEdit | None = None,
    person_edit: PersonEdit | None = None,
    ship_type_edit: ShipTypeEdit | None = None,
    city_edit: CityEdit | None = None,
    trade_region_goods: tuple[tuple[int, ...], ...] | None = None,
    item_edit: ItemEdit | None = None,
    fake_item_edit: FakeItemEdit | None = None,
    discovery_edit: DiscoveryEdit | None = None,
    hint_edit: HintEdit | None = None,
    discovery_hint_edit: DiscoveryHintEdit | None = None,
    library_book_edits: tuple[LibraryBookEdit, ...] = (),
    person_ability_limit: int | None = None,
    person_vitality_limit: int | None = None,
) -> Path | None:
    """Apply all selected settings atomically and create one original backup."""
    target = target.resolve(strict=True)
    original = target.read_bytes()
    before_coordinate = bytearray(original)
    failed_pottery_was_enabled = read_failed_pottery_patch_state(original)
    judgment_fix_was_enabled = _judgment_fix_patch_info(original)
    ship_reuse_fix_was_enabled = _ship_reuse_fix_patch_info(original)
    ship_purchase_blank_selection_fix_was_enabled = _ship_purchase_blank_selection_fix_patch_info(original)
    tavern_hint_bug_fix_was_enabled = read_tavern_hint_bug_fix_state(original)
    history_elapsed_years_fix_was_enabled = _history_elapsed_years_fix_patch_info(original)
    discover_avi_was_enabled = read_discover_avi_patch_state(original)
    save_slot_selector_was_enabled = read_save_slot_selector_patch_state(original)
    load_slot_selector_was_enabled = read_load_slot_selector_patch_state(original)
    # Coordinate-style restoration may clear extensions after its own payload.
    # Temporarily remove relocatable patches, apply the requested coordinate
    # style, then recreate all selected payloads in their reserved slots.
    if _pirate_selection_patch_info(original)[0]:
        apply_pirate_variety(before_coordinate, False)
    if read_mistranslation_patch_state(bytes(before_coordinate)):
        apply_mistranslation_fixes(before_coordinate, False)
    if failed_pottery_was_enabled:
        apply_failed_pottery_patch(before_coordinate, False)
    if judgment_fix_was_enabled:
        apply_judgment_fix(before_coordinate, False)
    if ship_reuse_fix_was_enabled:
        apply_ship_reuse_fix(before_coordinate, False)
    if ship_purchase_blank_selection_fix_was_enabled:
        apply_ship_purchase_blank_selection_fix(before_coordinate, False)
    if tavern_hint_bug_fix_was_enabled:
        apply_tavern_hint_bug_fix(before_coordinate, False)
    if history_elapsed_years_fix_was_enabled:
        apply_history_elapsed_years_fix(before_coordinate, False)
    if save_slot_selector_was_enabled:
        apply_save_slot_selector_patch(before_coordinate, False)
    if load_slot_selector_was_enabled:
        apply_load_slot_selector_patch(before_coordinate, False)
    if _eclipse_patch_info(bytes(before_coordinate))[0]:
        apply_eclipse_polar_caps(before_coordinate, False)
    if _npc_daily_departure_patch_info(before_coordinate):
        apply_npc_daily_departure(before_coordinate, False)
    updated = bytearray(_coordinate_bytes(bytes(before_coordinate), target, coordinate_style))
    if set_resolution:
        apply_resolution(updated, presets)
    apply_npc_travel(updated, departure_denominator, arrival_wait_days)
    apply_npc_daily_departure(updated, npc_daily_departure_enabled)
    apply_judgment_fix(updated, judgment_fix_enabled)
    apply_cannon_accuracy_fix(updated, cannon_accuracy_fix_enabled)
    apply_ship_reuse_fix(updated, ship_reuse_fix_enabled)
    apply_ship_purchase_blank_selection_fix(updated, ship_purchase_blank_selection_fix_enabled)
    apply_disev_language_fix(updated, disev_language_fix_enabled)
    apply_history_elapsed_years_fix(updated, history_elapsed_years_fix_enabled)
    apply_gameplay_options(
        updated,
        long_rest_max,
        exploration_preparation_days,
        telescope_city_discovery_bonus,
        succession_min_age,
        cold_north_limit,
        cold_south_limit,
        cash_limit,
        deposit_limit,
        fame_limit,
        infamy_limit,
    )
    if person_ability_limit is not None or person_vitality_limit is not None:
        if person_ability_limit is None or person_vitality_limit is None:
            raise ValueError("능력치·생명력 상한은 함께 지정해야 합니다.")
        apply_person_stat_limits(updated, person_ability_limit, person_vitality_limit)
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
    apply_fake_item_edit(updated, fake_item_edit)
    apply_discovery_edit(updated, discovery_edit)
    apply_hint_edit(updated, hint_edit)
    apply_discovery_hint_edit(updated, discovery_hint_edit)
    apply_library_book_edits(updated, library_book_edits)
    # Coordinated bug fixes intentionally win over direct edits to their rows
    # so a checked feature can never be saved half-applied.
    if failed_pottery_enabled or failed_pottery_was_enabled:
        apply_failed_pottery_patch(updated, failed_pottery_enabled)
    if tavern_hint_bug_fix_enabled or tavern_hint_bug_fix_was_enabled:
        apply_tavern_hint_bug_fix(updated, tavern_hint_bug_fix_enabled)
    if discover_avi_enabled or discover_avi_was_enabled:
        apply_discover_avi_patch(updated, discover_avi_enabled)
    if save_slot_selector_enabled or save_slot_selector_was_enabled:
        apply_save_slot_selector_patch(updated, save_slot_selector_enabled)
    if load_slot_selector_enabled or load_slot_selector_was_enabled:
        apply_load_slot_selector_patch(updated, load_slot_selector_enabled)
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
