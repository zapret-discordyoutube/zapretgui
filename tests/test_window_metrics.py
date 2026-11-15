from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))


class WindowMetricsTests(unittest.TestCase):
    def test_minimum_shrinks_to_a_small_scaled_screen(self) -> None:
        import config.window_metrics as window_metrics

        # 1280x800 при масштабе 150 %: рабочая область около 853x500.
        self.assertEqual(window_metrics.fit_size_to_screen(900, 580, 853, 500), (853, 500))

    def test_minimum_is_kept_when_the_screen_is_big_enough(self) -> None:
        import config.window_metrics as window_metrics

        self.assertEqual(window_metrics.fit_size_to_screen(900, 580, 1920, 1040), (900, 580))

    def test_window_is_never_squeezed_below_the_floor(self) -> None:
        import config.window_metrics as window_metrics

        self.assertEqual(
            window_metrics.fit_size_to_screen(900, 580, 400, 300),
            (window_metrics.FLOOR_WIDTH, window_metrics.FLOOR_HEIGHT),
        )

    def test_unknown_screen_changes_nothing(self) -> None:
        import config.window_metrics as window_metrics

        self.assertEqual(window_metrics.fit_size_to_screen(900, 580, 0, 0), (900, 580))

    def test_minimum_window_width_keeps_fluent_layout_usable(self) -> None:
        import config.window_metrics as window_metrics

        self.assertGreaterEqual(window_metrics.MIN_WIDTH, 900)

    def test_full_hd_screen_uses_1280_by_720_default_size(self) -> None:
        import config.window_metrics as window_metrics

        with (
            patch.object(window_metrics, "_get_screen_dpi", return_value=120),
            patch.object(window_metrics, "_get_primary_screen_size", return_value=(1920, 1080)),
        ):
            self.assertEqual(window_metrics.get_scaled_window_size(), (1280, 720))

    def test_2k_screen_uses_1920_by_1080_default_size(self) -> None:
        import config.window_metrics as window_metrics

        with (
            patch.object(window_metrics, "_get_screen_dpi", return_value=96),
            patch.object(window_metrics, "_get_primary_screen_size", return_value=(2560, 1440)),
        ):
            self.assertEqual(window_metrics.get_scaled_window_size(), (1920, 1080))

    def test_large_non_special_screen_keeps_regular_default_size(self) -> None:
        import config.window_metrics as window_metrics

        with (
            patch.object(window_metrics, "_get_screen_dpi", return_value=96),
            patch.object(window_metrics, "_get_primary_screen_size", return_value=(3840, 2160)),
        ):
            self.assertEqual(
                window_metrics.get_scaled_window_size(),
                (window_metrics.BASE_WIDTH, window_metrics.BASE_HEIGHT),
            )


if __name__ == "__main__":
    unittest.main()
