"""Verify reversible camera installation and coexistence with map options."""

from pathlib import Path
import hashlib
import json
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from city_discovery_notice_patch import (
    DEFAULT_LABEL_COLORS,
    DEFAULT_LABEL_OUTLINE,
    DEFAULT_SHOW_NATION,
    apply_city_discovery_notice_patch,
    read_city_discovery_notice_patch_state,
    read_city_label_colors,
    read_city_label_outline,
    read_city_label_show_nation,
)
from high_speed_map_patch import apply_high_speed_map_fix, read_high_speed_map_fix_state
from pe_patch_section import (
    PATCH_SECTION_WORLD_MAP_FOLLOW_SIZE,
    WORLD_MAP_FOLLOW_SLOT_OFFSET,
    WORLD_MAP_FOLLOW_SLOT_SIZE,
    find_patch_section,
    ensure_patch_section,
)
import world_map_follow_patch as follow_patch
from world_map_follow_patch import (
    MAGIC,
    VERSION,
    _layout,
    _legacy_layout,
    _legacy_v2_layout,
    _legacy_v3_layout,
    _legacy_v4_layout,
    _legacy_v5_layout,
    _patch_version,
    apply_world_map_follow_patch,
    read_world_map_follow_patch_state,
)


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"


def _offset(data, va):
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        return pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)
    finally:
        pe.close()


def _text_range(data):
    pe = pefile.PE(data=bytes(data), fast_load=True)
    try:
        section = next(s for s in pe.sections if s.Name.rstrip(b"\0") == b".text")
        return range(section.PointerToRawData, section.PointerToRawData + section.SizeOfRawData)
    finally:
        pe.close()


def _install_legacy(data, version):
    """Install the frozen release directly, never through the current writer."""
    section, _ = ensure_patch_section(data, PATCH_SECTION_WORLD_MAP_FOLLOW_SIZE)
    off, va = section.slot(WORLD_MAP_FOLLOW_SLOT_OFFSET, WORLD_MAP_FOLLOW_SLOT_SIZE)
    payload, hooks, _ = {
        1: _legacy_layout, 2: _legacy_v2_layout, 3: _legacy_v3_layout,
        4: _legacy_v4_layout, 5: _legacy_v5_layout,
    }[version](va)
    data[off:off + len(payload)] = payload
    for hook_va, original, replacement in hooks:
        hook_off = _offset(data, hook_va)
        if data[hook_off:hook_off + len(original)] != original:
            raise AssertionError("The legacy fixture requires original hook sites")
        data[hook_off:hook_off + len(replacement)] = replacement
    flags = struct.unpack_from("<I", data, section.header_offset + 36)[0]
    struct.pack_into("<I", data, section.header_offset + 36, flags | 0x80000000)
    return off, va


def _install_v1(data):
    return _install_legacy(data, 1)


