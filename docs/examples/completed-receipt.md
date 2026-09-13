> Synthetic example; all names, settings, and numbers are invented.

# Codex turn usage report

Example · Build a local reading list · Turn 111111111111

2026-01-01T10:40:00Z → 2026-01-01T10:42:22Z

| Item | Observation |
| --- | --- |
| Elapsed time (including waits) | 142.0 s |
| Task total tokens (parent) | 126,800 |
| Observed total tokens | 24,600 |
| This turn's added tokens (parent) | 18,400 |
| Subagent tokens | 6,200 |
| Subagent coverage | Subagents with usage records: 1/1 · Records observed |
| Input (including cached) | 22,140 |
| Cached input subset | 14,760 |
| Output (including reasoning) | 2,460 |
| Reasoning token subset | 1,230 |

## This turn's parent settings

example-model · Reasoning high
Shown in observed order for this turn; tokens are not allocated by setting.

Data status: boundary_counter_difference

### Account quota snapshot

Observed plan: Pro

| Window | Remaining | Since prior turn |
| --- | --- | --- |
| Codex · 7 d | 72% | -1 pp |
| Codex · 5 h | 88% | Display unchanged |

Compared with the prior turn&#x27;s card in this Task. Quota is shared across the account, includes other Tasks and may update late; this is not usage attributable to this turn.

Changes follow the source&#x27;s displayed precision. An unchanged display does not mean no usage.

## Limitations

- This is a user-turn Stop snapshot, not proof the whole Task is complete; another hook may continue it.
- Parent usage uses the native turn counter or boundary differences; subagents use saved per-request records in this window. Both may lag and are not quota percentages or billing.
- Counter resets or source changes remain unknown. Tail scans are bounded and cannot prove there were no unobserved resets.
- Input includes cached tokens and output includes reasoning; subsets are not added again. Observed zero does not mean a free turn.
- Only this Task and linked descendants are included. Request timestamps determine the window; subagent lifetime totals are not added again.
- Unfinished children, missing request records and scan limits remain incomplete. Reporting does not wait, force-stop or request continuation.
- Fixed local Python rendering calls no model, requests no continuation and edits neither the assistant's answer nor source conversations.
