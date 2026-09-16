#!/usr/bin/env python3
"""Control this plugin's signed runtime channel without changing hook trust."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import publisher_bootstrap as bootstrap


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "on", "off", "update", "rollback"))
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    plugin = Path(__file__).resolve().parents[1]
    policy = json.loads((plugin / "runtime/publisher.json").read_text())
    source = (plugin / "scripts/publisher_bootstrap.py").read_bytes()
    os.environ["PLUGIN_ROOT"] = str(plugin)
    root = args.data_dir or bootstrap.data_root(policy)
    return bootstrap.main(source, policy, ["--control", str(root), args.action])



if __name__ == "__main__":
    raise SystemExit(main())
