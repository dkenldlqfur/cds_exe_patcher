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

sys.path.insert(0, str(Path(__file__).resolve().parent))
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

from patch_cds_integrated import (
    BarmaidEdit,
    BarmaidChildAptitudes,
    BarmaidRecord,
    SponsorEdit,
    SponsorRecord,
    COLD_LIMIT_DISABLED_VALUE,
    PirateVarietySettings,
    apply_all,
    cold_limit_to_latitude,
    get_screen_bounds,
    latitude_to_cold_limit,
    read_barmaid_child_aptitudes,
    read_barmaid_records,
    read_sponsor_records,
    read_settings,
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
SPONSOR_GENDER_NAMES = ("남성", "여성")
SPONSOR_PREFERENCE_NAMES = ("지리", "역사", "보물", "종교", "교역품", "미신", "생물", "민족")


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
                text = self._limit_cp949_bytes(raw_text, self.max_bytes) if self.max_bytes is not None else raw_text
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
        self._integer_validation_command = self.register(self._validate_integer_text)
        self._decimal_validation_command = self.register(self._validate_decimal_text)
        self.barmaid_face_code.trace_add("write", self._on_barmaid_face_code_changed)
        self.sponsor_face_code.trace_add("write", self._on_sponsor_face_code_changed)
        self.sponsor_gender.trace_add("write", self._on_sponsor_face_code_changed)
        for variable in self.barmaid_child_aptitudes:
            variable.trace_add("write", self._update_barmaid_child_aptitude_total)
        self._build()
        self._center_main_window()
        self._show_splash()

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
        settings_notebook.add(basic_tab, text="기본 정보")
        settings_notebook.add(additional_tab, text="추가 패치")
        settings_notebook.add(barmaid_tab, text="여급")
        settings_notebook.add(sponsor_tab, text="후원자")

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

        translation_box = ttk.LabelFrame(additional_left_column, text="오역 수정", padding=10)
        translation_box.grid(row=0, column=0, sticky="ew")
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
        discovery_box.grid(row=1, column=0, pady=(10, 0), sticky="ew")

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

        barmaid_list_box = ttk.LabelFrame(barmaid_tab, text="여급 목록", padding=10)
        barmaid_list_box.grid(row=0, column=0, rowspan=2, sticky="ns")
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
        sponsor_list_box.grid(row=0, column=0, rowspan=3, sticky="ns")
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
        self.sponsor_list.column("name", width=150, anchor="w", stretch=True)
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
            sponsor_box, from_=0, to=412, textvariable=self.sponsor_face_code, width=5, state="disabled",
        )
        self.sponsor_face_code_entry.grid(row=1, column=1, padx=(6, 0), pady=(8, 0), sticky="w")
        self._limit_integer_input(self.sponsor_face_code_entry, 0, 412)
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

    def _show_barmaid_face(self, face_code: int | None) -> None:
        """Display the FEMALE.CDS portrait assigned to the current face code."""
        if face_code is None:
            self._barmaid_face_photo = None
            self.barmaid_image_preview.configure(image="", text="이미지 없음")
            return
        image_path = bundled_resource_path(
            "Resources", "faces", "female", f"female_{face_code:03d}.png",
        )
        try:
            with Image.open(image_path) as source:
                self._barmaid_face_photo = ImageTk.PhotoImage(source.convert("RGBA"))
        except (OSError, tk.TclError):
            self._barmaid_face_photo = None
            self.barmaid_image_preview.configure(image="", text="이미지 없음")
            return
        self.barmaid_image_preview.configure(image=self._barmaid_face_photo, text="")

    def _on_sponsor_face_code_changed(self, *_args: str) -> None:
        """Refresh the sponsor portrait after face code or gender changes."""
        try:
            face_code = int(self.sponsor_face_code.get())
        except ValueError:
            self._show_sponsor_face(None, None)
            return
        self._show_sponsor_face(face_code, self.sponsor_gender_selector.current())

    def _show_sponsor_face(self, face_code: int | None, gender: int | None) -> None:
        """Display the portrait from MALE.CDS or FEMALE.CDS for the sponsor."""
        if face_code is None or gender not in range(len(SPONSOR_GENDER_NAMES)):
            self._sponsor_face_photo = None
            self.sponsor_image_preview.configure(image="", text="이미지 없음")
            return
        archive = "female" if gender == 1 else "male"
        image_path = bundled_resource_path(
            "Resources", "faces", archive, f"{archive}_{face_code:03d}.png",
        )
        try:
            with Image.open(image_path) as source:
                self._sponsor_face_photo = ImageTk.PhotoImage(source.convert("RGBA"))
        except (OSError, tk.TclError):
            self._sponsor_face_photo = None
            self.sponsor_image_preview.configure(image="", text="이미지 없음")
            return
        self.sponsor_image_preview.configure(image=self._sponsor_face_photo, text="")

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
            raise ValueError("여급 얼굴 코드는 0~143 사이의 정수여야 합니다.") from error
        if not 0 <= face_code <= 143:
            raise ValueError("여급 얼굴 코드는 0~143 사이의 정수여야 합니다.")
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
            except Exception as exc:
                messagebox.showerror("EXE 읽기 실패", str(exc), parent=self)
                return
            self.path.set(selected)
            self._load_barmaid_records(barmaid_records, barmaid_child_aptitudes)
            self._load_sponsor_records(sponsor_records)
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
                barmaid_edit,
                sponsor_edit,
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
        self._slave_was_enabled = self.slave_enabled.get()
        self._mughal_was_enabled = self.mughal_enabled.get()


if __name__ == "__main__":
    CDSExecutablePatcher().mainloop()
