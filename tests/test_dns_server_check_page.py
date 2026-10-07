import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, QSize
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QStyleOptionViewItem

from blockcheck.ui.page import BlockcheckPage
from dns import server_check as sc
from dns import server_check_plans as plans
from dns import server_check_verdict as verdicts
from dns.ui.server_check_cards import _CARD_HEIGHT, _LIST_MAX_HEIGHT, FILTER_ALL, ServerCardsView, SeverityBar, StatusFilter
from dns.ui.server_check_page import ServerCheckPage
from utils.dns_wire import FAILURE_REFUSED, FAILURE_TIMEOUT
from ui.widgets.fun.mascot import MOOD_ALARM, MOOD_BUSY, MOOD_IDLE
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
        # Коротко, чтобы помещалось в столбец; полная фраза — в подсказке строки.
        self.assertEqual(blocked.note, "Отвечает чужая сеть")
        self.assertIn("Обычные запросы выполняет сеть NSDI, а шифрованные — GOOGLE", blocked.tooltip)
        self.assertEqual((blocked.note_level, blocked.level), (sc.LEVEL_INFO, sc.LEVEL_WARN))
        self.assertEqual((healthy.level, healthy.note), (sc.LEVEL_OK, "Без замечаний"))

    def test_transport_answering_every_other_time_shows_how_many_of_how_many(self) -> None:
        shaky = sc.Cell(state=sc.STATE_OK, elapsed_ms=40.0, failure=FAILURE_TIMEOUT, reason="сервер молчит", trail=(True, False, False))
        row = _row(BACKUP, icmp=_ok(1.0), udp=_ok(9.0), tcp=_ok(20.0), dot=_ok(60.0), doh=shaky)
        (shown,) = plans.build_rows(_report(rows=(row,), total=1))

        self.assertEqual((shown.cells[-1], shown.cell_levels[-1]), ("40 мс · 1 из 3", plans.CELL_WARN))
        self.assertIn("DoH 443: 40 мс, ответил на 1 из 3 запросов (остальные: сервер молчит)", shown.tooltip)

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

        self.assertEqual(shown.note, "Ответы подменяются · Отвечает чужая сеть")
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


class VerdictTests(unittest.TestCase):
    def _real(self, rows, canary=False, **overrides) -> sc.ServerCheckReport:
        rows = tuple(rows)
        return _report(rows=rows, total=len(rows), canary=canary, findings=sc.judge_report(rows, canary, {}.get), **overrides)

    def test_findings_from_the_real_judge_split_into_title_and_detail(self) -> None:
        # Тексты берём у настоящего judge_report: смена их формата должна ронять этот тест.
        verdict = verdicts.build_verdict(self._real((BLOCKED, HEALTHY), canary=True))

        self.assertEqual((verdict.kind, verdict.suggests_encrypted_dns), (verdicts.KIND_FAIL, True))
        by_title = {item.title: item for item in verdict.items}
        intercepted = by_title["Обычные DNS-запросы перехватываются по дороге"]
        self.assertEqual(intercepted.level, sc.LEVEL_FAIL)
        self.assertTrue(intercepted.detail.startswith("Ответил адрес, где DNS-сервера нет."))
        dot = by_title["Шифрованный DNS по DoT (порт 853) закрыт у части серверов"]
        self.assertEqual((dot.level, dot.detail), (sc.LEVEL_WARN, "Google DNS (8.8.8.8)."))
        best = by_title["Для защищённого DNS сейчас лучше всего подходит Google DNS (8.8.4.4)"]
        self.assertEqual(best.detail, "Шифрованный запрос проходит за 55 мс. Без замечаний: 1 из 2 адресов.")
        for item in verdict.items:
            self.assertFalse(item.title.endswith((":", ".", " у")), item.title)

    def test_headline_follows_the_worst_finding(self) -> None:
        closed = sc.Observation(target=GOOGLE, cells=BLOCKED.cells, findings=BLOCKED.findings[1:])
        self.assertEqual(verdicts.build_verdict(self._real((closed, HEALTHY))).kind, verdicts.KIND_WARN)

        clean = verdicts.build_verdict(self._real((HEALTHY,)))
        self.assertEqual((clean.kind, clean.suggests_encrypted_dns), (verdicts.KIND_OK, False))

        bypass = sc.Finding(sc.LEVEL_INFO, sc.CODE_BYPASS_RUNNING, "Во время проверки работали: Zapret. Такие программы…")
        item = verdicts.split_finding(bypass)
        self.assertEqual((item.title, item.detail), ("Проверка шла вместе с программами обхода", bypass.text))

        stopped = verdicts.build_verdict(_report(rows=(HEALTHY,), stopped=True, findings=()))
        self.assertEqual((stopped.kind, stopped.items), (verdicts.KIND_STOPPED, ()))
        self.assertIn("1 из 2 адресов", stopped.detail)
        self.assertEqual(verdicts.build_verdict(_report(rows=(), total=0, findings=())).kind, verdicts.KIND_EMPTY)

    def test_counters_show_answering_and_silent_addresses(self) -> None:
        silent = sc.Cell(state=sc.STATE_FAIL, failure=FAILURE_TIMEOUT, reason="сервер молчит")
        dead = _row(
            sc.CheckTarget("Xbox DNS", "10.9.9.9"), icmp=_ok(1.0), udp=silent, tcp=silent, dot=silent, doh=silent,
            findings=(sc.Finding(sc.LEVEL_FAIL, sc.CODE_DEAD, "не отвечает ни одним способом"),),
        )
        rows = (BLOCKED, HEALTHY, dead)

        self.assertEqual(verdicts.tally(_report(rows=rows, total=3, finished=False)), verdicts.Tally(good=2, silent=1))


