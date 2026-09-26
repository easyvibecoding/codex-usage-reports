<p align="center">
  <img src="docs/assets/hero.png" alt="Codex Usage Reports — Every task. Every turn." width="100%">
</p>

# Codex Usage Reports

**Local usage reports for each Codex Task and turn.** See observed token totals, turn deltas, model and reasoning settings, child-agent coverage, and account quota without treating them as one number.

[![CI](https://github.com/easyvibecoding/codex-usage-reports/actions/workflows/ci.yml/badge.svg)](https://github.com/easyvibecoding/codex-usage-reports/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-mintcream)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB)](pyproject.toml)

**English** · [繁體中文](docs/i18n/README.zh-TW.md) · [简体中文](docs/i18n/README.zh-CN.md) · [日本語](docs/i18n/README.ja.md)

[Quick start](#quick-start) · [What it reports](#what-it-reports) · [Examples](#examples) · [Documentation](#documentation)

## Quick start

Requires Python 3.10+ and a local Codex host with plugin hooks. Inline cards also require a desktop surface that supports local visualizations; saved reports work without one. See [compatibility](docs/VALIDATION.md#compatibility-boundaries).

1. Install the plugin from this repository's marketplace:

   ```sh
   codex plugin marketplace add easyvibecoding/codex-usage-reports
   codex plugin add codex-usage-reports@codex-usage-reports
   ```

2. In the Codex CLI, open `/hooks`, review and trust every changed or untrusted installed definition, including `SubagentStart`, then start a **new Task**. An enabled plugin with untrusted or modified hooks does not run those hooks. See the [plugin](https://learn.chatgpt.com/docs/plugins) and [hook](https://learn.chatgpt.com/docs/hooks) guides.
3. Work normally. The plugin is enabled by default. Supported lifecycle events can give the parent and each subagent their own pre-final card and local turn receipt. Ask the bundled `usage-report` skill: “Show the usage report for this Task.”

To inspect a selected Task directly from a checkout:

```sh
python3 plugins/codex-usage-reports/scripts/usage_reports.py auto-report status
python3 plugins/codex-usage-reports/scripts/usage_reports.py task "$TASK_ID" --format markdown
```

Set `TASK_ID` to the exact native Task ID. The [usage guide](docs/USAGE.md) covers HTML/JSON export, settings, and installed versus checkout commands.

Automatic card instructions apply only to the footer. If the final answer must be exact, JSON-only, code-only, or follow a final-answer schema, skip both the preview and its reference. File formats do not determine eligibility. Eligible parent and child replies append only their own unchanged reference on a separate line, never another agent's reference. See [automatic reports](docs/USAGE.md#automatic-reports).

An inherited parent visualization directory is automatically relocated to the subagent's own directory. If the sandbox denies that write, the same snapshot can use the child's workspace at `work/codex-usage-cards`. Existing cards stay unchanged; start a new Task to load the updated runtime.

## What it reports

| Scope | Observation |
| --- | --- |
| Selected Task | Its own latest observed cumulative token counter and this plugin's recorded turn receipts, whether parent or subagent. |
| Current turn | Native turn counter, or a valid difference between counters from the same source. |
| Descendant agents | Separate attributable subtotal with coverage status in a parent Task report; never silently added to its native counter. |
| Model context | Model and reasoning effort observed during each turn, including visible changes. |
| Cache | Cached input as part of input, plus cache-read share when the input denominator is observed and nonzero. |
| Account quota | Native account observation when available, separate from Task usage. |
| Receipts | Private HTML, Markdown, and JSON; a fresh Task query selects the latest published revision. |

A card is a **pre-final snapshot** for the selected Task. After a supported terminal event (`Stop` or `SubagentStop`), one bounded local worker can publish a separate completion revision when that Task and turn's native `task_complete` record appears. It preserves the original receipt files and never rewrites a card already shown in chat. A subagent's own counter and receipt stay distinct from the parent's verified descendant subtotal. Match a child in the parent report to its own card or report by the same `@` selector, even when agents share a name. Missing hooks, counters, lineage, or incomplete coverage remain unknown or partial. See [metrics](docs/METRICS.md) and [completion behavior](docs/ARCHITECTURE.md#completion-reconciliation).

The Python runtime uses the standard library. Reporting needs no API key, model call, or hosted analytics service. It does not calculate exact charges, enforce a budget, deny a tool, or stop work. This is the independent reporting extraction of [Codex Run Budget](https://github.com/easyvibecoding/codex-run-budget).

## Examples

![Synthetic automatic turn report with Task total and turn delta](docs/examples/turn-en.png)

*Rendered by the real report template from synthetic data. The desktop provides its own surrounding theme.*

[Open the example gallery](docs/examples/README.md) for localized cards, HTML, a completed receipt, and a selected-Task report. Brand illustrations were generated with Codex image generation; product screenshots use synthetic application output. [Artwork prompts](docs/assets/PROMPTS.md).

## Other capabilities

- **Project exec activity:** list extra `codex exec` sessions in one exact working directory, watch in the foreground, or opt into a launcher that records start, exit, and invocation usage even for ephemeral runs. Hook notices appear at tool-return boundaries. Launcher attribution, native child lineage, and parent usage remain separate. [Commands and coverage](docs/EXEC_ACTIVITY.md).
- **Signed runtime updates:** after an initial `/hooks` review, the fixed publisher entry can activate compatible signed runtime and CLI updates for new Tasks while existing Tasks stay pinned. Check, disable, manually update, or roll back with `scripts/publisher_updates.py`; entry, key, hook, or plugin structure changes still need normal plugin review. [Trust and controls](docs/SIGNED_UPDATES.md).
- **Update and trust read-back:** `updates check` reports installed package version and native hook trust independently. The standalone reminder can report changed hooks on a prompt; its scope differs from the active signed runtime version. [Notice behavior](docs/UPDATE_NOTICES.md).
- **Localized reports:** cards and saved human reports support English, Traditional and Simplified Chinese, Japanese, Korean, German, French, Spanish, and Portuguese. The README has four language versions. [Language selection](docs/USAGE.md#language).

## Paired project change review

Codex Run Budget offers an **experimental, optional** cross-project review feature. Its users choose exact local Codex project roots, register named pairs, and enable both a global switch and each desired pair. A project can belong to multiple pairs. For this repository's pair, an enabled project-scoped `Stop` hook can request a read-only review Task when it observes a new remote `main` range. It binds the two Tasks one-to-one within that pair and relays later Stop summaries to the same counterpart, with an echo guard. The [specific review contract](docs/CROSS_REPO_REVIEW.md) requires an evidenced alignment decision. Run Budget owns the pairing and optional manual remote scan; Usage Reports remains independent and report-only. [Configuration guide](https://github.com/easyvibecoding/codex-run-budget/blob/main/docs/PAIRED_REVIEW_AUTOMATION.md).

## Documentation

The [documentation index](docs/README.md) organizes the user guides, feature references, troubleshooting, security, and maintainer material. Start with [usage and CLI](docs/USAGE.md), [numbers and status](docs/METRICS.md), or [troubleshooting](docs/TROUBLESHOOTING.md).

If you also use Codex Run Budget, [move only automatic reporting](docs/MIGRATION.md) to avoid duplicate cards. The two plugins keep independent data and controls.

Reports can expose project names and usage patterns. Keep real reports local and use synthetic fixtures in public issues. [Privacy and security](SECURITY.md). See [CONTRIBUTING.md](CONTRIBUTING.md) for development and validation.

MIT © EasyVibeCoding contributors. Independent community project; not affiliated with or endorsed by OpenAI. [Provenance](NOTICE.md).
