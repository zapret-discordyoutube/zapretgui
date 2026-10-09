import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget

from blockcheck.ui.page import SCOPE_FULL, BlockcheckPage
from blockcheck.ui.result_cards import (
    CardsGrid,
    HostingDots,
    ProgressSteps,
    ResultCardsView,
    ResultDetailView,
    card_plain_text,
)
from blockcheck.ui.result_cards_model import FILTER_MARK, PREVIEW_LINES, Card, Line, build_cards, build_counters
from diagnostics.freeze_check import FreezeState, check_freeze, every_freeze_target
from diagnostics.tls_probe import ProbeResult


def _target(host, purpose="сайт", ok=True, **extra):
    item = {
        "host": host,
        "purpose": purpose,
        "ok": ok,
        "state": "ok" if ok else "dpi",
        "short": "открывается" if ok else "соединение сброшено",
        "text": "открывается (20 мс)" if ok else "соединение сброшено (1.2.3.4)",
    }
    item.update(extra)
    return item


def _service(key, label, level, targets, **extra):
    return {"key": key, "label": label, "level": level, "kind": "", "targets": targets, **extra}


def _servers():
    rows = [
        ("AWS", "DE.AWS-01", "a.example", "freeze", "загрузка оборвалась на 16 КБ", "download"),
        ("AWS", "FR.AWS-02", "b.example", "ok", "получено 32 КБ без обрыва", "download"),
        ("Akamai", "SE.AKM-01", "c.example", "freeze", "загрузка проходит, но отправка замирает", "upload"),
        ("OVH", "FR.OVH-01", "d.example", "unknown", "не удалось проверить", "download"),
    ]
    return [
        {"provider": p, "id": i, "host": h, "state": s, "text": t, "direction": d, "seconds": 1.25}
        for p, i, h, s, t, d in rows
    ]


_REPORT = {
    "scope": "full",
    "services": [
        _service("youtube", "YouTube", "warn", [
            _target("www.youtube.com", quic="blocked_by_name", quic_text="блокируется по имени сайта"),
            _target("i.ytimg.com", "превью видео"),
        ], dns_note="DNS подменяет адрес www.youtube.com"),
        _service("x", "X (Twitter)", "fail", [
            _target("x.com", ok=False, cause="by_name", cause_text="Блокировка по имени сайта.", volume="", note="адрес из hosts"),
        ], kind="sni", advice=["Подберите стратегию"]),
        _service("google", "Google", "ok", [_target("www.google.com", quic="ok", quic_text="отвечает")], control=True),
        _service("user:my.example", "my.example", "unknown", [_target("my.example", ok=False, state="unknown")]),
    ],
    "freeze": {"level": "fail", "headline": "Обрыв", "advice": ["Подберите стратегию"], "items": [], "servers": _servers()},
    "voice": {
        "level": "warn",
        "headline": "Звонки с перебоями",
        "advice": [],
        "items": [
            {"name": "Google STUN", "ok": True, "state": "ok", "text": "отвечает"},
            {"name": "Telegram STUN", "ok": False, "state": "fail", "text": "не ответил"},
        ],
    },
    "reference": [
        {"label": "Cloudflare", "address": "1.1.1.1", "ok": True, "reason": ""},
        {"label": "Google", "address": "8.8.8.8", "ok": False, "reason": "сервер молчит"},
    ],
    "spoofed_hosts": [],
    "ipv6": {"state": "absent", "text": "в этой сети его нет"},
    "dns_servers": {
        "level": "fail",
        "findings": [{"level": "fail", "text": "Обычные DNS-запросы перехватываются по дороге."}],
        "text": "полная таблица серверов",
    },
    "filter": {
        "host": "rutracker.org",
        "address": "104.21.32.39",
        "found": True,
        "hop": 2,
        "text": "фильтр стоит между узлом 1 и узлом 2",
        "hops": [
            {"ttl": 1, "address": "10.0.0.1", "rtt_ms": 0.4},
            {"ttl": 2, "address": "10.0.0.2", "rtt_ms": 42.0},
            {"ttl": 3, "address": "", "rtt_ms": None},
        ],
    },
    "system": [
        {"key": "admin", "title": "Права администратора", "level": "ok", "text": "есть", "advice": ""},
        {"key": "bfe", "title": "Служба BFE", "level": "fail", "text": "не работает", "advice": "Включите службу"},
        {"key": "proxy", "title": "Системный прокси", "level": "warn", "text": "включён", "advice": ""},
        {"key": "av", "title": "Антивирус", "level": "info", "text": "Kaspersky", "advice": ""},
    ],
}


class CardsModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cards = {card.key: card for card in build_cards(_REPORT)}

    def test_broken_sites_go_first_and_checks_follow_sites(self) -> None:
        keys = list(self.cards)

        self.assertEqual(keys[0], "site:x")
        self.assertEqual(keys[4:], ["hostings", "voice", "dns", "dns_servers", "ipv6", "filter", "system"])
        self.assertTrue(all(self.cards[key].site for key in keys[:4]))

    def test_site_card_names_the_kind_and_keeps_every_measurement(self) -> None:
        card = self.cards["site:x"]

        self.assertEqual((card.level, card.kind, card.status), ("fail", "sni", "По имени (SNI)"))
        self.assertIn(("блокировка по имени", "fail"), card.chips)
        detail = card_plain_text(card)
        for expected in ("x.com — сайт", "Блокировка по имени сайта.", "адрес из hosts", "Подберите стратегию"):
            self.assertIn(expected, detail)

    def test_open_site_with_spoofed_dns_is_not_a_broken_site(self) -> None:
        card = self.cards["site:youtube"]

        self.assertEqual((card.level, card.status), ("ok", "Открывается"))
        self.assertIn(("QUIC: закрыт", "warn"), card.chips)
        self.assertIn(("DNS: подменён", "warn"), card.chips)
        self.assertEqual([line.name for line in card.lines], ["сайт", "превью видео"])

    def test_control_and_unknown_sites(self) -> None:
        self.assertIn(("контрольный", "info"), self.cards["site:google"].chips)
        self.assertIn(("QUIC: работает", "ok"), self.cards["site:google"].chips)
        unknown = self.cards["site:user:my.example"]
        self.assertEqual((unknown.level, unknown.status, unknown.icon), ("unknown", "Не удалось проверить", "fa5s.globe"))
        self.assertEqual(unknown.lines[0].state, "unknown")

    def test_hostings_card_counts_directions_and_groups_dots_by_provider(self) -> None:
        card = self.cards["hostings"]

        self.assertTrue(card.wide)
        self.assertEqual(card.status, "Обрыв у 2 из 4")
        self.assertEqual(
            [(line.name, line.text) for line in card.lines],
            [
                ("Проходит без обрыва", "1"),
                ("Обрыв загрузки на 16–20 КБ", "1"),
                ("Обрыв отправки", "1"),
                ("Сервер не ответил", "1"),
            ],
        )
        self.assertEqual([(group.name, group.states) for group in card.dots], [
            ("AWS", ("fail", "ok")), ("Akamai", ("fail",)), ("OVH", ("unknown",)),
        ])
        self.assertIn("DE.AWS-01 · a.example", card.dots[0].hints[0])
        titles = [section.title for section in card.sections]
        self.assertIn("AWS — обрыв у 1 из 2", titles)
        self.assertIn("1.2 с", card_plain_text(card))

    def test_unknown_server_is_not_counted_as_cut(self) -> None:
        report = {"freeze": {"level": "unknown", "servers": [dict(_servers()[3])]}}
        [card] = build_cards(report)

        self.assertEqual(card.status, "Не удалось проверить")
        self.assertEqual(card.dots[0].states, ("unknown",))

    def test_quick_check_without_server_list_has_no_hostings_card(self) -> None:
        self.assertEqual(build_cards({"freeze": {"level": "ok", "items": [{"name": "AWS"}]}}), [])

    def test_dns_card_names_silent_reference_and_spoofed_hosts(self) -> None:
        card = self.cards["dns"]
        self.assertEqual((card.level, card.status), ("warn", "Эталоны: 1 из 2"))
        self.assertEqual(card.lines[1].text, "сервер молчит")

        spoofed = {c.key: c for c in build_cards({**_REPORT, "spoofed_hosts": ["x.com"]})}["dns"]
        self.assertEqual(spoofed.status, "Подмена адресов: 1")
        self.assertEqual(spoofed.lines[0].text, "x.com")

    def test_ipv6_absent_is_neither_good_nor_bad(self) -> None:
        self.assertEqual((self.cards["ipv6"].level, self.cards["ipv6"].status), ("unknown", "Нет в этой сети"))

    def test_filter_card_marks_the_hop_in_details(self) -> None:
        card = self.cards["filter"]

        self.assertEqual((card.level, card.status), ("warn", "Между узлами 1 и 2"))
        hops = [line.name for line in card.sections[1].lines]
        self.assertEqual(hops, ["Узел 1", f"── {FILTER_MARK} ──", "Узел 2", "Узел 3"])
        self.assertEqual(card.sections[1].lines[0].text, "10.0.0.1 · < 1 мс")
        self.assertEqual(card.sections[1].lines[3].text, "не ответил")

        missing = build_cards({"filter": {"host": "x.com", "found": False, "hop": None, "text": "не найден", "hops": []}})[0]
        self.assertEqual((missing.level, missing.status), ("unknown", "Не найдено"))
        self.assertNotIn(FILTER_MARK, card_plain_text(missing))

    def test_system_card_shows_the_worst_first_and_advice_in_details(self) -> None:
        card = self.cards["system"]

        self.assertEqual((card.level, card.status), ("fail", "Мешает работе: 1"))
        self.assertEqual(card.lines[0].name, "Служба BFE")
        self.assertEqual(card.sections[0].title, "Что делать")
        self.assertEqual(card.sections[0].lines[0].text, "Включите службу")

        fine = build_cards({"system": [dict(_REPORT["system"][0]), dict(_REPORT["system"][3])]})[0]
        self.assertEqual((fine.level, fine.status), ("ok", "В порядке: 1 из 2"))
        unknown = build_cards({"system": [{"title": "Часы", "level": "unknown", "text": "не удалось"}]})[0]
        self.assertEqual(unknown.status, "Не проверено")

    def test_dns_servers_card_keeps_full_text_for_details(self) -> None:
        card = self.cards["dns_servers"]

        self.assertEqual((card.level, card.status), ("fail", "Есть проблемы"))
        self.assertEqual(card.sections[-1].text, "полная таблица серверов")

    def test_counters_show_the_amount_of_work(self) -> None:
        counters = {counter.caption: counter.value for counter in build_counters(_REPORT)}

        self.assertEqual(counters["сайтов"], 4)
        self.assertEqual(counters["адресов сайтов"], 5)
        self.assertEqual(counters["проверок QUIC"], 2)
        self.assertEqual(counters["серверов хостингов"], 4)
        self.assertEqual(counters["узлов по дороге"], 3)
        self.assertEqual(build_counters({}), [])


