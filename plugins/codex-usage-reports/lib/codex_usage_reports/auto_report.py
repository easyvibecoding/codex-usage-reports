"""Default-on, user-switchable receipts. Reporting never controls the agent loop."""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import sqlite3
import stat
import tempfile
import time
from dataclasses import asdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from .report_i18n import ReportText, resolve_locale
from .task_catalog import task_description
from .transcript import _usage_from_line, request_usage
from .util import stable_hash

SCAN_BYTES = 8 * 1024 * 1024
MAX_ROWS = 10_000
START_EVENTS = {"UserPromptSubmit", "PreToolUse", "PostToolUse"}
EVENTS = START_EVENTS | {"Stop", "Interrupt", "SessionEnd", "SubagentStop"}


def _pending(key: str, locale="zh-Hant") -> str:
    text = ReportText(locale)
    return (
        f"# {text('title')}\n\n{ text('pending')}\n\n{text('pending_note')}\n\n"
        f"<!-- usage-reports-pending:{key} -->\n"
    )


def _footer(directory: Path, key: str, payload: dict[str, Any], locale="zh-Hant") -> dict[str, Any]:
    """Publish a real pending target before asking for one normal-answer link."""
    target = directory / f"{key}.md"
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(_pending(key, locale))
    digest = getattr(__loader__, "runtime_digest", None)
    runner = (
        directory.parent / "runtimes" / (digest + ".pyz")
        if digest else Path(__file__).resolve().parents[2] / "scripts/hook.py"
    )
    command = shlex.join([
        "python3", "-I", str(runner), "--preview", payload["session_id"], payload["turn_id"],
        "--data-dir", str(directory.parent.absolute()),
    ])
    return {"hookSpecificOutput": {
        "hookEventName": payload.get("hook_event_name", "UserPromptSubmit"),
        "additionalContext": (
            "Before final, run once: " + command
            + " --output-dir <Task visualization root from writable roots; else cwd/work>. "
            "Append its visualize reference unchanged on a final-answer line. "
            "Do not read/analyze the card or load skills for it; no retries. "
            "Skip if disabled or the answer format conflicts."
        ),
    }}


def _directory(root: Path) -> Path:
    if root.is_symlink():
        raise ValueError("report root is a symlink")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = root / "auto-reports"
    if target.is_symlink():
        raise ValueError("report directory is a symlink")
    target.mkdir(exist_ok=True, mode=0o700)
    return target


def settings(root: Path) -> dict[str, Any]:
    path = root / "auto-report.json"
    if not path.exists() and not path.is_symlink():
        return {"enabled": True, "threshold_seconds": 0}
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("nonregular auto-report settings")
        raw = stream.read(2049)
    value = json.loads(raw) if len(raw) <= 2048 else None
    if (
        not isinstance(value, dict)
        or type(value.get("enabled")) is not bool
        or type(value.get("threshold_seconds")) not in (int, float)
        or not 0 <= value["threshold_seconds"] <= 86400
    ):
        raise ValueError("invalid auto-report settings")
    return {"enabled": value["enabled"], "threshold_seconds": value["threshold_seconds"]}


def configure(root: Path, *, enabled: bool, threshold_seconds: float = 0) -> dict[str, Any]:
    if (
        type(enabled) is not bool
        or type(threshold_seconds) not in (int, float)
        or not 0 <= threshold_seconds <= 86400
    ):
        raise ValueError("invalid report configuration")
    _directory(root)
    target = root / "auto-report.json"
    if target.is_symlink():
        raise ValueError("settings target is a symlink")
    descriptor, temporary = tempfile.mkstemp(prefix="auto-report-", dir=root)
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump({"enabled": enabled, "threshold_seconds": threshold_seconds}, output)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return settings(root)


def _connect(root: Path) -> sqlite3.Connection:
    directory = _directory(root)
    path = directory / "timing.sqlite3"
    if path.is_symlink():
        raise ValueError("timing store is a symlink")
    if not path.exists():
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass  # Another Task may initialize the shared store simultaneously.
        else:
            os.close(descriptor)
    if path.is_symlink() or not path.is_file():
        raise ValueError("timing store is not a regular file")
    connection = sqlite3.connect(path, timeout=0.4, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS turns ("
            "key TEXT PRIMARY KEY, session_hash TEXT NOT NULL, turn_hash TEXT NOT NULL, "
            "started REAL NOT NULL, monotonic REAL NOT NULL, baseline TEXT NOT NULL, "
            "state TEXT NOT NULL, elapsed REAL, report TEXT)"
        )
    except Exception:
        connection.close()
        raise
    return connection


def _model(value: Any) -> str | None:
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[a-z0-9][-a-z0-9.]{0,79}", value)
        else None
    )


