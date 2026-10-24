"""Define the counting contract before comparing alternative interpretations."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from text_stats import count_items  # noqa: E402


class TextStatsTests(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(count_items(" \t\n"), {"items": 0, "distinct_items": 0})

    def test_repeated_items(self):
        self.assertEqual(
            count_items("alpha beta alpha"), {"items": 3, "distinct_items": 2}
        )

    def test_punctuation_and_identifiers_remain_attached(self):
        self.assertEqual(
            count_items("hello, hello error_code"), {"items": 3, "distinct_items": 3}
        )


if __name__ == "__main__":
    unittest.main()
