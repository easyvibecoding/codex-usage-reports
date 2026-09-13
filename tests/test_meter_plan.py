from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "codex-usage-reports"
sys.path.insert(0, str(PLUGIN / "lib"))

from codex_usage_reports.meter_plan import (  # noqa: E402
    normalize_subscription,
    plan_type,
)


class MeterPlanTest(unittest.TestCase):
    def test_plan_type_accepts_only_live_schema_enum(self) -> None:
        self.assertEqual(plan_type("plus"), "plus")
        self.assertEqual(plan_type("enterprise_cbp_usage_based"), "enterprise_cbp_usage_based")
        for value in ("unknown", "pro-plus", "private-tier", "", None, 1, [], {}):
            with self.subTest(value=value):
                self.assertIsNone(plan_type(value))

    def test_native_plan_is_reported_and_quota_plans_are_sorted(self) -> None:
        result = normalize_subscription(
            {
                "account": {
                    "type": "chatgpt",
                    "planType": "plus",
                    "email": "private@example.com",
                }
            },
            [
                {"limit_id": "spark", "plan_type": "plus"},
                {"limit_id": "codex", "plan_type": "plus"},
            ],
        )
        self.assertEqual(result["auth_type"], "chatgpt")
        self.assertEqual(result["plan_type"], "plus")
        self.assertEqual(result["status"], "reported")
        self.assertEqual(result["source"], "account/read")
        self.assertEqual(result["quota_plan_types"], ["plus"])
        self.assertIsNone(result["tier_multiplier"])
        self.assertNotIn("private@example.com", json.dumps(result))

    def test_native_and_bucket_plan_conflict_is_explicit(self) -> None:
        result = normalize_subscription(
            {"account": {"type": "chatgpt", "planType": "plus"}},
            [{"limit_id": "codex", "plan_type": "pro"}],
        )
        self.assertEqual(result["plan_type"], "plus")
        self.assertEqual(result["status"], "conflicted")
        self.assertEqual(result["source"], "account/read")
        self.assertEqual(result["quota_plan_types"], ["pro"])

    def test_bucket_fallback_requires_one_allowlisted_plan(self) -> None:
        result = normalize_subscription(
            {"account": {"type": "chatgpt"}},
            [{"limit_id": "codex", "plan_type": "pro"}],
        )
        self.assertEqual(result["auth_type"], "chatgpt")
        self.assertEqual(result["plan_type"], "pro")
        self.assertEqual(result["status"], "fallback")
        self.assertEqual(result["source"], "quota_buckets")

        ambiguous = normalize_subscription(
            None,
            [
                {"limit_id": "codex", "plan_type": "pro"},
                {"limit_id": "spark", "plan_type": "plus"},
            ],
        )
        self.assertIsNone(ambiguous["plan_type"])
        self.assertEqual(ambiguous["status"], "conflicted")
        self.assertEqual(ambiguous["source"], "quota_buckets")
        self.assertEqual(ambiguous["quota_plan_types"], ["plus", "pro"])

    def test_non_chatgpt_routes_do_not_claim_bucket_subscription(self) -> None:
        for auth_type in ("apiKey", "amazonBedrock"):
            with self.subTest(auth_type=auth_type):
                result = normalize_subscription(
                    {"account": {"type": auth_type}},
                    [{"limit_id": "codex", "plan_type": "pro"}],
                )
                self.assertEqual(result["auth_type"], auth_type)
                self.assertIsNone(result["plan_type"])
                self.assertEqual(result["status"], "conflicted")
                self.assertEqual(result["source"], "account/read")

                route_only = normalize_subscription({"account": {"type": auth_type}}, [])
                self.assertEqual(route_only["status"], "reported")
                self.assertEqual(route_only["source"], "account/read")

    def test_unknown_native_values_never_leak_or_override_fallback(self) -> None:
        result = normalize_subscription(
            {
                "account": {
                    "type": "private-auth-type",
                    "planType": "private-plan-name",
                    "email": "private@example.com",
                },
                "accountId": "private-account-id",
            },
            [
                {"limit_id": "codex", "plan_type": "pro"},
                {"limit_id": "spark", "plan_type": "private-bucket-plan"},
            ],
        )
        self.assertIsNone(result["auth_type"])
        self.assertEqual(result["plan_type"], "pro")
        self.assertEqual(result["status"], "fallback")
        self.assertEqual(result["source"], "quota_buckets")
        self.assertEqual(result["quota_plan_types"], ["pro"])
        blob = json.dumps(result)
        for private in (
            "private-auth-type",
            "private-plan-name",
            "private@example.com",
            "private-account-id",
        ):
            self.assertNotIn(private, blob)

    def test_missing_subscription_is_unavailable(self) -> None:
        result = normalize_subscription({"account": None}, [])
        self.assertEqual(
            result,
            {
                "auth_type": None,
                "plan_type": None,
                "status": "unavailable",
                "source": "none",
                "quota_plan_types": [],
                "tier_multiplier": None,
                "limitations": result["limitations"],
            },
        )
        self.assertTrue(result["limitations"])


if __name__ == "__main__":
    unittest.main()
