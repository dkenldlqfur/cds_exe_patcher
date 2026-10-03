"""Reference-only localization, reversal, custom edits and integrated saves."""
from dataclasses import asdict
import inspect
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Resources' / 'py'))
import patch_cds_integrated as patch
from localization_catalog import translate_text
from localization_patch import (
    POOL_OFFSET, _read_text, apply_extended_localization,
    manifest, read_extended_localization_state,
)
from pe_patch_section import LOCALIZATION_SLOT_OFFSET, find_patch_section

FIXTURE = Path(__file__).parent / 'fixtures' / 'coordinate_compass_test.exe'


class LocalizationTests(unittest.TestCase):
    def setUp(self):
        self.original = FIXTURE.read_bytes()
        self.data = bytearray(self.original)

    def assert_references(self, enabled):
        pe = pefile.PE(data=bytes(self.data), fast_load=True)
        try:
            for entry in manifest():
                for ref, _prefix in entry['refs']:
                    pointer = struct.unpack_from('<I', self.data, ref)[0]
                    self.assertEqual(_read_text(self.data, pe, pointer),
                                     entry['corrected' if enabled else 'original'])
                    if not enabled:
                        self.assertEqual(pointer, struct.unpack_from('<I', self.original, ref)[0])
        finally:
            pe.close()

    def test_all_references_apply_idempotently_and_restore(self):
        self.assertTrue(patch.apply_mistranslation_fixes(self.data, True))
        self.assertTrue(patch.read_mistranslation_patch_state(self.data))
        self.assertTrue(read_extended_localization_state(self.data))
        self.assert_references(True)
        self.assertFalse(patch.apply_mistranslation_fixes(self.data, True))
        self.assertTrue(patch.apply_mistranslation_fixes(self.data, False))
        self.assertFalse(read_extended_localization_state(self.data))
        self.assert_references(False)
        self.assertFalse(patch.apply_mistranslation_fixes(self.data, False))

    def test_changes_only_manifest_refs_and_reserved_section(self):
        apply_extended_localization(self.data, True)
        pe = pefile.PE(data=self.original, fast_load=True)
        try:
            allowed = {i for e in manifest() for ref, _ in e['refs'] for i in range(ref, ref+4)}
            for section in pe.sections:
                lo, size = section.PointerToRawData, section.SizeOfRawData
                for i in range(lo, lo + size):
                    if self.original[i] != self.data[i]:
                        self.assertIn(i, allowed, hex(i))
        finally:
            pe.close()
        for e in manifest():
            lo, size = e['offset'], len(e['original'].encode('cp949')) + 1
            self.assertEqual(self.data[lo:lo+size], self.original[lo:lo+size])

    def test_record_numeric_fields_and_editor_capacities_unchanged(self):
        apply_extended_localization(self.data, True)
        for reader, max_bytes in (
            (patch._read_city_records_from_data, patch.CITY_NAME_MAX_BYTES),
            (patch._read_item_records_from_data, patch.ITEM_NAME_MAX_BYTES),
            (patch._read_discovery_records_from_data, patch.DISCOVERY_NAME_MAX_BYTES),
            (patch._read_person_records_from_data, None),
            (patch._read_sponsor_records_from_data, None),
        ):
            before, after = reader(self.original), reader(bytes(self.data))
            self.assertEqual(len(before), len(after))
            for a, b in zip(before, after):
                if max_bytes:
                    self.assertLessEqual(len(b.name.encode('cp949')), max_bytes)
                for key, value in asdict(a).items():
                    if not isinstance(value, str):
                        self.assertEqual(value, asdict(b)[key], (reader.__name__, a.identifier, key))
        cities = patch._read_city_records_from_data(bytes(self.data))
        self.assertEqual(cities[0].name, '리스보아')
        self.assertEqual(cities[7].name, '세비야')
        self.assertIn('베네치아', [r.name for r in cities])
        items = patch._read_item_records_from_data(bytes(self.data))
        self.assertIn('레더 아머', [r.name for r in items])

    def set_city_name(self, name):
        record = patch._read_city_records_from_data(bytes(self.data))[0]
        values = asdict(record)
        values['name'] = name
        patch.apply_city_edit(self.data, patch.CityEdit(**values))

    def test_custom_names_before_and_after_patch_are_preserved(self):
        self.set_city_name('새 리스본')
        apply_extended_localization(self.data, True)
        self.assertEqual(patch._read_city_records_from_data(bytes(self.data))[0].name, '새 리스본')
        apply_extended_localization(self.data, False)
        self.assertEqual(patch._read_city_records_from_data(bytes(self.data))[0].name, '새 리스본')
        self.set_city_name('리스본')
        apply_extended_localization(self.data, True)
        self.set_city_name('나의 도시')
        apply_extended_localization(self.data, False)
        self.assertEqual(patch._read_city_records_from_data(bytes(self.data))[0].name, '나의 도시')

    def test_reused_editable_original_slot_restores_verified_master(self):
        self.set_city_name('임시 이름')
        self.set_city_name('리스본')
        apply_extended_localization(self.data, True)
        self.set_city_name('임시 이름')
        self.set_city_name('리스보아')  # Copy into the same reusable edit slot.
        apply_extended_localization(self.data, False)
        self.assertEqual(patch._read_city_records_from_data(bytes(self.data))[0].name, '리스본')

    def test_legacy_patch_is_recognized_and_upgraded(self):
        patch.apply_mistranslation_fixes(self.data, True, include_extended=False)
        self.assertTrue(patch.read_mistranslation_patch_state(self.data))
        self.assertFalse(read_extended_localization_state(self.data))
        patch.apply_mistranslation_fixes(self.data, True)
        self.assert_references(True)

    def test_corrupted_payload_rejected_without_partial_changes(self):
        patch.apply_mistranslation_fixes(self.data, True)
        section = find_patch_section(self.data)
        self.data[section.raw_offset + LOCALIZATION_SLOT_OFFSET + POOL_OFFSET] ^= 1
        before = bytes(self.data)
        with self.assertRaises(ValueError):
            patch.apply_mistranslation_fixes(self.data, False)
        self.assertEqual(self.data, before)

    def test_changed_code_prefix_not_overwritten(self):
        entry = next(e for e in manifest() if any(prefix for _, prefix in e['refs']))
        ref, prefix = next((r, p) for r, p in entry['refs'] if p)
        self.data[ref-1] ^= 1
        prior = bytes(self.data[ref-1:ref+4])
        apply_extended_localization(self.data, True)
        self.assertEqual(self.data[ref-1:ref+4], prior)

    def test_unknown_reserved_data_is_not_overwritten(self):
        apply_extended_localization(self.data, True)
        apply_extended_localization(self.data, False)
        section = find_patch_section(self.data)
        self.data[section.raw_offset + LOCALIZATION_SLOT_OFFSET + 20] = 1
        before = bytes(self.data)
        with self.assertRaises(ValueError):
            apply_extended_localization(self.data, True)
        self.assertEqual(self.data, before)

    def test_integrated_coordinate_changes_and_open_tab_values(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'CDS_95.EXE'
            target.write_bytes(self.original)
            for style, enabled, expected in (
                ('original', True, '리스보아'),
                ('korean3', True, '리스보아'),
                ('korean2', False, '리스본'),
                ('original', True, '리스보아'),
            ):
                settings = patch.read_settings(target)
                bound = inspect.signature(patch.apply_all).bind(
                    target, style, True, settings[1], *settings[2:])
                bound.arguments['mistranslation_fixes_enabled'] = enabled
                city = patch.read_city_records(target)[0]
                bound.arguments['city_edit'] = patch.CityEdit(**asdict(city))
                patch.apply_all(*bound.args, **bound.kwargs)
                self.assertEqual(patch.read_city_records(target)[0].name, expected)
                self.assertEqual(read_extended_localization_state(target.read_bytes()), enabled)

    def test_translation_is_non_cascading_and_short_name_is_exact_only(self):
        self.assertEqual(translate_text('잭'), '자크')
        self.assertEqual(translate_text('잭슨'), '잭슨')
        self.assertEqual(translate_text('리스본을 떠나 리벳트로 고친다'), '리스보아를 떠나 리벳으로 고친다')
        self.assertEqual(translate_text('히말라야'), '히말라야')


if __name__ == '__main__':
    unittest.main()
