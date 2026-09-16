"""Synthetic update/trust checks; no network, native account, or real Task data."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
NAME = "codex-usage-reports"
PLUGIN = ROOT / "plugins" / NAME
sys.path.insert(0, str(PLUGIN / "lib"))
from codex_usage_reports import update_notice as notice  # noqa: E402

NATIVE_STATUS = notice.hook_status
FETCH_LATEST = notice.fetch_latest


class UpdateNoticeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.plugin = self.root / "plugin"
        (self.plugin / ".codex-plugin").mkdir(parents=True)
        (self.plugin / "hooks").mkdir()
        self.manifest = self.plugin / ".codex-plugin/plugin.json"
        self.manifest.write_text(json.dumps({"name": NAME, "version": "1.0.0"}))
        (self.plugin / "hooks/hooks.json").write_text('{"hooks": {}}')
        self.data = self.root / "state"
        self.env = {"PLUGIN_ROOT": str(self.plugin),
                    NAME.upper().replace("-", "_") + "_HOME": str(self.data),
                    "CODEX_HOME": str(self.root / "native")}
        self.payload = {"hook_event_name": "UserPromptSubmit", "session_id": "example-task",
                        "cwd": str(self.root), "prompt": "EXAMPLE PRIVATE PROMPT"}
        self.patch_env = patch.dict(os.environ, self.env)
        self.patch_env.start()
        self.addCleanup(self.patch_env.stop)
        self.remote = patch.object(notice, "fetch_latest", return_value="1.1.0").start()
        self.trust = patch.object(notice, "hook_status", return_value={
            "status": "trusted", "enabled": 8, "needs_review": 0}).start()
        self.addCleanup(patch.stopall)

    def test_first_prompt_once_per_task_and_installation(self):
        result = notice.hook(NAME, self.payload)
        self.assertIn("Update available", result["systemMessage"])
        self.assertIn("/hooks", result["systemMessage"])
        self.assertEqual(notice.hook(NAME, self.payload), {})
        self.assertEqual(self.trust.call_count, 1)
        self.assertEqual(self.remote.call_count, 1)
        # A new Task uses the cached version but checks native trust afresh.
        notice.hook(NAME, {**self.payload, "session_id": "example-second-task"})
        self.assertEqual(self.remote.call_count, 1)
        self.assertEqual(self.trust.call_count, 2)
        self.manifest.write_text(json.dumps({"name": NAME, "version": "1.0.1"}))
        self.assertIn("Update available", notice.hook(NAME, self.payload)["systemMessage"])
        # No native identifier, prompt, or path is persisted, including SQLite pages.
        raw = (self.data / "update-notices.sqlite3").read_bytes()
        for private in ("example-task", "example-second-task", "EXAMPLE PRIVATE PROMPT",
                        str(self.root)):
            self.assertNotIn(private.encode(), raw)
        self.assertEqual((self.data / "update-notices.sqlite3").stat().st_mode & 0o777, 0o600)

    def test_updated_hooks_notice_survives_missing_network(self):
        self.remote.side_effect = TimeoutError()
        self.trust.return_value = {"status": "needs_review", "enabled": 8, "needs_review": 7}
        result = notice.hook(NAME, self.payload)
        self.assertIn("7 hooks", result["systemMessage"])
        self.assertIn("codex → /hooks", result["systemMessage"])
        self.assertNotIn("Update available", result["systemMessage"])
        self.assertEqual(set(result), {"systemMessage", "hookSpecificOutput"})
        self.assertEqual(set(result["hookSpecificOutput"]),
                         {"hookEventName", "additionalContext"})

    def test_current_ahead_unknown_and_build_metadata(self):
        db = notice.state(self.data)
        self.addCleanup(db.close)
        for remote, expected in (("1.0.0+build", "current"), ("0.9.0", "ahead"),
                                 ("1.1.0", "update_available"), (None, "unknown")):
            with self.subTest(remote=remote):
                self.remote.return_value = remote
                result = notice.check(db, NAME, self.plugin, self.root, self.root,
                                      refresh=True)
                self.assertEqual(result["release_status"], expected)
                self.assertEqual(bool(result["messages"]), expected == "update_available")
        for invalid in (None, "1.0.0-rc.1", "1.0", "1.2.3\nPRIVATE", "01.2.3"):
            self.assertIsNone(notice.version(invalid))

    def test_cache_expiry_failure_backoff_and_offline(self):
        db = notice.state(self.data)
        self.addCleanup(db.close)
        self.assertEqual(notice.latest(db, NAME, 100), ("1.1.0", "remote"))
        self.remote.side_effect = TimeoutError()
        self.assertEqual(notice.latest(db, NAME, 101, offline=True), ("1.1.0", "cached"))
        self.assertEqual(notice.latest(db, NAME, 100 + notice.TTL), (None, "unavailable"))
        self.assertEqual(notice.latest(db, NAME, 101 + notice.TTL), (None, "unavailable"))
        self.assertEqual(self.remote.call_count, 2)
        notice.latest(db, NAME, 1000 + notice.TTL, offline=True)
        self.assertEqual(self.remote.call_count, 2)

    def test_disable_subagent_other_events_and_missing_identity_do_not_check(self):
        for extra in ({"agent_id": "example-child"}, {"session_id": ""},
                      {"hook_event_name": "PostToolUse"}):
            self.assertEqual(notice.hook(NAME, {**self.payload, **extra}), {})
        with patch.dict(os.environ, {"CODEX_PLUGIN_UPDATE_NOTICES": "0"}):
            self.assertEqual(notice.hook(NAME, self.payload), {})
        with patch.dict(os.environ, {NAME.upper().replace("-", "_") + "_UPDATE_NOTICES": "0"}):
            self.assertEqual(notice.hook(NAME, self.payload), {})
        self.remote.assert_not_called()
        self.trust.assert_not_called()
        self.assertFalse(self.data.exists())

    def test_native_status_scopes_plugin_and_preserves_unknown(self):
        def record(trust, enabled=True, plugin=NAME):
            return {"pluginId": f"{plugin}@{plugin}", "trustStatus": trust, "enabled": enabled}
        self.assertEqual(notice.summarize_hooks({}, NAME)["status"], "unknown")
        self.assertEqual(notice.summarize_hooks([record("trusted"), record("modified", False),
                                               record("untrusted", plugin="other")], NAME),
                         {"status": "trusted", "enabled": 1, "needs_review": 0})
        self.assertEqual(notice.summarize_hooks([record("modified")], NAME)["status"],
                         "needs_review")
        self.assertEqual(notice.summarize_hooks([record("future_status")], NAME)["status"],
                         "unknown")
        self.assertEqual(notice.summarize_hooks([record("trusted", False)], NAME)["status"],
                         "disabled")
        self.assertEqual(notice.summarize_hooks({"hooks": [record("trusted")],
                                                "errors": ["example"]}, NAME)["status"], "unknown")

    def test_unsafe_state_paths_are_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.data.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            notice.state(self.data)
        self.data.unlink()
        self.data.mkdir()
        (self.data / "update-notices.sqlite3-journal").symlink_to(outside / "private")
        with self.assertRaises(ValueError):
            notice.state(self.data)
        self.assertEqual(list(outside.iterdir()), [])

    def test_claim_is_shared_between_connections(self):
        first, second = notice.state(self.data), notice.state(self.data)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        self.assertTrue(notice.claim(first, "example-task", "a" * 64, time.time()))
        self.assertFalse(notice.claim(second, "example-task", "a" * 64, time.time()))

    def test_native_rpc_timeout_is_unknown_and_reaped(self):
        binary = self.root / "codex"
        marker = self.root / "started"
        binary.write_text(f"#!{sys.executable}\nimport os, time\n"
                          f"open({str(marker)!r}, 'w').write(str(os.getpid()))\n"
                          "time.sleep(10)\n")
        binary.chmod(0o700)
        started = time.monotonic()
        with patch.dict(os.environ, {"PATH": str(self.root)}):
            result = NATIVE_STATUS(NAME, self.root, self.root / "native")
        self.assertEqual(result, {"status": "unknown"})
        self.assertLess(time.monotonic() - started, 2.5)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)

    def test_public_manifest_fetch_keeps_tls_and_uses_system_ca_when_missing(self):
        with (patch.object(notice.ssl, "create_default_context") as create,
              patch.object(notice.urllib.request, "urlopen") as opened,
              patch.object(notice.sys, "platform", "darwin"),
              patch.object(Path, "is_file", return_value=True),
              patch.dict(os.environ, {"SSL_CERT_FILE": "", "SSL_CERT_DIR": ""})):
            context = create.return_value
            context.cert_store_stats.return_value = {"x509_ca": 0}
            opened.return_value = io.BytesIO(json.dumps({"name": NAME, "version": "1.2.3"})
                                            .encode())
            self.assertEqual(FETCH_LATEST(NAME), "1.2.3")
            context.load_verify_locations.assert_called_once_with(cafile="/etc/ssl/cert.pem")
            args, kwargs = opened.call_args
            self.assertEqual(args[0].full_url,
                             f"https://raw.githubusercontent.com/easyvibecoding/{NAME}/main/"
                             f"plugins/{NAME}/.codex-plugin/plugin.json")
            self.assertIs(kwargs["context"], context)
            self.assertLessEqual(kwargs["timeout"], 1)
            self.assertNotIn("example-task", args[0].full_url)
            # An explicit custom trust store is respected, not replaced.
            context.load_verify_locations.reset_mock()
            opened.return_value = io.BytesIO(b'{"name":"other","version":"1.2.3"}')
            with patch.dict(os.environ, {"SSL_CERT_FILE": "example-custom-ca.pem"}):
                with self.assertRaises(ValueError):
                    FETCH_LATEST(NAME)
            context.load_verify_locations.assert_not_called()

    def test_manual_cli_works_without_trusting_hooks(self):
        script = PLUGIN / "scripts" / ("usage_reports.py" if NAME.endswith("reports")
                                      else "run_budget.py")
        # No executable native CLI in PATH: release cache still works, trust is unknown.
        db = notice.state(self.data)
        with db:
            db.execute("INSERT OR REPLACE INTO release VALUES (?, ?, ?)",
                       (NAME, "99.0.0", time.time()))
        db.close()
        process = subprocess.run(
            [sys.executable, str(script), "--data-dir", str(self.data), "updates", "check",
             "--offline", "--cwd", str(self.root)],
            env={**os.environ, "PATH": str(self.root)}, text=True, capture_output=True, timeout=3)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["release_status"], "update_available")
        self.assertEqual(result["hooks"], {"status": "unknown"})
        self.assertIn("Update available", result["messages"][0])

    def test_embedded_command_is_independent_of_new_plugin_runtime(self):
        spec = importlib.util.spec_from_file_location("update_builder", ROOT / "scripts" /
                                                      "build_hook_runtime.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        destination = self.root / NAME
        shutil.copytree(PLUGIN, destination)

        def hooks():
            raw = builder.artifacts(destination)[destination / "hooks/hooks.json"]
            return json.loads(raw)["hooks"]["UserPromptSubmit"][0]["hooks"]

        before = hooks()
        package = destination / "lib" / NAME.replace("-", "_")
        (package / "__init__.py").write_text('__version__ = "99.0.0"\n')
        (destination / ".codex-plugin/plugin.json").write_text(
            json.dumps({"name": NAME, "version": "99.0.0"}))
        after = hooks()
        self.assertNotEqual(before[0]["command"], after[0]["command"])
        self.assertEqual(before[1], after[1])
        # Execute the actual embedded command with broken/new package code.
        (package / "update_notice.py").write_text('raise RuntimeError("must not execute")')
        command = shlex.split(after[1]["command"])
        command[0] = sys.executable
        env = {**os.environ, **self.env, "PLUGIN_ROOT": str(destination),
               "CODEX_PLUGIN_UPDATE_NOTICES": "0"}
        completed = subprocess.run(command, input=json.dumps(self.payload), env=env,
                                   text=True, capture_output=True, timeout=3)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout), {})
        self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()
