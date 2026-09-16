"""Synthetic signed releases exercise the actual fixed native command boundary."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = next(p for p in (ROOT / "plugins").iterdir()
              if (p / "runtime/publisher.json").is_file())
SPEC = importlib.util.spec_from_file_location(
    "publisher_test_module", PLUGIN / "scripts/publisher_bootstrap.py")
BOOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOT)
SOURCE = (PLUGIN / "scripts/publisher_bootstrap.py").read_bytes()


class PublisherUpdatesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        cls.key = Path(cls.keys.name) / "synthetic.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt",
                        "rsa_keygen_bits:3072", "-out", str(cls.key)],
                       capture_output=True, check=True)
        result = subprocess.run(["openssl", "rsa", "-in", str(cls.key), "-noout", "-modulus"],
                                capture_output=True, text=True, check=True)
        cls.policy = json.loads((PLUGIN / "runtime/publisher.json").read_text())
        cls.policy["rsa_n"] = result.stdout.strip().split("=", 1)[1].lower()

    @classmethod
    def tearDownClass(cls):
        cls.keys.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.publisher = BOOT.Publisher(self.root / "data", self.policy, SOURCE)
        self.addCleanup(self.publisher.close)

    def release(self, sequence, marker, **overrides):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("__main__.py", "import json\nprint(json.dumps(" +
                             repr({"systemMessage": marker}) + "))\n")
        blob = stream.getvalue()
        manifest = {"schema": 1, "plugin": PLUGIN.name, "channel": "stable",
                    "version": f"9.0.{sequence}", "sequence": sequence,
                    "bootstrap_sha256": BOOT.contract(SOURCE, self.policy),
                    "runtime_sha256": hashlib.sha256(blob).hexdigest(), "runtime_size": len(blob),
                    **overrides}
        result = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(self.key)],
                                input=BOOT.canonical(manifest), capture_output=True, check=True)
        return {**manifest, "signature": base64.b64encode(result.stdout).decode()}, blob

    def fetcher(self, manifest, blob):
        def fetch(url, limit):
            self.assertTrue(url.startswith("https://raw.githubusercontent.com/easyvibecoding/"))
            return BOOT.canonical(manifest) if url.endswith("release.json") else blob
        return fetch

    def invoke(self, task, event="UserPromptSubmit", *, plugin=None, policy=None):
        selected = policy or self.policy
        command = [sys.executable, "-I", "-c", BOOT.entry(SOURCE, selected), event]
        env = {**os.environ, "CODEX_PLUGIN_AUTO_UPDATE": "0",
               "PLUGIN_ROOT": str(plugin or self.root / "missing-cache"),
               PLUGIN.name.upper().replace("-", "_") + "_HOME": str(self.root / "data"),
               "CODEX_HOME": str(self.root / "native")}
        result = subprocess.run(command, input=json.dumps({"session_id": task,
                                "hook_event_name": event, "turn_id": "synthetic-turn",
                                "cwd": str(self.root)}), env=env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def test_fixed_native_command_executes_new_signed_release_and_pins_old_task(self):
        a, blob_a = self.release(1, "SYNTHETIC_A")
        b, blob_b = self.release(2, "SYNTHETIC_B")
        self.publisher.activate(a, blob_a)
        self.assertIn("SYNTHETIC_A", self.invoke("synthetic-old-task")["systemMessage"])
        result = self.publisher.update(self.fetcher(b, blob_b))
        self.assertEqual(result["status"], "updated")
        self.assertEqual(self.invoke("synthetic-old-task")["systemMessage"], "SYNTHETIC_A")
        self.assertIn("SYNTHETIC_B", self.invoke("synthetic-new-task")["systemMessage"])
        persisted = (self.root / "data/publisher-updates.sqlite3").read_bytes()
        self.assertNotIn(b"synthetic-old-task", persisted)
        self.assertNotIn(b"synthetic-new-task", persisted)

    def test_signature_metadata_and_payload_tampering_leave_old_runtime_active(self):
        a, blob_a = self.release(1, "SAFE")
        b, blob_b = self.release(2, "NEVER_EXECUTED")
        self.publisher.activate(a, blob_a)
        for manifest, blob in [({**b, "version": "9.0.999"}, blob_b),
                               ({**b, "signature": ""}, blob_b),
                               ({**b, "plugin": "other-plugin"}, blob_b),
                               (b, blob_b + b"corrupt"), ({**b, "extra": 1}, blob_b)]:
            with self.subTest(manifest=manifest["version"], size=len(blob)):
                result = self.publisher.update(self.fetcher(manifest, blob))
                self.assertEqual(result["status"], "unavailable_or_rejected")
                self.assertEqual(self.publisher.select()[0]["runtime_sha256"], a["runtime_sha256"])
                self.assertNotIn(b["runtime_sha256"],
                                 [p.stem for p in (self.root / "data/runtimes").glob("*.pyz")])

    def test_incompatible_signed_bootstrap_requires_plugin_update(self):
        a, blob_a = self.release(1, "A")
        b, blob_b = self.release(2, "B", bootstrap_sha256="0" * 64)
        self.publisher.activate(a, blob_a)
        self.assertEqual(self.publisher.update(self.fetcher(b, blob_b))["status"],
                         "requires_plugin_update")
        self.assertIn("hook review required", self.invoke("synthetic-new")["systemMessage"])
        self.assertEqual(self.publisher.select()[0]["version"], "9.0.1")

    def test_rollback_keeps_watermark_and_ignores_replayed_release(self):
        a, blob_a = self.release(1, "A")
        b, blob_b = self.release(2, "B")
        self.publisher.activate(a, blob_a)
        self.publisher.activate(b, blob_b)
        self.publisher.control("rollback")
        self.assertEqual(self.publisher.update(self.fetcher(b, blob_b))["status"],
                         "current_or_older")
        self.assertEqual(self.publisher.select()[0]["version"], "9.0.1")
        self.assertFalse(self.publisher.activate(a, blob_a))

    def test_disabled_updater_does_not_fetch_or_activate_inflight_release(self):
        a, blob_a = self.release(1, "A")
        b, blob_b = self.release(2, "B")
        self.publisher.activate(a, blob_a)
        self.publisher.control("off")
        def forbidden(*args):
            self.fail("disabled updater fetched metadata")
        self.assertEqual(self.publisher.update(forbidden, automatic=True)["status"], "disabled")
        self.assertFalse(self.publisher.schedule())
        self.publisher.control("on")
        def in_flight(url, limit):
            if url.endswith("publisher.pyz"):
                self.publisher.control("off")
                return blob_b
            return BOOT.canonical(b)
        self.publisher.update(in_flight, automatic=True)
        self.assertEqual(self.publisher.select()[0]["version"], "9.0.1")
        self.assertEqual(self.publisher.update(self.fetcher(b, blob_b))["status"], "updated")

    def test_corrupt_cache_falls_back_for_new_tasks_only(self):
        a, blob_a = self.release(1, "A")
        b, blob_b = self.release(2, "B")
        self.publisher.activate(a, blob_a)
        self.publisher.activate(b, blob_b)
        self.publisher.select("synthetic-pinned-b")
        (self.root / "data/runtimes" / (b["runtime_sha256"] + ".pyz")).write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            self.publisher.select("synthetic-pinned-b")
        self.assertEqual(self.publisher.select("synthetic-new")[0]["version"], "9.0.1")

    def test_cached_manifest_rechecked_before_execution(self):
        a, blob_a = self.release(1, "SAFE")
        self.publisher.activate(a, blob_a)
        with self.publisher.db:
            self.publisher.db.execute("UPDATE releases SET manifest=?", (
                json.dumps({**a, "version": "9.0.99"}),))
        with self.assertRaises(ValueError):
            self.publisher.select()
        result = self.invoke("synthetic-task")
        if PLUGIN.name == "codex-run-budget":
            self.assertEqual(result["decision"], "block")
        else:
            self.assertEqual(result, {})

    def test_future_bootstrap_upgrade_has_independent_activation_and_task_pins(self):
        a, blob_a = self.release(1, "A")
        self.publisher.activate(a, blob_a)
        self.publisher.select("synthetic-same-task")
        changed_source = SOURCE + b"\n# synthetic reviewed new entry\n"
        b, blob_b = self.release(2, "B", bootstrap_sha256=BOOT.contract(
            changed_source, self.policy))
        upgraded = BOOT.Publisher(self.root / "data", self.policy, changed_source)
        try:
            self.assertTrue(upgraded.activate(b, blob_b))
            self.assertEqual(upgraded.select("synthetic-same-task")[0]["version"], "9.0.2")
            self.assertEqual(self.publisher.select("synthetic-same-task")[0]["version"], "9.0.1")
            self.assertEqual(self.publisher.select()[0]["version"], "9.0.1")
        finally:
            upgraded.close()

    def test_duplicate_metadata_and_symlink_paths_are_rejected(self):
        with self.assertRaises(ValueError):
            BOOT.parse('{"schema":1,"schema":2}')
        outside = self.root / "outside"
        outside.mkdir()
        link = self.root / "linked"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            BOOT.Publisher(link, self.policy, SOURCE)
        self.assertEqual(list(outside.iterdir()), [])

    def test_worker_scheduled_once_without_model_or_shell(self):
        with patch.object(BOOT.subprocess, "Popen") as spawn, patch.dict(
                os.environ, {"CODEX_PLUGIN_AUTO_UPDATE": "1"}):
            self.assertTrue(self.publisher.schedule())
            self.assertFalse(self.publisher.schedule())
        spawn.assert_called_once()
        args, kwargs = spawn.call_args
        self.assertEqual(args[0][:3], [sys.executable, "-I", "-c"])
        self.assertEqual(args[0][3], BOOT.entry(SOURCE, self.policy))
        self.assertEqual(args[0][4], "--worker")
        self.assertNotIn("shell", kwargs)
        self.assertTrue(kwargs["start_new_session"])

    def test_packaged_signed_runtime_runs_actual_hook_and_cli(self):
        policy = json.loads((PLUGIN / "runtime/publisher.json").read_text())
        result = self.invoke("synthetic-packaged", plugin=PLUGIN, policy=policy)
        version = json.loads((PLUGIN / "runtime/release.json").read_text())["version"]
        self.assertIn("verified signed runtime " + version, result["systemMessage"])
        self.assertNotIn("decision", result)
        script = "run_budget.py" if PLUGIN.name == "codex-run-budget" else "usage_reports.py"
        result = subprocess.run([sys.executable, str(PLUGIN / "scripts" / script),
                                 "--data-dir", str(self.root / "data"), "--help"],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("usage:", result.stdout)

    def test_runtime_edit_does_not_change_native_hook_definitions(self):
        spec = importlib.util.spec_from_file_location("publisher_builder",
                                                     ROOT / "scripts/build_hook_runtime.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        before = builder.artifacts(PLUGIN)
        init = PLUGIN / "lib" / PLUGIN.name.replace("-", "_") / "__init__.py"
        original = Path.read_bytes
        def changed(path):
            return b'__version__ = "99.0.0"\n' if path == init else original(path)
        with patch.object(Path, "read_bytes", changed):
            after = builder.artifacts(PLUGIN)
        self.assertNotEqual(before[PLUGIN / "runtime/publisher.pyz"],
                            after[PLUGIN / "runtime/publisher.pyz"])
        self.assertEqual(before[PLUGIN / "hooks/hooks.json"], after[PLUGIN / "hooks/hooks.json"])
        # The shipped shell argument must equal the independent bootstrap entry builder.
        policy = json.loads((PLUGIN / "runtime/publisher.json").read_text())
        hooks = json.loads(before[PLUGIN / "hooks/hooks.json"])["hooks"]
        for event, groups in hooks.items():
            self.assertEqual(shlex.split(groups[0]["hooks"][0]["command"]),
                             ["python3", "-I", "-c", BOOT.entry(SOURCE, policy), event])


if __name__ == "__main__":
    unittest.main()
