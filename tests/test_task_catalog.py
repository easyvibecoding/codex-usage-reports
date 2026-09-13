from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/codex-usage-reports/lib"))
from datetime import datetime, timezone  # noqa: E402

from codex_usage_reports.task_catalog import TaskCatalog, task_description  # noqa: E402
from codex_usage_reports.util import stable_hash  # noqa: E402

TASK_A = "12345678-1234-1234-1234-123456789abc"
TASK_B = "22345678-1234-1234-1234-123456789abc"


def event(stamp, kind, payload):
    return {"timestamp": datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
            "type": kind, "payload": payload}


def request(stamp, task):
    return event(stamp, "token_usage_record", {"thread_id": task, "turn_id": "t"})

TASK_C = "32345678-1234-1234-1234-123456789abc"


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.db = self.root / "state_5.sqlite"
        connection = sqlite3.connect(self.db)
        connection.execute(
            "CREATE TABLE threads (id TEXT PRIMARY KEY,name TEXT,title TEXT,"
            "agent_nickname TEXT,agent_role TEXT,agent_path TEXT,source TEXT,updated_at INT)"
        )
        for identifier, name, parent, nickname, number in (
            (TASK_A, "改善報告", None, None, 1),
            (TASK_B, None, TASK_A, "Curie", 2),
            (TASK_C, None, TASK_B, "Erdos", 3),
        ):
            source = (
                json.dumps({"subagent": {"thread_spawn": {"parent_thread_id": parent}}})
                if parent
                else "vscode"
            )
            connection.execute(
                "INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    name,
                    "PRIVATE PROMPT FALLBACK",
                    nickname,
                    "worker" if parent else None,
                    "/root/helper" if parent else None,
                    source,
                    number,
                ),
            )
        connection.commit()
        connection.close()
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()
        for identifier, parent in ((TASK_A, None), (TASK_B, TASK_A), (TASK_C, TASK_B)):
            page = self.sessions / f"rollout-2026-01-01-{identifier}.jsonl"
            metadata = {"id": identifier}
            if parent:
                metadata["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent}}}
            page.write_text(
                "\n".join(
                    json.dumps(row)
                    for row in (
                        event(999000, "session_meta", metadata),
                        event(999001, "turn_context", {"model": "gpt-6-astra", "turn_id": "t"}),
                        request(999900, identifier),
                    )
                )
                + "\n"
            )
            os.utime(page, (1000000, 1000000))

    def test_native_name_not_prompt_fallback_and_nested_ownership(self):
        before = self.db.read_bytes()
        with TaskCatalog(self.root) as catalog:
            root = catalog.get(TASK_A)
            self.assertEqual(root["display_name"], "改善報告")
            leaf = catalog.describe(catalog.get(stable_hash(TASK_C)[:12]))
            self.assertEqual(leaf["root_name"], "改善報告")
            self.assertEqual(leaf["parent_name"], "Curie / helper")
            self.assertEqual(leaf["parent_hash"], stable_hash(TASK_B))
            self.assertEqual(len(catalog.family(root)), 3)
        self.assertNotIn("PRIVATE PROMPT", json.dumps(leaf))
        self.assertNotIn(TASK_A, json.dumps(leaf))
        self.assertEqual(self.db.read_bytes(), before)

    def test_unknown_schema_missing_parent_cycles_and_limits(self):
        with TaskCatalog(self.root) as catalog:
            self.assertEqual(len(catalog.family(catalog.get(TASK_A), 2)), 2)
            self.assertTrue(catalog.limited)
        connection = sqlite3.connect(self.db)
        connection.execute(
            "UPDATE threads SET source=? WHERE id=?",
            (
                json.dumps({"subagent": {"thread_spawn": {"parent_thread_id": TASK_C}}}),
                TASK_A,
            ),
        )
        connection.commit()
        connection.close()
        self.assertEqual(
            task_description(TASK_C, self.root)["lineage_status"], "cycle_or_depth_limit"
        )
        self.assertIsNone(task_description(TASK_A, self.root / "missing"))
        self.assertIsNone(task_description("bad", self.root))




if __name__ == "__main__":
    unittest.main()