def _request_thread_counter(payload: dict, task: str, turn: str) -> dict | None:
    """Use the native cumulative counter, never add request and event totals.

    A request record can precede the post-tool token_count event. Its own
    usage is not a thread total; require explicit, consistent native scopes.
    """
    if (payload.get("thread_id") != task or payload.get("turn_id") != turn
            or payload.get("session_id", task) != task
            or payload.get("root_turn_id", turn) != turn):
        return None
    response = payload.get("response_id")
    if not isinstance(response, str) or not 0 < len(response) <= 512:
        return None
    values = [request_usage(payload.get(key)) for key in
              ("usage", "turn_token_usage", "thread_token_usage")]
    if any(value is None or any(n > 2**63 - 1 for n in value.values()) for value in values):
        return None
    if any(any(left[key] > right[key] for key in left)
           for left, right in zip(values, values[1:])):
        return None
    return {key: values[-1][key + "_tokens"] for key in
            ("total", "input", "cached_input", "output", "reasoning_output")}


def _setting_values(payload: dict) -> dict:
    """Allowlisted point-in-time settings; contradictory aliases stay unknown."""
    collaboration = payload.get("collaboration_mode")
    nested = collaboration.get("settings") if isinstance(collaboration, dict) else None
    reasoning = payload.get("reasoning")
    candidates = {"model": [], "reasoning_effort": []}
    for source in (payload, nested if isinstance(nested, dict) else {}):
        if "model" in source:
            candidates["model"].append(_model(source["model"]))
        for field in ("effort", "reasoning_effort"):
            if field in source:
                candidates["reasoning_effort"].append(source[field])
    if isinstance(reasoning, dict) and "effort" in reasoning:
        candidates["reasoning_effort"].append(reasoning["effort"])
    values = {}
    for field, items in candidates.items():
        if items:
            value = items[0] if all(item == items[0] for item in items) else None
            if field == "reasoning_effort" and value not in (
                "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"
            ):
                value = None
            values[field] = value
    return values


def _turn_contexts(records: list, task: str, turn: str, *, limited=False) -> tuple[list, bool]:
    """A bounded, chronological settings lane, isolated from previous turns.

    Settings without a turn ID may refine only an active matching turn (or
    seed the immediately following start). No Task/global preference fallback.
    """
    active = None
    pending = None
    current = {"model": None, "reasoning_effort": None, "fast_mode": None}
    contexts = []
    stamp = None

    def timestamp(record):
        try:
            value = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")).timestamp()
            return value if math.isfinite(value) else None
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
            return None

    def apply(values):
        nonlocal limited
        if not values:
            return
        if any(value is None for value in values.values()):
            limited = True
        current.update(values)
        if not contexts or current != contexts[-1]:
            contexts.append(dict(current))
            if len(contexts) > 16:
                contexts.pop(0)
                limited = True

    for record in records:
        kind, payload = record["type"], record["payload"]
        event = payload.get("type")
        is_context = kind == "turn_context" or kind == "event_msg" and event == "turn_context"
        is_start = kind == "event_msg" and event == "task_started"
        if payload.get("thread_id", task) != task:
            if is_start or is_context:
                active = None
                current = {"model": None, "reasoning_effort": None, "fast_mode": None}
            continue
        if is_start or is_context:
            next_turn = payload.get("turn_id")
            now = timestamp(record)
            if (is_context and active == turn and stamp is not None
                    and now is not None and now < stamp):
                limited = True
                continue
            if is_start or next_turn != active:
                current = {"model": None, "reasoning_effort": None, "fast_mode": None}
                stamp = None
            active = next_turn
            if now is not None:
                stamp = now
            if active == turn:
                if is_start and pending is not None:
                    pending_values, pending_stamp = pending
                    if now is not None and pending_stamp is not None and pending_stamp > now:
                        limited = True
                    else:
                        apply(pending_values)
                if is_context:
                    apply(_setting_values(payload))
            pending = None
            continue
        is_settings = kind == "thread_settings_applied" or (
            kind == "event_msg" and event == "thread_settings_applied"
        )
        is_update = kind == "response_item" and event == "configuration_update"
        is_request = kind == "token_usage_record"
        if not (is_settings or is_update or is_request):
            if kind == "event_msg" and event in ("task_complete", "turn_aborted"):
                active = None
                pending = None
            continue
        values = {}
        if is_settings and isinstance(payload.get("thread_settings"), dict):
            values.update(_setting_values(payload["thread_settings"]))
        top = _setting_values({"reasoning": payload.get("reasoning")}
                              if is_update else payload)
        for field, value in top.items():
            values[field] = None if field in values and values[field] != value else value
        if is_settings and active is None and payload.get("turn_id") is None:
            pending = (values, timestamp(record))
            continue
        if active != turn or payload.get("turn_id", active) != turn:
            continue
        if payload.get("root_turn_id", turn) != turn:
            continue
        now = timestamp(record)
        if now is not None and stamp is not None and now < stamp:
            limited = True
            continue
        if now is not None:
            stamp = now
        apply(values)
    return contexts, limited


