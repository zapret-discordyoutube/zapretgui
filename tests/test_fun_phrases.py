from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ui.fun_phrases import StickyPhrase, busy_kind, loading_phrase_for, phrases, random_phrase

_KINDS = ("engine_start", "preset_apply", "engine_stop", "loading", "log_analysis")


class FunPhrasesTests(unittest.TestCase):
    def test_every_block_has_its_own_big_pool_in_both_languages(self) -> None:
        for language in ("ru", "en"):
            seen: dict[str, str] = {}
            for kind in _KINDS:
                pool = phrases(kind, language)
                self.assertGreaterEqual(len(pool), 50, (language, kind))
                for phrase in pool:
                    self.assertLessEqual(len(phrase), 70, phrase)
                    self.assertNotIn(phrase, seen, (language, kind, seen.get(phrase)))
                    seen[phrase] = kind
        self.assertNotEqual(phrases("loading", "ru"), phrases("loading", "en"))

    def test_short_captions_keep_the_meaning_first(self) -> None:
        # Шутка не заменяет смысл: подпись начинается с того, что происходит.
        starts = {
            "engine_start": ("Запус", "Завод", "Включа", "Старт", "Поехали", "Окей"),
            "preset_apply": ("Применяем", "Меняем"),
            "engine_stop": ("Останавливаем", "Глушим", "Выключаем"),
            "loading": ("Загруж", "Загрузка"),
        }
        for kind, prefixes in starts.items():
            for phrase in phrases(kind, "ru"):
                self.assertTrue(phrase.startswith(prefixes), (kind, phrase))
                self.assertLessEqual(len(phrase), 55, phrase)

    def test_busy_text_maps_to_its_block_and_unknown_text_stays(self) -> None:
        self.assertEqual(busy_kind("Применяем пресет..."), "preset_apply")
        self.assertEqual(busy_kind("Запуск Zapret..."), "engine_start")
        self.assertEqual(busy_kind("Остановка Zapret..."), "engine_stop")
        self.assertEqual(busy_kind("Переключаем режим запуска..."), "")
        sticky = StickyPhrase()
        self.assertEqual(sticky.busy("Переключаем режим запуска..."), "Переключаем режим запуска...")
        self.assertEqual(sticky.busy(""), "")

    def test_phrase_does_not_flicker_while_the_state_is_the_same(self) -> None:
        sticky = StickyPhrase()
        first = sticky.busy("Применяем пресет...")
        self.assertIn(first, phrases("preset_apply"))
        self.assertEqual({sticky.busy("Применяем пресет...") for _ in range(20)}, {first})
        self.assertIn(sticky.busy("Остановка Zapret...", "en"), phrases("engine_stop", "en"))

    def test_loading_phrase_is_stable_for_a_page(self) -> None:
        class Page:
            pass

        page = Page()
        first = loading_phrase_for(page, "ru", default="Загрузка…")
        self.assertIn(first, phrases("loading"))
        self.assertEqual({loading_phrase_for(page, "ru") for _ in range(20)}, {first})
        self.assertEqual(random_phrase("нет такого", default="Загрузка…"), "Загрузка…")


if __name__ == "__main__":
    unittest.main()
