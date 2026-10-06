"""Очередь тяжёлых задач GUI-потока: только в паузах пользователя."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from main import post_startup_idle_tasks as idle_tasks  # noqa: E402
from main.post_startup_idle_tasks import (  # noqa: E402
    BUSY_POLL_MS,
    HIDDEN_POLL_MS,
    IDLE_REQUIRED_MAX_MS,
    IDLE_REQUIRED_MS,
    TASK_GAP_MS,
    IdleUiTaskQueue,
)


# Именно QApplication: приложение одно на весь прогон тестов, и созданное
# здесь QCoreApplication оставило бы без виджетов тесты из других файлов.
_APP = QApplication.instance() or QApplication([])


class _World:
    """Управляемые часы и состояние пользователя для очереди."""

    def __init__(self) -> None:
        self.now = 100.0
        self.alive = True
        self.shown = True
        self.app_active = True
        self.mouse_pressed = False
        self.idle_ms: int | None = 10_000

    def build(self) -> IdleUiTaskQueue:
        queue = IdleUiTaskQueue(
            is_alive=lambda: self.alive,
            is_window_shown=lambda: self.shown,
            idle_ms=lambda: self.idle_ms,
            is_app_active=lambda: self.app_active,
            mouse_pressed=lambda: self.mouse_pressed,
            clock=lambda: self.now,
        )
        # Таймер подменён: тест сам решает, когда «сработал» тик, и видит,
        # на сколько очередь попросила себя разбудить.
        queue._timer = Mock()
        return queue

    def advance(self, ms: int) -> None:
        self.now += ms / 1000.0


def _armed_ms(queue: IdleUiTaskQueue) -> int:
    return int(queue._timer.start.call_args.args[0])


class IdleUiTaskQueueTests(unittest.TestCase):
    def test_task_runs_when_user_is_idle(self) -> None:
        world = _World()
        queue = world.build()
        ran: list[str] = []

        queue.add("page", lambda: ran.append("page"))
        queue._on_timer()

        self.assertEqual(ran, ["page"])
        self.assertEqual(queue.pending_names(), ())

    def test_task_waits_while_user_moves_mouse_or_types(self) -> None:
        world = _World()
        world.idle_ms = IDLE_REQUIRED_MS - 1
        queue = world.build()
        ran: list[str] = []

        queue.add("page", lambda: ran.append("page"))
        queue._on_timer()

        self.assertEqual(ran, [])
        self.assertEqual(queue.pending_names(), ("page",))
        self.assertEqual(_armed_ms(queue), BUSY_POLL_MS)

        world.idle_ms = IDLE_REQUIRED_MS
        queue._on_timer()
        self.assertEqual(ran, ["page"])

    def test_task_waits_while_mouse_button_is_held(self) -> None:
        world = _World()
        world.mouse_pressed = True
        queue = world.build()
        ran: list[str] = []

        queue.add("page", lambda: ran.append("page"))
        queue._on_timer()

        self.assertEqual(ran, [])
        self.assertEqual(_armed_ms(queue), BUSY_POLL_MS)

    def test_task_does_not_start_before_its_delay(self) -> None:
        world = _World()
        queue = world.build()
        ran: list[str] = []

        queue.add("page", lambda: ran.append("page"), delay_ms=1_000)
        self.assertEqual(_armed_ms(queue), 1_000)
        world.advance(400)
        queue._on_timer()

        self.assertEqual(ran, [])
        self.assertEqual(_armed_ms(queue), 600)

        world.advance(600)
        queue._on_timer()
        self.assertEqual(ran, ["page"])

    def test_tasks_run_one_per_tick_with_a_gap(self) -> None:
        world = _World()
        queue = world.build()
        ran: list[str] = []

        queue.add("first", lambda: ran.append("first"))
        queue.add("second", lambda: ran.append("second"))
        queue._on_timer()

        # Вторая задача не выполняется тем же куском: окну нужен перерыв,
        # чтобы перерисоваться и принять ввод.
        self.assertEqual(ran, ["first"])
        self.assertEqual(_armed_ms(queue), TASK_GAP_MS)

        queue._on_timer()
        self.assertEqual(ran, ["first", "second"])

    def test_tasks_run_in_order_of_their_delays(self) -> None:
        world = _World()
        queue = world.build()
        ran: list[str] = []

        queue.add("late", lambda: ran.append("late"), delay_ms=5_000)
        queue.add("early", lambda: ran.append("early"), delay_ms=1_000)
        world.advance(6_000)
        queue._on_timer()
        queue._on_timer()

        self.assertEqual(ran, ["early", "late"])

    def test_long_task_makes_queue_wait_for_a_longer_pause(self) -> None:
        world = _World()
        queue = world.build()
        ran: list[str] = []

        def slow() -> None:
            ran.append("slow")

        queue.add("slow", slow)
        queue.add("next", lambda: ran.append("next"))
        # Задача «заняла» интерфейс на 400 мс: так выглядит медленный компьютер.
        with patch.object(idle_tasks.time, "perf_counter", side_effect=[10.0, 10.4]):
            queue._on_timer()

        self.assertEqual(queue.required_idle_ms(), 1_600)
        world.idle_ms = 1_000
        queue._on_timer()
        self.assertEqual(ran, ["slow"])

        world.idle_ms = 1_600
        queue._on_timer()
        self.assertEqual(ran, ["slow", "next"])

    def test_required_pause_is_capped(self) -> None:
        world = _World()
        queue = world.build()
        queue._last_task_ms = 60_000.0

        self.assertEqual(queue.required_idle_ms(), IDLE_REQUIRED_MAX_MS)

    def test_hidden_window_holds_page_tasks(self) -> None:
        world = _World()
        world.shown = False
        queue = world.build()
        ran: list[str] = []

        queue.add("page", lambda: ran.append("page"))
        queue._on_timer()

        # Окно в трее: страницу никто не увидит, память и процессор не тратим.
        self.assertEqual(ran, [])
        self.assertEqual(_armed_ms(queue), HIDDEN_POLL_MS)

        world.shown = True
        queue._on_timer()
        self.assertEqual(ran, ["page"])

    def test_hidden_window_runs_task_that_does_not_need_it_at_once(self) -> None:
        world = _World()
        world.shown = False
        # Пользователь активен в другой программе: скрытому окну это не мешает.
        world.idle_ms = 0
        queue = world.build()
        ran: list[str] = []

        queue.add("update", lambda: ran.append("update"), needs_shown_window=False)
        queue._on_timer()

        self.assertEqual(ran, ["update"])

    def test_inactive_app_does_not_wait_for_pause(self) -> None:
        world = _World()
        world.app_active = False
        world.idle_ms = 0
        queue = world.build()
        ran: list[str] = []

        queue.add("page", lambda: ran.append("page"))
        queue._on_timer()

        self.assertEqual(ran, ["page"])

    def test_unknown_idle_time_does_not_block_tasks(self) -> None:
        world = _World()
        world.idle_ms = None
        queue = world.build()
        ran: list[str] = []

        queue.add("page", lambda: ran.append("page"))
        queue._on_timer()

        self.assertEqual(ran, ["page"])

    def test_closing_app_drops_tasks(self) -> None:
        world = _World()
        queue = world.build()
        ran: list[str] = []

        queue.add("page", lambda: ran.append("page"))
        world.alive = False
        queue._on_timer()

        self.assertEqual(ran, [])
        self.assertEqual(queue.pending_names(), ())

    def test_failing_task_does_not_stop_the_queue(self) -> None:
        world = _World()
        queue = world.build()
        ran: list[str] = []

        def broken() -> None:
            raise RuntimeError("boom")

        queue.add("broken", broken)
        queue.add("next", lambda: ran.append("next"))
        queue._on_timer()
        queue._on_timer()

        self.assertEqual(ran, ["next"])


class IdleUiTaskQueueRealTimerTests(unittest.TestCase):
    def test_real_timer_runs_task_through_event_loop(self) -> None:
        from PyQt6.QtCore import QEventLoop, QTimer

        ran: list[str] = []
        queue = IdleUiTaskQueue(
            is_alive=lambda: True,
            is_window_shown=lambda: True,
            idle_ms=lambda: 10_000,
            is_app_active=lambda: True,
            mouse_pressed=lambda: False,
        )
        loop = QEventLoop()
        queue.add("page", lambda: (ran.append("page"), loop.quit()))
        QTimer.singleShot(2_000, loop.quit)
        loop.exec()
        queue.stop()

        self.assertEqual(ran, ["page"])


if __name__ == "__main__":
    unittest.main()
