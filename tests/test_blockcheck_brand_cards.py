"""Итоги и отчёты BlockCheck: логотипы в фирменных цветах, строки-таблицы, метки серверов, текст не обрезается."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from blockcheck.ui.brand_icons import brands_in_text, named_brand, readable_color, site_brand
from blockcheck.ui.check_results import (
    BlockcheckSummaryPanel,
    cluster_problems,
    cut_providers,
    problem_brand,
    problem_card_key,
    split_named_sites,
    split_problem_text,
    split_server_list,
)
from blockcheck.ui.result_cards import ResultCard, ResultDetailView, card_hint, line_icon, section_icon, tally
from blockcheck.ui.result_cards_model import Card, Line, Section


def _problem(text, *, kind="other", title="", level="fail", target="", advice=(), action="", evidence=()):
    return {
        "level": level,
        "text": text,
        "kind": kind,
        "title": title,
        "target": target,
        "advice": list(advice),
        "evidence": list(evidence),
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
        # В находке по DNS названы сервисы, но это не строка про сайт Cloudflare: у неё точка важности.
        self.assertIsNone(problem_brand(_problem("Обычные запросы к Cloudflare перехватываются", kind="dns")))
        # У звонков — логотип того, чьи звонки.
        self.assertEqual(problem_brand(_problem("Звонки в Telegram могут не работать: серверы молчат", kind="voice"))[0], "simple:telegram:TG")

    def test_hosting_is_found_by_name_and_unknown_name_has_no_logo(self) -> None:
        self.assertEqual(named_brand("Akamai").icon, "simple:akamai:AK")
        self.assertEqual(named_brand("AdGuard").color, "#68BC71")
        self.assertIsNone(named_brand("Constant"))


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


class ProblemListTests(unittest.TestCase):
    def test_server_list_is_split_into_services_count_and_the_rest(self) -> None:
        detail = "Quad9 (9.9.9.9), Quad9 (149.112.112.112), Яндекс Семейный (2a02:6b8::feed:a11) и ещё 5. Так бывает."
        self.assertEqual(
            split_server_list(detail),
            ([("Quad9", ["9.9.9.9", "149.112.112.112"]), ("Яндекс Семейный", ["2a02:6b8::feed:a11"])], 5, "Так бывает."),
        )
        self.assertEqual(split_server_list("Xbox DNS (111.88.96.54)."), ([("Xbox DNS", ["111.88.96.54"])], 0, ""))
        self.assertEqual(split_server_list("Сервер молчит."), ([], 0, "Сервер молчит."))

    def test_sites_named_in_a_title_are_taken_out(self) -> None:
        self.assertEqual(
            split_named_sites("QUIC (UDP 443) блокируется по имени для: Facebook, YouTube"),
            ("QUIC (UDP 443) блокируется по имени", ["Facebook", "YouTube"]),
        )
        self.assertEqual(split_named_sites("Отвечают через раз"), ("Отвечают через раз", []))

    def test_sites_with_the_same_explanation_share_one_row(self) -> None:
        same = "Тот же адрес отвечает по порту 80."
        problems = [
            _problem("Telegram не открывается", kind="ip", title="Telegram", advice=["Адрес отвечает на пинг."]),
            _problem("Instagram не открывается", kind="ip", title="Instagram", advice=[same, "общий"]),
            _problem("X не открывается", kind="ip", title="X (Twitter)", advice=[same, "общий"]),
            # С кнопкой не объединяется: у кнопки своя цель.
            _problem("LinkedIn не открывается", kind="ip", title="LinkedIn", advice=[same, "общий"], action="strategy"),
        ]
        clusters = cluster_problems(problems, hidden_advice=("общий",))
        self.assertEqual([[item["title"] for item in cluster] for cluster in clusters], [["Telegram"], ["Instagram", "X (Twitter)"], ["LinkedIn"]])

    def test_cut_providers_lists_only_servers_with_a_cut(self) -> None:
        report = {
            "freeze": {
                "servers": [
                    {"provider": "Akamai", "host": "a", "state": "freeze"},
                    {"provider": "Akamai", "host": "b", "state": "ok"},
                    {"provider": "OVH", "host": "c", "state": "unknown"},
                ]
            }
        }
        self.assertEqual(cut_providers(report), [("Akamai", ["a"])])


class OpenReportTests(unittest.TestCase):
    """Строка итога открывает полный отчёт той карточки, о которой она говорит."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    REPORT = {
        "services": [
            {"key": "telegram", "label": "Telegram", "level": "fail", "kind": "ip", "targets": [{"host": "telegram.org"}]},
            {"key": "youtube", "label": "YouTube", "level": "warn", "kind": "ip", "targets": [{"host": "www.youtube.com"}]},
            {"key": "x", "label": "X (Twitter)", "level": "fail", "kind": "ip", "targets": [{"host": "x.com"}]},
        ],
        "dns_servers": {"level": "fail", "findings": [], "text": ""},
    }

    def test_problem_is_matched_to_its_card(self) -> None:
        report = self.REPORT
        self.assertEqual(problem_card_key(_problem("Telegram не открывается", kind="ip", title="Telegram"), report), "site:telegram")
        self.assertEqual(problem_card_key(_problem("YouTube открывается, но не работают видео", kind="ip"), report), "site:youtube")
        self.assertEqual(problem_card_key(_problem("Не открывается", kind="sni", target="x.com"), report), "site:x")
        self.assertEqual(problem_card_key(_problem("QUIC блокируется по имени для: YouTube, X. Сайты открываются", kind="quic"), report), "site:youtube")
        self.assertEqual(problem_card_key(_problem("Обычные ответы подменяются", kind="dns"), report), "dns_servers")
        self.assertEqual(problem_card_key(_problem("Обычные ответы подменяются", kind="dns"), {}), "dns")
        self.assertEqual(problem_card_key(_problem("Провайдер обрывает загрузку", kind="cut16"), report), "hostings")
        self.assertEqual(problem_card_key(_problem("Что-то ещё"), report), "")

    def test_rows_open_reports_and_each_site_of_a_shared_row_opens_its_own(self) -> None:
        opened = []
        panel = BlockcheckSummaryPanel(on_action=lambda *_args: None, on_open=opened.append)
        self.addCleanup(panel.deleteLater)
        same = "Тот же адрес отвечает по порту 80."
        panel.show_report(
            {
                **self.REPORT,
                "problems": [
                    # Объяснение — свидетельство проверки: оно остаётся в строке, а не уходит в заголовок группы.
                    _problem("Telegram не открывается", kind="ip", title="Telegram", advice=[same], evidence=[same]),
                    _problem("X не открывается", kind="ip", title="X (Twitter)", advice=[same], evidence=[same]),
                    # Карточки «Этот компьютер» в этом отчёте нет — строка не нажимается.
                    _problem("Что-то: непонятное", kind="system"),
                ],
            }
        )

        system, shared = sorted(panel.problem_rows(), key=lambda row: len(row.badges))
        self.assertEqual([badge.card_key for badge in shared.badges], ["site:telegram", "site:x"])
        self.assertEqual(shared.focusPolicy(), Qt.FocusPolicy.TabFocus)
        QTest.mouseClick(shared.badges[1], Qt.MouseButton.LeftButton)
        QTest.keyClick(shared, Qt.Key.Key_Return)
        self.assertEqual(opened, ["site:x", "site:telegram"])
        self.assertEqual(system.card_key, "")
        QTest.mouseClick(system, Qt.MouseButton.LeftButton)
        self.assertEqual(len(opened), 2)

    def test_card_hint_lists_every_line(self) -> None:
        card = Card(
            key="site:x",
            icon="",
            title="X",
            level="fail",
            status="По имени (SNI)",
            lines=tuple(Line("fail", f"адрес {index}", "сброшено") for index in range(6)),
            chips=(("QUIC закрыт", "warn"),),
        )
        hint = card_hint(card)
        self.assertIn("адрес 5: сброшено", hint)
        self.assertIn("QUIC закрыт", hint)


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_rows_get_icons_by_meaning_and_sections_by_kind(self) -> None:
        site = Card(key="site:x", icon="", title="X", level="fail", status="", site=True)
        hostings = Card(key="hostings", icon="", title="Хостинги", level="fail", status="")
        target = Section("x.com — сайт", (Line("fail", "Соединение", "сброшено"), Line("info", "TLS 1.3", "ок")))
        advice = Section("Что делать", (Line("info", "Подберите стратегию"),))
        self.assertEqual([line_icon(line, target) for line in target.lines], ["fa5s.plug", "fa5s.lock"])
        self.assertEqual(line_icon(advice.lines[0], advice), "fa5s.arrow-right")
        self.assertEqual(section_icon(target, site), ("fa5s.link", ""))
        self.assertEqual(section_icon(advice, site), ("fa5s.lightbulb", ""))
        self.assertEqual(section_icon(Section("Akamai — обрыв у 0 из 4"), hostings)[0], "simple:akamai:AK")
        self.assertEqual(tally(target.lines), {"fail": 1})

    def test_report_page_shows_summary_numbers_and_table_rows(self) -> None:
        card = Card(
            key="hostings",
            icon="fa5s.server",
            title="Зарубежные хостинги",
            level="fail",
            status="Обрыв у 1 из 3",
            sections=(
                Section(
                    "Akamai — обрыв у 1 из 3",
                    (
                        Line("ok", "SE.AKM-01 · a.example", "получено 32 КБ"),
                        Line("fail", "SE.AKM-02 · b.example", "обрыв"),
                        Line("unknown", "SE.AKM-03 · c.example", "не удалось проверить"),
                    ),
                ),
                Section("Что делать", (Line("info", "Подберите стратегию"),)),
            ),
        )
        view = ResultDetailView()
        self.addCleanup(view.deleteLater)
        view.resize(1000, 600)
        view.show()
        view.show_card(card)
        self.app.processEvents()

        self.assertEqual([count.value() for count in view.hero.counts], [1, 1, 1])
        self.assertEqual(view.hero.bar.segments(), [("ok", 1), ("fail", 1), ("unknown", 1)])
        akamai, advice = view.blocks
        self.assertEqual(akamai.summary_label.text(), "в порядке 1 из 3")
        self.assertIsNone(advice.bar)
        # Названия измерений стоят столбцом одной ширины — значения начинаются с одной линии.
        self.assertEqual(len({row.name_label.width() for row in akamai.rows}), 1)
        self.assertEqual(len({row.text_label.x() for row in akamai.rows}), 1)
        # Строка с переносом не раздувается: страница просит высоту по содержимому.
        view.resize(1000, view.minimumHeight())
        self.app.processEvents()
        self.assertLess(advice.height(), 90)
        self.assertLess(view.minimumHeight(), 420)


class CardsOnScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_problem_row_has_logo_title_explanation_and_server_chips(self) -> None:
        panel = BlockcheckSummaryPanel(on_action=lambda *_args: None)
        self.addCleanup(panel.deleteLater)
        panel.show_report(
            {
                "problems": [
                    _problem("Telegram не открывается: бан по адресу", kind="ip", title="Telegram"),
                    _problem(
                        "Шифрованный DNS по DoT закрыт у части серверов: AdGuard (94.140.15.15), AdGuard (94.140.14.14) и ещё 3. Так бывает",
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
        self.assertIsNone(site.dot)
        self.assertEqual(dns.text_label.text(), "Шифрованный DNS по DoT закрыт у части серверов")
        # Перечень адресов свёрнут в метку сервиса со счётчиком; остальное — пояснение.
        self.assertEqual([chip.text for chip in dns.server_chips], ["AdGuard ×2"])
        self.assertEqual(dns.server_chips[0].icon.icon_name(), "simple:adguard:AG")
        self.assertEqual(dns.more_label.text(), "и ещё 3")
        self.assertEqual(dns.detail_label.text(), "Так бывает.")
        # У строки не про сайт — точка важности вместо логотипа.
        self.assertIsNone(dns.icon)
        self.assertIsNotNone(dns.dot)
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
