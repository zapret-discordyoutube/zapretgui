from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.page_names import PageName
from main.post_startup_secondary_page_warmup import (
    SECONDARY_PAGE_WARMUP_PLAN,
    install_secondary_page_warmup,
)


def _startup_host(ensure_page=None) -> SimpleNamespace:
    return SimpleNamespace(
        startup_interactive_ready=SimpleNamespace(connect=Mock()),
        startup_state=SimpleNamespace(interactive_logged=True),
        ensure_page=ensure_page or Mock(return_value=object()),
        is_alive=Mock(return_value=True),
    )


def _immediate_startup_gate():
    """Гейт старта планирует запуск через QTimer, которого в тестах нет."""
    return patch(
        "main.post_startup_gate.QTimer.singleShot",
        side_effect=lambda _delay, callback: callback(),
    )


class SecondaryPageWarmupTests(unittest.TestCase):
    """Страницы греются в паузе, чтобы первый клик не строил их в GUI-потоке."""

    def test_plan_covers_pages_that_stuttered_on_first_click(self) -> None:
        pages = {page for page, _delay in SECONDARY_PAGE_WARMUP_PLAN}

        self.assertIn(PageName.ZAPRET2_USER_PRESETS, pages)
        self.assertIn(PageName.APPEARANCE, pages)
        self.assertIn(PageName.PREMIUM, pages)

    def test_pages_are_spread_out_in_time(self) -> None:
        delays = [delay for _page, delay in SECONDARY_PAGE_WARMUP_PLAN]

        self.assertEqual(delays, sorted(delays))
        self.assertEqual(len(set(delays)), len(delays))
        # Не пересекаемся с прогревом Telegram Proxy (3000 мс).
        self.assertTrue(all(delay > 3_000 for delay in delays))

    def test_each_page_is_scheduled_once(self) -> None:
        scheduled: list[tuple[int, object]] = []
        host = _startup_host()

        with patch(
            "main.post_startup_secondary_page_warmup.schedule_after",
            side_effect=lambda delay, callback: scheduled.append((delay, callback)),
        ), _immediate_startup_gate():
            install_secondary_page_warmup(host, log_startup_metric=Mock())

        self.assertEqual(len(scheduled), len(SECONDARY_PAGE_WARMUP_PLAN))

        for _delay, callback in scheduled:
            callback()

        warmed = [call.args[0] for call in host.ensure_page.call_args_list]
        self.assertEqual(warmed, [page for page, _delay in SECONDARY_PAGE_WARMUP_PLAN])

    def test_failing_page_does_not_break_the_rest(self) -> None:
        scheduled: list[object] = []
        host = _startup_host(ensure_page=Mock(side_effect=RuntimeError("сломалась")))

        with patch(
            "main.post_startup_secondary_page_warmup.schedule_after",
            side_effect=lambda delay, callback: scheduled.append(callback),
        ), _immediate_startup_gate():
            install_secondary_page_warmup(host, log_startup_metric=Mock())

        for callback in scheduled:
            callback()

        self.assertEqual(host.ensure_page.call_count, len(SECONDARY_PAGE_WARMUP_PLAN))

    def test_dead_host_stops_warmup(self) -> None:
        scheduled: list[object] = []
        host = _startup_host()

        with patch(
            "main.post_startup_secondary_page_warmup.schedule_after",
            side_effect=lambda delay, callback: scheduled.append(callback),
        ), patch(
            "main.post_startup_secondary_page_warmup.is_startup_host_alive",
            return_value=False,
        ), _immediate_startup_gate():
            install_secondary_page_warmup(host, log_startup_metric=Mock())
            for callback in scheduled:
                callback()

        host.ensure_page.assert_not_called()


if __name__ == "__main__":
    unittest.main()
