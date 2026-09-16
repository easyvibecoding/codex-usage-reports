# Privacy and security

## Local data

The reporter reads the selected Task's native catalog metadata and bounded usage records. It can also read related child-agent usage and native account quota through the installed Codex interface. The reporter has no hosted analytics endpoint and no direct LLM or API-key dependency. Codex itself may contact its services when fulfilling a native quota request.

The report store contains hashed identity keys, timing baselines, counters, states, and reports. Private local reports may display native Task names and agent nicknames. Prompts, tool arguments, tool output, and transcript text must not be copied into reports or public artifacts. Display names are escaped and treated as data.

This does not anonymize every report: project names, times, models, and usage patterns can be sensitive. Keep generated reports outside Git. The repository includes only synthetic examples.

The separate first-prompt update checker fetches only this plugin's public
GitHub manifest, with a six-hour cache. No Task ID, project path, prompt, or usage
is sent. Its embedded command does not execute newly installed plugin code and
never changes native hook trust. Set `CODEX_PLUGIN_UPDATE_NOTICES=0` to disable
automatic checks. See [update notices](docs/UPDATE_NOTICES.md) for scope and limits.

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
