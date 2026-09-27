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
import time
import unittest
from unittest.mock import patch

from updater.install import watchdog as update_watchdog


REFERENCE_EXE = Path(r"C:\Windows\System32\notepad.exe")


def _product_version(path: Path) -> str:
    """Версия именно этого файла.

    У системных exe версия берётся из языкового ``.mui`` рядом с файлом, поэтому
    у копии без него она другая: сверять нужно с самой копией.
    """
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"(Get-Item -LiteralPath '{path}').VersionInfo.ProductVersion",
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
        application = self.install_root / "_internal" / "Zapret.exe"
        shutil.copy2(REFERENCE_EXE, application)
        self.installed_version = _product_version(application)
        self.script_path = update_watchdog.install_watchdog_script(
            self.state_dir / "watchdog.ps1"
        )
        self.state_path = self.state_dir / "handoff.json"

    def _write_state(
        self,
        *,
        version: str,
        installer_arguments: list[str],
        installer_path: str = r"C:\Windows\System32\cmd.exe",
        installer_sha256: str = "",
        state: str = "prepared",
    ) -> None:
        self.state_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "state": state,
                    "version": version,
                    "target_root": str(self.install_root),
                    "installer_path": installer_path,
                    "installer_sha256": installer_sha256,
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

    def _run_watchdog(self, *, recovery: bool = False) -> int:
        command = update_watchdog.build_watchdog_command(
            script_path=self.script_path,
            state_path=self.state_path,
            recovery=recovery,
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

    def test_background_powershell_reaches_script_body(self) -> None:
        """Тот же фоновый запуск, который приложение использует перед выходом."""
        ready_path = self.state_dir / "background-ready.txt"
        probe_path = self.state_dir / "background-probe.ps1"
        probe_path.write_text(
            "param([string]$ReadyPath)\n"
            "Set-Content -LiteralPath $ReadyPath -Value 'ready' -Encoding UTF8\n",
            encoding="utf-8-sig",
        )
        command = (
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(probe_path),
            "-ReadyPath",
            str(ready_path),
        )

        real_popen = subprocess.Popen
        processes: list[subprocess.Popen] = []

        def capture_process(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        with patch.object(update_watchdog.subprocess, "Popen", side_effect=capture_process):
            self.assertTrue(update_watchdog.spawn_background(command))
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and not ready_path.exists():
                time.sleep(0.05)

        self.assertTrue(
            ready_path.exists(),
            "фоновый PowerShell завершился до исполнения файла наблюдателя",
        )
        processes[0].wait(timeout=10)

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


    def test_installer_child_process_does_not_hold_the_watchdog(self) -> None:
        """Установщик в конце запускает новую программу и выходит.

        ``Start-Process -Wait`` ждал бы и этого потомка, то есть до закрытия
        программы пользователем, и итог обновления так и не записывался.
        """
        powershell = shutil.which("powershell") or "powershell"
        self._write_state(
            version=self.installed_version,
            installer_path=powershell,
            installer_arguments=[
                "-NoProfile",
                "-Command",
                "Start-Process ping -ArgumentList '-n','40','127.0.0.1' -WindowStyle Hidden; exit 0",
            ],
        )

        started = time.monotonic()
        self.assertEqual(self._run_watchdog(), 0)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 30.0, "наблюдатель ждал потомка установщика")
        self.assertEqual(self._stored_state()["state"], "succeeded")

    def test_directory_with_spaces_reaches_installer_as_one_argument(self) -> None:
        probe = self.state_dir / "args-probe.ps1"
        probe.write_text(
            "param([string]$Out)\n"
            "[System.IO.File]::WriteAllText($Out, ($args -join '|'))\n",
            encoding="utf-8-sig",
        )
        output = self.state_dir / "args.txt"
        target = r"/DIR=C:\Program Files\Zapret"
        self._write_state(
            version=self.installed_version,
            installer_path=shutil.which("powershell") or "powershell",
            installer_arguments=[
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(probe),
                "-Out",
                str(output),
                target,
            ],
        )

        self.assertEqual(self._run_watchdog(), 0)
        self.assertEqual(output.read_text(encoding="utf-8"), target)

    def test_state_is_written_without_bom(self) -> None:
        self._write_state(
            version=self.installed_version,
            installer_arguments=["/c", "exit", "0"],
        )

        self._run_watchdog()

        self.assertFalse(self.state_path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_cancelled_update_does_not_start_installer(self) -> None:
        """Приложение отменило обновление, пока наблюдатель запускался."""
        marker = self.state_dir / "installer-ran.txt"
        self._write_state(
            version=self.installed_version,
            installer_arguments=["/c", "echo", "ran", ">", str(marker)],
            state="failed",
        )

        self.assertEqual(self._run_watchdog(), 0)

        self.assertFalse(marker.exists())
        self.assertEqual(self._stored_state()["state"], "failed")

    def test_changed_installer_is_not_started(self) -> None:
        marker = self.state_dir / "installer-ran.txt"
        self._write_state(
            version=self.installed_version,
            installer_arguments=["/c", "echo", "ran", ">", str(marker)],
            installer_sha256="00" * 32,
        )

        self.assertEqual(self._run_watchdog(), 1)

        self.assertFalse(marker.exists())
        self.assertIn("изменён", self._stored_state()["error"])

    def test_recovery_never_downgrades_newer_installation(self) -> None:
        """Пользователь уже обновился дальше: старый установщик ставить нельзя."""
        marker = self.state_dir / "installer-ran.txt"
        self._write_state(
            version="1.0.0.0",
            installer_arguments=["/c", "echo", "ran", ">", str(marker)],
            state="launched",
        )

        self.assertEqual(self._run_watchdog(recovery=True), 0)

        self.assertFalse(marker.exists())
        self.assertEqual(self._stored_state()["state"], "succeeded")

    def test_recovery_ignores_settled_update(self) -> None:
        marker = self.state_dir / "installer-ran.txt"
        self._write_state(
            version="99.99.99.99",
            installer_arguments=["/c", "echo", "ran", ">", str(marker)],
            state="failed",
        )

        self.assertEqual(self._run_watchdog(recovery=True), 0)

        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
