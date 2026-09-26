"""Bounded, deterministic child-task usage receipts.

The hook receives a ``SubagentStop`` payload before a child transcript is
necessarily flushed.  This module keeps the evidence that is already
available and lets a later preview reconcile it with the native transcript.
Only numeric values, hashes, timestamps and small state labels are persisted;
native identifiers and paths never leave memory.

The public surface is intentionally small:

``capture(payload, root, *, home=None, now=None)``
    Best-effort receipt at a SubagentStop boundary.  It never raises into the
    hook caller and never asks the child to continue.

``collect(root, session, since, until, *, home=None)``
    Selects the exact descendant set from the native Task catalog and returns
    a bounded per-request subtotal for ``[since, until)``.  A missing source
    is unknown, not zero.  The function is read-mostly; any reconciliation
    write is best effort and cannot block a report.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import stat
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .task_catalog import MAX_DEPTH, TaskCatalog, _uuid
from .transcript import request_usage
from .util import stable_hash

DB_NAME = "child-usage.sqlite3"
HEADER_BYTES = 128 * 1024
TAIL_BYTES = 1024 * 1024
MAX_DB_REQUESTS = 50_000
MAX_DB_AGENTS = 512
MAX_SCAN_RECORDS = 20_000
MAX_ROWS = 32
MAX_DESCENDANTS = 33
MAX_ID_BYTES = 256

USAGE_KEYS = ("total", "input", "cached_input", "output", "reasoning_output")
_ZERO_USAGE = {key: 0 for key in USAGE_KEYS}
_STATUS_RANK = {"unavailable": 0, "partial": 1, "observed": 2}
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_MAX_INT = 2**63 - 1
# A bounded revocation table never evicts child tombstones. If its capacity
# is exhausted, this non-identifier marker suspends child subtotals in this
# reporting cache without changing hook continuation.
_REVOCATIONS_SATURATED = "*"


def _empty(status: str = "unavailable") -> dict[str, Any]:
    return {
        "usage": None,
        "status": status,
        "agents_seen": 0,
        "agents_with_usage": 0,
        "pending_agents": 0,
        "missing_agents": 0,
        "selection_limited": False,
        "request_count": 0,
        "scan_bytes": 0,
        "rows": [],
    }


def _bounded_text(value: Any) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8", "replace")) > MAX_ID_BYTES
    ):
        return None
    return value


def _hash(value: Any) -> str | None:
    text = _bounded_text(value)
    return stable_hash(text) if text is not None else None


def _stamp(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) else None
    if not isinstance(value, str) or len(value) > 128:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        number = parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _native_stamp(value: Any) -> float | None:
    """Parse native lifecycle timestamps, which are usually milliseconds."""

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(number):
            return None
        # Codex lifecycle payloads use Unix milliseconds.  Keep support for
        # second-resolution fixtures and older payloads without accepting an
        # unbounded integer as a timestamp.
        if abs(number) >= 1e11:
            number /= 1000.0
        return number if math.isfinite(number) else None
    return _stamp(value)


def _lifecycle_stamp(record: dict[str, Any], payload: dict[str, Any], kind: str) -> float | None:
    """Prefer native lifecycle fields and fall back to the record timestamp."""

    fields = ("started_at",) if kind == "started" else ("completed_at", "ended_at")
    for field in fields:
        stamp = _native_stamp(payload.get(field))
        if stamp is not None:
            return stamp
    return _stamp(record.get("timestamp"))


def _contract_usage(value: dict[str, Any]) -> dict[str, int]:
    return {
        "total": value["total_tokens"],
        "input": value["input_tokens"],
        "cached_input": value["cached_input_tokens"],
        "output": value["output_tokens"],
        "reasoning_output": value.get("reasoning_output_tokens", 0),
    }


def _valid_contract_usage(value: Any) -> bool:
    if not isinstance(value, dict) or any(key not in value for key in USAGE_KEYS):
        return False
    if any(type(value[key]) is not int or not 0 <= value[key] <= _MAX_INT for key in USAGE_KEYS):
        return False
    return (
        value["total"] == value["input"] + value["output"]
        and value["cached_input"] <= value["input"]
        and value["reasoning_output"] <= value["output"]
    )


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and _HASH_RE.fullmatch(value) is not None


def _is_record(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("payload"), dict)


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    return record.get("payload") if isinstance(record.get("payload"), dict) else {}


def _metadata(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return child id and nested parent id from an authentic child metadata record."""

    child = _bounded_text(payload.get("id"))
    source = payload.get("source")
    subagent = source.get("subagent") if isinstance(source, dict) else None
    spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
    parent = _bounded_text(spawn.get("parent_thread_id")) if isinstance(spawn, dict) else None
    if child is None or parent is None or child == parent:
        return None, None
    return child, parent


def _iter_records(raw: bytes, *, drop_first: bool) -> tuple[list[dict[str, Any]], bool]:
    """Decode complete JSON lines only; return a malformed-line flag."""

    lines = raw.split(b"\n")
    malformed = False
    if drop_first and lines:
        lines = lines[1:]
    if lines and lines[-1]:
        try:
            json.loads(lines[-1])
        except (ValueError, UnicodeError, RecursionError):
            lines.pop()
            # The final record was not complete at the snapshot boundary.  A
            # later preview may recover it, so retain the data but mark this
            # scan partial rather than claiming full coverage.
            malformed = True
    records: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            malformed = True
            continue
        if _is_record(record):
            records.append(record)
    # A bounded scan must expose truncation as incomplete coverage.  Treating
    # an over-limit page as a clean snapshot could incorrectly produce an
    # ``observed`` subtotal from only the first records.
    if len(records) > MAX_SCAN_RECORDS:
        malformed = True
    return records[:MAX_SCAN_RECORDS], malformed


