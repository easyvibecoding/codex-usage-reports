<p align="center">
  <img src="docs/assets/hero.png" alt="Codex Usage Reports — Every task. Every turn." width="100%">
</p>

# Codex Usage Reports

**Automatic token usage reports for every Codex Task and turn.** See Task totals, turn deltas, observed models, reasoning effort, and subagent usage in a compact local report.

[![CI](https://github.com/easyvibecoding/codex-usage-reports/actions/workflows/ci.yml/badge.svg)](https://github.com/easyvibecoding/codex-usage-reports/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-mintcream)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB)](pyproject.toml)

**English** · [繁體中文](docs/i18n/README.zh-TW.md) · [简体中文](docs/i18n/README.zh-CN.md) · [日本語](docs/i18n/README.ja.md)

[Quick start](#quick-start) · [Examples](#see-the-report) · [User guide](docs/USAGE.md) · [How the numbers work](docs/METRICS.md) · [Troubleshooting](docs/TROUBLESHOOTING.md)

## Know what each turn used

A long Task can include many turns, model switches, and child agents. A single session total does not explain the latest change. Codex Usage Reports keeps those scopes visible:

| Question | What the report shows |
| --- | --- |
| How much has this Task used? | The selected parent Task's observed cumulative tokens. |
| What changed this turn? | The difference between valid native counter observations. |
| Which model and effort ran? | Settings observed in that turn, including visible changes. |
| Did subagents contribute? | A separate child subtotal and coverage status. |
| How much account quota remains? | Native quota observations when available, separate from Task tokens. |
| Can I inspect it later? | Local HTML, Markdown, and JSON receipts. |

The runtime uses Python's standard library. It does not call an LLM to calculate usage, require an API key, or send reports to a hosted analytics service. This is the standalone reporting extraction of [Codex Run Budget](https://github.com/easyvibecoding/codex-run-budget).

## See the report

![Automatic per-turn report showing Task cumulative tokens and current-turn delta](docs/examples/turn-en.png)

*Synthetic example rendered by the real report template in a standalone documentation frame. The desktop supplies its own surrounding theme.*

<details>
<summary>繁體中文 example</summary>

![繁體中文每輪報告範例](docs/examples/turn-zh-Hant.png)

</details>

[Open the example gallery](docs/examples/README.md) for downloadable HTML, a completed receipt, and the selected-Task report. Brand illustrations were created with Codex image generation; usage screenshots were rendered from synthetic fixtures. [Artwork prompts](docs/assets/PROMPTS.md).

## Quick start

Requires Python 3.10+ and a local Codex environment with plugin hooks. The inline card needs a desktop surface that supports local visualizations. CLI users can read the saved reports. Native state schemas are version-dependent; see [compatibility and validation](docs/VALIDATION.md).

### 1. Install the plugin

```sh
codex plugin marketplace add easyvibecoding/codex-usage-reports
codex plugin add codex-usage-reports@codex-usage-reports
```

Review and trust the plugin hooks in Codex, then **start a new Task**. Installed hooks are loaded according to the host's trust and lifecycle rules. See the official [plugin guide](https://learn.chatgpt.com/docs/plugins) and [hook guide](https://learn.chatgpt.com/docs/hooks).

After an update, open `/hooks` in the Codex CLI and review changed plugin hooks again. Trust is bound to the exact hook definition: an installed and enabled plugin can still have `modified` hooks that Codex skips. Restarting the app or opening a Task from a phone does not grant trust. After completing the review, start a new Task.

### 2. Work normally

Ask Codex to do your usual work. The hook records a turn baseline, asks for one pre-final card, and saves a completed receipt when a supported terminal event arrives. Reporting is enabled by default.

The inline card is a snapshot taken **before** the final answer. The completed receipt may contain later usage. If the host skips a hook or does not yet expose counters, the report marks the data as pending, partial, or unavailable.

### 3. Inspect a Task

Use the bundled `usage-report` skill, for example:

> Show the usage report for this Task, including turn totals and observed settings.

Or clone the repository for the standalone CLI:

```sh
git clone https://github.com/easyvibecoding/codex-usage-reports.git
cd codex-usage-reports
python3 plugins/codex-usage-reports/scripts/usage_reports.py auto-report status
python3 plugins/codex-usage-reports/scripts/usage_reports.py task "$TASK_ID" --format markdown
```

Set `TASK_ID` to the native ID of the Task you want to inspect. The report stays scoped to that Task. See [the full usage guide](docs/USAGE.md) for exporting HTML, configuration, and uninstalling.

## Reporting you can inspect

- **Honest missing data.** Counter resets, truncated records, and conflicting settings stay partial or unknown.
- **Historical settings.** Today's global model preference never replaces a past turn's observation.
- **Separate scopes.** Cached input is a subset of input; reasoning output is a subset of output. Child usage and account quota remain distinct.
- **Local files.** Native identifiers are hashed in report state. Private display names may appear in your local reports.
- **Lightweight hooks.** Reporting errors do not deny tools or stop the agent.
- **Localized cards.** English, Traditional Chinese, Simplified Chinese, Japanese, Korean, German, French, Spanish, and Portuguese; four README translations.

## Moving from Codex Run Budget

Both plugins are independent. If you keep the original for budget controls, disable its automatic report before enabling this one to avoid duplicate cards. No historical database migration is required. [Migration instructions](docs/MIGRATION.md).

## Limits

These are observed usage reports, **not invoices or exact charges**. Quota belongs to the account and cannot be allocated to a Task from token totals. Subagent attribution depends on available native lineage. Hook delivery and native schemas can change with Codex versions. The reporting plugin does not impose budgets or interrupt work.

Reports can reveal project names and usage patterns. Keep real reports local; use only synthetic fixtures in public issues. [Privacy and security](SECURITY.md).

## Contribute

See [CONTRIBUTING.md](CONTRIBUTING.md), [architecture](docs/ARCHITECTURE.md), and [validation](docs/VALIDATION.md). Small reproducible bug reports and native-schema compatibility fixes are welcome. Please never attach a real transcript or database.

MIT © EasyVibeCoding contributors. Independent community project; not affiliated with or endorsed by OpenAI. [Provenance](NOTICE.md).
