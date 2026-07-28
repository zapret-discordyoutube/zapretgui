from __future__ import annotations

"""Наблюдатель обновления в работе: настоящий PowerShell, настоящий процесс.

На Linux эти проверки пропускаются — там нет ни PowerShell, ни сведений о
версии в исполняемых файлах. На Windows они проверяют то, ради чего
наблюдатель существует: он не считает установку удавшейся, пока код возврата
и версия на диске не подтвердят это оба.
"""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from updater import update_watchdog


REFERENCE_EXE = Path(r"C:\Windows\System32\notepad.exe")


def _reference_product_version() -> str:
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"(Get-Item -LiteralPath '{REFERENCE_EXE}').VersionInfo.ProductVersion",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.stdout.strip()


@unittest.skipUnless(sys.platform == "win32", "наблюдатель работает на Windows")
@unittest.skipUnless(REFERENCE_EXE.is_file(), "нужен системный exe с данными о версии")
class WatchdogExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.state_dir = Path(self._temp.name) / "state"
        self.state_dir.mkdir(parents=True)
        self.install_root = Path(self._temp.name) / "install"
        (self.install_root / "_internal").mkdir(parents=True)
        # Копия системного файла подменяет приложение: у неё есть настоящая
        # ProductVersion, а значит наблюдатель может её сверить.
        shutil.copy2(REFERENCE_EXE, self.install_root / "_internal" / "Zapret.exe")
        self.installed_version = _reference_product_version()
        self.script_path = update_watchdog.install_watchdog_script(
            self.state_dir / "update_watchdog.ps1"
        )
        self.state_path = self.state_dir / "handoff.json"

    def _write_state(self, *, version: str, installer_arguments: list[str]) -> None:
        self.state_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "state": "prepared",
                    "version": version,
                    "target_root": str(self.install_root),
                    "installer_path": r"C:\Windows\System32\cmd.exe",
                    "arguments": installer_arguments,
                    "gui_pid": 0,
                    "installer_exit_code": None,
                    "installed_version": "",
                    "error": "",
                    "updated_at": 0.0,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _run_watchdog(self) -> int:
        command = update_watchdog.build_watchdog_command(
            script_path=self.script_path,
            state_path=self.state_path,
        )
        # Без -Unattended наблюдатель показал бы модальное сообщение и открыл
        # установщик: тест ждал бы человека у экрана.
        completed = subprocess.run(
            [*command, "-Unattended"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        return completed.returncode

    def _stored_state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8-sig"))

    def test_zero_exit_code_with_expected_version_is_a_success(self) -> None:
        self._write_state(
            version=self.installed_version,
            installer_arguments=["/c", "exit", "0"],
        )

        self.assertEqual(self._run_watchdog(), 0)

        stored = self._stored_state()
        self.assertEqual(stored["state"], "succeeded")
        self.assertEqual(stored["installer_exit_code"], 0)
        self.assertEqual(stored["installed_version"], self.installed_version)

    def test_nonzero_exit_code_is_a_failure(self) -> None:
        self._write_state(
            version=self.installed_version,
            installer_arguments=["/c", "exit", "5"],
        )

        self.assertEqual(self._run_watchdog(), 1)

        stored = self._stored_state()
        self.assertEqual(stored["state"], "failed")
        self.assertEqual(stored["installer_exit_code"], 5)
        self.assertIn("код 5", stored["error"])

    def test_unchanged_version_after_clean_exit_is_a_failure(self) -> None:
        """Установщик отчитался об успехе, но на диске осталась прежняя версия."""
        self._write_state(
            version="99.99.99.99",
            installer_arguments=["/c", "exit", "0"],
        )

        self.assertEqual(self._run_watchdog(), 1)

        stored = self._stored_state()
        self.assertEqual(stored["state"], "failed")
        self.assertIn("версия на диске не изменилась", stored["error"])


if __name__ == "__main__":
    unittest.main()