def _first_turn_proof(records, turn: str) -> tuple[bool, bool]:
    """Prove a full, original first-turn prefix; absence alone is never zero."""
    if not records or records[0].get("type") != "session_meta":
        return False, False
    metadata = records[0]["payload"]
    if metadata.get("history_base") or any(value for key, value in metadata.items()
           if key.startswith("fork") or key == "parent_thread_id"):
        return False, False
    began = model_seen = usage_seen = False
    previous = None
    for record in records[1:]:
        kind, payload = record["type"], record["payload"]
        event = payload.get("type")
        if kind not in ("event_msg", "response_item", "turn_context", "world_state",
                        "token_usage_record"):
            return False, False
        if kind == "session_meta" or payload.get("turn_id") not in (None, turn):
            return False, False
        if payload.get("thread_id") not in (None, metadata.get("id")):
            return False, False
        if kind == "event_msg" and event == "task_started":
            if began or payload.get("turn_id") != turn:
                return False, False
            began = True
        elif not began:
            # Copied conversation, a prior turn, or an incomplete prefix.
            return False, False
        if kind == "response_item":
            model_seen |= event != "message" or payload.get("role") not in (
                "user", "developer", "system"
            )
        values = None
        if kind == "event_msg":
            model_seen |= event in ("agent_message", "agent_reasoning")
            if event == "token_count":
                usage_seen = True
                usage = _usage_from_line(record)
                if usage is None:
                    return False, False
                values = asdict(usage)
        if kind == "token_usage_record":
            model_seen = True
            values = _request_thread_counter(payload, metadata.get("id"), turn)
            # Older clients may only have per-request usage. They establish
            # model activity, but cannot supply a cumulative counter.
            if "thread_token_usage" in payload and values is None:
                return False, False
        if values is not None:
            usage_seen = True
            if previous and any(values[key] < previous[key] for key in values):
                return False, False
            previous = values
    return began, began and not model_seen and not usage_seen


def _completion_prefix(lines: list[tuple[bytes, int]], task: str, turn: str) -> tuple:
    """Find a witnessed terminal boundary without reading any later turn as evidence.

    A completion marker alone cannot identify the preceding unscoped counters.
    Require a matching start/context in this bounded prefix, and reject broken
    ordering or explicit foreign identities. File quietness is never evidence.
    """
    active = None
    target_seen = False

    def unavailable(status, **extra):
        return None, {"completion_observed": False, "completion_status": status, **extra}

    for index, (line, line_end) in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            return unavailable("completion_structure_invalid", invalid_records=True)
        if (not isinstance(record, dict) or not isinstance(record.get("type"), str)
                or not isinstance(record.get("payload"), dict)):
            return unavailable("completion_structure_invalid", invalid_records=True)
        kind, payload = record["type"], record["payload"]
        event = payload.get("type")
        if kind == "session_meta":
            if index != 0 or payload.get("id") != task:
                return unavailable("completion_identity_ambiguous")
            continue
        if (payload.get("thread_id", task) != task
                or payload.get("session_id", task) != task):
            return unavailable("completion_identity_ambiguous")
        is_context = kind == "turn_context" or kind == "event_msg" and event == "turn_context"
        is_start = kind == "event_msg" and event == "task_started"
        if is_context or is_start:
            next_turn = payload.get("turn_id")
            if not isinstance(next_turn, str) or not next_turn:
                return unavailable("completion_order_ambiguous")
            if target_seen and (next_turn != turn or is_start):
                return unavailable("completion_order_ambiguous")
            active = next_turn
            target_seen |= active == turn
            continue
        if kind == "event_msg" and event in ("task_complete", "turn_aborted"):
            event_turn = payload.get("turn_id")
            if event_turn == turn:
                if event == "turn_aborted":
                    return unavailable("completion_aborted")
                if not target_seen or active != turn:
                    return unavailable("completion_start_unobserved")
                if payload.get("root_turn_id", turn) != turn:
                    return unavailable("completion_identity_ambiguous")
                evidence = {"completion_observed": True, "completion_status": "observed",
                            "completion_end": line_end}
                stamp = record.get("timestamp")
                if isinstance(stamp, str) and 0 < len(stamp) <= 128:
                    try:
                        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                        if parsed.tzinfo is not None and math.isfinite(parsed.timestamp()):
                            evidence["completed_at"] = stamp
                    except (ValueError, OverflowError):
                        pass
                return lines[:index + 1], evidence
            if active == turn:
                return unavailable("completion_order_ambiguous")
            if event_turn in (None, active):
                active = None
            continue
        if active == turn and (payload.get("turn_id", turn) != turn
                               or payload.get("root_turn_id", turn) != turn):
            return unavailable("completion_identity_ambiguous")
    return unavailable("completion_not_observed")


