#!/usr/bin/env python3
"""Check local documentation links and required public assets."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    problems = []
    files = [*ROOT.glob("*.md"), *ROOT.joinpath("docs").rglob("*.md")]
    for path in files:
        content = path.read_text()
        links = re.findall(r"!?\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)", content)
        links += re.findall(r'(?:src|href)="([^"]+)"', content)
        for link in links:
            url = urlsplit(link.strip("<>"))
            if url.scheme or url.netloc or not url.path:
                continue
            target = (path.parent / unquote(url.path)).resolve()
            if not target.is_relative_to(ROOT) or not target.exists():
                problems.append(f"{path.relative_to(ROOT)}: missing local target {url.path}")
    required = [
        "README.md", "docs/i18n/README.zh-TW.md", "docs/i18n/README.zh-CN.md",
        "docs/i18n/README.ja.md", "docs/assets/hero.png", "docs/assets/icon.png",
        "docs/assets/social-preview.jpg", "docs/examples/turn-en.png",
        "docs/examples/turn-zh-Hant.png", "docs/VALIDATION.md",
    ]
    problems.extend(f"Missing required artifact: {p}" for p in required if not (ROOT / p).is_file())
    for problem in problems:
        print(problem)
    print(f"Documentation check: {len(files)} Markdown files, {len(problems)} problems.")
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
