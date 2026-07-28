from __future__ import annotations

"""Наблюдатель обновления и системная страховка.

Наблюдатель — единственный участник, который доживает до конца установки,
поэтому проверяются и его запуск, и правила, по которым он признаёт установку
состоявшейся. Сам скрипт исполняется только на Windows, поэтому его решения
проверяются по тексту: правила там ровно одни и те же.
"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from updater import recovery_hook, update_paths, update_watchdog
from updater.handoff_state import HandoffState, UpdateHandoffRecord, read_record


def _record() -> UpdateHandoffRecord:
    return UpdateHandoffRecord(
        state=HandoffState.PREPARED,
        version="21.1.1.4",
        target_root=r"C:\Zapret\Stable",
        installer_path=r"C:\ProgramData\Zapret\update\stable\Zapret2Setup.exe",
        arguments=("/AUTOUPDATE", "/SILENT"),
    )


class WatchdogScriptContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = update_watchdog.render_watchdog_script()

    def test_script_shares_recovery_hook_location_with_application(self) -> None:
        self.assertIn(f"'HKLM:\\{recovery_hook.RUNONCE_KEY}'", self.script)
        self.assertIn(f"'{recovery_hook.RUNONCE_VALUE_NAME}'", self.script)
        self.assertNotIn("@RUNONCE_KEY@", self.script)

    def test_script_uses_declared_attempt_limits(self) -> None:
        self.assertIn(
            f"$maxAttempts = {update_watchdog.INSTALL_ATTEMPTS}",
            self.script,
        )
        self.assertIn(
            f"$guiExitTimeoutSeconds = {update_watchdog.GUI_EXIT_TIMEOUT_SECONDS}",
            self.script,
        )

    def test_success_requires_both_exit_code_and_installed_version(self) -> None:
        self.assertIn("if ($lastExitCode -ne 0)", self.script)
        self.assertIn("if (Test-VersionInstalled $targetRoot $expectedVersion)", self.script)
        self.assertIn(
            "установщик отчитался об успехе, но версия на диске не изменилась",
            self.script,
        )

    def test_recovery_hook_is_released_only_after_confirmed_success(self) -> None:
        failure_marker = "Save-State $state 'failed'"
        self.assertIn(failure_marker, self.script)

        before_failure, _, after_failure = self.script.partition(failure_marker)
        self.assertIn("Remove-RecoveryHook", before_failure)
        self.assertNotIn("Remove-RecoveryHook", after_failure)

    def test_missing_application_opens_installer_wizard(self) -> None:
        _, _, after_failure = self.script.partition("Save-State $state 'failed'")
        self.assertIn("Start-Process -FilePath $installer -ArgumentList @(\"/DIR=$targetRoot\")", after_failure)
        self.assertIn("Show-Message", after_failure)
        self.assertIn("Start-InstalledApplication", after_failure)

    def test_unattended_mode_touches_neither_windows_nor_applications(self) -> None:
        show_message = self.script[self.script.index("function Show-Message"):]
        start_application = self.script[self.script.index("function Start-InstalledApplication"):]

        self.assertIn("if ($Unattended)", show_message[: show_message.index("\n}")])
        self.assertIn("if ($Unattended) { return }", start_application[: start_application.index("\n}")])
        self.assertIn("(-not $Unattended)", self.script)

    def test_script_waits_for_application_to_exit(self) -> None:
        self.assertIn("Wait-ForProcessExit ([int]$state.gui_pid)", self.script)

    def test_rendered_script_has_no_leftover_placeholders(self) -> None:
        self.assertNotIn("@", self.script.replace("@(", "").replace("@{", ""))

    def test_rendered_script_is_structurally_balanced(self) -> None:
        """Грубая защита от опечатки: сломанный скрипт не запустит установку."""
        for opening, closing in (("{", "}"), ("(", ")"), ("[", "]")):
            self.assertEqual(
                self.script.count(opening),
                self.script.count(closing),
                f"несбалансированные {opening}{closing}",
            )
        self.assertEqual(self.script.count("'") % 2, 0)
        self.assertEqual(self.script.count('"') % 2, 0)

    def test_recovery_mode_stops_when_expected_version_is_already_installed(self) -> None:
        self.assertIn("if ($Recovery)", self.script)
        self.assertIn("Восстановление не требуется", self.script)


class WatchdogCommandTests(unittest.TestCase):
    def test_command_bypasses_execution_policy_and_names_state_file(self) -> None:
        command = update_watchdog.build_watchdog_command(
            script_path=r"C:\ProgramData\Zapret\update\stable\update_watchdog.ps1",
            state_path=r"C:\ProgramData\Zapret\update\stable\handoff.json",
        )

        self.assertEqual(command[0], "powershell")
        self.assertIn("-ExecutionPolicy", command)
        self.assertIn("Bypass", command)
        self.assertIn("-File", command)
        self.assertIn("-StatePath", command)
        self.assertNotIn("-Recovery", command)

    def test_recovery_command_is_marked(self) -> None:
        command = update_watchdog.build_watchdog_command(
            script_path="script.ps1",
            state_path="handoff.json",
            recovery=True,
        )

        self.assertEqual(command[-1], "-Recovery")

    def test_script_is_written_with_bom_and_without_leftovers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            script_path = Path(temp_dir) / "update_watchdog.ps1"
            update_watchdog.install_watchdog_script(script_path)

            self.assertEqual(
                sorted(item.name for item in Path(temp_dir).iterdir()),
                ["update_watchdog.ps1"],
            )
            self.assertTrue(script_path.read_bytes().startswith(b"\xef\xbb\xbf"))


class WatchdogLaunchTests(unittest.TestCase):
    def _launch(self, temp_dir: str, **overrides):
        state_path = Path(temp_dir) / "handoff.json"
        script_path = Path(temp_dir) / "update_watchdog.ps1"
        log_path = Path(temp_dir) / "watchdog.log"

        options = {
            "gui_pid": 4242,
            "state_path": state_path,
            "script_path": script_path,
            "log_path": log_path,
            "is_admin": lambda: True,
            "spawn_detached": Mock(return_value=True),
            "spawn_elevated": Mock(return_value=True),
            "timeout_seconds": 1.0,
            "poll_seconds": 0.1,
            "sleep": lambda _seconds: None,
        }
        options.update(overrides)
        return options, update_watchdog.launch_update_watchdog(_record(), **options)

    def test_launch_writes_prepared_state_and_starts_without_uac_when_elevated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "watchdog.log"

            def spawn(_command):
                log_path.write_text("наблюдатель запущен", encoding="utf-8")
                return True

            options, launched = self._launch(
                temp_dir,
                spawn_detached=Mock(side_effect=spawn),
            )

            self.assertTrue(launched)
            options["spawn_detached"].assert_called_once()
            options["spawn_elevated"].assert_not_called()

            stored = read_record(options["state_path"])
            self.assertEqual(stored.state, HandoffState.PREPARED)
            self.assertEqual(stored.gui_pid, 4242)
            self.assertEqual(stored.version, "21.1.1.4")
            self.assertTrue(Path(options["script_path"]).is_file())

    def test_launch_asks_for_elevation_when_application_is_not_admin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "watchdog.log"

            def spawn(_command):
                log_path.write_text("наблюдатель запущен", encoding="utf-8")
                return True

            options, launched = self._launch(
                temp_dir,
                is_admin=lambda: False,
                spawn_elevated=Mock(side_effect=spawn),
            )

            self.assertTrue(launched)
            options["spawn_elevated"].assert_called_once()
            options["spawn_detached"].assert_not_called()

    def test_launch_fails_when_process_cannot_start(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _options, launched = self._launch(
                temp_dir,
                spawn_detached=Mock(return_value=False),
            )

            self.assertFalse(launched)

    def test_launch_fails_when_watchdog_never_reports_for_duty(self) -> None:
        """Молчащий наблюдатель хуже отсутствующего: приложение бы закрылось."""
        ticks = iter([0.0, 0.5, 1.5, 2.0])

        with tempfile.TemporaryDirectory() as temp_dir:
            _options, launched = self._launch(
                temp_dir,
                spawn_detached=Mock(return_value=True),
                monotonic=lambda: next(ticks),
            )

            self.assertFalse(launched)

    def test_previous_journal_is_kept_aside(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "watchdog.log"
            log_path.write_text("прошлая попытка", encoding="utf-8")

            def spawn(_command):
                log_path.write_text("наблюдатель запущен", encoding="utf-8")
                return True

            self._launch(temp_dir, spawn_detached=Mock(side_effect=spawn))

            previous = Path(temp_dir) / update_watchdog.PREVIOUS_WATCHDOG_LOG_NAME
            self.assertTrue(previous.is_file())
            self.assertEqual(previous.read_text(encoding="utf-8"), "прошлая попытка")


class _FakeRegistryKey:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def __enter__(self) -> "_FakeRegistryKey":
        return self

    def __exit__(self, *_exc_info) -> bool:
        return False


class _FakeRegistry:
    HKEY_LOCAL_MACHINE = 0x80000002
    KEY_SET_VALUE = 0x0002
    REG_SZ = 1

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.opened_keys: list[str] = []

    def CreateKeyEx(self, _root, sub_key, _reserved, _access):  # noqa: N802
        self.opened_keys.append(sub_key)
        return _FakeRegistryKey(self.values)

    def SetValueEx(self, _key, name, _reserved, _value_type, value):  # noqa: N802
        self.values[name] = value

    def DeleteValue(self, _key, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


class RecoveryHookTests(unittest.TestCase):
    def test_hook_stores_recovery_command(self) -> None:
        registry = _FakeRegistry()
        command = update_watchdog.build_watchdog_command(
            script_path=r"C:\ProgramData\Zapret\update\stable\update_watchdog.ps1",
            state_path=r"C:\ProgramData\Zapret\update\stable\handoff.json",
            recovery=True,
        )

        with patch.object(recovery_hook, "winreg", registry):
            self.assertTrue(recovery_hook.set_recovery_hook(command))

        stored = registry.values[recovery_hook.RUNONCE_VALUE_NAME]
        self.assertIn("-Recovery", stored)
        self.assertIn("update_watchdog.ps1", stored)
        self.assertEqual(registry.opened_keys, [recovery_hook.RUNONCE_KEY])

    def test_hook_is_cleared_and_absence_is_not_an_error(self) -> None:
        registry = _FakeRegistry()
        registry.values[recovery_hook.RUNONCE_VALUE_NAME] = "команда"

        with patch.object(recovery_hook, "winreg", registry):
            self.assertTrue(recovery_hook.clear_recovery_hook())
            self.assertTrue(recovery_hook.clear_recovery_hook())

        self.assertEqual(registry.values, {})

    def test_empty_command_is_refused(self) -> None:
        registry = _FakeRegistry()

        with patch.object(recovery_hook, "winreg", registry):
            self.assertFalse(recovery_hook.set_recovery_hook("   "))

        self.assertEqual(registry.values, {})

    def test_unavailable_registry_is_reported_not_raised(self) -> None:
        broken = Mock()
        broken.CreateKeyEx.side_effect = OSError("нет доступа")
        broken.HKEY_LOCAL_MACHINE = 0
        broken.KEY_SET_VALUE = 0

        with patch.object(recovery_hook, "winreg", broken):
            self.assertFalse(recovery_hook.set_recovery_hook(["powershell"]))
            self.assertFalse(recovery_hook.clear_recovery_hook())


class SupervisedInstallationTests(unittest.TestCase):
    def setUp(self) -> None:
        update_paths.reset_update_state_dir_cache()
        self.addCleanup(update_paths.reset_update_state_dir_cache)

    def _handoff(self):
        from updater.update_pipeline import InstallerHandoff

        return InstallerHandoff(
            version="21.1.1.4",
            installer_path=r"C:\ProgramData\Zapret\update\stable\Zapret2Setup.exe",
            arguments=("/AUTOUPDATE", "/SILENT"),
        )

    def test_supervised_start_arms_recovery_before_installation(self) -> None:
        from updater import update_pipeline

        with (
            patch.object(update_pipeline, "set_recovery_hook", return_value=True) as set_hook,
            patch.object(update_pipeline, "clear_recovery_hook") as clear_hook,
            patch.object(update_pipeline, "launch_update_watchdog", return_value=True) as launch,
        ):
            self.assertTrue(update_pipeline.start_supervised_installation(self._handoff()))

        set_hook.assert_called_once()
        launch.assert_called_once()
        clear_hook.assert_not_called()

        record = launch.call_args.args[0]
        self.assertEqual(record.state, HandoffState.PREPARED)
        self.assertEqual(record.version, "21.1.1.4")
        self.assertEqual(record.arguments, ("/AUTOUPDATE", "/SILENT"))

    def test_failed_watchdog_start_releases_recovery_hook(self) -> None:
        """Иначе система обещала бы чинить установку, которая не начиналась."""
        from updater import update_pipeline

        with (
            patch.object(update_pipeline, "set_recovery_hook", return_value=True),
            patch.object(update_pipeline, "clear_recovery_hook") as clear_hook,
            patch.object(update_pipeline, "launch_update_watchdog", return_value=False),
        ):
            self.assertFalse(update_pipeline.start_supervised_installation(self._handoff()))

        clear_hook.assert_called_once()


if __name__ == "__main__":
    unittest.main()
