"""Read the EXE hint master and every shared-ID discovery target."""

from __future__ import annotations

import struct
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pefile


DISCOVERY_MASTER_TABLE_VA = 0x51C540
DISCOVERY_MASTER_RECORD_COUNT = 274
DISCOVERY_MASTER_RECORD_SIZE = 0x5C
DISCOVERY_NAME_POINTER_OFFSET = 0x00
DISCOVERY_TARGET_ID_OFFSET = 0x08
HINT_MASTER_TABLE_VA = 0x4D8E88
HINT_MASTER_RECORD_COUNT = 186
HINT_MASTER_RECORD_SIZE = 0x50
HINT_NAME_POINTER_OFFSET = -0x08
HINT_TARGET_ID_OFFSET = 0x00
HINT_REQUIRED_SKILL_OFFSET = 0x18
HINT_REQUIRED_LANGUAGE_OFFSET = 0x1C
HINT_REQUIRED_LEVEL_OFFSET = 0x20
HINT_PREREQUISITE_DISCOVERY_LIST_OFFSET = 0x28
HINT_PREREQUISITE_DISCOVERY_CAPACITY = 8
BOOK_TABLE_VA = 0x4C4748
BOOK_RECORD_COUNT = 257
BOOK_RECORD_SIZE = 0x58
BOOK_TITLE_POINTER_OFFSET = 0x00
BOOK_AUTHOR_POINTER_OFFSET = 0x04
BOOK_CITY_LIST_OFFSET = 0x18
BOOK_HINT_LIST_OFFSET = 0x38
BOOK_LIST_CAPACITY = 8
ITEM_TABLE_VA = 0x4FD558
ITEM_RECORD_COUNT = 286
ITEM_RECORD_SIZE = 0x1C
ITEM_NAME_POINTER_OFFSET = 0x00
ITEM_CATEGORY_OFFSET = 0x14
ITEM_HINT_ID_OFFSET = 0x18
ITEM_BOOK_CATEGORY = 7


@dataclass(frozen=True)
class DiscoveryHintTarget:
    """One discovery-master row sharing a hint's internal target ID."""

    record_number: int
    name: str
    kind: str


@dataclass(frozen=True)
class DiscoveryHintBookSource:
    """One EXE book-table row that grants a hint."""

    record_number: int
    title: str
    author: str
    city_ids: tuple[int, ...]


@dataclass(frozen=True)
class DiscoveryHintItemSource:
    """One inventory book item that grants a hint when used."""

    record_number: int
    name: str


@dataclass(frozen=True)
class DiscoveryHintLink:
    """One hint-master row, its requirements, sources, and target rows."""

    hint_id: int
    hint_name: str
    target_id: int
    required_skill_id: int
    required_language_id: int
    required_level: int
    prerequisite_discoveries: tuple[DiscoveryHintTarget, ...]
    targets: tuple[DiscoveryHintTarget, ...]
    book_sources: tuple[DiscoveryHintBookSource, ...]
    item_sources: tuple[DiscoveryHintItemSource, ...]

    @property
    def identifier(self) -> str:
        return str(self.hint_id)


def _record_kind(record_number: int) -> str:
    if record_number < 230 or record_number == 273:
        return "발견물"
    if record_number < 258:
        return "모조품"
    return "기타"


def _read_cp949_text(
    data: bytes,
    pe: pefile.PE,
    pointer_offset: int,
    description: str,
) -> str:
    name_va = struct.unpack_from("<I", data, pointer_offset)[0]
    try:
        name_offset = pe.get_offset_from_rva(name_va - pe.OPTIONAL_HEADER.ImageBase)
    except pefile.PEFormatError as error:
        raise ValueError(f"{description} 주소를 검증하지 못했습니다.") from error
    name_end = data.find(b"\0", name_offset, min(name_offset + 80, len(data)))
    if not 0 <= name_offset < len(data) or name_end < 0:
        raise ValueError(f"{description} 주소를 검증하지 못했습니다.")
    try:
        name = data[name_offset:name_end].decode("cp949")
    except UnicodeDecodeError as error:
        raise ValueError(f"{description}을 읽지 못했습니다.") from error
    if not name:
        raise ValueError(f"{description}이 비어 있습니다.")
    return name


