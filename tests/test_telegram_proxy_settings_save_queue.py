"""Очередь сохранения настроек Telegram Proxy в фасаде и барьер запуска.

Очередь живёт в TelegramProxyFeature: основная страница, вложенная страница
и трей видят одно состояние. Пока очередь не пуста, запуск прокси ждёт —
иначе worker запуска прочитал бы из хранилища старые значения.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app.feature_facades.telegram_proxy as telegram_proxy_feature_module
from app.feature_facades.telegram_proxy import TelegramProxyFeature, TelegramProxySettingsSaveState
from telegram_proxy.ui.page import TelegramProxyPage
from telegram_proxy.ui.worker_state import TelegramProxyPageWorkerState


class _FakeSaveRuntime:
    """Запоминает запуски worker-ов сохранения вместо настоящих потоков."""

    def __init__(self) -> None:
        self.request_id = 0
        self.started: list[dict] = []
        self.running = False
        self.stopped = False
        self.cancelled = False

    def is_running(self) -> bool:
        return self.running

    def is_current(self, request_id: int, *, cleanup_in_progress: bool = False) -> bool:
        return not cleanup_in_progress and int(request_id) == self.request_id

    def start_qthread_worker(self, **kwargs):
        self.request_id += 1
        self.running = True
        self.started.append(kwargs)
        return self.request_id, None

    def stop(self, **_kwargs) -> None:
        self.stopped = True

    def cancel(self) -> None:
        self.cancelled = True
        self.request_id += 1


def _make_feature(*, manager=None, save_runtime=None, **overrides) -> TelegramProxyFeature:
    kwargs = dict(
        start_proxy_if_enabled_async=Mock(),
        get_proxy_manager=Mock(return_value=manager or SimpleNamespace(is_running=False, cleanup=Mock())),
        get_start_config=Mock(),
        set_enabled=Mock(),
        build_upstream_config=Mock(),
        load_page_initial_state=Mock(),
        save_settings_action=Mock(),
        check_relay_reachable=Mock(),
        check_relay_http=Mock(),
        check_cloudflare_connectivity=Mock(),
        get_cloudflare_dns_records_text=Mock(),
        get_cloudflare_worker_code=Mock(),
        get_fake_tls_nginx_config=Mock(),
        build_diagnostics_start_plan=Mock(),
        build_diagnostics_poll_plan=Mock(),
        build_diagnostics_finish_plan=Mock(),
        copy_text=Mock(),
        open_log_file=Mock(),
        open_external_link=Mock(),
        run_telegram_hosts_action=Mock(),
        run_diagnostics=Mock(),
        append_log_line=Mock(),
        consume_auto_deeplink_request=Mock(),
        _settings_save_state=TelegramProxySettingsSaveState(runtime=save_runtime or _FakeSaveRuntime()),
    )
    kwargs.update(overrides)
    return TelegramProxyFeature(**kwargs)


def _run_single_shot_now():
    return SimpleNamespace(singleShot=lambda _delay, callback: callback())


class _QueueDriver:
    """Шаги жизни worker-а: сохранено → поток закончился → следующий из очереди."""

    def __init__(self, feature: TelegramProxyFeature) -> None:
        self.feature = feature
        self.runtime = feature._settings_save_state.runtime

    def complete_current(self, restart: str = "") -> None:
        self.feature._on_settings_save_completed(self.runtime.request_id, "action", None, {"restart": restart})

    def fail_current(self) -> None:
        self.feature._on_settings_save_failed(self.runtime.request_id, "action", "disk full", {})

    def finish_current(self, single_shot) -> None:
        self.runtime.running = False
        with patch.object(telegram_proxy_feature_module, "QTimer", single_shot, create=True):
            self.feature._on_settings_save_worker_finished(object())


class TelegramProxySettingsSaveQueueTests(unittest.TestCase):
    def test_queue_replaces_pending_payload_for_same_action(self) -> None:
        feature = _make_feature()
        runtime = feature._settings_save_state.runtime

        feature.request_settings_save("host", host="first.local")
        feature.request_settings_save("host", host="old.local")
        feature.request_settings_save("port", port=9090)
        feature.request_settings_save("host", host="new.local")

        self.assertEqual(len(runtime.started), 1)
        pending = feature._settings_save_state.queue.pending
        self.assertEqual([(item["action"], item["host"], item["port"]) for item in pending], [
            ("port", "", 9090),
            ("host", "new.local", 0),
        ])

    def test_new_value_never_overtakes_queued_value_between_workers(self) -> None:
        feature = _make_feature()
        driver = _QueueDriver(feature)
        runtime = driver.runtime

        feature.request_settings_save("host", host="a.local")
        feature.request_settings_save("port", port=2000)
        driver.complete_current()
        # Поток первого сохранения уже не работает, но в очереди ещё ждёт порт:
        # новое сохранение должно встать в очередь, а не стартовать сразу.
        runtime.running = False
        feature.request_settings_save("port", port=3000)

        self.assertEqual(len(runtime.started), 1)
        self.assertEqual([item["port"] for item in feature._settings_save_state.queue.pending], [3000])

    def test_merged_restart_is_delivered_once_after_queue_drains(self) -> None:
        feature = _make_feature()
        driver = _QueueDriver(feature)
        listener = Mock()
        feature.add_settings_flushed_listener(listener)
        deferred = []

        def keep_single_shot(_delay, callback):
            deferred.append(callback)

        feature.request_settings_save("mtproxy_secret", value="s", restart="now")
        feature.request_settings_save("upstream_enabled", enabled=True, restart="upstream")
        feature.request_settings_save("pool_size", value=6, restart="schedule")

        driver.complete_current("now")
        listener.assert_not_called()
        driver.finish_current(SimpleNamespace(singleShot=keep_single_shot))
        # Между «сохранено» и запуском следующего worker-а очередь всё ещё занята.
        self.assertTrue(feature.has_pending_settings_saves())
        deferred.pop()()
        self.assertEqual(len(driver.runtime.started), 2)

        driver.complete_current("upstream")
        driver.finish_current(_run_single_shot_now())
        driver.complete_current("schedule")

        listener.assert_called_once_with("now")
        self.assertFalse(feature.has_pending_settings_saves())
        self.assertEqual(feature._settings_save_state.restart_pending, "")

    def test_failed_save_still_flushes_with_earlier_restart(self) -> None:
        feature = _make_feature()
        driver = _QueueDriver(feature)
        listener = Mock()
        feature.add_settings_flushed_listener(listener)

        feature.request_settings_save("dc_ip", value="4:1.2.3.4", restart="now")
        feature.request_settings_save("port", port=9000)
        driver.complete_current("now")
        driver.finish_current(_run_single_shot_now())
        with patch("log.log.log"):
            driver.fail_current()

        listener.assert_called_once_with("now")
        self.assertFalse(feature.has_pending_settings_saves())

    def test_removed_listener_is_not_called(self) -> None:
        feature = _make_feature()
        driver = _QueueDriver(feature)
        kept = Mock()
        removed = Mock()
        feature.add_settings_flushed_listener(kept)
        feature.add_settings_flushed_listener(removed)
        feature.remove_settings_flushed_listener(removed)

        feature.request_settings_save("host", host="a.local", restart="now")
        driver.complete_current("now")

        kept.assert_called_once_with("now")
        removed.assert_not_called()

    def test_stale_result_after_cleanup_is_ignored(self) -> None:
        feature = _make_feature()
        driver = _QueueDriver(feature)
        listener = Mock()
        feature.add_settings_flushed_listener(listener)
        feature.request_settings_save("host", host="a.local", restart="now")
        old_request = driver.runtime.request_id

        feature.cleanup()
        feature._on_settings_save_completed(old_request, "host", None, {"restart": "now"})

        self.assertTrue(driver.runtime.stopped)
        self.assertTrue(driver.runtime.cancelled)
        listener.assert_not_called()
        self.assertEqual(feature._settings_save_state.listeners, [])

    def test_save_worker_has_no_parent_widget(self) -> None:
        feature = _make_feature()

        feature.request_settings_save("port", port=1443, restart="now")
        worker_factory = feature._settings_save_state.runtime.started[0]["worker_factory"]
        with patch.object(TelegramProxyFeature, "create_settings_save_worker") as create_worker:
            worker_factory(1)

        kwargs = create_worker.call_args.kwargs
        self.assertIsNone(kwargs["parent"])
        self.assertEqual(kwargs["action"], "port")
        self.assertEqual(kwargs["port"], 1443)
        self.assertEqual(kwargs["context_extra"], {"restart": "now"})

    def test_tray_start_waits_for_pending_saves(self) -> None:
        start_runtime = SimpleNamespace(is_running=Mock(return_value=False), start_qthread_worker=Mock())
        stop_runtime = SimpleNamespace(is_running=Mock(return_value=False), start_qthread_worker=Mock())
        manager = SimpleNamespace(is_running=False, cleanup=Mock())
        feature = _make_feature(
            manager=manager,
            _tray_start_runtime=start_runtime,
            _tray_stop_runtime=stop_runtime,
        )
        driver = _QueueDriver(feature)

        feature.request_settings_save("port", port=1443, restart="now")
        feature.toggle_async()

        start_runtime.start_qthread_worker.assert_not_called()
        self.assertEqual(feature._tray_toggle_state.pending_count, 1)

        with patch.object(telegram_proxy_feature_module, "QTimer", _run_single_shot_now(), create=True):
            driver.complete_current("now")

        start_runtime.start_qthread_worker.assert_called_once()
        self.assertEqual(feature._tray_toggle_state.pending_count, 0)


def _page_with_feature(feature) -> TelegramProxyPage:
    page = TelegramProxyPage.__new__(TelegramProxyPage)
    page._cleanup_in_progress = False
    page._telegram_proxy = feature
    page._start_after_settings_flush = False
    page._proxy_start_runtime = SimpleNamespace(is_running=Mock(return_value=False))
    page._proxy_start_state = TelegramProxyPageWorkerState(page._proxy_start_runtime)
    page._proxy_stop_runtime = SimpleNamespace(is_running=Mock(return_value=True))
    page._proxy_stop_state = TelegramProxyPageWorkerState(page._proxy_stop_runtime)
    page._start_proxy_worker = Mock()
    return page


class TelegramProxyStartBarrierTests(unittest.TestCase):
    def test_start_waits_for_pending_port_save_and_runs_after_flush(self) -> None:
        feature = _make_feature()
        driver = _QueueDriver(feature)
        page = _page_with_feature(feature)
        feature.add_settings_flushed_listener(page._on_settings_flushed)
        page._restart_if_running = Mock()

        page._request_settings_save("port", port=1443)
        TelegramProxyPage._request_proxy_start(page)

        page._start_proxy_worker.assert_not_called()
        self.assertTrue(page._start_after_settings_flush)

        driver.complete_current("")

        page._start_proxy_worker.assert_called_once_with()
        self.assertFalse(page._start_after_settings_flush)

    def test_flush_dispatches_restart_before_deferred_start(self) -> None:
        page = _page_with_feature(SimpleNamespace(has_pending_settings_saves=Mock(return_value=False)))
        page._start_after_settings_flush = True
        calls = []
        page._restart_if_running = lambda: calls.append("restart")
        page._request_proxy_start = lambda: calls.append("start")

        TelegramProxyPage._on_settings_flushed(page, "now")

        self.assertEqual(calls, ["restart", "start"])

    def test_stop_cancels_deferred_start(self) -> None:
        page = _page_with_feature(SimpleNamespace(has_pending_settings_saves=Mock(return_value=True)))

        TelegramProxyPage._request_proxy_start(page)
        self.assertTrue(page._start_after_settings_flush)
        TelegramProxyPage._request_proxy_stop(page)

        self.assertFalse(page._start_after_settings_flush)
        page._start_after_settings_flush = False
        TelegramProxyPage._on_settings_flushed(page, "")
        page._start_proxy_worker.assert_not_called()

    def test_cancel_restart_clears_deferred_start(self) -> None:
        page = _page_with_feature(SimpleNamespace(has_pending_settings_saves=Mock(return_value=True)))
        page._start_after_settings_flush = True

        TelegramProxyPage._set_restarting_from_toggle(page, False)

        self.assertFalse(page._start_after_settings_flush)


if __name__ == "__main__":
    unittest.main()
