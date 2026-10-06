"""Проверкой Premium-статуса владеет один объект на всё время работы программы.

Главные сценарии, ради которых он появился:

- подписка не «пропадает» после перезапуска: за сохранённым статусом сразу
  идёт проверка на сервере, а дальше она повторяется сама;
- код отправлен боту — привязка подхватывается без кнопки «Обновить статус»,
  на какой бы странице ни был пользователь и есть ли на устройстве старый токен.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.state_store import MainWindowStateStore  # noqa: E402
from donater import status_schedule  # noqa: E402
from donater.status_runtime import PremiumStatusRuntime  # noqa: E402
from donater.subscription_ui import SubscriptionUiActions  # noqa: E402


ACTIVE = {
    "found": True,
    "activated": True,
    "is_premium": True,
    "days_remaining": 40,
    "status": "Активировано",
    "subscription_level": "zapretik",
    "source": "api",
    "pairing_pending": False,
    "network_failed": False,
    "direct_window": False,
}
FREE_UNLINKED = {
    "found": False,
    "activated": False,
    "is_premium": False,
    "days_remaining": None,
    "status": "Устройство не привязано",
    "subscription_level": "–",
    "source": "api",
    "pairing_pending": False,
    "network_failed": False,
    "direct_window": False,
}


class _WorkerRuntime:
    """Подмена общего запуска фоновых задач: проверку завершает сам тест."""

    def __init__(self) -> None:
        self.request_id = 0
        self.running = False
        self.started: list[bool] = []
        self.on_finished = None
        self.stop = Mock(return_value=False)
        self._worker = None

    def is_running(self) -> bool:
        return self.running

    def is_current(self, request_id: int, *, cleanup_in_progress: bool = False) -> bool:
        return (not cleanup_in_progress) and int(request_id) == int(self.request_id)

    def start_qobject_worker(self, *, parent, worker_factory, bind_worker, on_finished):
        _ = parent
        self.request_id += 1
        self._worker = worker_factory(self.request_id)
        bind_worker(self._worker)
        self.started.append(bool(self._worker._use_cache))
        self.on_finished = on_finished
        self.running = True
        return self.request_id, self._worker, None

    def cancel(self) -> None:
        self.request_id += 1
        self.running = False


class PremiumStatusScheduleTests(unittest.TestCase):
    def test_pending_pair_code_is_polled_often(self) -> None:
        info = dict(FREE_UNLINKED, pairing_pending=True)

        self.assertEqual(
            status_schedule.next_check_delay_ms(info),
            status_schedule.PAIRING_POLL_MS,
        )
        self.assertLessEqual(status_schedule.PAIRING_POLL_MS, 5_000)

    def test_pending_pair_code_is_polled_even_when_old_token_exists(self) -> None:
        # Старый токен с неактивной подпиской раньше отключал опрос целиком.
        info = dict(FREE_UNLINKED, found=True, pairing_pending=True)

        self.assertEqual(
            status_schedule.next_check_delay_ms(info),
            status_schedule.PAIRING_POLL_MS,
        )

    def test_pairing_poll_slows_down_when_each_request_pauses_winws2(self) -> None:
        info = dict(FREE_UNLINKED, pairing_pending=True, direct_window=True)

        self.assertEqual(
            status_schedule.next_check_delay_ms(info),
            status_schedule.PAIRING_POLL_DIRECT_MS,
        )
        self.assertGreater(
            status_schedule.PAIRING_POLL_DIRECT_MS,
            status_schedule.PAIRING_POLL_MS,
        )

    def test_regular_refresh_is_far_inside_signed_cache_lifetime(self) -> None:
        week_ms = 7 * 24 * 60 * 60 * 1000

        self.assertEqual(status_schedule.next_check_delay_ms(ACTIVE), status_schedule.REFRESH_MS)
        self.assertLess(status_schedule.REFRESH_MS * 10, week_ms)

    def test_linked_inactive_device_is_rechecked_sooner(self) -> None:
        info = dict(FREE_UNLINKED, found=True)

        self.assertEqual(
            status_schedule.next_check_delay_ms(info),
            status_schedule.INACTIVE_REFRESH_MS,
        )
        self.assertLess(status_schedule.INACTIVE_REFRESH_MS, status_schedule.REFRESH_MS)

    def test_network_failures_back_off(self) -> None:
        info = dict(ACTIVE, network_failed=True, source="offline")
        steps = status_schedule.NETWORK_RETRY_STEPS_MS

        delays = [
            status_schedule.next_check_delay_ms(info, network_failures=count)
            for count in range(1, len(steps) + 3)
        ]

        self.assertEqual(delays[: len(steps)], list(steps))
        self.assertEqual(delays[-1], steps[-1])
        self.assertEqual(delays, sorted(delays))
        self.assertEqual(
            status_schedule.next_check_delay_ms(None, network_failures=1),
            steps[0],
        )

    def test_soft_refresh_is_rate_limited(self) -> None:
        self.assertTrue(status_schedule.soft_refresh_allowed(now=10.0, last_network_check_at=0.0))
        self.assertFalse(status_schedule.soft_refresh_allowed(now=100.0, last_network_check_at=90.0))
        self.assertTrue(
            status_schedule.soft_refresh_allowed(
                now=100.0 + status_schedule.SOFT_REFRESH_MIN_GAP_SEC,
                last_network_check_at=100.0,
            )
        )


class PremiumStatusRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.store = MainWindowStateStore()
        self.set_status = Mock()
        self.mark_startup_ready = Mock()
        self.runtime = PremiumStatusRuntime(
            thread_parent=None,
            ui_actions=SubscriptionUiActions(
                set_status=self.set_status,
                ui_state_store=self.store,
                mark_startup_ready=self.mark_startup_ready,
            ),
            get_premium_checker=Mock(return_value="checker"),
            check_device_activation=Mock(),
        )
        self.workers = _WorkerRuntime()
        self.runtime._worker_runtime = self.workers
        self.checked: list[dict] = []
        self.failed: list[str] = []
        self.runtime.status_checked.connect(self.checked.append)
        self.runtime.status_failed.connect(self.failed.append)

    def tearDown(self) -> None:
        self.runtime.cleanup()

    def _finish(self, payload, success: bool = True) -> None:
        """Завершает идущую проверку так, как это делает фоновый поток."""
        request_id = self.workers.request_id
        self.workers.running = False
        self.runtime._on_check_finished(request_id, payload, success)
        self.workers.on_finished(request_id, None)

    # ── подписка не пропадает после перезапуска ──────────────────────────────

    def test_startup_shows_saved_status_then_asks_server(self) -> None:
        self.runtime.start()

        self.assertEqual(self.workers.started, [True])
        self._finish(dict(FREE_UNLINKED, source="cache"))

        # Сохранённый статус показан, запуск не ждёт сеть...
        self.mark_startup_ready.assert_called_once_with("subscription_ready")
        self.assertTrue(self.store.snapshot().subscription_known)
        # ...и сразу следом назначена проверка на сервере.
        self.assertTrue(self.runtime._timer.isActive())
        self.assertEqual(
            self.runtime._timer.interval(),
            status_schedule.STARTUP_NETWORK_DELAY_MS,
        )

        self.runtime._on_timer()

        self.assertEqual(self.workers.started, [True, False])

    def test_expired_saved_status_is_restored_by_server_without_button(self) -> None:
        # Ровно жалоба: сохранённый ответ устарел, после перезапуска «Free».
        self.runtime.start()
        self._finish(dict(FREE_UNLINKED, found=True, source="cache", status="Не активировано"))
        self.assertFalse(self.store.snapshot().subscription_is_premium)

        self.runtime._on_timer()
        self._finish(ACTIVE)

        self.assertTrue(self.store.snapshot().subscription_is_premium)
        self.assertEqual(self.store.snapshot().subscription_days_remaining, 40)
        self.assertEqual(self.runtime._timer.interval(), status_schedule.REFRESH_MS)
        self.assertTrue(self.runtime._timer.isActive())

    def test_refresh_repeats_forever_without_any_page(self) -> None:
        self.runtime.start()
        self._finish(dict(ACTIVE, source="cache"))

        for _round in range(3):
            self.assertTrue(self.runtime._timer.isActive())
            self.runtime._on_timer()
            self.assertFalse(self.workers.started[-1])
            self._finish(ACTIVE)

        self.assertEqual(self.workers.started, [True, False, False, False])

    def test_startup_failure_keeps_status_unknown_and_retries(self) -> None:
        self.runtime.start()
        self._finish("база недоступна", success=False)

        self.mark_startup_ready.assert_called_once_with("subscription_init_failed")
        self.assertFalse(self.store.snapshot().subscription_known)
        self.assertEqual(self.failed, ["база недоступна"])
        self.assertTrue(self.runtime._timer.isActive())

    def test_broken_check_does_not_turn_premium_into_free(self) -> None:
        self.runtime.start()
        self._finish(dict(ACTIVE, source="cache"))
        self.runtime._on_timer()
        self._finish("сбой", success=False)

        self.assertTrue(self.store.snapshot().subscription_is_premium)
        self.assertEqual(
            self.runtime._timer.interval(),
            status_schedule.NETWORK_RETRY_STEPS_MS[0],
        )

    def test_network_failure_retries_with_backoff_and_recovers(self) -> None:
        offline = dict(ACTIVE, network_failed=True, source="offline")
        self.runtime.start()
        self._finish(dict(ACTIVE, source="cache"))

        seen = []
        for _attempt in range(3):
            self.runtime._on_timer()
            self._finish(offline)
            seen.append(self.runtime._timer.interval())
        self.assertEqual(seen, list(status_schedule.NETWORK_RETRY_STEPS_MS[:3]))

        self.runtime._on_timer()
        self._finish(ACTIVE)
        self.assertEqual(self.runtime._timer.interval(), status_schedule.REFRESH_MS)

        self.runtime._on_timer()
        self._finish(offline)
        self.assertEqual(
            self.runtime._timer.interval(),
            status_schedule.NETWORK_RETRY_STEPS_MS[0],
        )

    # ── код подхватывается без кнопки ────────────────────────────────────────

    def test_created_pair_code_is_polled_until_bot_confirms(self) -> None:
        waiting = dict(FREE_UNLINKED, pairing_pending=True, status="Ожидание привязки")
        self.runtime.start()
        self._finish(dict(FREE_UNLINKED, source="cache"))

        # Страница сообщила: код создан.
        self.runtime.request_refresh(force=True)
        self.assertEqual(self.workers.started[-1], False)

        for _poll in range(3):
            self._finish(waiting)
            self.assertEqual(self.runtime._timer.interval(), status_schedule.PAIRING_POLL_MS)
            self.assertTrue(self.runtime._timer.isActive())
            self.runtime._on_timer()

        self._finish(ACTIVE)

        self.assertTrue(self.store.snapshot().subscription_is_premium)
        self.assertEqual(self.checked[-1], ACTIVE)
        self.assertEqual(self.runtime._timer.interval(), status_schedule.REFRESH_MS)

    def test_pending_pair_code_found_at_startup_resumes_polling(self) -> None:
        # Программу перезапустили, пока код ждал подтверждения.
        self.runtime.start()
        self._finish(dict(FREE_UNLINKED, pairing_pending=True, source="cache"))
        self.runtime._on_timer()
        self._finish(dict(FREE_UNLINKED, pairing_pending=True))

        self.assertEqual(self.runtime._timer.interval(), status_schedule.PAIRING_POLL_MS)

    def test_forced_refresh_during_running_check_runs_again_after_it(self) -> None:
        self.runtime.start()

        self.runtime.request_refresh(force=True)

        self.assertEqual(self.workers.started, [True])
        with patch("donater.status_runtime.QTimer.singleShot") as single_shot:
            self._finish(dict(FREE_UNLINKED, source="cache"))
            single_shot.assert_called_once()
            self.assertEqual(single_shot.call_args.args[0], 0)
            single_shot.call_args.args[1]()

        self.assertEqual(self.workers.started, [True, False])
        self.assertFalse(self.runtime._timer.isActive())

    def test_soft_refresh_is_dropped_right_after_network_check(self) -> None:
        self.runtime.start()
        self._finish(dict(ACTIVE, source="cache"))
        self.runtime._on_timer()
        self._finish(ACTIVE)

        self.runtime.request_refresh()

        self.assertEqual(self.workers.started, [True, False])

        self.runtime._last_network_check_at = (
            time.monotonic() - status_schedule.SOFT_REFRESH_MIN_GAP_SEC - 1
        )
        self.runtime.request_refresh()

        self.assertEqual(self.workers.started, [True, False, False])

    # ── запись статуса и сброс ───────────────────────────────────────────────

    def test_store_is_written_only_when_status_changes(self) -> None:
        seen: list[frozenset[str]] = []
        self.store.subscribe(
            lambda _state, changed: seen.append(changed),
            fields={"subscription_known", "subscription_is_premium", "subscription_days_remaining"},
        )
        with patch("donater.status_runtime.apply_premium_state_to_store") as write:
            self.runtime.start()
            self._finish(dict(ACTIVE, source="cache"))
            for _round in range(3):
                self.runtime._on_timer()
                self._finish(ACTIVE)
            self.assertEqual(write.call_count, 1)

            self.runtime._on_timer()
            self._finish(dict(ACTIVE, days_remaining=39))
            self.assertEqual(write.call_count, 2)

        # Страница получает итог каждой проверки, даже если статус тот же.
        self.assertEqual(len(self.checked), 5)

    def test_local_reset_makes_device_free_and_drops_stale_result(self) -> None:
        self.runtime.start()
        self._finish(dict(ACTIVE, source="cache"))
        self.runtime._on_timer()

        # Проверка ещё идёт, а пользователь уже сбросил привязку.
        self.runtime.apply_local_reset()
        self.assertFalse(self.store.snapshot().subscription_is_premium)

        self._finish(ACTIVE)

        self.assertFalse(self.store.snapshot().subscription_is_premium)
        self.assertEqual(len(self.checked), 1)
        self.assertTrue(self.runtime._timer.isActive())

    def test_cleanup_stops_schedule_and_ignores_late_result(self) -> None:
        self.runtime.start()
        request_id = self.workers.request_id

        self.runtime.cleanup()
        self.runtime._on_check_finished(request_id, ACTIVE, True)

        self.assertFalse(self.runtime._timer.isActive())
        self.assertFalse(self.store.snapshot().subscription_known)
        self.workers.stop.assert_called_once()
        self.assertFalse(self.workers.stop.call_args.kwargs["blocking"])
        self.runtime.request_refresh(force=True)
        self.assertEqual(self.workers.started, [True])


class _Api:
    uses_direct_window = False

    def __init__(self) -> None:
        self.confirmed = False
        self.finish_calls = 0
        self.check_calls = 0
        self.activated = True

    @staticmethod
    def _response(payload, nonce):
        return {"success": True, "signed": payload, "kid": "test", "sig": "test", "_http_status": 200}, nonce

    def post_pair_start(self, *, request_id, device_id, device_name=None):
        _ = device_name
        return self._response(
            {
                "type": "zapret_pairing_started",
                "request_id": request_id,
                "device_id": device_id,
                "pairing_id": "pairing-1",
                "pair_code": "ABCDEFGH",
                "pair_expires_at": int(time.time()) + 600,
            },
            "start-nonce",
        )

    def post_pair_finish(self, *, request_id, device_id, pairing_id):
        _ = request_id, pairing_id
        self.finish_calls += 1
        if not self.confirmed:
            return (
                {
                    "success": False,
                    "error": {"code": "pairing_not_confirmed", "retryable": True},
                    "_http_status": 409,
                },
                "finish-nonce",
            )
        return self._response(
            {
                "type": "zapret_premium_activation",
                "device_id": device_id,
                "binding_id": "binding-2",
                "binding_generation": 2,
                "device_token": "fresh-secret-token",
                "linked": True,
                "activated": True,
                "subscription_level": "zapretik",
                "expires_at": "2099-01-01T00:00:00+00:00",
                "expires_at_epoch": 4070908800,
                "days_remaining": 100,
                "valid_until": int(time.time()) + 3600,
                "message": "Активировано",
            },
            "finish-nonce",
        )

    def post_check(self, *, request_id, device_id, device_token):
        _ = request_id
        self.check_calls += 1
        activated = self.activated and device_token == "fresh-secret-token"
        return self._response(
            {
                "type": "zapret_premium_status",
                "device_id": device_id,
                "linked": True,
                "activated": activated,
                "subscription_level": "zapretik" if activated else None,
                "expires_at": "2099-01-01T00:00:00+00:00" if activated else None,
                "expires_at_epoch": 4070908800 if activated else None,
                "days_remaining": 100 if activated else 0,
                "valid_until": int(time.time()) + 3600,
                "message": "Активировано" if activated else "Подписка не активна",
            },
            "status-nonce",
        )


class PremiumPairingStatusFlagsTests(unittest.TestCase):
    """Сервис сообщает владельцу статуса, ждать ли ещё подтверждения кода."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        from settings import store

        self.store = store
        self.old_root = store.MAIN_DIRECTORY
        store.close_settings_database()
        store.MAIN_DIRECTORY = self.tmp.name

    def tearDown(self) -> None:
        self.store.close_settings_database()
        self.store.MAIN_DIRECTORY = self.old_root
        self.tmp.cleanup()

    @staticmethod
    def _verify(raw, *, expected_device_id, expected_nonce=None, **_kwargs):
        _ = expected_nonce
        signed = raw.get("signed") if isinstance(raw, dict) else None
        if not isinstance(signed, dict):
            return None
        if str(signed.get("device_id")) != str(expected_device_id):
            return None
        return signed

    def _service(self):
        from donater.service import PremiumService

        service = PremiumService(api_base_url="https://premium.test/api")
        service._api = _Api()
        return service

    def test_pair_code_waits_then_binds_without_manual_refresh(self) -> None:
        service = self._service()
        with patch("donater.service.verify_signed_response", side_effect=self._verify):
            self.assertTrue(service.pair_start()[0])

            waiting = service.check_device_activation()
            self.assertTrue(waiting["pairing_pending"])
            self.assertFalse(waiting["activated"])
            self.assertFalse(waiting["network_failed"])

            # Тот же ответ даёт и проход без сети — так опрос возобновляется
            # после перезапуска программы.
            self.assertTrue(service.check_device_activation(use_cache=True)["pairing_pending"])

            service._api.confirmed = True
            bound = service.check_device_activation()

        self.assertTrue(bound["activated"])
        self.assertTrue(bound["found"])
        self.assertFalse(bound["pairing_pending"])

    def test_new_pair_code_is_finished_even_with_old_inactive_token(self) -> None:
        # Случай со снимка: токен устройства есть, подписка «не активна»,
        # пользователь создаёт новый код и отправляет его боту.
        from donater.storage import PremiumStorage

        service = self._service()
        device_id = PremiumStorage.get_device_id()
        with patch("donater.service.verify_signed_response", side_effect=self._verify):
            self.assertTrue(
                PremiumStorage.store_after_pairing(
                    device_id=device_id,
                    binding_id="binding-1",
                    binding_generation=1,
                    device_token="old-secret-token",
                    signed_payload={"device_id": device_id, "activated": False},
                    kid="test",
                    sig="test",
                )
            )
            self.assertTrue(service.pair_start()[0])

            waiting = service.check_device_activation()
            self.assertTrue(waiting["found"])
            self.assertFalse(waiting["activated"])
            self.assertTrue(waiting["pairing_pending"])
            self.assertEqual(service._api.finish_calls, 1)

            service._api.confirmed = True
            bound = service.check_device_activation()

        self.assertTrue(bound["activated"])
        self.assertFalse(bound["pairing_pending"])
        self.assertEqual(PremiumStorage.get_binding()["binding_id"], "binding-2")

    def test_expired_pair_code_stops_waiting(self) -> None:
        from donater.storage import PremiumStorage

        service = self._service()
        with patch("donater.service.verify_signed_response", side_effect=self._verify):
            self.assertTrue(service.pair_start()[0])
            with patch("donater.service.time.time", return_value=time.time() + 3600):
                expired = service.check_device_activation()

        self.assertFalse(expired["pairing_pending"])
        self.assertIsNone(PremiumStorage.get_pending_pairing())
        self.assertEqual(service._api.finish_calls, 0)

    def test_network_failure_is_reported_for_backoff(self) -> None:
        service = self._service()
        service._api.post_pair_finish = Mock(
            return_value=(
                {
                    "success": False,
                    "error": {"code": "connect_timeout", "retryable": True},
                    "_http_status": 0,
                },
                "finish-nonce",
            )
        )
        with patch("donater.service.verify_signed_response", side_effect=self._verify):
            self.assertTrue(service.pair_start()[0])
            result = service.check_device_activation()

        self.assertTrue(result["network_failed"])
        self.assertTrue(result["pairing_pending"])
        self.assertEqual(
            status_schedule.next_check_delay_ms(result),
            status_schedule.PAIRING_NETWORK_RETRY_MS,
        )