def snapshot(path_value: Any, turn_id: str, *, at_turn_start: bool = False,
             completed_only: bool = False) -> dict[str, Any]:
    """Only header identity and a bounded tail are read; no text is persisted."""
    unavailable: dict[str, Any] = {"status": "unavailable", "usage": None, "contexts": []}
    if completed_only:
        unavailable.update(completion_observed=False, completion_status="unavailable")
        if at_turn_start:
            return {**unavailable, "completion_status": "conflicting_snapshot_modes"}
    result = dict(unavailable)
    if not isinstance(path_value, str) or not path_value:
        return result
    path = Path(path_value)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return result
            header = stream.readline(128 * 1024)
            metadata = json.loads(header)
            if metadata.get("type") != "session_meta":
                return result
            payload = metadata.get("payload") or {}
            identity = payload.get("id")
            if not isinstance(identity, str) or not 0 < len(identity) <= 256:
                return result
            source = payload.get("source")
            if isinstance(source, dict) and source.get("subagent") is not None:
                return {**result, "status": "subagent"}
            offset = max(0, info.st_size - SCAN_BYTES)
            stream.seek(offset)
            raw = stream.read(info.st_size - offset)
            current = os.fstat(stream.fileno())
            if len(raw) != info.st_size - offset or current.st_size < info.st_size:
                return result
        result.update(
            status="observed",
            task_hash=stable_hash(identity),
            source_hash=stable_hash(str(path.absolute())),
            device=info.st_dev,
            inode=info.st_ino,
            size=info.st_size,
            scan_bytes=len(header) + len(raw),
            tail_limited=bool(offset),
        )
        lines = []
        position = offset
        for line in raw.split(b"\n"):
            end = position + len(line)
            lines.append((line, end))
            position = end + 1
        # Incomplete first/last records never establish a counter value.
        if offset:
            lines = lines[1:]
        if completed_only:
            prefix, evidence = _completion_prefix(lines, identity, turn_id)
            result.update(evidence)
            if prefix is None:
                return {**result, "status": "unavailable", "usage": None, "contexts": []}
            lines = prefix
            result["size"] = result["completion_end"]
        if lines[-1][0]:
            try:
                json.loads(lines[-1][0])
            except (ValueError, RecursionError):
                lines.pop()
                result["invalid_records"] = True
        usage_seen = False
        turn_usage_seen = False
        later_turn_usage = None
        turn_counter_invalid = False
        later_counter = None
        later_counter_end = None
        records = []
        seeking_start = at_turn_start
        for line, line_end in reversed(lines):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except (ValueError, RecursionError):
                result["invalid_records"] = True
                continue
            if not isinstance(record, dict):
                result["invalid_records"] = True
                continue
            payload = record.get("payload")
            if not isinstance(record.get("type"), str) or not isinstance(payload, dict):
                result["invalid_records"] = True
                continue
            if seeking_start:
                # Reconstruct the baseline, never substitute a mid-turn counter.
                # A stale event must not recover a completed or different turn.
                kind, event = record["type"], payload.get("type")
                if (kind == "event_msg" and event in ("task_complete", "turn_aborted")
                        or kind == "turn_context" and payload.get("turn_id") != turn_id):
                    return {"status": "unavailable", "usage": None, "contexts": []}
                if kind != "event_msg" or event != "task_started":
                    continue
                if (payload.get("turn_id") != turn_id
                        or payload.get("thread_id", identity) != identity
                        or result.get("invalid_records")):
                    return {"status": "unavailable", "usage": None, "contexts": []}
                stamp = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
                if stamp.tzinfo is None or not math.isfinite(stamp.timestamp()):
                    return {"status": "unavailable", "usage": None, "contexts": []}
                result["native_started_at"] = stamp.timestamp()
                result["size"] = line_end
                seeking_start = False
            records.append(record)
            counter = None
            if record.get("type") == "event_msg" and payload.get("type") == "token_count":
                observed = _usage_from_line(record)
                counter = asdict(observed) if observed else None
                if not usage_seen:
                    usage_seen = True
                    result["usage"] = counter
                    result["usage_end"] = line_end
            if (record.get("type") == "token_usage_record" and payload.get("turn_id") == turn_id
                    and "thread_token_usage" in payload):
                cumulative = _request_thread_counter(payload, identity, turn_id)
                counter = cumulative
                if not usage_seen:
                    usage_seen = True
                    result["usage"] = cumulative
                    result["usage_end"] = line_end
                if not turn_usage_seen:
                    turn_usage_seen = True
                    if cumulative and cumulative == result.get("usage"):
                        result["turn_usage"] = {
                            key: payload["turn_token_usage"][key + "_tokens"] for key in cumulative
                        }
                if cumulative:
                    native_turn = {key: payload["turn_token_usage"][key + "_tokens"]
                                   for key in cumulative}
                    if later_turn_usage and any(native_turn[key] > later_turn_usage[key]
                                                for key in native_turn):
                        turn_counter_invalid = True
                    later_turn_usage = native_turn
                else:
                    turn_counter_invalid = True
            if counter:
                if later_counter and any(counter[key] > later_counter[key] for key in counter):
                    result.setdefault("counter_reset_end", later_counter_end)
                later_counter, later_counter_end = counter, line_end
        if seeking_start:
            return {"status": "unavailable", "usage": None, "contexts": []}
        if turn_counter_invalid or result.get("invalid_records"):
            result.pop("turn_usage", None)
        result["contexts"], result["contexts_limited"] = _turn_contexts(
            list(reversed(records)), identity, turn_id,
            limited=bool(offset or result.get("invalid_records")),
        )
        result["requested_turn_hash"] = stable_hash(turn_id)
        first, fresh = (False, False)
        if not offset and not result.get("invalid_records"):
            first, fresh = _first_turn_proof(list(reversed(records)), turn_id)
        result.update(first_turn_only=first, fresh_turn_start=fresh)
        return result
    except (OSError, ValueError, TypeError, AttributeError, RecursionError,
            KeyError, OverflowError):
        return unavailable


