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

from profile.strategy_catalog import _parse_catalog_file
from profile.strategy_list_filter import (
    build_profile_strategy_list_plan,
    strategy_matches_search,
    strategy_tooltip_text,
)
from profile.ui.profile_strategy_list_widget import ProfileStrategyListWidget

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
        }
        for strategy_id, (name, old_name) in expected.items():
            with self.subTest(strategy=strategy_id):
                self.assertEqual(tcp[strategy_id].name, name)
                self.assertEqual(tcp[strategy_id].old_name, old_name)


class StrategyOldNameSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])
        cls.entries = _catalogs()["winws2/tcp.txt"]

    def test_search_by_old_name_finds_the_renamed_strategy(self) -> None:
        plan = build_profile_strategy_list_plan(
            entries=self.entries,
            states={},
            current_strategy_id="none",
            search_text="SIMPLE FAKE ALT 1.9.9 (game filter) / Telegram",
        )

        self.assertEqual([row.name for row in plan.rows], ["General Simple Fake ALT 1.9.9 · из Telegram"])
        self.assertIn(
            "Раньше называлась: general SIMPLE FAKE ALT 1.9.9 (game filter) / Telegram",
            plan.rows[0].tooltip_text,
        )

    def test_search_by_new_name_still_works(self) -> None:
        plan = build_profile_strategy_list_plan(
            entries=self.entries,
            states={},
            current_strategy_id="none",
            search_text="Flowseal ALT8 1.10.3 · из YouTube",
        )

        self.assertEqual([row.strategy_id for row in plan.rows], ["flowseal_1103_alt8_google"])

    def test_list_built_without_background_worker_searches_old_names_too(self) -> None:
        widget = ProfileStrategyListWidget()
        self.addCleanup(widget.deleteLater)
        widget._strategy_filter_runtime = None
        widget.set_rows(entries=self.entries, states={}, current_strategy_id="none")

        widget._search.setText("(game filter) / telegram")

        shown = [
            widget._list.item(row).data(widget._ROLE_NAME_TEXT)
            for row in range(widget._list.count())
            if widget._list.item(row).data(widget._ROLE_STRATEGY_ID)
        ]
        self.assertGreater(len(shown), 5)
        for name in shown:
            self.assertTrue(name.endswith("· из Telegram"), name)
        item = widget._item_by_strategy_id["stock_missing_199_tcp_59"]
        self.assertIn("Раньше называлась: general SIMPLE FAKE ALT", item.data(widget._ROLE_TOOLTIP_TEXT))

    def test_strategy_that_kept_its_name_has_no_old_name_line(self) -> None:
        text = strategy_tooltip_text(visual_description="Подмена пакета", args="--lua-desync=fake", old_name="")

        self.assertEqual(text, "Подмена пакета\n\n--lua-desync=fake")
        self.assertFalse(
            strategy_matches_search("telegram", name="Fake", old_name="", args="--lua-desync=fake", visual_search="")
        )


if __name__ == "__main__":
    unittest.main()
