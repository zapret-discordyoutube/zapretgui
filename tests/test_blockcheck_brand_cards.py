"""Карточки BlockCheck: логотипы в фирменных цветах, заголовок отдельно от пояснения, текст не обрезается."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from blockcheck.ui.brand_icons import brands_in_text, readable_color, site_brand
from blockcheck.ui.check_results import BlockcheckSummaryPanel, problem_brand, split_problem_text
from blockcheck.ui.result_cards import ResultCard
from blockcheck.ui.result_cards_model import Card, Line


def _problem(text, *, kind="other", title="", level="fail", target="", advice=(), action=""):
    return {
        "level": level,
        "text": text,
        "kind": kind,
        "title": title,
        "target": target,
        "advice": list(advice),
        "evidence": [],
        "action": action,
    }


class BrandLookupTests(unittest.TestCase):
    def test_site_is_found_by_key_title_or_address(self) -> None:
        self.assertEqual(site_brand("youtube").color, "#FF0000")
        self.assertEqual(site_brand("", "X (Twitter)").icon, "simple:x:X")
        self.assertEqual(site_brand("", "", "www.instagram.com").icon, "simple:instagram:IN")
        self.assertEqual(site_brand("Яндекс").icon, "fa5b.yandex")
        self.assertIsNone(site_brand("user:my.example", "my.example"))

    def test_dns_services_named_in_a_finding_are_listed_once(self) -> None:
        text = "Обычные ответы подменяются у серверов: Cloudflare (1.1.1.1), Cloudflare (1.0.0.1), AdGuard (94.140.14.14)"
        self.assertEqual([brand.name for brand in brands_in_text(text)], ["Cloudflare", "AdGuard"])
        self.assertEqual(brands_in_text("Соединение обрывается"), [])

    def test_black_logo_stays_visible_on_the_dark_theme(self) -> None:
        self.assertEqual(readable_color("#000000", light_theme=False), "#f2f2f2")
        self.assertEqual(readable_color("#000000", light_theme=True), "#000000")
        self.assertEqual(readable_color("#FF0000", light_theme=False), "#ff0000")

    def test_problem_gets_site_logo_only_for_site_kinds(self) -> None:
        self.assertEqual(problem_brand(_problem("Telegram не открывается", kind="ip", title="Telegram")), ("simple:telegram:TG", "#26A5E4"))
        # Название сайта стоит первым словом фразы.
        self.assertEqual(problem_brand(_problem("YouTube открывается, но не работают видео", kind="ip"))[0], "simple:youtube:YT")
        # В находке по DNS названы сервисы, но это не карточка сайта Cloudflare.
        self.assertEqual(problem_brand(_problem("Обычные запросы к Cloudflare перехватываются", kind="dns")), ("fa5s.network-wired", ""))


class ProblemTextTests(unittest.TestCase):
    def test_site_title_is_used_as_is(self) -> None:
        self.assertEqual(split_problem_text(_problem("Telegram не открывается: бан", title="Telegram")), ("Telegram", ""))

    def test_dns_finding_is_split_at_the_colon(self) -> None:
        problem = _problem("Шифрованный DNS AdGuard (94.140.14.140) недоступен: сервер молчит", kind="dns")
        self.assertEqual(split_problem_text(problem), ("Шифрованный DNS AdGuard (94.140.14.140) недоступен", "Сервер молчит."))

    def test_other_problem_is_split_at_the_first_sentence(self) -> None:
        problem = _problem("DNS подменяет ответы для www.youtube.com, rutracker.org. Браузер этого не замечает", kind="dns")
        self.assertEqual(
            split_problem_text(problem),
            ("DNS подменяет ответы для www.youtube.com, rutracker.org", "Браузер этого не замечает."),
        )
        self.assertEqual(split_problem_text(_problem("Служба BFE: не работает", kind="system")), ("Служба BFE", "Не работает."))
        self.assertEqual(split_problem_text(_problem("Соединение обрывается")), ("Соединение обрывается", ""))


class CardsOnScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_problem_card_has_logo_title_explanation_and_dns_service_icons(self) -> None:
        panel = BlockcheckSummaryPanel(on_action=lambda *_args: None)
        self.addCleanup(panel.deleteLater)
        panel.show_report(
            {
                "problems": [
                    _problem("Telegram не открывается: бан по адресу", kind="ip", title="Telegram"),
                    _problem(
                        "Шифрованный DNS AdGuard (94.140.14.140) недоступен: сервер молчит",
                        kind="dns",
                        level="warn",
                        action="dns",
                    ),
                ],
            }
        )

        site, dns = panel.problem_rows()
        self.assertEqual((site.icon.icon_name(), site.icon.brand_color()), ("simple:telegram:TG", "#26A5E4"))
        self.assertEqual(site.text_label.text(), "Telegram")
        # Обычная ошибка не помечается на каждой карточке — помечаются исключения.
        self.assertIsNone(site.level_label)
        self.assertEqual(dns.text_label.text(), "Шифрованный DNS AdGuard (94.140.14.140) недоступен")
        self.assertEqual(dns.detail_label.text(), "Сервер молчит.")
        self.assertEqual(dns.level_label.text(), "работает не полностью")
        self.assertEqual([icon.icon_name() for icon in dns.brand_icons], ["simple:adguard:AG"])
        self.assertIsNotNone(dns.action_button)

    def test_narrow_card_shows_title_and_status_in_full(self) -> None:
        card = Card(
            key="site:instagram",
            icon="fa5b.instagram",
            title="Instagram",
            level="fail",
            status="Способ не определён",
            kind="unclear",
            lines=(Line("ok", "превью видео", "открывается"),),
            site=True,
        )
        widget = ResultCard(card)
        self.addCleanup(widget.deleteLater)
        widget.resize(250, 140)
        widget.show()
        self.app.processEvents()

        self.assertEqual(widget.title_label.text(), "Instagram")
        self.assertEqual(widget.status_label.text(), "Способ не определён")
        self.assertEqual(widget.rows[0].name_label.text(), "превью видео")
        self.assertEqual(widget._icon.brand_color(), "#E4405F")


if __name__ == "__main__":
    unittest.main()
