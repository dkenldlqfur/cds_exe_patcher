"""Verify map-only city labels and removal of legacy extra notices."""

from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from city_discovery_notice_patch import (  # noqa: E402
    COMMON_POPUP_VA,
    HOOKS,
    LEGACY_SLOT_SIZE,
    MAP_HOOK_VA,
    MAP_ORIGINAL,
    MAP_RESUME_VA,
    MAP_TEXT_VA,
    PIXEL_TEXT_VA,
    NATION_TABLE_VA,
    NOTICE_FORMAT,
    SPEECH_POPUP_VA,
    _call,
    _popup_payload,
    _payload,
    _map_hook,
    apply_city_discovery_notice_patch,
    read_city_discovery_notice_patch_state,
)
from pe_patch_section import (  # noqa: E402
    CITY_DISCOVERY_NOTICE_SLOT_OFFSET,
    CITY_DISCOVERY_NOTICE_SLOT_SIZE,
    PATCH_SECTION_CITY_DISCOVERY_NOTICE_SIZE,
    ensure_patch_section,
    find_patch_section,
)

try:
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
    from unicorn.x86_const import (
        UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX,
        UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP,
        UC_X86_REG_EIP, UC_X86_REG_EFLAGS,
    )
except ImportError:
    Uc = None


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"


def _offsets(data):
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return [pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase) for va, _, _, _ in HOOKS]
    finally:
        pe.close()


