"""Synthetic terminal-boundary evidence, independent from file timing or quietness."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_auto_report as fixtures
from codex_usage_reports.auto_report import _delta, snapshot

TASK = "example-completion-task"
TURN = "example-completion-turn"
STAMP = "2026-09-13T12:00:00+00:00"


def event(kind, turn=TURN, **payload):
    return {"type": "event_msg", "timestamp": STAMP,
            "payload": {"type": kind, "turn_id": turn, **payload}}


def context(turn=TURN, model="example-first-model"):
    return {"type": "turn_context", "timestamp": STAMP,
            "payload": {"turn_id": turn, "model": model, "effort": "low"}}


class CompletionSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic.jsonl"
        self.header = {"type": "session_meta", "payload": {"id": TASK}}
        self.write(self.header, event("task_started"), context())

    def write(self, *records):
        self.path.write_bytes(b"".join((json.dumps(record, ensure_ascii=False) + "\n").encode()
                                     for record in records))

    def append(self, *records):
        with self.path.open("ab") as stream:
            for record in records:
                stream.write((json.dumps(record, ensure_ascii=False) + "\n").encode())

    def completed(self):
        return snapshot(str(self.path), TURN, completed_only=True)

    def assertUnavailable(self, result, reason):
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["completion_observed"])
        self.assertEqual(result["completion_status"], reason)
        self.assertIsNone(result["usage"])
        self.assertEqual(result["contexts"], [])
        self.assertNotIn("completion_end", result)

    def test_late_final_request_requires_explicit_completion(self):
        before = snapshot(str(self.path), TURN)
        self.append(fixtures.native_counter(TASK, TURN, 1000))
        self.assertUnavailable(self.completed(), "completion_not_observed")
        self.append(fixtures.native_counter(TASK, TURN, 1400, request=400))
        self.assertUnavailable(self.completed(), "completion_not_observed")
        self.append(event("task_complete"))
        result = self.completed()
        self.assertEqual(result["status"], "observed")
        self.assertTrue(result["completion_observed"])
        self.assertEqual(result["completion_status"], "observed")
        self.assertEqual(result["completed_at"], STAMP)
        self.assertEqual(result["usage"]["total"], 1400)
        self.assertEqual(_delta(before, result)[0]["total"], 1400)
        self.assertTrue(result["first_turn_only"])
        self.assertFalse(result["fresh_turn_start"])

    def test_later_turn_counters_settings_and_broken_tail_cannot_leak(self):
        self.append(fixtures.native_counter(TASK, TURN, 1400), event("task_complete"))
        boundary = self.path.stat().st_size - 1
        original = self.completed()
        self.append(event("task_started", "example-later-turn"),
                    context("example-later-turn", "example-later-model"),
                    fixtures.counter(9999000),
                    fixtures.native_counter(TASK, "example-later-turn", 9999000))
        with self.path.open("ab") as stream:
            stream.write(b'{"incomplete":')
        result = self.completed()
        self.assertEqual(result["size"], boundary)
        self.assertEqual(result["completion_end"], boundary)
        self.assertLess(result["usage_end"], boundary)
        for key in ("usage", "turn_usage", "contexts", "contexts_limited", "first_turn_only"):
            self.assertEqual(result[key], original[key], key)
        self.assertNotIn("invalid_records", result)
        self.assertNotIn("example-later-model", json.dumps(result))

    def test_offset_counter_lanes_stop_at_completion_before_later_turn_counters(self):
        self.write(self.header, fixtures.counter(10800), event("task_started"), context())
        before = snapshot(str(self.path), TURN)
        self.append(fixtures.native_counter(TASK, TURN, 2000, request=200, turn_total=200),
                    fixtures.counter(11000),
                    fixtures.native_counter(TASK, TURN, 2300, request=300, turn_total=500))
        native_end = self.path.stat().st_size - 1
        self.append(fixtures.counter(11300), event("task_complete"))
        completion_end = self.path.stat().st_size - 1
        self.append(event("task_started", "example-next-turn"),
                    context("example-next-turn", "example-next-model"),
                    fixtures.native_counter(TASK, "example-next-turn", 9999000),
                    fixtures.counter(19999000))
        result = self.completed()
        self.assertTrue(result["completion_observed"])
        self.assertEqual(result["counter_source"], "native_request")
        self.assertEqual(result["usage"]["total"], 2300)
        self.assertEqual(result["turn_usage"]["total"], 500)
        self.assertEqual(result["usage_end"], native_end)
        self.assertEqual(result["size"], completion_end)
        self.assertEqual(result["completion_end"], completion_end)
        self.assertNotIn("counter_reset_end", result)
        self.assertEqual(result["contexts"][0]["model"], "example-first-model")
        usage, status = _delta(before, result)
        self.assertEqual((usage["total"], status), (500, "native_turn_counter"))

    def test_malformed_native_at_completion_never_reuses_earlier_native_or_event_value(self):
        self.append(fixtures.counter(10800))
        before = snapshot(str(self.path), TURN)
        broken = fixtures.native_counter(TASK, TURN, 2300, request=300, turn_total=500)
        broken["payload"]["turn_token_usage"] = None
        self.append(fixtures.native_counter(TASK, TURN, 2000, request=200, turn_total=200),
                    fixtures.counter(11000), broken, fixtures.counter(11300),
                    event("task_complete"))
        result = self.completed()
        self.assertTrue(result["completion_observed"])
        self.assertEqual(result["counter_source"], "native_request")
        self.assertIsNone(result["usage"])
        self.assertNotIn("turn_usage", result)
        self.assertIsNone(_delta(before, result)[0])

    def test_unscoped_later_same_task_counters_are_excluded(self):
        self.append(fixtures.counter(800), event("task_complete"))
        boundary = self.path.stat().st_size - 1
        self.append(fixtures.counter(9999000))
        result = self.completed()
        self.assertEqual(result["usage"]["total"], 800)
        self.assertEqual(result["size"], boundary)
        # Opting in to completion bounds must not change ordinary snapshots.
        current = snapshot(str(self.path), TURN)
        self.assertEqual(current["usage"]["total"], 9999000)
        self.assertEqual(current["size"], self.path.stat().st_size)
        self.assertNotIn("completion_observed", current)

    def test_missing_aborted_and_foreign_completion_are_not_terminal_proof(self):
        cases = (
            ([], "completion_not_observed"),
            ([event("turn_aborted")], "completion_aborted"),
            ([event("task_complete", "example-foreign-turn")], "completion_order_ambiguous"),
            ([event("task_complete", None)], "completion_order_ambiguous"),
            ([event("turn_aborted"), event("task_complete")], "completion_aborted"),
        )
        for suffix, reason in cases:
            with self.subTest(reason=reason, suffix=suffix):
                self.write(self.header, event("task_started"), fixtures.counter(800), *suffix)
                self.assertUnavailable(self.completed(), reason)

    def test_completion_cannot_bridge_missing_or_different_turn_start(self):
        cases = (
            ([self.header, fixtures.counter(800)], "completion_start_unobserved"),
            ([self.header, event("task_started", "example-other-turn")],
             "completion_start_unobserved"),
            ([self.header, event("task_started"), event("task_started", "example-other-turn")],
             "completion_order_ambiguous"),
            ([self.header, event("task_started"), event("task_started")],
             "completion_order_ambiguous"),
        )
        for prefix, reason in cases:
            with self.subTest(prefix=prefix):
                self.write(*prefix, event("task_complete"))
                self.assertUnavailable(self.completed(), reason)

    def test_foreign_task_or_turn_identity_never_supplies_counters(self):
        cases = [event("task_complete", thread_id="example-other-task"),
                 event("task_complete", session_id="example-other-task"),
                 event("task_complete", root_turn_id="example-other-turn"),
                 {"type": "session_meta", "payload": {"id": "example-other-task"}},
                 {**fixtures.counter(9999000), "payload": {
                     **fixtures.counter(9999000)["payload"], "thread_id": "example-other-task"}},
                 fixtures.native_counter(TASK, "example-other-turn", 9999000)]
        for record in cases:
            with self.subTest(record=record):
                self.write(self.header, event("task_started"), record, event("task_complete"))
                self.assertUnavailable(self.completed(), "completion_identity_ambiguous")

    def test_invalid_records_before_completion_remain_unavailable(self):
        invalids = (b'{"type":', b"not-json\n", b"[]\n",
                    b'{"type":"event_msg","payload":null}\n')
        for invalid in invalids:
            with self.subTest(invalid=invalid):
                self.write(self.header, event("task_started"), fixtures.counter(800))
                with self.path.open("ab") as stream:
                    stream.write(invalid)
                self.append(event("task_complete"))
                result = self.completed()
                self.assertUnavailable(result, "completion_structure_invalid")
                self.assertTrue(result["invalid_records"])

    def test_partial_completion_can_be_retried_after_record_finishes(self):
        self.append(fixtures.counter(800))
        completed_line = json.dumps(event("task_complete")).encode()
        with self.path.open("ab") as stream:
            stream.write(completed_line[:30])
        self.assertUnavailable(self.completed(), "completion_structure_invalid")
        with self.path.open("ab") as stream:
            stream.write(completed_line[30:] + b"\n")
        self.assertEqual(self.completed()["usage"]["total"], 800)

    def test_counter_reset_and_native_turn_inconsistency_stay_visible(self):
        before = snapshot(str(self.path), TURN)
        self.append(fixtures.native_counter(TASK, TURN, 1200),
                    fixtures.native_counter(TASK, TURN, 600), event("task_complete"))
        result = self.completed()
        self.assertTrue(result["completion_observed"])
        self.assertIn("counter_reset_end", result)
        self.assertNotIn("turn_usage", result)
        self.assertEqual(_delta(before, result), (None, "counter_reset_or_inconsistent"))
        self.assertFalse(result["first_turn_only"])

    def test_invalid_native_counter_does_not_become_complete_zero(self):
        request = fixtures.native_counter(TASK, TURN, 1200)
        request["payload"]["thread_token_usage"]["total_tokens"] = True
        self.append(request, event("task_complete"))
        result = self.completed()
        self.assertTrue(result["completion_observed"])
        self.assertIsNone(result["usage"])
        self.assertNotIn("turn_usage", result)
        self.assertFalse(result["first_turn_only"])

    def test_bounded_tail_requires_visible_context_and_marks_partial(self):
        padding = {"type": "response_item", "payload": {"type": "message", "text": "x" * 4096}}
        self.append(padding, context(), fixtures.native_counter(TASK, TURN, 1200),
                    event("task_complete"))
        with patch("codex_usage_reports.auto_report.SCAN_BYTES", 1500):
            result = self.completed()
        self.assertTrue(result["completion_observed"])
        self.assertTrue(result["tail_limited"])
        self.assertTrue(result["contexts_limited"])
        self.assertFalse(result["first_turn_only"])
        self.assertEqual(result["turn_usage"]["total"], 1200)
        self.write(self.header, event("task_started"), padding,
                   fixtures.native_counter(TASK, TURN, 1200), event("task_complete"))
        with patch("codex_usage_reports.auto_report.SCAN_BYTES", 1500):
            self.assertUnavailable(self.completed(), "completion_start_unobserved")

    def test_completion_outside_bounded_tail_is_unavailable(self):
        self.append(fixtures.counter(800), event("task_complete"),
                    {"type": "response_item", "payload": {"text": "x" * 4096}},
                    event("task_started", "example-later-turn"), fixtures.counter(9999000))
        with patch("codex_usage_reports.auto_report.SCAN_BYTES", 1500):
            result = self.completed()
        self.assertUnavailable(result, "completion_not_observed")
        self.assertTrue(result["tail_limited"])

    def test_timestamp_is_optional_validated_and_no_identity_is_persisted(self):
        for stamp in (None, 5, "bad", "2026-09-13T12:00:00", "x" * 1000, STAMP):
            with self.subTest(stamp=stamp):
                terminal = event("task_complete")
                terminal["timestamp"] = stamp
                self.write(self.header, event("task_started"), fixtures.counter(800), terminal)
                result = self.completed()
                self.assertTrue(result["completion_observed"])
                self.assertEqual("completed_at" in result, stamp == STAMP)
                for private in (TASK, TURN, str(self.path)):
                    self.assertNotIn(private, json.dumps(result))

    def test_invalid_source_and_incompatible_mode_explain_missing_completion(self):
        self.assertUnavailable(snapshot(None, TURN, completed_only=True), "unavailable")
        self.assertUnavailable(snapshot(str(self.path), TURN, at_turn_start=True,
                                        completed_only=True), "conflicting_snapshot_modes")
        self.path.write_text("not-json\n")
        self.assertUnavailable(self.completed(), "unavailable")


if __name__ == "__main__":
    unittest.main()
