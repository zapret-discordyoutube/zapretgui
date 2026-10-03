from __future__ import annotations

import os
import inspect
import unittest
from types import SimpleNamespace


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from ui.fluent_app_window import ZapretFluentWindow
from ui.navigation.search import attach_sidebar_search_to_titlebar, update_titlebar_search_width
from ui.window_ui_facade import _SidebarSearchNavWidget


class FluentAppWindowChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_window_uses_qfluentwidgets_content_margins_without_resize_compensation(self) -> None:
        window = ZapretFluentWindow()
        margins = window.widgetLayout.contentsMargins()

        self.assertEqual(margins.top(), 48)
        self.assertEqual(margins.right(), 0)
        self.assertEqual(margins.bottom(), 0)

    def test_background_image_is_decoded_once_downscaled_and_released(self) -> None:
        import tempfile
        from unittest.mock import patch

        from PyQt6.QtGui import QColor, QPixmap

        from ui import fluent_app_window

        window = ZapretFluentWindow()
        window.resize(800, 600)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bg.png")
            big = QPixmap(4000, 3000)
            big.fill(QColor("red"))
            self.assertTrue(big.save(path))

            window.set_background_image(path)
            source = window._bg_source.size()
            self.assertLessEqual(source.width(), 2560)
            self.assertLessEqual(source.height(), 1600)

            with patch.object(fluent_app_window, "QPixmap", wraps=QPixmap) as pixmap_cls:
                window.resize(900, 700)
                window._rescale_bg()
            loaded_from_disk = [call for call in pixmap_cls.call_args_list if call.args and isinstance(call.args[0], str)]
            self.assertEqual(loaded_from_disk, [])

        window.set_background_image(None)
        self.assertIsNone(window._bg_source)
        self.assertTrue(window._bg_label.pixmap().isNull())

    def test_cursor_enter_leave_does_not_repaint_whole_window(self) -> None:
        # qfluentwidgets на вход/уход курсора, щелчок и фокус запускает анимацию
        # цвета фона окна, и каждый её кадр перерисовывает всё окно. Цвет при
        # этом тот же: замер на win10 — 15.6% ядра при 10 переходах в секунду.
        from unittest.mock import patch

        from PyQt6.QtCore import QEvent, QPointF
        from PyQt6.QtGui import QColor, QEnterEvent

        window = ZapretFluentWindow()
        self.addCleanup(window.deleteLater)
        point = QPointF(10.0, 10.0)

        with patch.object(window, "update") as repaint:
            for _ in range(5):
                window.enterEvent(QEnterEvent(point, point, point))
                window.leaveEvent(QEvent(QEvent.Type.Leave))
            QApplication.processEvents()
            repaint.assert_not_called()

            # Настоящая смена цвета (сила тонировки окна) по-прежнему доходит.
            with patch.object(window, "isMicaEffectEnabled", return_value=True):
                window.set_tint_overlay(10, 20, 30, 200)
                window.backgroundColorAni.setCurrentTime(window.backgroundColorAni.duration())
                QApplication.processEvents()
                repaint.assert_called()
                self.assertEqual(QColor(window.backgroundColor), QColor(10, 20, 30, 200))

    def test_window_chrome_has_no_legacy_border_radius_or_handle_hooks(self) -> None:
        source = inspect.getsource(ZapretFluentWindow)

        self.assertNotIn("WINDOW_RESIZE_SAFE_MARGIN", source)
        self.assertNotIn("_apply_window_content_margins", source)
        self.assertNotIn("_update_border_radius", source)
        self.assertNotIn("_set_handles_visible", source)
        self.assertFalse(hasattr(ZapretFluentWindow, "set_zoom_chrome_compact"))

    def test_replaced_titlebar_is_removed_from_window_event_filters(self) -> None:
        class EventFilterTrackingWindow(ZapretFluentWindow):
            def removeEventFilter(self, event_filter) -> None:  # noqa: N802
                detached = self.__dict__.setdefault("_detached_event_filters", [])
                detached.append(event_filter)
                super().removeEventFilter(event_filter)

        window = EventFilterTrackingWindow()
        detached = window.__dict__.get("_detached_event_filters", [])

        self.assertGreaterEqual(len(detached), 1)
        self.assertNotIn(window.titleBar, detached)
        self.assertTrue(all(hasattr(title_bar, "maxBtn") for title_bar in detached))

    def test_fluent_window_does_not_own_app_geometry_policy(self) -> None:
        source = inspect.getsource(ZapretFluentWindow)

        self.assertNotIn("setMinimumSize(", source)

    def test_titlebar_search_has_screen_reader_text(self) -> None:
        search_widget = _SidebarSearchNavWidget()
        self.addCleanup(search_widget.deleteLater)

        self.assertEqual(search_widget.accessibleName(), "Глобальный поиск по ZapretGUI")
        self.assertEqual(search_widget.property("screenReaderStateText"), "Глобальный поиск по ZapretGUI")
        self.assertIn("страницу, preset или profile", search_widget.accessibleDescription())
        self.assertEqual(search_widget._search.accessibleName(), "Глобальный поиск по ZapretGUI")
        self.assertEqual(search_widget._search.property("screenReaderStateText"), "Глобальный поиск по ZapretGUI")
        self.assertIn("страницу, preset или profile", search_widget._search.accessibleDescription())

    def test_window_only_renders_application_owned_icon(self) -> None:
        source = inspect.getsource(ZapretFluentWindow)

        self.assertIn("_sync_titlebar_icon_from_application", source)
        self.assertIn("app.windowIcon()", source)
        self.assertNotIn("resolve_existing_app_icon_path", source)
        self.assertNotIn("setWindowIcon", source)
        self.assertNotIn("singleShot", source)
        self.assertNotIn("os.path.exists", source)
        self.assertNotIn("ICON_DEV_PATH", source)
        self.assertNotIn("ICON_PATH", source)

    def test_titlebar_search_is_centered_in_the_whole_window(self) -> None:
        window = ZapretFluentWindow()
        search_widget = _SidebarSearchNavWidget()
        window.ui_session = SimpleNamespace(
            sidebar_search_nav_widget=search_widget,
            sidebar_search_titlebar_attached=False,
        )
        window.resize(1571, 1070)
        window.show()
        self._app.processEvents()

        attach_sidebar_search_to_titlebar(window)
        update_titlebar_search_width(window)
        window.titleBar.hBoxLayout.activate()
        self._app.processEvents()

        search_center = window.titleBar.x() + search_widget.x() + search_widget.width() / 2
        window_center = window.width() / 2

        self.assertAlmostEqual(search_center, window_center, delta=4)

    def test_titlebar_search_does_not_overlap_window_buttons_on_narrow_window(self) -> None:
        window = ZapretFluentWindow()
        search_widget = _SidebarSearchNavWidget()
        window.ui_session = SimpleNamespace(
            sidebar_search_nav_widget=search_widget,
            sidebar_search_titlebar_attached=False,
        )
        window.resize(520, 700)
        window.show()
        self._app.processEvents()

        attach_sidebar_search_to_titlebar(window)
        update_titlebar_search_width(window)
        window.titleBar.hBoxLayout.activate()
        self._app.processEvents()

        layout = window.titleBar.hBoxLayout
        search_index = layout.indexOf(search_widget)
        right_controls = layout.itemAt(search_index + 2)

        search_right = search_widget.geometry().right()
        controls_left = right_controls.geometry().left()

        self.assertLess(search_right, controls_left)


if __name__ == "__main__":
    unittest.main()