def _open_regular(path: Path) -> tuple[int, os.stat_result] | None:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            os.close(descriptor)
            return None
        return descriptor, info
    except (OSError, ValueError):
        return None


def _scan(
    path_value: Any,
    *,
    expected_child: str | None,
    expected_parent: str | None,
) -> dict[str, Any]:
    """Read at most 128 KiB of header and 1 MiB of tail from one transcript."""

    result: dict[str, Any] = {
        "status": "unavailable",
        "child_id": None,
        "parent_id": None,
        "requests": [],
        "terminal_observed": False,
        "scan_bytes": 0,
        "tail_limited": False,
        "malformed": False,
        "lifecycle_malformed": False,
        "identity_ambiguous": False,
        "metadata_conflict": False,
        "terminal_reset": False,
        # Lifecycle evidence is retained in memory only.  It lets collection
        # distinguish a child that finished before the parent window from a
        # child that was reused or remained active across it.
        "lifecycle": [],
        "lifecycle_suffix_observed": False,
    }
    path = Path(path_value) if isinstance(path_value, str) and path_value else None
    if path is None:
        return result
    opened = _open_regular(path)
    if opened is None:
        return result
    descriptor, info = opened
    try:
        size = info.st_size
        header_size = min(size, HEADER_BYTES)
        os.lseek(descriptor, 0, os.SEEK_SET)
        header = os.read(descriptor, header_size)
        tail_offset = max(0, size - TAIL_BYTES)
        os.lseek(descriptor, tail_offset, os.SEEK_SET)
        tail = os.read(descriptor, size - tail_offset)
        after = os.fstat(descriptor)
        if after.st_size != size or len(header) != header_size or len(tail) != size - tail_offset:
            result["malformed"] = True
            result["lifecycle_malformed"] = True
        result["scan_bytes"] = len(header) + len(tail)
        result["tail_limited"] = size > TAIL_BYTES
        result["lifecycle_suffix_observed"] = not bool(tail_offset)
    except (OSError, ValueError):
        return result
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass

    header_records, header_bad = _iter_records(header, drop_first=False)
    tail_records, tail_bad = _iter_records(tail, drop_first=bool(tail_offset))
    # When the tail contains the entire file, its header is redundant and
    # may end mid-record merely because of the header read bound. Do not
    # mistake that artificial cut for malformed source or replay lifecycle
    # events by parsing the same prefix twice.
    result["malformed"] |= tail_bad or (bool(tail_offset) and header_bad)
    # A header ending in the middle of a line is expected for a bounded read
    # of a large source.  The suffix remains a complete lifecycle boundary as
    # long as its own final line parsed cleanly; keep that distinction for
    # window-scope filtering while preserving the public partial status.
    result["lifecycle_malformed"] |= tail_bad
    records = header_records + tail_records if tail_offset else tail_records
    if len(records) > MAX_SCAN_RECORDS:
        # Header and tail are each bounded independently; their concatenation
        # can still exceed the per-page record cap (and therefore has unknown
        # coverage even when every individual line parsed successfully).
        result["malformed"] = True
        result["lifecycle_malformed"] = True
    child_id = expected_child
    parent_id = expected_parent
    matched_metadata = False
    active_thread: str | None = None
    child_turns: set[str] = set()
    observations: dict[str, dict[str, Any]] = {}
    header_count = len(header_records)
    for index, record in enumerate(records[:MAX_SCAN_RECORDS]):
        payload = _payload(record)
        record_type = record.get("type")
        payload_type = payload.get("type")
        if record_type == "session_meta":
            candidate, candidate_parent = _metadata(payload)
            raw_id = _bounded_text(payload.get("id"))
            if (
                expected_child is not None
                and raw_id == expected_child
                and expected_parent is not None
                and candidate_parent != expected_parent
            ):
                # A later matching record cannot repair contradictory
                # attribution for the same child. Metadata for other ids is
                # expected when a fork includes its parent's history.
                result["metadata_conflict"] = True
            if candidate is not None and candidate_parent is not None:
                if expected_child is None or candidate == expected_child:
                    if expected_parent is None or candidate_parent == expected_parent:
                        child_id, parent_id, matched_metadata = candidate, candidate_parent, True
            active_thread = raw_id
        if record_type != "token_usage_record":
            if record_type == "event_msg" and payload_type in (
                "task_started",
                "task_complete",
                "turn_aborted",
            ):
                explicit = _bounded_text(payload.get("thread_id"))
                event_turn = _bounded_text(payload.get("turn_id"))
                belongs = explicit == child_id or (
                    explicit is None
                    and (active_thread == child_id or event_turn in child_turns)
                )
                if belongs:
                    kind = "started" if payload_type == "task_started" else "terminal"
                    lifecycle_stamp = _lifecycle_stamp(record, payload, kind)
                    result["lifecycle"].append(
                        {"kind": kind, "stamp": lifecycle_stamp}
                    )
                    # An event in the suffix is enough to establish the final
                    # lifecycle state even when the middle of a large source
                    # was outside the bounded scan.  If there is no suffix
                    # event, collection must retain unknown coverage.
                    if index >= header_count:
                        result["lifecycle_suffix_observed"] = True
                    if payload_type in ("task_complete", "turn_aborted"):
                        result["terminal_observed"] = True
                        result["terminal_reset"] = False
                    else:
                        result["terminal_observed"] = False
                        result["terminal_reset"] = True
            continue
        # Parent records copied into a fork can share the file.  A request is
        # accepted only with an explicit child thread and bounded turn id.
        explicit_thread = _bounded_text(payload.get("thread_id"))
        turn_id = _bounded_text(payload.get("turn_id"))
        response_id = _bounded_text(payload.get("response_id"))
        stamp = _stamp(record.get("timestamp"))
        usage = request_usage(payload.get("usage"))
        contract = _contract_usage(usage) if usage is not None else None
        if (
            child_id is None
            or explicit_thread != child_id
            or turn_id is None
            or response_id is None
            or stamp is None
            or usage is None
            or not _valid_contract_usage(contract)
        ):
            # A missing/invalid child identity is an unresolved observation,
            # whereas an explicit *different* thread is an expected fork
            # copy and is intentionally excluded without affecting coverage.
            if explicit_thread != child_id and explicit_thread is not None:
                continue
            if (
                explicit_thread == child_id
                or explicit_thread is None
                or response_id is not None
                or usage is not None
            ):
                result["identity_ambiguous"] = True
            continue
        response_hash = stable_hash(response_id)
        item = {
            "child_hash": stable_hash(child_id),
            "response_hash": response_hash,
            "turn_hash": stable_hash(turn_id),
            "stamp": stamp,
            "usage": contract,
            "conflict": False,
            "source": "live",
        }
        child_turns.add(turn_id)
        if result["terminal_observed"]:
            result["terminal_observed"] = False
            result["terminal_reset"] = True
        prior = observations.get(response_hash)
        if prior is None:
            observations[response_hash] = item
        elif (
            prior["usage"] != item["usage"]
            or prior["turn_hash"] != item["turn_hash"]
            or prior["stamp"] != item["stamp"]
        ):
            prior["conflict"] = True
    if result["metadata_conflict"]:
        result["identity_ambiguous"] = True
        result["terminal_observed"] = False
        return result
    result["child_id"] = child_id if matched_metadata else None
    result["parent_id"] = parent_id if matched_metadata else None
    result["requests"] = list(observations.values())
    if matched_metadata:
        result["status"] = "observed"
        if result["malformed"] or result["tail_limited"] or result["identity_ambiguous"]:
            result["status"] = "partial"
    return result


