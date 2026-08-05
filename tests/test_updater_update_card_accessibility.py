from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QAbstractAnimation, QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

from updater.ui.update_card import UpdateStatusCard


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
            (lambda card: card.show_found_update("21.1.5.36", "GitHub"), "ПРОВЕРИТЬ СНОВА"),
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


if __name__ == "__main__":
    unittest.main()
