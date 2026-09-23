"""One bounded pre-final snapshot for Codex's inline visualization surface.

This tool never starts or completes a turn, calls a model, reads unrelated Tasks,
or rewrites a previously presented snapshot. The model emits only its reference.
"""

from __future__ import annotations

import json
import math
import os
import pkgutil
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from string import Template
from urllib.parse import quote

from .auto_report import _delta, child_coverage, observed_total, settings, snapshot
from .child_usage import collect
from .report_i18n import ReportText, resolve_locale
from .task_catalog import TaskCatalog, _uuid
from .transcript import cache_read_share_percent
from .util import stable_hash


def _checked_output_dir(output_dir: Path, session: str, home: Path, workspace) -> Path:
    """Accept only known desktop-readable Task roots, before collecting usage.

    Full filesystem access is not the desktop visualization read policy. The
    native root uses the UUIDv7's UTC date, not today's date or a mutable cwd.
    Explicit extra sandbox roots are intentionally unsupported here: the preview
    tool cannot independently verify the desktop's effective policy for them.
    """
    identifier = uuid.UUID(session)
    if identifier.version != 7:
        raise ValueError("inline preview requires a native UUIDv7 Task")
    day = datetime.fromtimestamp(int(identifier.hex[:12], 16) / 1000, timezone.utc)
    native = home / "visualizations" / day.strftime("%Y/%m/%d") / session
    roots = [native]
    if isinstance(workspace, str) and Path(workspace).is_absolute():
        roots.append(Path(workspace))
    if (not output_dir.is_absolute() or ".." in output_dir.parts
            or any(p.is_symlink() for p in (output_dir, *output_dir.parents))):
        raise ValueError("output must be an absolute nonsymlink Task directory")
    if not any(root.is_absolute() and ".." not in root.parts
               and output_dir.is_relative_to(root) for root in roots):
        raise ValueError("output is outside the Task's desktop-readable roots")
    return output_dir


def render_card(receipt: dict) -> str:
    from .quota_view import render_html

    text = ReportText(receipt.get("locale", "zh-Hant"))
    parent = receipt["usage"]
    task_usage = receipt.get("task_usage")
    children = receipt.get("subagents") or {"status": "unavailable"}
    usage, complete = observed_total(parent, children)
    pending = receipt.get("usage_status") in ("first_turn_usage_pending", "turn_usage_pending")

    def number(key):
        return text.number(usage[key] if usage is not None else None)

    cache_share = cache_read_share_percent(usage)
    cached_display = number("cached_input") + (
        f" ({cache_share:g}%)" if cache_share is not None else ""
    )

    contexts = receipt.get("contexts") or []
    context_pairs = [text(
        "context_pair", model=context.get("model") or text("not_observed"),
        effort=context.get("reasoning_effort") or text("not_observed"),
    ) for context in contexts[:16]]
    contexts_limited = receipt.get("contexts_limited", False) or len(contexts) > 16
    parent_total = parent.get("total") if parent is not None else None

    seconds = receipt["elapsed_seconds"]
    elapsed = text.duration(seconds)
    template = pkgutil.get_data("codex_usage_reports", "assets/turn-card.html")
    if template is None:
        raise ValueError("card template unavailable")
    values = {
        "key": receipt["key"], "task": receipt["task_name"],
        "captured": receipt["captured_at"],
        "total": text("pending_usage") if pending and usage is None else number("total"),
        "task_total": text.number(task_usage.get("total") if task_usage is not None else None),
        "turn_delta": (text("pending_usage") if pending and parent_total is None else
                       ("+" if parent_total is not None and parent_total > 0 else "")
                       + text.number(parent_total)),
        "elapsed": elapsed,
        "locale": text.locale,
        "total_label": text("total" if complete else "subtotal"),
        "usage_label": text("pending_usage_note") if pending else
        "" if complete else text("incomplete"),
        "parent_total": text("pending_usage") if pending and parent_total is None else
        text.number(parent_total),
        "child_total": (
            text("na") if children.get("status") == "none" else
            text.number(children['usage']['total'] if children.get("usage") is not None else None)
        ),
        "coverage": child_coverage(children, text.locale),
        "input": number("input"), "cached": cached_display,
        "output": number("output"), "reasoning": number("reasoning_output"),
    }
    values.update({"label_" + key: text(key) for key in (
        "card_title", "pre_final", "elapsed_wait", "details", "parent", "child_subtotal",
        "input", "cached", "output", "reasoning", "card_note", "task_total", "turn_delta",
        "context_heading", "context_note", "combined",
    )})
    escaped = {k: escape(str(v)) for k, v in values.items()}
    # Native display names are data. Only this fixed markup is inserted unescaped.
    if len(context_pairs) > 1:
        escaped["contexts"] = '<ol class="report-context-list">' + "".join(
            "<li>" + escape(pair) + "</li>" for pair in context_pairs
        ) + "</ol>"
    else:
        escaped["contexts"] = '<p class="report-context-single">' + escape(
            context_pairs[0] if context_pairs else text("not_observed")
        ) + "</p>"
    escaped["context_partial"] = (
        '<p class="text-small report-warning">' + escape(text("context_partial")) + "</p>"
        if contexts_limited else ""
    )
    escaped["agents"] = "".join(
        '<div class="report-agent"><div>' + escape(row["display_name"])
        + '<div class="text-small">'
        + escape(text("owner", name=row.get("parent_name") or text("unknown_parent")))
        + " · " + escape(text("terminal" if row.get("terminal_observed") else "pending_end"))
        + '</div></div><div class="tabular-nums">'
        + text.number(row['usage']['total'] if row.get("usage") is not None else None)
        + "</div></div>"
        for row in children.get("rows", [])[:32]
    )
    escaped["quota"] = render_html(receipt.get("quota"), text, collapsible=True)
    return Template(template.decode()).substitute(escaped)


