"""Очередь задач прогона: предел одновременных и никаких потоков на ожидание."""

from __future__ import annotations

import threading
import unittest

from diagnostics.run_context import Run


class LaneTests(unittest.TestCase):
    def test_no_more_than_the_limit_run_at_once_and_waiting_takes_no_thread(self) -> None:
        run = Run(None, workers=50)
        self.addCleanup(run.close)
        lane = run.lane(3)
        lock = threading.Lock()
        now, peak = [0], [0]
        release = threading.Event()

        def task(value: int) -> int:
            with lock:
                now[0] += 1
                peak[0] = max(peak[0], now[0])
            release.wait(5)
            with lock:
                now[0] -= 1
            return value * 2

        before = threading.active_count()
        futures = [lane.submit(task, value) for value in range(20)]
        # Двадцать задач в очереди, а потоков поднято только на три идущие.
        self.assertLessEqual(threading.active_count() - before, 3)
        release.set()

        self.assertEqual([future.result(timeout=5) for future in futures], [value * 2 for value in range(20)])
        self.assertEqual(peak[0], 3)

    def test_an_error_in_a_task_reaches_its_future_and_frees_the_place(self) -> None:
        run = Run(None, workers=10)
        self.addCleanup(run.close)
        lane = run.lane(1)

        def broken() -> None:
            raise ValueError("сломалось")

        first = lane.submit(broken)
        second = lane.submit(lambda: "дальше")

        with self.assertRaises(ValueError):
            first.result(timeout=5)
        self.assertEqual(second.result(timeout=5), "дальше")

    def test_closing_the_run_cancels_what_is_still_waiting(self) -> None:
        run = Run(None, workers=10)
        lane = run.lane(1)
        release = threading.Event()
        running = lane.submit(release.wait, 5)
        waiting = lane.submit(lambda: "не дойдёт")

        run.close()
        release.set()

        self.assertTrue(waiting.cancelled())
        self.assertTrue(lane.submit(lambda: 1).cancelled())
        running.result(timeout=5)


if __name__ == "__main__":
    unittest.main()


class StagesTests(unittest.TestCase):
    def test_stages_run_one_after_another_and_report_start_and_end(self) -> None:
        from diagnostics.run_context import Steps

        run = Run(None, workers=10)
        self.addCleanup(run.close)
        log: list[str] = []
        lock = threading.Lock()

        def task(name: str) -> str:
            with lock:
                log.append(f"работа {name}")
            return name.upper()

        first = run.later("a", task, "a1")
        twin = run.later("a", task, "a2")
        second = run.later("b", task, "b1")
        broken = run.later("b", lambda: 1 / 0)

        def note(stage: str, done: bool) -> None:
            with lock:
                log.append(f"{'конец' if done else 'начало'} {stage}")

        # Пока шаги не запущены, отложенное не выполняется.
        self.assertEqual(log, [])
        run.start_stages(("a", "b", "пустой"), note)

        self.assertEqual((first.result(5), twin.result(5), second.result(5)), ("A1", "A2", "B1"))
        with self.assertRaises(ZeroDivisionError):
            broken.result(5)
        deadline = threading.Event()
        while "конец пустой" not in log and not deadline.wait(0.01):
            pass
        # Шаг «b» начинается только после конца шага «a»; задачи одного шага идут между его отметками.
        self.assertEqual(log[0], "начало a")
        self.assertEqual(sorted(log[1:3]), ["работа a1", "работа a2"])
        self.assertEqual(log[3:5], ["конец a", "начало b"])
        self.assertEqual(log[-3:], ["конец b", "начало пустой", "конец пустой"])

        steps = Steps(None)
        steps("sites", 0, 5)
        steps("sites", 5, 5)
        self.assertEqual(list(steps.seconds()), ["sites"])
        self.assertIn("сайты", steps.line({"sites": "сайты"}))
