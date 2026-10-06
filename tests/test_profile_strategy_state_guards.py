from __future__ import annotations

import copy
import unittest
from unittest.mock import Mock, patch

from profile.strategy_state import ProfileStrategyState, ProfileStrategyStateStore


class _MemorySettings:
    """Раздел настроек в памяти вместо settings.sqlite3."""

    def __init__(self, data: dict | None = None) -> None:
        self.data = copy.deepcopy(data) if data is not None else {"version": 1, "profiles": {}}
        self.writes = 0

    def read(self) -> dict:
        return copy.deepcopy(self.data)

    def write(self, values: dict) -> dict:
        from settings.normalize import normalize_profile_strategy_state

        self.data = normalize_profile_strategy_state(values)
        self.writes += 1
        return copy.deepcopy(self.data)

    def patched(self):
        return (
            patch("profile.strategy_state.settings_store.get_profile_strategy_state_settings", side_effect=self.read),
            patch("profile.strategy_state.settings_store.set_profile_strategy_state_settings", side_effect=self.write),
        )


class ProfileStrategyOpenGroupTests(unittest.TestCase):
    """Открытая группа готовых стратегий помнится для каждого профиля."""

    def _store(self, data: dict | None = None) -> tuple[ProfileStrategyStateStore, _MemorySettings]:
        settings = _MemorySettings(data)
        for item in settings.patched():
            item.start()
            self.addCleanup(item.stop)
        return ProfileStrategyStateStore(), settings

    def test_nothing_is_remembered_until_the_user_opens_a_group(self) -> None:
        store, _settings = self._store()

        self.assertIsNone(store.get_open_group("uid:youtube"))
        self.assertIsNone(store.get_open_group("not-a-profile-key"))

    def test_group_is_saved_per_profile_and_survives_normalization(self) -> None:
        store, settings = self._store()

        self.assertTrue(store.set_open_group("uid:youtube", "fake_split"))
        self.assertTrue(store.set_open_group("uid:discord", "host"))

        self.assertEqual(store.get_open_group("uid:youtube"), "fake_split")
        self.assertEqual(store.get_open_group("uid:discord"), "host")
        self.assertEqual(settings.data["profiles"]["uid:youtube"], {"open_group": "fake_split"})

    def test_all_groups_closed_is_remembered_too(self) -> None:
        store, _settings = self._store()
        store.set_open_group("uid:youtube", "fake")

        store.set_open_group("uid:youtube", "")

        self.assertEqual(store.get_open_group("uid:youtube"), "")

    def test_same_group_is_not_written_twice(self) -> None:
        store, settings = self._store()
        store.set_open_group("uid:youtube", "fake")

        self.assertFalse(store.set_open_group("uid:youtube", "fake"))

        self.assertEqual(settings.writes, 1)

    def test_bad_group_key_is_rejected(self) -> None:
        store, settings = self._store()

        with self.assertRaises(ValueError):
            store.set_open_group("uid:youtube", "Подмена пакета")
        with self.assertRaises(ValueError):
            store.set_open_group("", "fake")

        self.assertEqual(settings.writes, 0)

    def test_ratings_and_open_group_do_not_erase_each_other(self) -> None:
        store, settings = self._store()
        store.set_open_group("uid:youtube", "split")
        store.set_strategy_state("uid:youtube", "tls_fake", rating="work")

        self.assertEqual(store.get_open_group("uid:youtube"), "split")
        self.assertEqual(store.get_strategy_state("uid:youtube", "tls_fake").rating, "work")

        # Последняя оценка снята — открытая группа остаётся.
        store.set_strategy_state("uid:youtube", "tls_fake", rating="")
        self.assertEqual(settings.data["profiles"]["uid:youtube"], {"open_group": "split"})

        store.set_strategy_state("uid:youtube", "tls_fake", favorite=True)
        store.clear_strategy_state("uid:youtube", "tls_fake")
        self.assertEqual(store.get_open_group("uid:youtube"), "split")

    def test_profile_without_marks_and_open_group_is_still_dropped(self) -> None:
        store, settings = self._store()
        store.set_strategy_state("uid:youtube", "tls_fake", rating="work")

        store.set_strategy_state("uid:youtube", "tls_fake", rating="")

        self.assertEqual(settings.data["profiles"], {})

    def test_settings_normalization_drops_a_broken_group_key(self) -> None:
        from settings.normalize import normalize_profile_strategy_state

        normalized = normalize_profile_strategy_state(
            {
                "profiles": {
                    "uid:a": {"open_group": "fake"},
                    "uid:b": {"open_group": ""},
                    "uid:c": {"open_group": "DROP TABLE"},
                    "uid:d": {"open_group": 7},
                    "uid:e": {"open_group": "split", "strategies": {"tls_fake": {"rating": "work"}}},
                }
            }
        )

        self.assertEqual(normalized["profiles"]["uid:a"], {"open_group": "fake"})
        self.assertEqual(normalized["profiles"]["uid:b"], {"open_group": ""})
        self.assertNotIn("uid:c", normalized["profiles"])
        self.assertNotIn("uid:d", normalized["profiles"])
        self.assertEqual(normalized["profiles"]["uid:e"]["open_group"], "split")
        self.assertEqual(normalized["profiles"]["uid:e"]["strategies"]["tls_fake"]["rating"], "work")

    def test_every_real_group_key_can_be_saved(self) -> None:
        from profile.strategy_families import STRATEGY_FAMILIES

        store, _settings = self._store()
        for family in STRATEGY_FAMILIES:
            with self.subTest(group=family.key):
                store.set_open_group("uid:youtube", family.key)
                self.assertEqual(store.get_open_group("uid:youtube"), family.key)


