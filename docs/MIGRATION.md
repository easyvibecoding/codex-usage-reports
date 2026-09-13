# Move reporting out of Codex Run Budget

Codex Usage Reports is an independently installable extraction. It has its own package, plugin, configuration, pinned runtimes, and data directory. It does not import or install the budget governor.

| | Codex Run Budget | Codex Usage Reports |
| --- | --- | --- |
| Purpose | Shared budget controls and reporting | Automatic Task and turn reports |
| Data | `~/.codex/run-budget` | `~/.codex/usage-reports` |
| Budget enforcement | Remains in the original | Not included |
| Report history | Remains in its existing directory | New receipts start after installation |

## Keep budget controls, switch reports

From your existing Codex Run Budget checkout:

```sh
python3 plugins/codex-run-budget/scripts/run_budget.py auto-report off
```

Then install the new plugin following the [usage guide](USAGE.md), trust its hooks, and start a new Task. To find a different installed command path, consult the original plugin's README. Do not edit the original SQLite files.

## Roll back

Turn off the new reports with its `auto-report off` command, remove the new plugin if desired, and turn the original plugin's reports back on using `auto-report on`. Begin a new Task to pick up hook changes.

No database migration is needed or performed. Old receipts remain readable where they were saved. This release does not remove reporting code from the original repository or change an existing user's installation automatically.
