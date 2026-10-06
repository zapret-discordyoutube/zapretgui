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


class _RecordingIdleTasks:
    """Очередь пауз в тесте только записывает задачи; запускает их сам тест."""

    def __init__(self) -> None:
        self.tasks: list[tuple[str, object, int, bool]] = []

    def add(self, name, callback, *, delay_ms=0, needs_shown_window=True) -> None:
        self.tasks.append((name, callback, int(delay_ms), bool(needs_shown_window)))

    def run_all(self) -> None:
        for _name, callback, _delay, _needs_window in self.tasks:
            callback()


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

    def test_each_page_goes_through_idle_queue_once(self) -> None:
        idle_tasks = _RecordingIdleTasks()
        host = _startup_host()

        with _immediate_startup_gate():
            install_secondary_page_warmup(host, log_startup_metric=Mock(), idle_tasks=idle_tasks)

        # Сборка страницы занимает GUI-поток, поэтому не идёт по голому
        # таймеру: её время выбирает очередь пауз пользователя.
        host.ensure_page.assert_not_called()
        self.assertEqual(
            [(delay, needs_window) for _name, _cb, delay, needs_window in idle_tasks.tasks],
            [(delay, True) for _page, delay in SECONDARY_PAGE_WARMUP_PLAN],
        )

        idle_tasks.run_all()

        warmed = [call.args[0] for call in host.ensure_page.call_args_list]
        self.assertEqual(warmed, [page for page, _delay in SECONDARY_PAGE_WARMUP_PLAN])

    def test_failing_page_does_not_break_the_rest(self) -> None:
        idle_tasks = _RecordingIdleTasks()
        host = _startup_host(ensure_page=Mock(side_effect=RuntimeError("сломалась")))

        with _immediate_startup_gate():
            install_secondary_page_warmup(host, log_startup_metric=Mock(), idle_tasks=idle_tasks)

        idle_tasks.run_all()

        self.assertEqual(host.ensure_page.call_count, len(SECONDARY_PAGE_WARMUP_PLAN))

    def test_dead_host_stops_warmup(self) -> None:
        idle_tasks = _RecordingIdleTasks()
        host = _startup_host()

        with patch(
            "main.post_startup_secondary_page_warmup.is_startup_host_alive",
            return_value=False,
        ), _immediate_startup_gate():
            install_secondary_page_warmup(host, log_startup_metric=Mock(), idle_tasks=idle_tasks)
            idle_tasks.run_all()

        host.ensure_page.assert_not_called()

    def test_host_closed_after_queueing_skips_page(self) -> None:
        idle_tasks = _RecordingIdleTasks()
        host = _startup_host()

        with _immediate_startup_gate():
            install_secondary_page_warmup(host, log_startup_metric=Mock(), idle_tasks=idle_tasks)

        host.is_alive.return_value = False
        idle_tasks.run_all()

        host.ensure_page.assert_not_called()


if __name__ == "__main__":
    unittest.main()