def _root_dir(root: Path) -> Path:
    root = Path(root)
    if root.is_symlink():
        raise ValueError("child usage root is a symlink")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("child usage root is not a directory")
    return root


def _connect(root: Path) -> sqlite3.Connection:
    directory = _root_dir(root)
    path = directory / DB_NAME
    if path.is_symlink():
        raise ValueError("child usage database is a symlink")
    if not path.exists():
        try:
            descriptor = os.open(
                path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
    if path.is_symlink() or not path.is_file():
        raise ValueError("child usage database is not a regular file")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    connection = sqlite3.connect(path, timeout=0.25, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=250")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS agents ("
        "identity_hash TEXT PRIMARY KEY, parent_hash TEXT, agent_hash TEXT, child_hash TEXT, "
        "status TEXT NOT NULL, terminal_observed INTEGER NOT NULL DEFAULT 0, "
        "observed_at REAL, scan_bytes INTEGER NOT NULL DEFAULT 0)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS requests ("
        "child_hash TEXT NOT NULL, parent_hash TEXT NOT NULL, response_hash TEXT NOT NULL, "
        "turn_hash TEXT NOT NULL, stamp REAL NOT NULL, total INTEGER NOT NULL, "
        "input INTEGER NOT NULL, cached_input INTEGER NOT NULL, output INTEGER NOT NULL, "
        "reasoning_output INTEGER NOT NULL, source TEXT NOT NULL, "
        "conflict INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY(child_hash,response_hash))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS requests_child_stamp ON requests(child_hash,stamp)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS child_revocations (child_hash TEXT PRIMARY KEY)"
    )
    return connection


def _status_better(old: str | None, new: str) -> str:
    if old not in _STATUS_RANK:
        return new
    return old if _STATUS_RANK[old] >= _STATUS_RANK[new] else new


def _revocations(connection: sqlite3.Connection, child_hashes: set[str]) -> set[str]:
    if not child_hashes:
        return set()
    placeholders = ",".join("?" for _ in child_hashes)
    rows = connection.execute(
        "SELECT child_hash FROM child_revocations WHERE child_hash IN ("
        + placeholders + ") OR child_hash=?",
        (*child_hashes, _REVOCATIONS_SATURATED),
    ).fetchall()
    revoked = {row[0] for row in rows}
    return set(child_hashes) if _REVOCATIONS_SATURATED in revoked else revoked


def _revoke_child(connection: sqlite3.Connection, child_hash: str) -> None:
    """Persist a monotonic child-level rejection inside the writer transaction."""

    if _revocations(connection, {child_hash}):
        return
    count = connection.execute("SELECT count(*) FROM child_revocations").fetchone()[0]
    marker = child_hash if count < MAX_DB_AGENTS else _REVOCATIONS_SATURATED
    connection.execute(
        "INSERT OR IGNORE INTO child_revocations(child_hash) VALUES(?)", (marker,)
    )


def _load_revocations(root: Path, child_hashes: set[str]) -> tuple[set[str], bool]:
    """Take the report's final revocation snapshot; an unreadable gate denies usage."""

    connection = None
    try:
        connection = _connect(root)
        return _revocations(connection, child_hashes), False
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return set(child_hashes), True
    finally:
        if connection is not None:
            connection.close()


def _save_scan(root: Path, scan: dict[str, Any], *, parent_hash: str, agent_hash: str | None,
               now: float, force_terminal: bool = False) -> None:
    child_id = scan.get("child_id")
    child_hash = stable_hash(child_id) if isinstance(child_id, str) else None
    if child_hash is None and agent_hash is None:
        return
    identity_hash = stable_hash([parent_hash, child_hash or agent_hash])
    status = scan.get("status") if scan.get("status") in _STATUS_RANK else "unavailable"
    connection = None
    try:
        connection = _connect(root)
        connection.execute("BEGIN IMMEDIATE")
        if scan.get("metadata_conflict"):
            # Revoke previously cached attribution as well. Keeping a
            # conflicting receipt as merely partial would still include its
            # tokens, and a missing later source could resurrect that sum.
            if _valid_hash(agent_hash):
                _revoke_child(connection, agent_hash)
                connection.execute(
                    "UPDATE requests SET conflict=1 WHERE child_hash=?", (agent_hash,)
                )
                connection.execute(
                    "UPDATE agents SET status=CASE WHEN status='observed' THEN 'partial' "
                    "ELSE status END, terminal_observed=0 WHERE child_hash=?",
                    (agent_hash,),
                )
            connection.commit()
            return
        # A scan has no trusted generation/revalidation proof. Both scans
        # obtained before the conflict and later ordinary scans remain
        # revoked. This check and every insert share the same write lock.
        if _revocations(connection, {child_hash or agent_hash}):
            connection.commit()
            return
        existing = connection.execute(
            "SELECT * FROM agents WHERE identity_hash=?", (identity_hash,)
        ).fetchone()
        if existing is None:
            agent_count = connection.execute("SELECT count(*) FROM agents").fetchone()[0]
            if agent_count >= MAX_DB_AGENTS:
                connection.commit()
                return
            connection.execute(
                "INSERT INTO agents(identity_hash,parent_hash,agent_hash,child_hash,status,"
                "terminal_observed,observed_at,scan_bytes) VALUES(?,?,?,?,?,?,?,?)",
                (
                    identity_hash,
                    parent_hash,
                    agent_hash,
                    child_hash,
                    status,
                    int(
                        bool(force_terminal or scan.get("terminal_observed"))
                        and not scan.get("terminal_reset")
                    ),
                    now,
                    min(int(scan.get("scan_bytes", 0) or 0), HEADER_BYTES + TAIL_BYTES),
                ),
            )
        else:
            status = _status_better(existing["status"], status)
            terminal_reset = bool(scan.get("terminal_reset"))
            terminal_value = (
                0
                if terminal_reset
                else max(
                    int(existing["terminal_observed"]),
                    int(bool(force_terminal or scan.get("terminal_observed"))),
                )
            )
            connection.execute(
                "UPDATE agents SET parent_hash=COALESCE(parent_hash,?), "
                "agent_hash=COALESCE(agent_hash,?), child_hash=COALESCE(child_hash,?), "
                "status=?, terminal_observed=?, observed_at=?, "
                "scan_bytes=MAX(scan_bytes,?) WHERE identity_hash=?",
                (
                    parent_hash,
                    agent_hash,
                    child_hash,
                    status,
                    terminal_value,
                    now,
                    min(int(scan.get("scan_bytes", 0) or 0), HEADER_BYTES + TAIL_BYTES),
                    identity_hash,
                ),
            )
        for item in scan.get("requests", [])[:MAX_SCAN_RECORDS]:
            usage = item.get("usage")
            if (
                not _valid_hash(child_hash)
                or not _valid_hash(item.get("response_hash"))
                or not _valid_hash(item.get("turn_hash"))
                or not _valid_contract_usage(usage)
                or not isinstance(item.get("stamp"), (int, float))
                or not math.isfinite(float(item["stamp"]))
                or item.get("source") not in ("hook", "live")
            ):
                continue
            values = tuple(usage[key] for key in USAGE_KEYS)
            prior = connection.execute(
                "SELECT total,input,cached_input,output,reasoning_output,turn_hash,stamp,conflict "
                "FROM requests WHERE child_hash=? AND response_hash=?",
                (child_hash, item.get("response_hash")),
            ).fetchone()
            if prior is None:
                count = connection.execute("SELECT count(*) FROM requests").fetchone()[0]
                if count >= MAX_DB_REQUESTS:
                    continue
                connection.execute(
                    "INSERT INTO requests(child_hash,parent_hash,response_hash,turn_hash,stamp,"
                    "total,input,cached_input,output,reasoning_output,source,conflict) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        child_hash,
                        parent_hash,
                        item["response_hash"],
                        item["turn_hash"],
                        float(item["stamp"]),
                        *values,
                        item.get("source", "hook"),
                        int(bool(item.get("conflict"))),
                    ),
                )
            else:
                conflict = (
                    bool(prior["conflict"])
                    or tuple(prior[:5]) != values
                    or prior["turn_hash"] != item["turn_hash"]
                    or float(prior["stamp"]) != float(item["stamp"])
                    or bool(item.get("conflict"))
                )
                connection.execute(
                    "UPDATE requests SET conflict=? WHERE child_hash=? AND response_hash=?",
                    (int(conflict), child_hash, item["response_hash"]),
                )
        connection.commit()
    except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError):
        if connection is not None:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
    finally:
        if connection is not None:
            connection.close()


