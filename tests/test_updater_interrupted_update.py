from __future__ import annotations

"""Распознавание сорвавшегося обновления при следующем запуске.

Пользователь не должен гадать, почему версия осталась прежней: программа
обязана сказать это сама — один раз и с причиной.
"""

from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from updater.install import interrupted
from updater.install.handoff import HandoffState, UpdateHandoffRecord, write_record
from updater.install.interrupted import (
    describe_interrupted_update,
    detect_interrupted_update,
    installation_in_progress,
)


def _record(**overrides) -> UpdateHandoffRecord:
    payload = {
        "state": HandoffState.FAILED,
        "version": "21.1.1.4",
        "target_root": r"C:\Zapret\Stable",
        "installer_path": r"C:\ProgramData\Zapret\update\stable\Zapret2Setup.exe",
        "arguments": ("/AUTOUPDATE", "/SILENT"),
        "error": "установщик вернул код 5",
    }
    payload.update(overrides)
    return UpdateHandoffRecord(**payload)


class InterruptedUpdateDetectionTests(unittest.TestCase):
    def test_failed_update_with_old_version_is_reported(self) -> None:
        detected = detect_interrupted_update(
            current_version="21.1.1.3",
            record=_record(),
            forget=False,
        )

        self.assertIsNotNone(detected)
        self.assertEqual(detected.expected_version, "21.1.1.4")
        self.assertEqual(detected.running_version, "21.1.1.3")
        self.assertEqual(detected.reason, "установщик вернул код 5")
        self.assertEqual(detected.state, HandoffState.FAILED)

    def test_launched_state_without_outcome_is_reported_too(self) -> None:
        """Наблюдателя убили вместе с установкой: итог никто не дописал."""
        detected = detect_interrupted_update(
            current_version="21.1.1.3",
            record=_record(state=HandoffState.LAUNCHED, error=""),
            forget=False,
        )

        self.assertIsNotNone(detected)
        self.assertEqual(detected.reason, "установка прервалась без объяснения")

    def test_successful_update_is_silent(self) -> None:
        self.assertIsNone(
            detect_interrupted_update(
                current_version="21.1.1.4",
                record=_record(state=HandoffState.SUCCEEDED),
                forget=False,
            )
        )

    def test_version_on_disk_wins_over_recorded_failure(self) -> None:
        """Установка всё же дошла до конца — жаловаться не на что."""
        self.assertIsNone(
            detect_interrupted_update(
                current_version="21.1.1.4",
                record=_record(state=HandoffState.FAILED),
                forget=False,
            )
        )

    def test_newer_running_version_is_not_a_failure(self) -> None:
        self.assertIsNone(
            detect_interrupted_update(
                current_version="21.1.2.0",
                record=_record(),
                forget=False,
            )
        )

    def test_prepared_state_is_not_treated_as_failure(self) -> None:
        self.assertIsNone(
            detect_interrupted_update(
                current_version="21.1.1.3",
                record=_record(state=HandoffState.PREPARED),
                forget=False,
            )
        )

    def test_absent_state_means_nothing_to_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertIsNone(
                detect_interrupted_update(
                    current_version="21.1.1.3",
                    state_path=Path(temp_dir) / "handoff.json",
                )
            )


class InterruptedUpdateStateCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        hook_patch = patch.object(interrupted, "clear_recovery_hook", return_value=True)
        self.clear_hook = hook_patch.start()
        self.addCleanup(hook_patch.stop)

    def test_failure_written_by_watchdog_with_bom_is_reported(self) -> None:
        """Так пишет наблюдатель под Windows PowerShell 5.1.

        Раньше BOM ломал чтение, и о сорвавшемся обновлении программа молчала.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "handoff.json"
            payload = _record().to_payload()
            state_path.write_bytes(b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8"))

            detected = detect_interrupted_update(
                current_version="21.1.1.3",
                state_path=state_path,
            )

        self.assertIsNotNone(detected)
        self.assertEqual(detected.reason, "установщик вернул код 5")

    def test_record_with_broken_version_is_dropped_not_repeated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "handoff.json"
            write_record(_record(version=""), state_path)

            detected = detect_interrupted_update(
                current_version="21.1.1.3",
                state_path=state_path,
            )

            self.assertIsNone(detected)
            self.assertFalse(state_path.exists())

    def test_settled_update_releases_recovery_hook(self) -> None:
        """Зависший прежний наблюдатель не снимал страховку сам."""
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "handoff.json"
            write_record(_record(state=HandoffState.LAUNCHED), state_path)

            detect_interrupted_update(current_version="21.1.1.4", state_path=state_path)

        self.clear_hook.assert_called_once()

    def test_reported_failure_is_not_repeated_next_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "handoff.json"
            write_record(_record(), state_path)

            first = detect_interrupted_update(
                current_version="21.1.1.3",
                state_path=state_path,
            )
            second = detect_interrupted_update(
                current_version="21.1.1.3",
                state_path=state_path,
            )

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertFalse(state_path.exists())

    def test_prepared_state_survives_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "handoff.json"
            write_record(_record(state=HandoffState.PREPARED), state_path)

            detect_interrupted_update(current_version="21.1.1.3", state_path=state_path)

            self.assertTrue(state_path.exists())

    def test_completed_update_clears_its_own_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "handoff.json"
            write_record(_record(state=HandoffState.SUCCEEDED), state_path)

            detect_interrupted_update(current_version="21.1.1.4", state_path=state_path)

            self.assertFalse(state_path.exists())


class InterruptedUpdateMessageTests(unittest.TestCase):
    def test_message_names_versions_reason_and_saved_installer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            installer = Path(temp_dir) / "Zapret2Setup.exe"
            installer.write_bytes(b"setup")

            detected = detect_interrupted_update(
                current_version="21.1.1.3",
                record=_record(installer_path=str(installer)),
                forget=False,
            )
            message = describe_interrupted_update(detected)

        self.assertTrue(detected.installer_available)
        self.assertIn("21.1.1.4", message)
        self.assertIn("21.1.1.3", message)
        self.assertIn("установщик вернул код 5", message)
        self.assertIn(str(installer), message)

    def test_message_without_saved_installer_points_to_the_usual_path(self) -> None:
        detected = detect_interrupted_update(
            current_version="21.1.1.3",
            record=_record(installer_path=r"C:\absent\Zapret2Setup.exe"),
            forget=False,
        )
        message = describe_interrupted_update(detected)

        self.assertFalse(detected.installer_available)
        self.assertNotIn(r"C:\absent", message)
        self.assertIn("Серверы", message)


class InstallationInProgressTests(unittest.TestCase):
    """Пока установщик работает, прежняя версия не должна запускаться поверх."""

    NOW = 1_800_000_000.0

    def _in_progress(self, *, current: str = "21.1.1.3", age: float = 10.0, **overrides):
        record = _record(error="", updated_at=self.NOW - age, **overrides)
        return installation_in_progress(current_version=current, record=record, now=self.NOW)

    def test_running_installer_is_seen_by_the_older_version(self) -> None:
        self.assertIsNotNone(self._in_progress(state=HandoffState.LAUNCHED))
        # Программа уже отдала установщик наблюдателю и закрывается.
        self.assertIsNotNone(self._in_progress(state=HandoffState.PREPARED))

    def test_new_version_opened_by_the_installer_starts_normally(self) -> None:
        # Установщик открывает новую версию раньше, чем наблюдатель запишет
        # итог: запись ещё «запущена», но этой версии она не касается.
        self.assertIsNone(self._in_progress(state=HandoffState.LAUNCHED, current="21.1.1.4"))

    def test_finished_installation_blocks_nothing(self) -> None:
        self.assertIsNone(self._in_progress(state=HandoffState.SUCCEEDED))
        self.assertIsNone(self._in_progress(state=HandoffState.FAILED))

    def test_stale_record_never_locks_the_app_out(self) -> None:
        # Установка оборвалась (выключили компьютер): программа обязана открыться.
        self.assertIsNone(
            self._in_progress(
                state=HandoffState.LAUNCHED, age=interrupted.LAUNCHED_IN_PROGRESS_SECONDS + 1
            )
        )
        self.assertIsNone(
            self._in_progress(
                state=HandoffState.PREPARED, age=interrupted.PREPARED_IN_PROGRESS_SECONDS + 1
            )
        )

    def test_clock_moved_back_does_not_lock_the_app_out(self) -> None:
        self.assertIsNone(self._in_progress(state=HandoffState.LAUNCHED, age=-3600.0))

    def test_broken_version_does_not_lock_the_app_out(self) -> None:
        self.assertIsNone(self._in_progress(state=HandoffState.LAUNCHED, version="мусор"))

    def test_running_installer_is_not_reported_as_a_failed_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "handoff.json"
            write_record(_record(state=HandoffState.LAUNCHED, error=""), state_path)

            with patch.object(interrupted, "clear_recovery_hook") as clear_hook:
                detected = detect_interrupted_update(current_version="21.1.1.3", state_path=state_path)

            self.assertIsNone(detected)
            # Запись нужна наблюдателю, чтобы дописать итог.
            self.assertTrue(state_path.exists())
            clear_hook.assert_not_called()


class StartupDuringInstallationTests(unittest.TestCase):
    def _bootstrap(self, record):
        from main import shell

        with (
            patch.object(interrupted, "installation_in_progress", return_value=record),
            patch.object(shell, "is_admin", return_value=True) as is_admin,
            patch.object(shell, "log"),
            patch("startup.single_instance.create_mutex", return_value=(1, False)) as create_mutex,
            patch("startup.single_instance.create_show_event"),
            patch.object(shell, "register_exit_step"),
        ):
            try:
                result = shell.shell_bootstrap(["zapret.exe"])
            except SystemExit as exc:
                result = exc
        return result, is_admin, create_mutex

    def test_launch_during_installation_steps_aside_before_anything_else(self) -> None:
        result, is_admin, create_mutex = self._bootstrap(_record(state=HandoffState.LAUNCHED))

        self.assertIsInstance(result, SystemExit)
        self.assertEqual(result.code, 0)
        # Ни окна UAC, ни отметки «программа уже запущена», которая не дала
        # бы открыться новой версии.
        is_admin.assert_not_called()
        create_mutex.assert_not_called()

    def test_ordinary_launch_goes_on(self) -> None:
        result, _is_admin, create_mutex = self._bootstrap(None)

        self.assertIs(result, False)
        create_mutex.assert_called_once()

    def test_unreadable_update_state_never_blocks_the_launch(self) -> None:
        from main import shell

        with patch.object(interrupted, "installation_in_progress", side_effect=OSError("нет доступа")):
            self.assertFalse(shell._update_is_being_installed())


if __name__ == "__main__":
    unittest.main()
