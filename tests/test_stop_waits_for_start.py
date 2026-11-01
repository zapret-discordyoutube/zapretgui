"""«Стоп» во время запуска: ждёт конца запуска, иначе остановка не нашла бы winws и запуск поднял бы его после неё."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from winws_runtime.runtime import stop_flow


def _owner(*, starting: bool):
    owner = Mock()
    owner._dpi_stop_thread = None
    owner._dpi_start_thread = Mock()
    owner._dpi_start_thread.isRunning.return_value = starting
    owner._runtime_service.return_value.snapshot.return_value = Mock(launch_method="direct_zapret2")
    return owner


class StopWaitsForStartTests(unittest.TestCase):
    def test_stop_during_start_is_retried_later_and_stops_nothing_now(self) -> None:
        owner = _owner(starting=True)
        timers: list = []

        with (
            patch.object(stop_flow, "QTimer") as q_timer,
            patch.object(stop_flow, "start_worker_thread") as start_worker,
        ):
            q_timer.singleShot.side_effect = lambda _ms, callback: timers.append(callback)
            stop_flow.stop_dpi_async(owner)

            start_worker.assert_not_called()
            owner._begin_runtime_stop.assert_not_called()
            self.assertEqual(len(timers), 1)

            # Запуск закончился — повторная попытка уже останавливает.
            owner._dpi_start_thread.isRunning.return_value = False
            timers.pop()()
            start_worker.assert_called_once()
            owner._begin_runtime_stop.assert_called_once()

    def test_stop_without_a_running_start_goes_straight_through(self) -> None:
        owner = _owner(starting=False)

        with (
            patch.object(stop_flow, "QTimer") as q_timer,
            patch.object(stop_flow, "start_worker_thread") as start_worker,
        ):
            stop_flow.stop_dpi_async(owner)

        q_timer.singleShot.assert_not_called()
        start_worker.assert_called_once()

    def test_a_start_that_never_ends_does_not_block_stop_forever(self) -> None:
        owner = _owner(starting=True)

        with (
            patch.object(stop_flow, "QTimer"),
            patch.object(stop_flow, "start_worker_thread") as start_worker,
        ):
            stop_flow.stop_dpi_async(owner, _waited_ms=stop_flow.START_WAIT_FOR_STOP_MS)

        start_worker.assert_called_once()


if __name__ == "__main__":
    unittest.main()
