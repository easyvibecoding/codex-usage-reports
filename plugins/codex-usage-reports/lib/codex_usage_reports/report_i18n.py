"""Small, deterministic human-output language interface; no global locale mutation.

Only bundled strings are formatted. Native Task names and machine-readable
receipt fields are not translated. Locale discovery is separate from rendering,
so a saved receipt can always be rendered in its recorded language.
"""

from __future__ import annotations

import json
import os
import pkgutil
import re
import stat
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

try:
    import tomllib as _tomllib
except ImportError:  # Python 3.10 keeps the runtime dependency-free.
    _tomllib = None

LOCALES = ("en", "zh-Hant", "zh-Hans", "ja", "ko", "de", "fr", "es", "pt")
DOMAINS = ("turn",)
CONFIG_BYTES = 256 * 1024


def _statements(raw: str):
    """Keep quoted/multiline TOML strings in one statement; ignore comments."""
    result, buffer = [], []
    delimiter = None
    index = 0
    while index < len(raw):
        char = raw[index]
        if delimiter:
            if delimiter[0] == '"' and char == "\\":
                buffer.append(raw[index:index + 2])
                index += 2
                continue
            if raw.startswith(delimiter, index):
                buffer.append(delimiter)
                index += len(delimiter)
                delimiter = None
                continue
            if char == "\n" and len(delimiter) == 1:
                return []  # Unterminated single-line string.
        elif char == "#":
            end = raw.find("\n", index)
            index = len(raw) if end < 0 else end
            continue
        elif char in ('"', "'"):
            delimiter = char * (3 if raw.startswith(char * 3, index) else 1)
            buffer.append(delimiter)
            index += len(delimiter)
            continue
        elif char == "\n":
            result.append("".join(buffer).strip())
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    return [] if delimiter else [*result, "".join(buffer).strip()]


def _legacy_override(raw: str):
    """3.10 adapter for Codex's ordinary table/root-dotted scalar setting.

    The scanner recognizes string boundaries, not a full TOML value grammar.
    Unsupported locale expressions cannot establish a preference; fake sections
    inside quoted instructions are never treated as settings. 3.11+ uses tomllib.
    """
    section = "root"
    candidates = []
    for line in _statements(raw):
        if line.startswith("["):
            section = "desktop" if line == "[desktop]" else "other"
            continue
        if section == "other":
            continue
        key = r"localeOverride" if section == "desktop" else r"desktop\.localeOverride"
        match = re.fullmatch(key + r'''\s*=\s*("(?:[^"\\]|\\.)*"|'[^']*')\s*(?:#.*)?''', line)
        if match:
            value = match[1]
            candidates.append(json.loads(value) if value.startswith('"') else value[1:-1])
    return candidates[0] if len(candidates) == 1 else None


def _app_override(home):
    path = home / "config.toml"
    try:
        if home.is_symlink():
            return None
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > CONFIG_BYTES:
                return None
            raw = source.read(CONFIG_BYTES + 1)
        if len(raw) > CONFIG_BYTES:
            return None
        decoded = raw.decode("utf-8")
        if _tomllib is None:
            return _legacy_override(decoded)
        desktop = _tomllib.loads(decoded).get("desktop", {})
        return desktop.get("localeOverride") if isinstance(desktop, dict) else None
    except (OSError, ValueError, TypeError, RecursionError):
        return None


def _mac_language(domain):
    try:
        result = subprocess.run(
            ["/usr/bin/defaults", "read", domain, "AppleLanguages"],
            capture_output=True, text=True, timeout=0.25, check=False,
        )
        if result.returncode or len(result.stdout) > 4096:
            return None
        # Only the requested language array is read, never a defaults dump.
        match = re.search(r'\(\s*"?([a-zA-Z]{2,3}(?:[-_][a-zA-Z0-9]{2,8})*)"?\s*[,)]',
                          result.stdout)
        return match[1] if match else None
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None


