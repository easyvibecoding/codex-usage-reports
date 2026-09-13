from __future__ import annotations

import json
import sys
import tempfile
import unittest
from html import escape
from pathlib import Path
from string import Formatter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/codex-usage-reports/lib"))

from codex_usage_reports.auto_preview import render_card  # noqa: E402
from codex_usage_reports.auto_report import _documents, _pending, handle, recent  # noqa: E402
from codex_usage_reports.report_i18n import (  # noqa: E402
    CONFIG_BYTES,
    LOCALES,
    ReportText,
    _catalog,
    _legacy_override,
    human_text,
    normalize,
    resolve_locale,
)
from test_auto_report import counter  # noqa: E402


class ReportI18nTest(unittest.TestCase):
    def test_human_seam_is_explicit_and_technical_values_do_not_resolve_preferences(self):
        with patch("codex_usage_reports.report_i18n.resolve_locale") as resolver:
            text = ReportText("de")
            self.assertEqual(text.number(12.5), "12,5")
            self.assertEqual(text.number(12000), "12.000")
            resolver.assert_not_called()
            resolver.return_value = {"locale": "ja", "locale_source": "example"}
            self.assertEqual(human_text(home=Path("example-home")).locale, "ja")
            resolver.assert_called_once_with(home=Path("example-home"))

    def test_catalog_paths_are_fixed_and_fallback_is_domain_local(self):
        with self.assertRaises(ValueError):
            _catalog("en", "../../private")
        with self.assertRaises(ValueError):
            _catalog("../../private")
        def catalog(locale, domain="turn"):
            if domain == "turn":
                return {"common": "共有"}
            return {"specific": "English {value}"} if locale == "en" else {}
        with patch("codex_usage_reports.report_i18n._catalog", side_effect=catalog):
            text = ReportText("ja", domain="cli")
            self.assertEqual(text("specific", value="Native"), "English Native")
            self.assertEqual(text("common"), "共有")

    def test_locale_normalization_and_unknown_fallback(self):
        for value, expected in {
            "en-US": "en", "zh_TW": "zh-Hant", "zh-Hant-TW": "zh-Hant",
            "zh-HK": "zh-Hant", "zh-MO": "zh-Hant", "zh-Hans-TW": "zh-Hans",
            "zh-CN": "zh-Hans", "zh": "zh-Hans", "ja-JP": "ja", "fr-CA": "fr",
            "pt-BR": "pt", "es-419": "es", "de-DE": "de", "ko-KR": "ko",
        }.items():
            self.assertEqual(normalize(value), expected)
        for value in (None, {}, "../../en", "en<script>", "auto", "C.UTF-8", "xx-YY"):
            self.assertIsNone(normalize(value))
            self.assertEqual(ReportText(value).locale, "en")

    def test_all_catalog_keys_placeholders_and_formats(self):
        base = ReportText("en").values
        def fields(value):
            return {name for _, name, _, _ in Formatter().parse(value) if name}
        for locale in LOCALES:
            text = ReportText(locale)
            self.assertEqual(set(text.values), set(base), locale)
            for key, value in text.values.items():
                self.assertTrue(value, (locale, key))
                self.assertEqual(fields(value), fields(base[key]), (locale, key))
            self.assertNotEqual(text("unknown"), "0")
        self.assertEqual(ReportText("de").number(12345), "12.345")
        self.assertEqual(ReportText("de").number(12.5, 1), "12,5")
        self.assertEqual(ReportText("fr").number(12345), "12\u202f345")
        self.assertEqual(ReportText("en").number(12345), "12,345")

    def receipt(self, locale):
        return {
            "key": "safe", "locale": locale, "task_name": "Native <Task> $total",
            "task": {"display_name": "Native <Task> $total"},
            "task_hash": "a" * 64, "turn_hash": "b" * 64,
            "usage": {"total": 12345, "input": 12000, "cached_input": 10000,
                      "output": 345, "reasoning_output": 100},
            "usage_status": "boundary_counter_difference", "contexts": [],
            "stop_contexts": [], "start_model": None, "stop_model": None,
            "elapsed_seconds": 60, "captured_at": "12:34:56 UTC",
            "started_at": "2026-09-13T00:00:00Z", "stopped_at": "2026-09-13T00:01:00Z",
            "subagents": {"status": "none"},
        }

    def test_every_language_renders_without_mutating_usage_or_native_names(self):
        for locale in LOCALES:
            receipt = self.receipt(locale)
            before = json.dumps(receipt)
            text = ReportText(locale)
            card = render_card(receipt)
            markdown, page = _documents(receipt)
            for document in (card, markdown, page):
                self.assertIn(text.number(12345), document)
                self.assertIn("Native &lt;Task&gt; $total", document)
            self.assertIn(text("not_observed"), card)
            for document in (markdown, page):
                self.assertTrue(text("unknown") in document or text("not_observed") in document)
            self.assertIn(escape(text("task_total")), card)
            self.assertIn(escape(text("turn_delta")), card)
            self.assertIn(escape(text("context_heading")), card)
            self.assertNotIn("Fast", card)
            self.assertIn('lang="' + locale + '"', card)
            self.assertIn('lang="' + locale + '"', page)
            self.assertIn(escape(text("card_title")), card)
            self.assertIn(text("pending"), _pending("safe", locale))
            self.assertEqual(json.dumps(receipt), before)
            if locale in ("en", "de", "fr", "es", "pt"):
                for document in (card, markdown, page):
                    self.assertNotRegex(document, r"[\u3400-\u9fff]")

    def test_desktop_override_wins_and_is_reread_without_settings_mutation(self):
        with tempfile.TemporaryDirectory() as temporary, patch(
            "codex_usage_reports.report_i18n._system_locale", side_effect=AssertionError("no read")
        ):
            home = Path(temporary)
            path = home / "config.toml"
            for locale, expected in (("ja-JP", "ja"), ("zh-TW", "zh-Hant"), ("en-US", "en")):
                raw = f'[desktop]\nlocaleOverride = "{locale}"\nprivate = "do-not-export"\n'
                path.write_text(raw)
                self.assertEqual(resolve_locale(home=home), {
                    "locale": expected, "locale_source": "codex_desktop_override"
                })
                self.assertEqual(path.read_text(), raw)
            path.write_text('[desktop]\nlocaleOverride = "ar"\n')
            self.assertEqual(resolve_locale(home=home), {
                "locale": "en", "locale_source": "unsupported_override_english"
            })

    def test_auto_missing_invalid_and_unsafe_config_use_bounded_fallback(self):
        with tempfile.TemporaryDirectory() as temporary, patch(
            "codex_usage_reports.report_i18n._system_locale",
            return_value=("zh-Hant-TW", "macos_system_language"),
        ):
            home = Path(temporary)
            path = home / "config.toml"
            self.assertEqual(resolve_locale(home=home)["locale"], "zh-Hant")
            for raw in ('[desktop]\n', '[desktop]\nlocaleOverride = ""\n',
                        '[desktop]\nlocaleOverride = "auto"\n', 'invalid TOML [',
                        '#' * (CONFIG_BYTES + 1)):
                path.write_text(raw)
                self.assertEqual(resolve_locale(home=home)["locale"], "zh-Hant")
            path.unlink()
            target = home / "elsewhere"
            target.write_text('[desktop]\nlocaleOverride = "ja-JP"\n')
            path.symlink_to(target)
            self.assertEqual(resolve_locale(home=home)["locale"], "zh-Hant")

    def test_python310_parser_rejects_fake_sections_and_reads_native_scalar(self):
        self.assertEqual(_legacy_override('[desktop]\nlocaleOverride = "ja-JP" # note'), "ja-JP")
        self.assertEqual(_legacy_override("desktop.localeOverride = 'en-US'"), "en-US")
        self.assertEqual(_legacy_override(
            'instructions = """hello\n[desktop]\nlocaleOverride="en"\n"""\n'
            '[desktop]\nlocaleOverride="ja-JP"'
        ), "ja-JP")
        self.assertEqual(_legacy_override(
            "instructions = '''hello\n[desktop]\nlocaleOverride='en'\n'''\n"
            "[desktop]\nlocaleOverride='fr-FR'"
        ), "fr-FR")
        for raw in ('[desktop]\nlocaleOverride="ja"\nlocaleOverride="en"',
                    'instructions = """\n[desktop]\nlocaleOverride="ja"\n"""',
                    '[unrelated]\ndesktop.localeOverride = "ja"',
                    'instructions = """unterminated\n[desktop]\nlocaleOverride="ja"',
                    "[desktop]\nlocaleOverride = {value='ja'}"):
            self.assertIsNone(_legacy_override(raw))
        with tempfile.TemporaryDirectory() as temporary, patch(
            "codex_usage_reports.report_i18n._tomllib", None
        ):
            home = Path(temporary)
            (home / "config.toml").write_text('[desktop]\nlocaleOverride="de-DE"')
            self.assertEqual(resolve_locale(home=home)["locale"], "de")

    def test_macos_ignores_terminal_language_and_read_failure_is_safe(self):
        with tempfile.TemporaryDirectory() as temporary, patch(
            "codex_usage_reports.report_i18n.sys.platform", "darwin"
        ), patch.dict("os.environ", {"LC_ALL": "en_US.UTF-8"}), patch(
            "codex_usage_reports.report_i18n._mac_language", side_effect=[None, "zh-Hant-TW"]
        ) as reader:
            self.assertEqual(resolve_locale(home=temporary), {
                "locale": "zh-Hant", "locale_source": "macos_system_language"
            })
            self.assertEqual([call.args[0] for call in reader.call_args_list],
                             ["com.openai.codex", "-g"])
        with tempfile.TemporaryDirectory() as temporary, patch(
            "codex_usage_reports.report_i18n._system_locale", side_effect=RuntimeError("secret")
        ):
            self.assertEqual(resolve_locale(home=temporary), {
                "locale": "en", "locale_source": "fallback_english"
            })

    def test_locale_switch_between_start_and_stop_preserves_pending_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            page = root / "source.jsonl"
            meta = {"type": "session_meta", "payload": {"id": "test-task"}}
            page.write_text(json.dumps(meta) + "\n" + json.dumps(counter(1000)) + "\n")
            payload = {"session_id": "test-task", "turn_id": "turn-one",
                       "transcript_path": str(page)}
            data = root / "data"
            with patch("codex_usage_reports.auto_report.resolve_locale", return_value={
                "locale": "ja", "locale_source": "app_override"
            }):
                handle({**payload, "hook_event_name": "UserPromptSubmit"}, data,
                       wall=100, monotonic=100)
            pending = next((data / "auto-reports").glob("*.md"))
            self.assertIn(ReportText("ja")("pending"), pending.read_text())
            page.write_text(page.read_text() + json.dumps(counter(2000)) + "\n")
            with patch("codex_usage_reports.auto_report.resolve_locale", return_value={
                "locale": "de", "locale_source": "app_override"
            }):
                result = handle({**payload, "hook_event_name": "Stop"}, data,
                                wall=101, monotonic=101)
            self.assertEqual(recent(data)[0]["state"], "reported")
            self.assertIn(ReportText("de")("report_link"), result["systemMessage"])
            self.assertIn(ReportText("de")("title"), pending.read_text())
            receipt = json.loads(next((data / "auto-reports").glob("*.json")).read_text())
            self.assertEqual(receipt["locale"], "de")
            self.assertEqual(receipt["pending_locale"], "ja")
            self.assertEqual(receipt["usage"]["total"], 1000)

    def test_locale_failure_cannot_escape_report_error_handler(self):
        with tempfile.TemporaryDirectory() as temporary, patch(
            "codex_usage_reports.auto_report.resolve_locale", side_effect=RuntimeError("fail")
        ):
            response = handle({"hook_event_name": "UserPromptSubmit", "session_id": "task",
                               "turn_id": "turn"}, Path(temporary))
            self.assertEqual(set(response), {"systemMessage"})
            self.assertIn("can continue", response["systemMessage"])


if __name__ == "__main__":
    unittest.main()
