from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from donater.premium_display import (  # noqa: E402
    TIER_ACTIVE,
    TIER_FREE,
    TIER_UNKNOWN,
    TIER_URGENT,
    TIER_WARNING,
    PremiumDisplay,
    build_premium_display,
    days_unit,
    format_days_left,
    premium_display_from_ui_state,
)
from donater.state import normalize_days_remaining, premium_state_from_activation_info  # noqa: E402


class NormalizeDaysRemainingTests(unittest.TestCase):
    def test_unknown_and_garbage_stay_unknown_instead_of_zero(self) -> None:
        for raw in (None, "", "abc", object(), True, False):
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_days_remaining(raw))

    def test_numbers_are_ints_and_negative_is_clamped_to_zero(self) -> None:
        self.assertEqual(normalize_days_remaining(0), 0)
        self.assertEqual(normalize_days_remaining(12), 12)
        self.assertEqual(normalize_days_remaining("7"), 7)
        self.assertEqual(normalize_days_remaining(-3), 0)


class BuildPremiumDisplayTests(unittest.TestCase):
    def test_tiers_follow_single_threshold_table(self) -> None:
        cases = (
            (None, PremiumDisplay(TIER_ACTIVE, None)),
            (31, PremiumDisplay(TIER_ACTIVE, 31)),
            (30, PremiumDisplay(TIER_WARNING, 30)),
            (8, PremiumDisplay(TIER_WARNING, 8)),
            (7, PremiumDisplay(TIER_URGENT, 7)),
            (1, PremiumDisplay(TIER_URGENT, 1)),
            (0, PremiumDisplay(TIER_URGENT, 0)),
            (-5, PremiumDisplay(TIER_URGENT, 0)),
            ("garbage", PremiumDisplay(TIER_ACTIVE, None)),
        )
        for days, expected in cases:
            with self.subTest(days=days):
                self.assertEqual(build_premium_display(is_premium=True, days_remaining=days), expected)

    def test_free_ignores_days(self) -> None:
        display = build_premium_display(is_premium=False, days_remaining=40)

        self.assertEqual(display, PremiumDisplay(TIER_FREE, None))
        self.assertFalse(display.is_premium)
        self.assertTrue(display.is_known)

    def test_unknown_status_is_neither_free_nor_premium(self) -> None:
        display = build_premium_display(is_premium=True, days_remaining=40, known=False)

        self.assertEqual(display.tier, TIER_UNKNOWN)
        self.assertFalse(display.is_known)
        self.assertFalse(display.is_premium)

    def test_reads_ui_state_including_known_flag(self) -> None:
        unknown = SimpleNamespace(
            subscription_known=False,
            subscription_is_premium=False,
            subscription_days_remaining=None,
        )
        premium = SimpleNamespace(
            subscription_known=True,
            subscription_is_premium=True,
            subscription_days_remaining=12,
        )

        self.assertEqual(premium_display_from_ui_state(unknown).tier, TIER_UNKNOWN)
        self.assertEqual(premium_display_from_ui_state(premium), PremiumDisplay(TIER_WARNING, 12))


class DaysTextTests(unittest.TestCase):
    def test_russian_plural_forms(self) -> None:
        cases = {
            0: "дней",
            1: "день",
            2: "дня",
            4: "дня",
            5: "дней",
            11: "дней",
            12: "дней",
            14: "дней",
            21: "день",
            22: "дня",
            25: "дней",
            101: "день",
            111: "дней",
        }
        for days, expected in cases.items():
            with self.subTest(days=days):
                self.assertEqual(days_unit(days, language="ru"), expected)

    def test_english_plural_forms(self) -> None:
        self.assertEqual(days_unit(1, language="en"), "day")
        self.assertEqual(days_unit(0, language="en"), "days")
        self.assertEqual(days_unit(21, language="en"), "days")

    def test_days_left_text(self) -> None:
        self.assertEqual(format_days_left(1, language="ru"), "Осталось 1 день")
        self.assertEqual(format_days_left(3, language="ru"), "Осталось 3 дня")
        self.assertEqual(format_days_left(12, language="ru"), "Осталось 12 дней")
        self.assertEqual(format_days_left(1, language="en"), "1 day left")
        self.assertEqual(format_days_left(12, language="en"), "12 days left")


class PremiumStateConversionTests(unittest.TestCase):
    def test_activation_info_keeps_unknown_days_unknown(self) -> None:
        state = premium_state_from_activation_info(
            {"activated": True, "days_remaining": None, "status": "Активировано", "source": "cache"}
        )

        self.assertTrue(state.is_premium)
        self.assertIsNone(state.days_remaining)
        self.assertEqual(state.source, "cache")

    def test_activation_info_drops_days_for_free(self) -> None:
        state = premium_state_from_activation_info({"activated": False, "days_remaining": 10})

        self.assertFalse(state.is_premium)
        self.assertIsNone(state.days_remaining)
        self.assertEqual(state.subscription_level, "-")
        self.assertEqual(state.status_msg, "Не активировано")

    def test_missing_info_is_free(self) -> None:
        state = premium_state_from_activation_info(None)

        self.assertFalse(state.is_premium)
        self.assertEqual(state.source, "api")


if __name__ == "__main__":
    unittest.main()
