from __future__ import annotations

"""Проверка, установка и возврат DPI — по поведению.

Главные обещания: DPI никогда не остаётся выключенным после проверки или
неудачной установки; после успешного запуска установщика DPI обратно не
запускается и программа закрывается штатно; номер проверки у координатора
закрывается всегда.
"""

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QApplication

from app.feature_facades.updater import UpdaterFeature
from ui.page_deps.types import UpdateRuntimeActions
from updater.check.flow import CheckOutcome, run_update_check
from updater.check.service import UpdateCheckService
from updater.download import flow as download_flow
from updater.download.downloader import CancellationToken, LocalWriteError, UpdateCancelled, UpdatePipelineError
from updater.download.service import UpdateInstallService
from updater.dpi_guard import DpiGuard, DpiStopError
from updater.release.resolver import ReleaseLookup


def setUpModule() -> None:
    # Сигналы из фоновых потоков доставляет только живое приложение Qt.
    QApplication.instance() or QApplication([])


def _wait(predicate, timeout: float = 5.0) -> bool:
    app = QCoreApplication.instance()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


class _Dpi:
    """Поддельный DPI: помнит, сколько раз его останавливали и запускали."""

    def __init__(self, running: bool = True, still_running: bool = False) -> None:
        self.running = running
        self.still_running = still_running
        self.stops: list[dict] = []
        self.restarts = 0

    def shutdown_sync(self, **kwargs):
        self.stops.append(kwargs)
        self.running = self.still_running
        return SimpleNamespace(still_running=self.still_running)

    def restart(self):
        self.restarts += 1
        self.running = True
        return True

    def guard(self, **kwargs) -> DpiGuard:
        return DpiGuard(
            is_any_running=lambda: self.running,
            shutdown_sync=self.shutdown_sync,
            is_available=lambda: True,
            restart=self.restart,
            **kwargs,
        )

    def actions(self, **overrides) -> UpdateRuntimeActions:
        values = dict(
            is_any_running=lambda: self.running,
            shutdown_sync=self.shutdown_sync,
            is_available=lambda: True,
            restart=self.restart,
            mark_stopped=Mock(),
            request_exit=Mock(),
        )
        values.update(overrides)
        return UpdateRuntimeActions(**values)


class DpiGuardTests(unittest.TestCase):
    def test_not_running_dpi_is_left_alone(self) -> None:
        dpi = _Dpi(running=False)
        guard = dpi.guard()

        self.assertFalse(guard.stop(reason="test"))
        guard.restore()

        self.assertEqual((dpi.stops, dpi.restarts), ([], 0))

    def test_stopped_dpi_is_restored_exactly_once(self) -> None:
        dpi = _Dpi()
        on_stopped = Mock()
        guard = dpi.guard(on_stopped=on_stopped)

        self.assertTrue(guard.stop(reason="test", update_runtime_state=False))
        guard.restore()
        guard.restore()

        self.assertFalse(dpi.stops[0]["update_runtime_state"])
        on_stopped.assert_called_once_with()
        self.assertEqual(dpi.restarts, 1)

    def test_half_stopped_dpi_is_still_restored(self) -> None:
        dpi = _Dpi(still_running=True)
        guard = dpi.guard()

        with self.assertRaises(DpiStopError):
            guard.stop(reason="test")
        guard.restore()

        self.assertEqual(dpi.restarts, 1)

    def test_nothing_is_restarted_while_program_exits(self) -> None:
        dpi = _Dpi()
        guard = dpi.guard(allow_restore=lambda: False)

        guard.stop(reason="test")
        guard.restore()

        self.assertEqual(dpi.restarts, 0)


