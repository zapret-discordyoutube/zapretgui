from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from install_integrity.models import (
    IntegrityCause,
    IntegrityFinding,
    IntegrityReport,
    KIND_MISSING,
    ROLE_CRITICAL,
)
from utils.file_digest import sha256_file


def _damaged_report() -> IntegrityReport:
    return IntegrityReport(
        cause=IntegrityCause.REMOVED_AFTER_INSTALL,
        findings=(
            IntegrityFinding(
                path="exe/winws2.exe",
                kind=KIND_MISSING,
                role=ROLE_CRITICAL,
                engines=("winws2",),
            ),
        ),
        manifest_version="21.1.1.4",
        checked_files=5,
    )


class SelfRepairAttemptLimitTests(unittest.TestCase):
    def test_daily_limit_blocks_further_attempts(self) -> None:
        from settings.store import append_self_repair_attempt, get_self_repair_attempts

        with tempfile.TemporaryDirectory() as tmp:
            with patch("settings.store.MAIN_DIRECTORY", tmp):
                for index in range(3):
                    allowed, count = append_self_repair_attempt(
                        max_attempts=3,
                        window_seconds=86400,
                        now=1_000_000 + index,
                    )
                    self.assertTrue(allowed)
                    self.assertEqual(count, index + 1)

                allowed, count = append_self_repair_attempt(
                    max_attempts=3,
                    window_seconds=86400,
                    now=1_000_010,
                )

                self.assertFalse(allowed)
                self.assertEqual(count, 3)
                self.assertEqual(len(get_self_repair_attempts()), 3)

    def test_attempts_outside_window_are_forgotten(self) -> None:
        from settings.store import append_self_repair_attempt

        with tempfile.TemporaryDirectory() as tmp:
            with patch("settings.store.MAIN_DIRECTORY", tmp):
                for index in range(3):
                    append_self_repair_attempt(
                        max_attempts=3,
                        window_seconds=86400,
                        now=1_000_000 + index,
                    )

                allowed, count = append_self_repair_attempt(
                    max_attempts=3,
                    window_seconds=86400,
                    now=1_000_000 + 86400 + 5,
                )

                self.assertTrue(allowed)
                self.assertEqual(count, 1)


