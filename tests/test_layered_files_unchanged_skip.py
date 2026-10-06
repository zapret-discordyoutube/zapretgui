"""Фоновая сверка списка не читает файлы, если они не менялись."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lists import ipsets_manager  # noqa: E402
from lists.core import layered_files  # noqa: E402
from lists.core.builders import dedup_preserve_order, merge_base_and_user  # noqa: E402
from lists.core.layered_files import (  # noqa: E402
    rebuild_all_layered_list_files,
    rebuild_profile_list_file,
    write_profile_user_list_text,
)


class UnchangedListSkipTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "base").mkdir()
        (self.root / "user").mkdir()
        (self.root / "base" / "site.txt").write_text("a.com\nb.com\n", encoding="utf-8")
        (self.root / "base" / "other.txt").write_text("x.com\n", encoding="utf-8")
        layered_files._RECONCILED_STAMPS.clear()
        self.addCleanup(layered_files._RECONCILED_STAMPS.clear)

    def _counted_snapshots(self):
        return patch.object(layered_files, "_load_snapshot", wraps=layered_files._load_snapshot)

    @staticmethod
    def _touch_later(path: Path) -> None:
        # Часы файловой системы могут не успеть сдвинуться между двумя
        # записями в тесте: время изменения двигаем явно.
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))

    def test_repeated_pass_over_unchanged_lists_reads_nothing(self) -> None:
        # Сверка всех списков идёт при запуске дважды и перед каждым пуском
        # обхода: 128 файлов, 2 МБ текста, всё в Python под GIL.
        self.assertEqual(rebuild_all_layered_list_files(self.root), 2)

        with self._counted_snapshots() as snapshots:
            self.assertEqual(rebuild_all_layered_list_files(self.root), 2)

        snapshots.assert_not_called()
        self.assertEqual((self.root / "site.txt").read_text(encoding="utf-8"), "a.com\nb.com\n")

    def test_first_pass_after_start_is_always_real(self) -> None:
        with self._counted_snapshots() as snapshots:
            rebuild_all_layered_list_files(self.root)

        self.assertEqual(snapshots.call_count, 2)

    def test_changed_user_layer_is_merged_again(self) -> None:
        rebuild_profile_list_file(self.root, "site.txt")
        user_path = self.root / "user" / "site.txt"
        user_path.write_text("mine.com\n", encoding="utf-8")

        rebuild_profile_list_file(self.root, "site.txt")

        self.assertEqual((self.root / "site.txt").read_text(encoding="utf-8"), "a.com\nb.com\nmine.com\n")

    def test_same_size_edit_with_new_time_is_merged_again(self) -> None:
        user_path = self.root / "user" / "site.txt"
        user_path.write_text("one.com\n", encoding="utf-8")
        rebuild_profile_list_file(self.root, "site.txt")

        user_path.write_text("two.com\n", encoding="utf-8")
        self._touch_later(user_path)
        rebuild_profile_list_file(self.root, "site.txt")

        self.assertEqual((self.root / "site.txt").read_text(encoding="utf-8"), "a.com\nb.com\ntwo.com\n")

    def test_changed_base_layer_is_merged_again(self) -> None:
        rebuild_profile_list_file(self.root, "site.txt")
        (self.root / "base" / "site.txt").write_text("a.com\nb.com\nc.com\n", encoding="utf-8")

        rebuild_profile_list_file(self.root, "site.txt")

        self.assertEqual((self.root / "site.txt").read_text(encoding="utf-8"), "a.com\nb.com\nc.com\n")

    def test_deleted_final_file_is_restored(self) -> None:
        rebuild_profile_list_file(self.root, "site.txt")
        (self.root / "site.txt").unlink()

        rebuild_profile_list_file(self.root, "site.txt")

        self.assertEqual((self.root / "site.txt").read_text(encoding="utf-8"), "a.com\nb.com\n")

    def test_final_file_spoiled_from_outside_is_restored(self) -> None:
        rebuild_profile_list_file(self.root, "site.txt")
        final_path = self.root / "site.txt"
        final_path.write_text("чужое.com\n", encoding="utf-8")
        self._touch_later(final_path)

        rebuild_profile_list_file(self.root, "site.txt")

        self.assertEqual(final_path.read_text(encoding="utf-8"), "a.com\nb.com\n")

    def test_user_action_is_never_skipped(self) -> None:
        write_profile_user_list_text(self.root, "site.txt", "mine.com\n")

        with self._counted_snapshots() as snapshots:
            write_profile_user_list_text(self.root, "site.txt", "mine.com\n")

        snapshots.assert_called_once()

    def test_background_pass_after_user_action_is_skipped(self) -> None:
        write_profile_user_list_text(self.root, "site.txt", "mine.com\n")

        with self._counted_snapshots() as snapshots:
            rebuild_profile_list_file(self.root, "site.txt")

        snapshots.assert_not_called()
        self.assertEqual((self.root / "site.txt").read_text(encoding="utf-8"), "a.com\nb.com\nmine.com\n")

    def test_failed_pass_is_not_remembered(self) -> None:
        with patch.object(layered_files, "_apply_plan", side_effect=OSError("диск")):
            with self.assertRaises(OSError):
                rebuild_profile_list_file(self.root, "site.txt")

        with self._counted_snapshots() as snapshots:
            rebuild_profile_list_file(self.root, "site.txt")

        snapshots.assert_called_once()


class MergeOrderTests(unittest.TestCase):
    def test_base_goes_first_and_repeats_are_dropped(self) -> None:
        self.assertEqual(
            merge_base_and_user(["b.com", "a.com", "b.com"], ["c.com", "a.com", "c.com", "d.com"]),
            ["b.com", "a.com", "c.com", "d.com"],
        )
        self.assertEqual(dedup_preserve_order(["x", "y", "x", "z", "y"]), ["x", "y", "z"])
        self.assertEqual(merge_base_and_user([], []), [])


class StartupLogLineCountTests(unittest.TestCase):
    def _write(self, text: str) -> str:
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "ipset-all.txt"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_count_matches_full_parse_for_built_file(self) -> None:
        path = self._write("# шапка\n\n10.0.0.0/8\n8.8.8.8\n2001:db8::/32\n")

        self.assertEqual(ipsets_manager._count_list_lines(path), 3)
        self.assertEqual(len(ipsets_manager._read_effective_ip_entries(path)), 3)

    def test_missing_file_counts_as_empty(self) -> None:
        self.assertEqual(ipsets_manager._count_list_lines("/nonexistent/zapret/ipset.txt"), 0)

    def test_startup_check_does_not_parse_addresses_for_the_log_line(self) -> None:
        # Разбор 33 тысяч строк через ipaddress ради числа в журнале занимал
        # 120 мс чистого Python в фоновом потоке в первые секунды работы.
        path = self._write("\n".join(f"10.{i // 256}.{i % 256}.0/24" for i in range(2_000)) + "\n")
        real_normalize = ipsets_manager._normalize_ip_entry
        calls = 0

        def counting(text):
            nonlocal calls
            calls += 1
            return real_normalize(text)

        with (
            patch.object(ipsets_manager, "IPSET_ALL_PATH", path),
            patch.object(ipsets_manager, "IPSET_ALL_USER_PATH", path),
            patch.object(ipsets_manager, "IPSET_RU_PATH", path),
            patch.object(ipsets_manager, "IPSET_RU_USER_PATH", path),
            patch.object(ipsets_manager, "LISTS_FOLDER", str(Path(path).parent)),
            patch.object(ipsets_manager, "rebuild_profile_list_file"),
            patch.object(ipsets_manager, "_normalize_ip_entry", side_effect=counting),
            patch.object(ipsets_manager, "log"),
        ):
            self.assertTrue(ipsets_manager.startup_ipsets_check())

        # Одна запись — проверка «файл не пустой»; остальное только считается.
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