class CheckFlowTests(unittest.TestCase):
    def _run(self, *, lookups, online, dpi):
        rows: list[tuple[str, dict]] = []
        calls = iter(lookups)
        telegram = Mock()
        outcome = run_update_check(
            "dev",
            language="ru",
            emit_row=lambda name, status: rows.append((name, status)),
            dpi=dpi,
            lookup=lambda _channel: next(calls),
            probe=lambda **_kwargs: online,
            probe_telegram=telegram,
        )
        return outcome, telegram

    def test_found_release_does_not_touch_dpi(self) -> None:
        dpi = _Dpi()
        outcome, telegram = self._run(lookups=[ReleaseLookup({"version": "1.0.0.9"})], online=True, dpi=dpi.guard())

        self.assertEqual(outcome.release["version"], "1.0.0.9")
        self.assertEqual(dpi.stops, [])
        telegram.assert_called_once()

    def test_found_release_does_not_wait_for_a_silent_source_row(self) -> None:
        """Раньше карточка показывала «Проверка…», пока не ответит мёртвое зеркало."""
        table_released = threading.Event()
        self.addCleanup(table_released.set)

        def slow_table(**_kwargs) -> bool:
            table_released.wait(10.0)
            return True

        started = time.monotonic()
        outcome = run_update_check(
            "dev",
            language="ru",
            emit_row=lambda *_row: None,
            dpi=_Dpi().guard(),
            lookup=lambda _channel: ReleaseLookup({"version": "1.0.0.9"}),
            probe=slow_table,
            probe_telegram=Mock(),
        )

        self.assertEqual(outcome.release["version"], "1.0.0.9")
        self.assertLess(time.monotonic() - started, 2.0)

    def test_failed_lookup_waits_for_the_table_before_touching_dpi(self) -> None:
        """Повтор без DPI решается по таблице: её итог нельзя угадывать заранее."""
        dpi = _Dpi()

        def late_table(**_kwargs) -> bool:
            time.sleep(0.2)
            return True

        outcome = run_update_check(
            "dev",
            language="ru",
            emit_row=lambda *_row: None,
            dpi=dpi.guard(),
            lookup=lambda _channel: ReleaseLookup(None, "плохой выпуск"),
            probe=late_table,
            probe_telegram=Mock(),
        )

        self.assertEqual(outcome.error, "плохой выпуск")
        self.assertEqual(dpi.stops, [])

    def test_unreachable_sources_retry_once_without_dpi_and_restore_it(self) -> None:
        dpi = _Dpi()
        outcome, _ = self._run(
            lookups=[ReleaseLookup(None, "нет сети"), ReleaseLookup({"version": "1.0.0.9"})],
            online=False,
            dpi=dpi.guard(),
        )

        self.assertEqual(outcome.release["version"], "1.0.0.9")
        self.assertEqual(len(dpi.stops), 1)
        self.assertTrue(dpi.stops[0]["update_runtime_state"])
        self.assertEqual(dpi.restarts, 1)

    def test_dpi_is_restored_even_when_retry_crashes(self) -> None:
        dpi = _Dpi()
        passes = iter([False])

        def probe(**_kwargs):
            try:
                return next(passes)
            except StopIteration:
                raise RuntimeError("сбой повтора") from None

        outcome = run_update_check(
            "dev",
            language="ru",
            emit_row=lambda *_row: None,
            dpi=dpi.guard(),
            lookup=lambda _channel: ReleaseLookup(None, "нет сети"),
            probe=probe,
            probe_telegram=Mock(),
        )

        self.assertIsNone(outcome.release)
        self.assertEqual(len(dpi.stops), 1)
        self.assertEqual(dpi.restarts, 1)

    def test_no_retry_when_some_source_answers(self) -> None:
        dpi = _Dpi()
        outcome, _ = self._run(lookups=[ReleaseLookup(None, "плохой выпуск")], online=True, dpi=dpi.guard())

        self.assertEqual(outcome.error, "плохой выпуск")
        self.assertEqual(dpi.stops, [])