def capture(payload: dict, root: Path, *, home=None, now=None) -> None:
    """Persist any child request evidence available at ``SubagentStop``.

    All failures are intentionally swallowed: accounting a child must never
    change hook continuation or block the primary agent.
    """

    try:
        if not isinstance(payload, dict) or payload.get("hook_event_name") != "SubagentStop":
            return None
        # Native SubagentStop uses the session-tree root for every descendant,
        # including nested agents.  The direct parent must come from the
        # catalog; a matching timestamp or working directory is not lineage.
        agent_id = _uuid(payload.get("agent_id"))
        lineage = _hook_lineage(agent_id, payload.get("session_id"), home)
        if lineage is None:
            return None
        parent, catalog_path = lineage
        parent_hash = stable_hash(parent)
        agent_hash = _hash(agent_id)
        stamp = _stamp(now) if now is not None else time.time()
        if stamp is None:
            stamp = time.time()
        path = payload.get("agent_transcript_path")
        if not _bounded_text(path):
            path = catalog_path
        expected_child = agent_id
        scan = (
            _scan(path, expected_child=expected_child, expected_parent=parent)
            if _bounded_text(path)
            else _empty_scan()
        )
        # Mark evidence captured at the stop boundary separately from a later
        # transcript reconciliation.  The persisted schema keeps only this
        # small source label, never the native transcript itself.
        for item in scan.get("requests", []):
            item["source"] = "hook"
        if scan.get("child_id") is None and agent_hash is None:
            return None
        _save_scan(
            Path(root),
            scan,
            parent_hash=parent_hash,
            agent_hash=agent_hash,
            now=stamp,
            force_terminal=scan.get("child_id") is not None,
        )
    except Exception:
        return None
    return None


