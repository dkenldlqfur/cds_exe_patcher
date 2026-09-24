"""Regression tests for the 수수 경단 recruitment-name repair."""

from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from patch_cds_integrated import (  # noqa: E402
    MISTRANSLATION_DANGO_SEARCH_CORRECTED,
    MISTRANSLATION_DANGO_SEARCH_ORIGINAL_VA,
    MISTRANSLATION_DANGO_SEARCH_POINTER_OFFSET,
    MISTRANSLATION_DANGO_SEARCH_SLOT_OFFSET,
    _mistranslation_slot_payload,
    apply_mistranslation_fixes,
    read_mistranslation_patch_state,
)
from pe_patch_section import (  # noqa: E402
    MISTRANSLATION_SLOT_OFFSET,
    MISTRANSLATION_SLOT_SIZE,
    find_patch_section,
)


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"


class MistranslationDangoFixTests(unittest.TestCase):
    def test_redirects_exact_item_name_search_and_restores_it(self) -> None:
        edited = bytearray(FIXTURE.read_bytes())
        self.assertFalse(read_mistranslation_patch_state(bytes(edited)))

        self.assertTrue(apply_mistranslation_fixes(edited, True))
        section = find_patch_section(edited)
        self.assertIsNotNone(section)
        assert section is not None
        slot_offset, slot_va = section.slot(
            MISTRANSLATION_SLOT_OFFSET, MISTRANSLATION_SLOT_SIZE,
        )
        self.assertEqual(
            struct.unpack_from("<I", edited, MISTRANSLATION_DANGO_SEARCH_POINTER_OFFSET)[0],
            slot_va + MISTRANSLATION_DANGO_SEARCH_SLOT_OFFSET,
        )
        payload = _mistranslation_slot_payload()
        self.assertEqual(edited[slot_offset:slot_offset + len(payload)], payload)
        self.assertIn(MISTRANSLATION_DANGO_SEARCH_CORRECTED.encode("cp949"), payload)
        self.assertTrue(read_mistranslation_patch_state(bytes(edited)))
        self.assertFalse(apply_mistranslation_fixes(edited, True))

        self.assertTrue(apply_mistranslation_fixes(edited, False))
        self.assertEqual(
            struct.unpack_from("<I", edited, MISTRANSLATION_DANGO_SEARCH_POINTER_OFFSET)[0],
            MISTRANSLATION_DANGO_SEARCH_ORIGINAL_VA,
        )
        self.assertFalse(read_mistranslation_patch_state(bytes(edited)))


if __name__ == "__main__":
    unittest.main()