def _delta(before: dict[str, Any], after: dict[str, Any]) -> tuple[dict[str, int] | None, str]:
    if before.get("status") != "observed" or after.get("status") != "observed":
        return None, "snapshot_unavailable"
    if any(before.get(k) != after.get(k) for k in ("task_hash", "source_hash", "device", "inode")):
        return None, "source_changed"
    if after["size"] < before["size"]:
        return None, "source_truncated"
    if after.get("invalid_records"):
        return None, "snapshot_incomplete"
    if after.get("counter_reset_end", 0) > before["size"]:
        return None, "counter_reset_or_inconsistent"
    first, last = before.get("usage"), after.get("usage")
    if first and last and any(last[key] < first[key] for key in first):
        return None, "counter_reset_or_inconsistent"
    if (after.get("turn_usage") is not None
            and before.get("requested_turn_hash") == after.get("requested_turn_hash")):
        return after["turn_usage"], "native_turn_counter"
    if before.get("invalid_records"):
        return None, "snapshot_incomplete"
    if (first and first == last
            and after.get("usage_end", after["size"] + 1) <= before["size"]):
        # Re-reading the previous turn's last counter is not observed zero work.
        return None, "turn_usage_pending"
    verified_first = (
        first is None and before.get("fresh_turn_start") is True
        and after.get("first_turn_only") is True
        and before.get("requested_turn_hash") == after.get("requested_turn_hash")
    )
    if verified_first and last is not None:
        first = {key: 0 for key in last}
    if not first or not last:
        return None, "counter_unavailable"
    delta = {k: last[k] - first[k] for k in first}
    if (
        any(v < 0 for v in delta.values())
        or delta["total"] != delta["input"] + delta["output"]
        or delta["cached_input"] > delta["input"]
        or delta["reasoning_output"] > delta["output"]
    ):
        return None, "counter_reset_or_inconsistent"
    return delta, "verified_first_turn_counter" if verified_first else "boundary_counter_difference"


def observed_total(parent: dict | None, children: dict) -> tuple[dict | None, bool]:
    """Known subtotal only; missing or partial sources never become complete zeroes."""
    child = children.get("usage") if children.get("status") != "none" else None
    available = [value for value in (parent, child) if value is not None]
    total = (
        {key: sum(value[key] for value in available) for key in available[0]} if available else None
    )
    if parent is None and total is not None and total["total"] == 0:
        total = None
    complete = (
        parent is not None and children.get("status") in ("observed", "none")
        and not children.get("pending_agents") and not children.get("missing_agents")
        and not children.get("selection_limited")
    )
    return total, complete


