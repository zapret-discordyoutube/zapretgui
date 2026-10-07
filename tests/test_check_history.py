from __future__ import annotations

import json
import os
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from diagnostics import history
from settings import schema
from settings.normalize import normalize_check_history, normalize_settings
from settings.schema import build_default_settings

WHEN = datetime(2026, 10, 7, 20, 15, 30)


def _service(label, level, *, control=False, key=""):
    return {"key": key or label.lower(), "label": label, "level": level, "control": control}


def _report(*services, problems=(), scope="all") -> dict:
    return {
        "scope": scope,
        "services": list(services),
        "problems": [{"level": level, "text": text} for level, text in problems],
    }


class EntryTests(unittest.TestCase):
    def test_entry_keeps_summary_and_state_of_every_service(self) -> None:
        report = _report(
            _service("Discord", "ok"),
            _service("YouTube", "fail"),
            _service("Google", "ok", control=True),
            problems=(("fail", "YouTube не открывается"), ("warn", "DNS подменяет ответы")),
        )
        entry = history.blockcheck_entry(report, log_file="C:\\logs\\run.log", when=WHEN)

        self.assertEqual(entry["kind"], history.KIND_BLOCKCHECK)
        self.assertEqual(entry["time"], "2026-10-07T20:15:30")
        self.assertEqual(entry["title"], "Все сайты")
        self.assertEqual(entry["level"], "fail")
        self.assertEqual(entry["headline"], "YouTube не открывается")
        self.assertEqual(entry["problems"], ["YouTube не открывается", "DNS подменяет ответы"])
        # Контрольные сайты в сравнение не идут: они нужны только чтобы понять, есть ли интернет.
        self.assertEqual(entry["states"], {"Discord": "ok", "YouTube": "fail"})
        self.assertEqual(entry["log_file"], "C:\\logs\\run.log")

    def test_clean_run_and_scope_title(self) -> None:
        entry = history.blockcheck_entry(_report(_service("Discord", "ok"), scope="main"), when=WHEN)

        self.assertEqual((entry["level"], entry["headline"], entry["title"]), ("ok", "Всё открывается", "Discord и YouTube"))

    def test_full_check_has_its_own_title_and_is_not_compared_with_all_sites(self) -> None:
        full = history.blockcheck_entry(_report(_service("YouTube", "fail"), scope="full"), when=WHEN)
        earlier = [history.blockcheck_entry(_report(_service("YouTube", "ok"), scope="all"), when=WHEN)]

        self.assertEqual(full["title"], "Полная проверка")
        self.assertIsNone(history.previous_run(earlier, full))

    def test_overall_level_is_the_worst_problem_level(self) -> None:
        def level(*problems):
            return history.blockcheck_entry(_report(problems=problems), when=WHEN)["level"]

        self.assertEqual(level(("warn", "a"), ("unknown", "b")), "warn")
        self.assertEqual(level(("unknown", "b")), "unknown")
        self.assertEqual(level(("warn", "a"), ("fail", "c")), "fail")


class DnsServersAdapterTests(unittest.TestCase):
    def test_report_becomes_plain_dictionary_for_the_engine(self) -> None:
        from types import SimpleNamespace

        from blockcheck import commands

        report = SimpleNamespace(
            findings=(
                SimpleNamespace(level="info", text="Не отвечают совсем: X."),
                SimpleNamespace(level="warn", text="DoT закрыт у: Y."),
            )
        )
        stops: list = []

        def run_server_check(*, on_progress=None, should_stop=None):
            stops.append(should_stop)
            return report

        marker = lambda: False  # noqa: E731
        with (
            patch("dns.commands.run_server_check", run_server_check),
            patch("dns.server_check_plans.build_text_report", lambda _report: "таблица"),
        ):
            result = commands.check_dns_servers(should_stop=marker)

        self.assertEqual(result["level"], "warn")
        self.assertEqual(result["findings"][1], {"level": "warn", "text": "DoT закрыт у: Y."})
        self.assertEqual(result["text"], "таблица")
        self.assertIs(stops[0], marker)

    def test_level_is_worst_finding_or_unknown_without_findings(self) -> None:
        from types import SimpleNamespace

        from blockcheck import commands

        def level(*levels):
            report = SimpleNamespace(findings=tuple(SimpleNamespace(level=item, text="x") for item in levels))
            with (
                patch("dns.commands.run_server_check", lambda **_k: report),
                patch("dns.server_check_plans.build_text_report", lambda _report: ""),
            ):
                return commands.check_dns_servers()["level"]

        self.assertEqual(level("ok", "info"), "ok")
        self.assertEqual(level("warn", "fail"), "fail")
        self.assertEqual(level(), "unknown")


