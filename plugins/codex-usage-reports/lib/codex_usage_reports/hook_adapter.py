"""Report-only hook protocol: every failure skips reporting and returns success."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .auto_report import handle
from .util import data_path

MAX_INPUT_BYTES = 2 * 1024 * 1024
EVENTS = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStop",
          "Stop", "SessionEnd", "Interrupt")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", choices=EVENTS)
    parser.add_argument("--preview", nargs=2, metavar=("TASK", "TURN"))
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--output-dir")
    parser.add_argument("--data-dir")
    parser.add_argument("--codex-home")
    args = parser.parse_args()
    root = Path(args.data_dir).expanduser() if args.data_dir else data_path()
    home = Path(args.codex_home).expanduser() if args.codex_home else None
    try:
        if args.preview is not None:
            from .auto_preview import preview
            if args.event or args.reconcile or not args.output_dir:
                raise ValueError("preview requires an output directory")
            result = preview(root, *args.preview, output_dir=Path(args.output_dir), home=home)
        else:
            raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
            if len(raw) > MAX_INPUT_BYTES:
                raise ValueError("input exceeds limit")
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict) or not payload.get("session_id"):
                raise ValueError("missing identity")
            if args.reconcile:
                from .reconcile import worker
                worker(payload, root, home=home)
                return 0
            if args.event and payload.get("hook_event_name") != args.event:
                raise ValueError("event mismatch")
            if payload.get("hook_event_name") not in EVENTS:
                raise ValueError("unsupported event")
            result = handle(payload, root, home=home) or {}
            from .exec_activity import add_notice
            result = add_notice(result, payload, root, home=home)
            from .reconcile import schedule
            schedule(payload, root, home=home)
    except Exception:
        result = ({"status": "unavailable", "reason": "preview not generated; do not retry"}
                  if args.preview else {})
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0
