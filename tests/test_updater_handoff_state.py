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

from updater.install.handoff import (
    HandoffState,
    SCHEMA_VERSION,
    UpdateHandoffRecord,
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

    def test_partial_file_is_not_mistaken_for_state_and_is_removed(self) -> None:
        """Битая запись удаляется, иначе предупреждение повторялось бы вечно."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "handoff.json"
            path.write_text('{"state": "launc', encoding="utf-8")

            self.assertIsNone(read_record(path))
            self.assertFalse(path.exists())

    def test_record_written_by_windows_powershell_with_bom_is_read(self) -> None:
        """Windows PowerShell 5.1 пишет UTF-8 с BOM — так выглядела живая запись."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "handoff.json"
            payload = {
                "schema_version": 1,
                "state": "succeeded",
                "version": "21.1.5.67",
                "target_root": "C:\\Zapret\\Dev",
                "installer_path": "C:\\ProgramData\\Zapret\\update\\dev\\Zapret2Setup.exe",
                "arguments": ["/AUTOUPDATE", "/SILENT"],
                "gui_pid": 23544,
                "installer_exit_code": None,
                "installed_version": "21.1.5.67",
                "error": "",
                "updated_at": 1787348465.317,
            }
            path.write_bytes(b"\xef\xbb\xbf" + json.dumps(payload, indent=4).encode("utf-8"))

            restored = read_record(path)

        self.assertIsNotNone(restored)
        self.assertEqual(restored.state, HandoffState.SUCCEEDED)
        self.assertEqual(restored.installed_version, "21.1.5.67")
        self.assertEqual(restored.schema_version, 1)
        self.assertEqual(restored.installer_sha256, "")

    def test_installer_checksum_travels_with_the_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "handoff.json"
            write_record(_record(installer_sha256="ab" * 32), path)

            restored = read_record(path)

        self.assertEqual(restored.installer_sha256, "ab" * 32)

    def test_planted_temporary_file_does_not_capture_the_write(self) -> None:
        """Временный файл с предсказуемым именем мог подложить обычный пользователь."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "handoff.json"
            planted = Path(temp_dir) / "handoff.json.new"
            planted.write_text("planted", encoding="utf-8")

            self.assertTrue(write_record(_record(), path))

            self.assertEqual(planted.read_text(encoding="utf-8"), "planted")
            self.assertEqual(read_record(path).version, "21.1.1.4")

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


if __name__ == "__main__":
    unittest.main()