class RepairInstallationTests(unittest.TestCase):
    def setUp(self) -> None:
        from updater.self_repair import reset_repair_process_guard

        reset_repair_process_guard()
        self.addCleanup(reset_repair_process_guard)

    def _cached_installer(self, root: Path, *, version: str) -> tuple[Path, dict]:
        installer = root / "Zapret2Setup.exe"
        installer.write_bytes(b"installer-bytes")
        meta = {
            "version": version,
            "sha256": sha256_file(installer),
            "size": installer.stat().st_size,
        }
        return installer, meta

    def test_source_run_is_not_repairable(self) -> None:
        from updater import self_repair

        with patch.object(self_repair, "PACKAGED_RUNTIME", False):
            outcome = self_repair.repair_installation(_damaged_report())

        self.assertFalse(outcome.started)
        self.assertIn("установленной", outcome.reason)

    def test_intact_installation_is_not_repaired(self) -> None:
        from updater import self_repair

        intact = IntegrityReport(cause=IntegrityCause.OK, manifest_version="21.1.1.4")
        with patch.object(self_repair, "PACKAGED_RUNTIME", True):
            outcome = self_repair.repair_installation(intact)

        self.assertFalse(outcome.started)
        self.assertIn("не повреждена", outcome.reason)

    def test_missing_manifest_is_not_repaired(self) -> None:
        from updater import self_repair

        absent = IntegrityReport(cause=IntegrityCause.MANIFEST_ABSENT)
        with patch.object(self_repair, "PACKAGED_RUNTIME", True):
            outcome = self_repair.repair_installation(absent)

        self.assertFalse(outcome.started)

    def test_cached_installer_of_current_version_repairs_without_network(self) -> None:
        from updater import self_repair

        with tempfile.TemporaryDirectory() as tmp:
            installer, meta = self._cached_installer(Path(tmp), version="21.1.1.4")

            with (
                patch.object(self_repair, "PACKAGED_RUNTIME", True),
                patch.object(self_repair, "APP_VERSION", "21.1.1.4"),
                patch.object(self_repair, "cached_installer_path", return_value=installer),
                patch.object(self_repair, "read_cached_installer_meta", return_value=meta),
                patch.object(self_repair, "installer_arguments", return_value=("/AUTOUPDATE",)),
                patch.object(self_repair, "UpdatePipeline") as pipeline_cls,
                patch.object(self_repair, "launch_installer_winapi", return_value=True) as launch,
                patch("settings.store.append_self_repair_attempt", return_value=(True, 1)),
            ):
                outcome = self_repair.repair_installation(_damaged_report())

            self.assertTrue(outcome.started)
            self.assertTrue(outcome.used_cache)
            launch.assert_called_once()
            self.assertEqual(launch.call_args.args[0], str(installer))
            pipeline_cls.assert_not_called()

    def test_cached_installer_of_other_version_is_rejected_offline(self) -> None:
        from updater import self_repair

        with tempfile.TemporaryDirectory() as tmp:
            installer, meta = self._cached_installer(Path(tmp), version="20.0.0.0")

            with (
                patch.object(self_repair, "PACKAGED_RUNTIME", True),
                patch.object(self_repair, "APP_VERSION", "21.1.1.4"),
                patch.object(self_repair, "cached_installer_path", return_value=installer),
                patch.object(self_repair, "read_cached_installer_meta", return_value=meta),
                patch.object(self_repair, "launch_installer_winapi", return_value=True) as launch,
                patch("settings.store.append_self_repair_attempt", return_value=(True, 1)),
            ):
                outcome = self_repair.repair_installation(
                    _damaged_report(),
                    allow_download=False,
                )

            self.assertFalse(outcome.started)
            launch.assert_not_called()

    def test_tampered_cached_installer_is_rejected(self) -> None:
        from updater import self_repair

        with tempfile.TemporaryDirectory() as tmp:
            installer, meta = self._cached_installer(Path(tmp), version="21.1.1.4")
            installer.write_bytes(b"tampered-installer")

            with (
                patch.object(self_repair, "PACKAGED_RUNTIME", True),
                patch.object(self_repair, "APP_VERSION", "21.1.1.4"),
                patch.object(self_repair, "cached_installer_path", return_value=installer),
                patch.object(self_repair, "read_cached_installer_meta", return_value=meta),
                patch.object(self_repair, "launch_installer_winapi", return_value=True) as launch,
                patch("settings.store.append_self_repair_attempt", return_value=(True, 1)),
            ):
                outcome = self_repair.repair_installation(
                    _damaged_report(),
                    allow_download=False,
                )

            self.assertFalse(outcome.started)
            launch.assert_not_called()

    def test_exhausted_limit_stops_repair_loop(self) -> None:
        from updater import self_repair

        with (
            patch.object(self_repair, "PACKAGED_RUNTIME", True),
            patch.object(self_repair, "launch_installer_winapi", return_value=True) as launch,
            patch("settings.store.append_self_repair_attempt", return_value=(False, 3)),
        ):
            outcome = self_repair.repair_installation(_damaged_report())

        self.assertFalse(outcome.started)
        self.assertIn("карантин", outcome.reason)
        launch.assert_not_called()

    def test_second_repair_in_same_process_is_refused(self) -> None:
        from updater import self_repair

        with tempfile.TemporaryDirectory() as tmp:
            installer, meta = self._cached_installer(Path(tmp), version="21.1.1.4")

            with (
                patch.object(self_repair, "PACKAGED_RUNTIME", True),
                patch.object(self_repair, "APP_VERSION", "21.1.1.4"),
                patch.object(self_repair, "cached_installer_path", return_value=installer),
                patch.object(self_repair, "read_cached_installer_meta", return_value=meta),
                patch.object(self_repair, "installer_arguments", return_value=("/AUTOUPDATE",)),
                patch.object(self_repair, "launch_installer_winapi", return_value=True) as launch,
                patch("settings.store.append_self_repair_attempt", return_value=(True, 1)),
            ):
                first = self_repair.repair_installation(_damaged_report())
                second = self_repair.repair_installation(_damaged_report())

            self.assertTrue(first.started)
            self.assertFalse(second.started)
            self.assertIn("в этом запуске", second.reason)
            launch.assert_called_once()


class StartupDiagnosisPublicationTests(unittest.TestCase):
    def test_multiline_diagnosis_produces_single_user_line(self) -> None:
        from winws_runtime.health import startup_error_diagnosis

        diagnosis = "📁 ФАЙЛ ИЛИ ПАПКА НЕ НАЙДЕНЫ\n\n❌ Executable not found\n   Решение: Переустановите"
        with patch.object(startup_error_diagnosis, "log") as log_mock:
            summary = startup_error_diagnosis.publish_startup_diagnosis(diagnosis)

        self.assertEqual(summary, "📁 ФАЙЛ ИЛИ ПАПКА НЕ НАЙДЕНЫ")
        log_mock.assert_called_once()
        # Детали уходят в журнал уровнем без всплывающего уведомления:
        # каждая ERROR-строка порождала отдельный тост об одной ошибке.
        self.assertNotIn("ERROR", str(log_mock.call_args.args[1]).upper())

    def test_empty_diagnosis_publishes_nothing(self) -> None:
        from winws_runtime.health import startup_error_diagnosis

        with patch.object(startup_error_diagnosis, "log") as log_mock:
            summary = startup_error_diagnosis.publish_startup_diagnosis("   ")

        self.assertEqual(summary, "")
        log_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