class ChangesTests(unittest.TestCase):
    @staticmethod
    def _entry(**states):
        return {"kind": "blockcheck", "title": "Все сайты", "time": "2026-10-06T10:00:00", "states": states}

    def test_stopped_and_recovered_services_are_named(self) -> None:
        before = self._entry(Discord="ok", YouTube="warn", Telegram="fail", X="fail")
        after = self._entry(Discord="fail", YouTube="fail", Telegram="ok", X="fail")

        self.assertEqual(
            history.describe_changes(before, after),
            ["перестали открываться: Discord, YouTube", "снова открываются: Telegram"],
        )

    def test_unknown_or_missing_state_is_not_a_change(self) -> None:
        before = self._entry(Discord="ok", YouTube="unknown")
        after = self._entry(Discord="unknown", YouTube="fail", Новый="fail")

        self.assertEqual(history.describe_changes(before, after), [])

    def test_no_previous_run_means_nothing_to_compare(self) -> None:
        self.assertEqual(history.describe_changes(None, self._entry(Discord="fail")), [])

    def test_previous_run_is_the_latest_of_same_kind_and_scope(self) -> None:
        runs = [
            {"kind": "blockcheck", "title": "Все сайты", "time": "1"},
            {"kind": "blockcheck", "title": "Discord и YouTube", "time": "2"},
            {"kind": "blockcheck", "title": "Все сайты", "time": "3"},
            {"kind": "dns_servers", "title": "Все сайты", "time": "4"},
        ]

        self.assertEqual(history.previous_run(runs, {"kind": "blockcheck", "title": "Все сайты"})["time"], "3")
        self.assertEqual(history.previous_run(runs, {"kind": "blockcheck", "title": "Discord и YouTube"})["time"], "2")
        self.assertIsNone(history.previous_run(runs, {"kind": "blockcheck", "title": "Другое"}))

    def test_time_is_shown_short(self) -> None:
        self.assertEqual(history.format_time("2026-10-07T20:15:30"), "07.10 20:15")
        self.assertEqual(history.format_time("когда-то"), "когда-то")


class JsonReportTests(unittest.TestCase):
    def test_document_names_its_format_and_keeps_russian_text(self) -> None:
        report = _report(_service("Discord", "ok"), problems=(("warn", "DNS подменяет ответы"),))
        entry = history.blockcheck_entry(report, when=WHEN)
        text = history.report_json(report, entry, app_version="21.1.7.50")
        document = json.loads(text)

        self.assertEqual(document["format"], "zapretgui.blockcheck/1")
        self.assertEqual(document["app_version"], "21.1.7.50")
        self.assertEqual(document["time"], "2026-10-07T20:15:30")
        self.assertEqual(document["report"]["problems"][0]["text"], "DNS подменяет ответы")
        self.assertIn("DNS подменяет ответы", text)


class NormalizeTests(unittest.TestCase):
    def test_default_settings_have_empty_history(self) -> None:
        self.assertEqual(build_default_settings()["blockcheck"]["check_history"], [])
        self.assertEqual(normalize_settings({})["blockcheck"]["check_history"], [])

    def test_only_known_fields_survive_and_garbage_is_dropped(self) -> None:
        runs = normalize_check_history(
            [
                {
                    "kind": "blockcheck",
                    "time": "2026-10-07T20:15:30",
                    "title": "Все сайты",
                    "level": "катастрофа",
                    "headline": "x" * 1000,
                    "problems": ["a", "", 5, "b"] + ["лишнее"] * 30,
                    "states": {"Discord": "ok", "YouTube": "сломан", "": "ok"},
                    "log_file": "C:\\logs\\run.log",
                    "password": "секрет",
                },
                {"kind": "", "time": "2026-10-07T20:15:30"},
                {"kind": "blockcheck"},
                "мусор",
            ]
        )

        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run["level"], "unknown")
        self.assertEqual(len(run["headline"]), schema.CHECK_HISTORY_TEXT_LIMIT)
        self.assertEqual(run["problems"][:3], ["a", "5", "b"])
        self.assertEqual(len(run["problems"]), schema.CHECK_HISTORY_PROBLEMS_LIMIT)
        self.assertEqual(run["states"], {"Discord": "ok"})
        self.assertNotIn("password", run)
        self.assertEqual(normalize_check_history("не список"), [])

    def test_only_the_latest_runs_are_kept(self) -> None:
        many = [{"kind": "blockcheck", "time": f"t{index}"} for index in range(schema.CHECK_HISTORY_LIMIT + 7)]
        runs = normalize_check_history(many)

        self.assertEqual(len(runs), schema.CHECK_HISTORY_LIMIT)
        self.assertEqual(runs[0]["time"], "t7")
        self.assertEqual(runs[-1]["time"], f"t{schema.CHECK_HISTORY_LIMIT + 6}")


