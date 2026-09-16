# Update and hook-trust notices

The first eligible `UserPromptSubmit` in each Task checks this plugin's version
and native hook trust. A changed installation triggers another check on the next
prompt, including in an existing Task. Notices appear only when a newer stable
version is observed or this plugin has enabled hooks awaiting trust. They never
block a prompt, deny a tool, change budgets, install updates, or grant trust.

## Your update workflow

1. Review an available update and update the plugin in Codex's Plugins interface.
2. Open a terminal and run `codex` in the project you use.
3. Enter `/hooks`, review the changed definitions, and trust the updated hooks
   (the CLI's trust-all action can be used after reviewing that list).
4. Start a new Task if the existing host session still retains older hooks.

Installing or enabling a plugin is separate from trusting its hooks. Native
`untrusted` and `modified` hooks are skipped. See the official
[hook trust guide](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks).

## Why the reminder can survive an update

A second `UserPromptSubmit` handler embeds a small standalone checker directly
in its command. Its definition contains no release number, installed path, or
changing runtime digest, so routine main-runtime updates preserve its native
trust hash. The checker reads metadata and calls `codex app-server --stdio` for
`hooks/list`; it does not execute plugin files, downloaded code, or other hooks.
The normal hooks retain their own SHA-256-pinned runtime and trust review.

**The reminder itself must be trusted once when it is first installed.** An older
installation without this handler cannot announce its own upgrade. If a future
release changes the reminder's definition, it too needs review before it can
run again. Disabled plugins/hooks cannot notify. Codex's CLI startup warning and
`/hooks` remain the fallback. No background monitoring or model run is created.

## Manual check

From the selected installed plugin directory:

```sh
python3 scripts/usage_reports.py updates check --refresh --cwd "$PROJECT_DIR"
python3 scripts/usage_reports.py updates check --offline --cwd "$PROJECT_DIR"
```

Replace `PROJECT_DIR` with the project whose effective hook configuration you
want to inspect. Run from the installed plugin to inspect its installed version;
running a source checkout reports the checkout's version. Native trust is always
queried for the selected project and Codex home. The manual check works even
before the reminder hook is trusted. `--refresh` bypasses the version cache;
`--offline` avoids the public version request but still reads local native trust.

JSON separates `release_status` (`update_available`, `current`, `ahead`, or
`unknown`) from `hooks.status` (`needs_review`, `trusted`, `disabled`, or
`unknown`). A network or native RPC failure is unknown, never proof of the latest
version or successful authorization. Stable versions ignore build metadata;
prerelease versions are not guessed. Deliberately disabled hooks are not reported
as requiring trust. Messages contain fixed English and Traditional Chinese text.

## Cost and privacy

Version checks fetch only this project's public plugin manifest from GitHub's
`main` branch, with a six-hour success cache and ten-minute failure backoff.
This is the published marketplace version, not a GitHub release-tag lookup.
The HTTP request includes no Task ID, workspace path, prompt, transcript, or
usage data. TLS verification stays enabled; macOS Python installations missing
their default CA bundle can use the OS-maintained `/etc/ssl/cert.pem`. Native trust reads have a short deadline; the entire hook is capped
at three seconds. No model calls are made. Subsequent prompts in the same Task
skip both checks until the installation changes; use the manual command to
refresh sooner. This is not an idle-app notification service.

The private `update-notices.sqlite3` in the plugin's existing data directory
stores hashed Task/installation identities, timestamps, and public versions.
It retains the latest 4096 Task checks; revisiting an evicted Task may check again.
Both plugins keep independent state and do not depend on each other.

Set `CODEX_PLUGIN_UPDATE_NOTICES=0` to disable both automatic checkers, or
`CODEX_USAGE_REPORTS_UPDATE_NOTICES=0` for this plugin alone.
Disabling automatic usage cards does not disable update notices. The manual
command remains available. No Codex trust or account configuration is edited.