def child_coverage(children: dict, locale="zh-Hant") -> str:
    text = ReportText(locale)
    if children.get("status") == "none":
        return text("no_children")
    if children.get("status") == "unavailable":
        return text("child_unavailable")
    return (
        text("child_counts", seen=text.number(children.get('agents_with_usage', 0)),
             total=text.number(children.get('agents_seen', 0)))
        + " · " + text("partial" if children.get("status") != "observed" else "observed")
    )


def _documents(receipt: dict[str, Any]) -> tuple[str, str]:
    from .quota_view import render_html, render_markdown

    text = ReportText(receipt.get("locale", "zh-Hant"))
    children = receipt.get("subagents") or {"status": "unavailable"}
    usage, complete = observed_total(receipt["usage"], children)

    def number(key: str) -> str:
        return text.number(usage[key] if usage is not None else None)

    rows = [
        (text("report_revision"), str(receipt.get("revision", 1))),
        (text("reconcile_heading"), text("reconcile_" + receipt.get(
            "reconciliation_status", "pending"))),
        (text("report_updated"), receipt.get("reconciled_at", receipt["stopped_at"])),
        (text("elapsed_wait"), text.duration(receipt['elapsed_seconds'], minutes=False)),
        (text("task_total"), text.number((receipt.get("task_usage") or {}).get("total"))),
        (text("total" if complete else "subtotal"), number("total")),
        (text("turn_delta"),
         text.number(receipt['usage']['total'] if receipt["usage"] else None)),
        (text("children"), text("na") if children.get("status") == "none" else
         text.number(children['usage']['total'] if children.get("usage") else None)),
        (text("coverage"), child_coverage(children, text.locale)),
        (text("input"), number("input")),
        (text("cached"), number("cached_input")),
        (text("output"), number("output")),
        (text("reasoning"), number("reasoning_output")),
    ]
    notes = [text("note_" + key) for key in (
        "stop", "usage", "counter", "subsets", "scope", "children", "render"
    )]
    if receipt.get("completion_observed"):
        notes[0] = text("note_completion")
    notes.append(text("note_reconcile"))
    contexts = receipt["stop_contexts"]
    context_lines = []
    for context in contexts:
        context_lines.append(
            text("context_pair", model=context['model'] or text("unknown_model"),
                 effort=context['reasoning_effort'] or text("unknown"))
        )
    if not context_lines:
        context_lines = [text("context_missing")]
    context_lines.append(text("context_partial" if receipt.get("contexts_limited")
                              else "context_note"))
    title = text("title")
    task = receipt.get("task") or {}
    label = task.get("display_name") or f"{text('unnamed')} · {receipt['task_hash'][:12]}"
    identity = label + " · " + text("turn", value=receipt['turn_hash'][:12])
    markdown_identity = escape(identity, quote=False)
    for character, replacement in (
        ("[", "&#91;"),
        ("]", "&#93;"),
        ("*", "&#42;"),
        ("_", "&#95;"),
        ("`", "&#96;"),
    ):
        markdown_identity = markdown_identity.replace(character, replacement)
    period = f"{receipt['started_at']} → {receipt['stopped_at']}"
    markdown = "\n".join(
        [
            f"# {title}",
            "",
            markdown_identity,
            "",
            period,
            "",
            f"| {text('column_item')} | {text('column_observation')} |",
            "| --- | --- |",
            *(f"| {k} | {v} |" for k, v in rows),
            "",
            "## " + text("context_heading"),
            "",
            *context_lines,
            "",
            text("status", value=receipt['usage_status']),
            "",
            render_markdown(receipt.get("quota"), text),
            "",
            "## " + text("limits"),
            "",
            *(f"- {line}" for line in notes),
            "",
        ]
    )
    body = "".join(f"<tr><th>{escape(k)}</th><td>{escape(v)}</td></tr>" for k, v in rows)
    html = (
        f'<!doctype html><html lang="{text.locale}"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
        f"<title>{title}</title><style>:root{{color-scheme:light dark}}"
        "body{font:15px/1.7 system-ui;max-width:850px;margin:auto;padding:28px;"
        "color:light-dark(#19332f,#e1ebe7);background:light-dark(#fcfcfa,#141918)}"
        "h1{font-size:30px}h2{font-size:18px;margin-top:30px}p{overflow-wrap:anywhere}"
        "table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px 0;"
        "border-bottom:1px solid light-dark(#d3ded8,#394640)}td{text-align:right;"
        "font-variant-numeric:tabular-nums}li{margin:8px 0}</style><main>"
        f"<h1>{title}</h1><p>{escape(identity)}</p><p>{escape(period)}</p><table>{body}</table>"
        f"<h2>{escape(text('context_heading'))}</h2>"
        + "".join(f"<p>{escape(line)}</p>" for line in context_lines)
        + f"<p>{escape(text('status', value=receipt['usage_status']))}</p>"
        + render_html(receipt.get("quota"), text)
        + f"<h2>{escape(text('limits'))}</h2><ul>"
        + "".join(f"<li>{escape(line)}</li>" for line in notes)
        + "</ul></main></html>"
    )
    return markdown, html


