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
        from qfluentwidgets import RoundMenu
        from PyQt6.QtWidgets import QWidget

        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        feature = SimpleNamespace(
            launch_phase=Mock(return_value=phase),
            preset_snapshot=Mock(return_value=snapshot),
            telegram_proxy_label=Mock(return_value="Telegram Proxy: выкл"),
            refresh_preset_snapshot=Mock(),
            restart_dpi=Mock(),
            toggle_dpi=Mock(),
            activate_preset=Mock(return_value=True),
        )
        port = SimpleNamespace(
            create_menu=lambda: RoundMenu(parent=owner),
            is_visible=Mock(return_value=visible),
            ui_language=Mock(return_value="ru"),
            exec_popup_menu=Mock(),
        )
        manager = tray.SystemTrayManager.__new__(tray.SystemTrayManager)
        manager.window_port = port
        manager._tray_feature = feature
        manager._launch_phase = ""
        manager._launch_method = "zapret2_mode"
        manager._preset_name = ""
        manager._menu = None
        manager._header = None
        manager._launch_action = None
        manager._stopped_notify_timer = None
        manager._status_icon_key = ""
        manager._status_icon_handles = {}
        manager._tooltip = ""
        manager._icon_visible = False
        manager._message_window = None
        return manager, feature, port

    @staticmethod
    def _snapshot():
        from tray_workers import TrayPresetSnapshot

        return TrayPresetSnapshot(
            launch_method="zapret2_mode",
            presets=(("default.txt", "Default v1"), ("game.txt", "Game filter")),
            selected_file_name="default.txt",
            selected_display_name="Default v1",
        )

    def test_round_tray_menu_uses_global_hairline_fix(self) -> None:
        from ui.popup_menu_style import install_global_round_menu_hairline_fix

        manager, _feature, _port = self._manager()
        with patch("ui.popup_menu_style._is_windows_11_or_newer", return_value=True):
            install_global_round_menu_hairline_fix()
            menu = manager._build_menu()
        self.addCleanup(menu.deleteLater)

        self.assertIn("MenuActionListWidget", menu.styleSheet())
        self.assertIn("zapretgui-round-menu-hairline-begin", menu.styleSheet())
        self.assertNotIn("border-left", menu.styleSheet())

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
                menu = manager._build_menu()
                self.addCleanup(menu.deleteLater)
                texts = [action.text() for action in menu.actions()]

                self.assertEqual(manager._launch_action.text(), text)
                self.assertEqual(manager._launch_action.isEnabled(), enabled)
                self.assertEqual("Перезапустить" in texts, phase == "running")
                manager._launch_action.trigger()
                self.assertEqual(feature.toggle_dpi.call_count, 1 if enabled else 0)

    def test_tray_menu_header_shows_mode_status_and_preset(self) -> None:
        manager, _feature, _port = self._manager(phase="running", snapshot=self._snapshot())
        menu = manager._build_menu()
        self.addCleanup(menu.deleteLater)

        self.assertEqual(manager._header.title_label.text(), "Zapret 2 · работает")
        self.assertEqual(manager._header.preset_label.text(), "Default v1")

    def test_tray_preset_submenu_marks_active_and_activates_choice(self) -> None:
        manager, feature, _port = self._manager(snapshot=self._snapshot())
        menu = manager._build_menu()
        self.addCleanup(menu.deleteLater)

        preset_menu = next(sub for sub in menu._subMenus if sub.title() == "Пресет")
        items = [(action.text(), action.isChecked()) for action in preset_menu.actions()]
        self.assertEqual(items, [("Default v1", True), ("Game filter", False)])

        preset_menu.actions()[1].trigger()
        feature.activate_preset.assert_called_once_with("game.txt", "Game filter")

    def test_tray_preset_submenu_hidden_for_orchestra(self) -> None:
        from tray_workers import TrayPresetSnapshot

        manager, _feature, _port = self._manager(snapshot=TrayPresetSnapshot(launch_method="orchestra"))
        menu = manager._build_menu()
        self.addCleanup(menu.deleteLater)

        self.assertNotIn("Пресет", [sub.title() for sub in menu._subMenus])

    def test_tray_menu_is_rebuilt_and_released_on_each_open(self) -> None:
        manager, feature, port = self._manager()

        manager.show_context_menu()
        manager.show_context_menu()

        self.assertEqual(port.exec_popup_menu.call_count, 2)
        first_menu = port.exec_popup_menu.call_args_list[0].args[0]
        second_menu = port.exec_popup_menu.call_args_list[1].args[0]
        self.assertIsNot(first_menu, second_menu)
        self.assertIsNone(manager._menu)
        self.assertEqual(feature.refresh_preset_snapshot.call_count, 2)

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
