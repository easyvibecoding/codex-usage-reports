"""Manual update/trust read-back, usable even before the sentinel is trusted."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .update_notice import check, state


def add_parser(commands, *, native_home_option=False):
    command = commands.add_parser("updates", help="Check plugin updates and hook trust")
    command.add_argument("action", choices=("check",))
    command.add_argument("--refresh", action="store_true", help="Bypass the release cache")
    command.add_argument("--offline", action="store_true", help="Use cached release metadata only")
    command.add_argument("--cwd", type=Path, default=Path.cwd(), help="Project hook scope")
    if native_home_option:
        command.add_argument("--codex-home", type=Path)


def run(args, root):
    directory = Path(__file__).resolve().parents[2]
    # Installed cache directories are named for the version, not the plugin.
    plugin = "codex-usage-reports" if "usage_reports" in __package__ else "codex-run-budget"
    home = args.codex_home or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    try:
        db = state(root)
        try:
            result = check(db, plugin, directory, args.cwd, home,
                           refresh=args.refresh, offline=args.offline)
        finally:
            db.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        print(json.dumps({"plugin": plugin, "release_status": "unknown",
                          "hooks": {"status": "unknown"}}))
        return 1
