"""Окно появляется на экране уже нарисованным, без белой вспышки: при запуске и из трея."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtCore import QEvent  # noqa: E402
from PyQt6.QtGui import QPaintEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from platforms.windows import first_frame as window_first_frame  # noqa: E402
from platforms.windows.first_frame import FrameReveal, cloak_until_painted, set_window_cloaked  # noqa: E402


_APP = QApplication.instance() or QApplication([])


class FrameRevealTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[bool] = []
        self.order: list[str] = []
        self.accept = True
        self.window = QWidget()
        self.addCleanup(self.window.deleteLater)

    def _cloak(self, hwnd: int, cloaked: bool) -> bool:
        self.assertTrue(hwnd)
        self.calls.append(cloaked)
        self.order.append("cloak" if cloaked else "uncloak")
        return self.accept

    def _composed(self) -> None:
        self.order.append("composed")

    def _reveal(self, **kwargs) -> FrameReveal:
        return FrameReveal(self.window, cloak=self._cloak, wait_composed=self._composed, **kwargs)

    def _send_paint(self) -> None:
        _APP.sendEvent(self.window, QPaintEvent(self.window.rect()))

    def test_window_stays_hidden_until_first_paint_is_done(self) -> None:
        reveal = self._reveal()

        self.assertTrue(reveal.start())
        self.assertEqual(self.calls, [True])

        # Событие отрисовки приходит раньше самой отрисовки: открывать окно
        # прямо в нём рано, на экран попал бы тот же пустой кадр.
        self._send_paint()
        self.assertEqual(self.calls, [True])
        self.assertTrue(reveal.is_cloaked())

        _APP.processEvents()
        self.assertEqual(self.calls, [True, False])
        self.assertFalse(reveal.is_cloaked())

    def test_window_is_revealed_only_once(self) -> None:
        reveal = self._reveal()
        reveal.start()
        self._send_paint()
        _APP.processEvents()
        self._send_paint()
        _APP.processEvents()
        reveal.reveal()

        self.assertEqual(self.calls, [True, False])

    def test_shade_lifts_only_after_windows_composed_the_frame(self) -> None:
        # Иначе окно ещё ~20 мс показывалось белым уже без шторки.
        reveal = self._reveal()
        reveal.start()
        self._send_paint()
        _APP.processEvents()

        self.assertEqual(self.order, ["cloak", "composed", "uncloak"])

    def test_window_is_hidden_again_on_next_show(self) -> None:
        # Возврат из трея: окно снова прячется до своей отрисовки.
        reveal = self._reveal()
        reveal.start()
        self._send_paint()
        _APP.processEvents()

        self.assertTrue(reveal.start())
        self.assertTrue(reveal.is_cloaked())
        self._send_paint()
        _APP.processEvents()

        self.assertEqual(self.calls, [True, False, True, False])

    def test_one_reveal_serves_every_show_of_a_window(self) -> None:
        self.window._frame_reveal = self._reveal()

        first = cloak_until_painted(self.window)
        self._send_paint()
        _APP.processEvents()
        second = cloak_until_painted(self.window)

        self.assertIs(first, second)
        self.assertEqual(self.calls, [True, False, True])

    def test_window_is_revealed_when_paint_never_comes(self) -> None:
        # Белое окно лучше невидимой программы.
        reveal = self._reveal(wait_max_ms=0)
        reveal.start()

        _APP.processEvents()

        self.assertEqual(self.calls, [True, False])

    def test_refused_cloak_leaves_window_alone(self) -> None:
        self.accept = False
        reveal = self._reveal()

        self.assertFalse(reveal.start())
        self._send_paint()
        _APP.processEvents()

        self.assertEqual(self.calls, [True])
        self.assertFalse(reveal.is_cloaked())

    def test_other_systems_do_nothing(self) -> None:
        if sys.platform == "win32":
            self.skipTest("проверка для систем без DWM")
        self.assertFalse(set_window_cloaked(1, True))

    def test_deadline_is_bounded(self) -> None:
        self.assertLessEqual(window_first_frame.FRAME_WAIT_MAX_MS, 2_000)


class FrameRevealWiringTests(unittest.TestCase):
    def _window(self):
        from types import SimpleNamespace

        from main.window_lifecycle import WindowLifecycleMixin

        class _Base:
            def installEventFilter(self, _event_filter) -> None:
                pass

            def showEvent(self, _event) -> None:
                pass

        class Window(WindowLifecycleMixin, _Base):
            def __init__(self) -> None:
                self.startup_state = SimpleNamespace(ttff_logged=False, ttff_ms=None)

        return Window()

    def _show_events(self, window, *events):
        from unittest.mock import patch

        with (
            patch("main.window_lifecycle.QTimer.singleShot"),
            patch("main.window_lifecycle.emit_startup_metric"),
            patch.object(__import__("platforms").current().desktop, "cloak_until_painted") as cloak,
        ):
            for event in events:
                window.showEvent(event)
        return cloak

    def test_every_own_show_hides_window_until_painted(self) -> None:
        from types import SimpleNamespace

        own_show = SimpleNamespace(spontaneous=lambda: False)
        window = self._window()

        # Запуск и два возврата из трея.
        cloak = self._show_events(window, own_show, own_show, own_show)

        self.assertEqual(cloak.call_count, 3)
        cloak.assert_called_with(window)

    def test_window_restored_by_windows_is_not_hidden(self) -> None:
        from types import SimpleNamespace

        system_show = SimpleNamespace(spontaneous=lambda: True)
        window = self._window()

        cloak = self._show_events(window, system_show)

        cloak.assert_not_called()


class SidebarHeaderSettleTests(unittest.TestCase):
    """Заголовки групп меню не растут на глазах после появления окна."""

    def test_running_header_animations_jump_to_the_end(self) -> None:
        from types import SimpleNamespace

        from qfluentwidgets import NavigationInterface, NavigationItemPosition

        import ui.navigation.sidebar_builder as sidebar_builder

        host = QWidget()
        self.addCleanup(host.deleteLater)
        nav = NavigationInterface(host, showMenuButton=True)
        header = nav.addItemHeader("Группа", NavigationItemPosition.SCROLL)
        nav.expand(False)
        self.assertEqual(header.height(), 0)

        session = SimpleNamespace(nav_headers=[(header, (), "key"), (SimpleNamespace(), (), "fake")])
        sidebar_builder._settle_sidebar_header_animations(session)

        self.assertEqual(header.height(), header.heightAni.endValue())
        self.assertGreater(header.height(), 0)

    def test_init_navigation_settles_headers_last(self) -> None:
        import inspect

        import ui.navigation.sidebar_builder as sidebar_builder

        source = inspect.getsource(sidebar_builder.init_navigation)
        self.assertLess(
            source.index("_restore_sidebar_expanded_state(window)"),
            source.index("_settle_sidebar_header_animations(session)"),
        )


if __name__ == "__main__":
    unittest.main()
