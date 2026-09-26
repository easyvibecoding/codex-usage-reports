from __future__ import annotations

import copy
import sys
import unittest
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/codex-usage-reports/lib"))

from codex_usage_reports.auto_preview import render_card  # noqa: E402
from codex_usage_reports.auto_report import _documents  # noqa: E402
from codex_usage_reports.report_i18n import LOCALES, ReportText  # noqa: E402

CHILD_SCOPES = ("subagent", "agent_turn_stop_boundary", "agent_turn_completion_boundary")
ROOT_SCOPES = ("parent", "user_turn_stop_boundary", "user_turn_completion_boundary")


class ChildLocalizationTest(unittest.TestCase):
    def receipt(self, locale="en", scope="agent_turn_stop_boundary"):
        return {
            "key": "synthetic", "locale": locale, "scope": scope,
            "task_name": "Synthetic task",
            "task": {"display_name": "Synthetic task", "selector": "a" * 12,
                     "parent_name": "Synthetic parent"},
            "task_hash": "a" * 64, "turn_hash": "b" * 64,
            "source_identity_verified": True,
            "usage": {"total": 50, "input": 40, "cached_input": 32,
                      "output": 10, "reasoning_output": 5},
            "task_usage": {"total": 1500, "input": 1200, "cached_input": 1000,
                           "output": 300, "reasoning_output": 100},
            "usage_status": "boundary_counter_difference",
            "contexts": [{"model": "synthetic-model", "reasoning_effort": "high"}],
            "stop_contexts": [{"model": "synthetic-model", "reasoning_effort": "high"}],
            "elapsed_seconds": 60, "captured_at": "12:01:00 UTC",
            "started_at": "2026-09-13T12:00:00Z", "stopped_at": "2026-09-13T12:01:00Z",
            "completion_observed": scope.endswith("completion_boundary"),
            "subagents": {"status": "none"},
        }

    def test_child_card_labels_cover_all_child_scopes_and_nine_languages(self):
        labels = {
            "task_total": "child_task_total", "turn_delta": "child_turn_delta",
            "context_heading": "child_context_heading", "combined": "child_combined",
            "child_subtotal": "child_descendant_subtotal", "card_note": "child_card_note",
        }
        for locale in LOCALES:
            text = ReportText(locale)
            for scope in ROOT_SCOPES:
                with self.subTest(locale=locale, scope=scope):
                    receipt = self.receipt(locale, scope)
                    before = copy.deepcopy(receipt)
                    card = render_card(receipt)
                    for root_key in labels:
                        self.assertIn(escape(text(root_key)), card)
                    self.assertIn("<dt>" + escape(text("parent")) + "</dt>", card)
                    self.assertEqual(receipt, before)
            for scope in CHILD_SCOPES:
                with self.subTest(locale=locale, scope=scope):
                    receipt = self.receipt(locale, scope)
                    before = copy.deepcopy(receipt)
                    card = render_card(receipt)
                    for root_key, child_key in labels.items():
                        self.assertIn(escape(text(child_key)), card)
                        self.assertNotIn(escape(text(root_key)), card)
                    self.assertIn("<dt>" + escape(text("own_agent")) + "</dt>", card)
                    self.assertNotIn("<dt>" + escape(text("parent")) + "</dt>", card)
                    self.assertIn('data-metric="task-total">' + text.number(1500), card)
                    self.assertIn('data-metric="turn-delta">+50</dd>', card)
                    self.assertEqual(receipt, before)

    def test_document_labels_and_boundary_notes_preserve_root_and_child_scopes(self):
        labels = {
            "task_total": "child_task_total", "turn_delta": "child_turn_delta",
            "context_heading": "child_context_heading", "children": "child_descendant_subtotal",
            "note_usage": "child_note_usage",
        }
        for locale in LOCALES:
            text = ReportText(locale)
            for scope in (*ROOT_SCOPES, *CHILD_SCOPES):
                with self.subTest(locale=locale, scope=scope):
                    receipt = self.receipt(locale, scope)
                    before = copy.deepcopy(receipt)
                    markdown, page = _documents(receipt)
                    is_child = scope in CHILD_SCOPES
                    note = "note_completion" if receipt["completion_observed"] else "note_stop"
                    for document, escaped in ((markdown, False), (page, True)):
                        for root_key, child_key in labels.items():
                            wanted = text(child_key if is_child else root_key)
                            self.assertIn(escape(wanted) if escaped else wanted, document)
                            if is_child:
                                unwanted = text(root_key)
                                self.assertNotIn(
                                    escape(unwanted) if escaped else unwanted, document,
                                )
                        wanted = text("child_" + note if is_child else note)
                        self.assertIn(escape(wanted) if escaped else wanted, document)
                    self.assertEqual(receipt, before)

    def test_child_documents_without_verified_task_do_not_become_parent_reports(self):
        for locale in LOCALES:
            text = ReportText(locale)
            for scope in CHILD_SCOPES:
                with self.subTest(locale=locale, scope=scope):
                    receipt = self.receipt(locale, scope)
                    receipt.update(task=None, source_identity_verified=False, usage=None,
                                   task_usage=None, stop_contexts=[])
                    markdown, page = _documents(receipt)
                    for document, escaped in ((markdown, False), (page, True)):
                        for key in ("task_total", "turn_delta", "context_heading"):
                            wanted, unwanted = text("child_" + key), text(key)
                            self.assertIn(escape(wanted) if escaped else wanted, document)
                            self.assertNotIn(escape(unwanted) if escaped else unwanted, document)
                        self.assertIn(text("unnamed"), document)
                        self.assertIn(text("not_observed"), document)
                        self.assertNotIn("Synthetic parent", document)
                        self.assertNotIn("@" + "a" * 12, document)

    def test_nested_parent_names_and_selectors_are_consistent_and_escaped(self):
        parent_name = 'Synthetic <parent> [link](https://example.invalid) | *name* "q"'
        child_name = "Synthetic <child> [nested] $total"
        grandchild_name = "Synthetic <descendant> [leaf]"
        selector, descendant_selector = "a" * 12, "c" * 12
        descendant_usage = {"total": 23, "input": 20, "cached_input": 10,
                            "output": 3, "reasoning_output": 2}
        for locale in LOCALES:
            with self.subTest(locale=locale):
                text = ReportText(locale)
                child = self.receipt(locale)
                child["task"] = {"display_name": child_name, "selector": selector,
                                 "parent_name": parent_name}
                child["task_name"] = (child_name + " · @" + selector + " · "
                                      + text("owner", name=parent_name))
                child["subagents"] = {
                    "status": "observed", "agents_with_usage": 1, "agents_seen": 1,
                    "usage": descendant_usage,
                    "rows": [{"display_name": grandchild_name, "selector": descendant_selector,
                              "parent_name": child_name, "usage": descendant_usage,
                              "terminal_observed": True}],
                }
                root = self.receipt(locale, "user_turn_stop_boundary")
                root["subagents"] = {
                    "status": "observed", "agents_with_usage": 1, "agents_seen": 1,
                    "usage": child["usage"],
                    "rows": [{**child["task"], "usage": child["usage"],
                              "terminal_observed": True}],
                }
                for receipt in (root, child):
                    before = copy.deepcopy(receipt)
                    card = render_card(receipt)
                    markdown, page = _documents(receipt)
                    for document in (card, markdown, page):
                        self.assertIn("@" + selector, document)
                    for document in (card, page):
                        self.assertIn(escape(text("owner", name=parent_name)), document)
                        self.assertIn(escape(child_name), document)
                        self.assertNotIn("Synthetic <parent>", document)
                        self.assertNotIn("Synthetic <child>", document)
                    self.assertIn("&lt;parent&gt; &#91;link&#93;", markdown)
                    self.assertIn("&#124; &#42;name&#42;", markdown)
                    self.assertNotIn("[link](https://example.invalid)", markdown)
                    self.assertIn("&lt;child&gt; &#91;nested&#93; $total", markdown)
                    self.assertEqual(receipt, before)
                card = render_card(child)
                markdown, page = _documents(child)
                for document in (card, page):
                    self.assertIn(escape(text("owner", name=child_name)), document)
                    self.assertIn(escape(grandchild_name), document)
                    self.assertIn("@" + descendant_selector, document)
                self.assertIn("&lt;descendant&gt; &#91;leaf&#93;", markdown)
                self.assertIn("## " + text("child_descendant_subtotal"), markdown)
                self.assertIn('data-metric="task-total">' + text.number(1500), card)
                self.assertIn('data-metric="turn-delta">+50</dd>', card)
                self.assertIn('class="tabular-nums">73</strong>', card)
                self.assertIn("<dd>23</dd>", card)


if __name__ == "__main__":
    unittest.main()