def _server(name: str, *addresses, findings=(), **cells) -> list[sc.Observation]:
    values = dict(icmp=_ok(1.0), udp=_ok(2.0), tcp=_ok(3.0), dot=_ok(40.0), doh=_ok(50.0))
    values.update(cells)
    return [_row(sc.CheckTarget(name, address, dot_host="d", doh_host="d"), findings=findings, **values) for address in addresses]


SILENT = sc.Cell(state=sc.STATE_FAIL, failure=FAILURE_TIMEOUT, reason="сервер молчит")
DEAD = sc.Finding(sc.LEVEL_FAIL, sc.CODE_DEAD, "не отвечает ни одним способом")


def _mixed_report(**overrides) -> sc.ServerCheckReport:
    """Серверы на каждый вывод."""
    rows = (
        *_server("Быстрый", "10.0.0.1", doh=_ok(20.0)),
        *_server("Медленный", "10.0.1.1", "10.0.1.2", doh=_ok(300.0)),
        *_server("Молчун", "10.0.2.1", udp=SILENT, tcp=SILENT, dot=SILENT, doh=SILENT, findings=(DEAD,)),
        *_server(
            "Фильтр",
            "10.0.3.1",
            findings=(sc.Finding(sc.LEVEL_INFO, sc.CODE_SELF_FILTER, "сам не отдаёт адреса сайтов: rutor.info"),),
        ),
        *_server(
            "Закрытый",
            "10.0.4.1",
            doh=_fail(FAILURE_REFUSED, "порт закрыт"),
            findings=(
                sc.Finding(sc.LEVEL_FAIL, sc.CODE_SPOOFED, "обычные ответы подменяются: про rutor.info"),
                sc.Finding(sc.LEVEL_WARN, sc.CODE_DOH_BLOCKED, "шифрованный DNS по DoH закрыт: порт закрыт"),
            ),
        ),
        *_server(
            "Без DoH",
            "10.0.6.1",
            doh=_fail(FAILURE_REFUSED, "порт закрыт"),
            findings=(sc.Finding(sc.LEVEL_WARN, sc.CODE_DOH_BLOCKED, "шифрованный DNS по DoH закрыт: порт закрыт"),),
        ),
        *_server(
            "Шаткий",
            "10.0.5.1",
            findings=(sc.Finding(sc.LEVEL_WARN, sc.CODE_UNSTABLE, "отвечает через раз: DoH — 1 из 3 запросов"),),
        ),
    )
    values = dict(rows=rows, total=len(rows), canary=False, findings=())
    values.update(overrides)
    return _report(**values)


