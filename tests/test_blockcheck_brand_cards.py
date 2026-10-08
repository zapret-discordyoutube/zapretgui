"""Итоги и отчёты BlockCheck: логотипы в фирменных цветах, строки-таблицы, метки серверов, текст не обрезается."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from blockcheck.ui.brand_icons import brands_in_text, named_brand, readable_color, site_brand
from blockcheck.ui.check_results import (
    BlockcheckSummaryPanel,
    is_site_problem,
    site_explanation,
    site_name,
    site_note,
    cut_providers,
    problem_brand,
    problem_card_key,
    problem_finding_card,
    problem_parts,
    split_named_sites,
    split_problem_text,
    split_server_list,
)
from blockcheck.ui.result_cards import (
    ANIMATED_BLOCKS,
    TEXT_PREVIEW_LINES,
    ResultCard,
    ResultDetailView,
    TilesGrid,
    card_hint,
    line_icon,
    line_tile,
    section_icon,
    tally,
    wants_tiles,
)
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
        # Вид блокировки, которого здесь не знают, — всё равно про сайт: логотип подбирается.
        self.assertEqual(problem_brand(_problem("Canva не открывается", kind="noconnect", title="Canva"))[0], "own:canva:CA")
        # Своего логотипа нет — значок с карточки сайта, нейтральным цветом.
        unknown = {**_problem("Meduza не открывается", kind="sni", title="Meduza"), "card_icon": "fa5s.newspaper"}
        self.assertEqual(problem_brand(unknown), ("fa5s.newspaper", ""))
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

    def test_site_card_says_only_what_differs_and_keeps_the_rest_for_the_hint(self) -> None:
        full = _problem(
            "Telegram не открывается: бан",
            kind="ip",
            title="Telegram",
            target="telegram.org",
            advice=["Адрес отвечает на пинг.", "Попробуйте VPN"],
            evidence=["Адрес отвечает на пинг."],
        )
        self.assertEqual((site_name(full), site_note({**full, "cause_word": "адрес закрыт"})), ("Telegram", "telegram.org · адрес закрыт"))
        self.assertEqual(site_explanation(full), ("Адрес отвечает на пинг.", "→ Попробуйте VPN"))
        # Частично работающий сайт: название — с карточки, вторая строка — что не работает.
        partial = {**_problem("YouTube открывается, но не работают видео: сервер заблокирован", kind="ip", level="warn"), "site_label": "YouTube"}
        self.assertEqual((site_name(partial), site_note(partial)), ("YouTube", "открывается, но не работают видео"))
        self.assertEqual(site_explanation(partial), ("Сервер заблокирован.",))

    def test_sites_are_cards_and_everything_else_stays_a_row(self) -> None:
        self.assertTrue(is_site_problem(_problem("Canva не открывается", kind="noconnect", title="Canva")))
        self.assertTrue(is_site_problem(_problem("Не открывается", kind="sni", target="x.com")))
        self.assertFalse(is_site_problem(_problem("Обычные ответы подменяются", kind="dns")))
        self.assertFalse(is_site_problem(_problem("Звонки в Telegram могут не работать", kind="voice")))
        # Общая строка про обрыв на 16 КБ — не про один сайт.
        self.assertFalse(is_site_problem(_problem("Обрывается загрузка с зарубежных серверов", kind="cut16")))

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

    def test_site_cards_open_their_reports_and_carry_no_repeated_text(self) -> None:
        opened, acted = [], []
        panel = BlockcheckSummaryPanel(on_action=lambda *args: acted.append(args), on_open=opened.append)
        self.addCleanup(panel.deleteLater)
        same = "Тот же адрес отвечает по порту 80."
        panel.show_report(
            {
                **self.REPORT,
                "problems": [
                    _problem("Telegram не открывается", kind="ip", title="Telegram", target="telegram.org", advice=[same], evidence=[same]),
                    _problem("X не открывается", kind="ip", title="X (Twitter)", target="x.com", advice=[same], evidence=[same], action="strategy"),
                    # Карточки «Этот компьютер» в этом отчёте нет — строка не нажимается.
                    _problem("Что-то: непонятное", kind="system"),
                ],
            }
        )

        telegram, x, system = sorted(panel.problem_rows(), key=lambda row: getattr(row, "title", ""))
        [ip_group] = [group for group in panel.problem_groups() if group.kind() == "ip"]
        # Оба сайта — в одной сетке; текста объяснения между карточками нет.
        self.assertEqual(ip_group.flow.cards(), [telegram, x])
        self.assertEqual((telegram.title, telegram.note), ("Telegram", "telegram.org"))
        self.assertEqual((telegram.card_key, x.card_key), ("site:telegram", "site:x"))
        self.assertEqual((telegram.icon.icon_name(), telegram.icon.brand_color()), ("simple:telegram:TG", "#26A5E4"))
        # Объяснение целиком — в подсказке.
        self.assertIn(same, telegram.hint_text)
        self.assertIn(same, telegram.toolTip())

        self.assertEqual(x.focusPolicy(), Qt.FocusPolicy.TabFocus)
        QTest.mouseClick(x, Qt.MouseButton.LeftButton)
        QTest.keyClick(telegram, Qt.Key.Key_Return)
        self.assertEqual(opened, ["site:x", "site:telegram"])
        # Кнопка-значок делает своё дело и говорит о нём в подсказке; отчёт при этом не открывается.
        self.assertIsNone(telegram.action_button)
        self.assertEqual(x.action_button.toolTip(), "Подобрать стратегию для x.com")
        x.action_button.click()
        self.assertEqual((acted, len(opened)), ([("strategy", "x.com")], 2))
        self.assertEqual(system.card_key, "")
        QTest.mouseClick(system, Qt.MouseButton.LeftButton)
        self.assertEqual(len(opened), 2)
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


class TilesTests(unittest.TestCase):
    """Перечень серверов хостинга — сетка карточек одним виджетом."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_line_becomes_a_short_tile_and_keeps_the_whole_phrase_in_the_hint(self) -> None:
        long = "не удалось проверить: подключение есть, но сервер не ответил за 0 с — так выглядит блокировка провайдером · 5.0 с"
        tile = line_tile(Line("unknown", "US.DO-01 · ecomstal.com", long))
        self.assertEqual((tile.tag, tile.title, tile.seconds), ("US.DO-01", "ecomstal.com", "5.0 с"))
        self.assertEqual(tile.result, "Сервер не ответил за 0 с")
        self.assertIn(long, tile.hint)
        self.assertEqual(line_tile(Line("ok", "FR.A-1 · a.b", "получено 32 КБ без обрыва; отправка тоже проходит · 5.0 с")).result, "Получено 32 КБ без обрыва")
        # Суть ошибки не теряется: она стоит после «но».
        cut = line_tile(Line("fail", "CA.F-2 · c.d", "загрузка проходит, но отправка 64 КБ замирает, хотя короткую тот же сервер принимает · 6.1 с"))
        self.assertEqual(cut.result, "Отправка 64 КБ замирает")

    def test_only_hosting_servers_go_into_tiles(self) -> None:
        servers = Section("Akamai — обрыв у 0 из 2", (Line("ok", "SE.A-1 · a.b", "получено"), Line("ok", "SE.A-2 · c.d", "получено")))
        hostings = Card(key="hostings", icon="", title="Хостинги", level="ok", status="")
        site = Card(key="site:x", icon="", title="X", level="ok", status="", site=True)
        self.assertTrue(wants_tiles(servers, hostings))
        self.assertFalse(wants_tiles(servers, site))
        self.assertFalse(wants_tiles(Section("Что делать", (Line("info", "Подберите стратегию"),)), hostings))

    def test_grid_is_one_widget_with_columns_by_width_and_a_hint_per_tile(self) -> None:
        tiles = [line_tile(Line("ok", f"US.X-{index} · host{index}.example", "получено 32 КБ · 1.0 с")) for index in range(7)]
        grid = TilesGrid(tiles)
        self.addCleanup(grid.deleteLater)
        grid.resize(800, 10)
        grid.show()
        self.app.processEvents()

        self.assertEqual(grid.findChildren(QWidget), [])
        self.assertEqual([grid.columns_for(width) for width in (200, 520, 800)], [1, 2, 3])
        # Семь карточек в три колонки — три ряда; высоту сетка считает сама.
        self.assertEqual(grid.height(), 3 * TilesGrid.HEIGHT + 2 * TilesGrid.GAP)
        last = grid.tile_rect(6).center()
        self.assertEqual(grid.tile_at(last.x(), last.y()), 6)
        self.assertEqual(grid.tile_at(790, grid.height() - 2), -1)
        self.assertFalse(grid.grab().isNull())

    def test_hostings_report_uses_grids_and_animates_only_the_first_blocks(self) -> None:
        sections = tuple(
            Section(f"Провайдер {index} — обрыв у 0 из 2", (Line("ok", "A-1 · a.b", "получено · 1.0 с"), Line("fail", "A-2 · c.d", "обрыв · 1.0 с")))
            for index in range(12)
        )
        view = ResultDetailView()
        self.addCleanup(view.deleteLater)
        view.resize(1000, 700)
        view.show()
        with patch("blockcheck.ui.result_cards.float_in") as rise:
            view.show_card(Card(key="hostings", icon="", title="Хостинги", level="fail", status="", sections=sections))

        self.assertTrue(all(block.grid is not None and block.rows == [] for block in view.blocks))
        self.assertEqual(rise.call_count, ANIMATED_BLOCKS)


