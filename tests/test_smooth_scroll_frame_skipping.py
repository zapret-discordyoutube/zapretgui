from __future__ import annotations

import os
import unittest
from collections import deque
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QListWidget
from qfluentwidgets.common.smooth_scroll import FixedStepSmoothScrollEngine, SmoothMode

import ui.smooth_scroll as smooth_scroll
from ui.smooth_scroll import _FRAME_SKIP_MAX_STEPS, install_frame_skipping


INTERVAL = 1.0 / 60


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def advance_ms(self, milliseconds: float) -> None:
        self.now += milliseconds / 1000.0

    def __call__(self) -> float:
        return self.now


def _library_total_delta(engine) -> float:
    """Один тик исходного движка qfluentwidgets (эталон для сравнения)."""
    total = 0
    for item in engine.stepsLeftQueue:
        total += engine._subDelta(item[0], item[1])
        item[1] -= 1
    while engine.stepsLeftQueue and engine.stepsLeftQueue[0][1] == 0:
        engine.stepsLeftQueue.popleft()
    return total


class FrameSkippingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.view = QListWidget()
        self.addCleanup(self.view.deleteLater)
        # Своя копия класса: глобальный движок библиотеки остаётся как был.
        self.engine_cls = type("Engine", (FixedStepSmoothScrollEngine,), {})
        install_frame_skipping(self.engine_cls)
        self.clock = _Clock()
        patcher = mock.patch.object(smooth_scroll.time, "monotonic", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _engine(self, deltas=(180.0,), steps: int = 24):
        engine = self.engine_cls(self.view)
        engine.smoothMode = SmoothMode.COSINE
        engine.stepsTotal = steps
        engine.stepsLeftQueue = deque([[delta, steps] for delta in deltas])
        return engine

    def _reference(self, deltas=(180.0,), steps: int = 24):
        engine = FixedStepSmoothScrollEngine(self.view)
        engine.smoothMode = SmoothMode.COSINE
        engine.stepsTotal = steps
        engine.stepsLeftQueue = deque([[delta, steps] for delta in deltas])
        return engine

    def test_on_time_frames_move_one_step_each_exactly_as_before(self) -> None:
        engine, reference = self._engine(), self._reference()
        for tick in range(24):
            self.clock.advance_ms(1000 / 60)
            self.assertAlmostEqual(engine._getTotalDelta(), _library_total_delta(reference), places=9, msg=f"tick {tick}")
        self.assertFalse(engine.stepsLeftQueue)

    def test_late_frames_catch_up_to_the_same_place_in_the_animation(self) -> None:
        engine, reference = self._engine(), self._reference()
        moved = reference_moved = 0.0
        self.clock.advance_ms(1000 / 60)
        moved += engine._getTotalDelta()          # первый тик — всегда один шаг
        reference_moved += _library_total_delta(reference)
        for steps_late in (3, 2, 5, 1, 4):
            self.clock.advance_ms(steps_late * 1000 / 60)
            moved += engine._getTotalDelta()
            for _ in range(steps_late):
                reference_moved += _library_total_delta(reference)
            self.assertAlmostEqual(moved, reference_moved, places=9)

    def test_whole_animation_covers_the_same_distance_with_fewer_frames(self) -> None:
        engine, reference = self._engine((180.0, 90.0)), self._reference((180.0, 90.0))
        expected = 0.0
        while reference.stepsLeftQueue:
            expected += _library_total_delta(reference)

        moved, frames = 0.0, 0
        while engine.stepsLeftQueue:
            self.clock.advance_ms(50)             # кадр в три раза тяжелее тика
            moved += engine._getTotalDelta()
            frames += 1
            self.assertLess(frames, 100)
        self.assertAlmostEqual(moved, expected, places=9)
        self.assertLessEqual(frames, 12)          # вместо 24

    def test_catch_up_is_capped(self) -> None:
        engine, reference = self._engine(steps=60), self._reference(steps=60)
        self.clock.advance_ms(1000 / 60)
        engine._getTotalDelta()
        _library_total_delta(reference)

        self.clock.advance_ms(2000)               # окно «зависло» на две секунды
        moved = engine._getTotalDelta()
        expected = sum(_library_total_delta(reference) for _ in range(_FRAME_SKIP_MAX_STEPS))
        self.assertAlmostEqual(moved, expected, places=9)
        self.assertTrue(engine.stepsLeftQueue)

    def test_new_scroll_after_a_pause_starts_with_one_step(self) -> None:
        engine = self._engine(steps=2)
        engine.smoothMoveTimer.stop()
        for _ in range(2):
            self.clock.advance_ms(1000 / 60)
            engine._smoothMove()
        self.assertFalse(engine.stepsLeftQueue)
        self.assertIsNone(engine.__dict__.get("_zapret_last_tick"))

        self.clock.advance_ms(30_000)             # человек не крутил колесо полминуты
        engine.stepsLeftQueue.append([120.0, 24])
        reference = self._reference((120.0,))
        self.assertAlmostEqual(engine._getTotalDelta(), _library_total_delta(reference), places=9)

    def test_installing_twice_does_not_stack_the_patch(self) -> None:
        engine_cls = type("Engine2", (FixedStepSmoothScrollEngine,), {})
        install_frame_skipping(engine_cls)
        first = engine_cls._getTotalDelta
        install_frame_skipping(engine_cls)
        self.assertIs(engine_cls._getTotalDelta, first)

    def test_global_policy_patches_the_library_engine(self) -> None:
        smooth_scroll.install_global_smooth_scroll_policy()
        self.assertTrue(FixedStepSmoothScrollEngine.__dict__.get("_zapret_frame_skipping"))


if __name__ == "__main__":
    unittest.main()
