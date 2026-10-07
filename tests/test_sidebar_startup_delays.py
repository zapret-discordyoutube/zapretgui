from __future__ import annotations

import inspect
import unittest


class SidebarStartupDelayTests(unittest.TestCase):
    """Боковое меню не достраивается таймерами после показа окна.

    Замер на Windows: 16 пунктов меню, пять скрытых и поле поиска строятся за
    ~5 мс, пока фоновые потоки молчат. Отложенные на «после показа», они
    попадали на одно время с фоновыми задачами запуска и из-за общего замка
    Python занимали 116 + 356 + 30 мс, а окно при этом перестраивалось на
    глазах у пользователя.
    """

    def test_sidebar_has_no_deferred_build_stages(self) -> None:
        import ui.navigation.sidebar_builder as sidebar_builder

        for name in (
            "SIDEBAR_SECONDARY_GROUPS_AFTER_INTERACTIVE_MS",
            "SIDEBAR_SECONDARY_GROUP_STEP_MS",
            "SIDEBAR_SEARCH_AFTER_INTERACTIVE_MS",
            "SIDEBAR_HIDDEN_MODE_ITEMS_AFTER_INTERACTIVE_MS",
            "_schedule_sidebar_search_after_interactive",
            "_schedule_secondary_sidebar_groups_after_interactive",
            "_schedule_hidden_mode_nav_items_after_interactive",
            "_install_secondary_sidebar_groups",
            "_install_hidden_mode_nav_items",
        ):
            self.assertFalse(hasattr(sidebar_builder, name), name)

    def test_init_navigation_does_not_wait_for_interactive_signal(self) -> None:
        import ui.navigation.sidebar_builder as sidebar_builder

        source = inspect.getsource(sidebar_builder.init_navigation)
        self.assertNotIn("startup_interactive_ready", source)
        self.assertIn("_install_sidebar_search(window)", source)

    def test_startup_log_contract_has_no_sidebar_after_interactive_markers(self) -> None:
        from main import startup_log_contract

        for marker in startup_log_contract._AFTER_INTERACTIVE_MARKERS:
            self.assertNotIn("Sidebar", marker)
            self.assertNotIn("HiddenModeNav", marker)


if __name__ == "__main__":
    unittest.main()
