from __future__ import annotations

import unittest

from updater.ui import plans as update_page_plans


class UpdaterChangelogProgressPlanTests(unittest.TestCase):
    def test_download_speed_and_eta_stay_visible_between_samples(self) -> None:
        first = update_page_plans.build_changelog_progress_plan(
            percent=50,
            done_bytes=50 * 1024 * 1024,
            total_bytes=100 * 1024 * 1024,
            last_speed_time=0.0,
            last_speed_bytes=0,
            smoothed_speed=0.0,
            language="ru",
            now=2.0,
            progress_bar_visible=True,
        )

        second = update_page_plans.build_changelog_progress_plan(
            percent=52,
            done_bytes=52 * 1024 * 1024,
            total_bytes=100 * 1024 * 1024,
            last_speed_time=first.last_speed_time,
            last_speed_bytes=first.last_speed_bytes,
            smoothed_speed=first.smoothed_speed,
            download_speed_kb=first.download_speed_kb,
            download_eta_seconds=first.download_eta_seconds,
            language="ru",
            now=2.2,
            progress_bar_visible=True,
        )

        self.assertNotEqual(second.speed_label_text, "Скорость: —")
        self.assertNotEqual(second.eta_label_text, "Осталось: —")
        self.assertEqual(second.download_speed_kb, first.download_speed_kb)
        self.assertEqual(second.download_eta_seconds, first.download_eta_seconds)

    def test_silent_server_shows_waiting_instead_of_zero_speed(self) -> None:
        # Выход 21.1.7.207: сервер молчал, окно минутами показывало «0 КБ/с» и «9 мин».
        moving = update_page_plans.build_changelog_progress_plan(
            percent=85, done_bytes=1_300_000, total_bytes=1_500_000,
            last_speed_time=0.0, last_speed_bytes=1_000_000, smoothed_speed=0.0,
            previous_done_bytes=1_000_000, last_progress_time=0.0,
            language="ru", now=10.0, progress_bar_visible=True,
        )
        silent = update_page_plans.build_changelog_progress_plan(
            percent=85, done_bytes=1_300_000, total_bytes=1_500_000,
            last_speed_time=moving.last_speed_time, last_speed_bytes=moving.last_speed_bytes,
            smoothed_speed=moving.smoothed_speed, download_speed_kb=moving.download_speed_kb,
            download_eta_seconds=moving.download_eta_seconds,
            previous_done_bytes=1_300_000, last_progress_time=moving.last_progress_time,
            language="ru", now=10.0 + update_page_plans.DOWNLOAD_WAITING_SECONDS, progress_bar_visible=True,
        )

        self.assertNotEqual(moving.speed_label_text, "Ждём ответа сервера…")
        self.assertEqual(silent.speed_label_text, "Ждём ответа сервера…")
        self.assertEqual(silent.eta_label_text, "Осталось: —")
        self.assertIsNone(silent.download_eta_seconds)

    def test_a_short_pause_keeps_the_last_speed(self) -> None:
        moving = update_page_plans.build_changelog_progress_plan(
            percent=50, done_bytes=500_000, total_bytes=1_000_000,
            last_speed_time=0.0, last_speed_bytes=0, smoothed_speed=0.0,
            previous_done_bytes=0, last_progress_time=0.0,
            language="ru", now=2.0, progress_bar_visible=True,
        )
        paused = update_page_plans.build_changelog_progress_plan(
            percent=50, done_bytes=500_000, total_bytes=1_000_000,
            last_speed_time=moving.last_speed_time, last_speed_bytes=moving.last_speed_bytes,
            smoothed_speed=moving.smoothed_speed, download_speed_kb=moving.download_speed_kb,
            download_eta_seconds=moving.download_eta_seconds,
            previous_done_bytes=500_000, last_progress_time=moving.last_progress_time,
            language="ru", now=2.5, progress_bar_visible=True,
        )

        self.assertEqual(paused.speed_label_text, moving.speed_label_text)

    def test_speed_under_ten_kb_has_a_decimal_instead_of_zero(self) -> None:
        plan = update_page_plans.build_changelog_progress_plan(
            percent=10, done_bytes=400, total_bytes=1_000_000,
            last_speed_time=0.0, last_speed_bytes=0, smoothed_speed=0.0,
            previous_done_bytes=0, last_progress_time=0.0,
            language="ru", now=1.0, progress_bar_visible=True,
        )

        self.assertEqual(plan.speed_label_text, "Скорость: 0.4 КБ/с")


if __name__ == "__main__":
    unittest.main()
