"""Standalone, read-only update/trust sentinel, embedded verbatim in its hook.

Keep this command stable across releases. Never import or execute plugin code,
downloaded code, or hook commands here: those require their own native trust.
Only fixed public version metadata is fetched. No model calls or config writes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import sqlite3
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PLUGINS = {"codex-run-budget": "run-budget", "codex-usage-reports": "usage-reports"}
LIMIT = 1024 * 1024
TTL = 6 * 3600


def version(value):
    """Compare stable releases; ignore build metadata, never guess prereleases."""
    if not isinstance(value, str) or len(value) > 100:
        return None
    match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\+[\w.-]+)?", value)
    return tuple(map(int, match.groups())) if match else None


def read_json(path):
    with path.open("rb") as source:
        raw = source.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("metadata too large")
    return json.loads(raw)


def installed(plugin, directory):
    manifest = read_json(directory / ".codex-plugin/plugin.json")
    if manifest.get("name") != plugin or version(manifest.get("version")) is None:
        raise ValueError("unknown installation")
    hooks = read_json(directory / "hooks/hooks.json")
    identity = json.dumps([manifest["version"], hooks], sort_keys=True).encode()
    return manifest["version"], hashlib.sha256(identity).hexdigest()


def state(root):
    """Private, bounded state; no raw Task IDs, paths, prompts, or hook definitions."""
    root = root.expanduser().absolute()
    path = root / "update-notices.sqlite3"
    sidecars = [Path(str(path) + suffix) for suffix in ("-journal", "-wal", "-shm")]
    if any(p.is_symlink() for p in (path, root, *root.parents, *sidecars)):
        raise ValueError("symlink state")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(descriptor)
    db = sqlite3.connect(path, timeout=0.05)
    db.execute("CREATE TABLE IF NOT EXISTS seen (task TEXT PRIMARY KEY, install TEXT, at REAL)")
    db.execute("CREATE TABLE IF NOT EXISTS release (plugin TEXT PRIMARY KEY, value TEXT, at REAL)")
    db.commit()
    return db


def claim(db, task, identity, now):
    task_hash = hashlib.sha256(task.encode()).hexdigest()
    with db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT install FROM seen WHERE task = ?", (task_hash,)).fetchone()
        if old and old[0] == identity:
            return False
        db.execute("INSERT OR REPLACE INTO seen VALUES (?, ?, ?)", (task_hash, identity, now))
        # Retain the most recent 4096 Tasks; an evicted Task may get a fresh check.
        db.execute("DELETE FROM seen WHERE task NOT IN (SELECT task FROM seen "
                   "ORDER BY at DESC LIMIT 4096)")
    return True


def fetch_latest(plugin):
    url = (f"https://raw.githubusercontent.com/easyvibecoding/{plugin}/main/"
           f"plugins/{plugin}/.codex-plugin/plugin.json")
    request = urllib.request.Request(url, headers={"User-Agent": "Codex-Plugin-Update-Notice/1"})
    context = ssl.create_default_context()
    # python.org macOS installs may lack their optional bundled CA file. Use the
    # OS-maintained trust store, keeping TLS and hostname verification enabled.
    if (sys.platform == "darwin" and not context.cert_store_stats()["x509_ca"]
            and not os.environ.get("SSL_CERT_FILE") and not os.environ.get("SSL_CERT_DIR")
            and Path("/etc/ssl/cert.pem").is_file()):
        context.load_verify_locations(cafile="/etc/ssl/cert.pem")
    with urllib.request.urlopen(request, timeout=0.8, context=context) as response:
        raw = response.read(16385)
    if len(raw) > 16384:
        raise ValueError("release metadata too large")
    result = json.loads(raw)
    value = result.get("version")
    if result.get("name") != plugin or version(value) is None:
        raise ValueError("unknown release")
    return value


def latest(db, plugin, now, *, refresh=False, offline=False):
    cached = db.execute("SELECT value, at FROM release WHERE plugin = ?", (plugin,)).fetchone()
    if cached and not refresh and 0 <= now - cached[1] < (TTL if cached[0] else 600):
        return cached[0], "cached" if cached[0] else "unavailable"
    if offline:
        return None, "unavailable"
    try:
        value = fetch_latest(plugin)
        source = "remote"
    except Exception:
        value, source = None, "unavailable"
    with db:
        db.execute("INSERT OR REPLACE INTO release VALUES (?, ?, ?)", (plugin, value, now))
    return value, source


def hook_status(plugin, cwd, home):
    """Ask native Codex for trust; never interpret config strings or change trust."""
    process = None
    try:
        process = subprocess.Popen(
            ["codex", "app-server", "--stdio"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=cwd, env={**os.environ, "CODEX_HOME": str(home)},
        )
        deadline = time.monotonic() + 1.25
        buffer = b""
        total = 0
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)

            def send(value):
                process.stdin.write((json.dumps(value) + "\n").encode())
                process.stdin.flush()

            def receive(identifier):
                nonlocal buffer, total
                while time.monotonic() < deadline:
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        message = json.loads(line)
                        if message.get("id") == identifier:
                            if "error" in message:
                                raise ValueError("native status unavailable")
                            return message["result"]
                    if not selector.select(max(0, deadline - time.monotonic())):
                        break
                    block = os.read(process.stdout.fileno(), 65536)
                    total += len(block)
                    if not block or total > 8 * LIMIT:
                        break
                    buffer += block
                raise TimeoutError("native status unavailable")

            send({"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "plugin-update-notice", "version": "1"},
                "capabilities": {"experimentalApi": True}}})
            receive(1)
            send({"method": "initialized", "params": {}})
            send({"id": 2, "method": "hooks/list", "params": {"cwd": str(cwd)}})
            result = receive(2)
        return summarize_hooks(result, plugin)
    except Exception:
        return {"status": "unknown"}
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.1)
            process.stdin.close()
            process.stdout.close()


def summarize_hooks(result, plugin):
    records, diagnostics = [], []

    def walk(value):
        if isinstance(value, dict):
            if value.get("pluginId") == f"{plugin}@{plugin}" and "trustStatus" in value:
                records.append(value)
            diagnostics.extend(value.get("errors") or [])
            diagnostics.extend(value.get("warnings") or [])
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(result)
    enabled = [r for r in records if r.get("enabled") is True]
    pending = sum(r.get("trustStatus") in {"untrusted", "modified"} for r in enabled)
    unknown = any(r.get("trustStatus") not in {"trusted", "untrusted", "modified"}
                  for r in enabled)
    status = ("needs_review" if pending else "unknown" if not records or diagnostics or unknown
              else "disabled" if not enabled else "trusted")
    return {"status": status, "enabled": len(enabled), "needs_review": pending}


def check(db, plugin, directory, cwd, home, *, refresh=False, offline=False):
    current, _ = installed(plugin, directory)
    available, source = latest(db, plugin, time.time(), refresh=refresh, offline=offline)
    release = ("unknown" if version(available) is None else
               "update_available" if version(available) > version(current) else
               "current" if version(available) == version(current) else "ahead")
    trust = hook_status(plugin, cwd, home)
    messages = []
    if release == "update_available":
        messages.append(f"{plugin}: 有更新 / Update available: {current} → {available}. "
                        "請在 Plugins 更新外掛；更新後開啟 codex → /hooks，"
                        "檢查並信任更新的 hooks。 "
                        "Update in Plugins, then open codex → /hooks to review and trust changes.")
    if trust["status"] == "needs_review":
        messages.append(f"{plugin}: {trust['needs_review']} hooks "
                        "需要重新授權 / need trust review. "
                        "終端輸入 codex → /hooks → review and trust all updated hooks；"
                        "完成前這些 hooks 會被跳過 / skipped until trusted.")
    return {"plugin": plugin, "local_version": current, "latest_version": available,
            "release_status": release, "release_source": source, "hooks": trust,
            "messages": messages}


def hook(plugin, payload):
    if plugin not in PLUGINS or payload.get("hook_event_name") != "UserPromptSubmit":
        return {}
    prefix = plugin.upper().replace("-", "_")
    if (os.environ.get("CODEX_PLUGIN_UPDATE_NOTICES") == "0"
            or os.environ.get(prefix + "_UPDATE_NOTICES") == "0" or payload.get("agent_id")):
        return {}
    task = payload.get("session_id")
    location = os.environ.get("PLUGIN_ROOT") or os.environ.get("CLAUDE_PLUGIN_ROOT")
    if not isinstance(task, str) or not task or not location:
        return {}
    directory = Path(location)
    _, identity = installed(plugin, directory)
    root = Path(os.environ.get(prefix + "_HOME", str(Path.home() / ".codex" / PLUGINS[plugin])))
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    db = state(root)
    try:
        if not claim(db, task, identity, time.time()):
            return {}
        result = check(db, plugin, directory, Path(payload.get("cwd") or os.getcwd()), home)
    finally:
        db.close()
    message = "\n".join(result["messages"])
    if not message:
        return {}
    return {"systemMessage": message, "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit", "additionalContext": message}}


def main():
    try:
        raw = sys.stdin.buffer.read(LIMIT + 1)
        payload = json.loads(raw) if len(raw) <= LIMIT else None
        result = hook(sys.argv[1], payload) if isinstance(payload, dict) else {}
    except Exception:
        result = {}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
