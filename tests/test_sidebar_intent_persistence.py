from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))


class _Mode(SimpleNamespace):
    """Имитация NavigationDisplayMode: у enum-значения есть .name."""


def _mode(name: str) -> _Mode:
    return _Mode(name=name)


class SidebarIntentControllerTests(unittest.TestCase):
    def _controller(self, *, intent: bool, last_saved=None):
        from ui.navigation.sidebar_intent import SidebarIntentController

        return SidebarIntentController(intent=intent, last_saved=last_saved)

    def _classify_after_user_toggle(self, controller, mode, *, window_width: int, threshold: int = 700):
        controller.note_user_toggle(100.0)
        return controller.classify_display_mode_change(
            mode,
            window_width=window_width,
            user_initiated=controller.consume_user_toggle(100.2),
            threshold=threshold,
        )

    def test_auto_collapse_on_narrow_window_does_not_change_intent(self) -> None:
        controller = self._controller(intent=True)

        result = self._classify_after_user_toggle(controller, _mode("COMPACT"), window_width=680)

        self.assertIsNone(result)
        self.assertTrue(controller.intent)

    def test_user_collapse_on_wide_window_changes_intent(self) -> None:
        controller = self._controller(intent=True)

        result = self._classify_after_user_toggle(controller, _mode("COMPACT"), window_width=900)

        self.assertFalse(result)
        self.assertFalse(controller.intent)

    def test_user_expand_on_wide_window_changes_intent(self) -> None:
        controller = self._controller(intent=False)

        result = self._classify_after_user_toggle(controller, _mode("EXPAND"), window_width=900)

        self.assertTrue(result)
        self.assertTrue(controller.intent)

    def test_minimal_mode_is_treated_as_collapse(self) -> None:
        controller = self._controller(intent=True)

        result = self._classify_after_user_toggle(controller, _mode("MINIMAL"), window_width=900)

        self.assertFalse(result)

    def test_menu_overlay_transitions_are_ignored(self) -> None:
        controller = self._controller(intent=False)

        result = self._classify_after_user_toggle(controller, _mode("MENU"), window_width=680)

        self.assertIsNone(result)
        self.assertFalse(controller.intent)

    def test_expand_on_narrow_window_is_ignored(self) -> None:
        controller = self._controller(intent=False)

        result = self._classify_after_user_toggle(controller, _mode("EXPAND"), window_width=680)

        self.assertIsNone(result)
        self.assertFalse(controller.intent)

    def test_unchanged_intent_produces_no_save(self) -> None:
        controller = self._controller(intent=True)

        result = self._classify_after_user_toggle(controller, _mode("EXPAND"), window_width=900)

        self.assertIsNone(result)

    def test_events_during_programmatic_apply_are_ignored(self) -> None:
        controller = self._controller(intent=True)
        controller.applying = True

        result = self._classify_after_user_toggle(controller, _mode("COMPACT"), window_width=900)

        self.assertIsNone(result)
        self.assertTrue(controller.intent)

    def test_raw_string_display_mode_is_accepted(self) -> None:
        controller = self._controller(intent=True)

        result = self._classify_after_user_toggle(controller, "compact", window_width=900)

        self.assertFalse(result)

    def test_programmatic_collapse_without_recent_toggle_is_ignored(self) -> None:
        # Регрессия: применение maximized на старте сворачивало панель, COMPACT
        # эмитился уже на широком окне и затирал сохранённое «развёрнуто».
        controller = self._controller(intent=True)

        result = controller.classify_display_mode_change(
            _mode("COMPACT"),
            window_width=1920,
            user_initiated=controller.consume_user_toggle(100.0),
            threshold=700,
        )

        self.assertIsNone(result)
        self.assertTrue(controller.intent)

    def test_stale_user_toggle_does_not_classify_late_transition(self) -> None:
        from ui.navigation.sidebar_intent import USER_TOGGLE_INTENT_WINDOW_S

        controller = self._controller(intent=True)
        controller.note_user_toggle(100.0)

        user_initiated = controller.consume_user_toggle(
            100.0 + USER_TOGGLE_INTENT_WINDOW_S + 0.1
        )
        result = controller.classify_display_mode_change(
            _mode("COMPACT"),
            window_width=900,
            user_initiated=user_initiated,
            threshold=700,
        )

        self.assertFalse(user_initiated)
        self.assertIsNone(result)
        self.assertTrue(controller.intent)

    def test_user_toggle_is_consumed_by_only_one_display_mode_change(self) -> None:
        controller = self._controller(intent=True)
        controller.note_user_toggle(100.0)

        first = controller.consume_user_toggle(100.1)
        second = controller.consume_user_toggle(100.2)

        self.assertTrue(first)
        self.assertFalse(second)

    def test_menu_transition_consumes_toggle_before_later_programmatic_collapse(self) -> None:
        controller = self._controller(intent=True)
        controller.note_user_toggle(100.0)

        menu_result = controller.classify_display_mode_change(
            _mode("MENU"),
            window_width=680,
            user_initiated=controller.consume_user_toggle(100.1),
            threshold=700,
        )
        collapse_result = controller.classify_display_mode_change(
            _mode("COMPACT"),
            window_width=900,
            user_initiated=controller.consume_user_toggle(100.2),
            threshold=700,
        )

        self.assertIsNone(menu_result)
        self.assertIsNone(collapse_result)
        self.assertTrue(controller.intent)

    def test_reapply_expand_only_when_wide_collapsed_and_intended(self) -> None:
        controller = self._controller(intent=True)

        self.assertTrue(
            controller.should_reapply_expand(window_width=900, is_collapsed=True, threshold=700)
        )
        self.assertFalse(
            controller.should_reapply_expand(window_width=680, is_collapsed=True, threshold=700)
        )
        self.assertFalse(
            controller.should_reapply_expand(window_width=900, is_collapsed=False, threshold=700)
        )

        controller.intent = False
        self.assertFalse(
            controller.should_reapply_expand(window_width=900, is_collapsed=True, threshold=700)
        )

        controller.intent = True
        controller.applying = True
        self.assertFalse(
            controller.should_reapply_expand(window_width=900, is_collapsed=True, threshold=700)
        )

    def test_pending_flush_ignores_worker_saved_state(self) -> None:
        # Сигналу saved асинхронного воркера сознательно не доверяем:
        # flush при выходе пишет намерение, даже если last_saved совпадает.
        controller = self._controller(intent=True, last_saved=True)
        self.assertTrue(controller.pending_flush())

    def test_pending_flush_deduplicates_after_mark_flushed(self) -> None:
        controller = self._controller(intent=True, last_saved=True)

        controller.mark_flushed(True)
        self.assertIsNone(controller.pending_flush())

        self._classify_after_user_toggle(controller, _mode("COMPACT"), window_width=900)
        self.assertFalse(controller.pending_flush())

        controller.mark_flushed(False)
        self.assertIsNone(controller.pending_flush())

    def test_pending_flush_with_unknown_saved_state_reports_intent(self) -> None:
        controller = self._controller(intent=True, last_saved=None)

        self.assertTrue(controller.pending_flush())


