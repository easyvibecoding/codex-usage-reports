"""One bounded, report-only completion check after Stop; never resumes a model.

The child receives native selectors on stdin only. Persistent job state contains
hashes, counters and status, never selectors, paths or source conversation text.
Stop receipts remain immutable; a verified completion gets its own revision.
"""
from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .auto_report import (
    _child_lineage,
    _connect,
    _delta,
    _directory,
    _documents,
    observed_total,
    settings,
    snapshot,
)
from .child_usage import collect
from .report_i18n import ReportText
from .util import stable_hash

DELAYS = (0.1, 0.25, 0.5, 1, 2, 3, 5, 8)
MAX_BYTES = 1024 * 1024


class _DeadlineExpired(BaseException):
    """Bypass report collectors' nonblocking ordinary-error handlers."""


def worker(payload: dict, root: Path, *, home=None) -> dict:
    """The detached entry point also bounds a slow scan/child collector on POSIX."""
    def expired(signum, frame):
        raise _DeadlineExpired()

    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(25)
    try:
        return run(payload, root, home=home)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _store(root):
    connection = _connect(root)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS reconciliations ("
            "key TEXT PRIMARY KEY, state TEXT NOT NULL, attempts INTEGER NOT NULL, "
            "updated REAL NOT NULL)"
        )
    except Exception:
        connection.close()
        raise
    return connection


def _read(path: Path) -> bytes:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("symlink report path")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("nonregular report")
        raw = source.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("report too large")
    return raw


def _selectors(payload):
    selected = {key: payload.get(key) for key in ("session_id", "turn_id")}
    selected["transcript_path"] = (
        payload.get("agent_transcript_path")
        if payload.get("hook_event_name") == "SubagentStop"
        else payload.get("transcript_path")
    )
    if any(not isinstance(value, str) or not 0 < len(value) <= 4096
           for value in selected.values()):
        raise ValueError("missing reconciliation selector")
    if payload.get("agent_id") is not None:
        agent = payload["agent_id"]
        if not isinstance(agent, str) or not 0 < len(agent) <= 256:
            raise ValueError("invalid reconciliation agent")
        selected["agent_id"] = agent
    return selected


def _runner(root):
    digest = getattr(__loader__, "runtime_digest", None)
    if digest:
        return root / "runtimes" / (digest + ".pyz")
    invoked = Path(sys.argv[0]).absolute()
    if invoked.suffix == ".pyz" and invoked.is_file():
        return invoked
    return Path(__file__).resolve().parents[2] / "scripts/hook.py"


def schedule(payload: dict, root: Path, *, home=None) -> bool:
    """Launch at most once after a reported Stop; spawn errors cannot block work."""
    if payload.get("hook_event_name") not in ("Stop", "SubagentStop"):
        return False
    connection = None
    key = None
    try:
        if not settings(root)["enabled"]:
            return False
        selected = _selectors(payload)
        owner = selected.get("agent_id", selected["session_id"])
        if "agent_id" in selected and _child_lineage(
            owner, selected["session_id"], home=home,
        ) is None:
            return False
        key = stable_hash([owner, selected["turn_id"]])
        connection = _store(root)
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM turns WHERE key=?", (key,)).fetchone()
        if row is None or row["state"] != "reported":
            return False
        inserted = connection.execute(
            "INSERT OR IGNORE INTO reconciliations VALUES (?,'queued',0,?)", (key, time.time())
        ).rowcount
        connection.commit()
        if not inserted:
            return False
        command = [sys.executable, "-I", str(_runner(root)), "--reconcile",
                   "--data-dir", str(root.absolute())]
        if home is not None:
            command.extend(["--codex-home", str(Path(home).absolute())])
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True,
        )
        try:
            process.stdin.write(json.dumps(selected).encode())
            process.stdin.close()
        except Exception:
            process.terminate()
            process.wait(timeout=1)
            raise
        return True
    except Exception:
        if connection is not None and key is not None:
            try:
                connection.rollback()
                connection.execute(
                    "UPDATE reconciliations SET state='failed',updated=? "
                    "WHERE key=? AND state='queued'", (time.time(), key),
                )
            except Exception:
                pass
        return False
    finally:
        if connection is not None:
            connection.close()


def _publish(root, key, revised):
    directory = _directory(root)
    markdown, html = _documents(revised)
    # No clobber: all original Stop artifacts stay immutable. A selected Task
    # query follows the index to the new revision after every file is written.
    for extension, content in (("json", json.dumps(revised, ensure_ascii=True, indent=2)),
                               ("html", html), ("md", markdown)):
        target = directory / f"{key}.reconciled.{extension}"
        blob = content.encode()
        if len(blob) > MAX_BYTES:
            raise ValueError("reconciliation too large")
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(blob)


