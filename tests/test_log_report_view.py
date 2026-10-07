import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QTextDocument
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

from ui.code_editor.log_syntax import (
    KIND_FAIL,
    KIND_HEADING,
    KIND_OK,
    KIND_PLAIN,
    KIND_RULE,
    KIND_WARN,
    LogSyntaxHighlighter,
    count_log_states,
    heading_title,
    log_line_kind,
    log_outline,
    state_color,
)
from ui.code_editor.syntax import SyntaxTheme
from ui.widgets.log_report_view import LogReport, LogReportView

_TEXT = "\n".join(
    [
        "ПРОВЕРКА DNS-СЕРВЕРОВ",
        "Проверено 2 из 2 адресов за 12 с.",
        "",
        "Итог:",
        "  ✗ Обычные DNS-запросы перехватываются по дороге.",
        "  ✓ Лучше всего подходит Google DNS (8.8.4.4).",
        "",
        "=== Адреса с разных DNS ===",
        "Google DNS [8.8.8.8] (12 мс): 142.250.74.14",
        "[12:01:46.020] ❌ youtube.com: обрыв после 16 КБ (TIMEOUT)",
        "[12:01:46.500] ⚠️ rutracker.org отвечает через раз",
        "--------------------",
        "TIMEOUT",
    ]
)


class LogLinesTests(unittest.TestCase):
    def test_headings_are_found_by_frame_capitals_and_colon(self) -> None:
        self.assertEqual(heading_title("=== Пинг ==="), "Пинг")
        self.assertEqual(heading_title("ПРОВЕРКА DNS-СЕРВЕРОВ"), "ПРОВЕРКА DNS-СЕРВЕРОВ")
        self.assertEqual(heading_title("Подробности:"), "Подробности")
        # Одно слово заглавными — это ответ сервера, а не раздел; строка с отступом — тоже не раздел.
        self.assertEqual(heading_title("TIMEOUT"), "")
        self.assertEqual(heading_title("  обычные запросы выполняет:"), "")
        self.assertEqual(heading_title("[12:01:44] Запуск стратегии:"), "")
        self.assertEqual(heading_title("Google DNS [8.8.8.8] (12 мс): 142.250.74.14"), "")
        self.assertEqual(heading_title("=========="), "")

    def test_line_kind_outline_and_counts(self) -> None:
        kinds = [log_line_kind(line) for line in _TEXT.split("\n")]
        self.assertEqual(
            kinds,
            [
                KIND_HEADING,
                KIND_PLAIN,
                KIND_PLAIN,
                KIND_HEADING,
                KIND_FAIL,
                KIND_OK,
                KIND_PLAIN,
                KIND_HEADING,
                KIND_PLAIN,
                KIND_FAIL,
                KIND_WARN,
                KIND_RULE,
                KIND_PLAIN,
            ],
        )
        self.assertEqual(log_outline(_TEXT), ((1, "ПРОВЕРКА DNS-СЕРВЕРОВ"), (4, "Итог"), (8, "Адреса с разных DNS")))
        self.assertEqual(count_log_states(_TEXT), {KIND_OK: 1, KIND_WARN: 1, KIND_FAIL: 2})


class HighlighterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _formats(self, line: str):
        """Раскраска одной строки: {позиция: (цвет, жирный)}."""
        document = QTextDocument()
        self.addCleanup(document.deleteLater)
        theme = SyntaxTheme(accent=QColor("#60cdff"), text=QColor("#ffffff"))
        highlighter = LogSyntaxHighlighter(document, theme=theme)
        document.setPlainText(line)
        highlighter.rehighlight()
        result = {}
        for item in document.firstBlock().layout().formats():
            for position in range(item.start, item.start + item.length):
                result[position] = (
                    item.format.foreground().color().name(QColor.NameFormat.HexArgb),
                    item.format.fontWeight() == QFont.Weight.Bold,
                )
        return result

    def test_states_addresses_and_time_get_their_own_look(self) -> None:
        fail = state_color(KIND_FAIL, dark=True).name(QColor.NameFormat.HexArgb)
        ok = state_color(KIND_OK, dark=True).name(QColor.NameFormat.HexArgb)
        accent = "#ff60cdff"

        line = "12:01:46 youtube.com 8.8.8.8 не работает за 310 мс, 2a13:1001::86 работает"
        formats = self._formats(line)
        # Время в начале строки приглушено и не принято за адрес IPv6.
        self.assertEqual(formats[0], ("#69ffffff", False))
        self.assertEqual(formats[line.index("youtube")], (accent, False))
        self.assertEqual(formats[line.index("8.8.8.8")], (accent, False))
        self.assertEqual(formats[line.index("2a13")], (accent, False))
        # «не работает» целиком красное: отрицание перекрывает зелёное «работает».
        self.assertEqual(formats[line.index("не работает")], (fail, True))
        self.assertEqual(formats[line.index("не работает") + 4], (fail, True))
        self.assertEqual(formats[line.rindex("работает")], (ok, True))
        self.assertEqual(formats[line.index("310")], ("#ffffffff", True))
        self.assertNotIn(line.index("за"), formats)
        # Время посреди строки — не адрес IPv6: в нём всего два двоеточия.
        self.assertEqual(self._formats("упал в 12:01:46 ночи"), {})

    def test_heading_rule_and_marked_line_are_painted_whole(self) -> None:
        heading = self._formats("=== Пинг ===")
        self.assertEqual(set(heading.values()), {("#ff60cdff", True)})
        self.assertEqual(len(heading), len("=== Пинг ==="))
        self.assertEqual(set(self._formats("----------").values()), {("#55ffffff", False)})

        # Значок-эмодзи занимает у Qt две позиции: строка должна быть покрашена до последнего символа.
        line = "🚫 порт 443 не для нас"
        marked = self._formats(line)
        utf16_length = len(line.encode("utf-16-le")) // 2
        self.assertEqual(utf16_length, len(line) + 1)
        self.assertEqual(sorted(marked), list(range(utf16_length)))
        fail = state_color(KIND_FAIL, dark=True).name(QColor.NameFormat.HexArgb)
        self.assertEqual(marked[utf16_length - 1], (fail, False))

    def test_light_theme_uses_darker_state_colors(self) -> None:
        self.assertNotEqual(state_color(KIND_FAIL, dark=True), state_color(KIND_FAIL, dark=False))


class LogReportViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _view(self) -> LogReportView:
        view = LogReportView()
        self.addCleanup(view.deleteLater)
        view.resize(1000, 600)
        return view

    def _report(self, **overrides) -> LogReport:
        values = dict(title="Отчёт проверки", text=_TEXT, root_title="DNS-серверы", description="Полный текст.")
        values.update(overrides)
        return LogReport(**values)

    def test_report_is_shown_in_read_only_editor_with_chips(self) -> None:
        view = self._view()
        view.show_report(self._report(), animate=False)

        self.assertEqual(view.breadcrumb.count(), 2)
        self.assertEqual((view.title_label.text(), view.description_label.text()), ("Отчёт проверки", "Полный текст."))
        self.assertTrue(view.editor.isReadOnly())
        self.assertEqual(view.editor.toPlainText(), _TEXT)
        self.assertIsInstance(view.editor._highlighter, LogSyntaxHighlighter)
        self.assertEqual(view.editor.cursor_status().line, 1)
        # Счётчики — только тех видов строк, что есть в тексте; подложка нейтральная, цвет — точкой.
        self.assertEqual(
            {kind: chip.text() for kind, chip in view.state_chips.items()},
            {KIND_FAIL: "Ошибки: 2", KIND_WARN: "Предупреждения: 1", KIND_OK: "Успешно: 1"},
        )
        self.assertIsNotNone(view.state_chips[KIND_FAIL].dot_color())
        self.assertEqual([chip.text() for chip in view.section_chips], ["ПРОВЕРКА DNS-СЕРВЕРОВ", "Итог", "Адреса с разных DNS"])
        self.assertIsNone(view.section_chips[0].dot_color())
        self.assertIn("всего строк: 13", view.status_label.text())
        self.assertIn("Ctrl+F", view.status_label.text())

    def test_chips_jump_to_sections_and_to_the_next_problem_line(self) -> None:
        view = self._view()
        view.show_report(self._report(), animate=False)

        view.section_chips[2].click()
        self.assertEqual(view.editor.cursor_status().line, 8)
        # После раздела следующая ошибка — на строке 10, затем по кругу — снова первая (строка 5).
        view.state_chips[KIND_FAIL].click()
        self.assertEqual(view.editor.cursor_status().line, 10)
        self.assertEqual(view.goto_next_state(KIND_FAIL), 5)
        self.assertEqual(view.goto_next_state(KIND_WARN), 11)
        self.assertEqual(view.goto_next_state("нет такого"), 0)

    def test_escape_closes_search_first_and_only_then_the_page(self) -> None:
        view = self._view()
        view.show_report(self._report(), animate=False)
        closed: list[bool] = []
        view.closed.connect(lambda: closed.append(True))

        view.find_button.click()
        self.assertFalse(view.find_bar.isHidden())
        self.assertTrue(view.find_bar.replaceRow.isHidden())
        view.find_bar.search_input.setText("DNS")
        view.find_controller.refresh_matches()
        self.assertEqual(len(view.find_controller.result.matches), 5)
        QTest.keyClick(view.editor, Qt.Key.Key_Escape)
        self.assertTrue(view.find_bar.isHidden())
        self.assertEqual(closed, [])
        QTest.keyClick(view.editor, Qt.Key.Key_Escape)
        self.assertEqual(closed, [True])

        view._on_breadcrumb(view.REPORT_KEY)
        self.assertEqual(closed, [True])
        view._on_breadcrumb(view.ROOT_KEY)
        self.assertEqual(closed, [True, True])

    def test_copy_wrap_tail_and_empty_report(self) -> None:
        view = self._view()
        view.show_report(self._report(), animate=False)

        view.copy_button.click()
        self.assertEqual(QApplication.clipboard().text(), _TEXT)
        self.assertEqual(view.copy_button.text(), "Скопировано")
        view.wrap_button.click()
        self.assertEqual(view.editor.lineWrapMode(), view.editor.LineWrapMode.WidgetWidth)

        # Живой лог открывается с конца, длинный — дописывается сразу целиком.
        long_text = "\n".join(f"строка {number}" for number in range(1, 2501))
        view.show_report(self._report(text=long_text, scroll_to_end=True), animate=False)
        self.assertEqual(view.editor.blockCount(), 2500)
        self.assertEqual(view.editor.cursor_status().line, 2500)
        self.assertEqual(view.copy_button.text(), "Скопировать всё")
        self.assertTrue(view.chips.isHidden())

        # Длинный отчёт без «с конца» дописывается порциями; переход к дальнему разделу дописывает остаток.
        view.show_report(self._report(text=long_text + "\n=== Конец ==="), animate=False)
        self.assertLess(view.editor.blockCount(), 2501)
        view.section_chips[0].click()
        self.assertEqual(view.editor.cursor_status().line, 2501)

        view.show_report(self._report(text="", empty_text="Проверка ещё не запускалась."), animate=False)
        self.assertEqual(view.editor.toPlainText(), "Проверка ещё не запускалась.")
        self.assertFalse(view.copy_button.isEnabled())
        self.assertFalse(view.find_button.isEnabled())

    def test_editor_fills_the_window_height_so_the_page_does_not_scroll(self) -> None:
        area = QScrollArea()
        self.addCleanup(area.deleteLater)
        area.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        view = LogReportView(content)
        layout.addWidget(view)
        layout.addStretch(1)
        area.setWidget(content)
        area.resize(1000, 700)
        area.show()
        view.show_report(self._report(), animate=False)
        for _ in range(5):
            QApplication.processEvents()

        self.assertGreater(view.editor.height(), 450)
        self.assertEqual(area.verticalScrollBar().maximum(), 0)
        # Окно стало ниже — редактор ужимается вместе с ним, а не выталкивает полосу прокрутки страницы.
        area.resize(1000, 520)
        for _ in range(5):
            QApplication.processEvents()
        self.assertLess(view.editor.height(), 450)
        self.assertEqual(area.verticalScrollBar().maximum(), 0)


if __name__ == "__main__":
    unittest.main()