def _system_locale():
    if sys.platform == "darwin":
        for domain, source in (("com.openai.codex", "macos_app_language"),
                               ("-g", "macos_system_language")):
            value = _mac_language(domain)
            if value:
                return value, source
        # A terminal's C/English locale is not evidence of the macOS App language.
        return None, "unavailable"
    if sys.platform == "win32":
        try:
            import ctypes
            from locale import windows_locale
            return (windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage()),
                    "windows_ui_language")
        except (OSError, AttributeError):
            return None, "unavailable"
    for key in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        value = os.environ.get(key, "").split(":", 1)[0].split(".", 1)[0].split("@", 1)[0]
        if value:
            return value, "posix_environment"
    return None, "unavailable"


def resolve_locale(*, home=None) -> dict[str, str]:
    """Prefer the observed Codex setting; auto uses an OS-language fallback.

    Hooks do not expose Electron's effective UI locale/feature flags. These are
    read-only host signals, not a guarantee about a remote client's current UI.
    Only a normalized language and source label leave this module.
    """
    try:
        explicit = normalize(os.environ.get("CODEX_USAGE_REPORTS_LOCALE"))
        if explicit:
            return {"locale": explicit, "locale_source": "report_environment_override"}
        native_home = Path(home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        override = _app_override(native_home)
        if override not in (None, "", "auto"):
            locale = normalize(override)
            return {"locale": locale or "en", "locale_source": (
                "codex_desktop_override" if locale else "unsupported_override_english"
            )}
        candidate, source = _system_locale()
        locale = normalize(candidate)
        return {"locale": locale or "en", "locale_source": (
            source if locale else "fallback_english"
        )}
    except Exception:
        return {"locale": "en", "locale_source": "fallback_english"}


def normalize(value) -> str | None:
    if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(
        r"[a-zA-Z]{2,3}(?:[-_][a-zA-Z0-9]{2,8})*", value
    ):
        return None
    parts = value.lower().replace("_", "-").split("-")
    if parts[0] == "zh":
        if "hant" in parts:
            return "zh-Hant"
        if "hans" in parts:
            return "zh-Hans"
        return "zh-Hant" if any(p in ("tw", "hk", "mo") for p in parts) else "zh-Hans"
    return parts[0] if parts[0] in LOCALES else None


@lru_cache(maxsize=len(LOCALES) * len(DOMAINS))
def _catalog(locale: str, domain: str = "turn") -> dict[str, str]:
    # Both identifiers are allowlisted, never paths from user preferences.
    if locale not in LOCALES or domain not in DOMAINS:
        raise ValueError("unsupported language catalog")
    prefix = "" if domain == "turn" else domain + "/"
    content = pkgutil.get_data("codex_usage_reports", f"assets/locales/{prefix}{locale}.json")
    if content is None:
        raise ValueError("report language bundle unavailable")
    values = json.loads(content)
    if not isinstance(values, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in values.items()
    ):
        raise ValueError("invalid report language bundle")
    return values


class ReportText:
    def __init__(self, locale="en", *, domain="turn"):
        self.locale = normalize(locale) or "en"
        self.domain = domain
        self.values = _catalog(self.locale, domain)

    def _value(self, key: str) -> str:
        if key in self.values:
            return self.values[key]
        fallback = _catalog("en", self.domain)
        if key in fallback:
            return fallback[key]
        # Common display primitives are owned by the small turn catalog.
        return _catalog(self.locale)[key]

    def __call__(self, key: str, **values) -> str:
        return self._value(key).format(**values)

    def number(self, value, digits=0) -> str:
        if value is None:
            return self("not_observed")
        if type(value) not in (int, float):
            return str(value)
        text = format(value, f",.{digits}f") if digits else format(value, ",")
        separators = {",": self._value("group"), ".": self._value("decimal")}
        return text.translate(str.maketrans(separators))

    def duration(self, seconds, *, minutes=True) -> str:
        if seconds is None:
            return self("unknown")
        return self("minutes" if minutes else "seconds",
                    value=self.number(seconds / 60 if minutes else seconds, 1))


def human_text(domain="turn", *, home=None) -> ReportText:
    """Resolve preferences only at a human-output seam, never for machine JSON."""
    return ReportText(resolve_locale(home=home)["locale"], domain=domain)
