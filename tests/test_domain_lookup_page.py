import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from blockcheck.ui.page import BlockcheckPage
from dns import domain_lookup as engine
from dns.ui import domain_lookup_cards as lookup_cards
from dns.ui.domain_lookup_page import DomainLookupPage


def _report(**overrides) -> engine.DomainLookupReport:
    values = dict(
        target="example.com",
        kind=engine.KIND_DOMAIN,
        primary_ip="93.184.216.34",
        answers=engine.annotate_answers(
            [
                engine.ResolverAnswer(engine.DnsServer("Хороший", "9.9.9.9"), "ok", ipv4=("93.184.216.34",), elapsed_ms=10.0),
                engine.ResolverAnswer(engine.DnsServer("Провайдер", "10.0.0.53"), "ok", ipv4=("195.82.146.214",)),
            ]
        ),
        ping=engine.PingReport(ip="93.184.216.34", sent=4, received=4, min_ms=1.0, avg_ms=2.0, max_ms=3.0),
        tcp=engine.TcpReport("93.184.216.34", 443, "ok", 5.0),
        network=engine.NetworkInfo(asn="64500", prefix="93.184.216.0/24", owner="EXAMPLE"),
        sources=(engine.NeighborSource(engine.SOURCE_THC, engine.SOURCE_OK, names=("a.example", "b.example")),),
        finished=True,
        elapsed_s=1.5,
    )
    values.update(overrides)
    return engine.DomainLookupReport(**values)


def _names(view) -> list[str]:
    """Подписи всех строк, показанных в виде групп."""
    return [row.name for group in view.groups() for row in group.rows]


