from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TrayMenuContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PyQt6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_tray_launch_state_reads_runtime_snapshot_contract(self) -> None:
        from app.feature_facades.tray import TrayFeature

        runtime_feature = SimpleNamespace(
            snapshot=Mock(return_value=SimpleNamespace(running=True, phase="running"))
        )
        feature = TrayFeature(
            _deps=SimpleNamespace(),
            _runtime_feature=runtime_feature,
            _telegram_proxy_feature=SimpleNamespace(),
        )

        self.assertEqual(feature.launch_state(), (True, "running"))

    def test_legacy_right_click_callback_opens_context_menu_at_cursor(self) -> None:
        import tray

        tray.WM_CONTEXTMENU = 0x007B
        tray.WM_RBUTTONUP = 0x0205
        tray.WM_LBUTTONUP = 0x0202
        tray.WM_LBUTTONDBLCLK = 0x0203
        tray.NIN_SELECT = 0x0400
        tray.NIN_KEYSELECT = 0x0401

        manager = tray.SystemTrayManager.__new__(tray.SystemTrayManager)
        manager.show_context_menu = Mock()
        manager._schedule_visibility_toggle = Mock()

        with patch.object(tray.QTimer, "singleShot", side_effect=lambda _ms, callback: callback()):
            tray.SystemTrayManager._handle_native_callback(manager, tray.WM_RBUTTONUP, anchor_x=1, anchor_y=0)

        manager.show_context_menu.assert_called_once_with(anchor_x=None, anchor_y=None)
        manager._schedule_visibility_toggle.assert_not_called()

    def _manager(self, *, phase: str = "running", snapshot=None, visible: bool = False):
        import tray

        feature = SimpleNamespace(
            launch_phase=Mock(return_value=phase),
            preset_snapshot=Mock(return_value=snapshot),
            telegram_proxy_label=Mock(return_value="Telegram Proxy: выкл"),
            refresh_preset_snapshot=Mock(),
            restart_dpi=Mock(),
            toggle_dpi=Mock(),
            activate_preset=Mock(return_value=True),
            apply_window_opacity=Mock(),
        )
        port = SimpleNamespace(
            is_visible=Mock(return_value=visible),
            ui_language=Mock(return_value="ru"),
        )
        manager = tray.SystemTrayManager.__new__(tray.SystemTrayManager)
        manager.window_port = port
        manager._tray_feature = feature
        manager._launch_phase = ""
        manager._launch_method = "zapret2_mode"
        manager._preset_name = ""
        manager._popup = None
        manager._stopped_notify_timer = None
        manager._status_icon_key = ""
        manager._status_icon_handles = {}
        manager._tooltip = ""
        manager._icon_visible = False
        manager._message_window = None
        return manager, feature, port

    @staticmethod
    def _snapshot(count: int = 0):
        from tray_workers import TrayPresetSnapshot

        presets = (("default.txt", "Default v1"), ("game.txt", "Game filter"))
        presets += tuple((f"extra{i}.txt", f"Extra {i}") for i in range(count))
        return TrayPresetSnapshot(
            launch_method="zapret2_mode",
            presets=presets,
            selected_file_name="default.txt",
            selected_display_name="Default v1",
        )

    @staticmethod
    def _rows(manager, page: str = "main"):
        return manager._menu_model().page(page).rows

    def _popup(self, manager):
        popup = manager._ensure_popup()
        self.addCleanup(popup.deleteLater)
        self.addCleanup(popup.hide)
        popup.set_model(manager._menu_model())
        return popup

    def test_tray_menu_main_action_follows_launch_phase(self) -> None:
        cases = {
            "running": ("Остановить Zapret", True),
            "starting": ("Остановить Zapret", True),
            "stopping": ("Zapret останавливается…", False),
            "stopped": ("Запустить Zapret", True),
            "failed": ("Запустить Zapret", True),
        }
        for phase, (text, enabled) in cases.items():
            with self.subTest(phase=phase):
                manager, feature, _port = self._manager(phase=phase)
                rows = self._rows(manager)
                launch = next(row for row in rows if row.command == "toggle_dpi")

                self.assertEqual((launch.text, launch.enabled), (text, enabled))
                self.assertEqual("Перезапустить" in [row.text for row in rows], phase == "running")
                manager._run_menu_command(launch.command, launch.arg)
                feature.toggle_dpi.assert_called_once_with()

    def test_tray_menu_header_shows_mode_status_and_preset(self) -> None:
        manager, _feature, _port = self._manager(phase="running", snapshot=self._snapshot())
        header = self._rows(manager)[0]

        self.assertEqual(header.kind, "status")
        self.assertEqual(header.text, "Zapret 2 · работает")
        self.assertEqual(header.detail, "Default v1")
        self.assertEqual(header.command, "show_window")

    def test_tray_preset_page_marks_active_and_activates_choice(self) -> None:
        manager, feature, _port = self._manager(snapshot=self._snapshot())
        entry = next(row for row in self._rows(manager) if row.page == "presets")
        self.assertEqual((entry.text, entry.detail), ("Пресет", "Default v1"))

        items = [row for row in self._rows(manager, "presets") if row.kind == "item"]
        self.assertEqual([(row.text, row.checked) for row in items], [("Default v1", True), ("Game filter", False)])

        manager._run_menu_command(items[1].command, items[1].arg)
        feature.activate_preset.assert_called_once_with("game.txt", "Game filter")

    def test_tray_preset_page_hidden_for_orchestra(self) -> None:
        from tray_workers import TrayPresetSnapshot

        manager, _feature, _port = self._manager(snapshot=TrayPresetSnapshot(launch_method="orchestra"))
        model = manager._menu_model()

        self.assertNotIn("presets", model.pages)
        self.assertNotIn("presets", [row.page for row in model.page("main").rows])

    def test_tray_menu_commands_are_all_handled(self) -> None:
        manager, _feature, _port = self._manager(snapshot=self._snapshot())
        model = manager._menu_model()
        commands = {row.command for page in model.pages.values() for row in page.rows if row.command}
        for name in ("show_window", "_toggle_primary_visibility_action", "show_console", "exit_only",
                     "exit_and_stop", "_toggle_tg_proxy", "_set_window_opacity"):
            setattr(manager, name, Mock())

        with patch("tray.log") as log:
            for command in sorted(commands):
                arg = ("game.txt", "Game filter") if command == "activate_preset" else 50
                manager._run_menu_command(command, arg)

        log.assert_not_called()

    def test_tray_menu_window_is_reused_and_presets_are_refreshed_on_each_open(self) -> None:
        manager, feature, _port = self._manager(snapshot=self._snapshot())
        popup = self._popup(manager)

        manager.show_context_menu()
        self.assertTrue(popup.isVisible())
        manager.show_context_menu()
        popup.hide()
        manager.show_context_menu()

        self.assertIs(manager._ensure_popup(), popup)
        self.assertEqual(feature.refresh_preset_snapshot.call_count, 2)

    def test_open_tray_menu_follows_launch_status(self) -> None:
        manager, feature, _port = self._manager(phase="running", snapshot=self._snapshot())
        manager._status_icon_handle_for = Mock(return_value=None)
        manager._modify_icon = Mock()
        manager.show_notification = Mock()
        popup = self._popup(manager)
        manager.show_context_menu()

        feature.launch_phase.return_value = "stopped"
        manager.apply_launch_status(phase="stopped", launch_method="zapret2_mode", preset_name="Default v1")

        self.assertEqual(popup.rows()[0].text, "Zapret 2 · остановлен")
        self.assertIn("Запустить Zapret", [row.text for row in popup.rows()])

    def test_long_preset_list_scrolls_inside_a_small_window(self) -> None:
        from ui.tray_menu import style

        manager, _feature, _port = self._manager(snapshot=self._snapshot(count=150))
        popup = self._popup(manager)
        manager.show_context_menu()
        main_height = popup.height()
        popup.open_page("presets")

        self.assertEqual(len(popup.rows()), 154)
        self.assertLessEqual(popup._view_height, style.MAX_LIST_ROWS * style.ROW_HEIGHT)
        self.assertLess(popup.height(), main_height + 6 * style.ROW_HEIGHT)
        self.assertEqual(popup.width(), style.WIDTH + 2 * style.SHADOW)

    def test_preset_page_opens_scrolled_to_the_active_preset(self) -> None:
        from tray_workers import TrayPresetSnapshot

        base = self._snapshot(count=150)
        snapshot = TrayPresetSnapshot(
            launch_method=base.launch_method,
            presets=base.presets,
            selected_file_name="extra100.txt",
            selected_display_name="Extra 100",
        )
        manager, _feature, _port = self._manager(snapshot=snapshot)
        popup = self._popup(manager)
        manager.show_context_menu()
        popup.open_page("presets")

        checked = next(index for index, row in enumerate(popup.rows()) if row.checked)
        self.assertTrue(popup._body_rect().contains(popup._row_rect(checked)))

    def test_typing_filters_presets_and_escape_steps_back(self) -> None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        manager, _feature, _port = self._manager(snapshot=self._snapshot(count=20))
        popup = self._popup(manager)
        manager.show_context_menu()
        popup.open_page("presets")
        height = popup.height()

        QTest.keyClicks(popup, "game")
        self.assertEqual([row.text for row in popup.rows() if row.kind == "item"], ["Game filter"])
        self.assertEqual(popup.height(), height)

        QTest.keyClicks(popup, "zz")
        self.assertEqual([row.kind for row in popup.rows()][-1], "note")

        QTest.keyClick(popup, Qt.Key.Key_Escape)
        self.assertEqual((popup.query(), popup.page_key()), ("", "presets"))
        QTest.keyClick(popup, Qt.Key.Key_Escape)
        self.assertEqual(popup.page_key(), "main")
        QTest.keyClick(popup, Qt.Key.Key_Escape)
        self.assertFalse(popup.isVisible())

    def test_click_on_row_closes_menu_and_runs_command(self) -> None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        manager, feature, _port = self._manager(phase="running", snapshot=self._snapshot())
        popup = self._popup(manager)
        manager.show_context_menu()
        rows = popup.rows()

        restart = next(index for index, row in enumerate(rows) if row.command == "restart_dpi")
        QTest.mouseClick(popup, Qt.MouseButton.LeftButton, pos=popup._row_rect(restart).center())
        self.assertFalse(popup.isVisible())
        self._app.processEvents()
        feature.restart_dpi.assert_called_once_with()

    def test_click_on_disabled_row_does_nothing(self) -> None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        manager, feature, _port = self._manager(phase="stopping", snapshot=self._snapshot())
        popup = self._popup(manager)
        manager.show_context_menu()

        launch = next(index for index, row in enumerate(popup.rows()) if row.command == "toggle_dpi")
        QTest.mouseClick(popup, Qt.MouseButton.LeftButton, pos=popup._row_rect(launch).center())
        self._app.processEvents()

        self.assertTrue(popup.isVisible())
        feature.toggle_dpi.assert_not_called()

    def test_keyboard_walks_rows_and_opens_preset_page(self) -> None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        manager, _feature, _port = self._manager(phase="stopped", snapshot=self._snapshot())
        popup = self._popup(manager)
        manager.show_context_menu()

        visited = []
        for _ in range(3):
            QTest.keyClick(popup, Qt.Key.Key_Down)
            visited.append(popup.current_row().text)
        self.assertEqual(visited, ["Zapret 2 · остановлен", "Запустить Zapret", "Пресет"])

        QTest.keyClick(popup, Qt.Key.Key_Right)
        self.assertEqual(popup.page_key(), "presets")
        QTest.keyClick(popup, Qt.Key.Key_Left)
        self.assertEqual(popup.page_key(), "main")

    def test_menu_stays_inside_the_screen_and_keeps_its_edge(self) -> None:
        from PyQt6.QtCore import QPoint, QRect
        from ui.tray_menu.popup import menu_origin

        available = QRect(0, 0, 1920, 1040)
        anchor = QPoint(1900, 1070)
        short = menu_origin(anchor=anchor, edge_y=1062, opens_up=True, width=292, height=300, available=available)
        tall = menu_origin(anchor=anchor, edge_y=1062, opens_up=True, width=292, height=450, available=available)
        down = menu_origin(anchor=QPoint(-50, 5), edge_y=13, opens_up=False, width=292, height=300, available=available)

        self.assertEqual((short.x(), short.y() + 300), (1920 - 292 - 8, 1040 - 8))
        self.assertEqual(tall.y() + 450, short.y() + 300)
        self.assertEqual((down.x(), down.y()), (8, 13))

    def test_every_menu_icon_is_drawn(self) -> None:
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QColor, QImage, QPainter
        from ui.tray_menu import style

        manager, _feature, _port = self._manager(phase="running", snapshot=self._snapshot())
        used = {row.icon for page in manager._menu_model().pages.values() for row in page.rows if row.icon}
        self.assertLessEqual(used, set(style.tray_icon_names()))

        for name in style.tray_icon_names():
            with self.subTest(icon=name):
                image = QImage(32, 32, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(0)
                painter = QPainter(image)
                style.paint_tray_icon(painter, name, QRectF(0, 0, 32, 32), QColor("#ffffff"))
                painter.end()
                self.assertTrue(any(image.pixel(x, y) for x in range(32) for y in range(32)))

    def test_tray_menu_does_not_use_library_menu(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src"
        sources = [root / "tray.py", *sorted((root / "ui" / "tray_menu").glob("*.py"))]

        for path in sources:
            with self.subTest(file=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("RoundMenu", source)
                self.assertNotIn("QMenu", source)

    def test_tray_icon_dot_color_by_phase(self) -> None:
        import tray

        self.assertEqual(tray.tray_icon_dot_color("running"), "#6ccb5f")
        self.assertEqual(tray.tray_icon_dot_color("starting"), "#f5a623")
        self.assertEqual(tray.tray_icon_dot_color("stopping"), "#f5a623")
        self.assertIsNone(tray.tray_icon_dot_color("stopped"))
        self.assertIsNone(tray.tray_icon_dot_color("failed"))

    def test_status_icon_draws_dot_in_corner_and_keeps_logo(self) -> None:
        import tray
        from PyQt6.QtGui import QColor, QImage

        base = QImage(64, 64, QImage.Format.Format_ARGB32)
        base.fill(QColor("#3366cc"))

        image = tray.render_status_icon_image(base, "#6ccb5f", 20)
        plain = tray.render_status_icon_image(base, None, 20)

        self.assertEqual(QColor(image.pixel(16, 16)).name(), "#6ccb5f")
        self.assertEqual(QColor(image.pixel(4, 4)).name(), "#3366cc")
        self.assertEqual(QColor(plain.pixel(16, 16)).name(), "#3366cc")

    def test_tray_tooltip_has_mode_status_and_preset(self) -> None:
        import tray

        tooltip = tray.build_tray_tooltip(
            phase="running",
            launch_method="zapret2_mode",
            preset_name="Default v1",
            language="ru",
        )

        self.assertEqual(tooltip, "Zapret 2 — работает\nПресет: Default v1")

    def test_start_notification_only_when_window_hidden(self) -> None:
        for visible, expected in ((False, 1), (True, 0)):
            with self.subTest(window_visible=visible):
                manager, _feature, _port = self._manager(visible=visible)
                manager.show_notification = Mock()
                manager._status_icon_handle_for = Mock(return_value=None)
                manager._modify_icon = Mock()

                manager.apply_launch_status(phase="starting", launch_method="zapret2_mode", preset_name="Default v1")
                manager.apply_launch_status(phase="running", launch_method="zapret2_mode", preset_name="Default v1")

                self.assertEqual(manager.show_notification.call_count, expected)
                if expected:
                    title, body = manager.show_notification.call_args.args
                    self.assertEqual(title, "Zapret запущен")
                    self.assertIn("Default v1", body)

    def test_first_status_does_not_notify(self) -> None:
        manager, _feature, _port = self._manager()
        manager.show_notification = Mock()
        manager._status_icon_handle_for = Mock(return_value=None)
        manager._modify_icon = Mock()

        manager.apply_launch_status(phase="running", launch_method="zapret2_mode", preset_name="")

        manager.show_notification.assert_not_called()
        manager._modify_icon.assert_called_once_with()

    def test_stop_notification_is_skipped_when_restart_follows(self) -> None:
        import tray

        manager, _feature, _port = self._manager()
        manager.show_notification = Mock()
        manager._status_icon_handle_for = Mock(return_value=None)
        manager._modify_icon = Mock()
        timers = []

        class _Timer:
            def __init__(self) -> None:
                self.stopped = False
                self.callback = None
                timers.append(self)

            def setSingleShot(self, _value) -> None:  # noqa: N802
                return None

            @property
            def timeout(self):
                return SimpleNamespace(connect=lambda callback: setattr(self, "callback", callback))

            def start(self, _ms) -> None:
                return None

            def stop(self) -> None:
                self.stopped = True

        with patch.object(tray, "QTimer", _Timer):
            manager.apply_launch_status(phase="running", launch_method="zapret2_mode", preset_name="")
            manager.apply_launch_status(phase="stopping", launch_method="zapret2_mode", preset_name="")
            manager.apply_launch_status(phase="stopped", launch_method="zapret2_mode", preset_name="")
            self.assertEqual(len(timers), 1)
            manager.apply_launch_status(phase="starting", launch_method="zapret2_mode", preset_name="")

        self.assertTrue(timers[0].stopped)
        manager.show_notification.assert_not_called()

        manager._launch_phase = "stopped"
        manager._show_stopped_notification()
        manager.show_notification.assert_called_once_with("Zapret остановлен", "Обход блокировок выключен")

    def test_tray_feature_toggles_through_launch_control(self) -> None:
        from app.feature_facades.tray import TrayFeature

        launch_control = Mock()
        runtime_feature = Mock()
        feature = TrayFeature(
            _deps=SimpleNamespace(),
            _runtime_feature=runtime_feature,
            _telegram_proxy_feature=SimpleNamespace(),
        )
        feature.configure(launch_control=launch_control)

        feature.toggle_dpi()
        feature.restart_dpi()

        launch_control.toggle.assert_called_once_with()
        launch_control.restart.assert_called_once_with()
        runtime_feature.start.assert_not_called()
        runtime_feature.stop.assert_not_called()

    def test_tray_feature_reads_phase_from_ui_state_store(self) -> None:
        from app.feature_facades.tray import TrayFeature

        store = SimpleNamespace(snapshot=lambda: SimpleNamespace(launch_phase="stopping", launch_running=True))
        feature = TrayFeature(
            _deps=SimpleNamespace(),
            _runtime_feature=SimpleNamespace(),
            _telegram_proxy_feature=SimpleNamespace(),
            _ui_state_store=store,
        )

        self.assertEqual(feature.launch_phase(), "stopping")

    def test_tray_feature_activate_preset_moves_checkmark_and_starts_worker(self) -> None:
        from app.feature_facades.tray import TrayFeature

        presets_feature = Mock()
        feature = TrayFeature(
            _deps=SimpleNamespace(),
            _runtime_feature=SimpleNamespace(),
            _telegram_proxy_feature=SimpleNamespace(),
            _presets_feature=presets_feature,
        )
        feature._preset_snapshot = self._snapshot()
        started = []
        feature._preset_activate_runtime = SimpleNamespace(
            start_qthread_worker=lambda **kwargs: started.append(kwargs)
        )

        self.assertFalse(feature.activate_preset("default.txt", "Default v1"))
        self.assertTrue(feature.activate_preset("game.txt", "Game filter"))

        self.assertTrue(feature.preset_snapshot().is_selected("game.txt"))
        self.assertEqual(len(started), 1)
        started[0]["worker_factory"](7)
        presets_feature.create_preset_activate_worker.assert_called_once_with(
            7,
            launch_method="zapret2_mode",
            file_name="game.txt",
            display_name="Game filter",
            activate_error_level="error",
            activate_error_mode="friendly",
            parent=None,
        )

    def test_tray_preset_snapshot_loader_marks_selected_preset(self) -> None:
        from tray_workers import load_tray_preset_snapshot

        presets_feature = SimpleNamespace(
            list_preset_manifests=lambda _method: [
                SimpleNamespace(file_name="Default.txt", name="Default v1"),
                SimpleNamespace(file_name="game.txt", name="Game filter"),
            ],
            get_selected_source_preset_file_name=lambda _method: "default.txt",
        )

        snapshot = load_tray_preset_snapshot(get_launch_method=lambda: "zapret2_mode", presets_feature=presets_feature)
        orchestra = load_tray_preset_snapshot(get_launch_method=lambda: "orchestra", presets_feature=presets_feature)

        self.assertEqual(snapshot.selected_display_name, "Default v1")
        self.assertTrue(snapshot.is_selected("DEFAULT.TXT"))
        self.assertEqual(orchestra.presets, ())

    def test_native_tray_winapi_calls_have_pointer_safe_ctypes_signatures(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src" / "tray.py").read_text(encoding="utf-8")

        required_signatures = [
            "kernel32.GetModuleHandleW.restype",
            "user32.RegisterClassExW.argtypes",
            "user32.RegisterClassExW.restype",
            "user32.UnregisterClassW.argtypes",
            "user32.UnregisterClassW.restype",
            "user32.CreateWindowExW.argtypes",
            "user32.CreateWindowExW.restype",
            "user32.DestroyWindow.argtypes",
            "user32.DestroyWindow.restype",
            "user32.LoadImageW.argtypes",
            "user32.LoadImageW.restype",
            "user32.LoadIconW.argtypes",
            "user32.LoadIconW.restype",
            "user32.DestroyIcon.argtypes",
            "user32.DestroyIcon.restype",
            "shell32.Shell_NotifyIconW.argtypes",
            "shell32.Shell_NotifyIconW.restype",
        ]

        missing = [signature for signature in required_signatures if signature not in source]

        self.assertEqual(missing, [])

    def test_native_tray_message_window_unregisters_class_on_destroy(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src" / "tray.py").read_text(encoding="utf-8")

        self.assertIn("user32.UnregisterClassW(self.owner._class_name, instance)", source)

    def test_native_tray_class_name_is_unique_per_manager_instance(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src" / "tray.py").read_text(encoding="utf-8")

        self.assertIn('self._class_name = f"Zapret2TrayWindow_{os.getpid()}_{id(self):x}"', source)


if __name__ == "__main__":
    unittest.main()