class NewSectionsCardsTests(unittest.TestCase):
    """Сравнение «с Zapret и без», замедление по имени и время по частям — на карточках."""

    COMPARE = {
        "level": "warn",
        "headline": "Пресет «Default» чинит не всё: помог 1, не помог 1",
        "preset": "Default",
        "zapret_in": "current",
        "other_time": "2026-10-08T12:00:00",
        "helped": ["YouTube"],
        "not_helped": ["Telegram"],
        "fine_anyway": [],
        "broken": [],
        "notes": ["Это итог для выбранного пресета."],
    }

    @staticmethod
    def _card(report: dict, key: str) -> Card:
        return next(card for card in build_cards(report) if card.key == key)

    def test_compare_card_names_groups_preset_and_the_other_run(self) -> None:
        card = self._card({"compare": self.COMPARE}, "compare")
        text = " ".join(f"{line.name} {line.text}" for section in card.sections for line in section.lines)

        self.assertEqual((card.level, card.title), ("warn", "С Zapret и без"))
        self.assertEqual([(line.state, line.text) for line in card.lines], [("ok", "YouTube"), ("fail", "Telegram")])
        for expected in ("Default", "08.10 12:00", "без Zapret", "Это итог для выбранного пресета."):
            self.assertIn(expected, text)
        # Пустые группы не показываются.
        self.assertFalse(any("перестали" in section.title for section in card.sections))

    def test_compare_tile_opens_the_site_page_and_says_it_is_one_preset(self) -> None:
        protocols = [{"key": "tls13", "title": "TLS 1.3", "state": "ok", "word": "проходит", "text": "проходит"}]
        youtube = _service("youtube", "YouTube", "ok", [_target("www.youtube.com", main=True, protocols=protocols, quic="ok", dns_state="ok")])
        card = self._card({"compare": self.COMPARE, "services": [youtube]}, "compare")
        # Сравнение — итог одного пресета: это сказано в слове итога и первой строкой отчёта.
        self.assertIn("пресет «Default»", card.status)
        self.assertIn("Это итог одного пресета — «Default»", card.sections[0].lines[0].name)

        helped = next(section for section in card.sections if section.title == "Обход помог")
        page = helped.lines[0].page
        # Страница сайта: что было с Zapret и без, пресет, и все пробы этой проверки ниже.
        self.assertEqual((page.title, page.status, page.level, page.site), ("YouTube", "Обход помог", "ok", True))
        verdict = page.sections[0]
        rows = {line.name: line.text for line in verdict.lines}
        self.assertEqual(rows["С Zapret (эта проверка)"], "открывается")
        self.assertEqual(rows["Без Zapret (проверка 08.10 12:00)"], "не открывается")
        self.assertEqual(rows["Пресет"], "Default")
        self.assertTrue(any("одного пресета" in line.name for line in verdict.lines))
        self.assertTrue(page.marks)
        self.assertGreater(len(page.sections), 1)
        # Сайта нет среди проверенных в этот раз — остаётся само сравнение.
        alone = next(section for section in card.sections if section.title == "Не открываются и с обходом").lines[0].page
        self.assertEqual((alone.title, len(alone.sections)), ("Telegram", 1))
        self.assertEqual({line.name: line.text for line in alone.sections[0].lines}["С Zapret (эта проверка)"], "не открывается")

        # Прошлая проверка была с Zapret, а эта — без: подписи меняются местами.
        swapped = self._card({"compare": {**self.COMPARE, "zapret_in": "past"}, "services": [youtube]}, "compare")
        rows = {line.name: line.text for line in next(s for s in swapped.sections if s.title == "Обход помог").lines[0].page.sections[0].lines}
        self.assertEqual((rows["Без Zapret (эта проверка)"], rows["С Zapret (проверка 08.10 12:00)"]), ("не открывается", "открывается"))

    def test_filter_card_leads_with_the_places_found_for_each_mechanism(self) -> None:
        place = {
            "state": "disagree", "found": False, "hop": None, "text": "По разным сайтам фильтр оказался на разных узлах.",
            "mechanisms": [
                {"key": "quic", "title": "QUIC по имени сайта", "hop": 2, "text": "между узлами 1 и 2"},
                {"key": "tcp", "title": "TLS по имени сайта", "hop": None, "text": "место не найдено"},
                {"key": "dns", "title": "Перехват обычных DNS-запросов", "hop": 9, "text": "между узлами 8 и 9"},
            ],
        }
        card = self._card({"filter": place}, "filter")
        # Одного места нет, но по способам оно найдено: это и стоит в слове итога и первыми строками.
        self.assertEqual((card.status, card.level), ("По способам: узлы 2, 9", "warn"))
        self.assertEqual([line.name for line in card.lines[:2]], ["QUIC по имени сайта", "Перехват обычных DNS-запросов"])
        # Ничего не нашли вовсе — остаётся прежнее слово.
        nothing = self._card({"filter": {**place, "mechanisms": []}}, "filter")
        self.assertEqual(nothing.status, "Узлы не совпали")

    def test_site_status_names_a_cause_that_is_not_the_provider(self) -> None:
        def card(**extra):
            [built] = [item for item in build_cards({"services": [_service("chatgpt", "ChatGPT", "warn", [_target("chatgpt.com", main=True, **extra)])]}) if item.site]
            return built

        # Запись в hosts ведёт на нерабочий адрес: сайт жив, мешает сама запись.
        self.assertEqual(card(hosts_stale=True).status, "Мешает запись в hosts")
        # Вместо сайта отвечает кто-то другой — названо кто.
        self.assertEqual(card(cert={"code": "antivirus", "text": "x"}).status, "Сертификат подменяет антивирус")
        self.assertEqual(card(cert={"code": "что-то новое", "text": "x"}).status, "Чужой сертификат")
        # Чужой сертификат важнее записи в hosts; без обеих причин слово итога прежнее.
        self.assertEqual(card(hosts_stale=True, cert={"code": "other_site"}).status, "Отвечает другой сайт")
        self.assertNotIn("hosts", card().status)

    def test_site_that_opens_every_other_time_is_a_third_outcome(self) -> None:
        from blockcheck.ui.block_kinds_view import kind_color, site_groups
        from blockcheck.ui.check_results import BlockcheckSummaryPanel
        from blockcheck.ui.site_road import site_road
        from diagnostics.block_kind import KIND_ORDER, KIND_UNSTABLE, kind_info

        protocols = [{"key": "tls13", "title": "TLS 1.3", "state": "ok", "word": "проходит", "text": "проходит"}]
        shaky = _service("signal", "Signal", "warn", [_target("signal.org", main=True, protocols=protocols, unstable="открылся со второй попытки, а при повторе снова сбой", rechecked="unstable")])
        steady = _service("github", "GitHub", "ok", [_target("github.com", main=True)])
        report = {"services": [shaky, steady], "problems": [{"level": "warn", "kind": "", "title": "Signal", "text": "Signal открывается через раз", "target": "signal.org", "advice": [], "evidence": [], "action": "strategy"}], "working": ["GitHub"]}

        [card] = [item for item in build_cards(report) if item.key == "site:signal"]
        # На карточке — своё слово и свой вид, а не общее «Есть проблемы».
        self.assertEqual((card.kind, card.status, card.level), (KIND_UNSTABLE, "Через раз", "warn"))
        self.assertIn(KIND_UNSTABLE, KIND_ORDER)
        self.assertIn("через раз", kind_info(KIND_UNSTABLE).title.lower())
        # В полосе итога — своя доля между «открываются» и блокировками, своим цветом.
        groups = site_groups(report)
        self.assertEqual([(group.key, group.names) for group in groups], [("open", ("GitHub",)), (KIND_UNSTABLE, ("Signal",))])
        self.assertNotEqual(kind_color(KIND_UNSTABLE), kind_color("open"))
        self.assertNotEqual(kind_color(KIND_UNSTABLE), kind_color("sni"))
        # Дорога до сайта не оборвана: крестика нет, у сайта — «через раз».
        stages, broken = site_road(card)
        self.assertEqual((broken, stages[-1].word, stages[-1].state), (-1, "через раз", "warn"))
        # В списке проблем — своя группа, а не «Остальное».
        panel = BlockcheckSummaryPanel(on_action=lambda *_args: None, on_open=lambda _key: None)
        self.addCleanup(panel.deleteLater)
        panel.show_report(report)
        [group] = panel.problem_groups()
        self.assertEqual((group.kind(), group.title_label.text()), (KIND_UNSTABLE, "Открываются через раз"))
        # Чужой сертификат и запись в hosts важнее: их слово итога остаётся.
        both = _service("x", "X", "warn", [_target("x.com", main=True, unstable="через раз", hosts_stale=True)])
        [other] = [item for item in build_cards({"services": [both]}) if item.site]
        self.assertEqual(other.status, "Мешает запись в hosts")

    def test_mark_explains_itself_on_hover_and_its_word_fits(self) -> None:
        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QMouseEvent

        from blockcheck.ui.result_cards import SITE_CARD_MIN_WIDTH, ResultCard

        def proto(title: str, state: str, word: str, hint: str, text: str) -> dict:
            return {"key": title, "title": title, "state": state, "word": word, "text": text, "hint": hint}

        protocols = [
            # Браузерное приветствие проходит — обычные «молчат», и это не беда: состояние нейтральное.
            proto("TLS 1.2", "info", "не мешает", "TLS 1.2 — старый вид защищённого соединения.", "Молчит, но исправлять ничего не нужно."),
            proto("TLS 1.3", "info", "не мешает", "TLS 1.3 — современный вид защищённого соединения.", "Молчит, но исправлять ничего не нужно."),
            proto("Как Chrome", "ok", "проходит", "Так здоровается браузер Chrome.", "проходит"),
            proto("HTTP", "info", "переход", "HTTP — соединение без защиты.", "переход на HTTPS"),
        ]
        service = _service("youtube", "YouTube", "ok", [_target("www.youtube.com", main=True, protocols=protocols, quic="ok", dns_state="ok")])
        [card] = [item for item in build_cards({"services": [service]}) if item.site]
        tls, _tls13, chrome, _http, quic, dns = card.marks
        # Подсказка: что это за дорога простыми словами и чем кончилась проба; у QUIC и DNS текст свой.
        self.assertEqual(tls.state, "info")
        self.assertIn("старый вид защищённого соединения", tls.hint)
        self.assertIn("исправлять ничего не нужно", tls.hint)
        self.assertIn("быстрый способ соединения", quic.hint)
        self.assertIn("справочная", dns.hint)

        widget = ResultCard(card)
        self.addCleanup(widget.deleteLater)
        widget.resize(SITE_CARD_MIN_WIDTH, widget.height_for(SITE_CARD_MIN_WIDTH))
        widget.show()
        # В самой узкой карточке сетки слово у «Chrome» помещается целиком.
        cell = widget.mark_rect(2)
        need = 18 + widget._text_metrics.horizontalAdvance(chrome.label) + 2 + 6 + widget._text_metrics.horizontalAdvance(chrome.word)
        self.assertLessEqual(need, cell.width())
        # Наведение на дорогу показывает её подсказку; над остальной карточкой её нет.
        center = cell.center()
        self.assertEqual(widget.mark_at(center.x(), center.y()), 2)
        self.assertEqual(widget.mark_at(5, 5), -1)
        self.assertEqual(widget.mark_hint(2), "Chrome: проходит\nТак здоровается браузер Chrome.\nпроходит")
        self.assertEqual(widget.mark_hint(-1), "")
        move = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(center), QPointF(widget.mapToGlobal(center.toPoint())), Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        widget.mouseMoveEvent(move)
        self.assertEqual(widget._mark_hint.text(), widget.mark_hint(2))
        widget.grab()

    def test_site_card_words_come_from_the_report_and_nothing_is_derived(self) -> None:
        """Сторож: у свежего отчёта слово итога, дороги и метки карточки — ровно те, что в отчёте."""
        from blockcheck.ui import result_cards_model as model

        roads = [
            {"key": "tls12", "label": "TLS 1.2", "state": "info", "word": "не мешает", "text": "молчит, исправлять не нужно", "hint": "старый вид защищённого соединения"},
            {"key": "tls13", "label": "TLS 1.3", "state": "fail", "word": "сброс", "text": "соединение сброшено", "hint": "современный вид"},
            {"key": "browser", "label": "Chrome", "state": "ok", "word": "проходит", "text": "проходит", "hint": "так здоровается браузер"},
            {"key": "http", "label": "HTTP", "state": "info", "word": "переход", "text": "переход на HTTPS", "hint": "без защиты"},
            {"key": "quic", "label": "QUIC", "state": "warn", "word": "закрыт", "text": "QUIC режут", "hint": "быстрый способ"},
            {"key": "dns", "label": "DNS", "state": "unknown", "word": "—", "text": "сверить не удалось", "hint": "справочная"},
        ]
        tags = [
            {"key": "cause", "text": "причина из отчёта", "state": "fail"},
            {"key": "registry", "text": "в реестре РКН", "state": "info"},
            {"key": "cut16", "text": "обрыв на 16 КБ", "state": "warn"},
        ]
        # Данные адреса нарочно говорят другое: если бы экран выводил слова сам, они бы разошлись с отчётом.
        target = _target("example.org", main=True, ok=True, quic="ok", dns_state="spoofed", hosts_stale=True, unstable="через раз", volume="", cert={"code": "antivirus"}, cause="by_name")
        service = {
            **_service("example", "Example", "warn", [target], kind="fingerprint"),
            "status": "Слово из отчёта",
            "roads": roads,
            "tags": tags,
            "dns_note": "подмена",
            "control": True,
        }
        with patch.object(model, "_legacy_site_words", side_effect=AssertionError("у свежего отчёта запасной путь не нужен")), patch.object(
            model, "_legacy_site_level", side_effect=AssertionError("уровень берётся из отчёта")
        ), patch.object(model, "_legacy_site_kind", side_effect=AssertionError("вид берётся из отчёта")):
            [card] = [item for item in build_cards({"services": [service]}) if item.site]

        self.assertEqual((card.status, card.level, card.kind), ("Слово из отчёта", "warn", "fingerprint"))
        self.assertEqual([(mark.label, mark.word, mark.state) for mark in card.marks], [(road["label"], road["word"], road["state"]) for road in roads])
        # Причина — из отчёта: она идёт в полный набор меток, а на самой карточке не повторяет слово итога.
        self.assertEqual(card.tags, tuple((tag["text"], tag["state"]) for tag in tags if tag["key"] != "cause"))
        self.assertIn(("причина из отчёта", "fail"), card.chips)
        # Своего слова причины экран не подставляет, хотя у адреса стоит код блокировки по имени.
        self.assertNotIn("блокировка по имени", [text for text, _state in card.chips])
        # Подсказка дороги — пояснение и фраза из отчёта, без добавок.
        self.assertEqual(card.marks[4].hint, "быстрый способ\nQUIC режут")
        # Значок — по ключу дороги: это вид, он остаётся на экране.
        self.assertEqual([mark.icon for mark in card.marks], ["fa5s.lock", "fa5s.lock", "fa5b.chrome", "fa5s.unlock-alt", "fa5s.bolt", "fa5s.exchange-alt"])
        # Уровень и вид — как в отчёте, даже когда данные адресов намекают на другое.
        self.assertEqual(model.site_level({**service, "level": "ok"}), "ok")
        self.assertEqual(model.site_kind({**service, "kind": ""}, "warn"), "")

        # Отчёт, сохранённый до этой правки, слов не содержит — работает подписанный запасной путь.
        old = _service("example", "Example", "warn", [_target("example.org", main=True, hosts_stale=True)])
        self.assertFalse(model.has_ready_words(old))
        [legacy] = [item for item in build_cards({"services": [old]}) if item.site]
        self.assertEqual(legacy.status, "Мешает запись в hosts")

    def test_crowd_card_shows_the_report_words_and_says_the_check_caused_the_stop(self) -> None:
        crowd = {
            "state": "freeze", "level": "warn", "host": "www.instagram.com", "address": "163.70.151.174", "label": "Instagram",
            "text": "Instagram замирает от нескольких соединений сразу.",
            "advice": "Остановку вызвала сама проверка: так фильтр наказывает за пачку соединений.",
            "before": "ok", "crowd": ["ok", "timeout"], "after": "timeout", "control": "ok", "recovered_s": 42.0, "waited_s": 60.0,
        }
        card = self._card({"crowd": crowd}, "crowd")
        self.assertEqual((card.title, card.level), ("Несколько соединений сразу", "warn"))
        # Вывод и оговорка «остановку вызвала сама проверка» — прямо на карточке, словами отчёта.
        self.assertEqual([line.name for line in card.lines], [crowd["text"], crowd["advice"]])
        facts = {line.name: line.text for line in card.sections[-1].lines}
        self.assertEqual((facts["Сайт"], facts["Адрес сервера"], facts["Сайт вернулся через"]), ("Instagram · www.instagram.com", "163.70.151.174", "42.0 с"))
        # Коды исходов соединений экран в слова не переводит: строки появятся, когда их даст отчёт.
        self.assertNotIn("timeout", " ".join(f"{line.name} {line.text}" for section in card.sections for line in section.lines))
        with_rows = self._card({"crowd": {**crowd, "status": "Замирает", "rows": [{"title": "Соединение 2 из пачки", "text": "без ответа", "state": "fail"}]}}, "crowd")
        self.assertEqual(with_rows.status, "Замирает")
        self.assertIn(("Соединение 2 из пачки", "без ответа", "fail"), [(line.name, line.text, line.state) for line in with_rows.sections[-1].lines])
        # Готовые строки отчёта уже говорят, когда сайт вернулся: своих чисел экран рядом не дописывает.
        self.assertNotIn("Сайт вернулся через", [line.name for line in with_rows.sections[-1].lines])
        # Проба не делалась — карточки нет.
        self.assertFalse([item for item in build_cards({"crowd": None}) if item.key == "crowd"])

    def test_no_comparison_no_card(self) -> None:
        self.assertFalse([card for card in build_cards({"compare": None}) if card.key == "compare"])

    def test_speed_card_shows_throttling_by_name(self) -> None:
        speed = {
            "level": "ok",
            "headline": "Заметной разницы нет",
            "items": [{"name": "OVH", "state": "ok", "text": "40 Мбит/с"}],
            "names": {
                "headline": "Замедляют по имени сайта: YouTube",
                "server": "mirror.yandex.ru",
                "control": "40 Мбит/с",
                "items": [
                    {"name": "YouTube", "host": "www.youtube.com", "state": "warn", "text": "0.2 Мбит/с — замедляют"},
                    {"name": "GitHub", "host": "github.com", "state": "ok", "text": "38 Мбит/с"},
                ],
            },
        }
        card = self._card({"speed": speed}, "speed")
        rows = [(line.state, line.name) for line in card.sections[0].lines]

        self.assertEqual((card.level, card.status), ("warn", "Замедляют: YouTube"))
        self.assertIn(("warn", "YouTube (www.youtube.com)"), rows)
        self.assertIn(("ok", "GitHub (github.com)"), rows)
        self.assertIn("mirror.yandex.ru", rows[0][1])

        calm = dict(speed, names=dict(speed["names"], items=[speed["names"]["items"][1]]))
        calm_card = self._card({"speed": calm}, "speed")
        self.assertEqual((calm_card.level, calm_card.status), ("ok", "Разницы нет"))
        self.assertTrue(any("GitHub" in line.name for section in calm_card.sections for line in section.lines))

    def test_site_row_tells_where_the_time_went(self) -> None:
        from diagnostics.report_text import stages_text

        self.assertEqual(stages_text({"dns": 0.4, "reach": 6.2, "cause": 3.0, "roads": 1.0}), "соединение 6 с, причина 3 с, дороги 1 с")
        self.assertEqual(stages_text({"dns": 0.2}), "")
        self.assertEqual(stages_text(None), "")


class CardsWidgetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_grid_uses_more_columns_on_a_wide_window_and_wide_card_takes_a_row(self) -> None:
        grid = CardsGrid(250)
        self.addCleanup(grid.deleteLater)
        self.assertEqual([grid.columns_for(width) for width in (200, 520, 1040, 1600)], [1, 2, 4, 6])

        grid.show()
        grid.resize(1040, 10)
        grid.show_cards(build_cards(_REPORT), animate=False)
        widgets = {widget.card.key: widget for widget in grid.cards()}
        sites = [widgets[key] for key in ("site:x", "site:youtube", "site:google", "site:user:my.example")]
        # Четыре сайта встали в один ряд, каждый в свою колонку.
        self.assertEqual(len({widget.x() for widget in sites}), 4)
        self.assertEqual({widget.y() for widget in sites}, {0})
        hostings = widgets["hostings"]
        self.assertEqual((hostings.x(), hostings.width()), (0, 1040))
        self.assertGreaterEqual(hostings.y(), max(widget.geometry().bottom() for widget in sites))
        # Высота сетки — по нижней карточке: вложенная прокрутка её сама не узнает.
        self.assertEqual(grid.height(), max(widget.geometry().bottom() for widget in grid.cards()) + 1)

        grid.resize(300, 10)
        self.assertEqual({widget.x() for widget in grid.cards()}, {0})

    def test_cards_stand_in_even_rows_of_equal_height(self) -> None:
        grid = CardsGrid(250)
        self.addCleanup(grid.deleteLater)
        grid.resize(800, 10)
        grid.show()
        tall = tuple(Line("ok", f"строка {n}", "да") for n in range(4))
        cards = [Card(f"site:{n}", "fa5s.globe", f"Сайт {n}", "ok", "Открывается", lines=tall if n % 2 else tall[:1]) for n in range(5)]
        grid.show_cards(cards, animate=False)
        widgets = grid.cards()
        self.app.processEvents()

        # Три столбца: первые три карточки — один ряд, оставшиеся две — второй.
        self.assertEqual([widget.y() for widget in widgets[:3]], [0, 0, 0])
        self.assertEqual(len({widget.y() for widget in widgets[3:]}), 1)
        # В ряду все одной высоты — по самой высокой, хотя строк на карточках разное число.
        self.assertEqual(len({widget.height() for widget in widgets[:3]}), 1)
        self.assertEqual(widgets[0].height(), widgets[1].height_for(widgets[1].width()))
        self.assertEqual(widgets[3].y(), widgets[0].height() + 10)
        self.assertEqual(grid.height(), widgets[4].geometry().bottom() + 1)

    def test_site_card_shows_its_roads_in_fixed_places_instead_of_a_pile_of_chips(self) -> None:
        def proto(title: str, state: str, word: str) -> dict:
            return {"key": title, "title": title, "state": state, "word": word, "text": word}

        def site(key: str, quic: str, **extra) -> dict:
            protocols = [proto("TLS 1.2", "fail", "сброс"), proto("TLS 1.3", "fail", "нет соединения"), proto("Как Chrome", "ok", "проходит"), proto("HTTP", "info", "переход")]
            main = _target(f"www.{key}.com", ok=False, main=True, protocols=protocols, quic=quic, dns_state="ok", cause="by_name", registry={"listed": True})
            return _service(key, key.title(), "fail", [main], **extra)

        report = {"services": [site("instagram", "ok"), site("linkedin", "blocked_by_name", dns_note="подмена")]}
        first, second = [card for card in build_cards(report) if card.site]
        # Дороги у каждого сайта — в одном порядке: по ним глаз сравнивает сайты между собой.
        self.assertEqual([mark.label for mark in first.marks], ["TLS 1.2", "TLS 1.3", "Chrome", "HTTP", "QUIC", "DNS"])
        self.assertEqual([mark.label for mark in second.marks], [mark.label for mark in first.marks])
        self.assertEqual([(mark.word, mark.state) for mark in first.marks], [("сброс", "fail"), ("нет связи", "fail"), ("проходит", "ok"), ("переход", "info"), ("работает", "ok"), ("честный", "ok")])
        self.assertEqual([(mark.word, mark.state) for mark in second.marks[4:]], [("закрыт", "warn"), ("подменён", "warn")])
        # Метками остаётся только то, чего в дорогах нет; полный набор меток — для отчёта и подсказки.
        self.assertEqual(first.tags, (("в реестре РКН", "info"),))
        self.assertIn(("TLS 1.2: сброс", "fail"), first.chips)
        self.assertIn(("блокировка по имени", "fail"), first.chips)
        # Сайт без проверки по протоколам остаётся с метками, как раньше.
        [plain] = [card for card in build_cards({"services": [_service("x", "X", "fail", [_target("x.com", ok=False)])]}) if card.site]
        self.assertEqual(plain.marks, ())

        grid = CardsGrid(250)
        self.addCleanup(grid.deleteLater)
        grid.resize(640, 10)
        grid.show()
        grid.show_cards([first, second], animate=False)
        one, two = grid.cards()
        self.app.processEvents()
        # Одна и та же дорога стоит на одном месте у обеих карточек.
        for index in range(6):
            self.assertEqual((one.mark_rect(index).top(), one.mark_rect(index).left()), (two.mark_rect(index).top(), two.mark_rect(index).left()))
        self.assertEqual(one.mark_rect(0).top(), one.mark_rect(1).top())
        self.assertLess(one.mark_rect(1).top(), one.mark_rect(2).top())
        self.assertEqual(one.height(), two.height())
        one.grab()

    def test_cards_appear_as_the_check_goes_and_shown_ones_are_not_rebuilt(self) -> None:
        view = ResultCardsView()
        self.addCleanup(view.deleteLater)
        view.resize(900, 600)
        view.show()
        first = _service("discord", "Discord", "ok", [_target("discord.com")])
        second = _service("youtube", "YouTube", "fail", [_target("www.youtube.com", ok=False)])
        empty = {"partial": True, "services": [], "freeze": None, "voice": None, "system": None}
        view.show_partial(empty)
        self.assertFalse(view.has_cards())

        view.show_partial({**empty, "services": [first]})
        [discord] = view.sites_grid.cards()
        self.assertTrue(view.has_cards())
        # Счётчики «что проверено» ждут итога.
        self.assertEqual(view.counters.tiles(), [])

        view.show_partial({**empty, "services": [first, second]})
        # Пока проверка идёт, показанная карточка стоит на месте (и это тот же виджет), новая — следом,
        # хотя сайт с проблемой в итоге встанет первым.
        kept, youtube = view.sites_grid.cards()
        self.assertIs(kept, discord)
        self.assertEqual(youtube.card.title, "YouTube")
        self.assertGreater(youtube.x(), kept.x())

        # Сайт перепроверили, и он открылся: его карточка заменилась, соседняя не тронута.
        fixed = _service("youtube", "YouTube", "ok", [_target("www.youtube.com")])
        view.show_partial({**empty, "services": [first, fixed]})
        by_key = {widget.card.key: widget for widget in view.sites_grid.cards()}
        self.assertIs(by_key["site:discord"], discord)
        self.assertIsNot(by_key["site:youtube"], youtube)
        self.assertEqual(by_key["site:youtube"].card.level, "ok")

        # Итог: карточки сайтов остаются те же, добавляются разделы и счётчики.
        final = {**_REPORT, "services": [first, fixed], "partial": False}
        view.show_report(final, animate=False)
        after = {widget.card.key: widget for widget in view.sites_grid.cards()}
        self.assertIs(after["site:discord"], discord)
        self.assertIs(after["site:youtube"], by_key["site:youtube"])
        self.assertTrue(view.checks_grid.cards())
        self.assertTrue(view.counters.tiles())
        # Порядок «проблемные первыми» наводит итог.
        view.show_report({**final, "services": [first, second]}, animate=False)
        self.assertEqual([widget.card.title for widget in view.sites_grid.cards()], ["YouTube", "Discord"])
        # Новая проверка начинает с чистого места.
        view.clear()
        self.assertFalse(view.has_cards())
        self.assertEqual(view.sites_grid.cards(), [])

    def test_site_road_shows_where_the_connection_is_cut(self) -> None:
        from blockcheck.ui.site_road import SiteRoad, site_road

        def proto(title: str, state: str, word: str) -> dict:
            return {"key": title, "title": title, "state": state, "word": word, "text": word}

        def card(key: str, level: str, kind: str, words: tuple[str, str, str, str], **extra):
            states = ["ok" if word == "проходит" else "fail" for word in words]
            protocols = [proto(title, state, word) for title, state, word in zip(("TLS 1.2", "TLS 1.3", "Как Chrome", "HTTP"), states, words)]
            target = _target(f"{key}.com", ok=level == "ok", main=True, protocols=protocols, quic="ok", dns_state="ok", **extra)
            [built] = [item for item in build_cards({"services": [_service(key, key.title(), level, [target], kind=kind)]}) if item.site]
            return built

        by_name = card("instagram", "fail", "sni", ("сброс", "сброс", "проходит", "проходит"))
        stages, broken = site_road(by_name)
        self.assertEqual([stage.title for stage in stages], ["Вы", "DNS", "Сервер", "TLS", "Сайт"])
        # Режут на TLS: до него всё зелёное, сам он красный, до сайта не дошли.
        self.assertEqual(broken, 3)
        self.assertEqual([stage.state for stage in stages], ["ok", "ok", "ok", "fail", "unknown"])
        self.assertEqual((stages[3].word, stages[4].word), ("сброс", "не дошли"))

        by_address = card("telegram", "fail", "ip", ("нет соединения",) * 4)
        stages, broken = site_road(by_address)
        self.assertEqual((broken, stages[2].word, stages[3].word), (2, "адрес закрыт", "не дошли"))

        opens = card("youtube", "ok", "", ("проходит",) * 4)
        stages, broken = site_road(opens)
        self.assertEqual((broken, [stage.state for stage in stages]), (-1, ["ok"] * 5))

        view = ResultDetailView()
        self.addCleanup(view.deleteLater)
        view.resize(1000, 600)
        view.show()
        view.show_card(by_name)
        self.assertIsInstance(view.road, SiteRoad)
        # Точка доезжает до места обрыва, а не до сайта.
        self.assertEqual(view.road.last_reached(), 3)
        self.assertLess(view.road.node_center(0).x(), view.road.node_center(4).x())
        view.road.grab()
        # У отчёта не про сайт дороги нет.
        view.show_card(Card("hostings", "fa5s.server", "Хостинги", "ok", "В порядке"))
        self.assertIsNone(view.road)

    def test_card_shows_few_lines_and_says_how_many_are_hidden(self) -> None:
        view = ResultCardsView()
        self.addCleanup(view.deleteLater)
        many = {"system": [{"title": f"Пункт {n}", "level": "ok", "text": "да"} for n in range(PREVIEW_LINES + 3)]}
        view.show_report(many, animate=False)

        card = view.card("system")
        self.assertEqual(len(card.shown_lines()), PREVIEW_LINES)
        self.assertIn("и ещё 3", card.more_text())
        # Карточку рисует один виджет: надписей-виджетов на каждую строку в ней нет.
        self.assertEqual(card.findChildren(QWidget), [])
        self.assertEqual(card.height_for(330), card.heightForWidth(330))
        self.assertGreater(card.height_for(330), PREVIEW_LINES * card.LINE)
        card.resize(330, card.height_for(330))
        card.grab()
        # Тот же итог повторно (вернулись на страницу) карточки не пересобирает.
        view.show_report(many, animate=False)
        self.assertIs(view.card("system"), card)
        self.assertEqual(card.accessibleName(), f"Этот компьютер: В порядке: {PREVIEW_LINES + 3} из {PREVIEW_LINES + 3}")

    def test_click_and_enter_open_the_card(self) -> None:
        view = ResultCardsView()
        self.addCleanup(view.deleteLater)
        view.resize(900, 600)
        view.show()
        view.show_report(_REPORT, animate=False)
        opened = []
        view.opened.connect(opened.append)

        QTest.mouseClick(view.card("voice"), Qt.MouseButton.LeftButton)
        QTest.keyClick(view.card("site:x"), Qt.Key.Key_Return)

        self.assertEqual([card.key for card in opened], ["voice", "site:x"])

    def test_view_separates_sites_from_other_checks_and_clears(self) -> None:
        view = ResultCardsView()
        self.addCleanup(view.deleteLater)
        view.show_report(_REPORT, animate=False)

        self.assertEqual(len(view.sites_grid.cards()), 4)
        self.assertEqual(len(view.checks_grid.cards()), 7)
        self.assertEqual(view.accessibleName(), "Результаты BlockCheck: карточек 11, с проблемами 7")
        self.assertEqual(len(view.counters.tiles()), len(build_counters(_REPORT)))

        view.clear()
        self.assertEqual(view.cards(), [])
        self.assertEqual(view.counters.tiles(), [])

    def test_hosting_dots_wrap_and_give_a_hint_per_server(self) -> None:
        card = {c.key: c for c in build_cards(_REPORT)}["hostings"]
        dots = HostingDots(card.dots)
        self.addCleanup(dots.deleteLater)

        self.assertGreater(dots.heightForWidth(90), dots.heightForWidth(900))
        dots.resize(900, dots.heightForWidth(900))
        dots.grab()
        hints = [hint for _rect, hint in dots._hits]
        self.assertEqual(len(hints), 4)
        rect = dots._hits[0][0]
        self.assertEqual(dots.hint_at(rect.center().x(), rect.center().y()), hints[0])
        self.assertEqual(dots.hint_at(-5, -5), "")
        self.assertEqual(dots.accessibleName(), "Серверов хостингов: 4, с обрывом: 2")

    def test_progress_steps_count_and_hide_steps_of_the_full_check(self) -> None:
        steps = ProgressSteps()
        self.addCleanup(steps.deleteLater)
        steps.start(("sites", "hostings", "voice"))

        self.assertTrue(steps.rows["filter"].isHidden())
        self.assertFalse(steps.rows["hostings"].isHidden())
        self.assertEqual(steps.rows["hostings"].count_label.text(), "ждёт")
        steps.set_progress("hostings", 23, 59)
        steps.set_progress("voice", 1, 1)
        steps.set_progress("нет такого", 1, 1)
        self.assertEqual(steps.rows["hostings"].count_label.text(), "23 / 59")
        self.assertEqual(steps.rows["hostings"].bar.value(), 23)
        self.assertEqual(steps.rows["voice"].count_label.text(), "готово")
        self.assertEqual(steps.accessibleName(), "Ход BlockCheck: готово шагов 1 из 3")
        steps.start(("sites",))
        self.assertEqual(steps.rows["voice"].count_label.text(), "ждёт")

    def test_progress_rows_show_how_long_each_step_took(self) -> None:
        # Секунды приходят из отчёта проверки (step_seconds), а не считаются экраном.
        steps = ProgressSteps()
        self.addCleanup(steps.deleteLater)
        steps.start(("sites", "hostings", "voice"))
        steps.set_progress("sites", 45, 45)
        steps.set_progress("hostings", 3, 57)
        steps.set_seconds({"sites": 23.4, "hostings": 5.0, "voice": 9.0, "нет такого": 1.0})

        self.assertEqual(steps.rows["sites"].count_label.text(), "45 / 45 · 23 с")
        self.assertEqual(steps.rows["hostings"].count_label.text(), "3 / 57 · 5 с")
        # Шаг ещё не начинался: секунд у него на экране нет.
        self.assertEqual(steps.rows["voice"].count_label.text(), "ждёт")
        steps.set_progress("voice", 1, 1)
        self.assertEqual(steps.rows["voice"].count_label.text(), "готово · 9 с")
        steps.start(("sites",))
        steps.set_progress("sites", 0, 45)
        self.assertEqual(steps.rows["sites"].count_label.text(), "0 / 45")

    def test_run_card_shows_step_times_conditions_and_the_registry_list(self) -> None:
        report = {
            "step_seconds": {"sites": 95.0, "hostings": 30.2},
            "environment": ["🖥️ Windows 10"],
            "zapret_line": "✅ Zapret запущен",
            "other_bypass_tools": ["sing-box"],
            "registry": {"state": "fresh", "updated": 1791454670.0, "hosts": 1709109, "networks": 69858},
        }
        [card] = build_cards(report)

        self.assertEqual((card.key, card.title, card.level), ("run", "Ход проверки", "warn"))
        self.assertIn("сайты, 95 с", card.status)
        self.assertEqual([section.title for section in card.sections], ["Время по шагам", "Условия проверки", "Список реестра РКН"])
        self.assertEqual([(line.name, line.text) for line in card.sections[0].lines], [("Сайты", "95 с"), ("Хостинги", "30 с")])
        self.assertIn("sing-box", card.sections[1].lines[-1].text)
        self.assertEqual(card.sections[2].lines[1].text, "1 709 109")
        self.assertEqual(build_cards({"partial": True}), [])

    def test_detail_view_shows_sections_and_closes_by_breadcrumb_and_escape(self) -> None:
        detail = ResultDetailView()
        self.addCleanup(detail.deleteLater)
        card = {c.key: c for c in build_cards(_REPORT)}["hostings"]
        closed = []
        detail.closed.connect(lambda: closed.append(True))

        detail.show_card(card)
        self.assertEqual(detail.title_label.text(), "Зарубежные хостинги")
        self.assertEqual(len(detail.blocks), len(card.sections))
        self.assertEqual(detail.breadcrumb.count(), 2)
        detail._on_breadcrumb(detail.CARD_KEY)
        self.assertEqual(closed, [])
        detail._on_breadcrumb(detail.ROOT_KEY)
        QTest.keyClick(detail, Qt.Key.Key_Escape)
        self.assertEqual(closed, [True, True])

        detail.copy_button.click()
        self.assertEqual(QApplication.clipboard().text(), card_plain_text(card))
        # Повторный показ не копит разделы прошлой карточки.
        detail.show_card({c.key: c for c in build_cards(_REPORT)}["ipv6"])
        self.assertEqual(len(detail.blocks), 1)


