# Contributing

Start with a reproducible issue or a focused pull request. Use synthetic native records and explain which observable behavior changes. Keep runtime dependencies in Python's standard library.

## Development

```sh
python3 scripts/build_hook_runtime.py
python3 -m unittest discover -s tests -v
ruff check plugins tests scripts
python3 scripts/validate_repo.py
python3 scripts/check_docs.py
```

Development tools such as Ruff and browser automation are optional tooling, not runtime dependencies. See [validation](docs/VALIDATION.md) for the release checks.

After changing source or assets packaged in the hook, rebuild the deterministic zipapp and include the updated hooks. Tests should exercise the public reporting seam and meaningful missing-data cases.

Before commit, stage only the intended files and run the sensitive-data gate. Enable the included pre-commit hook with `git config core.hooksPath .githooks`.

## Documentation and translations

The English README is the source document. Keep Traditional Chinese, Simplified Chinese, and Japanese in sync when commands or behavior change. Machine-readable names and status values remain untranslated. Run `python3 scripts/check_docs.py` to catch missing relative links and assets.

Regenerate screenshots from the synthetic example generator; do not publish a real Task. Include exact build commands in the pull request.

## Scope

This project owns reporting. Budget enforcement, cross-task workflow monitoring, hosted telemetry, and billing calculations belong in separate projects or explicit proposals.
