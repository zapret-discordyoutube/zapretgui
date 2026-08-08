"""Pending preset switch обязан завершаться, а не зависать.

Сценарий бага: второе изменение пресета приходит, пока первый switch ещё
работает. Worker первого поколения финиширует как «устаревший», singleShot(0)
из finish-хендлера гонится с ещё живым QThread — и если проигрывает, pending
поколение раньше не обрабатывалось никогда: busy («Применяем пресет...»)
зависал навсегда.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt6.QtWidgets import QApplication


def _make_runtime_owner(**overrides):
    from settings.mode import ZAPRET2_MODE

    service = SimpleNamespace(
        snapshot=Mock(
            return_value=SimpleNamespace(
                launch_method=ZAPRET2_MODE,
                phase="running",
                running=True,
            )
        ),
        set_busy=Mock(),
    )
    owner = SimpleNamespace(
        _presets_switch_requested_generation=2,
        _presets_switch_completed_generation=1,
        _presets_switch_method=ZAPRET2_MODE,
        _presets_switch_thread=None,
        _presets_switch_worker=None,
        _presets_switch_wait_queued=False,
        _dpi_start_thread=None,
        _dpi_stop_thread=None,
        _runtime_service=lambda: service,
        runtime_service=service,
        is_running=Mock(return_value=True),
        _mark_runtime_running=Mock(),
        _process_pending_presets_switch=Mock(),
        _on_presets_switch_finished=Mock(),
        _runtime_feature=SimpleNamespace(
            dependencies=SimpleNamespace(presets_feature=object()),
            events=SimpleNamespace(publish_status=Mock()),
        ),
    )
    for key, value in overrides.items():
        setattr(owner, key, value)
    return owner


class PendingPresetSwitchProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_pending_switch_retries_when_previous_thread_still_running(self) -> None:
        from winws_runtime.runtime import restart_flow

        owner = _make_runtime_owner(
            _presets_switch_thread=SimpleNamespace(isRunning=lambda: True),
        )
        captured: list = []
        fake_qtimer = SimpleNamespace(singleShot=lambda _delay, cb: captured.append(cb))

        with patch.object(restart_flow, "QTimer", fake_qtimer):
            restart_flow.process_pending_presets_switch(owner)

        self.assertTrue(owner._presets_switch_wait_queued)
        self.assertEqual(len(captured), 1)

        # Поток умер — сработавший повтор запускает worker pending-поколения.
        owner._presets_switch_thread = None
        with patch.object(restart_flow, "start_worker_thread") as start_worker:
            captured[0]()

        self.assertFalse(owner._presets_switch_wait_queued)
        start_worker.assert_called_once()

    def test_pending_switch_retries_when_start_or_stop_pipeline_runs(self) -> None:
        from winws_runtime.runtime import restart_flow

        for busy_attr in ("_dpi_start_thread", "_dpi_stop_thread"):
            with self.subTest(busy_attr=busy_attr):
                owner = _make_runtime_owner(
                    **{busy_attr: SimpleNamespace(isRunning=lambda: True)}
                )
                captured: list = []
                fake_qtimer = SimpleNamespace(
                    singleShot=lambda _delay, cb: captured.append(cb)
                )

                with patch.object(restart_flow, "QTimer", fake_qtimer):
                    restart_flow.process_pending_presets_switch(owner)

                self.assertTrue(owner._presets_switch_wait_queued)
                self.assertEqual(len(captured), 1)

    def test_pending_switch_retry_is_not_stacked(self) -> None:
        from winws_runtime.runtime import restart_flow

        owner = _make_runtime_owner(
            _presets_switch_thread=SimpleNamespace(isRunning=lambda: True),
        )
        captured: list = []
        fake_qtimer = SimpleNamespace(singleShot=lambda _delay, cb: captured.append(cb))

        with patch.object(restart_flow, "QTimer", fake_qtimer):
            restart_flow.process_pending_presets_switch(owner)
            restart_flow.process_pending_presets_switch(owner)

        self.assertEqual(len(captured), 1)

    def test_pending_restart_retries_while_start_or_stop_thread_runs(self) -> None:
        from winws_runtime.runtime import restart_flow

        for busy_attr in ("_dpi_start_thread", "_dpi_stop_thread"):
            with self.subTest(busy_attr=busy_attr):
                owner = _make_runtime_owner(
                    _restart_request_generation=2,
                    _restart_completed_generation=1,
                    _restart_force_stop_generation=0,
                    _restart_pending_stop_generation=0,
                    _schedule_pending_restart_retry=Mock(),
                    **{busy_attr: SimpleNamespace(isRunning=lambda: True)},
                )

                restart_flow.process_pending_restart_request(owner)

                owner._schedule_pending_restart_retry.assert_called_once()

    def test_stale_success_finish_records_pid_and_reschedules_pending(self) -> None:
        from settings.mode import ZAPRET2_MODE
        from winws_runtime.runtime import restart_flow

        owner = _make_runtime_owner(
            _presets_switch_requested_generation=5,
            _presets_switch_completed_generation=3,
            _presets_switch_worker=SimpleNamespace(started_pid=256),
        )
        captured: list = []
        fake_qtimer = SimpleNamespace(singleShot=lambda _delay, cb: captured.append(cb))

        with patch.object(restart_flow, "QTimer", fake_qtimer):
            restart_flow.handle_presets_switch_finished(
                owner, True, "", 4, ZAPRET2_MODE, True
            )

        # Устаревшее поколение уже переключило процесс: PID фиксируется,
        # busy остаётся до завершения pending-поколения.
        owner._mark_runtime_running.assert_called_once_with(pid=256)
        owner.runtime_service.set_busy.assert_not_called()
        self.assertEqual(owner._presets_switch_completed_generation, 4)
        self.assertEqual(captured, [owner._process_pending_presets_switch])

    def test_stale_finish_without_spawn_does_not_touch_runtime_state(self) -> None:
        from settings.mode import ZAPRET2_MODE
        from winws_runtime.runtime import restart_flow

        owner = _make_runtime_owner(
            _presets_switch_requested_generation=5,
            _presets_switch_completed_generation=3,
            _presets_switch_worker=SimpleNamespace(started_pid=None),
        )
        fake_qtimer = SimpleNamespace(singleShot=lambda _delay, cb: None)

        with patch.object(restart_flow, "QTimer", fake_qtimer):
            restart_flow.handle_presets_switch_finished(
                owner, True, "", 4, ZAPRET2_MODE, True
            )

        owner._mark_runtime_running.assert_not_called()
        owner.runtime_service.set_busy.assert_not_called()

    def test_switch_worker_reports_pid_even_when_generation_went_stale(self) -> None:
        from settings.mode import ZAPRET2_MODE
        from winws_runtime.runtime.control_workers import PresetSwitchWorker

        launch_preset = SimpleNamespace(preset_path="preset.txt", display_name="Preset")
        presets_feature = SimpleNamespace(
            get_launch_snapshot=Mock(
                return_value=SimpleNamespace(to_launch_preset=lambda: launch_preset)
            )
        )
        runner = SimpleNamespace(
            switch_preset_file_fast=Mock(return_value=True),
            get_runner_state_snapshot=Mock(return_value=SimpleNamespace(pid=256)),
            last_error="",
        )
        # Поколение устаревает сразу ПОСЛЕ успешного switch.
        generation_checks = iter([True, True, False])
        worker = PresetSwitchWorker(
            presets_feature,
            ZAPRET2_MODE,
            4,
            lambda _generation: next(generation_checks, False),
        )
        finishes: list[tuple] = []
        worker.finished.connect(lambda *args: finishes.append(args))

        with (
            patch(
                "winws_runtime.runners.runner_factory.get_strategy_runner",
                return_value=runner,
            ),
            patch("settings.mode.exe_path_for_launch_method", return_value="winws2.exe"),
        ):
            worker.run()

        self.assertEqual(worker.started_pid, 256)
        self.assertEqual(finishes, [(True, "", 4, ZAPRET2_MODE, True)])


if __name__ == "__main__":
    unittest.main()
