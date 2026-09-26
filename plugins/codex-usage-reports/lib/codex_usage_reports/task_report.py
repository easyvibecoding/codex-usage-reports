"""One explicitly selected Task: current native counters and recorded turn receipts.

No global transcript search, pricing estimate, or model call. A bounded native
snapshot is a current cumulative observation, never proof of lifetime coverage.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import quote

from .auto_report import SCAN_BYTES, _catalog_child_lineage, child_coverage, snapshot
from .report_i18n import ReportText, resolve_locale
from .task_catalog import TaskCatalog
from .transcript import cache_read_share_percent
from .util import stable_hash

MAX_RECEIPT_BYTES = 256 * 1024


def _latest_turn(path_value: str) -> str | None:
    """Read only a bounded tail; keep the turn selector in memory."""
    descriptor = os.open(path_value, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            return None
        offset = max(0, info.st_size - SCAN_BYTES)
        stream.seek(offset)
        lines = stream.read(SCAN_BYTES).splitlines()
    if offset:
        lines = lines[1:]
    for raw in reversed(lines):
        try:
            record = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        if not isinstance(record, dict):
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if record.get("type") not in ("turn_context", "token_usage_record", "event_msg"):
            continue
        if (record["type"] == "event_msg" and payload.get("type") not in
                ("task_started", "task_complete", "turn_aborted")):
            continue
        turn = payload.get("turn_id")
        if isinstance(turn, str) and 0 < len(turn) <= 256:
            return turn
    return None


def _turns(root: Path, task_hash: str, limit: int, *,
           root_hash: str | None = None,
           direct_parent_hash: str | None = None) -> tuple[list, bool, str]:
    path = root / "auto-reports/timing.sqlite3"
    if not path.exists():
        return [], False, "not_recorded"
    if any(p.is_symlink() for p in (root, path.parent, path)) or not path.is_file():
        return [], False, "unavailable"
    connection = sqlite3.connect(
        "file:" + quote(str(path.absolute())) + "?mode=ro", uri=True, timeout=0.4
    )
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT key,turn_hash,started,state,elapsed,report FROM turns "
            "WHERE session_hash=? ORDER BY started DESC,key DESC LIMIT ?",
            (task_hash, limit + 1),
        ).fetchall()
    finally:
        connection.close()
    turns = []
    for row in rows[:limit]:
        if not all(isinstance(row[key], str) and re.fullmatch(r"[0-9a-f]{64}", row[key])
                   for key in ("key", "turn_hash")):
            continue
        item = {key: row[key] for key in ("turn_hash", "state", "elapsed")}
        item.update(started_at=datetime.fromtimestamp(row["started"], timezone.utc).isoformat(),
                    usage=None, task_usage=None, contexts=[], usage_status="not_recorded")
        if row["state"] == "reported":
            reconciled = row["report"] == row["key"] + ".reconciled.md"
            suffix = ".reconciled.json" if reconciled else ".json"
            receipt_path = path.parent / (row["key"] + suffix)
            try:
                descriptor = os.open(
                    receipt_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                )
                with os.fdopen(descriptor, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ValueError("receipt is not a regular file")
                    raw = stream.read(MAX_RECEIPT_BYTES + 1)
                if len(raw) > MAX_RECEIPT_BYTES:
                    raise ValueError("receipt too large")
                receipt = json.loads(raw)
                if (receipt.get("task_hash") != task_hash
                        or receipt.get("turn_hash") != row["turn_hash"]
                        or (root_hash is not None and (
                            receipt.get("root_hash") != root_hash
                            or receipt.get("direct_parent_hash") != direct_parent_hash))):
                    raise ValueError("receipt identity mismatch")
                item.update(usage=receipt.get("usage"), task_usage=receipt.get("task_usage"),
                            parent_cache_read_share_percent=cache_read_share_percent(receipt.get("usage")),
                            counter_source=receipt.get("counter_source"),
                            contexts=receipt.get("stop_contexts", []),
                            contexts_limited=receipt.get("contexts_limited", False),
                            usage_status=receipt.get("usage_status", "unavailable"),
                            subagents=receipt.get("subagents"),
                            revision=receipt.get("revision", 1),
                            reconciliation_status=receipt.get("reconciliation_status", "pending"),
                            reconciled_at=receipt.get("reconciled_at"))
            except (OSError, ValueError, TypeError, AttributeError, RecursionError):
                item["usage_status"] = "receipt_unavailable"
        turns.append(item)
    return turns, len(rows) > limit, "observed"


def build_task_report(root: Path, selector: str, *, home=None, limit=50) -> dict:
    """Private report for one exact UUID or unambiguous hashed Task selector."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("turn limit must be 1..100")
    locale = resolve_locale(home=home)
    text = ReportText(locale["locale"])
    with TaskCatalog(home, unnamed_label=text("unnamed")) as catalog:
        task = catalog.get(selector)
        lineage = _catalog_child_lineage(catalog, task) if task["role"] == "subagent" else None
        if task["role"] == "subagent" and lineage is None:
            raise ValueError("selected subagent lineage unavailable")
        description = lineage["task"] if lineage else catalog.describe(task)
        row = catalog.connection.execute(
            "SELECT rollout_path FROM threads WHERE id=?", (task["id"],)
        ).fetchone()
        identifier = task["id"]
    current = {"status": "unavailable", "usage": None, "contexts": []}
    turn = None
    if row and isinstance(row[0], str):
        try:
            turn = _latest_turn(row[0])
            expected = ({"expected_child": identifier,
                         "expected_parent": lineage["parent_id"],
                         "expected_root": lineage["root_id"]} if lineage else {})
            current = snapshot(row[0], turn or "", **expected)
        except (OSError, ValueError):
            pass
    if current.get("task_hash") != stable_hash(identifier):
        current = {"status": "source_unavailable", "usage": None, "contexts": []}
    turns, limited, recording = _turns(
        root, stable_hash(identifier), limit,
        root_hash=stable_hash(lineage["root_id"]) if lineage else None,
        direct_parent_hash=stable_hash(lineage["parent_id"]) if lineage else None,
    )
    return {
        "schema_version": 1,
        "scope": "selected_subagent" if lineage else "selected_task",
        "task": description,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "task_usage": current.get("usage"),
        "parent_cache_read_share_percent": cache_read_share_percent(current.get("usage")),
        "counter_source": current.get("counter_source"),
        "usage_status": current["status"],
        "latest_turn_hash": stable_hash(turn) if turn else None,
        "contexts": current.get("contexts", []),
        "contexts_limited": current.get("contexts_limited", True),
        "counter_reset_observed": bool(current.get("counter_reset_end")),
        "native_tail_limited": current.get("tail_limited", False),
        "history_complete": False,
        "turn_recording_status": recording,
        "turns_limited": limited,
        "turns": turns,
        "model_requests_for_report": 0,
        "native_quota_refreshed": False,
        **locale,
    }


