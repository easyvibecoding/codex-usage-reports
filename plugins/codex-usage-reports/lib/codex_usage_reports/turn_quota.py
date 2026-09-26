"""One account-quota observation per inline card, isolated from token accounting.

Only this Task's immediately preceding turn is compared. No network work occurs
at start/Stop, no quota mutation is performed, and failures never fail a report.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import stat
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from .meter_plan import normalize_subscription, plan_type
from .meter_source import read_meter_sources
from .util import stable_hash

MAX_ROWS = 10_000
MAX_BYTES = 32 * 1024
MAX_BUCKETS = 8
TIMEOUT = 2.0
_HASH = re.compile(r"[a-f0-9]{64}\Z")


def _number(value, *, integer=False):
    if type(value) not in (int, float):
        return None
    try:
        if not math.isfinite(value) or not 0 <= value <= 2**53 - 1:
            return None
        if integer and int(value) != value:
            return None
    except (OverflowError, ValueError):
        return None
    return int(value) if integer else value


def _normalize(sources):
    """Drop raw account/bucket IDs, names, balances, errors and arbitrary text."""
    start, end = (_number(sources.get(key)) for key in ("started_at", "finished_at"))
    if start is None or end is None or end < start:
        return {"status": "unavailable", "rows": [], "captured_at": None}
    raw = sources.get("rate_limits")
    raw = raw if isinstance(raw, dict) else {}
    account = raw.get("accountId")
    by_id = raw.get("rateLimitsByLimitId")
    if isinstance(by_id, dict):
        buckets = sorted(by_id.items(), key=lambda item: item[0] != "codex")[:MAX_BUCKETS]
    else:
        bucket = raw.get("rateLimits")
        buckets = [(bucket.get("limitId") or "legacy", bucket)] if isinstance(bucket, dict) else []
    rows, plans = [], []
    for key, bucket in buckets:
        if not isinstance(key, str) or not 0 < len(key) <= 128 or not isinstance(bucket, dict):
            continue
        if bucket.get("limitId", key) != key:
            continue
        plan = plan_type(bucket.get("planType"))
        plans.append({"plan_type": plan})
        alias = bucket.get("normalModelSlug")
        alias = stable_hash(alias) if isinstance(alias, str) and 0 < len(alias) <= 128 else None
        for slot in ("primary", "secondary"):
            window = bucket.get(slot)
            if not isinstance(window, dict):
                continue
            used = _number(window.get("usedPercent"))
            reset = _number(window.get("resetsAt"), integer=True)
            remaining = None
            if used is not None:
                difference = Decimal(100) - Decimal(str(used))
                remaining = float(max(Decimal(0), min(Decimal(100), difference)))
            rows.append({
                "window_hash": stable_hash([key, slot]),
                "bucket": key if key in ("codex", "spark", "codex_bengalfox") else "other",
                "duration_minutes": _number(window.get("windowDurationMins"), integer=True) or None,
                "resets_at": reset or None,
                "remaining_percent": remaining if reset is None or end < reset else None,
                "plan_type": plan, "alias_hash": alias,
                "expired": reset is not None and end >= reset,
            })
    return {
        "status": "observed" if any(r["remaining_percent"] is not None for r in rows)
        else "unavailable",
        "capture_started_at": start, "captured_at": end,
        "account_hash": stable_hash(account)
        if isinstance(account, str) and 0 < len(account) <= 512 else None,
        "subscription": normalize_subscription(sources.get("account_response"), plans),
        "rows": sorted(rows, key=lambda r: (r["bucket"] != "codex", r["bucket"],
                                             r["duration_minutes"] or 0, r["window_hash"])),
    }


def _compare(current, previous, *, first=False):
    """Remaining-percentage-point movement, never quota attributed to this Task."""
    subscription = current.get("subscription") or {}
    displayed_plan = subscription.get("plan_type") if (
        subscription.get("status") == "reported" and subscription.get("auth_type") == "chatgpt"
    ) else None
    result = {"status": current["status"], "captured_at": current.get("captured_at"),
              "plan_type": displayed_plan,
              "previous_captured_at": (previous or {}).get("captured_at"), "rows": []}
    old = {row["window_hash"]: row for row in (previous or {}).get("rows", [])}
    for row in current["rows"]:
        before = old.get(row["window_hash"])
        comparison, delta = "baseline" if first else "previous_unavailable", None
        if row.get("expired"):
            comparison = "expired"
        elif row["remaining_percent"] is None:
            comparison = "not_comparable"
        elif previous and before:
            prior_plan = previous.get("subscription") or {}
            plan = current.get("subscription") or {}
            if (not current.get("account_hash") or not previous.get("account_hash")
                    or current["account_hash"] != previous["account_hash"]
                    or any(p.get("status") == "conflicted" for p in (plan, prior_plan))
                    or plan.get("auth_type") != prior_plan.get("auth_type")
                    or plan.get("plan_type") != prior_plan.get("plan_type")
                    or not row["plan_type"] or row["plan_type"] != before["plan_type"]
                    or row["alias_hash"] != before["alias_hash"]
                    or current["capture_started_at"] <= previous["captured_at"]):
                comparison = "not_comparable"
            elif not row["duration_minutes"] or not row["resets_at"] or not before["resets_at"]:
                comparison = "not_comparable"
            elif (row["duration_minutes"] != before["duration_minutes"]
                  or row["resets_at"] != before["resets_at"]
                  or current["captured_at"] >= before["resets_at"]):
                comparison = "reset"
            elif before["remaining_percent"] is None:
                comparison = "previous_unavailable"
            else:
                movement = float(Decimal(str(row["remaining_percent"]))
                                 - Decimal(str(before["remaining_percent"])))
                if movement > 0:
                    comparison = "corrected"
                else:
                    delta = movement
                    comparison = "unchanged" if delta == 0 else "observed"
        result["rows"].append({key: row[key] for key in (
            "bucket", "duration_minutes", "resets_at", "remaining_percent"
        )} | {"delta_pp": delta, "comparison": comparison})
    return result


def _regular(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("unsafe quota path")
    if path.exists() and not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("quota path is not a regular file")


def _connect(root, *, create=False):
    path = root / "auto-reports/quota.sqlite3"
    _regular(path)
    if not path.exists():
        if not create:
            return None
        # The report timing directory must already exist; do not create elsewhere.
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
    connection = sqlite3.connect("file:" + quote(str(path.absolute())) + "?mode=rw",
                                 uri=True, timeout=0.25, isolation_level=None)
    connection.row_factory = sqlite3.Row
    if create:
        connection.execute("CREATE TABLE IF NOT EXISTS snapshots ("
                           "key TEXT PRIMARY KEY, task_hash TEXT NOT NULL, "
                           "state TEXT NOT NULL, payload TEXT)")
    return connection


def _load(connection, key):
    row = connection.execute(
        "SELECT state,substr(payload,1,?) AS payload FROM snapshots WHERE key=?",
        (MAX_BYTES + 1, key),
    ).fetchone()
    if (row is None or row["state"] != "saved" or not row["payload"]
            or len(row["payload"]) > MAX_BYTES):
        return None
    value = json.loads(row["payload"])
    return value if isinstance(value, dict) else None


def saved(root: Path, key: str) -> dict:
    """Return only the prior pre-final result; Stop performs no network read."""
    empty = {"status": "unavailable", "captured_at": None, "rows": []}
    try:
        connection = _connect(root)
        if connection is None:
            return empty
        try:
            return (_load(connection, key) or {}).get("display") or empty
        finally:
            connection.close()
    except (OSError, ValueError, sqlite3.Error, TypeError):
        return empty


def observe(root: Path, key: str, *, home=None) -> dict:
    """Claim one capture before reading, compare to this Task's previous turn.

    RPC budget: 2s plus at most 1s process cleanup. No retry after a crash or
    timeout. Only allowlisted quota data is saved, privately, up to 10k turns.
    """
    empty = {"status": "unavailable", "captured_at": None, "rows": []}
    if not isinstance(key, str) or not _HASH.fullmatch(key):
        return empty
    connection = None
    try:
        timing = root / "auto-reports/timing.sqlite3"
        _regular(timing)
        with (closing(sqlite3.connect("file:" + quote(str(timing.absolute())) + "?mode=ro",
                                      uri=True, timeout=0.25)) as index, index):
            index.row_factory = sqlite3.Row
            turn = index.execute("SELECT * FROM turns WHERE key=?", (key,)).fetchone()
            if turn is None or turn["state"] not in ("started", "short"):
                return empty
            previous_turn = index.execute(
                "SELECT key,started FROM turns WHERE session_hash=? AND key<>? "
                "AND started<=? ORDER BY started DESC,key DESC LIMIT 1",
                (turn["session_hash"], key, turn["started"]),
            ).fetchone()
        connection = _connect(root, create=True)
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute("SELECT state FROM snapshots WHERE key=?", (key,)).fetchone()
        if existing:
            value = _load(connection, key)
            connection.commit()
            return value["display"] if value else empty
        if connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] >= MAX_ROWS:
            connection.commit()
            return empty
        prior = _load(connection, previous_turn["key"]) if previous_turn else None
        connection.execute("INSERT INTO snapshots VALUES (?,?,'pending',NULL)",
                           (key, turn["session_hash"]))
        connection.commit()
        # Never hold a database lock while waiting on the account RPC.
        sources = read_meter_sources(timeout=TIMEOUT, codex_home=home, include_usage=False)
        current = _normalize(sources)
        old = (prior or {}).get("snapshot")
        if previous_turn and previous_turn["started"] >= turn["started"]:
            old = None
        display = _compare(current, old, first=previous_turn is None)
        encoded = json.dumps({"snapshot": current, "display": display}, allow_nan=False)
        if len(encoded) > MAX_BYTES:
            return empty
        connection.execute(
            "UPDATE snapshots SET state='saved',payload=? WHERE key=? AND state='pending'",
            (encoded, key),
        )
        return display
    except Exception:
        return empty
    finally:
        if connection is not None:
            connection.close()