class CardPlanTests(unittest.TestCase):
    def test_one_card_per_server_with_who_is_to_blame(self) -> None:
        cards = {card.server: card for card in plans.build_cards(_mixed_report())}

        self.assertEqual(
            {name: card.status for name, card in cards.items()},
            {
                "Быстрый": plans.CARD_OK,
                "Медленный": plans.CARD_OK,
                "Молчун": plans.CARD_SILENT,
                "Фильтр": plans.CARD_SELF,
                "Закрытый": plans.CARD_NETWORK,
                "Шаткий": plans.CARD_PARTIAL,
                "Без DoH": plans.CARD_PARTIAL,
            },
        )
        # Два адреса одного сервера — одна карточка, по точке на адрес у каждого способа связи.
        self.assertEqual(len(cards["Медленный"].addresses), 2)
        self.assertEqual(cards["Медленный"].dots, ((plans.CELL_OK,) * 2,) * 4)
        self.assertEqual(cards["Закрытый"].dots[-1], (plans.CELL_FAIL,))
        # На карточке ячеек не видно, поэтому закрытый способ назван словами; важное идёт первым.
        self.assertEqual(cards["Закрытый"].note, "Ответы подменяются · DoH закрыт")
        self.assertEqual(cards["Фильтр"].note, "Сам не отдаёт часть сайтов")
        self.assertEqual((cards["Быстрый"].note, cards["Быстрый"].best), ("", "DoH 20 мс"))
        self.assertIn("решил сам сервер", cards["Фильтр"].tooltip)
        self.assertIn("Это доказано", cards["Закрытый"].tooltip)
        # Закрытый способ связи сам по себе вину провайдера не доказывает: так мог сделать и сервер.
        self.assertEqual(cards["Без DoH"].note, "DoH закрыт")
        self.assertIn("и провайдер, и сам сервер", cards["Без DoH"].tooltip)

    def test_cards_go_from_blocked_to_silent_and_faster_first(self) -> None:
        order = [card.server for card in plans.build_cards(_mixed_report())]

        self.assertEqual(order, ["Закрытый", "Шаткий", "Без DoH", "Фильтр", "Быстрый", "Медленный", "Молчун"])

    def test_server_with_one_silent_address_is_alive(self) -> None:
        rows = (
            *_server("Двойной", "10.0.0.1"),
            *_server("Двойной", "10.0.0.2", udp=SILENT, tcp=SILENT, dot=SILENT, doh=SILENT, findings=(DEAD,)),
        )
        (card,) = plans.build_cards(_report(rows=rows, total=2, findings=()))

        self.assertEqual((card.status, card.note), (plans.CARD_OK, "Молчит адресов: 1 из 2"))

    def test_foreign_network_blames_the_road_only_when_interception_is_proven(self) -> None:
        foreign = sc.Finding(sc.LEVEL_INFO, sc.CODE_FOREIGN_ANSWERS, "обычные запросы выполняет сеть NSDI")
        rows = tuple(_server("Чужой", "10.0.0.1", findings=(foreign,)))
        plain = plans.build_cards(_report(rows=rows, total=1, findings=()))
        proven = plans.build_cards(
            _report(rows=rows, total=1, findings=(sc.Finding(sc.LEVEL_FAIL, sc.CODE_INTERCEPTED, "Перехват."),))
        )

        self.assertEqual((plain[0].status, proven[0].status), (plans.CARD_OK, plans.CARD_NETWORK))

    def test_unfinished_check_shows_only_answering_and_silent(self) -> None:
        rows = (*_server("Живой", "10.0.0.1"), *_server("Молчун", "10.0.2.1", udp=SILENT, tcp=SILENT, dot=SILENT, doh=SILENT))
        cards = plans.build_cards(_report(rows=rows, total=5, finished=False, findings=()))

        self.assertEqual([card.status for card in cards], [plans.CARD_OK, plans.CARD_SILENT])


class CardsViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _view(self) -> ServerCardsView:
        view = ServerCardsView()
        self.addCleanup(view.deleteLater)
        view.resize(1000, 400)
        view.set_cards(plans.build_cards(_mixed_report()))
        return view

    def test_filter_shows_one_group_and_names_it_for_screen_reader(self) -> None:
        view = self._view()
        self.assertEqual(len(view.cards()), 7)

        view.set_filter(plans.CARD_NETWORK)
        self.assertEqual([card.server for card in view.cards()], ["Закрытый"])
        self.assertIn("показано 1 из 7", view.accessibleName())
        spoken = view.model().index(0).data()
        self.assertIn("Закрытый. Блокируется по дороге. Ответы подменяются", spoken)

        view.set_filter("что-то не то")
        self.assertEqual((view.filter(), len(view.cards())), (FILTER_ALL, 7))

    def test_click_opens_addresses_and_list_never_grows_past_its_window(self) -> None:
        view = self._view()
        delegate, index = view.itemDelegate(), view.model().index(5)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 900, _CARD_HEIGHT)
        closed = delegate.sizeHint(option, index).height()
        self.assertEqual((closed, view.maximumHeight()), (_CARD_HEIGHT, _LIST_MAX_HEIGHT))

        view.toggle(index)
        self.assertTrue(view.is_expanded(5))
        # У «Медленного» два адреса: карточка выросла на две строки.
        self.assertGreater(delegate.sizeHint(option, index).height(), closed + 40)

        # Много серверов — окошко списка не растёт: лишнее прокручивается, а не рисуется.
        rows = tuple(row for number in range(60) for row in _server(f"Сервер {number}", f"10.1.{number}.1"))
        view.set_cards(plans.build_cards(_report(rows=rows, total=60, findings=())))
        self.assertEqual(view.maximumHeight(), _LIST_MAX_HEIGHT)

    def test_cards_paint_in_both_states(self) -> None:
        view = self._view()
        view.toggle(view.model().index(0))
        pixmap = QPixmap(QSize(1000, 400))
        pixmap.fill()
        view.render(pixmap)
        # Делегат отработал без ошибок и нарисовал цветное: полоски выводов, точки, плашки.
        image = pixmap.toImage()
        colors = {image.pixel(x, y) for x in range(0, 1000, 3) for y in range(0, 400, 3)}
        self.assertGreater(len(colors), 12)

    def test_bar_and_filter_show_how_bad_it_is(self) -> None:
        counts = plans.count_cards(plans.build_cards(_mixed_report()))
        bar, chips = SeverityBar(), StatusFilter()
        self.addCleanup(bar.deleteLater)
        self.addCleanup(chips.deleteLater)
        picked: list[str] = []
        chips.changed.connect(picked.append)

        bar.set_counts(counts)
        chips.set_counts(counts)
        self.assertEqual(bar.counts()[plans.CARD_OK], 2)
        self.assertIn("работают 2", bar.accessibleName())
        self.assertEqual(chips.chips[FILTER_ALL].text(), "Все 7")
        self.assertEqual(chips.chips[plans.CARD_NETWORK].text(), "Блокируются 1")
        self.assertEqual(chips.chips[plans.CARD_PARTIAL].text(), "Не полностью 2")
        self.assertIn("провайдер", chips.chips[plans.CARD_NETWORK].accessibleDescription())

        chips.chips[plans.CARD_SELF].click()
        self.assertEqual((picked, chips.current()), ([plans.CARD_SELF], plans.CARD_SELF))
        # Группа без серверов прячется, но выбранная остаётся, пока с неё не уйдут.
        chips.set_counts({plans.CARD_OK: 3})
        self.assertFalse(chips.chips[plans.CARD_SELF].isHidden())
        self.assertTrue(chips.chips[plans.CARD_NETWORK].isHidden())


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

        # До первой проверки таблицы нет, медоед ждёт и объясняет, что будет проверено.
        panel = page.verdict_panel
        self.assertEqual((panel.kind, panel.mascot.mood()), ("idle", MOOD_IDLE))
        self.assertIn("DoH (порт 443)", panel.detail_label.text())
        self.assertTrue(page.servers_card.isHidden())
        self.assertTrue(panel.progress_bar.isHidden())
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
        self.assertEqual((panel.kind, panel.mascot.mood()), ("pending", MOOD_BUSY))
        self.assertFalse(panel.progress_bar.isHidden())
        self.assertTrue(panel.ticker.is_running())

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

        # Промежуточные результаты копятся и показываются не чаще раза в несколько кадров,
        # а готовый отчёт из них пропускается: следом он придёт как итог.
        page._lane.runtime.is_current = Mock(return_value=True)
        partial = _report(rows=(HEALTHY,), finished=False, findings=())
        with patch.object(page, "_show_report", wraps=page._show_report) as shown:
            page._on_stage(5, _report(rows=(), finished=False, findings=()))
            page._on_stage(5, partial)
            page._on_stage(5, _report())
            shown.assert_not_called()
            page._flush_stage()
            shown.assert_called_once_with(partial)
        self.assertEqual((panel.kind, panel.progress_bar.value(), panel.progress_bar.maximum()), ("pending", 1, 2))
        self.assertIn("1 / 2", panel.title_label.text())
        self.assertEqual((panel.good_badge.value(), panel.good_badge.label_text()), (1, "✓ 1  отвечают"))
        self.assertEqual([card.server for card in page.cards.cards()], ["Google DNS"])

        # Итог: медоед насторожился, главная фраза и находки с заголовком и подробностями.
        page._on_finished(_report())
        self.assertEqual((panel.kind, panel.mascot.mood()), (verdicts.KIND_FAIL, MOOD_ALARM))
        self.assertEqual(panel.title_label.text(), "Обычный DNS перехватывают — нужен шифрованный")
        self.assertTrue(panel.progress_bar.isHidden())
        self.assertFalse(panel.ticker.is_running())
        titles = [row.title_label.text() for row in panel.finding_rows()]
        self.assertEqual(titles[0], "Обычные DNS-запросы перехватываются по дороге")
        self.assertIn("лучше всего подходит Google DNS (8.8.4.4)", titles[1])
        # Счётчики хода проверки уходят: их сменяют полоса и фильтр над карточками.
        self.assertTrue(panel.good_badge.isHidden())
        self.assertIn("Проверено 2 из 2 адресов", page.status_lines._lines[0].text)
        self.assertFalse(page._running)
        self.assertTrue(page.start_button.isEnabled())
        self.assertTrue(page.report_button.isEnabled())
        self.assertFalse(page.servers_card.isHidden())

        # Оба адреса Google — одна карточка: закрытые DoT и DoH на одном адресе видны точками и словами.
        (card,) = page.cards.cards()
        self.assertEqual((card.server, card.status, len(card.addresses)), ("Google DNS", plans.CARD_NETWORK, 2))
        self.assertEqual(card.note, "DoT закрыт · DoH закрыт · Отвечает чужая сеть")
        self.assertEqual(card.addresses[0].cells, ("нет ответа", "12 мс", "30 мс", "молчит", "порт закрыт"))
        self.assertEqual(page.severity_bar.counts()[plans.CARD_NETWORK], 1)
        self.assertEqual(page.status_filter.chips[plans.CARD_NETWORK].text(), "Блокируются 1")
        self.assertIn("с проблемами 1", page.cards.accessibleName())

        # Фильтр прячет лишнее, а новая проверка возвращает «Все».
        page.status_filter.chips[plans.CARD_NETWORK].click()
        self.assertEqual(page.cards.filter(), plans.CARD_NETWORK)

        # Новая проверка начинается с чистого экрана.
        page.start_check()
        self.assertTrue(page.servers_card.isHidden())
        self.assertEqual((page.cards.cards(), page.cards.filter()), ((), FILTER_ALL))
        self.assertEqual((panel.kind, panel.finding_rows()), ("pending", []))
        page._on_failed("нет сети")
        self.assertEqual((panel.kind, panel.title_label.text()), ("error", "Проверка не удалась"))
        self.assertEqual(panel.detail_label.text(), "нет сети")
        self.assertFalse(page._running)

        # Английский интерфейс: подписи кнопок и столбцов переведены.
        host.set_ui_language("en")
        self.assertEqual(page.start_button.text(), "Check servers")
        self.assertEqual(page.servers_title.text(), "Servers")
        self.assertEqual(host._tabs_pivot.items["dns_servers"].text(), "DNS Servers")

        # Закрытие страницы BlockCheck закрывает и дорожку вкладки.
        page._lane.close = Mock()
        host.cleanup()
        page._lane.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
