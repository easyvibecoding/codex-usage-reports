"""Bounded, read-only native Task names and parent links, never prompt fallbacks.

Codex's local state schema is observational, not a stable public contract.
Missing/changed schema fails explicitly; raw database rows never leave this module.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import unicodedata
import uuid
from pathlib import Path
from urllib.parse import quote

from .util import stable_hash

MAX_TASKS = 100
MAX_DEPTH = 12


def _uuid(value):
    try:
        parsed = str(uuid.UUID(value))
        return parsed if parsed == value.lower() else None
    except (ValueError, TypeError, AttributeError):
        return None


def _label(value, limit=160):
    if not isinstance(value, str):
        return None
    value = "".join(c for c in value if not unicodedata.category(c).startswith("C"))
    return value.strip()[:limit] or None


def unnamed(thread_hash, label="未命名任務"):
    return label + " · " + thread_hash[:12]


class TaskCatalog:
    """One short-lived snapshot; IDs stay internal, public rows use hashed selectors."""

    def __init__(self, home=None, *, unnamed_label="未命名任務"):
        self.unnamed_label = unnamed_label
        home = Path(home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        # Exact current schema file only: never select a similarly named backup.
        path = home / "state_5.sqlite"
        if home.is_symlink() or path.is_symlink() or not path.is_file():
            raise ValueError("native Task catalog unavailable")
        self.connection = sqlite3.connect(
            "file:" + quote(str(path.absolute())) + "?mode=ro", uri=True, timeout=0.1
        )
        self.connection.row_factory = sqlite3.Row
        self.deadline = time.monotonic() + 0.8
        self.connection.set_progress_handler(lambda: int(time.monotonic() > self.deadline), 1000)
        self.connection.create_function("report_task_hash", 1, stable_hash)
        self.connection.execute("BEGIN")
        self.cache = {}
        self.limited = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.connection.close()

    def _rows(self, where, parameters=(), suffix=""):
        # name is Codex's displayed name. title/preview/first_user_message may
        # contain the user's prompt and are deliberately never selected.
        rows = self.connection.execute(
            "SELECT id,substr(name,1,160) AS name,substr(agent_nickname,1,160) AS nickname,"
            "substr(agent_role,1,80) AS agent_role,substr(agent_path,1,256) AS agent_path,"
            "substr(source,1,4096) AS source FROM threads WHERE " + where + " " + suffix,
            parameters,
        ).fetchall()
        result = []
        for row in rows:
            identifier = _uuid(row["id"])
            if not identifier:
                continue
            try:
                source = json.loads(row["source"])
            except (ValueError, TypeError):
                source = None
            subagent = source.get("subagent") if isinstance(source, dict) else None
            spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
            parent = _uuid(spawn.get("parent_thread_id")) if isinstance(spawn, dict) else None
            nickname = _label(row["nickname"])
            agent_path = _label(row["agent_path"], 256)
            if agent_path and not re.fullmatch(r"/[-a-zA-Z0-9_/]{1,255}", agent_path):
                agent_path = None
            name = _label(row["name"])
            thread_hash = stable_hash(identifier)
            label = name or nickname or unnamed(thread_hash, self.unnamed_label)
            if not name and agent_path:
                label += " / " + agent_path.rsplit("/", 1)[-1]
            item = {
                "id": identifier,
                "thread_hash": thread_hash,
                "display_name": label,
                "name_source": "codex_name"
                if name
                else "agent_metadata"
                if nickname or agent_path
                else "unnamed",
                "agent_nickname": nickname,
                "agent_role": _label(row["agent_role"]),
                "agent_path": agent_path,
                "parent_id": parent,
                "role": "subagent" if subagent is not None else "parent",
            }
            self.cache[identifier] = item
            result.append(item)
        return result

    def get(self, selector):
        identifier = _uuid(selector)
        if identifier:
            if identifier in self.cache:
                return self.cache[identifier]
            rows = self._rows("id=?", (identifier,))
        else:
            reference = selector.removeprefix("@") if isinstance(selector, str) else ""
            if not re.fullmatch(r"[0-9a-f]{12,64}", reference):
                raise ValueError("choose an exact Task UUID or a listed selector")
            rows = self._rows("report_task_hash(id) LIKE ?", (reference + "%",), "LIMIT 2")
        if len(rows) != 1:
            raise ValueError("Task missing or selector ambiguous; list Tasks first")
        return rows[0]

    def recent(self, limit=10):
        if type(limit) is not int or not 1 <= limit <= MAX_TASKS:
            raise ValueError("catalog limit must be 1..100")
        rows = self._rows("1", (), f"ORDER BY updated_at DESC, id LIMIT {limit + 1}")
        self.limited = len(rows) > limit
        return rows[:limit]

    def family(self, root, limit=50):
        if not 1 <= limit <= MAX_TASKS:
            raise ValueError("tree limit must be 1..100")
        found, pending, seen = [], [(root, 0)], set()
        while pending:
            row, depth = pending.pop(0)
            if row["id"] in seen:
                self.limited = True
                continue
            seen.add(row["id"])
            found.append(row)
            if depth >= MAX_DEPTH:
                self.limited = True
                continue
            children = self._rows(
                "CASE WHEN json_valid(source) THEN "
                "json_extract(source,'$.subagent.thread_spawn.parent_thread_id') END=?",
                (row["id"],),
                f"ORDER BY id LIMIT {limit + 1}",
            )
            capacity = limit - len(found) - len(pending)
            self.limited |= len(children) > capacity
            pending.extend((child, depth + 1) for child in children[: max(0, capacity)])
        return found

    def describe(self, row):
        public = {key: value for key, value in row.items() if key not in ("id", "parent_id")}
        public.update(
            selector=row["thread_hash"][:12],
            parent_hash=None,
            parent_name=None,
            root_hash=None,
            root_name=None,
            lineage_status="observed",
        )
        current, seen = row, {row["id"]}
        for depth in range(MAX_DEPTH + 1):
            parent_id = current["parent_id"]
            if parent_id is None:
                if current["role"] == "subagent":
                    public["lineage_status"] = "parent_unavailable"
                else:
                    public.update(
                        root_hash=current["thread_hash"], root_name=current["display_name"]
                    )
                break
            if depth == MAX_DEPTH or parent_id in seen:
                public["lineage_status"] = "cycle_or_depth_limit"
                break
            seen.add(parent_id)
            if depth == 0:
                public["parent_hash"] = stable_hash(parent_id)
            try:
                current = self.get(parent_id)
            except ValueError:
                public["lineage_status"] = "parent_unavailable"
                break
            if depth == 0:
                public["parent_name"] = current["display_name"]
        return public


def task_description(identifier, home=None, *, unnamed_label="未命名任務"):
    """Hook-safe exact lookup; never scans or opens transcript bodies."""
    if not _uuid(identifier):
        return None
    try:
        with TaskCatalog(home, unnamed_label=unnamed_label) as catalog:
            return catalog.describe(catalog.get(identifier))
    except (OSError, ValueError, sqlite3.Error):
        return None
