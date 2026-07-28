from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from settings.mode import normalize_launch_method

from PyQt6.QtCore import QObject, Qt, pyqtSignal


class RuntimeEventDispatcher(QObject):
    status = pyqtSignal(str)
    runner_failure = pyqtSignal(object)
    launch_error = pyqtSignal(str)
    active_preset_content_changed = pyqtSignal(str)
    unexpected_process_exit = pyqtSignal(object)
    installation_damaged = pyqtSignal(object)


@dataclass(slots=True)
class RuntimeUiPort:
    runtime_ui_bridge: Any = None
    notify: Any = None

    def set_status(self, text: str) -> None:
        self.require_runtime_ui_bridge().set_status(str(text or ""))

    def show_launch_error(self, text: str) -> None:
        self.require_runtime_ui_bridge().show_launch_error(str(text or ""))

    def handle_runtime_content_changed(self, key: str) -> None:
        self.require_runtime_ui_bridge().handle_runtime_content_changed(str(key or ""))

    def require_runtime_ui_bridge(self):
        bridge = self.runtime_ui_bridge
        if bridge is None:
            raise RuntimeError("Runtime UI bridge is not configured")
        return bridge

    def configure_runtime_ui_bridge(self, bridge) -> None:
        self.runtime_ui_bridge = bridge

    def configure_notifications(self, *, notify) -> None:
        self.notify = notify

    def require_notifications(self):
        if self.notify is None:
            raise RuntimeError("Runtime notifications are not configured")
        return self.notify


@dataclass(frozen=True, slots=True)
class RuntimeLifecyclePort:
    startup_state: Any = None
    qt_parent: Any = None
    mark_stop_and_exit_requested_callback: Any = None

    def mark_stop_and_exit_requested(self) -> None:
        callback = self.mark_stop_and_exit_requested_callback
        if callable(callback):
            callback()


@dataclass(frozen=True, slots=True)
class RuntimeDependencies:
    presets_feature: Any
    profile_feature: Any
    orchestra_feature: Any


@dataclass(slots=True)
class RuntimeFlags:
    manually_stopped: bool = False
    intentional_start: bool = False

    def mark_manual_stop(self) -> None:
        self.manually_stopped = True

    def mark_intentional_start(self) -> None:
        self.intentional_start = True


@dataclass(slots=True)
class RuntimeObjects:
    runtime_service: Any
    process_monitor_manager: Any = None
    launch_runtime_api: Any = None
    launch_runtime: Any = None
    warned_foreign_pid_set: frozenset = frozenset()

    def snapshot(self):
        if self.runtime_service is None:
            raise RuntimeError("Runtime service is required")
        return self.runtime_service.snapshot()

    def is_available(self) -> bool:
        return self.launch_runtime is not None

    def is_running(self) -> bool:
        try:
            snapshot = self.runtime_service.snapshot()
            return bool(getattr(snapshot, "running", False)) and str(
                getattr(snapshot, "phase", "") or ""
            ).strip().lower() == "running"
        except Exception:
            return False

    def is_any_running(self, *, silent: bool = True) -> bool:
        if self.launch_runtime_api is None:
            return False
        try:
            return bool(self.launch_runtime_api.is_any_running(silent=silent))
        except Exception:
            return False

    def transition_pipeline_in_progress(self, launch_method: str | None = None) -> bool:
        if self.launch_runtime is None:
            return False
        return bool(self.launch_runtime.transition_pipeline_in_progress(launch_method))

    def observe_process_details(self, details: dict | None) -> None:
        try:
            self.runtime_service.observe_process_details(dict(details or {}))
        except Exception:
            pass

    def current_process_pid(self, launch_method: str) -> int | None:
        """Возвращает PID только из опубликованного monitor snapshot."""
        try:
            snapshot = self.runtime_service.snapshot()
        except Exception:
            snapshot = None
        snapshot_pid = getattr(snapshot, "pid", None)
        snapshot_running = bool(getattr(snapshot, "running", False))
        snapshot_method = str(getattr(snapshot, "launch_method", "") or "").strip().lower()
        requested_method = normalize_launch_method(launch_method, default="")
        if isinstance(snapshot_pid, int) and snapshot_running and snapshot_method == requested_method:
            return snapshot_pid
        return None

    def observe_foreign_processes(self, foreign: dict | None) -> None:
        """Warns when a non-canonical winws runs next to our active DPI.

        Two winws instances fight over the WinDivert filter, so this is a real
        user-facing conflict. One warning per unique foreign pid-set; silent
        while DPI is not running or an external scan (BlockCheck) is active.
        """
        normalized = {int(pid): str(path or "") for pid, path in dict(foreign or {}).items()}
        pid_set = frozenset(normalized)
        if not pid_set:
            self.warned_foreign_pid_set = frozenset()
            return
        if pid_set == self.warned_foreign_pid_set:
            return

        try:
            snapshot = self.runtime_service.snapshot()
        except Exception:
            return
        if not bool(getattr(snapshot, "running", False)):
            return

        try:
            from winws_runtime.runtime.scan_guard import is_external_winws_scan_active

            if is_external_winws_scan_active():
                return
        except Exception:
            pass

        self.warned_foreign_pid_set = pid_set
        from log.log import log

        listed = "; ".join(
            f"PID {pid}: {normalized[pid]}" for pid in sorted(normalized)
        )
        log(
            "Обнаружен посторонний winws рядом с работающим Zapret — два обхода DPI "
            f"конфликтуют за WinDivert. Закройте другую копию ({listed})",
            "ERROR",
        )

    def ensure_process_monitor_manager(self):
        if self.process_monitor_manager is None:
            from winws_runtime.monitoring import ProcessMonitorManager

            self.process_monitor_manager = ProcessMonitorManager(
                observe_process_details=self.observe_process_details,
                observe_foreign_processes=self.observe_foreign_processes,
            )
        return self.process_monitor_manager

    def cleanup_process_monitor(self) -> None:
        manager = self.process_monitor_manager
        if manager is not None:
            manager.stop_monitoring()


