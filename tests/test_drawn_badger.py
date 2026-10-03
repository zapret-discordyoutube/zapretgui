from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

import ui.widgets.fun.mascot as mascot_module
from ui.widgets.fun.badger import BLINK_PAUSE_MAX_MS, BLINK_PAUSE_MIN_MS, PAW_REST, DrawnBadger
from ui.widgets.fun.mascot import GESTURE_TOSS, MOOD_ALARM, MOOD_BUSY, MOOD_HAPPY, MOOD_IDLE, MOOD_SAD


class DrawnBadgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._enabled = True
        patcher = mock.patch.object(mascot_module, "are_live_animations_enabled", side_effect=lambda: self._enabled)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _badger(self, *, shown: bool = True) -> tuple[DrawnBadger, QWidget]:
        host = QWidget()
        badger = DrawnBadger(host, size=46)
        QVBoxLayout(host).addWidget(badger)
        self.addCleanup(host.deleteLater)
        if shown:
            host.show()
        return badger, host

    def test_blinks_on_a_rare_single_shot_timer_only_while_visible(self) -> None:
        badger, host = self._badger(shown=False)
        self.assertFalse(badger.is_blink_scheduled())

        host.show()
        self.assertTrue(badger.is_blink_scheduled())
        self.assertTrue(badger._blink_timer.isSingleShot())
        self.assertGreaterEqual(badger._blink_timer.interval(), BLINK_PAUSE_MIN_MS)
        self.assertLessEqual(badger._blink_timer.interval(), BLINK_PAUSE_MAX_MS)

        host.hide()
        self.assertFalse(badger.is_blink_scheduled())

    def test_blink_closes_and_reopens_the_eyes(self) -> None:
        badger, _host = self._badger()
        badger.blink()
        anim = badger._blink_anim
        self.assertEqual(anim.state(), anim.State.Running)

        anim.setCurrentTime(int(anim.duration() * 0.4))
        self.assertAlmostEqual(badger.blink_progress(), 1.0, places=2)
        self.assertLess(badger.eye_openness(), 0.05)

        anim.setCurrentTime(anim.duration())
        self.assertEqual(badger.blink_progress(), 0.0)
        self.assertEqual(badger.eye_openness(), 1.0)

    def test_no_blinking_without_live_animations(self) -> None:
        self._enabled = False
        badger, _host = self._badger()
        badger.blink()
        self.assertNotEqual(badger._blink_anim.state(), badger._blink_anim.State.Running)
        self.assertFalse(badger.is_blink_scheduled())

    def test_paws_follow_mood_and_gestures(self) -> None:
        badger, _host = self._badger(shown=False)
        self.assertEqual(badger.paw_angles(), (PAW_REST, PAW_REST))

        # Спокойное дыхание раскачивает лапы в разные стороны.
        badger._breath = 1.0
        left, right = badger.paw_angles()
        self.assertGreater(left, PAW_REST)
        self.assertLess(right, PAW_REST)
        badger._breath = 0.0

        # Замах: правая лапа (к стене) уходит высоко вверх.
        badger._gesture, badger._t = GESTURE_TOSS, 0.35
        self.assertGreater(badger.paw_angles()[1], 150.0)

        # Радость: обе лапы вверх.
        badger._gesture, badger._t = MOOD_HAPPY, 0.4
        self.assertTrue(all(angle > 120.0 for angle in badger.paw_angles()))

        # Суета: лапы перебирают по очереди.
        badger._gesture, badger._t = MOOD_BUSY, 0.125
        left, right = badger.paw_angles()
        self.assertNotAlmostEqual(left, right)

        badger._gesture, badger._t = "", 0.0
        badger._mood = MOOD_SAD
        self.assertTrue(all(angle < PAW_REST for angle in badger.paw_angles()))
        badger._mood = MOOD_ALARM
        self.assertTrue(all(angle > PAW_REST + 40.0 for angle in badger.paw_angles()))

    def test_eyes_show_the_mood_and_watch_the_wall(self) -> None:
        badger, _host = self._badger(shown=False)
        badger._mood = MOOD_SAD
        self.assertLess(badger.eye_openness(), 0.6)
        badger._mood = MOOD_ALARM
        self.assertGreater(badger.eye_openness(), 1.0)
        self.assertGreater(badger.look_offset(), 0.5)
        badger._mood, badger._gesture = MOOD_IDLE, MOOD_HAPPY
        self.assertEqual(badger.eye_openness(), 0.0)

    def test_breath_repaints_paws_only_on_noticeable_change(self) -> None:
        badger, _host = self._badger(shown=False)
        with mock.patch.object(badger, "update") as update:
            badger.set_breath(0.02)
            update.assert_not_called()
            badger.set_breath(0.5)
            update.assert_called_once()

    def test_paints_every_pose(self) -> None:
        badger, _host = self._badger(shown=False)
        poses = [(MOOD_IDLE, "", 0.0), (MOOD_ALARM, GESTURE_TOSS, 0.4), (MOOD_BUSY, MOOD_BUSY, 0.3),
                 (MOOD_IDLE, MOOD_HAPPY, 0.5), (MOOD_SAD, "", 0.0), (MOOD_ALARM, MOOD_ALARM, 0.5)]
        for mood, gesture, t in poses:
            badger._mood, badger._gesture, badger._t = mood, gesture, t
            image = QPixmap(badger.size())
            image.fill(QColor(0, 0, 0, 0))
            badger.render(image)
            self.assertFalse(image.toImage().isNull())


if __name__ == "__main__":
    unittest.main()
