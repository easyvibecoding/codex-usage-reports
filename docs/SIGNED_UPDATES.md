# Signed runtime updates

## What one-time trust means

Install the publisher-mode plugin, run CLI `codex`, then review its definitions
in `/hooks`. This is one initial migration from digest-per-version trust. The
embedded verifier, public key, hook events, and standalone trust reminder remain
identical for routine releases. Codex therefore sees the same native hashes.
The publisher can ship future signed Python program changes under that grant.
New events, changes to the verifier/key, or plugin and skill metadata still need
normal plugin installation and any changed-hook review. Disabling the plugin or
all applicable hooks prevents automatic checking. No native trust is granted by
this code and no model request or scheduled Task is created.

## Update lifecycle and controls

At an eligible `UserPromptSubmit`, a detached local Python worker checks at most
once every six hours (ten minutes after failure). A lease avoids duplicate
workers. The worker has a 20-second wall deadline and bounded HTTPS reads.
The foreground hook uses a previously verified runtime immediately; updates
become active for new Tasks. Existing Tasks keep their signed digest. The first
eligible hook in a new Task displays the verified runtime version; it can still
be the old version when a background download is in progress. New Tasks after
completion use the update. The latest 10,000 Task pins are retained; an evicted
old Task is selected as a new Task when revisited.

From the installed plugin directory, select one action:

```sh
python3 scripts/publisher_updates.py status
python3 scripts/publisher_updates.py off
python3 scripts/publisher_updates.py on
python3 scripts/publisher_updates.py update
python3 scripts/publisher_updates.py rollback
```

`--data-dir PATH` selects an explicit private store. `status` reports the active
and previous runtime versions, enabled state, last check and result. `off` also
prevents an in-flight automatic worker from activating a download; explicit
`update` still works. `CODEX_PLUGIN_AUTO_UPDATE=0` prevents scheduling, as does
`CODEX_USAGE_REPORTS_AUTO_UPDATE=0` for this plugin alone. Disabling update notices or
usage cards does not disable program updates. `rollback` retains the high-water
release sequence so the same rejected newer release is not reapplied. It affects
new Tasks and manual CLI calls; existing Task pins stay unchanged. No prior
verified runtime means rollback returns unavailable. Runtime files are retained
for Task continuity; this version does not automatically garbage-collect them.

The Plugins interface reports the installed package version, which can be older
than the active runtime. The normal CLI uses the signed active runtime after the
store has been initialized. `updates check` compares package versions and native
trust; `publisher_updates.py status` is authoritative for the runtime.

## Verification and failure behavior

The signed manifest binds plugin ID, stable channel, version, monotonically
increasing sequence, exact bootstrap contract, runtime byte count and SHA-256.
The embedded public key validates the entire canonical manifest. Duplicate keys,
unknown fields, wrong identities, oversized files, invalid signatures, corrupted
ZIPs and digest mismatches are rejected before activation or execution. The
verified archive is loaded from the checked bytes in memory. Cache manifests and
runtime bytes are checked again on every selection; no unauthenticated cache is
trusted. Symlink paths are rejected. A signed release for a different bootstrap
produces `requires_plugin_update` and a new-Task reminder to update and review.

Activation is transactional. Download/signature failure keeps the verified
current release. A corrupt active cache can fall back to the verified previous
release for a new Task. A pinned Task does not silently change versions on
failure. Existing product failure policy is preserved: reporting errors are
nonblocking; Run Budget unavailable-runtime admission checks remain fail-closed.
Network checks never run inside `Governor.handle` and make no policy decisions.

Local state is private `publisher-updates.sqlite3` plus content-addressed files
under `runtimes/` in the plugin's existing data directory. Native Task IDs are
hashed. Activation and Task pins are scoped by the reviewed bootstrap contract,
so a future entry migration cannot overwrite an old entry's active version. Requests carry no private data. Runtime updates do not rewrite plugin
caches, Codex configuration, native databases or hook trust grants.

## Publishing a compatible release

Keep `scripts/publisher_bootstrap.py`, `runtime/publisher.json`, and the embedded
standalone trust reminder unchanged for an ordinary runtime release. Rebuild
artifacts, sign with the maintainer key stored outside this repository, run all
repository checks, then publish the reviewed commit:

```sh
python3 scripts/build_hook_runtime.py
python3 scripts/publisher_release.py --key "$PUBLISHER_PRIVATE_KEY"
python3 scripts/publisher_release.py --check
python3 scripts/validate_repo.py
```

The signing script requires a higher sequence and an external private-key path.
CI needs only the committed public policy, runtime and signed manifest. Private
keys are never committed or required on a user's computer. Rotate a compromised
key with a normal plugin release and native hook review. An unavailable key
prevents signing new releases; back it up securely. A release manifest/runtime
race at the public endpoint is rejected and retried after the failure interval.

Legacy `runtime/hook.pyz` remains for pre-migration digest-pinned Tasks. It is not
the automatically selected signed runtime. Tests cover both legacy behavior and
the new publisher boundary with generated synthetic keys and payloads.