class _FakeSignal:
    def __init__(self) -> None:
        self.callback = None

    def connect(self, callback) -> None:
        self.callback = callback

    def emit(self, value=None) -> None:
        if self.callback is not None:
            self.callback(value)


class _FakeMenuButton:
    def __init__(self) -> None:
        self.event_filter = None

    def installEventFilter(self, event_filter) -> None:  # noqa: N802 (Qt API)
        self.event_filter = event_filter


class SidebarBuilderIntentBindingTests(unittest.TestCase):
    def _make_window(self, *, intent: bool, width: int, threshold: int = 700):
        from ui.navigation.sidebar_intent import SidebarIntentController

        panel = SimpleNamespace(
            minimumExpandWidth=threshold,
            isCollapsed=lambda: True,
            menuButton=_FakeMenuButton(),
        )
        nav = SimpleNamespace(
            displayModeChanged=_FakeSignal(),
            panel=panel,
            expand=Mock(),
        )
        session = SimpleNamespace(
            sidebar_intent_controller=SidebarIntentController(intent=intent, last_saved=intent),
        )
        window = SimpleNamespace(
            ui_session=session,
            navigationInterface=nav,
            width=lambda: width,
        )
        return window, nav, session

    def _click_menu_button(self, nav) -> None:
        from PyQt6.QtCore import QEvent

        event_filter = nav.panel.menuButton.event_filter
        self.assertIsNotNone(event_filter)
        event_filter.eventFilter(
            nav.panel.menuButton,
            QEvent(QEvent.Type.MouseButtonRelease),
        )

    def test_responsive_collapse_does_not_start_save_worker(self) -> None:
        import ui.navigation.sidebar_builder as sidebar_builder

        window, nav, session = self._make_window(intent=True, width=680)
        sidebar_builder._bind_sidebar_expanded_state(window)

        with (
            patch.object(sidebar_builder, "_start_sidebar_expanded_save_worker") as start_worker,
            patch.object(sidebar_builder.QTimer, "singleShot"),
        ):
            nav.displayModeChanged.emit(_mode("COMPACT"))

        start_worker.assert_not_called()
        self.assertTrue(session.sidebar_intent_controller.intent)

    def test_user_collapse_on_wide_window_starts_save_worker(self) -> None:
        import ui.navigation.sidebar_builder as sidebar_builder

        window, nav, session = self._make_window(intent=True, width=900)
        sidebar_builder._bind_sidebar_expanded_state(window)

        with patch.object(sidebar_builder, "_start_sidebar_expanded_save_worker") as start_worker:
            self._click_menu_button(nav)
            nav.displayModeChanged.emit(_mode("COMPACT"))

        start_worker.assert_called_once_with(window, False)
        self.assertFalse(session.sidebar_intent_controller.intent)

    def test_real_qfluent_expand_signal_after_user_activation_is_saved(self) -> None:
        """Регрессия: qfluentwidgets эмитит EXPAND внутри clicked -> toggle()."""
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QApplication, QWidget
        from qfluentwidgets import NavigationInterface

        import ui.navigation.sidebar_builder as sidebar_builder
        from ui.navigation.sidebar_intent import SidebarIntentController

        app = QApplication.instance() or QApplication([])
        window = QWidget()
        window.resize(900, 600)
        nav = NavigationInterface(window)
        nav.resize(900, 600)
        nav.setMinimumExpandWidth(700)
        window.navigationInterface = nav
        window.ui_session = SimpleNamespace(
            sidebar_intent_controller=SidebarIntentController(
                intent=False,
                last_saved=False,
            ),
        )
        window.show()
        app.processEvents()
        self.addCleanup(window.deleteLater)

        sidebar_builder._bind_sidebar_expanded_state(window)

        with patch.object(
            sidebar_builder,
            "_start_sidebar_expanded_save_worker",
        ) as start_worker:
            QTest.mouseClick(nav.panel.menuButton, Qt.MouseButton.LeftButton)
            app.processEvents()

        start_worker.assert_called_once_with(window, True)
        self.assertTrue(window.ui_session.sidebar_intent_controller.intent)

    def test_menu_overlay_close_does_not_start_save_worker(self) -> None:
        import ui.navigation.sidebar_builder as sidebar_builder

        window, nav, session = self._make_window(intent=True, width=680)
        sidebar_builder._bind_sidebar_expanded_state(window)

        with (
            patch.object(sidebar_builder, "_start_sidebar_expanded_save_worker") as start_worker,
            patch.object(sidebar_builder.QTimer, "singleShot"),
        ):
            self._click_menu_button(nav)
            nav.displayModeChanged.emit(_mode("MENU"))
            nav.displayModeChanged.emit(_mode("COMPACT"))

        start_worker.assert_not_called()
        self.assertTrue(session.sidebar_intent_controller.intent)

    def test_programmatic_collapse_without_click_is_ignored_and_reapplied(self) -> None:
        # Регрессия maximize-на-старте: COMPACT на широком окне без клика по
        # гамбургеру не должен менять намерение и обязан вернуть панель назад.
        import ui.navigation.sidebar_builder as sidebar_builder

        window, nav, session = self._make_window(intent=True, width=1920)
        sidebar_builder._bind_sidebar_expanded_state(window)
        scheduled: list = []

        with (
            patch.object(sidebar_builder, "_start_sidebar_expanded_save_worker") as start_worker,
            patch.object(
                sidebar_builder.QTimer,
                "singleShot",
                side_effect=lambda _delay_ms, callback: scheduled.append(callback),
            ),
        ):
            nav.displayModeChanged.emit(_mode("COMPACT"))

            start_worker.assert_not_called()
            self.assertTrue(session.sidebar_intent_controller.intent)
            self.assertEqual(len(scheduled), 1)

            scheduled[0]()

        nav.expand.assert_called_once_with(False)

    def test_reapply_on_resize_expands_when_intent_is_expanded(self) -> None:
        import ui.navigation.sidebar_builder as sidebar_builder

        window, nav, session = self._make_window(intent=True, width=900)

        self.assertTrue(sidebar_builder.reapply_sidebar_intent_on_resize(window))

        nav.expand.assert_called_once_with(False)
        self.assertFalse(session.sidebar_intent_controller.applying)

    def test_reapply_on_resize_keeps_collapsed_when_intent_is_collapsed(self) -> None:
        import ui.navigation.sidebar_builder as sidebar_builder

        window, nav, _session = self._make_window(intent=False, width=900)

        self.assertFalse(sidebar_builder.reapply_sidebar_intent_on_resize(window))

        nav.expand.assert_not_called()

    def test_reapply_on_resize_does_nothing_on_narrow_window(self) -> None:
        import ui.navigation.sidebar_builder as sidebar_builder

        window, nav, _session = self._make_window(intent=True, width=680)

        self.assertFalse(sidebar_builder.reapply_sidebar_intent_on_resize(window))

        nav.expand.assert_not_called()

    def test_startup_recheck_expands_collapsed_panel_against_intent(self) -> None:
        import ui.navigation.sidebar_builder as sidebar_builder

        window, nav, _session = self._make_window(intent=True, width=900)

        sidebar_builder._recheck_sidebar_intent_after_init(window)

        nav.expand.assert_called_once_with(False)

    def test_startup_recheck_is_noop_when_panel_matches_intent(self) -> None:
        import ui.navigation.sidebar_builder as sidebar_builder

        window, nav, _session = self._make_window(intent=True, width=900)
        nav.panel.isCollapsed = lambda: False

        sidebar_builder._recheck_sidebar_intent_after_init(window)

        nav.expand.assert_not_called()

    def test_restore_on_narrow_window_keeps_intent_without_menu_overlay(self) -> None:
        import ui.navigation.sidebar_builder as sidebar_builder
        from ui.navigation.sidebar_intent import SidebarIntentController
        from ui.navigation.sidebar_state import store_warmed_sidebar_expanded

        controller = SidebarIntentController(intent=True, last_saved=True)
        session = SimpleNamespace(sidebar_intent_controller=controller)
        nav = SimpleNamespace(
            expand=Mock(),
            panel=SimpleNamespace(minimumExpandWidth=700),
        )
        window = SimpleNamespace(
            ui_session=session,
            navigationInterface=nav,
            width=lambda: 680,
        )

        store_warmed_sidebar_expanded(True)
        try:
            sidebar_builder._restore_sidebar_expanded_state(window)
        finally:
            store_warmed_sidebar_expanded(None)

        nav.expand.assert_not_called()
        self.assertTrue(controller.intent)

    def test_restore_guards_controller_against_recording(self) -> None:
        import ui.navigation.sidebar_builder as sidebar_builder
        from ui.navigation.sidebar_intent import SidebarIntentController

        seen_applying = []

        controller = SidebarIntentController(intent=True, last_saved=True)
        session = SimpleNamespace(sidebar_intent_controller=controller)
        nav = SimpleNamespace(
            expand=lambda useAni=True: seen_applying.append(controller.applying),
            panel=SimpleNamespace(minimumExpandWidth=700),
        )
        window = SimpleNamespace(ui_session=session, navigationInterface=nav, width=lambda: 900)

        from ui.navigation.sidebar_state import store_warmed_sidebar_expanded

        store_warmed_sidebar_expanded(True)
        try:
            sidebar_builder._restore_sidebar_expanded_state(window)
        finally:
            store_warmed_sidebar_expanded(None)

        self.assertEqual(seen_applying, [True])
        self.assertFalse(controller.applying)