class ProfileStrategyStateGuardTests(unittest.TestCase):
    def test_clear_strategy_state_skips_write_when_strategy_is_already_absent(self) -> None:
        data = {
            "version": 1,
            "profiles": {
                "name:Speedtest": {
                    "strategies": {
                        "other_strategy": {
                            "favorite": False,
                            "rating": "work",
                            "updated_at": "2026-05-31T00:00:00Z",
                        },
                    },
                },
            },
        }

        with (
            patch(
                "profile.strategy_state.settings_store.get_profile_strategy_state_settings",
                side_effect=lambda: copy.deepcopy(data),
            ),
            patch(
                "profile.strategy_state.settings_store.set_profile_strategy_state_settings",
                Mock(side_effect=AssertionError("absent strategy state must not be written")),
            ),
        ):
            ProfileStrategyStateStore().clear_strategy_state("name:Speedtest", "tls_fake")

    def test_set_strategy_state_skips_write_when_state_is_unchanged(self) -> None:
        data = {
            "version": 1,
            "profiles": {
                "name:Speedtest": {
                    "strategies": {
                        "tls_fake": {
                            "favorite": True,
                            "rating": "work",
                            "updated_at": "2026-05-31T00:00:00Z",
                        },
                    },
                },
            },
        }

        with (
            patch(
                "profile.strategy_state.settings_store.get_profile_strategy_state_settings",
                side_effect=lambda: copy.deepcopy(data),
            ),
            patch(
                "profile.strategy_state.settings_store.set_profile_strategy_state_settings",
                Mock(side_effect=AssertionError("unchanged strategy state must not be written")),
            ),
        ):
            state = ProfileStrategyStateStore().set_strategy_state(
                "name:Speedtest",
                "tls_fake",
                rating="work",
                favorite=True,
            )

        self.assertEqual(state, ProfileStrategyState(rating="work", favorite=True))


if __name__ == "__main__":
    unittest.main()


class ProfileStrategyStateUidMigrationTests(unittest.TestCase):
    def test_uid_keys_are_accepted(self) -> None:
        data = {"version": 1, "profiles": {}}
        writes = []
        with (
            patch(
                "profile.strategy_state.settings_store.get_profile_strategy_state_settings",
                side_effect=lambda: copy.deepcopy(data),
            ),
            patch(
                "profile.strategy_state.settings_store.set_profile_strategy_state_settings",
                side_effect=lambda value: writes.append(value) or value,
            ),
        ):
            store = ProfileStrategyStateStore()
            store.set_strategy_state("uid:abc123", "fake_tls", rating="work")

        self.assertEqual(len(writes), 1)
        self.assertIn("uid:abc123", writes[0]["profiles"])

    def test_migrate_moves_legacy_rows_to_uid(self) -> None:
        data = {
            "version": 1,
            "profiles": {
                "name:YouTube": {
                    "strategies": {"fake_tls": {"favorite": True, "rating": "work", "updated_at": "2026-01-01T00:00:00Z"}},
                },
                "uid:kept": {
                    "strategies": {"other": {"favorite": False, "rating": "notwork", "updated_at": "2026-01-01T00:00:00Z"}},
                },
            },
        }
        writes = []
        with (
            patch(
                "profile.strategy_state.settings_store.get_profile_strategy_state_settings",
                side_effect=lambda: copy.deepcopy(data),
            ),
            patch(
                "profile.strategy_state.settings_store.set_profile_strategy_state_settings",
                side_effect=lambda value: writes.append(value) or value,
            ),
        ):
            store = ProfileStrategyStateStore()
            changed = store.migrate_profile_keys({"name:YouTube": "uid:ytb", "name:Absent": "uid:none"})

        self.assertTrue(changed)
        profiles = writes[0]["profiles"]
        self.assertNotIn("name:YouTube", profiles)
        self.assertTrue(profiles["uid:ytb"]["strategies"]["fake_tls"]["favorite"])
        self.assertIn("uid:kept", profiles)

    def test_migrate_existing_uid_row_wins(self) -> None:
        data = {
            "version": 1,
            "profiles": {
                "name:YouTube": {"strategies": {"a": {"favorite": True, "rating": "work", "updated_at": "2026-01-01T00:00:00Z"}}},
                "uid:ytb": {"strategies": {"b": {"favorite": False, "rating": "notwork", "updated_at": "2026-01-02T00:00:00Z"}}},
            },
        }
        writes = []
        with (
            patch(
                "profile.strategy_state.settings_store.get_profile_strategy_state_settings",
                side_effect=lambda: copy.deepcopy(data),
            ),
            patch(
                "profile.strategy_state.settings_store.set_profile_strategy_state_settings",
                side_effect=lambda value: writes.append(value) or value,
            ),
        ):
            store = ProfileStrategyStateStore()
            changed = store.migrate_profile_keys({"name:YouTube": "uid:ytb"})

        self.assertTrue(changed)
        profiles = writes[0]["profiles"]
        self.assertNotIn("name:YouTube", profiles)
        # Существующая uid-запись не перезаписана legacy-данными.
        self.assertEqual(list(profiles["uid:ytb"]["strategies"].keys()), ["b"])

    def test_migrate_noop_without_legacy_rows(self) -> None:
        data = {"version": 1, "profiles": {"uid:only": {"strategies": {}}}}
        writes = []
        with (
            patch(
                "profile.strategy_state.settings_store.get_profile_strategy_state_settings",
                side_effect=lambda: copy.deepcopy(data),
            ),
            patch(
                "profile.strategy_state.settings_store.set_profile_strategy_state_settings",
                side_effect=lambda value: writes.append(value) or value,
            ),
        ):
            store = ProfileStrategyStateStore()
            changed = store.migrate_profile_keys({"name:Ghost": "uid:ghost"})

        self.assertFalse(changed)
        self.assertEqual(writes, [])
