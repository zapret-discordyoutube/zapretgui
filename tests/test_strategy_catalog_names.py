"""Названия готовых стратегий записаны по одной схеме.

Схема: серия → вариант → версия → уточнение после « · ». Примеры:
«General ALT3 1.9.9 · из Telegram», «Flowseal ALT8 1.10.3 · из YouTube и Google»,
«Split2 seqovl 2 · устаревшая». Прежнее название лежит рядом в поле old_name:
по нему стратегию находит поиск, а подсказка напоминает, как она называлась.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from profile.strategy_list import PlanRequest, build_plan, build_strategy_facts
from profile.strategy_catalog import _parse_catalog_file

CATALOGS_ROOT = Path(__file__).resolve().parents[1] / "src" / "system" / "strategy_catalogs"

# Значки-эмодзи и прочие картинки в начале имени.
_PICTURE = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def _catalogs() -> dict[str, dict]:
    return {
        f"{path.parent.name}/{path.name}": _parse_catalog_file(path, path.stem)
        for path in sorted(CATALOGS_ROOT.glob("*/*.txt"))
    }


class StrategyCatalogNameSchemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalogs = _catalogs()

    def _names(self):
        for file, entries in self.catalogs.items():
            for entry in entries.values():
                yield file, entry

    def test_names_are_unique_inside_each_catalog(self) -> None:
        for file, entries in self.catalogs.items():
            with self.subTest(catalog=file):
                names = [entry.name for entry in entries.values()]
                repeated = sorted({name for name in names if names.count(name) > 1})
                self.assertEqual(repeated, [])

    def test_names_have_no_pictures_and_no_service_marks(self) -> None:
        for file, entry in self._names():
            with self.subTest(catalog=file, strategy=entry.strategy_id):
                self.assertIsNone(_PICTURE.search(entry.name), entry.name)
                self.assertNotIn("(game filter)", entry.name)
                self.assertNotIn(" & ", entry.name)
                # Источник пишется как « · из …», а не через косую черту.
                self.assertNotIn(" / ", entry.name)
                self.assertNotIn("Устаревший", entry.name)
                self.assertEqual(entry.name, entry.name.strip())

    def test_names_start_with_a_capital_letter(self) -> None:
        for file, entry in self._names():
            first_word = entry.name.split()[0]
            if "_" in first_word:
                # Имя функции zapret как есть: hostfakesplit_multi, fake_2_n2.
                continue
            with self.subTest(catalog=file, strategy=entry.strategy_id):
                self.assertFalse(entry.name[0].islower(), entry.name)

    def test_series_are_written_one_way(self) -> None:
        for file, entry in self._names():
            with self.subTest(catalog=file, strategy=entry.strategy_id):
                self.assertIsNone(re.match(r"general\b", entry.name), entry.name)
                self.assertNotIn("SIMPLE FAKE", entry.name)
                self.assertNotIn("FAKE TLS AUTO", entry.name)
                self.assertIsNone(re.search(r"\(alt ?v?\d*\)", entry.name), entry.name)
                self.assertNotIn("Dronator", entry.name)
                self.assertNotIn("YtDisBystro", entry.name)

    def test_old_name_is_kept_only_where_the_name_changed(self) -> None:
        kept = 0
        for file, entry in self._names():
            with self.subTest(catalog=file, strategy=entry.strategy_id):
                self.assertNotEqual(entry.old_name, entry.name)
                # Поле old_name — не часть стратегии: в её строки оно не попадает.
                for line in entry.args.splitlines():
                    self.assertTrue(line.startswith("--"), line)
            kept += bool(entry.old_name)
        self.assertGreater(kept, 700)

    def test_known_examples(self) -> None:
        tcp = self.catalogs["winws2/tcp.txt"]
        expected = {
            "stock_missing_199_tcp_59": (
                "General Simple Fake ALT 1.9.9 · из Telegram",
                "general SIMPLE FAKE ALT 1.9.9 (game filter) / Telegram",
            ),
            "flowseal_1103_alt8_google": (
                "Flowseal ALT8 1.10.3 · из YouTube и Google",
                "Flowseal 1.10.3 / ALT8 / YouTube и Google",
            ),
            "general_fake_tls_auto_alt_185": (
                "General Fake TLS Auto ALT 1.8.5",
                "general (fake TLS auto alt) 1.8.5",
            ),
            "tls_fake_only_md5": ("TLS Fake Only + MD5sig", "🎭 TLS Fake Only + MD5sig"),
            "general_alt11_all_sites": ("General ALT11 · из «Все сайты»", "general ALT11 / Все сайты"),
            "split2_split_2": ("Split2 split seqovl 2 · устаревшая", "Устаревший split2 split seqovl 2"),
            # Стратегия, добавленная из встроенного пресета, названа по нему,
            # а не перечислением своих параметров.
            "stock_dead_by_daylight_v2_game_filter_03": (
                "Dead by Daylight v2 · из Telegram",
                "send x2 + syndata tls_google + pass",
            ),
        }
        for strategy_id, (name, old_name) in expected.items():
            with self.subTest(strategy=strategy_id):
                self.assertEqual(tcp[strategy_id].name, name)
                self.assertEqual(tcp[strategy_id].old_name, old_name)


class StrategyFromBuiltinPresetNameTests(unittest.TestCase):
    """Стратегии, автоматически добавленные из встроенных пресетов."""

    _SOURCE = re.compile(r"встроенного пресета .+?\.txt \([a-z0-9_]+\)")

    def test_they_are_named_after_the_preset_and_its_profile(self) -> None:
        checked = 0
        for path in sorted(CATALOGS_ROOT.glob("*/*.txt")):
            name = ""
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("name = "):
                    name = line[len("name = ") :]
                elif line.startswith("description = ") and self._SOURCE.search(line):
                    with self.subTest(catalog=path.name, name=name):
                        self.assertIn(" · из ", name)
                        self.assertNotIn(" + Syndata tls", name)
                    checked += 1
        self.assertGreater(checked, 80)


class StrategyOldNameSearchTests(unittest.TestCase):
    """Переименованную стратегию находят и по новому, и по прежнему названию."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.entries = _catalogs()["winws2/tcp.txt"]
        cls.facts = build_strategy_facts(cls.entries)

    def _found(self, query: str) -> set[str]:
        plan = build_plan(PlanRequest(facts=self.facts, query=query))
        return {item.strategy_id for group in plan.groups for item in group.items}

    def test_search_by_old_name_finds_the_renamed_strategy(self) -> None:
        renamed = next(item for item in self.facts.values() if item.old_name and item.old_name.lower() not in item.name.lower())

        self.assertIn(renamed.strategy_id, self._found(renamed.old_name))
        self.assertIn(renamed.strategy_id, self._found(renamed.name))

    def test_tooltip_mentions_old_name_only_for_renamed_strategy(self) -> None:
        plan = build_plan(PlanRequest(facts=self.facts))
        items = {item.strategy_id: item for group in plan.groups for item in group.items}
        renamed = next(item for item in self.facts.values() if item.old_name)
        kept = next(item for item in self.facts.values() if not item.old_name)

        self.assertIn(f"Раньше называлась: {renamed.old_name}", items[renamed.strategy_id].tooltip)
        self.assertNotIn("Раньше называлась", items[kept.strategy_id].tooltip)


if __name__ == "__main__":
    unittest.main()
