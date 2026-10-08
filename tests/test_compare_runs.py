"""Сравнение двух проверок — с Zapret и без: что обход чинит, а что нет."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from diagnostics import compare, history
from settings.normalize import normalize_check_history


def _run(time: str, zapret: str, *, title: str = "Все сайты", preset: str = "", tools: bool = False, **states) -> dict:
    return {
        "kind": "blockcheck",
        "time": time,
        "title": title,
        "zapret": zapret,
        "preset": preset,
        "tools": tools,
        "states": states,
    }


class CounterpartTests(unittest.TestCase):
    def test_takes_latest_run_in_the_opposite_state(self) -> None:
        runs = [
            _run("2026-10-08T10:00:00", "off"),
            _run("2026-10-08T11:00:00", "on"),
            _run("2026-10-08T12:00:00", "off"),
        ]
        entry = _run("2026-10-08T13:00:00", "on")

        self.assertEqual(compare.counterpart(runs, entry)["time"], "2026-10-08T12:00:00")

    def test_same_state_other_scope_and_unknown_state_are_not_a_pair(self) -> None:
        entry = _run("2026-10-08T13:00:00", "on")

        self.assertIsNone(compare.counterpart([_run("2026-10-08T12:00:00", "on")], entry))
        self.assertIsNone(compare.counterpart([_run("2026-10-08T12:00:00", "off", title="Полная проверка")], entry))
        self.assertIsNone(compare.counterpart([_run("2026-10-08T12:00:00", "")], entry))
        self.assertIsNone(compare.counterpart([_run("2026-10-08T12:00:00", "off")], _run("2026-10-08T13:00:00", "")))

    def test_old_run_is_not_compared(self) -> None:
        # За несколько дней меняются сами блокировки: разницу дало бы время, а не обход.
        entry = _run("2026-10-08T13:00:00", "on")

        self.assertIsNone(compare.counterpart([_run("2026-10-01T13:00:00", "off")], entry))
        self.assertIsNotNone(compare.counterpart([_run("2026-10-06T14:00:00", "off")], entry))


class CompareTests(unittest.TestCase):
    def _pair(self, on: dict, off: dict, *, current: str = "on", preset: str = "Default") -> dict | None:
        with_zapret = _run("2026-10-08T13:00:00", "on", preset=preset, **on)
        without = _run("2026-10-08T12:00:00", "off", **off)
        return compare.compare_runs(with_zapret, without) if current == "on" else compare.compare_runs(without, with_zapret)

    def test_services_are_sorted_into_four_groups(self) -> None:
        result = self._pair(
            {"YouTube": "ok", "Telegram": "fail", "GitHub": "ok", "Steam": "fail", "Сбой": "unknown"},
            {"YouTube": "fail", "Telegram": "fail", "GitHub": "warn", "Steam": "ok", "Сбой": "fail"},
        )

        self.assertEqual(result["helped"], ["YouTube"])
        self.assertEqual(result["not_helped"], ["Telegram"])
        self.assertEqual(result["fine_anyway"], ["GitHub"])
        self.assertEqual(result["broken"], ["Steam"])
        # Про сервис без ответа в одной из проверок ничего не говорится.
        self.assertNotIn("Сбой", json.dumps(result, ensure_ascii=False))

    def test_result_is_the_same_whichever_run_is_current(self) -> None:
        on, off = {"YouTube": "ok", "Telegram": "fail"}, {"YouTube": "fail", "Telegram": "fail"}
        now_on, now_off = self._pair(on, off), self._pair(on, off, current="off")

        self.assertEqual((now_on["helped"], now_on["not_helped"]), (now_off["helped"], now_off["not_helped"]))
        self.assertEqual((now_on["zapret_in"], now_off["zapret_in"]), ("current", "past"))
        self.assertEqual(now_off["preset"], "Default")

    def test_unfixed_site_is_blamed_on_the_preset_not_on_zapret(self) -> None:
        # Пресет решает, что включено: «не помог» — это про пресет, а не про Zapret вообще.
        result = self._pair({"Telegram": "fail"}, {"Telegram": "fail"}, preset="Мой пресет")

        self.assertEqual(result["level"], "fail")
        self.assertIn("«Мой пресет»", result["headline"])
        self.assertNotIn("Zapret не", result["headline"])
        self.assertTrue(any("другой пресет" in note for note in result["notes"]))

    def test_everything_fixed_needs_no_preset_note(self) -> None:
        result = self._pair({"YouTube": "ok"}, {"YouTube": "fail"})

        self.assertEqual((result["level"], result["notes"]), ("ok", []))

    def test_vpn_in_either_run_is_mentioned(self) -> None:
        with_zapret = _run("2026-10-08T13:00:00", "on", YouTube="ok")
        without = _run("2026-10-08T12:00:00", "off", tools=True, YouTube="fail")

        result = compare.compare_runs(with_zapret, without)
        self.assertTrue(any("VPN" in note for note in result["notes"]))
        self.assertTrue(result["disturbed"])
        self.assertFalse(compare.compare_runs(with_zapret, dict(without, tools=False))["disturbed"])

    def test_nothing_to_compare_gives_nothing(self) -> None:
        self.assertIsNone(compare.compare_runs(_run("t", "on", YouTube="ok"), None))
        self.assertIsNone(self._pair({"YouTube": "ok"}, {"Discord": "fail"}))

    def test_lines_name_every_group(self) -> None:
        result = self._pair({"YouTube": "ok", "Telegram": "fail"}, {"YouTube": "fail", "Telegram": "fail"})
        text = "\n".join(compare.lines(result, format_time=history.format_time))

        self.assertIn("Обход помог: YouTube", text)
        self.assertIn("Не открываются и с обходом: Telegram", text)
        self.assertIn("08.10 12:00", text)
        self.assertEqual(compare.lines(None), [])


class EntryAndSettingsTests(unittest.TestCase):
    def test_entry_records_conditions_of_the_run(self) -> None:
        report = {
            "scope": "all",
            "services": [],
            "zapret_running": True,
            "other_bypass_tools": ["AmneziaVPN"],
            "tools_in_path": ["AmneziaVPN"],
            "habits": {"code": "none"},
        }
        entry = history.blockcheck_entry(report, preset="Default")

        self.assertEqual((entry["zapret"], entry["preset"], entry["tools"], entry["habit"]), ("on", "Default", True, "none"))
        # VPN запущен, но интернет шёл мимо него — проверке он не мешал.
        idle = history.blockcheck_entry({"scope": "all", "other_bypass_tools": ["WireGuard"], "tools_in_path": []})
        self.assertFalse(idle["tools"])
        self.assertEqual(history.blockcheck_entry({"scope": "all", "zapret_running": None})["zapret"], "")
        self.assertEqual(history.blockcheck_entry({"scope": "all", "zapret_running": False})["zapret"], "off")

    def test_settings_keep_the_conditions(self) -> None:
        entry = history.blockcheck_entry({"scope": "all", "zapret_running": False, "habits": {"code": "tcp"}}, preset="P")
        kept = normalize_check_history([entry])[0]

        self.assertEqual((kept["zapret"], kept["preset"], kept["tools"], kept["habit"]), ("off", "P", False, "tcp"))
        # Старые записи и мусор не ломают историю.
        old = normalize_check_history([{"kind": "blockcheck", "time": "t", "zapret": "да", "tools": "yes"}])[0]
        self.assertEqual((old["zapret"], old["preset"], old["tools"], old["habit"]), ("", "", False, ""))


class RememberTests(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        self.root = Path(self._folder.name)
        patcher = patch("settings.store.MAIN_DIRECTORY", str(self.root))
        patcher.start()
        self.addCleanup(patcher.stop)
        from settings import store

        self.addCleanup(store.close_settings_database)
        store.close_settings_database()

    @staticmethod
    def _report(zapret: bool, **levels) -> dict:
        return {
            "scope": "all",
            "zapret_running": zapret,
            "services": [{"key": name, "label": name, "level": level} for name, level in levels.items()],
            "problems": [],
            "text": ["первая строка"],
        }

    def test_second_run_in_the_other_state_gets_a_comparison_saved_to_the_file(self) -> None:
        from blockcheck import commands

        first = self._report(False, YouTube="fail", Telegram="fail")
        commands.remember_blockcheck_run(first, str(self.root / "blockcheck_run_1.json"))
        self.assertIsNone(first["compare"])

        second = self._report(True, YouTube="ok", Telegram="fail")
        note = commands.remember_blockcheck_run(second, str(self.root / "blockcheck_run_2.json"), preset="Default")

        self.assertEqual((second["compare"]["helped"], second["compare"]["not_helped"]), (["YouTube"], ["Telegram"]))
        self.assertEqual(second["preset"], "Default")
        # Файл — источник истины: и данные сравнения, и его строки лежат в нём.
        saved = json.loads((self.root / "blockcheck_run_2.json").read_text("utf-8"))["report"]
        self.assertEqual(saved["compare"], second["compare"])
        self.assertEqual(saved["text"][0], "первая строка")
        self.assertTrue(any("Обход помог: YouTube" in line for line in saved["text"]))
        self.assertEqual(note["lines"], saved["text"][1:])


if __name__ == "__main__":
    unittest.main()
