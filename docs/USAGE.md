# Usage guide

[English README](../README.md) · [Metrics](METRICS.md) · [Troubleshooting](TROUBLESHOOTING.md)

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

## CLI

All examples run from the cloned repository root. No runtime dependency installation is needed.

```sh
python3 plugins/codex-usage-reports/scripts/usage_reports.py --help
python3 plugins/codex-usage-reports/scripts/usage_reports.py auto-report status
python3 plugins/codex-usage-reports/scripts/usage_reports.py auto-report list
python3 plugins/codex-usage-reports/scripts/usage_reports.py auto-report off
python3 plugins/codex-usage-reports/scripts/usage_reports.py auto-report on
python3 plugins/codex-usage-reports/scripts/usage_reports.py auto-report threshold 60
```

The threshold is elapsed seconds; zero includes short turns. Switching reports off preserves existing receipts.

### Selected Task

Set `TASK_ID` to the selected Task's native ID, then run:

```sh
python3 plugins/codex-usage-reports/scripts/usage_reports.py task "$TASK_ID" --format markdown
python3 plugins/codex-usage-reports/scripts/usage_reports.py task "$TASK_ID" --format json --output ./work/task.json
python3 plugins/codex-usage-reports/scripts/usage_reports.py task "$TASK_ID" --format html --output ./work/task.html
```

Use the Task's ID from Codex rather than its display name. Display names are not unique. The skill can resolve the current Task from its native context.

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

Refresh this marketplace, reinstall the plugin, and start a new Task. Existing Tasks may continue using their pinned runtime.

```sh
codex plugin marketplace upgrade codex-usage-reports
codex plugin add codex-usage-reports@codex-usage-reports
```

```sh
codex plugin remove codex-usage-reports@codex-usage-reports
```

Uninstalling hooks does not delete your report history. You can inspect or archive the data directory separately. For coexistence with the original plugin, read [migration](MIGRATION.md).