def _empty_scan() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "child_id": None,
        "parent_id": None,
        "requests": [],
        "terminal_observed": False,
        "scan_bytes": 0,
        "tail_limited": False,
        "malformed": False,
        "lifecycle_malformed": False,
        "identity_ambiguous": False,
        "metadata_conflict": False,
        "terminal_reset": False,
        "lifecycle": [],
        "lifecycle_suffix_observed": False,
    }


def _paths(catalog: TaskCatalog, rows: list[dict[str, Any]]) -> dict[str, str]:
    """Read only selected rollout paths when the native schema exposes them."""

    connection = getattr(catalog, "connection", None)
    if connection is None:
        return {}
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)").fetchall()}
        if "rollout_path" not in columns:
            return {}
        result: dict[str, str] = {}
        for row in rows[:MAX_ROWS]:
            identifier = row.get("id")
            if not isinstance(identifier, str):
                continue
            found = connection.execute(
                "SELECT rollout_path FROM threads WHERE id=?", (identifier,)
            ).fetchone()
            path = found[0] if found else None
            if isinstance(path, str) and path:
                result[identifier] = path
        return result
    except (sqlite3.Error, TypeError, ValueError):
        return {}


def _hook_lineage(agent_id: Any, root_id: Any, home=None) -> tuple[str, str | None] | None:
    """Prove the agent's direct parent and ancestry up to the hook root."""

    child_id, root_id = _uuid(agent_id), _uuid(root_id)
    if child_id is None or root_id is None or child_id == root_id:
        return None
    try:
        with TaskCatalog(home) as catalog:
            row = catalog.get(child_id)
            direct_parent = row.get("parent_id")
            if row.get("role") != "subagent" or direct_parent is None:
                return None
            current, seen = row, {child_id}
            for _ in range(MAX_DEPTH):
                parent_id = current.get("parent_id")
                if parent_id is None or parent_id in seen:
                    return None
                parent = catalog.get(parent_id)
                if parent_id == root_id:
                    if parent.get("role") != "parent" or parent.get("parent_id") is not None:
                        return None
                    return direct_parent, _paths(catalog, [row]).get(child_id)
                if parent.get("role") != "subagent":
                    return None
                seen.add(parent_id)
                current = parent
            return None
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None


