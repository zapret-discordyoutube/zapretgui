from __future__ import annotations

from pathlib import Path
import contextlib
import inspect
import os
import sys
import unittest
from unittest.mock import patch


PUBLIC_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PUBLIC_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from startup import check_start  # noqa: E402
from config.runtime_layout import ApplicationPaths  # noqa: E402


class StartupInstallPathChecksTests(unittest.TestCase):
    def _collect_notifications(self, root: str, executable: str | None = None) -> list[dict]:
        """Стартовые уведомления для установки в ``root``; остальные проверки отключены."""
        patches = [
            patch.object(check_start, "APPLICATION_PATHS", ApplicationPaths.from_root(root)),
            patch.object(check_start, "check_windows_version", return_value=(False, "")),
            patch.object(check_start, "check_system_commands", return_value=(False, "")),
            patch.object(check_start, "check_if_application_root_is_temporary", return_value=False),
            patch.object(check_start, "check_path_for_onedrive", return_value=(False, "")),
            patch.object(check_start, "build_proxy_notification", return_value=None),
        ]
        if executable is not None:
            patches.append(patch.object(check_start.sys, "executable", executable))
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            return check_start.collect_startup_notifications()

    @unittest.skipUnless(sys.platform == "win32", "проверка использует семантику Windows-путей")
    def test_internal_runtime_path_is_not_treated_as_install_path(self) -> None:
        notifications = self._collect_notifications(
            r"C:\Zapret\Dev",
            executable=r"C:\Zapret\Dev\_INTER~1\python.exe",
        )

        self.assertEqual(notifications, [])

    @unittest.skipUnless(sys.platform == "win32", "проверка использует семантику Windows-путей")
    def test_onedrive_check_uses_application_root_only(self) -> None:
        with (
            patch.object(
                check_start,
                "APPLICATION_PATHS",
                ApplicationPaths.from_root(r"C:\Zapret\Dev"),
            ),
            patch.object(
                check_start.sys,
                "executable",
                r"C:\Users\privacy\OneDrive\_INTER~1\python.exe",
            ),
            patch.dict(
                os.environ,
                {"ONEDRIVE": r"C:\Users\privacy\OneDrive"},
                clear=False,
            ),
        ):
            in_onedrive, message = check_start.check_path_for_onedrive()

        self.assertFalse(in_onedrive)
        self.assertEqual(message, "")

    def test_spaces_and_non_ascii_in_install_path_are_not_a_problem(self) -> None:
        # Оба движка (winws.exe и winws2.exe) — собственные сборки под Windows
        # и понимают пробелы и не-ASCII символы в путях: предупреждать не о чем.
        for root in (r"C:\Zapret Builds\Dev", r"C:\Программы\Запрет", r"D:\Моя папка (1)\zapret"):
            with self.subTest(root=root):
                self.assertEqual(self._collect_notifications(root), [])

    def test_special_chars_check_is_gone(self) -> None:
        self.assertFalse(hasattr(check_start, "contains_special_chars"))
        self.assertFalse(hasattr(check_start, "check_path_for_special_chars"))
        self.assertNotIn("special_chars_path", inspect.getsource(check_start))

    @unittest.skipUnless(sys.platform == "win32", "проверка использует семантику Windows-путей")
    def test_temporary_directory_check_uses_application_root(self) -> None:
        environment = {
            "TEMP": r"C:\Users\privacy\AppData\Local\Temp",
            "TMP": r"C:\Users\privacy\AppData\Local\Temp",
            "WINDIR": r"C:\Windows",
        }
        with (
            patch.object(
                check_start,
                "APPLICATION_PATHS",
                ApplicationPaths.from_root(
                    r"C:\Users\privacy\AppData\Local\Temp\Zapret"
                ),
            ),
            patch.object(check_start.sys, "executable", r"C:\Python314\python.exe"),
            patch.dict(os.environ, environment, clear=False),
        ):
            self.assertTrue(check_start.check_if_application_root_is_temporary())

    @unittest.skipUnless(sys.platform == "win32", "проверка использует семантику Windows-путей")
    def test_similar_directory_prefix_is_not_treated_as_temporary(self) -> None:
        environment = {
            "TEMP": r"C:\Temp",
            "TMP": r"C:\Temp",
            "WINDIR": r"C:\Windows",
        }
        with (
            patch.object(
                check_start,
                "APPLICATION_PATHS",
                ApplicationPaths.from_root(r"C:\TempBackup\Zapret"),
            ),
            patch.dict(os.environ, environment, clear=False),
        ):
            self.assertFalse(check_start.check_if_application_root_is_temporary())

    def test_path_validation_has_no_dependency_on_runtime_executable(self) -> None:
        source = inspect.getsource(check_start)

        self.assertNotIn("sys.executable", source)
        self.assertNotIn("BIN_FOLDER", source)


if __name__ == "__main__":
    unittest.main()
