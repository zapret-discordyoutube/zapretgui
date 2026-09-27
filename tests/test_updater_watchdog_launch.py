from __future__ import annotations

"""Наблюдатель обновления и системная страховка.

Наблюдатель — единственный участник, который доживает до конца установки,
поэтому проверяются и его запуск, и правила, по которым он признаёт установку
состоявшейся. Сам скрипт исполняется только на Windows (см.
``test_updater_watchdog_windows``), здесь его правила проверяются по тексту.
"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from updater.install import launcher, recovery_hook, watchdog, watchdog_script
from updater.install import paths as update_paths
from updater.install.handoff import HandoffState, read_record


class WatchdogScriptContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = watchdog_script.render_watchdog_script()

    def test_script_shares_recovery_hook_location_with_application(self) -> None:
        self.assertIn(f"'HKLM:\\{recovery_hook.RUNONCE_KEY}'", self.script)
        self.assertIn(f"'{recovery_hook.RUNONCE_VALUE_NAME}'", self.script)

    def test_script_uses_declared_attempt_limits(self) -> None:
        self.assertIn(f"$maxAttempts = {watchdog_script.INSTALL_ATTEMPTS}", self.script)
        self.assertIn(
            f"$guiExitTimeoutSeconds = {watchdog_script.GUI_EXIT_TIMEOUT_SECONDS}",
            self.script,
        )

    def test_installer_is_awaited_without_its_descendants(self) -> None:
        """``-Wait`` ждал и новую программу, которую запускает установщик."""
        self.assertNotIn("-Wait", self.script)
        self.assertIn("$process.WaitForExit()", self.script)

    def test_arguments_are_joined_with_quotes_not_by_start_process(self) -> None:
        """Массив в ``-ArgumentList`` терял кавычки у пути с пробелами."""
        self.assertIn("function Join-InstallerArguments", self.script)
        self.assertNotIn("-ArgumentList $arguments", self.script)
        self.assertNotIn("-ArgumentList @(", self.script)

    def test_state_is_written_without_bom(self) -> None:
        self.assertNotIn("Set-Content", self.script)
        self.assertNotIn("Add-Content", self.script)
        self.assertIn("New-Object System.Text.UTF8Encoding($false)", self.script)

    def test_success_requires_both_exit_code_and_installed_version(self) -> None:
        self.assertIn("if ($lastExitCode -ne 0)", self.script)
        self.assertIn("if (Test-VersionInstalled $targetRoot $expectedVersion)", self.script)

    def test_newer_installed_version_counts_as_installed(self) -> None:
        """Иначе восстановление ставило бы старый установщик поверх новой версии."""
        self.assertIn("[version]$installed -ge [version]", self.script)

    def test_installer_checksum_is_verified_right_before_start(self) -> None:
        loop = self.script[self.script.index("for ($attempt = 1"):]
        self.assertLess(
            loop.index("Test-InstallerHash $installer $installerSha"),
            loop.index("Start-Process -FilePath $installer"),
        )

    def test_recovery_only_finishes_started_installation(self) -> None:
        recovery = self.script[self.script.index("if ($Recovery) {"):]
        self.assertIn("-ne 'launched'", recovery[: recovery.index("} else {")])

    def test_cancelled_update_is_detected_before_installer_starts(self) -> None:
        wait_index = self.script.index("Wait-ForProcessExit $preparedPid")
        cancel_index = self.script.index("Обновление отменено приложением")
        launch_index = self.script.index("Save-State $state 'launched'")
        self.assertLess(wait_index, cancel_index)
        self.assertLess(cancel_index, launch_index)

    def test_unattended_mode_touches_neither_windows_nor_applications(self) -> None:
        show_message = self.script[self.script.index("function Show-Message"):]
        start_application = self.script[self.script.index("function Start-InstalledApplication"):]

        self.assertIn("if ($Unattended)", show_message[: show_message.index("\n}")])
        self.assertIn("if ($Unattended) { return }", start_application[: start_application.index("\n}")])

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


class WatchdogCommandTests(unittest.TestCase):
    def test_command_bypasses_execution_policy_and_names_state_file(self) -> None:
        command = watchdog.build_watchdog_command(
            script_path=r"C:\ProgramData\Zapret\update\stable\watchdog.ps1",
            state_path=r"C:\ProgramData\Zapret\update\stable\handoff.json",
        )

        self.assertEqual(command[0], "powershell")
        self.assertIn("Bypass", command)
        self.assertIn("-File", command)
        self.assertIn("-StatePath", command)
        self.assertNotIn("-Recovery", command)

    def test_recovery_command_is_marked(self) -> None:
        command = watchdog.build_watchdog_command(
            script_path="script.ps1",
            state_path="handoff.json",
            recovery=True,
        )

        self.assertEqual(command[-1], "-Recovery")

    def test_script_is_written_with_bom_and_without_leftovers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            script_path = Path(temp_dir) / "watchdog.ps1"
            watchdog.install_watchdog_script(script_path)

            self.assertEqual(
                sorted(item.name for item in Path(temp_dir).iterdir()),
                ["watchdog.ps1"],
            )
            self.assertTrue(script_path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_new_script_name_differs_from_legacy_one(self) -> None:
        """По имени скрипта отличаем зависший наблюдатель прежних версий."""
        self.assertNotEqual(update_paths.WATCHDOG_SCRIPT_NAME, update_paths.LEGACY_WATCHDOG_SCRIPT_NAME)


class StopWatchdogsTests(unittest.TestCase):
    SCRIPT = r"C:\ProgramData\Zapret\update\dev\update_watchdog.ps1"

    def test_only_processes_running_given_script_are_stopped(self) -> None:
        processes = [
            (101, ("powershell", "-File", self.SCRIPT.upper(), "-StatePath", "x")),
            (102, ("powershell", "-File", r"C:\other\update_watchdog.ps1")),
            (103, ("powershell", "-Command", "Get-Date")),
        ]
        killed: list[int] = []

        stopped = watchdog.stop_watchdogs(
            (self.SCRIPT,),
            list_processes=lambda: processes,
            kill_process=killed.append,
        )

        if watchdog.os.name == "nt":
            self.assertEqual(killed, [101])
        else:
            # На Linux normcase не сводит регистр, поэтому совпадение точное.
            self.assertEqual(killed, [])
        self.assertEqual(stopped, len(killed))

    def test_listing_failure_is_not_fatal(self) -> None:
        def broken():
            raise RuntimeError("нет доступа")

        self.assertEqual(
            watchdog.stop_watchdogs((self.SCRIPT,), list_processes=broken, kill_process=Mock()),
            0,
        )

    def test_legacy_watchdog_is_stopped_and_its_script_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            legacy = Path(temp_dir) / update_paths.LEGACY_WATCHDOG_SCRIPT_NAME
            legacy.write_text("old", encoding="utf-8")
            with (
                patch.object(watchdog.paths, "legacy_watchdog_script_path", return_value=legacy),
                patch.object(watchdog, "stop_watchdogs") as stop,
            ):
                watchdog.retire_legacy_watchdog()

            stop.assert_called_once_with((legacy,))
            self.assertFalse(legacy.exists())

    def test_nothing_is_scanned_without_legacy_script(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(
                    watchdog.paths,
                    "legacy_watchdog_script_path",
                    return_value=Path(temp_dir) / "absent.ps1",
                ),
                patch.object(watchdog, "stop_watchdogs") as stop,
            ):
                watchdog.retire_legacy_watchdog()

        stop.assert_not_called()


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
        command = watchdog.build_watchdog_command(
            script_path=r"C:\ProgramData\Zapret\update\stable\watchdog.ps1",
            state_path=r"C:\ProgramData\Zapret\update\stable\handoff.json",
            recovery=True,
        )

        with patch.object(recovery_hook, "winreg", registry):
            self.assertTrue(recovery_hook.set_recovery_hook(command))

        stored = registry.values[recovery_hook.RUNONCE_VALUE_NAME]
        self.assertIn("-Recovery", stored)
        self.assertIn("watchdog.ps1", stored)
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
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.state_dir = Path(self._temp.name)
        update_paths.reset_update_state_dir_cache()
        self.addCleanup(update_paths.reset_update_state_dir_cache)
        launcher.reset_state_dir_hardening()
        self.addCleanup(launcher.reset_state_dir_hardening)
        for target, value in (
            (update_paths, "preferred_update_state_dir"),
        ):
            patcher = patch.object(target, value, return_value=self.state_dir)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.set_hook = self._patch(launcher, "set_recovery_hook", return_value=True)
        self.clear_hook = self._patch(launcher, "clear_recovery_hook", return_value=True)

    def _patch(self, target, name, **kwargs):
        patcher = patch.object(target, name, **kwargs)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def _handoff(self) -> launcher.InstallerHandoff:
        return launcher.InstallerHandoff(
            version="21.1.1.4",
            installer_path=str(self.state_dir / "Zapret2Setup.exe"),
            installer_sha256="ab" * 32,
            arguments=("/AUTOUPDATE", "/SILENT"),
        )

    def _start(self, *, started: bool = True, spawned: bool = True, admin: bool = True, **kwargs):
        self.spawn_background = Mock(return_value=spawned)
        self.spawn_elevated = Mock(return_value=spawned)
        self.stop_watchdogs = Mock(return_value=0)
        return launcher.start_supervised_installation(
            self._handoff(),
            is_admin=lambda: admin,
            spawn_background=self.spawn_background,
            spawn_elevated=self.spawn_elevated,
            stop_watchdogs=self.stop_watchdogs,
            wait_for_start=lambda _path: started,
            **kwargs,
        )

    def test_prepared_record_carries_checksum_and_pid(self) -> None:
        self.assertTrue(self._start(gui_pid=4242))

        record = read_record(self.state_dir / "handoff.json")
        self.assertEqual(record.state, HandoffState.PREPARED)
        self.assertEqual(record.gui_pid, 4242)
        self.assertEqual(record.installer_sha256, "ab" * 32)
        self.assertEqual(record.arguments, ("/AUTOUPDATE", "/SILENT"))
        self.assertTrue((self.state_dir / update_paths.WATCHDOG_SCRIPT_NAME).exists())
        self.set_hook.assert_called_once()
        self.clear_hook.assert_not_called()
        self.spawn_background.assert_called_once()
        self.spawn_elevated.assert_not_called()

    def test_previous_watchdogs_are_stopped_before_new_one(self) -> None:
        self._start()

        stopped_paths = self.stop_watchdogs.call_args.args[0]
        self.assertIn(self.state_dir / update_paths.WATCHDOG_SCRIPT_NAME, stopped_paths)
        self.assertIn(self.state_dir / update_paths.LEGACY_WATCHDOG_SCRIPT_NAME, stopped_paths)

    def test_non_admin_application_asks_for_elevation(self) -> None:
        self.assertTrue(self._start(admin=False))

        self.spawn_elevated.assert_called_once()
        self.spawn_background.assert_not_called()

    def test_silent_watchdog_cancels_update_and_releases_hook(self) -> None:
        """Опоздавший наблюдатель не должен найти запись и запустить установщик."""
        self.assertFalse(self._start(started=False))

        self.assertFalse((self.state_dir / "handoff.json").exists())
        self.clear_hook.assert_called_once()

    def test_failed_spawn_cancels_update(self) -> None:
        self.assertFalse(self._start(spawned=False))

        self.assertFalse((self.state_dir / "handoff.json").exists())
        self.clear_hook.assert_called_once()

    def test_previous_journal_is_kept_aside(self) -> None:
        (self.state_dir / "watchdog.log").write_text("old", encoding="utf-8")

        self._start()

        self.assertFalse((self.state_dir / "watchdog.log").exists())
        self.assertEqual(
            (self.state_dir / watchdog.PREVIOUS_WATCHDOG_LOG_NAME).read_text(encoding="utf-8"),
            "old",
        )


class StageInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.state_dir = self.root / "state"
        update_paths.reset_update_state_dir_cache()
        self.addCleanup(update_paths.reset_update_state_dir_cache)
        patcher = patch.object(update_paths, "preferred_update_state_dir", return_value=self.state_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_installer_and_metadata_land_in_state_directory(self) -> None:
        downloaded = self.root / "download.exe"
        downloaded.write_bytes(b"installer")

        handoff = launcher.stage_installer(downloaded, version="21.1.1.4", sha256="cd" * 32, size=9)

        self.assertEqual(Path(handoff.installer_path), self.state_dir / "Zapret2Setup.exe")
        self.assertEqual(Path(handoff.installer_path).read_bytes(), b"installer")
        self.assertEqual(handoff.installer_sha256, "cd" * 32)
        self.assertEqual(
            launcher.read_cached_installer_meta(),
            {"version": "21.1.1.4", "sha256": "cd" * 32, "size": 9},
        )
        self.assertEqual(
            sorted(item.name for item in self.state_dir.iterdir()),
            ["Zapret2Setup.exe", "installer.json"],
        )

    def test_planted_temporary_installer_is_not_used(self) -> None:
        self.state_dir.mkdir(parents=True)
        planted = self.state_dir / "Zapret2Setup.exe.new"
        planted.write_bytes(b"planted")
        downloaded = self.root / "download.exe"
        downloaded.write_bytes(b"installer")

        handoff = launcher.stage_installer(downloaded, version="21.1.1.4", sha256="cd" * 32, size=9)

        self.assertEqual(Path(handoff.installer_path).read_bytes(), b"installer")
        self.assertEqual(planted.read_bytes(), b"planted")


if __name__ == "__main__":
    unittest.main()
