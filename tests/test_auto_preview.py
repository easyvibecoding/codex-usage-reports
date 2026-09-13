from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/codex-usage-reports/lib"))

from codex_usage_reports.auto_preview import preview, render_card  # noqa: E402
from codex_usage_reports.auto_report import configure, handle, observed_total, recent  # noqa: E402
from codex_usage_reports.util import stable_hash  # noqa: E402
from test_auto_report import counter, native_counter  # noqa: E402

TASK = "00000000-0000-7000-8000-000000000001"


class AutoPreviewTest(unittest.TestCase):
    def setUp(self):
        quota_source = patch("codex_usage_reports.turn_quota.read_meter_sources", return_value={})
        quota_source.start()
        self.addCleanup(quota_source.stop)
        for module in ("auto_preview", "auto_report"):
            locale = patch("codex_usage_reports." + module + ".resolve_locale",
                           return_value={"locale": "zh-Hant", "locale_source": "test"})
            locale.start()
            self.addCleanup(locale.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / "data"
        self.workspace = self.root / "workspace"
        self.output = self.workspace / "visuals"
        self.transcript = self.root / "secret-source.jsonl"
        self.meta = {"type": "session_meta", "payload": {"id": TASK}}
        self.transcript.write_text(json.dumps(self.meta) + "\n" + json.dumps(counter(1000)) + "\n")
        self.db = sqlite3.connect(self.root / "state_5.sqlite")
        self.addCleanup(self.db.close)
        self.db.execute(
            "CREATE TABLE threads (id TEXT PRIMARY KEY,name TEXT,agent_nickname TEXT,"
            "agent_role TEXT,agent_path TEXT,source TEXT,rollout_path TEXT)"
        )
        self.db.execute("INSERT INTO threads VALUES (?, ?, NULL, NULL, NULL, 'vscode', ?)",
                        (TASK, '改善 <報告> $total', str(self.transcript)))
        self.db.commit()
        self.db.execute("ALTER TABLE threads ADD COLUMN cwd TEXT")
        self.db.execute("UPDATE threads SET cwd=? WHERE id=?", (str(self.workspace), TASK))
        self.db.commit()
        self.payload = {
            "session_id": TASK, "turn_id": "turn-1", "hook_event_name": "UserPromptSubmit",
            "transcript_path": str(self.transcript), "model": "gpt-6-astra",
        }
        handle(self.payload, self.data)
        with self.transcript.open("a") as output:
            output.write(json.dumps({"type": "turn_context", "payload": {
                "turn_id": "turn-1", "model": "gpt-6-astra", "effort": "xhigh"
            }}) + "\n" + json.dumps(counter(1500)) + "\n")

    def preview(self, **extra):
        return preview(self.data, TASK, "turn-1", output_dir=self.output, home=self.root, **extra)

    def test_inline_reference_real_snapshot_escaped_private_and_no_state_change(self):
        before = recent(self.data)
        result = self.preview()
        self.assertEqual(result["status"], "preview")
        self.assertLess(len(json.dumps(result)), 700)
        target = next(self.output.glob("*.html"))
        self.assertIn(str(target), result["reference"])
        content = target.read_text()
        for expected in ("500", "450", "400", "50", "25", "gpt-6-astra", "xhigh"):
            self.assertIn(expected, content)
        self.assertIn('data-metric="task-total">1,500</dd>', content)
        self.assertIn('data-metric="turn-delta">+500</dd>', content)
        self.assertNotIn("Fast", content)
        self.assertIn("改善 &lt;報告&gt; $total", content)
        for forbidden in (TASK, "secret-source", "<!doctype", "<html", "<script", "fetch("):
            self.assertNotIn(forbidden, content)
        self.assertEqual(recent(self.data), before)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        handle({**self.payload, "hook_event_name": "Stop"}, self.data)
        self.assertEqual(target.read_text(), content)
        self.assertEqual(recent(self.data)[0]["state"], "reported")

    def test_disable_and_threshold_avoid_preview_side_effects(self):
        configure(self.data, enabled=False)
        self.assertEqual(self.preview()["status"], "disabled")
        configure(self.data, enabled=True, threshold_seconds=300)
        self.assertEqual(self.preview()["status"], "below_threshold")
        self.assertFalse(self.output.exists())

    def test_wrong_turn_source_and_subagent_do_not_leak_another_task(self):
        self.assertEqual(preview(self.data, TASK, "other", output_dir=self.output,
                                 home=self.root)["status"], "no_active_start")
        self.transcript.write_text('{}\n')
        self.assertEqual(self.preview()["status"], "source_unavailable")
        self.db.execute("UPDATE threads SET source=?", (json.dumps({"subagent": {}}),))
        self.db.commit()
        self.assertEqual(self.preview()["status"], "subagent")
        self.assertFalse(self.output.exists())

    def test_foreign_start_cannot_render_a_restored_source_or_collect_quota(self):
        other_data = self.root / "foreign-start-data"
        self.transcript.write_text(json.dumps({"type": "session_meta", "payload": {
            "id": "example-foreign-task"}}) + "\n")
        handle(self.payload, other_data)
        self.transcript.write_text(json.dumps(self.meta) + "\n" + json.dumps(counter(9000)) + "\n")
        with patch("codex_usage_reports.turn_quota.observe") as quota, patch(
            "codex_usage_reports.auto_preview.collect"
        ) as children:
            result = preview(other_data, TASK, "turn-1", output_dir=self.output, home=self.root)
        self.assertEqual(result["status"], "source_unavailable")
        quota.assert_not_called()
        children.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_missing_counter_is_not_zero_and_symlink_output_is_rejected(self):
        self.transcript.write_text(json.dumps(self.meta) + "\n")
        self.preview()
        card = next(self.output.glob("*.html")).read_text()
        self.assertIn('data-metric="task-total">未觀測</dd>', card)
        self.assertIn('data-metric="turn-delta">未觀測</dd>', card)
        link = self.root / "link"
        link.symlink_to(self.output)
        with self.assertRaises(ValueError):
            preview(self.data, TASK, "turn-1", output_dir=link, home=self.root)

    def test_desktop_readable_root_required_before_quota_or_child_collection(self):
        outside = (
            self.root / ".local/state/example/reports",
            self.root / "workspace-other/reports",
            self.root / "visualizations/1970/01/01/00000000-0000-7000-8000-000000000002",
            self.root / "visualizations/1970/01/02" / TASK,
            self.workspace / ".." / "private-reports",
            Path("relative/reports"),
        )
        with patch("codex_usage_reports.turn_quota.observe") as quota, patch(
            "codex_usage_reports.auto_preview.collect"
        ) as children:
            for directory in outside:
                with self.subTest(directory=directory), self.assertRaises(ValueError):
                    preview(self.data, TASK, "turn-1", output_dir=directory, home=self.root)
                self.assertFalse(directory.exists())
            quota.assert_not_called()
            children.assert_not_called()
        self.assertFalse(self.workspace.exists())

    def test_native_visualization_root_uses_task_uuid_utc_date(self):
        directory = self.root / "visualizations/1970/01/01" / TASK
        self.db.execute("UPDATE threads SET cwd=NULL")
        self.db.commit()
        result = preview(self.data, TASK, "turn-1", output_dir=directory, home=self.root)
        target = next(directory.glob("*.html"))
        reference = json.loads(result["reference"].split("\ue202")[1][:-1])
        self.assertEqual(reference, {"path": str(target)})
        self.assertRegex(target.name, r"^[a-z0-9]+(?:-[a-z0-9]+)*\.html$")
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            self.preview()

    def test_symlink_ancestor_does_not_authorize_an_external_directory(self):
        self.workspace.mkdir()
        (self.workspace / "alias").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            preview(self.data, TASK, "turn-1", output_dir=self.workspace / "alias/reports",
                    home=self.root)
        self.assertFalse((self.root / "reports").exists())

    def test_path_is_rechecked_after_collection(self):
        self.workspace.mkdir()
        with patch("codex_usage_reports.turn_quota.observe", side_effect=lambda *a, **k:
                   self.output.symlink_to(self.root, target_is_directory=True)):
            with self.assertRaises(ValueError):
                self.preview()
        self.assertFalse(list(self.root.glob("*.html")))

    def test_incomplete_scope_is_not_promoted_to_complete_or_zero(self):
        zero = {key: 0 for key in ("total", "input", "cached_input", "output", "reasoning_output")}
        self.assertEqual(observed_total(None, {"status": "none", "usage": zero}), (None, False))
        for status in ("observed", "partial", "unavailable"):
            self.assertEqual(observed_total(None, {"status": status, "usage": zero}), (None, False))
        for issue in ("pending_agents", "missing_agents", "selection_limited"):
            self.assertFalse(observed_total(zero, {"status": "observed", "usage": zero,
                                                  issue: 1})[1])

    def test_first_turn_pending_preview_and_separate_stop_settlement(self):
        turn = "fresh-turn"
        self.transcript.write_text("".join(json.dumps(row) + "\n" for row in (
            self.meta,
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn}},
            {"type": "turn_context", "payload": {"turn_id": turn}},
        )))
        payload = {**self.payload, "turn_id": turn}
        handle(payload, self.data)
        result = preview(self.data, TASK, turn, output_dir=self.output, home=self.root)
        self.assertEqual(result["status"], "preview")
        card_path = next(self.output.glob("*.html"))
        card = card_path.read_text()
        self.assertIn('data-metric="task-total">未觀測</dd>', card)
        self.assertIn('data-metric="turn-delta">等待用量寫入</dd>', card)
        self.assertIn("首筆請求用量尚未寫入", card)
        self.assertNotIn('data-metric="turn-delta">0</dd>', card)
        with self.transcript.open("a") as output:
            output.write(json.dumps(counter(1200)) + "\n")
        handle({**payload, "hook_event_name": "Stop"}, self.data, home=self.root)
        receipt = json.loads(next((self.data / "auto-reports").glob("*.json")).read_text())
        self.assertEqual(receipt["usage"]["total"], 1200)
        self.assertEqual(receipt["usage_status"], "verified_first_turn_counter")
        self.assertEqual(card_path.read_text(), card)

    def test_first_request_is_visible_inline_before_cumulative_event(self):
        turn = "fresh-request-turn"
        self.transcript.write_text("".join(json.dumps(row) + "\n" for row in (
            self.meta,
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn}},
            {"type": "turn_context", "payload": {"turn_id": turn}},
        )))
        payload = {**self.payload, "turn_id": turn}
        handle(payload, self.data)
        with self.transcript.open("a") as output:
            output.write(json.dumps(native_counter(TASK, turn, 1200)) + "\n")
        result = preview(self.data, TASK, turn, output_dir=self.output, home=self.root)
        self.assertEqual(result["status"], "preview")
        card_path = next(self.output.glob("*.html"))
        card = card_path.read_text()
        self.assertIn('data-metric="task-total">1,200</dd>', card)
        self.assertIn('data-metric="turn-delta">+1,200</dd>', card)
        self.assertNotIn("等待用量寫入", card)
        with self.transcript.open("a") as output:
            output.write(json.dumps(native_counter(TASK, turn, 1500,
                                                   request=300, turn_total=1500)) + "\n")
            output.write(json.dumps(counter(1500)) + "\n")
        handle({**payload, "hook_event_name": "Stop"}, self.data, home=self.root)
        receipt = json.loads(next((self.data / "auto-reports").glob("*.json")).read_text())
        self.assertEqual(receipt["usage"]["total"], 1500)
        self.assertEqual(card_path.read_text(), card)

    def test_child_stop_and_final_card_merge_without_model_self_report(self):
        child = "aaaaaaaa-1234-1234-1234-123456789abc"
        source = {"subagent": {"thread_spawn": {"parent_thread_id": TASK}}}
        transcript = self.root / "private-child.jsonl"
        stamp = datetime.fromtimestamp(time.time(), timezone.utc).isoformat()
        request = {"type": "token_usage_record", "timestamp": stamp, "payload": {
            "thread_id": child, "turn_id": "child-turn", "response_id": "private-response",
            "usage": {"total_tokens": 200, "input_tokens": 180, "output_tokens": 20,
                      "cached_input_tokens": 100, "reasoning_output_tokens": 10},
        }}
        records = [
            {"type": "session_meta", "timestamp": stamp, "payload": {
                "id": child, "source": source}}, request,
            {"type": "event_msg", "timestamp": stamp, "payload": {
                "type": "task_complete", "turn_id": "child-turn"}},
        ]
        transcript.write_text("".join(json.dumps(row) + "\n" for row in records))
        self.db.execute("INSERT INTO threads VALUES (?, ?, ?, NULL, NULL, ?, ?, NULL)",
                        (child, None, '<測試代理> $total', json.dumps(source), str(transcript)))
        self.db.commit()
        child_stop = {"session_id": TASK, "turn_id": "child-turn", "agent_id": child,
                      "hook_event_name": "SubagentStop", "agent_transcript_path": str(transcript)}
        self.assertIsNone(handle(child_stop, self.data, home=self.root))
        self.assertFalse(list((self.data / "auto-reports").glob("*.json")))
        result = self.preview()
        self.assertEqual(result["status"], "preview")
        card = next(self.output.glob("*.html")).read_text()
        for expected in ("700", "500", "200", "&lt;測試代理&gt; $total", "改善 &lt;報告&gt;"):
            self.assertIn(expected, card)
        self.assertIn('data-metric="task-total">1,500</dd>', card)
        self.assertIn('data-metric="turn-delta">+500</dd>', card)
        self.assertNotIn('data-metric="turn-delta">+700</dd>', card)
        for private in (child, "private-child", "private-response"):
            self.assertNotIn(private, card)
        handle({**self.payload, "hook_event_name": "Stop"}, self.data, home=self.root)
        receipt = json.loads(next((self.data / "auto-reports").glob("*.json")).read_text())
        self.assertTrue(receipt["subagents_included"])
        self.assertEqual(receipt["subagents"]["usage"]["total"], 200)
        self.assertEqual(receipt["usage"]["total"], 500)
        self.assertEqual(next(self.output.glob("*.html")).read_text(), card)

    def test_card_preserves_paired_turn_switches_and_does_not_render_fast(self):
        contexts = [
            {"model": "example-a", "reasoning_effort": "low", "fast_mode": True},
            {"model": "example-b", "reasoning_effort": "high", "fast_mode": False},
            {"model": "example-a", "reasoning_effort": "low", "fast_mode": None},
        ]
        zero = dict.fromkeys(("total", "input", "cached_input", "output", "reasoning_output"), 0)
        current = {"task_hash": stable_hash(TASK), "usage": {**zero, "total": 1500},
                   "contexts": contexts, "contexts_limited": True}
        with patch("codex_usage_reports.auto_preview.snapshot", return_value=current), patch(
            "codex_usage_reports.auto_preview._delta", return_value=({**zero, "total": 500}, "test")
        ), patch("codex_usage_reports.auto_preview.render_card", wraps=render_card) as renderer:
            self.preview()
        self.assertEqual(renderer.call_args.args[0]["task_usage"], {**zero, "total": 1500})
        self.assertTrue(renderer.call_args.args[0]["contexts_limited"])
        card = next(self.output.glob("*.html")).read_text()
        ordered = (
            '<ol class="report-context-list"><li>example-a · 思考 low</li>'
            '<li>example-b · 思考 high</li><li>example-a · 思考 low</li></ol>'
        )
        self.assertIn(ordered, card)
        self.assertIn("切換紀錄可能不完整", card)
        self.assertNotIn("Fast", card)

    def test_legacy_card_missing_task_total_and_empty_context_remain_unknown(self):
        receipt = {
            "key": "example", "task_name": "Example task", "locale": "zh-Hant",
            "usage": {key: 0 for key in (
                "total", "input", "cached_input", "output", "reasoning_output"
            )}, "contexts": [], "elapsed_seconds": 0, "captured_at": "12:00:00 UTC",
            "subagents": {"status": "none"},
        }
        card = render_card(receipt)
        self.assertIn('data-metric="task-total">未觀測</dd>', card)
        self.assertIn('data-metric="turn-delta">0</dd>', card)
        self.assertIn('<p class="report-context-single">未觀測</p>', card)
        self.assertNotIn("Fast", card)

    def test_old_task_counter_without_this_turn_write_is_pending_not_zero(self):
        lines = self.transcript.read_text().splitlines()
        self.transcript.write_text("\n".join(lines[:-1]) + "\n")
        self.preview()
        card = next(self.output.glob("*.html")).read_text()
        self.assertIn('data-metric="task-total">1,000</dd>', card)
        self.assertIn('data-metric="turn-delta">等待用量寫入</dd>', card)
        self.assertNotIn('data-metric="turn-delta">0</dd>', card)

    def test_quota_read_stays_in_one_preview_and_stop_reuses_same_capture(self):
        from test_turn_quota import source

        with patch("codex_usage_reports.turn_quota.read_meter_sources",
                   return_value=source()) as reader:
            self.preview()
            card = next(self.output.glob("*.html")).read_text()
            self.assertIn("80%", card)
            self.assertIn("Pro", card)
            handle({**self.payload, "hook_event_name": "Stop"}, self.data, home=self.root)
            reader.assert_called_once()
        receipt = json.loads(next((self.data / "auto-reports").glob("*.json")).read_text())
        self.assertEqual(receipt["quota"]["rows"][0]["remaining_percent"], 80)
        self.assertFalse(receipt["native_quota_refreshed"])
        self.assertEqual(next(self.output.glob("*.html")).read_text(), card)



if __name__ == "__main__":
    unittest.main()