def _read_discovery_hint_targets_from_data(
    data: bytes,
    pe: pefile.PE,
) -> tuple[DiscoveryHintTarget, ...]:
    discovery_offset = pe.get_offset_from_rva(
        DISCOVERY_MASTER_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
    )
    discovery_size = DISCOVERY_MASTER_RECORD_COUNT * DISCOVERY_MASTER_RECORD_SIZE
    if discovery_offset < 0 or discovery_offset + discovery_size > len(data):
        raise ValueError("발견물 마스터 테이블의 범위를 검증하지 못했습니다.")
    targets: list[DiscoveryHintTarget] = []
    for record_number in range(DISCOVERY_MASTER_RECORD_COUNT):
        record_offset = discovery_offset + record_number * DISCOVERY_MASTER_RECORD_SIZE
        name = _read_cp949_text(
            data,
            pe,
            record_offset + DISCOVERY_NAME_POINTER_OFFSET,
            f"발견물 마스터 {record_number}번 이름",
        )
        targets.append(DiscoveryHintTarget(
            record_number, name, _record_kind(record_number),
        ))
    return tuple(targets)


def read_discovery_hint_targets(target: Path) -> tuple[DiscoveryHintTarget, ...]:
    """Read every discovery-master row available to prerequisite slots."""
    data = target.resolve(strict=True).read_bytes()
    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        return _read_discovery_hint_targets_from_data(data, pe)
    finally:
        pe.close()