def _publish(root: Path, key: str, receipt: dict[str, Any]) -> str:
    directory = _directory(root)
    markdown, html = _documents(receipt)
    # Completed receipts remain no-clobber. Only our exact pending Markdown may
    # be atomically replaced, after both other artifacts have been written.
    for extension, content in (
        ("html", html),
        ("json", json.dumps(receipt, ensure_ascii=True, indent=2)),
    ):
        target = directory / f"{key}.{extension}"
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(content)
    target = directory / f"{key}.md"
    if target.exists() or target.is_symlink():
        expected = _pending(key, receipt.get("pending_locale", "zh-Hant")).encode()
        descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            if (
                not stat.S_ISREG(os.fstat(source.fileno()).st_mode)
                or source.read(len(expected) + 1) != expected
            ):
                raise ValueError("report is not our pending target")
        descriptor, temporary = tempfile.mkstemp(prefix="receipt-", dir=directory)
        try:
            with os.fdopen(descriptor, "w") as output:
                output.write(markdown)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    else:
        # Compatibility with starts recorded by older pinned runtimes.
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(markdown)
    return str(target.absolute())


def handle(
    payload: dict[str, Any],
    root: Path,
    *,
    wall: float | None = None,
    monotonic: float | None = None,
    home: Path | None = None,
) -> dict[str, Any] | None:
    """One footer per turn; recover missing starts at supported tool boundaries."""
    event = payload.get("hook_event_name")
    if event not in EVENTS:
        return None
    try:
        if not settings(root)["enabled"]:
            return None
        if event == "SubagentStop":
            from .child_usage import capture
            capture(payload, root, home=home, now=wall)
            return None
        if payload.get("agent_id"):
            return None
        session, turn = payload.get("session_id"), payload.get("turn_id")
        if not isinstance(session, str) or not session or len(session) > 256:
            return None
        if event != "SessionEnd" and (not isinstance(turn, str) or not turn or len(turn) > 256):
            return None
        now = time.time() if wall is None else wall
        ticks = time.monotonic() if monotonic is None else monotonic
        key = stable_hash([session, turn])
        connection = None
        try:
            observed = None
            started, start_ticks = now, ticks
            if event in START_EVENTS:
                # The common path does no transcript or locale work. Recheck
                # under the write lock to deduplicate concurrent tool events.
                if (root / "auto-reports/timing.sqlite3").exists():
                    connection = _connect(root)
                    if connection.execute("SELECT 1 FROM turns WHERE key=?", (key,)).fetchone():
                        return None
                recovery = event != "UserPromptSubmit"
                observed = snapshot(payload.get("transcript_path"), turn, at_turn_start=recovery)
                if observed["status"] == "subagent":
                    return None
                if recovery:
                    native_start = observed.get("native_started_at")
                    if (observed.get("task_hash") != stable_hash(session)
                            or observed.get("invalid_records")
                            or native_start is None or not 0 <= now - native_start <= ticks):
                        return None
                    started = native_start
                    start_ticks = ticks - (now - started)
                    observed["recovered_at"] = now
                if observed.get("task_hash") != stable_hash(session):
                    # The hook selects the Task. A foreign transcript cannot
                    # seed counters, contexts, or identity in its private state.
                    observed = {"status": "source_unavailable", "usage": None, "contexts": []}
                observed["start_event"] = event
                # A later tool's model is not evidence of the original start model.
                observed["hook_model"] = None if recovery else _model(payload.get("model"))
                observed["report_locale"] = resolve_locale(home=home)["locale"]
            if connection is None:
                connection = _connect(root)
            connection.execute("BEGIN IMMEDIATE")
            if event == "SessionEnd":
                connection.execute(
                    "UPDATE turns SET state='session_ended' "
                    "WHERE session_hash=? AND state IN ('started','short')",
                    (stable_hash(session),),
                )
                connection.commit()
                return None
            row = connection.execute("SELECT * FROM turns WHERE key=?", (key,)).fetchone()
            if event in START_EVENTS:
                footer = None
                if row is None:
                    if connection.execute("SELECT count(*) FROM turns").fetchone()[0] >= MAX_ROWS:
                        raise ValueError("auto-report timing index is full")
                    connection.execute(
                        "INSERT INTO turns VALUES (?,?,?,?,?,?,'started',NULL,NULL)",
                        (
                            key,
                            stable_hash(session),
                            stable_hash(turn),
                            started,
                            start_ticks,
                            json.dumps(observed, separators=(",", ":")),
                        ),
                    )
                    footer = _footer(_directory(root), key, payload, observed["report_locale"])
                connection.commit()
                return footer
            if row is None or row["state"] not in ("started", "short"):
                return None
            if event == "Interrupt":
                connection.execute("UPDATE turns SET state='interrupted' WHERE key=?", (key,))
                connection.commit()
                return None
            elapsed = ticks - row["monotonic"]
            if (
                not math.isfinite(elapsed)
                or elapsed < 0
                or abs((now - row["started"]) - elapsed) > 10
            ):
                state = "clock_discontinuity"
            elif (
                settings(root)["threshold_seconds"] > 0
                and elapsed <= settings(root)["threshold_seconds"]
            ):
                state = "short"
            else:
                state = "generating"
            connection.execute(
                "UPDATE turns SET state=?,elapsed=? WHERE key=?", (state, elapsed, key)
            )
            # Claim before file generation: a crash or timeout cannot start a retry loop.
            connection.commit()
            if state != "generating":
                return None
            started = json.loads(row["baseline"])
            stopped = snapshot(payload.get("transcript_path"), turn)
            source_verified = (started.get("task_hash") == stable_hash(session)
                               and stopped.get("task_hash") == stable_hash(session))
            usage, status = _delta(started, stopped)
            if not source_verified:
                usage, status = None, "source_identity_unavailable"
            locale = resolve_locale(home=home)
            text = ReportText(locale["locale"])
            from .child_usage import collect
            from .turn_quota import saved
            children = collect(root, session, row["started"], now, home=home,
                               unnamed_label=text("unnamed"))
            receipt = {
                "schema_version": 2,
                "scope": "user_turn_stop_boundary",
                "task_hash": stable_hash(session),
                "source_identity_verified": source_verified,
                "task": task_description(session, home=home, unnamed_label=text("unnamed"))
                if source_verified else None,
                "turn_hash": row["turn_hash"],
                "elapsed_seconds": round(elapsed, 3),
                "started_at": datetime.fromtimestamp(row["started"], timezone.utc).isoformat(),
                "start_event": started.get("start_event", "UserPromptSubmit"),
                "start_recovered": "recovered_at" in started,
                "stopped_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                "usage": usage,
                "task_usage": stopped.get("usage") if source_verified else None,
                "usage_status": status,
                "start_model": started.get("hook_model") if source_verified else None,
                "stop_model": _model(payload.get("model")) if source_verified else None,
                "stop_contexts": stopped["contexts"] if source_verified else [],
                "contexts_limited": stopped.get("contexts_limited", False) or not source_verified,
                "snapshot_scan_bytes": started.get("scan_bytes", 0) + stopped.get("scan_bytes", 0),
                "stop_tail_limited": stopped.get("tail_limited"),
                "stop_hook_active": payload.get("stop_hook_active") is True,
                "model_requests_for_report": 0,
                "native_quota_refreshed": False,
                "quota": saved(root, key),
                "subagents_included": children.get("agents_with_usage", 0) > 0,
                "subagents": children,
                "final_usage_may_not_yet_be_persisted": True,
                **locale,
                "pending_locale": started.get("report_locale", "zh-Hant"),
            }
            try:
                report = _publish(root, key, receipt)
            except Exception:
                connection.execute("UPDATE turns SET state='failed' WHERE key=?", (key,))
                raise
            connection.execute(
                "UPDATE turns SET state='reported',report=? WHERE key=?", (key + ".md", key)
            )
            return {
                "systemMessage": text.duration(elapsed) + " · "
                + f"[{text('report_link')}](<{report}>) ({text('stop_note')})"
            }
        finally:
            if connection is not None:
                connection.close()
    except Exception:
        # A failed receipt never blocks work or asks the model to continue.
        try:
            message = ReportText(resolve_locale(home=home)["locale"])("failed")
        except Exception:
            message = (
                "Automatic usage report unavailable; Task work can continue."
            )
        return {"systemMessage": message}


def recent(root: Path, limit: int = 20) -> list[dict[str, Any]]:
    path = root / "auto-reports/timing.sqlite3"
    if not path.exists():
        return []
    if path.is_symlink():
        raise ValueError("timing store is a symlink")
    from urllib.parse import quote

    connection = sqlite3.connect("file:" + quote(str(path.absolute())) + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT key,turn_hash,started,state,elapsed,report FROM turns "
                "ORDER BY started DESC LIMIT ?",
                (limit,),
            )
        ]
    finally:
        connection.close()
