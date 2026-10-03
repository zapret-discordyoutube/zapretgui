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
    elif setting == "tinted_background":
        settings_store.set_tinted_background(True)
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
            self.assertFalse((root / "user" / "settings.json").exists())

    def test_orchestra_locked_state_for_all_askeys_is_written_in_one_update(self) -> None:
        from settings import store as settings_store

        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", temp_dir):
                settings_store.prepare_settings_database()
                settings_store.set_orchestra_locked_map("http", {"old.com": 1})
                with patch.object(settings_store, "_update_settings", wraps=settings_store._update_settings) as update:
                    settings_store.set_orchestra_locked_state(
                        {"tls": {"YouTube.com": 3}, "http": {}},
                        {"tls": ["YouTube.com", "youtube.com"], "http": []},
                    )
                tls = settings_store.get_orchestra_locked_map("tls")
                http = settings_store.get_orchestra_locked_map("http")
                user_tls = settings_store.get_orchestra_user_locked("tls")
                settings_store.close_settings_database()

        self.assertEqual(update.call_count, 1)
        self.assertEqual(tls, {"youtube.com": 3})
        self.assertEqual(http, {})
        self.assertEqual(user_tls, ["youtube.com"])

    def test_orchestra_history_for_several_targets_is_written_in_one_update(self) -> None:
        from settings import store as settings_store

        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", temp_dir):
                settings_store.prepare_settings_database()
                settings_store.set_orchestra_history({"old.com": {"1": {"successes": 1, "failures": 0}}})
                with patch.object(settings_store, "_update_settings", wraps=settings_store._update_settings) as update:
                    written = settings_store.set_orchestra_history_for_targets(
                        {
                            "YouTube.com": {"2": {"successes": 3, "failures": 1}},
                            "discord.com": {"4": {"successes": 0, "failures": 2}},
                        }
                    )
                history = settings_store.get_orchestra_history()
                settings_store.close_settings_database()

        self.assertEqual(written, 2)
        self.assertEqual(update.call_count, 1)
        self.assertEqual(set(history), {"old.com", "youtube.com", "discord.com"})
        self.assertEqual(history["youtube.com"]["2"], {"successes": 3, "failures": 1})

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
            premium_path = root / "user" / "premium.sqlite3"
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
                    for setting in ("display_mode", "tinted_background")
                ]
                for process in processes:
                    process.start()
                start_event.set()
                for process in processes:
                    process.join(20)
                    self.assertEqual(process.exitcode, 0)

                self.assertEqual(settings_store.get_display_mode(), "light")
                self.assertTrue(settings_store.get_tinted_background())

    def test_reset_premium_appearance_clears_premium_background_and_effects_in_one_write(self) -> None:
        from settings import store as settings_store

        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                settings_store.reset_settings()
                settings_store.set_background_preset("rkn_chan")
                settings_store.set_garland_enabled(True)
                settings_store.set_snowflakes_enabled(True)
                settings_store.set_rkn_background("rkn_tyan/bg.jpg")
                revision_before = settings_store.get_settings_revision()

                appearance = settings_store.reset_premium_appearance()

                self.assertEqual(appearance["background_preset"], "standard")
                self.assertFalse(appearance["garland_enabled"])
                self.assertFalse(appearance["snowflakes_enabled"])
                self.assertEqual(settings_store.get_background_preset(), "standard")
                self.assertEqual(settings_store.get_rkn_background(), "rkn_tyan/bg.jpg")
                self.assertEqual(settings_store.get_settings_revision(), revision_before + 1)
                settings_store.close_settings_database()

    def test_reset_premium_appearance_keeps_free_background(self) -> None:
        from settings import store as settings_store

        with TemporaryDirectory() as temp_dir:
            with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
                settings_store.reset_settings()
                settings_store.set_background_preset("standard")

                appearance = settings_store.reset_premium_appearance()

                self.assertEqual(appearance["background_preset"], "standard")
                settings_store.close_settings_database()


if __name__ == "__main__":
    unittest.main()
