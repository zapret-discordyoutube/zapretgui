from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class AtomicWriteTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _leftover_tmp_files(self) -> list[str]:
        return sorted(path.name for path in self.root.iterdir() if path.suffix == ".tmp")

    def test_writes_lf_text_with_single_final_newline(self) -> None:
        from utils.atomic_text import atomic_write_text

        target = self.root / "Preset.txt"
        atomic_write_text(target, "--a\r\n--b\r--c")

        self.assertEqual(target.read_bytes(), b"--a\n--b\n--c\n")
        self.assertEqual(self._leftover_tmp_files(), [])

    def test_failed_write_keeps_previous_file_intact(self) -> None:
        from utils.atomic_text import atomic_write_text

        target = self.root / "Preset.txt"
        target.write_text("--old\n", encoding="utf-8")

        # Одиночный суррогат не кодируется в UTF-8: запись падает посередине.
        # Прямой write_text к этому моменту уже обнулил бы файл пресета.
        with self.assertRaises(UnicodeEncodeError):
            atomic_write_text(target, "--new\n\udc80\n")

        self.assertEqual(target.read_text(encoding="utf-8"), "--old\n")
        self.assertEqual(self._leftover_tmp_files(), [])

    def test_read_only_file_is_not_replaced(self) -> None:
        import stat

        from utils.atomic_text import atomic_write_text

        target = self.root / "Preset.txt"
        target.write_text("--old\n", encoding="utf-8")
        target.chmod(stat.S_IRUSR)
        self.addCleanup(target.chmod, stat.S_IRUSR | stat.S_IWUSR)

        with self.assertRaises(PermissionError):
            atomic_write_text(target, "--new\n")

        self.assertEqual(target.read_text(encoding="utf-8"), "--old\n")
        self.assertEqual(self._leftover_tmp_files(), [])

    def test_replace_is_retried_while_windows_holds_the_file(self) -> None:
        from utils import atomic_text

        target = self.root / "Preset.txt"
        target.write_text("--old\n", encoding="utf-8")
        real_replace = os.replace
        attempts: list[str] = []

        def flaky_replace(source, destination):
            attempts.append(str(destination))
            if len(attempts) < 3:
                raise PermissionError(5, "Access is denied")
            real_replace(source, destination)

        with patch.object(atomic_text.os, "replace", flaky_replace), patch.object(atomic_text.time, "sleep"):
            atomic_text.atomic_write_text(target, "--new\n")

        self.assertEqual(len(attempts), 3)
        self.assertEqual(target.read_text(encoding="utf-8"), "--new\n")
        self.assertEqual(self._leftover_tmp_files(), [])

    def test_persistent_lock_raises_and_keeps_previous_file(self) -> None:
        from utils import atomic_text

        target = self.root / "Preset.txt"
        target.write_text("--old\n", encoding="utf-8")

        def locked_replace(_source, _destination):
            raise PermissionError(32, "The process cannot access the file")

        with patch.object(atomic_text.os, "replace", locked_replace), patch.object(atomic_text.time, "sleep"):
            with self.assertRaises(PermissionError):
                atomic_text.atomic_write_text(target, "--new\n")

        self.assertEqual(target.read_text(encoding="utf-8"), "--old\n")
        self.assertEqual(self._leftover_tmp_files(), [])


class ReadPresetFileTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_utf8_with_and_without_bom(self) -> None:
        from utils.atomic_text import read_preset_file_text

        plain = self.root / "plain.txt"
        plain.write_bytes("# Пресет\n--a\n".encode("utf-8"))
        bom = self.root / "bom.txt"
        bom.write_bytes(b"\xef\xbb\xbf" + "# Пресет\n--a\n".encode("utf-8"))

        self.assertEqual(read_preset_file_text(plain), "# Пресет\n--a\n")
        self.assertEqual(read_preset_file_text(bom), "# Пресет\n--a\n")

    def test_cp1251_file_keeps_russian_letters(self) -> None:
        from utils.atomic_text import read_preset_file_text

        legacy = self.root / "legacy.txt"
        legacy.write_bytes("# Мой пресет\n--hostlist=lists/мой.txt\n".encode("cp1251"))

        text = read_preset_file_text(legacy)

        self.assertEqual(text, "# Мой пресет\n--hostlist=lists/мой.txt\n")
        self.assertNotIn("�", text)


