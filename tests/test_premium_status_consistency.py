"""Premium-статус проходит одним путём: store → общие правила → все экраны."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from PyQt6.QtWidgets import QApplication, QLineEdit  # noqa: E402

from about.plans import build_subscription_status_plan  # noqa: E402
from app.feature_facades.premium import PremiumFeature  # noqa: E402
from app.state_store import AppUiState, MainWindowStateStore  # noqa: E402
from donater.premium_display import PremiumDisplay, build_premium_display  # noqa: E402
from donater.ui.page_lifecycle import handle_premium_ui_state_changed  # noqa: E402
from donater.ui.page_plans import (  # noqa: E402
    build_premium_display_plans,
    build_reset_plan,
    build_status_check_plan,
)
from donater.ui.status_workflow import apply_reset_plan_ui, apply_status_check_success  # noqa: E402
from presets.ui.control.top_summary_plan import build_premium_summary  # noqa: E402
from settings.appearance import AppearancePremiumEffectsPlan, build_premium_status_plan  # noqa: E402


def _tr(_key, default, **kwargs):
    return default.format(**kwargs) if kwargs else default


class SubscriptionStoreTests(unittest.TestCase):
    def test_status_is_unknown_until_first_write(self) -> None:
        store = MainWindowStateStore()

        self.assertFalse(store.snapshot().subscription_known)

        store.set_subscription(False)

        self.assertTrue(store.snapshot().subscription_known)
        self.assertFalse(store.snapshot().subscription_is_premium)

    def test_first_free_result_notifies_known_subscribers(self) -> None:
        store = MainWindowStateStore()
        seen: list[frozenset[str]] = []
        store.subscribe(lambda _state, changed: seen.append(changed), fields={"subscription_known"})

        store.set_subscription(False)

        self.assertEqual(seen, [frozenset({"subscription_known"})])

    def test_facade_keeps_unknown_days_unknown(self) -> None:
        store = MainWindowStateStore()
        feature = PremiumFeature(_ui_state_store=store)

        feature.apply_subscription_state_to_ui_store(is_premium=True, days_remaining=None)

        self.assertTrue(store.snapshot().subscription_is_premium)
        self.assertIsNone(store.snapshot().subscription_days_remaining)

    def test_facade_drops_days_for_free(self) -> None:
        store = MainWindowStateStore()
        feature = PremiumFeature(_ui_state_store=store)

        feature.apply_subscription_state_to_ui_store(is_premium=False, days_remaining=15)

        self.assertIsNone(store.snapshot().subscription_days_remaining)


class PremiumPagePlanTests(unittest.TestCase):
    def test_display_plans_follow_shared_tiers(self) -> None:
        cases = (
            (40, "active", "normal", "page.premium.status.active.title"),
            (20, "warning", "warning", "page.premium.status.active.title"),
            # ≤7 дней — жёлтое предупреждение и срочная подпись, не красный крест.
            (3, "warning", "urgent", "page.premium.status.expiring_soon.title"),
            (0, "warning", "urgent", "page.premium.status.expiring_soon.title"),
        )
        for days, status, days_kind, title_key in cases:
            with self.subTest(days=days):
                badge, days_plan = build_premium_display_plans(
                    build_premium_display(is_premium=True, days_remaining=days)
                )
                self.assertEqual(badge.status, status)
                self.assertEqual(badge.text_key, title_key)
                self.assertEqual(badge.details_key, "common.premium.days_left")
                self.assertEqual(badge.details_kwargs, {"days": days})
                self.assertEqual((days_plan.kind, days_plan.value), (days_kind, days))

    def test_unknown_days_never_become_zero(self) -> None:
        badge, days_plan = build_premium_display_plans(build_premium_display(is_premium=True, days_remaining=None))

        self.assertEqual(badge.status, "active")
        self.assertIsNone(badge.details_key)
        self.assertEqual(days_plan.kind, "none")

    def test_free_is_neutral(self) -> None:
        badge, days_plan = build_premium_display_plans(build_premium_display(is_premium=False, days_remaining=None))

        self.assertEqual(badge.status, "neutral")
        self.assertEqual(badge.text_key, "page.premium.status.inactive.title")
        self.assertEqual(days_plan.kind, "none")

    def test_status_check_matches_snapshot_rules(self) -> None:
        plan = build_status_check_plan(
            {"activated": True, "found": True, "days_remaining": 5, "status": "Активировано"},
            linked_hint="linked",
            unlinked_hint="unlinked",
        )
        snapshot_badge, snapshot_days = build_premium_display_plans(
            build_premium_display(is_premium=True, days_remaining=5)
        )

        self.assertEqual((plan.is_premium, plan.days_remaining), (True, 5))
        self.assertEqual(plan.badge_plan, snapshot_badge)
        self.assertEqual(plan.days_plan, snapshot_days)

    def test_status_check_with_unknown_days_keeps_none(self) -> None:
        plan = build_status_check_plan(
            {"activated": True, "found": True, "days_remaining": None, "status": "Активировано"},
            linked_hint="linked",
            unlinked_hint="unlinked",
        )

        self.assertIsNone(plan.days_remaining)
        self.assertEqual(plan.badge_plan.details_default, "Активировано")
        self.assertEqual(plan.days_plan.kind, "none")

    def test_free_status_check_shows_hint(self) -> None:
        plan = build_status_check_plan(
            {"activated": False, "found": False, "status": ""},
            linked_hint="linked",
            unlinked_hint="unlinked",
        )

        self.assertEqual(plan.badge_plan.status, "neutral")
        self.assertEqual(plan.badge_plan.details_default, "unlinked")

    def test_page_skips_snapshot_until_status_known(self) -> None:
        apply_snapshot = Mock()

        handle_premium_ui_state_changed(state=AppUiState(), apply_subscription_snapshot_fn=apply_snapshot)
        apply_snapshot.assert_not_called()

        handle_premium_ui_state_changed(
            state=AppUiState(subscription_known=True, subscription_is_premium=True, subscription_days_remaining=9),
            apply_subscription_snapshot_fn=apply_snapshot,
        )
        apply_snapshot.assert_called_once_with(PremiumDisplay(tier="warning", days=9))


class PremiumPageWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _run_status_check(self, result, calls: Mock):
        return apply_status_check_success(
            result,
            tr=_tr,
            refresh_btn=SimpleNamespace(set_loading=lambda _value: None),
            key_input=QLineEdit(),
            update_device_info=lambda: None,
            set_status_badge=calls.set_status_badge,
            set_activation_status=lambda **_kwargs: None,
            set_activation_section_visible=lambda _visible: None,
            stop_autopoll=lambda: None,
            sync_autopoll=lambda: None,
            apply_subscription_state=calls.apply_subscription_state,
        )

    def test_invalid_server_reply_is_not_written_as_free(self) -> None:
        for result in (None, {"found": True}):
            with self.subTest(result=result):
                calls = Mock()
                self._run_status_check(result, calls)

                calls.apply_subscription_state.assert_not_called()
                calls.set_status_badge.assert_called_once()

    def test_valid_reply_updates_store_before_status_card(self) -> None:
        calls = Mock()

        self._run_status_check({"activated": True, "found": True, "days_remaining": None, "status": "ok"}, calls)

        self.assertEqual(
            [name for name, *_ in calls.mock_calls],
            ["apply_subscription_state", "set_status_badge"],
        )
        calls.apply_subscription_state.assert_called_once_with(True, None)

    def test_reset_writes_free_before_reset_message(self) -> None:
        calls = Mock()

        kind, value = apply_reset_plan_ui(
            key_input=QLineEdit(),
            set_activation_status=lambda **_kwargs: None,
            update_device_info=lambda: None,
            set_status_badge=calls.set_status_badge,
            render_days_label=lambda: None,
            set_activation_section_visible=lambda _visible: None,
            stop_autopoll=lambda: None,
            apply_subscription_state=calls.apply_subscription_state,
        )

        self.assertEqual([name for name, *_ in calls.mock_calls][:2], ["apply_subscription_state", "set_status_badge"])
        calls.apply_subscription_state.assert_called_once_with(False, None)
        self.assertEqual(calls.set_status_badge.call_args.kwargs["text_key"], build_reset_plan().badge_plan.text_key)
        self.assertEqual((kind, value), ("none", 0))


class OtherSurfacesTests(unittest.TestCase):
    def test_about_uses_plural_and_never_zero_for_unknown_days(self) -> None:
        def label(is_premium, days, language="ru"):
            return build_subscription_status_plan(
                is_premium=is_premium,
                days=days,
                language=language,
                free_icon_color="#888",
                premium_icon_color="#ffc107",
            ).label_text

        self.assertEqual(label(True, 1), "Premium (осталось 1 день)")
        self.assertEqual(label(True, 3), "Premium (осталось 3 дня)")
        self.assertEqual(label(True, 5), "Premium (осталось 5 дней)")
        self.assertEqual(label(True, 1, "en"), "Premium (1 day left)")
        self.assertEqual(label(True, None), "Premium активен")
        self.assertEqual(label(True, -4), "Premium (осталось 0 дней)")
        self.assertEqual(label(False, 10), "Free версия")

    def test_control_summary_uses_plural(self) -> None:
        self.assertEqual(build_premium_summary(True, 21, language="ru"), ("Premium", "Осталось 21 день"))
        self.assertEqual(build_premium_summary(True, 2, language="ru"), ("Premium", "Осталось 2 дня"))
        self.assertEqual(build_premium_summary(True, None, language="ru"), ("Premium", "Активен"))


class AppearancePremiumGatingTests(unittest.TestCase):
    _effects = AppearancePremiumEffectsPlan(garland_enabled=True, snowflakes_enabled=True)

    def test_unknown_status_keeps_premium_settings(self) -> None:
        plan = build_premium_status_plan(
            is_premium=False,
            status_known=False,
            current_preset="amoled",
            was_garland_enabled=True,
            was_snowflakes_enabled=True,
            premium_effects=self._effects,
        )

        self.assertIsNone(plan.effective_preset)
        self.assertFalse(plan.disable_garland)
        self.assertFalse(plan.disable_snowflakes)
        self.assertTrue(plan.garland_checked)
        self.assertTrue(plan.snowflakes_checked)

    def test_known_free_status_resets_premium_settings(self) -> None:
        plan = build_premium_status_plan(
            is_premium=False,
            status_known=True,
            current_preset="amoled",
            was_garland_enabled=True,
            was_snowflakes_enabled=True,
            premium_effects=self._effects,
        )

        self.assertEqual(plan.effective_preset, "standard")
        self.assertTrue(plan.disable_garland)
        self.assertTrue(plan.disable_snowflakes)

    def test_known_premium_keeps_settings(self) -> None:
        plan = build_premium_status_plan(
            is_premium=True,
            status_known=True,
            current_preset="amoled",
            was_garland_enabled=True,
            was_snowflakes_enabled=True,
            premium_effects=self._effects,
        )

        self.assertIsNone(plan.effective_preset)
        self.assertFalse(plan.disable_garland)


if __name__ == "__main__":
    unittest.main()
