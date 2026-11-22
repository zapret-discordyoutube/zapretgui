"""Шлюз проб: без кнопки и без беды программа не устраивает пачек соединений."""

from __future__ import annotations

import threading
import unittest

from utils import probe_gate
from utils.probe_gate import BackgroundKind, Why, may_search, try_background, user_run

KIND = BackgroundKind("test_kind", 6 * 3600.0)
# Долгая проверка: «сделано» пишется по окончании, оборванная повторяется через полчаса.
LONG_KIND = BackgroundKind("test_long", 24 * 3600.0, retry_after_s=1800.0)


class _Clock:
    def __init__(self, kind: BackgroundKind = KIND) -> None:
        self.now = 1_000_000.0
        self.stored: dict[str, float] = {}
        self.kind = kind

    def ask(self) -> probe_gate.BackgroundPermit | None:
        return try_background(
            self.kind,
            clock=lambda: self.now,
            last_run=lambda key: self.stored.get(key, 0.0),
            store_last_run=self.stored.__setitem__,
        )


class ProbeGateTests(unittest.TestCase):
    def test_search_only_by_button_or_trouble(self) -> None:
        self.assertTrue(may_search(Why.USER))
        self.assertTrue(may_search(Why.SIGNAL))
        self.assertFalse(may_search(Why.SCHEDULE))

    def test_scheduled_check_runs_once_per_interval_even_across_restarts(self) -> None:
        clock = _Clock()
        permit = clock.ask()
        self.assertIsNotNone(permit)
        permit.release()
        # Время записано сразу: перезапуск через час (то же хранилище) проверку не повторяет.
        self.assertEqual(clock.stored, {"test_kind": clock.now})
        clock.now += 3600
        self.assertIsNone(clock.ask())
        clock.now += 6 * 3600
        again = clock.ask()
        self.assertIsNotNone(again)
        again.release()

    def test_long_check_cut_short_is_retried_in_half_an_hour_not_in_a_day(self) -> None:
        # Так было на деле: запуск взял разрешение, умер через 7 секунд, а следующий получил отказ на сутки.
        clock = _Clock(LONG_KIND)
        permit = clock.ask()
        self.assertIsNotNone(permit)
        permit.release()  # проверка оборвалась: complete() не вызывали
        self.assertEqual(clock.stored, {"test_long.attempt": clock.now})
        clock.now += 7
        self.assertIsNone(clock.ask(), "сразу после попытки повторять нельзя: это пачка запросов при каждом запуске")
        clock.now += 1800
        again = clock.ask()
        self.assertIsNotNone(again, "через полчаса оборванную проверку нужно повторить")
        again.release()

    def test_long_check_that_finished_waits_a_whole_interval(self) -> None:
        clock = _Clock(LONG_KIND)
        permit = clock.ask()
        self.assertIsNotNone(permit)
        with permit:
            permit.complete()
            permit.complete()  # повторный вызов безвреден
        self.assertEqual(clock.stored["test_long"], clock.now)
        clock.now += 3600
        self.assertIsNone(clock.ask())
        clock.now += 24 * 3600
        again = clock.ask()
        self.assertIsNotNone(again)
        again.release()

    def test_complete_of_a_quick_check_changes_nothing(self) -> None:
        clock = _Clock()
        permit = clock.ask()
        self.assertIsNotNone(permit)
        before = dict(clock.stored)
        clock.now += 50
        permit.complete()
        permit.release()
        self.assertEqual(clock.stored, before)

    def test_provider_addresses_kind_is_a_long_check_with_its_own_key(self) -> None:
        kind = probe_gate.PROVIDER_ADDRESSES
        self.assertIsNotNone(kind.retry_after_s)
        self.assertNotEqual(kind.key, probe_gate.ZAPRET_DNS_ADDRESS.key)

    def test_no_background_check_while_a_button_run_is_going(self) -> None:
        clock = _Clock()
        with user_run("blockcheck"):
            self.assertTrue(probe_gate.user_run_active())
            self.assertIsNone(clock.ask())
        self.assertFalse(probe_gate.user_run_active())
        self.assertEqual(clock.stored, {})
        permit = clock.ask()
        self.assertIsNotNone(permit)
        permit.release()

    def test_only_one_background_check_at_a_time(self) -> None:
        first, second = _Clock(), _Clock()
        held = first.ask()
        self.assertIsNotNone(held)
        try:
            self.assertIsNone(second.ask())
        finally:
            held.release()
        later = second.ask()
        self.assertIsNotNone(later)
        later.release()

    def test_nested_and_parallel_button_runs_are_counted(self) -> None:
        started = threading.Event()
        finish = threading.Event()

        def other_run() -> None:
            with user_run("scan"):
                started.set()
                finish.wait(2)

        worker = threading.Thread(target=other_run)
        worker.start()
        started.wait(2)
        with user_run("scan"):
            pass
        self.assertTrue(probe_gate.user_run_active())
        finish.set()
        worker.join(2)
        self.assertFalse(probe_gate.user_run_active())


if __name__ == "__main__":
    unittest.main()