class StoreAndRememberTests(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        self.root = Path(self._folder.name)
        patcher = patch("settings.store.MAIN_DIRECTORY", str(self.root))
        patcher.start()
        self.addCleanup(patcher.stop)
        from settings import store

        self.store = store
        self.addCleanup(store.close_settings_database)
        store.close_settings_database()

    def test_history_is_stored_next_to_other_blockcheck_settings(self) -> None:
        self.store.set_blockcheck_settings({"user_domains": ["example.com"]})
        runs = self.store.add_check_history_run({"kind": "blockcheck", "time": "t1", "level": "ok"})
        self.store.add_check_history_run({"kind": "blockcheck", "time": "t2", "level": "fail"})

        self.assertEqual([run["time"] for run in runs], ["t1"])
        self.assertEqual([run["time"] for run in self.store.get_check_history()], ["t1", "t2"])
        # Запись истории не затёрла соседнюю настройку.
        self.assertEqual(self.store.get_blockcheck_settings()["user_domains"], ["example.com"])

    def test_remembering_a_run_reports_what_changed_and_writes_json(self) -> None:
        from blockcheck import commands

        log_file = str(self.root / "blockcheck_run_1.log")
        first = commands.remember_blockcheck_run(_report(_service("Discord", "ok"), _service("YouTube", "ok")), log_file)
        self.assertEqual((first["changes"], first["previous_time"]), ([], ""))

        second_report = _report(
            _service("Discord", "ok"), _service("YouTube", "fail"), problems=(("fail", "YouTube не открывается"),)
        )
        second = commands.remember_blockcheck_run(second_report, log_file)

        self.assertEqual(second["changes"], ["перестали открываться: YouTube"])
        self.assertEqual([run["level"] for run in second["history"]], ["ok", "fail"])
        self.assertTrue(second["previous_time"])
        self.assertEqual(len(self.store.get_check_history()), 2)
        document = json.loads(Path(second["json_file"]).read_text("utf-8"))
        self.assertEqual(Path(second["json_file"]).name, "blockcheck_run_1.json")
        self.assertEqual(document["format"], history.REPORT_FORMAT)
        self.assertEqual(document["level"], "fail")

    def test_run_of_another_scope_is_not_compared(self) -> None:
        from blockcheck import commands

        commands.remember_blockcheck_run(_report(_service("YouTube", "ok"), scope="all"), None)
        note = commands.remember_blockcheck_run(_report(_service("YouTube", "fail"), scope="main"), None)

        self.assertEqual((note["changes"], note["json_file"]), ([], ""))


class WorkerTests(unittest.TestCase):
    def _worker(self, remember):
        from blockcheck.worker import BlockcheckWorker

        return BlockcheckWorker(
            start_run_log=lambda *_a: None,
            append_run_log=lambda *_a: None,
            close_run_log=lambda *_a: None,
            remember_run=remember,
        )

    def test_changes_are_added_to_report_and_printed(self) -> None:
        worker = self._worker(
            lambda report, log: {"changes": ["перестали открываться: YouTube"], "previous_time": "2026-10-06T10:00:00", "json_file": "x.json"}
        )
        lines: list[str] = []
        worker.log_message.connect(lines.append)
        report = {"scope": "all"}
        worker._remember(report)

        self.assertEqual(report["changes"], ["перестали открываться: YouTube"])
        self.assertEqual(report["json_file"], "x.json")
        self.assertEqual(report["history"], [])
        self.assertIn("🕘 С прошлой проверки (06.10 10:00) перестали открываться: YouTube.", lines)

    def test_failed_history_write_does_not_cost_the_result(self) -> None:
        def broken(_report, _log):
            raise OSError("диск переполнен")

        report = {"scope": "all", "services": []}
        self._worker(broken)._remember(report)

        self.assertNotIn("changes", report)

    def test_no_changes_means_no_extra_lines(self) -> None:
        worker = self._worker(lambda report, log: {"changes": [], "previous_time": "", "json_file": ""})
        lines: list[str] = []
        worker.log_message.connect(lines.append)
        worker._remember({"scope": "all"})

        self.assertEqual(lines, [])


if __name__ == "__main__":
    unittest.main()
