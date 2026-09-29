from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget

from dns.ui.custom_dns_dialog import CustomDnsDialog


class CustomDnsAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_custom_dns_dialog_input_clear_buttons_do_not_take_tab_focus(self) -> None:
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        dialog = CustomDnsDialog(parent)
        self.addCleanup(dialog.deleteLater)
        dialog.nameEdit.setText("Мой DNS")
        dialog.primaryEdit.setText("8.8.8.8")
        dialog.secondaryEdit.setText("1.1.1.1")
        dialog.show()
        self._app.processEvents()

        for line_edit in (dialog.nameEdit, dialog.primaryEdit, dialog.secondaryEdit):
            with self.subTest(name=line_edit.accessibleName()):
                buttons = [
                    child
                    for child in line_edit.findChildren(object)
                    if str(getattr(child, "objectName", lambda: "")() or "") == "lineEditButton"
                    and hasattr(child, "setFocusPolicy")
                ]
                self.assertTrue(buttons)
                self.assertTrue(all(button.focusPolicy() == Qt.FocusPolicy.NoFocus for button in buttons))

    def test_custom_dns_dialog_title_and_cancel_action_are_named_for_screen_reader(self) -> None:
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        dialog = CustomDnsDialog(parent)
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(dialog.titleLabel.accessibleName(), "Диалог: Добавить свой DNS")
        self.assertEqual(
            dialog.titleLabel.property("screenReaderStateText"),
            "Диалог: Добавить свой DNS",
        )
        self.assertEqual(
            dialog.subtitleLabel.accessibleName(),
            "Описание диалога DNS: Укажите DNS-сервер. После сохранения он появится в общем списке DNS.",
        )
        self.assertEqual(dialog.cancelButton.accessibleName(), "Отменить добавление своего DNS")
        self.assertEqual(
            dialog.cancelButton.property("screenReaderStateText"),
            "Отменить добавление своего DNS",
        )

    def test_edit_custom_dns_dialog_cancel_action_is_named_for_screen_reader(self) -> None:
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        dialog = CustomDnsDialog(parent, server={"id": "custom-1", "name": "Дом", "ipv4": ["8.8.8.8"]})
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(dialog.titleLabel.accessibleName(), "Диалог: Редактировать свой DNS")
        self.assertEqual(dialog.yesButton.accessibleName(), "Сохранить свой DNS")
        self.assertEqual(dialog.cancelButton.accessibleName(), "Отменить изменение своего DNS")


class _Card(QWidget):
    def add_layout(self, layout) -> None:
        self.setLayout(layout)


if __name__ == "__main__":
    unittest.main()
