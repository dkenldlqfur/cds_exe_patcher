"""Unified GUI for the supported CDS III executable patches."""

from __future__ import annotations

import json
import sys
import threading
import tkinter as tk
import ctypes
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

# 보조 모듈은 Resources/py에 둔다. 소스 실행과 PyInstaller one-file
# 배포본 모두에서 동일한 리소스 위치를 사용한다.
_runtime_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
sys.path.insert(0, str(_runtime_root / "Resources" / "py"))
from app_update import GitHubReleaseUpdater, UpdateError, bundled_resource_path, load_update_config
from kaaba_patch import KaabaPatchError, apply as apply_kaaba_patch, is_enabled as is_kaaba_enabled
from kaaba_save_patch import KaabaSavePatchError, promote_game_savedata
from slave_patch import (
    SlavePatchError,
    apply_dialogue as apply_slave_dialogue,
    apply_library_hint as apply_slave_library_hint,
    is_dialogue_enabled as is_slave_dialogue_enabled,
    is_library_enabled as is_slave_library_enabled,
)
from mughal_patch import MughalPatchError, apply as apply_mughal_patch, is_enabled as is_mughal_enabled
from avi_preview import AviPreview
from city_reader import CityImageReadError, read_city_image
from discover_animation_preview import DiscoverAnimationPreview
from discovery_reader import DiscoveryImageReadError, discovery_still_count, read_discovery_still
from item_reader import ItemImageReadError, read_item_image
from portrait_reader import PortraitReadError, portrait_count, read_portrait

from patch_cds_integrated import (
    BarmaidEdit,
    BarmaidChildAptitudes,
    BarmaidRecord,
    SponsorEdit,
    SponsorRecord,
    PersonEdit,
    PersonRecord,
    ShipTypeEdit,
    ShipTypeRecord,
    CityEdit,
    CityRecord,
    ItemEdit,
    ItemRecord,
    DiscoveryEdit,
    DiscoveryRecord,
    FigureheadEffectSettings,
    COLD_LIMIT_DISABLED_VALUE,
    PirateVarietySettings,
    apply_all,
    cold_limit_to_latitude,
    latitude_to_world_y,
    longitude_to_world_x,
    get_screen_bounds,
    latitude_to_cold_limit,
    read_barmaid_child_aptitudes,
    read_barmaid_records,
    read_sponsor_records,
    read_person_records,
    read_ship_type_records,
    read_city_records,
    read_trade_good_names,
    read_trade_region_goods,
    read_item_records,
    read_discovery_records,
    read_figurehead_effect_settings,
    read_settings,
    world_x_to_longitude,
    world_y_to_latitude,
)


# GitHub Releases 저장소를 설정하면 시작 시 비동기로 업데이트를 확인한다.
APP_UPDATE_CONFIG = load_update_config()
APP_VERSION = APP_UPDATE_CONFIG.version


MISTRANSLATION_DETAILS = """by kseokjung, 오쌍, ladyous

[용어·문장]
선두상 → 선수상
놓아 가고 → 놓아 두고
사교 / 사교좌 → 주교 / 주교좌
예하 → 성하
웅변 → 변론
규칙 → 규율
깨진 일본어 문장 → 그런 말도 안되는…이 자식 그거 누구한테 들었어！

[지명]
르완다 / 르완다항 → 루안다 / 루안다항

[아이템·발견물]
군관조 / 큰군관조 → 군함조 / 큰군함조
식충동물 → 식충식물
아이베는 안강 → 도지기리 야스츠나
주탄동자 → 슈텐도지

[인명]
라이스 → 레이스

[발견물 힌트]
중국의 항주 → 중국의 남경
아프리카 남안 → 아프리카 서안
군관조 → 군함조
개미지옥 → 파리지옥
시에라리온의 동쪽 → 베르데 곶의 동쪽
타브리즈 기준 북동쪽 → 북서쪽
산속 도시 방향 북동쪽 → 북서쪽
"""

KAABA_DETAILS = """by 히소카

카바신전 발견물

- 발견물 ID 672와 카바신전 설명을 활성화합니다.
- DSTILL.CDS에 카바신전 내장 정지 이미지 1개를 추가합니다.
- DISEV.CDS의 이벤트 파트 63을 카바신전 발견 이벤트·대사로 교체합니다.
- SAVEDATA.CDS에서 카바신전이 미등록(00)인 경우, 발견·보고 날짜에 맞춰 미발견·발견·보고 완료 상태로 보정합니다.

체크 해제 시 EXE, DSTILL.CDS, DISEV.CDS는 주입 전 상태로 복원합니다.
이미 진행에 영향을 줄 수 있는 SAVEDATA.CDS의 상태는 해제해도 유지합니다.
"""

SLAVE_DETAILS = """by ladyous

노예 발견물

- EXE의 도서관 힌트 조건 두 곳을 수정해 노예 힌트를 열람할 수 있게 합니다.
- SAVEDATA.CDS의 도서관 힌트 상태를 함께 설정합니다.
- SAVEDATA.CDS의 노예 발견물 상태가 미등록(00)이면 발견·보고 날짜에 맞춰 미발견·발견·보고 완료 상태로 보정합니다.
- DISEV.CDS의 이벤트 파트 229에 노예 발견 대사·분기를 추가합니다.

체크 해제 시 위 EXE·SAVEDATA.CDS·DISEV.CDS 변경을 패치 전 값으로 복원합니다.
이미 발견 또는 보고 상태가 된 노예 발견물은 변경하지 않습니다.
"""

MUGHAL_DETAILS = """by 히소카

무제국 발견 조건 수정

- 무제국 힌트를 획득한 경우에만 무제국을 발견할 수 있도록 수정합니다.
- DISEV.CDS의 이벤트 파트 92를 무제국 발견 조건 수정본으로 교체합니다.

체크 해제 시 이벤트 파트 92만 지원하는 원본 상태로 복원합니다.
"""


BARMAID_PERSONALITY_NAMES = (
    "냉냉한", "강인한", "의지가 강한", "용감한",
    "친절한", "로맨틱한", "섬세한", "견실한",
)
BARMAID_LANGUAGE_NAMES = (
    "스페인어", "포르투갈어", "로망스어", "게르만어",
    "슬라브·그리스어", "아랍어", "페르시아어", "중국어",
    "힌두어", "위굴어", "아프리카토착어", "중남미토착어",
    "동남아시아토착어", "동아시아토착어",
)
BARMAID_CHILD_APTITUDE_NAMES = ("체력", "지력", "무력", "매력", "운", "신앙심")


