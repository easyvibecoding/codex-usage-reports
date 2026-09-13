"""Shared identity hashing and the independent local reporting data directory."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def stable_hash(value: Any) -> str:
    if isinstance(value, str):
        payload = value.encode("utf-8", errors="replace")
    else:
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
        ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def data_path() -> Path:
    configured = os.environ.get("CODEX_USAGE_REPORTS_HOME")
    return (Path(configured).expanduser() if configured
            else Path.home() / ".codex" / "usage-reports")
