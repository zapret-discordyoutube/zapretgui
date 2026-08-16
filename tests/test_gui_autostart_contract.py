from __future__ import annotations

import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))


class _FakeToggle:
    def __init__(self, checked: bool = False):
        self._checked = bool(checked)
        self.set_calls: list[tuple[bool, bool]] = []

    def isChecked(self) -> bool:  # noqa: N802
        return self._checked

    def setChecked(self, checked: bool, block_signals: bool = False):  # noqa: N802
        self.set_calls.append((bool(checked), bool(block_signals)))
        self._checked = bool(checked)


class GuiAutostartContractTests(unittest.TestCase):
    def test_registers_elevated_logon_task_in_scheduler(self) -> None:
        from autostart import scheduled_task_api

        exe_path = r"C:\Program Files\Zapret\_internal\Zapret.exe"
        captured_xml = b""

        def run_schtasks(arguments):
            nonlocal captured_xml
            self.assertEqual(
                arguments[:3],
                ["/Create", "/TN", scheduled_task_api.AUTOSTART_TASK_NAME],
            )
            xml_path = Path(arguments[4])
            captured_xml = xml_path.read_bytes()
            return subprocess.CompletedProcess(arguments, 0, b"SUCCESS", b"")

        with (
            patch.object(scheduled_task_api, "_current_user_id", return_value=r"DESKTOP\Tester"),
            patch.object(scheduled_task_api, "_run_schtasks", side_effect=run_schtasks),
        ):
            result = scheduled_task_api.create_or_update_autostart_task(exe_path)

        self.assertTrue(result)
        root = ET.fromstring(captured_xml)
        values = {
            node.tag.rsplit("}", 1)[-1]: str(node.text or "")
            for node in root.iter()
        }
        self.assertEqual(values["UserId"], r"DESKTOP\Tester")
        self.assertEqual(values["Delay"], "PT3S")
        self.assertEqual(values["LogonType"], "InteractiveToken")
        self.assertEqual(values["RunLevel"], "HighestAvailable")
        self.assertEqual(values["MultipleInstancesPolicy"], "IgnoreNew")
        self.assertEqual(values["DisallowStartIfOnBatteries"], "false")
        self.assertEqual(values["StopIfGoingOnBatteries"], "false")
        self.assertEqual(values["ExecutionTimeLimit"], "PT0S")
        self.assertEqual(values["Command"], exe_path)
        self.assertEqual(values["Arguments"], "--tray")
        self.assertEqual(values["WorkingDirectory"], r"C:\Program Files\Zapret")

    def test_reads_task_action_from_utf16_schtasks_xml(self) -> None:
        from autostart import scheduled_task_api

        payload = scheduled_task_api._build_autostart_task_xml(
            r"C:\Zapret\_internal\Zapret.exe",
            r"DESKTOP\Tester",
        )
        result = subprocess.CompletedProcess([], 0, payload, b"")
        with patch.object(scheduled_task_api, "_run_schtasks", return_value=result) as run:
            action = scheduled_task_api.get_autostart_task_action()

        self.assertEqual(action, (r"C:\Zapret\_internal\Zapret.exe", "--tray"))
        run.assert_called_once_with(
            ["/Query", "/TN", scheduled_task_api.AUTOSTART_TASK_NAME, "/XML", "ONE"]
        )

    def test_task_xml_preserves_windows_paths_with_xml_characters(self) -> None:
        from autostart import scheduled_task_api

        exe_path = r"C:\Zapret & Tools\_internal\Zapret.exe"
        payload = scheduled_task_api._build_autostart_task_xml(
            exe_path,
            r"DESKTOP\Tester",
        )
        root = ET.fromstring(payload)
        values = {
            node.tag.rsplit("}", 1)[-1]: str(node.text or "")
            for node in root.iter()
        }
        self.assertEqual(values["Command"], exe_path)
        self.assertEqual(values["WorkingDirectory"], r"C:\Zapret & Tools")
        self.assertEqual(
            scheduled_task_api._first_task_action(payload.decode("utf-16")),
            (exe_path, "--tray"),
        )

    def test_autostart_runtime_does_not_import_com(self) -> None:
        from autostart import scheduled_task_api

        source = Path(scheduled_task_api.__file__).read_text(encoding="utf-8")
        self.assertNotIn("pythoncom", source)
        self.assertNotIn("win32com", source)
        self.assertNotIn('Dispatch("Schedule.Service")', source)
        self.assertIn("schtasks.exe", source)

    def test_rejects_flat_or_source_autostart_target(self) -> None:
        from autostart import scheduled_task_api

        with patch.object(scheduled_task_api, "_run_schtasks") as run_schtasks:
            result = scheduled_task_api.create_or_update_autostart_task(
                r"C:\Program Files\Zapret\Zapret.exe"
            )

        self.assertFalse(result)
        run_schtasks.assert_not_called()

    def test_enable_gui_autostart_creates_task_and_removes_legacy_shortcut(self) -> None:
        from autostart.public import enable_gui_autostart
        from config.runtime_layout import APPLICATION_PATHS

        with (
            patch("autostart.startup_shortcut_api.delete_startup_shortcut", return_value=True) as delete_shortcut,
            patch("autostart.scheduled_task_api.create_or_update_autostart_task", return_value=True) as create_task,
        ):
            result = enable_gui_autostart()

        self.assertTrue(result.success)
        delete_shortcut.assert_called_once()
        create_task.assert_called_once_with(str(APPLICATION_PATHS.executable))

    def test_enable_gui_autostart_returns_readable_error_message(self) -> None:
        from autostart.public import enable_gui_autostart

        with (
            patch("autostart.startup_shortcut_api.delete_startup_shortcut", return_value=False),
            patch("autostart.scheduled_task_api.create_or_update_autostart_task", return_value=False),
        ):
            result = enable_gui_autostart()

        self.assertFalse(result.success)
        self.assertFalse(result.restart_requested)
        self.assertIn("Не удалось включить автозапуск", result.message)

    def test_disable_gui_autostart_removes_task_and_legacy_shortcut(self) -> None:
        from autostart.public import disable_gui_autostart

        with (
            patch("autostart.scheduled_task_api.delete_autostart_task", return_value=True),
            patch("autostart.startup_shortcut_api.delete_startup_shortcut", return_value=True),
        ):
            result = disable_gui_autostart()

        self.assertTrue(result.success)
        self.assertEqual(result.removed_count, 2)

    def test_migration_is_noop_when_autostart_disabled(self) -> None:
        from autostart.public import ensure_gui_autostart_migrated

        with (
            patch("settings.store.get_gui_autostart_enabled", return_value=False),
            patch("autostart.scheduled_task_api.create_or_update_autostart_task") as create_task,
        ):
            migrated = ensure_gui_autostart_migrated()

        self.assertFalse(migrated)
        create_task.assert_not_called()

    def test_migration_is_noop_when_task_matches_and_no_shortcut(self) -> None:
        from autostart.public import ensure_gui_autostart_migrated
        from config.runtime_layout import APPLICATION_PATHS

        shortcut_path = Path(r"C:\nonexistent\ZapretGUI.lnk")
        task_path = str(APPLICATION_PATHS.executable).swapcase()
        with (
            patch("settings.store.get_gui_autostart_enabled", return_value=True),
            patch(
                "autostart.scheduled_task_api.get_autostart_task_action",
                return_value=(task_path, "--tray"),
            ),
            patch(
                "autostart.startup_shortcut_api.get_startup_shortcut_path",
                return_value=shortcut_path,
            ),
            patch("autostart.scheduled_task_api.create_or_update_autostart_task") as create_task,
        ):
            migrated = ensure_gui_autostart_migrated()

        self.assertFalse(migrated)
        create_task.assert_not_called()

    def test_migration_replaces_legacy_shortcut_with_task(self) -> None:
        from autostart.public import ensure_gui_autostart_migrated
        from config.runtime_layout import APPLICATION_PATHS

        with tempfile.TemporaryDirectory() as tmp_dir:
            legacy_shortcut = Path(tmp_dir) / "ZapretGUI.lnk"
            legacy_shortcut.write_bytes(b"legacy")

            with (
                patch("settings.store.get_gui_autostart_enabled", return_value=True),
                patch("autostart.scheduled_task_api.get_autostart_task_action", return_value=None),
                patch(
                    "autostart.startup_shortcut_api.get_startup_shortcut_path",
                    return_value=legacy_shortcut,
                ),
                patch(
                    "autostart.scheduled_task_api.create_or_update_autostart_task",
                    return_value=True,
                ) as create_task,
            ):
                migrated = ensure_gui_autostart_migrated()

            self.assertTrue(migrated)
            create_task.assert_called_once_with(str(APPLICATION_PATHS.executable))
            self.assertFalse(legacy_shortcut.exists())

    def test_migration_replaces_task_with_wrong_arguments(self) -> None:
        from autostart.public import ensure_gui_autostart_migrated
        from config.runtime_layout import APPLICATION_PATHS

        shortcut_path = Path(r"C:\nonexistent\ZapretGUI.lnk")
        with (
            patch("settings.store.get_gui_autostart_enabled", return_value=True),
            patch(
                "autostart.scheduled_task_api.get_autostart_task_action",
                return_value=(str(APPLICATION_PATHS.executable), ""),
            ),
            patch(
                "autostart.startup_shortcut_api.get_startup_shortcut_path",
                return_value=shortcut_path,
            ),
            patch(
                "autostart.scheduled_task_api.create_or_update_autostart_task",
                return_value=True,
            ) as create_task,
            patch("autostart.startup_shortcut_api.delete_startup_shortcut"),
        ):
            migrated = ensure_gui_autostart_migrated()

        self.assertTrue(migrated)
        create_task.assert_called_once_with(str(APPLICATION_PATHS.executable))

    def test_autostart_error_notification_payload_is_user_readable(self) -> None:
        from autostart.ui.notifications import build_autostart_error_notification

        payload = build_autostart_error_notification("Подробности Планировщика")

        self.assertEqual(payload["level"], "error")
        self.assertEqual(payload["title"], "Автозапуск не включён")
        self.assertIn("Подробности Планировщика", payload["content"])
        self.assertEqual(payload["source"], "autostart.gui")
        self.assertEqual(payload["queue"], "immediate")

    def test_gui_autostart_lives_in_program_settings_snapshot(self) -> None:
        from core.runtime.program_settings_runtime_service import ProgramSettingsRuntimeService

        settings = {
            "program": {
                "dpi_autostart": True,
                "gui_autostart_enabled": True,
                "defender_disabled": False,
                "max_blocked": False,
                "russian_state_media_blocked": False,
            },
            "window": {
                "tray_close_mode": "normal",
            },
        }
        with (
            patch("settings.store.read_settings", return_value=settings),
            patch("windows_features.defender_manager.WindowsDefenderManager") as defender_cls,
            patch("windows_features.max_blocker.is_max_blocked", return_value=False),
        ):
            defender_cls.return_value.is_defender_disabled.return_value = False
            snapshot = ProgramSettingsRuntimeService().read_snapshot()

        self.assertTrue(snapshot.gui_autostart_enabled)
        self.assertEqual(snapshot.revision[1], True)

    def test_gui_autostart_toggle_uses_program_settings_action(self) -> None:
        from autostart.public import GuiAutostartResult
        from program_settings.commands import set_gui_autostart_enabled

        with (
            patch("autostart.public.enable_gui_autostart", return_value=GuiAutostartResult(success=True)) as enable,
            patch("autostart.public.save_gui_autostart_enabled", return_value=True) as save,
        ):
            result = set_gui_autostart_enabled(True)

        enable.assert_called_once()
        save.assert_called_once_with(True)
        self.assertEqual(result.level, "success")
        self.assertIsNone(result.revert_checked)

    def test_gui_autostart_snapshot_sync_blocks_toggle_signal(self) -> None:
        from core.runtime.program_settings_runtime_service import ProgramSettingsSnapshot
        from presets.ui.control.control_page_runtime_shared import apply_program_settings_toggles

        toggle = _FakeToggle(False)
        snapshot = ProgramSettingsSnapshot(
            revision=(False, True, "normal", False, False, False),
            auto_dpi_enabled=False,
            gui_autostart_enabled=True,
            tray_close_mode="normal",
            defender_disabled=False,
            max_blocked=False,
            russian_state_media_blocked=False,
        )

        apply_program_settings_toggles(snapshot, gui_autostart_toggle=toggle)

        self.assertEqual(toggle.set_calls, [(True, True)])

    def test_gui_autostart_toggle_is_top_program_settings_row_for_both_modes(self) -> None:
        import inspect

        import presets.ui.control.zapret1.sections_build as winws1_sections
        import presets.ui.control.zapret2.sections_build as winws2_sections

        for source in (
            inspect.getsource(winws1_sections.build_winws1_pages_settings_sections),
            inspect.getsource(winws2_sections.build_winws2_pages_settings_sections),
        ):
            self.assertIn("gui_autostart_toggle", source)
            self.assertLess(
                source.index("program_settings_card.addSettingCard(gui_autostart_toggle)"),
                source.index("program_settings_card.addSettingCard(auto_dpi_toggle)"),
            )

    def test_autostart_is_no_longer_registered_as_standalone_page(self) -> None:
        import ui.pages as pages
        from app.page_names import PageName
        from app.search_index import SEARCH_ENTRIES
        from ui.navigation.schema import PAGE_ROUTE_SPECS
        from ui.page_composition import PAGE_DEPS_BUILDERS

        self.assertFalse(hasattr(PageName, "AUTOSTART"))
        self.assertNotIn("AutostartPage", pages.__all__)
        self.assertFalse(
            any(entry.entry_id.startswith("autostart.") for entry in SEARCH_ENTRIES)
        )
        self.assertFalse(
            any(
                str(getattr(page_name, "name", "")) == "AUTOSTART"
                for page_name in (*PAGE_ROUTE_SPECS.keys(), *PAGE_DEPS_BUILDERS.keys())
            )
        )


if __name__ == "__main__":
    unittest.main()