class CityDiscoveryNoticePatchTests(unittest.TestCase):
    def test_integrated_save_reload_coordinate_change_and_disable(self):
        from patch_cds_integrated import apply_all, read_settings

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "CDS_95.EXE"
            shutil.copyfile(FIXTURE, target)
            for style, enabled in ((None, True), ("korean3", True), ("original", False)):
                settings = read_settings(target)
                apply_all(
                    target, settings[0] if style is None else style,
                    True, settings[1], *settings[2:],
                    city_discovery_notice_enabled=enabled,
                )
                self.assertEqual(
                    read_city_discovery_notice_patch_state(target.read_bytes()), enabled,
                )

    def test_applies_once_and_restores_only_discovery_and_map_hooks(self):
        original = FIXTURE.read_bytes()
        data = bytearray(original)
        self.assertFalse(read_city_discovery_notice_patch_state(data))
        self.assertTrue(apply_city_discovery_notice_patch(data, True))
        self.assertTrue(read_city_discovery_notice_patch_state(data))
        for off, (hook, target, _, _) in zip(_offsets(data), HOOKS):
            self.assertEqual(data[off:off + 5], _call(hook, target))
        enabled = bytes(data)
        self.assertFalse(apply_city_discovery_notice_patch(data, True))
        self.assertEqual(bytes(data), enabled)
        section = find_patch_section(data)
        self.assertIsNotNone(section)
        permitted = {k for off in _offsets(original) for k in range(off, off + 5)}
        pe = pefile.PE(data=original, fast_load=True)
        try:
            map_offset = pe.get_offset_from_rva(MAP_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase)
            permitted.update(range(map_offset, map_offset + len(MAP_ORIGINAL)))
            text_section = next(s for s in pe.sections if s.Name.rstrip(b"\0") == b".text")
            start = text_section.PointerToRawData
            end = start + text_section.SizeOfRawData
            changed = {k for k in range(start, end) if original[k] != data[k]}
        finally:
            pe.close()
        self.assertTrue(changed)
        self.assertTrue(changed <= permitted)
        self.assertTrue(apply_city_discovery_notice_patch(data, False))
        self.assertFalse(read_city_discovery_notice_patch_state(data))
        for off in _offsets(data):
            self.assertEqual(data[off:off + 5], original[off:off + 5])
        self.assertEqual(data[map_offset:map_offset + len(MAP_ORIGINAL)], MAP_ORIGINAL)
        off, _ = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
        self.assertFalse(any(data[off:off + CITY_DISCOVERY_NOTICE_SLOT_SIZE]))
        self.assertFalse(apply_city_discovery_notice_patch(data, False))

    def test_upgrades_and_restores_legacy_popup_only_patch(self):
        data = bytearray(FIXTURE.read_bytes())
        section, _ = ensure_patch_section(data, CITY_DISCOVERY_NOTICE_SLOT_OFFSET + LEGACY_SLOT_SIZE)
        offset, va = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, LEGACY_SLOT_SIZE)
        data[offset:offset + LEGACY_SLOT_SIZE] = _popup_payload(va, 1)
        for off, (hook, _, wrapper, _) in zip(_offsets(data), HOOKS):
            data[off:off + 5] = _call(hook, va + wrapper)
        self.assertTrue(read_city_discovery_notice_patch_state(data))
        legacy = bytes(data)
        self.assertTrue(apply_city_discovery_notice_patch(data, True))
        self.assertTrue(read_city_discovery_notice_patch_state(data))
        self.assertFalse(apply_city_discovery_notice_patch(data, True))
        self.assertTrue(apply_city_discovery_notice_patch(data, False))
        self.assertFalse(read_city_discovery_notice_patch_state(data))
        data = bytearray(legacy)
        self.assertTrue(apply_city_discovery_notice_patch(data, False))
        self.assertFalse(read_city_discovery_notice_patch_state(data))

    def test_rejects_partial_and_corrupted_patches(self):
        data = bytearray(FIXTURE.read_bytes())
        apply_city_discovery_notice_patch(data, True)
        off = _offsets(data)[0]
        va, target, _, _ = HOOKS[0]
        data[off:off + 5] = _call(va, target + 1)
        with self.assertRaises(ValueError):
            read_city_discovery_notice_patch_state(data)

    def test_migrates_v2_popup_and_map_patch_to_map_only(self):
        from high_speed_map_patch import apply_high_speed_map_fix, read_high_speed_map_fix_state
        for enable in (True, False):
            with self.subTest(enable=enable):
                data = bytearray(FIXTURE.read_bytes())
                section, _ = ensure_patch_section(data, PATCH_SECTION_CITY_DISCOVERY_NOTICE_SIZE)
                offset, va = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
                data[offset:offset + CITY_DISCOVERY_NOTICE_SLOT_SIZE] = _payload(va, 2)
                for off, (hook, _, wrapper, _) in zip(_offsets(data), HOOKS):
                    data[off:off + 5] = _call(hook, va + wrapper)
                pe = pefile.PE(data=bytes(data))
                try:
                    map_offset = pe.get_offset_from_rva(MAP_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase)
                finally:
                    pe.close()
                data[map_offset:map_offset + len(MAP_ORIGINAL)] = _map_hook(va)
                apply_high_speed_map_fix(data, True)
                self.assertTrue(read_city_discovery_notice_patch_state(data))
                self.assertTrue(apply_city_discovery_notice_patch(data, enable))
                self.assertEqual(read_city_discovery_notice_patch_state(data), enable)
                self.assertTrue(read_high_speed_map_fix_state(data))
                for off, (hook, target, _, _) in zip(_offsets(data), HOOKS):
                    self.assertEqual(data[off:off + 5], _call(hook, target))
                self.assertNotIn(NOTICE_FORMAT, data[offset:offset + CITY_DISCOVERY_NOTICE_SLOT_SIZE])
                self.assertEqual(data[map_offset:map_offset + len(MAP_ORIGINAL)], _map_hook(va) if enable else MAP_ORIGINAL)
                self.assertFalse(apply_city_discovery_notice_patch(data, enable))

    def test_upgrades_v3_map_only_patch_without_extra_popup(self):
        data = bytearray(FIXTURE.read_bytes())
        section, _ = ensure_patch_section(data, PATCH_SECTION_CITY_DISCOVERY_NOTICE_SIZE)
        off, va = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
        data[off:off + CITY_DISCOVERY_NOTICE_SLOT_SIZE] = _payload(va, 3)
        pe = pefile.PE(data=bytes(data))
        try:
            map_offset = pe.get_offset_from_rva(MAP_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase)
        finally:
            pe.close()
        data[map_offset:map_offset + len(MAP_ORIGINAL)] = _map_hook(va)
        self.assertTrue(read_city_discovery_notice_patch_state(data))
        self.assertTrue(apply_city_discovery_notice_patch(data, True))
        self.assertEqual(data[off:off + CITY_DISCOVERY_NOTICE_SLOT_SIZE], _payload(va))
        for offset, (hook, target, _, _) in zip(_offsets(data), HOOKS):
            self.assertEqual(data[offset:offset + 5], _call(hook, target))

    def test_rejects_original_map_hook_with_payload_and_corrupted_payload(self):
        data = bytearray(FIXTURE.read_bytes())
        apply_city_discovery_notice_patch(data, True)
        pe = pefile.PE(data=bytes(data), fast_load=True)
        try:
            map_offset = pe.get_offset_from_rva(MAP_HOOK_VA - pe.OPTIONAL_HEADER.ImageBase)
        finally:
            pe.close()
        data[map_offset:map_offset + len(MAP_ORIGINAL)] = MAP_ORIGINAL
        with self.assertRaises(ValueError):
            read_city_discovery_notice_patch_state(data)
        data = bytearray(FIXTURE.read_bytes())
        apply_city_discovery_notice_patch(data, True)
        section = find_patch_section(data)
        off, _ = section.slot(CITY_DISCOVERY_NOTICE_SLOT_OFFSET, CITY_DISCOVERY_NOTICE_SLOT_SIZE)
        data[off + 0xA0] ^= 1
        with self.assertRaises(ValueError):
            read_city_discovery_notice_patch_state(data)

    def test_keeps_preexisting_patch_payloads(self):
        data = bytearray(FIXTURE.read_bytes())
        section, _ = ensure_patch_section(data, PATCH_SECTION_CITY_DISCOVERY_NOTICE_SIZE)
        marker = b"unrelated feature"
        off, _ = section.slot(0x4F200, len(marker))
        data[off:off + len(marker)] = marker
        for enabled in (True, False, True):
            apply_city_discovery_notice_patch(data, enabled)
            self.assertEqual(data[off:off + len(marker)], marker)


