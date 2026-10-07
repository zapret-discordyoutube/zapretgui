"""Окно появляется на экране уже нарисованным, без белой вспышки."""

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

from ui import window_first_frame  # noqa: E402
from ui.window_first_frame import FirstFrameReveal, set_window_cloaked  # noqa: E402


_APP = QApplication.instance() or QApplication([])


class FirstFrameRevealTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[bool] = []
        self.accept = True
        self.window = QWidget()
        self.addCleanup(self.window.deleteLater)

    def _cloak(self, hwnd: int, cloaked: bool) -> bool:
        self.assertTrue(hwnd)
        self.calls.append(cloaked)
        return self.accept

    def _reveal(self, **kwargs) -> FirstFrameReveal:
        return FirstFrameReveal(self.window, cloak=self._cloak, **kwargs)

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
        self.assertLessEqual(window_first_frame.FIRST_FRAME_WAIT_MAX_MS, 2_000)


class FirstFrameWiringTests(unittest.TestCase):
    def test_first_show_event_starts_the_reveal(self) -> None:
        import inspect

        from main.window_lifecycle import WindowLifecycleMixin

        source = inspect.getsource(WindowLifecycleMixin.showEvent)
        call = "begin_first_frame_reveal(self)"
        self.assertIn(call, source)
        # Только при первом показе: дальше окно уже нарисовано.
        self.assertLess(source.index("ttff_logged = True"), source.index(call))
        self.assertLess(source.index(call), source.index("geometry_runtime = self._get_window_geometry_runtime()"))

    def test_first_show_event_cloaks_only_once(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        from main.window_lifecycle import WindowLifecycleMixin

        class _Base:
            def installEventFilter(self, _event_filter) -> None:
                pass

            def showEvent(self, _event) -> None:
                pass

        class Window(WindowLifecycleMixin, _Base):
            def __init__(self) -> None:
                self.startup_state = SimpleNamespace(ttff_logged=False, ttff_ms=None)

        window = Window()
        with (
            patch("main.window_lifecycle.QTimer.singleShot"),
            patch("main.window_lifecycle.emit_startup_metric"),
            patch("main.window_lifecycle.begin_first_frame_reveal") as begin,
        ):
            window.showEvent(object())
            window.showEvent(object())

        begin.assert_called_once_with(window)


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
