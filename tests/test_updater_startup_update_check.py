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
        self.auto_install_attempts = 0
        patches = (
            patch(
                "settings.store.get_auto_install_attempts",
                side_effect=lambda _version: self.auto_install_attempts,
            ),
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

    def _check(self, lookup: ReleaseLookup, *, now: float = NOW, signalled: bool = False) -> dict:
        def fake_lookup(channel: str) -> ReleaseLookup:
            self.lookups.append(channel)
            return lookup

        with patch("updater.release.resolver.lookup_latest_release", side_effect=fake_lookup):
            return startup_update_check.check_for_update_sync(now=now, signalled=signalled)

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

    def test_server_signal_is_checked_despite_the_pause(self) -> None:
        self._check(ReleaseLookup({"version": "21.1.5.79"}))
        signalled = self._check(
            ReleaseLookup({"version": "21.1.5.80"}), now=NOW + 60, signalled=True
        )

        self.assertTrue(signalled["has_update"])
        self.assertEqual(self.lookups, ["dev", "dev"])

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


class StartupSkippedVersionTests(unittest.TestCase):
    """«Пропустить версию» решает фоновая проверка: интерфейс не читает настройки."""

    setUp = StartupUpdateCheckTests.setUp
    _check = StartupUpdateCheckTests._check

    def _found(self, skipped: str, *, signalled: bool = False) -> dict:
        release = {"version": "21.1.5.80", "release_notes": "новое", "source": "Forgejo"}
        with patch("settings.store.get_update_skipped_version", return_value=skipped):
            return self._check(ReleaseLookup(release), signalled=signalled)

    def test_skipped_version_is_marked(self) -> None:
        result = self._found("21.1.5.80")

        self.assertTrue(result["has_update"])
        self.assertTrue(result["user_skipped"])

    def test_newer_version_than_skipped_is_offered_again(self) -> None:
        self.assertFalse(self._found("21.1.5.79")["user_skipped"])

    def test_found_update_carries_history(self) -> None:
        result = self._found("")

        self.assertEqual([item["version"] for item in result["release_history"]], ["21.1.5.80"])
        self.assertTrue(result["release_url"].endswith("/releases/tag/21.1.5.80"))


class SelfInstallDecisionTests(unittest.TestCase):
    """Ставить ли находку без вопроса, решает сервер с очередью, а не программа."""

    setUp = StartupUpdateCheckTests.setUp
    _check = StartupUpdateCheckTests._check
    _found = StartupSkippedVersionTests._found

    def test_update_allowed_by_server_is_installed_without_asking(self) -> None:
        self.assertTrue(self._found("", signalled=True)["auto_install"])

    def test_app_never_installs_on_its_own_decision(self) -> None:
        # Пользователей около миллиона: без очереди на сервере все установки
        # пошли бы за установщиком разом.
        found = self._found("")

        self.assertTrue(found["has_update"])
        self.assertFalse(found["auto_install"])

    def test_skipped_version_is_not_installed_by_the_app(self) -> None:
        self.assertFalse(self._found("21.1.5.80", signalled=True)["auto_install"])

    def test_app_stops_installing_a_version_that_keeps_failing(self) -> None:
        # Каждая попытка закрывает программу: без лимита сбой установщика
        # стал бы петлёй перезапусков.
        self.auto_install_attempts = startup_update_check.AUTO_INSTALL_MAX_ATTEMPTS - 1
        self.assertTrue(self._found("", signalled=True)["auto_install"])

        self.auto_install_attempts = startup_update_check.AUTO_INSTALL_MAX_ATTEMPTS
        limited = self._found("", signalled=True)

        self.assertFalse(limited["auto_install"])
        # Обновление никуда не делось: его предложит окно.
        self.assertTrue(limited["has_update"])

    def test_unreadable_attempt_count_falls_back_to_asking(self) -> None:
        with patch("settings.store.get_auto_install_attempts", side_effect=OSError("диск")):
            self.assertFalse(self._found("", signalled=True)["auto_install"])


class SelfInstallAttemptCounterTests(unittest.TestCase):
    def test_attempts_are_counted_per_version(self) -> None:
        import tempfile

        from settings.store import add_auto_install_attempt, get_auto_install_attempts

        with tempfile.TemporaryDirectory() as tmp, patch("settings.store.MAIN_DIRECTORY", tmp):
            self.assertEqual(get_auto_install_attempts("21.1.5.80"), 0)
            self.assertEqual(add_auto_install_attempt("21.1.5.80"), 1)
            self.assertEqual(add_auto_install_attempt("21.1.5.80"), 2)
            self.assertEqual(get_auto_install_attempts("21.1.5.80"), 2)

            # У следующей версии счёт начинается заново.
            self.assertEqual(get_auto_install_attempts("21.1.5.81"), 0)
            self.assertEqual(add_auto_install_attempt("21.1.5.81"), 1)
            self.assertEqual(get_auto_install_attempts("21.1.5.80"), 0)

    def test_attempt_remembers_where_the_update_started_and_its_outcome(self) -> None:
        import tempfile

        from settings.store import (
            add_auto_install_attempt,
            get_auto_install_state,
            set_auto_install_outcome,
        )

        with tempfile.TemporaryDirectory() as tmp, patch("settings.store.MAIN_DIRECTORY", tmp):
            add_auto_install_attempt("21.1.5.80", from_version="21.1.5.79", granted_at=1234.5)

            state = get_auto_install_state()
            self.assertEqual(
                (state["version"], state["from_version"], state["granted_at"], state["outcome"]),
                ("21.1.5.80", "21.1.5.79", 1234.5, "started"),
            )

            # Исход чужой версии эту запись не трогает.
            self.assertFalse(set_auto_install_outcome("21.1.5.81", "failed"))
            self.assertTrue(set_auto_install_outcome("21.1.5.80", "failed"))
            self.assertEqual(get_auto_install_state()["outcome"], "failed")
            self.assertTrue(set_auto_install_outcome("21.1.5.80", ""))
            # Счёт попыток при этом сохраняется: он останавливает петлю перезапусков.
            self.assertEqual(get_auto_install_state()["attempts"], 1)

    def test_garbage_in_saved_outcome_is_dropped(self) -> None:
        from settings.normalize import normalize_auto_install

        state = normalize_auto_install(
            {"version": "21.1.5.80", "attempts": 2, "granted_at": "вчера", "outcome": "что-то"}
        )

        self.assertEqual((state["granted_at"], state["outcome"], state["attempts"]), (0.0, "", 2))
        self.assertEqual(normalize_auto_install({"outcome": "failed"})["outcome"], "")


if __name__ == "__main__":
    unittest.main()
