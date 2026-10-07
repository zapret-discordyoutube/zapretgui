import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from blockcheck.ui.page import BlockcheckPage
from dns import server_check as sc
from dns import server_check_plans as plans
from dns.ui.server_check_page import ServerCheckPage
from utils.dns_wire import FAILURE_REFUSED, FAILURE_TIMEOUT
from utils.ip_owner import IpOwner

GOOGLE = sc.CheckTarget("Google DNS", "8.8.8.8", dot_host="dns.google", doh_host="dns.google")
BACKUP = sc.CheckTarget("Google DNS", "8.8.4.4", dot_host="dns.google", doh_host="dns.google")


def _ok(ms: float) -> sc.Cell:
    return sc.Cell(state=sc.STATE_OK, elapsed_ms=ms)


def _fail(failure: str, reason: str) -> sc.Cell:
    return sc.Cell(state=sc.STATE_FAIL, failure=failure, reason=reason)


def _row(target, *, icmp, udp, tcp, dot, doh, findings=(), **extra) -> sc.Observation:
    cells = tuple(zip(sc.TRANSPORTS, (icmp, udp, tcp, dot, doh)))
    return sc.Observation(target=target, cells=cells, findings=tuple(findings), **extra)


BLOCKED = _row(
    GOOGLE,
    icmp=_fail(FAILURE_TIMEOUT, "не отвечает на пинг"),
    udp=_ok(12.4),
    tcp=_ok(30.0),
    dot=_fail(FAILURE_TIMEOUT, "сервер молчит"),
    doh=_fail(FAILURE_REFUSED, "порт закрыт"),
    udp_egress="4.4.4.4",
    secure_egress="2.2.2.2",
    findings=(
        sc.Finding(sc.LEVEL_INFO, sc.CODE_FOREIGN_ANSWERS, "обычные запросы выполняет сеть NSDI, а шифрованные — GOOGLE"),
        sc.Finding(sc.LEVEL_WARN, sc.CODE_DOT_BLOCKED, "шифрованный DNS по DoT закрыт: сервер молчит"),
        sc.Finding(sc.LEVEL_WARN, sc.CODE_DOH_BLOCKED, "шифрованный DNS по DoH закрыт: порт закрыт"),
    ),
)
HEALTHY = _row(BACKUP, icmp=_ok(0.4), udp=_ok(9.0), tcp=_ok(20.0), dot=_ok(60.0), doh=_ok(55.0))


def _report(**overrides) -> sc.ServerCheckReport:
    values = dict(
        rows=(BLOCKED, HEALTHY),
        total=2,
        canary=True,
        owners=(("4.4.4.4", IpOwner(asn="8492", owner="NSDI")), ("2.2.2.2", IpOwner(asn="15169", owner="GOOGLE"))),
        findings=(
            sc.Finding(sc.LEVEL_FAIL, sc.CODE_INTERCEPTED, "Обычные DNS-запросы перехватываются по дороге."),
            sc.Finding(sc.LEVEL_OK, sc.CODE_BEST, "Для защищённого DNS сейчас лучше всего подходит Google DNS (8.8.4.4)."),
        ),
        finished=True,
        elapsed_s=12.0,
    )
    values.update(overrides)
    return sc.ServerCheckReport(**values)


