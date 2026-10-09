"""Блок «Прошлые проверки»: понятная таблица, до 50 записей, строка открывает ту проверку целиком."""

import os
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from blockcheck import commands
from blockcheck.ui.check_results import HISTORY_ALL, HISTORY_SHOWN, BlockcheckHistoryList, history_rows
from blockcheck.ui.page import BlockcheckPage
from blockcheck.ui.past_check_view import report_from_history
from diagnostics import history
from settings import schema


def _run(time, title, level, states, problems=(), headline=""):
    return {
        "kind": "blockcheck",
        "time": time,
        "title": title,
        "level": level,
        "headline": headline,
        "problems": list(problems),
        "states": dict(states),
        "log_file": "",
    }


RUNS = [
    _run("2026-10-07T21:00:00", "Полная проверка", "fail", {"Telegram": "fail", "Discord": "fail", "YouTube": "ok"}, ["a", "b"], "Telegram не открывается"),
    _run("2026-10-07T22:00:00", "Все сайты", "ok", {"YouTube": "ok"}, [], "Всё открывается"),
    _run("2026-10-07T23:00:00", "Полная проверка", "fail", {"Telegram": "fail", "Discord": "ok", "YouTube": "ok"}, ["a"], "Telegram не открывается"),
]


class HistoryRowsTests(unittest.TestCase):
    def test_row_says_how_many_sites_opened_and_what_changed(self) -> None:
        newest, middle, oldest = history_rows(RUNS)

        self.assertEqual((newest.when, newest.scope), ("07.10 23:00", "Полная проверка"))
        self.assertEqual(newest.outcome, "Открывается 2 из 3 · проблем: 1")
        # Сравнение — с прошлой проверкой того же набора сайтов, а не с соседней строкой.
        self.assertEqual(newest.changes, "Снова открываются: Discord")
        self.assertEqual((newest.opened, newest.blocked, newest.unknown), (2, 1, 0))
        self.assertEqual((middle.outcome, middle.changes), ("Открывается 1 из 1", "Первая такая проверка"))
        self.assertEqual(oldest.changes, "Первая такая проверка")

    def test_same_result_is_called_no_changes_and_old_record_keeps_its_headline(self) -> None:
        rows = history_rows([RUNS[2], {**RUNS[2], "time": "2026-10-07T23:30:00"}])
        self.assertEqual(rows[0].changes, "Без изменений")
        # Запись без состояний сайтов (старая) показывает то, что в ней есть.
        [row] = history_rows([{"kind": "blockcheck", "time": "2026-10-06T10:00:00", "title": "Все сайты", "level": "fail", "headline": "YouTube не открывается"}])
        self.assertEqual(row.outcome, "YouTube не открывается")

    def test_history_keeps_fifty_runs(self) -> None:
        self.assertEqual((schema.CHECK_HISTORY_LIMIT, HISTORY_ALL), (50, 50))


class PastReportFileTests(unittest.TestCase):
    def test_saved_report_is_read_back_and_a_missing_or_foreign_file_gives_none(self) -> None:
        report = {"scope": "full", "problems": [], "services": [{"key": "youtube", "label": "YouTube", "level": "ok"}]}
        with TemporaryDirectory() as folder:
            log_file = os.path.join(folder, "run.log")
            entry = history.blockcheck_entry(report, log_file=log_file)
            with open(os.path.join(folder, "run.json"), "w", encoding="utf-8") as stream:
                stream.write(history.report_json(report, entry))
            self.assertEqual(commands.load_past_blockcheck_report(log_file), report)

            with open(os.path.join(folder, "other.json"), "w", encoding="utf-8") as stream:
                stream.write('{"format": "чужой", "report": {}}')
            self.assertIsNone(commands.load_past_blockcheck_report(os.path.join(folder, "other.log")))
            self.assertIsNone(commands.load_past_blockcheck_report(os.path.join(folder, "нет.log")))
        self.assertIsNone(commands.load_past_blockcheck_report(""))

    def test_history_record_alone_still_gives_something_to_show(self) -> None:
        shown = report_from_history(RUNS[0])
        self.assertEqual([service["label"] for service in shown["services"]], ["Telegram", "Discord", "YouTube"])
        self.assertEqual(shown["working"], ["YouTube"])
        self.assertEqual([problem["text"] for problem in shown["problems"]], ["a", "b"])


