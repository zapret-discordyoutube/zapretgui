from __future__ import annotations

import os
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

import ui.widgets.shimmer_label as shimmer_module
from ui.widgets.shimmer_label import ShimmerLabel


def _wait(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QApplication.processEvents()


def _ink_box(image) -> tuple[int, int, int, int] | None:
    background = image.pixel(0, 0)
    xs: list[int] = []
    ys: list[int] = []
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixel(x, y) != background:
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _max_channel_diff(first, second) -> int:
    worst = 0
    for y in range(first.height()):
        for x in range(first.width()):
            a = QColor.fromRgba(first.pixel(x, y))
            b = QColor.fromRgba(second.pixel(x, y))
            worst = max(
                worst,
                abs(a.red() - b.red()),
                abs(a.green() - b.green()),
                abs(a.blue() - b.blue()),
                abs(a.alpha() - b.alpha()),
            )
    return worst


class ShimmerLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        patcher = mock.patch.object(shimmer_module, "are_live_animations_enabled", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.host = QWidget()
        self.addCleanup(self.host.deleteLater)
        self.layout = QVBoxLayout(self.host)
        self.host.resize(900, 160)

    def _build_motto(self) -> list[ShimmerLabel]:
        from ui.pages.about_page_help_build import build_about_page_motto_block
        from ui.theme import get_theme_tokens

        wrap = build_about_page_motto_block(tr_fn=lambda _key, default: default, tokens=get_theme_tokens())
        self.layout.addWidget(wrap)
        self.host.show()
        QApplication.processEvents()
        return wrap.findChildren(ShimmerLabel)

    def test_motto_lines_float_in_one_after_another(self) -> None:
        labels = self._build_motto()
        self.assertEqual(len(labels), 3)
        _wait(0.12)

        progress = [label.enter_progress() for label in labels]
        self.assertGreater(progress[0], 0.0)
        self.assertLess(progress[0], 1.0)
        self.assertLess(progress[1], progress[0])
        self.assertEqual(progress[2], 0.0)
        _wait(1.2)
        self.assertEqual([label.enter_progress() for label in labels], [1.0, 1.0, 1.0])

    def test_floating_text_lands_exactly_on_native_text(self) -> None:
        labels = self._build_motto()
        _wait(1.2)
        for label in labels:
            with self.subTest(text=label.text()[:20]):
                native = label.grab().toImage()
                label._refresh_snapshot()
                label._enter = 0.9999999
                label.update()
                QApplication.processEvents()
                landing = label.grab().toImage()
                label._enter = 1.0
                # Буквы на тех же местах; разница только в округлении прозрачности.
                self.assertEqual(_ink_box(native), _ink_box(landing))
                self.assertLessEqual(_max_channel_diff(native, landing), 2)

    def test_sheen_loops_over_the_text(self) -> None:
        label = ShimmerLabel("keep thinking", period_ms=2400, first_delay_ms=0)
        self.layout.addWidget(label)
        self.host.show()
        _wait(0.3)
        self.assertTrue(label.is_sweeping())
        label.grab()
        _wait(shimmer_module.SWEEP_DURATION_MS / 1000 + 0.1)
        self.assertFalse(label.is_sweeping())
        _wait(0.8)
        self.assertTrue(label.is_sweeping())

    def test_nothing_moves_when_live_animations_are_off(self) -> None:
        with mock.patch.object(shimmer_module, "are_live_animations_enabled", return_value=False):
            labels = self._build_motto()
            _wait(1.2)
            self.assertEqual([label.enter_progress() for label in labels], [1.0, 1.0, 1.0])
            self.assertFalse(any(label.is_sweeping() for label in labels))

    def test_hiding_stops_everything(self) -> None:
        labels = self._build_motto()
        _wait(0.1)
        self.host.hide()
        QApplication.processEvents()
        for label in labels:
            self.assertEqual(label.enter_progress(), 1.0)
            self.assertFalse(label.is_sweeping())
            self.assertFalse(label._pause.isActive())


if __name__ == "__main__":
    unittest.main()