@unittest.skipIf(Uc is None, "Install unicorn to execute the x86 discovery branches")
class CityDiscoveryNoticeExecutionTests(unittest.TestCase):
    def _run(self, speaker, city_id=0, name=None, no_discovery=False):
        data = bytearray(FIXTURE.read_bytes())
        apply_city_discovery_notice_patch(data, True)
        pe = pefile.PE(data=bytes(data))
        try:
            mapped = pe.get_memory_mapped_image()
            base = pe.OPTIONAL_HEADER.ImageBase
            size = (pe.OPTIONAL_HEADER.SizeOfImage + 0xFFF) & ~0xFFF
        finally:
            pe.close()
        machine = Uc(UC_ARCH_X86, UC_MODE_32)
        machine.mem_map(base, size)
        machine.mem_write(base, mapped)
        stack_base = 0x10000000
        machine.mem_map(stack_base, 0x20000)
        initial_sp = stack_base + 0x18000
        if name is not None:
            name_va = stack_base + 0x100
            machine.mem_write(name_va, name.encode("cp949") + b"\0")
            machine.mem_write(0x4D14B0 + city_id * 0x88, struct.pack("<I", name_va))
        registers = {
            UC_X86_REG_EBX: 0x11112222,
            UC_X86_REG_ECX: 0x33334444,
            UC_X86_REG_EDX: 0x55556666,
            UC_X86_REG_ESI: 0x77778888,
            UC_X86_REG_EDI: 0x12345678,
            UC_X86_REG_EBP: city_id,
        }
        for reg, value in registers.items():
            machine.reg_write(reg, value)
        machine.reg_write(UC_X86_REG_ESP, initial_sp)
        machine.reg_write(UC_X86_REG_EAX, 0 if speaker else 0xFFFFFFFF)
        machine.reg_write(UC_X86_REG_EFLAGS, 0x202)
        messages = []
        original_result = 0x55667788
        actor = 0x586F00

        def words(address, count):
            return struct.unpack(f"<{count}I", machine.mem_read(address, count * 4))

        def text(address):
            result = bytearray()
            while True:
                value = machine.mem_read(address, 1)
                if value == b"\0":
                    return result.decode("cp949")
                result += value
                address += 1

        def returned(result, popped=0):
            sp = machine.reg_read(UC_X86_REG_ESP)
            return_address = words(sp, 1)[0]
            machine.reg_write(UC_X86_REG_EAX, result)
            machine.reg_write(UC_X86_REG_ESP, sp + 4 + popped)
            machine.reg_write(UC_X86_REG_EIP, return_address)

        def intercept(uc, address, size, context):
            if address == 0x48DA19:
                uc.emu_stop()
                return
            sp = uc.reg_read(UC_X86_REG_ESP)
            if address == 0x47CC60:
                self.assertEqual(words(sp + 4, 2), (0, 1))
                returned(actor, 8)
            elif address == SPEECH_POPUP_VA:
                args = words(sp + 4, 4)
                self.assertEqual(args[:3], (actor, 0, 0))
                messages.append(text(args[3]))
                returned(original_result)
            elif address == COMMON_POPUP_VA:
                args = words(sp + 4, 3)
                self.assertEqual(args[:2], (0, 0))
                fmt = text(args[2])
                self.assertEqual(fmt, "도시를 발견했습니다!")
                messages.append(fmt)
                returned(original_result)

        machine.hook_add(UC_HOOK_CODE, intercept)
        machine.emu_start(0x48D97E if no_discovery else 0x48D9E1, 0x48DA29, count=30000)
        self.assertEqual(machine.reg_read(UC_X86_REG_EIP), 0x48DA19)
        self.assertEqual(machine.reg_read(UC_X86_REG_ESP), initial_sp)
        for reg in (UC_X86_REG_EBX, UC_X86_REG_EDX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP):
            self.assertEqual(machine.reg_read(reg), registers[reg])
        if not no_discovery:
            self.assertEqual(machine.reg_read(UC_X86_REG_EAX), original_result)
        return messages

    def test_only_original_speech_is_shown(self):
        self.assertEqual(self._run(True), ["제독, 도시가 보입니다!"])

    def test_only_original_generic_notice_is_shown(self):
        self.assertEqual(self._run(False, 14), ["도시를 발견했습니다!"])

    def test_renamed_city_does_not_add_a_popup(self):
        self.assertEqual(self._run(True, name="새도시"), ["제독, 도시가 보입니다!"])

    def test_no_new_city_shows_no_popup(self):
        self.assertEqual(self._run(False, no_discovery=True), [])


