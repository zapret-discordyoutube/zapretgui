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


if __name__ == "__main__":
    unittest.main()
