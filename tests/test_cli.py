"""Public CLI checks with a private synthetic native catalog and real receipts."""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from html import unescape
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/codex-usage-reports/lib"))

from codex_usage_reports.auto_report import handle  # noqa: E402
from codex_usage_reports.cli import main  # noqa: E402
from codex_usage_reports.task_report import build_task_report, render_task_report  # noqa: E402
from codex_usage_reports.util import data_path, stable_hash  # noqa: E402
from test_auto_report import counter, native_counter  # noqa: E402

TASK = "11111111-2222-4333-8444-555555555555"
OTHER = "22222222-2222-4333-8444-555555555555"


class CliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.native = self.root / "native"
        self.native.mkdir()
        self.data = self.root / "reports"
        self.page = self.native / "synthetic.jsonl"
        self.page.write_text(json.dumps({"type": "session_meta", "payload": {"id": TASK}})
                             + "\n" + json.dumps(counter(1000)) + "\n")
        with sqlite3.connect(self.native / "state_5.sqlite") as db:
            db.execute("CREATE TABLE threads (id TEXT,name TEXT,title TEXT,agent_nickname TEXT,"
                       "agent_role TEXT,agent_path TEXT,source TEXT,rollout_path TEXT)")
            db.execute("INSERT INTO threads VALUES (?,?,?,NULL,NULL,NULL,?,?)",
                       (TASK, "Example <Task> [link](example.invalid)", "PRIVATE TITLE",
                        "vscode", str(self.page)))
            db.execute("INSERT INTO threads VALUES (?,?,?,NULL,NULL,NULL,?,?)",
                       (OTHER, "Unrelated Example", "PRIVATE OTHER", "vscode", "/nonexistent"))
        self.payload = {"session_id": TASK, "turn_id": "example-turn", "transcript_path":
                        str(self.page), "prompt": "PRIVATE PROMPT"}
        locale = patch.dict(os.environ, {"CODEX_USAGE_REPORTS_LOCALE": "en"})
        locale.start()
        self.addCleanup(locale.stop)

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--data-dir", str(self.data), "--codex-home", str(self.native), *args])
        return code, out.getvalue(), err.getvalue()

    def record(self):
        handle({**self.payload, "hook_event_name": "UserPromptSubmit"}, self.data,
               home=self.native, wall=1_000_000, monotonic=5000)
        with self.page.open("a") as page:
            page.write(json.dumps({"type": "turn_context", "payload": {"turn_id": "example-turn",
                       "model": "example-model", "effort": "high"}}) + "\n")
            page.write(json.dumps(native_counter(TASK, "example-turn", 1400,
                                                 request=400, turn_total=400)) + "\n")
        handle({**self.payload, "hook_event_name": "Stop"}, self.data,
               home=self.native, wall=1_000_020, monotonic=5020)

    def test_settings_independent_default_on_and_threshold_preserves_disable(self):
        code, out, _ = self.invoke("auto-report", "status")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"enabled": True, "threshold_seconds": 0})
        self.assertFalse(self.data.exists())
        self.invoke("auto-report", "off")
        self.invoke("auto-report", "threshold", "60")
        self.assertFalse(json.loads(self.invoke("auto-report", "status")[1])["enabled"])
        self.invoke("auto-report", "on")
        self.assertEqual(json.loads(self.invoke("auto-report", "status")[1]),
                         {"enabled": True, "threshold_seconds": 60})
        self.assertEqual(self.invoke("auto-report", "threshold", "nan")[0], 1)
        with patch.dict(os.environ, {"CODEX_USAGE_REPORTS_HOME": str(self.data),
                                     "CODEX_RUN_BUDGET_HOME": str(self.root / "old")}):
            self.assertEqual(data_path(), self.data)
        self.assertFalse((self.root / "old").exists())

    def test_selected_task_native_cumulative_is_separate_from_turn_delta(self):
        self.record()
        code, output, err = self.invoke("task", TASK, "--format", "json")
        self.assertEqual((code, err), (0, ""))
        report = json.loads(output)
        self.assertEqual(report["task_usage"]["total"], 1400)
        self.assertEqual(report["turns"][0]["usage"]["total"], 400)
        self.assertEqual(report["parent_cache_read_share_percent"], 88.89)
        self.assertEqual(report["turns"][0]["parent_cache_read_share_percent"], 88.89)
        self.assertEqual(report["turns"][0]["contexts"][0]["model"], "example-model")
        self.assertFalse(report["history_complete"])
        for private in (TASK, OTHER, str(self.page), "PRIVATE PROMPT",
                        "PRIVATE TITLE", "Unrelated"):
            self.assertNotIn(private, output)
        self.assertFalse((self.native / "auto-reports").exists())

    def test_task_without_recorded_history_does_not_invent_zero(self):
        report = build_task_report(self.data, TASK, home=self.native)
        self.assertEqual(report["task_usage"]["total"], 1000)
        self.assertEqual(report["turns"], [])
        self.assertEqual(report["turn_recording_status"], "not_recorded")
        self.assertFalse(self.data.exists())
        self.page.write_text('{"type":"session_meta","payload":{"id":"foreign"}}\n')
        report = build_task_report(self.data, TASK, home=self.native)
        self.assertIsNone(report["task_usage"])
        self.assertEqual(report["usage_status"], "source_unavailable")

    def test_cli_can_select_subagent_without_adding_its_tokens_to_parent(self):
        child = "aaaaaaaa-1234-4234-8234-123456789abc"
        child_path = self.native / "synthetic-child.jsonl"
        source = {"subagent": {"thread_spawn": {"parent_thread_id": TASK}}}
        child_path.write_text("".join(json.dumps(record) + "\n" for record in (
            {"type": "session_meta", "payload": {"id": child, "source": source}},
            {"type": "event_msg", "payload": {"type": "task_started",
                                               "turn_id": "synthetic-child-turn"}},
            counter(300),
        )))
        with sqlite3.connect(self.native / "state_5.sqlite") as db:
            db.execute("INSERT INTO threads VALUES (?,?,?,NULL,NULL,NULL,?,?)",
                       (child, "Synthetic <child>", "PRIVATE CHILD TITLE",
                        json.dumps(source), str(child_path)))
        code, output, err = self.invoke("task", child, "--format", "json")
        self.assertEqual((code, err), (0, ""))
        report = json.loads(output)
        self.assertEqual(report["scope"], "selected_subagent")
        self.assertEqual(report["task"]["selector"], stable_hash(child)[:12])
        self.assertEqual(report["task"]["parent_hash"], stable_hash(TASK))
        self.assertEqual(report["task_usage"]["total"], 300)
        self.assertEqual(report["turns"], [])
        self.assertNotIn(child, output)
        self.assertNotIn(TASK, output)
        parent = build_task_report(self.data, TASK, home=self.native)
        self.assertEqual(parent["task_usage"]["total"], 1000)

    def test_formats_locale_safe_names_and_no_clobber(self):
        self.record()
        code, output, _ = self.invoke("--locale", "ja", "task", TASK)
        self.assertEqual(code, 0)
        self.assertIn("タスク使用量レポート", output)
        self.assertNotIn("[link]", output)
        self.assertNotIn("<Task>", output)
        target = self.root / "private/report.html"
        self.assertEqual(
            self.invoke("task", TASK, "--format", "html", "--output", str(target))[0], 0)
        html = target.read_text()
        self.assertIn("Content-Security-Policy", html)
        self.assertIn("&lt;Task&gt;", html)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.invoke("task", TASK, "--output", str(target))[0], 1)
        self.assertEqual(target.read_text(), html)
        link = self.root / "link"
        link.symlink_to(self.root / "private", target_is_directory=True)
        self.assertEqual(self.invoke("task", TASK, "--output", str(link / "new.md"))[0], 1)
        self.assertFalse((target.parent / "new.md").exists())
        self.assertEqual(os.environ["CODEX_USAGE_REPORTS_LOCALE"], "en")

    def test_partial_evidence_and_child_subtotal_are_visible_in_human_formats(self):
        self.record()
        report = build_task_report(self.data, TASK, home=self.native)
        report.update(counter_reset_observed=True, native_tail_limited=True)
        report["turns"][0].update(contexts_limited=True, usage_status="snapshot_incomplete",
                                  subagents={"status": "partial", "usage": {"total": 250},
                                             "agents_with_usage": 1, "agents_seen": 2,
                                             "pending_agents": 1, "missing_agents": 1})
        for format in ("markdown", "html"):
            output = render_task_report(report, format)
            for evidence in ("counter_reset_observed=true", "native_tail_limited=true",
                             "contexts_limited=true", "history_complete=false",
                             "snapshot_incomplete", "250"):
                self.assertIn(evidence, unescape(output))

    def test_task_errors_are_redacted_and_receipt_identity_is_checked(self):
        code, _, err = self.invoke("task", "PRIVATE BAD SELECTOR")
        self.assertEqual(code, 1)
        self.assertNotIn("PRIVATE", err)
        self.record()
        receipt = next((self.data / "auto-reports").glob("*.json"))
        value = json.loads(receipt.read_text())
        value["task_hash"] = "f" * 64
        receipt.write_text(json.dumps(value))
        report = build_task_report(self.data, TASK, home=self.native)
        self.assertEqual(report["turns"][0]["usage_status"], "receipt_unavailable")
        self.assertIsNone(report["turns"][0]["usage"])
        with self.assertRaises(ValueError):
            render_task_report(report, "script")
