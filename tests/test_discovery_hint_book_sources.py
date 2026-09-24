"""Regression tests for joining library books to hint text records."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

from discovery_hint_links import read_discovery_hint_links  # noqa: E402


FIXTURE = Path(__file__).parent / "fixtures" / "coordinate_compass_test.exe"


class DiscoveryHintBookSourceTests(unittest.TestCase):
    def test_book_hint_list_uses_hint_master_ids_not_text_ids(self) -> None:
        links = read_discovery_hint_links(FIXTURE)

        self.assertEqual(
            [
                (
                    source.record_number,
                    source.title,
                    source.author,
                    source.city_ids,
                )
                for source in links[79].book_sources
            ],
            [(82, "베오울프", "노삼브리아 궁정 엮음", (38,))],
        )
        # Book record 125 stores hint master ID 76. Its equality with hint 83's
        # body ID (76) is coincidental and must not be treated as a link.
        self.assertEqual(links[83].text_id, 76)
        self.assertEqual(
            [source.record_number for source in links[76].book_sources],
            [125],
        )
        self.assertEqual(links[83].book_sources, ())
        self.assertEqual(
            [(source.record_number, source.name) for source in links[30].item_sources],
            [(83, "로제타석")],
        )


if __name__ == "__main__":
    unittest.main()
