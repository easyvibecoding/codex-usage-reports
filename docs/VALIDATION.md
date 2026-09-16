# Validation and compatibility

## 0.5.0 signed updates and native trust continuity

Validated on macOS, Python 3.12.8 and Codex CLI 0.154.0 on 2026-09-17.

- All 222 unit/integration tests passed, including independent OpenSSL signing,
  actual fixed-command execution of signed A/B runtimes, old Task pin retention,
  signature/payload/metadata rejection, anti-replay rollback, corrupt-cache
  fallback, disabled and in-flight updates, private hashed state, future entry
  migrations, bounded worker scheduling, and the packaged hook/CLI.
- Ruff, deterministic artifacts, signed manifest/runtime verification, plugin
  validation and the staged sensitive-data scan passed.
- A real native CLI canary installed `0.5.0+canary.A` in a disposable
  Codex home. Trust grants were seeded once in that isolated fixture only.
  The installed `UserPromptSubmit` command started its ordinary background worker,
  fetched the signed `0.5.0` runtime from the public GitHub `main`
  endpoint, and activated its exact published digest. No model call was made.
- A new synthetic Task's actual hook executed the published runtime. The older
  Task's verified pin remained on A. Native `hooks/list` reported all
  **8 hooks enabled and trusted**, with identical hashes before and after
  the automatic update. The entire isolated native config file was unchanged.
- A subsequent normal package reinstall from A to the published version also
  preserved all native hook hashes and trust states without another grant.

Published runtime SHA-256:
`9aa51aad7104c062ac1704d7ca8d2d28add2ac01b5c4a28450af43a46694cdd0`.

This proves real download/verification/activation, installed hook execution and
native trust classification. It is not a model-driven Desktop rendering test.
Migrating an existing pre-publisher installation still requires one review of the
new fixed entry. Future hook/key/entry changes require their own native review.
See [signed update controls and boundaries](SIGNED_UPDATES.md).


## 0.4.0 first-prompt update and trust notices

Validated on macOS, Python 3.12.8 and Codex CLI 0.154.0 on 2026-09-17.

- All 210 tests passed, including first-prompt/installation deduplication,
  cache expiry and failure backoff, unknown trust, disabled hooks, scoped plugin
  identity, private hashed state, bounded/reaped native RPC, independent embedded
  commands, CLI fallback, and verified TLS with the macOS system-CA fallback.
- Ruff, repository/runtime validation and Plugin Creator/skill validation passed.
- A fresh isolated Codex home installed 0.4.0; installed runtime and hook bytes
  matched source. Its actual installed CLI fetched the public GitHub manifest
  successfully and read native pending trust. No model request was made.
- A separate native upgrade canary installed synthetic versions 9.0.0 and 9.0.1
  using the real plugin CLI. Trust was seeded only in disposable isolated homes.
  `hooks/list` observed all 8 hooks trusted before the upgrade; afterwards the
  7 changed main hooks were `modified`, while the standalone reminder retained
  exactly the same native hash and remained `trusted` across cache-version paths.
- Invoking that installed reminder emitted both update and reauthorization
  notices, then no duplicate on the next prompt. After isolated re-trust, the
  trust warning disappeared. The cached-release/native-trust fixture took
  0.137 seconds; this is an observation, not a production latency guarantee.

Installed runtime SHA-256:
`d150ca31a6bde3222323818740d10f2f24ee85bafecd088542a3756034bd5d85`.

The test proves native trust classification and the actual handler's output. It
is not a model-driven Desktop rendering test, nor evidence that untrusted hooks
can execute. First installation of the reminder still requires user review.
The maintainer's active installation and hook grants were not changed.

## 0.3.0 project exec activity

Validated on macOS, Python 3.12.8 and Codex CLI 0.154.0 on 2026-09-16.

- All 198 unit/integration tests passed. New coverage uses synthetic catalogs
  and real local subprocesses for exact project scope, foreign metadata, symlinks,
  native/legacy counter separation, same-source resets, distinct resume launch
  attribution, private hashed state, hook notice deduplication, partial rendering,
  exit propagation, ambiguous JSON usage and nonblocking telemetry failures.