def _load_cache(
    root: Path, child_hashes: set[str]
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, sqlite3.Row], bool]:
    observations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    agents: dict[str, sqlite3.Row] = {}
    if not child_hashes:
        return observations, agents, False
    connection = None
    failed = False
    try:
        connection = _connect(Path(root))
        placeholders = ",".join("?" for _ in child_hashes)
        selected = tuple(child_hashes)
        rows = connection.execute(
            "SELECT * FROM agents WHERE child_hash IN (" + placeholders + ") LIMIT ?",
            (*selected, MAX_DB_AGENTS),
        ).fetchall()
        for row in rows:
            if (
                not _valid_hash(row["child_hash"])
                or row["child_hash"] not in child_hashes
                or not _valid_hash(row["parent_hash"])
                or (
                    row["agent_hash"] is not None
                    and not _valid_hash(row["agent_hash"])
                )
                or row["status"] not in _STATUS_RANK
                or type(row["terminal_observed"]) is not int
                or row["terminal_observed"] not in (0, 1)
            ):
                failed = True
                continue
            agents[row["child_hash"]] = row
        rows = connection.execute(
            "SELECT * FROM requests WHERE child_hash IN (" + placeholders + ") "
            "ORDER BY stamp LIMIT ?",
            (*selected, MAX_DB_REQUESTS),
        ).fetchall()
        for row in rows:
            usage = {key: row[key] for key in USAGE_KEYS}
            stamp = row["stamp"]
            if (
                not _valid_hash(row["child_hash"])
                or row["child_hash"] not in child_hashes
                or not _valid_hash(row["parent_hash"])
                or not _valid_hash(row["response_hash"])
                or not _valid_hash(row["turn_hash"])
                or type(stamp) not in (int, float)
                or isinstance(stamp, bool)
                or not math.isfinite(float(stamp))
                or not _valid_contract_usage(usage)
                or row["source"] not in ("hook", "live")
                or type(row["conflict"]) is not int
                or row["conflict"] not in (0, 1)
            ):
                failed = True
                continue
            observations[row["child_hash"]].append(
                {
                    "child_hash": row["child_hash"],
                    "response_hash": row["response_hash"],
                    "turn_hash": row["turn_hash"],
                    "stamp": row["stamp"],
                    "usage": usage,
                    "conflict": bool(row["conflict"]),
                    "source": row["source"],
                }
            )
    except (OSError, sqlite3.Error, ValueError, TypeError):
        failed = True
    finally:
        if connection is not None:
            connection.close()
    return observations, agents, failed


def _merge_observations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in items:
        response_hash = item.get("response_hash")
        if not isinstance(response_hash, str):
            continue
        prior = merged.get(response_hash)
        if prior is None:
            merged[response_hash] = dict(item)
            continue
        if (
            prior.get("child_hash") != item.get("child_hash")
            or prior.get("usage") != item.get("usage")
            or prior.get("turn_hash") != item.get("turn_hash")
            or prior.get("stamp") != item.get("stamp")
        ):
            prior["conflict"] = True
        elif bool(item.get("conflict")):
            prior["conflict"] = True
    return list(merged.values())


def _in_window(stamp: Any, since: float, until: float) -> bool:
    return (
        isinstance(stamp, (int, float))
        and math.isfinite(float(stamp))
        and since <= float(stamp) < until
    )


def _created_at(catalog: TaskCatalog, rows: list[dict[str, Any]]) -> dict[str, float]:
    """Read native creation evidence when the catalog exposes it.

    Creation is useful for excluding a child that did not exist until after a
    historical report window.  It is deliberately not used as an end-time
    or idle-time signal; a child created before the window still needs
    lifecycle evidence (or remains unknown).
    """

    connection = getattr(catalog, "connection", None)
    if connection is None:
        return {}
    try:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(threads)").fetchall()
        }
        candidates = [
            name for name in ("created_at_ms", "created_at") if name in columns
        ]
        if not candidates:
            return {}
        selected = [row.get("id") for row in rows[:MAX_ROWS] if isinstance(row.get("id"), str)]
        if not selected:
            return {}
        placeholders = ",".join("?" for _ in selected)
        fields = ",".join(candidates)
        result: dict[str, float] = {}
        for native in connection.execute(
            "SELECT id," + fields + " FROM threads WHERE id IN (" + placeholders + ")",
            tuple(selected),
        ).fetchall():
            stamp = None
            for field in candidates:
                stamp = _native_stamp(native[field])
                if stamp is not None:
                    break
            if stamp is not None:
                result[native["id"]] = stamp
        return result
    except (sqlite3.Error, TypeError, ValueError, OverflowError):
        return {}


def _scope_intersects(
    scan: dict[str, Any] | None,
    items: list[dict[str, Any]],
    since: float,
    until: float,
    *,
    created_at: float | None = None,
) -> bool:
    """Return whether reliable child activity intersects ``[since, until)``.

    A complete lifecycle timeline can prove that an old child finished before
    the window, or that a child was created after it.  Every missing or
    incomplete piece of evidence stays in scope so it is reported as
    unknown/partial rather than silently treated as zero.
    """

    if created_at is not None and created_at >= until:
        return False
    if any(_in_window(item.get("stamp"), since, until) for item in items):
        return True
    if not scan:
        return True
    # A scan without matching metadata cannot prove which lifecycle belongs
    # to this child.  Keep the row in the denominator and expose its unknown
    # state to the caller.
    if scan.get("child_id") is None:
        return True
    lifecycle = scan.get("lifecycle")
    if not isinstance(lifecycle, list) or not lifecycle:
        return True
    if (
        scan.get("lifecycle_malformed")
        or scan.get("identity_ambiguous")
        or not scan.get("lifecycle_suffix_observed")
    ):
        return True

    timeline: list[tuple[str, float]] = []
    previous: float | None = None
    for event in lifecycle:
        if not isinstance(event, dict) or event.get("kind") not in ("started", "terminal"):
            return True
        stamp = event.get("stamp")
        if (
            not isinstance(stamp, (int, float))
            or isinstance(stamp, bool)
            or not math.isfinite(float(stamp))
        ):
            return True
        stamp = float(stamp)
        # Native event order and timestamps should agree.  A disagreement is
        # safer as unknown than as evidence that an interval missed the
        # requested window.
        if previous is not None and stamp < previous:
            return True
        previous = stamp
        timeline.append((event["kind"], stamp))

    if any(since <= stamp < until for _, stamp in timeline):
        return True

    active_start: float | None = None
    for kind, stamp in timeline:
        if kind == "started":
            # Nested starts without a terminal boundary cannot establish a
            # closed interval; fail closed for the scope denominator.
            if active_start is not None:
                return True
            active_start = stamp
            continue
        if active_start is None:
            # A start may be outside the bounded prefix of a large source.
            # A terminal strictly before the window still proves that this
            # observed lifecycle had ended; a terminal at/after the window
            # cannot prove that it did not overlap the window.
            if stamp < since:
                continue
            return True
        if active_start < until and stamp > since:
            return True
        active_start = None

    if active_start is not None and active_start < until:
        return True
    return False


