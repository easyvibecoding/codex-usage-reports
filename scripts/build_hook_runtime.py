#!/usr/bin/env python3
"""Build/check the deterministic hook zipapp and cache-independent commands."""

from __future__ import annotations

import argparse
import base64
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
    "auto_report.py", "auto_preview.py", "reconcile.py", "task_catalog.py", "child_usage.py",
    "report_i18n.py", "exec_activity.py",
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
    # Publisher mode keeps this definition stable across runtime releases.
    source = (plugin / "scripts/publisher_bootstrap.py").read_bytes()
    policy = json.loads((plugin / "runtime/publisher.json").read_text())
    canonical_policy = json.dumps(policy, sort_keys=True, separators=(",", ":"))
    entry = ("import base64,json\ns=base64.b64decode(" + repr(base64.b64encode(source).decode())
             + ")\np=json.loads(" + repr(canonical_policy) + ")\n"
             "exec(compile(s,'<publisher-bootstrap>','exec'),"
             "{'__name__':'__main__','SOURCE':s,'POLICY':p})\n")
    hooks = {}
    for event in EVENTS:
        command = "python3 -I -c " + shlex.quote(entry) + " " + event
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
    # Separate trust boundary: do not put the sentinel in the changing zipapp.
    # No release version, runtime digest, or installed path may enter this command.
    sentinel = (package / "update_notice.py").read_text()
    hooks["UserPromptSubmit"][0]["hooks"].append({
        "type": "command",
        "command": "python3 -I -c " + shlex.quote(sentinel) + " " + plugin.name,
        "timeout": 3,
        "statusMessage": "Checking plugin updates and hook trust",
    })
    document = {
        "description": "Publisher-signed, report-only hooks; errors never block work.",
        "hooks": hooks,
    }
    # Full signed payload serves hooks and manual CLI commands. Legacy hook.pyz
    # remains available for old, digest-pinned Tasks and retained installations.
    full = {"__main__.py": (
        "import sys\nif len(sys.argv) > 1 and sys.argv[1] == '--cli':\n"
        "    del sys.argv[1]\n    from " + package.name + ".cli import main\n"
        "else:\n    from " + package.name + ".hook_adapter import main\n"
        "raise SystemExit(main())\n").encode()}
    for path in sorted(package.rglob("*")):
        if (path.is_file()
                and path.suffix in {".py", ".json", ".html", ".sql", ".css", ".js", ".svg"}):
            full[package.name + "/" + path.relative_to(package).as_posix()] = path.read_bytes()
    published = io.BytesIO()
    with zipfile.ZipFile(published, "w", compression=zipfile.ZIP_STORED) as output:
        for name, blob in sorted(full.items()):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100600 << 16
            output.writestr(info, blob)
    if len(published.getvalue()) > 8 * 1024 * 1024:
        raise ValueError("signed runtime exceeds the 8 MiB limit")
    return {
        plugin / "runtime/publisher.pyz": published.getvalue(),
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
