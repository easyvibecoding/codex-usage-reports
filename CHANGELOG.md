# Changelog

## 0.6.0

- Show observed cached-input share in automatic receipts, preview cards, and selected Task reports.
- Add parent-only cache-read share to receipt and Task JSON; missing or zero input remains unavailable.
- Keep parent, child, and quota scopes separate; the percentage does not diagnose server-side cache misses or estimate savings.

## 0.5.0

- Add fixed publisher-trust hooks with signed runtime and CLI updates, enabled by default.
- Verify RSA-3072/SHA-256 release signatures, runtime digests and bootstrap compatibility; reject replayed releases.
- Pin each Task to its starting runtime, retain the previous version, and provide status, on/off, update and rollback controls.
- Preserve native hook trust across routine updates; new entry definitions still need one user review.
- Keep the independent hook-trust reminder and all existing reporting/governance policies.

## 0.4.0

- Add first-prompt update and native hook-trust notices with private deduplication,
  a bounded version cache, and separate unknown states.
- Keep a standalone reminder definition stable across main-runtime updates so it
  can explain CLI `codex` → `/hooks` reauthorization while changed hooks are skipped.
- Add `updates check` for manual read-back before hook trust; no auto-update,
  auto-trust, model calls, or policy changes.

## 0.3.0

- Add project-scoped exec activity lists, foreground change monitoring and an optional
  launcher with private start/exit receipts, including ephemeral runs.
- Add nonblocking, deduplicated hook notices at tool-return boundaries.
- Keep launcher attribution, native child lineage and usage scopes distinct;
  preserve unknown/partial observations and all existing budget decisions.


## 0.2.1 — 2026-09-13

- Fix false unobserved turn usage when native request totals and legacy token-count events use different historical baselines.
- Keep Task and turn totals from the same validated native request source; record `counter_source` and never subtract counters across sources.
- Preserve same-source resets, malformed records, source identity checks, and completion-boundary protections.

## 0.2.0 — 2026-09-13

- Add one bounded local completion worker after each reported Stop: up to eight scans with a 25-second retry deadline, without calling a model or continuing a Task.
- Reconcile only through the selected turn's explicit native `task_complete` boundary, preserving the original child attribution window and partial-data handling.
- Publish separate revision 2 JSON, HTML, and Markdown receipts; preserve all original Stop JSON/HTML/Markdown files and use the latest published revision in fresh selected-Task reports.
- Keep inline preview files immutable. A phone remote A/B experiment showed that a new reference read updated content while the original card retained its earlier snapshot after Task re-entry.
- Explain completion revisions, bounded retries, and inline refresh limits consistently in all four READMEs and troubleshooting documentation.

## 0.1.1 — 2026-09-13

- Make inline report text readable when a remote host omits or misbinds theme colors, using paired browser system colors and scoped typography.
- Add fragment-level Chromium and WebKit checks across four languages, phone/desktop widths, both themes, and three host color configurations.
- Refresh report screenshots and document the new-Task boundary for installed updates.

## 0.1.0 — 2026-09-13

- Extract automatic Task and turn reporting into an independent Codex plugin and Python CLI.
- Preserve native token observations, turn settings, child attribution, and optional account quota.
- Separate report state and pinned runtimes from Codex Run Budget.
- Add synthetic examples, Codex-generated brand art, four README languages, and installation documentation.
