from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QApplication, QWidget

from app.state_store import AppUiState, MainWindowStateStore
from main.window_state_actions import WindowStateActions
from settings import appearance as appearance_settings
from ui import holiday_effects
from ui.holiday_effects import FRAME_INTERVAL_MS, HolidayEffectsManager
from ui.window_appearance_state import apply_garland_enabled, apply_snowflakes_enabled
from ui.window_premium_appearance import WindowPremiumAppearance


class _CountingCard(QWidget):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.paint_count = 0

    def paintEvent(self, event) -> None:
        self.paint_count += 1


class HolidayEffectsPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _shown_host(self, width: int = 640, height: int = 480) -> QWidget:
        host = QWidget()
        host.resize(width, height)
        host.show()
        self._app.processEvents()
        self.addCleanup(host.deleteLater)
        return host

    def _manager(self, host: QWidget) -> HolidayEffectsManager:
        manager = HolidayEffectsManager(host)
        self.addCleanup(manager.cleanup)
        return manager

    def test_effects_live_in_separate_click_through_window(self) -> None:
        host = self._shown_host()
        manager = self._manager(host)
        manager.set_snowflakes_enabled(True)

        overlay = manager.overlay
        self.assertTrue(overlay.isWindow())
        self.assertTrue(overlay.isVisible())
        flags = overlay.windowFlags()
        self.assertTrue(flags & Qt.WindowType.Tool)
        self.assertTrue(flags & Qt.WindowType.WindowTransparentForInput)
        self.assertTrue(flags & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.assertTrue(overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        self.assertTrue(overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        self.assertEqual(overlay.size(), host.size())

    def test_animation_frames_do_not_repaint_host_widgets(self) -> None:
        host = self._shown_host()
        card = _CountingCard(host)
        card.setGeometry(0, 0, 640, 480)
        card.show()
        manager = self._manager(host)
        manager.set_garland_enabled(True)
        manager.set_snowflakes_enabled(True)
        for _ in range(3):
            self._app.processEvents()
        card.paint_count = 0

        for _ in range(10):
            manager._on_frame()
            self._app.processEvents()

        self.assertEqual(card.paint_count, 0)

    def test_single_frame_timer_runs_at_thirty_fps(self) -> None:
        host = self._shown_host()
        manager = self._manager(host)
        manager.set_garland_enabled(True)
        manager.set_snowflakes_enabled(True)

        self.assertEqual(FRAME_INTERVAL_MS, 33)
        self.assertEqual(manager._timer.interval(), FRAME_INTERVAL_MS)
        self.assertTrue(manager.is_running())

    def test_garland_only_frame_repaints_only_garland_band(self) -> None:
        host = self._shown_host(800, 600)
        manager = self._manager(host)
        manager.set_garland_enabled(True)
        dirty = []
        manager.overlay.update = lambda *args: dirty.append(args)

        manager._on_frame()

        self.assertEqual(len(dirty), 1)
        (rect,) = dirty[0]
        self.assertEqual(rect.width(), 800)
        self.assertLessEqual(rect.height(), 60)

    def test_sprites_are_prepared_once_and_reused_between_frames(self) -> None:
        host = self._shown_host()
        manager = self._manager(host)
        manager.set_garland_enabled(True)
        manager.set_snowflakes_enabled(True)
        manager._garland_opacity = manager._snow_opacity = 1.0
        image = QImage(640, 480, QImage.Format.Format_ARGB32_Premultiplied)

        def paint_frame() -> None:
            painter = QPainter(image)
            manager._paint(painter)
            painter.end()

        paint_frame()
        prepared = len(manager._sprites)
        self.assertGreater(prepared, 0)
        with patch.object(holiday_effects, "_render_dot", side_effect=AssertionError("повторная отрисовка")):
            paint_frame()
        self.assertEqual(len(manager._sprites), prepared)

    def test_snow_density_stays_bounded_on_large_windows(self) -> None:
        total = sum(
            holiday_effects._SnowField.target_count(spec, 3840, 2160) for spec in holiday_effects._SNOW_LAYERS
        )
        self.assertLessEqual(total, 150)

    def test_enabled_snow_starts_spread_over_the_whole_window(self) -> None:
        host = self._shown_host(640, 480)
        manager = self._manager(host)
        manager.set_snowflakes_enabled(True)

        flakes = manager._snow.flakes(holiday_effects._FAR)
        self.assertTrue(any(flake.y > 240 for flake in flakes))

    def test_snow_motion_depends_on_time_not_on_frame_count(self) -> None:
        one_step = holiday_effects._SnowField()
        two_steps = holiday_effects._SnowField()
        for field in (one_step, two_steps):
            field.resize(640, 10000)
            field.populate(seed_visible=True)
        two_steps._flakes = {name: [_copy_flake(f) for f in flakes] for name, flakes in one_step._flakes.items()}

        one_step.step(0.1, spawning=True)
        two_steps.step(0.05, spawning=True)
        two_steps.step(0.05, spawning=True)

        for name, flakes in one_step._flakes.items():
            for first, second in zip(flakes, two_steps._flakes[name]):
                self.assertAlmostEqual(first.y, second.y, places=6)

    def test_landing_snow_builds_drift_that_never_exceeds_cap(self) -> None:
        drift = holiday_effects._SnowDrift()
        drift.resize(600)
        for _ in range(2000):
            drift.deposit(300.0, 4.0)
            drift.deposit(3.0, 4.0)
        drift.pixmap(1.0)

        self.assertGreater(drift.height_at(300.0), 5.0)
        for height, cap in zip(drift.heights, drift.caps):
            self.assertLessEqual(height, cap + 1e-6)
            self.assertLessEqual(cap, holiday_effects._SnowDrift.MAX_HEIGHT)

    def test_holiday_effects_do_not_pause_for_ui_work(self) -> None:
        self.assertFalse(hasattr(HolidayEffectsManager, "suspend_for_ui_work"))

    def test_manager_pauses_and_resumes_timer_without_disabling_effects(self) -> None:
        host = self._shown_host()
        manager = self._manager(host)
        manager.set_garland_enabled(True)
        manager.set_snowflakes_enabled(True)
        self.assertTrue(manager.is_running())

        manager.set_animation_active(False)

        self.assertTrue(manager.is_garland_enabled())
        self.assertTrue(manager.is_snowflakes_enabled())
        self.assertFalse(manager.is_running())
        self.assertTrue(manager.overlay.isVisible())

        manager.set_animation_active(True)

        self.assertTrue(manager.is_running())

    def test_overlay_hides_with_host_window_and_returns_on_show(self) -> None:
        host = self._shown_host()
        manager = self._manager(host)
        manager.set_snowflakes_enabled(True)

        host.hide()
        self.assertFalse(manager.overlay.isVisible())
        self.assertFalse(manager.is_running())

        host.show()
        self._app.processEvents()
        self.assertTrue(manager.overlay.isVisible())
        self.assertTrue(manager.is_running())

    def test_overlay_follows_host_resize(self) -> None:
        host = self._shown_host(640, 480)
        manager = self._manager(host)
        manager.set_snowflakes_enabled(True)

        host.resize(900, 700)
        self._app.processEvents()

        self.assertEqual(manager.overlay.size(), host.size())

    def test_disabling_all_effects_hides_overlay_and_frees_resources(self) -> None:
        host = self._shown_host()
        manager = self._manager(host)
        manager.set_garland_enabled(True)
        manager.set_snowflakes_enabled(True)
        manager.set_animation_active(False)

        manager.set_garland_enabled(False)
        manager.set_snowflakes_enabled(False)

        self.assertFalse(manager.overlay.isVisible())
        self.assertFalse(manager.is_running())
        self.assertEqual(manager._snow.flake_count(), 0)
        self.assertEqual(manager._garland.bulbs, [])
        self.assertEqual(len(manager._sprites), 0)

    def test_fade_out_finishes_and_hides_overlay(self) -> None:
        host = self._shown_host()
        manager = self._manager(host)
        manager.set_garland_enabled(True)
        manager._garland_opacity = 1.0

        manager.set_garland_enabled(False)
        self.assertTrue(manager.overlay.isVisible())
        with patch.object(manager._clock, "restart", return_value=100):
            for _ in range(5):
                manager._on_frame()

        self.assertFalse(manager.overlay.isVisible())
        self.assertFalse(manager.is_running())

    def test_cleanup_stops_timer_and_detaches_from_host(self) -> None:
        host = self._shown_host()
        manager = HolidayEffectsManager(host)
        manager.set_snowflakes_enabled(True)

        manager.cleanup()

        self.assertFalse(manager.is_running())
        self.assertFalse(manager.overlay.isVisible())
        host.resize(700, 500)
        host.hide()
        host.show()
        self._app.processEvents()

    def test_disabling_holiday_effects_does_not_create_overlay_manager(self) -> None:
        host = QWidget()
        host.visual_state = SimpleNamespace(holiday_effects=None)

        apply_garland_enabled(host, False)
        apply_snowflakes_enabled(host, False)

        self.assertIsNone(host.visual_state.holiday_effects)

    def _window_actions(self, host, *, animations_enabled: bool) -> WindowStateActions:
        appearance_settings.store_warmed_animations_enabled(animations_enabled)
        appearance_settings.store_warmed_premium_effects(True, True)
        self.addCleanup(appearance_settings.clear_warmed_animations_enabled_cache)
        self.addCleanup(appearance_settings.clear_warmed_premium_effects_cache)
        store = MainWindowStateStore(AppUiState(subscription_known=True, subscription_is_premium=True))
        return WindowStateActions(
            host,
            store,
            WindowPremiumAppearance(window=host, ui_state_store=store, create_reset_worker=Mock()),
        )

    def test_animation_master_disables_existing_holiday_overlays(self) -> None:
        effects = SimpleNamespace(set_garland_enabled=Mock(), set_snowflakes_enabled=Mock())
        host = QWidget()
        host.visual_state = SimpleNamespace(holiday_effects=effects)
        actions = self._window_actions(host, animations_enabled=True)

        with patch("ui.window_appearance_state.apply_window_animation_policy"):
            actions.set_animations_enabled(False)

        effects.set_garland_enabled.assert_called_once_with(False)
        effects.set_snowflakes_enabled.assert_called_once_with(False)

    def test_window_actions_keep_holiday_overlays_off_when_animation_master_is_off(self) -> None:
        effects = SimpleNamespace(set_garland_enabled=Mock(), set_snowflakes_enabled=Mock())
        host = QWidget()
        host.visual_state = SimpleNamespace(holiday_effects=effects)
        actions = self._window_actions(host, animations_enabled=False)

        actions.set_snowflakes_enabled(True)

        effects.set_snowflakes_enabled.assert_called_once_with(False)
        effects.set_garland_enabled.assert_called_once_with(False)


def _copy_flake(flake):
    copy = object.__new__(type(flake))
    for name in type(flake).__slots__:
        setattr(copy, name, getattr(flake, name))
    return copy


if __name__ == "__main__":
    unittest.main()
