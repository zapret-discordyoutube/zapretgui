from __future__ import annotations

import inspect
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

import support_request_bundle


class SupportRequestClipboardBoundaryTests(unittest.TestCase):
    def test_support_request_bundle_does_not_use_qt_clipboard(self) -> None:
        source = inspect.getsource(support_request_bundle)

        self.assertNotIn("QApplication", source)
        self.assertNotIn("PyQt6", source)

    def test_prepare_support_request_uses_local_clipboard_helper(self) -> None:
        with patch.object(support_request_bundle, "_copy_to_clipboard", return_value=True) as copy:
            result = support_request_bundle.prepare_support_request(
                bundle_prefix="support",
                context_label="Test",
                candidate_paths=[],
                open_discussions=False,
                open_bundle_folder=False,
            )

        copy.assert_called_once_with(result.template_text)
        self.assertTrue(result.copied_to_clipboard)

    def test_large_support_log_is_split_into_upload_sized_archives(self) -> None:
        rng = random.Random(12345)

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            big_log = base / "zapret_winws2_debug_20260613.log"
            big_log.write_bytes(bytes(rng.randrange(0, 256) for _ in range(7000)))

            archive_paths, included_files = support_request_bundle.create_support_archives(
                bundle_prefix="support_logs",
                candidate_paths=[big_log],
                output_dir=base / "bundles",
                max_archive_bytes=4096,
            )

            self.assertGreater(len(archive_paths), 1)
            self.assertTrue(all(Path(path).stat().st_size <= 4096 for path in archive_paths))
            self.assertTrue(any(".part01-of-" in name for name in included_files))


if __name__ == "__main__":
    unittest.main()


class BlockcheckBundleFilesTests(unittest.TestCase):
    """В архив обращения BlockCheck идут свежие отчёты, а не забытый текстовый журнал."""

    def test_old_text_log_is_not_picked_and_two_latest_reports_are(self) -> None:
        import os
        import tempfile
        from pathlib import Path

        import support_request_actions as actions
        from support_request_bundle import find_recent_logs

        # Шаблона текстового журнала проверки больше нет: файл давно не пишется.
        self.assertFalse(any(pattern.startswith("blockcheck_run_") and pattern.endswith(".log") for pattern in actions.BLOCKCHECK_RECENT_PATTERNS))
        with tempfile.TemporaryDirectory() as folder:
            names = ["blockcheck_run_2026-10-08_14-29-04_full.log", "blockcheck_run_1_full.json", "blockcheck_run_2_full.json", "blockcheck_run_3_full.json"]
            for order, name in enumerate(names):
                path = Path(folder) / name
                path.write_text("x", encoding="utf-8")
                os.utime(path, (1000 + order, 1000 + order))
            found = find_recent_logs(logs_folder=folder, patterns=actions.BLOCKCHECK_RECENT_PATTERNS, limit_per_pattern=actions.BLOCKCHECK_RECENT_REPORTS)
        # Два последних отчёта — пара «с Zapret и без»; старый .log в архив не попадает.
        self.assertEqual([path.name for path in found], ["blockcheck_run_3_full.json", "blockcheck_run_2_full.json"])
