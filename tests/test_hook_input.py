"""Complete synthetic hook input across publisher and report-only runtime seams."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/codex-usage-reports"
sys.path.insert(0, str(PLUGIN / "lib"))

from codex_usage_reports import hook_adapter  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "publisher_hook_input_test", PLUGIN / "scripts/publisher_bootstrap.py")
BOOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOT)


def payload_bytes(size=256, event="PostToolUse"):
    payload = {"hook_event_name": event, "session_id": "synthetic-only",
               "turn_id": "synthetic-turn", "padding": ""}
    initial = json.dumps(payload, separators=(",", ":")).encode()
    payload["padding"] = "x" * (size - len(initial))
    raw = json.dumps(payload, separators=(",", ":")).encode()
    assert len(raw) == size
    return raw


class PublisherHookInputTest(unittest.TestCase):
    def invoke(self, raw, *, runtime_error=None, parse_error=None):
        publisher = Mock()
        publisher.select.return_value = ({}, b"synthetic-runtime", False)
        output = io.StringIO()
        stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")

        def run(args):
            self.assertEqual(args, ["--event", "PostToolUse"])
            self.assertEqual(sys.stdin.buffer.read(), raw)
            if runtime_error is not None:
                raise runtime_error
            print("{}")

        with patch.object(BOOT, "Publisher", return_value=publisher), \
                patch.object(BOOT, "RuntimeLoader") as loader, \
                patch.object(BOOT, "data_root", return_value=Path("synthetic-store")), \
                patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", output), \
                patch.object(BOOT, "parse", wraps=BOOT.parse, side_effect=parse_error):
            loader.return_value.run.side_effect = run
            code = BOOT.main(b"synthetic-source", {"plugin": "codex-usage-reports",
                             "events": ["PostToolUse"]}, ["PostToolUse"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), {})
        publisher.close.assert_called_once()
        return publisher, loader

    def test_complete_large_input_reaches_original_task_pin_and_runtime_unchanged(self):
        for size in (2 * 1024 * 1024, 2 * 1024 * 1024 + 1, 6 * 1024 * 1024):
            with self.subTest(size=size):
                publisher, loader = self.invoke(payload_bytes(size))
                publisher.select.assert_called_once_with("synthetic-only")
                loader.assert_called_once_with(b"synthetic-runtime", "codex_usage_reports")

    def test_complete_multibyte_utf8_input_reaches_runtime_unchanged(self):
        payload = json.loads(payload_bytes())
        payload["padding"] = "字" * (1024 * 1024)
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.assertGreater(len(raw), 2 * 1024 * 1024)
        publisher, _ = self.invoke(raw)
        publisher.select.assert_called_once_with("synthetic-only")

    def test_invalid_input_never_selects_runtime_or_emits_a_decision(self):
        for raw in (payload_bytes(6 * 1024 * 1024)[:-1], b"\xff", b"[]", b"{}",
                    b'{"session_id":"a","session_id":"b"}',
                    b'{"hook_event_name":"Stop","session_id":"synthetic-only"}',
                    b'{"hook_event_name":"PostToolUse","session_id":true}',
                    b'{"hook_event_name":"PostToolUse","session_id":""}',
                    json.dumps({"hook_event_name": "PostToolUse",
                                "session_id": "x" * 257}).encode()):
            with self.subTest(input_bytes=len(raw)):
                publisher, loader = self.invoke(raw)
                publisher.select.assert_not_called()
                loader.assert_not_called()

    def test_parser_recursion_and_runtime_errors_remain_private_and_nonblocking(self):
        publisher, loader = self.invoke(payload_bytes(),
                                        parse_error=RecursionError("SYNTHETIC_PRIVATE"))
        publisher.select.assert_not_called()
        loader.assert_not_called()
        publisher, loader = self.invoke(payload_bytes(),
                                        runtime_error=RuntimeError("SYNTHETIC_PRIVATE"))
        publisher.select.assert_called_once_with("synthetic-only")
        loader.return_value.run.assert_called_once()


class RuntimeHookInputTest(unittest.TestCase):
    def invoke(self, raw, *, reconcile=False, invalid=False):
        args = ["--reconcile"] if reconcile else ["--event", "PostToolUse"]
        args.extend(["--data-dir", "synthetic-store"])
        output = io.StringIO()
        stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
        with patch.object(sys, "argv", ["synthetic-hook", *args]), \
                patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", output), \
                patch.object(hook_adapter, "handle", return_value={}) as handle, \
                patch("codex_usage_reports.exec_activity.add_notice",
                      side_effect=lambda result, *args, **kwargs: result), \
                patch("codex_usage_reports.reconcile.schedule") as schedule, \
                patch("codex_usage_reports.reconcile.worker") as worker:
            self.assertEqual(hook_adapter.main(), 0)
        self.assertEqual(output.getvalue(), "" if reconcile and not invalid else "{}\n")
        return handle, schedule, worker

    def test_large_hook_and_reconciliation_input_are_complete(self):
        raw = payload_bytes(6 * 1024 * 1024)
        expected = json.loads(raw)
        handle, schedule, worker = self.invoke(raw)
        self.assertEqual(handle.call_args.args[0], expected)
        self.assertEqual(schedule.call_args.args[0], expected)
        worker.assert_not_called()
        handle, schedule, worker = self.invoke(raw, reconcile=True)
        self.assertEqual(worker.call_args.args[0], expected)
        handle.assert_not_called()
        schedule.assert_not_called()

    def test_invalid_large_hook_and_reconciliation_input_skip_all_reporting(self):
        for reconcile in (False, True):
            with self.subTest(reconcile=reconcile):
                handle, schedule, worker = self.invoke(
                    payload_bytes(6 * 1024 * 1024)[:-1], reconcile=reconcile, invalid=True)
                handle.assert_not_called()
                schedule.assert_not_called()
                worker.assert_not_called()

    def test_packaged_timeouts_match_terminal_work_and_keep_other_hooks_short(self):
        hooks = json.loads((PLUGIN / "hooks/hooks.json").read_text())["hooks"]
        for event, groups in hooks.items():
            for group in groups:
                for hook in group["hooks"]:
                    with self.subTest(event=event, message=hook["statusMessage"]):
                        self.assertEqual(hook["timeout"],
                                         10 if event in {"Stop", "SubagentStop"} else 3)


if __name__ == "__main__":
    unittest.main()
