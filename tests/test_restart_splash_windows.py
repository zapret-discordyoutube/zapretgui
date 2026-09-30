from __future__ import annotations

"""Окно-продолжение в работе: настоящий PowerShell и WinForms.

На Linux пропускается — там нет ни PowerShell, ни WinForms. На Windows
проверяет то, ради чего окно существует: оно быстро встаёт на место окна
обновления, уходит при неудаче или отмене и гаснет, когда новая версия
открылась. Без сигналов — ждёт установщик.
"""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from updater.install.splash import RestartSplashSpec, build_splash_command
from updater.install.splash_script import render_splash_script


@unittest.skipUnless(sys.platform == "win32", "нужны PowerShell и WinForms")
class RestartSplashWindowsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.state = self.dir / "handoff.json"
        self.shown = self.dir / "shown"
        self.ready = self.dir / "ready.json"
        self.script = self.dir / "splash.ps1"
        self.script.write_bytes(render_splash_script().encode("utf-8-sig"))
        spec = RestartSplashSpec(
            x=100,
            y=100,
            width=800,
            height=560,
            title="Обновляем Zapret до v2.0",
            subtitle="v1.9 → v2.0",
            stages=("Закрываем", "Устанавливаем", "Запускаем"),
            footer="Окно закроется само",
            window_title="Zapret — обновление",
            jokes=("шутка",),
        )
        self.spec_path = self.dir / "spec.json"
        self.spec_path.write_text(
            json.dumps(
                spec.to_payload(
                    logo_path="",
                    shown_path=str(self.shown),
                    ready_path=str(self.ready),
                    log_path=str(self.dir / "splash.log"),
                ),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _state(self, value: str | None) -> None:
        if value is None:
            self.state.unlink(missing_ok=True)
        else:
            self.state.write_text(json.dumps({"state": value}), encoding="utf-8")

    def _start(self) -> subprocess.Popen:
        command = build_splash_command(script_path=self.script, spec_path=self.spec_path, state_path=self.state)
        process = subprocess.Popen(list(command), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline = time.monotonic() + 10
        while not self.shown.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(self.shown.exists(), "окно не отметилось как показанное")
        return process

    def test_failed_install_closes_window_at_once(self) -> None:
        self._state("launched")
        process = self._start()
        self._state("failed")
        self.assertEqual(process.wait(timeout=10), 0)

    def test_new_version_ready_closes_window(self) -> None:
        self._state("succeeded")
        process = self._start()
        time.sleep(0.3)
        self.ready.write_text("{}", encoding="utf-8")
        self.assertEqual(process.wait(timeout=10), 0)
        # Закрытое окно больше не ждёт: обычные запуски метку не пишут.
        self.assertFalse(self.spec_path.exists())

    def test_cancelled_update_closes_window(self) -> None:
        self._state("prepared")
        process = self._start()
        self._state(None)
        self.assertEqual(process.wait(timeout=15), 0)

    def test_window_waits_while_installer_works(self) -> None:
        self._state("launched")
        process = self._start()
        with self.assertRaises(subprocess.TimeoutExpired):
            process.wait(timeout=3)

    def test_snapshot_draws_every_stage(self) -> None:
        for stage in ("prepared", "launched", "succeeded"):
            snapshot = self.dir / f"{stage}.png"
            command = build_splash_command(script_path=self.script, spec_path=self.spec_path, state_path=self.state)
            subprocess.run(
                list(command) + ["-SnapshotPath", str(snapshot), "-SnapshotState", stage, "-SnapshotAfterMs", "600"],
                timeout=30,
                check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.assertGreater(snapshot.stat().st_size, 1000, stage)


if __name__ == "__main__":
    unittest.main()