class DecodePresetBytesTests(unittest.TestCase):
    def test_one_broken_byte_does_not_turn_utf8_file_into_cp1251(self) -> None:
        from utils.atomic_text import decode_preset_bytes

        text = decode_preset_bytes("# Мой пресет\n--hostlist=lists/мой.txt\n".encode("utf-8") + b"\xd0")

        # Раньше весь файл читался как cp1251: «РњРѕР№ РїСЂРµСЃРµС‚».
        self.assertTrue(text.startswith("# Мой пресет\n--hostlist=lists/мой.txt\n"))

    def test_real_cp1251_file_is_still_detected(self) -> None:
        from utils.atomic_text import decode_preset_bytes

        self.assertEqual(decode_preset_bytes("# Мой пресет\n".encode("cp1251")), "# Мой пресет\n")


class PresetFileStoreEncodingAndWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        from core.paths import AppPaths
        from presets.file_store import PresetFileStore

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.store = PresetFileStore(AppPaths(user_root=root / "user", local_root=root / "local"))
        self.user_dir = self.store._engine_paths("winws2").user_presets_dir
        self.user_dir.mkdir(parents=True, exist_ok=True)

    def test_cp1251_preset_is_listed_read_and_saved_without_corruption(self) -> None:
        source = "# Preset: Мой пресет\n--filter-tcp=443\n--lua-desync=pass\n"
        (self.user_dir / "Legacy.txt").write_bytes(source.encode("cp1251"))

        manifest = self.store.get_manifest("winws2", "Legacy.txt")
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest.name, "Мой пресет")
        self.assertEqual(self.store.read_source_text("winws2", "Legacy.txt"), source)

        self.store.update_preset("winws2", "Legacy.txt", source + "# ещё строка\n", None)

        saved = (self.user_dir / "Legacy.txt").read_bytes().decode("utf-8")
        self.assertEqual(saved, source + "# ещё строка\n")
        self.assertEqual(sorted(path.name for path in self.user_dir.iterdir()), ["Legacy.txt"])

    def test_rename_marks_old_path_as_own_change(self) -> None:
        from presets.own_write_registry import was_recent_own_preset_write

        (self.user_dir / "Old name unique.txt").write_text("--filter-tcp=443\n", encoding="utf-8")

        self.store.rename_preset("winws2", "Old name unique.txt", "New name unique")

        # Исчезновение старого файла — своя операция, а не внешняя правка
        # (иначе watcher активного пресета перезапускал DPI лишний раз).
        self.assertTrue(was_recent_own_preset_write("Old name unique.txt"))
        self.assertTrue(was_recent_own_preset_write("New name unique.txt"))


class SharedFolderSettingsLockTests(unittest.TestCase):
    def test_preset_and_profile_folders_share_one_settings_lock(self) -> None:
        # Обе стороны пишут целиком одну секцию «folders»: под разными
        # замками параллельная запись одной откатывала правку другой.
        from presets import folders as preset_folders
        from profile import folders as profile_folders

        self.assertIs(preset_folders._PRESET_FOLDER_STATE_LOCK, profile_folders._PROFILE_FOLDER_STATE_LOCK)


class WorkerRuntimeStopTests(unittest.TestCase):
    def test_stop_reports_forced_termination(self) -> None:
        from unittest.mock import Mock

        from ui.one_shot_worker_runtime import OneShotWorkerRuntime

        runtime = OneShotWorkerRuntime()
        hung = Mock()
        hung.isRunning.return_value = True
        hung.wait.return_value = False
        del hung.is_running
        del hung.stop
        runtime.thread = hung

        self.assertTrue(runtime.stop(blocking=True))
        hung.terminate.assert_called_once_with()

        finished = Mock()
        finished.isRunning.return_value = True
        finished.wait.return_value = True
        del finished.is_running
        del finished.stop
        runtime.thread = finished
        self.assertFalse(runtime.stop(blocking=True))


if __name__ == "__main__":
    unittest.main()
