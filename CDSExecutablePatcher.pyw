"""Unified GUI for the supported CDS III executable patches."""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app_update import load_update_config

from patch_cds_integrated import (
    COLD_LIMIT_DISABLED_VALUE,
    PirateVarietySettings,
    apply_all,
    cold_limit_to_latitude,
    get_screen_bounds,
    latitude_to_cold_limit,
    read_settings,
)


# GitHub Releases 기반 업데이트 기반은 함께 패키징하되, app_config.json에
# repository를 지정하기 전까지는 네트워크 확인이나 UI 동작을 시작하지 않는다.
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


class CDSExecutablePatcher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"대항해시대 III EXE 패치 v{APP_VERSION}")
        self.resizable(False, False)
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
        self.cold_north_latitude = tk.StringVar(value="0")
        self.cold_south_latitude = tk.StringVar(value="0")
        self.cold_north_unlocked = tk.BooleanVar(value=False)
        self.cold_south_unlocked = tk.BooleanVar(value=False)
        self.eclipse_enabled = tk.BooleanVar(value=False)
        self.eclipse_latitude = tk.StringVar(value="0")
        self._build()

    def _build(self) -> None:
        ttk.Style(self).configure("Credit.TLabel", foreground="#1A73E8")
        frame = ttk.Frame(self, padding=14)
        frame.grid(sticky="nsew")
        ttk.Label(frame, text="대상 실행 파일").grid(row=0, column=0, padx=(0, 8), sticky="w")
        self.path_entry = ttk.Entry(frame, textvariable=self.path, width=38, state="readonly")
        self.path_entry.grid(row=0, column=1, sticky="ew")
        ttk.Button(frame, text="EXE 선택…", command=self.select_exe).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(frame, text="선택한 설정으로 패치", command=self.apply).grid(row=0, column=3, padx=(8, 0))
        frame.columnconfigure(1, weight=1)

        settings_frame = ttk.Frame(frame)
        settings_frame.grid(row=1, column=0, columnspan=4, pady=(12, 0), sticky="nsew")
        settings_frame.columnconfigure(0, weight=1)
        settings_frame.columnconfigure(1, weight=1)
        left_column = ttk.Frame(settings_frame)
        left_column.grid(row=0, column=0, padx=(0, 5), sticky="new")
        right_column = ttk.Frame(settings_frame)
        right_column.grid(row=0, column=1, padx=(5, 0), sticky="new")
        left_column.columnconfigure(0, weight=1)
        right_column.columnconfigure(0, weight=1)

        coordinate_box = ttk.LabelFrame(right_column, text="좌표 표시", padding=10)
        coordinate_box.grid(row=0, column=0, sticky="ew")
        for row, (value, label) in enumerate((
            ("original", "원본: 북위 40  동경 110"),
            ("korean2", "소수 둘째 자리: 북위40.00 동경110.00"),
            ("korean3", "소수 셋째 자리: 북 40.000 동 110.000"),
        )):
            ttk.Radiobutton(coordinate_box, text=label, value=value, variable=self.coordinate).grid(row=row, column=0, sticky="w", pady=2)

        resolution_box = ttk.LabelFrame(left_column, text="해상도 선택지", padding=10)
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

        npc_box = ttk.LabelFrame(left_column, text="일반 NPC 이동", padding=10)
        npc_box.grid(row=2, column=0, pady=(10, 0), sticky="ew")
        ttk.Label(npc_box, text="월간 출발 확률: 1 /").grid(row=0, column=0, sticky="w")
        ttk.Entry(npc_box, textvariable=self.departure, width=6).grid(row=0, column=1, padx=(4, 0))
        ttk.Label(npc_box, text="(분모 1~127, 원본 5)").grid(row=0, column=2, padx=(5, 0), sticky="w")
        ttk.Label(npc_box, text="도착 대기:").grid(row=1, column=0, pady=(5, 0), sticky="w")
        ttk.Entry(npc_box, textvariable=self.arrival_wait, width=6).grid(row=1, column=1, padx=(4, 0), pady=(5, 0), sticky="w")
        ttk.Label(npc_box, text="일 (0~60, 기본값 60)").grid(row=1, column=2, padx=(5, 0), pady=(5, 0), sticky="w")
        gameplay_box = ttk.LabelFrame(left_column, text="게임 진행 설정", padding=10)
        gameplay_box.grid(row=0, column=0, sticky="ew")
        gameplay_rows = (
            ("장기 휴양 최대 기간", self.long_rest_max, "개월 (1~127, 원본 12)"),
            ("탐험 준비 기간", self.exploration_days, "일 (1~127, 원본 10)"),
            ("세대교체 가능 나이", self.succession_age, "세 (1~127, 원본 18)"),
        )
        for row, (label, variable, suffix) in enumerate(gameplay_rows):
            ttk.Label(gameplay_box, text=f"{label}:").grid(row=row, column=0, pady=2, sticky="w")
            ttk.Entry(gameplay_box, textvariable=variable, width=8).grid(row=row, column=1, padx=(6, 0), pady=2, sticky="w")
            ttk.Label(gameplay_box, text=suffix).grid(row=row, column=2, padx=(5, 0), pady=2, sticky="w")
        ttk.Label(gameplay_box, text="인물 활동 가능 나이:").grid(row=3, column=0, pady=2, sticky="w")
        activity_age_frame = ttk.Frame(gameplay_box)
        activity_age_frame.grid(row=3, column=1, columnspan=2, padx=(6, 0), pady=2, sticky="w")
        ttk.Entry(activity_age_frame, textvariable=self.npc_activity_min_age, width=6).grid(row=0, column=0)
        ttk.Label(activity_age_frame, text="~").grid(row=0, column=1, padx=5)
        ttk.Entry(activity_age_frame, textvariable=self.npc_activity_max_age, width=6).grid(row=0, column=2)
        ttk.Label(activity_age_frame, text="세 (0~127, 원본 18~60)").grid(row=0, column=3, padx=(5, 0))

        encounter_box = ttk.LabelFrame(right_column, text="전투 인카운트", padding=10)
        encounter_box.grid(row=1, column=0, pady=(10, 0), sticky="ew")
        ttk.Label(encounter_box, text="서부 해역 (해적·추격대): 1 /").grid(row=0, column=0, sticky="w")
        ttk.Entry(encounter_box, textvariable=self.western_encounter_denominator, width=7).grid(row=0, column=1, padx=(4, 0), sticky="w")
        ttk.Label(encounter_box, text="(원본 700)").grid(row=0, column=2, padx=(5, 0), sticky="w")
        ttk.Label(encounter_box, text="동부 해역 (이슬람 함대): 1 /").grid(row=1, column=0, pady=(5, 0), sticky="w")
        ttk.Entry(encounter_box, textvariable=self.islamic_encounter_denominator, width=7).grid(row=1, column=1, padx=(4, 0), pady=(5, 0), sticky="w")
        ttk.Label(encounter_box, text="(원본 400)").grid(row=1, column=2, padx=(5, 0), pady=(5, 0), sticky="w")
        ttk.Label(encounter_box, text="분모 1~32,768: 값이 작을수록 자주 발생합니다.").grid(
            row=2, column=0, columnspan=3, pady=(5, 0), sticky="w",
        )

        pirate_box = ttk.LabelFrame(right_column, text="명성별 해적 확장", padding=10)
        pirate_box.grid(row=2, column=0, pady=(10, 0), sticky="ew")
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
        ttk.Label(fame_range, text="—").grid(row=0, column=3, padx=6)
        self.pirate_fame_high_entry = ttk.Entry(fame_range, textvariable=self.pirate_fame_high, width=8)
        self.pirate_fame_high_entry.grid(row=0, column=4)

        ttk.Label(pirate_box, text="추격대 단계 경계:").grid(row=2, column=0, pady=2, sticky="w")
        pursuit_range = ttk.Frame(pirate_box)
        pursuit_range.grid(row=2, column=1, columnspan=5, padx=(6, 0), pady=2, sticky="w")
        ttk.Label(pursuit_range, text="0").grid(row=0, column=0)
        ttk.Label(pursuit_range, text="—").grid(row=0, column=1, padx=6)
        self.pirate_pursuit_middle_entry = ttk.Entry(
            pursuit_range, textvariable=self.pirate_pursuit_middle_threshold, width=8,
        )
        self.pirate_pursuit_middle_entry.grid(row=0, column=2)
        ttk.Label(pursuit_range, text="—").grid(row=0, column=3, padx=6)
        self.pirate_pursuit_high_entry = ttk.Entry(
            pursuit_range, textvariable=self.pirate_pursuit_high_threshold, width=8,
        )
        self.pirate_pursuit_high_entry.grid(row=0, column=4)

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

        latitude_box = ttk.LabelFrame(left_column, text="위도 경계", padding=10)
        latitude_box.grid(row=3, column=0, pady=(10, 0), sticky="ew")
        ttk.Label(latitude_box, text="북쪽 추위 전멸 경계:").grid(row=0, column=0, pady=2, sticky="w")
        self.cold_north_entry = ttk.Entry(latitude_box, textvariable=self.cold_north_latitude, width=8)
        self.cold_north_entry.grid(row=0, column=1, padx=(6, 0), pady=2, sticky="w")
        ttk.Label(latitude_box, text="°N (원본 76.995°N)").grid(row=0, column=2, padx=(5, 0), pady=2, sticky="w")
        ttk.Checkbutton(
            latitude_box, text="제한 해제", variable=self.cold_north_unlocked,
            command=self._update_cold_limit_entry_states,
        ).grid(row=0, column=3, padx=(10, 0), pady=2, sticky="w")

        ttk.Label(latitude_box, text="남쪽 추위 전멸 경계:").grid(row=1, column=0, pady=2, sticky="w")
        self.cold_south_entry = ttk.Entry(latitude_box, textvariable=self.cold_south_latitude, width=8)
        self.cold_south_entry.grid(row=1, column=1, padx=(6, 0), pady=2, sticky="w")
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

        translation_box = ttk.LabelFrame(left_column, text="오역 수정", padding=10)
        translation_box.grid(row=4, column=0, pady=(10, 0), sticky="ew")
        ttk.Checkbutton(
            translation_box,
            text="용어·지명·아이템명·인명·힌트 오역 수정 적용",
            variable=self.mistranslation_fixes_enabled,
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            translation_box,
            text="수정 내역…",
            command=self.show_mistranslation_details,
        ).grid(row=0, column=1, padx=(10, 0), sticky="e")
    def show_mistranslation_details(self) -> None:
        window = tk.Toplevel(self)
        window.title("오역 수정 내역")
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
        text.insert("1.0", MISTRANSLATION_DETAILS)
        text.tag_add("credit", "1.0", "1.end")
        text.tag_configure("credit", foreground="#1A73E8")
        text.configure(state=tk.DISABLED)
        ttk.Button(window, text="닫기", command=window.destroy).pack(pady=(0, 10))
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
        selected = filedialog.askopenfilename(title="대항해시대 III 실행 파일 선택", filetypes=(("실행 파일", "*.exe"), ("모든 파일", "*.*")))
        if selected:
            target = Path(selected)
            try:
                (
                    coordinate, presets, departure, arrival_wait,
                    long_rest_max, exploration_days, succession_age,
                    cold_north_limit, cold_south_limit,
                    npc_activity_min_age, npc_activity_max_age,
                    western_encounter_denominator, islamic_encounter_denominator,
                    pirate_variety_enabled,
                    pirate_settings,
                    eclipse_enabled,
                    eclipse_latitude,
                    mistranslation_fixes_enabled,
                ) = read_settings(target)
            except Exception as exc:
                messagebox.showerror("EXE 읽기 실패", str(exc))
                return
            self.path.set(selected)
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

    def apply(self) -> None:
        if not self.path.get():
            messagebox.showwarning("실행 파일 필요", "패치할 EXE 파일을 선택해 주세요.")
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
            backup = apply_all(
                Path(self.path.get()), self.coordinate.get(), True, presets,
                int(self.departure.get()), int(self.arrival_wait.get()),
                int(self.long_rest_max.get()), int(self.exploration_days.get()),
                int(self.succession_age.get()), cold_north_limit,
                cold_south_limit,
                int(self.npc_activity_min_age.get()), int(self.npc_activity_max_age.get()),
                int(self.western_encounter_denominator.get()),
                int(self.islamic_encounter_denominator.get()),
                pirate_variety_enabled,
                pirate_settings,
                self.eclipse_enabled.get(),
                self.eclipse_latitude.get(),
                self.mistranslation_fixes_enabled.get(),
            )
            self.cold_north_latitude.set(f"{cold_limit_to_latitude(cold_north_limit):.3f}")
            self.cold_south_latitude.set(f"{cold_limit_to_latitude(cold_south_limit):.3f}")
        except ValueError as exc:
            messagebox.showerror("입력 또는 패치 오류", str(exc))
            return
        except Exception as exc:
            messagebox.showerror("패치 실패", str(exc))
            return
        if backup is None:
            messagebox.showinfo("완료", "선택한 설정이 이미 적용되어 있습니다.")
        else:
            messagebox.showinfo("완료", f"선택한 설정을 적용했습니다.\n\n원본 백업:\n{backup}")


if __name__ == "__main__":
    CDSExecutablePatcher().mainloop()
