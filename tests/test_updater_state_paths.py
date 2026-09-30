from __future__ import annotations

"""Каталог состояния обновления и аргументы установщика.

Проверяется главное свойство: всё, что нужно для восстановления после
сорвавшегося обновления, лежит вне каталога установки — иначе неудачная
установка стирает и средство восстановления, и диагностику.
"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from config.runtime_layout import paths_overlap, resolve_update_state_dir
from updater.install import paths as update_paths


class UpdateStateDirectoryTests(unittest.TestCase):
    def test_program_data_is_preferred_and_lives_outside_install_root(self) -> None:
        resolved = resolve_update_state_dir(
            channel="stable",
            application_root=r"C:\Zapret\Stable",
            program_data=r"C:\ProgramData",
            local_app_data=r"C:\Users\tester\AppData\Local",
        )

        self.assertEqual(resolved.parts[-3:], ("Zapret", "update", "stable"))
        self.assertIn("ProgramData", str(resolved))
        self.assertFalse(paths_overlap(resolved, r"C:\Zapret\Stable"))

    def test_local_app_data_is_used_when_program_data_is_unknown(self) -> None:
        resolved = resolve_update_state_dir(
            channel="dev",
            application_root=r"C:\Zapret\Dev",
            program_data="",
            local_app_data=r"C:\Users\tester\AppData\Local",
        )

        self.assertIn("AppData", str(resolved))
        self.assertEqual(resolved.parts[-1], "dev")

    def test_candidate_inside_install_root_is_rejected(self) -> None:
        """Установка прямо в ProgramData: «внешний» каталог был бы внутренним."""
        resolved = resolve_update_state_dir(
            channel="stable",
            application_root=r"C:\ProgramData",
            program_data=r"C:\ProgramData",
            local_app_data=r"C:\Users\tester\AppData\Local",
        )

        self.assertIn("AppData", str(resolved))

    def test_install_directory_is_the_last_resort(self) -> None:
        resolved = resolve_update_state_dir(
            channel="stable",
            application_root=r"C:\Zapret\Stable",
            program_data=None,
            local_app_data=None,
        )

        self.assertEqual(
            resolved,
            Path(r"C:\Zapret\Stable") / "user" / "update_cache",
        )

    def test_unknown_channel_falls_back_to_stable_leaf(self) -> None:
        resolved = resolve_update_state_dir(
            channel="   ",
            application_root=r"C:\Zapret\Stable",
            program_data=r"C:\ProgramData",
            local_app_data=None,
        )

        self.assertEqual(resolved.parts[-1], "stable")


class ResolvedStateDirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        update_paths.reset_update_state_dir_cache()
        self.addCleanup(update_paths.reset_update_state_dir_cache)

    def test_state_directory_is_created_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            preferred = Path(temp_dir) / "state"
            with patch.object(update_paths, "preferred_update_state_dir", return_value=preferred):
                first = update_paths.update_state_dir()
                second = update_paths.update_state_dir()

            self.assertEqual(first, preferred)
            self.assertEqual(second, preferred)
            self.assertTrue(preferred.is_dir())

    def test_unwritable_preferred_directory_falls_back_to_install_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            unusable = Path(temp_dir) / "blocked" / "state"
            unusable.parent.write_text("занято файлом", encoding="utf-8")

            with patch.object(update_paths, "preferred_update_state_dir", return_value=unusable):
                resolved = update_paths.update_state_dir()

            self.assertEqual(
                resolved,
                Path(update_paths.APPLICATION_PATHS.update_cache_dir),
            )

    def test_all_update_files_share_one_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            preferred = Path(temp_dir) / "state"
            with patch.object(update_paths, "preferred_update_state_dir", return_value=preferred):
                paths = (
                    update_paths.cached_installer_path(),
                    update_paths.cached_installer_meta_path(),
                    update_paths.handoff_state_path(),
                    update_paths.setup_log_path(),
                    update_paths.watchdog_script_path(),
                    update_paths.watchdog_log_path(),
                    update_paths.legacy_watchdog_script_path(),
                )

            self.assertEqual({path.parent for path in paths}, {preferred})
            self.assertEqual(len({path.name for path in paths}), len(paths))


class StateDirectoryHardeningTests(unittest.TestCase):
    """Каталог в %ProgramData% закрывается от записи обычными пользователями.

    Иначе пользователь мог подложить туда файл, который приложение затем
    запустило бы или перезаписало с правами администратора.
    """

    def test_owner_is_taken_before_access_list_is_replaced(self) -> None:
        commands = update_paths.build_harden_commands(r"C:\ProgramData\Zapret\update\dev")

        self.assertEqual(len(commands), 3)
        # Сначала закрывается сам каталог: новые файлы подложить уже нельзя.
        self.assertIn("/inheritance:r", commands[0])
        self.assertIn("/setowner", commands[1])
        self.assertIn("*S-1-5-32-544", commands[1])
        self.assertIn("/reset", commands[2])
        self.assertTrue(commands[2][1].endswith("*"), "сбрасывать нужно содержимое, не сам каталог")

    def test_explicit_grants_are_not_forced_onto_files(self) -> None:
        """С ``/T`` файлы получали пустой список прав и становились нечитаемыми."""
        grants = update_paths.build_harden_commands("state")[0]

        self.assertNotIn("/T", grants)

    def test_users_only_read_while_system_and_administrators_write(self) -> None:
        grants = update_paths.build_harden_commands("state")[0]

        self.assertIn("*S-1-5-18:(OI)(CI)F", grants)
        self.assertIn("*S-1-5-32-544:(OI)(CI)F", grants)
        self.assertIn("*S-1-5-32-545:(OI)(CI)RX", grants)
        self.assertFalse(any("S-1-5-32-545:(OI)(CI)F" in item for item in grants))

    def test_only_program_data_is_hardened(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            program_data = Path(temp_dir) / "ProgramData"
            with patch.dict("os.environ", {"ProgramData": str(program_data)}):
                self.assertTrue(
                    update_paths._is_inside_program_data(program_data / "Zapret" / "update")
                )
                self.assertFalse(
                    update_paths._is_inside_program_data(Path(temp_dir) / "LocalAppData")
                )


class InstallerArgumentsTests(unittest.TestCase):
    def setUp(self) -> None:
        update_paths.reset_update_state_dir_cache()
        self.addCleanup(update_paths.reset_update_state_dir_cache)

    def _arguments(self, state_dir: Path) -> tuple[str, ...]:
        from updater.install.launcher import installer_arguments

        with patch.object(update_paths, "preferred_update_state_dir", return_value=state_dir):
            return installer_arguments()

    def test_installer_never_suppresses_its_own_errors(self) -> None:
        """/SUPPRESSMSGBOXES означает Abort в ситуациях Abort/Retry.

        Именно он превращал сбой распаковки в молчаливый выход установщика.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            arguments = self._arguments(Path(temp_dir) / "state")

        self.assertNotIn("/SUPPRESSMSGBOXES", arguments)
        # Своей полоски у установщика нет — ход показывает окно-продолжение,
        # а сообщения об ошибках без /SUPPRESSMSGBOXES видны и так.
        self.assertIn("/VERYSILENT", arguments)
        self.assertNotIn("/SILENT", arguments)

    def test_installer_keeps_unattended_flags_and_logs_outside_install_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir) / "state"
            arguments = self._arguments(state_dir)

        for expected in ("/AUTOUPDATE", "/NORESTART", "/NOCANCEL", "/CLOSEAPPLICATIONS"):
            self.assertIn(expected, arguments)

        log_arguments = [item for item in arguments if item.startswith("/LOG=")]
        self.assertEqual(len(log_arguments), 1)
        self.assertTrue(log_arguments[0].endswith(update_paths.SETUP_LOG_NAME))
        self.assertFalse(
            paths_overlap(
                log_arguments[0][len("/LOG="):],
                update_paths.APPLICATION_PATHS.root,
            )
        )

        dir_arguments = [item for item in arguments if item.startswith("/DIR=")]
        self.assertEqual(
            dir_arguments,
            [f"/DIR={update_paths.APPLICATION_PATHS.root}"],
        )


if __name__ == "__main__":
    unittest.main()
