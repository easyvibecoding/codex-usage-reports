"""Embedded verbatim in trusted hook commands: no cache file is needed to start.

The SHA-256 argument pins one zipapp. Saved runtimes live with the reporting data,
outside Codex's replaceable plugin cache, and are never garbage-collected here.
An unavailable or invalid runtime skips the report and never blocks user work.
"""

from __future__ import annotations

import hashlib
import importlib.abc
import importlib.util
import io
import os
import re
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

MAX_RUNTIME_BYTES = 1024 * 1024


class RuntimeLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Import only this package from already-verified bytes, never by pathname."""

    def __init__(self, blob):
        self.archive = zipfile.ZipFile(io.BytesIO(blob))
        self.runtime_digest = hashlib.sha256(blob).hexdigest()

    def member(self, fullname):
        if fullname != "codex_usage_reports" and not fullname.startswith("codex_usage_reports."):
            return None
        base = fullname.replace(".", "/")
        for name in (base + "/__init__.py", base + ".py"):
            if name in self.archive.namelist():
                return name
        return None

    def find_spec(self, fullname, path=None, target=None):
        name = self.member(fullname)
        if name:
            return importlib.util.spec_from_loader(
                fullname, self, is_package=name.endswith("/__init__.py")
            )
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        name = self.member(module.__name__)
        module.__file__ = "<usage-reports-runtime>/" + name
        exec(compile(self.archive.read(name), module.__file__, "exec"), module.__dict__)

    def get_data(self, path):
        # pkgutil.get_data supports nested resources on Python 3.10 as well as
        # newer versions. The pathname is only a key into verified memory.
        normalized = path.replace("\\", "/")
        prefix = "<usage-reports-runtime>/"
        if not normalized.startswith(prefix):
            raise OSError("resource is outside the pinned runtime")
        return self.archive.read(normalized[len(prefix) :])

    def run(self, event):
        sys.meta_path.insert(0, self)
        sys.argv = ["usage-reports-runtime", "--event", event]
        exec(
            compile(self.archive.read("__main__.py"), "<usage-reports-entry>", "exec"),
            {"__name__": "__main__"},
        )


def verified_bytes(path: Path, expected: str) -> bytes | None:
    descriptor = None
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RUNTIME_BYTES:
            return None
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            blob = stream.read(MAX_RUNTIME_BYTES + 1)
        if len(blob) <= MAX_RUNTIME_BYTES and hashlib.sha256(blob).hexdigest() == expected:
            return blob
    except OSError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return None


def main() -> int:
    event = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    expected = sys.argv[2] if len(sys.argv) > 2 else ""
    temporary = None
    try:
        if not re.fullmatch("[0-9a-f]{64}", expected):
            raise ValueError("invalid runtime identity")
        root = Path(
            os.environ.get("CODEX_USAGE_REPORTS_HOME") or Path.home() / ".codex" / "usage-reports"
        ).expanduser()
        if not root.is_absolute() or root.is_symlink():
            raise ValueError("invalid data directory")
        directory = root / "runtimes"
        if directory.is_symlink():
            raise ValueError("invalid runtime directory")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = directory / (expected + ".pyz")
        if verified_bytes(target, expected) is None:
            source_root = os.environ.get("PLUGIN_ROOT") or os.environ.get("CLAUDE_PLUGIN_ROOT")
            if not source_root:
                raise ValueError("missing source")
            blob = verified_bytes(Path(source_root) / "runtime" / "hook.pyz", expected)
            if blob is None:
                raise ValueError("runtime unavailable")
            descriptor, temporary = tempfile.mkstemp(prefix="runtime-", dir=directory)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(blob)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            temporary = None
        runtime = verified_bytes(target, expected)
        if runtime is None:
            raise ValueError("runtime integrity failed")
        RuntimeLoader(runtime).run(event)
    except Exception:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        print("{}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