def collect(root: Path, session: str, since: float, until: float, *, home=None,
            unnamed_label="未命名任務") -> dict:
    """Return a bounded child subtotal for one parent turn window."""

    result = _empty()
    try:
        if not isinstance(session, str) or not session:
            return result
        if (
            isinstance(since, bool)
            or isinstance(until, bool)
            or not isinstance(since, (int, float))
            or not isinstance(until, (int, float))
            or not math.isfinite(float(since))
            or not math.isfinite(float(until))
            or float(until) < float(since)
        ):
            return result
        since, until = float(since), float(until)
        with TaskCatalog(home, unnamed_label=unnamed_label) as catalog:
            parent = catalog.get(session)
            family = catalog.family(parent, limit=MAX_DESCENDANTS)
            limited = bool(getattr(catalog, "limited", False))
            descendants = [
                row
                for row in family
                if row.get("id") != session and row.get("role") == "subagent"
            ]
            if not descendants:
                if limited:
                    limited_result = _empty("partial")
                    limited_result["selection_limited"] = True
                    return limited_result
                empty = _empty("none")
                empty["usage"] = dict(_ZERO_USAGE)
                empty["selection_limited"] = limited
                return empty
            descendants = descendants[:MAX_ROWS]
            paths = _paths(catalog, descendants)
            created_at = _created_at(catalog, descendants)
            descriptions: dict[str, dict[str, Any]] = {}
            for row in descendants:
                try:
                    descriptions[row["id"]] = catalog.describe(row)
                except Exception:
                    descriptions[row["id"]] = {}
        child_hashes = {
            stable_hash(row["id"])
            for row in descendants
            if isinstance(row.get("id"), str)
        }
        cached, agent_cache, cache_failed = _load_cache(Path(root), child_hashes)
        all_items: dict[str, list[dict[str, Any]]] = defaultdict(list)
        statuses: dict[str, str] = {}
        terminals: dict[str, bool] = {}
        scans: dict[str, dict[str, Any]] = {}
        scan_bytes = 0
        for row in descendants:
            child_id = row.get("id")
            if not isinstance(child_id, str):
                continue
            child_hash = stable_hash(child_id)
            all_items[child_hash].extend(cached.get(child_hash, []))
            cached_agent = agent_cache.get(child_hash)
            statuses[child_hash] = cached_agent["status"] if cached_agent else "unavailable"
            terminals[child_hash] = bool(cached_agent and cached_agent["terminal_observed"])
            # A native creation timestamp is a safe lower-boundary signal:
            # this child did not exist during the requested window.  Skip its
            # transcript entirely to avoid scanning a future child.
            if created_at.get(child_id) is not None and created_at[child_id] >= until:
                continue
            path = paths.get(child_id)
            if path:
                scan = _scan(
                    path,
                    expected_child=child_id,
                    expected_parent=row.get("parent_id"),
                )
                scans[child_hash] = scan
                scan_bytes += int(scan.get("scan_bytes", 0) or 0)
                if scan.get("metadata_conflict"):
                    # Both the fresh scan and older receipts now have
                    # disputed lineage. Preserve the missing row, never its
                    # subtotal, and retain the rejection in the cache.
                    statuses[child_hash] = (
                        "partial"
                        if all_items[child_hash]
                        or (cached_agent and cached_agent["status"] != "unavailable")
                        else "unavailable"
                    )
                    all_items[child_hash].clear()
                    terminals[child_hash] = False
                    _save_scan(
                        Path(root),
                        scan,
                        parent_hash=stable_hash(row.get("parent_id") or session),
                        agent_hash=child_hash,
                        now=time.time(),
                    )
                elif scan.get("child_id") == child_id:
                    all_items[child_hash].extend(scan.get("requests", []))
                    # A fresh transcript is authoritative for terminal state.
                    # A later task_started/request invalidates an old Stop;
                    # never OR a stale completion forever.
                    terminals[child_hash] = bool(scan.get("terminal_observed"))
                    scan_status = scan.get("status", "unavailable")
                    statuses[child_hash] = scan_status
                    if (
                        scan.get("tail_limited")
                        or scan.get("malformed")
                        or scan.get("identity_ambiguous")
                        or (
                            cached_agent is not None
                            and scan.get("scan_bytes", 0) < cached_agent["scan_bytes"]
                        )
                    ):
                        # A smaller source may have been replaced/truncated
                        # between reads. Keep known receipts, not a claim of
                        # complete fresh coverage.
                        statuses[child_hash] = "partial"
                    parent_hash = (
                        stable_hash(row.get("parent_id"))
                        if row.get("parent_id")
                        else stable_hash(session)
                    )
                    _save_scan(
                        Path(root),
                        scan,
                        parent_hash=parent_hash,
                        agent_hash=None,
                        now=time.time(),
                    )
                else:
                    # A missing/mismatched/unreadable fresh source cannot be
                    # upgraded to complete by an older cache snapshot.  With
                    # no prior evidence at all this is simply unavailable;
                    # when a cache exists, retain the conservative partial
                    # quality marker rather than presenting stale data as
                    # complete.
                    statuses[child_hash] = (
                        "partial"
                        if cached_agent is not None or bool(cached.get(child_hash))
                        else "unavailable"
                    )
                    terminals[child_hash] = False

        # Cache reads and transcript scans precede this single SQLite read.
        # A revocation committed during either step must suppress even the
        # in-memory copy of previously valid receipts. This is the report's
        # revocation snapshot boundary; a later commit belongs to a later
        # report. No lifecycle from a revoked source can exclude its row.
        revoked, revocation_failed = _load_revocations(Path(root), child_hashes)
        cache_failed |= revocation_failed
        for child_hash in revoked:
            statuses[child_hash] = (
                "partial"
                if all_items[child_hash] or statuses.get(child_hash) != "unavailable"
                else "unavailable"
            )
            all_items[child_hash].clear()
            terminals[child_hash] = False
            scans[child_hash] = _empty_scan()

        # Group globally so a response id attributed to two child Tasks is
        # excluded from both subtotals instead of silently double-counted.
        merged_by_child = {child: _merge_observations(items) for child, items in all_items.items()}
        by_response: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for items in merged_by_child.values():
            for item in items:
                by_response[item["response_hash"]].append(item)
        conflicted: set[str] = set()
        for response_hash, items in by_response.items():
            child_set = {item.get("child_hash") for item in items}
            if len(child_set) > 1 or any(bool(item.get("conflict")) for item in items):
                conflicted.add(response_hash)

        subtotal: dict[str, int] = dict(_ZERO_USAGE)
        total_requests = 0
        rows_out: list[dict[str, Any]] = []
        agents_with_usage = pending = missing = 0
        for row in descendants[:MAX_ROWS]:
            child_id = row.get("id")
            if not isinstance(child_id, str):
                continue
            child_hash = stable_hash(child_id)
            child_items = merged_by_child.get(child_hash, [])
            if not _scope_intersects(
                scans.get(child_hash),
                child_items,
                since,
                until,
                created_at=created_at.get(child_id),
            ):
                # The child has reliable lifecycle evidence entirely outside
                # this parent turn.  It must not inflate missing/pending
                # counts for the current window.
                continue
            valid_items = [
                item
                for item in child_items
                if item.get("response_hash") not in conflicted
                and _in_window(item.get("stamp"), since, until)
            ]
            child_subtotal = dict(_ZERO_USAGE) if valid_items else None
            for item in valid_items:
                for key in USAGE_KEYS:
                    subtotal[key] += int(item["usage"][key])
                    child_subtotal[key] += int(item["usage"][key])
            if valid_items:
                agents_with_usage += 1
                total_requests += len(valid_items)
            terminal = terminals.get(child_hash, False)
            if not terminal:
                pending += 1
            if not valid_items:
                missing += 1
            status = statuses.get(child_hash, "unavailable")
            # Cache-only evidence cannot prove that a later request was not
            # persisted; preserve the conservative partial state.
            if valid_items and not paths.get(child_id):
                status = "partial"
            if valid_items and any(
                item.get("response_hash") in conflicted for item in child_items
            ):
                status = "partial"
            if not valid_items and status == "observed":
                # No records is an unknown unless a complete transcript scan
                # proved an empty window.  ``_scan`` does not currently retain
                # that proof in cache rows, so stay conservative.
                status = "unavailable"
            description = descriptions.get(child_id) or {}
            rows_out.append(
                {
                    "display_name": description.get("display_name") or unnamed_label,
                    "selector": description.get("selector") or child_hash[:12],
                    "parent_name": description.get("parent_name"),
                    "usage": child_subtotal,
                    "status": status,
                    "terminal_observed": bool(terminal),
                    "request_count": len(valid_items),
                }
            )

        if agents_with_usage:
            overall_status = (
                "observed"
                if agents_with_usage == len(rows_out)
                and pending == 0
                and missing == 0
                and all(item["status"] == "observed" for item in rows_out)
                and not limited
                else "partial"
            )
        else:
            # No valid subtotal is conservatively unavailable unless a
            # selected child was actually scanned but had incomplete/invalid
            # coverage.  In that case report the evidence quality rather than
            # silently collapsing it to an unknown zero.
            overall_status = (
                "partial"
                if limited or any(item["status"] == "partial" for item in rows_out)
                else "none"
                if not rows_out
                else "unavailable"
            )
        result.update(
            usage=subtotal if agents_with_usage else None,
            status=overall_status,
            agents_seen=len(rows_out),
            agents_with_usage=agents_with_usage,
            pending_agents=pending,
            missing_agents=missing,
            selection_limited=limited,
            request_count=total_requests,
            scan_bytes=scan_bytes,
            rows=rows_out[:MAX_ROWS],
        )
        if cache_failed and result["status"] == "observed":
            result["status"] = "partial"
        return result
    except Exception:
        return result


__all__ = ["capture", "collect", "DB_NAME", "HEADER_BYTES", "TAIL_BYTES"]
