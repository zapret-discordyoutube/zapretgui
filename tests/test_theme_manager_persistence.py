from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))


class ThemeManagerTests(unittest.TestCase):
    def test_theme_comes_from_the_system_and_manager_has_nothing_to_persist(self) -> None:
        # Тему определяет isDarkTheme(), а не выбор пользователя: менеджер ничего
        # не строит в фоне и не сохраняет. Раньше здесь жила цепочка из фонового
        # построения CSS и сохранения темы, до которой никто не доходил.
        import ui.theme as theme

        removed = (
            "ThemeBuildWorker",
            "apply_theme_async",
            "_on_theme_css_ready",
            "_on_theme_build_error",
            "_cleanup_theme_build_thread",
            "_apply_css_only",
            "_request_theme_persist",
            "_start_theme_persist_worker",
        )
        self.assertEqual([name for name in removed if hasattr(theme, name) or hasattr(theme.ThemeManager, name)], [])

        manager = theme.ThemeManager(app=Mock(), widget=Mock())
        self.assertIn(manager.current_theme, {"light", "dark"})
        self.assertEqual(manager.themes, [])

    def test_cleanup_marks_the_manager_and_does_not_raise(self) -> None:
        import ui.theme as theme

        manager = theme.ThemeManager(app=Mock(), widget=Mock())
        manager.cleanup()
        manager.cleanup()
        self.assertTrue(manager._cleanup_in_progress)

    def test_startup_creates_the_manager_without_a_persist_worker(self) -> None:
        from main.window_startup_services import init_theme_manager

        window = SimpleNamespace(visual_state=SimpleNamespace(theme_manager=None))
        with patch("PyQt6.QtWidgets.QApplication.instance", return_value=Mock()):
            init_theme_manager(window, appearance_feature=Mock(spec=[]))
        self.assertIsNotNone(window.visual_state.theme_manager)

    def test_appearance_feature_has_no_theme_persist_leftovers(self) -> None:
        import settings.appearance as appearance
        import settings.appearance_workers as appearance_workers
        from app.feature_facades.appearance import AppearanceFeature

        self.assertFalse(hasattr(appearance_workers, "ThemePersistWorker"))
        self.assertFalse(hasattr(appearance, "save_selected_theme"))
        self.assertFalse(hasattr(AppearanceFeature, "create_theme_persist_worker"))
        self.assertFalse(hasattr(AppearanceFeature, "save_selected_theme"))


class ThemeModeSyncTests(unittest.TestCase):
    def test_same_theme_mode_does_not_restyle_all_widgets(self) -> None:
        from unittest.mock import patch

        from qfluentwidgets import Theme, qconfig

        from ui import theme as theme_module

        with patch("qfluentwidgets.setTheme") as set_theme:
            qconfig.themeMode.value = Theme.DARK
            theme_module._sync_theme_mode_to_qfluent("dark")
            set_theme.assert_not_called()
            theme_module._sync_theme_mode_to_qfluent("light")
            set_theme.assert_called_once_with(Theme.LIGHT)


if __name__ == "__main__":
    unittest.main()
