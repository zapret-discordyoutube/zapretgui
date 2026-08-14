from __future__ import annotations

import copy
import unittest
from unittest.mock import Mock, patch

from profile.strategy_state import ProfileStrategyState, ProfileStrategyStateStore


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
