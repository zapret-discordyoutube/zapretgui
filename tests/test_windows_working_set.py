"""Снимок страниц памяти и подтягивание их пачкой (platforms.windows.working_set)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from platforms.windows import working_set as ws  # noqa: E402


class MergePagesTests(unittest.TestCase):
    def test_neighbouring_pages_become_one_range(self) -> None:
        page = ws.PAGE_SIZE
        pages = [0x10000, 0x10000 + page, 0x10000 + 2 * page, 0x40000, 0x80000, 0x80000 + page]

        merged = ws._merge_pages(pages)

        self.assertEqual(merged, [(0x10000, 3 * page), (0x40000, page), (0x80000, 2 * page)])

    def test_empty_input_gives_no_ranges(self) -> None:
        self.assertEqual(ws._merge_pages([]), [])


class OutsideWindowsTests(unittest.TestCase):
    def test_other_systems_do_nothing(self) -> None:
        with patch.object(ws.sys, "platform", "linux"):
            self.assertIsNone(ws.snapshot_working_set())
            snapshot = ws.WorkingSetSnapshot(ranges=((0x1000, 0x1000),), total_bytes=0x1000, taken_ms=0.0)
            self.assertIsNone(ws.prefetch_working_set(snapshot))

    def test_prefetch_without_snapshot_does_nothing(self) -> None:
        self.assertIsNone(ws.prefetch_working_set(None))
        empty = ws.WorkingSetSnapshot(ranges=(), total_bytes=0, taken_ms=0.0)
        self.assertIsNone(ws.prefetch_working_set(empty))

    def test_module_never_empties_the_working_set(self) -> None:
        # «Минус единицы» в SetProcessWorkingSetSizeEx — это выгрузка памяти; хватку
        # отпускаем малым минимумом, а не ею.
        source = Path(ws.__file__).read_text(encoding="utf-8")
        self.assertNotIn("EmptyWorkingSet(", source)
        self.assertNotIn("(-1", source)
        self.assertNotIn("c_size_t(-1)", source)

    def test_page_faults_are_unknown_outside_windows(self) -> None:
        with patch.object(ws.sys, "platform", "linux"):
            self.assertIsNone(ws.process_page_faults())

    def test_hold_and_release_do_nothing_outside_windows(self) -> None:
        with patch.object(ws.sys, "platform", "linux"):
            self.assertIsNone(ws.hold_working_set(100 << 20))
            self.assertFalse(ws.release_working_set_hold())
            self.assertIsNone(ws.current_working_set_bytes())
            self.assertIsNone(ws.available_physical_bytes())


class HoldSizeTests(unittest.TestCase):
    MB = 1 << 20

    def test_holds_the_working_set_plus_a_margin(self) -> None:
        size = ws.hold_size_for(80 * self.MB, 8 * 1024 * self.MB)
        self.assertEqual(size, 80 * self.MB + ws.HOLD_MARGIN_BYTES)

    def test_large_working_set_is_capped(self) -> None:
        self.assertEqual(ws.hold_size_for(900 * self.MB, 8 * 1024 * self.MB), ws.HOLD_MAX_BYTES)

    def test_tiny_or_unknown_working_set_is_not_held(self) -> None:
        self.assertIsNone(ws.hold_size_for(None, None))
        self.assertIsNone(ws.hold_size_for(ws.HOLD_MIN_BYTES - 1, None))

    def test_tight_memory_is_not_made_tighter(self) -> None:
        size = 80 * self.MB + ws.HOLD_MARGIN_BYTES
        self.assertIsNone(ws.hold_size_for(80 * self.MB, size + ws.HOLD_SPARE_BYTES - 1))
        self.assertEqual(ws.hold_size_for(80 * self.MB, size + ws.HOLD_SPARE_BYTES), size)

    def test_weak_pc_with_under_a_gigabyte_free_is_still_protected(self) -> None:
        # Раньше «свободно меньше 1 ГБ» отключало защиту именно там, где Windows
        # отбирает страницы сильнее всего.
        self.assertIsNotNone(ws.hold_size_for(80 * self.MB, 700 * self.MB))

    def test_unknown_free_memory_does_not_block_the_hold(self) -> None:
        self.assertIsNotNone(ws.hold_size_for(80 * self.MB, None))


if __name__ == "__main__":
    unittest.main()