class _FeatureStub:
    pass


class PageCardsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _page(self) -> BlockcheckPage:
        with patch.object(BlockcheckPage, "_request_page_initial_state_load", lambda self: None):
            page = BlockcheckPage(
                blockcheck_feature=_FeatureStub(),
                dns_feature=_FeatureStub(),
                create_strategy_scan_worker=lambda *_args, **_kwargs: None,
            )
        self.addCleanup(page.deleteLater)
        return page

    def test_full_check_is_first_and_selected(self) -> None:
        page = self._page()

        self.assertEqual(page._scope_combo.currentIndex(), 0)
        self.assertEqual(page._current_scope(), SCOPE_FULL)
        self.assertEqual(page._scope_combo.itemText(0), "Полная проверка (около минуты)")
        self.assertEqual([page._scope_combo.itemData(index) for index in range(3)], ["full", "all", "main"])
        # Смена языка расставляет названия в том же порядке.
        page.set_ui_language("ru")
        self.assertEqual(page._scope_combo.itemText(0), "Полная проверка (около минуты)")
        self.assertEqual(page._scope_combo.itemText(2), "Только Discord и YouTube (быстро)")

    def test_card_opens_details_over_the_page_and_closing_brings_it_back(self) -> None:
        page = self._page()
        page._report_lines = ["x"]
        page._on_finished({**_REPORT, "problems": [], "working": []})
        self.assertTrue(page._progress_card.isHidden())

        page.resize(900, 420)
        page.show()
        QApplication.processEvents()
        bar = page.verticalScrollBar()
        bar.setValue(min(180, bar.maximum()))
        was_at = bar.value()

        page._open_card_detail(page._result_cards.card("hostings").card)
        self.assertFalse(page._detail_view.isHidden())
        self.assertTrue(page._tabs_pivot.isHidden())
        self.assertTrue(all(widget.isHidden() for widget in page._tab_widgets))
        # На подстранице первой идёт строка пути: название и описание раздела скрыты.
        self.assertTrue(page.title_label.isHidden())
        self.assertTrue(page.subtitle_label.isHidden())
        self.assertEqual(bar.value(), 0)

        page._detail_view.closed.emit()
        QApplication.processEvents()
        self.assertTrue(page._detail_view.isHidden())
        self.assertFalse(page._tabs_pivot.isHidden())
        self.assertFalse(page.title_label.isHidden())
        self.assertFalse(page.subtitle_label.isHidden())
        # Возврат — к тому месту списка, с которого уходили.
        self.assertGreater(was_at, 0)
        self.assertEqual(bar.value(), was_at)

        # Длинный текст из отчёта карточки открывается страницей-редактором.
        page._open_card_by_key("dns_servers")
        page._detail_view.text_opened.emit("Все серверы", "строка 1\nстрока 2")
        self.assertFalse(page._log_report_view.isHidden())
        self.assertTrue(page._detail_view.isHidden())
        self.assertEqual(page._log_report_view.report().title, "Все серверы")
        page._escape_shortcut.activated.emit()
        self.assertTrue(page._log_report_view.isHidden())

        # Строка итога открывает ту же страницу по ключу карточки, а Esc закрывает её с любого места.
        page._open_card_by_key("hostings")
        self.assertFalse(page._detail_view.isHidden())
        page._open_card_by_key("нет такой")
        self.assertEqual(page._escape_shortcut.context(), Qt.ShortcutContext.WidgetWithChildrenShortcut)
        page._escape_shortcut.activated.emit()
        self.assertTrue(page._detail_view.isHidden())
        self.assertFalse(page._tabs_pivot.isHidden())
        self.assertFalse(page._results_card.isHidden())
        self.assertTrue(page._progress_card.isHidden())

    def test_window_history_gets_tabs_details_and_reports_as_separate_screens(self) -> None:
        page = self._page()
        page._report_lines = ["x"]
        page._on_finished({**_REPORT, "problems": [], "working": []})
        page.show()
        QApplication.processEvents()
        seen = []
        page.navigation_screen_changed.connect(lambda: seen.append(page.navigation_screen().key))

        self.assertEqual(page.navigation_screen().key, "tab:blockcheck")
        self.assertEqual(page.navigation_screen().title, "")

        page._open_card_by_key("hostings")
        card_screen = page.navigation_screen()
        self.assertEqual(card_screen.key, "card:hostings")
        self.assertEqual(card_screen.title, page._result_cards.card("hostings").card.title)

        page._open_report()
        report_screen = page.navigation_screen()
        self.assertEqual(report_screen.key, "report:Подробный отчёт BlockCheck")

        page._escape_shortcut.activated.emit()
        self.assertEqual(page.navigation_screen().key, "tab:blockcheck")
        page.switch_to_tab(page.TAB_DNS_SPOOFING)
        tab_screen = page.navigation_screen()
        self.assertEqual(tab_screen.key, "tab:dns_spoofing")
        self.assertEqual(tab_screen.title, "DNS подмена")
        self.assertEqual(seen[-1], "tab:dns_spoofing")
        self.assertIn("card:hostings", seen)
        self.assertIn(report_screen.key, seen)

        # «Назад» окна: журнал просит открыть записанный экран заново.
        self.assertTrue(page.restore_navigation_screen(card_screen))
        self.assertFalse(page._detail_view.isHidden())
        self.assertEqual(page.TAB_ORDER[page._active_tab_index], page.TAB_BLOCKCHECK)
        self.assertEqual(page.navigation_screen().key, "card:hostings")

        self.assertTrue(page.restore_navigation_screen(report_screen))
        self.assertFalse(page._log_report_view.isHidden())
        self.assertTrue(page._detail_view.isHidden())
        self.assertEqual(page._log_report_view.report().title, "Подробный отчёт BlockCheck")

        self.assertTrue(page.restore_navigation_screen(tab_screen))
        self.assertTrue(page._log_report_view.isHidden())
        self.assertFalse(page._tabs_pivot.isHidden())
        self.assertEqual(page.navigation_screen().key, "tab:dns_spoofing")

        self.assertFalse(page.restore_navigation_screen(type(tab_screen)(key="tab:нет такой")))
        self.assertFalse(page.restore_navigation_screen(type(tab_screen)(key="card:hostings")))

    def test_progress_reaches_the_steps_widget(self) -> None:
        page = self._page()

        page._on_progress("hostings", 5, 59)

        self.assertEqual(page._progress_steps.rows["hostings"].count_label.text(), "5 / 59")


