# Troubleshooting

| Symptom | Check |
| --- | --- |
| Card borders or elapsed time appear, but main text is blank | Update to 0.1.1 or later and begin a new Task. Older cards and Tasks can retain the old theme-dependent template. Browser coverage is documented in [validation](VALIDATION.md); actual iPhone remote rendering still needs device confirmation. |
| No card after an update, restart, or phone-created Task | In the Codex CLI, open `/hooks` and inspect this plugin. `modified` or `untrusted` hooks are skipped even if the plugin is enabled. Review and trust the changed definitions, then start a new Task. `auto-report status` only shows report settings; it does not verify hook trust. |
| No automatic card | Confirm the plugin is installed, hooks are trusted, and this is a new Task. Run `auto-report status`. |
| CLI report works, no inline visualization | The host must support local visualization references. Read the HTML/Markdown receipt instead. |
| The inline card still shows earlier usage after re-entering a Task | Cards are immutable pre-final snapshots. In the phone remote A/B experiment, the old card retained A after the same file was changed to B; a new reference displayed B. Re-entering a Task is not a supported refresh mechanism. Request a fresh Task report or open the `.reconciled.html` / `.reconciled.md` receipt. |
| The revised report differs from the inline card or original Stop report | A completion revision can include usage written after Stop. Look for `revision: 2`, `reconciliation_status`, and `reconciled_at` in the revised JSON. Original Stop JSON, HTML, and Markdown receipts and inline snapshots are preserved. A fresh Task-report query selects the latest published revision. |
| No completion revision appears | Reconciliation runs once per reported Stop, only while automatic reports are enabled. It checks at most eight times with a 25-second retry deadline and requires that exact turn's native `task_complete` record. Missing completion, unavailable source data, or worker failure leaves the provisional Stop receipt intact. It does not keep retrying indefinitely. |
| A completion revision still says partial | A completion boundary establishes where to stop reading; it does not repair reset counters, truncated observations, or missing child coverage. Do not replace these states with estimated totals. |
| The original Markdown link did not change | This is expected: reconciliation preserves all original Stop files and does not rewrite their links. Request a fresh Task report for the latest published revision, or inspect the separate `.reconciled.md` receipt. |
| `missing_start` / `no_active_start` | Preview needs a running turn's baseline; install before starting a new Task. |
| `below_threshold` | The turn has not exceeded the configured seconds. Use `auto-report threshold 0` for all durations. |
| `source_unavailable` or unknown tokens | Native state is missing, changed, bounded, or inconsistent. Do not substitute estimated values. |
| Task total appears but this-turn tokens say unobserved after an update | Update to 0.2.1 or later. Earlier versions could mix native request and legacy event totals with different baselines and report a false reset. The fix keeps sources separate; true resets and invalid native counters remain unavailable. Trust the updated hooks and start a new Task; old inline cards remain snapshots. |
| Invalid output directory | Use an absolute nonsymlink path under the Task's native visualization root or catalog workspace. |
| Two cards | Disable automatic reports in one of the two plugins. See [migration](MIGRATION.md). |
| Settings differ from current preferences | Reports describe historical observations; this may be expected. |
| Quota unavailable | The installed Codex build/account did not expose an accepted native observation. |
| Permission or SQLite errors | Verify ownership of the selected local data directory. Do not weaken permissions across the entire Codex home. |

Completion reconciliation is a local Python process, not an automation that wakes
the model. It neither sends a new message nor resumes a Task. Turning off automatic
reports prevents new workers, and running workers stop at their next settings
check. See [architecture](ARCHITECTURE.md#completion-reconciliation) for the
completion boundary and receipt-preservation rules.

## A useful bug report

Include the plugin version, Python version, Codex version, operating system, the command or event name, and a synthetic reproduction. Include status labels, not private report bodies.

Do not post transcripts, account tokens, native databases, Task IDs, private paths, or project names. See [SECURITY.md](../SECURITY.md). Reporting failures are designed to leave normal agent work running.
