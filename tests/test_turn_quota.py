from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/codex-usage-reports/lib"))

from codex_usage_reports import turn_quota as quota  # noqa: E402
from codex_usage_reports.util import stable_hash  # noqa: E402


def source(used=20, *, at=1000, reset=9999, plan="pro", account="example-account"):
    return {"started_at": at, "finished_at": at + 0.1, "rate_limits": {
        "accountId": account, "private": "DROP ME",
        "rateLimitsByLimitId": {"codex": {
            "limitId": "codex", "planType": plan, "limitName": "DROP ME",
            "primary": {"usedPercent": used, "windowDurationMins": 300, "resetsAt": reset},
        }}}, "account_response": {"account": {"type": "chatgpt", "planType": plan,
                                              "email": "example@example.invalid"}}}


class TurnQuotaTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "auto-reports").mkdir()
        with sqlite3.connect(self.root / "auto-reports/timing.sqlite3") as db:
            db.execute("CREATE TABLE turns (key TEXT PRIMARY KEY, session_hash TEXT, "
                       "turn_hash TEXT, started REAL, state TEXT)")

    def add(self, number, *, task="example-task", started=None):
        key = stable_hash([task, str(number)])
        with sqlite3.connect(self.root / "auto-reports/timing.sqlite3") as db:
            db.execute("INSERT INTO turns VALUES (?,?,?,?,'started')",
                       (key, stable_hash(task), stable_hash(str(number)), started or number * 100))
        return key

    def observe(self, key, value):
        with patch.object(quota, "read_meter_sources", return_value=value) as reader:
            result = quota.observe(self.root, key)
        return result, reader

    def test_baseline_next_turn_fractional_remaining_delta_and_private_storage(self):
        first = self.add(1)
        a, reader = self.observe(first, source())
        self.assertEqual(a["rows"][0]["comparison"], "baseline")
        self.assertEqual(a["plan_type"], "pro")
        reader.assert_called_once_with(timeout=2.0, codex_home=None, include_usage=False)
        second = self.add(2)
        b, _ = self.observe(second, source(20.25, at=1100))
        self.assertEqual((b["rows"][0]["remaining_percent"], b["rows"][0]["delta_pp"]),
                         (79.75, -0.25))
        self.assertEqual(b["rows"][0]["comparison"], "observed")
        self.assertEqual(quota.saved(self.root, second), b)
        with sqlite3.connect(self.root / "auto-reports/quota.sqlite3") as db:
            raw = " ".join(r[0] for r in db.execute("SELECT payload FROM snapshots"))
        for forbidden in ("DROP ME", "example-account", "example@example.invalid", "example-task"):
            self.assertNotIn(forbidden, raw)
        self.assertEqual((self.root / "auto-reports/quota.sqlite3").stat().st_mode & 0o777, 0o600)

    def test_unchanged_is_not_free_or_a_claim_of_less_than_one_percent(self):
        old = quota._normalize(source())
        current = quota._normalize(source(at=1100))
        row = quota._compare(current, old)["rows"][0]
        self.assertEqual((row["delta_pp"], row["comparison"]), (0, "unchanged"))

    def test_reset_changed_window_and_expired_snapshot_never_subtract(self):
        old = quota._normalize(source())
        changed = source(2, at=1100, reset=99999)
        current = quota._normalize(changed)
        row = quota._compare(current, old)["rows"][0]
        self.assertEqual((row["delta_pp"], row["comparison"]), (None, "reset"))
        window = changed["rate_limits"]["rateLimitsByLimitId"]["codex"]["primary"]
        window["windowDurationMins"] = 10080
        self.assertEqual(quota._compare(quota._normalize(changed), old)["rows"][0]["comparison"],
                         "reset")
        expired = quota._normalize(source(at=1100, reset=1100))
        self.assertEqual(quota._compare(expired, old)["rows"][0]["comparison"], "expired")
        self.assertIsNone(expired["rows"][0]["remaining_percent"])

    def test_account_plan_unknown_identity_clock_and_correction_are_not_consumption(self):
        old = quota._normalize(source())
        cases = [source(at=1100, account="different-example"), source(at=1100, plan="plus"),
                 source(at=1100, account=None), source(at=999)]
        for current in cases:
            with self.subTest(current=current["started_at"]):
                row = quota._compare(quota._normalize(current), old)["rows"][0]
                self.assertEqual((row["delta_pp"], row["comparison"]), (None, "not_comparable"))
        row = quota._compare(quota._normalize(source(19, at=1100)), old)["rows"][0]
        self.assertEqual((row["delta_pp"], row["comparison"]), (None, "corrected"))

    def test_only_immediate_previous_turn_no_cross_task_or_failed_turn_bridge(self):
        self.observe(self.add(1), source())
        self.observe(self.add(2, task="different-task"), source(80, at=1100))
        result, _ = self.observe(self.add(3), source(21, at=1200))
        self.assertEqual(result["rows"][0]["delta_pp"], -1)
        self.add(4)  # No inline receipt for this interrupted turn.
        result, _ = self.observe(self.add(5), source(25, at=1300))
        self.assertEqual(result["rows"][0]["comparison"], "previous_unavailable")
        result, _ = self.observe(self.add(6), {})
        self.assertEqual(result["status"], "unavailable")
        result, _ = self.observe(self.add(7), source(30, at=1400))
        self.assertEqual(result["rows"][0]["comparison"], "previous_unavailable")

    def test_idempotent_claim_and_crash_have_no_second_rpc(self):
        key = self.add(1)
        first, _ = self.observe(key, source())
        again, reader = self.observe(key, source(50, at=1100))
        self.assertEqual(first, again)
        reader.assert_not_called()
        key = self.add(2)
        with patch.object(quota, "read_meter_sources", side_effect=RuntimeError("private-error")):
            self.assertEqual(quota.observe(self.root, key)["status"], "unavailable")
        result, reader = self.observe(key, source(30, at=1200))
        reader.assert_not_called()
        self.assertEqual(result["status"], "unavailable")

    def test_symlink_capacity_unknown_start_and_stop_never_call_source(self):
        self.assertEqual(quota.saved(self.root, stable_hash("missing"))["status"], "unavailable")
        result, reader = self.observe(stable_hash("missing"), source())
        reader.assert_not_called()
        key = self.add(1)
        with patch.object(quota, "MAX_ROWS", 0):
            result, reader = self.observe(key, source())
            reader.assert_not_called()
        (self.root / "auto-reports/quota.sqlite3").rename(self.root / "quota-real.sqlite3")
        (self.root / "auto-reports/quota.sqlite3").symlink_to(self.root / "quota-real.sqlite3")
        result, reader = self.observe(key, source())
        reader.assert_not_called()
        self.assertEqual(result["status"], "unavailable")

    def test_normalization_unknown_buckets_bad_fields_and_legacy_view(self):
        raw = source()
        bucket = raw["rate_limits"]["rateLimitsByLimitId"].pop("codex")
        del bucket["limitId"]
        raw["rate_limits"]["rateLimitsByLimitId"]["private-bucket"] = bucket
        result = quota._normalize(raw)
        self.assertEqual(result["rows"][0]["bucket"], "other")
        self.assertNotIn("private-bucket", json.dumps(result))
        for bad in (True, "20", -1, float("nan"), float("inf"), 10**1000):
            bucket["primary"]["usedPercent"] = bad
            self.assertIsNone(quota._normalize(raw)["rows"][0]["remaining_percent"])
        raw = source()
        raw["rate_limits"]["rateLimits"] = raw["rate_limits"].pop("rateLimitsByLimitId")["codex"]
        self.assertEqual(quota._normalize(raw)["rows"][0]["remaining_percent"], 80)

    def test_pro_and_plus_use_observed_windows_not_plan_based_assumptions(self):
        pro = source(plan="pro")
        primary = pro["rate_limits"]["rateLimitsByLimitId"]["codex"]["primary"]
        primary["windowDurationMins"] = 10080
        result = quota._compare(quota._normalize(pro), None, first=True)
        self.assertEqual(result["plan_type"], "pro")
        self.assertEqual([r["duration_minutes"] for r in result["rows"]], [10080])
        plus = source(plan="plus", at=1100)
        result = quota._compare(quota._normalize(plus), quota._normalize(pro))
        self.assertEqual(result["plan_type"], "plus")
        self.assertEqual(result["rows"][0]["duration_minutes"], 300)
        self.assertIsNone(result["rows"][0]["delta_pp"])
        # Pro can also report a 5h window; never replace it with a guessed week.
        result = quota._compare(quota._normalize(source(plan="pro")), None, first=True)
        self.assertEqual(result["rows"][0]["duration_minutes"], 300)


if __name__ == "__main__":
    unittest.main()