class SidebarExitFlushTests(unittest.TestCase):
    def test_pending_intent_is_flushed_synchronously(self) -> None:
        from main.window_lifecycle_cleanup import persist_sidebar_state
        from ui.navigation.sidebar_intent import SidebarIntentController

        controller = SidebarIntentController(intent=False, last_saved=True)
        session = SimpleNamespace(sidebar_intent_controller=controller)
        window = SimpleNamespace(ui_session=session)

        with patch("program_settings.public.save_ui_state_settings") as save:
            persist_sidebar_state(window, context="test")

        save.assert_called_once_with({"sidebar_expanded": False})
        self.assertFalse(controller.last_saved)
        self.assertIsNone(controller.pending_flush())

    def test_flush_writes_even_when_worker_reported_saved(self) -> None:
        # Защитная мера: сигналу saved воркера не доверяем — flush пишет всегда.
        from main.window_lifecycle_cleanup import persist_sidebar_state
        from ui.navigation.sidebar_intent import SidebarIntentController

        controller = SidebarIntentController(intent=True, last_saved=True)
        session = SimpleNamespace(sidebar_intent_controller=controller)
        window = SimpleNamespace(ui_session=session)

        with patch("program_settings.public.save_ui_state_settings") as save:
            persist_sidebar_state(window, context="test")

        save.assert_called_once_with({"sidebar_expanded": True})

    def test_flush_skips_second_write_in_same_exit(self) -> None:
        from main.window_lifecycle_cleanup import persist_sidebar_state
        from ui.navigation.sidebar_intent import SidebarIntentController

        controller = SidebarIntentController(intent=True, last_saved=True)
        session = SimpleNamespace(sidebar_intent_controller=controller)
        window = SimpleNamespace(ui_session=session)

        with patch("program_settings.public.save_ui_state_settings") as save:
            persist_sidebar_state(window, context="request_exit")
            persist_sidebar_state(window, context="закрытии")

        save.assert_called_once_with({"sidebar_expanded": True})

    def test_flush_without_session_is_noop(self) -> None:
        from main.window_lifecycle_cleanup import persist_sidebar_state

        with patch("program_settings.public.save_ui_state_settings") as save:
            persist_sidebar_state(SimpleNamespace(), context="test")

        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
