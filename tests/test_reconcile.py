"""Completion reconciliation through public entry points and a real hook child."""
from __future__ import annotations

import io
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import test_auto_report as fixtures
from codex_usage_reports.auto_report import configure, handle, recent
from codex_usage_reports.hook_adapter import main as hook_main
from codex_usage_reports.reconcile import _DeadlineExpired, run, schedule
from codex_usage_reports.task_report import build_task_report
from codex_usage_reports.util import stable_hash

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "plugins/codex-usage-reports/scripts/hook.py"
TASK = "11111111-2222-4333-8444-555555555555"
TURN = "example-reconciliation-turn"


class ReconcileTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "synthetic-codex-home"
        self.home.mkdir()
        self.data = self.root / "reports"
        self.path = self.home / "synthetic.jsonl"
        self.now = time.time()
        self.header = {"type": "session_meta", "payload": {"id": TASK}}
        self.write(self.header, fixtures.counter(1000), self.native_event("task_started"),
                   {"type": "turn_context", "payload": {
                       "turn_id": TURN, "model": "example-model", "effort": "low"}})
        with sqlite3.connect(self.home / "state_5.sqlite") as db:
            db.execute("CREATE TABLE threads (id TEXT,name TEXT,title TEXT,agent_nickname TEXT,"
                       "agent_role TEXT,agent_path TEXT,source TEXT,rollout_path TEXT)")
            db.execute("INSERT INTO threads VALUES (?,?,?,NULL,NULL,NULL,?,?)",
                       (TASK, "Synthetic example", "EXAMPLE PRIVATE TITLE", "vscode",
                        str(self.path)))
        self.payload = {"session_id": TASK, "turn_id": TURN, "transcript_path": str(self.path),
                        "prompt": "EXAMPLE PRIVATE PROMPT"}
        self.stop = {**self.payload, "hook_event_name": "Stop"}
        self.key = stable_hash([TASK, TURN])
        self.directory = self.data / "auto-reports"
        locale = patch.dict(os.environ, {"CODEX_USAGE_REPORTS_LOCALE": "en"})
        locale.start()
        self.addCleanup(locale.stop)

    def native_event(self, kind, *, turn=TURN, stamp=None, **extra):
        return {"type": "event_msg", "timestamp": datetime.fromtimestamp(
            self.now if stamp is None else stamp, timezone.utc).isoformat(),
            "payload": {"type": kind, "turn_id": turn, **extra}}

    def write(self, *records):
        self.path.write_text("".join(json.dumps(record) + "\n" for record in records))

    def append(self, *records):
        with self.path.open("a") as stream:
            for record in records:
                stream.write(json.dumps(record) + "\n")

    def begin(self):
        return handle({**self.payload, "hook_event_name": "UserPromptSubmit"}, self.data,
                      home=self.home, wall=self.now, monotonic=5000)

    def stopped(self):
        self.assertIsNotNone(self.begin())
        self.append(fixtures.counter(1200))
        result = handle(self.stop, self.data, home=self.home,
                        wall=self.now + 1, monotonic=5001)
        self.assertIn("systemMessage", result)
        self.original = {extension: self.artifact(extension).read_bytes()
                         for extension in ("json", "html", "md")}
        self.assertEqual(json.loads(self.original["json"])["usage"]["total"], 200)

    def artifact(self, extension, *, revised=False):
        suffix = ".reconciled." if revised else "."
        return self.directory / (self.key + suffix + extension)

    def complete(self):
        self.append(fixtures.native_counter(TASK, TURN, 1500, request=300, turn_total=500),
                    self.native_event("task_complete", stamp=self.now + 2))

    def queued(self):
        process = Mock()
        with patch("codex_usage_reports.reconcile.subprocess.Popen", return_value=process) as spawn:
            self.assertTrue(schedule(self.stop, self.data, home=self.home))
        self.assertEqual(spawn.call_count, 1)
        return spawn, process

    def job(self):
        with sqlite3.connect(self.directory / "timing.sqlite3") as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM reconciliations WHERE key=?", (self.key,)).fetchone()
            return dict(row) if row else None

    def assertStopUnchanged(self, *, markdown=True):
        for extension in ("json", "html", "md") if markdown else ("json", "html"):
            self.assertEqual(self.artifact(extension).read_bytes(), self.original[extension])

    def assertNoRevision(self):
        self.assertEqual(list(self.directory.glob("*.reconciled.*")), [])
        self.assertEqual(recent(self.data)[0]["report"], self.key + ".md")

    def test_late_final_counter_waits_for_completion_then_preserves_stop_artifacts(self):
        self.stopped()
        self.queued()
        calls = 0

        def append_late(_delay):
            nonlocal calls
            calls += 1
            self.assertNoRevision()
            if calls == 1:
                self.append(fixtures.native_counter(TASK, TURN, 1500,
                                                    request=300, turn_total=500))
            else:
                self.append(self.native_event("task_complete", stamp=self.now + 2))

        with patch("codex_usage_reports.reconcile.time.sleep", side_effect=append_late):
            result = run(self.payload, self.data, home=self.home, delays=(0, 0))
        self.assertEqual(result, {"status": "complete", "attempts": 2})
        revised = json.loads(self.artifact("json", revised=True).read_text())
        self.assertEqual(revised["revision"], 2)
        self.assertEqual(revised["usage"]["total"], 500)
        self.assertEqual(revised["task_usage"]["total"], 1500)
        self.assertFalse(revised["final_usage_may_not_yet_be_persisted"])
        self.assertTrue(revised["completion_observed"])
        self.assertEqual(revised["usage_status"], "native_turn_counter")
        self.assertStopUnchanged()
        self.assertNotEqual(self.artifact("md").read_bytes(),
                            self.artifact("md", revised=True).read_bytes())
        self.assertEqual(self.job()["state"], "complete")
        self.assertEqual(recent(self.data)[0]["report"], self.key + ".reconciled.md")

    def test_later_turn_large_counter_is_excluded_and_task_report_selects_revision_two(self):
        self.stopped()
        self.queued()
        self.complete()
        self.append(self.native_event("task_started", turn="example-next-turn"),
                    {"type": "turn_context", "payload": {
                        "turn_id": "example-next-turn", "model": "example-next-model"}},
                    fixtures.counter(9999000))
        self.assertEqual(run(self.payload, self.data, home=self.home,
                             delays=(0,))["status"], "complete")
        report = build_task_report(self.data, TASK, home=self.home)
        self.assertEqual(report["task_usage"]["total"], 9999000)
        turn = report["turns"][0]
        self.assertEqual(turn["revision"], 2)
        self.assertEqual(turn["reconciliation_status"], "complete")
        self.assertEqual(turn["usage"]["total"], 500)
        self.assertEqual(turn["task_usage"]["total"], 1500)
        self.assertEqual(turn["contexts"][0]["model"], "example-model")
        self.assertStopUnchanged()

    def test_subagent_stop_reconciles_its_own_turn_without_parent_tokens(self):
        child = "aaaaaaaa-1234-4234-8234-123456789abc"
        child_turn = "child-turn"
        child_path = self.home / "synthetic-child.jsonl"
        source = {"subagent": {"thread_spawn": {"parent_thread_id": TASK}}}
        child_path.write_text("".join(json.dumps(record) + "\n" for record in (
            {"type": "session_meta", "payload": {"id": child, "source": source,
                                                 "forked_from_id": TASK,
                                                 "agent_path": "/root/synthetic-child"}},
            self.header,
            self.native_event("task_started", turn=TURN),
            fixtures.counter(900_000),  # Copied parent history is never child usage.
            self.native_event("task_started", turn=child_turn, thread_id=child,
                              session_id=TASK),
            {"type": "turn_context", "payload": {"turn_id": child_turn,
                                               "thread_id": child, "session_id": TASK}},
        )))
        with sqlite3.connect(self.home / "state_5.sqlite") as db:
            db.execute("INSERT INTO threads VALUES (?,?,?,NULL,NULL,NULL,?,?)",
                       (child, "Synthetic child", "EXAMPLE PRIVATE CHILD TITLE",
                        json.dumps(source), str(child_path)))
        start = {"hook_event_name": "SubagentStart", "session_id": TASK,
                 "agent_id": child, "turn_id": child_turn,
                 "transcript_path": str(child_path)}
        self.assertIn("hookSpecificOutput", handle(start, self.data, home=self.home,
                                                   wall=self.now, monotonic=5000))
        with sqlite3.connect(self.directory / "timing.sqlite3") as db:
            baseline = json.loads(db.execute("SELECT baseline FROM turns WHERE key=?",
                                             (stable_hash([child, child_turn]),)).fetchone()[0])
        self.assertIsNone(baseline["usage"])
        first = fixtures.native_counter(child, child_turn, 1200)
        first["payload"].update(session_id=TASK, root_turn_id=TURN)
        first["timestamp"] = datetime.fromtimestamp(self.now + .5, timezone.utc).isoformat()
        with child_path.open("a") as stream:
            stream.write(json.dumps(first) + "\n")
        stop = {**start, "hook_event_name": "SubagentStop", "transcript_path": str(self.path),
                "agent_transcript_path": str(child_path)}
        self.assertIn("systemMessage", handle(stop, self.data, home=self.home,
                                               wall=self.now + 1, monotonic=5001))
        child_key = stable_hash([child, child_turn])
        original_path = self.directory / (child_key + ".json")
        original = json.loads(original_path.read_text())
        self.assertEqual(original["scope"], "agent_turn_stop_boundary")
        self.assertEqual(original["usage"]["total"], 1200)
        self.assertEqual(original["root_hash"], stable_hash(TASK))
        self.assertEqual(original["direct_parent_hash"], stable_hash(TASK))
        self.assertEqual(original["task"]["selector"], stable_hash(child)[:12])
        with patch("codex_usage_reports.reconcile.subprocess.Popen", return_value=Mock()):
            self.assertTrue(schedule(stop, self.data, home=self.home))
        last = fixtures.native_counter(child, child_turn, 1500, request=300,
                                       turn_total=1500)
        last["payload"].update(session_id=TASK, root_turn_id=TURN)
        last["timestamp"] = datetime.fromtimestamp(self.now + 1.5, timezone.utc).isoformat()
        with child_path.open("a") as stream:
            stream.write(json.dumps(last) + "\n")
            stream.write(json.dumps(self.native_event(
                "task_complete", turn=child_turn, stamp=self.now + 2,
                thread_id=child, session_id=TASK, root_turn_id=TURN)) + "\n")
        self.assertEqual(run(stop, self.data, home=self.home, delays=(0,))["status"],
                         "complete")
        revised = json.loads((self.directory / (child_key + ".reconciled.json")).read_text())
        self.assertEqual(revised["scope"], "agent_turn_completion_boundary")
        self.assertEqual(revised["revision"], 2)
        self.assertEqual(revised["usage"]["total"], 1500)
        self.assertEqual(revised["task_usage"]["total"], 1500)
        self.assertEqual(original_path.read_text(), json.dumps(original, ensure_ascii=True,
                                                              indent=2))
        selected = build_task_report(self.data, child, home=self.home)
        self.assertEqual(selected["task_usage"]["total"], 1500)
        self.assertEqual(selected["turns"][0]["revision"], 2)
        self.assertEqual(selected["turns"][0]["usage"]["total"], 1500)
        for private in (child, TASK, str(child_path)):
            self.assertNotIn(private, original_path.read_text())
            self.assertNotIn(private, json.dumps(revised))

    def test_child_completion_requires_start_identity_even_with_valid_stop_receipt(self):
        changes = ("missing-root", "wrong-root", "missing-parent", "wrong-parent",
                   "wrong-task", "changed-root", "changed-parent", "child-to-root",
                   "root-to-child", "explicit-parent", "unknown-role", "null-role",
                   "no-role-evidence", "legacy-child", "legacy-root", "control")
        for change in changes:
            with self.subTest(change=change):
                fixture = fixtures.child_fixture(self.root / change,
                                                 as_root=change in ("root-to-child",
                                                 "no-role-evidence", "legacy-root"))
                self.assertIn("hookSpecificOutput", fixture["start"])
                stop = {**fixture["payload"], "hook_event_name": "Stop"}
                self.assertIn("systemMessage", handle(stop, fixture["data"],
                              home=fixture["home"], wall=1001, monotonic=1001))
                directory = fixture["data"] / "auto-reports"
                original = {extension: (directory / (fixture["key"] + "." + extension)).read_bytes()
                            for extension in ("json", "md", "html")}
                self.assertTrue(json.loads(original["json"])["source_identity_verified"])
                fixtures.change_child_identity(fixture, change)
                stop = {**fixture["payload"], "hook_event_name": "Stop"}
                with patch("codex_usage_reports.reconcile.subprocess.Popen", return_value=Mock()):
                    self.assertTrue(schedule(stop, fixture["data"], home=fixture["home"]))
                with fixture["path"].open("a") as stream:
                    if change != "child-to-root":
                        stream.write(json.dumps(fixtures.counter(300)) + "\n")
                    stream.write(json.dumps({"type": "event_msg", "timestamp":
                        "2026-01-01T00:00:02+00:00", "payload": {
                            "type": "task_complete", "turn_id": fixture["turn"],
                            "thread_id": fixture["child"], "session_id": stop["session_id"]
                        }}) + "\n")
                result = run(stop, fixture["data"], home=fixture["home"], delays=(0,))
                if change in ("control", "legacy-child", "legacy-root"):
                    self.assertEqual(result["status"], "complete")
                    revised = json.loads((directory / (fixture["key"] +
                                                       ".reconciled.json")).read_text())
                    self.assertEqual(revised["scope"], "user_turn_completion_boundary" if
                                     change == "legacy-root" else "agent_turn_completion_boundary")
                    self.assertEqual(revised["usage"]["total"], 200)
                    self.assertEqual(revised["task_usage"]["total"], 300)
                else:
                    self.assertEqual(result, {"status": "failed", "attempts": 0})
                    self.assertEqual(list(directory.glob("*.reconciled.*")), [])
                    self.assertEqual(recent(fixture["data"])[0]["report"], fixture["key"] + ".md")
                for extension, content in original.items():
                    self.assertEqual((directory / (fixture["key"] + "." + extension)).read_bytes(),
                                     content)

    def test_timeout_leaves_stop_report_available(self):
        self.stopped()
        self.queued()
        result = run(self.payload, self.data, home=self.home, delays=(0, 0))
        self.assertEqual(result, {"status": "expired", "attempts": 2})
        self.assertEqual(self.job()["state"], "expired")
        self.assertStopUnchanged()
        self.assertNoRevision()
        report = build_task_report(self.data, TASK, home=self.home)
        self.assertEqual(report["turns"][0]["revision"], 1)

    def test_excess_delays_never_expand_the_eight_snapshot_attempt_limit(self):
        self.stopped()
        self.queued()
        from codex_usage_reports.auto_report import snapshot

        with patch("codex_usage_reports.reconcile.snapshot", wraps=snapshot) as observe:
            result = run(self.payload, self.data, home=self.home, delays=(0,) * 20)
        self.assertEqual(result, {"status": "expired", "attempts": 8})
        self.assertEqual(observe.call_count, 8)
        self.assertEqual(self.job()["attempts"], 8)
        self.assertStopUnchanged()
        self.assertNoRevision()

    def test_deadline_bypasses_collectors_ordinary_exception_handler(self):
        self.stopped()
        self.queued()
        self.complete()
        swallowed = False

        def ordinary_error_collector(*_args, **_kwargs):
            nonlocal swallowed
            try:
                raise _DeadlineExpired()
            except Exception:
                swallowed = True
                return {"status": "none"}

        with patch("codex_usage_reports.reconcile.collect", ordinary_error_collector):
            result = run(self.payload, self.data, home=self.home, delays=(0,))
        self.assertFalse(swallowed)
        self.assertEqual(result, {"status": "expired", "attempts": 1})
        self.assertEqual(self.job()["state"], "expired")
        self.assertStopUnchanged()
        self.assertNoRevision()

    @unittest.skipUnless(hasattr(signal, "SIGALRM") and hasattr(signal, "setitimer"),
                         "requires POSIX alarm support")
    def test_real_worker_alarm_interrupts_slow_collector_and_restores_handler(self):
        self.stopped()
        self.queued()
        self.complete()
        script = self.root / "synthetic-deadline-worker.py"
        script.write_text(textwrap.dedent("""\
            import json
            import signal
            import sys
            import time
            from pathlib import Path
            from unittest.mock import patch

            sys.path.insert(0, sys.argv[1])
            from codex_usage_reports import reconcile

            entered = False
            swallowed = False

            def slow_collector(*args, **kwargs):
                global entered, swallowed
                entered = True
                try:
                    time.sleep(5)
                except Exception:
                    swallowed = True
                    return {"status": "none"}
                raise AssertionError("worker alarm did not interrupt the collector")

            def fast_alarm(seconds):
                signal.setitimer(signal.ITIMER_REAL, 0.3 if seconds else 0)
                return 0

            previous = signal.getsignal(signal.SIGALRM)
            started = time.monotonic()
            with patch.object(reconcile, "collect", slow_collector), \\
                    patch.object(reconcile.signal, "alarm", fast_alarm):
                result = reconcile.worker(json.load(sys.stdin), Path(sys.argv[2]),
                                          home=Path(sys.argv[3]))
            print(json.dumps({"result": result, "entered": entered, "swallowed": swallowed,
                              "elapsed": time.monotonic() - started,
                              "handler_restored": signal.getsignal(signal.SIGALRM) == previous,
                              "alarm_remaining": signal.getitimer(signal.ITIMER_REAL)[0]}))
            """))
        child = subprocess.run(
            [sys.executable, "-I", str(script),
             str(ROOT / "plugins/codex-usage-reports/lib"), str(self.data), str(self.home)],
            input=json.dumps(self.payload), text=True, capture_output=True, timeout=3,
        )
        self.assertEqual((child.returncode, child.stderr), (0, ""))
        evidence = json.loads(child.stdout)
        self.assertTrue(evidence["entered"])
        self.assertFalse(evidence["swallowed"])
        self.assertTrue(evidence["handler_restored"])
        self.assertEqual(evidence["alarm_remaining"], 0)
        self.assertLess(evidence["elapsed"], 1.5)
        self.assertEqual(evidence["result"], {"status": "expired", "attempts": 1})
        self.assertEqual(self.job()["state"], "expired")
        self.assertStopUnchanged()
        self.assertNoRevision()

    def test_disabled_reporting_never_spawns_and_queued_worker_exits(self):
        self.stopped()
        configure(self.data, enabled=False)
        with patch("codex_usage_reports.reconcile.subprocess.Popen") as spawn:
            self.assertFalse(schedule(self.stop, self.data, home=self.home))
            spawn.assert_not_called()
        configure(self.data, enabled=True)
        self.queued()
        configure(self.data, enabled=False)
        self.assertEqual(run(self.payload, self.data, home=self.home, delays=(0,)),
                         {"status": "disabled", "attempts": 0})
        self.assertEqual(self.job()["state"], "disabled")
        self.assertStopUnchanged()
        self.assertNoRevision()

    def test_disable_while_worker_waits_prevents_publication(self):
        self.stopped()
        self.queued()

        def disable(_delay):
            configure(self.data, enabled=False)
            self.complete()

        with patch("codex_usage_reports.reconcile.time.sleep", side_effect=disable):
            result = run(self.payload, self.data, home=self.home, delays=(0,))
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(self.job()["state"], "disabled")
        self.assertStopUnchanged()
        self.assertNoRevision()

    def test_duplicate_schedule_and_run_never_rewrite_or_spawn_again(self):
        self.stopped()
        self.queued()
        self.complete()
        with patch("codex_usage_reports.reconcile.subprocess.Popen") as spawn:
            self.assertFalse(schedule(self.stop, self.data, home=self.home))
            self.assertEqual(run(self.payload, self.data, home=self.home,
                                 delays=(0,))["status"], "complete")
            saved = {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
                     for path in self.directory.glob("*.reconciled.*")}
            self.assertEqual(run(self.payload, self.data, home=self.home, delays=(0,)),
                             {"status": "not_queued"})
            self.assertFalse(schedule(self.stop, self.data, home=self.home))
            spawn.assert_not_called()
        self.assertEqual(saved, {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
                                 for path in self.directory.glob("*.reconciled.*")})
        self.assertEqual(self.job()["attempts"], 1)

    def test_foreign_source_identity_does_not_publish_or_replace_stop(self):
        self.stopped()
        self.queued()
        self.write({"type": "session_meta", "payload": {"id": "example-foreign-task"}},
                   self.native_event("task_started"), fixtures.counter(9999000),
                   self.native_event("task_complete", stamp=self.now + 2))
        self.assertEqual(run(self.payload, self.data, home=self.home,
                             delays=(0,))["status"], "failed")
        self.assertEqual(self.job()["state"], "failed")
        self.assertStopUnchanged()
        self.assertNoRevision()

    def test_counter_reset_publishes_partial_without_inventing_delta(self):
        self.stopped()
        self.queued()
        self.append(fixtures.native_counter(TASK, TURN, 1200, request=200, turn_total=200),
                    fixtures.native_counter(TASK, TURN, 600, request=100, turn_total=100),
                    self.native_event("task_complete", stamp=self.now + 2))
        self.assertEqual(run(self.payload, self.data, home=self.home,
                             delays=(0,))["status"], "partial")
        revised = json.loads(self.artifact("json", revised=True).read_text())
        self.assertEqual(revised["revision"], 2)
        self.assertIsNone(revised["usage"])
        self.assertEqual(revised["usage_status"], "counter_reset_or_inconsistent")
        self.assertTrue(revised["final_usage_may_not_yet_be_persisted"])
        self.assertEqual(self.job()["state"], "partial")
        self.assertStopUnchanged()

    def test_user_modified_markdown_is_preserved_while_revision_is_available(self):
        self.stopped()
        self.queued()
        changed = b"# Synthetic user notes\nKeep this text exactly.\n"
        self.artifact("md").write_bytes(changed)
        self.complete()
        self.assertEqual(run(self.payload, self.data, home=self.home,
                             delays=(0,))["status"], "complete")
        self.assertEqual(self.artifact("md").read_bytes(), changed)
        self.assertStopUnchanged(markdown=False)
        report = build_task_report(self.data, TASK, home=self.home)
        self.assertEqual(report["turns"][0]["revision"], 2)

    def test_user_edit_during_publication_is_preserved(self):
        self.stopped()
        self.queued()
        self.complete()
        changed = b"# Synthetic concurrent edit\nWritten during revision publication.\n"
        real_open = os.open
        edited = False

        def write_user_edit(path, flags, *args, **kwargs):
            nonlocal edited
            if Path(path) == self.artifact("html", revised=True):
                self.artifact("md").write_bytes(changed)
                edited = True
            return real_open(path, flags, *args, **kwargs)

        with patch("codex_usage_reports.reconcile.os.open", side_effect=write_user_edit):
            result = run(self.payload, self.data, home=self.home, delays=(0,))
        self.assertTrue(edited)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(self.artifact("md").read_bytes(), changed)
        self.assertStopUnchanged(markdown=False)
        self.assertEqual(self.job()["state"], "complete")
        self.assertEqual(recent(self.data)[0]["report"], self.key + ".reconciled.md")
        report = build_task_report(self.data, TASK, home=self.home)
        self.assertEqual(report["turns"][0]["revision"], 2)

    def test_symlink_cannot_overwrite_external_markdown(self):
        self.stopped()
        self.queued()
        outside = self.root / "outside.md"
        outside.write_bytes(b"Synthetic external document\n")
        self.artifact("md").unlink()
        self.artifact("md").symlink_to(outside)
        self.complete()
        result = run(self.payload, self.data, home=self.home, delays=(0,))
        self.assertEqual(outside.read_bytes(), b"Synthetic external document\n")
        self.assertTrue(self.artifact("md").is_symlink())
        self.assertStopUnchanged(markdown=False)
        self.assertEqual(self.job()["state"], result["status"])

    def test_existing_revision_target_cannot_be_clobbered_or_mark_job_complete(self):
        self.stopped()
        self.queued()
        sentinel = b"Synthetic unrelated existing HTML\n"
        self.artifact("html", revised=True).write_bytes(sentinel)
        self.complete()
        self.assertEqual(run(self.payload, self.data, home=self.home,
                             delays=(0,))["status"], "failed")
        self.assertEqual(self.job()["state"], "failed")
        self.assertEqual(self.artifact("html", revised=True).read_bytes(), sentinel)
        self.assertStopUnchanged()
        self.assertEqual(recent(self.data)[0]["report"], self.key + ".md")

    def test_native_selectors_are_stdin_only_and_absent_from_job_database(self):
        self.stopped()
        spawn, process = self.queued()
        command = spawn.call_args.args[0]
        transmitted = json.loads(process.stdin.write.call_args.args[0])
        self.assertEqual(transmitted, {key: self.payload[key]
                                      for key in ("session_id", "turn_id", "transcript_path")})
        process.stdin.close.assert_called_once()
        for private in (TASK, TURN, str(self.path), self.payload["prompt"]):
            self.assertNotIn(private, " ".join(command))
        self.complete()
        run(self.payload, self.data, home=self.home, delays=(0,))
        database = self.directory / "timing.sqlite3"
        with sqlite3.connect(database) as db:
            dump = "\n".join(db.iterdump())
        for private in (TASK, TURN, str(self.path), self.payload["prompt"]):
            self.assertNotIn(private, dump)
            self.assertNotIn(private.encode(), database.read_bytes())

    def test_spawn_error_keeps_stop_hook_successful_and_report_available(self):
        handle({**self.payload, "hook_event_name": "UserPromptSubmit"}, self.data, home=self.home)
        self.append(fixtures.counter(1200))
        out = io.StringIO()
        source = io.TextIOWrapper(io.BytesIO(json.dumps(self.stop).encode()))
        with patch.object(sys, "argv", [str(HOOK), "--event", "Stop", "--data-dir", str(self.data),
                                       "--codex-home", str(self.home)]), patch.object(
            sys, "stdin", source
        ), patch("codex_usage_reports.reconcile.subprocess.Popen",
                 side_effect=OSError("example")), redirect_stdout(out):
            code = hook_main()
        self.assertEqual(code, 0)
        response = json.loads(out.getvalue())
        self.assertIn("systemMessage", response)
        self.assertNotIn("decision", response)
        self.assertNotIn("continue", response)
        self.assertEqual(recent(self.data)[0]["state"], "reported")
        self.assertEqual(self.job()["state"], "failed")
        self.assertTrue(self.artifact("json").is_file())
        self.assertNoRevision()

    def test_real_stop_hook_returns_before_child_observes_late_completion(self):
        env = {**os.environ, "CODEX_HOME": str(self.home),
               "CODEX_USAGE_REPORTS_LOCALE": "en"}

        def invoke(event):
            return subprocess.run(
                [sys.executable, "-I", str(HOOK), "--event", event, "--data-dir", str(self.data),
                 "--codex-home", str(self.home)],
                input=json.dumps({**self.payload, "hook_event_name": event}), text=True,
                capture_output=True, env=env, timeout=5,
            )

        started = invoke("UserPromptSubmit")
        self.assertEqual((started.returncode, started.stderr), (0, ""))
        self.assertIn("hookSpecificOutput", json.loads(started.stdout))
        self.append(fixtures.counter(1200))
        began = time.monotonic()
        stopped = invoke("Stop")
        elapsed = time.monotonic() - began
        self.assertEqual((stopped.returncode, stopped.stderr), (0, ""))
        self.assertIn("systemMessage", json.loads(stopped.stdout))
        self.assertLess(elapsed, 2, "Stop waited for the background reconciliation window")
        self.assertNoRevision()
        self.original = {extension: self.artifact(extension).read_bytes()
                         for extension in ("json", "html", "md")}
        timer = threading.Timer(0.6, self.complete)
        timer.start()
        self.addCleanup(timer.join, 2)
        deadline = time.monotonic() + 10
        try:
            while time.monotonic() < deadline:
                state = self.job()
                if state and state["state"] not in ("queued", "running"):
                    break
                time.sleep(0.05)
            self.assertEqual(self.job()["state"], "complete", self.job())
            revised = json.loads(self.artifact("json", revised=True).read_text())
            self.assertEqual(revised["usage"]["total"], 500)
            self.assertEqual(revised["revision"], 2)
            self.assertStopUnchanged()
        finally:
            configure(self.data, enabled=False)


if __name__ == "__main__":
    unittest.main()
