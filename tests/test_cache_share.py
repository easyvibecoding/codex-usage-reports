from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/codex-usage-reports/lib"))

from codex_usage_reports.transcript import cache_read_share_percent  # noqa: E402


class CacheShareTest(unittest.TestCase):
    def test_observed_share_and_missing_denominator(self):
        self.assertEqual(cache_read_share_percent({"input": 4, "cached_input": 3}), 75.0)
        self.assertIsNone(cache_read_share_percent({"input": 0, "cached_input": 0}))
        self.assertIsNone(cache_read_share_percent(None))
        self.assertIsNone(cache_read_share_percent({"input": 1, "cached_input": 2}))


if __name__ == "__main__":
    unittest.main()
