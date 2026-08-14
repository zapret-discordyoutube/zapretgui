from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class UpdaterForgejoCacheStorageTests(unittest.TestCase):
    def test_normalize_settings_drops_legacy_github_api_state(self) -> None:
        from settings.normalize import normalize_settings
        from settings.schema import build_default_settings

        settings = build_default_settings()
        settings["program"]["remove_github_api"] = True
        settings["hosts"]["bootstrap_signature"] = "v3"
        settings["updater"]["github_cache"] = {
            "https://api.github.test/releases": {
                "timestamp": 123,
                "content": [{"body": "x" * 10_000}],
            }
        }
        settings["updater"]["github_rate_limit_reset"] = 123

        normalized = normalize_settings(settings)

        self.assertNotIn("remove_github_api", normalized["program"])
        self.assertNotIn("bootstrap_signature", normalized["hosts"])
        self.assertNotIn("github_cache", normalized["updater"])
        self.assertNotIn("github_rate_limit_reset", normalized["updater"])

    def test_forgejo_cache_is_saved_outside_settings_database(self) -> None:
        from config.runtime_layout import ApplicationPaths
        from settings import store as settings_store
        from updater import forgejo_cache_storage

        cache_payload = {
            "https://git.zapret.moe/api/v1/repos/example/releases": {
                "timestamp": 123.0,
                "content": [{"tag_name": "v1"}],
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                patch("settings.store.MAIN_DIRECTORY", str(root)),
                patch(
                    "updater.forgejo_cache_storage.APPLICATION_PATHS",
                    ApplicationPaths.from_root(root),
                ),
            ):
                settings_store.reset_settings()
                forgejo_cache_storage.save_forgejo_cache(cache_payload)

                settings_data = settings_store.read_settings()
                self.assertNotIn("github_cache", settings_data["updater"])
                self.assertNotIn("github_rate_limit_reset", settings_data["updater"])
                self.assertEqual(forgejo_cache_storage.load_forgejo_cache(), cache_payload)

    def test_legacy_github_cache_file_is_ignored(self) -> None:
        from config.runtime_layout import ApplicationPaths
        from updater import forgejo_cache_storage

        payload = {"legacy": {"timestamp": 1, "content": []}}
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = ApplicationPaths.from_root(Path(temp_dir))
            paths.tmp_dir.mkdir(parents=True, exist_ok=True)
            (paths.tmp_dir / "updater_github_cache.json").write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            with patch("updater.forgejo_cache_storage.APPLICATION_PATHS", paths):
                self.assertEqual(forgejo_cache_storage.load_forgejo_cache(), {})

    def test_prepare_settings_database_creates_only_sqlite_storage(self) -> None:
        from settings import store as settings_store

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                prepared = settings_store.prepare_settings_database()

            self.assertNotIn("github_cache", prepared["updater"])
            self.assertTrue((root / "user" / "settings.sqlite3").is_file())
            self.assertFalse((root / "user" / "settings.json").exists())


if __name__ == "__main__":
    unittest.main()
