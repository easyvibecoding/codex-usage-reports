# How the numbers work

| Field | Meaning |
| --- | --- |
| Task cumulative | Latest observed native counter for the selected Task itself, across its turns. |
| This-turn delta | Selected Task's valid native turn counter; otherwise, a valid same-source current counter minus its turn baseline. |
| Descendant subtotal | Observed usage from verified descendant agents attributable to the selected interval. |
| Observed combined subtotal | Selected Task's turn usage plus available descendant usage; check coverage. |
| Input | Native input tokens, including cached input. |
| Cached input | Subset of input, not an extra chargeable token count. |
| Cache-read share | Cached input divided by observed input for the same displayed scope. Missing or zero input is unavailable, not 0%. |
| Output | Native output tokens, including reasoning output when represented that way. |
| Reasoning output | Subset of output; do not add it again to total. |
| Model / effort | Chronological settings observed in this turn. |
| Elapsed | Wall-clock turn time, including waiting; not model compute time. |
| Account quota | A native account observation at capture time, not Task consumption. |
| Recorded turn rows | Receipts written by this plugin; they are not a reconstruction of every historical native turn. |

## Task and descendant scope

A parent Task and each subagent have distinct native counters. A subagent's own
Task report uses its own counter and recorded turn receipts. A parent Task report
shows verified descendant usage separately; the descendant subtotal is not part
of the parent's native cumulative counter. A combined subtotal is an observed
sum for the stated interval, not a replacement for either native counter.

Descendant attribution requires native lineage and compatible usage evidence.
An agent's name, a shared session ID, a nearby timestamp, or a hook event alone
does not establish parentage or complete coverage. Missing, conflicting, or
bounded evidence leaves the descendant subtotal partial or unavailable. Where
lineage is verified, a short hashed `@` selector appears on the descendant row
and that subagent's own card and report so same-named agents can be matched.
The selector identifies a Task within the selected native catalog; it is not
a token counter or proof of complete usage.

Child receipt JSON uses `agent_turn_stop_boundary` for the initial observation
and `agent_turn_completion_boundary` for a later, explicitly completed revision.
The child receipt's `task.parent_hash` identifies verified lineage without turning
its usage into the parent's own counter.

Timing baselines retain the original Task role and hashes of the root and direct
parent. Preview, Stop and completion reconciliation require that original
identity; a current catalog lookup cannot replace missing historical evidence.
Child cards and receipts label their own usage and settings separately from
their descendants and show the verified direct parent.

Contradictory parent metadata for the same child revokes its saved request
evidence. A durable hashed revocation also excludes delayed and later matching
scans, including when no request was stored before the conflict. There is no
trusted generation signal to restore that identity automatically; a new child
identity is independent. Ordinary copied-parent metadata for different Tasks
remains valid. Revocation storage is bounded without dropping exclusion
evidence; unreadable or saturated state leaves coverage partial or unavailable,
never an observed zero. Reporting failures do not block tools or change budgets.

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
