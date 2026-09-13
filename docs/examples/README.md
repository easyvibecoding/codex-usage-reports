# Example gallery

All names, IDs, model labels, token counters, and quota values here are synthetic.
The HTML and screenshots use the actual report renderer. The preview uses a
standalone documentation frame; the Codex desktop supplies its own surrounding
styles in normal use.

| Example | Preview | Download / inspect |
| --- | --- | --- |
| English turn card | [PNG](turn-en.png) | [HTML](turn-en.html) |
| 繁體中文 | [PNG](turn-zh-Hant.png) | [HTML](turn-zh-Hant.html) |
| 简体中文 | [PNG](turn-zh-Hans.png) | [HTML](turn-zh-Hans.html) |
| 日本語 | [PNG](turn-ja.png) | [HTML](turn-ja.html) |
| Selected Task | [PNG](selected-task.png) | [HTML](selected-task.html), [Markdown](selected-task.md), [JSON](selected-task.json) |
| Completed receipt | — | [HTML](completed-receipt.html), [Markdown](completed-receipt.md) |
| Input fixture | — | [JSON](synthetic-receipt.json) |

GitHub displays HTML source; download an HTML file and open it locally to use
the disclosures.

## Reproduce

From the repository root:

```sh
python3 scripts/generate_examples.py
node scripts/capture_examples.cjs
```

The second command needs Playwright and its Chromium browser installed as
development tools. Set `BROWSER_CHANNEL=chrome` to use an installed Chrome.
The script also checks four locales at two widths in light and dark themes
and exercises each disclosure with the keyboard. No real Codex data is read.
