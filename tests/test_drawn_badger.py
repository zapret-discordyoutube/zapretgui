from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

import ui.widgets.fun.mascot as mascot_module
from ui.widgets.fun.badger import BLINK_PAUSE_MAX_MS, BLINK_PAUSE_MIN_MS, DrawnBadger
from ui.widgets.fun.logo_badger import BadgerPose, paint_logo_badger
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

    def test_parts_follow_mood_and_gestures(self) -> None:
        badger, _host = self._badger(shown=False)
        rest = badger.logo_pose()
        self.assertEqual((rest.paw, rest.jaw, rest.ear, rest.bolt_glow), (0.0, 0.0, 0.0, 0.0))

        # Спокойное дыхание покачивает лапу с молнией и чуть приоткрывает пасть.
        badger._breath = 1.0
        self.assertGreater(badger.logo_pose().paw, 0.0)
        self.assertGreater(badger.logo_pose().jaw, 0.0)
        badger._breath = 0.0

        # Бросок: сначала замах вниз, потом рывок вверх, пасть отпускает.
        badger._gesture, badger._t = GESTURE_TOSS, 0.25
        self.assertLess(badger.logo_pose().paw, 0.0)
        badger._t = 0.55
        self.assertGreater(badger.logo_pose().paw, 20.0)
        self.assertGreater(badger.logo_pose().jaw, 0.3)

        # Радость: пасть настежь, молния вверх, по ней бежит блик, глаз жмурится.
        badger._gesture, badger._t = MOOD_HAPPY, 0.5
        happy = badger.logo_pose()
        self.assertGreater(happy.jaw, 0.9)
        self.assertGreater(happy.paw, 20.0)
        self.assertAlmostEqual(happy.bolt_glow, 0.5)
        self.assertGreater(happy.blink, 0.8)

        # Суета: челюсть жуёт — в разные моменты то открыта, то сомкнута.
        badger._gesture = MOOD_BUSY
        badger._t = 0.125
        open_jaw = badger.logo_pose().jaw
        badger._t = 0.375
        self.assertGreater(open_jaw, 0.0)
        self.assertLess(badger.logo_pose().jaw, 0.0)

        # Вздрогнул: ухо прижато, челюсть стиснута.
        badger._gesture, badger._t = MOOD_ALARM, 0.5
        flinch = badger.logo_pose()
        self.assertLess(flinch.ear, -10.0)
        self.assertLess(flinch.jaw, -0.5)

        badger._gesture, badger._t = "", 0.0
        badger._mood = MOOD_SAD
        sad = badger.logo_pose()
        self.assertLess(sad.paw, 0.0)
        self.assertLess(sad.eye_open, 0.6)

    def test_logo_parts_really_move(self) -> None:
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QImage, QPainter

        def frame(pose: BadgerPose) -> QImage:
            image = QImage(100, 100, QImage.Format.Format_ARGB32)
            image.fill(0)
            painter = QPainter(image)
            paint_logo_badger(painter, pose)
            painter.end()
            return image

        rest = frame(BadgerPose())
        for changed in (
            BadgerPose(blink=1.0), BadgerPose(look=1.0), BadgerPose(jaw=1.0), BadgerPose(paw=20.0),
            BadgerPose(ear=-12.0), BadgerPose(bolt_glow=0.5), BadgerPose(eye_open=0.5),
        ):
            with self.subTest(pose=changed):
                self.assertNotEqual(frame(changed), rest)

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


class LogoBadgerShapeCacheTests(unittest.TestCase):
    """Контуры медоеда не зависят от позы и собираются один раз, а не каждый кадр."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    @staticmethod
    def _render(pose: BadgerPose):
        from PyQt6.QtGui import QImage, QPainter

        image = QImage(88, 88, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        painter.scale(0.88, 0.88)
        paint_logo_badger(painter, pose)
        painter.end()
        return image

    def test_paths_are_built_once_for_all_frames(self) -> None:
        import ui.widgets.fun.logo_badger as logo

        paint_logo_badger_warmup = self._render(BadgerPose())
        self.assertFalse(paint_logo_badger_warmup.isNull())
        with mock.patch.object(logo, "_path", wraps=logo._path) as build, mock.patch.object(
            logo, "_smooth", wraps=logo._smooth
        ) as smooth:
            for step in range(30):
                self._render(BadgerPose(blink=step % 2, jaw=step / 30.0, paw=step, ear=-step, bolt_glow=step / 30.0))
        build.assert_not_called()
        smooth.assert_not_called()

    def test_cached_shapes_draw_exactly_like_fresh_ones(self) -> None:
        import ui.widgets.fun.logo_badger as logo

        poses = [
            BadgerPose(),
            BadgerPose(blink=1.0, jaw=0.6, paw=20.0),
            BadgerPose(eye_open=1.18, look=-0.8, ear=-14.0, bolt_glow=0.5),
            BadgerPose(jaw=-0.8, paw=-9.0, bolt_glow=1.0),
        ]
        warm = [self._render(pose) for pose in poses]
        for cached in (
            logo._holes,
            logo._body_shape,
            logo._fold_shape,
            logo._stripe_path,
            logo._ear_inner_path,
            logo._closed_eye_path,
            logo._outer_body,
            logo._fold,
            logo._gap,
            logo._neck,
            logo._head,
            logo._mouth,
            logo._throat,
            logo._jaw,
            logo._bolt,
            logo._paw,
        ):
            cached.cache_clear()
        fresh = [self._render(pose) for pose in poses]
        for index, (a, b) in enumerate(zip(warm, fresh)):
            self.assertEqual(a, b, f"поза {index}: картинка из запаса отличается от свежей")

    def test_drawing_never_changes_shared_paths(self) -> None:
        import ui.widgets.fun.logo_badger as logo

        before = {name: getattr(logo, name)().elementCount() for name in ("_body_shape", "_fold_shape", "_bolt", "_jaw")}
        for step in range(5):
            self._render(BadgerPose(jaw=step / 5.0, paw=10.0 * step, bolt_glow=step / 5.0))
        after = {name: getattr(logo, name)().elementCount() for name in before}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