class LongTextTests(unittest.TestCase):
    """Длинный текст отчёта — в редакторе со своей прокруткой, а не одной надписью на сотни строк."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_text_section_is_an_editor_of_limited_height_and_opens_full_page(self) -> None:
        table = "\n".join(f"Cloudflare   1.1.1.{index}   {index} мс   обрыв" for index in range(300))
        card = Card(
            key="dns_servers",
            icon="fa5s.network-wired",
            title="DNS-серверы",
            level="fail",
            status="Есть проблемы",
            sections=(Section("Все серверы и способы связи", text=table),),
        )
        view = ResultDetailView()
        self.addCleanup(view.deleteLater)
        view.resize(1000, 700)
        view.show()
        opened = []
        view.text_opened.connect(lambda title, text: opened.append((title, text)))
        view.show_card(card)
        self.app.processEvents()

        [block] = view.blocks
        self.assertTrue(block.editor.isReadOnly())
        # Высота — на несколько строк, а не на все триста: дальше прокручивает сам редактор.
        line = block.editor.fontMetrics().lineSpacing()
        self.assertLess(block.editor.height(), (TEXT_PREVIEW_LINES + 4) * line)
        self.assertLess(view.minimumHeight(), 700)
        # Самой надписи на весь текст больше нет.
        self.assertFalse(any(table in label.text() for label in block.findChildren(QLabel)))
        block.open_text_button.click()
        self.assertEqual(opened, [("Все серверы и способы связи", table)])


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
        # Не хостинги: у них серверы идут сеткой карточек, а здесь проверяется таблица строк.
        card = Card(
            key="voice",
            icon="fa5s.server",
            title="Голосовые серверы",
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
        # Отчёт короче окна: шапка и строки не растягиваются на всю его высоту.
        self.assertGreater(view.height(), view.minimumHeight() + 150)
        self.assertLess(view.hero.height(), 140)
        self.assertLess(akamai.rows[0].height(), 50)
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
        self.assertEqual(site.title, "Telegram")
        self.assertIsNone(site.dot)
        self.assertEqual(dns.title, "Шифрованный DNS по DoT закрыт у части серверов")
        # Перечень адресов свёрнут в метку сервиса со счётчиком; остальное — пояснение.
        self.assertEqual([chip.text for chip in dns.server_chips], ["AdGuard ×2"])
        self.assertEqual(dns.server_chips[0].icon.icon_name(), "simple:adguard:AG")
        self.assertEqual(dns.more_label.text(), "и ещё 3")
        # Пояснение — в подсказке: на карточке только суть.
        self.assertIn("Так бывает", dns.hint_text)
        # У находки не про сайт — точка важности вместо логотипа.
        self.assertIsNotNone(dns.dot)
        # Кнопка действия — одна на группу, в её заголовке.
        self.assertIsNone(dns.action_button)
        [dns_group] = [group for group in panel.problem_groups() if group.kind() == "dns"]
        self.assertEqual(dns_group.shared_action_button.text(), "Настройка DNS")

    def test_dns_problem_with_ready_parts_lists_every_server(self) -> None:
        text = "Обычные ответы подменяются у серверов: Cloudflare (1.1.1.1), Cloudflare (1.0.0.1), AdGuard (94.140.14.14), Quad9 (9.9.9.9) и ещё 2."
        servers = [["Cloudflare", "1.1.1.1"], ["Cloudflare", "1.0.0.1"], ["AdGuard", "94.140.14.14"], ["Quad9", "9.9.9.9"]]
        servers += [["Quad9", "149.112.112.112"], ["Google DNS", "8.8.8.8"]]
        ready = {
            **_problem(text, kind="dns", action="dns"),
            "parts": {"title": "Обычные ответы подменяются у серверов", "servers": servers, "note": ""},
        }
        # Проблема из отчёта прошлой проверки: частей нет, есть только фраза.
        past = _problem(text, kind="dns", action="dns")

        self.assertEqual(
            problem_parts(ready),
            (
                "Обычные ответы подменяются у серверов",
                [
                    ("Cloudflare", ["1.1.1.1", "1.0.0.1"]),
                    ("AdGuard", ["94.140.14.14"]),
                    ("Quad9", ["9.9.9.9", "149.112.112.112"]),
                    ("Google DNS", ["8.8.8.8"]),
                ],
                0,
                "",
                "Cloudflare: 1.1.1.1, 1.0.0.1\nAdGuard: 94.140.14.14\nQuad9: 9.9.9.9, 149.112.112.112\nGoogle DNS: 8.8.8.8",
            ),
        )
        title, chips, more, rest, _detail = problem_parts(past)
        self.assertEqual((title, [name for name, _addresses in chips], more, rest), (ready["parts"]["title"], ["Cloudflare", "AdGuard", "Quad9"], 2, ""))

        card = problem_finding_card(ready)
        self.addCleanup(card.deleteLater)
        self.assertEqual([chip.text for chip in card.server_chips], ["Cloudflare ×2", "AdGuard", "Quad9 ×2"])
        self.assertEqual(card.more_label.text(), "и ещё 1")
        self.assertIn("Google DNS: 8.8.8.8", card.hint_text)
        old = problem_finding_card(past)
        self.addCleanup(old.deleteLater)
        self.assertEqual([chip.text for chip in old.server_chips], ["Cloudflare ×2", "AdGuard", "Quad9"])
        self.assertEqual(old.more_label.text(), "и ещё 2")

        panel = BlockcheckSummaryPanel(on_action=lambda *_args: None)
        self.addCleanup(panel.deleteLater)
        panel.show_report({"problems": [ready]})
        [row] = panel.problem_rows()
        self.assertEqual(row.title, "Обычные ответы подменяются у серверов")
        self.assertEqual([chip.text for chip in row.server_chips], ["Cloudflare ×2", "AdGuard", "Quad9 ×2"])
        self.assertEqual(row.more_label.text(), "и ещё 1")
        self.assertIn("Google DNS: 8.8.8.8", row.hint_text)

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


class LightAndFittingTests(unittest.TestCase):
    """Метки без своей рамки и только целиком; узкие разделы отчёта — столбцами."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _card(self, width: int):
        from blockcheck.ui.finding_parts import FindingCard

        servers = [("www.youtube.com", []), ("www.facebook.com", []), ("www.linkedin.com", [])]
        # Карточка стоит в сетке: ширину ей задаёт сетка, а не её собственное содержимое.
        host = QWidget()
        self.addCleanup(host.deleteLater)
        card = FindingCard("DNS подменяет ответы", host, servers=servers, more=11)
        card.setGeometry(0, 0, width, FindingCard.HEIGHT)
        host.resize(700, 80)
        host.show()
        self.app.processEvents()
        return card

    def test_chips_are_never_cut_and_hidden_ones_are_counted(self) -> None:
        wide = self._card(600)
        self.assertEqual([chip.isVisible() for chip in wide.server_chips], [True, True, True])
        self.assertEqual(wide.more_label.text(), "и ещё 11")
        # Метка показывает название целиком: её ширина не меньше нужной тексту.
        self.assertTrue(all(chip.width() >= chip.sizeHint().width() for chip in wide.server_chips))
        self.assertEqual(wide.server_chips[0].label.text(), "youtube.com")

        narrow = self._card(200)
        self.assertEqual([chip.isVisible() for chip in narrow.server_chips], [True, False, False])
        self.assertEqual(narrow.more_label.text(), "и ещё 13")
        right = max(chip.geometry().right() for chip in narrow.server_chips if chip.isVisible())
        self.assertLess(right, narrow.width())

    def test_short_sections_share_a_row_instead_of_stretching(self) -> None:
        from blockcheck.ui.result_cards import ResultDetailView, is_narrow_section
        from blockcheck.ui.result_cards_model import Card, Line, Section

        def server(address: str) -> Section:
            return Section(address, (Line("ok", "Пинг", "5 мс"), Line("ok", "UDP 53", "43 мс"), Line("fail", "DoH 443", "обрыв")))

        long_line = Line("warn", "Что найдено", "Провайдер подменяет ответы обычного DNS у этих серверов, поэтому сайты открываются не туда.")
        card = Card(
            key="service",
            icon="fa5s.server",
            title="Cloudflare",
            level="fail",
            status="Мешает работе",
            sections=(Section("Что найдено", (long_line,)), server("1.1.1.1"), server("1.0.0.1"), server("2606:4700:4700::1111")),
        )
        self.assertEqual([is_narrow_section(section) for section in card.sections], [False, True, True, True])
        view = ResultDetailView()
        self.addCleanup(view.deleteLater)
        view.resize(1300, 700)
        view.show()
        view.show_card(card)
        self.app.processEvents()

        wide, first, second, third = view.blocks
        self.assertGreater(wide.width(), 1200)
        self.assertEqual(len({first.y(), second.y(), third.y()}), 1)
        self.assertLess(first.width(), 450)
        # Узкое окно — те же разделы один под другим.
        view.resize(500, 700)
        self.app.processEvents()
        self.assertLess(first.y(), second.y())
        self.assertLess(second.y(), third.y())

    def test_counter_stops_counting_when_its_report_is_closed(self) -> None:
        from blockcheck.ui import result_cards

        label = result_cards._CountLabel(12)
        self.addCleanup(label.deleteLater)
        # Кадр анимации приходит в метод счётчика: безымянная функция падала после закрытия отчёта.
        label._show_share(0.5)
        self.assertEqual(label.text(), "6")
        label.show()
        with patch.object(result_cards, "are_live_animations_enabled", return_value=True):
            label.play()
        self.assertEqual(label._anim.state(), label._anim.State.Running)
        label.hide()
        self.assertEqual(label._anim.state(), label._anim.State.Stopped)
        self.assertEqual(label.text(), "12")
