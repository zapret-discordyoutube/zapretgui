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
        self.assertEqual(order.index("domain_lookup") + 1, order.index("dns_spoofing"))
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
        self.assertEqual(page.dns_table.rowCount(), 2)
        self.assertIn("заглушка (Ростелеком)", page.dns_table.item(1, 2).text())
        self.assertIn("a.example", page.neighbors_text.toPlainText())
        self.assertIn("AS64500", page.network_lines._lines[-1].text)

        # Проверка адреса (не домена): таблицы DNS нет.
        page._on_finished(_report(kind=engine.KIND_IP, target="93.184.216.34", answers=()))
        self.assertTrue(page.dns_card.isHidden())

        # Повторная проверка того же домена: поле соседей очищается и заполняется заново,
        # хотя текст совпадает с прошлым.
        page.start_lookup()
        self.assertEqual(page.neighbors_text.toPlainText(), "")
        page._on_finished(_report())
        self.assertIn("a.example", page.neighbors_text.toPlainText())

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
