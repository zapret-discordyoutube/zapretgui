"""Premium-статус проходит одним путём: store → общие правила → все экраны."""

from __future__ import annotations

import inspect
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
from donater.premium_display import (  # noqa: E402
    PremiumDisplay,
    build_premium_display,
    premium_display_from_ui_state,
)
from donater.state import premium_state_from_activation_info  # noqa: E402
from donater.subscription_ui import apply_premium_state_to_store  # noqa: E402
from donater.ui.page_lifecycle import handle_premium_ui_state_changed  # noqa: E402
from donater.ui.page_plans import (  # noqa: E402
    build_premium_display_plans,
    build_reset_plan,
    build_status_check_plan,
)
from donater.ui.status_workflow import apply_reset_plan_ui, apply_status_check_success  # noqa: E402
from presets.ui.control.top_summary_plan import build_premium_summary  # noqa: E402
from settings.appearance import (  # noqa: E402
    AppearancePremiumEffectsPlan,
    effective_background_preset,
    effective_holiday_effects,
    resolve_premium_access,
)


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

    def test_store_writer_keeps_unknown_days_unknown(self) -> None:
        store = MainWindowStateStore()

        apply_premium_state_to_store(
            ui_state_store=store,
            state=premium_state_from_activation_info({"activated": True, "days_remaining": None}),
        )

        self.assertTrue(store.snapshot().subscription_is_premium)
        self.assertIsNone(store.snapshot().subscription_days_remaining)

    def test_store_writer_drops_days_for_free(self) -> None:
        store = MainWindowStateStore()

        apply_premium_state_to_store(
            ui_state_store=store,
            state=premium_state_from_activation_info({"activated": False, "days_remaining": 15}),
        )

        self.assertIsNone(store.snapshot().subscription_days_remaining)

    def test_facade_has_no_second_status_writer(self) -> None:
        # Статус в общий UI-store пишет только donater.status_runtime.
        self.assertFalse(hasattr(PremiumFeature, "apply_subscription_state_to_ui_store"))
        self.assertFalse(hasattr(PremiumFeature, "check_device_activation"))
        self.assertFalse(hasattr(PremiumFeature, "get_premium_state"))


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
        )

    def test_page_only_draws_status_check_result(self) -> None:
        # Страница не пишет статус в общий UI-store: это уже сделал владелец
        # статуса до того, как передал ей итог проверки.
        import donater.ui.status_workflow as status_workflow

        self.assertNotIn(
            "apply_subscription_state",
            inspect.signature(apply_status_check_success).parameters,
        )
        self.assertNotIn("set_subscription", inspect.getsource(status_workflow))

        for result in (None, {"found": True}, {"activated": True, "found": True, "status": "ok"}):
            with self.subTest(result=result):
                calls = Mock()
                self._run_status_check(result, calls)

                calls.set_status_badge.assert_called_once()

    def test_reset_writes_free_before_reset_message(self) -> None:
        calls = Mock()

        kind, value = apply_reset_plan_ui(
            key_input=QLineEdit(),
            set_activation_status=lambda **_kwargs: None,
            update_device_info=lambda: None,
            set_status_badge=calls.set_status_badge,
            render_days_label=lambda: None,
            set_activation_section_visible=lambda _visible: None,
            apply_local_reset=calls.apply_local_reset,
        )

        self.assertEqual([name for name, *_ in calls.mock_calls][:2], ["apply_local_reset", "set_status_badge"])
        calls.apply_local_reset.assert_called_once_with()
        self.assertEqual(calls.set_status_badge.call_args.kwargs["text_key"], build_reset_plan().badge_plan.text_key)
        self.assertEqual((kind, value), ("none", 0))


class OtherSurfacesTests(unittest.TestCase):
    def test_about_uses_plural_and_never_zero_for_unknown_days(self) -> None:
        def label(is_premium, days, language="ru"):
            return build_subscription_status_plan(
                build_premium_display(is_premium=is_premium, days_remaining=days),
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
        def summary(is_premium, days):
            return build_premium_summary(
                build_premium_display(is_premium=is_premium, days_remaining=days),
                language="ru",
            )

        self.assertEqual(summary(True, 21), ("Premium", "Осталось 21 день"))
        self.assertEqual(summary(True, 2), ("Premium", "Осталось 2 дня"))
        self.assertEqual(summary(True, None), ("Premium", "Активен"))

    def test_unknown_status_is_checking_not_free_everywhere(self) -> None:
        unknown = premium_display_from_ui_state(AppUiState(subscription_known=False))

        about = build_subscription_status_plan(
            unknown,
            language="ru",
            free_icon_color="#888",
            premium_icon_color="#ffc107",
        )
        self.assertEqual(about.label_text, "Проверка подписки...")
        self.assertEqual(about.icon_color, "#888")
        self.assertEqual(
            build_premium_summary(unknown, language="ru"),
            ("Проверка...", "Узнаём статус подписки"),
        )
        self.assertEqual(
            build_premium_summary(unknown, language="en"),
            ("Checking...", "Checking subscription status"),
        )

        known_free = premium_display_from_ui_state(AppUiState(subscription_known=True))
        self.assertEqual(build_premium_summary(known_free, language="ru")[0], "Free")


class AppearancePremiumGatingTests(unittest.TestCase):
    _effects = AppearancePremiumEffectsPlan(garland_enabled=True, snowflakes_enabled=True)

    def test_unknown_status_keeps_premium_settings(self) -> None:
        access = resolve_premium_access(subscription_known=False, is_premium=False)

        self.assertTrue(access.premium_allowed)
        self.assertFalse(access.reset_saved_premium)
        self.assertEqual(effective_background_preset("amoled", access), "amoled")
        effects = effective_holiday_effects(self._effects, animations_enabled=True, access=access)
        self.assertTrue(effects.garland_enabled)
        self.assertTrue(effects.snowflakes_enabled)

    def test_known_free_status_resets_premium_settings(self) -> None:
        access = resolve_premium_access(subscription_known=True, is_premium=False)

        self.assertFalse(access.premium_allowed)
        self.assertTrue(access.reset_saved_premium)
        self.assertEqual(effective_background_preset("amoled", access), "standard")
        self.assertEqual(effective_background_preset("rkn_chan", access), "standard")
        self.assertEqual(effective_background_preset("standard", access), "standard")
        effects = effective_holiday_effects(self._effects, animations_enabled=True, access=access)
        self.assertFalse(effects.garland_enabled)
        self.assertFalse(effects.snowflakes_enabled)

    def test_known_premium_keeps_settings(self) -> None:
        access = resolve_premium_access(subscription_known=True, is_premium=True)

        self.assertTrue(access.premium_allowed)
        self.assertFalse(access.reset_saved_premium)
        self.assertEqual(effective_background_preset("rkn_chan", access), "rkn_chan")

    def test_disabled_animations_turn_effects_off_even_for_premium(self) -> None:
        access = resolve_premium_access(subscription_known=True, is_premium=True)

        effects = effective_holiday_effects(self._effects, animations_enabled=False, access=access)

        self.assertFalse(effects.garland_enabled)
        self.assertFalse(effects.snowflakes_enabled)


if __name__ == "__main__":
    unittest.main()
