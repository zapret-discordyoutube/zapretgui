import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from blockcheck.ui.page import BlockcheckPage
from dns import domain_lookup as engine
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

        # Итог: карточки видны, таблица заполнена, заглушка помечена.
        page._on_finished(_report())
        self.assertFalse(page._running)
        self.assertTrue(page.start_button.isEnabled())
        self.assertTrue(page.report_button.isEnabled())
        self.assertFalse(page.ping_card.isHidden())
        self.assertFalse(page.dns_card.isHidden())
        self.assertFalse(page.neighbors_card.isHidden())
        [answers] = page.dns_rows.groups()
        self.assertEqual(len(answers.rows), 2)
        self.assertIn("заглушка (Ростелеком)", answers.rows[1].text)
        self.assertEqual(answers.rows[1].state, "fail")
        self.assertIn("a.example", _names(page.neighbors_rows))
        self.assertIn("AS64500", page.network_lines._lines[-1].text)
        # Пути в этом отчёте нет — карточка скрыта.
        self.assertTrue(page.path_card.isHidden())

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
        self.assertFalse(page.path_card.isHidden())
        self.assertEqual(page.path_title.text(), "Путь до сервера")
        self.assertIn("Фильтр стоит между узлом 2 (10.0.0.2) и узлом 3 (10.0.0.3).", [line.text for line in page.path_lines._lines])
        [road] = page.path_rows.groups()
        self.assertEqual([row.name for row in road.rows], ["Узел 1", "Узел 2", lookup_plans.FILTER_MARK, "Узел 3", "Узел 4"])
        self.assertEqual((road.rows[2].state, road.rows[-1].state), ("fail", "info"))
        self.assertIn("сам сервер", road.rows[-1].text)
        page._on_finished(_report())
        self.assertTrue(page.path_card.isHidden())

        # Проверка адреса (не домена): таблицы DNS нет.
        page._on_finished(_report(kind=engine.KIND_IP, target="93.184.216.34", answers=()))
        self.assertTrue(page.dns_card.isHidden())

        # Повторная проверка того же домена: поле соседей очищается и заполняется заново,
        # хотя текст совпадает с прошлым.
        page.start_lookup()
        self.assertEqual(page.neighbors_rows.groups(), ())
        page._on_finished(_report())
        self.assertIn("a.example", _names(page.neighbors_rows))

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
