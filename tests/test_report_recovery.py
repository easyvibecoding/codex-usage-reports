"""Missing prompt-hook recovery through the reporting interface used by Codex."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import test_auto_report as fixtures
from codex_usage_reports.auto_report import configure, handle, recent


class ReportRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.page = self.root / "source.jsonl"
        self.session, self.turn = "example-session", "example-turn"
        self.now = 1_000_000
        self.records = [
            {"type": "session_meta", "payload": {"id": self.session}},
            self.start(),
            {"type": "turn_context", "payload": {"turn_id": self.turn}},
            fixtures.counter(1000),
        ]
        self.write()
        self.payload = {"session_id": self.session, "turn_id": self.turn,
                        "transcript_path": str(self.page)}
        for target, value in (("time.time", self.now + 10), ("time.monotonic", 5000),
                              ("resolve_locale", {"locale": "en", "locale_source": "test"})):
            mock = patch("codex_usage_reports.auto_report." + target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)

    def start(self, turn=None, stamp=None):
        return {"type": "event_msg", "timestamp": datetime.fromtimestamp(
            self.now if stamp is None else stamp, timezone.utc).isoformat(),
            "payload": {"type": "task_started", "turn_id": turn or self.turn}}

    def write(self):
        self.page.write_text("\n".join(map(json.dumps, self.records)) + "\n")

    def event(self, event="PreToolUse", **extra):
        return handle({**self.payload, "hook_event_name": event, **extra}, self.data)

    def receipt(self):
        return json.loads(next((self.data / "auto-reports").glob("*.json")).read_text())

    def test_first_tool_recovers_original_start_and_whole_first_turn(self):
        for event in ("PreToolUse", "PostToolUse"):
            with self.subTest(event=event):
                self.data = self.root / event
                output = self.event(event, model="example-later-model")
                self.assertEqual(set(output), {"hookSpecificOutput"})
                self.assertEqual(output["hookSpecificOutput"]["hookEventName"], event)
                context = output["hookSpecificOutput"]["additionalContext"]
                self.assertEqual(context.count("--preview"), 1)
                self.assertEqual(recent(self.data)[0]["started"], self.now)
                self.assertIsNotNone(self.event("Stop"))
                receipt = self.receipt()
                self.assertTrue(receipt["start_recovered"])
                self.assertEqual(receipt["start_event"], event)
                self.assertIsNone(receipt["start_model"])
                self.assertEqual(receipt["elapsed_seconds"], 10)
                self.assertEqual(receipt["usage"]["total"], 1000)

    def test_existing_start_or_terminal_never_rereads_or_reinjects(self):
        self.event()
        with patch("codex_usage_reports.auto_report.snapshot", side_effect=AssertionError):
            for event in ("PreToolUse", "PostToolUse", "UserPromptSubmit"):
                self.assertIsNone(self.event(event))
        self.event("Interrupt")
        self.assertIsNone(self.event())
        self.assertEqual(recent(self.data)[0]["state"], "interrupted")

    def test_concurrent_tool_events_inject_exactly_once(self):
        # Concurrent first report starts race from an empty timing index.
        self.assertIsNone(self.event("SessionStart"))
        self.assertEqual(recent(self.data), [])
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda n: self.event(
                "PreToolUse" if n % 2 else "PostToolUse"), range(8)))
        self.assertEqual(sum(bool(value) for value in results), 1, results)
        self.assertEqual(len(recent(self.data)), 1)

    def test_later_turn_recovers_prior_counter_not_midturn_value(self):
        self.records[1] = self.start("example-prior-turn", self.now - 20)
        self.records[2]["payload"]["turn_id"] = "example-prior-turn"
        self.records += [self.start(), fixtures.counter(1400)]
        self.write()
        self.event()
        self.event("Stop")
        self.assertEqual(self.receipt()["usage"]["total"], 400)

    def test_no_counter_proof_stays_unknown_instead_of_zero(self):
        self.records[0]["payload"]["forked_from_id"] = "example-parent"
        self.write()
        self.assertIsNotNone(self.event())
        self.event("Stop")
        self.assertIsNone(self.receipt()["usage"])

    def test_recovers_native_turn_counter_even_without_old_counter(self):
        self.records[0]["payload"]["forked_from_id"] = "example-parent"
        self.records[-1] = fixtures.native_counter(self.session, self.turn, 1400,
                                                   request=400, turn_total=400)
        self.write()
        self.event()
        self.event("Stop")
        self.assertEqual(self.receipt()["usage"]["total"], 400)
        self.assertEqual(self.receipt()["usage_status"], "native_turn_counter")

    def test_missing_stale_future_or_ambiguous_start_does_not_activate(self):
        original = json.dumps(self.records)
        cases = ("missing", "foreign", "future", "no_timezone", "bad_time", "completed",
                 "aborted", "new_turn", "wrong_session", "broken", "tail_limited")
        for case in cases:
            with self.subTest(case=case):
                self.data = self.root / case
                self.records = json.loads(original)
                if case == "missing":
                    self.records.pop(1)
                elif case == "foreign":
                    self.records[1]["payload"]["turn_id"] = "example-other"
                elif case == "future":
                    self.records[1] = self.start(stamp=self.now + 100)
                elif case in ("no_timezone", "bad_time"):
                    self.records[1]["timestamp"] = "2000-01-01" if case == "no_timezone" else "bad"
                elif case in ("completed", "aborted"):
                    self.records.append({"type": "event_msg", "payload": {
                        "type": "task_complete" if case == "completed" else "turn_aborted"}})
                elif case == "new_turn":
                    self.records.append(self.start("example-other"))
                elif case == "wrong_session":
                    self.records[0]["payload"]["id"] = "example-other"
                elif case == "tail_limited":
                    self.records.append({"type": "response_item", "payload": {
                        "example": "x" * fixtures.SCAN_BYTES}})
                self.write()
                if case == "broken":
                    with self.page.open("a") as stream:
                        stream.write("not-json\n")
                self.assertIsNone(self.event())
                self.assertEqual(recent(self.data), [])

    def test_subagent_and_disabled_never_recover(self):
        self.assertIsNone(self.event(agent_id="example-child"))
        self.records[0]["payload"]["source"] = {"subagent": {"parent": self.session}}
        self.write()
        self.assertIsNone(self.event())
        self.assertEqual(recent(self.data), [])
        configure(self.data, enabled=False)
        self.assertIsNone(self.event())
        self.assertEqual(recent(self.data), [])


    def test_threshold_uses_recovered_time_and_storage_omits_payload(self):
        configure(self.data, enabled=True, threshold_seconds=5)
        self.event(tool_input={"command": "DO NOT STORE THIS"})
        self.event("Stop")
        self.assertEqual(recent(self.data)[0]["state"], "reported")
        with sqlite3.connect(self.data / "auto-reports/timing.sqlite3") as connection:
            baseline = connection.execute("SELECT baseline FROM turns").fetchone()[0]
        for private in (self.session, self.turn, str(self.page), "DO NOT STORE THIS"):
            self.assertNotIn(private, baseline)


if __name__ == "__main__":
    unittest.main()
