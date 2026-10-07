"""Журнал экранов окна: кнопки «назад» и «вперёд» возвращают на последний экран,
включая то, что было открыто внутри страницы."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_SRC = Path(__file__).resolve().parent.parent / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget

from app.page_names import PageName
from ui.navigation.history import HistoryEntry, NavigationHistory, ScreenState
from ui.navigation.history_controller import WindowNavigationHistory

A, B, C = PageName.ZAPRET2_MODE_CONTROL, PageName.BLOCKCHECK, PageName.NETWORK


def _entry(page: PageName, key: str = "", payload: object = None) -> HistoryEntry:
    return HistoryEntry(page, ScreenState(key=key, title=key, payload=payload))


class NavigationHistoryModelTests(unittest.TestCase):
    def test_back_and_forward_walk_the_visited_screens(self) -> None:
        history = NavigationHistory()
        for entry in (_entry(A), _entry(B, "tab"), _entry(B, "card")):
            history.visit(entry)

        self.assertTrue(history.can_go_back())
        self.assertFalse(history.can_go_forward())
        self.assertEqual([e.screen.key for _i, e in history.back_entries()], ["tab", ""])

        history.move_to(history.index - 1)
        self.assertEqual(history.current(), _entry(B, "tab"))
        self.assertEqual([e.screen.key for _i, e in history.forward_entries()], ["card"])

    def test_new_screen_after_back_drops_the_forward_part(self) -> None:
        history = NavigationHistory()
        for entry in (_entry(A), _entry(B), _entry(C)):
            history.visit(entry)
        history.move_to(0)

        history.visit(_entry(B, "card"))

        self.assertEqual(history.entries(), (_entry(A), _entry(B, "card")))
        self.assertFalse(history.can_go_forward())

    def test_same_screen_is_updated_in_place_and_keeps_the_forward_part(self) -> None:
        history = NavigationHistory()
        for entry in (_entry(A), _entry(B, "card", payload="old"), _entry(C)):
            history.visit(entry)
        history.move_to(1)

        history.visit(_entry(B, "card", payload="new"))

        self.assertEqual(len(history.entries()), 3)
        self.assertEqual(history.current().screen.payload, "new")
        self.assertTrue(history.can_go_forward())

    def test_limit_forgets_the_oldest_screens(self) -> None:
        history = NavigationHistory(limit=3)
        for number in range(5):
            history.visit(_entry(B, str(number)))

        self.assertEqual([e.screen.key for e in history.entries()], ["2", "3", "4"])
        self.assertEqual(history.index, 2)

    def test_dropped_screen_keeps_the_pointer_and_merges_equal_neighbours(self) -> None:
        history = NavigationHistory()
        for entry in (_entry(A), _entry(B, "gone"), _entry(A)):
            history.visit(entry)

        history.drop(1)

        self.assertEqual(history.entries(), (_entry(A),))
        self.assertEqual(history.index, 0)
        self.assertFalse(history.can_go_back())

    def test_listeners_hear_every_change(self) -> None:
        history = NavigationHistory()
        heard = []
        history.subscribe(lambda: heard.append(history.index))

        history.visit(_entry(A))
        history.visit(_entry(B))
        history.move_to(0)

        self.assertEqual(heard, [0, 1, 0])


class _FakePage:
    """Страница с вложенными экранами: открыть можно только те, что в ``known``."""

    def __init__(self, *known: str) -> None:
        self.known = {"", *known}
        self.screen = ScreenState()

    def open(self, key: str) -> None:
        self.screen = ScreenState(key=key, title=key)

    def navigation_screen(self) -> ScreenState:
        return self.screen

    def restore_navigation_screen(self, screen: ScreenState) -> bool:
        if screen.key not in self.known:
            return False
        self.screen = screen
        return True


class _FakeHost:
    def __init__(self) -> None:
        self.pages: dict[PageName, _FakePage] = {}
        self.current: PageName | None = None
        self.blocked: set[PageName] = set()
        self.navigation_history = WindowNavigationHistory(object(), self)

    def current_page(self):
        return self.pages.get(self.current)

    def page_name_of(self, page):
        return next((name for name, loaded in self.pages.items() if loaded is page), None)

    def get_loaded_page(self, page_name):
        return self.pages.get(page_name)

    def show_page(self, page_name, *, allow_internal: bool = False) -> bool:
        if page_name in self.blocked:
            return False
        self.current = page_name
        self.navigation_history.note_screen_changed()
        return True

    # Действия человека: каждое заканчивается записью в журнал, как в конце витка событий.
    def user_opens(self, page_name: PageName) -> None:
        self.show_page(page_name)
        self.navigation_history.commit()

    def user_opens_inside(self, key: str) -> None:
        self.pages[self.current].open(key)
        self.navigation_history.note_screen_changed()
        self.navigation_history.commit()

    def where(self) -> tuple[PageName | None, str]:
        return self.current, self.current_page().screen.key


class WindowNavigationHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _host(self) -> _FakeHost:
        host = _FakeHost()
        host.pages = {A: _FakePage(), B: _FakePage("tab", "card"), C: _FakePage()}
        return host

    def test_back_returns_to_the_screen_inside_the_page_not_to_the_previous_page(self) -> None:
        host = self._host()
        nav = host.navigation_history
        host.user_opens(A)
        host.user_opens(B)
        host.user_opens_inside("tab")
        host.user_opens_inside("card")

        self.assertTrue(nav.go_back())
        self.assertEqual(host.where(), (B, "tab"))
        self.assertTrue(nav.go_back())
        self.assertEqual(host.where(), (B, ""))
        self.assertTrue(nav.go_back())
        self.assertEqual(host.where(), (A, ""))
        self.assertFalse(nav.can_go_back())

    def test_returning_from_another_page_reopens_the_inner_screen(self) -> None:
        host = self._host()
        nav = host.navigation_history
        host.user_opens(B)
        host.user_opens_inside("card")
        host.user_opens(C)
        # Пока человек был на другой странице, эта вернулась в исходный вид.
        host.pages[B].open("")

        self.assertTrue(nav.go_back())

        self.assertEqual(host.where(), (B, "card"))

    def test_forward_undoes_back_until_a_new_screen_is_opened(self) -> None:
        host = self._host()
        nav = host.navigation_history
        host.user_opens(A)
        host.user_opens(B)
        host.user_opens_inside("card")

        nav.go_back()
        nav.go_back()
        self.assertTrue(nav.can_go_forward())
        self.assertTrue(nav.go_forward())
        self.assertEqual(host.where(), (B, ""))
        self.assertTrue(nav.go_forward())
        self.assertEqual(host.where(), (B, "card"))
        self.assertFalse(nav.can_go_forward())

        nav.go_back()
        host.user_opens(C)
        self.assertFalse(nav.can_go_forward())

    def test_page_and_its_inner_screen_opened_together_are_one_step(self) -> None:
        host = self._host()
        nav = host.navigation_history
        host.user_opens(A)

        host.show_page(B)
        host.pages[B].open("card")
        nav.note_screen_changed()
        QApplication.processEvents()

        self.assertEqual([e.screen.key for e in nav.history.entries()], ["", "card"])

    def test_screen_that_cannot_be_opened_anymore_is_skipped_and_forgotten(self) -> None:
        host = self._host()
        nav = host.navigation_history
        host.user_opens(A)
        host.user_opens(B)
        host.user_opens_inside("card")
        host.user_opens(C)
        host.pages[B].known.discard("card")

        self.assertTrue(nav.go_back())

        self.assertEqual(host.where(), (B, ""))
        self.assertEqual([e.screen.key for e in nav.history.entries()], ["", "", ""])
        self.assertEqual(nav.history.index, 1)

    def test_page_unavailable_in_this_mode_is_skipped(self) -> None:
        host = self._host()
        nav = host.navigation_history
        host.user_opens(A)
        host.user_opens(B)
        host.user_opens(C)
        host.blocked.add(B)

        self.assertTrue(nav.go_back())

        self.assertEqual(host.where(), (A, ""))
        self.assertEqual([e.page for e in nav.history.entries()], [A, C])

    def test_picking_a_screen_from_the_list_jumps_straight_to_it(self) -> None:
        host = self._host()
        nav = host.navigation_history
        host.user_opens(A)
        host.user_opens(B)
        host.user_opens_inside("card")
        host.user_opens(C)
        back = [entry for _index, entry in nav.history.back_entries()]

        self.assertTrue(nav.go_to(back[-1]))

        self.assertEqual(host.where(), (A, ""))
        self.assertEqual(len(nav.history.forward_entries()), 3)

    def test_list_names_the_page_and_the_screen_inside_it(self) -> None:
        host = self._host()
        nav = host.navigation_history
        with patch("ui.navigation.text_sync.get_nav_label", return_value="BlockCheck"):
            self.assertEqual(nav.describe(_entry(B)), "BlockCheck")
            self.assertEqual(
                nav.describe(HistoryEntry(B, ScreenState(key="card:dns", title="DNS"))),
                "BlockCheck — DNS",
            )


class HistoryButtonsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        from qfluentwidgets import FluentWindow
        from qfluentwidgets.common.router import qrouter

        from ui.navigation.history_buttons import HistoryButtons

        window = FluentWindow()
        self.addCleanup(window.deleteLater)
        host = _FakeHost()
        host.pages = {A: _FakePage(), B: _FakePage("card"), C: _FakePage()}
        with patch("ui.navigation.history_buttons.get_window_ui_session", return_value=None):
            buttons = HistoryButtons(window, host.navigation_history)
        return window, host, buttons, qrouter

    def test_back_button_follows_the_window_history_instead_of_the_library_one(self) -> None:
        window, host, buttons, qrouter = self._window()
        self.assertFalse(buttons.back_button.isEnabled())

        host.user_opens(A)
        host.user_opens(B)
        host.user_opens_inside("card")
        self.assertTrue(buttons.back_button.isEnabled())

        with patch.object(type(qrouter), "pop") as library_pop:
            buttons.back_button.click()
        library_pop.assert_not_called()
        self.assertEqual(host.where(), (B, ""))

        # Библиотечный список страниц больше не решает, активна ли стрелка.
        qrouter.emptyChanged.emit(True)
        self.assertTrue(buttons.back_button.isEnabled())

    def test_forward_button_appears_only_after_back(self) -> None:
        window, host, buttons, _qrouter = self._window()
        host.user_opens(A)
        host.user_opens(B)
        self.assertTrue(buttons.forward_button.isHidden())

        buttons.back_button.click()
        self.assertFalse(buttons.forward_button.isHidden())

        buttons.forward_button.click()
        self.assertEqual(host.where(), (B, ""))
        self.assertTrue(buttons.forward_button.isHidden())

    def test_menu_lists_screens_behind_nearest_first(self) -> None:
        window, host, buttons, _qrouter = self._window()
        host.user_opens(A)
        host.user_opens(B)
        host.user_opens_inside("card")
        host.user_opens(C)

        behind = buttons.menu_entries(forward=False)

        self.assertEqual([(e.page, e.screen.key) for e in behind], [(B, "card"), (B, ""), (A, "")])
        self.assertEqual(buttons.menu_entries(forward=True), ())

    def _mouse(self, button, kind, mouse_button=Qt.MouseButton.LeftButton) -> None:
        position = QPointF(button.rect().center())
        event = QMouseEvent(kind, position, button.mapToGlobal(position), mouse_button, mouse_button, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(button, event)

    def test_long_press_opens_the_list_and_does_not_also_go_back(self) -> None:
        from ui.navigation import history_buttons

        window, host, buttons, _qrouter = self._window()
        host.user_opens(A)
        host.user_opens(B)
        shown = []
        with (
            patch.object(history_buttons, "LONG_PRESS_MS", 1),
            patch.object(history_buttons.HistoryButtons, "show_menu", lambda self, *, forward: shown.append(forward)),
        ):
            gesture = history_buttons.HistoryButtonGesture(buttons.back_button, lambda: buttons.show_menu(forward=False))
            self._mouse(buttons.back_button, QEvent.Type.MouseButtonPress)
            QTest.qWait(40)
            self._mouse(buttons.back_button, QEvent.Type.MouseButtonRelease)
            gesture.deleteLater()

        self.assertEqual(shown, [False])
        self.assertEqual(host.where(), (B, ""))

    def test_forward_button_keeps_its_mouse_events_from_the_title_bar(self) -> None:
        # Шапка окна двигает окно и разворачивает его по событиям, которые никто не забрал.
        window, host, buttons, _qrouter = self._window()
        host.user_opens(A)
        host.user_opens(B)
        buttons.back_button.click()
        button = buttons.forward_button
        position = QPointF(button.rect().center())

        for kind in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonDblClick,
            QEvent.Type.MouseButtonRelease,
        ):
            event = QMouseEvent(
                kind,
                position,
                button.mapToGlobal(position),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            event.ignore()
            QApplication.sendEvent(button, event)
            self.assertTrue(event.isAccepted(), kind)

    def test_short_click_still_goes_back(self) -> None:
        window, host, buttons, _qrouter = self._window()
        host.user_opens(A)
        host.user_opens(B)

        self._mouse(buttons.back_button, QEvent.Type.MouseButtonPress)
        self._mouse(buttons.back_button, QEvent.Type.MouseButtonRelease)

        self.assertEqual(host.where(), (A, ""))


class PageHostRecordsScreensTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_base_page_has_one_plain_screen(self) -> None:
        from ui.pages.base_page import BasePage

        page = BasePage("Страница")
        self.addCleanup(page.deleteLater)

        self.assertEqual(page.navigation_screen(), ScreenState())
        self.assertTrue(page.restore_navigation_screen(ScreenState()))
        self.assertFalse(page.restore_navigation_screen(ScreenState(key="card:dns")))

    def test_created_page_reports_its_inner_screens_to_the_window_history(self) -> None:
        from types import SimpleNamespace

        from ui.page_host import WindowPageHost
        from ui.pages.base_page import BasePage

        page = BasePage("Страница")
        self.addCleanup(page.deleteLater)
        factory = SimpleNamespace(create_page=lambda _name: SimpleNamespace(page=page, elapsed_ms=0))
        host = WindowPageHost(QWidget(), factory)
        self.assertIs(host.ensure_page(B), page)
        self.assertIs(host.page_name_of(page), B)

        with patch.object(host.navigation_history, "commit") as commit:
            page.navigation_screen_changed.emit()
            QApplication.processEvents()

        commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
