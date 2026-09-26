# Validation and compatibility

This file preserves dated release checks. The current package version is
declared in [pyproject.toml](../pyproject.toml) and the plugin manifest; see the
[changelog](../CHANGELOG.md) for changes after a dated check. Historical
canaries do not prove the status of a current installation. The commands near
the end reproduce the repository checks on the current checkout.


## 0.7.3 subagent visualization output — 2026-09-26

The compatible signed runtime has sequence `1790433141` and SHA-256
`d13fa3a736920411badd3af95cdab3d38a453efdeab2ab6ac78dc0752f050d32`.
Ruff, all 260 tests, repository and plugin validation, the 25-file documentation
check, reproducible runtime checks and signature verification passed. The hook
definitions and publisher bootstrap remain unchanged.

The preview tests exercise verified nested-child relocation across UUIDv7 UTC
dates, preserved relative subdirectories and parent snapshots, unchanged direct
child/workspace output, and rejection of foreign roots, traversal, symlinks and
unproven lineage. Relocation-only `PermissionError` cases cover mkdir and file
creation, a single checked child-workspace fallback, the same collected snapshot
and filename, and no overwrite. Source mutations during rendering or the denied
native write are rejected before either destination is written. Independent
review reproduced these two source-recheck gaps before the final fix and verified
both regressions afterward; ordinary `OSError` does not trigger fallback.

A disposable Codex home installed version 0.7.3. Its manifest, hook definitions,
both runtime archives, release manifest and publisher policy matched the
checkout byte-for-byte. All 31 preview tests passed with package modules loaded
from the installed signed archive. Invoking the installed `SubagentStart` command
against a synthetic native catalog/transcript and then its durable preview
command produced the child's own dated path. A real OS permission denial
(`EACCES`, directory mode 0500) produced the checked workspace fallback; the
fixture's original permissions were restored. Both cards were mode 0600, kept
the observed child counters, and left the parent sentinel unchanged.

An offline harness executed the unmodified reader, schema and path-checking
functions extracted from local Codex Desktop 26.924.22138 (build 11645), with
minimal host adapters and real filesystem I/O. It accepted the installed
runtime's child-root and workspace-fallback files and rejected the parent
sentinel under the same child ID, with the workspace feature flag both enabled
and disabled. The six direct-read checks preserved file hashes and mtimes.
This establishes output-path compatibility for that installed reader; it is
not native GUI painting or model-driven lifecycle verification. Existing Task
runtime pins and previously emitted snapshots remain unchanged.

## 0.7.2 footer eligibility and reference alignment — 2026-09-26

Aligned the reporting interface reviewed from Run Budget source range
`b5bf01e739c4f11842c843333ca8793fb6573976..f3ffb509f8f07109360921d48a640f3203035438`,
including the preceding footer delimiters. Usage Reports keeps its own branding,
report-only implementation, native ownership and release trust state.

Ruff, all 249 unit/integration tests, repository validation, the 25-file
documentation check and signed-release verification passed. The deterministic
runtime has sequence `1790406877` and SHA-256
`e1ab602a0137fbc0bebf29ec8ce3606255303156197765a11f8c416ad5be32c0`.
Tests cover eligibility before the preview command, parent/child command ownership,
own-reference wording and compact JSON round-trips for Unicode, spaces, quotes,
backslashes and control characters.

A disposable Codex home installed this checkout as version 0.7.2. Installed
runtime, release manifest and hook bytes matched the checkout. Six focused tests
loaded the installed signed zipapp and passed; its actual CLI read back the
reporting settings. Hook definitions remained byte-identical to 0.7.1. The
maintainer's active installation and native hook grants were not changed.

These are deterministic code, synthetic lifecycle and isolated installation
checks. No Usage Reports model-driven conversation or Desktop painting canary
was run for this change. Run Budget's six-model high-effort matrix is source-only
evidence; its earlier failures and unknown results are not replaced or imported
as Usage Reports validation. Existing Tasks may retain an older pinned runtime
and previously injected instructions.

## 0.7.1 child identity, cache revocation and labels

Checked on 2026-09-26. Ruff, all 248 tests, repository and plugin validation,
the 25-file documentation check and signed-release verification passed.
The final signed runtime has sequence `1790392814` and SHA-256
`021c5b2fc5176b23290fa97ca0b97e41ba595c3bc4686922d6d9e3971ec26253`.

