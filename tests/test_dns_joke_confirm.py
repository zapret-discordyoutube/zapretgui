from __future__ import annotations

import os
import random
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.ui_texts import tr as tr_catalog
from dns import joke_confirm
from dns.adapters import DnsAdapter
from dns.dns_providers import DNS_PROVIDERS, FUN_GROUP, catalog_problems
from dns.state import DnsState

ETH = "{00000000-0000-0000-0000-000000000009}"


class JokePhrasesTests(unittest.TestCase):
    def test_there_are_at_least_ten_phrases_in_both_languages(self) -> None:
        self.assertGreaterEqual(joke_confirm.PHRASE_COUNT, 10)
        seen = set()
        for number in range(1, joke_confirm.PHRASE_COUNT + 1):
            title_key, content_key = joke_confirm.phrase_keys(number)
            for language, word in (("ru", "Роскомнадзор"), ("en", "Roskomnadzor")):
                title = tr_catalog(title_key, language=language, default="")
                content = tr_catalog(content_key, language=language, default="")
                self.assertTrue(title, (language, title_key))
                # В каждой фразе сказано, что этим DNS блокируют сервисы.
                self.assertIn(word, content, (language, content_key))
                seen.add((language, title, content))
        self.assertEqual(len(seen), joke_confirm.PHRASE_COUNT * 2)

    def test_phrase_number_out_of_range_is_an_error(self) -> None:
        for number in (0, joke_confirm.PHRASE_COUNT + 1):
            with self.assertRaises(ValueError):
                joke_confirm.phrase_keys(number)

    def test_same_phrase_never_comes_twice_in_a_row_and_all_come_up(self) -> None:
        rng = random.Random(7)
        last, shown = 0, set()
        for _ in range(400):
            number = joke_confirm.pick(last, rng)
            self.assertNotEqual(number, last)
            self.assertTrue(1 <= number <= joke_confirm.PHRASE_COUNT)
            last = number
            shown.add(number)
        self.assertEqual(shown, set(range(1, joke_confirm.PHRASE_COUNT + 1)))


class JokeCatalogTests(unittest.TestCase):
    def test_tile_is_in_its_own_group_and_the_catalog_stays_valid(self) -> None:
        tile = DNS_PROVIDERS[FUN_GROUP]["НСДИ"]
        self.assertTrue(joke_confirm.needs_confirmation(tile))
        self.assertEqual(tile["ipv4"][:2], ["195.208.6.1", "195.208.7.1"])
        self.assertIn("РКН-тян", tile["desc"])
        self.assertEqual(catalog_problems(), [])
        # Остальные плитки без окна.
        others = [d for g, items in DNS_PROVIDERS.items() for n, d in items.items() if n != "НСДИ"]
        self.assertFalse(any(joke_confirm.needs_confirmation(d) for d in others))


class JokePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        for target in ("dns.ui.page.InfoBar",):
            started = patch(target).start()
            self.addCleanup(patch.stopall)
        patch("dns.ui.now_panel.are_live_animations_enabled", return_value=True).start()
        patch("dns.ui.provider_grid.are_live_animations_enabled", return_value=True).start()

    def _page(self):
        from dns.ui.page import NetworkPage

        adapter = DnsAdapter(ETH, "Ethernet", "", "ethernet", connected=True, internet=True)
        state = DnsState(adapters=(adapter,), doh_supported=True)
        feature = SimpleNamespace(
            consume_warmed_page_data=Mock(return_value=state),
            create_page_load_worker=Mock(),
            create_dns_apply_worker=Mock(),
            create_dns_flush_cache_worker=Mock(),
            create_dns_latency_worker=Mock(),
            create_dns_answering_worker=Mock(),
            create_isp_dns_warning_worker=Mock(),
            create_custom_server_worker=Mock(),
        )
        page = NetworkPage(deps=SimpleNamespace(dns_feature=feature, open_custom_server=Mock(), create_open_url_worker=Mock()))
        self.addCleanup(page.deleteLater)
        page.resize(1200, 900)
        for lane in page._lanes():
            lane.request = Mock()
        page.on_page_activated()
        return page

    def _click(self, page, name, answer):
        box = Mock()
        box.exec.return_value = answer
        with patch("ui.fluent_dialog.MessageBox", return_value=box) as box_cls, patch(
            "ui.message_box_accessibility.set_message_box_button_accessibility"
        ), patch("dns.ui.page.joke_effects.play") as self.play:
            page.grid.activated.emit(name)
        return box_cls

    def test_declined_joke_changes_nothing(self) -> None:
        page = self._page()

        box_cls = self._click(page, "НСДИ", answer=False)

        box_cls.assert_called_once()
        page._apply_lane.request.assert_not_called()

    def test_confirmed_joke_sets_the_dns_like_any_other_tile(self) -> None:
        page = self._page()

        self._click(page, "НСДИ", answer=True)

        payload = page._apply_lane.request.call_args.args[0]
        self.assertEqual((payload["action"], payload["name"]), ("provider", "НСДИ"))
        self.assertEqual(payload["data"]["ipv4"], ["195.208.6.1", "195.208.7.1"])

    def test_every_click_asks_with_another_phrase(self) -> None:
        page = self._page()
        titles = []
        for _ in range(8):
            box_cls = self._click(page, "НСДИ", answer=False)
            titles.append(box_cls.call_args.args[0])
        self.assertTrue(all(titles))
        self.assertTrue(all(a != b for a, b in zip(titles, titles[1:])))

    def test_ordinary_tile_has_no_joke_window(self) -> None:
        page = self._page()

        box_cls = self._click(page, "Quad9", answer=False)

        box_cls.assert_not_called()
        page._apply_lane.request.assert_called_once()

    def test_group_has_its_own_tab_and_note(self) -> None:
        page = self._page()
        self.assertEqual(page._group_title(FUN_GROUP), "Для смеха")
        self.assertTrue(page._group_note(FUN_GROUP))


if __name__ == "__main__":
    unittest.main()
