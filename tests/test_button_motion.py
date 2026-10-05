from __future__ import annotations

import gc
import inspect
import math
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QPointF, Qt, qInstallMessageHandler
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

import ui.button_motion as motion_module
from ui.button_motion import button_motion, install_button_motion


def _wait(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QApplication.processEvents()


def _mouse(kind: QEvent.Type, widget, pos: QPointF) -> QMouseEvent:
    buttons = Qt.MouseButton.LeftButton if kind == QEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton
    return QMouseEvent(
        kind,
        pos,
        widget.mapToGlobal(pos),
        Qt.MouseButton.LeftButton,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )


class ButtonMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])
        install_button_motion()

    def setUp(self) -> None:
        patcher = mock.patch.object(motion_module, "are_live_animations_enabled", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.host = QWidget()
        self.addCleanup(self.host.deleteLater)
        self.layout = QVBoxLayout(self.host)

    def _add(self, widget):
        self.layout.addWidget(widget)
        self.host.show()
        QApplication.processEvents()
        return widget

    def _press(self, button, pos: QPointF | None = None) -> None:
        point = pos or QPointF(6, 6)
        QApplication.sendEvent(button, _mouse(QEvent.Type.MouseButtonPress, button, point))

    def _release(self, button) -> None:
        QApplication.sendEvent(button, _mouse(QEvent.Type.MouseButtonRelease, button, QPointF(6, 6)))

    def test_hover_sways_icon_and_settles(self) -> None:
        from qfluentwidgets import FluentIcon, PushButton

        button = self._add(PushButton("Скачать", icon=FluentIcon.DOWNLOAD))
        button.enterEvent(QEvent(QEvent.Type.Enter))
        _wait(0.08)

        motion = button_motion(button)
        self.assertGreater(abs(motion.icon_angle()), 0.5)
        self.assertLessEqual(abs(motion.icon_angle()), motion_module.WOBBLE_AMPLITUDE_DEG)
        _wait(motion_module.WOBBLE_DURATION_MS / 1000 + 0.1)
        self.assertEqual(motion.icon_angle(), 0.0)

    def test_nothing_moves_when_live_animations_are_off(self) -> None:
        from qfluentwidgets import FluentIcon, PushButton

        button = self._add(PushButton("Копировать", icon=FluentIcon.COPY))
        with mock.patch.object(motion_module, "are_live_animations_enabled", return_value=False):
            button.enterEvent(QEvent(QEvent.Type.Enter))
            self._press(button)
            _wait(0.06)

        motion = button_motion(button)
        self.assertEqual(motion.icon_angle(), 0.0)
        self.assertEqual(motion.icon_scale(), 1.0)
        self.assertFalse(motion.ripple_active())
        self._release(button)

    def test_press_squeezes_icon_starts_ripple_and_release_springs_back(self) -> None:
        from qfluentwidgets import FluentIcon, PrimaryPushButton

        button = self._add(PrimaryPushButton("Открыть", icon=FluentIcon.SEND))
        self._press(button, QPointF(20, 10))
        _wait(0.15)

        motion = button_motion(button)
        self.assertLess(motion.icon_scale(), 0.9)
        self.assertTrue(motion.ripple_active())
        clicked = mock.Mock()
        button.clicked.connect(clicked)
        self._release(button)
        clicked.assert_called_once()
        _wait((motion_module.RELEASE_DURATION_MS + motion_module.RIPPLE_DURATION_MS) / 1000 + 0.1)
        self.assertAlmostEqual(motion.icon_scale(), 1.0, places=3)
        self.assertFalse(motion.ripple_active())

    def test_icon_is_rotated_once_even_when_draw_icon_delegates(self) -> None:
        import qfluentwidgets.components.widgets.button as fluent_button
        from qfluentwidgets import FluentIcon, PrimaryDropDownPushButton, TogglePushButton

        toggle = self._add(TogglePushButton("Режим", icon=FluentIcon.SEND))
        dropdown = self._add(PrimaryDropDownPushButton("Ещё", icon=FluentIcon.COPY))
        original_draw = fluent_button.drawIcon

        for button, checked in ((toggle, False), (toggle, True), (dropdown, False)):
            with self.subTest(button=type(button).__name__, checked=checked):
                if button is toggle:
                    toggle.setChecked(checked)
                button.enterEvent(QEvent(QEvent.Type.Enter))
                _wait(0.08)
                motion = button_motion(button)
                seen: list[tuple[float, float]] = []

                def record(icon, painter, rect, *args, _motion=motion, _seen=seen, **kwargs):
                    transform = painter.worldTransform()
                    _seen.append((math.degrees(math.atan2(transform.m12(), transform.m11())), _motion.icon_angle()))
                    return original_draw(icon, painter, rect, *args, **kwargs)

                # Подмена — сама функция, а не Mock: Mock запоминает аргументы вызова,
                # то есть держит QPainter дольше отрисовки, и его разбор сборщиком
                # мусора после grab() роняет интерпретатор.
                with mock.patch.object(fluent_button, "drawIcon", record):
                    button.grab()
                self.assertEqual(len(seen), 1)
                painter_angle, motion_angle = seen[0]
                self.assertGreater(abs(motion_angle), 0.5)
                self.assertAlmostEqual(painter_angle, motion_angle, places=3)
                _wait(motion_module.WOBBLE_DURATION_MS / 1000 + 0.05)

    def test_ripple_paints_on_top_without_painter_conflicts(self) -> None:
        from qfluentwidgets import DropDownPushButton, FluentIcon, PillPushButton, PushButton, PushSettingCard

        from donater.premium_display import PREMIUM_TIERS, PremiumDisplay
        from ui.subscription_title_badge import SubscriptionTitleBadge

        card = PushSettingCard("Выбрать", FluentIcon.INFO, "Карточка", "Описание")
        buttons = [
            self._add(PushButton("Обычная", icon=FluentIcon.COPY)),
            self._add(DropDownPushButton("Меню", icon=FluentIcon.COPY)),
            self._add(PillPushButton("Pill", icon=FluentIcon.COPY)),
        ]
        badge = SubscriptionTitleBadge(language_provider=lambda: "ru")
        # Метка Premium рисует свой блик поверх кнопки вторым QPainter.
        badge.set_display(PremiumDisplay(tier=next(iter(PREMIUM_TIERS)), days=37))
        buttons.append(self._add(badge))
        self._add(card)
        buttons.append(card.button)

        messages: list[str] = []
        previous = qInstallMessageHandler(lambda _kind, _ctx, message: messages.append(message))
        try:
            for button in buttons:
                self._press(button)
            _wait(0.12)
            for button in buttons:
                with self.subTest(button=type(button).__name__):
                    self.assertTrue(button_motion(button).ripple_active())
                    button.grab()
            for button in buttons:
                self._release(button)
        finally:
            qInstallMessageHandler(previous)
        self.assertEqual([m for m in messages if "paint" in m.lower()], [])

    def test_plain_buttons_created_by_qfluentwidgets_get_gestures(self) -> None:
        from qfluentwidgets import FluentIcon, MessageBoxBase, PrimaryPushSettingCard

        card = self._add(PrimaryPushSettingCard("Открыть", FluentIcon.INFO, "Карточка"))
        dialog = MessageBoxBase(self.host)
        self.addCleanup(dialog.deleteLater)

        for button in (card.button, dialog.cancelButton):
            with self.subTest(button=button.text()):
                self.assertTrue(getattr(button, motion_module._FILTERED_ATTR, False))
        self._press(card.button)
        self.assertTrue(button_motion(card.button).ripple_active())
        self._release(card.button)

    def test_gestures_survive_garbage_collection_of_button_wrapper(self) -> None:
        # Кнопкой владеет Qt, а её Python-обёртку никто не держит: так бывает с
        # кнопками, которые создал сам Qt или чей Python-владелец уже отпущен.
        button = QPushButton("Отмена", self.host)
        motion_module.attach_button_motion(button)
        self._add(button)
        sip.transferto(button, None)
        del button

        # В сборке Nuitka метод — не обычный метод Python, и PyQt держит слот
        # сильной ссылкой. ``__call__`` связанного метода даёт здесь то же самое.
        make_animation = motion_module._make_animation
        errors: list[BaseException] = []
        with (
            mock.patch.object(
                motion_module,
                "_make_animation",
                lambda parent, on_value, **kwargs: make_animation(parent, on_value.__call__, **kwargs),
            ),
            mock.patch.object(sys, "excepthook", lambda _kind, error, _tb: errors.append(error)),
        ):
            self._press(self.host.findChild(QPushButton))
            _wait(0.05)
            gc.collect()
            _wait(0.1)

            self.assertEqual(errors, [])
            found = self.host.findChild(QPushButton)
            self.assertTrue(button_motion(found).ripple_active())
            self._release(found)
            _wait(motion_module.RIPPLE_DURATION_MS / 1000 + 0.1)
            self.assertFalse(button_motion(found).ripple_active())
            self.assertEqual(errors, [])
        self.assertEqual(len(found.findChildren(motion_module._ButtonMotion)), 1)

    def test_motion_is_forgotten_when_button_is_deleted(self) -> None:
        button = QPushButton("Отмена", self.host)
        motion_module.attach_button_motion(button)
        self._add(button)
        self._press(button)
        key = sip.unwrapinstance(button)
        self.assertIn(key, motion_module._MOTIONS)

        sip.delete(button)

        self.assertNotIn(key, motion_module._MOTIONS)

    def test_module_contract(self) -> None:
        source = inspect.getsource(motion_module)
        self.assertNotIn("QPropertyAnimation(", source)
        self.assertNotIn("themeChanged", source)

        runtime_source = (Path(__file__).resolve().parents[1] / "src" / "main" / "qt_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("install_button_motion()", runtime_source)


if __name__ == "__main__":
    unittest.main()