def preview(root: Path, session: str, turn: str, *, output_dir: Path, home=None) -> dict:
    """Return only a reference/status, never a report body or raw native paths."""
    policy = settings(root)
    if not policy["enabled"]:
        return {"status": "disabled"}
    if not _uuid(session) or not isinstance(turn, str) or not 0 < len(turn) <= 256:
        raise ValueError("invalid preview identity")
    key = stable_hash([session, turn])
    path = root / "auto-reports/timing.sqlite3"
    if root.is_symlink() or path.parent.is_symlink() or path.is_symlink() or not path.is_file():
        return {"status": "missing_start"}
    connection = sqlite3.connect(
        "file:" + quote(str(path.absolute())) + "?mode=ro", uri=True, timeout=0.4
    )
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute("SELECT * FROM turns WHERE key=?", (key,)).fetchone()
    finally:
        connection.close()
    if row is None or row["state"] not in ("started", "short"):
        return {"status": "no_active_start"}
    if row["session_hash"] != stable_hash(session) or row["turn_hash"] != stable_hash(turn):
        raise ValueError("timing identity mismatch")
    captured = time.time()
    seconds = time.monotonic() - row["monotonic"]
    if (
        not math.isfinite(seconds) or seconds < 0
        or abs(captured - row["started"] - seconds) > 10
    ):
        seconds = None
    if policy["threshold_seconds"] and (seconds is None or seconds <= policy["threshold_seconds"]):
        return {"status": "below_threshold"}
    locale = resolve_locale(home=home)
    text = ReportText(locale["locale"])
    with TaskCatalog(home, unnamed_label=text("unnamed")) as catalog:
        task = catalog.get(session)
        if task["role"] != "parent":
            return {"status": "subagent"}
        native = catalog.connection.execute(
            "SELECT rollout_path FROM threads WHERE id=?", (session,)
        ).fetchone()
        # Optional column on older catalogs; absence cannot authorize another root.
        columns = {r[1] for r in catalog.connection.execute("PRAGMA table_info(threads)")}
        workspace = catalog.connection.execute(
            "SELECT cwd FROM threads WHERE id=?", (session,)
        ).fetchone() if "cwd" in columns else None
        native_home = Path(home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        output_dir = _checked_output_dir(
            output_dir, session, native_home, workspace[0] if workspace else None,
        )
        current = snapshot(native[0] if native else None, turn)
    if current.get("task_hash") != stable_hash(session):
        return {"status": "source_unavailable"}
    baseline = json.loads(row["baseline"])
    if baseline.get("task_hash") != stable_hash(session):
        return {"status": "source_unavailable"}
    usage, status = _delta(baseline, current)
    if (status == "counter_unavailable" and baseline.get("fresh_turn_start")
            and current.get("first_turn_only") and current.get("usage") is None):
        status = "first_turn_usage_pending"
    children = collect(root, session, row["started"], captured, home=home,
                       unnamed_label=text("unnamed"))
    # One small account read inside the existing preview tool, never in hooks.
    from .turn_quota import observe
    quota = observe(root, key, home=home)
    receipt = {
        "key": key, "task_name": task["display_name"], "usage": usage, "usage_status": status,
        "parent_cache_read_share_percent": cache_read_share_percent(usage),
        "contexts": current["contexts"], "contexts_limited": current.get("contexts_limited", False),
        "task_usage": current.get("usage"), "elapsed_seconds": seconds,
        "counter_source": current.get("counter_source"),
        "captured_at": datetime.fromtimestamp(captured).astimezone().strftime("%H:%M:%S %Z"),
        "subagents": children, "quota": quota,
        **locale,
    }
    # Recheck immediately before writing, after potentially slow collection.
    _checked_output_dir(output_dir, session, native_home, workspace[0] if workspace else None)
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = output_dir / ("codex-turn-" + key[:16] + "-" + str(time.time_ns()) + ".html")
    content = render_card(receipt)
    if len(content.encode()) > 1_000_000:
        raise ValueError("card too large")
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(content)
    reference = (
        "\ue200visualize\ue202" + json.dumps({"path": str(target)}, ensure_ascii=False) + "\ue201"
    )
    return {"status": "preview", "reference": reference}
