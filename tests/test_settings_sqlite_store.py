from __future__ import annotations

import json
import multiprocessing
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


def _write_setting_from_process(
    root: str,
    start_event,
    setting: str,
) -> None:
    from settings import store as settings_store

    settings_store.close_settings_database()
    settings_store.MAIN_DIRECTORY = root
    start_event.wait(10)
    if setting == "display_mode":
        settings_store.set_display_mode("light")
    elif setting == "mica_enabled":
        settings_store.set_mica_enabled(False)
    else:  # pragma: no cover - test helper contract
        raise ValueError(setting)
    settings_store.close_settings_database()


class SettingsSqliteStoreTests(unittest.TestCase):
    def test_database_has_normalized_sections_and_revision(self) -> None:
        from settings import store as settings_store

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                prepared = settings_store.prepare_settings_database()
                database_path = settings_store.get_settings_database_path()
                settings_store.close_settings_database()

            with closing(sqlite3.connect(database_path)) as connection:
                sections = {
                    str(row[0])
                    for row in connection.execute("SELECT section FROM settings_sections")
                }
                revision = int(
                    connection.execute(
                        "SELECT value FROM settings_meta WHERE key='revision'"
                    ).fetchone()[0]
                )
                user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])

            self.assertEqual(sections, set(prepared))
            self.assertGreaterEqual(revision, 1)
            self.assertEqual(user_version, 1)
            self.assertFalse((root / "settings" / "settings.json").exists())

    def test_cache_refreshes_after_another_connection_commits(self) -> None:
        from settings import store as settings_store

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                settings_store.reset_settings()
                self.assertEqual(settings_store.get_display_mode(), "dark")
                database_path = settings_store.get_settings_database_path()

                with closing(sqlite3.connect(database_path, timeout=10.0)) as external:
                    external.execute("PRAGMA busy_timeout=10000")
                    appearance = json.loads(
                        external.execute(
                            "SELECT payload FROM settings_sections WHERE section='appearance'"
                        ).fetchone()[0]
                    )
                    appearance["display_mode"] = "light"
                    external.execute(
                        "UPDATE settings_sections SET payload=? WHERE section='appearance'",
                        (json.dumps(appearance),),
                    )
                    external.execute(
                        "UPDATE settings_meta SET value=value+1 WHERE key='revision'"
                    )
                    external.commit()

                self.assertEqual(settings_store.get_display_mode(), "light")

    def test_reset_settings_does_not_touch_premium_database(self) -> None:
        from settings import store as settings_store

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            premium_path = root / "settings" / "premium.sqlite3"
            premium_path.parent.mkdir(parents=True)
            premium_path.write_bytes(b"premium-binding-sentinel")

            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                settings_store.set_display_mode("light")
                reset = settings_store.reset_settings()

            self.assertEqual(reset["appearance"]["display_mode"], "dark")
            self.assertEqual(premium_path.read_bytes(), b"premium-binding-sentinel")

    def test_two_processes_do_not_lose_updates_in_the_same_section(self) -> None:
        from settings import store as settings_store

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                settings_store.reset_settings()
                settings_store.close_settings_database()

                context = multiprocessing.get_context("spawn")
                start_event = context.Event()
                processes = [
                    context.Process(
                        target=_write_setting_from_process,
                        args=(str(root), start_event, setting),
                    )
                    for setting in ("display_mode", "mica_enabled")
                ]
                for process in processes:
                    process.start()
                start_event.set()
                for process in processes:
                    process.join(20)
                    self.assertEqual(process.exitcode, 0)

                self.assertEqual(settings_store.get_display_mode(), "light")
                self.assertFalse(settings_store.get_mica_enabled())


if __name__ == "__main__":
    unittest.main()
