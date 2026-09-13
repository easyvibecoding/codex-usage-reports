"""Deterministic, localized views of an already captured account quota snapshot.

No collection, persistence, model calls, or token-to-quota conversion belongs here.
Only known display fields are admitted; absent and expired values are not zero.
"""

from __future__ import annotations

import math
from datetime import datetime
from html import escape

from .meter_plan import plan_type
from .report_i18n import ReportText


def _number(value, text: ReportText, *, low=0, high=100) -> str | None:
    if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
        return None
    # ReportText preserves supplied fractional precision; never round a nonzero
    # observation down to a displayed zero or infer a hidden native precision.
    return text.number(int(value) if value == int(value) else value)


def _window(row: dict, text: ReportText) -> str:
    bucket_id = row.get("bucket")
    if not isinstance(bucket_id, str):
        bucket_id = "other"
    bucket = {
        "codex": "Codex", "spark": "Spark",
        "codex_bengalfox": text("quota_bucket_additional"),
    }.get(bucket_id, text("quota_bucket_other"))
    minutes = row.get("duration_minutes")
    if type(minutes) is not int or not 0 < minutes <= 525600:
        duration = text("quota_window_unknown")
    elif minutes % 1440 == 0:
        duration = text("quota_days", value=text.number(minutes // 1440))
    elif minutes % 60 == 0:
        duration = text("quota_hours", value=text.number(minutes // 60))
    else:
        duration = text("minutes", value=text.number(minutes))
    return text("quota_window_value", bucket=bucket, duration=duration)


def _comparison(row: dict, text: ReportText) -> str:
    comparison = row.get("comparison")
    if comparison in ("baseline", "previous_unavailable", "reset", "corrected",
                      "not_comparable", "expired"):
        return text("quota_" + comparison)
    if _number(row.get("remaining_percent"), text) is None:
        return text("not_observed")
    delta = row.get("delta_pp")
    value = _number(delta, text, low=-100)
    if comparison == "unchanged" or (comparison == "observed" and value is not None and delta == 0):
        return text("quota_unchanged")
    if comparison == "observed" and value is not None:
        if delta > 0:
            return text("quota_corrected")
        return text("quota_delta_pp", value=value)
    return text("not_observed")


def _rows(quota, text: ReportText) -> list[tuple[str, str, str]]:
    if not isinstance(quota, dict) or quota.get("status") not in ("observed", "unavailable"):
        return []
    source = quota.get("rows")
    if not isinstance(source, list):
        return []
    result = []
    for row in source[:16]:
        if not isinstance(row, dict):
            continue
        if quota.get("status") == "unavailable":
            row = {**row, "remaining_percent": None}
        remaining = None if row.get("comparison") == "expired" else _number(
            row.get("remaining_percent"), text
        )
        result.append((
            _window(row, text),
            (text("quota_percent", value=remaining)
             if remaining is not None else text("not_observed")),
            _comparison(row, text),
        ))
    return result


def _capture_note(quota, text: ReportText) -> str | None:
    if not isinstance(quota, dict) or quota.get("status") not in ("observed", "unavailable"):
        return None
    value = quota.get("captured_at")
    if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
        return None
    try:
        stamp = datetime.fromtimestamp(value).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except (OSError, OverflowError, ValueError):
        return None
    return text("quota_captured", time=stamp)


def _plan_note(quota, text: ReportText) -> str:
    plan = plan_type(quota.get("plan_type")) if isinstance(quota, dict) else None
    display = {"plus": "Plus", "pro": "Pro"}.get(plan, plan)
    return text("quota_plan", plan=display or text("quota_plan_unknown"))


def render_html(quota, text: ReportText, *, collapsible=False) -> str:
    """Return escaped fixed markup, suitable for both the inline card and Stop HTML."""
    title = escape(text("quota_heading"))
    container, heading = ("details", "summary") if collapsible else ("section", "h3")
    summary = title
    if collapsible and isinstance(quota, dict) and isinstance(quota.get("rows"), list):
        # Only an unambiguous native main-Codex weekly window belongs in the
        # compact summary. Never substitute another bucket or infer one by plan.
        weekly = [row for row in quota["rows"][:16] if isinstance(row, dict)
                  and row.get("bucket") == "codex" and row.get("duration_minutes") == 10080]
        values = _rows({**quota, "rows": weekly}, text) if len(weekly) == 1 else []
        if values:
            _, remaining, change = values[0]
            caption = " · ".join((text("quota_days", value=text.number(7)),
                                  text("quota_remaining") + ": " + remaining,
                                  text("quota_change") + ": " + change))
            summary += '<span class="report-quota-weekly">' + escape(caption) + '</span>'
    parts = [f'<{container} class="report-quota" aria-label="{title}">'
             f'<{heading}>{summary}</{heading}>']
    parts.append('<p class="text-small report-quota-plan">'
                 + escape(_plan_note(quota, text)) + '</p>')
    rows = _rows(quota, text)
    if rows:
        parts.append('<table class="report-quota-table"><thead><tr>' + "".join(
            '<th scope="col">' + escape(text(key)) + '</th>'
            for key in ("quota_window", "quota_remaining", "quota_change")
        ) + '</tr></thead><tbody>')
        for window, remaining, comparison in rows:
            parts.append('<tr><th scope="row">' + escape(window)
                         + '</th><td class="tabular-nums">' + escape(remaining)
                         + '</td><td>' + escape(comparison) + '</td></tr>')
        parts.append('</tbody></table>')
    else:
        parts.append('<p>' + escape(text("quota_unavailable")) + '</p>')
    captured = _capture_note(quota, text)
    if captured:
        parts.append('<p class="text-small">' + escape(captured) + '</p>')
    parts.extend('<p class="text-small">' + escape(text(key)) + '</p>'
                 for key in ("quota_scope_note", "quota_precision_note"))
    parts.append(f'</{container}>')
    return "".join(parts)


def render_markdown(quota, text: ReportText) -> str:
    """Return the same observations as compact, injection-safe human Markdown."""
    def safe(value):
        return escape(value).replace("|", "&#124;").replace("\n", " ")

    parts = ["### " + safe(text("quota_heading")), "", safe(_plan_note(quota, text)), ""]
    rows = _rows(quota, text)
    if rows:
        parts.extend([
            "| " + " | ".join(safe(text(key)) for key in (
                "quota_window", "quota_remaining", "quota_change"
            )) + " |", "| --- | --- | --- |",
        ])
        parts.extend("| " + " | ".join(safe(value) for value in row) + " |" for row in rows)
    else:
        parts.append(safe(text("quota_unavailable")))
    captured = _capture_note(quota, text)
    for note in (captured, text("quota_scope_note"), text("quota_precision_note")):
        if note:
            parts.extend(["", safe(note)])
    return "\n".join(parts)
