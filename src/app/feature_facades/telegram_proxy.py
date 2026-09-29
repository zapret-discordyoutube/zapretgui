from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from PyQt6.QtCore import QTimer

from ui.one_shot_worker_runtime import OneShotWorkerRuntime
from ui.queued_worker_state import QueuedWorkerState


@dataclass(slots=True)
class TelegramProxyTrayToggleState:
    pending_count: int = 0
    start_scheduled: bool = False


@dataclass(slots=True)
class TelegramProxySettingsSaveState:
    """Общая очередь сохранения настроек Telegram Proxy.

    Сохранения идут строго по одному в фоновом потоке. Когда очередь
    опустела, слушатели получают одно итоговое действие применения
    (перезапуск или горячая замена внешнего прокси).
    """

    runtime: OneShotWorkerRuntime = field(default_factory=OneShotWorkerRuntime)
    queue: QueuedWorkerState | None = None
    in_flight: bool = False
    restart_pending: str = ""
    listeners: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.queue is None:
            self.queue = QueuedWorkerState(self.runtime)


@dataclass(frozen=True, slots=True)
class TelegramProxyFeature:
    start_proxy_if_enabled_async: Callable
    get_proxy_manager: Callable
    get_start_config: Callable
    set_enabled: Callable
    build_upstream_config: Callable
    load_page_initial_state: Callable
    save_settings_action: Callable
    check_relay_reachable: Callable
    check_relay_http: Callable
    check_cloudflare_connectivity: Callable
    get_cloudflare_dns_records_text: Callable
    get_cloudflare_worker_code: Callable
    get_fake_tls_nginx_config: Callable
    build_diagnostics_start_plan: Callable
    build_diagnostics_poll_plan: Callable
    build_diagnostics_finish_plan: Callable
    copy_text: Callable
    open_log_file: Callable
    open_external_link: Callable
    run_telegram_hosts_action: Callable
    run_diagnostics: Callable
    append_log_line: Callable
    consume_auto_deeplink_request: Callable
    _tray_start_runtime: OneShotWorkerRuntime = field(default_factory=OneShotWorkerRuntime)
    _tray_stop_runtime: OneShotWorkerRuntime = field(default_factory=OneShotWorkerRuntime)
    _tray_toggle_state: TelegramProxyTrayToggleState = field(default_factory=TelegramProxyTrayToggleState)
    _settings_save_state: TelegramProxySettingsSaveState = field(default_factory=TelegramProxySettingsSaveState)

    def is_running(self) -> bool:
        try:
            return bool(self.get_proxy_manager().is_running)
        except Exception:
            return False

    def status_label(self) -> str:
        try:
            manager = self.get_proxy_manager()
            if manager.is_running:
                return f"Telegram Proxy: вкл ({manager.port})"
        except Exception:
            pass
        return "Telegram Proxy: выкл"

    def connect_status_changed(self, callback) -> None:
        try:
            self.get_proxy_manager().status_changed.connect(callback)
        except Exception:
            pass

    def cleanup(self) -> None:
        self._tray_toggle_state.pending_count = 0
        self._tray_toggle_state.start_scheduled = False
        self._tray_start_runtime.stop(blocking=False, warning_prefix="Telegram Proxy tray start worker")
        self._tray_stop_runtime.stop(blocking=False, warning_prefix="Telegram Proxy tray stop worker")
        save_state = self._settings_save_state
        save_state.runtime.stop(blocking=False, warning_prefix="Telegram Proxy settings save worker")
        save_state.runtime.cancel()
        save_state.queue.reset()
        save_state.in_flight = False
        save_state.restart_pending = ""
        save_state.listeners.clear()
        try:
            self.get_proxy_manager().cleanup()
        except Exception:
            pass

    def create_start_worker(self, *, manager, parent=None):
        from telegram_proxy.runtime.workers import TelegramProxyStartWorker

        return TelegramProxyStartWorker(
            manager=manager,
            load_start_config=self.get_start_config,
            parent=parent,
        )

    def create_upstream_apply_worker(self, *, manager, parent=None):
        from telegram_proxy.runtime.workers import TelegramProxyUpstreamApplyWorker

        return TelegramProxyUpstreamApplyWorker(
            manager=manager,
            build_upstream_config=self.build_upstream_config,
            parent=parent,
        )

    def create_stop_runtime_worker(
        self,
        *,
        manager,
        emit_status: bool = False,
        set_enabled=None,
        enabled_after_stop=None,
        parent=None,
    ):
        from telegram_proxy.runtime.workers import TelegramProxyStopRuntimeWorker

        return TelegramProxyStopRuntimeWorker(
            manager=manager,
            emit_status=bool(emit_status),
            set_enabled=set_enabled,
            enabled_after_stop=enabled_after_stop,
            parent=parent,
        )

    def create_open_log_file_worker(self, *, path: str, parent=None):
        from telegram_proxy.runtime.workers import TelegramProxyOpenLogFileWorker

        return TelegramProxyOpenLogFileWorker(
            open_log_file_fn=self.open_log_file,
            path=str(path or ""),
            parent=parent,
        )

    def create_external_link_worker(self, *, url: str, success_log: str, error_prefix: str, parent=None):
        from telegram_proxy.runtime.workers import TelegramProxyExternalLinkWorker

        return TelegramProxyExternalLinkWorker(
            open_external_link_fn=self.open_external_link,
            url=str(url or ""),
            success_log=str(success_log or ""),
            error_prefix=str(error_prefix or ""),
            parent=parent,
        )

    def create_log_line_worker(self, request_id: int, *, message: str, parent=None):
        from telegram_proxy.runtime.workers import TelegramProxyLogLineWorker

        return TelegramProxyLogLineWorker(
            request_id,
            append_log_line_fn=self.append_log_line,
            message=str(message or ""),
            parent=parent,
        )

    def create_auto_deeplink_worker(self, request_id: int, *, parent=None):
        from telegram_proxy.runtime.workers import TelegramProxyAutoDeeplinkWorker

        return TelegramProxyAutoDeeplinkWorker(
            request_id,
            consume_auto_deeplink_request_fn=self.consume_auto_deeplink_request,
            parent=parent,
        )

    def create_relay_check_worker(self, *, generation: int, get_zapret_running, parent=None):
        from telegram_proxy.runtime.workers import TelegramProxyRelayCheckWorker

        return TelegramProxyRelayCheckWorker(
            generation=generation,
            check_relay_reachable=self.check_relay_reachable,
            check_relay_http=self.check_relay_http,
            get_zapret_running=get_zapret_running,
            parent=parent,
        )

    def create_cloudflare_check_worker(self, request_id: int, *, kind: str, domains, parent=None):
        from telegram_proxy.runtime.workers import TelegramProxyCloudflareCheckWorker

        return TelegramProxyCloudflareCheckWorker(
            request_id,
            kind=str(kind or "domain"),
            domains=domains,
            check_cloudflare_connectivity=self.check_cloudflare_connectivity,
            append_log_line_fn=self.append_log_line,
            parent=parent,
        )

    def create_diagnostics_worker(self, *, proxy_port: int, parent=None):
        from telegram_proxy.runtime.workers import TelegramProxyDiagnosticsWorker

        return TelegramProxyDiagnosticsWorker(
            run_diagnostics_fn=self.run_diagnostics,
            proxy_port=proxy_port,
            parent=parent,
        )

    def create_hosts_worker(self, request_id: int, *, action: str, parent=None):
        from telegram_proxy.runtime.workers import TelegramHostsWorker

        return TelegramHostsWorker(
            request_id,
            action=action,
            run_hosts_action_fn=self.run_telegram_hosts_action,
            parent=parent,
        )

    def create_settings_save_worker(self, request_id: int, **kwargs):
        from telegram_proxy.runtime.workers import TelegramProxySettingsSaveWorker

        return TelegramProxySettingsSaveWorker(
            request_id,
            save_settings_action=self.save_settings_action,
            **kwargs,
        )

    # -- Очередь сохранения настроек ------------------------------------
    #
    # Страницы только просят сохранить значение. Очередь живёт здесь, чтобы
    # основная страница, вложенная страница и трей видели одно и то же
    # состояние: пока очередь не пуста, запуск прокси ждёт, иначе он
    # прочитал бы из хранилища ещё старые значения.

    def request_settings_save(
        self,
        action: str,
        *,
        restart: str = "",
        host: str = "",
        port: int = 0,
        user: str = "",
        password: str = "",
        preset_id: str = "",
        enabled: bool = False,
        value: object = "",
    ) -> None:
        payload = {
            "action": str(action or ""),
            "host": str(host or ""),
            "port": int(port or 0),
            "user": str(user or ""),
            "password": str(password or ""),
            "preset_id": str(preset_id or ""),
            "enabled": bool(enabled),
            "value": value,
            "restart": str(restart or ""),
        }
        if self.has_pending_settings_saves():
            # Новое значение не должно обогнать уже ждущее в очереди.
            self._queue_settings_save_payload(payload)
            return
        self._start_settings_save_worker(payload)

    def has_pending_settings_saves(self) -> bool:
        state = self._settings_save_state
        return bool(state.in_flight or state.queue.has_pending() or state.queue.start_scheduled)

    def add_settings_flushed_listener(self, callback) -> None:
        listeners = self._settings_save_state.listeners
        if callback not in listeners:
            listeners.append(callback)

    def remove_settings_flushed_listener(self, callback) -> None:
        listeners = self._settings_save_state.listeners
        if callback in listeners:
            listeners.remove(callback)

    def _queue_settings_save_payload(self, payload: dict) -> bool:
        return self._settings_save_state.queue.replace_by_key(
            dict(payload or {}),
            key=lambda pending: str(pending.get("action") or ""),
        )

    def _start_settings_save_worker(self, payload: dict) -> None:
        state = self._settings_save_state
        state.in_flight = True

        def bind_worker(worker) -> None:
            worker.completed.connect(self._on_settings_save_completed)
            worker.failed.connect(self._on_settings_save_failed)

        state.runtime.start_qthread_worker(
            worker_factory=lambda request_id: self._create_queued_settings_save_worker(request_id, payload),
            bind_worker=bind_worker,
            on_finished=self._on_settings_save_worker_finished,
        )

    def _create_queued_settings_save_worker(self, request_id: int, payload: dict):
        return self.create_settings_save_worker(
            request_id,
            action=str(payload.get("action") or ""),
            host=str(payload.get("host") or ""),
            port=int(payload.get("port") or 0),
            user=str(payload.get("user") or ""),
            password=str(payload.get("password") or ""),
            preset_id=str(payload.get("preset_id") or ""),
            enabled=bool(payload.get("enabled")),
            value=payload.get("value", ""),
            context_extra={"restart": str(payload.get("restart") or "")},
            parent=None,
        )

    def _on_settings_save_completed(self, request_id: int, _action: str, _result, context) -> None:
        from telegram_proxy.runtime.settings_save_flow import merge_restart_request

        state = self._settings_save_state
        if not state.runtime.is_current(request_id):
            return
        state.in_flight = False
        state.restart_pending = merge_restart_request(
            state.restart_pending,
            str(dict(context or {}).get("restart") or ""),
        )
        self._flush_settings_saves_if_drained()

    def _on_settings_save_failed(self, request_id: int, action: str, error: str, _context) -> None:
        state = self._settings_save_state
        if not state.runtime.is_current(request_id):
            return
        state.in_flight = False
        from log.log import log

        log(f"Telegram Proxy: не удалось сохранить настройку ({action}): {error}", "WARNING")
        # Рестарт от прошлых удачных сохранений не теряем: они уже записаны,
        # а работающий прокси всё ещё на старых значениях.
        self._flush_settings_saves_if_drained()

    def _on_settings_save_worker_finished(self, worker) -> None:
        # OneShotWorkerRuntime вызывает этот обработчик только для текущего
        # worker-а, поэтому дополнительная проверка не нужна.
        self._settings_save_state.queue.schedule_next_after_finish(
            worker,
            is_current_worker_finish=lambda _runtime, _worker: True,
            single_shot=QTimer.singleShot,
            start=self._start_settings_save_worker,
            queue_item=self._queue_settings_save_payload,
            is_cleanup_in_progress=lambda: False,
        )

    def _flush_settings_saves_if_drained(self) -> None:
        state = self._settings_save_state
        if state.in_flight or state.queue.has_pending() or state.queue.start_scheduled:
            return
        restart = state.restart_pending
        state.restart_pending = ""
        for callback in list(state.listeners):
            try:
                callback(restart)
            except Exception as exc:
                from log.log import log

                log(f"Telegram Proxy: ошибка обработчика сохранения настроек: {exc}", "WARNING")
        if self._tray_toggle_state.pending_count > 0 and not self._tray_toggle_is_busy():
            self._schedule_tray_toggle_start()

    def create_page_initial_state_worker(self, request_id: int, *, parent=None):
        from telegram_proxy.runtime.workers import TelegramProxyInitialStateWorker

        return TelegramProxyInitialStateWorker(
            request_id,
            load_page_initial_state=self.load_page_initial_state,
            parent=parent,
        )

    def toggle_async(self) -> None:
        try:
            if self._tray_toggle_is_busy():
                self._queue_tray_toggle()
                return
            manager = self.get_proxy_manager()
            if manager.is_running:
                self._tray_stop_runtime.start_qthread_worker(
                    worker_factory=lambda request_id: self._create_tray_stop_worker(
                        request_id,
                        manager=manager,
                    ),
                    on_finished=self._on_tray_toggle_worker_finished,
                    signal_includes_request_id=False,
                    loaded_signal_name="stopped",
                )
                return

            if self.has_pending_settings_saves():
                # Запуск прочитает настройки из хранилища, поэтому ждём,
                # пока очередь сохранений допишет последние значения.
                self._queue_tray_toggle()
                return
            self._tray_start_runtime.start_qthread_worker(
                worker_factory=lambda request_id: self._create_tray_start_worker(
                    request_id,
                    manager=manager,
                ),
                on_finished=self._on_tray_toggle_worker_finished,
                signal_includes_request_id=False,
                loaded_signal_name="completed",
            )
        except Exception as exc:
            from log.log import log

            log(f"Telegram Proxy toggle error: {exc}", "WARNING")

    def _tray_toggle_is_busy(self) -> bool:
        return (
            self._tray_start_runtime.is_running()
            or self._tray_stop_runtime.is_running()
            or bool(self._tray_toggle_state.start_scheduled)
        )

    def _queue_tray_toggle(self) -> None:
        self._tray_toggle_state.pending_count += 1

    def _on_tray_toggle_worker_finished(self, _worker) -> None:
        if not self._is_current_tray_toggle_worker_finish(_worker):
            return
        if self._tray_toggle_state.pending_count > 0:
            self._schedule_tray_toggle_start()

    def _create_tray_start_worker(self, request_id: int, **kwargs):
        worker = self.create_start_worker(**kwargs)
        self._mark_tray_toggle_worker(worker, request_id, "start")
        return worker

    def _create_tray_stop_worker(self, request_id: int, *, manager):
        worker = self.create_stop_runtime_worker(
            manager=manager,
            emit_status=True,
            set_enabled=self.set_enabled,
            enabled_after_stop=False,
        )
        self._mark_tray_toggle_worker(worker, request_id, "stop")
        return worker

    def _mark_tray_toggle_worker(self, worker, request_id: int, runtime_name: str) -> None:
        try:
            worker._request_id = int(request_id)
            worker._tray_toggle_runtime = str(runtime_name)
        except Exception:
            pass

    def _is_current_tray_toggle_worker_finish(self, worker) -> bool:
        runtime_name = getattr(worker, "_tray_toggle_runtime", None)
        runtime = None
        if runtime_name == "start":
            runtime = self._tray_start_runtime
        elif runtime_name == "stop":
            runtime = self._tray_stop_runtime
        else:
            for candidate in (self._tray_start_runtime, self._tray_stop_runtime):
                current_worker = getattr(candidate, "worker", None)
                if current_worker is not None and worker is current_worker:
                    return True
            return False

        request_id = getattr(worker, "_request_id", None)
        if request_id is None:
            current_worker = getattr(runtime, "worker", None)
            return current_worker is not None and worker is current_worker
        try:
            return int(request_id) == int(getattr(runtime, "request_id", -1))
        except (TypeError, ValueError):
            return False

    def _schedule_tray_toggle_start(self) -> None:
        if self._tray_toggle_state.start_scheduled:
            return
        self._tray_toggle_state.start_scheduled = True
        QTimer.singleShot(0, self._run_scheduled_tray_toggle)

    def _run_scheduled_tray_toggle(self) -> None:
        self._tray_toggle_state.start_scheduled = False
        if self._tray_toggle_state.pending_count <= 0:
            return
        self._tray_toggle_state.pending_count -= 1
        self.toggle_async()


