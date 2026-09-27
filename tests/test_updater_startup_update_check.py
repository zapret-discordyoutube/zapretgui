from __future__ import annotations

"""Проверка обновлений при запуске: всегда свежая, пауза — только после успеха.

Раньше неудачная проверка всё равно включала шестичасовую паузу, и после сбоя
сети программа часами не искала обновления.
"""

import unittest
from unittest.mock import patch

from updater import startup_update_check
from updater.release.resolver import ReleaseLookup

NOW = 1_800_000_000.0


class StartupUpdateCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stored: dict = {}
        self.lookups: list[str] = []
        patches = (
            patch("config.build_info.APP_VERSION", "21.1.5.79"),
            patch("config.build_info.CHANNEL", "dev"),
            patch(
                "settings.store.get_updater_settings",
                side_effect=lambda: {"auto_check": dict(self.stored)},
            ),
            patch(
                "settings.store.set_updater_settings",
                side_effect=lambda values: self.stored.update(values["auto_check"]),
            ),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def _check(self, lookup: ReleaseLookup, *, now: float = NOW) -> dict:
        def fake_lookup(channel: str) -> ReleaseLookup:
            self.lookups.append(channel)
            return lookup

        with patch("updater.release.resolver.lookup_latest_release", side_effect=fake_lookup):
            return startup_update_check.check_for_update_sync(now=now)

    def test_failed_check_reports_error_and_does_not_pause_next_launch(self) -> None:
        failed = self._check(ReleaseLookup(None, "Не удалось узнать новейшую версию"))
        retried = self._check(ReleaseLookup({"version": "21.1.5.79"}), now=NOW + 5)

        self.assertEqual(failed["error"], "Не удалось узнать новейшую версию")
        self.assertFalse(failed["has_update"])
        self.assertNotIn("skipped", retried)
        self.assertEqual(self.lookups, ["dev", "dev"])

    def test_found_update_is_offered_on_every_launch(self) -> None:
        release = {"version": "21.1.5.80", "release_notes": "новое"}

        first = self._check(ReleaseLookup(release))
        second = self._check(ReleaseLookup(release), now=NOW + 5)

        self.assertTrue(first["has_update"])
        self.assertTrue(second["has_update"])
        self.assertEqual(second["version"], "21.1.5.80")

    def test_quick_restart_after_clean_check_is_skipped(self) -> None:
        self._check(ReleaseLookup({"version": "21.1.5.79"}))
        skipped = self._check(ReleaseLookup({"version": "21.1.5.80"}), now=NOW + 60)

        self.assertTrue(skipped["skipped"])
        self.assertEqual(self.lookups, ["dev"])

    def test_pause_is_short(self) -> None:
        self._check(ReleaseLookup({"version": "21.1.5.79"}))
        later = self._check(
            ReleaseLookup({"version": "21.1.5.80"}),
            now=NOW + startup_update_check.AUTO_CHECK_PAUSE_SECONDS + 1,
        )

        self.assertTrue(later["has_update"])

    def test_clock_moved_back_does_not_block_checks(self) -> None:
        self._check(ReleaseLookup({"version": "21.1.5.79"}))
        after_clock_change = self._check(ReleaseLookup({"version": "21.1.5.80"}), now=NOW - 3600)

        self.assertTrue(after_clock_change["has_update"])


if __name__ == "__main__":
    unittest.main()
