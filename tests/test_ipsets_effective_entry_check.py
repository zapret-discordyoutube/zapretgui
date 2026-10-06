"""«Файл ipset не пустой» отвечается по первой настоящей записи."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lists import ipsets_manager  # noqa: E402


class HasEffectiveIpEntryTests(unittest.TestCase):
    def _write(self, text: str) -> str:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "ipset.txt"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_answer_matches_full_parse(self) -> None:
        cases = {
            "": False,
            "# только комментарий\n\n": False,
            "не адрес\n1.2.3.4-1.2.3.9\n": False,
            "# шапка\n10.0.0.0/8\n": True,
            "мусор\n\n8.8.8.8\n": True,
            "2001:db8::/32\n": True,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                path = self._write(text)
                self.assertEqual(ipsets_manager._has_effective_ip_entry(path), expected)
                self.assertEqual(bool(ipsets_manager._read_effective_ip_entries(path)), expected)

    def test_missing_file_has_no_entries(self) -> None:
        self.assertFalse(ipsets_manager._has_effective_ip_entry("/nonexistent/zapret/ipset.txt"))

    def test_big_file_is_not_parsed_to_the_end(self) -> None:
        path = self._write("\n".join(f"10.{i // 256}.{i % 256}.0/24" for i in range(30_000)) + "\n")
        real_normalize = ipsets_manager._normalize_ip_entry
        calls = 0

        def counting(text):
            nonlocal calls
            calls += 1
            return real_normalize(text)

        with patch.object(ipsets_manager, "_normalize_ip_entry", side_effect=counting):
            self.assertTrue(ipsets_manager._has_effective_ip_entry(path))

        # Разбор каждой строки держит GIL: полный проход по 30 тысячам строк
        # отнимал время у интерфейса ради ответа «да, не пустой».
        self.assertEqual(calls, 1)

    def test_rebuild_reports_ready_file_without_full_parse(self) -> None:
        path = self._write("10.0.0.0/8\n8.8.8.8\n")

        with (
            patch.object(ipsets_manager, "rebuild_profile_list_file"),
            patch.object(ipsets_manager, "IPSET_ALL_PATH", path),
            patch.object(
                ipsets_manager,
                "_read_effective_ip_entries",
                side_effect=AssertionError("полный разбор не нужен"),
            ),
        ):
            self.assertTrue(ipsets_manager.rebuild_ipset_all_files())


class FullParseYieldsGilTests(unittest.TestCase):
    """Полный разбор списка в фоне регулярно отдаёт GIL интерфейсу."""

    def test_long_file_yields_every_block_of_lines(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "ipset.txt"
        lines = 5 * ipsets_manager._GIL_YIELD_EVERY_LINES + 10
        path.write_text("\n".join(f"10.{i // 256}.{i % 256}.0/24" for i in range(lines)) + "\n", encoding="utf-8")

        with patch.object(ipsets_manager.time, "sleep") as sleep:
            entries = ipsets_manager._read_effective_ip_entries(str(path))

        # Windows отбирает GIL у занятого потока только раз в ~15 мс: без
        # добровольной паузы разбор 33 тысяч строк давал рывки интерфейса.
        self.assertEqual(len(entries), lines)
        self.assertEqual(sleep.call_count, 5)
        sleep.assert_called_with(0)

    def test_short_file_does_not_pause(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "ipset.txt"
        path.write_text("10.0.0.0/8\n8.8.8.8\n", encoding="utf-8")

        with patch.object(ipsets_manager.time, "sleep") as sleep:
            self.assertEqual(ipsets_manager._read_effective_ip_entries(str(path)), ["10.0.0.0/8", "8.8.8.8"])

        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
