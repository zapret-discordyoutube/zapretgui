"""История вкладок «Проверка домена» и «DNS подмена»: настройки → записи → экран."""

from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from diagnostics import history
from settings import schema
from settings.normalize import normalize_settings
from settings.schema import build_default_settings

WHEN = datetime(2026, 10, 8, 2, 15, 0)


def _run(title="x.com", level="ok", headline="Готово"):
    return {"kind": "domain", "time": WHEN.isoformat(timespec="seconds"), "title": title, "level": level, "headline": headline}


class SettingsTests(unittest.TestCase):
    def test_both_histories_exist_and_start_empty(self) -> None:
        for key in schema.TAB_HISTORY_KEYS:
            self.assertEqual(build_default_settings()["blockcheck"][key], [])
            self.assertEqual(normalize_settings({})["blockcheck"][key], [])

    def test_tab_history_is_cleaned_and_keeps_only_the_latest_runs(self) -> None:
        runs = [_run(title=f"s{n}.example") for n in range(schema.TAB_HISTORY_LIMIT + 5)] + ["мусор", {"kind": "", "time": ""}]
        cleaned = normalize_settings({"blockcheck": {"domain_history": runs}})["blockcheck"]

        self.assertEqual(len(cleaned["domain_history"]), schema.TAB_HISTORY_LIMIT)
        self.assertEqual(cleaned["domain_history"][-1]["title"], f"s{schema.TAB_HISTORY_LIMIT + 4}.example")
        # Общая история проверок сети от записей вкладок не зависит.
        self.assertEqual(cleaned["check_history"], [])

    def test_store_appends_to_the_named_history_only(self) -> None:
        from settings import store

        section = {"domain_history": [_run("old.example")], "check_history": [_run("blockcheck")]}

        def fake_update(mutator):
            mutator(section)
            return section

        with patch.object(store, "update_blockcheck_settings", fake_update):
            runs = store.add_tab_history_run("domain_history", _run("new.example"))
            with self.assertRaises(ValueError):
                store.add_tab_history_run("check_history", _run())

        self.assertEqual([run["title"] for run in runs], ["old.example", "new.example"])
        self.assertEqual(len(section["check_history"]), 1)

    def test_initial_page_state_carries_both_histories(self) -> None:
        from blockcheck import page_runtime

        settings = {"blockcheck": {"domain_history": [_run("x.com"), "мусор"], "dns_history": [_run("DNS подмена")]}}
        with patch("settings.store.read_settings", return_value=settings):
            state = page_runtime.load_page_initial_state()

        self.assertEqual(([run["title"] for run in state.domain_history], len(state.dns_history)), (["x.com"], 1))
        with patch("settings.store.read_settings", side_effect=OSError("нет базы")):
            self.assertEqual(page_runtime.load_page_initial_state().domain_history, ())


class EntryTests(unittest.TestCase):
    def test_dns_entry_names_spoofed_sites_and_skips_stopped_runs(self) -> None:
        results = {
            "domains": {
                "discord.com": {"state": "ok"},
                "www.youtube.com": {"state": "spoofed", "reason": "отвечает чужой сервер"},
                "i.ytimg.com": {"state": "unknown"},
            }
        }
        entry = history.dns_check_entry(results, when=WHEN)

        self.assertEqual((entry["kind"], entry["level"]), ("dns", "fail"))
        self.assertEqual(entry["headline"], "Подменяются адреса: www.youtube.com")
        self.assertEqual(entry["problems"], ["www.youtube.com: отвечает чужой сервер"])
        self.assertEqual(entry["states"], {"discord.com": "ok", "www.youtube.com": "fail", "i.ytimg.com": "unknown"})
        self.assertIsNone(history.dns_check_entry({"stopped": True, "domains": results["domains"]}))
        self.assertIsNone(history.dns_check_entry({"domains": {}}))

    def test_dns_entry_levels(self) -> None:
        self.assertEqual(history.dns_check_entry({"domains": {"a": {"state": "ok"}, "b": {"state": "local"}}})["level"], "ok")
        self.assertEqual(history.dns_check_entry({"domains": {"a": {"state": "unknown"}}})["level"], "warn")

    def test_entries_survive_the_settings_cleaning(self) -> None:
        entry = history.domain_entry("x.com", "fail", "Фильтр между узлами 3 и 4", ["DNS системы: заглушка"], when=WHEN)
        [cleaned] = normalize_settings({"blockcheck": {"domain_history": [entry]}})["blockcheck"]["domain_history"]

        self.assertEqual((cleaned["title"], cleaned["level"], cleaned["headline"]), ("x.com", "fail", "Фильтр между узлами 3 и 4"))

    def test_rows_go_newest_first_with_time(self) -> None:
        runs = [_run("old.example", "ok", "Готово"), _run("new.example", "fail", "Заглушка")]
        rows = history.history_rows(runs)

        self.assertEqual([row[0] for row in rows], ["fail", "ok"])
        self.assertTrue(rows[0][1].startswith("new.example · "))
        self.assertEqual(rows[0][2], "Заглушка")
        self.assertEqual(history.history_rows(None), [])


