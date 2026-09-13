#!/usr/bin/env python3
"""Build/check the deterministic hook zipapp and cache-independent commands."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shlex
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/codex-usage-reports"
EVENTS = (
    "UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStop",
    "Stop", "SessionEnd", "Interrupt",
)
MODULES = (
    "__init__.py", "transcript.py", "util.py", "hook_adapter.py",
    "auto_report.py", "auto_preview.py", "task_catalog.py", "child_usage.py", "report_i18n.py",
    "turn_quota.py", "quota_view.py", "meter_source.py", "meter_plan.py",
)


def artifacts(plugin: Path = PLUGIN) -> dict[Path, bytes]:
    content = {
        "__main__.py": (b"from codex_usage_reports.hook_adapter import main\n"
                        b"raise SystemExit(main())\n")
    }
    package = plugin / "lib/codex_usage_reports"
    for name in (*MODULES, "assets/turn-card.html"):
        content["codex_usage_reports/" + name] = (package / name).read_bytes()
    # CLI/manual-report domains are deliberately excluded from per-hook payloads.
    for path in sorted((package / "assets/locales").glob("*.json")):
        content["codex_usage_reports/assets/locales/" + path.name] = path.read_bytes()
    archive = io.BytesIO()
    # Stored members are portable and byte-identical across Python/zlib versions.
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as output:
        for name, blob in sorted(content.items()):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100600 << 16
            output.writestr(info, blob)
    runtime = archive.getvalue()
    if len(runtime) > 1024 * 1024:
        raise ValueError("hook runtime exceeds the bootstrap's 1 MiB limit")
    digest = hashlib.sha256(runtime).hexdigest()
    bootstrap = (plugin / "scripts/bootstrap.py").read_text()
    hooks = {}
    for event in EVENTS:
        command = "python3 -I -c " + shlex.quote(bootstrap) + " " + event + " " + digest
        group = {
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "timeout": 3,
                    "statusMessage": "Recording usage report",
                }
            ]
        }
        if event in {"PreToolUse", "PostToolUse"}:
            group["matcher"] = "*"
        hooks[event] = [group]
    document = {
        "description": "SHA-256-pinned, report-only hooks; errors never block work.",
        "hooks": hooks,
    }
    return {
        plugin / "runtime/hook.pyz": runtime,
        plugin / "hooks/hooks.json": (json.dumps(document, indent=2) + "\n").encode(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for path, expected in artifacts().items():
        if args.check:
            if not path.is_file() or path.read_bytes() != expected:
                print(
                    "Hook runtime artifacts are stale; run scripts/build_hook_runtime.py",
                    file=sys.stderr,
                )
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix="build-", dir=path.parent)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(expected)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
    print("Hook runtime artifacts " + ("verified." if args.check else "built."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
