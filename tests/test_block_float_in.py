"""Блочное появление: один снимок блока, один слой, общий такт кадров.

Проверяется то, ради чего появление переписано (см. ui.widgets.stagger_float_in):
блок не перерисовывается в полёте, снимается только видимая его часть,
невидимое не выплывает вовсе, а спрятанный на время полёта блок всегда
возвращается на место — даже когда такт кадров стоит.
"""

from __future__ import annotations

import os
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QApplication, QGraphicsOpacityEffect, QLabel, QScrollArea, QVBoxLayout, QWidget

import ui.widgets.stagger_float_in as float_module
from ui.frame_clock import frame_clock
from ui.widgets.stagger_float_in import (
    attach_stagger_float_in,
    float_in,
    float_in_progress_of,
    is_floating_in,
)


def _wait(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QApplication.processEvents()


_FLIGHT_S = float_module.FLOAT_IN_DURATION_MS / 1000.0


class _Card(QWidget):
    """Одноцветная карточка, которая считает свои перерисовки."""

    def __init__(self, color: str, height: int = 60) -> None:
        super().__init__()
        self._color = QColor(color)
        self.paints = 0
        self.setFixedHeight(height)

    def paintEvent(self, event) -> None:  # noqa: N802
        self.paints += 1
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._color)
        painter.end()


class BlockFloatInTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        patcher = mock.patch.object(float_module, "are_live_animations_enabled", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _container(self, *cards: QWidget, size=(320, 400)) -> QWidget:
        container = QWidget()
        self.addCleanup(container.deleteLater)
        layout = QVBoxLayout(container)
        for card in cards:
            layout.addWidget(card)
        layout.addStretch(1)
        container.resize(*size)
        return container

    def test_flight_draws_the_snapshot_and_hides_the_real_block(self) -> None:
        card = _Card("#ff0000")
        container = self._container(card)
        attach_stagger_float_in(container)
        container.show()
        _wait(_FLIGHT_S * 0.5)

        self.assertTrue(is_floating_in(card))
        self.assertIsNone(card.graphicsEffect())
        self.assertFalse(card.mask().isEmpty(), "настоящий блок на время полёта спрятан маской")
        # В середине полёта на месте блока полупрозрачная картинка: красного
        # уже много, но это ещё не сплошной красный.
        pixel = container.grab().toImage().pixelColor(card.geometry().center())
        self.assertGreater(pixel.red(), pixel.green())
        self.assertGreater(pixel.green(), 0)

        _wait(_FLIGHT_S * 0.5 + 0.2)
        self.assertFalse(is_floating_in(card))
        self.assertTrue(card.mask().isEmpty())
        pixel = container.grab().toImage().pixelColor(card.geometry().center())
        self.assertEqual((pixel.red(), pixel.green(), pixel.blue()), (255, 0, 0))

    def test_layer_and_its_objects_are_gone_after_the_flight(self) -> None:
        cards = [_Card("#336699") for _ in range(3)]
        container = self._container(*cards)
        controller = attach_stagger_float_in(container)
        container.show()
        _wait(0.05)
        self.assertIsNotNone(container.findChild(QWidget, "floatInOverlay"))

        _wait(_FLIGHT_S + 3 * float_module.FLOAT_IN_STEP_MS / 1000 + 0.2)
        self.assertFalse(controller.is_running())
        self.assertIsNone(container.findChild(QWidget, "floatInOverlay"))
        self.assertFalse(float_module._ENGINES, "слой появления живёт только во время полёта")
        # Весь полёт — один снимок и одна перерисовка на место; уход слоя
        # не перерисовывает страницу заново.
        self.assertEqual([card.paints for card in cards], [cards[0].paints] * 3)

    def test_landing_does_not_repaint_blocks_that_already_landed(self) -> None:
        cards = [_Card("#336699") for _ in range(3)]
        container = self._container(*cards)
        attach_stagger_float_in(container)
        container.show()
        QApplication.processEvents()
        for card in cards:
            card.paints = 0
        _wait(_FLIGHT_S + 3 * float_module.FLOAT_IN_STEP_MS / 1000 + 0.2)
        self.assertEqual([card.paints for card in cards], [2, 2, 2])

    def test_only_the_visible_part_of_a_tall_block_is_snapshotted(self) -> None:
        # Сетка серверов DNS: 1880 точек в высоту, видно четыре плитки. Раньше
        # эффект снимал её целиком каждые 0,12 с.
        scroll = QScrollArea()
        self.addCleanup(scroll.deleteLater)
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        tall = _Card("#00aa00", height=3000)
        layout.addWidget(tall)
        scroll.setWidget(content)
        attach_stagger_float_in(content)
        scroll.resize(300, 200)
        scroll.show()
        _wait(0.1)

        engine = float_module._engine_of(content, create=False)
        self.assertIsNotNone(engine)
        block = engine.block_of(tall)
        self.assertIsNotNone(block.pixmap)
        self.assertLessEqual(block.pixmap.height(), 200)
        self.assertGreater(block.pixmap.height(), 100)

    def test_row_below_the_window_edge_lands_without_a_flight(self) -> None:
        # Подбор стратегий: 200 строк приходят разом, видно десяток.
        scroll = QScrollArea()
        self.addCleanup(scroll.deleteLater)
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        rows = [_Card("#888888", height=40) for _ in range(30)]
        for row in rows:
            layout.addWidget(row)
        scroll.setWidget(content)
        scroll.resize(300, 200)
        scroll.show()
        QApplication.processEvents()
        for row in rows:
            row.paints = 0
            self.assertTrue(float_in(row))
        _wait(0.08)

        self.assertTrue(is_floating_in(rows[0]))
        self.assertFalse(is_floating_in(rows[-1]))
        self.assertTrue(rows[-1].mask().isEmpty())
        self.assertEqual(rows[-1].paints, 0, "невидимую строку незачем снимать в картинку")

    def test_resized_block_lands_at_once(self) -> None:
        card = _Card("#ff0000")
        container = self._container(card)
        attach_stagger_float_in(container)
        container.show()
        _wait(0.08)
        self.assertTrue(is_floating_in(card))

        card.setFixedHeight(120)
        QApplication.processEvents()
        self.assertFalse(is_floating_in(card), "картинка устарела — блок встаёт на место")
        self.assertTrue(card.mask().isEmpty())

    def test_moved_block_keeps_flying(self) -> None:
        # Выше появилось содержимое: блок сдвинулся, но картинка та же.
        first = _Card("#ff0000")
        second = _Card("#0000ff")
        container = self._container(first, second)
        attach_stagger_float_in(container)
        container.show()
        _wait(0.12)
        self.assertTrue(is_floating_in(second))
        old_top = second.y()

        first.setFixedHeight(100)
        QApplication.processEvents()
        self.assertGreater(second.y(), old_top)
        self.assertTrue(is_floating_in(second))
        engine = float_module._engine_of(container, create=False)
        self.assertEqual(engine.block_of(second).rect.top(), second.y())

    def test_block_lands_even_when_the_frame_clock_is_paused(self) -> None:
        # Сеанс заблокирован или дисплей выключен: такт стоит. Блок не должен
        # остаться спрятанным.
        card = _Card("#ff0000")
        container = self._container(card)
        attach_stagger_float_in(container)
        container.show()
        _wait(0.05)
        self.assertTrue(is_floating_in(card))

        clock = frame_clock()
        clock.set_paused("test", True)
        self.addCleanup(clock.set_paused, "test", False)
        _wait(_FLIGHT_S + float_module._DEADLINE_MARGIN_MS / 1000 + 0.3)
        self.assertFalse(is_floating_in(card))
        self.assertTrue(card.mask().isEmpty())

    def test_nothing_starts_while_the_screen_is_not_seen(self) -> None:
        clock = frame_clock()
        clock.set_paused("test", True)
        self.addCleanup(clock.set_paused, "test", False)
        card = _Card("#ff0000")
        container = self._container(card)
        controller = attach_stagger_float_in(container)
        container.show()
        _wait(0.05)
        self.assertFalse(controller.is_running())
        self.assertTrue(card.mask().isEmpty())

    def test_single_widget_floats_in_with_delay(self) -> None:
        card = _Card("#ff0000")
        container = self._container(card)
        container.show()
        QApplication.processEvents()

        self.assertTrue(float_in(card, delay_ms=150))
        _wait(0.05)
        self.assertEqual(float_in_progress_of(card), 0.0)
        _wait(0.2)
        self.assertGreater(float_in_progress_of(card), 0.0)

    def test_asking_again_restarts_the_flight(self) -> None:
        card = _Card("#ff0000")
        container = self._container(card)
        container.show()
        QApplication.processEvents()
        self.assertTrue(float_in(card))
        _wait(_FLIGHT_S * 0.7)
        first = float_in_progress_of(card)
        self.assertTrue(float_in(card), "повторная просьба не должна отказывать из-за нашей же маски")
        _wait(0.03)
        self.assertLess(float_in_progress_of(card), first)
        _wait(_FLIGHT_S + 0.2)
        self.assertTrue(card.mask().isEmpty())

    def test_widget_without_a_parent_or_with_its_own_effect_is_left_alone(self) -> None:
        loose = QLabel("без родителя")
        self.addCleanup(loose.deleteLater)
        self.assertFalse(float_in(loose))

        card = _Card("#ff0000")
        container = self._container(card)
        card.setGraphicsEffect(QGraphicsOpacityEffect(card))
        container.show()
        QApplication.processEvents()
        self.assertFalse(float_in(card))
        self.assertTrue(card.mask().isEmpty())

    def test_content_that_arrived_before_the_blocks_turn_gets_into_the_snapshot(self) -> None:
        # Снимок делается, когда до блока дошла очередь, а не при показе.
        class _Late(_Card):
            pass

        card = _Late("#ff0000")
        container = self._container(card)
        container.show()
        QApplication.processEvents()
        self.assertTrue(float_in(card, delay_ms=120))
        QTimer.singleShot(30, lambda: setattr(card, "_color", QColor("#00ff00")))
        _wait(0.12 + _FLIGHT_S * 0.6)

        self.assertTrue(is_floating_in(card))
        pixel = container.grab().toImage().pixelColor(card.geometry().center())
        self.assertGreater(pixel.green(), pixel.red())

    def test_deleted_block_is_forgotten(self) -> None:
        card = _Card("#ff0000")
        keep = _Card("#0000ff")
        container = self._container(card, keep)
        controller = attach_stagger_float_in(container)
        container.show()
        _wait(0.05)
        card.setParent(None)
        card.deleteLater()
        _wait(_FLIGHT_S + 0.4)
        self.assertFalse(controller.is_running())
        self.assertTrue(keep.mask().isEmpty())


if __name__ == "__main__":
    unittest.main()