- Ruff, repository/runtime validation and Plugin Creator validation passed.
- Fresh isolated Codex homes installed the plugin from its local marketplace.
  The installed bootstrap and pinned runtime emitted one exec notice after a
  synthetic catalog insertion and none on repetition. The post-tool call took
  0.0820 seconds in this small fixture; this is not a production latency bound.
- Installed CLI list/render and source identity read-back passed. An installed
  launcher invoked the real Codex binary with `exec --help`, forwarded its output
  and recorded start/exit with status 0. This smoke made no model request.
- Read-only parsing of one selected real native exec session found its metadata
  and completion event. Parent ownership and missing usage stayed unknown. No
  real transcript, identifier, path or generated report is included here.

Installed runtime SHA-256:
`25b65703d01bb6b08dafcfab405f86e2a15fffe00e5fd7b19de9fdea1440641a`.

The hook test uses synthetic native state and actual installed hook commands; it
does not prove every Desktop host delivers or displays every hook event. Native
turn completion does not prove process exit. Only instrumented launches retain
receipts for ephemeral runs; no background service or shared-budget enforcement
was added. The maintainer's active plugin setup was not changed.


## 0.2.1 counter-source isolation

- 182 unit/integration tests passed locally. Coverage includes positive and
  negative offsets between native request and legacy event totals, malformed
  native records, true same-source resets, and a turn-counter decrease whose
  preceding record has fallen outside the bounded tail.
- A fresh isolated Codex home installed 0.2.1. Actual installed bootstrap and
  pinned-runtime commands rendered synthetic preview delta 600, settled Stop
  delta 800 in 0.0924 seconds, then reconciled delta 1,000 / Task total 2,000.
  Legacy event totals were independently offset. A next-turn event of 9,990,000
  was excluded, all original Stop files stayed byte-identical, and the installed
  CLI returned revision 2 with `counter_source=native_request`.
- Read-only inspection of the selected affected native turn confirmed separate
  monotonic counter streams with different historical baselines. The corrected
  snapshot returned the explicit native turn counter through `task_complete`.
  Historical settings remained partial; private records and user totals are
  excluded from this repository. This is parsing evidence, not a new phone UI
  or live hook-delivery test.

The installed runtime SHA-256 was
`1158a88d63c2c170d1785beec399303e316373debd89215d1c04197e84a8669f`.

## 0.2.0 completion reconciliation

Verified on macOS with Codex CLI 0.154.0 and Python 3.12.8:

- 173 unit/integration tests passed, including 14 completion-boundary and 18
  background-worker cases. Ruff, packaging validation, nine-locale parity,
  documentation checks and the Plugin Creator validator passed.
- A fresh isolated Codex home installed 0.2.0 from the local marketplace. Its
  real installed bootstrap commands handled start and Stop, launched the pinned
  background runtime, and returned Stop in 0.0864 seconds. Synthetic turn usage
  increased from 200 to 500 after an explicit completion event; a following
  turn's counter of 9,999,000 was excluded. The installed CLI read revision 2.
  All original Stop JSON, HTML and Markdown bytes remained unchanged.
- Independent fault injection delayed the child catalog beyond the deadline:
  the worker exited after 25.012 seconds as `expired`, with zero publication
  calls. A dedicated deadline exception bypasses ordinary collector error
  handlers. Concurrent edits and symlinks at the original report are preserved
  because completion publication never writes original Stop files.
- A read-only check of this installation's actual native completion record
  returned `completion_observed=true`. Its bounded tail remained partial; this
  check is not a complete accounting claim or a new live model conversation.

The installed runtime SHA-256 was
`ad0a6916d3e33861c90f6045a921a4dc85e21dee8a52601fd6e0260674338e52`.

### Phone same-path experiment

The user confirmed the first inline card displayed synthetic version A / 100.
After overwriting that same HTML path with B / 250, a new reference displayed B,
but the original card still displayed A after navigating away and returning.
One intermediate return also showed no card. This demonstrates readable updated
source with a fresh reference, not reliable refresh of an existing phone card.
The implementation therefore preserves inline snapshots and publishes a separate
completion revision selected by subsequent Task-report queries.

Screenshots and raw native records stay private and are not committed. These
findings apply to this tested host/session; they do not establish universal
mobile behavior. The maintainer's active plugin is separate from isolated tests.

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
