"""Поиск имён, которые пропускает фильтр, и подстановка имени в подставной пакет стратегии."""

from __future__ import annotations

import unittest

from blockcheck.strategy_search import sni_discovery as sd
from blockcheck.strategy_search.ordering import Candidate

TARGET = "discord.com"


def _discover(answer):
    asked: list = []
    slept: list = []

    def hello(name):
        asked.append(name)
        return answer(name, len(asked))

    return sd.discover(hello, TARGET, sleep=slept.append, cancelled=lambda: False), asked, slept


class DiscoverTests(unittest.TestCase):
    def test_ordinary_filter_stops_after_the_neutral_name(self) -> None:
        search, asked, _slept = _discover(lambda name, _n: name == sd.NEUTRAL_NAME)

        self.assertEqual(search.kind, sd.FILTER_BLACK)
        self.assertEqual(asked, [TARGET, sd.NEUTRAL_NAME])

    def test_window_answer_after_every_name_cut_stops_the_name_hunt(self) -> None:
        asked: list = []
        windows: list = []

        def hello(name):
            asked.append(name)
            return False

        search = sd.discover(hello, TARGET, sleep=lambda _s: None, cancelled=lambda: False, window=lambda: windows.append(1) or True)

        self.assertEqual(search.kind, sd.FILTER_REPLY_WINDOW)
        self.assertEqual(asked, [TARGET, sd.NEUTRAL_NAME, None])
        self.assertEqual(windows, [1])

    def test_window_is_not_asked_when_a_foreign_name_passes(self) -> None:
        windows: list = []

        search = sd.discover(
            lambda name: name == sd.NEUTRAL_NAME, TARGET, sleep=lambda _s: None, cancelled=lambda: False, window=lambda: windows.append(1) or True
        )

        self.assertEqual((search.kind, windows), (sd.FILTER_BLACK, []))

    def test_whitelist_finds_up_to_three_names_confirmed_twice(self) -> None:
        allowed = {"ya.ru", "vk.com", "sberbank.ru", "ozon.ru"}
        search, asked, slept = _discover(lambda name, _n: name in allowed)

        self.assertEqual((search.kind, search.names), (sd.FILTER_WHITE, ("ya.ru", "vk.com", "sberbank.ru")))
        self.assertEqual(asked.count("ya.ru"), 2)
        self.assertNotIn("ozon.ru", asked)
        # Между пробами — шаг, между пачками — пауза подольше.
        # Паузы случайные, но в своих пределах: шаг внутри пачки и подольше — между пачками.
        self.assertTrue(all(0.4 <= pause <= 3.0 for pause in slept))
        self.assertTrue(any(pause >= sd.BATCH_PAUSE_S[0] for pause in slept))

    def test_unconfirmed_name_is_not_counted(self) -> None:
        # ya.ru прошло один раз, а при повторе — нет.
        seen: list = []

        def answer(name, _n):
            if name != "ya.ru":
                return False
            seen.append(name)
            return len(seen) == 1

        search, _asked, _slept = _discover(answer)

        self.assertEqual(search.names, ())

    def test_address_frozen_by_frequent_requests_stops_the_search(self) -> None:
        # Первые три вопроса (цель, постороннее имя, без имени) — отказ, дальше адрес молчит.
        search, asked, _slept = _discover(lambda name, n: None if n > 3 else False)

        self.assertTrue(search.frozen)
        self.assertEqual(len(asked), 3 + sd.FROZEN_AFTER)

    def test_target_passing_at_the_end_discards_findings(self) -> None:
        search, _asked, _slept = _discover(lambda name, n: name == "ya.ru" or (name == TARGET and n > 1))

        self.assertTrue(search.unstable)
        self.assertEqual(search.names, ())

    def test_no_more_than_the_limit_is_tried(self) -> None:
        search, _asked, _slept = _discover(lambda _name, _n: False)

        self.assertEqual((search.kind, search.tried), (sd.FILTER_WHITE, sd.MAX_TRIED))


class VariantTests(unittest.TestCase):
    def _variant(self, args: str):
        return sd.with_allowed_name(Candidate("s1", "Стратегия", args), "ya.ru")

    def test_every_way_of_naming_the_fake(self) -> None:
        for args, expected in (
            ("--lua-desync=hostfakesplit:host=www.google.com:tcp_ts=-1000", "--lua-desync=hostfakesplit:host=ya.ru:tcp_ts=-1000"),
            ("--lua-desync=hostfakesplit_multi:hosts=google.com,vimeo.com", "--lua-desync=hostfakesplit_multi:hosts=ya.ru"),
            ("--lua-desync=fake:blob=tls_google:tls_mod=rnd,dupsid,sni=www.google.com", "--lua-desync=fake:blob=tls_google:tls_mod=rnd,dupsid,sni=ya.ru"),
            ("--lua-desync=fake:blob=tls_google:tls_mod=rnd,rndsni,dupsid", "--lua-desync=fake:blob=tls_google:tls_mod=rnd,sni=ya.ru,dupsid"),
            ("--lua-desync=fake:blob=tls_google:tls_mod=rnd,dupsid", "--lua-desync=fake:blob=tls_google:tls_mod=rnd,dupsid,sni=ya.ru"),
        ):
            with self.subTest(args=args):
                variant = self._variant(args)
                self.assertEqual(variant.args, expected)
                self.assertEqual(variant.strategy_id, "s1@sni=ya.ru")
                self.assertEqual(sd.base_of(variant.strategy_id), ("s1", "ya.ru"))

    def test_strategy_without_a_named_fake_gets_no_variant(self) -> None:
        self.assertIsNone(self._variant("--lua-desync=multisplit:pos=1"))
        self.assertIsNone(self._variant("--lua-desync=fake:blob=fake_default_http"))
        # Имя уже то же — вариант ничего не меняет.
        self.assertIsNone(self._variant("--lua-desync=hostfakesplit:host=ya.ru"))

    def test_variants_take_the_best_strategies_per_name(self) -> None:
        ordered = [Candidate(f"s{i}", f"S{i}", f"--lua-desync=fake:tls_mod=rnd,dupsid:id={i}") for i in range(10)]
        made = sd.variants(ordered, ("ya.ru", "vk.com"), per_name=2)

        self.assertEqual([item.strategy_id for item in made], ["s0@sni=ya.ru", "s1@sni=ya.ru", "s0@sni=vk.com", "s1@sni=vk.com"])


if __name__ == "__main__":
    unittest.main()