class InstallFlowTests(unittest.TestCase):
    HANDOFF = SimpleNamespace(installer_path="setup.exe")

    def _run(self, attempts, *, dpi: _Dpi, start_ok: bool = True, token=None):
        stages: list[str] = []
        downloaded = Mock()
        start = Mock(return_value=start_ok)
        results = iter(attempts)

        def resolve_and_download(*_args, **_kwargs):
            result = next(results)
            if isinstance(result, BaseException):
                raise result
            return result

        with patch.object(download_flow, "_resolve_and_download", side_effect=resolve_and_download):
            try:
                download_flow.run_update_install(
                    "1.0.0.9",
                    token=token or CancellationToken(),
                    dpi=dpi.guard(),
                    on_stage=stages.append,
                    on_downloaded=downloaded,
                    start_installation=start,
                )
                error = None
            except Exception as exc:
                error = exc
        return error, stages, start, downloaded

    def test_success_stops_dpi_before_installer_and_never_restores_it(self) -> None:
        dpi = _Dpi()
        error, stages, start, downloaded = self._run([self.HANDOFF], dpi=dpi)

        self.assertIsNone(error)
        start.assert_called_once_with(self.HANDOFF)
        downloaded.assert_called_once_with()
        self.assertEqual([stop["reason"] for stop in dpi.stops], ["updater_installer_handoff"])
        self.assertFalse(dpi.stops[0]["update_runtime_state"])
        self.assertEqual(dpi.restarts, 0)
        self.assertLess(stages.index("Остановка DPI перед установкой…"), stages.index("Запуск установщика…"))

    def test_network_failure_retries_once_without_dpi(self) -> None:
        dpi = _Dpi()
        error, _stages, start, _ = self._run([UpdatePipelineError("нет сети"), self.HANDOFF], dpi=dpi)

        self.assertIsNone(error)
        self.assertEqual([stop["reason"] for stop in dpi.stops], ["updater_download_connectivity"])
        start.assert_called_once()
        self.assertEqual(dpi.restarts, 0)

    def test_failure_after_stopping_dpi_restores_it(self) -> None:
        dpi = _Dpi()
        error, _stages, start, _ = self._run(
            [UpdatePipelineError("нет сети"), UpdatePipelineError("всё ещё нет сети")], dpi=dpi
        )

        self.assertIsInstance(error, UpdatePipelineError)
        start.assert_not_called()
        self.assertEqual(dpi.restarts, 1)

    def test_installer_that_does_not_start_restores_dpi(self) -> None:
        dpi = _Dpi()
        error, *_ = self._run([self.HANDOFF], dpi=dpi, start_ok=False)

        self.assertIn("установщик", str(error))
        self.assertEqual(dpi.restarts, 1)

    def test_disk_error_is_not_retried_without_dpi(self) -> None:
        dpi = _Dpi()
        error, *_ = self._run([LocalWriteError("нет места")], dpi=dpi)

        self.assertIsInstance(error, LocalWriteError)
        self.assertEqual(dpi.stops, [])

    def test_cancel_after_download_does_not_stop_dpi(self) -> None:
        dpi = _Dpi()
        token = CancellationToken()

        def cancel_on_download():
            token.cancel()

        stages: list[str] = []
        with patch.object(download_flow, "_resolve_and_download", return_value=self.HANDOFF):
            with self.assertRaises(UpdateCancelled):
                download_flow.run_update_install(
                    "1.0.0.9",
                    token=token,
                    dpi=dpi.guard(),
                    on_stage=stages.append,
                    on_downloaded=cancel_on_download,
                    start_installation=Mock(return_value=True),
                )
        # Отмена проверяется до остановки DPI: остановленный зря DPI не нужен.
        self.assertEqual(dpi.stops, [])
        self.assertEqual(dpi.restarts, 0)