def build_telegram_proxy_feature() -> TelegramProxyFeature:
    def _commands():
        from telegram_proxy.runtime import commands as telegram_proxy_commands

        return telegram_proxy_commands

    def _public():
        from telegram_proxy import public as telegram_proxy_public

        return telegram_proxy_public

    def _copy_text_via_qt(*args, **kwargs):
        from PyQt6.QtGui import QGuiApplication

        clipboard = QGuiApplication.clipboard()

        def _write_clipboard(text: str) -> None:
            if clipboard is None:
                raise RuntimeError("clipboard unavailable")
            clipboard.setText(text)

        return _public().copy_text(*args, clipboard_writer=_write_clipboard, **kwargs)

    return TelegramProxyFeature(
        start_proxy_if_enabled_async=lambda *args, **kwargs: _public().start_proxy_if_enabled_async(*args, **kwargs),
        get_proxy_manager=lambda *args, **kwargs: _commands().get_proxy_manager(*args, **kwargs),
        get_start_config=lambda *args, **kwargs: _commands().get_start_config(*args, **kwargs),
        set_enabled=lambda *args, **kwargs: _public().set_enabled(*args, **kwargs),
        build_upstream_config=lambda *args, **kwargs: _commands().build_upstream_config(*args, **kwargs),
        load_page_initial_state=lambda *args, **kwargs: _commands().load_page_initial_state(*args, **kwargs),
        save_settings_action=lambda *args, **kwargs: _commands().save_settings_action(*args, **kwargs),
        check_relay_reachable=lambda *args, **kwargs: _commands().check_relay_reachable(*args, **kwargs),
        check_relay_http=lambda *args, **kwargs: _commands().check_relay_http(*args, **kwargs),
        check_cloudflare_connectivity=lambda *args, **kwargs: _commands().check_cloudflare_connectivity(*args, **kwargs),
        get_cloudflare_dns_records_text=lambda *args, **kwargs: _commands().get_cloudflare_dns_records_text(*args, **kwargs),
        get_cloudflare_worker_code=lambda *args, **kwargs: _commands().get_cloudflare_worker_code(*args, **kwargs),
        get_fake_tls_nginx_config=lambda *args, **kwargs: _commands().get_fake_tls_nginx_config(*args, **kwargs),
        build_diagnostics_start_plan=lambda *args, **kwargs: _public().build_diagnostics_start_plan(*args, **kwargs),
        build_diagnostics_poll_plan=lambda *args, **kwargs: _public().build_diagnostics_poll_plan(*args, **kwargs),
        build_diagnostics_finish_plan=lambda *args, **kwargs: _public().build_diagnostics_finish_plan(*args, **kwargs),
        copy_text=lambda *args, **kwargs: _copy_text_via_qt(*args, **kwargs),
        open_log_file=lambda *args, **kwargs: _public().open_log_file(*args, **kwargs),
        open_external_link=lambda *args, **kwargs: _public().open_external_link(*args, **kwargs),
        run_telegram_hosts_action=lambda *args, **kwargs: _public().run_telegram_hosts_action(*args, **kwargs),
        run_diagnostics=lambda *args, **kwargs: _public().run_diagnostics(*args, **kwargs),
        append_log_line=lambda *args, **kwargs: _public().append_log_line(*args, **kwargs),
        consume_auto_deeplink_request=lambda *args, **kwargs: _public().consume_auto_deeplink_request(*args, **kwargs),
    )