def render_task_report(report: dict, format: str = "markdown") -> str:
    if format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    text = ReportText(report.get("locale", "en"))
    label = report["task"]["display_name"]
    if report.get("scope") == "selected_subagent":
        label += " · @" + report["task"]["selector"]
        label += " · " + text("owner", name=report["task"].get("parent_name") or
                               text("unknown_parent"))
    usage = report.get("task_usage") or {}
    cache_share = cache_read_share_percent(usage)
    rows = [(text("child_task_total" if report.get("scope") == "selected_subagent"
                   else "task_total"), text.number(usage.get("total"))),
            *[(text(key), text.number(usage.get(field))) for key, field in
              (("input", "input"), ("cached", "cached_input"), ("output", "output"),
               ("reasoning", "reasoning_output"))]]
    if cache_share is not None:
        rows[2] = (rows[2][0], rows[2][1] + f" ({cache_share:g}%)")
    coverage = [report.get("usage_status", "unavailable"), "history_complete=false"]
    coverage.extend(key + "=true" for key in (
        "counter_reset_observed", "native_tail_limited", "contexts_limited", "turns_limited"
    ) if report.get(key))
    current_context = "; ".join(
        text("context_pair", model=item.get("model") or text("unknown"),
             effort=item.get("reasoning_effort") or text("unknown"))
        for item in report.get("contexts", [])
    ) or text("not_observed")
    rows.extend(((text("report_coverage"), "; ".join(coverage)),
                 (text("context_heading"), current_context),
                 (text("turn_record_state"), report.get("turn_recording_status", "unavailable"))))
    turn_rows = []
    child_rows = []
    for row in report.get("turns", []):
        contexts = "; ".join(text("context_pair", model=item.get("model") or text("unknown"),
                                 effort=item.get("reasoning_effort") or text("unknown"))
                             for item in row.get("contexts", [])) or text("not_observed")
        state = row["state"] + " · " + row.get("usage_status", "unavailable")
        if row.get("contexts_limited"):
            state += " · contexts_limited=true"
        children = row.get("subagents") or {"status": "unavailable"}
        child_total = (text("na") if children.get("status") == "none" else
                       text.number((children.get("usage") or {}).get("total")))
        child_total += " · " + child_coverage(children, text.locale)
        turn_rows.append((row["started_at"], state,
                          text.number((row.get("usage") or {}).get("total")),
                          child_total, contexts))
        for child in children.get("rows", []):
            if isinstance(child, dict) and child.get("selector"):
                child_rows.append((row["started_at"], "@" + child["selector"] + " · "
                                   + str(child.get("display_name") or text("unnamed")),
                                   text.number((child.get("usage") or {}).get("total"))))
    headers = (text("recorded_turns"), text("turn_record_state"), text("turn_delta"),
               text("child_subtotal"), text("context_heading"))
    note = text("task_report_note")
    if format == "markdown":
        def cell(value):
            # Native names and context strings remain data, including Markdown metacharacters.
            value = escape(str(value), quote=False).replace("|", "&#124;")
            for char in "[]*_`\\":
                value = value.replace(char, f"&#{ord(char)};")
            return value.replace("\n", " ").replace("\r", " ")
        child_section = (["## " + text("children"), "",
                          "| " + " | ".join((text("recorded_turns"), text("children"),
                                             text("child_subtotal"))) + " |",
                          "| --- | --- | ---: |",
                          *("| " + " | ".join(cell(value) for value in row) + " |"
                            for row in child_rows), ""] if child_rows else [])
        return "\n".join([
            "# " + text("task_report_title"), "", cell(label), "",
            "| " + text("column_item") + " | " + text("column_observation") + " |",
            "| --- | ---: |", *(f"| {cell(key)} | {cell(value)} |" for key, value in rows), "",
            "| " + " | ".join(headers) + " |", "| --- | --- | ---: | --- | --- |",
            *("| " + " | ".join(cell(value) for value in row) + " |" for row in turn_rows),
            "", *child_section, note, "",
        ])
    if format != "html":
        raise ValueError("unsupported report format")
    def table(headers, rows):
        return "<table><thead><tr>" + "".join("<th>" + escape(h) + "</th>" for h in headers) + (
            "</tr></thead><tbody>" + "".join("<tr>" + "".join("<td>" + escape(str(v)) +
            "</td>" for v in row) + "</tr>" for row in rows) + "</tbody></table>")
    return (f'<!doctype html><html lang="{text.locale}"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
            '<style>body{font:15px/1.6 system-ui;max-width:980px;margin:auto;padding:32px;'
            'background:#fcfcfa;color:#19332f}table{width:100%;border-collapse:collapse;'
            'margin:24px 0}th,td{padding:10px;text-align:left;border-bottom:1px solid #d3ded8;'
            'overflow-wrap:anywhere}h1{font-size:28px}p{overflow-wrap:anywhere}</style>'
            f'<title>{escape(text("task_report_title"))}</title><main>'
            f'<h1>{escape(text("task_report_title"))}</h1><p>{escape(label)}</p>'
            + table((text("column_item"), text("column_observation")), rows)
            + table(headers, turn_rows)
            + (f'<h2>{escape(text("children"))}</h2>'
               + table((text("recorded_turns"), text("children"), text("child_subtotal")),
                       child_rows) if child_rows else "")
            + f'<p>{escape(note)}</p></main></html>')