_AUTO_RESTART_WINDOW_SECONDS = 600.0
_AUTO_RESTART_MAX_PER_WINDOW = 2


@dataclass(slots=True, weakref_slot=True)
class RuntimeEvents:
    """Владелец Qt-диспетчера runtime-событий.

    `weakref_slot` обязателен: методы этого объекта подключаются к сигналам
    через `QueuedConnection`, а PyQt для такого соединения берёт слабую ссылку
    на приёмник. У `slots=True` без этого флага нет `__weakref__`, и connect
    падает `SystemError: ... QMetaObject.Connection returned a result with an
    exception set` — из-за чего весь launch runtime не поднимался и DPI не
    стартовал.
    """

    runtime_service: Any
    ui_port: Any = None
    ui_state: Any = None
    qt_parent: Any = None
    dispatcher: RuntimeEventDispatcher | None = None
    command_port: Any = None
    repair_port: Any = None
    auto_restart_history: list = field(default_factory=list)

    def ensure_dispatcher(self) -> RuntimeEventDispatcher:
        if self.dispatcher is None:
            dispatcher = RuntimeEventDispatcher(self.qt_parent)
            dispatcher.status.connect(
                self.handle_status,
                Qt.ConnectionType.QueuedConnection,
            )
            dispatcher.runner_failure.connect(
                self.handle_runner_failure,
                Qt.ConnectionType.QueuedConnection,
            )
            dispatcher.launch_error.connect(
                self.handle_launch_error,
                Qt.ConnectionType.QueuedConnection,
            )
            dispatcher.active_preset_content_changed.connect(
                self.handle_active_preset_content_changed,
                Qt.ConnectionType.QueuedConnection,
            )
            dispatcher.unexpected_process_exit.connect(
                self.handle_unexpected_process_exit,
                Qt.ConnectionType.QueuedConnection,
            )
            dispatcher.installation_damaged.connect(
                self.handle_installation_damaged,
                Qt.ConnectionType.QueuedConnection,
            )
            self.dispatcher = dispatcher
        return self.dispatcher

    def publish_runner_failure(self, *, launch_method: str, error: str = "") -> None:
        self.ensure_dispatcher().runner_failure.emit(
            {
                "launch_method": str(launch_method or "").strip().lower(),
                "error": str(error or "").strip(),
            }
        )

    def publish_status(self, text: str) -> None:
        self.ensure_dispatcher().status.emit(str(text or ""))

    def handle_status(self, text: str) -> None:
        if self.ui_port is not None:
            self.ui_port.set_status(str(text or ""))

    def publish_launch_error(self, error: str = "") -> None:
        text = str(error or "").strip()
        if text:
            self.ensure_dispatcher().launch_error.emit(text)

    def publish_active_preset_content_changed(self, path: str) -> None:
        normalized_path = str(path or "").strip()
        if normalized_path:
            self.ensure_dispatcher().active_preset_content_changed.emit(normalized_path)

    def publish_unexpected_process_exit(self, resolution) -> None:
        """Передаёт в UI уже готовый результат фоновой диагностики."""
        self.ensure_dispatcher().unexpected_process_exit.emit(resolution)

    def publish_installation_damaged(self, report) -> None:
        """Сообщает, что поставка не соответствует манифесту.

        Runtime не умеет и не должен уметь чинить установку: решение и
        исполнение живут в слое приложения, у которого есть updater.
        """
        if report is None:
            return
        self.ensure_dispatcher().installation_damaged.emit(report)

    def handle_installation_damaged(self, report) -> None:
        port = self.repair_port
        if port is None:
            return
        try:
            port.request_repair(report)
        except Exception:
            return

    def post_runtime_state_sync_after_shutdown(
        self,
        *,
        still_running: bool,
        launch_method: str,
    ) -> None:
        """Применяет runtime-state после остановки строго в GUI-потоке.

        Подписчики UI-state — живые виджеты, поэтому запись состояния из
        фонового потока (BlockCheck-сканер) обязана дойти до них через
        GUI-поток; иначе правка QWidget уходит в чужой поток и подвешивает окно.
        """
        from winws_runtime.runtime.sync_shutdown import apply_runtime_state_after_shutdown

        runtime_service = self.runtime_service

        def _apply() -> None:
            apply_runtime_state_after_shutdown(
                runtime_service=runtime_service,
                still_running=bool(still_running),
                launch_method=str(launch_method or ""),
            )

        store = self.ui_state
        post_to_ui_thread = getattr(store, "post_to_ui_thread", None) if store is not None else None
        if callable(post_to_ui_thread):
            post_to_ui_thread(_apply)
            return

        _apply()

    def handle_runner_failure(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return

        launch_method = str(payload.get("launch_method") or "").strip().lower()
        try:
            from settings.mode import is_preset_launch_method

            if not is_preset_launch_method(launch_method):
                return
        except Exception:
            return

        error_text = str(payload.get("error") or "").strip()
        self.mark_start_failed_if_current(
            launch_method=launch_method,
            error_text=error_text,
        )
        if error_text and self.ui_port is not None:
            try:
                self.ui_port.show_launch_error(error_text)
            except Exception:
                pass

    def handle_launch_error(self, error: str) -> None:
        if self.ui_port is None:
            return
        try:
            self.ui_port.show_launch_error(str(error or ""))
        except Exception:
            pass

    def handle_unexpected_process_exit(self, resolution) -> None:
        """Main-thread reaction uses only the prepared immutable result."""
        from log.log import log

        snapshot = self.runtime_service.snapshot()
        if snapshot.phase not in {"starting", "running"}:
            return

        if resolution is None:
            return

        if resolution.transient and self._try_reserve_auto_restart():
            log(
                f"{resolution.message} Выполняется автоматический перезапуск...",
                "WARNING",
            )
            self.runtime_service.mark_start_failed(resolution.message)
            if self._request_auto_restart():
                return
            log("Автоматический перезапуск не удалось запустить", "WARNING")

        message = resolution.message
        if resolution.transient:
            message = f"{message} Автоперезапуск отключён до ручного запуска."
        # Single user-facing publication for this death event (ERROR → toast).
        log(message, "ERROR")
        self.runtime_service.mark_start_failed(message)

    def _try_reserve_auto_restart(self, *, now: float | None = None) -> bool:
        import time

        current = float(now if now is not None else time.monotonic())
        window_start = current - _AUTO_RESTART_WINDOW_SECONDS
        self.auto_restart_history = [
            stamp for stamp in self.auto_restart_history if stamp > window_start
        ]
        if len(self.auto_restart_history) >= _AUTO_RESTART_MAX_PER_WINDOW:
            return False
        self.auto_restart_history.append(current)
        return True

    def _request_auto_restart(self) -> bool:
        command_port = self.command_port
        if command_port is None:
            return False
        try:
            return bool(command_port.start())
        except Exception:
            return False

    def mark_start_failed_if_current(
        self,
        *,
        launch_method: str,
        error_text: str,
    ) -> bool:
        method = str(launch_method or "").strip().lower()
        snapshot = self.runtime_service.snapshot()
        current_method = str(snapshot.launch_method or "").strip().lower()
        if current_method and current_method != method and snapshot.phase in {"starting", "running", "autostart_pending"}:
            return False
        self.runtime_service.mark_start_failed(
            str(error_text or "").strip() or "Запуск завершился ошибкой",
        )
        return True

    def handle_active_preset_content_changed(self, path: str) -> None:
        if self.ui_port is None:
            return
        try:
            self.ui_port.handle_runtime_content_changed(path)
        except Exception:
            pass


@dataclass(slots=True)
class RuntimeCommandPort:
    owner: Any

    @staticmethod
    def _runtime_commands():
        from winws_runtime.runtime import commands as runtime_commands

        return runtime_commands

    def _mark_startup_runtime_init_failed(self, exc: Exception) -> None:
        runtime_service = self.owner.objects.runtime_service
        try:
            runtime_service.set_busy(False)
        except Exception:
            pass
        try:
            runtime_service.mark_start_failed(str(exc or "").strip() or "Ошибка подготовки запуска")
        except Exception:
            pass

    def init_launch_runtime_api(self) -> None:
        runtime_commands = self._runtime_commands()
        try:
            self.owner.objects.launch_runtime_api = runtime_commands.init_launch_runtime_api(runtime_feature=self.owner)
        except Exception as exc:
            self._mark_startup_runtime_init_failed(exc)
            raise

    def init_launch_runtime(self) -> None:
        runtime_commands = self._runtime_commands()
        try:
            # QObject dispatcher создаётся один раз в главном Qt-потоке;
            # фоновые watcher/worker затем только посылают его сигналы.
            self.owner.events.ensure_dispatcher()
            notify = self.owner.ui_port.require_notifications()
            self.owner.objects.launch_runtime = runtime_commands.init_launch_runtime(
                runtime_feature=self.owner,
                runtime_api=self.owner.objects.launch_runtime_api,
                notify=notify,
            )
            self.owner.objects.runtime_service.set_busy(False)
        except Exception as exc:
            self._mark_startup_runtime_init_failed(exc)
            raise

    def init_process_monitor(self) -> None:
        runtime_commands = self._runtime_commands()
        runtime_commands.init_process_monitor(
            process_monitor_manager=self.owner.objects.ensure_process_monitor_manager(),
            runtime_api=self.owner.objects.launch_runtime_api,
            runtime_service=self.owner.objects.runtime_service,
        )

    def init_core_startup(self) -> None:
        runtime_commands = self._runtime_commands()
        runtime_commands.init_core_startup()

    def start(
        self,
        selected_mode: Any = None,
        launch_method: Any = None,
        *,
        skip_conflict_prompt: bool = False,
        startup_autostart: bool = False,
    ) -> bool:
        runtime_commands = self._runtime_commands()
        return bool(
            runtime_commands.start_dpi_async(
                runtime_feature=self.owner,
                selected_mode=selected_mode,
                launch_method=launch_method,
                skip_conflict_prompt=skip_conflict_prompt,
                startup_autostart=startup_autostart,
            )
        )

    def stop(
        self,
        *,
        force_cleanup: bool = False,
        cleanup_services: bool = False,
    ) -> bool:
        runtime_commands = self._runtime_commands()
        return bool(
            runtime_commands.stop_dpi_async(
                runtime_feature=self.owner,
                force_cleanup=force_cleanup,
                cleanup_services=cleanup_services,
            )
        )

    def restart(self, *, force_full_stop: bool = False) -> bool:
        runtime_commands = self._runtime_commands()
        return bool(
            runtime_commands.restart_dpi_async(
                runtime_feature=self.owner,
                force_full_stop=force_full_stop,
            )
        )

    def switch_preset(self, method: str | None = None) -> bool:
        runtime_commands = self._runtime_commands()
        return bool(runtime_commands.switch_presets_async(runtime_feature=self.owner, method=method))

    def stop_and_exit(self) -> bool:
        runtime_commands = self._runtime_commands()
        self.owner.lifecycle.mark_stop_and_exit_requested()
        return bool(runtime_commands.stop_and_exit_async(runtime_feature=self.owner))

    def cleanup_threads(self) -> bool:
        runtime_commands = self._runtime_commands()
        return bool(runtime_commands.cleanup_launch_threads(runtime_feature=self.owner))

    def shutdown_sync(
        self,
        *,
        reason: str = "",
        include_cleanup: bool = True,
        cleanup_services: bool = True,
        update_runtime_state: bool = True,
    ):
        runtime_commands = self._runtime_commands()
        return runtime_commands.shutdown_runtime_sync(
            runtime_feature=self.owner,
            reason=reason,
            include_cleanup=include_cleanup,
            cleanup_services=cleanup_services,
            update_runtime_state=update_runtime_state,
        )

    def shutdown_sync_from_worker(
        self,
        *,
        reason: str = "",
        include_cleanup: bool = True,
        cleanup_services: bool = True,
        update_runtime_state: bool = True,
    ):
        """Синхронная остановка для вызывающих из фоновых потоков.

        Drop-in замена `shutdown_sync`: процессную часть выполняет вызывающий
        поток, а runtime-state (и, значит, UI-подписчиков) обновляет GUI-поток.
        """
        from winws_runtime.runtime.sync_shutdown import resolve_launch_method

        launch_method = resolve_launch_method(self.owner) if update_runtime_state else ""
        result = self.shutdown_sync(
            reason=reason,
            include_cleanup=include_cleanup,
            cleanup_services=cleanup_services,
            update_runtime_state=False,
        )
        if update_runtime_state:
            self.owner.events.post_runtime_state_sync_after_shutdown(
                still_running=bool(getattr(result, "still_running", False)),
                launch_method=launch_method,
            )
        return result

    def start_autostart(self, launch_method: str | None = None) -> bool:
        runtime_commands = self._runtime_commands()
        return bool(
            runtime_commands.start_dpi_autostart(
                self.owner.lifecycle.startup_state,
                runtime_feature=self.owner,
                ui_state=self.owner.events.ui_state,
                launch_method=launch_method,
            )
        )

    def handle_launch_method_changed(self, method: str, *, autostart_enabled: bool, set_status=None):
        runtime_commands = self._runtime_commands()
        return runtime_commands.handle_launch_method_changed(
            method,
            runtime_feature=self.owner,
            ui_state=self.owner.events.ui_state,
            autostart_enabled=autostart_enabled,
            set_status=set_status,
        )

    def apply_selected_source_preset(
        self,
        *,
        launch_method: str,
        reason: str,
        preset_file_name: str = "",
    ) -> bool:
        runtime_commands = self._runtime_commands()
        return bool(
            runtime_commands.request_selected_source_preset_apply(
                runtime_feature=self.owner,
                launch_method=launch_method,
                reason=reason,
                preset_file_name=preset_file_name,
            )
        )

    def apply_preset_content(
        self,
        *,
        launch_method: str,
        reason: str,
        profile_key: str | None = None,
    ) -> bool:
        runtime_commands = self._runtime_commands()
        return bool(
            runtime_commands.request_preset_runtime_content_apply(
                runtime_feature=self.owner,
                launch_method=launch_method,
                reason=reason,
                profile_key=profile_key,
            )
        )

    def create_preset_runtime_coordinator(self, **kwargs):
        runtime_commands = self._runtime_commands()
        return runtime_commands.create_preset_runtime_coordinator(
            qt_parent=self.owner.lifecycle.qt_parent,
            runtime_feature=self.owner,
            **kwargs,
        )

    def resume_start_after_conflict_resolution(
        self,
        request_id: int,
        *,
        close_conflicts: bool,
    ) -> bool:
        runtime_commands = self._runtime_commands()
        return bool(
            runtime_commands.resume_start_after_conflict_resolution(
                request_id,
                runtime_feature=self.owner,
                close_conflicts=close_conflicts,
            )
        )

    def prepare_launch_conflict_resolution(self, request_id: int, *, close_conflicts: bool) -> tuple[bool, str]:
        runtime_commands = self._runtime_commands()
        return runtime_commands.prepare_launch_conflict_resolution(
            request_id,
            runtime_feature=self.owner,
            close_conflicts=close_conflicts,
        )

    def continue_start_after_conflict_resolution(self, request_id: int) -> bool:
        runtime_commands = self._runtime_commands()
        return bool(
            runtime_commands.continue_start_after_conflict_resolution(
                request_id,
                runtime_feature=self.owner,
            )
        )

    def cancel_start_after_conflict_prompt(self, request_id: int) -> bool:
        runtime_commands = self._runtime_commands()
        return bool(
            runtime_commands.cancel_start_after_conflict_prompt(
                request_id,
                runtime_feature=self.owner,
            )
        )

    def execute_windivert_autofix(self, action: str) -> tuple[bool, str]:
        runtime_commands = self._runtime_commands()
        return runtime_commands.execute_windivert_autofix(action)

    def install_windows_server_wlanapi(self) -> tuple[bool, str]:
        runtime_commands = self._runtime_commands()
        return runtime_commands.install_windows_server_wlanapi()
