"""Subprocess coverage of the shipped report-only hook and pinned runtime."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/codex-usage-reports"


class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "cache/version"
        (self.source / "runtime").mkdir(parents=True)
        shutil.copyfile(PLUGIN / "runtime/hook.pyz", self.source / "runtime/hook.pyz")
        self.data = self.root / "reports"
        self.env = {**os.environ, "PLUGIN_ROOT": str(self.source),
                    "CODEX_USAGE_REPORTS_HOME": str(self.data),
                    "CODEX_HOME": str(self.root / "native"),
                    "CODEX_USAGE_REPORTS_LOCALE": "en"}
        self.hooks = json.loads((PLUGIN / "hooks/hooks.json").read_text())["hooks"]
        self.digest = hashlib.sha256((PLUGIN / "runtime/hook.pyz").read_bytes()).hexdigest()
        self.saved = self.data / "runtimes" / (self.digest + ".pyz")
        self.page = self.root / "synthetic.jsonl"
        self.page.write_text(json.dumps({"type": "session_meta", "payload": {
            "id": "example-bootstrap-task"}}) + "\n")

    def invoke(self, event, *, raw=None, **extra):
        command = [sys.executable, "-I", "-c",
                   (PLUGIN / "scripts/bootstrap.py").read_text(), event, self.digest]
        payload = {"session_id": "example-bootstrap-task", "hook_event_name": event,
                   "transcript_path": str(self.page), "turn_id": "example-turn", **extra}
        result = subprocess.run(command, input=raw if raw is not None else json.dumps(payload),
                                text=True, capture_output=True, env=self.env, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        value = json.loads(result.stdout)
        self.assertNotIn("decision", value)
        self.assertNotIn("continue", value)
        self.assertNotIn("stopReason", value)
        self.assertNotIn("permissionDecision", value.get("hookSpecificOutput", {}))
        return value

    def test_pinned_reports_survive_plugin_cache_eviction(self):
        started = self.invoke("UserPromptSubmit", prompt="PRIVATE EXAMPLE INPUT")
        self.assertIn("--preview", started["hookSpecificOutput"]["additionalContext"])
        self.assertIn(str(self.saved), started["hookSpecificOutput"]["additionalContext"])
        self.assertTrue(self.saved.is_file())
        self.source.rename(self.root / "evicted")
        stopped = self.invoke("Stop")
        self.assertIn("systemMessage", stopped)
        files = list((self.data / "auto-reports").glob("*.json"))
        self.assertEqual(len(files), 1)
        receipt = files[0].read_text()
        for private in ("PRIVATE EXAMPLE INPUT", str(self.page), "example-bootstrap-task"):
            self.assertNotIn(private, receipt)
        self.assertFalse(list(self.data.rglob("*ledger*")))

    def test_missing_and_corrupt_runtime_never_block_any_event(self):
        (self.source / "runtime/hook.pyz").write_bytes(b"not a valid pinned archive")
        for event in self.hooks:
            with self.subTest(event=event):
                self.assertEqual(self.invoke(event), {})
        self.assertFalse(self.saved.exists())

    def test_saved_runtime_ignores_changed_cache_and_recovers_from_pinned_source(self):
        self.invoke("UserPromptSubmit")
        self.saved.write_bytes(b"corrupt")
        self.assertEqual(self.invoke("PreToolUse"), {})
        self.assertEqual(hashlib.sha256(self.saved.read_bytes()).hexdigest(), self.digest)
        (self.source / "runtime/hook.pyz").write_bytes(b"new release")
        self.assertIn("systemMessage", self.invoke("Stop"))

    def test_invalid_input_and_disabled_reporting_are_nonblocking(self):
        for raw in ("not json", "[]", "{}", "x" * (2 * 1024 * 1024 + 1)):
            self.assertEqual(self.invoke("UserPromptSubmit", raw=raw), {})
        self.assertEqual(self.invoke("UserPromptSubmit", hook_event_name="Stop"), {})
        self.data.mkdir(exist_ok=True)
        (self.data / "auto-report.json").write_text(
            '{"enabled": false, "threshold_seconds": 0}'
        )
        self.assertEqual(self.invoke("UserPromptSubmit"), {})
        self.assertFalse((self.data / "auto-reports").exists())

    def test_symlink_runtime_directory_never_executes_untrusted_file(self):
        self.data.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (self.data / "runtimes").symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.invoke("UserPromptSubmit"), {})
        self.assertEqual(list(outside.iterdir()), [])
