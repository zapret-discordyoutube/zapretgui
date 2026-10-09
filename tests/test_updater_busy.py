from __future__ import annotations

"""Автообновление не трогает программу, пока человек занят делом."""

import threading
import unittest
from unittest.mock import patch

from core.runtime import long_tasks
from updater import busy


class LongTaskTests(unittest.TestCase):
    def test_task_is_listed_only_while_it_runs(self) -> None:
        self.assertEqual(long_tasks.active_long_tasks(), ())

        with long_tasks.long_task("blockcheck"):
            self.assertEqual(long_tasks.active_long_tasks(), ("blockcheck",))

        self.assertEqual(long_tasks.active_long_tasks(), ())

    def test_mark_is_removed_even_when_the_task_crashes(self) -> None:
        with self.assertRaises(RuntimeError):
            with long_tasks.long_task("strategy_scan"):
                raise RuntimeError("сбой")

        self.assertEqual(long_tasks.active_long_tasks(), ())

    def test_two_tasks_of_one_kind_do_not_clear_each_other(self) -> None:
        first = long_tasks.begin_long_task("blockcheck")
        second = long_tasks.begin_long_task("blockcheck")

        long_tasks.end_long_task(first)
        long_tasks.end_long_task(first)  # повторное снятие безвредно
        self.assertEqual(long_tasks.active_long_tasks(), ("blockcheck",))

        long_tasks.end_long_task(second)
        self.assertEqual(long_tasks.active_long_tasks(), ())

    def test_mark_set_in_another_thread_is_seen(self) -> None:
        started, release = threading.Event(), threading.Event()

        def work() -> None:
            with long_tasks.long_task("blockcheck"):
                started.set()
                release.wait(5)

        thread = threading.Thread(target=work)
        thread.start()
        started.wait(5)
        try:
            self.assertEqual(busy.busy_reason(), "blockcheck")
        finally:
            release.set()
            thread.join(5)


class BusyReasonTests(unittest.TestCase):
    def test_free_person_can_be_updated(self) -> None:
        with patch.object(busy, "_notification_state", return_value=5):
            self.assertEqual(busy.busy_reason(), "")

    def test_fullscreen_game_video_and_presentation_mean_busy(self) -> None:
        for state in (2, 3, 4):
            with patch.object(busy, "_notification_state", return_value=state):
                self.assertEqual(busy.busy_reason(), busy.BUSY_FULLSCREEN, state)

    def test_locked_screen_and_quiet_hours_do_not_delay_the_update(self) -> None:
        for state in (0, 1, 6, 7):
            with patch.object(busy, "_notification_state", return_value=state):
                self.assertEqual(busy.busy_reason(), "", state)

    def test_running_check_matters_more_than_the_screen(self) -> None:
        with long_tasks.long_task("strategy_scan"), patch.object(busy, "_notification_state", return_value=3):
            self.assertEqual(busy.busy_reason(), "strategy_scan")


if __name__ == "__main__":
    unittest.main()
