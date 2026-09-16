#!/usr/bin/env python3
"""Validate report-only packaging, localization, and pinned runtime consistency."""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from string import Formatter

from build_hook_runtime import EVENTS, MODULES, artifacts

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/codex-usage-reports"
PACKAGE = PLUGIN / "lib/codex_usage_reports"
LOCALES = {"en", "zh-Hant", "zh-Hans", "ja", "ko", "de", "fr", "es", "pt"}
FORBIDDEN_MODULES = {"governor", "ledger", "audit", "workflow", "survey", "meter_policy"}


def validate_catalogs() -> None:
    directory = PACKAGE / "assets/locales"
    assert {p.stem for p in directory.glob("*.json")} == LOCALES, "language set differs"
    base = json.loads((directory / "en.json").read_text())
    formatter = Formatter()

    def placeholders(value):
        return sorted((field, spec, conversion or "")
                      for _, field, spec, conversion in formatter.parse(value) if field)

    for path in directory.glob("*.json"):
        values = json.loads(path.read_text())
        assert values.keys() == base.keys(), f"language keys differ: {path.name}"
        for key, value in values.items():
            assert isinstance(value, str) and value, f"invalid language value: {path.name}/{key}"
            assert placeholders(value) == placeholders(base[key]), (
                f"language placeholders differ: {path.name}/{key}")


def main() -> int:
    manifest = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text())
    marketplace = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())
    hooks = json.loads((PLUGIN / "hooks/hooks.json").read_text())
    assert manifest["name"] == PLUGIN.name == "codex-usage-reports", "plugin name mismatch"
    version = manifest["version"]
    project = (ROOT / "pyproject.toml").read_text()
    package = (PACKAGE / "__init__.py").read_text()
    assert re.search(r'^version = "' + re.escape(version) + r'"$', project, re.M)
    assert re.search(r'^__version__ = "' + re.escape(version) + r'"$', package, re.M)
    assert 'dependencies = []' in project, "runtime dependencies must stay empty"
    assert marketplace["name"] == "codex-usage-reports", "marketplace name mismatch"
    entries = [entry for entry in marketplace["plugins"] if entry["name"] == manifest["name"]]
    assert len(entries) == 1, "marketplace plugin must be unique"
    assert entries[0]["source"]["path"] == "./plugins/codex-usage-reports"
    assert set(hooks["hooks"]) == set(EVENTS), "unexpected hook events"
    assert not {"update_notice.py", "update_cli.py"}.intersection(MODULES), (
        "update checker must remain outside the reporting hook runtime")
    for event, groups in hooks["hooks"].items():
        for group in groups:
            for hook in group["hooks"]:
                assert hook["command"].startswith("python3 -I -c "), event
                assert hook["timeout"] <= 3, "report hooks must be bounded"
    for path, expected in artifacts().items():
        assert path.is_file() and path.read_bytes() == expected, "runtime artifacts are stale"
    assert not FORBIDDEN_MODULES.intersection(p.stem for p in PACKAGE.glob("*.py")), (
        "enforcement and unrelated analysis must not ship")
    for source in [*(PACKAGE.rglob("*.py")), *(PLUGIN / "scripts").glob("*.py")]:
        code = source.read_text()
        tree = ast.parse(code, filename=str(source))
        assert "[TODO:" not in code
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not FORBIDDEN_MODULES.intersection(node.module.split(".")), (
                    "unrelated module dependency")
                if node.level and source.parent == PACKAGE and source.name in MODULES:
                    module = node.module.split(".")[0] + ".py"
                    assert module in MODULES, f"pinned dependency missing: {module}"
    for field in ("composerIcon", "logo", "logoDark"):
        if field in manifest["interface"]:
            assert (PLUGIN / manifest["interface"][field]).is_file(), f"missing asset: {field}"
    skill = (PLUGIN / "skills/usage-report/SKILL.md").read_text()
    assert skill.startswith("---\nname: usage-report\n"), "skill frontmatter invalid"
    validate_catalogs()
    print("Repository validation passed: independent report-only runtime and nine locales.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, OSError, ValueError) as exc:
        print(f"Repository validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