class PremiumPageWaitingForBotTests(unittest.TestCase):
    """Пока код ждёт подтверждения, страница не прячет и не стирает его."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _apply(self, result, *, code: str):
        from PyQt6.QtWidgets import QLineEdit

        from donater.ui.status_workflow import apply_status_check_success

        key_input = QLineEdit()
        key_input.setText(code)
        calls = SimpleNamespace(
            activation_status=Mock(),
            section_visible=Mock(),
        )
        apply_status_check_success(
            result,
            tr=lambda _key, default, **kwargs: default.format(**kwargs) if kwargs else default,
            refresh_btn=SimpleNamespace(set_loading=lambda _value: None),
            key_input=key_input,
            update_device_info=lambda: None,
            set_status_badge=lambda **_kwargs: None,
            set_activation_status=calls.activation_status,
            set_activation_section_visible=calls.section_visible,
        )
        return key_input, calls

    def test_waiting_result_keeps_code_visible_for_linked_device(self) -> None:
        key_input, calls = self._apply(
            dict(FREE_UNLINKED, found=True, pairing_pending=True),
            code="ABCD12EF",
        )

        self.assertEqual(key_input.text(), "ABCD12EF")
        calls.section_visible.assert_called_once_with(True)
        calls.activation_status.assert_not_called()

    def test_confirmed_code_is_cleared_and_section_hidden(self) -> None:
        key_input, calls = self._apply(ACTIVE, code="ABCD12EF")

        self.assertEqual(key_input.text(), "")
        calls.section_visible.assert_called_once_with(False)
        self.assertEqual(
            calls.activation_status.call_args.kwargs["text_key"],
            "page.premium.activation.success.linked_active",
        )

    def test_dead_code_is_cleared_for_unlinked_device(self) -> None:
        key_input, calls = self._apply(
            dict(FREE_UNLINKED, status="Код истёк. Создайте новый код."),
            code="ABCD12EF",
        )

        self.assertEqual(key_input.text(), "")
        calls.section_visible.assert_called_once_with(True)
        calls.activation_status.assert_called_once_with(text="")


if __name__ == "__main__":
    unittest.main()