def _load_barmaid_city_names() -> tuple[str, ...]:
    """Load the 226 city names in their EXE city-ID order."""
    try:
        names = json.loads(
            bundled_resource_path("Resources", "data", "city_names.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("여급 출현 도시명 데이터를 읽지 못했습니다.") from error
    if not isinstance(names, list) or len(names) != 226 or not all(isinstance(name, str) and name for name in names):
        raise RuntimeError("여급 출현 도시명 데이터가 올바르지 않습니다.")
    return tuple(names)


BARMAID_CITY_NAMES = _load_barmaid_city_names()


def _load_sponsor_reference() -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Load the name tables used by the sponsor editor's comboboxes."""
    try:
        reference = json.loads(
            bundled_resource_path("Resources", "data", "sponsor_reference.json").read_text(encoding="utf-8")
        )
        nations = reference["nation_names"]
        jobs = reference["job_names"]
        buildings = reference["building_names"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("후원자 참조 데이터를 읽지 못했습니다.") from error
    if not (
        isinstance(nations, list) and len(nations) == 19
        and isinstance(jobs, list) and len(jobs) == 8
        and isinstance(buildings, list) and len(buildings) == 16
        and all(isinstance(value, str) and value for values in (nations, jobs, buildings) for value in values)
    ):
        raise RuntimeError("후원자 참조 데이터가 올바르지 않습니다.")
    return tuple(nations), tuple(jobs), tuple(buildings)


SPONSOR_NATION_NAMES, SPONSOR_JOB_NAMES, SPONSOR_BUILDING_NAMES = _load_sponsor_reference()


def _load_city_reference() -> tuple[
    tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...],
]:
    """Load the verified city field labels copied from the save editor."""
    try:
        reference = json.loads(
            bundled_resource_path("Resources", "data", "city_reference.json").read_text(encoding="utf-8")
        )
        groups = tuple(
            tuple(reference[key])
            for key in ("nation_names", "culture_names", "status_names", "facility_names")
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("도시 참조 데이터를 읽지 못했습니다.") from error
    if (
        tuple(map(len, groups)) != (78, 11, 14, 16)
        or not all(isinstance(value, str) and value for group in groups for value in group)
    ):
        raise RuntimeError("도시 참조 데이터가 올바르지 않습니다.")
    return groups


CITY_NATION_NAMES, CITY_CULTURE_NAMES, CITY_STATUS_NAMES, CITY_FACILITY_NAMES = _load_city_reference()
CITY_SHIPYARD_LEVEL_NAMES = tuple(
    f"{level}단계 (공급량 {supply})"
    for level, supply in enumerate((20, 50, 100, 200, 350, 500, 700, 1000))
)
SPONSOR_GENDER_NAMES = ("남성", "여성")
SPONSOR_PREFERENCE_NAMES = ("지리", "역사", "보물", "종교", "교역품", "미신", "생물", "민족")
PERSON_JOB_NAMES = ("탐험가", "발굴자", "상인", "정복자")
PERSON_BLOOD_NAMES = ("A형", "B형", "O형", "AB형")
# The EXE's state 0 is a rival, state 1 can converse but cannot be hired,
# and only state 2 enters the recruitable-person list.
PERSON_EMPLOYMENT_STATE_NAMES = ("경쟁자", "대화 가능", "등용 가능")
PERSON_ABILITY_NAMES = ("체력", "지력", "무력", "매력", "운", "신앙심")
PERSON_SKILL_NAMES = (
    "항해술", "운용술", "검술", "포술", "사격술", "의학", "웅변",
    "측량", "역사학", "회계", "조선기술", "신학", "과학",
)
# EXE 원본의 발견물/후원자 취향 비트 순서. CDS3_EXE_ANALYSIS.md와 대조 완료.
DISCOVERY_CATEGORY_NAMES = ("지리", "역사", "보물", "종교", "교역품", "미신", "생물", "민족")
# UI 분류는 세이브 에디터의 논리 분류 순서를 따른다. EXE 아이템 테이블
# +0x14의 원시 코드는 6=선수상, 7=서적, 8=동물로 뒤섞여 있으므로, 화면에
# 표시하거나 저장할 때 반드시 아래 대응표를 거친다.
ITEM_CATEGORY_NAMES = ("소지품", "복식품", "항해도구", "병기", "방어도구", "발견물", "서적", "동물", "선수상")
ITEM_RAW_CATEGORY_TO_DISPLAY = (0, 1, 2, 3, 4, 5, 8, 6, 7)
ITEM_DISPLAY_CATEGORY_TO_RAW = (0, 1, 2, 3, 4, 5, 7, 8, 6)
TRADE_GOOD_IMAGE_SLOT_OFFSET = 134
FIGUREHEAD_DISASTER_NAMES = (
    "쥐떼 발생 방지", "괴혈병·전염병 방지", "반란 방지", "폭풍·눈보라 방지",
)
FIGUREHEAD_DISASTER_GRADES = (1,) * 14 + (2,) * 12 + (3,) * 8


def item_category_name(raw_category_id: int) -> str:
    """Return the logical, user-facing item category for an EXE raw code."""
    return ITEM_CATEGORY_NAMES[ITEM_RAW_CATEGORY_TO_DISPLAY[raw_category_id]]


def item_category_raw_id(category_name: str) -> int:
    """Translate a user-facing item category back to the EXE raw code."""
    return ITEM_DISPLAY_CATEGORY_TO_RAW[ITEM_CATEGORY_NAMES.index(category_name)]


def _windows_dpi_scale(hwnd: int = 0) -> float:
    """Return the active Windows DPI scale for a native child control."""
    try:
        user32 = ctypes.windll.user32
        dpi = user32.GetDpiForWindow(hwnd) if hwnd else user32.GetDpiForSystem()
        if dpi:
            return dpi / 96.0
    except Exception:
        pass
    return 1.0


class NativeWinEdit:
    """The native Windows EDIT search field used by the Save Editor.

    Tk's Entry can lag behind a Korean IME composition.  This is a real Win32
    EDIT child control, polled while focused so the list is refreshed as text
    is composed instead of after the composition is committed.
    """

    _WS_CHILD = 0x40000000
    _WS_VISIBLE = 0x10000000
    _WS_TABSTOP = 0x00010000
    _ES_AUTOHSCROLL = 0x0080
    _WS_EX_CLIENTEDGE = 0x00000200
    _SWP_NOZORDER = 0x0004
    _SWP_NOACTIVATE = 0x0010
    _WM_SETFONT = 0x0030
    _EM_SETSEL = 0x00B1

    def __init__(self, host: tk.Frame, on_change, width: int = 110, height: int = 23) -> None:
        self.host = host
        self.root = host.winfo_toplevel()
        self.on_change = on_change
        self.hwnd: int | None = None
        self._last_text = ""
        self._poll_job: str | None = None
        self._font_handle: int | None = None
        self.enabled = True
        self.max_bytes: int | None = None
        self.max_characters: int | None = None
        self._user32 = None
        scale = _windows_dpi_scale(self.root.winfo_id())
        host.configure(width=round(width * scale), height=round(height * scale))
        host.pack_propagate(False)
        host.grid_propagate(False)
        host.bind("<Configure>", self._resize, add="+")
        host.bind("<Map>", self._wake_poll, add="+")
        host.bind("<Destroy>", self._destroy, add="+")
        self.root.after_idle(self._create)

    def _create(self) -> None:
        if self.hwnd or not self.host.winfo_exists():
            return
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        self._user32 = user32
        user32.CreateWindowExW.argtypes = [
            ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ]
        user32.CreateWindowExW.restype = ctypes.c_void_p
        user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.SetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        user32.EnableWindow.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        user32.GetFocus.restype = ctypes.c_void_p
        self.hwnd = user32.CreateWindowExW(
            self._WS_EX_CLIENTEDGE, "EDIT", "",
            self._WS_CHILD | self._WS_VISIBLE | self._WS_TABSTOP | self._ES_AUTOHSCROLL,
            0, 0, max(1, self.host.winfo_width()), max(1, self.host.winfo_height()),
            ctypes.c_void_p(self.host.winfo_id()), None, None, None,
        )
        if not self.hwnd:
            raise ctypes.WinError()
        user32.EnableWindow(ctypes.c_void_p(self.hwnd), self.enabled)
        dpi = _windows_dpi_scale(self.root.winfo_id()) * 96.0
        gdi32.CreateFontW.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_wchar_p,
        ]
        gdi32.CreateFontW.restype = ctypes.c_void_p
        self._font_handle = gdi32.CreateFontW(
            -max(1, round(9 * dpi / 72.0)), 0, 0, 0, 400, 0, 0, 0, 129,
            0, 0, 0, 0, "Malgun Gothic",
        )
        user32.SendMessageW(
            ctypes.c_void_p(self.hwnd), self._WM_SETFONT,
            ctypes.c_void_p(self._font_handle), ctypes.c_void_p(True),
        )
        self._poll()

    def _resize(self, _event: tk.Event | None = None) -> None:
        if self.hwnd:
            ctypes.windll.user32.SetWindowPos(
                ctypes.c_void_p(self.hwnd), None, 0, 0,
                max(1, self.host.winfo_width()), max(1, self.host.winfo_height()),
                self._SWP_NOZORDER | self._SWP_NOACTIVATE,
            )

    def _poll(self) -> None:
        try:
            if not self.hwnd or not self.host.winfo_exists():
                return
            visible = bool(self.host.winfo_ismapped())
            focused = self.enabled and visible and self._user32.GetFocus() == self.hwnd
            if focused:
                raw_text = self.get()
                text = raw_text[:self.max_characters] if self.max_characters is not None else raw_text
                text = self._limit_cp949_bytes(text, self.max_bytes) if self.max_bytes is not None else text
                if text != raw_text:
                    self._set_text_and_place_cursor_at_end(text)
                if text != self._last_text:
                    self._last_text = text
                    self.on_change()
            delay = 50 if focused else (250 if self.enabled and visible else 1000)
            self._poll_job = self.root.after(delay, self._poll)
        except tk.TclError:
            self._poll_job = None

    def _wake_poll(self, _event: tk.Event | None = None) -> None:
        if self._poll_job is not None:
            try:
                self.root.after_cancel(self._poll_job)
            except tk.TclError:
                pass
        self._poll_job = self.root.after_idle(self._poll)

    def get(self) -> str:
        if not self.hwnd or self._user32 is None:
            return ""
        length = self._user32.GetWindowTextLengthW(ctypes.c_void_p(self.hwnd))
        buffer = ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(ctypes.c_void_p(self.hwnd), buffer, len(buffer))
        return buffer.value

    def set(self, value: str) -> None:
        value = str(value)
        if self.hwnd and self._user32 is not None:
            self._user32.SetWindowTextW(ctypes.c_void_p(self.hwnd), value)
        self._last_text = value

    def _set_text_and_place_cursor_at_end(self, value: str) -> None:
        self.set(value)
        if self.hwnd:
            end = len(value)
            ctypes.windll.user32.SendMessageW(
                ctypes.c_void_p(self.hwnd), self._EM_SETSEL,
                ctypes.c_void_p(end), ctypes.c_void_p(end),
            )

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        if self.hwnd and self._user32 is not None:
            self._user32.EnableWindow(ctypes.c_void_p(self.hwnd), self.enabled)
        if self.enabled:
            self._wake_poll()

    @staticmethod
    def _limit_cp949_bytes(text: str, maximum: int) -> str:
        accepted: list[str] = []
        size = 0
        for character in text:
            try:
                encoded = character.encode("cp949")
            except UnicodeEncodeError:
                continue
            if size + len(encoded) > maximum:
                break
            accepted.append(character)
            size += len(encoded)
        return "".join(accepted)

    def _destroy(self, _event: tk.Event | None = None) -> None:
        if self._poll_job is not None:
            try:
                self.root.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None
        if self._font_handle:
            try:
                ctypes.windll.gdi32.DeleteObject(ctypes.c_void_p(self._font_handle))
            except Exception:
                pass
            self._font_handle = None


class CDSExecutablePatcher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.withdraw()
        self.title(f"대항해시대 III EXE 패치 v{APP_VERSION}")
        self.resizable(False, False)
        self._set_window_icon()
        self.path = tk.StringVar()
        self.coordinate = tk.StringVar(value="original")
        self.widths = [tk.StringVar(value="0") for _ in range(3)]
        self.heights = [tk.StringVar(value="0") for _ in range(3)]
        self.departure = tk.StringVar(value="0")
        self.arrival_wait = tk.StringVar(value="0")
        self.npc_activity_min_age = tk.StringVar(value="0")
        self.npc_activity_max_age = tk.StringVar(value="0")
        self.western_encounter_denominator = tk.StringVar(value="0")
        self.islamic_encounter_denominator = tk.StringVar(value="0")
        self.pirate_variety_enabled = tk.BooleanVar(value=False)
        self.mistranslation_fixes_enabled = tk.BooleanVar(value=False)
        self.pirate_fame_middle = tk.StringVar(value="0")
        self.pirate_fame_high = tk.StringVar(value="0")
        self.pirate_western_stage2_first_probability = tk.StringVar(value="0")
        self.pirate_western_stage2_second_probability = tk.StringVar(value="0")
        self.pirate_western_stage3_first_probability = tk.StringVar(value="0")
        self.pirate_western_stage3_second_probability = tk.StringVar(value="0")
        self.pirate_western_stage3_third_probability = tk.StringVar(value="0")
        self.pirate_eastern_stage2_first_probability = tk.StringVar(value="0")
        self.pirate_eastern_stage2_second_probability = tk.StringVar(value="0")
        self.pirate_eastern_stage3_first_probability = tk.StringVar(value="0")
        self.pirate_eastern_stage3_second_probability = tk.StringVar(value="0")
        self.pirate_eastern_stage3_third_probability = tk.StringVar(value="0")
        self.pirate_western_base_id = tk.StringVar(value="0x106")
        self.pirate_eastern_base_id = tk.StringVar(value="0x109")
        self.pirate_pursuit_base_id = tk.StringVar(value="0x10C")
        self.pirate_pursuit_middle_threshold = tk.StringVar(value="0")
        self.pirate_pursuit_high_threshold = tk.StringVar(value="0")
        self.long_rest_max = tk.StringVar(value="0")
        self.exploration_days = tk.StringVar(value="0")
        self.succession_age = tk.StringVar(value="0")
        self.cash_limit = tk.StringVar(value="0")
        self.deposit_limit = tk.StringVar(value="0")
        self.fame_limit = tk.StringVar(value="0")
        self.infamy_limit = tk.StringVar(value="0")
        self.cold_north_latitude = tk.StringVar(value="0")
        self.cold_south_latitude = tk.StringVar(value="0")
        self.cold_north_unlocked = tk.BooleanVar(value=False)
        self.cold_south_unlocked = tk.BooleanVar(value=False)
        self.eclipse_enabled = tk.BooleanVar(value=False)
        self.eclipse_latitude = tk.StringVar(value="0")
        self.kaaba_enabled = tk.BooleanVar(value=False)
        self.slave_enabled = tk.BooleanVar(value=False)
        self.mughal_enabled = tk.BooleanVar(value=False)
        self.sponsor_name = tk.StringVar()
        self.sponsor_face_code = tk.StringVar()
        self.sponsor_gender = tk.StringVar()
        self.sponsor_nation = tk.StringVar()
        self.sponsor_job = tk.StringVar()
        self.sponsor_appearance_year = tk.StringVar()
        self.sponsor_city_name = tk.StringVar()
        self.sponsor_building = tk.StringVar()
        self.sponsor_power = tk.StringVar()
        self.sponsor_wealth_factor = tk.StringVar()
        self.sponsor_appraisal = tk.StringVar()
        self.sponsor_preference_flags = [tk.BooleanVar(value=False) for _ in SPONSOR_PREFERENCE_NAMES]
        self.sponsor_language_flags = [tk.BooleanVar(value=False) for _ in BARMAID_LANGUAGE_NAMES]
        self._sponsor_records: tuple[SponsorRecord, ...] = ()
        self._sponsor_by_identifier: dict[int, SponsorRecord] = {}
        self._sponsor_controls: list[tk.Widget] = []
        self.person_name = tk.StringVar()
        self.person_face_code = tk.StringVar()
        self.person_gender = tk.StringVar()
        self.person_age = tk.StringVar()
        self.person_nation = tk.StringVar()
        self.person_job = tk.StringVar()
        self.person_fame = tk.StringVar()
        self.person_infamy = tk.StringVar()
        self.person_employment_state = tk.StringVar()
        self.person_city = tk.StringVar()
        self.person_building = tk.StringVar()
        self.person_blood = tk.StringVar()
        self.person_hire_cost = tk.StringVar()
        self.person_abilities = [tk.StringVar() for _ in PERSON_ABILITY_NAMES]
        self.person_vitality = tk.StringVar()
        self.person_skill_levels = [tk.StringVar() for _ in (*PERSON_SKILL_NAMES, *BARMAID_LANGUAGE_NAMES)]
        self._person_records: tuple[PersonRecord, ...] = ()
        self._person_by_identifier: dict[int, PersonRecord] = {}
        self._person_controls: list[tk.Widget] = []
        self.ship_name = tk.StringVar()
        self.ship_shipyard_requirement = tk.StringVar()
        self.ship_base_power = tk.StringVar()
        self.ship_power_limit = tk.StringVar()
        self.ship_base_durability = tk.StringVar()
        self.ship_durability_limit = tk.StringVar()
        self.ship_base_weight = tk.StringVar()
        self.ship_weight_limit = tk.StringVar()
        self.ship_base_capacity = tk.StringVar()
        self.ship_capacity_limit = tk.StringVar()
        self.ship_base_cannons = tk.StringVar()
        self.ship_cannon_limit = tk.StringVar()
        self.ship_min_crew = tk.StringVar()
        self._ship_type_records: tuple[ShipTypeRecord, ...] = ()
        self._ship_type_by_identifier: dict[int, ShipTypeRecord] = {}
        self._ship_type_controls: list[tk.Widget] = []
        self.city_name = tk.StringVar()
        self.city_inland_connections = [tk.StringVar(), tk.StringVar()]
        self.city_nation = tk.StringVar()
        self.city_culture = tk.StringVar()
        self.city_status = tk.StringVar()
        self.city_update_counter = tk.StringVar()
        self.city_discovered = tk.BooleanVar(value=False)
        self.city_ship_candidates = [tk.BooleanVar(value=False) for _ in range(8)]
        self.city_shipyard_level = tk.StringVar()
        self.city_facilities = [tk.BooleanVar(value=False) for _ in CITY_FACILITY_NAMES]
        self.city_trade_region = tk.StringVar()
        self.city_common_goods = [tk.StringVar(value="없음") for _ in range(5)]
        self.city_specialty = tk.StringVar()
        self.city_specialty_price = tk.StringVar()
        self.city_specialty_supply_index = tk.StringVar()
        self.city_market_goods = [tk.StringVar() for _ in range(8)]
        self.city_trade_region_editor = tk.StringVar(value="0")
        self.city_trade_region_goods = [tk.StringVar(value="없음") for _ in range(5)]
        self._trade_region_good_photos: list[ImageTk.PhotoImage | None] = [None] * 5
        self._city_records: tuple[CityRecord, ...] = ()
        self._city_by_identifier: dict[int, CityRecord] = {}
        self._trade_good_names: tuple[str, ...] = ()
        self._trade_region_goods: tuple[tuple[int, ...], ...] = ()
        self._city_controls: list[tk.Widget] = []
        self.item_name = tk.StringVar()
        self.item_category = tk.StringVar()
        self.item_buy_price = tk.StringVar()
        self.item_sell_price = tk.StringVar()
        self.item_effect_value = tk.StringVar()
        self.figurehead_disaster_chances = [tk.StringVar(value=value) for value in ("11", "41", "71")]
        self.figurehead_cannon_damage_reduction = tk.StringVar(value="20")
        self.figurehead_shooting_damage_reduction = tk.StringVar(value="50")
        self.figurehead_melee_damage_reduction = tk.StringVar(value="70")
        self.figurehead_cannon_attack_percent = tk.StringVar(value="120")
        self.figurehead_shooting_attack_percent = tk.StringVar(value="150")
        self.figurehead_melee_attack_percent = tk.StringVar(value="200")
        self.figurehead_special_cannon_attack_percent = tk.StringVar(value="200")
        self.figurehead_all_attack_percent = tk.StringVar(value="150")
        self.figurehead_hull_recovery = tk.StringVar(value="5")
        self.figurehead_movement_bonus = tk.StringVar(value="1")
        self.figurehead_movement_maximum = tk.StringVar(value="6")
        self.figurehead_selected_effect = tk.StringVar(value="")
        self.figurehead_selected_code = tk.StringVar(value="")
        self.figurehead_disaster_description = tk.StringVar(value="")
        self.figurehead_primary_label = tk.StringVar(value="효과 수치:")
        self.figurehead_primary_unit = tk.StringVar(value="")
        self.figurehead_secondary_label = tk.StringVar(value="")
        self.figurehead_secondary_unit = tk.StringVar(value="")
        self._figurehead_controls: list[tk.Widget] = []
        self._figurehead_loaded = False
        self._item_records: tuple[ItemRecord, ...] = ()
        self._item_by_identifier: dict[int, ItemRecord] = {}
        self._item_controls: list[tk.Widget] = []
        self.discovery_name = tk.StringVar()
        self.discovery_category = tk.StringVar()
        self.discovery_still_slot = tk.StringVar()
        self.discovery_value = tk.StringVar()
        self.discovery_min_x = tk.StringVar()
        self.discovery_min_y = tk.StringVar()
        self.discovery_max_x = tk.StringVar()
        self.discovery_max_y = tk.StringVar()
        self.discovery_min_x_direction = tk.StringVar()
        self.discovery_min_y_direction = tk.StringVar()
        self.discovery_max_x_direction = tk.StringVar()
        self.discovery_max_y_direction = tk.StringVar()
        self._discovery_records: tuple[DiscoveryRecord, ...] = ()
        self._discovery_by_identifier: dict[int, DiscoveryRecord] = {}
        self._discovery_controls: list[tk.Widget] = []
        self._discovery_coordinate_controls: list[tk.Widget] = []
        self._discovery_still_slot_count = 85
        # Standard archives contain 144 female and 414 male portraits.  The
        # values are replaced with the selected installation's actual counts.
        self._portrait_counts = {True: 144, False: 414}
        self.barmaid_selection = tk.StringVar()
        self.barmaid_face_code = tk.StringVar()
        self.barmaid_appearance_year = tk.StringVar()
        self.barmaid_city_name = tk.StringVar()
        self.barmaid_personality = tk.StringVar()
        self.barmaid_language_flags = [tk.BooleanVar(value=False) for _ in BARMAID_LANGUAGE_NAMES]
        self.barmaid_child_aptitudes = [tk.StringVar(value="0") for _ in BARMAID_CHILD_APTITUDE_NAMES]
        self.barmaid_child_aptitude_total = tk.StringVar(value="총합: 0")
        self._barmaid_records: tuple[BarmaidRecord, ...] = ()
        self._barmaid_child_aptitudes: tuple[BarmaidChildAptitudes, ...] = ()
        self._barmaid_by_identifier: dict[int, BarmaidRecord] = {}
        self._barmaid_controls: list[tk.Widget] = []
        self._slave_was_enabled = False
        self._mughal_was_enabled = False
        self._update_checking = False
        self._update_button: ttk.Button | None = None
        self._available_update: tuple[GitHubReleaseUpdater, dict, dict] | None = None
        self._splash: tk.Toplevel | None = None
        self._splash_image: ImageTk.PhotoImage | None = None
        self._barmaid_face_photo: ImageTk.PhotoImage | None = None
        self._sponsor_face_photo: ImageTk.PhotoImage | None = None
        self._person_face_photo: ImageTk.PhotoImage | None = None
        self._item_image_photo: ImageTk.PhotoImage | None = None
        self._city_image_photo: ImageTk.PhotoImage | None = None
        self._discovery_image_photo: ImageTk.PhotoImage | None = None
        self._ship_avi_preview: AviPreview | None = None
        self._discovery_avi_preview: AviPreview | None = None
        self._discover_animation_preview: DiscoverAnimationPreview | None = None
        self._integer_validation_command = self.register(self._validate_integer_text)
        self._decimal_validation_command = self.register(self._validate_decimal_text)
        self.barmaid_face_code.trace_add("write", self._on_barmaid_face_code_changed)
        self.sponsor_face_code.trace_add("write", self._on_sponsor_face_code_changed)
        self.sponsor_gender.trace_add("write", self._on_sponsor_face_code_changed)
        self.person_face_code.trace_add("write", self._on_person_face_code_changed)
        self.person_gender.trace_add("write", self._on_person_face_code_changed)
        self.discovery_still_slot.trace_add("write", self._on_discovery_media_changed)
        self.city_trade_region.trace_add("write", lambda *_args: self._refresh_city_common_goods())
        for variable in self.barmaid_child_aptitudes:
            variable.trace_add("write", self._update_barmaid_child_aptitude_total)
        self._build()
        self._center_main_window()
        self._show_splash()

    def destroy(self) -> None:
        """Release the native AVI decoder before Tk tears down its widgets."""
        if self._ship_avi_preview is not None:
            self._ship_avi_preview.stop()
        if self._discovery_avi_preview is not None:
            self._discovery_avi_preview.stop()
        if self._discover_animation_preview is not None:
            self._discover_animation_preview.stop()
        super().destroy()

    def _set_window_icon(self) -> None:
        """Use the bundled icon for the application window and taskbar."""
        try:
            self.iconbitmap(default=str(bundled_resource_path("Resources", "Icon.ico")))
        except tk.TclError:
            # The patcher can still run from an environment without ICO support.
            pass

    def _show_splash(self) -> None:
        """Display the bundled splash image before revealing the main window."""
        try:
            with Image.open(bundled_resource_path("Resources", "splash.jpg")) as source:
                self._splash_image = ImageTk.PhotoImage(source.copy())
        except (OSError, tk.TclError):
            self._finish_splash()
            return
        splash = tk.Toplevel(self)
        self._splash = splash
        splash.overrideredirect(True)
        splash.attributes("-topmost", True)
        ttk.Label(splash, image=self._splash_image).pack()
        splash.update_idletasks()
        width, height = self._splash_image.width(), self._splash_image.height()
        x = (splash.winfo_screenwidth() - width) // 2
        y = (splash.winfo_screenheight() - height) // 2
        splash.geometry(f"{width}x{height}+{x}+{y}")
        splash.after(1500, self._finish_splash)

    def _center_main_window(self) -> None:
        """Place the fully constructed main window at the center of the screen."""
        self.update_idletasks()
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        x = (self.winfo_screenwidth() - width) // 2
        y = (self.winfo_screenheight() - height) // 2
        self.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")

    def _finish_splash(self) -> None:
        if self._splash is not None and self._splash.winfo_exists():
            self._splash.destroy()
        self._splash = None
        self.deiconify()
        self.after(0, self._show_update_notice)
        if APP_UPDATE_CONFIG.enabled and getattr(sys, "frozen", False):
            self.after(300, lambda: self.check_for_updates(silent=True))

    def _center_dialog(self, window: tk.Toplevel) -> None:
        """Place a custom dialog in the center of the patcher window."""
        window.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - window.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - window.winfo_height()) // 2
        window.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    @staticmethod
    def _validate_integer_text(proposed: str, lower: str, upper: str) -> bool:
        """Allow only an in-range integer while the user is editing a field."""
        minimum, maximum = int(lower), int(upper)
        if proposed == "":
            return True
        if proposed == "-":
            return minimum < 0
        digits = proposed[1:] if proposed.startswith("-") else proposed
        if not digits.isdecimal():
            return False
        value = int(proposed)
        return minimum <= value <= maximum

    @staticmethod
    def _validate_decimal_text(proposed: str, lower: str, upper: str) -> bool:
        """Allow only an in-range decimal value while the user is editing a field."""
        if proposed in ("", "."):
            return True
        if proposed.count(".") > 1 or not all(character.isdecimal() or character == "." for character in proposed):
            return False
        try:
            value = float(proposed)
        except ValueError:
            return False
        return float(lower) <= value <= float(upper)

    def _limit_integer_input(self, widget: ttk.Entry | ttk.Spinbox, minimum: int, maximum: int) -> None:
        widget.configure(
            validate="key",
            validatecommand=(self._integer_validation_command, "%P", str(minimum), str(maximum)),
        )

    def _limit_decimal_input(self, widget: ttk.Entry, minimum: float, maximum: float) -> None:
        widget.configure(
            validate="key",
            validatecommand=(self._decimal_validation_command, "%P", str(minimum), str(maximum)),
        )

    def _build(self) -> None:
        ttk.Style(self).configure("Credit.TLabel", foreground="#1A73E8")
        frame = ttk.Frame(self, padding=14)
        frame.grid(sticky="nsew")
        top_bar = ttk.Frame(frame)
        top_bar.grid(row=0, column=0, sticky="w")
        ttk.Label(top_bar, text="대상 실행 파일").grid(row=0, column=0, padx=(0, 8), sticky="w")
        self.path_entry = ttk.Entry(top_bar, textvariable=self.path, width=25, state="readonly")
        self.path_entry.grid(row=0, column=1, sticky="w")
        ttk.Button(top_bar, text="EXE 선택…", command=self.select_exe).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(top_bar, text="선택한 설정으로 패치", command=self.apply).grid(row=0, column=3, padx=(8, 0))
        self._update_button = ttk.Button(top_bar, text="업데이트 설치…", command=self.install_available_update)
        self._update_button.grid(row=0, column=4, padx=(8, 0))
        self._update_button.grid_remove()

        settings_notebook = ttk.Notebook(frame)
        settings_notebook.grid(row=1, column=0, pady=(12, 0), sticky="nsew")
        basic_tab = ttk.Frame(settings_notebook, padding=10)
        additional_tab = ttk.Frame(settings_notebook, padding=10)
        barmaid_tab = ttk.Frame(settings_notebook, padding=10)
        sponsor_tab = ttk.Frame(settings_notebook, padding=10)
        person_tab = ttk.Frame(settings_notebook, padding=10)
        ship_tab = ttk.Frame(settings_notebook, padding=10)
        city_tab = ttk.Frame(settings_notebook, padding=10)
        trade_region_tab = ttk.Frame(settings_notebook, padding=10)
        item_tab = ttk.Frame(settings_notebook, padding=10)
        figurehead_tab = ttk.Frame(settings_notebook, padding=10)
        discovery_tab = ttk.Frame(settings_notebook, padding=10)
        settings_notebook.add(basic_tab, text="기본 정보")
        settings_notebook.add(additional_tab, text="추가 패치")
        settings_notebook.add(barmaid_tab, text="여급")
        settings_notebook.add(sponsor_tab, text="후원자")
        settings_notebook.add(person_tab, text="인물")
        settings_notebook.add(ship_tab, text="함선")
        settings_notebook.add(city_tab, text="도시")
        settings_notebook.add(trade_region_tab, text="교역권")
        settings_notebook.add(item_tab, text="아이템")
        settings_notebook.add(figurehead_tab, text="선수상 효과")
        settings_notebook.add(discovery_tab, text="발견물")
        # The discovery page is shorter than the largest notebook page.  Keep
        # its grid at the upper-left instead of centering it in the spare area.
        discovery_tab.grid_anchor("nw")

        basic_left_column = ttk.Frame(basic_tab)
        basic_left_column.grid(row=0, column=0, padx=(0, 5), sticky="new")
        basic_right_column = ttk.Frame(basic_tab)
        basic_right_column.grid(row=0, column=1, padx=(5, 0), sticky="new")
        additional_left_column = ttk.Frame(additional_tab)
        additional_left_column.grid(row=0, column=0, padx=(0, 5), sticky="new")
        additional_right_column = ttk.Frame(additional_tab)
        additional_right_column.grid(row=0, column=1, padx=(5, 0), sticky="new")
        for column in (
            basic_left_column,
            basic_right_column,
            additional_left_column,
            additional_right_column,
        ):
            column.columnconfigure(0, weight=1)

        coordinate_box = ttk.LabelFrame(basic_right_column, text="좌표 표시", padding=10)
        coordinate_box.grid(row=0, column=0, sticky="ew")
        for row, (value, label) in enumerate((
            ("original", "원본: 북위 40  동경 110"),
            ("korean2", "소수 둘째 자리: 북위40.00 동경110.00"),
            ("korean3", "소수 셋째 자리: 북 40.000 동 110.000"),
        )):
            ttk.Radiobutton(coordinate_box, text=label, value=value, variable=self.coordinate).grid(row=row, column=0, sticky="w", pady=2)

        resolution_box = ttk.LabelFrame(basic_left_column, text="해상도 선택지", padding=10)
        resolution_box.grid(row=1, column=0, pady=(10, 0), sticky="ew")
        ttk.Label(resolution_box, text="번호").grid(row=0, column=0, sticky="w")
        ttk.Label(resolution_box, text="가로").grid(row=0, column=1, padx=(12, 0))
        ttk.Label(resolution_box, text="세로").grid(row=0, column=3, padx=(12, 0))
        for index, (width, height) in enumerate(zip(self.widths, self.heights), start=1):
            ttk.Label(resolution_box, text=f"{index}번째").grid(row=index, column=0, sticky="w", pady=2)
            ttk.Entry(resolution_box, textvariable=width, width=7).grid(row=index, column=1, padx=(12, 0))
            ttk.Label(resolution_box, text="×").grid(row=index, column=2, padx=5)
            ttk.Entry(resolution_box, textvariable=height, width=7).grid(row=index, column=3)
        ttk.Button(resolution_box, text="전체화면 적용", command=self.apply_fullscreen).grid(row=3, column=4, padx=(12, 0), sticky="w")

        npc_box = ttk.LabelFrame(basic_left_column, text="일반 NPC 이동", padding=10)
        npc_box.grid(row=2, column=0, pady=(10, 0), sticky="ew")
        ttk.Label(npc_box, text="월간 출발 확률: 1 /").grid(row=0, column=0, sticky="w")
        departure_entry = ttk.Entry(npc_box, textvariable=self.departure, width=6)
        departure_entry.grid(row=0, column=1, padx=(4, 0))
        self._limit_integer_input(departure_entry, 1, 127)
        ttk.Label(npc_box, text="(분모 1~127, 원본 5)").grid(row=0, column=2, padx=(5, 0), sticky="w")
        ttk.Label(npc_box, text="도착 대기:").grid(row=1, column=0, pady=(5, 0), sticky="w")
        arrival_wait_entry = ttk.Entry(npc_box, textvariable=self.arrival_wait, width=6)
        arrival_wait_entry.grid(row=1, column=1, padx=(4, 0), pady=(5, 0), sticky="w")
        self._limit_integer_input(arrival_wait_entry, 0, 127)
        ttk.Label(npc_box, text="일 (0~127, 기본값 60)").grid(row=1, column=2, padx=(5, 0), pady=(5, 0), sticky="w")
        gameplay_box = ttk.LabelFrame(basic_left_column, text="게임 진행 설정", padding=10)
        gameplay_box.grid(row=0, column=0, sticky="ew")
        gameplay_rows = (
            ("장기 휴양 최대 기간", self.long_rest_max, "개월 (1~127)", 1, 127),
            ("탐험 준비 기간", self.exploration_days, "일 (1~127, 원본 10일)", 1, 127),
            ("세대교체 가능 나이", self.succession_age, "세 (1~127)", 1, 127),
            ("소지금 상한", self.cash_limit, "두캇 (1~99,999,999)", 1, 99_999_999),
            ("저금 상한", self.deposit_limit, "두캇 (1~99,999,999)", 1, 99_999_999),
            ("명성 상한", self.fame_limit, "(1~99,999,999)", 1, 99_999_999),
            ("악명 상한", self.infamy_limit, "(1~99,999,999)", 1, 99_999_999),
        )
        for row, (label, variable, suffix, minimum, maximum) in enumerate(gameplay_rows):
            ttk.Label(gameplay_box, text=f"{label}:").grid(row=row, column=0, pady=2, sticky="w")
            value_row = ttk.Frame(gameplay_box)
            value_row.grid(row=row, column=1, columnspan=2, padx=(6, 0), pady=2, sticky="w")
            entry = ttk.Entry(value_row, textvariable=variable, width=8)
            entry.pack(side=tk.LEFT)
            self._limit_integer_input(entry, minimum, maximum)
            ttk.Label(value_row, text=suffix).pack(side=tk.LEFT, padx=(5, 0))
        ttk.Label(gameplay_box, text="인물 활동 가능 나이:").grid(row=7, column=0, pady=2, sticky="w")
        activity_age_frame = ttk.Frame(gameplay_box)
        activity_age_frame.grid(row=7, column=1, columnspan=2, padx=(6, 0), pady=2, sticky="w")
        activity_minimum_entry = ttk.Entry(activity_age_frame, textvariable=self.npc_activity_min_age, width=6)
        activity_minimum_entry.grid(row=0, column=0)
        self._limit_integer_input(activity_minimum_entry, 0, 127)
        ttk.Label(activity_age_frame, text="~").grid(row=0, column=1, padx=5)
        activity_maximum_entry = ttk.Entry(activity_age_frame, textvariable=self.npc_activity_max_age, width=6)
        activity_maximum_entry.grid(row=0, column=2)
        self._limit_integer_input(activity_maximum_entry, 0, 127)
        ttk.Label(activity_age_frame, text="세 (0~127, 원본 18~60)").grid(row=0, column=3, padx=(5, 0))

        encounter_box = ttk.LabelFrame(basic_right_column, text="인카운트", padding=10)
        encounter_box.grid(row=1, column=0, pady=(10, 0), sticky="ew")
        ttk.Label(encounter_box, text="서부 해역 (해적·추격대): 1 /").grid(row=0, column=0, sticky="w")
        western_encounter_entry = ttk.Entry(encounter_box, textvariable=self.western_encounter_denominator, width=7)
        western_encounter_entry.grid(row=0, column=1, padx=(4, 0), sticky="w")
        self._limit_integer_input(western_encounter_entry, 1, 32_768)
        ttk.Label(encounter_box, text="(원본 700)").grid(row=0, column=2, padx=(5, 0), sticky="w")
        ttk.Label(encounter_box, text="동부 해역 (이슬람 함대): 1 /").grid(row=1, column=0, pady=(5, 0), sticky="w")
        islamic_encounter_entry = ttk.Entry(encounter_box, textvariable=self.islamic_encounter_denominator, width=7)
        islamic_encounter_entry.grid(row=1, column=1, padx=(4, 0), pady=(5, 0), sticky="w")
        self._limit_integer_input(islamic_encounter_entry, 1, 32_768)
        ttk.Label(encounter_box, text="(원본 400)").grid(row=1, column=2, padx=(5, 0), pady=(5, 0), sticky="w")
        ttk.Label(encounter_box, text="분모 1~32,768: 값이 작을수록 자주 발생합니다.").grid(
            row=2, column=0, columnspan=3, pady=(5, 0), sticky="w",
        )

        pirate_box = ttk.LabelFrame(additional_right_column, text="명성별 해적 확장", padding=10)
        pirate_box.grid(row=0, column=0, sticky="ew")
        pirate_option_row = ttk.Frame(pirate_box)
        pirate_option_row.grid(row=0, column=0, columnspan=6, sticky="w")
        self.pirate_enabled_checkbutton = ttk.Checkbutton(
            pirate_option_row,
            text="패치 적용",
            variable=self.pirate_variety_enabled,
            command=self._update_pirate_control_states,
        )
        self.pirate_enabled_checkbutton.pack(side=tk.LEFT)
        ttk.Label(pirate_option_row, text="by ladyous", style="Credit.TLabel").pack(side=tk.LEFT, padx=(4, 0))

        ttk.Label(pirate_box, text="명성 단계 기준:").grid(row=1, column=0, pady=(7, 2), sticky="w")
        fame_range = ttk.Frame(pirate_box)
        fame_range.grid(row=1, column=1, columnspan=5, padx=(6, 0), pady=(7, 2), sticky="w")
        ttk.Label(fame_range, text="0").grid(row=0, column=0)
        ttk.Label(fame_range, text="—").grid(row=0, column=1, padx=6)
        self.pirate_fame_middle_entry = ttk.Entry(fame_range, textvariable=self.pirate_fame_middle, width=8)
        self.pirate_fame_middle_entry.grid(row=0, column=2)
        self._limit_integer_input(self.pirate_fame_middle_entry, 0, 65_535)
        ttk.Label(fame_range, text="—").grid(row=0, column=3, padx=6)
        self.pirate_fame_high_entry = ttk.Entry(fame_range, textvariable=self.pirate_fame_high, width=8)
        self.pirate_fame_high_entry.grid(row=0, column=4)
        self._limit_integer_input(self.pirate_fame_high_entry, 0, 65_535)

        ttk.Label(pirate_box, text="추격대 단계 경계:").grid(row=2, column=0, pady=2, sticky="w")
        pursuit_range = ttk.Frame(pirate_box)
        pursuit_range.grid(row=2, column=1, columnspan=5, padx=(6, 0), pady=2, sticky="w")
        ttk.Label(pursuit_range, text="0").grid(row=0, column=0)
        ttk.Label(pursuit_range, text="—").grid(row=0, column=1, padx=6)
        self.pirate_pursuit_middle_entry = ttk.Entry(
            pursuit_range, textvariable=self.pirate_pursuit_middle_threshold, width=8,
        )
        self.pirate_pursuit_middle_entry.grid(row=0, column=2)
        self._limit_integer_input(self.pirate_pursuit_middle_entry, 0, 127)
        ttk.Label(pursuit_range, text="—").grid(row=0, column=3, padx=6)
        self.pirate_pursuit_high_entry = ttk.Entry(
            pursuit_range, textvariable=self.pirate_pursuit_high_threshold, width=8,
        )
        self.pirate_pursuit_high_entry.grid(row=0, column=4)
        self._limit_integer_input(self.pirate_pursuit_high_entry, 0, 127)

        self.pirate_probability_notebook = ttk.Notebook(pirate_box)
        self.pirate_probability_notebook.grid(
            row=3, column=0, columnspan=6, pady=(7, 0), sticky="ew",
        )
        self.pirate_probability_trees = {}
        region_fleets = {
            "western": ("서부 해역", ("사략 함대", "해적", "콜세르")),
            "eastern": ("동부 해역", ("아랍 해적", "이슬람 함대", "터키 해군")),
        }
        for region, (title, fleet_names) in region_fleets.items():
            tab = ttk.Frame(self.pirate_probability_notebook, padding=4)
            self.pirate_probability_notebook.add(tab, text=title)
            tree = ttk.Treeview(
                tab,
                columns=("stage", "first", "second", "third"),
                show="headings",
                height=3,
                selectmode="browse",
            )
            tree.pack(fill=tk.X, expand=True)
            for column, text, width in (
                ("stage", "단계", 65),
                ("first", fleet_names[0], 110),
                ("second", fleet_names[1], 110),
                ("third", fleet_names[2], 110),
            ):
                tree.heading(column, text=text)
                tree.column(column, width=width, minwidth=width, anchor=tk.CENTER, stretch=True)
            tree.bind("<Double-1>", lambda event, current_region=region: self._edit_pirate_probability(event, current_region))
            self.pirate_probability_trees[region] = tree
        self._pirate_probability_variables = {
            ("western", "stage2", "first"): self.pirate_western_stage2_first_probability,
            ("western", "stage2", "second"): self.pirate_western_stage2_second_probability,
            ("western", "stage3", "first"): self.pirate_western_stage3_first_probability,
            ("western", "stage3", "second"): self.pirate_western_stage3_second_probability,
            ("western", "stage3", "third"): self.pirate_western_stage3_third_probability,
            ("eastern", "stage2", "first"): self.pirate_eastern_stage2_first_probability,
            ("eastern", "stage2", "second"): self.pirate_eastern_stage2_second_probability,
            ("eastern", "stage3", "first"): self.pirate_eastern_stage3_first_probability,
            ("eastern", "stage3", "second"): self.pirate_eastern_stage3_second_probability,
            ("eastern", "stage3", "third"): self.pirate_eastern_stage3_third_probability,
        }
        self._refresh_pirate_probability_tree()
        ttk.Label(
            pirate_box,
            text="확률 셀을 더블클릭해 수정합니다. 각 단계의 합계는 100%여야 합니다.",
        ).grid(row=4, column=0, columnspan=6, pady=(5, 0), sticky="w")
        self._update_pirate_control_states()

        latitude_box = ttk.LabelFrame(basic_right_column, text="위도 경계", padding=10)
        latitude_box.grid(row=2, column=0, pady=(10, 0), sticky="ew")
        ttk.Label(latitude_box, text="북쪽 추위 전멸 경계:").grid(row=0, column=0, pady=2, sticky="w")
        self.cold_north_entry = ttk.Entry(latitude_box, textvariable=self.cold_north_latitude, width=8)
        self.cold_north_entry.grid(row=0, column=1, padx=(6, 0), pady=2, sticky="w")
        self._limit_decimal_input(self.cold_north_entry, 0, 180)
        ttk.Label(latitude_box, text="°N (원본 76.995°N)").grid(row=0, column=2, padx=(5, 0), pady=2, sticky="w")
        ttk.Checkbutton(
            latitude_box, text="제한 해제", variable=self.cold_north_unlocked,
            command=self._update_cold_limit_entry_states,
        ).grid(row=0, column=3, padx=(10, 0), pady=2, sticky="w")

        ttk.Label(latitude_box, text="남쪽 추위 전멸 경계:").grid(row=1, column=0, pady=2, sticky="w")
        self.cold_south_entry = ttk.Entry(latitude_box, textvariable=self.cold_south_latitude, width=8)
        self.cold_south_entry.grid(row=1, column=1, padx=(6, 0), pady=2, sticky="w")
        self._limit_decimal_input(self.cold_south_entry, 0, 180)
        ttk.Label(latitude_box, text="°S (원본 79.992°S)").grid(row=1, column=2, padx=(5, 0), pady=2, sticky="w")
        ttk.Checkbutton(
            latitude_box, text="제한 해제", variable=self.cold_south_unlocked,
            command=self._update_cold_limit_entry_states,
        ).grid(row=1, column=3, padx=(10, 0), pady=2, sticky="w")
        ttk.Label(latitude_box, text="일식 관측 위도:").grid(row=2, column=0, pady=(6, 2), sticky="w")
        self.eclipse_latitude_entry = ttk.Entry(latitude_box, textvariable=self.eclipse_latitude, width=8)
        self.eclipse_latitude_entry.grid(row=2, column=1, padx=(6, 0), pady=(6, 2), sticky="w")
        ttk.Label(latitude_box, text="° 이상 (남·북 공통)").grid(row=2, column=2, padx=(5, 0), pady=(6, 2), sticky="w")
        ttk.Checkbutton(
            latitude_box,
            text="일식 관측 활성화",
            variable=self.eclipse_enabled,
            command=self._update_cold_limit_entry_states,
        ).grid(row=2, column=3, padx=(10, 0), pady=(6, 2), sticky="w")
        self._update_cold_limit_entry_states()

        translation_box = ttk.LabelFrame(basic_right_column, text="오역 수정", padding=10)
        translation_box.grid(row=3, column=0, pady=(10, 0), sticky="ew")
        ttk.Checkbutton(
            translation_box,
            text="용어·지명·아이템명·인명·힌트 오역 수정 적용",
            variable=self.mistranslation_fixes_enabled,
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            translation_box,
            text="내용…",
            command=self.show_mistranslation_details,
        ).grid(row=0, column=1, padx=(10, 0), sticky="e")

        discovery_box = ttk.LabelFrame(additional_left_column, text="발견물", padding=10)
        discovery_box.grid(row=0, column=0, sticky="ew")

        ttk.Checkbutton(
            discovery_box,
            text="카바신전 발견물·내장 이미지 주입",
            variable=self.kaaba_enabled,
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            discovery_box,
            text="내용…",
            command=lambda: self.show_patch_details("카바신전", KAABA_DETAILS),
        ).grid(row=0, column=1, padx=(10, 0), sticky="e")
        ttk.Checkbutton(
            discovery_box,
            text="노예 도서관 힌트·발견 대사 추가",
            variable=self.slave_enabled,
        ).grid(row=1, column=0, pady=(6, 0), sticky="w")
        ttk.Button(
            discovery_box,
            text="내용…",
            command=lambda: self.show_patch_details("노예 발견물", SLAVE_DETAILS),
        ).grid(row=1, column=1, padx=(10, 0), pady=(6, 0), sticky="e")
        ttk.Checkbutton(
            discovery_box,
            text="무제국 발견 조건 수정",
            variable=self.mughal_enabled,
        ).grid(row=2, column=0, pady=(6, 0), sticky="w")
        ttk.Button(
            discovery_box,
            text="내용…",
            command=lambda: self.show_patch_details("무제국 발견 조건", MUGHAL_DETAILS),
        ).grid(row=2, column=1, padx=(10, 0), pady=(6, 0), sticky="e")

        person_list_box = ttk.LabelFrame(person_tab, text="인물 목록", padding=10)
        person_list_box.grid(row=0, column=0, rowspan=2, sticky="nsew")
        person_tab.grid_rowconfigure(1, weight=1)
        person_list_box.columnconfigure(0, weight=1)
        person_list_box.rowconfigure(1, weight=1)
        ttk.Label(person_list_box, text="검색:").grid(row=0, column=0, sticky="w")
        person_search_host = tk.Frame(person_list_box, width=150, height=23)
        person_search_host.grid(row=0, column=1, padx=(6, 0), sticky="w")
        self.person_search_entry = NativeWinEdit(person_search_host, self._schedule_person_list_refresh, width=150, height=23)
        person_list_frame = ttk.Frame(person_list_box)
        person_list_frame.grid(row=1, column=0, columnspan=2, pady=(8, 0), sticky="nsew")
        self.person_list = ttk.Treeview(person_list_frame, columns=("id", "name"), show="headings", height=15, selectmode="browse")
        self.person_list.heading("id", text="번호"); self.person_list.heading("name", text="이름")
        self.person_list.column("id", width=48, anchor="center", stretch=False); self.person_list.column("name", width=175, anchor="w")
        person_scroll = ttk.Scrollbar(person_list_frame, orient="vertical", command=self.person_list.yview)
        self.person_list.configure(yscrollcommand=person_scroll.set)
        self.person_list.grid(row=0, column=0, sticky="nsew"); person_scroll.grid(row=0, column=1, sticky="ns")
        self.person_list.bind("<<TreeviewSelect>>", self._on_person_selected)
        person_list_frame.columnconfigure(0, weight=1)
        person_list_frame.rowconfigure(0, weight=1)

        person_details = ttk.Notebook(person_tab)
        person_details.grid(row=0, column=1, padx=(10, 0), sticky="nw")
        person_basic_tab = ttk.Frame(person_details, padding=4)
        person_skill_tab = ttk.Frame(person_details, padding=4)
        person_details.add(person_basic_tab, text="기본 정보")
        person_details.add(person_skill_tab, text="기술·언어")

        person_box = ttk.LabelFrame(person_basic_tab, text="인물 정보", padding=10)
        person_box.grid(row=0, column=0, sticky="nw")
        ttk.Label(person_box, text="이름:").grid(row=0, column=0, sticky="w")
        ttk.Label(person_box, textvariable=self.person_name, width=26).grid(row=0, column=1, columnspan=3, padx=(6, 0), sticky="w")
        preview_box = tk.Frame(person_box, width=84, height=100, bg="#222222", relief="ridge", bd=2)
        preview_box.grid(row=0, column=4, rowspan=6, padx=(14, 0), sticky="n"); preview_box.grid_propagate(False)
        self.person_image_preview = tk.Label(preview_box, bg="#222222", fg="#DDDDDD", text="이미지 없음"); self.person_image_preview.pack(fill=tk.BOTH, expand=True)
        person_rows = (("얼굴 코드", self.person_face_code, 0, 413), ("1480년 나이", self.person_age, -100, 100), ("초기 명성", self.person_fame, 0, 65535), ("초기 악명", self.person_infamy, 0, 65535), ("고용비 계수", self.person_hire_cost, 0, 2000))
        for row, (label, variable, low, high) in enumerate(person_rows, start=1):
            ttk.Label(person_box, text=f"{label}:").grid(row=row, column=0, pady=2, sticky="w")
            entry = ttk.Spinbox(person_box, from_=low, to=high, textvariable=variable, width=7, state="disabled")
            entry.grid(row=row, column=1, padx=(6, 0), pady=2, sticky="w"); self._limit_integer_input(entry, low, high); self._person_controls.append(entry)
            if label == "얼굴 코드": self.person_face_entry = entry
        for row, label, variable, values, width in (
            (1, "성별", self.person_gender, SPONSOR_GENDER_NAMES, 8),
            (2, "국가", self.person_nation, SPONSOR_NATION_NAMES, 16),
            (3, "직업", self.person_job, PERSON_JOB_NAMES, 8),
            (4, "등용 상태", self.person_employment_state, PERSON_EMPLOYMENT_STATE_NAMES, 10),
            (6, "혈액형", self.person_blood, PERSON_BLOOD_NAMES, 8),
            (7, "출현 도시", self.person_city, ("도시 없음", *BARMAID_CITY_NAMES), 16),
            (8, "출현 시설", self.person_building, SPONSOR_BUILDING_NAMES, 8),
        ):
            column = 2 if row <= 4 else 0
            ttk.Label(person_box, text=f"{label}:").grid(row=row, column=column, padx=(14, 0) if column else 0, pady=2, sticky="w")
            combo = ttk.Combobox(person_box, textvariable=variable, values=values, width=width, state="disabled")
            combo.grid(row=row, column=column + 1, padx=(6, 0), pady=2, sticky="w"); self._bind_combobox_arrow_selection(combo); self._person_controls.append(combo)

        ability_box = ttk.LabelFrame(person_basic_tab, text="능력치", padding=10)
        ability_box.grid(row=1, column=0, pady=(10, 0), sticky="ew")
        for index, (name, variable) in enumerate(zip(PERSON_ABILITY_NAMES, self.person_abilities)):
            row, column = index % 2, (index // 2) * 2
            ttk.Label(ability_box, text=f"{name}:").grid(row=row, column=column, padx=(12, 0) if column else 0, pady=3, sticky="w")
            entry = ttk.Spinbox(ability_box, from_=0, to=255, textvariable=variable, width=6, state="disabled")
            entry.grid(row=row, column=column + 1, padx=(6, 0), pady=3, sticky="w")
            self._limit_integer_input(entry, 0, 255); self._person_controls.append(entry)
        ttk.Label(ability_box, text="생명력:").grid(row=2, column=0, pady=(8, 0), sticky="w")
        vitality_entry = ttk.Spinbox(ability_box, from_=0, to=2000, textvariable=self.person_vitality, width=6, state="disabled")
        vitality_entry.grid(row=2, column=1, padx=(6, 0), pady=(8, 0), sticky="w")
        self._limit_integer_input(vitality_entry, 0, 2000); self._person_controls.append(vitality_entry)

        skill_box = ttk.LabelFrame(person_skill_tab, text="기술", padding=10)
        skill_box.grid(row=0, column=0, sticky="nw")
        language_box = ttk.LabelFrame(person_skill_tab, text="언어", padding=10)
        language_box.grid(row=0, column=1, padx=(8, 0), sticky="nw")
        for index, (name, variable) in enumerate(zip(PERSON_SKILL_NAMES, self.person_skill_levels[:len(PERSON_SKILL_NAMES)])):
            row, column = index % 7, (index // 7) * 2
            ttk.Label(skill_box, text=f"{name}:").grid(row=row, column=column, padx=(12, 0) if column else 0, pady=2, sticky="w")
            entry = ttk.Spinbox(skill_box, from_=0, to=3, textvariable=variable, width=3, state="disabled")
            entry.grid(row=row, column=column + 1, padx=(4, 0), pady=2, sticky="w")
            self._limit_integer_input(entry, 0, 3); self._person_controls.append(entry)
        for index, (name, variable) in enumerate(zip(BARMAID_LANGUAGE_NAMES, self.person_skill_levels[len(PERSON_SKILL_NAMES):])):
            row, column = index % 7, (index // 7) * 2
            ttk.Label(language_box, text=f"{name}:").grid(row=row, column=column, padx=(12, 0) if column else 0, pady=2, sticky="w")
            entry = ttk.Spinbox(language_box, from_=0, to=3, textvariable=variable, width=3, state="disabled")
            entry.grid(row=row, column=column + 1, padx=(4, 0), pady=2, sticky="w")
            self._limit_integer_input(entry, 0, 3); self._person_controls.append(entry)
        self._person_controls.extend((self.person_search_entry, self.person_list))
        self._set_person_controls_enabled(False)

        ship_list_box = ttk.LabelFrame(ship_tab, text="선종 목록", padding=10)
        ship_list_box.grid(row=0, column=0, sticky="ns")
        self.ship_type_list = ttk.Treeview(ship_list_box, columns=("id", "name"), show="headings", height=8, selectmode="browse")
        self.ship_type_list.heading("id", text="번호"); self.ship_type_list.heading("name", text="선종")
        self.ship_type_list.column("id", width=48, anchor="center", stretch=False); self.ship_type_list.column("name", width=150, anchor="w")
        ship_scroll = ttk.Scrollbar(ship_list_box, orient="vertical", command=self.ship_type_list.yview)
        self.ship_type_list.configure(yscrollcommand=ship_scroll.set)
        self.ship_type_list.grid(row=0, column=0, sticky="nsew"); ship_scroll.grid(row=0, column=1, sticky="ns")
        self.ship_type_list.bind("<<TreeviewSelect>>", self._on_ship_type_selected)

        ship_box = ttk.LabelFrame(ship_tab, text="선종 기본 정보", padding=10)
        ship_box.grid(row=0, column=1, padx=(10, 0), sticky="nw")
        ttk.Label(ship_box, text="선종 이름 (한글 최대 5자):").grid(row=0, column=0, sticky="w")
        ship_name_host = tk.Frame(ship_box, width=180, height=23)
        ship_name_host.grid(row=0, column=1, columnspan=3, padx=(6, 0), sticky="w")
        self.ship_name_entry = NativeWinEdit(ship_name_host, lambda: None, width=180, height=23)
        self.ship_name_entry.max_bytes = 10
        self.ship_name_entry.max_characters = 5
        ship_rows = (
            ("조선소 조건", self.ship_shipyard_requirement, 0, 127),
            ("기본 추진력", self.ship_base_power, 0, 255),
            ("추진력 한계", self.ship_power_limit, 0, 255),
            ("기본 내구력", self.ship_base_durability, 0, 0x7FFFFFFF),
            ("내구력 한계", self.ship_durability_limit, 0, 0x7FFFFFFF),
            ("기본 중량", self.ship_base_weight, 0, 0xFFFFFFFF),
            ("중량 한계", self.ship_weight_limit, 0, 0xFFFFFFFF),
            ("기본 적재(내부)", self.ship_base_capacity, 0, 0xFFFFFFFF),
            ("적재 한계", self.ship_capacity_limit, 0, 0xFFFFFFFF),
            ("기본 포문", self.ship_base_cannons, 0, 255),
            ("포문 한계", self.ship_cannon_limit, 0, 255),
            ("최소 승무원", self.ship_min_crew, 10, 265),
        )
        for index, (label, variable, low, high) in enumerate(ship_rows):
            row, column = index % 6 + 1, (index // 6) * 2
            ttk.Label(ship_box, text=f"{label}:").grid(row=row, column=column, padx=(16, 0) if column else 0, pady=2, sticky="w")
            entry = ttk.Spinbox(ship_box, from_=low, to=high, textvariable=variable, width=11, state="disabled")
            entry.grid(row=row, column=column + 1, padx=(6, 0), pady=2, sticky="w")
            self._limit_integer_input(entry, low, high); self._ship_type_controls.append(entry)
        self._ship_type_controls.extend((self.ship_name_entry, self.ship_type_list))
        self._set_ship_type_controls_enabled(False)

        # The original S00~S07 AVI clips are 240×176.  Keep that exact size
        # directly beneath the static ship-type details.
        ship_preview_box = tk.Frame(
            ship_tab, width=244, height=180, bg="#222222", relief="ridge", bd=2,
        )
        ship_preview_box.grid(row=1, column=1, padx=(10, 0), pady=(10, 0), sticky="nw")
        ship_preview_box.grid_propagate(False)
        self.ship_image_preview = tk.Label(
            ship_preview_box, bg="#222222", fg="#DDDDDD", text="이미지 없음",
        )
        self.ship_image_preview.pack(fill=tk.BOTH, expand=True)
        self._ship_avi_preview = AviPreview(
            self, self.ship_image_preview, width=240, height=176,
        )

        city_list_box = ttk.LabelFrame(city_tab, text="도시 목록", padding=10)
        city_list_box.grid(row=0, column=0, rowspan=2, sticky="ns")
        city_tab.grid_columnconfigure(1, weight=1)
        city_tab.grid_rowconfigure(1, minsize=324)
        ttk.Label(city_list_box, text="검색:").grid(row=0, column=0, sticky="w")
        city_search_host = tk.Frame(city_list_box, width=180, height=23)
        city_search_host.grid(row=0, column=1, padx=(6, 0), sticky="w")
        self.city_search_entry = NativeWinEdit(
            city_search_host, self._schedule_city_list_refresh, width=180, height=23,
        )
        city_list_frame = ttk.Frame(city_list_box)
        city_list_frame.grid(row=1, column=0, columnspan=2, pady=(8, 0), sticky="nsew")
        self.city_list = ttk.Treeview(
            city_list_frame, columns=("id", "name"), show="headings", height=16, selectmode="browse",
        )
        self.city_list.heading("id", text="번호"); self.city_list.heading("name", text="이름")
        self.city_list.column("id", width=48, anchor="center", stretch=False)
        self.city_list.column("name", width=160, anchor="w", stretch=True)
        city_scroll = ttk.Scrollbar(city_list_frame, orient="vertical", command=self.city_list.yview)
        self.city_list.configure(yscrollcommand=city_scroll.set)
        self.city_list.grid(row=0, column=0, sticky="nsew"); city_scroll.grid(row=0, column=1, sticky="ns")
        self.city_list.bind("<<TreeviewSelect>>", self._on_city_selected)

        city_details_notebook = ttk.Notebook(city_tab)
        city_details_notebook.grid(row=0, column=1, padx=(10, 0), sticky="nsew")
        city_info_tab = ttk.Frame(city_details_notebook, padding=2)
        city_ship_tab = ttk.Frame(city_details_notebook, padding=2)
        city_market_tab = ttk.Frame(city_details_notebook, padding=2)
        city_trade_tab = ttk.Frame(city_details_notebook, padding=2)
        city_facility_tab = ttk.Frame(city_details_notebook, padding=2)
        city_details_notebook.add(city_info_tab, text="도시 정보")
        city_details_notebook.add(city_ship_tab, text="조선소")
        city_details_notebook.add(city_market_tab, text="시장 정보")
        city_details_notebook.add(city_trade_tab, text="교역")
        city_details_notebook.add(city_facility_tab, text="시설 정보")
        # Reserve the lower 400×320 preview first.  The editor notebook gets
        # only the vertical space left in the fixed-size main window.
        city_details_notebook.configure(height=130)

        city_box = ttk.Frame(city_info_tab)
        city_box.grid(row=0, column=0, sticky="nw")
        city_trade_box = ttk.Frame(city_trade_tab)
        city_trade_box.grid(row=0, column=0, pady=(6, 0), sticky="nw")
        ttk.Label(city_box, text="이름:").grid(row=0, column=0, sticky="w")
        city_name_host = tk.Frame(city_box, width=180, height=23)
        city_name_host.grid(row=0, column=1, padx=(6, 0), sticky="w")
        self.city_name_entry = NativeWinEdit(city_name_host, lambda: None, width=180, height=23)
        self.city_name_entry.max_characters = 8; self.city_name_entry.max_bytes = 16
        self.city_discovered_check = ttk.Checkbutton(
            city_box, text="발견", variable=self.city_discovered, state="disabled",
        )
        self.city_discovered_check.grid(row=0, column=2, columnspan=2, padx=(14, 0), sticky="w")

        ttk.Label(city_box, text="소속 국가:").grid(row=1, column=0, pady=(4, 0), sticky="w")
        self.city_nation_selector = ttk.Combobox(
            city_box, textvariable=self.city_nation, values=CITY_NATION_NAMES, width=18, state="disabled",
        )
        self.city_nation_selector.grid(row=1, column=1, padx=(6, 0), pady=(4, 0), sticky="w")
        self._bind_combobox_arrow_selection(self.city_nation_selector)
        ttk.Label(city_box, text="문화권:").grid(row=1, column=2, padx=(14, 0), pady=(4, 0), sticky="w")
        self.city_culture_selector = ttk.Combobox(
            city_box, textvariable=self.city_culture, values=CITY_CULTURE_NAMES, width=12, state="disabled",
        )
        self.city_culture_selector.grid(row=1, column=3, padx=(6, 0), pady=(4, 0), sticky="w")
        self._bind_combobox_arrow_selection(self.city_culture_selector)

        ttk.Label(city_box, text="도시 상태:").grid(row=2, column=0, pady=(4, 0), sticky="w")
        self.city_status_selector = ttk.Combobox(
            city_box, textvariable=self.city_status, values=CITY_STATUS_NAMES, width=18, state="disabled",
        )
        self.city_status_selector.grid(row=2, column=1, padx=(6, 0), pady=(4, 0), sticky="w")
        self._bind_combobox_arrow_selection(self.city_status_selector)
        ttk.Label(city_box, text="도시 규모:").grid(row=2, column=2, padx=(14, 0), pady=(4, 0), sticky="w")
        self.city_shipyard_level_selector = ttk.Combobox(
            city_box, textvariable=self.city_shipyard_level,
            values=CITY_SHIPYARD_LEVEL_NAMES, width=18, state="disabled",
        )
        self.city_shipyard_level_selector.grid(row=2, column=3, padx=(6, 0), pady=(4, 0), sticky="w")
        self._bind_combobox_arrow_selection(self.city_shipyard_level_selector)

        connection_values = ("연결 없음", *BARMAID_CITY_NAMES)
        self.city_connection_selectors: list[ttk.Combobox] = []
        for slot, variable in enumerate(self.city_inland_connections, start=1):
            label_column = 0 if slot == 1 else 2
            value_column = label_column + 1
            ttk.Label(city_box, text=f"내륙 연결 {slot}:").grid(
                row=3, column=label_column, padx=(14, 0) if slot == 2 else 0,
                pady=(4, 0), sticky="w",
            )
            selector = ttk.Combobox(
                city_box, textvariable=variable, values=connection_values, width=18, state="disabled",
            )
            selector.grid(row=3, column=value_column, padx=(6, 0), pady=(4, 0), sticky="w")
            self._bind_combobox_arrow_selection(selector)
            self.city_connection_selectors.append(selector); self._city_controls.append(selector)
        ttk.Label(city_trade_box, text="교역권:").grid(row=0, column=0, sticky="w")
        self.city_trade_region_entry = ttk.Spinbox(
            city_trade_box, from_=0, to=26, textvariable=self.city_trade_region, width=6, state="disabled",
        )
        self.city_trade_region_entry.grid(row=0, column=1, padx=(6, 0), sticky="w")
        self._limit_integer_input(self.city_trade_region_entry, 0, 26)
        ttk.Label(city_trade_box, text="시세:").grid(row=1, column=0, pady=(4, 0), sticky="w")
        self.city_update_counter_entry = ttk.Spinbox(
            city_trade_box, from_=0, to=255, textvariable=self.city_update_counter,
            width=6, state="disabled",
        )
        self.city_update_counter_entry.grid(row=1, column=1, padx=(6, 0), pady=(4, 0), sticky="w")
        self._limit_integer_input(self.city_update_counter_entry, 0, 255)
        ttk.Label(city_trade_box, text="특산품:").grid(row=2, column=0, pady=(4, 0), sticky="w")
        self.city_specialty_selector = ttk.Combobox(
            city_trade_box, textvariable=self.city_specialty, values=("특산품 없음",), width=18, state="disabled",
        )
        self.city_specialty_selector.grid(row=2, column=1, padx=(6, 0), pady=(4, 0), sticky="w")
        self._bind_combobox_arrow_selection(self.city_specialty_selector)
        ttk.Label(city_trade_box, text="기준가:").grid(row=3, column=0, pady=(4, 0), sticky="w")
        self.city_specialty_price_entry = ttk.Spinbox(
            city_trade_box, from_=0, to=99_999_999, textvariable=self.city_specialty_price,
            width=10, state="disabled",
        )
        self.city_specialty_price_entry.grid(row=3, column=1, padx=(6, 0), pady=(4, 0), sticky="w")
        self._limit_integer_input(self.city_specialty_price_entry, 0, 99_999_999)
        ttk.Label(city_trade_box, text="공급 단계:").grid(row=4, column=0, pady=(4, 0), sticky="w")
        self.city_specialty_supply_entry = ttk.Spinbox(
            city_trade_box, from_=0, to=7, textvariable=self.city_specialty_supply_index, width=6, state="disabled",
        )
        self.city_specialty_supply_entry.grid(row=4, column=1, padx=(6, 0), pady=(4, 0), sticky="w")
        self._limit_integer_input(self.city_specialty_supply_entry, 0, 7)
        self.city_common_good_entries: list[ttk.Entry] = []
        for identifier, variable in enumerate(self.city_common_goods):
            ttk.Label(city_trade_box, text=f"교역품 {identifier + 1}:").grid(
                row=identifier, column=2, padx=(24, 0),
                pady=(4, 0) if identifier else 0, sticky="w",
            )
            entry = ttk.Entry(
                city_trade_box, textvariable=variable, width=18,
                state="readonly", takefocus=False,
            )
            entry.grid(
                row=identifier, column=3, padx=(6, 0),
                pady=(4, 0) if identifier else 0, sticky="w",
            )
            self.city_common_good_entries.append(entry)
        self._city_controls.extend((
            self.city_name_entry, self.city_list, self.city_search_entry,
            self.city_discovered_check, self.city_nation_selector, self.city_culture_selector,
            self.city_status_selector, self.city_update_counter_entry,
            self.city_trade_region_entry, self.city_specialty_selector,
            self.city_specialty_price_entry, self.city_specialty_supply_entry,
        ))

        city_ship_box = ttk.Frame(city_ship_tab, padding=2)
        city_ship_box.grid(row=0, column=0, sticky="nw")
        self._city_controls.append(self.city_shipyard_level_selector)
        self.city_ship_candidate_buttons: list[ttk.Checkbutton] = []
        for identifier, variable in enumerate(self.city_ship_candidates):
            button = ttk.Checkbutton(city_ship_box, text=f"선종 {identifier}", variable=variable, state="disabled")
            button.grid(
                row=identifier // 4, column=identifier % 4,
                padx=(8, 0) if identifier % 4 else 0, pady=(8, 0), sticky="w",
            )
            self.city_ship_candidate_buttons.append(button); self._city_controls.append(button)

        city_facility_box = ttk.Frame(city_facility_tab, padding=2)
        city_facility_box.grid(row=0, column=0, sticky="nw")
        self.city_facility_buttons: list[ttk.Checkbutton] = []
        for identifier, (name, variable) in enumerate(zip(CITY_FACILITY_NAMES, self.city_facilities)):
            button = ttk.Checkbutton(city_facility_box, text=name, variable=variable, state="disabled")
            button.grid(
                row=identifier // 4, column=identifier % 4,
                padx=(10, 0) if identifier % 4 else 0, pady=(4, 0), sticky="w",
            )
            self.city_facility_buttons.append(button); self._city_controls.append(button)

        city_trade_region_box = ttk.LabelFrame(trade_region_tab, text="교역권 정보", padding=10)
        city_trade_region_box.grid(row=0, column=0, sticky="nw")
        ttk.Label(city_trade_region_box, text="교역권:").grid(row=0, column=0, sticky="w")
        self.city_trade_region_editor_selector = ttk.Combobox(
            city_trade_region_box, textvariable=self.city_trade_region_editor,
            values=tuple(str(identifier) for identifier in range(27)),
            width=6, state="disabled",
        )
        self.city_trade_region_editor_selector.grid(row=0, column=1, padx=(6, 0), sticky="w")
        self._bind_combobox_arrow_selection(self.city_trade_region_editor_selector)
        self.city_trade_region_editor_selector.bind(
            "<<ComboboxSelected>>", self._show_trade_region_editor,
        )
        self._city_controls.append(self.city_trade_region_editor_selector)
        self.city_trade_region_good_selectors: list[ttk.Combobox] = []
        self.city_trade_region_good_image_labels: list[tk.Label] = []
        for identifier, variable in enumerate(self.city_trade_region_goods):
            good_box = ttk.Frame(city_trade_region_box)
            good_box.grid(
                row=1, column=identifier, padx=(0, 10) if identifier < 4 else 0,
                pady=(12, 0), sticky="w",
            )
            ttk.Label(good_box, text=f"교역품 {identifier + 1}").pack(anchor="w")
            selector = ttk.Combobox(
                good_box, textvariable=variable,
                values=("없음",), width=15, state="disabled",
            )
            selector.pack(pady=(4, 0), anchor="w")
            self._bind_combobox_arrow_selection(selector)
            selector.bind(
                "<<ComboboxSelected>>",
                lambda _event, slot=identifier: self._on_trade_region_good_selected(slot),
            )
            self.city_trade_region_good_selectors.append(selector)
            self._city_controls.append(selector)

            preview_box = tk.Frame(
                city_trade_region_box, width=124, height=124,
                bg="#222222", relief="ridge", bd=2,
            )
            preview_box.grid(
                row=2, column=identifier, padx=(0, 10) if identifier < 4 else 0,
                pady=(8, 0), sticky="nw",
            )
            preview_box.pack_propagate(False)
            preview_label = tk.Label(
                preview_box, bg="#222222", fg="#dddddd", text="이미지 없음",
            )
            preview_label.pack(fill=tk.BOTH, expand=True)
            self.city_trade_region_good_image_labels.append(preview_label)

        city_market_box = ttk.Frame(city_market_tab)
        city_market_box.grid(row=0, column=0, sticky="nw")
        self.city_market_good_selectors: list[ttk.Combobox] = []
        for identifier, variable in enumerate(self.city_market_goods):
            row, column = identifier % 4, (identifier // 4) * 2
            ttk.Label(city_market_box, text=f"품목 {identifier + 1}:").grid(
                row=row, column=column, padx=(10, 0) if column else 0, pady=(4, 0), sticky="w",
            )
            selector = ttk.Combobox(
                city_market_box, textvariable=variable, values=("없음",), width=13, state="disabled",
            )
            selector.grid(row=row, column=column + 1, padx=(5, 0), pady=(4, 0), sticky="w")
            self._bind_combobox_arrow_selection(selector)
            self.city_market_good_selectors.append(selector); self._city_controls.append(selector)

        city_preview_box = tk.Frame(city_tab, width=404, height=324, bg="#222222", relief="ridge", bd=2)
        # CITYCG 원본 크기는 도시 정보 아래에 별도 프레임으로 둔다.
        city_preview_box.grid(row=1, column=1, padx=(10, 0), pady=(10, 0), sticky="sw")
        city_preview_box.grid_propagate(False)
        city_preview_box.pack_propagate(False)
        self.city_image_preview = tk.Label(city_preview_box, bg="#222222", fg="#DDDDDD", text="이미지 없음")
        self.city_image_preview.pack(fill=tk.BOTH, expand=True)
        self._set_city_controls_enabled(False)

        item_list_box = ttk.LabelFrame(item_tab, text="아이템 목록", padding=10)
        item_list_box.grid(row=0, column=0, rowspan=2, sticky="nsew")
        item_tab.grid_rowconfigure(1, weight=1)
        item_list_box.columnconfigure(0, weight=1)
        item_list_box.rowconfigure(1, weight=1)
        ttk.Label(item_list_box, text="검색:").grid(row=0, column=0, sticky="w")
        item_search_host = tk.Frame(item_list_box, width=180, height=23)
        item_search_host.grid(row=0, column=1, padx=(6, 0), sticky="w")
        self.item_search_entry = NativeWinEdit(item_search_host, self._schedule_item_list_refresh, width=180, height=23)
        item_list_frame = ttk.Frame(item_list_box)
        item_list_frame.grid(row=1, column=0, columnspan=2, pady=(8, 0), sticky="nsew")
        self.item_list = ttk.Treeview(
            item_list_frame, columns=("id", "category", "name"), show="headings", height=17, selectmode="browse",
        )
        self.item_list.heading("id", text="번호"); self.item_list.heading("category", text="분류"); self.item_list.heading("name", text="이름")
        self.item_list.column("id", width=48, anchor="center", stretch=False)
        self.item_list.column("category", width=68, anchor="center", stretch=False)
        self.item_list.column("name", width=185, anchor="w")
        item_scroll = ttk.Scrollbar(item_list_frame, orient="vertical", command=self.item_list.yview)
        self.item_list.configure(yscrollcommand=item_scroll.set)
        self.item_list.grid(row=0, column=0, sticky="nsew"); item_scroll.grid(row=0, column=1, sticky="ns")
        self.item_list.bind("<<TreeviewSelect>>", self._on_item_selected)
        item_list_frame.columnconfigure(0, weight=1)
        item_list_frame.rowconfigure(0, weight=1)

        item_box = ttk.LabelFrame(item_tab, text="아이템 정보", padding=10)
        item_box.grid(row=0, column=1, padx=(10, 0), sticky="nw")
        ttk.Label(item_box, text="이름 (한글 최대 11자):").grid(row=0, column=0, sticky="w")
        item_name_host = tk.Frame(item_box, width=220, height=23)
        item_name_host.grid(row=0, column=1, padx=(6, 0), sticky="w")
        self.item_name_entry = NativeWinEdit(item_name_host, lambda: None, width=220, height=23)
        self.item_name_entry.max_characters = 11
        self.item_name_entry.max_bytes = 21
        ttk.Label(item_box, text="분류:").grid(row=1, column=0, pady=(8, 0), sticky="w")
        self.item_category_selector = ttk.Combobox(
            item_box, textvariable=self.item_category, values=ITEM_CATEGORY_NAMES, width=12, state="disabled",
        )
        self.item_category_selector.grid(row=1, column=1, padx=(6, 0), pady=(8, 0), sticky="w")
        self._bind_combobox_arrow_selection(self.item_category_selector)
        ttk.Label(item_box, text="구매가:").grid(row=2, column=0, pady=(8, 0), sticky="w")
        self.item_buy_price_entry = ttk.Spinbox(
            item_box, from_=0, to=99_999_999, textvariable=self.item_buy_price, width=11, state="disabled",
        )
        self.item_buy_price_entry.grid(row=2, column=1, padx=(6, 0), pady=(8, 0), sticky="w")
        self._limit_integer_input(self.item_buy_price_entry, 0, 99_999_999)
        ttk.Label(item_box, text="판매가:").grid(row=3, column=0, pady=(8, 0), sticky="w")
        self.item_sell_price_entry = ttk.Spinbox(
            item_box, from_=0, to=99_999_999, textvariable=self.item_sell_price, width=11, state="disabled",
        )
        self.item_sell_price_entry.grid(row=3, column=1, padx=(6, 0), pady=(8, 0), sticky="w")
        self._limit_integer_input(self.item_sell_price_entry, 0, 99_999_999)
        ttk.Label(item_box, text="효과 코드:").grid(row=4, column=0, pady=(8, 0), sticky="w")
        self.item_effect_entry = ttk.Spinbox(
            item_box, from_=0, to=255, textvariable=self.item_effect_value, width=7, state="disabled",
        )
        self.item_effect_entry.grid(row=4, column=1, padx=(6, 0), pady=(8, 0), sticky="w")
        self._limit_integer_input(self.item_effect_entry, 0, 255)
        item_preview_box = tk.Frame(item_tab, width=124, height=124, bg="#222222", relief="ridge", bd=2)
        item_preview_box.grid(row=1, column=1, padx=(10, 0), pady=(10, 0), sticky="nw")
        item_preview_box.grid_propagate(False)
        self.item_image_preview = tk.Label(item_preview_box, bg="#222222", fg="#DDDDDD", text="이미지 없음")
        self.item_image_preview.pack(expand=True)
        self._item_controls.extend((
            self.item_search_entry, self.item_list, self.item_name_entry,
            self.item_category_selector, self.item_buy_price_entry,
            self.item_sell_price_entry, self.item_effect_entry,
        ))
        self._set_item_controls_enabled(False)

        figurehead_tab.columnconfigure(0, weight=1)
        figurehead_tab.columnconfigure(1, weight=1)
        figurehead_tab.rowconfigure(0, weight=1)

        figurehead_list_box = ttk.LabelFrame(figurehead_tab, text="선수상 효과 목록", padding=10)
        figurehead_list_box.grid(row=0, column=0, padx=(0, 5), sticky="nsew")
        figurehead_list_box.columnconfigure(0, weight=1)
        figurehead_list_box.rowconfigure(0, weight=1)
        figurehead_list_frame = ttk.Frame(figurehead_list_box)
        figurehead_list_frame.grid(row=0, column=0, sticky="nsew")
        figurehead_list_frame.columnconfigure(0, weight=1)
        figurehead_list_frame.rowconfigure(0, weight=1)
        self.figurehead_list = ttk.Treeview(
            figurehead_list_frame,
            columns=("code", "effect", "setting"),
            show="headings",
            height=20,
            selectmode="none",
        )
        self.figurehead_list.heading("code", text="코드")
        self.figurehead_list.heading("effect", text="효과")
        self.figurehead_list.heading("setting", text="설정값")
        self.figurehead_list.column("code", width=45, anchor="center", stretch=False)
        self.figurehead_list.column("effect", width=205, anchor="w", stretch=False)
        self.figurehead_list.column("setting", width=160, anchor="w", stretch=True)
        figurehead_scroll = ttk.Scrollbar(
            figurehead_list_frame, orient="vertical", command=self.figurehead_list.yview,
        )
        self.figurehead_list.configure(yscrollcommand=figurehead_scroll.set)
        self.figurehead_list.grid(row=0, column=0, sticky="nsew")
        figurehead_scroll.grid(row=0, column=1, sticky="ns")
        self.figurehead_list.bind("<<TreeviewSelect>>", self._on_figurehead_effect_selected)

        figurehead_detail_box = ttk.LabelFrame(figurehead_tab, text="선택한 선수상 효과", padding=10)
        figurehead_detail_box.grid(row=0, column=1, padx=(5, 0), sticky="new")
        ttk.Label(figurehead_detail_box, text="효과 코드:").grid(row=0, column=0, sticky="w")
        ttk.Label(figurehead_detail_box, textvariable=self.figurehead_selected_code).grid(
            row=0, column=1, columnspan=2, padx=(8, 0), sticky="w",
        )
        ttk.Label(figurehead_detail_box, text="효과:").grid(row=1, column=0, pady=(8, 0), sticky="w")
        ttk.Label(figurehead_detail_box, textvariable=self.figurehead_selected_effect).grid(
            row=1, column=1, columnspan=2, padx=(8, 0), pady=(8, 0), sticky="w",
        )

        figurehead_disaster_box = ttk.LabelFrame(
            figurehead_detail_box, text="해상 재해 방지", padding=10,
        )
        figurehead_disaster_box.grid(row=2, column=0, columnspan=3, pady=(12, 0), sticky="ew")
        figurehead_disaster_box.columnconfigure(1, weight=1)
        ttk.Label(
            figurehead_disaster_box, textvariable=self.figurehead_disaster_description,
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(figurehead_disaster_box, text="방지 확률:").grid(row=1, column=0, pady=(8, 0), sticky="w")
        self.figurehead_disaster_entry = ttk.Spinbox(
            figurehead_disaster_box, from_=0, to=100,
            textvariable=self.figurehead_disaster_chances[0], width=8, state="disabled",
        )
        self.figurehead_disaster_entry.grid(row=1, column=1, padx=(8, 4), pady=(8, 0), sticky="w")
        ttk.Label(figurehead_disaster_box, text="%").grid(row=1, column=2, pady=(8, 0), sticky="w")
        self._limit_integer_input(self.figurehead_disaster_entry, 0, 100)

        figurehead_value_box = ttk.LabelFrame(
            figurehead_detail_box, text="고유 효과", padding=10,
        )
        figurehead_value_box.grid(row=3, column=0, columnspan=3, pady=(10, 0), sticky="ew")
        self.figurehead_primary_label_widget = ttk.Label(
            figurehead_value_box, textvariable=self.figurehead_primary_label,
        )
        self.figurehead_primary_label_widget.grid(row=0, column=0, sticky="w")
        self.figurehead_primary_entry = ttk.Spinbox(
            figurehead_value_box, from_=0, to=1000, width=8, state="disabled",
        )
        self.figurehead_primary_entry.grid(row=0, column=1, padx=(8, 4), sticky="w")
        self.figurehead_primary_unit_widget = ttk.Label(
            figurehead_value_box, textvariable=self.figurehead_primary_unit,
        )
        self.figurehead_primary_unit_widget.grid(row=0, column=2, sticky="w")
        self.figurehead_secondary_label_widget = ttk.Label(
            figurehead_value_box, textvariable=self.figurehead_secondary_label,
        )
        self.figurehead_secondary_label_widget.grid(row=1, column=0, pady=(8, 0), sticky="w")
        self.figurehead_secondary_entry = ttk.Spinbox(
            figurehead_value_box, from_=1, to=127, width=8, state="disabled",
        )
        self.figurehead_secondary_entry.grid(row=1, column=1, padx=(8, 4), pady=(8, 0), sticky="w")
        self.figurehead_secondary_unit_widget = ttk.Label(
            figurehead_value_box, textvariable=self.figurehead_secondary_unit,
        )
        self.figurehead_secondary_unit_widget.grid(row=1, column=2, pady=(8, 0), sticky="w")
        self._figurehead_controls.extend((
            self.figurehead_list, self.figurehead_disaster_entry,
            self.figurehead_primary_entry, self.figurehead_secondary_entry,
        ))
        for variable in (
            *self.figurehead_disaster_chances,
            self.figurehead_cannon_damage_reduction,
            self.figurehead_shooting_damage_reduction,
            self.figurehead_melee_damage_reduction,
            self.figurehead_cannon_attack_percent,
            self.figurehead_shooting_attack_percent,
            self.figurehead_melee_attack_percent,
            self.figurehead_hull_recovery,
            self.figurehead_movement_bonus,
            self.figurehead_movement_maximum,
            self.figurehead_special_cannon_attack_percent,
            self.figurehead_all_attack_percent,
        ):
            variable.trace_add("write", self._on_figurehead_effect_value_changed)
        self._refresh_figurehead_effect_list()
        self._set_figurehead_controls_enabled(False)

        discovery_list_box = ttk.LabelFrame(discovery_tab, text="발견물 목록", padding=10)
        discovery_list_box.grid(row=0, column=0, rowspan=3, sticky="nsew")
        discovery_tab.grid_rowconfigure(2, weight=1)
        discovery_list_box.columnconfigure(0, weight=1)
        discovery_list_box.rowconfigure(1, weight=1)
        ttk.Label(discovery_list_box, text="검색:").grid(row=0, column=0, sticky="w")
        discovery_search_host = tk.Frame(discovery_list_box, width=160, height=23)
        discovery_search_host.grid(row=0, column=1, padx=(6, 0), sticky="w")
        self.discovery_search_entry = NativeWinEdit(
            discovery_search_host, self._schedule_discovery_list_refresh, width=160, height=23,
        )
        discovery_list_frame = ttk.Frame(discovery_list_box)
        discovery_list_frame.grid(row=1, column=0, columnspan=2, pady=(8, 0), sticky="nsew")
        self.discovery_list = ttk.Treeview(
            discovery_list_frame, columns=("id", "category", "name"), show="headings",
            height=16, selectmode="browse",
        )
        self.discovery_list.heading("id", text="번호")
        self.discovery_list.heading("category", text="분류")
        self.discovery_list.heading("name", text="이름")
        self.discovery_list.column("id", width=48, anchor="center", stretch=False)
        self.discovery_list.column("category", width=58, anchor="center", stretch=False)
        self.discovery_list.column("name", width=150, anchor="w", stretch=True)
        discovery_scroll = ttk.Scrollbar(
            discovery_list_frame, orient="vertical", command=self.discovery_list.yview,
        )
        self.discovery_list.configure(yscrollcommand=discovery_scroll.set)
        self.discovery_list.grid(row=0, column=0, sticky="nsew")
        discovery_scroll.grid(row=0, column=1, sticky="ns")
        self.discovery_list.bind("<<TreeviewSelect>>", self._on_discovery_selected)
        discovery_list_frame.columnconfigure(0, weight=1)
        discovery_list_frame.rowconfigure(0, weight=1)

        discovery_box = ttk.LabelFrame(discovery_tab, text="발견물 정보", padding=10)
        discovery_box.grid(row=0, column=1, padx=(10, 0), sticky="nw")
        discovery_name_row = ttk.Frame(discovery_box)
        discovery_name_row.grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(discovery_name_row, text="이름:").grid(row=0, column=0, sticky="w")
        discovery_name_host = tk.Frame(discovery_name_row, width=230, height=23)
        discovery_name_host.grid(row=0, column=1, padx=(6, 0), sticky="w")
        self.discovery_name_entry = NativeWinEdit(discovery_name_host, lambda: None, width=230, height=23)
        self.discovery_name_entry.max_bytes = 31
        self.discovery_media_label = ttk.Label(discovery_box, text="미디어 번호:")
        self.discovery_media_label.grid(row=0, column=2, padx=(14, 0), sticky="w")
        self.discovery_still_slot_entry = ttk.Spinbox(
            discovery_box, from_=0, to=84, textvariable=self.discovery_still_slot,
            width=7, state="disabled",
        )
        self.discovery_still_slot_entry.grid(row=0, column=3, padx=(6, 0), sticky="w")
        self._limit_integer_input(self.discovery_still_slot_entry, 0, 84)
        ttk.Label(discovery_box, text="분류:").grid(row=1, column=2, padx=(14, 0), pady=3, sticky="w")
        self.discovery_category_selector = ttk.Combobox(
            discovery_box, textvariable=self.discovery_category, values=DISCOVERY_CATEGORY_NAMES,
            width=10, state="disabled",
        )
        self.discovery_category_selector.grid(row=1, column=3, padx=(6, 0), pady=3, sticky="w")
        self._bind_combobox_arrow_selection(self.discovery_category_selector)
        ttk.Label(discovery_box, text="가치:").grid(row=2, column=2, padx=(14, 0), pady=3, sticky="w")
        self.discovery_value_entry = ttk.Spinbox(
            discovery_box, from_=0, to=99_999_999, textvariable=self.discovery_value,
            width=11, state="disabled",
        )
        self.discovery_value_entry.grid(row=2, column=3, padx=(6, 0), pady=3, sticky="w")
        self._limit_integer_input(self.discovery_value_entry, 0, 99_999_999)

        discovery_coordinates_box = ttk.Frame(discovery_box)
        discovery_coordinates_box.grid(row=1, column=0, rowspan=2, columnspan=2, pady=(3, 0), sticky="nw")
        for row, (axis, directions, first_direction, first, second_direction, second, high) in enumerate((
            ("위도", ("북위", "남위"), self.discovery_min_y_direction, self.discovery_min_y,
             self.discovery_max_y_direction, self.discovery_max_y, 90),
            ("경도", ("동경", "서경"), self.discovery_min_x_direction, self.discovery_min_x,
             self.discovery_max_x_direction, self.discovery_max_x, 180),
        )):
            ttk.Label(discovery_coordinates_box, text=f"{axis}:").grid(row=row, column=0, pady=3, sticky="w")
            first_direction_selector = ttk.Combobox(
                discovery_coordinates_box, textvariable=first_direction, values=directions,
                width=4, state="disabled",
            )
            first_direction_selector.grid(row=row, column=1, padx=(6, 0), pady=3, sticky="w")
            self._bind_combobox_arrow_selection(first_direction_selector)
            first_entry = ttk.Entry(discovery_coordinates_box, textvariable=first, width=7, state="disabled")
            first_entry.grid(row=row, column=2, padx=(4, 0), pady=3, sticky="w")
            self._limit_decimal_input(first_entry, 0, high)
            ttk.Label(discovery_coordinates_box, text="~").grid(row=row, column=3, padx=(4, 0), pady=3, sticky="w")
            second_direction_selector = ttk.Combobox(
                discovery_coordinates_box, textvariable=second_direction, values=directions,
                width=4, state="disabled",
            )
            second_direction_selector.grid(row=row, column=4, padx=(4, 0), pady=3, sticky="w")
            self._bind_combobox_arrow_selection(second_direction_selector)
            second_entry = ttk.Entry(discovery_coordinates_box, textvariable=second, width=7, state="disabled")
            second_entry.grid(row=row, column=5, padx=(4, 0), pady=3, sticky="w")
            self._limit_decimal_input(second_entry, 0, high)
            self._discovery_controls.extend((
                first_direction_selector, first_entry, second_direction_selector, second_entry,
            ))
            self._discovery_coordinate_controls.extend((
                first_direction_selector, first_entry, second_direction_selector, second_entry,
            ))

        discovery_preview_box = tk.Frame(discovery_tab, width=324, height=244, bg="#222222", relief="ridge", bd=2)
        discovery_preview_box.grid(row=1, column=1, padx=(10, 0), pady=(10, 0), sticky="nw")
        discovery_preview_box.grid_propagate(False)
        self.discovery_image_preview = tk.Label(
            discovery_preview_box, bg="#222222", fg="#DDDDDD", text="이미지 없음",
        )
        self.discovery_image_preview.pack(expand=True)
        self._discovery_avi_preview = AviPreview(
            self, self.discovery_image_preview, width=320, height=240,
        )
        self._discover_animation_preview = DiscoverAnimationPreview(
            self, self.discovery_image_preview,
        )
        self._discovery_controls.extend((
            self.discovery_search_entry,
            self.discovery_name_entry,
            self.discovery_list,
            self.discovery_category_selector,
            self.discovery_value_entry,
            self.discovery_still_slot_entry,
        ))
        self._set_discovery_controls_enabled(False)

        barmaid_list_box = ttk.LabelFrame(barmaid_tab, text="여급 목록", padding=10)
        barmaid_list_box.grid(row=0, column=0, rowspan=3, sticky="nsew")
        barmaid_tab.grid_rowconfigure(2, weight=1)
        barmaid_list_box.columnconfigure(0, weight=1)
        barmaid_list_box.rowconfigure(1, weight=1)
        ttk.Label(barmaid_list_box, text="검색:").grid(row=0, column=0, sticky="w")
        barmaid_search_host = tk.Frame(barmaid_list_box, width=150, height=23)
        barmaid_search_host.grid(row=0, column=1, padx=(6, 0), sticky="w")
        self.barmaid_search_entry = NativeWinEdit(
            barmaid_search_host, self._schedule_barmaid_list_refresh, width=150, height=23,
        )
        barmaid_list_frame = ttk.Frame(barmaid_list_box)
        barmaid_list_frame.grid(row=1, column=0, columnspan=2, pady=(8, 0), sticky="nsew")
        self.barmaid_list = ttk.Treeview(
            barmaid_list_frame, columns=("id", "name"), show="headings", height=15, selectmode="browse",
        )
        self.barmaid_list.heading("id", text="번호")
        self.barmaid_list.heading("name", text="이름")
        self.barmaid_list.column("id", width=48, anchor="center", stretch=False)
        self.barmaid_list.column("name", width=130, anchor="w", stretch=True)
        barmaid_list_scroll = ttk.Scrollbar(barmaid_list_frame, orient="vertical", command=self.barmaid_list.yview)
        self.barmaid_list.configure(yscrollcommand=barmaid_list_scroll.set)
        self.barmaid_list.grid(row=0, column=0, sticky="nsew")
        barmaid_list_scroll.grid(row=0, column=1, sticky="ns")
        self.barmaid_list.bind("<<TreeviewSelect>>", self._on_barmaid_selected)
        barmaid_list_frame.columnconfigure(0, weight=1)
        barmaid_list_frame.rowconfigure(0, weight=1)

        barmaid_box = ttk.LabelFrame(barmaid_tab, text="여급 정보", padding=10)
        barmaid_box.grid(row=0, column=1, padx=(10, 0), sticky="nw")
        ttk.Label(barmaid_box, text="이름 (한글 최대 6자):").grid(row=0, column=0, sticky="w")
        barmaid_name_host = tk.Frame(barmaid_box, width=150, height=23)
        barmaid_name_host.grid(row=0, column=1, padx=(6, 0), pady=(0, 8), sticky="w")
        self.barmaid_name_entry = NativeWinEdit(barmaid_name_host, lambda: None, width=150, height=23)
        self.barmaid_name_entry.max_bytes = 12
        ttk.Label(barmaid_box, text="출현 연도:").grid(row=1, column=0, sticky="w")
        self.barmaid_appearance_year_entry = ttk.Spinbox(
            barmaid_box, from_=1480, to=1600, textvariable=self.barmaid_appearance_year, width=7, state="disabled",
        )
        self.barmaid_appearance_year_entry.grid(row=1, column=1, padx=(6, 0), sticky="w")
        self._limit_integer_input(self.barmaid_appearance_year_entry, 1480, 1600)
        ttk.Label(barmaid_box, text="출현 도시:").grid(row=2, column=0, pady=(8, 0), sticky="w")
        self.barmaid_city_selector = ttk.Combobox(
            barmaid_box,
            textvariable=self.barmaid_city_name,
            values=BARMAID_CITY_NAMES,
            width=16,
            state="disabled",
        )
        self.barmaid_city_selector.grid(row=2, column=1, padx=(6, 0), pady=(8, 0), sticky="w")
        self._bind_combobox_arrow_selection(self.barmaid_city_selector)
        ttk.Label(barmaid_box, text="성격:").grid(row=3, column=0, pady=(8, 0), sticky="w")
        self.barmaid_personality_selector = ttk.Combobox(
            barmaid_box,
            textvariable=self.barmaid_personality,
            values=BARMAID_PERSONALITY_NAMES,
            width=16,
            state="disabled",
        )
        self.barmaid_personality_selector.grid(row=3, column=1, padx=(6, 0), pady=(8, 0), sticky="w")
        self._bind_combobox_arrow_selection(self.barmaid_personality_selector)
        ttk.Label(barmaid_box, text="얼굴 코드:").grid(row=4, column=0, pady=(8, 0), sticky="w")
        self.barmaid_face_code_entry = ttk.Spinbox(
            barmaid_box, from_=0, to=143, textvariable=self.barmaid_face_code, width=5, state="disabled",
        )
        self.barmaid_face_code_entry.grid(row=4, column=1, padx=(6, 0), pady=(8, 0), sticky="w")
        self._limit_integer_input(self.barmaid_face_code_entry, 0, 143)
        barmaid_preview_box = tk.Frame(
            barmaid_box, width=84, height=100, bg="#222222", relief="ridge", bd=2,
        )
        barmaid_preview_box.grid(row=0, column=2, rowspan=5, padx=(14, 0), sticky="n")
        barmaid_preview_box.grid_propagate(False)
        self.barmaid_image_preview = tk.Label(
            barmaid_preview_box, bg="#222222", fg="#DDDDDD", text="이미지 없음",
        )
        self.barmaid_image_preview.pack(fill=tk.BOTH, expand=True)

        language_box = ttk.LabelFrame(barmaid_tab, text="전수 언어", padding=8)
        language_box.grid(row=1, column=1, padx=(10, 0), pady=(10, 0), sticky="nw")
        language_buttons = ttk.Frame(language_box)
        language_buttons.grid(row=0, column=0, columnspan=3, pady=(0, 4), sticky="w")
        select_all_button = ttk.Button(language_buttons, text="전체 선택", command=self._select_all_barmaid_languages)
        select_all_button.grid(row=0, column=0)
        clear_all_button = ttk.Button(language_buttons, text="전체 해제", command=self._clear_all_barmaid_languages)
        clear_all_button.grid(row=0, column=1, padx=(6, 0))
        for index, (name, variable) in enumerate(zip(BARMAID_LANGUAGE_NAMES, self.barmaid_language_flags)):
            checkbutton = ttk.Checkbutton(language_box, text=name, variable=variable)
            checkbutton.grid(row=1 + index // 3, column=index % 3, padx=(0, 8), pady=1, sticky="w")
            self._barmaid_controls.append(checkbutton)
        self._barmaid_controls.extend((
            self.barmaid_search_entry,
            self.barmaid_name_entry,
            self.barmaid_list,
            self.barmaid_appearance_year_entry,
            self.barmaid_city_selector,
            self.barmaid_personality_selector,
            self.barmaid_face_code_entry,
            select_all_button,
            clear_all_button,
        ))

        aptitude_box = ttk.LabelFrame(barmaid_tab, text="자녀 능력치 보정", padding=10)
        aptitude_box.grid(row=1, column=2, padx=(10, 0), pady=(10, 0), sticky="nw")
        ttk.Label(aptitude_box, textvariable=self.barmaid_child_aptitude_total).grid(
            row=0, column=0, columnspan=2, pady=(0, 6), sticky="w",
        )
        for row, (name, variable) in enumerate(zip(BARMAID_CHILD_APTITUDE_NAMES, self.barmaid_child_aptitudes), start=1):
            ttk.Label(aptitude_box, text=f"{name}:").grid(row=row, column=0, pady=2, sticky="w")
            spinbox = ttk.Spinbox(
                aptitude_box, from_=-255, to=255, textvariable=variable, width=5, state="disabled",
            )
            spinbox.grid(row=row, column=1, padx=(6, 0), pady=2, sticky="w")
            self._limit_integer_input(spinbox, -255, 255)
            self._barmaid_controls.append(spinbox)
        self._set_barmaid_controls_enabled(False)

        sponsor_list_box = ttk.LabelFrame(sponsor_tab, text="후원자 목록", padding=10)
        sponsor_list_box.grid(row=0, column=0, rowspan=4, sticky="nsew")
        sponsor_tab.grid_rowconfigure(3, weight=1)
        sponsor_list_box.columnconfigure(0, weight=1)
        sponsor_list_box.rowconfigure(1, weight=1)
        ttk.Label(sponsor_list_box, text="검색:").grid(row=0, column=0, sticky="w")
        sponsor_search_host = tk.Frame(sponsor_list_box, width=150, height=23)
        sponsor_search_host.grid(row=0, column=1, padx=(6, 0), sticky="w")
        self.sponsor_search_entry = NativeWinEdit(
            sponsor_search_host, self._schedule_sponsor_list_refresh, width=150, height=23,
        )
        sponsor_list_frame = ttk.Frame(sponsor_list_box)
        sponsor_list_frame.grid(row=1, column=0, columnspan=2, pady=(8, 0), sticky="nsew")
        self.sponsor_list = ttk.Treeview(
            sponsor_list_frame, columns=("id", "name"), show="headings", height=15, selectmode="browse",
        )
        self.sponsor_list.heading("id", text="번호")
        self.sponsor_list.heading("name", text="이름")
        self.sponsor_list.column("id", width=48, anchor="center", stretch=False)
        self.sponsor_list.column("name", width=165, anchor="w", stretch=True)
        sponsor_list_scroll = ttk.Scrollbar(sponsor_list_frame, orient="vertical", command=self.sponsor_list.yview)
        self.sponsor_list.configure(yscrollcommand=sponsor_list_scroll.set)
        self.sponsor_list.grid(row=0, column=0, sticky="nsew")
        sponsor_list_scroll.grid(row=0, column=1, sticky="ns")
        self.sponsor_list.bind("<<TreeviewSelect>>", self._on_sponsor_selected)
        sponsor_list_frame.columnconfigure(0, weight=1)
        sponsor_list_frame.rowconfigure(0, weight=1)

        sponsor_box = ttk.LabelFrame(sponsor_tab, text="후원자 정보", padding=10)
        sponsor_box.grid(row=0, column=1, padx=(10, 0), sticky="nw")
        ttk.Label(sponsor_box, text="이름:").grid(row=0, column=0, sticky="w")
        ttk.Label(sponsor_box, textvariable=self.sponsor_name, width=24).grid(
            row=0, column=1, columnspan=3, padx=(6, 0), sticky="w",
        )
        sponsor_preview_box = tk.Frame(
            sponsor_box, width=84, height=100, bg="#222222", relief="ridge", bd=2,
        )
        sponsor_preview_box.grid(row=0, column=4, rowspan=6, padx=(14, 0), sticky="n")
        sponsor_preview_box.grid_propagate(False)
        self.sponsor_image_preview = tk.Label(
            sponsor_preview_box, bg="#222222", fg="#DDDDDD", text="이미지 없음",
        )
        self.sponsor_image_preview.pack(fill=tk.BOTH, expand=True)
        ttk.Label(sponsor_box, text="얼굴 코드:").grid(row=1, column=0, pady=(8, 0), sticky="w")
        self.sponsor_face_code_entry = ttk.Spinbox(
            sponsor_box, from_=0, to=413, textvariable=self.sponsor_face_code, width=5, state="disabled",
        )
        self.sponsor_face_code_entry.grid(row=1, column=1, padx=(6, 0), pady=(8, 0), sticky="w")
        self._limit_integer_input(self.sponsor_face_code_entry, 0, 413)
        ttk.Label(sponsor_box, text="성별:").grid(row=1, column=2, padx=(14, 0), pady=(8, 0), sticky="w")
        self.sponsor_gender_selector = ttk.Combobox(
            sponsor_box, textvariable=self.sponsor_gender, values=SPONSOR_GENDER_NAMES, width=7, state="disabled",
        )
        self.sponsor_gender_selector.grid(row=1, column=3, padx=(6, 0), pady=(8, 0), sticky="w")
        self._bind_combobox_arrow_selection(self.sponsor_gender_selector)
        ttk.Label(sponsor_box, text="국가:").grid(row=2, column=0, pady=(8, 0), sticky="w")
        self.sponsor_nation_selector = ttk.Combobox(
            sponsor_box, textvariable=self.sponsor_nation, values=SPONSOR_NATION_NAMES, width=18, state="disabled",
        )
        self.sponsor_nation_selector.grid(row=2, column=1, padx=(6, 0), pady=(8, 0), sticky="w")
        self._bind_combobox_arrow_selection(self.sponsor_nation_selector)
        ttk.Label(sponsor_box, text="직업:").grid(row=2, column=2, padx=(14, 0), pady=(8, 0), sticky="w")
        self.sponsor_job_selector = ttk.Combobox(
            sponsor_box, textvariable=self.sponsor_job, values=SPONSOR_JOB_NAMES, width=7, state="disabled",
        )
        self.sponsor_job_selector.grid(row=2, column=3, padx=(6, 0), pady=(8, 0), sticky="w")
        self._bind_combobox_arrow_selection(self.sponsor_job_selector)
        ttk.Label(sponsor_box, text="등장 연도:").grid(row=3, column=0, pady=(8, 0), sticky="w")
        self.sponsor_appearance_year_entry = ttk.Spinbox(
            sponsor_box, from_=1480, to=1600, textvariable=self.sponsor_appearance_year, width=7, state="disabled",
        )
        self.sponsor_appearance_year_entry.grid(row=3, column=1, padx=(6, 0), pady=(8, 0), sticky="w")
        self._limit_integer_input(self.sponsor_appearance_year_entry, 1480, 1600)
        ttk.Label(sponsor_box, text="소재 도시:").grid(row=3, column=2, padx=(14, 0), pady=(8, 0), sticky="w")
        self.sponsor_city_selector = ttk.Combobox(
            sponsor_box, textvariable=self.sponsor_city_name, values=BARMAID_CITY_NAMES, width=16, state="disabled",
        )
        self.sponsor_city_selector.grid(row=3, column=3, padx=(6, 0), pady=(8, 0), sticky="w")
        self._bind_combobox_arrow_selection(self.sponsor_city_selector)
        ttk.Label(sponsor_box, text="소재 시설:").grid(row=4, column=0, pady=(8, 0), sticky="w")
        self.sponsor_building_selector = ttk.Combobox(
            sponsor_box, textvariable=self.sponsor_building, values=SPONSOR_BUILDING_NAMES, width=10, state="disabled",
        )
        self.sponsor_building_selector.grid(row=4, column=1, padx=(6, 0), pady=(8, 0), sticky="w")
        self._bind_combobox_arrow_selection(self.sponsor_building_selector)
        ttk.Label(sponsor_box, text="권력:").grid(row=4, column=2, padx=(14, 0), pady=(8, 0), sticky="w")
        self.sponsor_power_entry = ttk.Spinbox(
            sponsor_box, from_=0, to=99, textvariable=self.sponsor_power, width=5, state="disabled",
        )
        self.sponsor_power_entry.grid(row=4, column=3, padx=(6, 0), pady=(8, 0), sticky="w")
        self._limit_integer_input(self.sponsor_power_entry, 0, 99)
        ttk.Label(sponsor_box, text="재산 계수:").grid(row=5, column=0, pady=(8, 0), sticky="w")
        self.sponsor_wealth_factor_entry = ttk.Spinbox(
            sponsor_box, from_=0, to=99, textvariable=self.sponsor_wealth_factor, width=5, state="disabled",
        )
        self.sponsor_wealth_factor_entry.grid(row=5, column=1, padx=(6, 0), pady=(8, 0), sticky="w")
        self._limit_integer_input(self.sponsor_wealth_factor_entry, 0, 99)
        ttk.Label(sponsor_box, text="계약금 평가:").grid(row=5, column=2, padx=(14, 0), pady=(8, 0), sticky="w")
        self.sponsor_appraisal_entry = ttk.Spinbox(
            sponsor_box, from_=0, to=99, textvariable=self.sponsor_appraisal, width=5, state="disabled",
        )
        self.sponsor_appraisal_entry.grid(row=5, column=3, padx=(6, 0), pady=(8, 0), sticky="w")
        self._limit_integer_input(self.sponsor_appraisal_entry, 0, 99)

        sponsor_preference_box = ttk.LabelFrame(sponsor_tab, text="발견물 취향", padding=8)
        sponsor_preference_box.grid(row=1, column=1, padx=(10, 0), pady=(10, 0), sticky="nw")
        for index, (name, variable) in enumerate(zip(SPONSOR_PREFERENCE_NAMES, self.sponsor_preference_flags)):
            checkbutton = ttk.Checkbutton(sponsor_preference_box, text=name, variable=variable)
            checkbutton.grid(row=index // 4, column=index % 4, padx=(0, 8), pady=1, sticky="w")
            self._sponsor_controls.append(checkbutton)

        sponsor_language_box = ttk.LabelFrame(sponsor_tab, text="계약 조건 언어", padding=8)
        sponsor_language_box.grid(row=2, column=1, padx=(10, 0), pady=(10, 0), sticky="nw")
        for index, (name, variable) in enumerate(zip(BARMAID_LANGUAGE_NAMES, self.sponsor_language_flags)):
            checkbutton = ttk.Checkbutton(sponsor_language_box, text=name, variable=variable)
            checkbutton.grid(row=index // 3, column=index % 3, padx=(0, 8), pady=1, sticky="w")
            self._sponsor_controls.append(checkbutton)
        self._sponsor_controls.extend((
            self.sponsor_search_entry,
            self.sponsor_list,
            self.sponsor_face_code_entry,
            self.sponsor_gender_selector,
            self.sponsor_nation_selector,
            self.sponsor_job_selector,
            self.sponsor_appearance_year_entry,
            self.sponsor_city_selector,
            self.sponsor_building_selector,
            self.sponsor_power_entry,
            self.sponsor_wealth_factor_entry,
            self.sponsor_appraisal_entry,
        ))
        self._set_sponsor_controls_enabled(False)

    def show_mistranslation_details(self) -> None:
        self.show_patch_details("오역 수정 내역", MISTRANSLATION_DETAILS, "#1A73E8")

    def _bind_combobox_arrow_selection(self, combobox: ttk.Combobox) -> None:
        """Make arrow keys change the selection without opening the drop-down."""
        combobox.bind("<Up>", lambda _event: self._move_combobox_selection(combobox, -1))
        combobox.bind("<Down>", lambda _event: self._move_combobox_selection(combobox, 1))

    @staticmethod
    def _move_combobox_selection(combobox: ttk.Combobox, direction: int) -> str:
        values = combobox.cget("values")
        if combobox.instate(("disabled",)) or not values:
            return "break"
        current = combobox.current()
        if current < 0:
            next_index = 0 if direction > 0 else len(values) - 1
        else:
            next_index = max(0, min(len(values) - 1, current + direction))
        if next_index != current:
            combobox.current(next_index)
            combobox.event_generate("<<ComboboxSelected>>")
        return "break"

    def _set_barmaid_controls_enabled(self, enabled: bool) -> None:
        for control in self._barmaid_controls:
            if isinstance(control, NativeWinEdit):
                control.set_enabled(enabled)
            elif isinstance(control, ttk.Combobox):
                control.configure(state="readonly" if enabled else "disabled")
            else:
                control.state(["!disabled"] if enabled else ["disabled"])

    def _schedule_barmaid_list_refresh(self) -> None:
        """Match the Save Editor's short debounce while a Korean IME is composing."""
        previous_job = getattr(self, "_barmaid_search_job", None)
        if previous_job is not None:
            try:
                self.after_cancel(previous_job)
            except tk.TclError:
                pass
        self._barmaid_search_job = self.after(120, self._run_barmaid_list_refresh)

    def _run_barmaid_list_refresh(self) -> None:
        self._barmaid_search_job = None
        self._refresh_barmaid_list()

    def _refresh_barmaid_list(self, *_args: str) -> None:
        """Show the filtered barmaid list without discarding the detail being edited."""
        tree = getattr(self, "barmaid_list", None)
        if tree is None:
            return
        selected_identifier = self.barmaid_selection.get()
        query = self.barmaid_search_entry.get().strip().casefold()
        tree.delete(*tree.get_children())
        for record in self._barmaid_records:
            if query and query not in record.name.casefold():
                continue
            tree.insert("", tk.END, iid=str(record.identifier), values=(f"{record.identifier:03d}", record.name))
        if selected_identifier and tree.exists(selected_identifier):
            tree.selection_set(selected_identifier)
            tree.focus(selected_identifier)
            tree.see(selected_identifier)

    def _selected_barmaid_record(self) -> BarmaidRecord | None:
        try:
            identifier = int(self.barmaid_selection.get())
        except ValueError:
            return None
        return self._barmaid_by_identifier.get(identifier)

    def _select_barmaid(self, identifier: int) -> None:
        item_id = str(identifier)
        if not self.barmaid_list.exists(item_id):
            return
        self.barmaid_list.selection_set(item_id)
        self.barmaid_list.focus(item_id)
        self.barmaid_list.see(item_id)
        self._on_barmaid_selected()

    def _load_barmaid_records(
        self,
        records: tuple[BarmaidRecord, ...],
        child_aptitudes: tuple[BarmaidChildAptitudes, ...],
    ) -> None:
        if len(child_aptitudes) != 144:
            raise ValueError("여급 자녀 능력치 보정 테이블을 읽지 못했습니다.")
        self._barmaid_records = records
        self._barmaid_child_aptitudes = child_aptitudes
        self._barmaid_by_identifier = {record.identifier: record for record in records}
        self.barmaid_search_entry.set("")
        self._refresh_barmaid_list()
        self._set_barmaid_controls_enabled(bool(records))
        if not records:
            self.barmaid_selection.set("")
            self.barmaid_name_entry.set("")
            self.barmaid_face_code.set("")
            self.barmaid_appearance_year.set("")
            self.barmaid_city_name.set("")
            self._show_barmaid_face(None)
            self.barmaid_personality.set("")
            for variable in self.barmaid_language_flags:
                variable.set(False)
            for variable in self.barmaid_child_aptitudes:
                variable.set("0")
            return
        self.barmaid_selection.set(str(records[0].identifier))
        self._refresh_barmaid_list()
        self._select_barmaid(records[0].identifier)

    def _on_barmaid_selected(self, _event: tk.Event | None = None) -> None:
        selection = self.barmaid_list.selection()
        if not selection:
            return
        try:
            record = self._barmaid_by_identifier.get(int(selection[0]))
        except ValueError:
            return
        if record is None:
            return
        self.barmaid_selection.set(str(record.identifier))
        self.barmaid_name_entry.set(record.name)
        self.barmaid_face_code.set(str(record.face_code))
        self.barmaid_appearance_year.set(str(record.appearance_year))
        self.barmaid_city_selector.current(record.city_id)
        self._show_barmaid_face(record.face_code)
        self.barmaid_personality.set(BARMAID_PERSONALITY_NAMES[record.personality_id])
        for bit, variable in enumerate(self.barmaid_language_flags):
            variable.set(bool(record.language_flags & (1 << bit)))
        self._refresh_barmaid_child_aptitudes(record.face_code)

    def _on_barmaid_face_code_changed(self, *_args: str) -> None:
        """Switch the shared child-modifier row when the editable face code changes."""
        try:
            face_code = int(self.barmaid_face_code.get())
        except ValueError:
            self._show_barmaid_face(None)
            return
        self._refresh_barmaid_child_aptitudes(face_code)
        self._show_barmaid_face(face_code)

    def _portrait_max_code(self, *, female: bool) -> int:
        """Return the last valid portrait code from the selected game files."""
        return max(0, self._portrait_counts[female] - 1)

    def _configure_face_code_entry(self, entry: ttk.Spinbox, *, female: bool) -> None:
        maximum = self._portrait_max_code(female=female)
        entry.configure(
            from_=0,
            to=maximum,
            validatecommand=(self._integer_validation_command, "%P", "0", str(maximum)),
        )

    def _configure_portrait_code_ranges(self) -> None:
        """Synchronize all face-code editors with the archive part counts."""
        self._configure_face_code_entry(self.barmaid_face_code_entry, female=True)
        sponsor_gender = self.sponsor_gender_selector.current()
        self._configure_face_code_entry(
            self.sponsor_face_code_entry, female=sponsor_gender == 1,
        )
        try:
            person_gender = SPONSOR_GENDER_NAMES.index(self.person_gender.get())
        except ValueError:
            person_gender = 0
        self._configure_face_code_entry(
            self.person_face_entry, female=person_gender == 1,
        )

    def _show_barmaid_face(self, face_code: int | None) -> None:
        """Display the selected game's FEMALE.CDS portrait for this face code."""
        if face_code is None:
            self._barmaid_face_photo = None
            self.barmaid_image_preview.configure(image="", text="이미지 없음")
            return
        try:
            source = self._read_game_portrait(face_code, female=True)
            self._barmaid_face_photo = ImageTk.PhotoImage(source.convert("RGBA"))
        except (PortraitReadError, tk.TclError):
            self._barmaid_face_photo = None
            self.barmaid_image_preview.configure(image="", text="이미지 없음")
            return
        self.barmaid_image_preview.configure(image=self._barmaid_face_photo, text="")

    def _on_sponsor_face_code_changed(self, *_args: str) -> None:
        """Refresh the sponsor portrait after face code or gender changes."""
        gender = self.sponsor_gender_selector.current()
        if gender in (0, 1):
            self._configure_face_code_entry(self.sponsor_face_code_entry, female=gender == 1)
        try:
            face_code = int(self.sponsor_face_code.get())
        except ValueError:
            self._show_sponsor_face(None, None)
            return
        self._show_sponsor_face(face_code, gender)

    def _show_sponsor_face(self, face_code: int | None, gender: int | None) -> None:
        """Display the portrait from MALE.CDS or FEMALE.CDS for the sponsor."""
        if face_code is None or gender not in range(len(SPONSOR_GENDER_NAMES)):
            self._sponsor_face_photo = None
            self.sponsor_image_preview.configure(image="", text="이미지 없음")
            return
        try:
            source = self._read_game_portrait(face_code, female=gender == 1)
            self._sponsor_face_photo = ImageTk.PhotoImage(source.convert("RGBA"))
        except (PortraitReadError, tk.TclError):
            self._sponsor_face_photo = None
            self.sponsor_image_preview.configure(image="", text="이미지 없음")
            return
        self.sponsor_image_preview.configure(image=self._sponsor_face_photo, text="")

    def _set_person_controls_enabled(self, enabled: bool) -> None:
        for control in self._person_controls:
            if isinstance(control, NativeWinEdit): control.set_enabled(enabled)
            elif isinstance(control, ttk.Treeview): control.configure(selectmode="browse" if enabled else "none")
            elif isinstance(control, ttk.Combobox): control.configure(state="readonly" if enabled else "disabled")
            else: control.state(["!disabled"] if enabled else ["disabled"])

    def _schedule_person_list_refresh(self) -> None:
        job = getattr(self, "_person_search_job", None)
        if job: self.after_cancel(job)
        self._person_search_job = self.after(120, self._refresh_person_list)

    def _refresh_person_list(self) -> None:
        tree = self.person_list; selected = tree.selection()
        for item in tree.get_children(): tree.delete(item)
        query = self.person_search_entry.get().strip().casefold()
        for record in self._person_records:
            if not query or query in record.name.casefold(): tree.insert("", "end", iid=str(record.identifier), values=(f"{record.identifier:03d}", record.name))
        if selected and tree.exists(selected[0]): tree.selection_set(selected[0])

    def _selected_person_record(self) -> PersonRecord | None:
        selection = self.person_list.selection()
        return self._person_by_identifier.get(int(selection[0])) if selection else None

    def _load_person_records(self, records: tuple[PersonRecord, ...]) -> None:
        self._person_records = records; self._person_by_identifier = {record.identifier: record for record in records}
        self.person_search_entry.set(""); self._refresh_person_list(); self._set_person_controls_enabled(bool(records))
        if records:
            self.person_list.selection_set(str(records[0].identifier)); self._on_person_selected()

    def _on_person_selected(self, _event: tk.Event | None = None) -> None:
        record = self._selected_person_record()
        if not record: return
        self.person_name.set(record.name); self.person_gender.set(SPONSOR_GENDER_NAMES[record.gender]); self.person_face_code.set(str(record.face_code)); self.person_age.set(str(record.age_at_1480)); self.person_nation.set(SPONSOR_NATION_NAMES[record.nation_id]); self.person_job.set(PERSON_JOB_NAMES[record.job_id]); self.person_fame.set(str(record.fame)); self.person_infamy.set(str(record.infamy)); self.person_employment_state.set(PERSON_EMPLOYMENT_STATE_NAMES[record.employment_state]); self.person_city.set("도시 없음" if record.city_id < 0 else BARMAID_CITY_NAMES[record.city_id]); self.person_building.set(SPONSOR_BUILDING_NAMES[record.building_id]); self.person_blood.set(PERSON_BLOOD_NAMES[record.blood_id]); self.person_hire_cost.set(str(record.hire_cost))
        for variable, value in zip(self.person_abilities, record.abilities): variable.set(str(value))
        self.person_vitality.set(str(record.vitality))
        for variable, value in zip(self.person_skill_levels, record.skills): variable.set(str(value))
        self._show_person_face(record.face_code, record.gender)

    def _on_person_face_code_changed(self, *_args: str) -> None:
        try:
            gender = SPONSOR_GENDER_NAMES.index(self.person_gender.get())
            self._configure_face_code_entry(self.person_face_entry, female=gender == 1)
            self._show_person_face(int(self.person_face_code.get()), gender)
        except ValueError: self._show_person_face(None, None)

    def _show_person_face(self, face_code: int | None, gender: int | None) -> None:
        if face_code is None or gender not in (0, 1): self._person_face_photo = None; self.person_image_preview.configure(image="", text="이미지 없음"); return
        try:
            source = self._read_game_portrait(face_code, female=gender == 1); self._person_face_photo = ImageTk.PhotoImage(source.convert("RGBA"))
            self.person_image_preview.configure(image=self._person_face_photo, text="")
        except (PortraitReadError, tk.TclError): self._person_face_photo = None; self.person_image_preview.configure(image="", text="이미지 없음")

    def _read_game_portrait(self, face_code: int, *, female: bool) -> Image.Image:
        if not self.path.get():
            raise PortraitReadError("대상 실행 파일을 먼저 선택해 주세요.")
        return read_portrait(
            self.path.get(), female=female, face_code=face_code,
            palette_path=bundled_resource_path("Resources", "face_palette.png"),
        )

    def _current_person_edit(self) -> PersonEdit | None:
        record = self._selected_person_record()
        if record is None: return None
        try:
            face_code = int(self.person_face_code.get())
            gender = SPONSOR_GENDER_NAMES.index(self.person_gender.get())
            city = -1 if self.person_city.get() == "도시 없음" else BARMAID_CITY_NAMES.index(self.person_city.get())
            abilities = tuple(int(variable.get()) for variable in self.person_abilities)
            skills = tuple(int(variable.get()) for variable in self.person_skill_levels)
            if not 0 <= face_code <= self._portrait_max_code(female=gender == 1):
                raise ValueError("인물 얼굴 코드가 선택한 성별의 이미지 범위를 벗어났습니다.")
            return PersonEdit(record.identifier, face_code, gender, int(self.person_age.get()), SPONSOR_NATION_NAMES.index(self.person_nation.get()), PERSON_JOB_NAMES.index(self.person_job.get()), int(self.person_fame.get()), int(self.person_infamy.get()), PERSON_EMPLOYMENT_STATE_NAMES.index(self.person_employment_state.get()), city, SPONSOR_BUILDING_NAMES.index(self.person_building.get()), PERSON_BLOOD_NAMES.index(self.person_blood.get()), int(self.person_vitality.get()), int(self.person_hire_cost.get()), abilities, skills)
        except (ValueError, IndexError) as error: raise ValueError("인물 입력값을 확인해 주세요.") from error

    def _set_ship_type_controls_enabled(self, enabled: bool) -> None:
        for control in self._ship_type_controls:
            if isinstance(control, NativeWinEdit): control.set_enabled(enabled)
            elif isinstance(control, ttk.Treeview): control.configure(selectmode="browse" if enabled else "none")
            else: control.state(["!disabled"] if enabled else ["disabled"])

    def _selected_ship_type_record(self) -> ShipTypeRecord | None:
        selection = self.ship_type_list.selection()
        return self._ship_type_by_identifier.get(int(selection[0])) if selection else None

    def _load_ship_type_records(self, records: tuple[ShipTypeRecord, ...]) -> None:
        self._ship_type_records = records
        self._ship_type_by_identifier = {record.identifier: record for record in records}
        self.ship_type_list.delete(*self.ship_type_list.get_children())
        for record in records:
            self.ship_type_list.insert("", "end", iid=str(record.identifier), values=(f"{record.identifier:02d}", record.name))
        self._set_ship_type_controls_enabled(bool(records))
        if records:
            self.ship_type_list.selection_set(str(records[0].identifier))
            self.ship_type_list.focus(str(records[0].identifier))
            self._on_ship_type_selected()

    def _on_ship_type_selected(self, _event: tk.Event | None = None) -> None:
        record = self._selected_ship_type_record()
        if record is None: return
        self.ship_name.set(record.name)
        self.ship_name_entry.set(record.name)
        for variable, value in zip(
            (self.ship_shipyard_requirement, self.ship_base_power, self.ship_power_limit,
             self.ship_base_durability, self.ship_durability_limit, self.ship_base_weight,
             self.ship_weight_limit, self.ship_base_capacity, self.ship_capacity_limit,
             self.ship_base_cannons, self.ship_cannon_limit, self.ship_min_crew),
            (record.shipyard_requirement, record.base_power, record.power_limit,
             record.base_durability, record.durability_limit, record.base_weight,
             record.weight_limit, record.base_capacity, record.capacity_limit,
             record.base_cannons, record.cannon_limit, record.min_crew),
        ):
            variable.set(str(value))
        self._show_ship_preview(record.identifier)

    def _show_ship_preview(self, ship_type_id: int) -> None:
        """Loop the selected game's original ship movie for the ship type."""
        video_path = self._game_ship_avi_path(ship_type_id)
        if (
            video_path is not None
            and self._ship_avi_preview is not None
            and self._ship_avi_preview.show(video_path)
        ):
            return
        if self._ship_avi_preview is not None:
            self._ship_avi_preview.stop()
        self.ship_image_preview.configure(image="", text="이미지 없음")

    def _game_ship_avi_path(self, ship_type_id: int) -> Path | None:
        """Locate `AVI/Sxx_0001.AVI` using the static EXE ship-type ID."""
        if not self.path.get() or not 0 <= ship_type_id < 8:
            return None
        avi_path = Path(self.path.get()).resolve().parent / "AVI" / f"S{ship_type_id:02d}_0001.AVI"
        return avi_path if avi_path.is_file() else None

    def _current_ship_type_edit(self) -> ShipTypeEdit | None:
        record = self._selected_ship_type_record()
        if record is None: return None
        try:
            return ShipTypeEdit(
                record.identifier, self.ship_name_entry.get().strip(), int(self.ship_shipyard_requirement.get()),
                int(self.ship_base_power.get()), int(self.ship_power_limit.get()),
                int(self.ship_base_durability.get()), int(self.ship_durability_limit.get()),
                int(self.ship_base_weight.get()), int(self.ship_weight_limit.get()),
                int(self.ship_base_capacity.get()), int(self.ship_capacity_limit.get()),
                int(self.ship_base_cannons.get()), int(self.ship_cannon_limit.get()),
                int(self.ship_min_crew.get()),
            )
        except ValueError as error:
            raise ValueError("선종 이름 또는 숫자 입력값을 확인해 주세요.") from error

    def _remember_ship_type_edit(self, edit: ShipTypeEdit | None) -> None:
        if edit is None:
            return
        self._ship_type_records = tuple(
            ShipTypeRecord(
                record.identifier,
                edit.name if record.identifier == edit.identifier else record.name,
                edit.shipyard_requirement if record.identifier == edit.identifier else record.shipyard_requirement,
                edit.base_power if record.identifier == edit.identifier else record.base_power,
                edit.power_limit if record.identifier == edit.identifier else record.power_limit,
                edit.base_durability if record.identifier == edit.identifier else record.base_durability,
                edit.durability_limit if record.identifier == edit.identifier else record.durability_limit,
                edit.base_weight if record.identifier == edit.identifier else record.base_weight,
                edit.weight_limit if record.identifier == edit.identifier else record.weight_limit,
                edit.base_capacity if record.identifier == edit.identifier else record.base_capacity,
                edit.capacity_limit if record.identifier == edit.identifier else record.capacity_limit,
                edit.base_cannons if record.identifier == edit.identifier else record.base_cannons,
                edit.cannon_limit if record.identifier == edit.identifier else record.cannon_limit,
                edit.min_crew if record.identifier == edit.identifier else record.min_crew,
            )
            for record in self._ship_type_records
        )
        self._ship_type_by_identifier = {record.identifier: record for record in self._ship_type_records}
        self.ship_type_list.item(str(edit.identifier), values=(f"{edit.identifier:02d}", edit.name))

    def _set_city_controls_enabled(self, enabled: bool) -> None:
        for control in self._city_controls:
            if isinstance(control, NativeWinEdit):
                control.set_enabled(enabled)
            elif isinstance(control, ttk.Treeview):
                control.configure(selectmode="browse" if enabled else "none")
            elif isinstance(control, ttk.Combobox):
                control.configure(state="readonly" if enabled else "disabled")
            else:
                control.state(["!disabled"] if enabled else ["disabled"])

    def _schedule_city_list_refresh(self) -> None:
        job = getattr(self, "_city_search_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
        self._city_search_job = self.after(120, self._refresh_city_list)

    def _refresh_city_list(self) -> None:
        tree = self.city_list
        selected = tree.selection()
        selected_identifier = selected[0] if selected else ""
        tree.delete(*tree.get_children())
        query = self.city_search_entry.get().strip().casefold()
        for record in self._city_records:
            if query and query not in record.name.casefold():
                continue
            tree.insert("", "end", iid=str(record.identifier), values=(f"{record.identifier:03d}", record.name))
        if selected_identifier and tree.exists(selected_identifier):
            tree.selection_set(selected_identifier)
            tree.focus(selected_identifier)
            tree.see(selected_identifier)

    def _selected_city_record(self) -> CityRecord | None:
        selection = self.city_list.selection()
        return self._city_by_identifier.get(int(selection[0])) if selection else None

    def _city_connection_values(self) -> tuple[str, ...]:
        return ("연결 없음", *(record.name for record in self._city_records))

    def _configure_city_dependent_values(self) -> None:
        connection_values = self._city_connection_values()
        for selector in self.city_connection_selectors:
            selector.configure(values=connection_values)
        self.city_specialty_selector.configure(values=("특산품 없음", *self._trade_good_names))
        self._trade_good_by_name = {
            name: identifier for identifier, name in enumerate(self._trade_good_names)
        }
        for selector in self.city_trade_region_good_selectors:
            selector.configure(values=("없음", *self._trade_good_names))
        self._city_market_good_by_name: dict[str, int] = {}
        market_values = ["없음"]
        for item in self._item_records:
            label = item.name
            if label in self._city_market_good_by_name:
                label = f"{item.name} ({item.identifier})"
            self._city_market_good_by_name[label] = item.identifier
            market_values.append(label)
        for selector in self.city_market_good_selectors:
            selector.configure(values=market_values)
        for identifier, button in enumerate(self.city_ship_candidate_buttons):
            name = self._ship_type_by_identifier.get(identifier)
            button.configure(text=name.name if name is not None else f"선종 {identifier}")

    def _refresh_city_common_goods(self) -> None:
        try:
            region_id = int(self.city_trade_region.get())
            goods = self._trade_region_goods[region_id]
        except (IndexError, ValueError):
            for variable in self.city_common_goods:
                variable.set("없음")
            return
        for variable, good_id in zip(self.city_common_goods, goods):
            variable.set(
                self._trade_good_names[good_id]
                if 0 <= good_id < len(self._trade_good_names)
                else "없음"
            )

    def _show_trade_region_editor(self, _event: tk.Event | None = None) -> None:
        """Load one global trade-region row into its five editable selectors."""
        try:
            region_id = int(self.city_trade_region_editor.get())
            goods = self._trade_region_goods[region_id]
        except (IndexError, ValueError):
            goods = (-1,) * len(self.city_trade_region_goods)
        for slot, (variable, good_id) in enumerate(zip(self.city_trade_region_goods, goods)):
            variable.set(
                self._trade_good_names[good_id]
                if 0 <= good_id < len(self._trade_good_names)
                else "없음"
            )
            self._show_trade_region_good_image(slot, good_id)

    def _show_trade_region_good_image(self, slot: int, good_id: int) -> None:
        """Show the selected trade good's original 120×120 ITEM.CDS image."""
        try:
            label = self.city_trade_region_good_image_labels[slot]
        except (AttributeError, IndexError):
            return
        if good_id < 0 or not self.path.get():
            self._trade_region_good_photos[slot] = None
            label.configure(image="", text="이미지 없음")
            return
        try:
            image = self._read_game_item_image(good_id + TRADE_GOOD_IMAGE_SLOT_OFFSET)
            photo = ImageTk.PhotoImage(image.convert("RGBA"))
        except (ItemImageReadError, tk.TclError):
            self._trade_region_good_photos[slot] = None
            label.configure(image="", text="이미지 없음")
            return
        self._trade_region_good_photos[slot] = photo
        label.configure(image=photo, text="")

    def _on_trade_region_good_selected(self, slot: int) -> None:
        """Keep edits for every trade region in memory until the EXE is saved."""
        try:
            region_id = int(self.city_trade_region_editor.get())
            value = self.city_trade_region_goods[slot].get()
            good_id = -1 if value == "없음" else self._trade_good_by_name[value]
            regions = [list(goods) for goods in self._trade_region_goods]
            regions[region_id][slot] = good_id
        except (IndexError, KeyError, ValueError):
            return
        self._trade_region_goods = tuple(tuple(goods) for goods in regions)
        self._show_trade_region_good_image(slot, good_id)
        self._refresh_city_common_goods()

    def _current_trade_region_goods(self) -> tuple[tuple[int, ...], ...]:
        """Return the complete edited table after validating all 27 rows."""
        if (
            len(self._trade_region_goods) != 27
            or any(len(goods) != 5 for goods in self._trade_region_goods)
            or any(not -1 <= good_id < len(self._trade_good_names)
                   for goods in self._trade_region_goods for good_id in goods)
        ):
            raise ValueError("교역권 품목 입력값을 확인해 주세요.")
        return self._trade_region_goods

    def _load_city_records(
        self, records: tuple[CityRecord, ...], trade_good_names: tuple[str, ...],
        trade_region_goods: tuple[tuple[int, ...], ...],
    ) -> None:
        self._city_records = records
        self._city_by_identifier = {record.identifier: record for record in records}
        self._trade_good_names = trade_good_names
        self._trade_region_goods = trade_region_goods
        self._configure_city_dependent_values()
        self.city_search_entry.set("")
        self._refresh_city_list()
        self._set_city_controls_enabled(bool(records))
        if records:
            first_identifier = str(records[0].identifier)
            self.city_list.selection_set(first_identifier)
            self.city_list.focus(first_identifier)
            self._on_city_selected()

    def _on_city_selected(self, _event: tk.Event | None = None) -> None:
        record = self._selected_city_record()
        if record is None:
            return
        self.city_name.set(record.name)
        self.city_name_entry.set(record.name)
        for variable, connection_id in zip(self.city_inland_connections, record.inland_connection_ids):
            connection = self._city_by_identifier.get(connection_id)
            variable.set(connection.name if connection is not None else "연결 없음")
        self.city_nation.set(CITY_NATION_NAMES[record.nation_id])
        self.city_culture.set(CITY_CULTURE_NAMES[record.culture_id])
        self.city_status.set(CITY_STATUS_NAMES[record.city_status])
        self.city_update_counter.set(str(record.update_counter))
        self.city_discovered.set(bool(record.default_flags & 0x0001))
        for identifier, variable in enumerate(self.city_ship_candidates):
            variable.set(bool(record.ship_candidate_mask & (1 << identifier)))
        self.city_shipyard_level.set(CITY_SHIPYARD_LEVEL_NAMES[record.shipyard_level])
        for identifier, variable in enumerate(self.city_facilities):
            variable.set(bool(record.facility_flags & (1 << identifier)))
        self.city_trade_region.set(str(record.trade_region_id))
        self.city_trade_region_editor.set(str(record.trade_region_id))
        self._show_trade_region_editor()
        self.city_specialty.set(
            "특산품 없음" if record.specialty_id < 0 else self._trade_good_names[record.specialty_id]
        )
        self.city_specialty_price.set(str(record.specialty_price))
        self.city_specialty_supply_index.set(str(record.specialty_supply_index))
        market_names_by_identifier = {identifier: name for name, identifier in self._city_market_good_by_name.items()}
        for variable, good_id in zip(self.city_market_goods, record.default_market_goods):
            variable.set("없음" if good_id < 0 else market_names_by_identifier.get(good_id, "없음"))
        self._refresh_city_common_goods()
        self._show_city_image(record.identifier)

    def _show_city_image(self, city_id: int) -> None:
        try:
            image = read_city_image(self.path.get(), city_id=city_id)
            self._city_image_photo = ImageTk.PhotoImage(image.convert("RGBA"))
            self.city_image_preview.configure(image=self._city_image_photo, text="")
        except (CityImageReadError, tk.TclError):
            self._city_image_photo = None
            self.city_image_preview.configure(image="", text="이미지 없음")

    def _current_city_edit(self) -> CityEdit | None:
        record = self._selected_city_record()
        if record is None:
            return None
        try:
            name_to_identifier = {city.name: city.identifier for city in self._city_records}
            connections = tuple(
                -1 if value.get() == "연결 없음" else name_to_identifier[value.get()]
                for value in self.city_inland_connections
            )
            ship_mask = sum(
                1 << identifier for identifier, value in enumerate(self.city_ship_candidates) if value.get()
            )
            specialty = (
                -1 if self.city_specialty.get() == "특산품 없음"
                else self._trade_good_names.index(self.city_specialty.get())
            )
            market_goods = tuple(
                -1 if variable.get() == "없음" else self._city_market_good_by_name[variable.get()]
                for variable in self.city_market_goods
            )
            default_flags = (record.default_flags & ~0x0001) | int(self.city_discovered.get())
            facility_flags = sum(
                1 << identifier for identifier, value in enumerate(self.city_facilities) if value.get()
            )
            return CityEdit(
                record.identifier, self.city_name_entry.get().strip(),
                record.world_x, record.world_y,
                connections, ship_mask, int(self.city_trade_region.get()),
                CITY_CULTURE_NAMES.index(self.city_culture.get()),
                CITY_NATION_NAMES.index(self.city_nation.get()),
                CITY_SHIPYARD_LEVEL_NAMES.index(self.city_shipyard_level.get()),
                int(self.city_update_counter.get()), specialty,
                int(self.city_specialty_price.get()), int(self.city_specialty_supply_index.get()),
                market_goods, CITY_STATUS_NAMES.index(self.city_status.get()),
                facility_flags, default_flags,
            )
        except (ValueError, IndexError, KeyError) as error:
            raise ValueError("도시 입력값을 확인해 주세요.") from error

    def _remember_city_edit(self, edit: CityEdit | None) -> None:
        if edit is None:
            return
        self._city_records = tuple(
            CityRecord(
                record.identifier,
                edit.name if record.identifier == edit.identifier else record.name,
                edit.world_x if record.identifier == edit.identifier else record.world_x,
                edit.world_y if record.identifier == edit.identifier else record.world_y,
                edit.inland_connection_ids if record.identifier == edit.identifier else record.inland_connection_ids,
                edit.ship_candidate_mask if record.identifier == edit.identifier else record.ship_candidate_mask,
                edit.trade_region_id if record.identifier == edit.identifier else record.trade_region_id,
                edit.culture_id if record.identifier == edit.identifier else record.culture_id,
                edit.nation_id if record.identifier == edit.identifier else record.nation_id,
                edit.shipyard_level if record.identifier == edit.identifier else record.shipyard_level,
                edit.update_counter if record.identifier == edit.identifier else record.update_counter,
                edit.specialty_id if record.identifier == edit.identifier else record.specialty_id,
                edit.specialty_price if record.identifier == edit.identifier else record.specialty_price,
                edit.specialty_supply_index if record.identifier == edit.identifier else record.specialty_supply_index,
                edit.default_market_goods if record.identifier == edit.identifier else record.default_market_goods,
                edit.city_status if record.identifier == edit.identifier else record.city_status,
                edit.facility_flags if record.identifier == edit.identifier else record.facility_flags,
                edit.default_flags if record.identifier == edit.identifier else record.default_flags,
            )
            for record in self._city_records
        )
        self._city_by_identifier = {record.identifier: record for record in self._city_records}
        self._configure_city_dependent_values()
        self._refresh_city_list()

    def _set_item_controls_enabled(self, enabled: bool) -> None:
        for control in self._item_controls:
            if isinstance(control, NativeWinEdit):
                control.set_enabled(enabled)
            elif isinstance(control, ttk.Treeview):
                control.configure(selectmode="browse" if enabled else "none")
            elif isinstance(control, ttk.Combobox):
                control.configure(state="readonly" if enabled else "disabled")
            else:
                control.state(["!disabled"] if enabled else ["disabled"])

    def _figurehead_effect_name(self, code: int) -> str:
        names: list[str] = []
        if code <= 33:
            names.append(FIGUREHEAD_DISASTER_NAMES[code % 4])
        unique_names = {
            26: "받는 함포 피해 감소",
            27: "받는 인접 사격 피해 감소",
            28: "받는 백병 피해 감소",
            29: "함포 공격 강화",
            30: "인접 사격 공격 강화",
            31: "백병 공격 강화",
            32: "턴당 내구 회복",
            33: "해전 이동력 증가",
            34: "특수 함포 공격 강화",
            35: "전 공격 강화",
        }
        if code in unique_names:
            names.append(unique_names[code])
        return " / ".join(names)

    def _figurehead_unique_effect_fields(
        self, code: int,
    ) -> tuple[tuple[str, tk.StringVar, int, int, str], ...]:
        effects = {
            26: (("받는 함포 피해 감소:", self.figurehead_cannon_damage_reduction, 0, 100, "%"),),
            27: (("받는 인접 사격 피해 감소:", self.figurehead_shooting_damage_reduction, 0, 100, "%"),),
            28: (("받는 백병 피해 감소:", self.figurehead_melee_damage_reduction, 0, 100, "%"),),
            29: (("함포 공격 배율:", self.figurehead_cannon_attack_percent, 0, 1000, "%"),),
            30: (("인접 사격 공격 배율:", self.figurehead_shooting_attack_percent, 0, 1000, "%"),),
            31: (("백병 공격 배율:", self.figurehead_melee_attack_percent, 0, 1000, "%"),),
            32: (("턴당 내구 회복:", self.figurehead_hull_recovery, 0, 9999, ""),),
            33: (
                ("해전 이동 보너스:", self.figurehead_movement_bonus, 0, 127, ""),
                ("해전 이동 최대값:", self.figurehead_movement_maximum, 1, 127, ""),
            ),
            34: (("함포 공격 배율:", self.figurehead_special_cannon_attack_percent, 0, 1000, "%"),),
            35: (("전 공격 배율:", self.figurehead_all_attack_percent, 0, 1000, "%"),),
        }
        return effects.get(code, ())

    def _figurehead_effect_summary(self, code: int) -> str:
        parts: list[str] = []
        if code <= 33:
            grade = FIGUREHEAD_DISASTER_GRADES[code]
            chance = self.figurehead_disaster_chances[grade - 1].get() or "?"
            parts.append(f"{grade}등급 {chance}%")
        fields = self._figurehead_unique_effect_fields(code)
        if fields:
            if code in (26, 27, 28):
                parts.append(f"{fields[0][1].get() or '?'}% 감소")
            elif code in (29, 30, 31, 34, 35):
                parts.append(f"{fields[0][1].get() or '?'}%")
            elif code == 32:
                parts.append(f"+{fields[0][1].get() or '?'}")
            else:
                parts.append(
                    f"+{fields[0][1].get() or '?'} / 최대 {fields[1][1].get() or '?'}"
                )
        return " / ".join(parts)

    def _refresh_figurehead_effect_list(self) -> None:
        if not hasattr(self, "figurehead_list"):
            return
        selected = self.figurehead_list.selection()
        selected_code = selected[0] if selected else ""
        for code in range(36):
            item_id = str(code)
            values = (
                f"{code:02d}",
                self._figurehead_effect_name(code),
                self._figurehead_effect_summary(code),
            )
            if self.figurehead_list.exists(item_id):
                self.figurehead_list.item(item_id, values=values)
            else:
                self.figurehead_list.insert("", "end", iid=item_id, values=values)
        if selected_code and self.figurehead_list.exists(selected_code):
            self.figurehead_list.selection_set(selected_code)
            self.figurehead_list.focus(selected_code)

    def _configure_figurehead_value_entry(
        self,
        entry: ttk.Spinbox,
        variable: tk.StringVar,
        minimum: int,
        maximum: int,
    ) -> None:
        entry.configure(
            from_=minimum,
            to=maximum,
            textvariable=variable,
            validate="key",
            validatecommand=(
                self._integer_validation_command, "%P", str(minimum), str(maximum),
            ),
        )

    def _on_figurehead_effect_selected(self, _event: tk.Event | None = None) -> None:
        selection = self.figurehead_list.selection()
        if not selection:
            return
        code = int(selection[0])
        self.figurehead_selected_effect.set(self._figurehead_effect_name(code))
        self.figurehead_selected_code.set(str(code))
        editable = self._figurehead_loaded

        if code <= 33:
            grade = FIGUREHEAD_DISASTER_GRADES[code]
            self.figurehead_disaster_description.set(
                f"{FIGUREHEAD_DISASTER_NAMES[code % 4]} · {grade}등급 (동일 등급 공통)"
            )
            self._configure_figurehead_value_entry(
                self.figurehead_disaster_entry,
                self.figurehead_disaster_chances[grade - 1], 0, 100,
            )
            self.figurehead_disaster_entry.state(["!disabled"] if editable else ["disabled"])
        else:
            self.figurehead_disaster_description.set("해상 재해 방지 효과 없음")
            self.figurehead_disaster_entry.state(["disabled"])

        fields = self._figurehead_unique_effect_fields(code)
        widgets = (
            (
                self.figurehead_primary_label_widget,
                self.figurehead_primary_entry,
                self.figurehead_primary_unit_widget,
                self.figurehead_primary_label,
                self.figurehead_primary_unit,
            ),
            (
                self.figurehead_secondary_label_widget,
                self.figurehead_secondary_entry,
                self.figurehead_secondary_unit_widget,
                self.figurehead_secondary_label,
                self.figurehead_secondary_unit,
            ),
        )
        for index, widget_group in enumerate(widgets):
            label_widget, entry, unit_widget, label_variable, unit_variable = widget_group
            if index < len(fields):
                label, variable, minimum, maximum, unit = fields[index]
                label_variable.set(label)
                unit_variable.set(unit)
                self._configure_figurehead_value_entry(entry, variable, minimum, maximum)
                label_widget.grid()
                entry.grid()
                unit_widget.grid()
                entry.state(["!disabled"] if editable else ["disabled"])
            else:
                label_widget.grid_remove()
                entry.grid_remove()
                unit_widget.grid_remove()

    def _on_figurehead_effect_value_changed(self, *_args) -> None:
        self._refresh_figurehead_effect_list()

    def _set_figurehead_controls_enabled(self, enabled: bool) -> None:
        self._figurehead_loaded = enabled
        self.figurehead_list.configure(selectmode="browse" if enabled else "none")
        if enabled and not self.figurehead_list.selection() and self.figurehead_list.exists("0"):
            self.figurehead_list.selection_set("0")
            self.figurehead_list.focus("0")
            self.figurehead_list.see("0")
        self._on_figurehead_effect_selected()

    def _load_figurehead_effect_settings(self, settings: FigureheadEffectSettings) -> None:
        for variable, value in zip(
            self.figurehead_disaster_chances,
            (
                settings.disaster_grade1_chance,
                settings.disaster_grade2_chance,
                settings.disaster_grade3_chance,
            ),
        ):
            variable.set(str(value))
        for variable, value in (
            (self.figurehead_cannon_damage_reduction, settings.cannon_damage_reduction),
            (self.figurehead_shooting_damage_reduction, settings.shooting_damage_reduction),
            (self.figurehead_melee_damage_reduction, settings.melee_damage_reduction),
            (self.figurehead_cannon_attack_percent, settings.cannon_attack_percent),
            (self.figurehead_shooting_attack_percent, settings.shooting_attack_percent),
            (self.figurehead_melee_attack_percent, settings.melee_attack_percent),
            (self.figurehead_special_cannon_attack_percent, settings.special_cannon_attack_percent),
            (self.figurehead_all_attack_percent, settings.all_attack_percent),
            (self.figurehead_hull_recovery, settings.hull_recovery),
            (self.figurehead_movement_bonus, settings.movement_bonus),
            (self.figurehead_movement_maximum, settings.movement_maximum),
        ):
            variable.set(str(value))
        self._set_figurehead_controls_enabled(True)

    def _current_figurehead_effect_settings(self) -> FigureheadEffectSettings:
        try:
            chances = tuple(int(variable.get()) for variable in self.figurehead_disaster_chances)
            return FigureheadEffectSettings(
                chances[0], chances[1], chances[2],
                int(self.figurehead_cannon_damage_reduction.get()),
                int(self.figurehead_shooting_damage_reduction.get()),
                int(self.figurehead_melee_damage_reduction.get()),
                int(self.figurehead_cannon_attack_percent.get()),
                int(self.figurehead_shooting_attack_percent.get()),
                int(self.figurehead_melee_attack_percent.get()),
                int(self.figurehead_hull_recovery.get()),
                int(self.figurehead_movement_bonus.get()),
                int(self.figurehead_movement_maximum.get()),
                int(self.figurehead_special_cannon_attack_percent.get()),
                int(self.figurehead_all_attack_percent.get()),
            )
        except (ValueError, IndexError) as error:
            raise ValueError("선수상 효과 입력값을 확인해 주세요.") from error

    def _schedule_item_list_refresh(self) -> None:
        job = getattr(self, "_item_search_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
        self._item_search_job = self.after(120, self._refresh_item_list)

    def _refresh_item_list(self) -> None:
        tree = self.item_list
        selected = tree.selection()
        selected_identifier = selected[0] if selected else ""
        tree.delete(*tree.get_children())
        query = self.item_search_entry.get().strip().casefold()
        for record in self._item_records:
            category = item_category_name(record.category_id)
            if query and query not in record.name.casefold() and query not in category.casefold():
                continue
            tree.insert(
                "", "end", iid=str(record.identifier),
                values=(f"{record.identifier:03d}", category, record.name),
            )
        if selected_identifier and tree.exists(selected_identifier):
            tree.selection_set(selected_identifier)
            tree.focus(selected_identifier)
            tree.see(selected_identifier)

    def _selected_item_record(self) -> ItemRecord | None:
        selection = self.item_list.selection()
        return self._item_by_identifier.get(int(selection[0])) if selection else None

    def _load_item_records(self, records: tuple[ItemRecord, ...]) -> None:
        self._item_records = records
        self._item_by_identifier = {record.identifier: record for record in records}
        self.item_search_entry.set("")
        self._refresh_item_list()
        self._set_item_controls_enabled(bool(records))
        if records:
            first_identifier = str(records[0].identifier)
            self.item_list.selection_set(first_identifier)
            self.item_list.focus(first_identifier)
            self._on_item_selected()

    def _on_item_selected(self, _event: tk.Event | None = None) -> None:
        record = self._selected_item_record()
        if record is None:
            return
        self.item_name.set(record.name)
        self.item_name_entry.set(record.name)
        self.item_category.set(item_category_name(record.category_id))
        self._show_item_image(record.image_id)
        self.item_buy_price.set(str(record.buy_price))
        self.item_sell_price.set(str(record.sell_price))
        self.item_effect_value.set(str(record.effect_value))

    def _show_item_image(self, image_slot: int | None) -> None:
        """Display the original 120×120 ITEM.CDS image assigned by the EXE."""
        if image_slot is None:
            self._item_image_photo = None
            self.item_image_preview.configure(image="", text="이미지 없음")
            return
        try:
            image = self._read_game_item_image(image_slot)
            self._item_image_photo = ImageTk.PhotoImage(image.convert("RGBA"))
            self.item_image_preview.configure(image=self._item_image_photo, text="")
        except (ItemImageReadError, tk.TclError):
            self._item_image_photo = None
            self.item_image_preview.configure(image="", text="이미지 없음")

    def _read_game_item_image(self, image_slot: int) -> Image.Image:
        if not self.path.get():
            raise ItemImageReadError("대상 실행 파일을 먼저 선택해 주세요.")
        return read_item_image(
            self.path.get(), image_slot=image_slot,
            palette_path=bundled_resource_path("Resources", "face_palette.png"),
        )

    def _current_item_edit(self) -> ItemEdit | None:
        record = self._selected_item_record()
        if record is None:
            return None
        try:
            return ItemEdit(
                record.identifier, self.item_name_entry.get().strip(),
                int(self.item_buy_price.get()), int(self.item_sell_price.get()),
                int(self.item_effect_value.get()), item_category_raw_id(self.item_category.get()),
            )
        except (ValueError, IndexError) as error:
            raise ValueError("아이템 입력값을 확인해 주세요.") from error

    def _remember_item_edit(self, edit: ItemEdit | None) -> None:
        if edit is None:
            return
        self._item_records = tuple(
            ItemRecord(
                record.identifier,
                edit.name if record.identifier == edit.identifier else record.name,
                record.image_id,
                edit.buy_price if record.identifier == edit.identifier else record.buy_price,
                edit.sell_price if record.identifier == edit.identifier else record.sell_price,
                edit.effect_value if record.identifier == edit.identifier else record.effect_value,
                edit.category_id if record.identifier == edit.identifier else record.category_id,
            )
            for record in self._item_records
        )
        self._item_by_identifier = {record.identifier: record for record in self._item_records}
        self.item_list.item(
            str(edit.identifier),
            values=(f"{edit.identifier:03d}", item_category_name(edit.category_id), edit.name),
        )

    def _set_discovery_controls_enabled(self, enabled: bool) -> None:
        for control in self._discovery_controls:
            if isinstance(control, NativeWinEdit):
                control.set_enabled(enabled)
            elif isinstance(control, ttk.Treeview):
                control.configure(selectmode="browse" if enabled else "none")
            elif isinstance(control, ttk.Combobox):
                control.configure(state="readonly" if enabled else "disabled")
            else:
                control.state(["!disabled"] if enabled else ["disabled"])

    def _schedule_discovery_list_refresh(self) -> None:
        job = getattr(self, "_discovery_search_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
        self._discovery_search_job = self.after(120, self._refresh_discovery_list)

    def _refresh_discovery_list(self) -> None:
        tree = self.discovery_list
        selected = tree.selection()
        selected_identifier = selected[0] if selected else ""
        tree.delete(*tree.get_children())
        query = self.discovery_search_entry.get().strip().casefold()
        for record in self._discovery_records:
            category = DISCOVERY_CATEGORY_NAMES[record.category_id]
            if query and query not in record.name.casefold() and query not in category.casefold():
                continue
            tree.insert(
                "", "end", iid=str(record.identifier),
                values=(f"{record.identifier:03d}", category, record.name),
            )
        if selected_identifier and tree.exists(selected_identifier):
            tree.selection_set(selected_identifier)
            tree.focus(selected_identifier)
            tree.see(selected_identifier)

    def _selected_discovery_record(self) -> DiscoveryRecord | None:
        selection = self.discovery_list.selection()
        return self._discovery_by_identifier.get(int(selection[0])) if selection else None

    def _load_discovery_records(self, records: tuple[DiscoveryRecord, ...]) -> None:
        self._discovery_records = records
        self._discovery_by_identifier = {record.identifier: record for record in records}
        self.discovery_search_entry.set("")
        self._refresh_discovery_list()
        self._set_discovery_controls_enabled(bool(records))
        if records:
            first_identifier = str(records[0].identifier)
            self.discovery_list.selection_set(first_identifier)
            self.discovery_list.focus(first_identifier)
            self._on_discovery_selected()

    def _on_discovery_selected(self, _event: tk.Event | None = None) -> None:
        record = self._selected_discovery_record()
        if record is None:
            return
        self.discovery_name.set(record.name)
        self.discovery_name_entry.set(record.name)
        media_kind, media_value, media_maximum = self._discovery_media_info(record)
        self.discovery_still_slot.set("" if media_value is None else str(media_value))
        self.discovery_media_label.configure(
            text={"still": "DSTILL 번호:", "avi": "AVI 번호:", "animation": "DISCOVER 번호:"}.get(
                media_kind, "미디어 없음:"
            )
        )
        self.discovery_still_slot_entry.configure(to=max(media_maximum, 0))
        self._limit_integer_input(self.discovery_still_slot_entry, 0, max(media_maximum, 0))
        self.discovery_still_slot_entry.state(
            ["!disabled"] if media_value is not None else ["disabled"]
        )
        self.discovery_category.set(DISCOVERY_CATEGORY_NAMES[record.category_id])
        self.discovery_value.set(str(record.value))
        self._show_discovery_image(record)
        coordinate_values = (
            (world_y_to_latitude(record.min_y), world_y_to_latitude(record.max_y),
             world_x_to_longitude(record.min_x), world_x_to_longitude(record.max_x))
            if record.min_x is not None else (None, None, None, None)
        )
        for direction, variable, value, positive, negative in zip(
            (
                self.discovery_min_y_direction, self.discovery_max_y_direction,
                self.discovery_min_x_direction, self.discovery_max_x_direction,
            ),
            (self.discovery_min_y, self.discovery_max_y, self.discovery_min_x, self.discovery_max_x),
            coordinate_values,
            ("북위", "북위", "동경", "동경"),
            ("남위", "남위", "서경", "서경"),
        ):
            direction.set("" if value is None else (positive if value >= 0 else negative))
            variable.set("" if value is None else f"{abs(value):.3f}")
        coordinate_state = ["!disabled"] if record.min_x is not None else ["disabled"]
        for control in self._discovery_coordinate_controls:
            control.state(coordinate_state)

    def _discovery_media_info(self, record: DiscoveryRecord) -> tuple[str | None, int | None, int]:
        """Return the actively selected discovery media field and its valid maximum.

        AVI has playback priority when an exceptional record includes both a
        DSTILL fallback and AVI.  The three source fields remain independent.
        """
        if record.avi_id is not None:
            return "avi", record.avi_id, 69
        if record.animation_part is not None:
            return "animation", record.animation_part, 28
        if record.still_slot is not None:
            return "still", record.still_slot, max(0, self._discovery_still_slot_count - 1)
        return None, None, 0

    def _on_discovery_media_changed(self, *_args: str) -> None:
        """Preview the selected media after its active field is edited."""
        record = self._selected_discovery_record()
        if record is None:
            return
        media_kind, _media_value, maximum = self._discovery_media_info(record)
        if media_kind is None:
            return
        try:
            media_value = int(self.discovery_still_slot.get())
            if not 0 <= media_value <= maximum:
                raise ValueError
            preview_record = DiscoveryRecord(
                record.identifier, record.name, record.category_id, record.game_id, record.value,
                record.min_x, record.min_y, record.max_x, record.max_y,
                media_value if media_kind == "still" else record.still_slot,
                media_value if media_kind == "avi" else record.avi_id,
                media_value if media_kind == "animation" else record.animation_part,
            )
            self._show_discovery_image(preview_record)
        except (ValueError, tk.TclError):
            self._stop_discovery_motion_previews()
            self._discovery_image_photo = None
            self.discovery_image_preview.configure(image="", text="이미지 없음")

    def _show_discovery_image(self, record: DiscoveryRecord) -> None:
        """Show a discovery movie first, then fall back to its still/item image."""
        movie_path = self._game_discovery_avi_path(record.avi_id)
        if self._discovery_avi_preview is not None and movie_path is not None:
            if self._discover_animation_preview is not None:
                self._discover_animation_preview.stop()
            if self._discovery_avi_preview.show(movie_path):
                self._discovery_image_photo = None
                return
        if self._discovery_avi_preview is not None:
            self._discovery_avi_preview.stop()
        if (
            self._discover_animation_preview is not None
            and record.animation_part is not None
            and self.path.get()
            and self._discover_animation_preview.show(
                self.path.get(), record.animation_part,
            )
        ):
            self._discovery_image_photo = None
            return
        try:
            if 203 <= record.identifier <= 229:
                trade_item = self._item_by_identifier.get(record.identifier - 17)
                if trade_item is None or trade_item.image_id is None:
                    raise ItemImageReadError("교역품 이미지를 찾지 못했습니다.")
                image = self._read_game_item_image(trade_item.image_id)
            elif record.still_slot not in (None, 0):
                image = self._read_game_discovery_still(record.still_slot)
            else:
                self._discovery_image_photo = None
                self.discovery_image_preview.configure(image="", text="이미지 없음")
                return
            self._discovery_image_photo = ImageTk.PhotoImage(image.convert("RGBA"))
            self.discovery_image_preview.configure(image=self._discovery_image_photo, text="")
        except (DiscoveryImageReadError, ItemImageReadError, tk.TclError):
            self._discovery_image_photo = None
            self.discovery_image_preview.configure(image="", text="이미지 없음")

    def _read_game_discovery_still(self, image_slot: int) -> Image.Image:
        if not self.path.get():
            raise DiscoveryImageReadError("대상 실행 파일을 먼저 선택해 주세요.")
        return read_discovery_still(
            self.path.get(), image_slot=image_slot,
            palette_path=bundled_resource_path("Resources", "face_palette.png"),
        )

    def _game_discovery_avi_path(self, avi_id: int | None) -> Path | None:
        """Locate a discovery AVI beside the selected game's executable."""
        if avi_id is None or not self.path.get():
            return None
        avi_path = Path(self.path.get()).resolve().parent / "AVI" / f"I{avi_id:02d}_0000.AVI"
        return avi_path if avi_path.is_file() else None

    def _stop_discovery_motion_previews(self) -> None:
        """Stop AVI and DISCOVER frame playback before showing a still image."""
        if self._discovery_avi_preview is not None:
            self._discovery_avi_preview.stop()
        if self._discover_animation_preview is not None:
            self._discover_animation_preview.stop()

    @staticmethod
    def _discovery_directional_degree(
        magnitude: str, direction: str, positive: str, negative: str, label: str,
    ) -> str:
        """Turn a game-style compass component into a signed degree string."""
        if direction not in (positive, negative):
            raise ValueError(f"{label} 방향을 선택해 주세요.")
        try:
            value = float(magnitude)
        except ValueError as error:
            raise ValueError(f"{label} 값은 숫자로 입력해 주세요.") from error
        maximum = 90 if label == "위도" else 180
        if not 0 <= value <= maximum:
            raise ValueError(f"{label} 값은 0~{maximum}도 사이여야 합니다.")
        return magnitude if direction == positive else f"-{magnitude}"

    def _current_discovery_edit(self) -> DiscoveryEdit | None:
        record = self._selected_discovery_record()
        if record is None:
            return None
        try:
            still_slot, avi_id, animation_part = (
                record.still_slot, record.avi_id, record.animation_part,
            )
            media_kind, _media_value, maximum = self._discovery_media_info(record)
            if media_kind is not None:
                media_value = int(self.discovery_still_slot.get())
                if not 0 <= media_value <= maximum:
                    raise ValueError("발견물 미디어 번호가 범위를 벗어났습니다.")
                if media_kind == "still":
                    still_slot = media_value
                elif media_kind == "avi":
                    avi_id = media_value
                else:
                    animation_part = media_value
            raw_coordinates = (
                self.discovery_min_y.get(), self.discovery_max_y.get(),
                self.discovery_min_x.get(), self.discovery_max_x.get(),
            )
            coordinates = (
                (None, None, None, None) if not any(raw_coordinates)
                else (
                    longitude_to_world_x(self._discovery_directional_degree(
                        self.discovery_min_x.get(), self.discovery_min_x_direction.get(),
                        "동경", "서경", "경도",
                    )),
                    latitude_to_world_y(self._discovery_directional_degree(
                        self.discovery_min_y.get(), self.discovery_min_y_direction.get(),
                        "북위", "남위", "위도",
                    )),
                    longitude_to_world_x(self._discovery_directional_degree(
                        self.discovery_max_x.get(), self.discovery_max_x_direction.get(),
                        "동경", "서경", "경도",
                    )),
                    latitude_to_world_y(self._discovery_directional_degree(
                        self.discovery_max_y.get(), self.discovery_max_y_direction.get(),
                        "북위", "남위", "위도",
                    )),
                )
            )
            return DiscoveryEdit(
                record.identifier,
                self.discovery_name_entry.get().strip(),
                DISCOVERY_CATEGORY_NAMES.index(self.discovery_category.get()),
                int(self.discovery_value.get()),
                *coordinates,
                still_slot,
                avi_id,
                animation_part,
            )
        except (ValueError, IndexError) as error:
            raise ValueError("발견물 입력값을 확인해 주세요.") from error

    def _remember_discovery_edit(self, edit: DiscoveryEdit | None) -> None:
        if edit is None:
            return
        self._discovery_records = tuple(
            DiscoveryRecord(
                record.identifier, edit.name, edit.category_id, record.game_id, edit.value,
                edit.min_x, edit.min_y, edit.max_x, edit.max_y,
                edit.still_slot, edit.avi_id, edit.animation_part,
            ) if record.identifier == edit.identifier else record
            for record in self._discovery_records
        )
        self._discovery_by_identifier = {record.identifier: record for record in self._discovery_records}
        self._refresh_discovery_list()

    def _refresh_barmaid_child_aptitudes(self, face_code: int) -> None:
        if not 0 <= face_code < len(self._barmaid_child_aptitudes):
            return
        aptitude = self._barmaid_child_aptitudes[face_code]
        for variable, value in zip(self.barmaid_child_aptitudes, aptitude.modifiers):
            variable.set(str(value))

    def _update_barmaid_child_aptitude_total(self, *_args: str) -> None:
        """Reflect the sum of the six editable child-aptitude modifiers."""
        try:
            total = sum(int(variable.get()) for variable in self.barmaid_child_aptitudes)
        except ValueError:
            self.barmaid_child_aptitude_total.set("총합: 입력 확인")
            return
        self.barmaid_child_aptitude_total.set(f"총합: {total:+d}" if total else "총합: 0")

    def _select_all_barmaid_languages(self) -> None:
        for variable in self.barmaid_language_flags:
            variable.set(True)

    def _clear_all_barmaid_languages(self) -> None:
        for variable in self.barmaid_language_flags:
            variable.set(False)

    def _current_barmaid_edit(self) -> BarmaidEdit | None:
        if not self._barmaid_records:
            return None
        record = self._selected_barmaid_record()
        if record is None:
            raise ValueError("수정할 여급을 선택해 주세요.")
        name = self.barmaid_name_entry.get().strip()
        if not name:
            raise ValueError("여급 이름을 입력해 주세요.")
        try:
            face_code = int(self.barmaid_face_code.get())
        except ValueError as error:
            maximum_face_code = self._portrait_max_code(female=True)
            raise ValueError(f"여급 얼굴 코드는 0~{maximum_face_code} 사이의 정수여야 합니다.") from error
        maximum_face_code = self._portrait_max_code(female=True)
        if not 0 <= face_code <= maximum_face_code:
            raise ValueError(f"여급 얼굴 코드는 0~{maximum_face_code} 사이의 정수여야 합니다.")
        try:
            appearance_year = int(self.barmaid_appearance_year.get())
        except ValueError as error:
            raise ValueError("여급 출현 연도는 1480~1600 사이의 정수여야 합니다.") from error
        if not 1480 <= appearance_year <= 1600:
            raise ValueError("여급 출현 연도는 1480~1600 사이의 정수여야 합니다.")
        city_id = self.barmaid_city_selector.current()
        if not 0 <= city_id < len(BARMAID_CITY_NAMES):
            raise ValueError("여급 출현 도시를 선택해 주세요.")
        try:
            personality_id = BARMAID_PERSONALITY_NAMES.index(self.barmaid_personality.get())
        except ValueError as error:
            raise ValueError("여급 성격을 선택해 주세요.") from error
        language_flags = sum(
            1 << bit for bit, variable in enumerate(self.barmaid_language_flags) if variable.get()
        )
        try:
            child_aptitude_modifiers = tuple(int(variable.get()) for variable in self.barmaid_child_aptitudes)
        except ValueError as error:
            raise ValueError("자녀 능력치 보정값은 -255~+255 사이의 정수여야 합니다.") from error
        return BarmaidEdit(
            record.identifier, name, face_code, appearance_year, city_id,
            personality_id, language_flags, child_aptitude_modifiers,
        )

    def _remember_barmaid_edit(self, edit: BarmaidEdit | None) -> None:
        if edit is None:
            return
        self._barmaid_records = tuple(
            BarmaidRecord(
                record.identifier, edit.name, edit.face_code, edit.appearance_year, edit.city_id,
                edit.personality_id, edit.language_flags,
            )
            if record.identifier == edit.identifier else record
            for record in self._barmaid_records
        )
        self._barmaid_child_aptitudes = tuple(
            BarmaidChildAptitudes(aptitude.face_code, edit.child_aptitude_modifiers)
            if aptitude.face_code == edit.face_code else aptitude
            for aptitude in self._barmaid_child_aptitudes
        )
        self._barmaid_by_identifier = {record.identifier: record for record in self._barmaid_records}
        self._refresh_barmaid_list()

    def _set_sponsor_controls_enabled(self, enabled: bool) -> None:
        for control in self._sponsor_controls:
            if isinstance(control, NativeWinEdit):
                control.set_enabled(enabled)
            elif isinstance(control, ttk.Combobox):
                control.configure(state="readonly" if enabled else "disabled")
            else:
                control.state(["!disabled"] if enabled else ["disabled"])

    def _schedule_sponsor_list_refresh(self) -> None:
        previous_job = getattr(self, "_sponsor_search_job", None)
        if previous_job is not None:
            try:
                self.after_cancel(previous_job)
            except tk.TclError:
                pass
        self._sponsor_search_job = self.after(120, self._run_sponsor_list_refresh)

    def _run_sponsor_list_refresh(self) -> None:
        self._sponsor_search_job = None
        self._refresh_sponsor_list()

    def _refresh_sponsor_list(self) -> None:
        tree = self.sponsor_list
        selected = tree.selection()
        selected_identifier = selected[0] if selected else ""
        query = self.sponsor_search_entry.get().strip().casefold()
        tree.delete(*tree.get_children())
        for record in self._sponsor_records:
            if query and query not in record.name.casefold():
                continue
            tree.insert("", tk.END, iid=str(record.identifier), values=(f"{record.identifier:03d}", record.name))
        if selected_identifier and tree.exists(selected_identifier):
            tree.selection_set(selected_identifier)
            tree.focus(selected_identifier)
            tree.see(selected_identifier)

    def _selected_sponsor_record(self) -> SponsorRecord | None:
        selection = self.sponsor_list.selection()
        if not selection:
            return None
        try:
            identifier = int(selection[0])
        except ValueError:
            return None
        return self._sponsor_by_identifier.get(identifier)

    def _select_sponsor(self, identifier: int) -> None:
        item_id = str(identifier)
        if not self.sponsor_list.exists(item_id):
            return
        self.sponsor_list.selection_set(item_id)
        self.sponsor_list.focus(item_id)
        self.sponsor_list.see(item_id)
        self._on_sponsor_selected()

    def _load_sponsor_records(self, records: tuple[SponsorRecord, ...]) -> None:
        self._sponsor_records = records
        self._sponsor_by_identifier = {record.identifier: record for record in records}
        self.sponsor_search_entry.set("")
        self._refresh_sponsor_list()
        self._set_sponsor_controls_enabled(bool(records))
        if not records:
            self.sponsor_name.set("")
            for variable in (
                self.sponsor_face_code, self.sponsor_gender, self.sponsor_nation, self.sponsor_job,
                self.sponsor_appearance_year, self.sponsor_city_name, self.sponsor_building,
                self.sponsor_power, self.sponsor_wealth_factor, self.sponsor_appraisal,
            ):
                variable.set("")
            for variable in (*self.sponsor_preference_flags, *self.sponsor_language_flags):
                variable.set(False)
            self._show_sponsor_face(None, None)
            return
        self._select_sponsor(records[0].identifier)

    def _on_sponsor_selected(self, _event: tk.Event | None = None) -> None:
        record = self._selected_sponsor_record()
        if record is None:
            return
        self.sponsor_name.set(record.name)
        self.sponsor_face_code.set(str(record.face_code))
        self.sponsor_gender_selector.current(record.gender)
        self.sponsor_nation_selector.current(record.nation_id)
        self.sponsor_job_selector.current(record.job_id - 14)
        self.sponsor_appearance_year.set(str(record.appearance_year))
        self.sponsor_city_selector.current(record.city_id)
        self.sponsor_building_selector.current(record.building_id)
        self.sponsor_power.set(str(record.power))
        self.sponsor_wealth_factor.set(str(record.wealth_factor))
        self.sponsor_appraisal.set(str(record.appraisal))
        for bit, variable in enumerate(self.sponsor_preference_flags):
            variable.set(bool(record.preference_flags & (1 << bit)))
        for bit, variable in enumerate(self.sponsor_language_flags):
            variable.set(bool(record.language_flags & (1 << bit)))
        self._show_sponsor_face(record.face_code, record.gender)

    def _current_sponsor_edit(self) -> SponsorEdit | None:
        if not self._sponsor_records:
            return None
        record = self._selected_sponsor_record()
        if record is None:
            raise ValueError("수정할 후원자를 선택해 주세요.")
        try:
            face_code = int(self.sponsor_face_code.get())
            appearance_year = int(self.sponsor_appearance_year.get())
            power = int(self.sponsor_power.get())
            wealth_factor = int(self.sponsor_wealth_factor.get())
            appraisal = int(self.sponsor_appraisal.get())
        except ValueError as error:
            raise ValueError("후원자 숫자 입력값을 확인해 주세요.") from error
        gender = self.sponsor_gender_selector.current()
        nation_id = self.sponsor_nation_selector.current()
        job_index = self.sponsor_job_selector.current()
        city_id = self.sponsor_city_selector.current()
        building_id = self.sponsor_building_selector.current()
        if gender not in range(len(SPONSOR_GENDER_NAMES)):
            raise ValueError("후원자 성별을 선택해 주세요.")
        maximum_face_code = self._portrait_max_code(female=gender == 1)
        if not 0 <= face_code <= maximum_face_code:
            raise ValueError(
                f"후원자 얼굴 코드는 0~{maximum_face_code} 사이의 정수여야 합니다."
            )
        if nation_id not in range(len(SPONSOR_NATION_NAMES)):
            raise ValueError("후원자 국가를 선택해 주세요.")
        if job_index not in range(len(SPONSOR_JOB_NAMES)):
            raise ValueError("후원자 직업을 선택해 주세요.")
        if city_id not in range(len(BARMAID_CITY_NAMES)):
            raise ValueError("후원자 소재 도시를 선택해 주세요.")
        if building_id not in range(len(SPONSOR_BUILDING_NAMES)):
            raise ValueError("후원자 소재 시설을 선택해 주세요.")
        preference_flags = sum(
            1 << bit for bit, variable in enumerate(self.sponsor_preference_flags) if variable.get()
        )
        language_flags = sum(
            1 << bit for bit, variable in enumerate(self.sponsor_language_flags) if variable.get()
        )
        return SponsorEdit(
            record.identifier, face_code, gender, nation_id, 14 + job_index, appearance_year,
            power, city_id, building_id, wealth_factor, appraisal, preference_flags, language_flags,
        )

    def _remember_sponsor_edit(self, edit: SponsorEdit | None) -> None:
        if edit is None:
            return
        self._sponsor_records = tuple(
            SponsorRecord(
                record.identifier, record.name, edit.face_code, edit.gender, edit.nation_id, edit.job_id,
                edit.appearance_year, edit.power, edit.city_id, edit.building_id, edit.wealth_factor,
                edit.appraisal, edit.preference_flags, edit.language_flags,
            )
            if record.identifier == edit.identifier else record
            for record in self._sponsor_records
        )
        self._sponsor_by_identifier = {record.identifier: record for record in self._sponsor_records}
        self._refresh_sponsor_list()

    def show_patch_details(self, title: str, details: str, first_line_color: str | None = None) -> None:
        window = tk.Toplevel(self)
        window.title(title)
        window.geometry("620x520")
        window.minsize(500, 380)
        window.transient(self)

        frame = ttk.Frame(window, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        text = tk.Text(frame, wrap=tk.WORD, padx=10, pady=10, spacing1=2, spacing3=2)
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        text.insert("1.0", details)
        first_line = details.splitlines()[0].strip() if details else ""
        if first_line.casefold().startswith("by "):
            first_line_color = "#1A73E8"
        if first_line_color:
            text.tag_add("first_line", "1.0", "1.end")
            text.tag_configure("first_line", foreground=first_line_color)
        text.configure(state=tk.DISABLED)
        ttk.Button(window, text="닫기", command=window.destroy).pack(pady=(0, 10))
        self._center_dialog(window)
        window.grab_set()

    def _update_pirate_control_states(self) -> None:
        enabled = self.pirate_variety_enabled.get()
        state = ["!disabled"] if enabled else ["disabled"]
        for entry in (
            self.pirate_fame_middle_entry,
            self.pirate_fame_high_entry,
            self.pirate_pursuit_middle_entry,
            self.pirate_pursuit_high_entry,
        ):
            entry.state(state)
        self.pirate_probability_notebook.state(state)
        tab_state = "normal" if enabled else "disabled"
        for tab in self.pirate_probability_notebook.tabs():
            self.pirate_probability_notebook.tab(tab, state=tab_state)
        for tree in self.pirate_probability_trees.values():
            tree.state(state)

    def _refresh_pirate_probability_tree(self) -> None:
        for region, tree in self.pirate_probability_trees.items():
            rows = (
                ("stage1", "1단계", "100% (고정)", "-", "-"),
                (
                    "stage2", "2단계",
                    f"{self._pirate_probability_variables[(region, 'stage2', 'first')].get()}%",
                    f"{self._pirate_probability_variables[(region, 'stage2', 'second')].get()}%",
                    "-",
                ),
                (
                    "stage3", "3단계",
                    f"{self._pirate_probability_variables[(region, 'stage3', 'first')].get()}%",
                    f"{self._pirate_probability_variables[(region, 'stage3', 'second')].get()}%",
                    f"{self._pirate_probability_variables[(region, 'stage3', 'third')].get()}%",
                ),
            )
            for item_id, stage, first, second, third in rows:
                values = (stage, first, second, third)
                if tree.exists(item_id):
                    tree.item(item_id, values=values)
                else:
                    tree.insert("", tk.END, iid=item_id, values=values)

    def _edit_pirate_probability(self, event: tk.Event, region: str) -> None:
        if not self.pirate_variety_enabled.get():
            return
        tree = self.pirate_probability_trees[region]
        column_numbers = {"#2": "first", "#3": "second", "#4": "third"}
        column = column_numbers.get(tree.identify_column(event.x))
        if tree.identify_region(event.x, event.y) != "cell" or column is None:
            return
        item_id = tree.identify_row(event.y)
        variable = self._pirate_probability_variables.get((region, item_id, column))
        if variable is None:
            return
        box = tree.bbox(item_id, column)
        if not box:
            return
        x, y, width, height = box
        editor = ttk.Entry(tree, textvariable=variable, justify=tk.CENTER)
        editor.place(x=x, y=y, width=width, height=height)
        self._limit_integer_input(editor, 0, 100)
        editor.focus_set()
        editor.selection_range(0, tk.END)
        closed = False

        def close(save: bool = True) -> None:
            nonlocal closed
            if closed:
                return
            closed = True
            if not save:
                variable.set(tree.set(item_id, column).removesuffix("%"))
            editor.destroy()
            self._refresh_pirate_probability_tree()

        editor.bind("<Return>", lambda _event: close())
        editor.bind("<Escape>", lambda _event: close(False))
        editor.bind("<FocusOut>", lambda _event: close())

    def _update_cold_limit_entry_states(self) -> None:
        for entry, unlocked in (
            (self.cold_north_entry, self.cold_north_unlocked),
            (self.cold_south_entry, self.cold_south_unlocked),
        ):
            entry.state(["disabled"] if unlocked.get() else ["!disabled"])
        self.eclipse_latitude_entry.state(["!disabled"] if self.eclipse_enabled.get() else ["disabled"])

    def select_exe(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="대항해시대 III 실행 파일 선택",
            filetypes=(("실행 파일", "*.exe"), ("모든 파일", "*.*")),
        )
        if selected:
            target = Path(selected)
            try:
                (
                    coordinate, presets, departure, arrival_wait,
                    long_rest_max, exploration_days, succession_age,
                    cold_north_limit, cold_south_limit,
                    cash_limit, deposit_limit,
                    fame_limit, infamy_limit,
                    npc_activity_min_age, npc_activity_max_age,
                    western_encounter_denominator, islamic_encounter_denominator,
                    pirate_variety_enabled,
                    pirate_settings,
                    eclipse_enabled,
                    eclipse_latitude,
                    mistranslation_fixes_enabled,
                ) = read_settings(target)
                barmaid_records = read_barmaid_records(target)
                barmaid_child_aptitudes = read_barmaid_child_aptitudes(target)
                sponsor_records = read_sponsor_records(target)
                person_records = read_person_records(target)
                ship_type_records = read_ship_type_records(target)
                city_records = read_city_records(target)
                trade_good_names = read_trade_good_names(target)
                trade_region_goods = read_trade_region_goods(target)
                item_records = read_item_records(target)
                figurehead_effect_settings = read_figurehead_effect_settings(target)
                discovery_records = read_discovery_records(target)
                discovery_slot_count = discovery_still_count(target)
                portrait_counts = {
                    True: portrait_count(target, female=True),
                    False: portrait_count(target, female=False),
                }
            except Exception as exc:
                messagebox.showerror("EXE 읽기 실패", str(exc), parent=self)
                return
            # 얼굴 미리보기도 아래 레코드 로드 중 바로 원본 게임 폴더에서 읽는다.
            self.path.set(selected)
            self._discovery_still_slot_count = discovery_slot_count
            discovery_slot_maximum = max(0, discovery_slot_count - 1)
            self.discovery_still_slot_entry.configure(
                to=discovery_slot_maximum,
                validatecommand=(
                    self._integer_validation_command, "%P", "0", str(discovery_slot_maximum),
                ),
            )
            self._portrait_counts = portrait_counts
            self._configure_portrait_code_ranges()
            self._load_barmaid_records(barmaid_records, barmaid_child_aptitudes)
            self._load_sponsor_records(sponsor_records)
            self._load_person_records(person_records)
            self._load_ship_type_records(ship_type_records)
            self._load_item_records(item_records)
            self._load_figurehead_effect_settings(figurehead_effect_settings)
            self._load_city_records(city_records, trade_good_names, trade_region_goods)
            self._load_discovery_records(discovery_records)
            self.coordinate.set(coordinate)
            for width, height, (current_width, current_height) in zip(self.widths, self.heights, presets):
                width.set(str(current_width))
                height.set(str(current_height))
            self.departure.set(str(departure))
            self.arrival_wait.set(str(arrival_wait))
            self.npc_activity_min_age.set(str(npc_activity_min_age))
            self.npc_activity_max_age.set(str(npc_activity_max_age))
            self.western_encounter_denominator.set(str(western_encounter_denominator))
            self.islamic_encounter_denominator.set(str(islamic_encounter_denominator))
            self.pirate_variety_enabled.set(pirate_variety_enabled)
            self.eclipse_enabled.set(eclipse_enabled)
            self.eclipse_latitude.set(f"{eclipse_latitude:.3f}" if eclipse_enabled else "0")
            self.mistranslation_fixes_enabled.set(mistranslation_fixes_enabled)
            discovery_errors: list[str] = []
            try:
                self.kaaba_enabled.set(is_kaaba_enabled(target))
            except KaabaPatchError as exc:
                self.kaaba_enabled.set(False)
                discovery_errors.append(str(exc))
            try:
                slave_enabled = (
                    is_slave_library_enabled(target) or is_slave_dialogue_enabled(target)
                )
                self.slave_enabled.set(slave_enabled)
                self._slave_was_enabled = slave_enabled
            except SlavePatchError as exc:
                self.slave_enabled.set(False)
                self._slave_was_enabled = False
                discovery_errors.append(str(exc))
            try:
                mughal_enabled = is_mughal_enabled(target)
                self.mughal_enabled.set(mughal_enabled)
                self._mughal_was_enabled = mughal_enabled
            except MughalPatchError as exc:
                self.mughal_enabled.set(False)
                self._mughal_was_enabled = False
                discovery_errors.append(str(exc))
            if discovery_errors:
                messagebox.showwarning(
                    "발견물 패치 상태 확인 필요",
                    "일반 EXE 설정은 읽었습니다.\n\n" + "\n\n".join(discovery_errors),
                    parent=self,
                )
            if pirate_variety_enabled:
                self.pirate_fame_middle.set(str(pirate_settings.fame_middle))
                self.pirate_fame_high.set(str(pirate_settings.fame_high))
                self.pirate_western_stage2_first_probability.set(str(pirate_settings.western_stage2_first_probability))
                self.pirate_western_stage2_second_probability.set(str(pirate_settings.western_stage2_second_probability))
                self.pirate_western_stage3_first_probability.set(str(pirate_settings.western_stage3_first_probability))
                self.pirate_western_stage3_second_probability.set(str(pirate_settings.western_stage3_second_probability))
                self.pirate_western_stage3_third_probability.set(str(pirate_settings.western_stage3_third_probability))
                self.pirate_eastern_stage2_first_probability.set(str(pirate_settings.eastern_stage2_first_probability))
                self.pirate_eastern_stage2_second_probability.set(str(pirate_settings.eastern_stage2_second_probability))
                self.pirate_eastern_stage3_first_probability.set(str(pirate_settings.eastern_stage3_first_probability))
                self.pirate_eastern_stage3_second_probability.set(str(pirate_settings.eastern_stage3_second_probability))
                self.pirate_eastern_stage3_third_probability.set(str(pirate_settings.eastern_stage3_third_probability))
                self.pirate_pursuit_middle_threshold.set(str(pirate_settings.pursuit_middle_threshold))
                self.pirate_pursuit_high_threshold.set(str(pirate_settings.pursuit_high_threshold))
            else:
                for variable in (
                    self.pirate_fame_middle,
                    self.pirate_fame_high,
                    self.pirate_western_stage2_first_probability,
                    self.pirate_western_stage2_second_probability,
                    self.pirate_western_stage3_first_probability,
                    self.pirate_western_stage3_second_probability,
                    self.pirate_western_stage3_third_probability,
                    self.pirate_eastern_stage2_first_probability,
                    self.pirate_eastern_stage2_second_probability,
                    self.pirate_eastern_stage3_first_probability,
                    self.pirate_eastern_stage3_second_probability,
                    self.pirate_eastern_stage3_third_probability,
                    self.pirate_pursuit_middle_threshold,
                    self.pirate_pursuit_high_threshold,
                ):
                    variable.set("0")
            self.pirate_western_base_id.set(f"0x{pirate_settings.western_base_id:X}")
            self.pirate_eastern_base_id.set(f"0x{pirate_settings.eastern_base_id:X}")
            self.pirate_pursuit_base_id.set(f"0x{pirate_settings.pursuit_base_id:X}")
            self._refresh_pirate_probability_tree()
            self._update_pirate_control_states()
            self.long_rest_max.set(str(long_rest_max))
            self.exploration_days.set(str(exploration_days))
            self.succession_age.set(str(succession_age))
            self.cash_limit.set(str(cash_limit))
            self.deposit_limit.set(str(deposit_limit))
            self.fame_limit.set(str(fame_limit))
            self.infamy_limit.set(str(infamy_limit))
            self.cold_north_latitude.set(f"{cold_limit_to_latitude(cold_north_limit):.3f}")
            self.cold_south_latitude.set(f"{cold_limit_to_latitude(cold_south_limit):.3f}")
            self.cold_north_unlocked.set(cold_north_limit > 10000)
            self.cold_south_unlocked.set(cold_south_limit > 10000)
            self._update_cold_limit_entry_states()
            self.path_entry.xview_moveto(1.0)

    def apply_fullscreen(self) -> None:
        """Fill the third preset with the largest size accepted by the game."""
        _, _, game_area = get_screen_bounds()
        self.widths[2].set(str(game_area[0]))
        self.heights[2].set(str(game_area[1]))

    def _show_update_notice(self) -> None:
        """Show the release note left by the replacement process, if present."""
        try:
            notice_index = sys.argv.index("--update-notice") + 1
            notice_path = Path(sys.argv[notice_index])
            notice = json.loads(notice_path.read_text(encoding="utf-8"))
            notice_path.unlink(missing_ok=True)
        except (ValueError, IndexError, OSError, json.JSONDecodeError):
            return
        version = str(notice.get("version", "")).strip()
        notes = str(notice.get("notes", "")).strip()
        message = f"v{version} 업데이트를 완료했습니다." if version else "업데이트를 완료했습니다."
        if notes:
            message += f"\n\n{notes}"
        messagebox.showinfo("업데이트 완료", message, parent=self)

    def _set_update_checking(self, checking: bool) -> None:
        self._update_checking = checking

    def check_for_updates(self, silent: bool = False) -> None:
        """Check GitHub Releases without blocking the Tk event loop."""
        if self._update_checking:
            return
        updater = GitHubReleaseUpdater(APP_UPDATE_CONFIG)
        if not updater.enabled:
            if not silent:
                messagebox.showinfo("업데이트", "업데이트 저장소가 설정되어 있지 않습니다.", parent=self)
            return
        self._set_update_checking(True)
        threading.Thread(
            target=self._fetch_latest_update,
            args=(updater, silent),
            daemon=True,
        ).start()

    def _fetch_latest_update(self, updater: GitHubReleaseUpdater, silent: bool) -> None:
        try:
            release = updater.fetch_latest_release()
        except UpdateError as exc:
            self.after(0, lambda: self._finish_update_check(None, None, silent, str(exc)))
            return
        if release is None or not updater.is_newer_release(release):
            self.after(0, lambda: self._finish_update_check(None, None, silent, None))
            return
        asset = updater.release_asset(release)
        if asset is None:
            self.after(0, lambda: self._finish_update_check(
                None, None, silent, "새 릴리스의 업데이트 ZIP을 찾지 못했습니다."
            ))
            return
        self.after(0, lambda: self._finish_update_check(updater, (release, asset), silent, None))

    def _finish_update_check(
        self,
        updater: GitHubReleaseUpdater | None,
        update: tuple[dict, dict] | None,
        silent: bool,
        error: str | None,
    ) -> None:
        self._set_update_checking(False)
        if error:
            if not silent:
                messagebox.showerror("업데이트 확인 실패", error, parent=self)
            return
        if update is None:
            if not silent:
                messagebox.showinfo("업데이트", "현재 최신 버전을 사용하고 있습니다.", parent=self)
            return
        release, asset = update
        assert updater is not None
        self._available_update = (updater, release, asset)
        if self._update_button is not None:
            version = str(release.get("tag_name", "")).strip() or "새 버전"
            self._update_button.configure(text=f"{version} 업데이트 설치…", state=tk.NORMAL)
            self._update_button.grid()

    def install_available_update(self) -> None:
        """Ask for consent and install the update found during automatic checking."""
        if self._available_update is None:
            return
        updater, release, asset = self._available_update
        version = str(release.get("tag_name", "")).strip() or "새 버전"
        if not messagebox.askyesno(
            "업데이트 설치",
            f"{version} 업데이트를 다운로드하고 설치할까요?",
            parent=self,
        ):
            return
        self._set_update_checking(True)
        if self._update_button is not None:
            self._update_button.configure(state=tk.DISABLED)
        threading.Thread(
            target=self._download_update,
            args=(updater, release, asset),
            daemon=True,
        ).start()

    def _download_update(self, updater: GitHubReleaseUpdater, release: dict, asset: dict) -> None:
        try:
            replacement = updater.download_and_extract(asset)
        except UpdateError as exc:
            self.after(0, lambda: self._finish_update_download(None, updater, release, str(exc)))
            return
        self.after(0, lambda: self._finish_update_download(replacement, updater, release, None))

    def _finish_update_download(
        self,
        replacement: Path | None,
        updater: GitHubReleaseUpdater,
        release: dict,
        error: str | None,
    ) -> None:
        self._set_update_checking(False)
        if error:
            if self._update_button is not None:
                self._update_button.configure(state=tk.NORMAL)
            messagebox.showerror("업데이트 실패", error, parent=self)
            return
        assert replacement is not None
        try:
            updater.launch_replacer(replacement, release)
        except UpdateError as exc:
            messagebox.showerror("업데이트 실패", str(exc), parent=self)
            return
        self.destroy()

    def apply(self) -> None:
        if not self.path.get():
            messagebox.showwarning("실행 파일 필요", "패치할 EXE 파일을 선택해 주세요.", parent=self)
            return
        try:
            presets = tuple((int(width.get()), int(height.get())) for width, height in zip(self.widths, self.heights))
            cold_north_limit = (
                COLD_LIMIT_DISABLED_VALUE if self.cold_north_unlocked.get()
                else latitude_to_cold_limit(self.cold_north_latitude.get())
            )
            cold_south_limit = (
                COLD_LIMIT_DISABLED_VALUE if self.cold_south_unlocked.get()
                else latitude_to_cold_limit(self.cold_south_latitude.get())
            )
            pirate_variety_enabled = self.pirate_variety_enabled.get()
            if pirate_variety_enabled:
                pirate_settings = PirateVarietySettings(
                    fame_middle=int(self.pirate_fame_middle.get()),
                    fame_high=int(self.pirate_fame_high.get()),
                    western_stage2_first_probability=int(self.pirate_western_stage2_first_probability.get()),
                    western_stage2_second_probability=int(self.pirate_western_stage2_second_probability.get()),
                    western_stage3_first_probability=int(self.pirate_western_stage3_first_probability.get()),
                    western_stage3_second_probability=int(self.pirate_western_stage3_second_probability.get()),
                    western_stage3_third_probability=int(self.pirate_western_stage3_third_probability.get()),
                    eastern_stage2_first_probability=int(self.pirate_eastern_stage2_first_probability.get()),
                    eastern_stage2_second_probability=int(self.pirate_eastern_stage2_second_probability.get()),
                    eastern_stage3_first_probability=int(self.pirate_eastern_stage3_first_probability.get()),
                    eastern_stage3_second_probability=int(self.pirate_eastern_stage3_second_probability.get()),
                    eastern_stage3_third_probability=int(self.pirate_eastern_stage3_third_probability.get()),
                    western_base_id=int(self.pirate_western_base_id.get(), 0),
                    eastern_base_id=int(self.pirate_eastern_base_id.get(), 0),
                    pursuit_base_id=int(self.pirate_pursuit_base_id.get(), 0),
                    pursuit_middle_threshold=int(self.pirate_pursuit_middle_threshold.get()),
                    pursuit_high_threshold=int(self.pirate_pursuit_high_threshold.get()),
                )
            else:
                pirate_settings = PirateVarietySettings()
            barmaid_edit = self._current_barmaid_edit()
            sponsor_edit = self._current_sponsor_edit()
            person_edit = self._current_person_edit()
            ship_type_edit = self._current_ship_type_edit()
            city_edit = self._current_city_edit()
            trade_region_goods_edit = self._current_trade_region_goods()
            item_edit = self._current_item_edit()
            figurehead_effect_settings = self._current_figurehead_effect_settings()
            discovery_edit = self._current_discovery_edit()
            target = Path(self.path.get())
            backed_up_paths: set[Path] = set()
            backup = apply_all(
                target, self.coordinate.get(), True, presets,
                int(self.departure.get()), int(self.arrival_wait.get()),
                int(self.long_rest_max.get()), int(self.exploration_days.get()),
                int(self.succession_age.get()), cold_north_limit,
                cold_south_limit,
                int(self.cash_limit.get()), int(self.deposit_limit.get()),
                int(self.fame_limit.get()), int(self.infamy_limit.get()),
                int(self.npc_activity_min_age.get()), int(self.npc_activity_max_age.get()),
                int(self.western_encounter_denominator.get()),
                int(self.islamic_encounter_denominator.get()),
                pirate_variety_enabled,
                pirate_settings,
                self.eclipse_enabled.get(),
                self.eclipse_latitude.get(),
                self.mistranslation_fixes_enabled.get(),
                figurehead_effect_settings,
                barmaid_edit,
                sponsor_edit,
                person_edit,
                ship_type_edit,
                city_edit,
                trade_region_goods_edit,
                item_edit,
                discovery_edit,
            )
            if backup is not None:
                backed_up_paths.add(target.resolve())
            kaaba_backups = apply_kaaba_patch(target, self.kaaba_enabled.get(), backed_up_paths)
            kaaba_save_backup = (
                promote_game_savedata(target, backed_up_paths) if self.kaaba_enabled.get() else None
            )
            slave_library_backups: tuple[Path, ...] = ()
            slave_dialogue_backups: tuple[Path, ...] = ()
            if self.slave_enabled.get() or self._slave_was_enabled:
                slave_library_backups = apply_slave_library_hint(
                    target, self.slave_enabled.get(), backed_up_paths
                )
                slave_dialogue_backups = apply_slave_dialogue(
                    target, self.slave_enabled.get(), backed_up_paths
                )
            mughal_backups: tuple[Path, ...] = ()
            if self.mughal_enabled.get() or self._mughal_was_enabled:
                mughal_backups = apply_mughal_patch(
                    target, self.mughal_enabled.get(), backed_up_paths
                )
            self.cold_north_latitude.set(f"{cold_limit_to_latitude(cold_north_limit):.3f}")
            self.cold_south_latitude.set(f"{cold_limit_to_latitude(cold_south_limit):.3f}")
        except (ValueError, KaabaPatchError, KaabaSavePatchError, SlavePatchError, MughalPatchError) as exc:
            messagebox.showerror("입력 또는 패치 오류", str(exc), parent=self)
            return
        except Exception as exc:
            messagebox.showerror("패치 실패", str(exc), parent=self)
            return
        if (backup is None and not kaaba_backups and kaaba_save_backup is None
                and not slave_library_backups and not slave_dialogue_backups and not mughal_backups):
            messagebox.showinfo("완료", "선택한 설정이 이미 적용되어 있습니다.", parent=self)
        else:
            backups = [
                backup, *kaaba_backups, kaaba_save_backup,
                *slave_library_backups, *slave_dialogue_backups, *mughal_backups,
            ]
            backup_text = "\n".join(str(path) for path in backups if path is not None)
            messagebox.showinfo("완료", f"선택한 설정을 적용했습니다.\n\n원본 백업:\n{backup_text}", parent=self)
        self._remember_barmaid_edit(barmaid_edit)
        self._remember_sponsor_edit(sponsor_edit)
        self._remember_ship_type_edit(ship_type_edit)
        self._remember_city_edit(city_edit)
        self._remember_item_edit(item_edit)
        self._remember_discovery_edit(discovery_edit)
        self._slave_was_enabled = self.slave_enabled.get()
        self._mughal_was_enabled = self.mughal_enabled.get()


if __name__ == "__main__":
    CDSExecutablePatcher().mainloop()
