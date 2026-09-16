"""Project-scoped exec observations. No command text, inferred ownership or budget writes.

This module is vendored identically in both independent plugins. Native state is
read-only; only hashes, numeric usage and allowlisted lifecycle values are saved.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import stat
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from .util import stable_hash

MAX_BYTES = 512 * 1024
MAX_ROWS = 100
EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}


def _identity(value):
    try:
        return str(uuid.UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


def _safe_path(path):
    path = Path(path).expanduser().absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("symlink path")
    return path


def _native(home):
    home = Path(home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    path = _safe_path(home / "state_5.sqlite")
    connection = sqlite3.connect("file:" + quote(str(path)) + "?mode=ro",
                                 uri=True, timeout=0.1)
    connection.row_factory = sqlite3.Row
    deadline = time.monotonic() + 0.5
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    return connection


def _state(root):
    root = _safe_path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = _safe_path(root / "exec-activity.sqlite3")
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(descriptor)
    connection = sqlite3.connect(path, timeout=0.1)
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE IF NOT EXISTS launches ("
                       "key TEXT PRIMARY KEY, project_hash TEXT, task_hash TEXT,"
                       "parent_hash TEXT, attribution TEXT, started REAL, finished REAL,"
                       "state TEXT, exit_code INTEGER, usage TEXT)")
    connection.execute("CREATE TABLE IF NOT EXISTS observers ("
                       "key TEXT PRIMARY KEY, since REAL, checked REAL, seen TEXT)")
    return connection


def tokens(value):
    """Validate counters; absent values remain unknown, never a fabricated zero."""
    if not isinstance(value, dict):
        return None
    required = ("input_tokens", "cached_input_tokens", "output_tokens")
    if any(type(value.get(k)) is not int or not 0 <= value[k] <= 2**63 - 1
           for k in required):
        return None
    total = value["input_tokens"] + value["output_tokens"]
    if (total > 2**63 - 1 or type(value.get("total_tokens", total)) is not int
            or value.get("total_tokens", total) != total):
        return None
    if value["cached_input_tokens"] > value["input_tokens"]:
        return None
    return {"total": total, "input": value["input_tokens"],
            "cached_input": value["cached_input_tokens"], "output": value["output_tokens"]}


def _read_exec(path, identifier, project):
    result = {"evidence": "catalog_only", "state": "unknown", "usage": None,
              "usage_scope": "session_cumulative", "usage_status": "unavailable",
              "counter_source": None, "model": None, "reasoning_effort": None}
    try:
        path = _safe_path(path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return result
            header = json.loads(stream.readline(128 * 1024))
            meta = header.get("payload", {})
            if (header.get("type") != "session_meta" or meta.get("id") != identifier
                    or meta.get("source") != "exec"
                    or Path(meta.get("cwd", "")).resolve() != project):
                return {**result, "evidence": "identity_mismatch"}
            header_end = stream.tell()
            offset = max(header_end, info.st_size - MAX_BYTES)
            stream.seek(offset)
            raw = stream.read(MAX_BYTES)
        limited = offset > header_end
        lines = raw.splitlines(keepends=True)
        if limited:
            lines = lines[1:]
        result["evidence"] = "native_metadata"
        lanes, invalid, reset = {}, False, set()
        for line in lines:
            if not line.endswith(b"\n"):
                invalid = True
                continue
            try:
                record = json.loads(line)
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    continue
            except (ValueError, AttributeError):
                invalid = True
                continue
            kind = record.get("type")
            if kind == "turn_context":
                model = payload.get("model")
                if isinstance(model, str) and re.fullmatch(r"[\w.:/+-]{1,100}", model):
                    result["model"] = model
                effort = payload.get("effort", payload.get("reasoning_effort"))
                if isinstance(effort, str) and effort in EFFORTS:
                    result["reasoning_effort"] = effort
            lane, value = None, None
            if kind == "token_usage_record" and payload.get("thread_id") == identifier:
                lane, value = "native_request", tokens(payload.get("thread_token_usage"))
            if kind == "event_msg":
                event = payload.get("type")
                if event in ("task_started", "task_complete", "turn_aborted"):
                    result["state"] = {"task_started": "last_turn_started",
                                       "task_complete": "last_turn_completed",
                                       "turn_aborted": "last_turn_aborted"}[event]
                if event == "token_count":
                    info = payload.get("info")
                    lane = "token_count"
                    value = (tokens(info.get("total_token_usage"))
                             if isinstance(info, dict) else None)
            if lane:
                previous = lanes.get(lane)
                if previous and value and any(value[k] < previous[k] for k in previous):
                    reset.add(lane)
                lanes[lane] = value
        lane = "native_request" if "native_request" in lanes else "token_count"
        result["counter_source"] = lane if lane in lanes else None
        result["usage"] = lanes.get(lane) if lane not in reset else None
        result["usage_status"] = (
            "counter_reset" if lane in reset else "unavailable" if not result["usage"]
            else "partial" if limited or invalid else "observed"
        )
        return result
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return result


def scan(project, *, root, home=None, since=None, limit=50):
    """Return one exact working directory's recent sessions and launcher receipts.

    Lifecycle records describe the last observed turn, not process liveness.
    Session totals are not window deltas and are never added to parent totals.
    """
    project = Path(project).expanduser().resolve()
    since = time.time() - 86400 if since is None else since
    if not math.isfinite(since) or not 1 <= limit <= MAX_ROWS:
        raise ValueError("invalid activity range")
    project_hash = stable_hash(str(project))
    result = {"status": "observed", "project_hash": project_hash,
              "coverage": "persisted_sessions_and_local_launcher_receipts",
              "since": since, "activities": [], "issues": [],
              "included_in_parent_usage": False}
    rows = []
    try:
        db = _native(home)
        try:
            rows = db.execute(
                "SELECT id,rollout_path,created_at,updated_at FROM threads "
                "WHERE source='exec' AND cwd=? AND updated_at>=? "
                "ORDER BY updated_at DESC,id LIMIT ?", (str(project), since, limit + 1)
            ).fetchall()
        finally:
            db.close()
    except (OSError, ValueError, sqlite3.Error):
        result["issues"].append("native_catalog_unavailable")
    if len(rows) > limit:
        result["issues"].append("row_limit")
    for row in rows[:limit]:
        identifier = _identity(row["id"])
        if not identifier:
            result["issues"].append("invalid_identity")
            continue
        item = {"key": stable_hash(identifier), "task_hash": stable_hash(identifier),
                "parent_hash": None, "attribution": "unknown", "started": row["created_at"],
                "updated": row["updated_at"], "exit_code": None}
        item.update(_read_exec(row["rollout_path"], identifier, project))
        result["activities"].append(item)
    path = Path(root) / "exec-activity.sqlite3"
    if path.exists():
        try:
            path = _safe_path(path)
            db = sqlite3.connect("file:" + quote(str(path)) + "?mode=ro", uri=True, timeout=0.1)
            db.row_factory = sqlite3.Row
            try:
                launched = db.execute(
                    "SELECT * FROM launches WHERE project_hash=? "
                    "AND COALESCE(finished,started)>=? ORDER BY started DESC LIMIT ?",
                    (project_hash, since, limit + 1),
                ).fetchall()
            finally:
                db.close()
            if len(launched) > limit:
                result["issues"].append("launcher_row_limit")
            native = {x["task_hash"]: x for x in result["activities"]}
            for row in launched[:limit]:
                match = native.get(row["task_hash"])
                if match is not None and match in result["activities"]:
                    result["activities"].remove(match)
                # Each invocation keeps its own attribution and time window,
                # even when multiple launches resume the same native session.
                item = {"key": row["key"], "task_hash": row["task_hash"],
                        "parent_hash": row["parent_hash"], "attribution": row["attribution"],
                        "started": row["started"], "updated": row["finished"],
                        "model": None, "reasoning_effort": None, "state": row["state"],
                        "launcher_state": row["state"], "exit_code": row["exit_code"],
                        "evidence": "launcher_receipt", "usage_scope": "launcher_invocation",
                        "usage": json.loads(row["usage"]) if row["usage"] else None,
                        "usage_status": "observed" if row["usage"] else "unavailable",
                        "counter_source": "exec_json" if row["usage"] else None}
                if match is not None:
                    item["native_session"] = {k: match[k] for k in (
                        "state", "usage", "usage_scope", "usage_status", "counter_source",
                        "model", "reasoning_effort", "evidence")}
                result["activities"].append(item)
        except (OSError, ValueError, sqlite3.Error):
            result["issues"].append("launcher_receipts_unavailable")
    result["activities"].sort(key=lambda x: x["started"], reverse=True)
    if len(result["activities"]) > limit:
        result["issues"].append("combined_row_limit")
        result["activities"] = result["activities"][:limit]
    if result["issues"]:
        result["status"] = "partial" if result["activities"] else "unavailable"
    return result


def record_launch(root, key, *, project, parent=None, attribution="unknown",
                  task=None, state="started", exit_code=None, usage=None):
    """Record only allowlisted launcher evidence; callers must never pass text."""
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError("invalid key")
    if state not in {"started", "exited", "interrupted", "launch_failed"}:
        raise ValueError("invalid state")
    if exit_code is not None and (type(exit_code) is not int or not -255 <= exit_code <= 255):
        raise ValueError("invalid exit code")
    if attribution not in {"unknown", "launcher_argument", "launcher_environment"}:
        raise ValueError("invalid attribution")
    parent, task = _identity(parent), _identity(task)
    if parent == task:
        parent = None
    # Revalidate the normalized counter before it enters private state.
    if usage is not None:
        usage = tokens({k + "_tokens": v for k, v in usage.items()})
    db = _state(root)
    try:
        with db:
            db.execute(
                "INSERT INTO launches VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET task_hash=COALESCE(excluded.task_hash,task_hash),"
                "parent_hash=excluded.parent_hash,attribution=excluded.attribution,"
                "finished=excluded.finished,state=excluded.state,exit_code=excluded.exit_code,"
                "usage=excluded.usage",
                (key, stable_hash(str(Path(project).expanduser().resolve())),
                 stable_hash(task) if task else None, stable_hash(parent) if parent else None,
                 attribution if parent else "unknown", time.time(),
                 time.time() if state != "started" else None, state, exit_code,
                 json.dumps(usage) if usage else None),
            )
    finally:
        db.close()


def notice(payload, root, *, home=None, now=None):
    """Best-effort hook notice at tool return, never a policy decision.

    No idle daemon is installed. Foreground watch/launcher modes cover execution
    between hooks. A missing initial hook is reported with bounded recovery.
    """
    event = payload.get("hook_event_name")
    if payload.get("agent_id"):
        return None
    if event not in {"UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}:
        return None
    session, turn, project = (payload.get(k) for k in ("session_id", "turn_id", "cwd"))
    if not _identity(session) or not isinstance(turn, str) or not project:
        return None
    plugin_setting = __package__.upper() + "_EXEC_ACTIVITY_NOTICES"
    if os.environ.get(plugin_setting, os.environ.get("CODEX_EXEC_ACTIVITY_NOTICES", "1")) == "0":
        return None
    now = time.time() if now is None else now
    key = stable_hash([session, turn, str(Path(project).expanduser().resolve())])
    try:
        db = _state(root)
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM observers WHERE key=?", (key,)).fetchone()
                if row is None:
                    since = now if event != "PostToolUse" else now - 60
                    db.execute("INSERT INTO observers VALUES (?,?,?,?)", (key, since, 0, "[]"))
                    row = {"since": since, "checked": 0, "seen": "[]"}
                if event in {"UserPromptSubmit", "PreToolUse"} or now - row["checked"] < 2:
                    return None
                report = scan(project, root=root, home=home, since=row["since"], limit=10)
                seen = set(json.loads(row["seen"]))
                fresh = [x for x in report["activities"] if x["key"] not in seen
                         and x.get("task_hash") != stable_hash(session)
                         and x["started"] >= row["since"]]
                seen.update(x["key"] for x in fresh)
                db.execute("UPDATE observers SET checked=?,seen=? WHERE key=?",
                           (now, json.dumps(sorted(seen)[-500:]), key))
                db.execute("DELETE FROM observers WHERE checked>0 AND checked<?", (now - 604800,))
                if not fresh:
                    return None
                return {"systemMessage": "Exec activity: " + str(len(fresh))
                        + " new codex exec session(s) observed in this working directory. "
                        + "Ownership is unknown unless recorded by the launcher; "
                        + "usage is separate from this Task. Use exec-activity list for details."}
        finally:
            db.close()
    except Exception:
        return None


def add_notice(result, payload, root, *, home=None):
    """Preserve all existing hook decisions and fields, including denials."""
    try:
        extra = notice(payload, root, home=home)
    except Exception:
        return result
    if not extra:
        return result
    result = dict(result or {})
    result["systemMessage"] = "\n".join(x for x in (result.get("systemMessage"),
                                                   extra["systemMessage"]) if x)
    return result
