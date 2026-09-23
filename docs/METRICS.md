# How the numbers work

| Field | Meaning |
| --- | --- |
| Task cumulative | Latest observed parent Task counter, across its turns. |
| This-turn delta | Valid native turn counter; otherwise, a valid same-source current counter minus the turn baseline. |
| Child subtotal | Observed child usage attributable to the selected interval. |
| Observed combined subtotal | Parent turn usage plus available child usage; check coverage. |
| Input | Native input tokens, including cached input. |
| Cached input | Subset of input, not an extra chargeable token count. |
| Cache-read share | Cached input divided by observed input for the same displayed scope. Missing or zero input is unavailable, not 0%. |
| Output | Native output tokens, including reasoning output when represented that way. |
| Reasoning output | Subset of output; do not add it again to total. |
| Model / effort | Chronological settings observed in this turn. |
| Elapsed | Wall-clock turn time, including waiting; not model compute time. |
| Account quota | A native account observation at capture time, not Task consumption. |

## Read the status as well as the number

Native request records (`token_usage_record`) and legacy `token_count` events
can use different historical baselines. Reports prefer a validated request
record for the selected Task and turn, keeping its thread and turn totals
together. Resets are checked within each source, never between alternating
sources. `counter_source` identifies the selected lane in report JSON.
If only legacy events are available, boundary subtraction requires the same
source on both sides. A source change without a valid native turn counter stays
unavailable (`counter_source_changed`). A valid native turn counter does not
require subtracting a baseline, including a baseline written by an older plugin.

A missing value is not zero. Resets, malformed or bounded transcript tails, conflicting timestamps, and missing lineage can prevent a trustworthy delta. The report preserves partial or unavailable status instead of reconstructing unsupported precision.

A first-turn baseline can only be zero when native evidence establishes that it is the first turn. A continuing Task with a missing baseline is not treated as a new Task.

The pre-final card excludes work that happens after capture, potentially including the final response itself. The terminal receipt is a different observation. Neither is a billing statement.

## Quota

The collapsed seven-day summary only uses an unambiguous native main Codex row with `bucket == "codex"` and `duration_minutes == 10080`. Other windows may appear in details. Missing native quota remains unavailable. Token totals cannot establish a per-model or per-Task allocation of shared quota.

## Settings

Settings come from native turn observations and configuration updates. A current account preference does not prove a previous turn's model. Multiple observations can be shown when settings changed; ambiguous observations stay partial. Fast mode is omitted because reliable per-turn evidence is not assumed.
