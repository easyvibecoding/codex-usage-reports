from __future__ import annotations

import json
import sqlite3
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/codex-usage-reports/lib"))

from codex_usage_reports.child_usage import (  # noqa: E402
    DB_NAME,
    HEADER_BYTES,
    TAIL_BYTES,
    _load_cache,
    _save_scan,
    _scan,
    capture,
    collect,
)
from codex_usage_reports.util import stable_hash  # noqa: E402

PARENT = "12345678-1234-1234-1234-123456789abc"
CHILD_A = "aaaaaaaa-1234-1234-1234-123456789abc"
CHILD_B = "bbbbbbbb-1234-1234-1234-123456789abc"
GRANDCHILD = "cccccccc-1234-1234-1234-123456789abc"
UNRELATED = "dddddddd-1234-1234-1234-123456789abc"
WINDOW_START = 1_700_000_000


def stamp(second: int) -> str:
    return datetime.fromtimestamp(WINDOW_START + second, timezone.utc).isoformat()


def usage(total: int = 12) -> dict[str, int]:
    return {
        "total_tokens": total,
        "input_tokens": total - 4,
        "cached_input_tokens": 2,
        "output_tokens": 4,
        "reasoning_output_tokens": 1,
    }


def synthetic_child(number: int) -> str:
    """Stable UUIDs for bounded scope fixtures; never a native Task id."""

    return f"{number:08x}-1111-2222-3333-444444444444"


def metadata(identifier: str, parent: str, second: int = 0) -> dict:
    return {
        "type": "session_meta",
        "timestamp": stamp(second),
        "payload": {
            "id": identifier,
            "source": {"subagent": {"thread_spawn": {"parent_thread_id": parent}}},
        },
    }


def request(identifier: str, response: str, second: int, total: int = 12) -> dict:
    return {
        "type": "token_usage_record",
        "timestamp": stamp(second),
        "payload": {
            "thread_id": identifier,
            "turn_id": f"turn-{identifier[:4]}",
            "response_id": response,
            "usage": usage(total),
        },
    }


def complete(second: int, *, identifier: str | None = None) -> dict:
    payload = {"type": "task_complete"}
    if identifier is not None:
        payload["thread_id"] = identifier
    return {"type": "event_msg", "timestamp": stamp(second), "payload": payload}


class ChildUsageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "data"
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir()
        self.db = sqlite3.connect(self.home / "state_5.sqlite")
        self.db.execute(
            "CREATE TABLE threads (id TEXT PRIMARY KEY,name TEXT,agent_nickname TEXT,"
            "agent_role TEXT,agent_path TEXT,source TEXT,rollout_path TEXT,updated_at INTEGER)"
        )
        self.paths: dict[str, Path] = {}
        self._insert(PARENT, "主代理", None, "vscode", None)

    def tearDown(self) -> None:
        self.db.close()

    def _insert(
        self,
        identifier: str,
        name: str | None,
        parent: str | None,
        source: str | dict,
        path: Path | None,
    ) -> None:
        if isinstance(source, dict):
            source = json.dumps(source)
        self.db.execute(
            "INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)",
            (
                identifier,
                name,
                None if name else f"agent-{identifier[:4]}",
                "worker" if parent else None,
                f"/root/{identifier[:4]}" if parent else None,
                source,
                str(path) if path else None,
                1,
            ),
        )
        self.db.commit()

    def _page(self, identifier: str, parent: str, records: list[dict], *, name: str) -> Path:
        path = Path(self.temp.name) / f"{identifier}.jsonl"
        path.write_text("".join(json.dumps(record) + "\n" for record in records))
        self.paths[identifier] = path
        self._insert(
            identifier,
            name,
            parent,
            {"subagent": {"thread_spawn": {"parent_thread_id": parent}}},
            path,
        )
        return path

    def _stop(self, child: str, path: Path | None, *, parent: str = PARENT) -> None:
        capture(
            {
                "hook_event_name": "SubagentStop",
                "session_id": parent,
                "agent_id": child,
                "agent_transcript_path": str(path) if path else None,
            },
            self.root,
            home=self.home,
            now=1_700_000_100,
        )

    def test_completed_child_capture_and_collect(self) -> None:
        path = self._page(
            CHILD_A,
            PARENT,
            [metadata(CHILD_A, PARENT), request(CHILD_A, "response-a", 10), complete(11)],
            name="子代理 A",
        )
        self._stop(CHILD_A, path)
        result = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["usage"]["total"], 12)
        self.assertEqual(result["request_count"], 1)
        self.assertEqual(result["rows"][0]["display_name"], "子代理 A")
        self.assertTrue(result["rows"][0]["terminal_observed"])
        with sqlite3.connect(self.root / DB_NAME) as stored:
            self.assertEqual(stored.execute("SELECT source FROM requests").fetchone()[0], "hook")

    def test_family_selects_two_children_grandchild_not_unrelated(self) -> None:
        a = self._page(
            CHILD_A, PARENT, [metadata(CHILD_A, PARENT), request(CHILD_A, "a", 10)], name="A"
        )
        b = self._page(
            CHILD_B, PARENT, [metadata(CHILD_B, PARENT), request(CHILD_B, "b", 10)], name="B"
        )
        g = self._page(
            GRANDCHILD,
            CHILD_A,
            [metadata(GRANDCHILD, CHILD_A), request(GRANDCHILD, "g", 10)],
            name="G",
        )
        u = self._page(
            UNRELATED,
            "eeeeeeee-1234-1234-1234-123456789abc",
            [
                metadata(UNRELATED, "eeeeeeee-1234-1234-1234-123456789abc"),
                request(UNRELATED, "u", 10),
            ],
            name="U",
        )
        for child, path in ((CHILD_A, a), (CHILD_B, b), (GRANDCHILD, g), (UNRELATED, u)):
            self._stop(child, path)
        result = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertEqual(result["agents_seen"], 3)
        self.assertEqual(result["agents_with_usage"], 3)
        self.assertEqual(result["usage"]["total"], 36)
        self.assertNotIn("U", {row["display_name"] for row in result["rows"]})
        self.assertEqual({row["parent_name"] for row in result["rows"]}, {"主代理", "A"})

    def test_nested_stop_uses_catalog_parent_for_hook_receipt_and_both_reports(self) -> None:
        self._page(CHILD_A, PARENT, [metadata(CHILD_A, PARENT)], name="A")
        self._page(
            GRANDCHILD,
            CHILD_A,
            [metadata(GRANDCHILD, CHILD_A), request(GRANDCHILD, "nested", 10, 23),
             complete(11)],
            name="G",
        )
        # The hook repeats the root session id for a nested agent. An absent
        # transcript path must resolve through the verified catalog row.
        self._stop(GRANDCHILD, None)
        with sqlite3.connect(self.root / DB_NAME) as stored:
            parent_hash, source = stored.execute(
                "SELECT parent_hash,source FROM requests"
            ).fetchone()
        self.assertEqual(parent_hash, stable_hash(CHILD_A))
        self.assertEqual(source, "hook")

        root_report = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20,
                              home=self.home)
        child_report = collect(self.root, CHILD_A, WINDOW_START, WINDOW_START + 20,
                               home=self.home)
        self.assertEqual(root_report["usage"]["total"], 23)
        self.assertEqual(child_report["usage"]["total"], 23)
        self.assertEqual(root_report["status"], "partial")
        self.assertEqual(child_report["status"], "observed")
        nested_row = next(row for row in root_report["rows"] if row["display_name"] == "G")
        self.assertEqual(nested_row["selector"], stable_hash(GRANDCHILD)[:12])
        self.assertEqual(child_report["rows"][0]["selector"], nested_row["selector"])
        self.assertNotIn(GRANDCHILD, json.dumps(root_report))
        self.assertEqual(nested_row["parent_name"], "A")
        self.assertEqual(child_report["rows"][0]["parent_name"], "A")

    def test_nested_stop_rejects_wrong_root_or_transcript_parent(self) -> None:
        self._page(CHILD_A, PARENT, [metadata(CHILD_A, PARENT)], name="A")
        path = self._page(
            GRANDCHILD,
            CHILD_A,
            [metadata(GRANDCHILD, CHILD_A), metadata(GRANDCHILD, PARENT),
             request(GRANDCHILD, "wrong-parent", 10, 23)],
            name="G",
        )
        self._stop(GRANDCHILD, path, parent=UNRELATED)
        self.assertFalse((self.root / DB_NAME).exists())
        self._stop(GRANDCHILD, path)
        with sqlite3.connect(self.root / DB_NAME) as stored:
            self.assertEqual(stored.execute("SELECT count(*) FROM requests").fetchone()[0], 0)
        result = collect(self.root, CHILD_A, WINDOW_START, WINDOW_START + 20,
                         home=self.home)
        self.assertIsNone(result["usage"])

    def test_duplicate_stop_and_cache_live_request_are_deduplicated(self) -> None:
        path = self._page(
            CHILD_A,
            PARENT,
            [metadata(CHILD_A, PARENT), request(CHILD_A, "same", 10), complete(11)],
            name="A",
        )
        self._stop(CHILD_A, path)
        self._stop(CHILD_A, path)
        result = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertEqual(result["request_count"], 1)
        self.assertEqual(result["usage"]["total"], 12)

    def test_window_is_strict_and_reused_child_does_not_include_history(self) -> None:
        path = self._page(
            CHILD_A,
            PARENT,
            [
                metadata(CHILD_A, PARENT),
                request(CHILD_A, "old", 10, 20),
                request(CHILD_A, "new", 20, 30),
            ],
            name="A",
        )
        self._stop(CHILD_A, path)
        result = collect(self.root, PARENT, 20 + 1_700_000_000, 21 + 1_700_000_000, home=self.home)
        self.assertEqual(result["usage"]["total"], 30)
        self.assertEqual(result["request_count"], 1)

    def test_completed_historical_children_do_not_fill_current_window_denominator(self) -> None:
        for number in range(1, 30):
            child = synthetic_child(number)
            self._page(
                child,
                PARENT,
                [
                    metadata(child, PARENT, 0),
                    request(child, f"old-{number}", 1, 20),
                    complete(2),
                ],
                name=f"historical-{number}",
            )

        result = collect(
            self.root,
            PARENT,
            WINDOW_START + 10,
            WINDOW_START + 20,
            home=self.home,
        )
        self.assertEqual(result["status"], "none")
        self.assertIsNone(result["usage"])
        self.assertEqual(result["agents_seen"], 0)
        self.assertEqual(result["agents_with_usage"], 0)
        self.assertEqual(result["pending_agents"], 0)
        self.assertEqual(result["missing_agents"], 0)
        self.assertEqual(result["rows"], [])

    def test_active_and_reused_children_still_intersect_the_window(self) -> None:
        active = synthetic_child(40)
        reused = synthetic_child(41)
        self._page(
            active,
            PARENT,
            [metadata(active, PARENT, 0), {"type": "event_msg", "timestamp": stamp(3),
             "payload": {"type": "task_started", "turn_id": "active-turn"}}],
            name="active",
        )
        self._page(
            reused,
            PARENT,
            [
                metadata(reused, PARENT, 0),
                request(reused, "before", 1, 20),
                complete(2),
                {"type": "event_msg", "timestamp": stamp(12),
                 "payload": {"type": "task_started", "turn_id": "reused-turn"}},
                request(reused, "inside", 14, 30),
                complete(15),
            ],
            name="reused",
        )

        result = collect(
            self.root,
            PARENT,
            WINDOW_START + 10,
            WINDOW_START + 20,
            home=self.home,
        )
        self.assertEqual(result["agents_seen"], 2)
        self.assertEqual(result["agents_with_usage"], 1)
        self.assertEqual(result["pending_agents"], 1)
        self.assertEqual(result["missing_agents"], 1)
        self.assertEqual(result["usage"]["total"], 30)
        self.assertEqual(result["request_count"], 1)
        self.assertEqual(
            {row["display_name"] for row in result["rows"]}, {"active", "reused"}
        )

    def test_missing_metadata_is_unknown_not_historical_exclusion(self) -> None:
        path = Path(self.temp.name) / f"{CHILD_A}-missing-metadata.jsonl"
        path.write_text(
            "".join(
                json.dumps(record) + "\n"
                for record in [
                    request(CHILD_A, "unknown", 1, 20),
                    complete(2),
                ]
            )
        )
        self._insert(
            CHILD_A,
            "missing-metadata",
            PARENT,
            {"subagent": {"thread_spawn": {"parent_thread_id": PARENT}}},
            path,
        )
        result = collect(
            self.root,
            PARENT,
            WINDOW_START + 10,
            WINDOW_START + 20,
            home=self.home,
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["agents_seen"], 1)
        self.assertEqual(result["agents_with_usage"], 0)
        self.assertEqual(result["pending_agents"], 1)
        self.assertEqual(result["missing_agents"], 1)

    def test_non_stop_capture_is_disabled_without_creating_storage(self) -> None:
        capture(
            {"hook_event_name": "UserPromptSubmit", "session_id": PARENT, "agent_id": CHILD_A},
            self.root,
            home=self.home,
        )
        self.assertFalse(self.root.exists())

    def test_source_shrink_keeps_known_usage_but_marks_partial(self) -> None:
        records = [
            metadata(CHILD_A, PARENT),
            request(CHILD_A, "first", 10, 15),
            request(CHILD_A, "second", 11, 17),
            complete(12),
        ]
        path = self._page(CHILD_A, PARENT, records, name="A")
        self._stop(CHILD_A, path)
        path.write_text("".join(json.dumps(row) + "\n" for row in (records[0], *records[2:])))
        result = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertEqual(result["usage"]["total"], 32)
        self.assertEqual(result["status"], "partial")

    def test_complete_tail_does_not_treat_header_cut_as_malformed(self) -> None:
        path = self._page(
            CHILD_A, PARENT,
            [metadata(CHILD_A, PARENT),
             {"type": "response_item", "payload": {"text": "x" * (HEADER_BYTES + 32)}},
             request(CHILD_A, "one", 10, 15), complete(11)],
            name="A",
        )
        self.assertLess(path.stat().st_size, TAIL_BYTES)
        self._stop(CHILD_A, path)
        result = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertEqual(result["usage"]["total"], 15)
        self.assertEqual(result["status"], "observed")

    def test_fork_copied_parent_request_is_excluded(self) -> None:
        path = self._page(
            CHILD_A,
            PARENT,
            [
                metadata(CHILD_A, PARENT),
                request(CHILD_A, "child", 10, 20),
                {
                    "type": "token_usage_record",
                    "timestamp": stamp(11),
                    "payload": {
                        "thread_id": PARENT,
                        "turn_id": "parent-turn",
                        "response_id": "parent-copy",
                        "usage": usage(900),
                    },
                },
                complete(12),
            ],
            name="A",
        )
        self._stop(CHILD_A, path)
        result = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertEqual(result["usage"]["total"], 20)
        self.assertEqual(result["request_count"], 1)

    def test_absent_path_preserves_cache_and_late_records_are_reconciled(self) -> None:
        path = self._page(
            CHILD_A,
            PARENT,
            [metadata(CHILD_A, PARENT), request(CHILD_A, "first", 10, 15), complete(11)],
            name="A",
        )
        self._stop(CHILD_A, path)
        path.unlink()
        self._stop(CHILD_A, None)
        cached = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertEqual(cached["usage"]["total"], 15)
        self.assertEqual(cached["status"], "partial")

        path.write_text(
            "".join(
                json.dumps(record) + "\n"
                for record in [
                    metadata(CHILD_A, PARENT),
                    request(CHILD_A, "late", 12, 17),
                    complete(13),
                ]
            )
        )
        repaired = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertEqual(repaired["usage"]["total"], 32)
        self.assertEqual(repaired["request_count"], 2)

    def test_missing_data_unknown_and_truncation_are_not_zero_or_observed(self) -> None:
        path = self._page(CHILD_A, PARENT, [metadata(CHILD_A, PARENT), complete(10)], name="A")
        self._stop(CHILD_A, path)
        missing = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertIsNone(missing["usage"])
        self.assertEqual(missing["status"], "unavailable")

        filler = self.root / "filler.jsonl"
        filler.write_bytes(
            (json.dumps(metadata(CHILD_B, PARENT)) + "\n").encode()
            + b"x" * (TAIL_BYTES + HEADER_BYTES)
            + (json.dumps(request(CHILD_B, "tail", 10, 19)) + "\n").encode()
        )
        self.db.execute(
            "INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)",
            (
                CHILD_B,
                "B",
                None,
                "worker",
                "/root/b",
                json.dumps({"subagent": {"thread_spawn": {"parent_thread_id": PARENT}}}),
                str(filler),
                1,
            ),
        )
        self.db.commit()
        self._stop(CHILD_B, filler)
        truncated = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        row = next(row for row in truncated["rows"] if row["display_name"].startswith("B"))
        self.assertEqual(row["status"], "partial")

    def test_symlink_unknown_schema_and_privacy_fail_closed(self) -> None:
        real = self._page(
            CHILD_A,
            PARENT,
            [metadata(CHILD_A, PARENT), request(CHILD_A, "secret", 10)],
            name="A",
        )
        link = Path(self.temp.name) / "link.jsonl"
        link.symlink_to(real)
        self.db.execute("UPDATE threads SET rollout_path=? WHERE id=?", (str(link), CHILD_A))
        self.db.commit()
        self._stop(CHILD_A, link)
        result = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertEqual(result["status"], "unavailable")

        bad_home = Path(self.temp.name) / "bad-home"
        bad_home.mkdir()
        self.assertEqual(
            collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=bad_home)["status"],
            "unavailable",
        )

        db_path = self.root / DB_NAME
        if db_path.exists():
            raw = db_path.read_bytes()
            for private in ("secret", str(real), CHILD_A, "private-response"):
                self.assertNotIn(private.encode(), raw)
            self.assertTrue(stat.S_ISREG(db_path.stat().st_mode))
            self.assertEqual(db_path.stat().st_mode & 0o777, 0o600)

    def test_conflict_identity_and_terminal_restart_stay_partial(self) -> None:
        path = self._page(
            CHILD_A,
            PARENT,
            [
                metadata(CHILD_A, PARENT),
                request(CHILD_A, "same", 10, 10),
                complete(11),
                request(CHILD_A, "same", 12, 20),
            ],
            name="A",
        )
        self._stop(CHILD_A, path)
        result = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertIsNone(result["usage"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["pending_agents"], 1)

        bad = self._page(
            CHILD_B,
            PARENT,
            [
                metadata(CHILD_B, PARENT),
                {**request(CHILD_B, "missing-id", 10), "payload": {"usage": usage()}},
            ],
            name="B",
        )
        self._stop(CHILD_B, bad)
        result = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(any(row["status"] == "partial" for row in result["rows"]))


    def test_conflicting_parent_metadata_rejects_native_requests_in_both_orders(self) -> None:
        for index, (matching_first, capture_first) in enumerate(
            ((True, False), (False, False), (True, True), (False, True)), start=1
        ):
            with self.subTest(matching_first=matching_first, capture_first=capture_first):
                child = synthetic_child(index)
                parents = (PARENT, UNRELATED) if matching_first else (UNRELATED, PARENT)
                path = self._page(
                    child,
                    PARENT,
                    [metadata(child, parents[0]), metadata(child, parents[1], 1),
                     request(child, f"conflicting-{index}", 10, 23), complete(11)],
                    name=f"conflicting-{index}",
                )
                if capture_first:
                    self._stop(child, path)

                result = collect(
                    self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home
                )

                self.assertIsNone(result["usage"])
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["request_count"], 0)
                self.assertEqual(result["agents_with_usage"], 0)
                self.assertEqual(result["missing_agents"], index)
                self.assertTrue(all(row["usage"] is None for row in result["rows"]))


    def test_parent_metadata_conflict_invalidates_cached_native_requests(self) -> None:
        records = [metadata(CHILD_A, PARENT), request(CHILD_A, "cached", 10, 23), complete(11)]
        path = self._page(CHILD_A, PARENT, records, name="A")
        self._stop(CHILD_A, path)
        before = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertEqual(before["usage"]["total"], 23)

        with path.open("a") as output:
            output.write(json.dumps(metadata(CHILD_A, UNRELATED, 12)) + "\n")
        after = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertIsNone(after["usage"])
        self.assertEqual(after["status"], "partial")
        self.assertEqual(after["request_count"], 0)
        self.assertEqual(after["missing_agents"], 1)

        # A later unreadable source cannot resurrect receipts already known
        # to have contradictory attribution.
        self._stop(CHILD_A, path)
        path.unlink()
        cached = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertIsNone(cached["usage"])
        self.assertEqual(cached["status"], "partial")
        self.assertEqual(cached["request_count"], 0)


    def test_stop_parent_metadata_conflict_revokes_cache_before_source_disappears(self) -> None:
        path = self._page(
            CHILD_A, PARENT,
            [metadata(CHILD_A, PARENT), request(CHILD_A, "cached", 10, 23), complete(11)],
            name="A",
        )
        self._stop(CHILD_A, path)
        with path.open("a") as output:
            output.write(json.dumps(metadata(CHILD_A, UNRELATED, 12)) + "\n")
        self._stop(CHILD_A, path)
        path.unlink()

        result = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertIsNone(result["usage"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["request_count"], 0)


    def test_revocation_rejects_delayed_and_later_normal_scans_after_reopen(self) -> None:
        records = [metadata(CHILD_A, PARENT), request(CHILD_A, "first", 10, 23)]
        path = self._page(CHILD_A, PARENT, records, name="A")
        self._stop(CHILD_A, path)
        records += [request(CHILD_A, "delayed", 11, 7), complete(12)]
        path.write_text("".join(json.dumps(row) + "\n" for row in records))
        delayed_scan = _scan(str(path), expected_child=CHILD_A, expected_parent=PARENT)

        with path.open("a") as output:
            output.write(json.dumps(metadata(CHILD_A, UNRELATED, 13)) + "\n")
        self._stop(CHILD_A, path)
        _save_scan(
            self.root, delayed_scan, parent_hash=stable_hash(PARENT),
            agent_hash=stable_hash(CHILD_A), now=WINDOW_START + 14,
        )
        # A normal-looking later file is not proof that the old lineage
        # dispute has been resolved, even when the request id is new.
        path.write_text("".join(json.dumps(row) + "\n" for row in (
            metadata(CHILD_A, PARENT), request(CHILD_A, "after-conflict", 15, 9), complete(16)
        )))
        self._stop(CHILD_A, path)
        path.unlink()

        with sqlite3.connect(self.root / DB_NAME) as reopened:
            self.assertEqual(reopened.execute(
                "SELECT count(*) FROM requests WHERE child_hash=? AND conflict=0",
                (stable_hash(CHILD_A),),
            ).fetchone()[0], 0)
        result = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertIsNone(result["usage"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["request_count"], 0)


    def test_revocation_without_existing_agent_or_requests_survives_reopen(self) -> None:
        path = self._page(
            CHILD_A, PARENT,
            [metadata(CHILD_A, PARENT), request(CHILD_A, "delayed", 10, 7), complete(11)],
            name="A",
        )
        delayed_scan = _scan(str(path), expected_child=CHILD_A, expected_parent=PARENT)
        path.write_text("".join(json.dumps(row) + "\n" for row in (
            metadata(CHILD_A, PARENT), metadata(CHILD_A, UNRELATED, 1)
        )))
        self._stop(CHILD_A, path)
        _save_scan(
            self.root, delayed_scan, parent_hash=stable_hash(PARENT),
            agent_hash=stable_hash(CHILD_A), now=WINDOW_START + 12,
        )
        path.unlink()
        with sqlite3.connect(self.root / DB_NAME) as reopened:
            self.assertEqual(reopened.execute("SELECT count(*) FROM requests").fetchone()[0], 0)
            self.assertEqual(reopened.execute("SELECT count(*) FROM agents").fetchone()[0], 0)
        result = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertIsNone(result["usage"])
        self.assertEqual(result["status"], "unavailable")


    def test_collect_rechecks_revocation_after_cache_load_and_fresh_scan(self) -> None:
        for phase in ("after-cache", "after-scan"):
            with self.subTest(phase=phase):
                data_root = self.root / phase
                records = [metadata(CHILD_A, PARENT), request(CHILD_A, "first", 10, 23)]
                if phase == "after-cache":
                    path = self._page(CHILD_A, PARENT, records, name="A")
                else:
                    path = self.paths[CHILD_A]
                    path.write_text("".join(json.dumps(row) + "\n" for row in records))
                original = _scan(str(path), expected_child=CHILD_A, expected_parent=PARENT)
                _save_scan(
                    data_root, original, parent_hash=stable_hash(PARENT),
                    agent_hash=stable_hash(CHILD_A), now=WINDOW_START + 11,
                )
                with path.open("a") as output:
                    output.write(json.dumps(metadata(CHILD_A, UNRELATED, 12)) + "\n")
                conflict = _scan(str(path), expected_child=CHILD_A, expected_parent=PARENT)
                path.write_text("".join(json.dumps(row) + "\n" for row in (
                    *records, request(CHILD_A, "delayed", 13, 7), complete(14)
                )))

                def revoke() -> None:
                    _save_scan(
                        data_root, conflict, parent_hash=stable_hash(PARENT),
                        agent_hash=stable_hash(CHILD_A), now=WINDOW_START + 15,
                    )

                def load_then_revoke(*args, **kwargs):
                    loaded = _load_cache(*args, **kwargs)
                    revoke()
                    path.unlink()
                    return loaded

                def scan_then_revoke(*args, **kwargs):
                    scanned = _scan(*args, **kwargs)
                    revoke()
                    return scanned

                target, replacement = (
                    ("_load_cache", load_then_revoke) if phase == "after-cache"
                    else ("_scan", scan_then_revoke)
                )
                with mock.patch(
                    f"codex_usage_reports.child_usage.{target}", side_effect=replacement
                ):
                    result = collect(
                        data_root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home
                    )
                self.assertIsNone(result["usage"])
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["request_count"], 0)


    def test_revoked_child_does_not_reject_a_new_child_hash(self) -> None:
        revoked = self._page(
            CHILD_A, PARENT,
            [metadata(CHILD_A, PARENT), metadata(CHILD_A, UNRELATED, 1)], name="revoked",
        )
        self._stop(CHILD_A, revoked)
        fresh = self._page(
            CHILD_B, PARENT,
            [metadata(CHILD_B, PARENT), request(CHILD_B, "fresh", 10, 23), complete(11)],
            name="fresh",
        )
        self._stop(CHILD_B, fresh)
        result = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertEqual(result["usage"]["total"], 23)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["agents_with_usage"], 1)
        self.assertEqual(result["missing_agents"], 1)


    def test_revocation_capacity_is_bounded_without_forgetting_rejection(self) -> None:
        fresh = self._page(
            GRANDCHILD, PARENT,
            [metadata(GRANDCHILD, PARENT), request(GRANDCHILD, "fresh", 10, 23), complete(11)],
            name="fresh",
        )
        self._stop(GRANDCHILD, fresh)
        with mock.patch("codex_usage_reports.child_usage.MAX_DB_AGENTS", 1):
            for child in (CHILD_A, CHILD_B):
                path = self._page(
                    child, PARENT,
                    [metadata(child, PARENT), metadata(child, UNRELATED, 1)], name="revoked",
                )
                self._stop(child, path)
        with sqlite3.connect(self.root / DB_NAME) as reopened:
            rows = reopened.execute("SELECT child_hash FROM child_revocations").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertIn(("*",), rows)
        self.assertIn((stable_hash(CHILD_A),), rows)
        result = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertIsNone(result["usage"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["request_count"], 0)


    def test_cache_without_revocation_table_is_upgraded_without_losing_usage(self) -> None:
        path = self._page(
            CHILD_A, PARENT,
            [metadata(CHILD_A, PARENT), request(CHILD_A, "legacy", 10, 23), complete(11)],
            name="A",
        )
        self._stop(CHILD_A, path)
        with sqlite3.connect(self.root / DB_NAME) as legacy:
            legacy.execute("DROP TABLE child_revocations")
        result = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertEqual(result["usage"]["total"], 23)
        self.assertEqual(result["status"], "observed")
        with sqlite3.connect(self.root / DB_NAME) as upgraded:
            self.assertEqual(upgraded.execute(
                "SELECT count(*) FROM child_revocations"
            ).fetchone()[0], 0)


    def test_unreadable_revocation_gate_cannot_count_live_or_cached_usage(self) -> None:
        path = self._page(
            CHILD_A, PARENT,
            [metadata(CHILD_A, PARENT), request(CHILD_A, "cached", 10, 23), complete(11)],
            name="A",
        )
        self._stop(CHILD_A, path)
        with sqlite3.connect(self.root / DB_NAME) as broken:
            broken.execute("ALTER TABLE child_revocations RENAME COLUMN child_hash TO unexpected")
        result = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertIsNone(result["usage"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["request_count"], 0)


    def test_fork_copied_parent_metadata_preserves_child_attribution(self) -> None:
        path = self._page(
            CHILD_A,
            PARENT,
            [
                {"type": "session_meta", "timestamp": stamp(0),
                 "payload": {"id": PARENT, "source": "vscode"}},
                request(PARENT, "parent-copy", 1, 900),
                complete(2, identifier=PARENT),
                metadata(CHILD_A, PARENT, 3),
                request(CHILD_A, "child", 10, 23),
                complete(11, identifier=CHILD_A),
            ],
            name="A",
        )
        self._stop(CHILD_A, path)
        result = collect(self.root, PARENT, WINDOW_START, WINDOW_START + 20, home=self.home)
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["usage"]["total"], 23)
        self.assertEqual(result["request_count"], 1)



if __name__ == "__main__":
    unittest.main()
