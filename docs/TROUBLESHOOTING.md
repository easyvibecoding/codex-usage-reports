# Troubleshooting

| Symptom | Check |
| --- | --- |
| Card borders or elapsed time appear, but main text is blank | Update to 0.1.1 or later and begin a new Task. Older cards and Tasks can retain the old theme-dependent template. Browser coverage is documented in [validation](VALIDATION.md); actual iPhone remote rendering still needs device confirmation. |
| No card after an update, restart, or phone-created Task | In the Codex CLI, open `/hooks` and inspect this plugin. `modified` or `untrusted` hooks are skipped even if the plugin is enabled. Review and trust the changed definitions, then start a new Task. `auto-report status` only shows report settings; it does not verify hook trust. |
| No automatic card | Confirm the plugin is installed, hooks are trusted, and this is a new Task. Run `auto-report status`. |
| CLI report works, no inline visualization | The host must support local visualization references. Read the HTML/Markdown receipt instead. |
| `missing_start` / `no_active_start` | Preview needs a running turn's baseline; install before starting a new Task. |
| `below_threshold` | The turn has not exceeded the configured seconds. Use `auto-report threshold 0` for all durations. |
| `source_unavailable` or unknown tokens | Native state is missing, changed, bounded, or inconsistent. Do not substitute estimated values. |
| Invalid output directory | Use an absolute nonsymlink path under the Task's native visualization root or catalog workspace. |
| Two cards | Disable automatic reports in one of the two plugins. See [migration](MIGRATION.md). |
| Settings differ from current preferences | Reports describe historical observations; this may be expected. |
| Quota unavailable | The installed Codex build/account did not expose an accepted native observation. |
| Permission or SQLite errors | Verify ownership of the selected local data directory. Do not weaken permissions across the entire Codex home. |

## A useful bug report

Include the plugin version, Python version, Codex version, operating system, the command or event name, and a synthetic reproduction. Include status labels, not private report bodies.

Do not post transcripts, account tokens, native databases, Task IDs, private paths, or project names. See [SECURITY.md](../SECURITY.md). Reporting failures are designed to leave normal agent work running.
