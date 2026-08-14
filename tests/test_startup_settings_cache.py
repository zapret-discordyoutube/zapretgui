from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class StartupSettingsCacheTests(unittest.TestCase):
    def test_repeated_settings_getters_reuse_one_database_snapshot(self) -> None:
        from settings import store as settings_store

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                settings_store.reset_settings()

                with patch.object(
                    settings_store,
                    "_read_settings_database_locked",
                    wraps=settings_store._read_settings_database_locked,
                ) as read_database:
                    self.assertEqual(settings_store.get_display_mode(), "dark")
                    self.assertEqual(settings_store.get_strategy_launch_method(), "zapret2_mode")
                    self.assertEqual(settings_store.get_window_opacity(), 100)

                self.assertEqual(read_database.call_count, 0)

    def test_settings_write_updates_cached_reads(self) -> None:
        from settings import store as settings_store

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                settings_store.reset_settings()
                self.assertEqual(settings_store.get_display_mode(), "dark")

                settings_store.set_display_mode("light")

                with patch.object(
                    settings_store,
                    "_read_settings_database_locked",
                    wraps=settings_store._read_settings_database_locked,
                ) as read_database:
                    self.assertEqual(settings_store.get_display_mode(), "light")

                self.assertEqual(read_database.call_count, 0)

    def test_same_settings_value_does_not_advance_database_revision(self) -> None:
        from settings import store as settings_store

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                settings_store.reset_settings()
                revision_before = settings_store.get_settings_revision()
                self.assertTrue(settings_store.set_display_mode("dark"))

                self.assertEqual(settings_store.get_display_mode(), "dark")
                self.assertEqual(settings_store.get_settings_revision(), revision_before)


if __name__ == "__main__":
    unittest.main()
