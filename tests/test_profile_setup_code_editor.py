from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication


def _worker_stub(*_args, **_kwargs):
    return None


class ProfileSetupCodeEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        self.app.processEvents()
        self.app.closeAllWindows()
        self.app.processEvents()

    def _page(self):
        from profile.ui.profile_setup_page import ProfileSetupPageBase

        page = ProfileSetupPageBase(
            create_profile_setup_load_worker=_worker_stub,
            create_profile_list_file_load_worker=_worker_stub,
            create_profile_list_file_save_worker=_worker_stub,
            create_profile_list_file_validation_worker=_worker_stub,
            create_profile_settings_save_worker=_worker_stub,
            create_profile_raw_text_save_worker=_worker_stub,
            create_profile_enabled_save_worker=_worker_stub,
            create_profile_user_update_worker=_worker_stub,
            create_profile_user_delete_worker=_worker_stub,
            create_profile_strategy_apply_worker=_worker_stub,
            create_profile_strategy_feedback_save_worker=_worker_stub,
            open_profiles=lambda: None,
            open_root=lambda: None,
            on_profile_changed=lambda: None,
        )
        self.addCleanup(page.deleteLater)
        return page

    def test_raw_profile_editor_is_code_editor_with_search(self) -> None:
        from ui.code_editor.editor import CodeEditor
        from ui.code_editor.find_bar import FindReplaceBar
        from ui.code_editor.syntax import PresetSyntaxHighlighter

        page = self._page()
        page._ensure_match_tab_built()

        self.assertIsInstance(page._raw_profile_text, CodeEditor)
        self.assertIsInstance(page._raw_profile_find_bar, FindReplaceBar)
        self.assertIsInstance(page._raw_profile_text._highlighter, PresetSyntaxHighlighter)
        # Панель поиска не занимает место, пока её не открыли по Ctrl+F.
        self.assertTrue(page._raw_profile_find_bar.isHidden())

    def test_raw_profile_search_highlights_matches(self) -> None:
        page = self._page()
        page._ensure_match_tab_built()
        page._raw_profile_text.setPlainText("--filter-tcp=443\n--filter-udp=443\n")

        QTest.keyClick(page._raw_profile_text, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        page._raw_profile_find_bar.search_input.setText("--filter")
        self.app.processEvents()

        self.assertFalse(page._raw_profile_find_bar.isHidden())
        self.assertEqual(page._raw_profile_find_controller.result.count, 2)
        self.assertEqual(page._raw_profile_find_bar.counterLabel.text(), "1 / 2")

    def test_list_file_editors_are_code_editors(self) -> None:
        from ui.code_editor.editor import CodeEditor
        from ui.code_editor.find_bar import FindReplaceBar
        from ui.code_editor.syntax import ListFileSyntaxHighlighter

        page = self._page()
        page._ensure_editor_tab_built()

        self.assertIsInstance(page._list_file_text, CodeEditor)
        self.assertIsInstance(page._list_file_base_text, CodeEditor)
        self.assertIsInstance(page._list_file_find_bar, FindReplaceBar)
        self.assertIsInstance(page._list_file_text._highlighter, ListFileSyntaxHighlighter)
        self.assertTrue(page._list_file_base_text.isReadOnly())
        self.assertTrue(page._list_file_find_bar.isHidden())

    def test_list_file_editor_style_keeps_zoom_working(self) -> None:
        page = self._page()
        page._ensure_editor_tab_built()
        page._refresh_list_file_editor_style(has_error=False)
        base = page._list_file_text.current_point_size()

        # QSS с font-size перебивал бы setFont и ломал зум по Ctrl+колесу.
        style = page._list_file_text.styleSheet()
        self.assertNotIn("font-size", style)
        self.assertNotIn("font-family", style)

        page._list_file_text.zoom_by(2)
        self.assertEqual(page._list_file_text.current_point_size(), base + 2)

    def test_theme_repaint_does_not_dirty_list_file_text(self) -> None:
        from ui.code_editor.syntax import SyntaxTheme

        page = self._page()
        page._ensure_editor_tab_built()
        page._loading = False
        page._list_file_text.setPlainText("youtube.com\ngoogle.com\n")
        page._list_file_text_dirty = False

        page._list_file_text._apply_highlighter_theme(
            SyntaxTheme(accent=QColor("#ff8c00"), text=QColor(0, 0, 0))
        )
        self.app.processEvents()

        self.assertFalse(page._list_file_text_dirty)

    def test_theme_repaint_does_not_invalidate_raw_profile_cache(self) -> None:
        from ui.code_editor.syntax import SyntaxTheme

        page = self._page()
        page._ensure_match_tab_built()
        page._raw_profile_text.setPlainText("--filter-tcp=443\n")
        page._raw_profile_text_cache = "--filter-tcp=443\n"

        page._raw_profile_text._apply_highlighter_theme(
            SyntaxTheme(accent=QColor("#ff8c00"), text=QColor(0, 0, 0))
        )
        self.app.processEvents()

        self.assertEqual(page._raw_profile_text_cache, "--filter-tcp=443\n")

    def test_user_edit_still_invalidates_raw_profile_cache(self) -> None:
        page = self._page()
        page._ensure_match_tab_built()
        page._raw_profile_text.setPlainText("--filter-tcp=443\n")
        page._raw_profile_text_cache = "--filter-tcp=443\n"

        page._raw_profile_text.insertPlainText("--hostlist=lists/a.txt\n")
        self.app.processEvents()

        self.assertIsNone(page._raw_profile_text_cache)


if __name__ == "__main__":
    unittest.main()
