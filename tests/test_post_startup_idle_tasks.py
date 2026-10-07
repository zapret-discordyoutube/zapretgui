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
    BACKGROUND_WAIT_MAX_MS,
    SPECULATIVE_IDLE_MS,
    SPECULATIVE_POLL_MS,
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
        self.background_busy = False

    def build(self) -> IdleUiTaskQueue:
        queue = IdleUiTaskQueue(
            is_alive=lambda: self.alive,
            is_window_shown=lambda: self.shown,
            idle_ms=lambda: self.idle_ms,
            is_background_busy=lambda: self.background_busy,
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


class IdleUiTaskQueueBackgroundTests(unittest.TestCase):
    """Страница не собирается, пока рядом работает фоновая задача.

    Замер на win10: сборка страницы настройки профиля — 72 мс при молчащем
    фоне, 113 мс рядом с одним занятым фоновым потоком и 254 мс рядом с тремя;
    окно на это время замирает.
    """

    def test_task_waits_while_background_is_busy(self) -> None:
        world = _World()
        world.background_busy = True
        queue = world.build()
        ran: list[str] = []

        queue.add("page", lambda: ran.append("page"))
        queue._on_timer()

        self.assertEqual(ran, [])
        self.assertEqual(queue.pending_names(), ("page",))
        self.assertEqual(_armed_ms(queue), BUSY_POLL_MS)

        world.background_busy = False
        world.advance(BUSY_POLL_MS)
        queue._on_timer()

        self.assertEqual(ran, ["page"])

    def test_hidden_window_also_waits_for_background(self) -> None:
        # В трее рывок не виден, но сборка рядом с фоновой задачей растянула
        # бы сам запуск: обе работы шли бы в разы дольше.
        world = _World()
        world.shown = False
        world.background_busy = True
        queue = world.build()
        ran: list[str] = []

        queue.add("update-dialog", lambda: ran.append("x"), needs_shown_window=False)
        queue._on_timer()

        self.assertEqual(ran, [])
        self.assertEqual(_armed_ms(queue), BUSY_POLL_MS)

    def test_stuck_background_does_not_hide_task_forever(self) -> None:
        # Через очередь идут окна «Что нового» и обновления.
        world = _World()
        world.background_busy = True
        queue = world.build()
        ran: list[str] = []

        queue.add("whats-new", lambda: ran.append("x"))
        queue._on_timer()
        self.assertEqual(ran, [])

        world.advance(BACKGROUND_WAIT_MAX_MS)
        queue._on_timer()

        self.assertEqual(ran, ["x"])

    def test_background_lane_does_not_start_task_while_page_is_built(self) -> None:
        from main import post_startup_threading as threading_module

        world = _World()
        queue = world.build()
        seen: list[bool] = []

        queue.add("page", lambda: seen.append(threading_module._GUI_BUILD_DONE.is_set()))
        queue._on_timer()

        self.assertEqual(seen, [False])
        self.assertTrue(threading_module._GUI_BUILD_DONE.is_set())

    def test_failed_page_build_releases_background_lane(self) -> None:
        from main import post_startup_threading as threading_module

        world = _World()
        queue = world.build()

        def _fail() -> None:
            raise RuntimeError("boom")

        queue.add("page", _fail)
        queue._on_timer()

        self.assertTrue(threading_module._GUI_BUILD_DONE.is_set())

    def test_default_queue_watches_lane_and_launch_transition(self) -> None:
        launch_busy = [False]
        host = type("Host", (), {"is_alive": lambda self: True, "is_window_shown": lambda self: True})()

        with patch.object(idle_tasks, "is_local_lane_busy", return_value=False) as lane_busy:
            queue = idle_tasks.build_idle_ui_task_queue(host, is_launch_busy=lambda: launch_busy[0])
            self.addCleanup(queue.stop)
            self.assertFalse(queue._is_background_busy())
            launch_busy[0] = True
            self.assertTrue(queue._is_background_busy())
            launch_busy[0] = False
            lane_busy.return_value = True
            self.assertTrue(queue._is_background_busy())


class SpeculativeTaskTests(unittest.TestCase):
    """Страница про запас собирается, только когда заминку окна некому заметить.

    Замер на win10: шесть сборок в первые восемь секунд после запуска давали
    шесть рывков анимации по 46–122 мс, хотя человек ничего не нажимал.
    """

    def test_watching_user_does_not_get_a_stall(self) -> None:
        world = _World()
        world.idle_ms = 5_000  # не трогает мышь, но смотрит на окно
        queue = world.build()
        ran: list[str] = []

        queue.add("page", lambda: ran.append("page"), speculative=True)
        queue._on_timer()

        self.assertEqual(ran, [])
        self.assertEqual(_armed_ms(queue), SPECULATIVE_POLL_MS)

    def test_page_is_built_when_user_left(self) -> None:
        world = _World()
        world.idle_ms = SPECULATIVE_IDLE_MS
        queue = world.build()
        ran: list[str] = []

        queue.add("page", lambda: ran.append("page"), speculative=True)
        queue._on_timer()

        self.assertEqual(ran, ["page"])

    def test_page_is_built_while_user_works_in_another_program(self) -> None:
        world = _World()
        world.idle_ms = 0
        world.app_active = False
        queue = world.build()
        ran: list[str] = []

        queue.add("page", lambda: ran.append("page"), speculative=True)
        queue._on_timer()

        self.assertEqual(ran, ["page"])

    def test_hidden_window_still_builds_no_pages(self) -> None:
        world = _World()
        world.shown = False
        queue = world.build()
        ran: list[str] = []

        queue.add("page", lambda: ran.append("page"), speculative=True)
        queue._on_timer()

        self.assertEqual(ran, [])

    def test_waiting_speculative_task_does_not_hold_ordinary_one(self) -> None:
        # Окно «Что нового» стоит в очереди позже страниц про запас.
        world = _World()
        world.idle_ms = 5_000
        queue = world.build()
        ran: list[str] = []

        queue.add("page-1", lambda: ran.append("page-1"), speculative=True)
        queue.add("page-2", lambda: ran.append("page-2"), speculative=True)
        queue.add("whats-new", lambda: ran.append("whats-new"))
        queue._on_timer()

        self.assertEqual(ran, ["whats-new"])
        self.assertEqual(queue.pending_names(), ("page-1", "page-2"))

    def test_ordinary_tasks_keep_their_order(self) -> None:
        world = _World()
        queue = world.build()
        ran: list[str] = []

        queue.add("first", lambda: ran.append("first"))
        queue.add("second", lambda: ran.append("second"))
        queue._on_timer()
        world.advance(TASK_GAP_MS)
        queue._on_timer()

        self.assertEqual(ran, ["first", "second"])

    def test_not_ready_task_is_not_started_early(self) -> None:
        world = _World()
        world.idle_ms = 5_000
        queue = world.build()
        ran: list[str] = []

        queue.add("page", lambda: ran.append("page"), speculative=True)
        queue.add("later", lambda: ran.append("later"), delay_ms=400)
        queue._on_timer()

        self.assertEqual(ran, [])
        # Просыпаемся к ближайшему событию: готовности второй задачи.
        self.assertEqual(_armed_ms(queue), 400)

    def test_default_threshold_means_user_is_away(self) -> None:
        self.assertGreaterEqual(SPECULATIVE_IDLE_MS, 10_000)


class LaunchTransitionProbeTests(unittest.TestCase):
    """Пока обход запускается или останавливается, страницы не собираются."""

    def test_probe_follows_launch_phase(self) -> None:
        from types import SimpleNamespace

        from main import post_startup

        state = SimpleNamespace(launch_phase="autostart_pending")
        probe = post_startup._launch_transition_probe(SimpleNamespace(snapshot=lambda: state))

        for phase, busy in (
            ("autostart_pending", True),
            ("starting", True),
            ("stopping", True),
            ("running", False),
            ("stopped", False),
            ("", False),
            (None, False),
        ):
            with self.subTest(phase=phase):
                state.launch_phase = phase
                self.assertEqual(probe(), busy)

    def test_no_store_means_no_probe(self) -> None:
        from main import post_startup

        self.assertIsNone(post_startup._launch_transition_probe(None))


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