def read_discovery_hint_links(
    target: Path,
    data_path: Path | None = None,
) -> tuple[DiscoveryHintLink, ...]:
    """Join each of the 186 EXE hints to every row with the same target ID.

    The direct relationship is the shared integer stored at discovery master
    ``+0x08`` and hint master ``+0x00``.  This intentionally keeps ordinary
    discoveries, fake market items, and other matching rows under the same
    hint while retaining their individual record numbers and kinds.
    """

    # ``data_path`` is retained for call compatibility with earlier builds.
    # Names are now read from the executable's real pointer field.
    _ = data_path
    data = target.resolve(strict=True).read_bytes()
    pe = pefile.PE(data=data, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x14C or pe.OPTIONAL_HEADER.ImageBase != 0x400000:
            raise ValueError("지원하는 32비트 CDS III 실행 파일이 아닙니다.")
        discovery_offset = pe.get_offset_from_rva(
            DISCOVERY_MASTER_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
        hint_offset = pe.get_offset_from_rva(
            HINT_MASTER_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
        book_offset = pe.get_offset_from_rva(
            BOOK_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
        item_offset = pe.get_offset_from_rva(
            ITEM_TABLE_VA - pe.OPTIONAL_HEADER.ImageBase
        )
        hint_size = HINT_MASTER_RECORD_COUNT * HINT_MASTER_RECORD_SIZE
        book_size = BOOK_RECORD_COUNT * BOOK_RECORD_SIZE
        item_size = ITEM_RECORD_COUNT * ITEM_RECORD_SIZE
        if hint_offset < 0 or hint_offset + hint_size > len(data):
            raise ValueError("힌트 마스터 테이블의 범위를 검증하지 못했습니다.")
        if book_offset < 0 or book_offset + book_size > len(data):
            raise ValueError("도서관 책 테이블의 범위를 검증하지 못했습니다.")
        if item_offset < 0 or item_offset + item_size > len(data):
            raise ValueError("아이템 테이블의 범위를 검증하지 못했습니다.")

        targets_by_id: defaultdict[int, list[DiscoveryHintTarget]] = defaultdict(list)
        all_targets = _read_discovery_hint_targets_from_data(data, pe)
        targets_by_record_number = {
            target.record_number: target for target in all_targets
        }
        for target in all_targets:
            record_number = target.record_number
            record_offset = discovery_offset + record_number * DISCOVERY_MASTER_RECORD_SIZE
            target_id = struct.unpack_from(
                "<I", data, record_offset + DISCOVERY_TARGET_ID_OFFSET,
            )[0]
            targets_by_id[target_id].append(target)

        books_by_hint: defaultdict[int, list[DiscoveryHintBookSource]] = defaultdict(list)
        for record_number in range(BOOK_RECORD_COUNT):
            record_offset = book_offset + record_number * BOOK_RECORD_SIZE
            title = _read_cp949_text(
                data, pe, record_offset + BOOK_TITLE_POINTER_OFFSET,
                f"도서관 책 {record_number}번 제목",
            )
            author = _read_cp949_text(
                data, pe, record_offset + BOOK_AUTHOR_POINTER_OFFSET,
                f"도서관 책 {record_number}번 저자",
            )
            city_ids = tuple(
                city_id
                for index in range(BOOK_LIST_CAPACITY)
                if (city_id := struct.unpack_from(
                    "<i", data, record_offset + BOOK_CITY_LIST_OFFSET + index * 4,
                )[0]) >= 0
            )
            hint_ids = tuple(
                hint_id
                for index in range(BOOK_LIST_CAPACITY)
                if (hint_id := struct.unpack_from(
                    "<i", data, record_offset + BOOK_HINT_LIST_OFFSET + index * 4,
                )[0]) >= 0
            )
            source = DiscoveryHintBookSource(
                record_number, title, author, city_ids,
            )
            for hint_id in hint_ids:
                if hint_id >= HINT_MASTER_RECORD_COUNT:
                    raise ValueError(
                        f"도서관 책 {record_number}번의 힌트 ID {hint_id}가 범위를 벗어났습니다."
                    )
                books_by_hint[hint_id].append(source)

        items_by_hint: defaultdict[int, list[DiscoveryHintItemSource]] = defaultdict(list)
        for record_number in range(ITEM_RECORD_COUNT):
            record_offset = item_offset + record_number * ITEM_RECORD_SIZE
            category = struct.unpack_from(
                "<I", data, record_offset + ITEM_CATEGORY_OFFSET,
            )[0]
            if category != ITEM_BOOK_CATEGORY:
                continue
            hint_id = struct.unpack_from(
                "<i", data, record_offset + ITEM_HINT_ID_OFFSET,
            )[0]
            if hint_id < 0:
                continue
            if hint_id >= HINT_MASTER_RECORD_COUNT:
                raise ValueError(
                    f"서적 아이템 {record_number}번의 힌트 ID {hint_id}가 범위를 벗어났습니다."
                )
            name = _read_cp949_text(
                data, pe, record_offset + ITEM_NAME_POINTER_OFFSET,
                f"서적 아이템 {record_number}번 이름",
            )
            items_by_hint[hint_id].append(DiscoveryHintItemSource(record_number, name))

        links: list[DiscoveryHintLink] = []
        for hint_id in range(HINT_MASTER_RECORD_COUNT):
            record_offset = hint_offset + hint_id * HINT_MASTER_RECORD_SIZE
            hint_name = _read_cp949_text(
                data,
                pe,
                record_offset + HINT_NAME_POINTER_OFFSET,
                f"힌트 {hint_id}번 이름",
            )
            target_id = struct.unpack_from(
                "<I", data, record_offset + HINT_TARGET_ID_OFFSET,
            )[0]
            required_skill_id = struct.unpack_from(
                "<i", data, record_offset + HINT_REQUIRED_SKILL_OFFSET,
            )[0]
            required_language_id = struct.unpack_from(
                "<i", data, record_offset + HINT_REQUIRED_LANGUAGE_OFFSET,
            )[0]
            required_level = struct.unpack_from(
                "<i", data, record_offset + HINT_REQUIRED_LEVEL_OFFSET,
            )[0]
            prerequisite_discovery_ids = tuple(
                prerequisite_id
                for index in range(HINT_PREREQUISITE_DISCOVERY_CAPACITY)
                if (prerequisite_id := struct.unpack_from(
                    "<i",
                    data,
                    record_offset
                    + HINT_PREREQUISITE_DISCOVERY_LIST_OFFSET
                    + index * 4,
                )[0]) >= 0
            )
            try:
                prerequisite_discoveries = tuple(
                    targets_by_record_number[prerequisite_id]
                    for prerequisite_id in prerequisite_discovery_ids
                )
            except KeyError as error:
                raise ValueError(
                    f"힌트 {hint_id}번의 선행 발견물 번호 {error.args[0]}가 범위를 벗어났습니다."
            ) from error
            targets = tuple(targets_by_id.get(target_id, ()))
            links.append(DiscoveryHintLink(
                hint_id, hint_name, target_id,
                required_skill_id, required_language_id, required_level,
                prerequisite_discoveries, targets,
                tuple(books_by_hint.get(hint_id, ())),
                tuple(items_by_hint.get(hint_id, ())),
            ))
        return tuple(links)
    finally:
        pe.close()
