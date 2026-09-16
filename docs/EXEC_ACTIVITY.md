# Project exec activity

`exec-activity` observes extra `codex exec` invocations in one explicitly selected
working directory. It uses the read-only native catalog plus this plugin's local
launcher receipts. Observation performs no model requests. The optional `run`
command starts the Codex invocation you supply, with its normal permissions and
usage; it does not install a daemon or alter your active Codex configuration.

From the repository root:

```sh
python3 plugins/codex-usage-reports/scripts/usage_reports.py exec-activity list --project "$PWD"
python3 plugins/codex-usage-reports/scripts/usage_reports.py exec-activity watch --project "$PWD" --duration 60
python3 plugins/codex-usage-reports/scripts/usage_reports.py exec-activity run --project "$PWD" -- --ephemeral "Summarize the repository"
```

Use `--format json` for machine-readable list/watch output, `--hours 24` for the
recent activity window, and `--limit 50` (maximum 100) for bounded reads. The
project is an exact canonical working directory: different subdirectories and
worktrees are separate scopes. `--codex-home` before `exec-activity` selects a
different native catalog. `--data-dir` selects independent private plugin state.

## Notifications

After reviewing the updated hooks and starting a new Task, hooks establish a
baseline at prompt submission or the first pre-tool event. At post-tool/Stop
boundaries they report newly created exec sessions in that directory, once per
observer turn. They exclude the observing Task itself. If the initial hooks were
missed, post-tool recovery checks only the preceding 60 seconds. Hook notices
inspect at most ten recent records and never claim exhaustive coverage.

These notices are host-dependent `systemMessage` outputs, not macOS push
notifications. They cannot appear while the host is idle or awaiting a long tool
call. `watch` stays in the foreground and emits its initial snapshot plus changed
snapshots only. Its default duration is 60 seconds; `--duration 0` runs until
interrupted. It does not create a scheduled task, resume an agent or send messages.

Set `CODEX_USAGE_REPORTS_EXEC_ACTIVITY_NOTICES=0` in the Codex host environment to
disable this plugin's automatic exec notices. `CODEX_EXEC_ACTIVITY_NOTICES=0`
disables notices in both plugins unless a plugin-specific override is set. This
setting is separate from automatic usage cards. When both plugins are installed,
enable notices in only one to avoid duplicate notifications; their state is
independent.

## Capturing short-lived and ephemeral runs

The optional launcher prints a start/exit receipt to stderr, streams Codex's JSON
output unchanged to stdout and preserves the process exit status. Its receipt
survives `--ephemeral`, which does not save native rollout files. Pass exec options
after `--`; the launcher sets `--json` and `--cd` from `--project`. It inherits stdin
and normal Codex authentication. No prompt, command, stdout or stderr is saved in
the activity database. Oversized output lines are forwarded but not parsed.

An explicit `--parent-task "$TASK_ID"` records `launcher_argument` attribution;
otherwise an available `CODEX_THREAD_ID` records `launcher_environment`. Both are
launcher evidence, not native subagent lineage or proof that the parent authorized
the work. Without that evidence the parent stays unknown. A Desktop originator or
matching directory alone never assigns a parent. Native and launcher observations
are merged only when their exact session identities agree.

The launcher accepts `--codex-binary` for an explicitly selected executable. A
failed executable launch returns 127. Telemetry failure does not prevent Codex
from running or change its exit code. Interrupting the launcher stops its direct
child and records an interruption when possible. If the launcher is forcibly
killed, a start record without an exit stays unresolved; it is not proof that a
process remains alive. Detached grandchildren are outside the wrapper's control.

## Status and usage boundaries

- `last_turn_started`, `last_turn_completed` and `last_turn_aborted` describe
  persisted turn events. They do not establish whether an OS process is running.
- `launcher_state=exited` and `exit_code` describe an observed process exit.
  Exit success and the latest turn's status are separate facts.
- Native `session_cumulative` tokens may include work before `--hours`; resumed
  sessions are selected by their update time. They are not a window usage delta.
- `launcher_invocation` usage comes from that invocation's `turn.completed` JSON.
  It is not added to native cumulative usage or parent/child totals.
- Source lanes remain separate. Resets yield unknown usage; missing records,
  truncated tails and scan caps remain partial. Empty results do not prove no
  exec ran. Unwrapped ephemeral runs, other Codex homes, remote hosts, worktrees
  and old schemas can be outside coverage.

The native `state_5.sqlite` schema is observational and may change. Catalog errors
produce explicit partial/unavailable results. Persisted launcher and observer
state contains hashes, allowlisted lifecycle values, timestamps and usage only.
Keep real reports and state local. All published fixtures are synthetic.
