import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QSize, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QStyleOptionViewItem

from blockcheck.ui.page import BlockcheckPage
from dns import server_check as sc
from dns import server_check_plans as plans
from dns import server_check_verdict as verdicts
from dns.dns_providers import DNS_PROVIDERS
from dns.ui import server_check_cards as cards_module
from dns.ui.server_check_details import ServerDetailView
from dns.ui.server_check_cards import (
    _CARD_HEIGHT,
    _COLUMN_MIN_WIDTH,
    FILTER_ALL,
    ServerCardsView,
    SeverityBar,
    StatusFilter,
)
from dns.ui.server_check_page import ServerCheckPage
from ui.widgets.fluent_item_tooltip import FLUENT_ITEM_TOOLTIP_ROLE
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


class DetailsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_details_tell_everything_about_every_address(self) -> None:
        fact = sc.DomainFact("rutor.info", udp_status="nxdomain", secure_status="ok", secure_ips=("6.6.6.6",))
        blocked = sc.Observation(
            target=GOOGLE, cells=BLOCKED.cells, findings=BLOCKED.findings, udp_egress="4.4.4.4", secure_egress="2.2.2.2", domains=(fact,)
        )
        details = plans.build_details(_report(rows=(blocked, HEALTHY)), "Google DNS")

        self.assertEqual([address.address for address in details.addresses], ["8.8.8.8", "8.8.4.4"])
        first = details.addresses[0]
        self.assertEqual(first.status, plans.CARD_NETWORK)
        # Причина отказа — словами, а не одним коротким словом из карточки.
        self.assertIn(("DoT 853", "молчит", plans.CELL_FAIL, "сервер молчит"), first.cells)
        self.assertIn((sc.LEVEL_WARN, "Шифрованный DNS по DoH закрыт: порт закрыт"), first.findings)
        self.assertEqual(first.who[0], "Обычные запросы выполняет: 4.4.4.4 (NSDI)")
        self.assertEqual(first.domains, (("rutor.info", "сайта нет", "6.6.6.6", True),))
        self.assertEqual(details.addresses[1].status, plans.CARD_OK)
        # Текст для кнопки «Скопировать» — тот же рассказ без разметки.
        self.assertIn("Google DNS — Блокируется по дороге", details.text)
        self.assertIn("DoT 853: сервер молчит", details.text)
        self.assertIsNone(plans.build_details(_report(), "Нет такого"))

    def test_details_keep_answer_time_for_speed_bars(self) -> None:
        details = plans.build_details(_report(), "Google DNS")

        # Время есть только у ответившего способа: у закрытого полоске расти не от чего.
        self.assertEqual(details.addresses[0].times_ms, (None, 12.4, 30.0, None, None))
        self.assertEqual(details.addresses[1].times_ms, (0.4, 9.0, 20.0, 60.0, 55.0))

    def test_summary_counts_addresses_per_transport(self) -> None:
        shaky = sc.Cell(state=sc.STATE_OK, elapsed_ms=40.0, failure=FAILURE_TIMEOUT, reason="сервер молчит", trail=(True, False, True))
        third = _row(
            sc.CheckTarget("Google DNS", "2001:4860:4860::8888"),
            icmp=_ok(1.0),
            udp=shaky,
            tcp=_ok(20.0),
            dot=sc.Cell(state=sc.STATE_SKIP),
            doh=_fail(FAILURE_TIMEOUT, "сервер молчит"),
        )
        details = plans.build_details(_report(rows=(BLOCKED, HEALTHY, third), total=3), "Google DNS")
        ping, udp, tcp, dot, doh = plans.build_transport_summary(details)

        # Молчание на пинг — не провал: считаем от всех адресов и не красим красным.
        self.assertEqual((ping.title, ping.answered, ping.total, ping.level), ("Пинг", 2, 3, plans.CELL_OK))
        self.assertIn("обычное дело", ping.note)
        self.assertEqual((udp.answered, udp.total, udp.level), (3, 3, plans.CELL_WARN))
        self.assertEqual(udp.note, "отвечает через раз на адресах: 1")
        self.assertEqual((tcp.answered, tcp.total, tcp.level, tcp.note), (3, 3, plans.CELL_OK, "отвечает без сбоев"))
        # Не объявленный на адресе способ в счёт не идёт.
        self.assertEqual((dot.answered, dot.total, dot.level), (1, 2, plans.CELL_FAIL))
        self.assertEqual(dot.dots, (plans.CELL_FAIL, plans.CELL_OK, plans.CELL_MUTED))
        self.assertEqual((doh.answered, doh.total, doh.note), (1, 3, "закрыт на адресах: 2"))
        self.assertTrue(all(item.about for item in (ping, udp, tcp, dot, doh)))

        dead = _row(BACKUP, icmp=_fail(FAILURE_TIMEOUT, "x"), udp=_fail(FAILURE_TIMEOUT, "x"), tcp=_fail(FAILURE_TIMEOUT, "x"), dot=sc.Cell(state=sc.STATE_SKIP), doh=sc.Cell(state=sc.STATE_SKIP))
        ping, udp, _tcp, dot, _doh = plans.build_transport_summary(plans.build_details(_report(rows=(dead,), total=1), "Google DNS"))
        self.assertEqual((ping.level, udp.level, udp.note), (plans.CELL_MUTED, plans.CELL_FAIL, "закрыт на всех адресах"))
        self.assertEqual((dot.level, dot.note), (plans.CELL_MUTED, "не объявлен у сервера"))

    def _details(self):
        odd = sc.CheckTarget("Свой <b>сервер</b>", "10.0.0.53")
        backup = sc.CheckTarget("Свой <b>сервер</b>", "10.0.0.54")
        fact = sc.DomainFact("rutor.info", udp_status="nxdomain", secure_status="ok", secure_ips=("6.6.6.6",))
        same = sc.DomainFact("rezka.ag", udp_status="ok", udp_ips=("7.7.7.7",), secure_status="ok", secure_ips=("7.7.7.7",))
        first = _row(
            odd,
            icmp=_ok(1.0),
            udp=_ok(2.0),
            tcp=_ok(3.0),
            dot=_fail(FAILURE_TIMEOUT, "сервер молчит"),
            doh=_ok(50.0),
            domains=(fact, same),
            udp_egress="4.4.4.4",
            secure_egress="2.2.2.2",
            findings=(sc.Finding(sc.LEVEL_WARN, sc.CODE_DOT_BLOCKED, "шифрованный DNS по DoT закрыт: сервер молчит"),),
        )
        second = _row(backup, icmp=_ok(1.0), udp=_ok(2.0), tcp=_ok(3.0), dot=_ok(4.0), doh=_ok(25.0))
        return plans.build_details(_report(rows=(first, second), total=2, findings=()), odd.provider)

    def test_page_shows_hero_summary_and_a_card_per_address(self) -> None:
        details = self._details()
        view = ServerDetailView()
        self.addCleanup(view.deleteLater)
        view.resize(1200, 600)
        view.show_details(details, animate=False)

        # Путь наверху, имя сервера как есть (без разбора разметки), вывод и его пояснение.
        self.assertEqual(view.breadcrumb.count(), 2)
        self.assertEqual(view.title_label.text(), "Свой <b>сервер</b>")
        self.assertEqual(view.title_label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(view.status_mark.text(), plans.CARD_TITLES[details.card.status])
        self.assertEqual(view.hint_label.text(), plans.CARD_HINTS[details.card.status])
        self.assertEqual(view.facts_label.text(), "адресов: 2 · DoH 25 мс")
        self.assertEqual(view.notes_label.text(), details.card.note)

        # Сводка: пять способов связи, у каждого счёт адресов и пояснение для новичка.
        self.assertEqual([tile.item.title for tile in view.summary_tiles], ["Пинг", "UDP 53", "TCP 53", "DoT 853", "DoH 443"])
        self.assertEqual([tile.value_label.text() for tile in view.summary_tiles][3:], ["1 из 2", "2 из 2"])
        self.assertTrue(all(tile.about_label.text() for tile in view.summary_tiles))

        first, second = view.address_cards
        self.assertEqual(first.address_label.text(), "10.0.0.53")
        self.assertEqual(first.status_mark.text(), plans.CARD_TITLES[first.address.status])
        self.assertEqual([tile.value_label.text() for tile in first.tiles], ["1 мс", "2 мс", "3 мс", "молчит", "50 мс"])
        # Причина отказа — словами под ячейкой; у ответившего способа её нет.
        self.assertEqual(first.tiles[3].reason_label.text(), "сервер молчит")
        self.assertTrue(first.tiles[0].reason_label.isHidden())
        # Полоска скорости — доля от самого долгого ответа сервера; у закрытого способа она пустая.
        self.assertEqual((first.tiles[4].bar._share, first.tiles[3].bar._share), (1.0, 0.0))
        self.assertEqual(second.tiles[4].bar._share, 0.5)
        self.assertEqual([row.title_label.text() for row in first.finding_rows], ["Шифрованный DNS по DoT закрыт: сервер молчит"])
        self.assertEqual(first.who_labels[0].text(), "Обычные запросы выполняет: 4.4.4.4 (NSDI)")
        # Контрольные сайты: расхождение обычного и шифрованного пути выделено, совпадение — нет.
        differs, same = first.domains_table.rows
        self.assertEqual([label.text() for label in differs], ["rutor.info", "сайта нет", "6.6.6.6"])
        self.assertTrue(differs[1].font().bold())
        self.assertFalse(same[1].font().bold())
        self.assertIsNone(second.domains_table)
        self.assertEqual((second.finding_rows, second.who_labels), ([], []))

    def test_page_uses_the_width_closes_and_copies(self) -> None:
        details = self._details()
        view = ServerDetailView()
        self.addCleanup(view.deleteLater)
        view.resize(1200, 600)
        view.show_details(details, animate=False)

        # На широкой странице адреса стоят в два столбца, на узкой — один под другим.
        self.assertEqual(view.columns(), 2)
        view.resize(700, 600)
        view._place_addresses()
        self.assertEqual(view.columns(), 1)

        closed: list[bool] = []
        view.closed.connect(lambda: closed.append(True))
        view._on_breadcrumb(view.SERVER_KEY)
        self.assertEqual(closed, [])
        view._on_breadcrumb(view.ROOT_KEY)
        QTest.keyClick(view, Qt.Key.Key_Escape)
        self.assertEqual(closed, [True, True])

        view.copy_button.click()
        self.assertEqual(QApplication.clipboard().text(), details.text)
        self.assertEqual(view.copy_button.text(), "Скопировано")

        # Счёт в сводке досчитывает от нуля, полоски скорости растут вместе с ним.
        view._on_reveal(0.0)
        self.assertEqual(view.summary_tiles[4].value_label.text(), "0 из 2")
        self.assertEqual(view.address_cards[0].tiles[4].bar._progress, 0.0)
        view._on_reveal(1.0)
        self.assertEqual(view.summary_tiles[4].value_label.text(), "2 из 2")

        # Другой сервер заменяет содержимое целиком, старые карточки не копятся.
        view.show_details(plans.build_details(_report(), "Google DNS"), animate=False)
        self.assertEqual([card.address_label.text() for card in view.address_cards], ["8.8.8.8", "8.8.4.4"])
        self.assertEqual(view.title_label.text(), "Google DNS")


class CardsViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _view(self) -> ServerCardsView:
        view = ServerCardsView()
        self.addCleanup(view.deleteLater)
        view.resize(1000, 400)
        view.viewport().resize(992, 400)
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

    def test_click_asks_to_open_details_and_list_is_as_tall_as_its_cards(self) -> None:
        """Своей прокрутки у списка нет: он вытянут по содержимому, прокручивает страница."""
        view = self._view()
        view.viewport().resize(_COLUMN_MIN_WIDTH + 100, 400)
        view._relayout_for_width()
        frame = 2 * view.frameWidth()
        self.assertEqual(view.columns(), 1)
        self.assertEqual((view.minimumHeight(), view.maximumHeight()), (7 * _CARD_HEIGHT + frame,) * 2)

        # Нажатие и Enter не раскрывают карточку на месте, а просят открыть подробности по серверу.
        opened: list[str] = []
        view.opened.connect(opened.append)
        view.clicked.emit(view.model().index(5))
        view.activated.emit(view.model().index(0))
        self.assertEqual(opened, ["Медленный", "Закрытый"])
        self.assertEqual(view.maximumHeight(), 7 * _CARD_HEIGHT + frame)
        self.assertIn("Нажмите, чтобы открыть подробности", view.model().index(5).data(FLUENT_ITEM_TOOLTIP_ROLE))

        # Много серверов — список растёт вместе с ними, а не прячет их под своей прокруткой.
        rows = tuple(row for number in range(60) for row in _server(f"Сервер {number}", f"10.1.{number}.1"))
        view.set_cards(plans.build_cards(_report(rows=rows, total=60, findings=())))
        self.assertEqual(view.maximumHeight(), 60 * _CARD_HEIGHT + frame)
        self.assertEqual(view.verticalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def test_wide_window_puts_cards_in_columns(self) -> None:
        view = self._view()
        frame = 2 * view.frameWidth()
        view.viewport().resize(_COLUMN_MIN_WIDTH + 200, 400)
        self.assertEqual(view.columns(), 1)

        # Ширина считается по окошку списка: у самого виджета по бокам поля оформления.
        view.viewport().resize(2 * _COLUMN_MIN_WIDTH + 40, 400)
        self.assertEqual(view.columns(), 2)
        view._relayout_for_width()
        # Две колонки занимают чуть меньше окошка: ряд «ровно до края» Qt переносит на новую строку.
        self.assertLess(2 * view.column_width(), view.viewport().width())
        self.assertGreater(2 * view.column_width(), view.viewport().width() - 4)
        self.assertEqual((view.is_last_column(0), view.is_last_column(1)), (False, True))
        # Семь карточек в две колонки — четыре ряда одного роста.
        self.assertEqual(view.maximumHeight(), 4 * _CARD_HEIGHT + frame)
        option = QStyleOptionViewItem()
        size = view.itemDelegate().sizeHint(option, view.model().index(5))
        self.assertEqual((size.width(), size.height()), (view.column_width(), _CARD_HEIGHT))

        # Очень широкое окно — не больше трёх колонок: карточки не дробятся в мелочь.
        view.viewport().resize(6000, 400)
        self.assertEqual(view.columns(), 3)

    def test_cards_really_stand_side_by_side_on_a_wide_list(self) -> None:
        """Проверка самой раскладки Qt, а не наших расчётов: вторая карточка стоит справа от первой."""
        view = self._view()
        view.resize(2 * _COLUMN_MIN_WIDTH + 60, 400)
        view.show()
        self._app.processEvents()
        self._app.processEvents()

        self.assertEqual(view.columns(), 2)
        first, second, third = (view.visualRect(view.model().index(row)) for row in range(3))
        self.assertEqual((first.y(), second.y()), (0, 0))
        self.assertGreater(second.x(), first.x() + _COLUMN_MIN_WIDTH - 1)
        self.assertEqual((third.x(), third.y()), (first.x(), _CARD_HEIGHT))
        # Всё содержимое на виду: прокручивать внутри списка нечего.
        self.assertEqual(view.verticalScrollBar().maximum(), 0)

    def test_mouse_over_the_list_does_not_crash(self) -> None:
        """Список библиотеки сообщает делегату о наведении; раньше на этом падала программа."""
        view = self._view()

        view._setHoverRow(2)
        self.assertEqual(view.hovered_row(), 2)
        view._setPressedRow(2)
        view._setSelectedRows([view.model().index(2)])
        view.leaveEvent(QEvent(QEvent.Type.Leave))
        self.assertEqual(view.hovered_row(), -1)

    def test_cards_paint_with_icon_and_road_for_every_verdict(self) -> None:
        view = self._view()
        pixmap = QPixmap(QSize(1000, 700))
        pixmap.fill()
        view.render(pixmap)
        # Делегат отработал без ошибок на всех выводах и нарисовал цветное: значки, дорожки, точки, плашки.
        image = pixmap.toImage()
        colors = {image.pixel(x, y) for x in range(0, 1000, 3) for y in range(0, 700, 3)}
        self.assertGreater(len(colors), 20)
        # Значок и цвет сервера приходят из каталога.
        cloudflare = sc.build_targets(DNS_PROVIDERS)[0]
        self.assertTrue(cloudflare.icon and cloudflare.color.startswith("#"), cloudflare)
        (card,) = plans.build_cards(_report(rows=(_row(cloudflare, icmp=_ok(1), udp=_ok(1), tcp=_ok(1), dot=_ok(1), doh=_ok(1)),), total=1, findings=()))
        self.assertEqual((card.icon, card.color), (cloudflare.icon, cloudflare.color))

    def test_road_runs_only_while_cards_are_on_screen_and_repaints_only_its_strip(self) -> None:
        view = self._view()
        self.assertFalse(view.is_road_running())
        # Неподвижная картинка: без такта кадров запросы стоят на месте.
        self.assertEqual(view.road_phase(0), view.road_phase(0))

        view.show()
        self._app.processEvents()
        self.assertTrue(view.is_road_running())

        # Первый кадр после показа Qt рисует целиком (раскладка списка), дальше — только полоски.
        view._on_road_frame()
        self._app.processEvents()

        # Кадр дорожек просит перерисовать только полоски, и делегат рисует только их.
        with patch.object(view.itemDelegate(), "_road", wraps=view.itemDelegate()._road) as road, patch(
            "dns.ui.server_check_cards.profile_icon_pixmap", wraps=cards_module.profile_icon_pixmap
        ) as icon:
            view._on_road_frame()
            self.assertFalse(view._road_region.isEmpty())
            self._app.processEvents()
            self.assertGreater(road.call_count, 0)
            icon.assert_not_called()

        # Живые анимации выключены — такт останавливается, картинка остаётся.
        with patch("dns.ui.server_check_cards.are_live_animations_enabled", return_value=False):
            view._on_road_frame()
        self.assertFalse(view.is_road_running())

        view.hide()
        self.assertFalse(view.is_road_running())

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

        # Нажатие на карточку открывает подробности сервера на всю страницу: вкладки и список уходят.
        page._open_details("Google DNS")
        detail = host._server_detail_view
        self.assertIsInstance(detail, ServerDetailView)
        self.assertFalse(detail.isHidden())
        self.assertEqual(detail.details().card.server, "Google DNS")
        self.assertTrue(host._tabs_pivot.isHidden())
        self.assertTrue(page.isHidden())
        # Строка пути возвращает ко вкладке со списком серверов.
        detail.closed.emit()
        self.assertTrue(detail.isHidden())
        self.assertFalse(host._tabs_pivot.isHidden())
        self.assertFalse(page.isHidden())
        # Смена вкладки снаружи тоже закрывает подробности, а не оставляет их поверх.
        page._open_details("Google DNS")
        host._switch_tab(order.index("dns_servers"))
        self.assertTrue(detail.isHidden())
        self.assertFalse(host._tabs_pivot.isHidden())
        page._open_details("Нет такого")
        self.assertTrue(detail.isHidden())

        # «Отчёт» открывается страницей-редактором на месте вкладки; путь ведёт обратно на неё.
        page.report_button.click()
        report_view = host._log_report_view
        self.assertFalse(report_view.isHidden())
        self.assertTrue(host._tabs_pivot.isHidden())
        self.assertTrue(page.isHidden())
        self.assertEqual(report_view.report().root_title, "DNS-серверы")
        self.assertIn("ПРОВЕРКА DNS-СЕРВЕРОВ", report_view.editor.toPlainText())
        self.assertEqual(report_view.section_chips[0].text(), "ПРОВЕРКА DNS-СЕРВЕРОВ")
        # Из отчёта можно сразу перейти к подробностям сервера: открытой остаётся одна страница.
        page._open_details("Google DNS")
        self.assertTrue(report_view.isHidden())
        self.assertFalse(detail.isHidden())
        detail.closed.emit()
        self.assertFalse(page.isHidden())
        page.report_button.click()
        report_view.closed.emit()
        self.assertTrue(report_view.isHidden())
        self.assertFalse(host._tabs_pivot.isHidden())
        self.assertFalse(page.isHidden())

        # Отчёт главной вкладки BlockCheck открывается той же страницей под своим названием.
        host._report_lines = ["=== DNS ===", "✅ discord.com 12 мс"]
        host._open_report()
        self.assertFalse(report_view.isHidden())
        self.assertEqual((report_view.report().root_title, report_view.title_label.text()), ("DNS-серверы", "Подробный отчёт BlockCheck"))
        host._switch_tab(order.index("dns_servers"))
        self.assertTrue(report_view.isHidden())

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