@unittest.skipIf(Uc is None, "Install unicorn to execute the x86 map label hook")
class CityMapLabelExecutionTests(unittest.TestCase):
    def _run_map(self, cities, origin=(90, 190), dimensions=(40, 20), nations=None):
        data = bytearray(FIXTURE.read_bytes())
        apply_city_discovery_notice_patch(data, True)
        pe = pefile.PE(data=bytes(data))
        try:
            mapped = pe.get_memory_mapped_image()
            base = pe.OPTIONAL_HEADER.ImageBase
            size = (pe.OPTIONAL_HEADER.SizeOfImage + 0xFFF) & ~0xFFF
        finally:
            pe.close()
        uc = Uc(UC_ARCH_X86, UC_MODE_32)
        uc.mem_map(base, size)
        uc.mem_write(base, mapped)
        memory = 0x10000000
        uc.mem_map(memory, 0x30000)
        widget, cache, initial_sp = memory + 0x8000, memory + 0xA000, memory + 0x28000
        width, height = dimensions

        def write_words(address, *values):
            uc.mem_write(address, struct.pack(f"<{len(values)}I", *values))

        def words(address, count):
            return struct.unpack(f"<{count}I", uc.mem_read(address, count * 4))

        def text(address):
            result = bytearray()
            while uc.mem_read(address, 1) != b"\0":
                result += uc.mem_read(address, 1)
                address += 1
            return result.decode("cp949")

        write_words(widget + 0xEC, width, height)
        write_words(widget + 0x28, 12, 16)
        write_words(widget + 0x54, 4, 32)
        write_words(widget + 0xC8, width * height * 2)
        write_words(widget + 0xD0, cache)
        uc.mem_write(widget + 0xD4, b"\x04")  # byte buffer underlying u16 cache
        uc.mem_write(cache - 16, b"\xA5" * (max(width * height * 2, 0) + 32))
        write_words(0x5B63A8, *origin)
        write_words(0x62B2C8, 111, 222)  # previous global drawing origin
        for identifier in range(226):
            uc.mem_write(0x5863A8 + identifier * 0x5C + 4, b"\0\0")
        for identifier, city in enumerate(cities):
            x, y, flags, name, footprint = city[:5]
            nation_id = city[5] if len(city) > 5 else -1
            runtime = 0x5863A8 + identifier * 0x5C
            write_words(runtime, nation_id & 0xFFFFFFFF)
            uc.mem_write(runtime + 4, struct.pack("<H", flags))
            master = 0x4D14B0 + identifier * 0x88
            name_pointer = memory + 0x100 + identifier * 0x100
            uc.mem_write(name_pointer, name.encode("cp949") + b"\0")
            write_words(master, name_pointer, x, y, footprint)
            write_words(master + 0x24, 1)  # deliberately differs from current nation
        for identifier, name in (nations or {}).items():
            pointer = memory + 0x20000 + identifier * 0x100
            uc.mem_write(pointer, name.encode("cp949") + b"\0")
            write_words(NATION_TABLE_VA + identifier * 24, pointer)
        registers = {
            UC_X86_REG_EAX: 0x10203040,
            UC_X86_REG_EBX: 0x12345678,
            UC_X86_REG_EDX: 0xABCDEF00,
            UC_X86_REG_ESI: 0x33445566,
            UC_X86_REG_EDI: 0x77889900,
            UC_X86_REG_EBP: 0xDEADBEEF,
        }
        for register, value in registers.items():
            uc.reg_write(register, value)
        uc.reg_write(UC_X86_REG_ESP, initial_sp)
        uc.reg_write(UC_X86_REG_EFLAGS, 0x202)
        write_words(initial_sp + 0x14, widget)
        labels, pixels = [], []

        def returned(popped):
            sp = uc.reg_read(UC_X86_REG_ESP)
            uc.reg_write(UC_X86_REG_EIP, words(sp, 1)[0])
            uc.reg_write(UC_X86_REG_ESP, sp + 4 + popped)

        def intercept(machine, address, size, context):
            sp = machine.reg_read(UC_X86_REG_ESP)
            if address == MAP_RESUME_VA:
                machine.emu_stop()
            elif address == PIXEL_TEXT_VA:
                x, y, name, color = words(sp + 4, 4)
                self.assertEqual(color, 10)
                labels.append((x, y, text(name)))
                # Execute the real outlined text function. Only the final
                # hardware draw is stubbed; viewport origin is preserved.
            elif address == 0x4B6071:
                name, limit = words(sp + 4, 2)
                self.assertEqual(limit, 0x7FFFFFFF)
                pixels.append((
                    *words(0x62B2D0, 2), *words(0x62B2C8, 2), text(name),
                ))
                returned(8)
            elif address == 0x4B9628:
                returned(0)  # final graphics-origin update; no hardware surface

        uc.hook_add(UC_HOOK_CODE, intercept)
        uc.emu_start(MAP_HOOK_VA, MAP_RESUME_VA + 1, count=100000)
        self.assertEqual(uc.reg_read(UC_X86_REG_EIP), MAP_RESUME_VA)
        self.assertEqual(uc.reg_read(UC_X86_REG_ESP), initial_sp)
        self.assertEqual(uc.reg_read(UC_X86_REG_ECX), widget)  # displaced MOV
        for register, value in registers.items():
            self.assertEqual(uc.reg_read(register), value)
        self.assertEqual(words(0x62B2C8, 2), (111, 222))
        self.assertEqual(bytes(uc.mem_read(cache - 16, 16)), b"\xA5" * 16)
        self.assertEqual(bytes(uc.mem_read(cache + width * height * 2, 16)), b"\xA5" * 16)
        cache_data = bytes(uc.mem_read(cache, max(width * height * 2, 0)))
        return labels, pixels, cache_data

    def test_only_visible_discovered_and_not_hidden_cities_are_named(self):
        labels, pixels, cache = self._run_map([
            (100, 200, 1, "리스본", 3),
            (105, 200, 0, "미발견", 3),
            (105, 200, 5, "숨김", 3),
            (200, 200, 1, "화면 밖", 3),
            (100, 300, 1, "화면 아래", 3),
            (105, 200, 1, "", 3),
        ])
        self.assertEqual(labels, [(160, 144, "리스본")])
        self.assertEqual(len(pixels), 4)
        self.assertTrue(all(point[2:4] == (16, 48) for point in pixels))
        self.assertEqual(pixels[2][:2], (160, 144))  # real outline helper
        self.assertEqual(cache[:7 * 40 * 2], b"\xA5" * (7 * 40 * 2))
        self.assertEqual(cache[7 * 40 * 2:11 * 40 * 2], b"\xFF" * (4 * 40 * 2))
        self.assertEqual(cache[11 * 40 * 2:], b"\xA5" * (9 * 40 * 2))

    def test_scrolling_moves_labels_and_runtime_renames_are_used(self):
        cities = [(100, 200, 1, "새도시", 3)]
        self.assertEqual(self._run_map(cities)[0], [(160, 144, "새도시")])
        self.assertEqual(self._run_map(cities, origin=(92, 191))[0], [(128, 128, "새도시")])
        self.assertEqual(self._run_map(cities, origin=(110, 190))[0], [])

    def test_world_wrap_partial_cities_and_edge_clamping(self):
        self.assertEqual(
            self._run_map([(1, 10, 1, "리스본", 3)], origin=(2499, 7))[0],
            [(32, 32, "리스본")],
        )
        self.assertEqual(
            self._run_map([(99, 190, 1, "리스본", 3)], origin=(100, 190))[0],
            [(1, 17, "리스본")],
        )
        self.assertEqual(
            self._run_map([(128, 209, 1, "긴도시이름", 3)], dimensions=(40, 20))[0],
            [(559, 288, "긴도시이름")],
        )

    def test_empty_view_does_not_draw_or_write_cache(self):
        self.assertEqual(
            self._run_map([(100, 200, 1, "리스본", 3)], dimensions=(0, 0))[0], [],
        )

    def test_city_and_current_nation_share_exact_pixel_centre(self):
        labels, pixels, cache = self._run_map([(100, 200, 1, "리스본", 3, 0)])
        self.assertEqual(labels, [(160, 144, "리스본"), (124, 128, "[포르투갈 왕국]")])
        self.assertEqual(len(pixels), 8)
        for x, y, name in labels:
            self.assertEqual(x + len(name.encode("cp949")) * 4, 184)

    def test_runtime_nation_changes_and_last_nation_are_used(self):
        self.assertEqual(self._run_map([(100, 200, 1, "도시", 3, 77)])[0],
                         [(168, 144, "도시"), (140, 128, "[잉카 제국]")])
        self.assertEqual(self._run_map([(100, 200, 1, "도시", 3, 0)], nations={0: "새 국가"})[0],
                         [(168, 144, "도시"), (148, 128, "[새 국가]")])

    def test_ascii_mixed_text_and_two_tile_city_are_pixel_centred(self):
        for name in ("ABC", "A가", "남경"):
            labels = self._run_map([(100, 200, 1, name, 2, 0)], nations={0: "Q"})[0]
            self.assertEqual(labels[1], (164, 128, "[Q]"))
            for x, y, label in labels:
                self.assertEqual(x + len(label.encode("cp949")) * 4, 176)

    def test_country_is_bounded_and_invalid_ids_leave_city_name(self):
        for identifier in (-1, 78):
            self.assertEqual(self._run_map([(100, 200, 1, "리스본", 3, identifier)])[0],
                             [(160, 144, "리스본")])
        for name in ("", "가" * 64):
            self.assertEqual(self._run_map([(100, 200, 1, "리스본", 3, 0)], nations={0: name})[0],
                             [(160, 144, "리스본")])

    def test_both_labels_stay_inside_top_and_right_edges(self):
        labels = self._run_map([(128, 190, 1, "리스본", 3, 0)])[0]
        self.assertEqual(labels, [(591, 17, "리스본"), (519, 1, "[포르투갈 왕국]")])
        for x, y, text in labels:
            self.assertGreaterEqual(x, 1)
            self.assertGreaterEqual(y, 1)
            self.assertLessEqual(x + len(text.encode("cp949")) * 8, 639)

    def test_label_wider_than_view_does_not_spill_into_other_ui(self):
        self.assertEqual(
            self._run_map(
                [(91, 191, 1, "긴도시이름", 3)], dimensions=(4, 4),
            )[0], [],
        )


if __name__ == "__main__":
    unittest.main()
