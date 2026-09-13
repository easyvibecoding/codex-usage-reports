from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/codex-usage-reports/lib"))

from codex_usage_reports.auto_report import _turn_contexts  # noqa: E402


def context(turn="current", **values):
    return {"type": "turn_context", "payload": {"turn_id": turn, **values}}


def settings(**values):
    return {"type": "event_msg", "payload": {"type": "thread_settings_applied",
            "thread_id": "task", "thread_settings": values}}


class TurnContextsTest(unittest.TestCase):
    def parse(self, *records, **options):
        return _turn_contexts(list(records), "task", "current", **options)

    def test_each_turn_isolated_and_settings_refine_current_pair_in_order(self):
        records, limited = self.parse(
            context("old", model="gpt-6-astra", effort="xhigh"),
            context(model="gpt-5.6-sol", effort="low"),
            settings(reasoning_effort="high"),
            settings(reasoning_effort="high"),
            settings(model="gpt-6-astra", reasoning_effort="low"),
            settings(model="gpt-5.6-sol", reasoning_effort="low"),
        )
        self.assertEqual([(r["model"], r["reasoning_effort"]) for r in records], [
            ("gpt-5.6-sol", "low"), ("gpt-5.6-sol", "high"),
            ("gpt-6-astra", "low"), ("gpt-5.6-sol", "low"),
        ])
        self.assertFalse(limited)

    def test_no_global_or_previous_turn_fallback_and_scope_checked(self):
        foreign = settings(model="gpt-6-astra")
        foreign["payload"]["thread_id"] = "other-task"
        other_turn = settings(reasoning_effort="ultra")
        other_turn["payload"]["turn_id"] = "other-turn"
        records, _ = self.parse(context("old", model="gpt-6-astra", effort="high"),
                                context(effort="low"), foreign, other_turn)
        self.assertEqual(records, [{"model": None, "reasoning_effort": "low", "fast_mode": None}])
        self.assertEqual(self.parse(settings(model="gpt-6-astra"))[0], [])

    def test_initial_applied_settings_seed_only_the_immediate_matching_start(self):
        start = {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "current"}}
        records, _ = self.parse(settings(model="gpt-5.6-sol", reasoning_effort="medium"), start,
                                context(model="gpt-5.6-sol", effort="medium"))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["model"], "gpt-5.6-sol")

    def test_nested_settings_alias_conflicts_and_invalid_values_are_not_guessed(self):
        records, _ = self.parse(context(collaboration_mode={"settings": {
            "model": "gpt-5.6-sol", "reasoning_effort": "medium"}}),
            context(model="gpt-6-astra", effort="high", collaboration_mode={"settings": {
                "model": "gpt-5.6-sol", "reasoning_effort": "low"}}),
            settings(model="<private-script>", reasoning_effort={"bad": "value"}))
        self.assertEqual(records[0]["reasoning_effort"], "medium")
        self.assertIsNone(records[-1]["model"])
        self.assertIsNone(records[-1]["reasoning_effort"])
        self.assertNotIn("private", str(records))

    def test_configuration_update_only_changes_effort_within_active_turn(self):
        update = {"type": "response_item", "payload": {"type": "configuration_update",
                  "model": "unrelated-model", "reasoning": {"effort": "high"}}}
        records, _ = self.parse(update, context(model="gpt-6-astra", effort="low"), update,
                                context("later", model="gpt-5.6-sol"), update)
        self.assertEqual([(r["model"], r["reasoning_effort"]) for r in records],
                         [("gpt-6-astra", "low"), ("gpt-6-astra", "high")])

    def test_late_out_of_order_settings_and_bounded_history_remain_partial(self):
        initial = context(model="gpt-6-astra", effort="low")
        initial["timestamp"] = "2026-01-01T00:02:00Z"
        stale = settings(reasoning_effort="high")
        stale["timestamp"] = "2026-01-01T00:01:00Z"
        records, limited = self.parse(initial, stale)
        self.assertTrue(limited)
        self.assertEqual(records[-1]["reasoning_effort"], "low")
        changes = [settings(reasoning_effort="high" if i % 2 else "low") for i in range(30)]
        records, limited = self.parse(initial, *changes)
        self.assertTrue(limited)
        self.assertEqual(len(records), 16)
        self.assertEqual(records[-1]["reasoning_effort"], "high")

    def test_future_pending_and_top_level_alias_conflicts_are_not_applied_as_known(self):
        start = {"type": "event_msg", "timestamp": "2026-01-01T00:01:00Z",
                 "payload": {"type": "task_started", "turn_id": "current"}}
        future = settings(model="gpt-6-astra", reasoning_effort="high")
        future["timestamp"] = "2026-01-01T00:02:00Z"
        records, limited = self.parse(future, start)
        self.assertEqual(records, [])
        self.assertTrue(limited)
        conflict = settings(model="gpt-5.6-sol")
        conflict["payload"]["model"] = "gpt-6-astra"
        records, limited = self.parse(start, conflict)
        self.assertIsNone(records[-1]["model"])
        self.assertTrue(limited)


if __name__ == "__main__":
    unittest.main()
