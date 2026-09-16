#!/usr/bin/env python3
"""Sign or verify a release. Private signing keys stay outside the repository."""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(plugin):
    path = plugin / "scripts/publisher_bootstrap.py"
    spec = importlib.util.spec_from_file_location("publisher_bootstrap", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_bytes(), json.loads((plugin / "runtime/publisher.json").read_text())


def validate(plugin):
    module, source, policy = load(plugin)
    manifest = module.verified(module.parse((plugin / "runtime/release.json").read_bytes()),
                               policy, source)
    module.runtime_bytes(plugin / "runtime/publisher.pyz", manifest)
    version = json.loads((plugin / ".codex-plugin/plugin.json").read_text())["version"]
    if manifest["version"] != version:
        raise ValueError("signed release version differs from plugin version")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--sequence", type=int)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    plugin = args.plugin or next(p for p in (ROOT / "plugins").iterdir()
                                 if (p / ".codex-plugin/plugin.json").is_file())
    if not args.check:
        if not args.key or args.key.resolve().is_relative_to(ROOT):
            parser.error("--key must point outside the repository")
        module, source, policy = load(plugin)
        manifest = json.loads((plugin / ".codex-plugin/plugin.json").read_text())
        previous = plugin / "runtime/release.json"
        prior = json.loads(previous.read_text())["sequence"] if previous.exists() else 0
        sequence = args.sequence or max(int(time.time()), prior + 1)
        if sequence <= prior:
            parser.error("release sequence must increase")
        runtime = (plugin / "runtime/publisher.pyz").read_bytes()
        unsigned = {"schema": 1, "plugin": manifest["name"], "channel": "stable",
                    "version": manifest["version"], "sequence": sequence,
                    "bootstrap_sha256": module.contract(source, policy),
                    "runtime_sha256": hashlib.sha256(runtime).hexdigest(),
                    "runtime_size": len(runtime)}
        signed = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(args.key)],
                                input=module.canonical(unsigned), capture_output=True, check=True)
        result = {**unsigned, "signature": base64.b64encode(signed.stdout).decode()}
        module.verified(result, policy, source)
        fd, temporary = tempfile.mkstemp(prefix="release-", dir=previous.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(json.dumps(result, indent=2) + "\n")
            os.replace(temporary, previous)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    release = validate(plugin)
    print(json.dumps({key: release[key] for key in ("plugin", "version", "sequence",
                                                   "runtime_sha256", "bootstrap_sha256")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
