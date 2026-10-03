from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QWidget

import ui.widgets.icon_loop as loop_module
import ui.widgets.motion_icon as motion_module
from presets.ui.control.quick_actions import build_quick_actions
from presets.ui.control.top_summary_widget import ControlTopSummaryWidget
from ui.widgets.icon_loop import LOOP_STEP_MS, icon_loop_conductor
from ui.widgets.line_icons import ANIMATED_LINE_ICONS, paint_line_icon
from ui.widgets.motion_icon import GESTURE_LOOP, MotionIcon


def _frame(name: str, t: float) -> QImage:
    image = QImage(48, 48, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    paint_line_icon(painter, name, QRectF(0, 0, 48, 48), QColor("#3ee0e8"), t)
    painter.end()
    return image


class LineIconMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_every_animated_icon_moves_and_returns_to_rest(self) -> None:
        for name in sorted(ANIMATED_LINE_ICONS):
            with self.subTest(icon=name):
                rest = _frame(name, 0.0)
                self.assertTrue(any(_frame(name, t) != rest for t in (0.25, 0.5, 0.75)))
                # Конец жеста совпадает с покоем: зацикливание без рывка.
                self.assertEqual(_frame(name, 1.0), rest)


class IconLoopConductorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._enabled = True
        for module in (loop_module, motion_module):
            patcher = mock.patch.object(module, "are_live_animations_enabled", side_effect=lambda: self._enabled)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.host = QWidget()
        self.addCleanup(self.host.deleteLater)
        layout = QHBoxLayout(self.host)
        self.icons = []
        for name in ("preset", "profiles", "mode"):
            icon = MotionIcon(self.host, size=24)
            icon.set_line_icon(name, color="#3ee0e8", size=24)
            layout.addWidget(icon)
            self.icons.append(icon)
        self.conductor = icon_loop_conductor(self.host)
        for icon in self.icons:
            self.conductor.register(icon)

    def test_one_conductor_per_host(self) -> None:
        self.assertIs(icon_loop_conductor(self.host), self.conductor)

    def test_sleeps_while_host_is_hidden(self) -> None:
        self.assertFalse(self.conductor.is_running())
        self.host.show()
        self.assertTrue(self.conductor.is_running())
        self.assertEqual(self.conductor._timer.interval(), LOOP_STEP_MS)
        self.host.hide()
        self.assertFalse(self.conductor.is_running())

    def test_icons_play_one_at_a_time_in_turn(self) -> None:
        self.host.show()
        played = []
        for _ in range(3):
            self.conductor._step()
            playing = [icon for icon in self.icons if icon.gesture() == GESTURE_LOOP]
            self.assertEqual(len(playing), 1)
            played.append(playing[0])
            playing[0]._anim.stop()
            playing[0]._on_finished()
        self.assertEqual(played, self.icons)

    def test_no_gesture_when_animations_are_off(self) -> None:
        self.host.show()
        self._enabled = False
        self.conductor._step()
        self.assertTrue(all(icon.gesture() == "" for icon in self.icons))
        # Таймер редкий и ждёт, когда анимации снова включат.
        self.assertTrue(self.conductor.is_running())

    def test_icon_without_own_gesture_is_skipped(self) -> None:
        star = MotionIcon(self.host, size=24)
        star.set_line_icon("star", color="#f5c542", size=24)
        self.assertFalse(star.can_loop())
        plain = MotionIcon(self.host, size=24)
        self.assertFalse(plain.can_loop())

    def test_loop_frame_is_painted_by_lines(self) -> None:
        self.host.show()
        icon = self.icons[0]
        self.assertTrue(icon.play_loop())
        self.assertFalse(icon.play_loop())
        icon._anim.setCurrentTime(icon._anim.duration() // 2)
        image = QImage(icon.size(), QImage.Format.Format_ARGB32)
        image.fill(0)
        icon.render(image)
        self.assertFalse(image.isNull())


class HomePageIconLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_summary_and_quick_actions_share_one_queue(self) -> None:
        content = QWidget()
        self.addCleanup(content.deleteLater)
        summary = ControlTopSummaryWidget(language="ru", mode_value="Zapret 2", parent=content)
        noop = lambda: None  # noqa: E731
        actions = build_quick_actions(
            tr_fn=lambda _key, default: default,
            text_prefix="page.winws2_control",
            on_open_onboarding_tour=noop,
            on_open_connection_test=noop,
            on_open_internet_cleanup=noop,
            on_open_folder=noop,
            on_open_docs=noop,
            parent=content,
        )
        conductor = icon_loop_conductor(content)
        icons = conductor.icons()

        self.assertEqual(len(icons), 3 + 5)
        self.assertIn(summary.preset_item._icon_label, icons)
        self.assertNotIn(summary.premium_item._icon_label, icons)
        self.assertIn(actions.docs_card.icon_widget(), icons)


if __name__ == "__main__":
    unittest.main()
