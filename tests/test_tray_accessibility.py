from __future__ import annotations

import unittest
from unittest.mock import patch

from platforms.windows.tray import SystemTrayManager


class _DialogButton:
    def __init__(self) -> None:
        self._accessible_name = ""
        self._accessible_description = ""

    def accessibleName(self) -> str:  # noqa: N802
        return self._accessible_name

    def setAccessibleName(self, text: str) -> None:  # noqa: N802
        self._accessible_name = str(text)

    def accessibleDescription(self) -> str:  # noqa: N802
        return self._accessible_description

    def setAccessibleDescription(self, text: str) -> None:  # noqa: N802
        self._accessible_description = str(text)


class _MessageBox:
    instances: list["_MessageBox"] = []

    class Icon:
        Warning = 1

    class StandardButton:
        Yes = 1
        No = 2

    def __init__(self) -> None:
        self._buttons = {
            self.StandardButton.Yes: _DialogButton(),
            self.StandardButton.No: _DialogButton(),
        }
        self.exec_called = False
        _MessageBox.instances.append(self)

    def setIcon(self, _icon) -> None:  # noqa: N802
        return None

    def setWindowTitle(self, title: str) -> None:  # noqa: N802
        self.title = str(title)

    def setText(self, text: str) -> None:  # noqa: N802
        self.text = str(text)

    def setInformativeText(self, text: str) -> None:  # noqa: N802
        self.informative_text = str(text)

    def setStandardButtons(self, buttons) -> None:  # noqa: N802
        self.standard_buttons = buttons

    def button(self, standard_button):
        return self._buttons.get(standard_button)

    def exec(self):
        self.exec_called = True
        return self.StandardButton.Yes


class TrayAccessibilityTests(unittest.TestCase):
    def test_discord_restart_confirm_buttons_are_named_for_screen_reader(self) -> None:
        manager = SystemTrayManager.__new__(SystemTrayManager)
        _MessageBox.instances = []

        with patch("platforms.windows.tray.QMessageBox", _MessageBox):
            confirmed = SystemTrayManager._confirm_disable_discord_restart(manager)

        self.assertTrue(confirmed)
        dialog = _MessageBox.instances[0]
        yes_button = dialog.button(_MessageBox.StandardButton.Yes)
        no_button = dialog.button(_MessageBox.StandardButton.No)
        self.assertEqual(yes_button.accessibleName(), "Отключить автоперезапуск Discord")
        self.assertIn(
            "Discord больше не будет перезапускаться автоматически",
            yes_button.accessibleDescription(),
        )
        self.assertEqual(no_button.accessibleName(), "Не отключать автоперезапуск Discord")
        self.assertIn("останется включённым", no_button.accessibleDescription())
        self.assertTrue(dialog.exec_called)


if __name__ == "__main__":
    unittest.main()
