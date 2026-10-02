"""Весёлые живые элементы: талисман, салют, тикер, шаги, счётчик."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget

from ui.widgets.fun import CounterBadge, FunTicker, Mascot, StepList, burst_confetti
from ui.widgets.fun.mascot import MOOD_BUSY, MOOD_HAPPY, MOOD_IDLE, MOOD_SAD


def _shown(widget: QWidget) -> QWidget:
    widget.show()
    QApplication.processEvents()
    return widget


class MascotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_moods_play_gestures_and_are_announced(self) -> None:
        mascot = _shown(Mascot(size=32))
        self.addCleanup(mascot.deleteLater)
        with patch("ui.widgets.fun.mascot.are_live_animations_enabled", return_value=True):
            mascot.set_mood(MOOD_BUSY)
            self.assertEqual(mascot.gesture(), MOOD_BUSY)
            self.assertEqual(mascot.property("screenReaderStateText"), "Талисман: работает")
            mascot.set_mood(MOOD_HAPPY)
            self.assertEqual(mascot.gesture(), MOOD_HAPPY)
            mascot.set_mood(MOOD_SAD)
            self.assertEqual(mascot.mood(), MOOD_SAD)

    def test_no_motion_when_live_animations_are_off(self) -> None:
        mascot = _shown(Mascot(size=32))
        self.addCleanup(mascot.deleteLater)
        with patch("ui.widgets.fun.mascot.are_live_animations_enabled", return_value=False):
            mascot.set_mood(MOOD_BUSY)
            self.assertEqual(mascot.gesture(), "")
            mascot.set_mood(MOOD_IDLE)
            self.assertFalse(mascot._look_timer.isActive())

    def test_hidden_mascot_stops_moving(self) -> None:
        mascot = _shown(Mascot(size=32))
        self.addCleanup(mascot.deleteLater)
        with patch("ui.widgets.fun.mascot.are_live_animations_enabled", return_value=True):
            mascot.set_mood(MOOD_BUSY)
            mascot.hide()
        self.assertEqual(mascot.gesture(), "")


class ConfettiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_confetti_respects_animation_switch_and_cleans_up(self) -> None:
        host = _shown(QWidget())
        host.resize(300, 200)
        self.addCleanup(host.deleteLater)
        with patch("ui.widgets.fun.confetti.are_live_animations_enabled", return_value=False):
            self.assertIsNone(burst_confetti(host))
        with patch("ui.widgets.fun.confetti.are_live_animations_enabled", return_value=True):
            layer = burst_confetti(host, seed=1)
        self.assertIsNotNone(layer)
        self.assertTrue(layer.isVisible())
        QTest.qWait(1600)
        self.assertFalse(any(child.isVisible() for child in host.findChildren(type(layer))))


class TickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_ticker_cycles_phrases_without_repeating_and_stops(self) -> None:
        ticker = _shown(FunTicker(seed=3))
        self.addCleanup(ticker.deleteLater)
        ticker.set_phrases(["один", "два", "три"])
        ticker.start()
        self.assertTrue(ticker.is_running())
        seen = [ticker.text()]
        for _ in range(6):
            ticker.next_phrase()
            self.assertNotEqual(ticker.text(), seen[-1])
            seen.append(ticker.text())
        self.assertTrue(set(seen) <= {"один", "два", "три"})
        ticker.stop("готово")
        self.assertFalse(ticker.is_running())
        self.assertEqual(ticker.text(), "готово")

    def test_ticker_shows_every_phrase_once_before_repeating(self) -> None:
        phrases = [f"фраза {index}" for index in range(12)]
        for seed in range(20):
            ticker = FunTicker(seed=seed)
            self.addCleanup(ticker.deleteLater)
            ticker.set_phrases(phrases)
            shown = []
            for _ in range(len(phrases) * 3):
                ticker.next_phrase()
                if shown:
                    self.assertNotEqual(ticker.text(), shown[-1])
                shown.append(ticker.text())
            for start in range(0, len(shown), len(phrases)):
                self.assertCountEqual(shown[start : start + len(phrases)], phrases)

    def test_single_phrase_ticker_keeps_showing_it(self) -> None:
        ticker = FunTicker(seed=1)
        self.addCleanup(ticker.deleteLater)
        ticker.set_phrases(["одна"])
        for _ in range(3):
            ticker.next_phrase()
            self.assertEqual(ticker.text(), "одна")

    def test_empty_ticker_does_not_start(self) -> None:
        ticker = FunTicker()
        self.addCleanup(ticker.deleteLater)
        ticker.start()
        self.assertFalse(ticker.is_running())


class StepsAndCounterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_step_list_updates_status_and_text(self) -> None:
        steps = StepList()
        self.addCleanup(steps.deleteLater)
        steps.reset([("a", "Первый"), ("b", "Второй")])
        steps.set_step("a", "done", "Первый готов")
        self.assertEqual(steps.status("a"), "done")
        self.assertEqual(steps.text("a"), "Первый готов")
        self.assertEqual(steps.status("b"), "pending")
        steps.reset([("c", "Новый")])
        self.assertEqual(steps.keys(), ["c"])

    def test_counter_shows_value(self) -> None:
        badge = CounterBadge("надёжно работают", mark="✓")
        self.addCleanup(badge.deleteLater)
        badge.set_value(2)
        self.assertEqual(badge.value(), 2)
        self.assertEqual(badge.label_text(), "✓ 2  надёжно работают")
        self.assertEqual(badge.property("screenReaderStateText"), "надёжно работают: 2")


if __name__ == "__main__":
    unittest.main()