class WorldMapFollowPatchTests(unittest.TestCase):
    def test_current_weather_helpers_occupy_reserved_code_and_zero_runtime(self):
        for va in (0x699A00, 0x700000, 0x745321):
            with self.subTest(slot_va=hex(va)):
                payload, _hooks, state = _layout(va)
                self.assertTrue(any(payload[0x2800:0x4000]))
                self.assertFalse(any(payload[0x6000:]))
                for name in ("weather_ready", "weather_invalidate"):
                    self.assertTrue(va + 0x2800 <= state[name] < va + 0x4000)

    def test_v1_snapshot_payload_and_hooks_match_shipped_release(self):
        # First digest captured from the deployed v1 EXE, before any v2 edits.
        for va, payload_hash, hooks_hash in (
            (0x699A00,
             "0b681ad9f454103330505a861571b2cac962337fe117e7f61c7d9d4eaa37252e",
             "80ef99f83902993d9831206086ebf15955fc6e14f0d41404f9cc4f3be5d7c39b"),
            (0x700000,
             "2c4486dad3aeda67f0f3f3c7aa2199a3c132af09a30e03901006986797a8c596",
             "4430bd77210e6d8b07d9d28c3aad23c3ac2fb2ebfeca703f1214e7db2ab9bf15"),
        ):
            with self.subTest(slot_va=hex(va)):
                payload, hooks, _ = _legacy_layout(va)
                self.assertEqual(hashlib.sha256(payload).hexdigest(), payload_hash)
                serialized_hooks = b"".join(
                    struct.pack("<I", hook_va) + original + replacement
                    for hook_va, original, replacement in hooks
                )
                self.assertEqual(hashlib.sha256(serialized_hooks).hexdigest(), hooks_hash)
                self.assertEqual(len(hooks), 20)

    def test_v2_snapshot_payload_and_hooks_match_shipped_release(self):
        # Actual installed v2 and two relocated layouts captured before v3.
        # The odd address also catches assumptions about aligned operands.
        for va, payload_hash, hooks_hash in (
            (0x699A00,
             "af024c9fa7b61f908b6ae29f946b4680a20fb3a61984781379cde811ba5c0151",
             "5aae3cefa18e02f230269177254756960e51461c5d925940212e99482e71e8a8"),
            (0x700000,
             "0c34823e7fabe7268095b8ace33896215ad883c76a9781bcd717ec76f1dc1005",
             "17fede9e2684eb2e438c63a75874a82b901e4ae95de2a16f74984d0cadf56fe5"),
            (0x745321,
             "b086b52aab8ba88f9be142942645cccd8f43a696875e2a643f50b6d125be149f",
             "98f58c274dc3d3edcb8fffa4bbf365aab938fe765e2ffefe3eba6719ca9daefc"),
        ):
            with self.subTest(slot_va=hex(va)):
                payload, hooks, _ = _legacy_v2_layout(va)
                self.assertEqual(hashlib.sha256(payload).hexdigest(), payload_hash)
                serialized_hooks = b"".join(
                    struct.pack("<I", hook_va) + original + replacement
                    for hook_va, original, replacement in hooks
                )
                self.assertEqual(hashlib.sha256(serialized_hooks).hexdigest(), hooks_hash)
                self.assertEqual(len(hooks), 29)

    def test_v3_snapshot_payload_and_hooks_match_shipped_release(self):
        # Exact installed v3 and two relocated layouts captured before v4.
        for va, payload_hash, hooks_hash in (
            (0x699A00,
             "efbdc7be0d01ea456814b78a8638474a9ad0f79bb7105d840aac12cb55137163",
             "44933725ebb1a2e0c39529c137fda075e68fe9c66bd13a01c98ecf36cdc3a134"),
            (0x700000,
             "577a665061da233d2e4b555fd918e77cc5e54d8ba15228b085c9b741cc0ee583",
             "288bbcb6e785fde5e5cc6d4e28ad44a947ca3be4e04309f8b98b95b61b2c8fef"),
            (0x745321,
             "4b4f209b615010ecb5f5342cb654efbe4b8b3d87e4db8fc47e481832e3cc8842",
             "5d351b21683fdee42ce7a5abd9df5a9e7b505323fc13febe6e72d7b15fbbdc6d"),
        ):
            with self.subTest(slot_va=hex(va)):
                payload, hooks, _ = _legacy_v3_layout(va)
                self.assertEqual(hashlib.sha256(payload).hexdigest(), payload_hash)
                serialized_hooks = b"".join(
                    struct.pack("<I", hook_va) + original + replacement
                    for hook_va, original, replacement in hooks
                )
                self.assertEqual(hashlib.sha256(serialized_hooks).hexdigest(), hooks_hash)
                self.assertEqual(len(hooks), 32)

    def test_v4_snapshot_payload_and_hooks_match_shipped_release(self):
        # Deployed v4 and relocated layouts captured before the weather fix.
        for va, payload_hash, hooks_hash in (
            (0x699A00,
             "6c477844556f87079f4f487cf0cded1c0f3bea1ca19bfe226fb062eaa15b52f5",
             "3daac677be4033fc7c27d531f62fff89a7390616a93f2484849a39490485563d"),
            (0x700000,
             "9a586abed1badf973cb02a3cabae0d75098e9752f099475021fbb6073056639c",
             "964378bb61b72ade700086cfe6958f6871c0f026894a9aa6902225b43ffab216"),
            (0x745321,
             "c244dc453b4b510dbdd135883962326b5cfebafb9b1e1ecfed986a8e7e797f84",
             "835d3350d706e1b8170fa611871551b64945903b51db106f5b8ea49d1661ce3c"),
        ):
            with self.subTest(slot_va=hex(va)):
                payload, hooks, _ = _legacy_v4_layout(va)
                self.assertEqual(hashlib.sha256(payload).hexdigest(), payload_hash)
                serialized_hooks = b"".join(
                    struct.pack("<I", hook_va) + original + replacement
                    for hook_va, original, replacement in hooks
                )
                self.assertEqual(hashlib.sha256(serialized_hooks).hexdigest(), hooks_hash)
                self.assertEqual(len(hooks), 34)

    def test_v5_snapshot_payload_and_hooks_match_shipped_release(self):
        # Exact deployed v5 and relocated snapshots taken before timing edits.
        for va, payload_hash, hooks_hash, state_hash in (
            (0x699A00,
             "ce55099d75178a74069539c56f0d4073b63af5be9eec3e4abe410d4e64f1bcb5",
             "a1716ceafd7a62f7f726c2de7aef660b43ac12b6b2b608ea7058cba4d2ddf497",
             "61c3f4b5844a30c9a11501c068900c7da1aed7ecbd04cde86c1debe613ea8a31"),
            (0x700000,
             "5777b27761ac4cdbaa92d8305c06a628676841002fa161a4159e7510049e95cf",
             "3596a5b03971a3a1edf9b3c05cb37ee9a4288b9724b6fb60a4249e1693086b16",
             "237212e9f2583c12482e9aefb114f708735ca62776231f1897c8b956a8f714de"),
            (0x745321,
             "a41b582f58ea42af7c083d1760f7ce93efd6a16b49a4daaf12a85ff46b780f2a",
             "34dd56cb37c82102f249d514c8a5b9acba8a2cc2fa04992004a3c6a193d477bf",
             "80fee30fb93faa59bf161570a32bb9bc1b90d8fce4c60d344231b2d0087ecffb"),
        ):
            with self.subTest(slot_va=hex(va)):
                payload, hooks, state = _legacy_v5_layout(va)
                self.assertEqual(hashlib.sha256(payload).hexdigest(), payload_hash)
                serialized_hooks = b"".join(
                    struct.pack("<I", hook_va) + original + replacement
                    for hook_va, original, replacement in hooks
                )
                self.assertEqual(hashlib.sha256(serialized_hooks).hexdigest(), hooks_hash)
                self.assertEqual(len(hooks), 42)
                serialized_state = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
                self.assertEqual(hashlib.sha256(serialized_state).hexdigest(), state_hash)
                self.assertEqual(len(state), 781)

    def test_legacy_read_disable_upgrade_and_current_idempotence(self):
        original = FIXTURE.read_bytes()
        text_range = _text_range(original)
        for version in (1, 2, 3, 4, 5):
            with self.subTest(version=version):
                legacy = bytearray(original)
                off, va = _install_legacy(legacy, version)
                self.assertEqual(_patch_version(legacy), version)
                self.assertTrue(read_world_map_follow_patch_state(legacy))
                data = bytearray(legacy)
                self.assertTrue(apply_world_map_follow_patch(data, False))
                self.assertFalse(read_world_map_follow_patch_state(data))
                self.assertFalse(any(data[off:off + WORLD_MAP_FOLLOW_SLOT_SIZE]))
                self.assertEqual(data[text_range.start:text_range.stop], original[text_range.start:text_range.stop])
                disabled = bytes(data)
                self.assertFalse(apply_world_map_follow_patch(data, False))
                self.assertEqual(bytes(data), disabled)
                data = bytearray(legacy)
                self.assertTrue(apply_world_map_follow_patch(data, True))
                self.assertEqual(_patch_version(data), VERSION)
                self.assertEqual(struct.unpack_from("<I", data, off + len(MAGIC))[0], VERSION)
                self.assertEqual(data[off:off + WORLD_MAP_FOLLOW_SLOT_SIZE], _layout(va)[0])
                current = bytes(data)
                self.assertFalse(apply_world_map_follow_patch(data, True))
                self.assertEqual(bytes(data), current)
                self.assertTrue(apply_world_map_follow_patch(data, False))
                self.assertEqual(data[text_range.start:text_range.stop], original[text_range.start:text_range.stop])

    def test_legacy_damage_and_unknown_versions_reject_without_mutation(self):
        for legacy_version in (1, 2, 3, 4, 5):
            legacy = bytearray(FIXTURE.read_bytes())
            off, va = _install_legacy(legacy, legacy_version)
            for label, changed_offset in (
                ("payload", off + 0x100),
                ("runtime", off + 0x6000),
                ("hook", _offset(legacy, _legacy_layout(va)[1][0][0])),
            ):
                with self.subTest(version=legacy_version, damage=label):
                    data = bytearray(legacy)
                    data[changed_offset] ^= 1
                    self._assert_rejected_unchanged(data)
            for unknown_version in (0, 99, 0xFFFFFFFF):
                with self.subTest(version=legacy_version, unknown_version=unknown_version):
                    data = bytearray(legacy)
                    struct.pack_into("<I", data, off + len(MAGIC), unknown_version)
                    self._assert_rejected_unchanged(data)

    def test_migration_handles_added_retired_hooks_and_atomic_conflicts(self):
        legacy = bytearray(FIXTURE.read_bytes())
        _off, va = _install_v1(legacy)
        payload, hooks, state = _layout(va)
        extra_va = 0x401000
        extra_off = _offset(legacy, extra_va)
        extra_original = bytes(legacy[extra_off:extra_off + 5])
        extra_replacement = b"\xE9\x11\x22\x33\x44"
        removed = hooks[0]
        replacement_layout = (
            payload,
            tuple(sorted(hooks[1:] + ((extra_va, extra_original, extra_replacement),))),
            state,
        )
        # Simulate a current generator with a new site and a retired v1 site;
        # validation must use the actual version's hook set, not a fixed list.
        with patch.object(follow_patch, "_layout", return_value=replacement_layout):
            data = bytearray(legacy)
            self.assertTrue(apply_world_map_follow_patch(data, True))
            self.assertEqual(data[extra_off:extra_off + 5], extra_replacement)
            removed_off = _offset(data, removed[0])
            self.assertEqual(data[removed_off:removed_off + len(removed[1])], removed[1])
            mixed = bytearray(data)
            mixed[removed_off:removed_off + len(removed[2])] = removed[2]
            self._assert_rejected_unchanged(mixed)
            self.assertTrue(apply_world_map_follow_patch(data, False))
            self.assertEqual(data[extra_off:extra_off + 5], extra_original)
            corrupt = bytearray(legacy)
            corrupt[extra_off] ^= 1
            before = bytes(corrupt)
            self.assertEqual(_patch_version(corrupt), 1)
            with self.assertRaises(ValueError):
                apply_world_map_follow_patch(corrupt, True)
            self.assertEqual(bytes(corrupt), before)

    def test_v2_only_retired_hook_and_new_site_conflict_are_validated(self):
        legacy = bytearray(FIXTURE.read_bytes())
        _off, va = _install_legacy(legacy, 2)
        payload, hooks, state = _layout(va)
        removed_va = 0x48EC84  # The smooth wait hook did not exist in v1.
        self.assertNotIn(removed_va, {hook[0] for hook in _legacy_layout(va)[1]})
        removed = next(hook for hook in _legacy_v2_layout(va)[1] if hook[0] == removed_va)
        extra_va = 0x401000
        extra_off = _offset(legacy, extra_va)
        extra_original = bytes(legacy[extra_off:extra_off + 5])
        extra_replacement = b"\xE9\x11\x22\x33\x44"
        replacement_layout = (
            payload,
            tuple(sorted(tuple(hook for hook in hooks if hook[0] != removed_va)
                         + ((extra_va, extra_original, extra_replacement),))),
            state,
        )
        with patch.object(follow_patch, "_layout", return_value=replacement_layout):
            data = bytearray(legacy)
            self.assertTrue(apply_world_map_follow_patch(data, True))
            removed_off = _offset(data, removed_va)
            self.assertEqual(data[removed_off:removed_off + len(removed[1])], removed[1])
            mixed = bytearray(data)
            mixed[removed_off:removed_off + len(removed[2])] = removed[2]
            self._assert_rejected_unchanged(mixed)
            corrupt = bytearray(legacy)
            corrupt[extra_off] ^= 1
            before = bytes(corrupt)
            self.assertEqual(_patch_version(corrupt), 2)
            with self.assertRaises(ValueError):
                apply_world_map_follow_patch(corrupt, True)
            self.assertEqual(bytes(corrupt), before)
            self.assertTrue(apply_world_map_follow_patch(data, False))
            self.assertEqual(data[extra_off:extra_off + len(extra_original)], extra_original)

    def test_integrated_legacy_migration_preserves_city_and_high_speed_options(self):
        from patch_cds_integrated import apply_all, read_settings

        colors, outline = (10, 21, 32, 43, 54, 73), (1, 21)
        for version, style in ((version, style) for version in (1, 2, 3, 4, 5)
                               for style in ("original", "korean2", "korean3")):
            for enabled in (True, False):
                with self.subTest(version=version, style=style, enabled=enabled), tempfile.TemporaryDirectory() as directory:
                    legacy = bytearray(FIXTURE.read_bytes())
                    apply_city_discovery_notice_patch(legacy, True, colors, outline, False)
                    apply_high_speed_map_fix(legacy, True)
                    _install_legacy(legacy, version)
                    target = Path(directory) / "CDS_95.EXE"
                    target.write_bytes(legacy)
                    settings = read_settings(target)
                    apply_all(
                        target, style, True, settings[1], *settings[2:],
                        world_map_follow_enabled=enabled,
                        city_discovery_notice_enabled=True,
                        high_speed_map_fix_enabled=True,
                    )
                    saved = target.read_bytes()
                    self.assertEqual(_patch_version(saved), VERSION if enabled else 0)
                    self.assertEqual(read_settings(target)[0], style)
                    self.assertTrue(read_city_discovery_notice_patch_state(saved))
                    self.assertTrue(read_high_speed_map_fix_state(saved))
                    self.assertEqual(read_city_label_colors(saved), colors)
                    self.assertEqual(read_city_label_outline(saved), outline)
                    self.assertIs(read_city_label_show_nation(saved), False)

    def test_v3_only_retired_hook_and_new_site_conflict_are_validated(self):
        legacy = bytearray(FIXTURE.read_bytes())
        _off, va = _install_legacy(legacy, 3)
        payload, hooks, state = _layout(va)
        removed_va = 0x4891C0  # Cloud capture was first installed in v3.
        self.assertNotIn(removed_va, {hook[0] for hook in _legacy_v2_layout(va)[1]})
        removed = next(hook for hook in _legacy_v3_layout(va)[1] if hook[0] == removed_va)
        extra_va = 0x401000
        extra_off = _offset(legacy, extra_va)
        extra_original = bytes(legacy[extra_off:extra_off + 5])
        extra_replacement = b"\xE9\x11\x22\x33\x44"
        replacement_layout = (
            payload,
            tuple(sorted(tuple(hook for hook in hooks if hook[0] != removed_va)
                         + ((extra_va, extra_original, extra_replacement),))),
            state,
        )
        with patch.object(follow_patch, "_layout", return_value=replacement_layout):
            data = bytearray(legacy)
            self.assertTrue(apply_world_map_follow_patch(data, True))
            self.assertEqual(_patch_version(data), VERSION)
            removed_off = _offset(data, removed_va)
            self.assertEqual(data[removed_off:removed_off + len(removed[1])], removed[1])
            current = bytes(data)
            self.assertFalse(apply_world_map_follow_patch(data, True))
            self.assertEqual(bytes(data), current)
            mixed = bytearray(data)
            mixed[removed_off:removed_off + len(removed[2])] = removed[2]
            self._assert_rejected_unchanged(mixed)
            corrupt = bytearray(legacy)
            corrupt[extra_off] ^= 1
            before = bytes(corrupt)
            # Reading an intact installed v3 must not depend on future hooks.
            self.assertEqual(_patch_version(corrupt), 3)
            with self.assertRaises(ValueError):
                apply_world_map_follow_patch(corrupt, True)
            self.assertEqual(bytes(corrupt), before)
            self.assertTrue(apply_world_map_follow_patch(data, False))
            self.assertEqual(data[extra_off:extra_off + len(extra_original)], extra_original)

    def test_new_current_hook_conflicts_reject_legacy_upgrade_atomically(self):
        # Exercise actual newly introduced sites as well as the synthetic
        # added/retired hook cases above; changing generators adds coverage.
        for version, build_legacy in (
            (1, _legacy_layout), (2, _legacy_v2_layout), (3, _legacy_v3_layout),
            (4, _legacy_v4_layout), (5, _legacy_v5_layout),
        ):
            legacy = bytearray(FIXTURE.read_bytes())
            _off, va = _install_legacy(legacy, version)
            old_ranges = tuple((hook_va, hook_va + len(original))
                               for hook_va, original, _ in build_legacy(va)[1])
            for hook_va, original, _ in _layout(va)[1]:
                # New bytes may be a wholly new site or a widened old hook.
                new_byte = next((hook_va + index for index in range(len(original))
                                 if not any(start <= hook_va + index < end
                                            for start, end in old_ranges)), None)
                if new_byte is None:
                    continue
                with self.subTest(version=version, new_hook=hex(hook_va)):
                    data = bytearray(legacy)
                    data[_offset(data, new_byte)] ^= 1
                    before = bytes(data)
                    self.assertEqual(_patch_version(data), version)
                    with self.assertRaises(ValueError):
                        apply_world_map_follow_patch(data, True)
                    self.assertEqual(bytes(data), before)

    def test_v4_only_retired_hook_and_new_site_conflict_are_validated(self):
        # World-coordinate current phase was added in v4.
        self._assert_retired_hook_validation(4, _legacy_v4_layout, _legacy_v3_layout, 0x48A518)

    def test_v5_only_retired_hook_and_new_site_conflict_are_validated(self):
        # The rain recorder was first installed in v5.
        self._assert_retired_hook_validation(5, _legacy_v5_layout, _legacy_v4_layout, 0x48AA6E)

    def _assert_retired_hook_validation(self, version, build_legacy, build_previous, removed_va):
        legacy = bytearray(FIXTURE.read_bytes())
        _off, va = _install_legacy(legacy, version)
        payload, hooks, state = _layout(va)
        self.assertNotIn(removed_va, {hook[0] for hook in build_previous(va)[1]})
        removed = next(hook for hook in build_legacy(va)[1] if hook[0] == removed_va)
        extra_va = 0x401000
        extra_off = _offset(legacy, extra_va)
        extra_original = bytes(legacy[extra_off:extra_off + 5])
        extra_replacement = b"\xE9\x11\x22\x33\x44"
        replacement_layout = (
            payload,
            tuple(sorted(tuple(hook for hook in hooks if hook[0] != removed_va)
                         + ((extra_va, extra_original, extra_replacement),))),
            state,
        )
        with patch.object(follow_patch, "_layout", return_value=replacement_layout):
            data = bytearray(legacy)
            self.assertTrue(apply_world_map_follow_patch(data, True))
            self.assertEqual(_patch_version(data), VERSION)
            removed_off = _offset(data, removed_va)
            self.assertEqual(data[removed_off:removed_off + len(removed[1])], removed[1])
            current = bytes(data)
            self.assertFalse(apply_world_map_follow_patch(data, True))
            self.assertEqual(bytes(data), current)
            mixed = bytearray(data)
            mixed[removed_off:removed_off + len(removed[2])] = removed[2]
            self._assert_rejected_unchanged(mixed)
            corrupt = bytearray(legacy)
            corrupt[extra_off] ^= 1
            before = bytes(corrupt)
            self.assertEqual(_patch_version(corrupt), version)
            with self.assertRaises(ValueError):
                apply_world_map_follow_patch(corrupt, True)
            self.assertEqual(bytes(corrupt), before)
            self.assertTrue(apply_world_map_follow_patch(data, False))
            self.assertEqual(data[extra_off:extra_off + len(extra_original)], extra_original)

    def test_disabled_is_byte_exact_no_op(self):
        original = FIXTURE.read_bytes()
        data = bytearray(original)
        self.assertFalse(read_world_map_follow_patch_state(data))
        self.assertFalse(apply_world_map_follow_patch(data, False))
        self.assertEqual(bytes(data), original)

    def test_apply_reload_idempotence_restore_and_text_isolation(self):
        original = FIXTURE.read_bytes()
        data = bytearray(original)
        self.assertTrue(apply_world_map_follow_patch(data, True))
        self.assertTrue(read_world_map_follow_patch_state(bytes(data)))
        snapshot = bytes(data)
        self.assertFalse(apply_world_map_follow_patch(data, True))
        self.assertEqual(bytes(data), snapshot)
        section = find_patch_section(data)
        off, va = section.slot(WORLD_MAP_FOLLOW_SLOT_OFFSET, WORLD_MAP_FOLLOW_SLOT_SIZE)
        payload, hooks, _ = _layout(va)
        self.assertEqual(data[off:off + WORLD_MAP_FOLLOW_SLOT_SIZE], payload)
        allowed = set()
        for hook_va, original_code, patched_code in hooks:
            hook_off = _offset(data, hook_va)
            self.assertEqual(original[hook_off:hook_off + len(original_code)], original_code)
            self.assertEqual(data[hook_off:hook_off + len(patched_code)], patched_code)
            allowed.update(range(hook_off, hook_off + len(patched_code)))
        text_range = _text_range(original)
        changes = {i for i in text_range if original[i] != data[i]}
        self.assertTrue(changes)
        self.assertTrue(changes <= allowed)
        self.assertTrue(apply_world_map_follow_patch(data, False))
        self.assertFalse(read_world_map_follow_patch_state(bytes(data)))
        self.assertFalse(any(data[off:off + WORLD_MAP_FOLLOW_SLOT_SIZE]))
        self.assertEqual(data[text_range.start:text_range.stop], original[text_range.start:text_range.stop])
        restored = bytes(data)
        self.assertFalse(apply_world_map_follow_patch(data, False))
        self.assertEqual(bytes(data), restored)
        self.assertTrue(apply_world_map_follow_patch(data, True))
        self.assertEqual(bytes(data), snapshot)

    def test_rejects_unknown_original_hooks_without_mutation(self):
        pristine = bytearray(FIXTURE.read_bytes())
        enabled = bytearray(pristine)
        apply_world_map_follow_patch(enabled, True)
        section = find_patch_section(enabled)
        _, va = section.slot(WORLD_MAP_FOLLOW_SLOT_OFFSET, WORLD_MAP_FOLLOW_SLOT_SIZE)
        _, hooks, _ = _layout(va)
        for hook_va, _, _ in hooks:
            with self.subTest(hook=hex(hook_va)):
                data = bytearray(pristine)
                data[_offset(data, hook_va)] ^= 1
                damaged = bytes(data)
                with self.assertRaises(ValueError):
                    read_world_map_follow_patch_state(data)
                for enabled in (True, False):
                    with self.assertRaises(ValueError):
                        apply_world_map_follow_patch(data, enabled)
                    self.assertEqual(bytes(data), damaged)

    def test_rejects_corrupt_hooks_payload_and_orphan_without_mutation(self):
        baseline = bytearray(FIXTURE.read_bytes())
        apply_world_map_follow_patch(baseline, True)
        section = find_patch_section(baseline)
        off, va = section.slot(WORLD_MAP_FOLLOW_SLOT_OFFSET, WORLD_MAP_FOLLOW_SLOT_SIZE)
        _, hooks, state = _layout(va)
        cases = [("hook " + hex(hook_va), _offset(baseline, hook_va)) for hook_va, _, _ in hooks]
        cases.extend((
            ("payload header", off),
            ("payload code", off + 0x100),
            ("payload tail", off + WORLD_MAP_FOLLOW_SLOT_SIZE - 1),
        ))
        # Mutable runtime fields must still be pristine in a saved executable.
        if state:
            state_va = next(iter(state.values()))
            if isinstance(state_va, int) and va <= state_va < va + WORLD_MAP_FOLLOW_SLOT_SIZE:
                cases.append(("runtime state", off + state_va - va))
        for label, changed_offset in cases:
            with self.subTest(corruption=label):
                data = bytearray(baseline)
                data[changed_offset] ^= 1
                self._assert_rejected_unchanged(data)
        data = bytearray(baseline)
        for hook_va, original_code, _ in hooks:
            hook_off = _offset(data, hook_va)
            data[hook_off:hook_off + len(original_code)] = original_code
        self._assert_rejected_unchanged(data)

    def _assert_rejected_unchanged(self, data):
        damaged = bytes(data)
        with self.assertRaises(ValueError):
            read_world_map_follow_patch_state(data)
        for enabled in (True, False):
            with self.assertRaises(ValueError):
                apply_world_map_follow_patch(data, enabled)
            self.assertEqual(bytes(data), damaged)

    def test_follow_preserves_existing_city_and_high_speed_patches(self):
        colors, outline = (10, 21, 32, 43, 54, 73), (1, 21)
        data = bytearray(FIXTURE.read_bytes())
        apply_city_discovery_notice_patch(data, True, colors, outline, False)
        apply_high_speed_map_fix(data, True)
        before = bytes(data)
        section = find_patch_section(data)
        earlier_slots = bytes(data[section.raw_offset + 0x100:section.raw_offset + WORLD_MAP_FOLLOW_SLOT_OFFSET])
        apply_world_map_follow_patch(data, True)
        self.assertTrue(read_city_discovery_notice_patch_state(data))
        self.assertTrue(read_high_speed_map_fix_state(data))
        self.assertEqual(read_city_label_colors(data), colors)
        self.assertEqual(read_city_label_outline(data), outline)
        self.assertIs(read_city_label_show_nation(data), False)
        self.assertEqual(data[section.raw_offset + 0x100:section.raw_offset + WORLD_MAP_FOLLOW_SLOT_OFFSET], earlier_slots)
        apply_world_map_follow_patch(data, False)
        text_range = _text_range(before)
        self.assertEqual(data[text_range.start:text_range.stop], before[text_range.start:text_range.stop])
        self.assertTrue(read_city_discovery_notice_patch_state(data))
        self.assertTrue(read_high_speed_map_fix_state(data))

    def test_integrated_style_and_option_changes_preserve_city_settings(self):
        from patch_cds_integrated import apply_all, read_settings

        colors = (10, 21, 32, 43, 54, 73)
        scenarios = (
            ("korean3", True, True, True, (1, 21), False),
            ("original", True, True, True, None, None),
            ("korean2", True, False, True, (0, 73), True),
            ("korean3", False, False, True, None, None),
            ("original", False, True, True, (1, 21), False),
            ("korean2", True, True, False, None, None),
            ("korean3", True, False, False, None, None),
            ("original", False, True, False, None, None),
            ("korean3", False, False, False, None, None),
        )
        expected_outline, expected_nation = DEFAULT_LABEL_OUTLINE, DEFAULT_SHOW_NATION
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "CDS_95.EXE"
            shutil.copyfile(FIXTURE, target)
            for style, follow, high_speed, city, outline, show_nation in scenarios:
                with self.subTest(style=style, follow=follow, high_speed=high_speed, city=city):
                    settings = read_settings(target)
                    backup = apply_all(
                        target, style, True, settings[1], *settings[2:],
                        world_map_follow_enabled=follow,
                        high_speed_map_fix_enabled=high_speed,
                        city_discovery_notice_enabled=city,
                        city_label_colors=colors if city else None,
                        city_label_outline=outline,
                        city_label_show_nation=show_nation,
                    )
                    self.assertIsNotNone(backup)
                    self.assertTrue(backup.is_file())
                    saved = target.read_bytes()
                    self.assertEqual(read_settings(target)[0], style)
                    self.assertEqual(read_world_map_follow_patch_state(saved), follow)
                    self.assertEqual(read_high_speed_map_fix_state(saved), high_speed)
                    self.assertEqual(read_city_discovery_notice_patch_state(saved), city)
                    expected_outline = (outline or expected_outline) if city else DEFAULT_LABEL_OUTLINE
                    expected_nation = (expected_nation if show_nation is None else show_nation) if city else DEFAULT_SHOW_NATION
                    self.assertEqual(read_city_label_colors(saved), colors if city else DEFAULT_LABEL_COLORS)
                    self.assertEqual(read_city_label_outline(saved), expected_outline)
                    self.assertEqual(read_city_label_show_nation(saved), expected_nation)
                    # Reopening and saving must retain the installed camera.
                    settings = read_settings(target)
                    repeat_backup = apply_all(
                        target, settings[0], True, settings[1], *settings[2:],
                        world_map_follow_enabled=follow,
                        high_speed_map_fix_enabled=high_speed,
                        city_discovery_notice_enabled=city,
                    )
                    repeated = target.read_bytes()
                    if style == "korean2" and repeat_backup is not None:
                        # Existing korean2 installation normalizes two stack
                        # operands on its second save, also without follow.
                        # Do not permit changes elsewhere in the executable.
                        section = find_patch_section(saved)
                        self.assertEqual(len(repeated), len(saved))
                        changed = {i for i, (a, b) in enumerate(zip(saved, repeated)) if a != b}
                        self.assertTrue(changed <= {
                            section.raw_offset + 0x1AF,
                            section.raw_offset + 0x1C0,
                        })
                        self.assertIsNone(apply_all(
                            target, settings[0], True, settings[1], *settings[2:],
                            world_map_follow_enabled=follow,
                            high_speed_map_fix_enabled=high_speed,
                            city_discovery_notice_enabled=city,
                        ))
                        self.assertEqual(target.read_bytes(), repeated)
                    else:
                        self.assertIsNone(repeat_backup)
                        self.assertEqual(repeated, saved)


if __name__ == "__main__":
    unittest.main()
