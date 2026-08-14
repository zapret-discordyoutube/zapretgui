from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication


class MatchRulerGeometryTests(unittest.TestCase):
    def test_marker_offset_spans_full_height(self) -> None:
        from ui.code_editor.match_ruler import MARKER_HEIGHT, marker_offset

        height = 203
        usable = height - MARKER_HEIGHT

        self.assertEqual(marker_offset(0, 100, height), 0)
        self.assertEqual(marker_offset(99, 100, height), usable)
        self.assertEqual(marker_offset(49, 100, height), round(49 / 99 * usable))

    def test_marker_offset_clamps_out_of_range_lines(self) -> None:
        from ui.code_editor.match_ruler import MARKER_HEIGHT, marker_offset

        height = 100
        self.assertEqual(marker_offset(-5, 10, height), 0)
        self.assertEqual(marker_offset(999, 10, height), height - MARKER_HEIGHT)

    def test_offset_and_line_are_inverse(self) -> None:
        from ui.code_editor.match_ruler import line_at_offset, marker_offset

        for line in (0, 17, 250, 749):
            offset = marker_offset(line, 750, 400)
            self.assertAlmostEqual(line_at_offset(offset, 750, 400), line, delta=2)

    def test_single_line_document_does_not_divide_by_zero(self) -> None:
        from ui.code_editor.match_ruler import line_at_offset, marker_offset

        self.assertEqual(marker_offset(0, 1, 120), 0)
        self.assertEqual(line_at_offset(60, 1, 120), 1)


class MatchRulerWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        self.app.processEvents()
        self.app.closeAllWindows()
        self.app.processEvents()

    def _editor(self, line_count: int = 400):
        from ui.code_editor.editor import CodeEditor

        editor = CodeEditor()
        self.addCleanup(editor.deleteLater)
        editor.resize(600, 400)
        editor.setPlainText("\n".join(f"line {index}" for index in range(line_count)))
        return editor

    def _matches_for(self, editor, query: str):
        from ui.code_editor.find_engine import find_matches

        return find_matches(editor.toPlainText(), query).matches

    def test_ruler_is_hidden_and_takes_no_width_without_matches(self) -> None:
        editor = self._editor()

        self.assertEqual(editor.match_ruler_width(), 0)
        self.assertTrue(editor._match_ruler.isHidden())
        self.assertEqual(editor.viewportMargins().right(), 0)

    def test_ruler_appears_with_matches_and_reserves_width(self) -> None:
        from ui.code_editor.match_ruler import RULER_WIDTH

        editor = self._editor()
        editor.show()
        self.app.processEvents()

        editor.set_search_highlights(self._matches_for(editor, "line 1"), 0)
        self.app.processEvents()

        self.assertEqual(editor.match_ruler_width(), RULER_WIDTH)
        self.assertFalse(editor._match_ruler.isHidden())
        self.assertEqual(editor.viewportMargins().right(), RULER_WIDTH)

    def test_ruler_marks_lines_of_every_match(self) -> None:
        editor = self._editor(line_count=50)
        editor.setPlainText("alpha\nbeta\ngamma\nalpha\n")

        editor.set_search_highlights(self._matches_for(editor, "alpha"), 1)

        self.assertEqual(editor._match_ruler.match_lines, (0, 3))
        self.assertEqual(editor._match_ruler._current_line, 3)
        self.assertEqual(editor._match_ruler._total_lines, editor.blockCount())

    def test_clearing_search_hides_ruler_again(self) -> None:
        editor = self._editor()
        editor.set_search_highlights(self._matches_for(editor, "line 2"), 0)
        self.assertTrue(editor._match_ruler.has_markers())

        editor.clear_search_highlights()

        self.assertFalse(editor._match_ruler.has_markers())
        self.assertEqual(editor.match_ruler_width(), 0)
        self.assertTrue(editor._match_ruler.isHidden())

    def test_ruler_tracks_visible_range_while_scrolling(self) -> None:
        editor = self._editor(line_count=600)
        editor.show()
        self.app.processEvents()
        editor.set_search_highlights(self._matches_for(editor, "line 5"), 0)
        self.app.processEvents()
        top_range = editor._match_ruler._visible_range

        editor.goto_line(300)
        self.app.processEvents()

        self.assertNotEqual(editor._match_ruler._visible_range, top_range)
        first, last = editor._match_ruler._visible_range
        self.assertLessEqual(first, 300)
        self.assertGreaterEqual(last, 299)

    def test_click_on_ruler_scrolls_to_that_part_of_document(self) -> None:
        editor = self._editor(line_count=600)
        editor.show()
        self.app.processEvents()
        editor.set_search_highlights(self._matches_for(editor, "line 5"), 0)
        self.app.processEvents()

        ruler = editor._match_ruler
        event = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(5.0, ruler.height() / 2),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.app.sendEvent(ruler, event)
        self.app.processEvents()

        middle = editor.blockCount() // 2
        self.assertAlmostEqual(editor.textCursor().blockNumber(), middle, delta=5)

    def test_keyboard_walks_between_markers(self) -> None:
        from PyQt6.QtTest import QTest

        editor = self._editor(line_count=40)
        editor.setPlainText("alpha\nbeta\nalpha\ngamma\nalpha\n")
        editor.show()
        self.app.processEvents()
        editor.set_search_highlights(self._matches_for(editor, "alpha"), 0)
        self.app.processEvents()

        ruler = editor._match_ruler
        self.assertEqual(ruler.match_lines, (0, 2, 4))

        QTest.keyClick(ruler, Qt.Key.Key_Down)
        self.assertEqual(editor.textCursor().blockNumber(), 2)

        QTest.keyClick(ruler, Qt.Key.Key_End)
        self.assertEqual(editor.textCursor().blockNumber(), 4)

        QTest.keyClick(ruler, Qt.Key.Key_Home)
        self.assertEqual(editor.textCursor().blockNumber(), 0)

    def test_keyboard_navigation_wraps_around(self) -> None:
        editor = self._editor(line_count=20)
        editor.setPlainText("alpha\nbeta\nalpha\n")
        editor.set_search_highlights(self._matches_for(editor, "alpha"), 1)

        ruler = editor._match_ruler
        self.assertEqual(ruler.step_to_neighbour_match(forward=True), 0)
        self.assertEqual(ruler.step_to_neighbour_match(forward=False), 0)

    def test_keyboard_is_ignored_without_markers(self) -> None:
        editor = self._editor(line_count=20)
        ruler = editor._match_ruler

        self.assertIsNone(ruler.step_to_neighbour_match(forward=True))

    def test_ruler_is_reachable_by_keyboard_and_named(self) -> None:
        editor = self._editor(line_count=20)
        ruler = editor._match_ruler

        self.assertEqual(ruler.focusPolicy(), Qt.FocusPolicy.TabFocus)
        self.assertEqual(ruler.accessibleName(), "Карта совпадений")
        self.assertEqual(ruler.property("screenReaderStateText"), "Карта совпадений")

    def test_ruler_colors_follow_theme_accent(self) -> None:
        from qfluentwidgets import themeColor

        editor = self._editor(line_count=10)
        editor.set_search_highlights(self._matches_for(editor, "line 1"), 0)

        accent = themeColor()
        self.assertEqual(editor._match_ruler._current_color.name(), accent.name())
        self.assertEqual(editor._match_ruler._marker_color.name(), accent.name())
        self.assertLess(editor._match_ruler._marker_color.alpha(), 255)


if __name__ == "__main__":
    unittest.main()
