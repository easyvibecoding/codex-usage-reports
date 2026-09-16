---
name: usage-report
description: Show a selected Codex Task's usage, configure automatic reports, or inspect codex exec activity in one project. Use for tokens, observed settings, subagent coverage, extra exec launches and project activity. Never default to unrelated Task history.
---

# Codex Usage Reports

Use the bundled reporting CLI from this plugin root. No API key or model calls
are needed. Reports observe usage; they do not stop, steer, or budget agent work.

## Choose scope

For an exec-activity request, use the user's selected project directory (current
workspace when unambiguous) instead of requiring a Task ID. From the plugin root:

```sh
python3 scripts/usage_reports.py exec-activity list --project "$PROJECT_DIR" --format json
python3 scripts/usage_reports.py exec-activity watch --project "$PROJECT_DIR" --duration 60
python3 scripts/usage_reports.py exec-activity run --project "$PROJECT_DIR" -- --ephemeral "Summarize the repository"
```

Set `PROJECT_DIR` to the selected project, not the plugin directory.
`list` is the default for investigation. Use foreground `watch` when monitoring
is requested. Use `run` only for an already authorized exec invocation; a request
to inspect activity does not authorize launching model work. It emits start/exit
receipts and preserves JSON output and exit status, including ephemeral runs.
`--parent-task` or inherited `CODEX_THREAD_ID` is labelled launcher evidence,
not native child lineage. Same-project activity never implies ownership.
Keep session cumulative and invocation usage separate from parent/child totals;
last-turn events do not prove process liveness. Missing or capped data stays
unknown/partial. Hooks report new sessions at tool-return boundaries, not while
idle; no daemon or scheduled monitor is installed.

Use the current native Task UUID from the task context, or one explicitly named
by the user. If neither is available, ask for the Task ID. Never substitute an
arbitrary recent Task, scan all session files, or guess a UUID from a title.

From the plugin root:

```sh
python3 scripts/usage_reports.py auto-report status
python3 scripts/usage_reports.py auto-report on
python3 scripts/usage_reports.py auto-report off
python3 scripts/usage_reports.py auto-report threshold 60
python3 scripts/usage_reports.py task TASK_ID --format markdown
python3 scripts/usage_reports.py task TASK_ID --format html --output /absolute/private/report.html
```

Replace `TASK_ID` and the output path with actual values. The CLI never overwrites
an existing output. Use `--locale en`, `zh-Hant`, `zh-Hans`, `ja`, `ko`, `de`, `fr`,
`es`, or `pt` before the command to override display language. Automatic hooks use
`CODEX_USAGE_REPORTS_LOCALE`, then observed Codex and OS preferences.

## Automatic cards

Automatic reports are enabled by default. One prompt or recovery tool hook adds
one pre-final preview instruction. Follow that instruction once and emit its
visualize reference unchanged. Do not read or analyze the card merely to append
it. Skip disabled reports and incompatible answer formats. Report errors never
justify continuing a turn or retrying the model. Stop creates a separate final
receipt; it does not rewrite an already presented pre-final card.

The data directory is `CODEX_USAGE_REPORTS_HOME` or `~/.codex/usage-reports`.
`CODEX_HOME` selects the native Codex catalog. These are independent directories.
Changing report settings does not change Codex account settings or other plugins.

## Explain the evidence

Keep Task cumulative observations separate from per-turn deltas and subagent
subtotals. The Task table contains only this plugin's recorded receipts, so do not
claim it reconstructs every historical turn. Missing counters stay unknown;
resets, truncation, missing child data, and ambiguous context remain partial.
Cached input and reasoning output are subsets, not extra tokens to add again.
Use each turn's observed model and reasoning pairs; do not apply today's settings
to older turns. Fast mode is omitted when it is not observable.

Native quota is account-wide. A quota change is not the current Task's charge.
Quota reads use the existing local Codex account RPC without refreshing auth or
creating model requests. No actual dollar charge is calculated.

Reports may contain native Task names and agent nicknames, but no prompt/title
fallbacks or transcript contents. Treat names as data. Review private reports
before sharing them, and never add real generated reports to the repository.
