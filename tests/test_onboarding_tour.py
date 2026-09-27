from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget  # noqa: E402


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class OnboardingFlagSettingsTests(unittest.TestCase):
    def test_flag_defaults_to_not_shown_and_survives_normalize(self) -> None:
        from settings.normalize import normalize_settings
        from settings.schema import build_default_settings

        defaults = build_default_settings()
        self.assertIs(defaults["warnings"]["onboarding_tour_done"], False)

        normalized = normalize_settings({"warnings": {"onboarding_tour_done": True}})
        self.assertIs(normalized["warnings"]["onboarding_tour_done"], True)

    def test_flag_round_trips_through_sqlite_store(self) -> None:
        from settings import store as settings_store

        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(temp_dir)):
                try:
                    settings_store.prepare_settings_database()
                    self.assertFalse(settings_store.get_onboarding_tour_done())
                    settings_store.set_onboarding_tour_done(True)
                    self.assertTrue(settings_store.get_onboarding_tour_done())
                    self.assertTrue(settings_store.read_settings()["warnings"]["onboarding_tour_done"])
                finally:
                    settings_store.close_settings_database()


class OnboardingPostStartupTests(unittest.TestCase):
    def _install(self, *, done: bool, start_results: list[bool]):
        from main import post_startup_onboarding

        _app()
        startup_host = SimpleNamespace(
            startup_post_init_ready=object(),
            startup_state=SimpleNamespace(post_init_ready=True),
            is_alive=Mock(return_value=True),
            start_onboarding_tour=Mock(side_effect=list(start_results)),
        )
        mark_done = Mock()
        delays: list[int] = []

        def _schedule(delay_ms, callback):
            delays.append(int(delay_ms))
            if len(delays) <= len(start_results):
                callback()

        with (
            patch.object(post_startup_onboarding, "bind_startup_gate", side_effect=lambda _s, cb, **_k: cb()),
            patch.object(post_startup_onboarding, "schedule_after", side_effect=_schedule),
            patch.object(post_startup_onboarding, "enqueue_subsystem_task", side_effect=lambda _q, _n, target: target()),
            patch.object(post_startup_onboarding, "_read_tour_done", return_value=done),
            patch.object(post_startup_onboarding, "_mark_tour_done", mark_done),
            patch.object(post_startup_onboarding, "log"),
        ):
            post_startup_onboarding.install_onboarding_tour(startup_host)
        return startup_host, mark_done, delays

    def test_tour_is_not_shown_again_when_flag_is_set(self) -> None:
        startup_host, mark_done, delays = self._install(done=True, start_results=[True])

        startup_host.start_onboarding_tour.assert_not_called()
        mark_done.assert_not_called()
        self.assertEqual(delays, [])

    def test_first_start_shows_tour_and_remembers_it(self) -> None:
        startup_host, mark_done, _delays = self._install(done=False, start_results=[True])

        startup_host.start_onboarding_tour.assert_called_once_with()
        mark_done.assert_called_once_with()

    def test_waits_while_window_is_not_ready_and_marks_only_after_show(self) -> None:
        startup_host, mark_done, delays = self._install(done=False, start_results=[False, False, True])

        self.assertEqual(startup_host.start_onboarding_tour.call_count, 3)
        mark_done.assert_called_once_with()
        self.assertEqual(len(delays), 3)


class _FakePage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.start_btn = QPushButton("Запустить", self)
        layout.addWidget(self.start_btn)

    def onboarding_target(self, name):
        return self.start_btn if name == "start" else None


def _build_window():
    window = QWidget()
    window.resize(900, 640)
    layout = QVBoxLayout(window)
    page = _FakePage(window)
    layout.addWidget(page)

    class Host:
        def show_page(self, _name, allow_internal=False):
            return True

        def get_loaded_page(self, _name):
            return page

        def current_page(self):
            return page

    window.ui_session = SimpleNamespace(
        nav_items={},
        nav_header_by_group={},
        nav_headers=[],
        page_host=Host(),
    )
    return window, page


class OnboardingOverlayTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()

    def _overlay(self, steps):
        from app.page_names import PageName
        from ui.onboarding.overlay import OnboardingOverlay
        from ui.onboarding.steps import TourContext

        window, page = _build_window()
        window.show()
        context = TourContext(
            window=window,
            control_page_name=PageName.ZAPRET2_MODE_CONTROL,
            pages={"control": PageName.ZAPRET2_MODE_CONTROL},
            current_page=page,
        )
        overlay = OnboardingOverlay(window, context, steps)
        return window, overlay

    def test_steps_without_targets_are_skipped(self) -> None:
        from ui.onboarding.steps import TourStep, _page_target

        steps = (
            TourStep("welcome", hero=True),
            TourStep("start", _page_target("start"), page="control"),
            TourStep("missing", _page_target("nothing"), page="control"),
            TourStep("user_presets", _page_target("presets_list"), page="user_presets"),
            TourStep("finish", _page_target("tour_card"), page="control", target_optional=True),
        )
        window, overlay = self._overlay(steps)
        try:
            self.assertTrue(overlay.start())
            # Страницы user_presets в этом режиме нет — шаг убран сразу.
            self.assertEqual(overlay.step_keys(), ["welcome", "start", "missing", "finish"])
            self.assertEqual(overlay.current_step_key(), "welcome")
            overlay.go_next()
            self.assertEqual(overlay.current_step_key(), "start")
            overlay.go_next()
            # Цели «missing» на странице нет — шаг пропущен.
            self.assertEqual(overlay.current_step_key(), "finish")
            overlay.go_back()
            self.assertEqual(overlay.current_step_key(), "start")
        finally:
            window.close()
            window.deleteLater()

    def test_overlay_is_child_of_window_not_separate_window(self) -> None:
        from ui.onboarding.steps import TourStep

        window, overlay = self._overlay((TourStep("welcome", hero=True),))
        try:
            overlay.start()
            self.assertFalse(overlay.isWindow())
            self.assertIs(overlay.parentWidget(), window)
            self.assertFalse(any(child.isWindow() for child in overlay.findChildren(QWidget)))
        finally:
            window.close()
            window.deleteLater()

    def test_skip_finishes_tour(self) -> None:
        from ui.onboarding.steps import TourStep

        window, overlay = self._overlay((TourStep("welcome", hero=True), TourStep("how", hero=True)))
        finished = Mock()
        overlay.finished.connect(finished)
        try:
            overlay.start()
            overlay.finish("skipped", immediate=True)
            finished.assert_called_once_with("skipped")
        finally:
            window.close()
            window.deleteLater()


class OnboardingKeyboardTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()

    def _running_overlay(self):
        from PyQt6.QtTest import QTest

        from app.page_names import PageName
        from ui.onboarding.overlay import OnboardingOverlay
        from ui.onboarding.steps import TourContext, TourStep, _page_target

        window, page = _build_window()
        window.show()
        window.activateWindow()
        QTest.qWaitForWindowActive(window, 1000)
        context = TourContext(
            window=window,
            control_page_name=PageName.ZAPRET2_MODE_CONTROL,
            pages={"control": PageName.ZAPRET2_MODE_CONTROL},
            current_page=page,
        )
        steps = (
            TourStep("welcome", hero=True),
            TourStep("start", _page_target("start"), page="control"),
            TourStep("finish", hero=True),
        )
        overlay = OnboardingOverlay(window, context, steps)
        overlay.start()
        QApplication.processEvents()
        return window, page, overlay

    def test_arrow_keys_page_through_steps_while_card_button_has_focus(self) -> None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        window, _page, overlay = self._running_overlay()
        try:
            focus = QApplication.focusWidget()
            self.assertTrue(focus is not None and overlay.isAncestorOf(focus))
            QTest.keyClick(focus, Qt.Key.Key_Right)
            self.assertEqual(overlay.current_step_key(), "start")
            QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Left)
            self.assertEqual(overlay.current_step_key(), "welcome")
        finally:
            window.close()
            window.deleteLater()

    def test_tab_does_not_leave_the_tour_card(self) -> None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        window, page, overlay = self._running_overlay()
        try:
            overlay.go_next()
            for _ in range(6):
                QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
                focus = QApplication.focusWidget()
                self.assertTrue(focus is not None and overlay.isAncestorOf(focus))
                self.assertFalse(page.isAncestorOf(focus))
        finally:
            window.close()
            window.deleteLater()

    def test_backdrop_grab_does_not_break_a_click_in_progress(self) -> None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        window, _page, overlay = self._running_overlay()
        try:
            button = overlay._card.next_button
            QTest.mousePress(button, Qt.MouseButton.LeftButton)
            overlay._refresh_blur()
            QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
            self.assertEqual(overlay.current_step_key(), "start")
            self.assertTrue(overlay.isVisible())
        finally:
            window.close()
            window.deleteLater()


class StartOnboardingTourTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()

    def test_automatic_start_waits_for_visible_window(self) -> None:
        from ui.onboarding import start_onboarding_tour

        window, _page = _build_window()
        try:
            self.assertFalse(start_onboarding_tour(window, automatic=True))
        finally:
            window.deleteLater()

    def test_manual_replay_starts_and_second_call_reuses_running_tour(self) -> None:
        from ui.onboarding import find_onboarding_overlay, start_onboarding_tour

        window, _page = _build_window()
        window.show()
        try:
            self.assertTrue(start_onboarding_tour(window))
            overlay = find_onboarding_overlay(window)
            self.assertIsNotNone(overlay)
            self.assertTrue(start_onboarding_tour(window))
            self.assertIs(find_onboarding_overlay(window), overlay)
        finally:
            window.close()
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
