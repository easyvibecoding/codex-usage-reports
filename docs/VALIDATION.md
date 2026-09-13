# Validation and compatibility

## Release environment

The 0.1.0 extraction was verified on macOS with Codex CLI 0.154.0 and Python 3.12.8.
The automated CI matrix targets Python 3.10, 3.11, 3.12, and 3.13 on Linux.
[Current CI runs](https://github.com/easyvibecoding/codex-usage-reports/actions/workflows/ci.yml).

| Check | Evidence |
| --- | --- |
| Regression suite | 141 unit/integration tests passed locally, including foreign-Task start/Stop isolation, counter resets, partial contexts, child coverage, and nonblocking runtime failure. |
| Standalone installation | A fresh isolated Codex home accepted marketplace registration and `codex plugin add codex-usage-reports@codex-usage-reports`. |
| Installed hook lifecycle | The installed hook commands received a synthetic native catalog/transcript: start, preview, and Stop produced a local card and settled receipt. Task counter 1,600; turn delta 600. |
| Installed CLI read-back | The installed CLI read the receipt and returned one recorded turn, cumulative 1,600 and delta 600. |
| Real native catalog | A read-only selected-Task query against the current local Codex catalog returned `usage_status=observed` and a native counter. Private output stayed outside Git. |
| Python packaging | A clean virtual environment built and installed the wheel with no runtime dependencies; its console command returned report settings. |
| Public examples | Actual renderers use synthetic fixtures only. |
| Browser checks | Four locales × two widths (390, 900) × two themes = 16 cases. No horizontal overflow or page errors; each disclosure toggled with the keyboard. |
| Privacy | Staged-index and reachable-history scans run in CI, including zipapp members. |

The installed hook test invokes real installed hook commands with synthetic native data. It does **not** establish that every Codex host delivers every event. The real catalog query checks current native parsing, not an end-to-end model conversation. Hook trust, native schema availability, and inline rendering remain host-dependent.

## 0.1.1 remote-card rendering fix

The earlier documentation frame supplied its own theme variables, masking the
report fragment's dependency on host foreground colors. A reproduction with an
inverted foreground makes the old template's title, totals, and disclosures
match the background (contrast 1:1), while muted elapsed text remains visible.
This matches the reported symptom; it does not identify the iPhone host's
internal implementation.

The updated fragment uses paired browser `CanvasText`/`Canvas` colors and scoped
text styles. Playwright 1.62.1 exercised Chromium and WebKit across four locales,
390/900 px widths, light/dark schemes, and missing/normal/inverted host tokens:
96 cases passed, with no horizontal overflow or page errors, readable text
(contrast at least 4.5:1), and keyboard-operable native disclosures. Both
collapsed and expanded states were checked. WebKit screenshots were inspected.

These are browser tests, not an end-to-end test on the iPhone remote app.
Existing artifacts do not refresh after installation, and existing Tasks can
keep their pinned runtime; start a new Task to pick up the corrected renderer.

To reproduce (optional development dependencies only):

```sh
npm install --no-save --package-lock=false playwright@1.62.1
npx playwright install chromium webkit
python3 scripts/generate_examples.py
node scripts/check_mobile_card.cjs
```

Use `BROWSER_CHANNEL=chrome` for an existing Chrome installation. CI runs this
fragment matrix separately from the framed screenshot checks.

## Post-update hook trust

A post-update phone-created Task completed without starting an automatic report.
Native `hooks/list` reported all 11 upstream Run Budget hooks as enabled but
`modified`, with no discovery errors. This is a separate condition from card
rendering: the installed hook definitions require a new trust review before
execution. Installation checks and renderer checks alone do not prove that the
host will execute an updated hook. The installed update changed only the runtime
version and card template; changing the pinned runtime digest still changes the
hook definition hash.

Use the Codex CLI `/hooks` review flow after updates. Do not infer trust from
`codex plugin list`, and do not replace a trust review with a bypass flag or
manual trust-hash edits. See the [official hook trust rules](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks).

## Reproduce the source checks

```sh
ruff check plugins tests scripts
python3 -m unittest discover -s tests -v
python3 scripts/build_hook_runtime.py --check
python3 scripts/validate_repo.py
python3 scripts/check_docs.py
python3 scripts/check_sensitive_data.py --index --fail-on-findings
python3 scripts/check_sensitive_data.py --history --fail-on-findings
```

For manifests, also use Codex's Plugin Creator validator. Rebuild with
`python3 scripts/build_hook_runtime.py` after packaged source changes.

## Reproduce the examples

```sh
python3 scripts/generate_examples.py
node scripts/capture_examples.cjs
```

The screenshot step requires the optional Playwright development tool and a
Chromium browser. [Details](examples/README.md).

## Compatibility boundaries

- Local Codex catalog and rollout formats are implementation details, not a stable public reporting protocol.
- Python runtime minimum: 3.10. macOS is the locally exercised host; Linux receives CI coverage.
- Native Windows plugin hook installation is not verified in this release. POSIX file-safety primitives are used by the runtime; Windows users should not assume parity from Python installation alone.
- A compatible desktop visualization surface is required for inline cards. Saved Markdown and HTML are available independently.
- No reporting API key is needed. Optional quota reads use the user's existing Codex account context and can be unavailable.
- Existing Tasks can retain pinned hooks after a plugin update. Begin a new Task for the new installation.