class CommandTests(unittest.TestCase):
    def test_finished_dns_check_is_remembered_and_broken_store_does_not_break_it(self) -> None:
        from dns import commands

        results = {"domains": {"a.example": {"state": "ok"}}}
        saved = patch("dns.commands._save_past_check", return_value="dns_check_1.json")
        with patch("diagnostics.engine.run_dns_check", return_value=dict(results)), patch("settings.store.add_tab_history_run") as add, saved:
            got = commands.run_dns_poisoning_check()
        self.assertEqual(got["domains"], results["domains"])
        self.assertEqual((add.call_args.args[0], add.call_args.args[1]["kind"]), ("dns_history", "dns"))
        # На экран уходит та самая запись, что сохранена в настройки, — с путём к файлу отчёта.
        self.assertEqual(got["history_entry"], add.call_args.args[1])
        self.assertEqual(got["history_entry"]["log_file"], "dns_check_1.json")

        with patch("diagnostics.engine.run_dns_check", return_value=dict(results)), patch("settings.store.add_tab_history_run", side_effect=OSError("диск")), saved:
            self.assertEqual(commands.run_dns_poisoning_check()["domains"], results["domains"])

        with patch("diagnostics.engine.run_dns_check", return_value={"stopped": True}), patch("settings.store.add_tab_history_run") as add, saved as save:
            commands.run_dns_poisoning_check()
        add.assert_not_called()
        save.assert_not_called()

    def test_past_dns_and_server_checks_are_saved_whole_and_read_back(self) -> None:
        import tempfile

        from test_dns_server_check_page import _report as server_report

        from dns import commands

        with tempfile.TemporaryDirectory() as folder, patch("config.runtime_layout.APPLICATION_PATHS", SimpleNamespace(logs_dir=folder)):
            results = {"domains": {"a.example": {"state": "spoofed", "ips": ["1.2.3.4"]}}}
            def fake_check(*, emit, should_stop):
                emit("строка первая")
                emit("строка вторая")
                return dict(results)

            shown: list[str] = []
            with patch("diagnostics.engine.run_dns_check", fake_check), patch("settings.store.add_tab_history_run"):
                got = commands.run_dns_poisoning_check(log_callback=shown.append)
            self.assertEqual(commands.load_past_dns_check_report(got["history_entry"]["log_file"]), results)
            # Текст отчёта лежит в том же файле — по нему у прошлой проверки работает кнопка «Отчёт».
            self.assertEqual(commands.load_past_dns_check_text(got["history_entry"]["log_file"]), "строка первая\nстрока вторая")
            self.assertEqual(shown, ["строка первая", "строка вторая"])
            self.assertEqual(commands.load_past_dns_check_text(""), "")

            report = server_report()
            with (
                patch("dns.server_check.run_server_check", return_value=report),
                patch("dns.commands.build_server_check_targets", return_value=()),
                patch("dns.commands._running_bypass", return_value=()),
                patch("settings.store.add_tab_history_run") as add,
            ):
                finished = commands.run_server_check()
            entry = finished.history_entry
            self.assertEqual((add.call_args.args[0], add.call_args.args[1]), ("servers_history", entry))
            self.assertEqual(entry["kind"], "servers")
            # Отчёт возвращается из файла тем же объектом: карточки по нему получаются те же.
            self.assertEqual(commands.load_past_server_check_report(entry["log_file"]), report)
            # Чужой или испорченный файл — «отчёта нет», а не ошибка.
            self.assertIsNone(commands.load_past_server_check_report(got["history_entry"]["log_file"]))
            self.assertIsNone(commands.load_past_dns_check_report(""))

    def test_server_check_entry_names_the_worst_finding_and_skips_unfinished_runs(self) -> None:
        from test_dns_server_check_page import _report as server_report

        report = server_report()
        entry = history.server_check_entry(report, when=WHEN)
        self.assertEqual((entry["kind"], entry["time"], entry["log_file"]), ("servers", WHEN.isoformat(timespec="seconds"), ""))
        self.assertIn(entry["level"], ("ok", "warn", "fail"))
        self.assertTrue(entry["headline"])
        self.assertIsNone(history.server_check_entry(server_report(finished=False)))
        self.assertIsNone(history.server_check_entry(server_report(stopped=True)))

    def test_domain_entry_is_built_only_for_finished_lookups(self) -> None:
        from dns import domain_lookup_plans as plans

        base = dict(kind="domain", target="x.com", finished=True, stopped=False, timed_out=False, primary_ip="1.2.3.4",
                    answers=(), route=None, quic=None, filter_facts=None, elapsed_s=2.0, sources=(), error="")
        entry = plans.build_history_entry(SimpleNamespace(**base))
        self.assertEqual((entry["kind"], entry["title"], entry["level"]), ("domain", "x.com", "ok"))
        self.assertEqual(plans.build_history_entry(SimpleNamespace(**{**base, "primary_ip": ""}))["level"], "warn")
        self.assertIsNone(plans.build_history_entry(SimpleNamespace(**{**base, "stopped": True})))
        self.assertIsNone(plans.build_history_entry(SimpleNamespace(**{**base, "finished": False})))
        self.assertIsNone(plans.build_history_entry(SimpleNamespace(**{**base, "kind": plans.KIND_INVALID})))


if __name__ == "__main__":
    unittest.main()
