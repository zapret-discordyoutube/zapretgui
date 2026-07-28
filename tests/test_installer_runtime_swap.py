from __future__ import annotations

"""Установщик не имеет права разрушать рабочую версию до готовности новой.

Прежняя схема удаляла ``{app}\\_internal`` до распаковки, а ``[InstallDelete]``
не участвует в откате Inno: любой сбой распаковки оставлял систему вообще без
приложения. Здесь закреплена схема «распаковать рядом — переключить одним
переименованием — проверить — только потом удалить прежнее».
"""

from pathlib import Path
import re
import unittest


PUBLIC_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = PUBLIC_ROOT.parent / "private_zapretgui"
INNO_SCRIPT = PRIVATE_ROOT / "build_zapret" / "zapret_universal.iss"


def _read_script() -> str:
    return INNO_SCRIPT.read_text(encoding="utf-8")


def _section(iss: str, name: str) -> str:
    match = re.search(rf"^\[{name}\]$(.*?)(?=^\[|\Z)", iss, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ""


def _routine(iss: str, name: str) -> str:
    code = iss[iss.index("[Code]"):]
    headers = list(re.finditer(r"^(?:function|procedure)\s+([A-Za-z_]\w*)", code, re.MULTILINE))
    for index, header in enumerate(headers):
        if header.group(1) != name:
            continue
        end = headers[index + 1].start() if index + 1 < len(headers) else len(code)
        return code[header.start():end]
    return ""


@unittest.skipUnless(INNO_SCRIPT.is_file(), "нужен закрытый репозиторий со сборкой установщика")
class InstallerRuntimeLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.iss = _read_script()

    def test_runtime_is_unpacked_next_to_the_working_copy(self) -> None:
        files = _section(self.iss, "Files")
        runtime_lines = [
            line
            for line in files.splitlines()
            if line.strip().startswith("Source:") and "_internal" in line
        ]

        self.assertEqual(len(runtime_lines), 1)
        self.assertIn(r'DestDir: "{app}\_internal.new"', runtime_lines[0])

    def test_runtime_is_not_scheduled_for_replacement_after_reboot(self) -> None:
        """restartreplace оставлял пользователя без приложения до перезагрузки."""
        files = _section(self.iss, "Files")

        self.assertNotIn("restartreplace", files)

    def test_working_runtime_is_never_deleted_before_installation(self) -> None:
        install_delete = _section(self.iss, "InstallDelete")
        deleted_names = re.findall(r'Name:\s*"([^"]+)"', install_delete)

        self.assertNotIn(r"{app}\_internal", deleted_names)
        self.assertIn(r"{app}\_internal.new", deleted_names)
        self.assertNotIn(r"{app}\_internal.old", deleted_names)

    def test_uninstall_removes_the_working_runtime_and_both_swap_leftovers(self) -> None:
        uninstall_delete = _section(self.iss, "UninstallDelete")
        deleted_names = re.findall(r'Name:\s*"([^"]+)"', uninstall_delete)

        for expected in (r"{app}\_internal", r"{app}\_internal.new", r"{app}\_internal.old"):
            self.assertIn(expected, deleted_names)


@unittest.skipUnless(INNO_SCRIPT.is_file(), "нужен закрытый репозиторий со сборкой установщика")
class InstallerRuntimeSwapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.iss = _read_script()
        self.swap = _routine(self.iss, "CommitRuntimeSwap")
        self.assertTrue(self.swap, "процедура свопа runtime отсутствует")

    def test_swap_refuses_to_start_without_a_complete_new_version(self) -> None:
        checkpoint = self.swap.index("RuntimeDirectoryContainsExe(StagingDir)")
        rename = self.swap.index("TryRenameRuntimeDirectory(LiveDir, BackupDir)")

        self.assertLess(checkpoint, rename)

    def test_swap_moves_working_copy_aside_before_switching(self) -> None:
        move_aside = self.swap.index("TryRenameRuntimeDirectory(LiveDir, BackupDir)")
        switch = self.swap.index("TryRenameRuntimeDirectory(StagingDir, LiveDir)")

        self.assertLess(move_aside, switch)

    def test_backup_is_removed_only_after_the_new_version_is_verified(self) -> None:
        verify = self.swap.index("RuntimeDirectoryContainsExe(LiveDir)")
        cleanup = self.swap.index("TryRemoveRuntimeDirectory(BackupDir)", verify)

        self.assertLess(verify, cleanup)

    def test_every_failure_restores_the_previous_version(self) -> None:
        rollbacks = self.swap.count("TryRenameRuntimeDirectory(BackupDir, LiveDir)")

        # Откат нужен и при неудачном переключении, и при непрошедшей проверке.
        self.assertGreaterEqual(rollbacks, 2)

    def test_directory_operations_survive_a_temporary_lock(self) -> None:
        rename = _routine(self.iss, "TryRenameRuntimeDirectory")
        remove = _routine(self.iss, "TryRemoveRuntimeDirectory")

        self.assertIn("for Attempt := 1 to", rename)
        self.assertIn("Sleep(", rename)
        self.assertIn("for Attempt := 1 to", remove)

    def test_swap_runs_before_anything_else_after_installation(self) -> None:
        step_changed = _routine(self.iss, "CurStepChanged")
        swap_call = step_changed.index("CommitRuntimeSwap(FailureReason)")

        for later in ("WriteInstallOwnerMarker", "RelocatePersistentBindingsIfNeeded",
                      "CleanupPreviousInstallIfRequested"):
            self.assertLess(swap_call, step_changed.index(later))

    def test_failed_swap_stops_finalization_and_tells_the_user(self) -> None:
        step_changed = _routine(self.iss, "CurStepChanged")
        failure_branch = step_changed[step_changed.index("if not RuntimeSwapSucceeded then"):]

        self.assertIn("WarnRuntimeSwapFailed(FailureReason)", failure_branch)
        self.assertIn("Exit;", failure_branch)
        self.assertIn("MsgBox", _routine(self.iss, "WarnRuntimeSwapFailed"))


if __name__ == "__main__":
    unittest.main()
