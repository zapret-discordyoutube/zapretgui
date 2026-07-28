from __future__ import annotations

"""Состояние передачи управления установщику.

Запись переживает закрытие приложения и читается наблюдателем, поэтому
проверяются два свойства: она никогда не видна наполовину записанной и
никогда не «выздоравливает» из битого файла в правдоподобное состояние.
"""

import json
from pathlib import Path
import tempfile
import unittest

from updater.handoff_state import (
    ALLOWED_TRANSITIONS,
    HandoffState,
    SCHEMA_VERSION,
    UpdateHandoffRecord,
    can_transition,
    clear_record,
    read_record,
    write_record,
)


def _record(**overrides) -> UpdateHandoffRecord:
    payload = {
        "state": HandoffState.PREPARED,
        "version": "21.1.1.4",
        "target_root": r"C:\Zapret\Stable",
        "installer_path": r"C:\ProgramData\Zapret\update\stable\Zapret2Setup.exe",
        "arguments": ("/AUTOUPDATE", "/SILENT"),
        "gui_pid": 4242,
    }
    payload.update(overrides)
    return UpdateHandoffRecord(**payload)


class HandoffRecordRoundTripTests(unittest.TestCase):
    def test_record_survives_write_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "handoff.json"
            self.assertTrue(write_record(_record(), path, now=1700000000.0))

            restored = read_record(path)

        self.assertIsNotNone(restored)
        self.assertEqual(restored.state, HandoffState.PREPARED)
        self.assertEqual(restored.version, "21.1.1.4")
        self.assertEqual(restored.arguments, ("/AUTOUPDATE", "/SILENT"))
        self.assertEqual(restored.gui_pid, 4242)
        self.assertEqual(restored.updated_at, 1700000000.0)
        self.assertEqual(restored.schema_version, SCHEMA_VERSION)

    def test_write_is_atomic_and_leaves_no_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "handoff.json"
            write_record(_record(), path)
            write_record(_record(state=HandoffState.LAUNCHED), path)

            leftovers = sorted(item.name for item in Path(temp_dir).iterdir())

        self.assertEqual(leftovers, ["handoff.json"])

    def test_partial_file_is_not_mistaken_for_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "handoff.json"
            path.write_text('{"state": "launc', encoding="utf-8")

            self.assertIsNone(read_record(path))

    def test_unknown_state_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "handoff.json"
            path.write_text(json.dumps({"state": "installing"}), encoding="utf-8")

            self.assertIsNone(read_record(path))

    def test_missing_file_reads_as_no_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertIsNone(read_record(Path(temp_dir) / "absent.json"))

    def test_broken_numbers_do_not_break_reading(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "handoff.json"
            path.write_text(
                json.dumps(
                    {
                        "state": "failed",
                        "version": "21.1.1.4",
                        "gui_pid": "не число",
                        "installer_exit_code": "нет",
                        "updated_at": None,
                    }
                ),
                encoding="utf-8",
            )

            restored = read_record(path)

        self.assertIsNotNone(restored)
        self.assertEqual(restored.gui_pid, 0)
        self.assertIsNone(restored.installer_exit_code)
        self.assertEqual(restored.updated_at, 0.0)

    def test_clear_removes_state_and_tolerates_absence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "handoff.json"
            write_record(_record(), path)
            clear_record(path)
            clear_record(path)

            self.assertFalse(path.exists())


class HandoffTransitionTests(unittest.TestCase):
    def test_first_state_can_only_be_prepared(self) -> None:
        self.assertTrue(can_transition(None, HandoffState.PREPARED))
        self.assertFalse(can_transition(None, HandoffState.LAUNCHED))
        self.assertFalse(can_transition("", HandoffState.SUCCEEDED))

    def test_update_runs_forward(self) -> None:
        self.assertTrue(can_transition(HandoffState.PREPARED, HandoffState.LAUNCHED))
        self.assertTrue(can_transition(HandoffState.LAUNCHED, HandoffState.SUCCEEDED))
        self.assertTrue(can_transition(HandoffState.LAUNCHED, HandoffState.FAILED))

    def test_finished_update_cannot_silently_reopen(self) -> None:
        self.assertFalse(can_transition(HandoffState.SUCCEEDED, HandoffState.LAUNCHED))
        self.assertFalse(can_transition(HandoffState.FAILED, HandoffState.SUCCEEDED))
        self.assertFalse(can_transition(HandoffState.PREPARED, HandoffState.SUCCEEDED))

    def test_retry_starts_from_preparation(self) -> None:
        self.assertTrue(can_transition(HandoffState.FAILED, HandoffState.PREPARED))
        self.assertTrue(can_transition(HandoffState.SUCCEEDED, HandoffState.PREPARED))

    def test_unknown_states_are_never_allowed(self) -> None:
        self.assertFalse(can_transition("installing", HandoffState.LAUNCHED))
        self.assertFalse(can_transition(HandoffState.PREPARED, "installing"))

    def test_every_state_has_a_declared_transition_set(self) -> None:
        self.assertEqual(set(ALLOWED_TRANSITIONS), set(HandoffState))


if __name__ == "__main__":
    unittest.main()