class EveryHostingTests(unittest.TestCase):
    TARGETS = (
        {"id": "A-1", "provider": "AWS", "url": "https://a.example/file"},
        {"id": "A-2", "provider": "AWS", "url": "https://b.example/file"},
        {"id": "H-1", "provider": "Old", "url": "http://plain.example/file"},
        {"id": "C-1", "provider": "CDN", "url": "https://c.example/file"},
    )

    def test_every_https_address_is_its_own_check(self) -> None:
        picked = every_freeze_target(self.TARGETS)

        self.assertEqual([(provider, [item["id"] for item in items]) for provider, items in picked], [
            ("AWS", ["A-1"]), ("AWS", ["A-2"]), ("CDN", ["C-1"]),
        ])

    def _run(self, *, every: bool):
        def download(host, _path):
            if host == "a.example":
                return ProbeResult(ip="1.1.1.1", kind="ok", body_size=16_500, body_cut=True)
            if host == "b.example":
                return ProbeResult(ip="1.1.1.2", kind="ok", body_size=65_536)
            return None

        seen = []
        with ThreadPoolExecutor(max_workers=8) as pool, patch("blockcheck.data_lists.TCP_16_20_TARGETS", self.TARGETS):
            servers = check_freeze(
                pool.submit,
                lambda future: future.result(),
                download,
                every=every,
                on_server=lambda server, done, total: seen.append((server.ident, done, total)),
            )
        return servers, seen

    def test_full_check_reports_each_server_with_provider_and_progress(self) -> None:
        servers, seen = self._run(every=True)

        self.assertEqual([(s.provider, s.ident, s.host, s.state) for s in servers], [
            ("AWS", "A-1", "a.example", FreezeState.FREEZE),
            ("AWS", "A-2", "b.example", FreezeState.OK),
            ("CDN", "C-1", "c.example", FreezeState.UNKNOWN),
        ])
        self.assertEqual(sorted(done for _ident, done, _total in seen), [1, 2, 3])
        self.assertEqual({total for _ident, _done, total in seen}, {3})

    def test_quick_check_still_takes_one_server_per_provider_with_spares(self) -> None:
        servers, seen = self._run(every=False)

        self.assertEqual([(s.provider, s.ident) for s in servers], [("AWS", "A-1"), ("CDN", "C-1")])
        self.assertEqual({total for _ident, _done, total in seen}, {2})

    def test_full_check_limits_simultaneous_servers(self) -> None:
        import threading
        import time

        targets = tuple({"id": f"S-{n}", "provider": "P", "url": f"https://h{n}.example/f"} for n in range(12))
        lock = threading.Lock()
        active = [0]
        peak = [0]

        def download(_host, _path):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.03)
            with lock:
                active[0] -= 1
            return None

        # Предел держит очередь прогона: ждущий сервер не занимает поток.
        from diagnostics.run_context import Run

        run = Run(None, workers=12)
        self.addCleanup(run.close)
        with patch("blockcheck.data_lists.TCP_16_20_TARGETS", targets):
            servers = check_freeze(run.lane(3).submit, lambda future: future.result(), download, every=True)

        self.assertEqual(len(servers), 12)
        self.assertLessEqual(peak[0], 3)


if __name__ == "__main__":
    unittest.main()
