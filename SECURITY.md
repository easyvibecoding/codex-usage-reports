# Privacy and security

## Local data

The reporter reads the selected Task's native catalog metadata and bounded usage records. It can also read related child-agent usage and native account quota through the installed Codex interface. The reporter has no hosted analytics endpoint and no direct LLM or API-key dependency. Codex itself may contact its services when fulfilling a native quota request.

The report store contains hashed identity keys, timing baselines, counters, states, and reports. Private local reports may display native Task names and agent nicknames. Prompts, tool arguments, tool output, and transcript text must not be copied into reports or public artifacts. Display names are escaped and treated as data.

Project exec activity reads bounded native catalog metadata. Its optional
launcher saves hashed identities, lifecycle and usage observations, not prompt
or stream contents. The launcher executes the `codex exec` command supplied by
the user, so that invocation has Codex's ordinary authentication and network
behavior. [Activity scope](docs/EXEC_ACTIVITY.md).

This does not anonymize every report: project names, times, models, and usage patterns can be sensitive. Keep generated reports outside Git. The repository includes only synthetic examples.

In publisher mode, the separate first-prompt reminder reads native hook trust;
manual `updates check` can fetch this plugin's public package manifest with a
six-hour cache. The signed runtime worker separately downloads public signed
metadata and archive bytes. Neither request sends a Task ID, project path,
prompt, or usage data. The reminder does not execute newly installed plugin
code or change native trust. Set `CODEX_PLUGIN_UPDATE_NOTICES=0` to disable
automatic reminders; signed update controls are separate. See
[update notices](docs/UPDATE_NOTICES.md) and [signed updates](docs/SIGNED_UPDATES.md).

## Boundaries

Reporting hooks do not deny tools, apply budgets, or stop the agent. Corrupt data should produce unavailable/partial evidence or a nonblocking reporting failure. Output directory validation, restrictive file modes, bounded reads, identifier hashing, and SHA-256-pinned runtimes reduce accidental exposure and cache drift.

Local users or tools that already have access to your Codex data retain that access. This project is not a security boundary against a compromised operating system.

## Report a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/easyvibecoding/codex-usage-reports/security/advisories/new) when enabled. Do not publish a credential or real transcript in an issue. Provide a synthetic reproduction and describe the impact.

The supported security target is the latest release. Compatibility with new native Codex schemas is tracked through tests and the validation document.

## Maintainer checks

Before committing, scan the staged index with:

```sh
python3 scripts/check_sensitive_data.py --index --fail-on-findings
```

CI scans both the index and reachable history, including embedded zipapp members. The scanner is a focused deterministic check; human review is still required for screenshots, names, and context it cannot classify.

## Signed runtime publisher trust

The fixed hook entry embeds this plugin's RSA-3072 public key and verifies exact
PKCS#1 v1.5 SHA-256 signatures using Python's standard library. Trusting it permits
future signed program changes, including program behavior changes, from that
publisher. It does not promise review of every future runtime by Codex. Key or
entry changes require new native hook trust; the updater never writes trusted
hashes. Keep the private key outside Git and back it up securely. A compromised
publisher key can authorize code; `off` stops automatic updates and `rollback`
selects the previous verified runtime for new Tasks and CLI calls.

A bounded local worker downloads only public signed metadata and runtime bytes
from this repository's fixed GitHub URL, with TLS verification enabled. No Task,
project, prompt, credential or usage data is included. Stored Task identities are
hashed; file permissions are private. Signature/digest failure keeps the current
verified release. See [signed updates](docs/SIGNED_UPDATES.md) for limits.
