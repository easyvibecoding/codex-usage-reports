"""Small reporting interface; no account mutation or token-budget controls."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .auto_report import configure, recent, settings
from .report_i18n import LOCALES
from .task_report import build_task_report, render_task_report
from .util import data_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="codex-usage-reports", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--data-dir", type=Path, help="Independent report state directory")
    parser.add_argument("--codex-home", type=Path, help="Read native Codex state here")
    parser.add_argument("--locale", choices=LOCALES, help="Override report display language")
    commands = parser.add_subparsers(dest="command", required=True)
    from .exec_activity_cli import add_parser
    add_parser(commands)
    auto = commands.add_parser("auto-report", help="Manage automatic per-turn reports")
    actions = auto.add_subparsers(dest="action", required=True)
    for action in ("status", "on", "off", "list"):
        actions.add_parser(action)
    threshold = actions.add_parser("threshold")
    threshold.add_argument("seconds", type=float, help="Report only turns longer than this value")
    task = commands.add_parser("task", help="Report one explicitly selected parent Task")
    task.add_argument("task_id", help="Exact Task UUID or unambiguous hashed selector")
    task.add_argument("--format", choices=("json", "markdown", "html"), default="markdown")
    task.add_argument("--output", type=Path, help="Create a private file without overwriting")
    task.add_argument("--limit", type=int, default=50, help="Recorded turn rows, 1..100")
    preview = commands.add_parser("preview", help="Render one pre-final inline card")
    preview.add_argument("task_id")
    preview.add_argument("turn_id")
    preview.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.data_dir.expanduser() if args.data_dir else data_path()
    previous_locale = os.environ.get("CODEX_USAGE_REPORTS_LOCALE")
    if args.locale:
        os.environ["CODEX_USAGE_REPORTS_LOCALE"] = args.locale
    try:
        if args.command == "exec-activity":
            from .exec_activity_cli import run
            return run(args, root)
        if args.command == "auto-report":
            current = settings(root)
            if args.action in ("on", "off", "threshold"):
                current = configure(
                    root, enabled=args.action == "on" if args.action != "threshold"
                    else current["enabled"], threshold_seconds=args.seconds
                    if args.action == "threshold" else current["threshold_seconds"],
                )
            result = recent(root) if args.action == "list" else current
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "task":
            report = build_task_report(root, args.task_id, home=args.codex_home, limit=args.limit)
            content = render_task_report(report, args.format)
            if args.output:
                target = args.output.expanduser().absolute()
                if any(path.is_symlink() for path in (target, *target.parents)):
                    raise ValueError("output path contains a symlink")
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                                     os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, "w") as output:
                    output.write(content)
                print(json.dumps({"status": "written", "path": str(target)}))
            else:
                print(content, end="" if content.endswith("\n") else "\n")
        else:
            from .auto_preview import preview as render_preview
            result = render_preview(root, args.task_id, args.turn_id,
                                    output_dir=args.output_dir, home=args.codex_home)
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception:
        # Do not expose native paths, Task IDs, raw inputs, or backend exceptions.
        print(json.dumps({"status": "unavailable", "reason":
                          "Report failed. Check selected Task, local state, and output path."}),
              file=sys.stderr)
        return 1
    finally:
        if args.locale:
            if previous_locale is None:
                os.environ.pop("CODEX_USAGE_REPORTS_LOCALE", None)
            else:
                os.environ["CODEX_USAGE_REPORTS_LOCALE"] = previous_locale
