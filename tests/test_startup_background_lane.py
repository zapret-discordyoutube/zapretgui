"""Фоновые задачи запуска идут по одной.

Раньше у каждой подсистемы был свой поток, и в первые полторы секунды после
появления окна одновременно работали пять-семь: они по очереди отбирали общий
замок Python друг у друга и у окна. Замер на win10, одна и та же работа в своём
потоке и на общей дорожке: список профилей 960 → 85 мс, сборка списков адресов
1097 → 26 мс, запуск обхода 1695 → 907 мс.
"""

from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from main import post_startup_threading as lane_module


class StartupBackgroundLaneTests(unittest.TestCase):
    def setUp(self) -> None:
        # Своя дорожка на тест: общую могут занимать задачи других тестов.
        patcher = patch.object(lane_module, "_LOCAL_LANE", lane_module._LocalLane())
        patcher.start()
        self.addCleanup(patcher.stop)

    def _wait_idle(self) -> None:
        done = threading.Event()
        lane_module.enqueue_subsystem_task("test", "Sentinel", done.set, warmup=True)
        self.assertTrue(done.wait(2.0))

    def test_tasks_of_different_subsystems_never_run_together(self) -> None:
        calls: list[str] = []
        first_started = threading.Event()
        release_first = threading.Event()
        profile_done = threading.Event()

        def hosts_task() -> None:
            calls.append("hosts:start")
            first_started.set()
            release_first.wait(2.0)
            calls.append("hosts:end")

        def profile_task() -> None:
            calls.append("profile")
            profile_done.set()

        lane_module.enqueue_subsystem_task("hosts", "HostsRefresh", hosts_task)
        lane_module.enqueue_subsystem_task("profile", "ProfileWarmup", profile_task)

        self.assertTrue(first_started.wait(1.0))
        # Пока hosts занята, задача другой подсистемы ждёт, а не идёт рядом.
        self.assertFalse(profile_done.wait(0.05))
        self.assertTrue(lane_module.is_local_lane_busy())

        release_first.set()
        self.assertTrue(profile_done.wait(1.0))
        self.assertEqual(calls, ["hosts:start", "hosts:end", "profile"])

    def test_tasks_keep_enqueue_order(self) -> None:
        calls: list[int] = []
        gate = threading.Event()
        lane_module.enqueue_subsystem_task("a", "Gate", lambda: gate.wait(2.0))
        for index in range(5):
            lane_module.enqueue_subsystem_task(f"q{index % 2}", f"T{index}", lambda index=index: calls.append(index))
        gate.set()
        self._wait_idle()

        self.assertEqual(calls, [0, 1, 2, 3, 4])

    def test_warmup_lets_later_ordinary_tasks_go_first(self) -> None:
        calls: list[str] = []
        gate = threading.Event()
        lane_module.enqueue_subsystem_task("a", "Gate", lambda: gate.wait(2.0))
        lane_module.enqueue_subsystem_task("profile", "StrategyUsage", lambda: calls.append("warmup"), warmup=True)
        lane_module.enqueue_subsystem_task("startup", "CoreStartup", lambda: calls.append("ordinary"))
        gate.set()
        self._wait_idle()

        self.assertEqual(calls, ["ordinary", "warmup"])

    def test_on_screen_task_goes_before_everything_queued(self) -> None:
        # Плитка «Профили» на главной странице не ждёт проверку hosts.
        calls: list[str] = []
        gate = threading.Event()
        lane_module.enqueue_subsystem_task("a", "Gate", lambda: gate.wait(2.0))
        lane_module.enqueue_subsystem_task("hosts", "HostsRefresh", lambda: calls.append("ordinary"))
        lane_module.enqueue_subsystem_task("profile", "StrategyUsage", lambda: calls.append("warmup"), warmup=True)
        lane_module.enqueue_subsystem_task("profile", "ProfileList", lambda: calls.append("on_screen"), on_screen=True)
        gate.set()
        self._wait_idle()

        self.assertEqual(calls, ["on_screen", "ordinary", "warmup"])

    def test_lane_reports_idle_after_last_task(self) -> None:
        self.assertFalse(lane_module.is_local_lane_busy())
        self._wait_idle()
        # Отметка «выполняется» снимается сразу после задачи.
        for _attempt in range(200):
            if not lane_module.is_local_lane_busy():
                break
            threading.Event().wait(0.005)
        self.assertFalse(lane_module.is_local_lane_busy())

    def test_failed_task_does_not_stop_the_lane(self) -> None:
        calls: list[str] = []

        def _fail() -> None:
            raise RuntimeError("boom")

        with patch.object(lane_module, "_log_task_failure") as log_failure:
            lane_module.enqueue_subsystem_task("a", "Broken", _fail)
            lane_module.enqueue_subsystem_task("a", "Next", lambda: calls.append("next"))
            self._wait_idle()

        self.assertEqual(calls, ["next"])
        self.assertEqual(log_failure.call_args.args[0], "Broken")

    def test_running_task_is_visible_in_thread_name(self) -> None:
        # Отчёт о рывках перечисляет занятые потоки по именам.
        names: list[str] = []
        lane_module.enqueue_subsystem_task("hosts", "HostsRefresh", lambda: names.append(threading.current_thread().name))
        self._wait_idle()

        self.assertEqual(names, ["StartupLane:HostsRefresh"])

    def test_lane_waits_while_gui_thread_builds_a_page(self) -> None:
        started = threading.Event()

        with lane_module.gui_build_turn():
            lane_module.enqueue_subsystem_task("hosts", "HostsRefresh", started.set)
            self.assertFalse(started.wait(0.1))

        self.assertTrue(started.wait(1.0))

    def test_waiting_queue_runs_beside_the_lane(self) -> None:
        # Задача, которая ждёт сеть, замок Python не держит и окну не мешает,
        # а на общей дорожке задержала бы всех на время сетевого тайм-аута.
        waiting_queue = next(iter(sorted(lane_module.WAITING_QUEUES)))
        lane_started = threading.Event()
        release_lane = threading.Event()
        network_done = threading.Event()

        lane_module.enqueue_subsystem_task("hosts", "HostsRefresh", lambda: (lane_started.set(), release_lane.wait(2.0)))
        self.assertTrue(lane_started.wait(1.0))
        thread = lane_module.enqueue_subsystem_task(waiting_queue, "NetworkTask", network_done.set)

        try:
            self.assertTrue(network_done.wait(1.0))
            self.assertEqual(thread.name, f"StartupQueue-{waiting_queue}")
        finally:
            release_lane.set()

    def test_heavy_local_subsystems_are_not_waiting_queues(self) -> None:
        for queue_name in ("profile", "presets", "hosts", "lists", "startup", "imports"):
            self.assertIn(queue_name, lane_module.LOCAL_QUEUES)
        self.assertFalse(lane_module.LOCAL_QUEUES & lane_module.WAITING_QUEUES)

    def test_every_queue_in_source_is_classified(self) -> None:
        # Автор задачи обязан решить, считает она или ждёт: задача со сном на
        # общей дорожке остановила бы все остальные, а считающая задача в
        # своём потоке снова делила бы замок Python с окном.
        import ast
        from pathlib import Path

        source_root = Path(lane_module.__file__).resolve().parents[1]
        known = lane_module.LOCAL_QUEUES | lane_module.WAITING_QUEUES
        seen: set[str] = set()
        for path in source_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "enqueue_subsystem_task" not in text:
                continue
            for node in ast.walk(ast.parse(text)):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                if name != "enqueue_subsystem_task" or not node.args:
                    continue
                queue_arg = node.args[0]
                location = f"{path.relative_to(source_root)}:{node.lineno}"
                self.assertIsInstance(queue_arg, ast.Constant, f"{location}: имя очереди должно быть строкой")
                self.assertIn(queue_arg.value, known, location)
                seen.add(queue_arg.value)

        self.assertEqual(seen, known)

    def test_no_startup_task_starts_its_own_thread_for_local_work(self) -> None:
        # start_daemon_thread остаётся только для задачи, которая ждёт чужой
        # процесс: перенос автозапуска вызывает Планировщик заданий.
        from pathlib import Path

        source_root = Path(lane_module.__file__).resolve().parents[1]
        users = sorted(
            str(path.relative_to(source_root))
            for path in source_root.rglob("*.py")
            if "start_daemon_thread(" in path.read_text(encoding="utf-8")
            and path.name != "post_startup_threading.py"
        )
        self.assertEqual(users, ["main/startup_coordinator.py"])


if __name__ == "__main__":
    unittest.main()
