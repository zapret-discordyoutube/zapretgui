import unittest
from unittest.mock import patch

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication, QWidget

from ui.popup_menu import exec_popup_menu


class _InstantMenu(QWidget):
    """Меню, которое закрывается сразу после показа."""

    def exec(self, _pos) -> None:  # noqa: A003 (Qt API)
        return None


class PopupMenuLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_menu_is_deleted_after_it_closes(self) -> None:
        owner = QWidget()
        menu = _InstantMenu(owner)

        with patch.object(menu, "deleteLater") as delete_later:
            exec_popup_menu(menu, QPoint(0, 0), owner=owner)

        delete_later.assert_called_once_with()
        owner.deleteLater()


if __name__ == "__main__":
    unittest.main()