def run(payload: dict, root: Path, *, home=None, delays=DELAYS) -> dict:
    """Wait a bounded interval for explicit completion, then publish once."""
    connection = None
    key = None
    claimed = False
    state = "failed"
    attempts = 0
    try:
        selected = _selectors(payload)
        session, turn = selected["session_id"], selected["turn_id"]
        owner = selected.get("agent_id", session)
        lineage = (_child_lineage(owner, session, home=home)
                   if "agent_id" in selected else None)
        if "agent_id" in selected and lineage is None:
            raise ValueError("subagent lineage unavailable")
        key = stable_hash([owner, turn])
        connection = _store(root)
        connection.execute("BEGIN IMMEDIATE")
        claimed = bool(connection.execute(
            "UPDATE reconciliations SET state='running',updated=? WHERE key=? AND state='queued'",
            (time.time(), key),
        ).rowcount)
        connection.commit()
        if not claimed:
            return {"status": "not_queued"}
        row = connection.execute("SELECT * FROM turns WHERE key=?", (key,)).fetchone()
        if (row is None or row["state"] != "reported"
                or row["session_hash"] != stable_hash(owner)
                or row["turn_hash"] != stable_hash(turn)):
            raise ValueError("turn identity unavailable")
        original = json.loads(_read(_directory(root) / f"{key}.json"))
        if (original.get("task_hash") != row["session_hash"]
                or original.get("turn_hash") != row["turn_hash"]
                or not original.get("source_identity_verified")
                or (lineage and (
                    original.get("scope") != "agent_turn_stop_boundary"
                    or original.get("root_hash") != stable_hash(session)
                    or original.get("direct_parent_hash") !=
                    stable_hash(lineage["parent_id"])))):
            raise ValueError("receipt identity unavailable")
        baseline = json.loads(row["baseline"])
        until = datetime.fromisoformat(original["stopped_at"]).timestamp()
        deadline = time.monotonic() + 25
        state = "expired"
        for delay in delays[:len(DELAYS)]:
            if not settings(root)["enabled"]:
                state = "disabled"
                break
            if time.monotonic() + delay > deadline:
                break
            time.sleep(delay)
            attempts += 1
            expected = ({"expected_child": owner,
                         "expected_parent": lineage["parent_id"],
                         "expected_root": session} if lineage else {})
            current = snapshot(selected["transcript_path"], turn,
                               completed_only=True, **expected)
            if not current.get("completion_observed"):
                continue
            if current.get("task_hash") != row["session_hash"]:
                raise ValueError("completion identity mismatch")
            usage, status = _delta(baseline, current)
            text = ReportText(original.get("locale", "zh-Hant"))
            children = collect(root, owner, row["started"], until, home=home,
                               unnamed_label=text("unnamed"))
            _, complete = observed_total(usage, children)
            complete = (complete and not current.get("invalid_records")
                        and not current.get("tail_limited")
                        and not current.get("contexts_limited"))
            state = "complete" if complete else "partial"
            revised = {
                **original, "scope": ("agent_turn_completion_boundary" if lineage
                                       else "user_turn_completion_boundary"), "revision": 2,
                "reconciliation_status": state,
                "reconciled_at": datetime.now(timezone.utc).isoformat(),
                "completion_observed": True, "completed_at": current.get("completed_at"),
                "usage": usage, "usage_status": status, "task_usage": current.get("usage"),
                "counter_source": current.get("counter_source"),
                "stop_contexts": current.get("contexts", []),
                "contexts_limited": current.get("contexts_limited", False),
                "subagents": children,
                "subagents_included": children.get("agents_with_usage", 0) > 0,
                "subagent_window_end": original["stopped_at"],
                "final_usage_may_not_yet_be_persisted": not complete,
                "snapshot_scan_bytes": baseline.get("scan_bytes", 0) + current.get("scan_bytes", 0),
            }
            if not settings(root)["enabled"]:
                state = "disabled"
                break
            if time.monotonic() >= deadline:
                state = "expired"
                break
            _publish(root, key, revised)
            connection.execute("UPDATE turns SET report=? WHERE key=? AND state='reported'",
                               (key + ".reconciled.md", key))
            break
        return {"status": state, "attempts": attempts}
    except _DeadlineExpired:
        state = "expired"
        return {"status": state, "attempts": attempts}
    except Exception:
        state = "failed"
        return {"status": "failed", "attempts": attempts}
    finally:
        if connection is not None:
            try:
                if claimed:
                    connection.execute(
                        "UPDATE reconciliations SET state=?,attempts=?,updated=? WHERE key=?",
                        (state, attempts, time.time(), key),
                    )
            except Exception:
                pass
            finally:
                connection.close()