Real SQLite regressions cover delayed request writes after revocation, conflicts
before any cached request, reopen/source removal, collection interleavings,
legacy schemas, bounded saturation and a broken revocation schema. Independent
checks inserted an otherwise valid request as an older runtime would: the new
collector still excluded the revoked child. Original Task/root/direct-parent
and role checks reject drift and contradictory explicit roles at preview, Stop
and completion; supported legacy baselines remain usable. Failed completion
checks leave the original Stop files unchanged. Nine-language rendering tests
cover child labels, selectors and escaped parent names, with unchanged root text.

A fresh disposable Codex home installed the 0.7.1 child-report candidate before
the final connection-only cleanup described below. All nine hook
definitions matched 0.7.0; only the isolated fixture received its previously
reviewed hashes. Native read-back showed nine enabled/trusted hooks. A real
`codex exec` spawned one child and exited successfully. Both emitted preview
references; the child's card used child-specific usage/settings labels and
showed its direct parent. Two Stop receipts and two completion revisions were
written. The child baseline's original root/direct-parent hashes matched native
lineage, and its selector matched the parent descendant row. The child observed
52,300 own-turn tokens; the parent observed 61,640 of its own and 52,300 separately
as descendant usage. Receipts contained no raw native Task UUIDs. Temporary
authentication material was removed after the run.

CI identified Python 3.13 ResourceWarnings from unclosed SQLite fixture handles
and the readonly quota timing index. Both were closed explicitly without
suppressing warnings or weakening CLI stderr assertions. The final complete
Python 3.13 suite passed with tracemalloc and ResourceWarnings enabled, with
zero warnings. A second disposable home installed the exact final runtime,
with nine trusted hooks. Five tests loaded from that installed signed zipapp
passed, covering revocation races and quota connection lifetime. The final
runtime also accepted the canary's actual parent/child baselines and rendered
the child labels, selector and parent correctly from that evidence, without
another model request.

The active user installation and hook grants were not changed by this check.
This proves isolated CLI hook execution and generated child-card content;
Desktop painting remains unobserved. Token totals are canary observations,
not plugin overhead or account billing.

## 0.7.0 child-agent hook and isolated native canary

Checked on 2026-09-26 with Codex CLI 0.157.1. Ruff, 229 tests, repository
validation, the 25-file documentation check, staged sensitive-data scan, and
signed-release verification passed. Runtime SHA-256:
`05396ebd97e3e7a30d5525330f0f3ad2b13726f8d67b6b2c493c02019d0d6c14`.

A fresh disposable `CODEX_HOME` installed this checkout's local marketplace and
plugin 0.7.0, with report data in a separate disposable directory. Native
`hooks/list` first reported nine enabled hooks needing review. Each installed
command matched the packaged hook definition; exact current hashes were trusted
only in this isolated fixture, after which all nine read back enabled and trusted.
This does not grant trust to the maintainer's disabled installation. That
installation needs its normal plugin update and `/hooks` review if selected.

One real `codex exec` turn spawned one subagent and exited successfully. The
installed hooks produced one parent and one child Stop receipt, plus completion
revisions. The child used `agent_turn_stop_boundary`; its 12-character hashed
selector matched the parent's child row. Its observed total was 17,329 tokens;
the parent recorded 46,378 of its own and the same 17,329 separately as
descendant usage. Neither receipt contained a raw Task UUID or `agent_path`.
This CLI run did not produce an inline visualization file, so it verifies native
hook execution and report correspondence, not Desktop card painting or the
original screenshot's renderer failure.

## 0.6.0 documentation and installation read-back

Checked on 2026-09-24 in this checkout. Ruff, all 223 unit tests, repository
validation, the 24-file documentation link check, and the staged sensitive-data
scan passed. A fresh isolated
`CODEX_HOME` installed this checkout's local marketplace and plugin 0.6.0.
The installed CLI reported 0.6.0, and its signed-runtime status reported an
active verified 0.6.0 runtime. No hooks were trusted in that disposable home;
an offline native trust query returned `unknown`. This check verifies package
installation and local CLI selection, not hook execution or Desktop rendering.

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
