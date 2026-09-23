# GitHub discoverability and launch assets

## Repository metadata

- Name: `codex-usage-reports`
- Description: **Automatic local token usage reports for every Codex Task and turn. Track observed models, reasoning effort, subagents, and quota. Python, MIT.**
- Homepage: the repository README anchor `#quick-start`.
- Topics: `codex`, `codex-plugin`, `openai-codex`, `token-usage`, `usage-tracking`, `llm-observability`, `developer-tools`, `python`, `local-first`, `subagents`, `markdown`, `multilingual`.

These metadata describe actual capabilities. README headings and natural-language search phrases help readers identify the project; they do not guarantee search ranking.

## Assets

- [Hero](assets/hero.png): README introduction.
- [Social preview](assets/social-preview.jpg): 1280 × 640 JPEG, under 1 MB.
- [Icon](assets/icon.png): plugin identity.
- [Product screenshots](examples/README.md): actual synthetic report output.
- [Generation prompts](assets/PROMPTS.md): reproduce the artistic direction.

GitHub supports repository topics for discovery. Its social preview is configured separately from the README image in **Settings → General → Social preview → Edit → Upload an image**. Merely committing the JPEG does not set that metadata. [GitHub topic documentation](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics), [social preview documentation](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview).

## Project introduction

Codex Usage Reports adds an automatic local receipt to every supported Codex turn. It separates the current turn from the Task's cumulative tokens, shows observed models and reasoning effort, and keeps subagent usage and account quota in their own scopes. The standalone Python plugin needs no reporting API key or hosted dashboard. Available observations stay explicit; missing data remains unknown.

## Short introduction

Every task. Every turn. Automatic local Codex usage reports with honest token deltas, observed settings, subagent attribution, and readable receipts.

## Release notes

See [CHANGELOG.md](../CHANGELOG.md). Publish a release only after CI passes for the exact tagged commit. The public examples must stay synthetic; never copy a personal report into a promotional post.