class UpdateCheckServiceTests(unittest.TestCase):
    def test_result_reaches_coordinator_with_release_source(self) -> None:
        feature = UpdaterFeature()
        rows: list[str] = []

        def run_check(_channel, *, language, emit_row, dpi):
            emit_row("Forgejo API", {"status": "online"})
            return CheckOutcome({"version": "999.0.0.1", "release_notes": "новое", "source": "Forgejo"})

        service = UpdateCheckService(updater_feature=feature, runtime_actions=_Dpi().actions(), run_check=run_check)
        service.server_status.connect(lambda name, _status: rows.append(name))

        self.assertTrue(service.start())
        self.assertFalse(service.start(), "вторая проверка не должна начаться поверх первой")
        self.assertTrue(_wait(lambda: not service.is_busy))

        snapshot = feature.current_update_check_snapshot()
        self.assertEqual(snapshot.phase, "completed")
        self.assertTrue(snapshot.has_update)
        self.assertEqual(snapshot.release_source, "Forgejo")
        self.assertEqual(rows, ["Forgejo API"])

    def test_crashed_check_still_closes_coordinator_token(self) -> None:
        """Иначе навсегда «Проверка…», и стартовая проверка не начнётся."""
        feature = UpdaterFeature()

        def run_check(*_args, **_kwargs):
            raise RuntimeError("сбой")

        service = UpdateCheckService(updater_feature=feature, runtime_actions=_Dpi().actions(), run_check=run_check)
        service.start()
        self.assertTrue(_wait(lambda: not service.is_busy))

        self.assertEqual(feature.current_update_check_snapshot().phase, "error")
        self.assertIsNotNone(feature.begin_update_check(source="startup"))

    def test_shutdown_during_check_closes_token_and_ignores_late_result(self) -> None:
        feature = UpdaterFeature()
        release = Mock()

        def run_check(*_args, **_kwargs):
            release.wait()
            return CheckOutcome({"version": "999.0.0.1"})

        import threading

        gate = threading.Event()
        release.wait = lambda: gate.wait(5)
        service = UpdateCheckService(updater_feature=feature, runtime_actions=_Dpi().actions(), run_check=run_check)
        service.start()
        service.shutdown()
        gate.set()
        _wait(lambda: False, timeout=0.2)

        snapshot = feature.current_update_check_snapshot()
        self.assertEqual(snapshot.phase, "skipped")
        self.assertFalse(snapshot.has_update)

    def test_dpi_is_not_restored_after_shutdown(self) -> None:
        dpi = _Dpi()
        feature = UpdaterFeature()
        import threading

        stopped = threading.Event()
        finish = threading.Event()

        def run_check(_channel, *, language, emit_row, dpi):
            dpi.stop(reason="server_status_probe_retry")
            stopped.set()
            finish.wait(5)
            dpi.restore()
            return CheckOutcome(None, "нет сети")

        service = UpdateCheckService(updater_feature=feature, runtime_actions=dpi.actions(), run_check=run_check)
        service.start()
        self.assertTrue(stopped.wait(5))
        service.shutdown()
        finish.set()
        _wait(lambda: False, timeout=0.2)

        self.assertEqual(dpi.restarts, 0)


class UpdateInstallServiceTests(unittest.TestCase):
    def test_launched_installer_requests_exit_on_main_thread_without_stopping_dpi(self) -> None:
        dpi = _Dpi()
        actions = dpi.actions()
        launched = Mock()

        def run_install(_version, *, token, dpi, on_stage, on_progress, on_downloaded, splash=None):
            on_stage("Скачивание обновления…")
            on_downloaded()
            dpi.stop(reason="updater_installer_handoff", update_runtime_state=False)

        service = UpdateInstallService(runtime_actions=actions, run_install=run_install)
        service.launched.connect(launched)

        self.assertTrue(service.start("1.0.0.9"))
        self.assertFalse(service.start("1.0.0.9"), "повторный клик не запускает вторую установку")
        self.assertTrue(_wait(lambda: actions.request_exit.called))

        launched.assert_called_once_with()
        actions.request_exit.assert_called_once_with(stop_dpi=False, farewell=False)
        actions.mark_stopped.assert_called_once_with()

    def test_failure_is_reported_and_service_is_free_again(self) -> None:
        errors: list[str] = []

        def run_install(*_args, **_kwargs):
            raise UpdatePipelineError("Не удалось скачать обновление")

        service = UpdateInstallService(runtime_actions=_Dpi().actions(), run_install=run_install)
        service.failed.connect(errors.append)
        service.start("1.0.0.9")

        self.assertTrue(_wait(lambda: bool(errors)))
        self.assertEqual(errors, ["Не удалось скачать обновление"])
        self.assertFalse(service.is_busy)



