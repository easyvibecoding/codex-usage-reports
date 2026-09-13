#!/usr/bin/env python3
"""Deterministic, redacted repository secret and private-artifact scanner.

The scanner deliberately uses a small high-signal rule set.  It is not a
replacement for a provider's secret scanner, but it gives the repository a
dependency-free gate that can inspect exactly what is staged and every blob
reachable from Git.  Findings never contain matched text: only a relative
location, rule, short SHA-256 fingerprint, and category are emitted.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_SCAN_BYTES = 4 * 1024 * 1024
MAX_ZIP_ENTRIES = 512
MAX_ZIP_MEMBER_BYTES = 4 * 1024 * 1024
MAX_ZIP_DEPTH = 4
MAX_ZIP_TOTAL_BYTES = 16 * MAX_SCAN_BYTES
FINGERPRINT_HEX = 16
_SENSITIVE_PATH_HINT = re.compile(
    r"(?i)(?:sk-(?:proj-)?[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"AKIA[0-9A-Z]{8,}|AIza[0-9A-Za-z_-]{12,}|/(?:Users|home)/|"
    r"/(?:private/)?(?:var/folders|tmp)/|[A-Za-z]:[\\/](?:Users|home)[\\/])"
)


@dataclass(frozen=True, order=True)
class Finding:
    """A safe-to-print scan result."""

    path: str
    line: int
    rule: str
    fingerprint: str
    category: str


class ScanError(RuntimeError):
    """Raised when a Git source cannot be read deterministically."""


def _fingerprint(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8", "surrogatepass")
    return "sha256:" + hashlib.sha256(value).hexdigest()[:FINGERPRINT_HEX]


def _path(value: str | os.PathLike[str]) -> str:
    """Normalize an emitted path without ever printing an absolute path."""

    normalized = str(value).replace("\\", "/")
    has_secret_pattern = any(
        category == "secret" and pattern.search(normalized)
        for _rule, category, pattern in globals().get("_PATTERNS", ())
    )
    if _SENSITIVE_PATH_HINT.search(normalized) or has_secret_pattern:
        return "<redacted-path-" + _fingerprint(normalized)[7:] + ">"
    normalized = normalized.lstrip("/")
    parts: list[str] = []
    for part in normalized.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        escaped = "".join(
            (
                f"\\x{ord(char):02x}"
                if ord(char) < 32 or ord(char) == 127
                else f"\\u{ord(char):04x}"
                if 0xD800 <= ord(char) <= 0xDFFF
                else char
            )
            for char in part
        )
        parts.append(escaped)
    return "/".join(parts) or "."


# These patterns intentionally require provider prefixes or a credential
# assignment.  A bare ``token_count``/``hash``/``model`` field is not a secret.
_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "private-key",
        "secret",
        re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----"),
    ),
    (
        "provider-token",
        "secret",
        re.compile(
            r"(?<![A-Za-z0-9_-])(?:"
            r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}|"
            r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
            r"xox[baprs]-[A-Za-z0-9-]{20,}|"
            r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}"
            r")(?![A-Za-z0-9_-])"
        ),
    ),
    (
        "bearer-token",
        "secret",
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    ),
    (
        "database-url-credential",
        "secret",
        re.compile(
            r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://"
            r"[^\s/:@]+:[^\s/@]+@"
        ),
    ),
    (
        "credential-assignment",
        "secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
            r"refresh[_-]?token|client[_-]?secret|private[_-]?key|"
            r"password|passwd)\b[\"']?\s*[:=]\s*"
            r"(?:\"([^\"]{8,})\"|'([^']{8,})'|([^\s,#]{8,}))"
        ),
    ),
    (
        "provider-variable",
        "secret",
        re.compile(
            r"\b(?:OPENAI|GITHUB|GH|AWS|SLACK|GOOGLE|AZURE|SENTRY|DATABASE|SERVICE)"
            r"[_-]?(?:API[_-]?)?(?:KEY|TOKEN|SECRET)\b[\"']?\s*[:=]\s*"
            r"(?:\"([^\"]{8,})\"|'([^']{8,})'|([^\s,#]{8,}))",
            re.IGNORECASE,
        ),
    ),
    (
        "task-identifier",
        "private-metadata",
        re.compile(
            r"(?i)(?:task|thread|session|run|execution|turn|agent)[_-]?"
            r"(?:id|uuid)[\"']?\s*[:=]\s*[\"']?"
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}"
        ),
    ),
)

_PRIVATE_PATH = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"/(?:Users|home)/[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9._~+@%=-]+)+|"
    r"/(?:private/)?(?:var/folders|tmp)/[A-Za-z0-9._~+@%=-]+"
    r"(?:/[A-Za-z0-9._~+@%=-]+)*|"
    r"[A-Za-z]:[\\/](?:Users|home)[\\/][A-Za-z0-9._-]+"
    r"(?:[\\/][A-Za-z0-9._~+@%=-]+)+"
    r")(?![A-Za-z0-9_])"
)

_TASK_CONTENT = re.compile(
    r"(?i)[\"'](?:prompt|message|command|tool[_-]?input|tool[_-]?output|"
    r"transcript|response)[\"']\s*:\s*[\"'][^\"']{20,}"
)

_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@"
    r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.-])"
)
_PHONE = re.compile(
    r"(?<!\d)(?:09\d{2}[- .]?\d{3}[- .]?\d{3}|"
    r"(?:\+?1[- .]?)?(?:[2-9]\d{2}[- .]?)\d{3}[- .]?\d{4})(?!\d)"
)
_PRIVATE_NETWORK = re.compile(
    r"(?<![\d.])(?:10(?:\.\d{1,3}){3}|"
    r"192\.168(?:\.\d{1,3}){2}|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}|"
    r"169\.254(?:\.\d{1,3}){2}|127(?:\.\d{1,3}){3})(?![\d.])"
)
_PRIVATE_HOST = re.compile(r"(?i)(?<![A-Za-z0-9_-])[A-Za-z0-9_-]+\.(?:internal|corp|lan|local)\b")
_IDENTIFIER_FIELD = re.compile(
    r"(?i)[\"'](?:account|user|organization|org|task|thread|session|run|agent)"
    r"(?:[_-]?(?:id|uuid|name|title))?[\"']\s*:\s*"
    r"(?:[\"'][^\"']{3,}[\"']|[0-9a-f]{8}-[0-9a-f-]{20,})"
)

_ARTIFACT_EXTENSIONS = {
    ".bak",
    ".csv",
    ".db",
    ".dump",
    ".html",
    ".htm",
    ".json",
    ".jsonl",
    ".md",
    ".ndjson",
    ".sql",
    ".sqlite",
    ".sqlite3",
    ".tar",
    ".tgz",
    ".tsv",
}
_ARTIFACT_NAME = re.compile(
    r"(?i)(?:^|/)(?:.*(?:report|transcript|rollout|session|dump|backup|snapshot|export)"
    r".*)$"
)
_LOCAL_SENSITIVE_NAME = re.compile(
    r"(?i)^(?:\.env(?:\..*)?|.*\.(?:pem|key|p12|pfx|kdbx|sqlite|sqlite3|db|dump|bak|"
    r"jsonl|ndjson)|(?:codex-turn-|turn-report-|usage-|report-).+\.(?:html|htm|md|json))$"
)
_LOCAL_PRUNE = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
}
_ARCHIVE_SUFFIXES = {".pyz", ".zip"}
_GENERATED_REPORT_NAME = re.compile(
    r"(?i)(?:^|/)(?:codex-turn-|turn-(?:card|report)-|usage(?:[-_]|$)|"
    r"session-(?:report|usage)-|report-(?:card|preview)-|rollout-(?:report|usage)-)"
)

_SAFE_PLACEHOLDER = re.compile(
    r"(?i)^(?:<[^>]+>|\$\{[^}]+\}|your(?:[-_ ]|$)[A-Za-z0-9._-]*|"
    r"(?:example(?:\.invalid|\.com)?(?:[-_][A-Za-z0-9._-]+)?|placeholder|change(?:me|this)|"
    r"replace[-_ ]?me|dummy|redacted|none|null|localhost|127\.0\.0\.1|"
    r"test[-_ ]?(?:key|token|secret)))$"
)


def _safe_placeholder(value: str) -> bool:
    return bool(_SAFE_PLACEHOLDER.fullmatch(value.strip().strip("\"'`")))


def _safe_private_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    match = re.search(r"/(?:Users|home)/([^/]+)(?:/|$)", normalized)
    return bool(
        match and match.group(1).lower() in {"example", "user", "username", "placeholder"}
    )


def _artifact_candidate(path: str) -> bool:
    normalized = path.replace("\\", "/")
    suffix = Path(normalized.split("!", 1)[-1]).suffix.lower()
    if suffix not in _ARTIFACT_EXTENSIONS:
        return False
    name = normalized.split("!", 1)[0]
    if name.startswith("outputs/"):
        return True
    basename = Path(name).name
    if suffix in {".html", ".htm", ".md"}:
        return bool(_GENERATED_REPORT_NAME.search(basename))
    return bool(_ARTIFACT_NAME.search(basename))


def _local_sensitive_candidate(path: str) -> bool:
    basename = Path(path.replace("\\", "/").split("!", 1)[0]).name
    return bool(_LOCAL_SENSITIVE_NAME.fullmatch(basename)) and basename != ".env.example"


def _archive_hint(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return Path(normalized.split("!")[-1]).suffix.lower() in _ARCHIVE_SUFFIXES


def _text(data: bytes) -> str | None:
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _matches(data: bytes, path: str) -> Iterator[Finding]:
    if _SENSITIVE_PATH_HINT.search(path):
        yield Finding(
            path=_path(path),
            line=1,
            rule="sensitive-path-name",
            fingerprint=_fingerprint(path),
            category="private-metadata",
        )
    if _local_sensitive_candidate(path):
        yield Finding(
            path=_path(path),
            line=1,
            rule="local-private-file",
            fingerprint=_fingerprint(path),
            category="artifact",
        )
    artifact = _artifact_candidate(path)
    if artifact:
        yield Finding(
            path=_path(path),
            line=1,
            rule="private-artifact-file",
            fingerprint=_fingerprint(path),
            category="artifact",
        )
    text = _text(data)
    if text is None:
        return
    for line_number, line in enumerate(text.splitlines(), 1):
        for rule, category, pattern in _PATTERNS:
            for match in pattern.finditer(line):
                # Only the exact captured value may be a safe placeholder.  A
                # nearby ``example.com`` comment must never hide a real token.
                if rule in {"credential-assignment", "provider-variable"}:
                    value = next((group for group in match.groups()[::-1] if group), "")
                    if _safe_placeholder(value):
                        continue
                elif rule not in {
                    "private-key",
                    "provider-token",
                    "bearer-token",
                    "database-url-credential",
                }:
                    if _safe_placeholder(match.group(0)):
                        continue
                yield Finding(
                    path=_path(path),
                    line=line_number,
                    rule=rule,
                    fingerprint=_fingerprint(match.group(0)),
                    category=category,
                )
        if artifact:
            for match in _TASK_CONTENT.finditer(line):
                if _safe_placeholder(match.group(0)):
                    continue
                yield Finding(
                    path=_path(path),
                    line=line_number,
                    rule="task-content-artifact",
                    fingerprint=_fingerprint(match.group(0)),
                    category="private-metadata",
                )
            for rule, pattern in (
                ("private-email-artifact", _EMAIL),
                ("private-phone-artifact", _PHONE),
                ("private-network-address", _PRIVATE_NETWORK),
                ("private-hostname", _PRIVATE_HOST),
                ("task-metadata-artifact", _IDENTIFIER_FIELD),
            ):
                for match in pattern.finditer(line):
                    if _safe_placeholder(match.group(0)):
                        continue
                    yield Finding(
                        path=_path(path),
                        line=line_number,
                        rule=rule,
                        fingerprint=_fingerprint(match.group(0)),
                        category="private-metadata",
                    )
        for match in _PRIVATE_PATH.finditer(line):
            if _safe_private_path(match.group(0)):
                continue
            yield Finding(
                path=_path(path),
                line=line_number,
                rule="private-absolute-path",
                fingerprint=_fingerprint(match.group(0)),
                category="private-metadata",
            )


def _scan_bytes(data: bytes, path: str, depth: int, budget: list[int]) -> list[Finding]:
    if len(data) > MAX_SCAN_BYTES:
        return [
            Finding(
                path=_path(path),
                line=1,
                rule="oversized-sensitive-artifact",
                fingerprint=_fingerprint(str(len(data))),
                category="artifact",
            )
        ]
    findings = list(_matches(data, path))
    try:
        valid_archive = zipfile.is_zipfile(io.BytesIO(data))
    except Exception:
        if _archive_hint(path):
            raise ScanError("archive source unavailable") from None
        return findings
    if not valid_archive:
        if _archive_hint(path):
            raise ScanError("archive source unavailable")
        return findings
    if depth >= MAX_ZIP_DEPTH:
        findings.append(
            Finding(
                path=_path(path),
                line=1,
                rule="oversized-zip-depth",
                fingerprint=_fingerprint(str(depth)),
                category="artifact",
            )
        )
        return findings
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        raise ScanError("archive source unavailable") from None
    with archive:
        try:
            infos = archive.infolist()
        except Exception:
            raise ScanError("archive source unavailable") from None
        if not infos and _archive_hint(path):
            findings.append(
                Finding(
                    path=_path(path),
                    line=1,
                    rule="empty-zip-artifact",
                    fingerprint=_fingerprint(path),
                    category="artifact",
                )
            )
        if len(infos) > MAX_ZIP_ENTRIES:
            findings.append(
                Finding(
                    path=_path(path),
                    line=1,
                    rule="oversized-zip-artifact",
                    fingerprint=_fingerprint(str(len(infos))),
                    category="artifact",
                )
            )
        for info in infos[:MAX_ZIP_ENTRIES]:
            member_path = path + "!" + info.filename
            if info.file_size > MAX_ZIP_MEMBER_BYTES:
                findings.append(
                    Finding(
                        path=_path(member_path),
                        line=1,
                        rule="oversized-zip-member",
                        fingerprint=_fingerprint(str(info.file_size)),
                        category="artifact",
                    )
                )
                continue
            budget[0] += info.file_size
            if budget[0] > MAX_ZIP_TOTAL_BYTES:
                findings.append(
                    Finding(
                        path=_path(member_path),
                        line=1,
                        rule="oversized-zip-total",
                        fingerprint=_fingerprint(str(budget[0])),
                        category="artifact",
                    )
                )
                break
            try:
                member = archive.read(info)
            except Exception:
                raise ScanError("archive source unavailable") from None
            findings.extend(_scan_bytes(member, member_path, depth + 1, budget))
    return findings


def scan_bytes(data: bytes, path: str) -> list[Finding]:
    """Scan one bounded blob and its bounded zip members."""

    return sorted(set(_scan_bytes(data, path, 0, [0])))


def _git(repo: Path, *args: str, check: bool = True) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScanError("git source unavailable") from exc
    if check and result.returncode:
        raise ScanError("git source unavailable")
    return result.stdout


def _nul_paths(repo: Path, *args: str) -> list[str]:
    return [
        item.decode("utf-8", "surrogateescape")
        for item in _git(repo, *args).split(b"\0")
        if item
    ]


def scan_worktree(repo: Path) -> list[Finding]:
    """Scan tracked and unignored untracked worktree files without following symlinks."""

    findings: list[Finding] = []
    names = set(_nul_paths(repo, "ls-files", "--cached", "--others", "--exclude-standard", "-z"))
    # A local .env/database/transcript/report is normally ignored on purpose,
    # but an audit should still say that it exists.  Walk only candidate names
    # and prune VCS/cache directories; symlinks are never followed.
    for directory, subdirectories, files in os.walk(repo, followlinks=False):
        subdirectories[:] = [name for name in subdirectories if name not in _LOCAL_PRUNE]
        for filename in files:
            relative = Path(directory, filename).relative_to(repo).as_posix()
            if _local_sensitive_candidate(relative):
                names.add(relative)
    for name in sorted(names):
        target = repo / Path(name)
        try:
            if target.is_symlink() or not target.is_file():
                continue
            stat_result = target.stat()
            if stat_result.st_size > MAX_SCAN_BYTES:
                findings.extend(scan_bytes(b"0" * (MAX_SCAN_BYTES + 1), name))
                continue
            findings.extend(scan_bytes(target.read_bytes(), name))
        except OSError as exc:
            raise ScanError("worktree source unavailable") from exc
    return sorted(set(findings))


def _index_entries(repo: Path) -> Iterator[tuple[str, bytes]]:
    raw = _git(repo, "ls-files", "--stage", "-z")
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            _mode, oid, _stage = metadata.decode("ascii").split()
            name = raw_path.decode("utf-8", "surrogateescape")
            blob = _git(repo, "cat-file", "blob", oid)
        except (ValueError, UnicodeError) as exc:
            raise ScanError("index source unavailable") from exc
        yield name, blob


def scan_index(repo: Path) -> list[Finding]:
    findings: list[Finding] = []
    for name, blob in _index_entries(repo):
        findings.extend(scan_bytes(blob, name))
    return sorted(set(findings))


def _history_blobs(repo: Path) -> Iterator[tuple[str, bytes]]:
    seen: set[str] = set()
    raw = _git(repo, "rev-list", "--objects", "--all")
    for record in raw.splitlines():
        parts = record.split(None, 1)
        if len(parts) != 2:
            continue
        oid = parts[0].decode("ascii", "ignore")
        name = parts[1].decode("utf-8", "surrogateescape")
        if oid in seen:
            continue
        seen.add(oid)
        try:
            kind = _git(repo, "cat-file", "-t", oid).decode("ascii").strip()
            if kind != "blob":
                continue
            yield name, _git(repo, "cat-file", "blob", oid)
        except UnicodeError as exc:
            raise ScanError("history source unavailable") from exc


def scan_history(repo: Path) -> list[Finding]:
    findings: list[Finding] = []
    for name, blob in _history_blobs(repo):
        findings.extend(scan_bytes(blob, "history/" + name))
    return sorted(set(findings))


def scan(repo: Path, scopes: Sequence[str]) -> list[Finding]:
    findings: list[Finding] = []
    for scope in scopes:
        if scope == "worktree":
            findings.extend(scan_worktree(repo))
        elif scope == "index":
            findings.extend(scan_index(repo))
        elif scope == "history":
            findings.extend(scan_history(repo))
        else:
            raise ValueError(f"unknown scan scope: {scope}")
    return sorted(set(findings))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--worktree", action="store_true", help="scan tracked and unignored files")
    scope.add_argument("--index", action="store_true", help="scan the Git index blobs")
    scope.add_argument("--history", action="store_true", help="scan every reachable Git blob")
    scope.add_argument("--all", action="store_true", help="scan worktree, index, and history")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", help="emit only redacted JSON")
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="return status 1 when findings exist (the default for --index/--worktree/--all)",
    )
    return parser


def _emit(findings: Iterable[Finding], as_json: bool) -> None:
    unique = sorted(set(findings))
    if as_json:
        print(json.dumps([asdict(item) for item in unique], ensure_ascii=False, sort_keys=True))
        return
    if not unique:
        print("Sensitive data scan passed: 0 findings")
        return
    print(f"Sensitive data scan: {len(unique)} finding(s)")
    for item in unique:
        print(
            f"{item.path}:{item.line}\trule={item.rule}"
            f"\tfingerprint={item.fingerprint}\tcategory={item.category}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    scopes = (
        ["worktree", "index", "history"]
        if args.all
        else ["worktree"]
        if args.worktree
        else ["index"]
        if args.index
        else ["history"]
        if args.history
        else ["index"]
    )
    try:
        findings = scan(args.repo.resolve(), scopes)
    except (OSError, ScanError, ValueError):
        if args.json:
            print(
                json.dumps(
                    {
                        "error": {
                            "path": ".",
                            "line": 1,
                            "rule": "scan-source-unavailable",
                            "fingerprint": _fingerprint("scan-source-unavailable"),
                            "category": "scanner",
                        }
                    },
                    sort_keys=True,
                )
            )
        else:
            print("Sensitive data scan failed: source unavailable", file=sys.stderr)
        return 2
    _emit(findings, args.json)
    should_fail = args.fail_on_findings or scopes != ["history"]
    return 1 if findings and should_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
