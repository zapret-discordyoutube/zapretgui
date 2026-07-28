from __future__ import annotations

"""Распознавание сорвавшегося обновления при следующем запуске.

Пользователь не должен гадать, почему версия осталась прежней: программа
обязана сказать это сама — один раз и с причиной.
"""

from pathlib import Path
import tempfile
import unittest

from updater.handoff_state import HandoffState, UpdateHandoffRecord, write_record
from updater.interrupted_update import (
    describe_interrupted_update,
    detect_interrupted_update,
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


if __name__ == "__main__":
    unittest.main()
