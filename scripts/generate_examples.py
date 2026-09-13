#!/usr/bin/env python3
"""Render public examples from synthetic data only; never reads a Codex home."""
from __future__ import annotations

import copy
import json
import sys
from html import escape
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/codex-usage-reports/lib"))

from codex_usage_reports.auto_preview import render_card  # noqa: E402
from codex_usage_reports.auto_report import _documents  # noqa: E402
from codex_usage_reports.task_report import render_task_report  # noqa: E402


def usage(total):
    return {"total": total, "input": total * 9 // 10, "cached_input": total * 6 // 10,
            "output": total // 10, "reasoning_output": total // 20}


def receipt():
    return {
        "key": "example", "task_name": "Example · Build a local reading list",
        "task": {"display_name": "Example · Build a local reading list"},
        "task_hash": "0" * 64, "turn_hash": "1" * 64,
        "locale": "en", "locale_source": "synthetic",
        "usage": usage(18400), "usage_status": "boundary_counter_difference",
        "task_usage": usage(126800), "elapsed_seconds": 142,
        "captured_at": "10:42:22 UTC", "started_at": "2026-01-01T10:40:00Z",
        "stopped_at": "2026-01-01T10:42:22Z",
        "contexts": [{"model": "example-model", "reasoning_effort": "high"}],
        "stop_contexts": [{"model": "example-model", "reasoning_effort": "high"}],
        "contexts_limited": False,
        "subagents": {
            "status": "observed", "usage": usage(6200),
            "agents_seen": 1, "agents_with_usage": 1,
            "pending_agents": 0, "missing_agents": 0, "selection_limited": False,
            "rows": [{"display_name": "Example · Component review",
                      "parent_name": "Example · Build a local reading list",
                      "terminal_observed": True, "usage": usage(6200)}],
        },
        "quota": {"status": "observed", "plan_type": "pro", "rows": [
            {"bucket": "codex", "duration_minutes": 10080,
             "remaining_percent": 72, "comparison": "observed", "delta_pp": -1},
            {"bucket": "codex", "duration_minutes": 300,
             "remaining_percent": 88, "comparison": "unchanged", "delta_pp": 0},
        ]},
    }


def frame(fragment, locale):
    return Template("""<!doctype html>
<html lang="$locale"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Codex Usage Reports · Synthetic example</title>
<style>
:root{color-scheme:light;--foreground:#1a2c35;--muted-foreground:#536772;--border:#d7e1e4}
*{box-sizing:border-box}body{margin:0;background:#eaf0f0;color:var(--foreground);
font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:900px;margin:0 auto;padding:36px}
.example-kicker{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:#536772;
margin:0 0 8px}.example-title{font-size:25px;margin:0 0 20px;font-weight:650}
.example-note{font-size:12px;color:#536772;margin:18px 0 0}
section[id]{background:#fff}h2{font-size:20px}.text-small{font-size:13px}
.viz-stat-value{font-size:36px;font-weight:650;letter-spacing:-.04em}
.tabular-nums{font-variant-numeric:tabular-nums}
summary:focus-visible{outline:2px solid #086b68;outline-offset:4px}
@media(max-width:500px){main{padding:16px}.viz-stat-value{font-size:30px}}
@media(prefers-color-scheme:dark){:root{color-scheme:dark;--foreground:#e6efef;
--muted-foreground:#a0b2bb;--border:#344650}body{background:#0b1a23}
section[id]{background:#132630}.example-kicker,.example-note{color:#a0b2bb}}
</style><main><p class="example-kicker">Codex Usage Reports / Product example</p>
<h1 class="example-title">Every task. Every turn.</h1>$fragment
<p class="example-note">Synthetic data · Real report template · Documentation frame</p>
</main></html>""").substitute(locale=escape(locale), fragment=fragment)


def main():
    directory = ROOT / "docs/examples"
    directory.mkdir(exist_ok=True, parents=True)
    base = receipt()
    (directory / "synthetic-receipt.json").write_text(
        json.dumps(base, ensure_ascii=False, indent=2) + "\n")
    for locale in ("en", "zh-Hant", "zh-Hans", "ja"):
        item = copy.deepcopy(base)
        item["locale"] = locale
        content = frame(render_card(item), locale)
        (directory / f"turn-{locale}.html").write_text(
            "\n".join(line.rstrip() for line in content.splitlines()) + "\n")
    markdown, html = _documents(base)
    (directory / "completed-receipt.md").write_text(
        "> Synthetic example; all names, settings, and numbers are invented.\n\n" + markdown)
    (directory / "completed-receipt.html").write_text(html)
    report = {
        "schema_version": 1, "scope": "selected_task", "task": base["task"], "locale": "en",
        "captured_at": "2026-01-01T10:42:22Z", "task_usage": base["task_usage"],
        "usage_status": "observed", "history_complete": False, "turns_limited": False,
        "contexts": base["contexts"], "turn_recording_status": "observed",
        "native_tail_limited": False, "counter_reset_observed": False,
        "turns": [
            {"started_at": "2026-01-01T10:40:00Z", "state": "reported",
             "usage": usage(18400), "contexts": base["contexts"], "usage_status": "observed"},
            {"started_at": "2026-01-01T10:35:00Z", "state": "reported",
             "usage": usage(12400), "contexts": [
                 {"model": "example-model", "reasoning_effort": "medium"}],
             "usage_status": "observed"},
        ],
    }
    for format in ("markdown", "html", "json"):
        extension = "md" if format == "markdown" else format
        (directory / ("selected-task." + extension)).write_text(render_task_report(report, format))
    print("Generated synthetic examples; no local Codex data was read.")


if __name__ == "__main__":
    main()
