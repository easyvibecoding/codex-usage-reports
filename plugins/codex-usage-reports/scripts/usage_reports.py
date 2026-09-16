#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
# Once a signed runtime is active, manual commands use the same verified code.
from publisher_bootstrap import runtime_cli  # noqa: E402

if __name__ == "__main__":
    selected = runtime_cli(PLUGIN_ROOT, sys.argv[1:])
    if selected is not None:
        raise SystemExit(selected)

sys.path.insert(0, str(PLUGIN_ROOT / "lib"))

from codex_usage_reports.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
