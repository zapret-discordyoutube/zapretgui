from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

from app.state_store import AppUiState
from donater.premium_display import TIER_FREE, TIER_WARNING, PremiumDisplay


class _FakePresetSwitchTimer:
    """Замена QTimer из refresh_runtime_state: копит start()/timeout-колбэки."""

    def __init__(self, parent=None) -> None:
        self.parent = parent
        self.single_shot = False
        self.start_calls: list[int] = []
        self._callbacks: list = []
        self.timeout = SimpleNamespace(connect=self._callbacks.append)

    def setSingleShot(self, value) -> None:
        self.single_shot = bool(value)

    def start(self, delay_ms) -> None:
        self.start_calls.append(int(delay_ms))

    def stop(self) -> None:
        pass

    def fire(self) -> None:
        for callback in list(self._callbacks):
            callback()


class ControlTopSummaryRefreshGuardTests(unittest.TestCase):
    def _assert_subscription_change_updates_premium_without_worker(self, page_cls) -> None:
        page = page_cls.__new__(page_cls)
        page._cleanup_in_progress = False
        page.top_summary = Mock()
        page._request_top_summary_worker = Mock(
            side_effect=AssertionError("subscription-only change must not reload preset/profile summary")
        )
        page.set_loading = Mock()
        page.update_status = Mock()
        page.update_strategy = Mock(side_effect=AssertionError("subscription-only change must not repaint strategy state"))

        page_cls._on_ui_state_changed(
            page,
            AppUiState(subscription_known=True, subscription_is_premium=True, subscription_days_remaining=14),
            frozenset({"subscription_is_premium", "subscription_days_remaining"}),
        )

        page.top_summary.set_premium.assert_called_once_with(PremiumDisplay(tier=TIER_WARNING, days=14))
        page._request_top_summary_worker.assert_not_called()

    def _assert_free_answer_repaints_premium_summary(self, page_cls) -> None:
        # Ответ «Free» меняет в store только subscription_known: is_premium и так False.
        page = page_cls.__new__(page_cls)
        page._cleanup_in_progress = False
        page.top_summary = Mock()
        page._request_top_summary_worker = Mock()
        page.set_loading = Mock()
        page.update_status = Mock()
        page.update_strategy = Mock()

        page_cls._on_ui_state_changed(
            page,
            AppUiState(subscription_known=True, subscription_is_premium=False),
            frozenset({"subscription_known"}),
        )

        page.top_summary.set_premium.assert_called_once_with(PremiumDisplay(tier=TIER_FREE))

    def test_free_answer_repaints_premium_summary_on_both_control_pages(self) -> None:
        from presets.ui.control.zapret1.page import Zapret1ModeControlPage
        from presets.ui.control.zapret2.page import Zapret2ModeControlPage

        for page_cls in (Zapret1ModeControlPage, Zapret2ModeControlPage):
            with self.subTest(page_cls=page_cls.__name__):
                self._assert_free_answer_repaints_premium_summary(page_cls)

    def _assert_subscription_change_skips_runtime_repaint(self, page_cls) -> None:
        page = page_cls.__new__(page_cls)
        page._cleanup_in_progress = False
        page.top_summary = Mock()
        page._request_top_summary_worker = Mock()
        page.set_loading = Mock(side_effect=AssertionError("subscription-only change must not repaint loading controls"))
        page.update_status = Mock(side_effect=AssertionError("subscription-only change must not repaint runtime status"))
        page.update_strategy = Mock()

        page_cls._on_ui_state_changed(
            page,
            AppUiState(subscription_is_premium=True, subscription_days_remaining=14),
            frozenset({"subscription_is_premium", "subscription_days_remaining"}),
        )

        page.set_loading.assert_not_called()
        page.update_status.assert_not_called()
        page.update_strategy.assert_not_called()

    def test_zapret1_subscription_change_skips_top_summary_worker(self) -> None:
        from presets.ui.control.zapret1.page import Zapret1ModeControlPage

        self._assert_subscription_change_updates_premium_without_worker(Zapret1ModeControlPage)

    def test_zapret2_subscription_change_skips_top_summary_worker(self) -> None:
        from presets.ui.control.zapret2.page import Zapret2ModeControlPage

        self._assert_subscription_change_updates_premium_without_worker(Zapret2ModeControlPage)

    def test_zapret1_subscription_change_skips_runtime_repaint(self) -> None:
        from presets.ui.control.zapret1.page import Zapret1ModeControlPage

        self._assert_subscription_change_skips_runtime_repaint(Zapret1ModeControlPage)

    def test_zapret2_subscription_change_skips_runtime_repaint(self) -> None:
        from presets.ui.control.zapret2.page import Zapret2ModeControlPage

        self._assert_subscription_change_skips_runtime_repaint(Zapret2ModeControlPage)

    def test_control_pages_delay_top_summary_worker_after_active_preset_switch(self) -> None:
        from presets.ui.control.refresh_runtime_state import ModeControlRefreshRuntime
        from presets.ui.control.zapret1.page import Zapret1ModeControlPage
        from presets.ui.control.zapret2.page import Zapret2ModeControlPage

        for page_cls in (Zapret1ModeControlPage, Zapret2ModeControlPage):
            with self.subTest(page_cls=page_cls.__name__):
                page = page_cls.__new__(page_cls)
                page._cleanup_in_progress = False
                page._refresh_runtime = ModeControlRefreshRuntime()
                page.isVisible = Mock(return_value=True)
                page.run_when_page_ready = Mock()
                page._request_top_summary_worker = Mock()
                page._schedule_additional_settings_reload_after_preset_switch = Mock()

                timers: list[_FakePresetSwitchTimer] = []

                def _timer_factory(parent=None):
                    timer = _FakePresetSwitchTimer(parent)
                    timers.append(timer)
                    return timer

                with patch(
                    "presets.ui.control.refresh_runtime_state.QTimer",
                    new=_timer_factory,
                ):
                    page_cls._on_ui_state_changed(
                        page,
                        AppUiState(current_strategy_summary="Профили"),
                        frozenset({"active_preset_revision"}),
                    )
                    page_cls._on_ui_state_changed(
                        page,
                        AppUiState(current_strategy_summary="Профили"),
                        frozenset({"active_preset_revision"}),
                    )

                page._request_top_summary_worker.assert_not_called()
                self.assertEqual(len(timers), 1)
                self.assertEqual(len(timers[0].start_calls), 2)

                timers[0].fire()

                page._request_top_summary_worker.assert_called_once_with()

    def test_control_pages_wait_until_preset_apply_finishes_before_top_summary_reload(self) -> None:
        from presets.ui.control.refresh_runtime_state import ModeControlRefreshRuntime
        from presets.ui.control.zapret1.page import Zapret1ModeControlPage
        from presets.ui.control.zapret2.page import Zapret2ModeControlPage

        for page_cls in (Zapret1ModeControlPage, Zapret2ModeControlPage):
            with self.subTest(page_cls=page_cls.__name__):
                runtime = ModeControlRefreshRuntime()
                page = page_cls.__new__(page_cls)
                page._cleanup_in_progress = False
                page._refresh_runtime = runtime
                page.isVisible = Mock(return_value=True)
                page.run_when_page_ready = Mock()
                page._request_top_summary_worker = Mock()
                page._schedule_additional_settings_reload_after_preset_switch = Mock()
                page.set_loading = Mock()
                page.update_status = Mock()
                page.update_strategy = Mock()
                page._refresh_last_status_message = Mock()

                timers: list[_FakePresetSwitchTimer] = []

                def _timer_factory(parent=None):
                    timer = _FakePresetSwitchTimer(parent)
                    timers.append(timer)
                    return timer

                with patch(
                    "presets.ui.control.refresh_runtime_state.QTimer",
                    new=_timer_factory,
                ):
                    page_cls._on_ui_state_changed(
                        page,
                        AppUiState(
                            current_strategy_summary="Профили",
                            launch_busy=True,
                            launch_busy_text="Применяем пресет...",
                        ),
                        frozenset({"active_preset_revision", "launch_busy", "launch_busy_text"}),
                    )

                page._request_top_summary_worker.assert_not_called()
                self.assertEqual(timers, [])
                self.assertTrue(runtime.top_summary_preset_apply_reload_state.has_pending())

                with patch(
                    "presets.ui.control.refresh_runtime_state.QTimer",
                    new=_timer_factory,
                ):
                    page_cls._on_ui_state_changed(
                        page,
                        AppUiState(current_strategy_summary="Профили", launch_busy=False),
                        frozenset({"launch_busy", "launch_busy_text"}),
                    )

                self.assertFalse(runtime.top_summary_preset_apply_reload_state.has_pending())
                self.assertEqual(len(timers), 1)
                timers[0].fire()
                page._request_top_summary_worker.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
