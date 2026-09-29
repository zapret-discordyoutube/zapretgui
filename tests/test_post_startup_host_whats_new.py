from __future__ import annotations

import unittest
from unittest.mock import patch

from main.post_startup_host import PostStartupHost


class _Window:
    def __init__(self, *, visible: bool = True, minimized: bool = False) -> None:
        self._visible = visible
        self._minimized = minimized

    def isVisible(self) -> bool:  # noqa: N802
        return self._visible

    def isMinimized(self) -> bool:  # noqa: N802
        return self._minimized


class PostStartupHostWhatsNewTests(unittest.TestCase):
    """«Что нового» после обновления показывается поверх готового окна программы."""

    def test_shows_whats_new_over_visible_window_in_ui_language(self) -> None:
        window = _Window()
        history = ({"version": "1.2.3", "notes": "новое"},)

        with (
            patch("updater.ui.update_dialog.show_whats_new_dialog") as show_dialog,
            patch("ui.navigation.text_sync.resolve_ui_language", return_value="en"),
        ):
            shown = PostStartupHost(window).show_whats_new("1.2.3", history)

        self.assertTrue(shown)
        show_dialog.assert_called_once_with(window, version="1.2.3", history=history, language="en")

    def test_waits_while_window_is_hidden_in_tray_or_minimized(self) -> None:
        for window in (_Window(visible=False), _Window(minimized=True)):
            with patch("updater.ui.update_dialog.show_whats_new_dialog") as show_dialog:
                shown = PostStartupHost(window).show_whats_new("1.2.3", ())

            self.assertFalse(shown)
            show_dialog.assert_not_called()


if __name__ == "__main__":
    unittest.main()
