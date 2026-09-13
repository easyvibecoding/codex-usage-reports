from __future__ import annotations

import copy
import sys
import unittest
from html import escape
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/codex-usage-reports/lib"))

from codex_usage_reports.auto_preview import render_card  # noqa: E402
from codex_usage_reports.quota_view import render_html, render_markdown  # noqa: E402
from codex_usage_reports.report_i18n import LOCALES, ReportText  # noqa: E402


def quota(*rows):
    return {"status": "observed", "captured_at": 1790000000, "previous_captured_at": 1789999990,
            "rows": list(rows)}


def row(**changes):
    return {"bucket": "codex", "duration_minutes": 10080, "remaining_percent": 72,
            "delta_pp": -1, "comparison": "observed", "resets_at": 1790100000, **changes}


class QuotaViewTest(unittest.TestCase):
    def test_inline_quota_is_collapsible_but_stop_html_remains_expanded(self):
        for locale in LOCALES:
            text = ReportText(locale)
            for source in (None, quota(row())):
                folded = render_html(source, text, collapsible=True)
                expanded = render_html(source, text)
                title = escape(text("quota_heading"))
                self.assertIn(f'<summary>{title}', folded)
                self.assertTrue(folded.endswith('</details>'))
                self.assertNotIn(' open', folded)
                self.assertIn(f'<h3>{title}</h3>', expanded)
                self.assertTrue(expanded.endswith('</section>'))

    def test_collapsed_weekly_summary_uses_only_main_window_and_localized_values(self):
        for locale in LOCALES:
            text = ReportText(locale)
            source = quota(row(remaining_percent=72.125, delta_pp=-0.125),
                           row(bucket="codex_bengalfox", remaining_percent=100, delta_pp=-42))
            output = render_html(source, text, collapsible=True)
            summary = output.split('</summary>', 1)[0]
            self.assertIn('class="report-quota-weekly"', summary)
            for expected in (text("quota_days", value=text.number(7)),
                             text("quota_percent", value=text.number(72.125)),
                             text("quota_delta_pp", value=text.number(-0.125))):
                self.assertIn(escape(expected), summary)
            self.assertNotIn("100", summary)
            self.assertNotIn("-42", summary)
            self.assertNotIn('report-quota-weekly', render_html(source, text))

    def test_collapsed_weekly_summary_preserves_unknown_reset_and_unchanged(self):
        text = ReportText("zh-Hant")
        for status in ("unchanged", "reset", "expired", "previous_unavailable", "not_comparable"):
            summary = render_html(quota(row(comparison=status, delta_pp=-42)), text,
                                  collapsible=True).split('</summary>', 1)[0]
            self.assertIn(escape(text("quota_" + status)), summary)
            self.assertNotIn("-42", summary)
            if status == "expired":
                self.assertNotIn("72%", summary)
        for source in (None, quota(row(bucket="spark")), quota(row(duration_minutes=300)),
                       quota(row(), row())):
            summary = render_html(source, text, collapsible=True).split('</summary>', 1)[0]
            self.assertNotIn('report-quota-weekly', summary)
        source = quota(row())
        source["status"] = "unavailable"
        summary = render_html(source, text, collapsible=True).split('</summary>', 1)[0]
        self.assertIn(text("not_observed"), summary)
        self.assertNotIn("72%", summary)

    def test_remaining_and_signed_percentage_points_preserve_small_observations(self):
        for locale, expected in (("en", "-0.125 pp"), ("de", "-0,125 Prozentpunkte")):
            text = ReportText(locale)
            source = quota(row(remaining_percent=72.125, delta_pp=-0.125))
            for renderer in (render_html, render_markdown):
                rendered = renderer(source, text)
                self.assertIn(expected, rendered)
                self.assertIn(text.number(72.125), rendered)
                self.assertNotIn("-0.125%", rendered)
                self.assertNotIn("-0,125 %", rendered)
            tiny = render_html(quota(row(remaining_percent=0.000001, delta_pp=-0.000001)), text)
            self.assertIn(text.number(0.000001), tiny)
            self.assertNotIn('>0%</td>', tiny)

    def test_unchanged_is_not_zero_consumption_or_claim_of_hidden_precision(self):
        text = ReportText("zh-Hant")
        for comparison in ("observed", "unchanged"):
            output = render_html(quota(row(comparison=comparison, delta_pp=0)), text)
            self.assertIn("顯示未變", output)
            self.assertIn("顯示未變不代表沒有使用", output)
            for forbidden in ("0 百分點", "<1%", "低於 1%", "免費"):
                self.assertNotIn(forbidden, output)

    def test_reset_rise_and_incomparable_never_present_a_consumption_delta(self):
        text = ReportText("en")
        for comparison in ("baseline", "previous_unavailable", "reset", "corrected",
                           "not_comparable"):
            source = quota(row(comparison=comparison, delta_pp=-42))
            for renderer in (render_html, render_markdown):
                output = renderer(source, text)
                self.assertIn(escape(text("quota_" + comparison)), output)
                self.assertNotIn("-42", output)
        output = render_html(quota(row(delta_pp=20)), text)
        self.assertIn(text("quota_corrected"), output)
        self.assertNotIn("20 pp", output)

    def test_expired_remaining_is_unknown_and_missing_legacy_snapshot_is_not_zero(self):
        text = ReportText("en")
        output = render_html(quota(row(comparison="expired", remaining_percent=72)), text)
        self.assertIn(text("quota_expired"), output)
        self.assertIn(text("not_observed"), output)
        self.assertNotIn("72%", output)
        source = quota(row(comparison="expired"), row(comparison="not_comparable"))
        source["status"] = "unavailable"
        for renderer in (render_html, render_markdown):
            output = renderer(source, text)
            self.assertIn(text("quota_expired"), output)
            self.assertIn(text("quota_not_comparable"), output)
            self.assertNotIn("72%", output)
        for source in (None, {}, {"status": "unavailable"}, quota()):
            for renderer in (render_html, render_markdown):
                output = renderer(source, text)
                self.assertIn(text("quota_unavailable"), output)
                self.assertNotIn("0%", output)

    def test_native_plan_display_has_no_inferred_multiplier_or_window(self):
        text = ReportText("en")
        for plan, expected in (("plus", "Plus"), ("pro", "Pro"), ("edu_plus", "edu_plus"),
                               (None, text("quota_plan_unknown")),
                               ("private-untrusted-plan", text("quota_plan_unknown"))):
            source = {**quota(row()), "plan_type": plan}
            for renderer in (render_html, render_markdown):
                output = renderer(source, text)
                self.assertIn(text("quota_plan", plan=expected), output)
                self.assertIn("7 d", output)
                for forbidden in ("5x", "20x", "5 h", "private-untrusted-plan"):
                    self.assertNotIn(forbidden, output)

    def test_windows_are_named_from_native_duration_and_buckets_are_neutral(self):
        text = ReportText("zh-Hant")
        source = quota(row(), row(bucket="spark", duration_minutes=300),
                       row(bucket="codex_bengalfox", duration_minutes=90),
                       row(bucket="private-hash-abc", duration_minutes=None))
        for renderer in (render_html, render_markdown):
            output = renderer(source, text)
            for expected in ("Codex · 7 天", "Spark · 5 小時", "Codex（另一額度） · 90 分鐘",
                             "其他額度 · 窗口未知"):
                self.assertIn(expected, output)
            self.assertNotIn("private-hash", output)
        weekly_only = render_html(quota(row()), text)
        self.assertNotIn("5 小時", weekly_only)

    def test_invalid_numeric_and_untrusted_fields_are_not_rendered(self):
        text = ReportText("en")
        for invalid in (None, True, "<script>private-value</script>", float("nan"),
                        float("inf"), -1, 101, 10 ** 1000):
            source = quota(row(bucket={"private": "value"}, duration_minutes=True,
                               remaining_percent=invalid, delta_pp=-0.1))
            source["captured_at"] = "private-time"
            source["extra"] = "private-value"
            for renderer in (render_html, render_markdown):
                output = renderer(source, text)
                self.assertIn(text("not_observed"), output)
                for forbidden in ("private", "<script", "-0.1 pp", "NaN", "Infinity"):
                    self.assertNotIn(forbidden, output)

    def test_all_locales_render_same_data_without_state_or_catalog_changes(self):
        source = quota(row(), row(bucket="spark", comparison="unchanged", delta_pp=0))
        before = copy.deepcopy(source)
        for locale in LOCALES:
            text = ReportText(locale)
            for renderer in (render_html, render_markdown):
                output = renderer(source, text)
                self.assertIn(escape(text("quota_heading")), output)
                self.assertIn(escape(text("quota_scope_note")), output)
                self.assertIn(escape(text("quota_precision_note")), output)
                self.assertIn(escape(text("quota_unchanged")), output)
                self.assertIn("2026-", output)
            self.assertEqual(source, before)

    def test_inline_card_adds_one_account_section_without_changing_token_scope(self):
        zero = dict.fromkeys(("total", "input", "cached_input", "output", "reasoning_output"), 0)
        receipt = {
            "key": "example", "locale": "en", "task_name": "Example <Task>",
            "usage": {**zero, "total": 5}, "task_usage": {**zero, "total": 100},
            "usage_status": "native_turn_counter", "contexts": [], "elapsed_seconds": 10,
            "captured_at": "00:00:00 UTC", "subagents": {"status": "none"},
            "quota": quota(row()),
        }
        card = render_card(receipt)
        self.assertEqual(card.count('class="report-quota"'), 1)
        self.assertIn('data-metric="task-total">100</dd>', card)
        self.assertIn('data-metric="turn-delta">+5</dd>', card)
        self.assertIn("72%", card)
        self.assertIn("-1 pp", card)
        self.assertIn('scope="col"', card)
        self.assertIn('scope="row"', card)
        self.assertNotIn("Fast", card)
        class Structure(HTMLParser):
            def __init__(self):
                super().__init__()
                self.depth = 0
                self.disclosures = []
                self.metrics = []

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "details":
                    self.disclosures.append((self.depth, "open" in attrs, attrs.get("class")))
                    self.depth += 1
                if "data-metric" in attrs:
                    self.metrics.append((attrs["data-metric"], self.depth))

            def handle_endtag(self, tag):
                if tag == "details":
                    self.depth -= 1

        structure = Structure()
        structure.feed(card)
        self.assertEqual(structure.disclosures, [
            (0, False, "report-scope"), (0, False, "report-quota"),
            (0, False, "report-context"), (0, False, None),
        ])
        self.assertEqual(structure.metrics, [("task-total", 0), ("turn-delta", 0)])


if __name__ == "__main__":
    unittest.main()
