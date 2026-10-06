from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import patch

from PyQt6.QtCore import QAbstractAnimation, QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import PushButton

from updater.ui import update_card
from updater.ui.update_card import FINISH_TURN_DEGREES, UpdateStatusCard


class UpdaterUpdateCardAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_update_status_card_has_initial_screen_reader_text(self) -> None:
        card = UpdateStatusCard(language="ru")

        self.assertEqual(
            card.accessibleName(),
            "Проверка обновлений. Нажмите для проверки доступных обновлений",
        )
        self.assertIn("текущий статус", card.accessibleDescription().lower())
        self.assertEqual(
            card._icon_label.accessibleName(),
            "Индикатор проверки обновлений: Проверка обновлений. Нажмите для проверки доступных обновлений",
        )
        self.assertEqual(
            card._icon_label.property("screenReaderStateText"),
            "Индикатор проверки обновлений: Проверка обновлений. Нажмите для проверки доступных обновлений",
        )
        self.assertEqual(card.check_btn.accessibleName(), "Проверить обновления")
        self.assertEqual(
            card.check_btn.property("screenReaderStateText"),
            "Проверить обновления",
        )
        self.assertIn("запускает проверку", card.check_btn.accessibleDescription().lower())

    def test_update_status_card_text_labels_are_named_for_screen_reader(self) -> None:
        card = UpdateStatusCard(language="ru")

        self.assertEqual(card.title_label.accessibleName(), "Заголовок проверки обновлений: Проверка обновлений")
        self.assertEqual(
            card.subtitle_label.accessibleName(),
            "Описание проверки обновлений: Нажмите для проверки доступных обновлений",
        )

    def test_update_status_card_opens_check_from_keyboard(self) -> None:
        card = UpdateStatusCard(language="ru")
        self.addCleanup(card.deleteLater)
        requested: list[bool] = []
        card.check_clicked.connect(lambda: requested.append(True))

        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(card, event)

        self.assertTrue(event.isAccepted())
        self.assertEqual(requested, [True])

    def test_update_status_card_reports_checking_state(self) -> None:
        card = UpdateStatusCard(language="ru")

        card.start_checking()

        self.assertEqual(
            card.accessibleName(),
            "Проверка обновлений... Подождите, идёт проверка серверов",
        )
        self.assertEqual(
            card.property("screenReaderStateText"),
            "Проверка обновлений... Подождите, идёт проверка серверов",
        )
        self.assertEqual(
            card._icon_label.accessibleName(),
            "Индикатор проверки обновлений: Проверка обновлений... Подождите, идёт проверка серверов",
        )
        self.assertEqual(
            card._icon_label.property("screenReaderStateText"),
            "Индикатор проверки обновлений: Проверка обновлений... Подождите, идёт проверка серверов",
        )
        self.assertEqual(card.check_btn.accessibleName(), "Проверка обновлений выполняется")
        self.assertEqual(
            card.check_btn.property("screenReaderStateText"),
            "Проверка обновлений выполняется",
        )
        self.assertEqual(card.check_btn._ring.accessibleName(), "Проверка обновлений выполняется")
        self.assertEqual(
            card.check_btn._ring.property("screenReaderStateText"),
            "Проверка обновлений выполняется",
        )
        self.assertIn("дождитесь завершения", card.check_btn.accessibleDescription().lower())

    def test_disabled_update_check_button_reports_unavailable_state(self) -> None:
        card = UpdateStatusCard(language="ru")

        card.set_check_enabled(False)

        self.assertFalse(card.check_btn.isEnabled())
        self.assertEqual(card.check_btn.accessibleName(), "Проверить обновления, недоступно")
        self.assertEqual(
            card.check_btn.property("screenReaderStateText"),
            "Проверить обновления, недоступно",
        )
        self.assertIn("сейчас недоступна", card.check_btn.accessibleDescription().lower())

        card.set_check_enabled(True)

        self.assertTrue(card.check_btn.isEnabled())
        self.assertEqual(card.check_btn.accessibleName(), "Проверить обновления")
        self.assertEqual(
            card.check_btn.property("screenReaderStateText"),
            "Проверить обновления",
        )

    def test_every_terminal_state_stops_and_hides_checking_ring(self) -> None:
        transitions = (
            (lambda card: card.show_found_update("21.1.5.36", "Forgejo"), "ПРОВЕРИТЬ СНОВА"),
            (lambda card: card.show_download_error(), "ПРОВЕРИТЬ СНОВА"),
            (lambda card: card.show_deferred("21.1.5.36"), "ПРОВЕРИТЬ СНОВА"),
            (lambda card: card.show_checked_ago(5.0), "ПРОВЕРИТЬ СНОВА"),
            (lambda card: card.show_auto_enabled_hint(), "ПРОВЕРИТЬ СНОВА"),
            (lambda card: card.show_manual_hint(), "ПРОВЕРИТЬ ВРУЧНУЮ"),
        )

        for transition, expected_text in transitions:
            with self.subTest(expected_text=expected_text):
                card = UpdateStatusCard(language="ru")
                self.addCleanup(card.deleteLater)
                card.start_checking()
                self.assertFalse(card.check_btn._ring.isHidden())

                transition(card)

                self.assertTrue(card.check_btn._ring.isHidden())
                self.assertEqual(
                    card.check_btn._ring.aniGroup.state(),
                    QAbstractAnimation.State.Stopped,
                )
                self.assertEqual(card.check_btn.text(), expected_text)
                self.assertTrue(card.check_btn.isEnabled())

    # ── значок и анимация кнопки проверки ────────────────────────────────

    def _shown_card(self) -> UpdateStatusCard:
        card = UpdateStatusCard(language="ru")
        self.addCleanup(card.deleteLater)
        card.resize(760, 80)
        card.show()
        self._app.processEvents()
        return card

    def _icon_draws(self, card: UpdateStatusCard) -> int:
        """Сколько раз кнопка нарисовала значок за одну отрисовку."""
        draws: list[bool] = []

        def record(_button, _icon, _painter, _rect, *_args, **_kwargs) -> None:
            draws.append(True)

        # Подмена — сама функция, а не Mock: Mock запоминает аргументы вызова,
        # то есть держит QPainter дольше отрисовки, и его разбор сборщиком
        # мусора после grab() роняет интерпретатор.
        with patch.object(PushButton, "_drawIcon", record):
            card.check_btn.grab()
        return len(draws)

    def test_check_button_has_icon_so_shared_button_motion_applies(self) -> None:
        from ui.button_motion import _button_has_icon

        card = self._shown_card()

        self.assertFalse(card.check_btn.icon().isNull())
        self.assertTrue(_button_has_icon(card.check_btn))
        self.assertEqual(self._icon_draws(card), 1)

        card.start_checking()
        card.show_checked_ago(5.0)
        card.check_btn._turn.stop()

        self.assertEqual(card.check_btn.text(), "ПРОВЕРИТЬ СНОВА")
        self.assertFalse(card.check_btn.icon().isNull())

    def test_check_button_hides_icon_under_ring_while_checking(self) -> None:
        card = self._shown_card()

        card.start_checking()

        self.assertEqual(self._icon_draws(card), 0)

        card.show_checked_ago(5.0)

        self.assertEqual(self._icon_draws(card), 1)

    def test_check_button_keeps_width_while_checking(self) -> None:
        card = self._shown_card()
        card.start_checking()
        card.show_checked_ago(5.0)
        card.check_btn._turn.stop()
        self._app.processEvents()
        idle_width = card.check_btn.width()
        idle_minimum = card.check_btn.minimumWidth()

        card.start_checking()
        self._app.processEvents()

        self.assertEqual(card.check_btn.width(), idle_width)

        card.show_checked_ago(5.0)
        card.check_btn._turn.stop()
        self._app.processEvents()

        self.assertEqual(card.check_btn.minimumWidth(), idle_minimum)
        self.assertEqual(card.check_btn.width(), idle_width)

    def test_finished_check_turns_button_icon_once_and_rests(self) -> None:
        card = self._shown_card()
        button = card.check_btn

        card.start_checking()
        self.assertFalse(button.is_finish_turn_running())

        with patch.object(update_card, "are_live_animations_enabled", return_value=True):
            card.show_checked_ago(5.0)

        self.assertTrue(button.is_finish_turn_running())
        self.assertEqual(button._turn.loopCount(), 1)

        button._turn.setCurrentTime(button._turn.duration() // 2)
        self.assertGreater(button._turn_angle, 0.0)
        self.assertLess(button._turn_angle, FINISH_TURN_DEGREES)

        button._turn.setCurrentTime(button._turn.duration())
        self._app.processEvents()

        self.assertFalse(button.is_finish_turn_running())
        self.assertEqual(button._turn_angle, 0.0)

    def test_finish_turn_is_skipped_without_check_animations_or_visibility(self) -> None:
        # Состояние сменилось без проверки (например, открыли страницу).
        card = self._shown_card()
        with patch.object(update_card, "are_live_animations_enabled", return_value=True):
            card.show_checked_ago(5.0)
        self.assertFalse(card.check_btn.is_finish_turn_running())

        # «Живые анимации» выключены.
        card.start_checking()
        with patch.object(update_card, "are_live_animations_enabled", return_value=False):
            card.show_checked_ago(5.0)
        self.assertFalse(card.check_btn.is_finish_turn_running())
        self.assertEqual(card.check_btn._turn_angle, 0.0)

        # Карточка не на экране: проверка при запуске идёт на скрытой странице.
        hidden = UpdateStatusCard(language="ru")
        self.addCleanup(hidden.deleteLater)
        hidden.start_checking()
        with patch.object(update_card, "are_live_animations_enabled", return_value=True):
            hidden.show_checked_ago(5.0)
        self.assertFalse(hidden.check_btn.is_finish_turn_running())

    def test_hiding_button_stops_finish_turn(self) -> None:
        card = self._shown_card()
        card.start_checking()
        with patch.object(update_card, "are_live_animations_enabled", return_value=True):
            card.show_checked_ago(5.0)
        card.check_btn._turn.setCurrentTime(card.check_btn._turn.duration() // 2)
        self.assertTrue(card.check_btn.is_finish_turn_running())

        card.hide()

        self.assertFalse(card.check_btn.is_finish_turn_running())
        self.assertEqual(card.check_btn._turn_angle, 0.0)


if __name__ == "__main__":
    unittest.main()