class _Feature:
    def __init__(self, report=None) -> None:
        self.report = report
        self.asked: list[str] = []

    def load_past_blockcheck_report(self, log_file):
        self.asked.append(log_file)
        return self.report


class PastCheckOnScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_list_shows_the_last_runs_and_all_of_them_by_button(self) -> None:
        many = [_run(f"2026-10-07T{hour:02}:00:00", "Все сайты", "ok", {"YouTube": "ok"}) for hour in range(20)]
        widget = BlockcheckHistoryList()
        self.addCleanup(widget.deleteLater)
        widget.show_history(many)

        self.assertEqual(len(widget.rows()), HISTORY_SHOWN)
        self.assertEqual(widget.more_button.text(), "Показать все: 20")
        widget.more_button.click()
        self.assertEqual(len(widget.rows()), 20)
        self.assertEqual(widget.more_button.text(), "Свернуть")
        # Мало записей — кнопки нет.
        widget.show_history(many[:3])
        self.assertTrue(widget.more_button.isHidden())

    def test_click_on_a_row_asks_to_open_that_run(self) -> None:
        widget = BlockcheckHistoryList()
        self.addCleanup(widget.deleteLater)
        widget.resize(1000, 300)
        widget.show()
        widget.show_history(RUNS)
        opened = []
        widget.run_opened.connect(opened.append)

        table = widget.table
        # Вторая строка сверху — предпоследняя запись.
        QTest.mouseClick(table, Qt.MouseButton.LeftButton, pos=QPoint(200, table.HEADER + table.ROW + 5))
        self.assertEqual([run["time"] for run in opened], ["2026-10-07T22:00:00"])
        QTest.mouseClick(table, Qt.MouseButton.LeftButton, pos=QPoint(200, 5))
        self.assertEqual(len(opened), 1)
        self.assertIn("Нажмите", table.hint(0) + "Нажмите")

    def _page(self, feature) -> BlockcheckPage:
        with patch.object(BlockcheckPage, "_request_page_initial_state_load", lambda self: None):
            page = BlockcheckPage(
                blockcheck_feature=feature,
                dns_feature=object(),
                create_strategy_scan_worker=lambda *_args, **_kwargs: None,
            )
        self.addCleanup(page.deleteLater)
        return page

    def test_page_opens_the_saved_report_full_page_and_returns_by_escape(self) -> None:
        saved = {
            "problems": [],
            "working": ["YouTube"],
            "services": [{"key": "youtube", "label": "YouTube", "level": "ok", "kind": "", "targets": []}],
        }
        feature = _Feature(saved)
        page = self._page(feature)
        page._show_history(RUNS)

        page._history_list.run_opened.emit({**RUNS[2], "log_file": "C:/logs/run.log"})
        view = page._past_check_view
        self.assertFalse(view.isHidden())
        self.assertEqual(feature.asked, ["C:/logs/run.log"])
        self.assertTrue(page._tabs_pivot.isHidden())
        # Заголовок стоит в ряду со ссылкой на инструкцию: прячется весь ряд.
        self.assertTrue(page._title_header.isHidden())
        self.assertEqual(view.title(), "Проверка 07.10 23:00 · Полная проверка")
        self.assertTrue(view.note_label.isHidden())
        self.assertEqual([card.card.key for card in view.cards.cards()], ["site:youtube"])

        # Отчёт карточки, открытый отсюда, возвращает сюда же: строка пути получает шаг «проверка».
        view.card_opened.emit(view.cards.cards()[0].card)
        detail = page._detail_view
        self.assertFalse(detail.isHidden())
        self.assertTrue(view.isHidden())
        self.assertEqual(detail.breadcrumb.count(), 3)
        detail.closed.emit()
        self.assertFalse(view.isHidden())
        self.assertTrue(detail.isHidden())
        self.assertTrue(page._tabs_pivot.isHidden())
        # Находка из прошлой проверки открывается своей страницей — сразу, без отчёта раздела перед ней.
        from blockcheck.ui.result_cards import finding_detail_card

        view.card_opened.emit(finding_detail_card("Отвечают через раз: Quad9 (9.9.9.9)", "warn"))
        self.assertEqual(detail.card().title, "Отвечают через раз")
        self.assertEqual(detail.breadcrumb.count(), 3)
        self.assertFalse(detail.go_back())
        detail.closed.emit()
        self.assertFalse(view.isHidden())
        # А «BlockCheck» в строке пути ведёт сразу на вкладку.
        view.card_opened.emit(view.cards.cards()[0].card)
        detail._on_breadcrumb(detail.ROOT_KEY)
        self.assertTrue(detail.isHidden())
        self.assertTrue(view.isHidden())
        self.assertFalse(page._tabs_pivot.isHidden())

        page._history_list.run_opened.emit({**RUNS[2], "log_file": "C:/logs/run.log"})
        page._escape_shortcut.activated.emit()
        self.assertTrue(view.isHidden())
        self.assertFalse(page._tabs_pivot.isHidden())

    def test_hover_hint_is_the_program_tooltip_not_the_system_one(self) -> None:
        from PyQt6.QtCore import QEvent, QPointF
        from PyQt6.QtGui import QHelpEvent, QMouseEvent

        def move(y: int) -> None:
            point = QPointF(200, y)
            event = QMouseEvent(
                QEvent.Type.MouseMove,
                point,
                QPointF(table.mapToGlobal(point.toPoint())),
                Qt.MouseButton.NoButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            QApplication.sendEvent(table, event)

        widget = BlockcheckHistoryList()
        self.addCleanup(widget.deleteLater)
        widget.resize(1000, 300)
        widget.show()
        widget.show_history(RUNS)
        table = widget.table
        move(table.HEADER + 5)
        self.assertIn("Нажмите, чтобы открыть эту проверку", table._hint.text())
        # Системная подсказка подавлена: на тёмной теме она мелькала белым окном.
        self.assertTrue(table.event(QHelpEvent(QEvent.Type.ToolTip, QPoint(200, 40), table.mapToGlobal(QPoint(200, 40)))))
        move(5)
        self.assertEqual(table._hint.text(), "")

    def test_past_check_opens_its_text_from_the_report_and_says_when_there_is_none(self) -> None:
        from blockcheck.ui.past_check_view import PastCheckView

        view = PastCheckView()
        self.addCleanup(view.deleteLater)
        opened = []
        view.text_opened.connect(lambda title, text: opened.append((title, text)))
        run = {"time": "2026-10-08T12:00:00", "title": "Все сайты", "level": "ok"}

        # Текст той проверки лежит в самом её отчёте.
        view.show_run(run, {"problems": [], "working": ["Discord"], "text": ["строка 1", "строка 2"]})
        self.assertTrue(view.report_button.isEnabled())
        view.report_button.click()
        self.assertEqual(opened, [(f"Отчёт: {view.title()}", "строка 1\nстрока 2")])
        # Отчёт, сохранённый до этого, текста не содержит: кнопка выключена и говорит почему.
        view.show_run(run, {"problems": [], "working": ["Discord"]})
        self.assertFalse(view.report_button.isEnabled())
        self.assertIn("не сохранялся", view.report_button.toolTip())
        view.show_run(run, None)
        self.assertFalse(view.report_button.isEnabled())

    def test_without_the_saved_file_the_page_shows_the_history_record_and_says_so(self) -> None:
        page = self._page(_Feature(None))
        page._open_past_check(RUNS[0])

        view = page._past_check_view
        self.assertFalse(view.note_label.isHidden())
        self.assertEqual(len(view.cards.cards()), 3)
        self.assertTrue(view.summary.title_label.text().startswith("Найдены проблемы"))


if __name__ == "__main__":
    unittest.main()
