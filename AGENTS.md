# Repository guidance

This repository contains the independent Codex Usage Reports plugin and CLI.

- Python 3.10+ standard-library runtime; no dependency on Codex Run Budget.
- Keep reporting errors nonblocking. Never emit tool-deny, budget, HALT, or STEER decisions.
- Preserve observed settings, counter-reset handling, partial states, and separate parent/child/quota scopes.
- Never substitute current model settings for historical observations.
- Keep native identifiers hashed in persisted state. Display names belong only in escaped private reports.
- Never commit transcripts, credentials, native databases, Task IDs/names, or real generated reports. Use clearly synthetic fixtures.
- Preserve unrelated changes; stage only your responsibility.
- Rebuild hooks after changes to packaged source, then run:
  `ruff check plugins tests scripts`,
  `python3 -m unittest discover -s tests -v`,
  `python3 scripts/validate_repo.py`,
  `python3 scripts/check_docs.py`,
  and `python3 scripts/check_sensitive_data.py --index --fail-on-findings`.
- Verify installation in an isolated Codex home; do not silently alter the maintainer's active plugin setup.
- Keep all four READMEs consistent. Screenshots must come from synthetic application output; brand art can use Codex image generation.

## Paired repository review

When this repository's remote `main` changes, apply
[the paired repository contract](docs/CROSS_REPO_REVIEW.md) to request a review
in Codex Run Budget. When reviewing a change from that repository, decide
whether alignment is needed here; never assume feature parity. Keep this
review report-only and preserve the recorded source SHA and decision.