class LookupCardsTests(unittest.TestCase):
    """Карточки «Проверки домена»: сайт теми же пробами, что в BlockCheck, страница DNS-сервера, значки."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    SITE = {
        "key": "user:example.com",
        "label": "example.com",
        "level": "fail",
        "kind": "sni",
        "headline": "example.com не открывается: блокировка по имени",
        "dns_note": "",
        "targets": [
            {
                "host": "example.com", "purpose": "сайт", "main": True, "ok": False, "state": "dpi",
                "short": "соединение сброшено", "text": "соединение сброшено (93.184.216.34)",
                "cause": "by_name", "cause_text": "Блокировка по имени сайта.", "quic": "ok", "quic_text": "работает",
                "dns_state": "ok", "registry": {"listed": True, "name": "", "network": ""},
                "protocols": [
                    {"key": "tls12", "title": "TLS 1.2", "state": "fail", "word": "сброс", "text": "соединение сброшено"},
                    {"key": "tls13", "title": "TLS 1.3", "state": "fail", "word": "сброс", "text": "соединение сброшено"},
                    {"key": "chrome", "title": "Как Chrome", "state": "ok", "word": "проходит", "text": "проходит"},
                    {"key": "http", "title": "HTTP", "state": "info", "word": "переход", "text": "переход на HTTPS"},
                ],
            }
        ],
    }

    def test_domain_gets_the_same_site_card_as_in_blockcheck(self) -> None:
        from dns import domain_lookup_plans as plans

        # Пока сайт не проверен, карточки нет; адрес (не домен) так не проверяется вовсе.
        self.assertNotIn("site:user:example.com", [card.key for card in lookup_cards.build_lookup_cards(_report())])
        report = _report(site=self.SITE)
        site = lookup_cards.build_lookup_cards(report)[0]
        self.assertEqual((site.key, site.title, site.site, site.level), ("site:user:example.com", "example.com", True, "fail"))
        self.assertEqual([mark.label for mark in site.marks], ["TLS 1.2", "TLS 1.3", "Chrome", "HTTP", "QUIC", "DNS"])
        self.assertEqual([mark.state for mark in site.marks][:3], ["fail", "fail", "ok"])
        self.assertIn(("в реестре РКН", "info"), site.tags)
        # Текстовый отчёт и запись истории тоже говорят про сайт.
        text = plans.build_text_report(report)
        self.assertIn("=== Как открывается сайт ===", text)
        self.assertIn("    TLS 1.2: соединение сброшено", text)
        self.assertIn("    Как блокируют: Блокировка по имени сайта.", text)
        entry = plans.build_history_entry(report)
        self.assertEqual((entry["level"], entry["problems"][0]), ("fail", self.SITE["headline"]))

    def test_dns_server_tile_opens_its_own_page_and_shows_the_service_logo(self) -> None:
        from blockcheck.ui.result_cards import ResultDetailView, line_icon, plain_tile
        from blockcheck.ui.result_cards_model import Line, Section

        answers = engine.annotate_answers(
            [engine.ResolverAnswer(engine.DnsServer("Cloudflare", "1.1.1.1"), "ok", ipv4=("93.184.216.34",), ipv6=("2606::1",), cnames=("a.example",), ttl=60, elapsed_ms=38.0)]
        )
        dns = next(card for card in lookup_cards.build_lookup_cards(_report(answers=answers)) if card.key == lookup_cards.KEY_DNS)
        [line] = dns.sections[1].lines
        page = line.page
        self.assertEqual((page.title, page.level), ("Cloudflare", "ok"))
        rows = {item.name: item.text for item in page.sections[0].lines}
        self.assertEqual((rows["Адрес сервера"], rows["IPv4"], rows["IPv6"], rows["Время ответа"]), ("1.1.1.1", "93.184.216.34", "2606::1", "38 мс"))
        self.assertIn("Способ запроса", rows)
        # Плитка — с логотипом сервиса и подсказкой, что её можно открыть.
        tile = plain_tile(line)
        self.assertEqual((tile.icon, tile.icon_color), ("simple:cloudflare:CF", "#F38020"))
        self.assertIn("Нажмите, чтобы открыть подробности", tile.hint)
        self.assertEqual(plain_tile(Line("ok", "Системный DNS · 8.8.8.8", "1.2.3.4")).icon_color, "")

        view = ResultDetailView()
        self.addCleanup(view.deleteLater)
        view.resize(900, 600)
        view.show()
        view.show_card(dns)
        grid = view.blocks[1].grid
        QTest.mouseClick(grid, Qt.MouseButton.LeftButton, pos=grid.tile_rect(0).center().toPoint())
        self.assertEqual(view.card(), page)
        self.assertTrue(view.go_back())
        self.assertEqual(view.card(), dns)
        grid = view.blocks[1].grid
        grid.grab()
        # У строк сети и пинга — свои значки, фраза без значения остаётся с точкой.
        section = Section("Сеть адреса", ())
        icons = [line_icon(Line("info", name, "x"), section) for name in ("Владелец сети", "ASN", "Подсеть", "Страна", "Пинг 1.2.3.4", "Подключение к порту 443 (HTTPS)")]
        self.assertEqual(icons, ["fa5s.building", "fa5s.project-diagram", "fa5s.network-wired", "fa5s.flag", "fa5s.satellite-dish", "fa5s.plug"])
        self.assertEqual(line_icon(Line("info", "Пинг до сервера не дошёл"), section), "")

    def test_past_lookup_text_is_saved_to_a_file_and_read_back(self) -> None:
        import tempfile
        from types import SimpleNamespace as NS

        from dns import commands

        with tempfile.TemporaryDirectory() as folder, patch("config.runtime_layout.APPLICATION_PATHS", NS(logs_dir=folder)):
            path = commands.save_domain_lookup_text("example.com", "строка 1\nстрока 2")
            self.assertTrue(path.startswith(folder) and path.endswith(".json"))
            self.assertEqual(commands.load_past_domain_lookup(path), "строка 1\nстрока 2")
            # Чужой или пропавший файл — пусто, а не ошибка.
            foreign = os.path.join(folder, "other.json")
            with open(foreign, "w", encoding="utf-8") as stream:
                stream.write('{"format": "something/else", "text": ["x"]}')
            self.assertEqual((commands.load_past_domain_lookup(foreign), commands.load_past_domain_lookup(""), commands.load_past_domain_lookup(path + "x")), ("", "", ""))


class DomainLookupPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_tab_runs_lookup_and_shows_results(self) -> None:
        # Одна страница на файл: повторное создание ломает общий qconfig при завершении.
        worker = SimpleNamespace(stage=Mock(), stop=Mock())
        feature = SimpleNamespace(create_domain_lookup_worker=Mock(return_value=worker))
        with patch.object(BlockcheckPage, "_request_page_initial_state_load", lambda self: None):
            host = BlockcheckPage(
                blockcheck_feature=SimpleNamespace(),
                dns_feature=feature,
                create_strategy_scan_worker=lambda *_args, **_kwargs: None,
            )
        self.addCleanup(host.deleteLater)

        # Вкладка стоит слева от «DNS подмена» и создаётся только при первом открытии.
        order = host.TAB_ORDER
        self.assertLess(order.index("domain_lookup"), order.index("dns_spoofing"))
        self.assertIsNone(host._domain_lookup_tab_page)
        host._switch_tab(order.index("domain_lookup"))
        page = host._domain_lookup_tab_page
        self.assertIsInstance(page, DomainLookupPage)
        self.assertFalse(page.isHidden())
        self.assertEqual(host._normalize_tab_key("ping"), "domain_lookup")

        # Пустой ввод ничего не запускает.
        page._lane.request = Mock()
        page.start_lookup()
        page._lane.request.assert_not_called()
        self.assertFalse(page._running)

        # Запуск: запрос уходит в дорожку, кнопки переключаются.
        page.set_target(" example.com ")
        page.external_check.setChecked(False)
        page.start_lookup()
        page._lane.request.assert_called_once_with({"target": "example.com", "use_external": False})
        self.assertTrue(page._running)
        self.assertFalse(page.start_button.isEnabled())
        self.assertFalse(page.stop_button.isHidden())
        page.start_lookup()  # повторное нажатие во время проверки ничего не делает
        page._lane.request.assert_called_once()

        # Фабрика воркера получает параметры и подписывается на промежуточные результаты.
        created = page._create_worker(5, {"target": "example.com", "use_external": False})
        self.assertIs(created, worker)
        feature.create_domain_lookup_worker.assert_called_once_with(5, target="example.com", use_external=False, parent=page)
        worker.stage.connect.assert_called_once()

        # «Стоп» просит воркер остановиться, а не убивает поток.
        page._lane.runtime.worker = worker
        page.stop_lookup()
        worker.stop.assert_called_once()
        page._lane.runtime.worker = None

        # Промежуточный результат чужого (устаревшего) запуска не показывается.
        page._on_stage(999, _report(target="old.example"))
        self.assertIsNone(page._report)

        # Промежуточные ответы идут десятками: экран перерисовывается один раз, последним из них.
        page._lane.runtime.is_current = lambda *_a, **_k: True
        shown: list = []
        with patch.object(page, "_show_report", shown.append):
            for name in ("a.example", "b.example", "c.example"):
                page._on_stage(5, _report(target=name))
            self.assertEqual(shown, [])
            page._flush_stage()
        self.assertEqual([item.target for item in shown], ["c.example"])

        # Итог: карточки видны, таблица заполнена, заглушка помечена.
        page._on_finished(_report())
        self.assertFalse(page._running)
        self.assertTrue(page.start_button.isEnabled())
        self.assertTrue(page.report_button.isEnabled())
        # Итог — тремя карточками (пути в этом отчёте нет); строк-виджетов на каждый сервер нет.
        cards = {card.key: card for card in page.result_cards()}
        self.assertEqual(list(cards), [lookup_cards.KEY_PING, lookup_cards.KEY_DNS, lookup_cards.KEY_NEIGHBORS])
        self.assertEqual([widget.card.key for widget in page.cards.cards()], list(cards))
        self.assertFalse(page.cards.isHidden())
        ping = cards[lookup_cards.KEY_PING]
        self.assertEqual((ping.title, ping.level, ping.status), ("Пинг и сеть", "ok", "Сервер отвечает"))
        self.assertIn(("ASN", "AS64500"), [(line.name, line.text) for line in ping.sections[1].lines])
        dns = cards[lookup_cards.KEY_DNS]
        self.assertEqual((dns.level, dns.status), ("fail", "Адрес назвали 2 из 2 серверов"))
        answers = dns.sections[1]
        self.assertTrue(answers.tiles)
        self.assertEqual(len(answers.lines), 2)
        self.assertIn("заглушка (Ростелеком)", answers.lines[1].text)
        self.assertEqual(answers.lines[1].state, "fail")
        # На самой карточке — только сервер, ответивший не как все.
        self.assertIn(answers.lines[1], dns.lines)
        self.assertNotIn(answers.lines[0], dns.lines)
        neighbors = cards[lookup_cards.KEY_NEIGHBORS]
        self.assertEqual(neighbors.status, "Найдено доменов: 2")
        self.assertEqual([line.name for line in neighbors.sections[1].lines], ["a.example", "b.example"])
        # Нажатие на карточку открывает её подробности страницей с хлебными крошками.
        page.cards.cards()[1].opened.emit(dns)
        detail = host._detail_view
        self.assertFalse(detail.isHidden())
        self.assertTrue(page.isHidden())
        self.assertEqual(detail.card(), dns)
        summary_block, answers_block = detail.blocks
        self.assertEqual([tile.title for tile in answers_block.grid.tiles()], ["Хороший", "Провайдер"])
        self.assertEqual(answers_block.rows, [])
        # Назад — на ту же вкладку.
        host._close_subpage()
        self.assertTrue(detail.isHidden())
        self.assertFalse(page.isHidden())
        # Законченная проверка сразу попадает в «Прошлые проверки» на этой вкладке.
        self.assertFalse(page.history_card.isHidden())
        [past] = page.history_rows.groups()
        self.assertEqual(past.title, "Прошлые проверки")
        self.assertTrue(past.rows[0].name.startswith(_report().target))
        # Прошлые проверки — плитками одной рисующей сетки, а не строкой-виджетом на запись.
        [block] = page.history_rows.blocks()
        self.assertEqual(block.grid.tiles()[0].title, _report().target)
        # Нажатие на плитку открывает ту проверку страницей: текст — из её файла.
        opened = []
        page.report_requested.connect(opened.append)
        feature.load_past_domain_lookup = Mock(return_value="полный текст")
        page._history_runs[-1]["log_file"] = "C:/logs/domain_lookup_1.json"
        QTest.mouseClick(block.grid, Qt.MouseButton.LeftButton, pos=block.grid.tile_rect(0).center().toPoint())
        feature.load_past_domain_lookup.assert_called_once_with("C:/logs/domain_lookup_1.json")
        self.assertEqual((opened[-1].text, opened[-1].title.split(" · ")[0]), ("полный текст", _report().target))
        # Файла нет (старая запись) — показываем то, что записано в истории, и говорим об этом.
        feature.load_past_domain_lookup = Mock(return_value="")
        page._open_past(0)
        self.assertIn("не сохранился", opened[-1].text)
        self.assertIn(_report().target, opened[-1].text)
        page.report_requested.disconnect(opened.append)
        host._switch_tab(order.index("domain_lookup"))

        # Отчёт с путём и найденным фильтром: карточка видна, в таблице узлов стоит отметка.
        from diagnostics.path_trace import FilterFacts, Hop, RouteTrace
        from diagnostics.quic_probe import QUIC_BLOCKED_BY_NAME, QuicVerdict
        from dns import domain_lookup_plans as lookup_plans
        from utils.windows_icmp import HOP_ROUTER, HOP_TARGET

        hops = tuple(Hop(ttl, HOP_ROUTER, f"10.0.0.{ttl}", 1.0) for ttl in range(1, 4)) + (Hop(4, HOP_TARGET, "93.184.216.34", 40.0),)
        page._on_finished(
            _report(
                route=RouteTrace("93.184.216.34", hops, reached=True),
                quic=QuicVerdict(QUIC_BLOCKED_BY_NAME, "блокируется по имени сайта"),
                filter_facts=FilterFacts(True, True, 3, 3),
            )
        )
        path = next(card for card in page.result_cards() if card.key == lookup_cards.KEY_PATH)
        self.assertEqual((path.title, path.level, path.status), ("Путь до сервера", "fail", "Найден фильтр по дороге"))
        self.assertIn("Фильтр стоит между узлом 2 (10.0.0.2) и узлом 3 (10.0.0.3).", [line.name for line in path.lines])
        road = path.sections[-1]
        self.assertTrue(road.tiles)
        self.assertEqual([row.name for row in road.lines], ["Узел 1", "Узел 2", lookup_plans.FILTER_MARK, "Узел 3", "Узел 4"])
        self.assertEqual((road.lines[2].state, road.lines[-1].state), ("fail", "info"))
        self.assertIn("сам сервер", road.lines[-1].text)
        page._on_finished(_report())
        self.assertNotIn(lookup_cards.KEY_PATH, [card.key for card in page.result_cards()])

        # Проверка адреса (не домена): таблицы DNS нет.
        page._on_finished(_report(kind=engine.KIND_IP, target="93.184.216.34", answers=()))
        self.assertNotIn(lookup_cards.KEY_DNS, [card.key for card in page.result_cards()])

        # Повторная проверка того же домена: поле соседей очищается и заполняется заново,
        # хотя текст совпадает с прошлым.
        page.start_lookup()
        self.assertEqual((page.result_cards(), page.cards.cards()), ([], []))
        self.assertTrue(page.cards.isHidden())
        page._on_finished(_report())
        self.assertEqual(len(page.cards.cards()), 3)

        # Смена языка переводит подписи и не теряет результат.
        host.set_ui_language("en")
        self.assertEqual(page.start_button.text(), "Check")
        self.assertEqual(host._tabs_pivot.items["domain_lookup"].text(), "Domain Lookup")
        self.assertIsNotNone(page._report)

        # Закрытие страницы закрывает дорожку: поздние результаты игнорируются.
        host.cleanup()
        self.assertTrue(page._closed)
        page._on_finished(_report(target="late.example"))
        self.assertNotEqual(page._report.target, "late.example")


if __name__ == "__main__":
    unittest.main()


class RowsViewReuseTests(unittest.TestCase):
    def test_only_changed_groups_are_rebuilt(self) -> None:
        from PyQt6.QtWidgets import QApplication

        from dns import domain_lookup_plans as plans
        from dns.ui.domain_lookup_page import RowsView

        _app = QApplication.instance() or QApplication([])

        def group(title: str, count: int):
            return plans.RowGroup(title, tuple(plans.Row("ok", f"строка {n}", "значение") for n in range(count)))

        view = RowsView()
        view.show_groups((group("Первая", 3), group("Вторая", 2)))
        first, second = list(view._blocks)

        # Вторая группа выросла: первая остаётся тем же виджетом, пересобирается только вторая.
        view.show_groups((group("Первая", 3), group("Вторая", 5)))

        self.assertIs(view._blocks[0], first)
        self.assertIsNot(view._blocks[1], second)
        self.assertEqual(len(view.groups()[1].rows), 5)
