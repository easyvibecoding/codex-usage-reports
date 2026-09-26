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
from .task_catalog import MAX_DEPTH, TaskCatalog, _uuid, task_description
from .transcript import _usage_from_line, cache_read_share_percent, request_usage
from .util import stable_hash

SCAN_BYTES = 8 * 1024 * 1024
MAX_ROWS = 10_000
START_EVENTS = {"UserPromptSubmit", "PreToolUse", "PostToolUse"}
EVENTS = START_EVENTS | {"SubagentStart", "Stop", "Interrupt", "SessionEnd", "SubagentStop"}


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
    owner = payload.get("agent_id") or payload["session_id"]
    command = shlex.join([
        "python3", "-I", str(runner), "--preview", owner, payload["turn_id"],
        "--data-dir", str(directory.parent.absolute()),
    ])
    return {"hookSpecificOutput": {
        "hookEventName": payload.get("hook_event_name", "UserPromptSubmit"),
        "additionalContext": (
            "Only if your current Task/agent ID is " + owner
            + ": skip inherited parent preview instructions. Before final, run once: "
            + command
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


def _request_thread_counter(payload: dict, task: str, turn: str,
                            *, root_session: str | None = None) -> dict | None:
    """Use the native cumulative counter, never add request and event totals.

    A request record can precede the post-tool token_count event. Its own
    usage is not a thread total; require explicit, consistent native scopes.
    """
    if (payload.get("thread_id") != task or payload.get("turn_id") != turn
            or payload.get("session_id", task) != (root_session or task)
            or (root_session is None and payload.get("root_turn_id", turn) != turn)
            or (root_session is not None and
                (not isinstance(payload.get("root_turn_id"), str)
                 or not 0 < len(payload["root_turn_id"]) <= 256))):
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


def _turn_contexts(records: list, task: str, turn: str, *, limited=False,
                   root_session: str | None = None) -> tuple[list, bool]:
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
        if ((root_session is None and payload.get("root_turn_id", turn) != turn)
                or (root_session is not None
                    and payload.get("session_id", root_session) != root_session)):
            continue
        now = timestamp(record)
        if now is not None and stamp is not None and now < stamp:
            limited = True
            continue
        if now is not None:
            stamp = now
        apply(values)
    return contexts, limited


def _first_turn_proof(records, turn: str, *, root_session: str | None = None
                      ) -> tuple[bool, bool]:
    """Prove a full, original first-turn prefix; absence alone is never zero."""
    if not records or records[0].get("type") != "session_meta":
        return False, False
    metadata = records[0]["payload"]
    if metadata.get("history_base") or any(value for key, value in metadata.items()
           if key.startswith("fork") or key == "parent_thread_id"):
        return False, False
    began = model_seen = usage_seen = False
    previous = {}
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
            values = _request_thread_counter(payload, metadata.get("id"), turn,
                                             root_session=root_session)
            # Older clients may only have per-request usage. They establish
            # model activity, but cannot supply a cumulative counter.
            if "thread_token_usage" in payload and values is None:
                return False, False
        if values is not None:
            usage_seen = True
            lane = previous.get(kind)
            if lane and any(values[key] < lane[key] for key in values):
                return False, False
            previous[kind] = values
    return began, began and not model_seen and not usage_seen


def _completion_prefix(lines: list[tuple[bytes, int]], task: str, turn: str,
                       *, root_session: str | None = None) -> tuple:
    """Find a witnessed terminal boundary without reading any later turn as evidence.

    A completion marker alone cannot identify the preceding unscoped counters.
    Require a matching start/context in this bounded prefix, and reject broken
    ordering or explicit foreign identities. File quietness is never evidence.
    """
    active = None
    target_seen = False

    def unavailable(status, **extra):
        return None, {"completion_observed": False, "completion_status": status, **extra}

    if root_session is not None:
        # A child rollout can contain copied parent session_meta, starts, and
        # counters before its own turn. They cannot establish child completion.
        owned_start = None
        for index, (line, _) in enumerate(lines):
            try:
                record = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if not isinstance(record, dict) or record.get("type") != "event_msg":
                continue
            payload = record.get("payload")
            if (isinstance(payload, dict) and payload.get("type") == "task_started"
                    and payload.get("turn_id") == turn
                    and payload.get("thread_id", task) == task
                    and payload.get("session_id", root_session) == root_session):
                owned_start = index
                break
        if owned_start is None:
            return unavailable("completion_start_unobserved")
        lines = lines[owned_start:]

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
                or payload.get("session_id", root_session or task) != (root_session or task)):
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
                if root_session is None and payload.get("root_turn_id", turn) != turn:
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
                               or (root_session is None
                                   and payload.get("root_turn_id", turn) != turn)):
            return unavailable("completion_identity_ambiguous")
    return unavailable("completion_not_observed")


def snapshot(path_value: Any, turn_id: str, *, at_turn_start: bool = False,
             completed_only: bool = False, expected_child: str | None = None,
             expected_parent: str | None = None,
             expected_root: str | None = None) -> dict[str, Any]:
    """Only header identity and a bounded tail are read; no text is persisted."""
    unavailable: dict[str, Any] = {"status": "unavailable", "usage": None, "contexts": []}
    if completed_only:
        unavailable.update(completion_observed=False, completion_status="unavailable")
        if at_turn_start:
            return {**unavailable, "completion_status": "conflicting_snapshot_modes"}
    result = dict(unavailable)
    child_mode = expected_child is not None
    if child_mode and not all(_uuid(item) for item in
                              (expected_child, expected_parent, expected_root)):
        return result
    if not child_mode and (expected_parent is not None or expected_root is not None):
        return result
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
            subagent = source.get("subagent") if isinstance(source, dict) else None
            if child_mode:
                spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
                if (identity != expected_child or not isinstance(spawn, dict)
                        or spawn.get("parent_thread_id") != expected_parent):
                    return result
            elif subagent is not None:
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
            source_role="subagent" if child_mode else "parent",
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
            prefix, evidence = _completion_prefix(
                lines, identity, turn_id, root_session=expected_root,
            )
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
        owned_start_end = None
        if child_mode:
            for line, line_end in lines:
                try:
                    record = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if not isinstance(record, dict) or record.get("type") != "event_msg":
                    continue
                event_payload = record.get("payload")
                if (isinstance(event_payload, dict)
                        and event_payload.get("type") == "task_started"
                        and event_payload.get("turn_id") == turn_id
                        and event_payload.get("thread_id", identity) == identity
                        and event_payload.get("session_id", expected_root) == expected_root):
                    owned_start_end = line_end
                    break
        # Native request totals and token_count events can have different
        # historical baselines. Never compare or subtract across these lanes.
        counters = {}
        turn_usage_seen = False
        later_turn_usage = None
        turn_counter_invalid = False
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
            source = None
            if (record.get("type") == "event_msg" and payload.get("type") == "token_count"
                    and (not child_mode or (owned_start_end is not None
                                            and line_end > owned_start_end))):
                observed = _usage_from_line(record)
                counter = asdict(observed) if observed else None
                source = "token_count"
            if (record.get("type") == "token_usage_record" and payload.get("turn_id") == turn_id
                    and "thread_token_usage" in payload):
                cumulative = _request_thread_counter(
                    payload, identity, turn_id, root_session=expected_root,
                )
                counter = cumulative
                source = "native_request"
                if not turn_usage_seen:
                    turn_usage_seen = True
                    if cumulative:
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
            if source:
                lane = counters.setdefault(source, {"usage": counter, "usage_end": line_end})
                later = lane.get("later")
                if counter:
                    if later and any(counter[key] > later[key] for key in counter):
                        lane.setdefault("counter_reset_end", lane["later_end"])
                    lane.update(later=counter, later_end=line_end)
        if seeking_start:
            return {"status": "unavailable", "usage": None, "contexts": []}
        source = "native_request" if "native_request" in counters else "token_count"
        if source in counters:
            lane = counters[source]
            result.update(counter_source=source, usage=lane["usage"], usage_end=lane["usage_end"])
            if "counter_reset_end" in lane:
                result["counter_reset_end"] = lane["counter_reset_end"]
        if turn_counter_invalid:
            result["turn_counter_invalid"] = True
        if turn_counter_invalid or result.get("invalid_records"):
            result.pop("turn_usage", None)
        result["contexts"], result["contexts_limited"] = _turn_contexts(
            list(reversed(records)), identity, turn_id,
            limited=bool(offset or result.get("invalid_records")),
            root_session=expected_root,
        )
        result["requested_turn_hash"] = stable_hash(turn_id)
        first, fresh = (False, False)
        if not offset and not result.get("invalid_records"):
            first, fresh = _first_turn_proof(
                list(reversed(records)), turn_id, root_session=expected_root,
            )
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
    if after.get("turn_counter_invalid"):
        return None, "counter_reset_or_inconsistent"
    first, last = before.get("usage"), after.get("usage")
    same_source = before.get("counter_source") == after.get("counter_source")
    if same_source and first and last and any(last[key] < first[key] for key in first):
        return None, "counter_reset_or_inconsistent"
    if (after.get("turn_usage") is not None
            and before.get("requested_turn_hash") == after.get("requested_turn_hash")):
        # A bounded tail may no longer contain the baseline's native record.
        # Compare turn counters too, even if the thread total still increases.
        prior_turn = before.get("turn_usage")
        if prior_turn and any(after["turn_usage"][key] < prior_turn[key] for key in prior_turn):
            return None, "counter_reset_or_inconsistent"
        return after["turn_usage"], "native_turn_counter"
    if first and last and not same_source:
        return None, "counter_source_changed"
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
    scope = receipt.get("scope", "")
    child_scope = scope == "subagent" or scope.startswith("agent_turn_")
    children_label = text("child_descendant_subtotal" if child_scope else "children")
    context_heading = text("child_context_heading" if child_scope else "context_heading")
    usage, complete = observed_total(receipt["usage"], children)

    def number(key: str) -> str:
        return text.number(usage[key] if usage is not None else None)

    cache_share = cache_read_share_percent(usage)
    cached_display = number("cached_input") + (
        f" ({cache_share:g}%)" if cache_share is not None else ""
    )

    rows = [
        (text("report_revision"), str(receipt.get("revision", 1))),
        (text("reconcile_heading"), text("reconcile_" + receipt.get(
            "reconciliation_status", "pending"))),
        (text("report_updated"), receipt.get("reconciled_at", receipt["stopped_at"])),
        (text("elapsed_wait"), text.duration(receipt['elapsed_seconds'], minutes=False)),
        (text("child_task_total" if child_scope else "task_total"),
         text.number((receipt.get("task_usage") or {}).get("total"))),
        (text("total" if complete else "subtotal"), number("total")),
        (text("child_turn_delta" if child_scope else "turn_delta"),
         text.number(receipt['usage']['total'] if receipt["usage"] else None)),
        (children_label, text("na") if children.get("status") == "none" else
         text.number(children['usage']['total'] if children.get("usage") else None)),
        (text("coverage"), child_coverage(children, text.locale)),
        (text("input"), number("input")),
        (text("cached"), cached_display),
        (text("output"), number("output")),
        (text("reasoning"), number("reasoning_output")),
    ]
    notes = [text("note_" + key) for key in (
        "stop", "usage", "counter", "subsets", "scope", "children", "render"
    )]
    if receipt.get("completion_observed"):
        notes[0] = text("child_note_completion" if child_scope else "note_completion")
    elif child_scope:
        notes[0] = text("child_note_stop")
    if child_scope:
        notes[1] = text("child_note_usage")
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
    if child_scope and task.get("selector"):
        label += " · @" + task["selector"]
        label += " · " + text("owner", name=task.get("parent_name") or
                               text("unknown_parent"))
    identity = label + " · " + text("turn", value=receipt['turn_hash'][:12])

    def markdown_data(value):
        safe = escape(str(value), quote=False)
        for character in "[]*_`\\|":
            safe = safe.replace(character, f"&#{ord(character)};")
        return safe.replace("\n", " ").replace("\r", " ")

    markdown_identity = markdown_data(identity)
    child_rows = [row for row in children.get("rows", [])
                  if isinstance(row, dict) and row.get("selector")]
    child_markdown = (["## " + children_label, ""] + [
        "- @" + markdown_data(row["selector"]) + " · "
        + markdown_data(row.get("display_name") or text("unnamed")) + " · "
        + markdown_data(text("owner", name=row.get("parent_name") or
                             text("unknown_parent"))) + " · "
        + text.number((row.get("usage") or {}).get("total"))
        for row in child_rows
    ] + [""]) if child_rows else []
    child_html = (f"<h2>{escape(children_label)}</h2><ul>" + "".join(
        "<li>@" + escape(str(row["selector"])) + " · "
        + escape(str(row.get("display_name") or text("unnamed"))) + " · "
        + escape(text("owner", name=row.get("parent_name") or text("unknown_parent")))
        + " · " + escape(text.number((row.get("usage") or {}).get("total"))) + "</li>"
        for row in child_rows
    ) + "</ul>") if child_rows else ""
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
            "## " + context_heading,
            "",
            *context_lines,
            "",
            *child_markdown,
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
        f"<h2>{escape(context_heading)}</h2>"
        + "".join(f"<p>{escape(line)}</p>" for line in context_lines)
        + child_html
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


def _baseline_is_subagent(baseline: dict) -> bool:
    """Keep the Start role even if a later hook or catalog calls the child a root."""
    return (baseline.get("source_role") == "subagent"
            or baseline.get("start_event") == "SubagentStart"
            or baseline.get("direct_parent_hash") is not None
            or baseline.get("root_hash") is not None)


def _baseline_identity_matches(
    baseline: dict, task_hash: str, *, root_hash: str | None = None,
    direct_parent_hash: str | None = None,
) -> bool:
    """Require the original Task, role and exact child lineage without filling gaps."""
    was_child = _baseline_is_subagent(baseline)
    if "source_role" in baseline:
        role = baseline["source_role"]
        if role not in ("parent", "subagent") or (role == "parent" and was_child):
            return False
    elif not was_child and baseline.get("start_event") not in START_EVENTS:
        # Version 0.7.0 kept child hashes and Start events but no source_role.
        # Accept that historical evidence; absence of all role evidence is unknown.
        return False
    is_child = root_hash is not None
    return (
        baseline.get("task_hash") == task_hash
        and was_child == is_child
        and (not is_child or (
            direct_parent_hash is not None
            and baseline.get("root_hash") == root_hash
            and baseline.get("direct_parent_hash") == direct_parent_hash
        ))
    )


def _catalog_child_lineage(catalog: TaskCatalog, child: dict,
                           expected_root: str | None = None) -> dict[str, Any] | None:
    """Walk exact native parent links; return raw IDs only to this process."""
    if child["role"] != "subagent" or not child["parent_id"]:
        return None
    direct_parent = child["parent_id"]
    current, seen = child, {child["id"]}
    for _ in range(MAX_DEPTH):
        parent_id = current["parent_id"]
        if parent_id is None or parent_id in seen:
            return None
        parent = catalog.get(parent_id)
        if parent["role"] == "parent":
            if parent["parent_id"] is not None or (
                expected_root is not None and parent_id != expected_root
            ):
                return None
            return {"parent_id": direct_parent, "root_id": parent_id,
                    "task": catalog.describe(child)}
        if parent["role"] != "subagent":
            return None
        current = parent
        seen.add(parent_id)
    return None


def _child_lineage(agent_id: Any, root_id: Any, *, home=None,
                   unnamed_label="未命名任務") -> dict[str, Any] | None:
    """Require one catalog chain from the child to this hook's root session."""
    agent_id, root_id = _uuid(agent_id), _uuid(root_id)
    if agent_id is None or root_id is None or agent_id == root_id:
        return None
    try:
        with TaskCatalog(home, unnamed_label=unnamed_label) as catalog:
            child = catalog.get(agent_id)
            return _catalog_child_lineage(catalog, child, root_id)
    except (OSError, ValueError, sqlite3.Error):
        return None
    return None


def handle(
    payload: dict[str, Any],
    root: Path,
    *,
    wall: float | None = None,
    monotonic: float | None = None,
    home: Path | None = None,
) -> dict[str, Any] | None:
    """One footer per owned turn; recover missing starts at tool boundaries."""
    event = payload.get("hook_event_name")
    if event not in EVENTS:
        return None
    try:
        if not settings(root)["enabled"]:
            return None
        if event == "SubagentStop":
            from .child_usage import capture
            try:
                capture(payload, root, home=home, now=wall)
            except Exception:
                pass  # Independent child receipt may still have usable evidence.
        session, turn = payload.get("session_id"), payload.get("turn_id")
        if not isinstance(session, str) or not session or len(session) > 256:
            return None
        if event != "SessionEnd" and (not isinstance(turn, str) or not turn or len(turn) > 256):
            return None
        agent = payload.get("agent_id")
        if event == "SubagentStart" and not agent:
            return None
        child_mode = bool(agent)
        owner = agent if child_mode else session
        child = _child_lineage(agent, session, home=home) if child_mode else None
        if child_mode and child is None:
            return None
        if event == "SubagentStop" and not child_mode:
            return None
        transcript_path = (payload.get("agent_transcript_path") if event == "SubagentStop"
                           else payload.get("transcript_path"))
        child_snapshot = ({"expected_child": owner,
                           "expected_parent": child["parent_id"],
                           "expected_root": session} if child_mode else {})
        now = time.time() if wall is None else wall
        ticks = time.monotonic() if monotonic is None else monotonic
        key = stable_hash([owner, turn])
        connection = None
        try:
            observed = None
            started, start_ticks = now, ticks
            if event in START_EVENTS or event == "SubagentStart":
                # The common path does no transcript or locale work. Recheck
                # under the write lock to deduplicate concurrent tool events.
                if (root / "auto-reports/timing.sqlite3").exists():
                    connection = _connect(root)
                    if connection.execute("SELECT 1 FROM turns WHERE key=?", (key,)).fetchone():
                        return None
                recovery = event not in ("UserPromptSubmit", "SubagentStart")
                observed = snapshot(transcript_path, turn, at_turn_start=recovery,
                                    **child_snapshot)
                if observed["status"] == "subagent":
                    return None
                if child_mode and observed["status"] != "observed":
                    return None
                if recovery:
                    native_start = observed.get("native_started_at")
                    if (observed.get("task_hash") != stable_hash(owner)
                            or observed.get("invalid_records")
                            or native_start is None or not 0 <= now - native_start <= ticks):
                        return None
                    started = native_start
                    start_ticks = ticks - (now - started)
                    observed["recovered_at"] = now
                if observed.get("task_hash") != stable_hash(owner):
                    # The hook selects the Task. A foreign transcript cannot
                    # seed counters, contexts, or identity in its private state.
                    observed = {"status": "source_unavailable", "usage": None, "contexts": []}
                observed["start_event"] = event
                observed["source_role"] = "subagent" if child_mode else "parent"
                if child_mode:
                    observed["root_hash"] = stable_hash(session)
                    observed["direct_parent_hash"] = stable_hash(child["parent_id"])
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
                    (stable_hash(owner),),
                )
                connection.commit()
                return None
            row = connection.execute("SELECT * FROM turns WHERE key=?", (key,)).fetchone()
            if event in START_EVENTS or event == "SubagentStart":
                footer = None
                if row is None:
                    if connection.execute("SELECT count(*) FROM turns").fetchone()[0] >= MAX_ROWS:
                        raise ValueError("auto-report timing index is full")
                    connection.execute(
                        "INSERT INTO turns VALUES (?,?,?,?,?,?,'started',NULL,NULL)",
                        (
                            key,
                            stable_hash(owner),
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
            baseline_verified = _baseline_identity_matches(
                started, stable_hash(owner),
                root_hash=stable_hash(session) if child_mode else None,
                direct_parent_hash=stable_hash(child["parent_id"]) if child_mode else None,
            )
            stopped = (snapshot(transcript_path, turn, **child_snapshot) if baseline_verified
                       else {"status": "source_unavailable", "usage": None, "contexts": []})
            source_verified = (baseline_verified
                               and stopped.get("task_hash") == stable_hash(owner))
            usage, status = _delta(started, stopped)
            if not source_verified:
                usage, status = None, "source_identity_unavailable"
            locale = resolve_locale(home=home)
            text = ReportText(locale["locale"])
            from .child_usage import collect
            from .turn_quota import saved
            children = (collect(root, owner, row["started"], now, home=home,
                                unnamed_label=text("unnamed")) if source_verified else
                        {"status": "unavailable", "usage": None, "rows": []})
            task = ((child["task"] if child_mode else task_description(
                owner, home=home, unnamed_label=text("unnamed")))
                    if source_verified else None)
            if task is not None:
                # Native agent_path is an internal routing identifier. Keep the
                # derived display name/hashed selector, never the raw path in a receipt.
                task = {key: value for key, value in task.items() if key != "agent_path"}
            receipt = {
                "schema_version": 2,
                "scope": ("agent_turn_stop_boundary" if _baseline_is_subagent(started)
                          else "user_turn_stop_boundary"),
                "task_hash": stable_hash(owner),
                "source_identity_verified": source_verified,
                "task": task,
                **({"root_hash": started.get("root_hash"),
                    "direct_parent_hash": started.get("direct_parent_hash")}
                   if _baseline_is_subagent(started) else {}),
                "turn_hash": row["turn_hash"],
                "elapsed_seconds": round(elapsed, 3),
                "started_at": datetime.fromtimestamp(row["started"], timezone.utc).isoformat(),
                "start_event": started.get("start_event", "UserPromptSubmit"),
                "start_recovered": "recovered_at" in started,
                "stopped_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                "usage": usage,
                "parent_cache_read_share_percent": cache_read_share_percent(usage),
                "task_usage": stopped.get("usage") if source_verified else None,
                "counter_source": stopped.get("counter_source") if source_verified else None,
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
