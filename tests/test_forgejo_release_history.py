from __future__ import annotations

"""Изменения всех пропущенных версий для окна обновления.

Forgejo уже отдаёт список выпусков канала — из него берутся версии между
установленной и предлагаемой, без лишних запросов. Зеркала знают только
последний выпуск, тогда в окне будет он один.
"""

import unittest
from unittest.mock import patch

from test_updater_forgejo_release import _FakeForgejo, _release, _Response
from updater.check.service import outcome_to_result
from updater.check.flow import CheckOutcome
from updater.release import forgejo
from updater.release.history import history_limit, recent_history, release_history_since


class ForgejoHistoryTests(unittest.TestCase):
    def test_latest_release_carries_channel_history_without_extra_requests(self) -> None:
        fake = _FakeForgejo([[_release("21.1.5.80"), _release("21.1.5.79"), _release("21.1.5.78")]])

        with patch.object(forgejo, "_get", side_effect=fake.get):
            release = forgejo.fetch_latest_release("dev")

        self.assertEqual([item["version"] for item in release["history"]], ["21.1.5.80", "21.1.5.79", "21.1.5.78"])
        self.assertEqual(release["history"][1]["notes"], "notes 21.1.5.79")
        self.assertTrue(release["history"][0]["url"].endswith("/releases/tag/21.1.5.80"))
        # Один запрос списка и один файл суммы — история ничего не добавила.
        self.assertEqual(len(fake.requested), 2)

    def test_history_marks_skipped_versions_new_and_adds_earlier_ones(self) -> None:
        release = {
            "version": "21.1.5.80",
            "release_notes": "80",
            "history": (
                {"version": "21.1.5.78", "notes": "78"},
                {"version": "21.1.5.80", "notes": "80"},
                {"version": "21.1.5.77", "notes": "77"},
                {"version": "21.1.5.79", "notes": "79"},
                {"version": "21.1.5.81", "notes": "не предлагается"},
                {"version": "мусор", "notes": "x"},
            ),
        }

        history = release_history_since(release, current_version="21.1.5.78")

        self.assertEqual(
            [(item["version"], item["is_new"]) for item in history],
            [("21.1.5.80", True), ("21.1.5.79", True), ("21.1.5.78", False), ("21.1.5.77", False)],
        )

    def test_history_is_ten_releases_but_never_drops_skipped_ones(self) -> None:
        entries = tuple({"version": f"1.0.{n}", "notes": str(n)} for n in range(1, 31))

        few_skipped = recent_history(entries, up_to_version="1.0.30", new_after_version="1.0.28")
        many_skipped = recent_history(entries, up_to_version="1.0.30", new_after_version="1.0.10")

        self.assertEqual(len(few_skipped), 10)
        self.assertEqual(sum(item["is_new"] for item in few_skipped), 2)
        self.assertEqual(len(many_skipped), 20)
        self.assertTrue(all(item["is_new"] for item in many_skipped))

    def test_stable_shows_three_releases_but_never_drops_skipped_ones(self) -> None:
        entries = tuple({"version": f"1.0.{n}", "notes": str(n)} for n in range(1, 31))

        with patch("updater.release.history.history_limit", return_value=history_limit("stable")):
            one_skipped = recent_history(entries, up_to_version="1.0.30", new_after_version="1.0.29")
            many_skipped = recent_history(entries, up_to_version="1.0.30", new_after_version="1.0.25")

        self.assertEqual(history_limit("stable"), 3)
        self.assertEqual(history_limit("dev"), 10)
        self.assertEqual([item["version"] for item in one_skipped], ["1.0.30", "1.0.29", "1.0.28"])
        self.assertEqual(len(many_skipped), 5)
        self.assertTrue(all(item["is_new"] for item in many_skipped))

    def test_mirror_release_without_history_shows_itself(self) -> None:
        release = {"version": "21.1.5.80", "release_notes": "с зеркала", "published_at": "2026-09-30"}

        history = release_history_since(release, current_version="21.1.5.70")

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["notes"], "с зеркала")
        # Тег выпуска совпадает с версией: страница на Forgejo всё равно есть.
        self.assertTrue(history[0]["url"].endswith("/releases/tag/21.1.5.80"))

    def test_manual_check_result_carries_history_and_url(self) -> None:
        release = {
            "version": "21.1.5.80",
            "release_notes": "80",
            "source": "Forgejo",
            "release_url": "https://git.zapret.moe/r/80",
            "history": ({"version": "21.1.5.80", "notes": "80"}, {"version": "21.1.5.79", "notes": "79"}),
        }

        result = outcome_to_result(CheckOutcome(release), app_version="21.1.5.78")

        self.assertEqual([item["version"] for item in result["release_history"]], ["21.1.5.80", "21.1.5.79"])
        self.assertEqual(result["release_url"], "https://git.zapret.moe/r/80")

    def test_release_notes_of_installed_version_come_by_tag(self) -> None:
        requested: list[str] = []

        def get(_session, url: str, *, accept: str):
            requested.append(url)
            return _Response({"tag_name": "21.1.5.80", "body": "текст", "published_at": "2026-09-30T10:00:00Z"})

        with patch.object(forgejo, "_get", side_effect=get):
            notes = forgejo.fetch_release_notes("v21.1.5.80")

        self.assertTrue(requested[0].endswith("/releases/tags/21.1.5.80"))
        self.assertEqual(notes["notes"], "текст")
        self.assertEqual(notes["version"], "21.1.5.80")

    def test_missing_release_notes_are_an_error(self) -> None:
        def get(_session, _url: str, *, accept: str):
            raise ConnectionError("404")

        with patch.object(forgejo, "_get", side_effect=get), self.assertRaises(forgejo.ForgejoReleaseError):
            forgejo.fetch_release_notes("21.1.5.80")


if __name__ == "__main__":
    unittest.main()
