# Usage and CLI

[Quick start](../README.md#quick-start) · [Documentation index](README.md) · [Metrics](METRICS.md) · [Troubleshooting](TROUBLESHOOTING.md)

## Install

```sh
codex plugin marketplace add easyvibecoding/codex-usage-reports
codex plugin add codex-usage-reports@codex-usage-reports
```

The marketplace is distributed by this repository; it is not a claim of inclusion in OpenAI's curated directory. Review and trust the hooks, then begin a new Task. Plugin discovery alone does not mean hooks are trusted or running.

To develop from a checkout, use the absolute repository path as the marketplace source:

```sh
codex plugin marketplace add "$PWD"
codex plugin add codex-usage-reports@codex-usage-reports
```

Do this from the repository root. Do not add a second marketplace with the same name without first checking `codex plugin marketplace list`.

## Automatic reports

1. A supported turn-start hook captures a local baseline.
2. Before the final answer, Codex runs the provided preview command once and attaches the returned visualization reference.
3. A supported terminal hook settles a separate receipt in the local report directory.
4. Resume normal work; later turns receive their own baselines.

The preview is immutable. A completed receipt does not rewrite a card already shown in chat. If a turn is still running or interrupted, read its status before interpreting totals.

The cached-input detail now includes its share of the same displayed input.
Cards and saved human receipts use the observed parent-plus-child subtotal when
available; JSON `parent_cache_read_share_percent` uses only the parent usage
record. A missing or zero input denominator remains `null`. The percentage is
an observation, not a server-side cache-miss reason or a savings estimate.

## CLI

All examples run from the cloned repository root. No runtime dependency installation is needed.

The bundled script exposes these commands. Global options such as `--data-dir`,
`--codex-home`, and `--locale` go before the command.

| Command | Purpose | Detailed guide |
| --- | --- | --- |
| `auto-report status|on|off|list|threshold` | Read or change automatic report settings and list recent receipts. | Below |
| `task TASK_ID` | Render one selected parent Task as Markdown, JSON, or HTML. | Below |
| `preview TASK_ID TURN_ID` | Render an active turn's pre-final card into an allowed output directory. | Below |
| `exec-activity list|watch|run` | Observe one project's extra exec sessions or opt into a launcher. | [Exec activity](EXEC_ACTIVITY.md) |
| `updates check` | Read installed package version and native hook trust. | [Update notices](UPDATE_NOTICES.md) |

Signed runtime controls are a separate installed-plugin script,
`scripts/publisher_updates.py status|on|off|update|rollback`. The package
version from `updates check` can differ from the active signed runtime version.
[Signed update controls](SIGNED_UPDATES.md).

```sh
python3 plugins/codex-usage-reports/scripts/usage_reports.py --help
python3 plugins/codex-usage-reports/scripts/usage_reports.py auto-report status
python3 plugins/codex-usage-reports/scripts/usage_reports.py auto-report list
python3 plugins/codex-usage-reports/scripts/usage_reports.py auto-report off
python3 plugins/codex-usage-reports/scripts/usage_reports.py auto-report on
python3 plugins/codex-usage-reports/scripts/usage_reports.py auto-report threshold 60
```

The threshold is elapsed seconds; zero includes short turns. Switching reports off preserves existing receipts.
`auto-report list` lists this plugin's recent local receipts, not all native Task
history.

### Selected Task

Set `TASK_ID` to the selected Task's native ID, then run:

```sh
python3 plugins/codex-usage-reports/scripts/usage_reports.py task "$TASK_ID" --format markdown
python3 plugins/codex-usage-reports/scripts/usage_reports.py task "$TASK_ID" --format json --output ./work/task.json
python3 plugins/codex-usage-reports/scripts/usage_reports.py task "$TASK_ID" --format html --output ./work/task.html
```

Use the Task's ID from Codex rather than its display name. Display names are not unique. The skill can resolve the current Task from its native context.
The default turn-row limit is 50 and the accepted range is 1–100. `--output`
creates a private file and refuses to overwrite an existing path.

### Data directories

Reporting data defaults to `~/.codex/usage-reports`. Override it with `CODEX_USAGE_REPORTS_HOME` or the CLI's `--data-dir`. Native Codex state defaults to `CODEX_HOME`, or `~/.codex`; use `--codex-home` for a specific catalog.

```sh
python3 plugins/codex-usage-reports/scripts/usage_reports.py --data-dir ./work/report-data auto-report status
```

Pass global options before the subcommand. Keep data directories out of source control.

### Preview a running turn

Normally the hook provides this command with the correct IDs and directory. Manual use:

```sh
python3 plugins/codex-usage-reports/scripts/usage_reports.py preview "$TASK_ID" "$TURN_ID" --output-dir "$OUTPUT_DIR"
```

The turn must have an active baseline. `OUTPUT_DIR` must be an absolute, nonsymlink directory inside the native Task visualization root or that Task's catalog-recorded workspace. An arbitrary temporary directory is not an accepted desktop output root.

### Optional Python installation

To get the `codex-usage-reports` command, install the checkout in a virtual environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/codex-usage-reports auto-report status
```

This installs the report CLI, not the Codex plugin hooks. Use the plugin installation above for automatic reports. Windows users can use the equivalent executables in `.venv\\Scripts`; native hook support remains subject to the compatibility table.

## Language

Cards follow the observed Codex desktop language override, then the system language, with English fallback. Available catalogs: `en`, `zh-Hant`, `zh-Hans`, `ja`, `ko`, `de`, `fr`, `es`, `pt`. Model names, identifiers, commands, and machine-readable status values are not translated.

Set `CODEX_USAGE_REPORTS_LOCALE` for an explicit report language, or use the global CLI option `--locale zh-Hant` before the subcommand. An explicit report preference takes priority over the desktop and system language.

## Update or uninstall

Compatible signed runtime updates normally need no package reinstall. To read
the active version, run this from the installed plugin directory:

```sh
python3 scripts/publisher_updates.py status
```

The same script accepts `on`, `off`, `update`, or `rollback` in place of
`status`. Existing Tasks retain their pinned runtime. See
[signed updates](SIGNED_UPDATES.md).

To inspect the installed **package** version and native hook trust for a
project, run from the installed plugin directory:

```sh
python3 scripts/usage_reports.py updates check --refresh --cwd "$PROJECT_DIR"
```

For a plugin, entry, key, hook, or skill structure change, refresh the
marketplace, reinstall the plugin, review changed definitions in CLI `/hooks`,
and start a new Task:

```sh
codex plugin marketplace upgrade codex-usage-reports
codex plugin add codex-usage-reports@codex-usage-reports
```

```sh
codex plugin remove codex-usage-reports@codex-usage-reports
```

Uninstalling hooks does not delete your report history. You can inspect or archive the data directory separately. For coexistence with the original plugin, read [migration](MIGRATION.md).
