"""Отчёт BlockCheck вглубь: страница одного сервера, выводы DNS по частям, сводка серверов, общие действия группы."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QScrollArea

from blockcheck.ui.check_results import BlockcheckSummaryPanel
from blockcheck.ui.finding_parts import split_finding
from blockcheck.ui.result_cards import ResultDetailView, server_card, wants_findings
from blockcheck.ui.result_cards_model import Card, Line, Section
from blockcheck.ui.server_matrix import cell_state, count_state, parse_server_table, summarize_servers

TABLE = "\n".join(
    [
        "ПРОВЕРКА DNS-СЕРВЕРОВ",
        "",
        "Итог:",
        "  ✗ что-то",
        "",
        "Сервер      Адрес                 Пинг   UDP 53          TCP 53  DoH 443",
        "Cloudflare  1.1.1.1               4 мс   16 мс           21 мс   487 мс",
        "Cloudflare  2606:4700:4700::1111  4 мс   41 мс · 2 из 3  37 мс   обрыв",
        "Quad9       9.9.9.9               36 мс  молчит          молчит  молчит",
        "",
        "Подробности:",
        "Cloudflare (1.1.1.1)",
    ]
)

AKAMAI = Section(
    "Akamai — обрыв у 1 из 2",
    (
        Line("ok", "SE.AKM-01 · media.miele.com", "получено 32 КБ без обрыва · 5.1 с"),
        Line("fail", "US.AKM-02 · www.roxio.com", "загрузка оборвалась на 16 КБ · 4.8 с"),
    ),
)
HOSTINGS = Card(key="hostings", icon="fa5s.server", title="Зарубежные хостинги", level="fail", status="Обрыв у 1 из 2", sections=(AKAMAI,))


class ServerTableTests(unittest.TestCase):
    def test_table_is_read_by_the_header_columns(self) -> None:
        columns, rows = parse_server_table(TABLE)
        self.assertEqual(columns, ["Пинг", "UDP 53", "TCP 53", "DoH 443"])
        self.assertEqual([(row.server, row.address) for row in rows], [("Cloudflare", "1.1.1.1"), ("Cloudflare", "2606:4700:4700::1111"), ("Quad9", "9.9.9.9")])
        self.assertEqual(rows[1].cells, ("4 мс", "41 мс · 2 из 3", "37 мс", "обрыв"))
        # Текста без таблицы сводка не касается.
        self.assertEqual(parse_server_table("узел 1  10.0.0.1"), ([], []))

    def test_cells_are_counted_per_service(self) -> None:
        self.assertEqual([cell_state(text) for text in ("16 мс", "41 мс · 2 из 3", "молчит", "", "—")], ["ok", "warn", "fail", "none", "none"])
        columns, rows = parse_server_table(TABLE)
        cloudflare, quad9 = summarize_servers(rows, len(columns))
        self.assertEqual((cloudflare.name, len(cloudflare.rows)), ("Cloudflare", 2))
        # UDP: один адрес отвечает, второй с потерями; DoH: один работает, один нет.
        self.assertEqual(cloudflare.counts, ((2, 0, 0), (1, 1, 0), (2, 0, 0), (1, 0, 1)))
        self.assertEqual([count_state(*count) for count in cloudflare.counts], ["ok", "warn", "ok", "warn"])
        self.assertEqual([count_state(*count) for count in quad9.counts], ["ok", "fail", "fail", "fail"])


class FindingTests(unittest.TestCase):
    def test_finding_is_split_into_title_and_details(self) -> None:
        self.assertEqual(split_finding("Отвечают через раз: OpenDNS (1.2.3.4) и ещё 3. Так бывает"), ("Отвечают через раз", "OpenDNS (1.2.3.4) и ещё 3. Так бывает."))
        self.assertEqual(split_finding("Потеряли по одному запросу. Похоже на потери в сети"), ("Потеряли по одному запросу", "Похоже на потери в сети."))
        self.assertEqual(split_finding("Всё хорошо"), ("Всё хорошо", ""))

    def test_only_dns_conclusions_are_shown_in_parts(self) -> None:
        conclusions = Section("Выводы", (Line("fail", "Обычные ответы подменяются у серверов: Cloudflare (1.1.1.1)"),))
        self.assertTrue(wants_findings(conclusions, Card(key="dns_servers", icon="", title="", level="fail", status="")))
        self.assertFalse(wants_findings(conclusions, Card(key="system", icon="", title="", level="fail", status="")))


class ReportOnScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _view(self) -> ResultDetailView:
        area = QScrollArea()
        self.addCleanup(area.deleteLater)
        area.setWidgetResizable(True)
        view = ResultDetailView()
        area.setWidget(view)
        area.resize(1000, 400)
        area.show()
        self.app.processEvents()
        return view

    def test_dns_report_shows_chips_and_a_service_matrix_instead_of_raw_text(self) -> None:
        card = Card(
            key="dns_servers",
            icon="fa5s.network-wired",
            title="DNS-серверы",
            level="fail",
            status="Есть проблемы",
            sections=(
                Section("Выводы", (Line("warn", "Отвечают через раз: Quad9 (9.9.9.9), Quad9 (149.112.112.112) и ещё 3. Так бывает."),)),
                Section("Все серверы и способы связи", text=TABLE),
            ),
        )
        view = self._view()
        view.show_card(card)
        self.app.processEvents()

        conclusions, table = view.blocks
        [row] = conclusions.rows
        self.assertEqual(row.name_label.text(), "Отвечают через раз")
        self.assertEqual([chip.text for chip in row.server_chips], ["Quad9 ×2"])
        self.assertEqual((row.more_label.text(), row.text_label.text()), ("и ещё 3", "Так бывает."))
        # Таблица — сводкой по сервисам; сырой текст открывается кнопкой, а не лежит на странице.
        self.assertEqual([service.name for service in table.matrix.services()], ["Cloudflare", "Quad9"])
        self.assertIsNone(table.editor)
        self.assertEqual(table.open_text_button.text(), "Подробный текст")
        self.assertIn("2606:4700:4700::1111: Пинг — 4 мс, UDP 53 — 41 мс · 2 из 3", table.matrix.hint(0))

    def test_server_page_tells_what_was_checked_and_what_it_means(self) -> None:
        card = server_card(AKAMAI.lines[1], AKAMAI)
        self.assertEqual((card.title, card.level, card.status), ("www.roxio.com", "fail", "Загрузка оборвалась на 16 КБ"))
        facts, result, meaning = card.sections
        self.assertEqual([(line.name, line.text) for line in facts.lines], [("Провайдер", "Akamai"), ("Адрес", "www.roxio.com"), ("Сервер", "US.AKM-02"), ("Время проверки", "4.8 с")])
        self.assertEqual(result.lines[0].text, "загрузка оборвалась на 16 КБ")
        self.assertIn("фильтр провайдера", meaning.lines[0].name)

    def test_tile_opens_the_server_page_and_back_returns_to_the_list(self) -> None:
        view = self._view()
        closed = []
        view.closed.connect(lambda: closed.append(True))
        view.show_card(HOSTINGS)
        self.app.processEvents()

        grid = view.blocks[0].grid
        QTest.mouseClick(grid, Qt.MouseButton.LeftButton, pos=grid.tile_rect(1).center().toPoint())
        self.assertEqual(view.card().title, "www.roxio.com")
        # Строка пути: BlockCheck → Зарубежные хостинги → сервер.
        self.assertEqual(view.breadcrumb.count(), 3)
        self.assertTrue(view.go_back())
        self.assertEqual(view.card().key, "hostings")
        self.assertEqual(view.breadcrumb.count(), 2)
        # Выше списка хостингов шагов внутри отчёта нет — дальше отчёт закрывают.
        self.assertFalse(view.go_back())

        QTest.mouseClick(grid if False else view.blocks[0].grid, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
        self.assertEqual(view.card().title, "media.miele.com")
        view._on_breadcrumb(f"{view.LEVEL_KEY}0")
        self.assertEqual(view.card().key, "hostings")
        # Esc со страницы сервера — шаг назад, а не выход из отчёта.
        view.open_child(server_card(AKAMAI.lines[0], AKAMAI))
        QTest.keyClick(view, Qt.Key.Key_Escape)
        self.assertEqual((view.card().key, closed), ("hostings", []))
        QTest.keyClick(view, Qt.Key.Key_Escape)
        self.assertEqual(closed, [True])


class GroupActionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _dns(text: str) -> dict:
        return {"level": "warn", "kind": "dns", "text": text, "title": "", "target": "", "advice": [], "evidence": [], "action": "dns"}

    def test_one_action_and_one_report_button_stand_in_the_group_header(self) -> None:
        opened, acted = [], []
        panel = BlockcheckSummaryPanel(on_action=lambda *args: acted.append(args), on_open=opened.append)
        self.addCleanup(panel.deleteLater)
        report = {
            "dns_servers": {"level": "fail", "findings": [{"level": "fail", "text": "x"}], "text": ""},
            "problems": [self._dns("Обычные ответы подменяются у серверов: Cloudflare (1.1.1.1)"), self._dns("Отвечают через раз: Quad9 (9.9.9.9)")],
        }
        panel.show_report(report)

        [group] = panel.problem_groups()
        self.assertEqual(group.shared_action_button.text(), "Настройка DNS")
        # Строки не повторяют кнопку и не открывают один и тот же отчёт каждая.
        self.assertEqual([(row.action_button, row.card_key) for row in group.rows], [(None, ""), (None, "")])
        group.shared_action_button.click()
        group.report_button.click()
        self.assertEqual((acted, opened), ([("dns", "")], ["dns_servers"]))

    def test_single_row_keeps_its_own_button(self) -> None:
        panel = BlockcheckSummaryPanel(on_action=lambda *_args: None, on_open=lambda _key: None)
        self.addCleanup(panel.deleteLater)
        panel.show_report({"dns_servers": {"level": "fail", "findings": [], "text": ""}, "problems": [self._dns("Отвечают через раз: Quad9 (9.9.9.9)")]})

        [group] = panel.problem_groups()
        self.assertIsNone(group.shared_action_button)
        self.assertIsNotNone(group.rows[0].action_button)
        self.assertEqual(group.rows[0].card_key, "dns_servers")


if __name__ == "__main__":
    unittest.main()