class DpiRestoreThreadTests(unittest.TestCase):
    """DPI запускается обратно в главном потоке: там у Qt есть очередь событий."""

    def _assert_restart_on_main_thread(self, service_factory, start) -> None:
        import threading

        dpi = _Dpi()
        restart_threads: list[threading.Thread] = []

        def restart():
            restart_threads.append(threading.current_thread())
            return True

        actions = dpi.actions(restart=restart)
        service = service_factory(actions)
        start(service)

        self.assertTrue(_wait(lambda: bool(restart_threads)))
        self.assertIs(restart_threads[0], threading.main_thread())
        self.assertEqual(len(dpi.stops), 1)

    def test_failed_install_restores_dpi_on_main_thread(self) -> None:
        def run_install(_version, *, token, dpi, on_stage, on_progress, on_downloaded, splash=None):
            try:
                dpi.stop(reason="updater_download_connectivity", update_runtime_state=False)
                raise UpdatePipelineError("нет сети")
            finally:
                dpi.restore()

        self._assert_restart_on_main_thread(
            lambda actions: UpdateInstallService(runtime_actions=actions, run_install=run_install),
            lambda service: service.start("1.0.0.9"),
        )

    def test_check_retry_restores_dpi_on_main_thread(self) -> None:
        def run_check(_channel, *, language, emit_row, dpi):
            try:
                dpi.stop(reason="server_status_probe_retry")
            finally:
                dpi.restore()
            return CheckOutcome(None, "нет сети")

        self._assert_restart_on_main_thread(
            lambda actions: UpdateCheckService(
                updater_feature=UpdaterFeature(), runtime_actions=actions, run_check=run_check
            ),
            lambda service: service.start(),
        )


class AutoCheckSettingTests(unittest.TestCase):
    def test_rapid_toggles_store_the_last_value(self) -> None:
        import threading

        from updater.page_actions import AutoCheckSetting

        written: list[bool] = []
        first_write_started = threading.Event()
        release_first_write = threading.Event()

        def set_enabled(value: bool) -> None:
            if not written:
                first_write_started.set()
                release_first_write.wait(5)
            written.append(value)

        feature = SimpleNamespace(set_auto_update_enabled=set_enabled, is_auto_update_enabled=lambda: True)
        setting = AutoCheckSetting(updater_feature=feature)
        setting.save(True)
        self.assertTrue(first_write_started.wait(5))
        setting.save(False)
        setting.save(True)
        setting.save(False)
        release_first_write.set()

        self.assertTrue(_wait(lambda: len(written) >= 2 and not setting._writer_running))
        self.assertEqual(written, [True, False])

    def test_loaded_value_does_not_override_user_choice(self) -> None:
        from updater.page_actions import AutoCheckSetting

        feature = SimpleNamespace(set_auto_update_enabled=Mock(), is_auto_update_enabled=lambda: True)
        setting = AutoCheckSetting(updater_feature=feature)
        loaded: list[bool] = []
        setting.loaded.connect(loaded.append)

        setting.load()
        self.assertTrue(_wait(lambda: bool(loaded)))
        self.assertEqual(loaded, [True])
        self.assertFalse(setting.user_changed)
        setting.save(False)
        self.assertTrue(setting.user_changed)


if __name__ == "__main__":
    unittest.main()