class PlanTests(unittest.TestCase):
    def test_cells_show_time_or_short_reason(self) -> None:
        blocked, healthy = plans.build_rows(_report())

        self.assertEqual(blocked.cells, ("нет ответа", "12 мс", "30 мс", "молчит", "порт закрыт"))
        self.assertEqual(healthy.cells, ("< 1 мс", "9 мс", "20 мс", "60 мс", "55 мс"))

    def test_silent_ping_is_not_painted_as_failure(self) -> None:
        blocked, healthy = plans.build_rows(_report())

        self.assertEqual(blocked.cell_levels, (plans.CELL_MUTED, plans.CELL_OK, plans.CELL_OK, plans.CELL_FAIL, plans.CELL_FAIL))
        self.assertEqual(set(healthy.cell_levels), {plans.CELL_OK})

    def test_note_does_not_repeat_what_cells_already_show(self) -> None:
        blocked, healthy = plans.build_rows(_report())

        # Закрытые DoT и DoH видны в ячейках; в замечаниях остаётся то, чего там нет.
        self.assertEqual(blocked.note, "Обычные запросы выполняет сеть NSDI, а шифрованные — GOOGLE")
        self.assertEqual((blocked.note_level, blocked.level), (sc.LEVEL_INFO, sc.LEVEL_WARN))
        self.assertEqual((healthy.level, healthy.note), (sc.LEVEL_OK, "Без замечаний"))

    def test_row_with_only_closed_transports_has_empty_note_not_all_clear(self) -> None:
        only_closed = sc.Observation(
            target=GOOGLE,
            cells=BLOCKED.cells,
            findings=(sc.Finding(sc.LEVEL_WARN, sc.CODE_DOT_BLOCKED, "шифрованный DNS по DoT закрыт: сервер молчит"),),
        )
        (shown,) = plans.build_rows(_report(rows=(only_closed,), total=1))

        self.assertEqual((shown.note, shown.level), ("", sc.LEVEL_WARN))
        self.assertIn("! Шифрованный DNS по DoT закрыт", shown.tooltip)

    def test_most_important_remark_goes_first(self) -> None:
        spoofed = sc.Observation(
            target=GOOGLE,
            cells=HEALTHY.cells,
            findings=(
                sc.Finding(sc.LEVEL_INFO, sc.CODE_FOREIGN_ANSWERS, "обычные запросы выполняет сеть NSDI"),
                sc.Finding(sc.LEVEL_FAIL, sc.CODE_SPOOFED, "обычные ответы подменяются"),
            ),
        )
        (shown,) = plans.build_rows(_report(rows=(spoofed,), total=1))

        self.assertEqual(shown.note, "Обычные ответы подменяются (и ещё 1)")
        self.assertEqual(shown.note_level, sc.LEVEL_FAIL)

    def test_tooltip_keeps_full_reasons_and_who_answers(self) -> None:
        tooltip = plans.build_rows(_report())[0].tooltip

        self.assertIn("DoT 853: сервер молчит", tooltip)
        self.assertIn("Кто выполняет обычные запросы: 4.4.4.4 (NSDI)", tooltip)
        self.assertIn("Кто выполняет шифрованные: 2.2.2.2 (GOOGLE)", tooltip)

    def test_server_without_encrypted_transport_shows_dash(self) -> None:
        skip = sc.Cell(state=sc.STATE_SKIP, reason="сервер не объявлял этот способ")
        row = _row(sc.CheckTarget("Свой", "10.0.0.53"), icmp=_ok(1.0), udp=_ok(2.0), tcp=_ok(3.0), dot=skip, doh=skip)
        (shown,) = plans.build_rows(_report(rows=(row,), total=1))

        self.assertEqual(shown.cells[-2:], ("—", "—"))
        self.assertEqual(shown.cell_levels[-2:], (plans.CELL_MUTED, plans.CELL_MUTED))

    def test_status_follows_run_state(self) -> None:
        running = plans.build_status(_report(rows=(BLOCKED,), finished=False))
        self.assertIn("готово 1 из 2", running.text)

        stopped = plans.build_status(_report(rows=(BLOCKED,), stopped=True))
        self.assertIn("остановлена", stopped.text)

        self.assertIn("Проверено 2 из 2 адресов за 12 с", plans.build_status(_report()).text)
        self.assertIn("не уложилась", plans.build_status(_report(timed_out=True)).text)
        self.assertIn("нет ни одного сервера", plans.build_status(_report(rows=(), total=0)).text)

    def test_unfinished_row_has_no_all_clear_yet(self) -> None:
        rows = plans.build_rows(_report(rows=(HEALTHY,), finished=False))

        self.assertEqual(rows[0].note, "")

    def test_text_report_has_summary_table_and_details(self) -> None:
        text = plans.build_text_report(_report())

        self.assertIn("ПРОВЕРКА DNS-СЕРВЕРОВ", text)
        self.assertIn("✗ Обычные DNS-запросы перехватываются по дороге.", text)
        self.assertIn("Контрольный адрес без DNS-сервера: ОТВЕТИЛ", text)
        header = next(line for line in text.splitlines() if line.startswith("Сервер"))
        for title in ("Пинг", "UDP 53", "TCP 53", "DoT 853", "DoH 443"):
            self.assertIn(title, header)
        self.assertIn("обычные запросы выполняет: 4.4.4.4 (NSDI)", text)
        self.assertIn("! Шифрованный DNS по DoH закрыт: порт закрыт", text)


class ServerCheckPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_tab_runs_check_and_shows_results(self) -> None:
        # Одна страница на файл: повторное создание ломает общий qconfig при завершении.
        worker = SimpleNamespace(stage=Mock(), stop=Mock())
        feature = SimpleNamespace(create_server_check_worker=Mock(return_value=worker))
        with patch.object(BlockcheckPage, "_request_page_initial_state_load", lambda self: None):
            host = BlockcheckPage(
                blockcheck_feature=SimpleNamespace(),
                dns_feature=feature,
                create_strategy_scan_worker=lambda *_args, **_kwargs: None,
            )
        self.addCleanup(host.deleteLater)

        # Вкладка стоит между «Проверкой домена» и «DNS подменой» и создаётся при первом открытии.
        order = host.TAB_ORDER
        self.assertEqual(order.index("domain_lookup") + 1, order.index("dns_servers"))
        self.assertEqual(order.index("dns_servers") + 1, order.index("dns_spoofing"))
        self.assertIsNone(host._dns_servers_tab_page)
        host._switch_tab(order.index("dns_servers"))
        page = host._dns_servers_tab_page
        self.assertIsInstance(page, ServerCheckPage)
        self.assertFalse(page.isHidden())
        self.assertEqual(host._normalize_tab_key("servers"), "dns_servers")
        self.assertEqual(host._tabs_pivot.currentRouteKey(), "dns_servers")

        # До первой проверки показывать нечего.
        self.assertTrue(page.summary_card.isHidden())
        self.assertTrue(page.table_card.isHidden())
        self.assertFalse(page.report_button.isEnabled())

        # Запуск: запрос уходит в дорожку, кнопки переключаются.
        page._lane.request = Mock()
        page.start_check()
        page._lane.request.assert_called_once_with()
        self.assertTrue(page._running)
        self.assertFalse(page.start_button.isEnabled())
        self.assertFalse(page.stop_button.isHidden())
        page.start_check()  # повторное нажатие во время проверки ничего не делает
        page._lane.request.assert_called_once()

        # Фабрика воркера подписывается на промежуточные результаты.
        created = page._create_worker(5, None)
        self.assertIs(created, worker)
        feature.create_server_check_worker.assert_called_once_with(5, parent=page)
        worker.stage.connect.assert_called_once()

        # «Стоп» просит воркер остановиться, а не убивает поток.
        page._lane.runtime.worker = worker
        page.stop_check()
        worker.stop.assert_called_once()
        page._lane.runtime.worker = None

        # Промежуточный результат чужого (устаревшего) запуска не показывается.
        page._on_stage(999, _report())
        self.assertIsNone(page._report)

        # Итог: карточки видны, таблица заполнена по столбцам способов связи.
        page._on_finished(_report())
        self.assertFalse(page._running)
        self.assertTrue(page.start_button.isEnabled())
        self.assertTrue(page.report_button.isEnabled())
        self.assertFalse(page.summary_card.isHidden())
        self.assertFalse(page.table_card.isHidden())
        table = page.table
        headers = [table.horizontalHeaderItem(column).text() for column in range(table.columnCount())]
        self.assertEqual(headers, ["Сервер", "Адрес", "Пинг", "UDP 53", "TCP 53", "DoT 853", "DoH 443", "Замечания"])
        self.assertEqual(table.rowCount(), 2)
        shown = [table.item(0, column).text() for column in range(table.columnCount())]
        self.assertEqual(shown[:7], ["Google DNS", "8.8.8.8", "нет ответа", "12 мс", "30 мс", "молчит", "порт закрыт"])
        self.assertIn("NSDI", shown[7])
        self.assertEqual(table.item(1, 1).text(), "8.8.4.4")
        self.assertIn("перехватываются", page.summary_lines._lines[0].text)
        self.assertIn("с замечаниями 1", table.accessibleName())

        # Новая проверка начинается с чистого экрана.
        page.start_check()
        self.assertTrue(page.table_card.isHidden())
        self.assertEqual(table.rowCount(), 0)
        page._on_failed("нет сети")
        self.assertIn("Проверка не удалась: нет сети", page.status_lines._lines[0].text)
        self.assertFalse(page._running)

        # Английский интерфейс: подписи кнопок и столбцов переведены.
        host.set_ui_language("en")
        self.assertEqual(page.start_button.text(), "Check servers")
        self.assertEqual(table.horizontalHeaderItem(7).text(), "Notes")
        self.assertEqual(host._tabs_pivot.items["dns_servers"].text(), "DNS Servers")

        # Закрытие страницы BlockCheck закрывает и дорожку вкладки.
        page._lane.close = Mock()
        host.cleanup()
        page._lane.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
