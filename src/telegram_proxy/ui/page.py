# ui/pages/telegram_proxy_page.py
"""Telegram WebSocket Proxy — UI page.

Provides controls for starting/stopping the proxy, mode selection,
port configuration, and quick-setup deep link for Telegram.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout,
)

from ui.pages.base_page import BasePage
from ui.one_shot_worker_runtime import OneShotWorkerRuntime
from ui.performance_metrics import log_ui_timing_since
from telegram_proxy.ui.build import (
    build_telegram_proxy_diag_panel,
    build_telegram_proxy_logs_panel,
    build_telegram_proxy_shell,
)
from telegram_proxy.ui.diagnostics_workflow import (
    finish_diagnostics,
    poll_diagnostics,
    start_diagnostics,
)
from telegram_proxy.ui.proxy_runtime_workflow import (
    apply_relay_result,
    apply_stats_updated,
    apply_status_changed,
    finish_proxy_start,
    handle_toggle_proxy,
    restart_proxy_if_running,
    start_proxy_runtime,
    start_relay_check,
    stop_proxy_runtime,
)
from telegram_proxy.ui.runtime_helpers import (
    apply_ui_texts,
    refresh_pivot_texts,
    refresh_status_texts,
)
from telegram_proxy.ui.text_plan import TELEGRAM_PROXY_SETTINGS_TEXT
from telegram_proxy.ui.upstream_workflow import schedule_upstream_restart
from telegram_proxy.ui.settings_build import build_telegram_proxy_settings_panel
from telegram_proxy.ui.worker_state import (
    TelegramProxyPageQueuedWorkerState,
    TelegramProxyPageWorkerState,
)
from ui.fluent_widgets import enable_setting_card_group_auto_height
from log.log import log

import telegram_proxy.ui.page_runtime as telegram_proxy_page_runtime
import telegram_proxy.config.settings as telegram_proxy_settings
from qfluentwidgets import (
    CaptionLabel,
    InfoBar,
    InfoBarPosition,
    SegmentedWidget,
    PushButton,
    PrimaryPushButton,
)

# How often (ms) the GUI reads new log lines from the ring buffer
_LOG_REFRESH_MS = 500

_ZASTOGRAM_URL = "https://git.zapret.moe/zastogram/ZaStoGram_desktop"



class _StatusDot(QWidget):
    """Small colored circle indicator."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self._active = False

    def set_active(self, active: bool):
        self._active = active
        self.update()

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QColor
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#4CAF50") if self._active else QColor("#888888")
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(1, 1, 10, 10)
        p.end()


class TelegramProxyPage(BasePage):
    """Telegram WebSocket Proxy settings page."""

    def __init__(self, parent=None, *, telegram_proxy_feature, get_zapret_running, open_advanced_settings):
        super().__init__(
            "Telegram Proxy",
            TELEGRAM_PROXY_SETTINGS_TEXT.page_subtitle,
            parent,
        )
        self._telegram_proxy = telegram_proxy_feature
        self._get_zapret_running = get_zapret_running
        self._open_advanced_settings = open_advanced_settings
        self._log_timer = None
        self._stats_timer = None
        self._diag_poll_timer = None
        self._restart_debounce_timer = None
        self._upstream_apply_debounce_timer = None
        self._diag_runtime = OneShotWorkerRuntime()
        self._proxy_start_runtime = OneShotWorkerRuntime()
        self._proxy_start_state = TelegramProxyPageWorkerState(self._proxy_start_runtime)
        self._proxy_stop_runtime = OneShotWorkerRuntime()
        self._proxy_stop_state = TelegramProxyPageWorkerState(self._proxy_stop_runtime)
        self._restart_stop_runtime = OneShotWorkerRuntime()
        self._restart_stop_state = TelegramProxyPageWorkerState(self._restart_stop_runtime)
        self._relay_check_runtime = OneShotWorkerRuntime()
        self._relay_check_state = TelegramProxyPageWorkerState(self._relay_check_runtime)
        self._ensure_hosts_runtime = OneShotWorkerRuntime()
        self._ensure_hosts_state = TelegramProxyPageWorkerState(self._ensure_hosts_runtime)
        self._upstream_apply_runtime = OneShotWorkerRuntime()
        self._upstream_apply_state = TelegramProxyPageWorkerState(self._upstream_apply_runtime)
        self._open_log_file_runtime = OneShotWorkerRuntime()
        self._open_log_file_state = TelegramProxyPageQueuedWorkerState(self._open_log_file_runtime)
        self._external_link_runtime = OneShotWorkerRuntime()
        self._external_link_state = TelegramProxyPageQueuedWorkerState(self._external_link_runtime)
        self._log_line_runtime = OneShotWorkerRuntime()
        self._log_line_state = TelegramProxyPageQueuedWorkerState(self._log_line_runtime)
        self._auto_deeplink_runtime = OneShotWorkerRuntime()
        self._auto_deeplink_state = TelegramProxyPageWorkerState(self._auto_deeplink_runtime)
        # Авто-настройка Telegram: ссылка открывается, когда прокси переходит
        # из «остановлен» в «работает», но не раньше загрузки настроек страницы.
        self._auto_deeplink_last_running = False
        self._auto_deeplink_due = False
        self._initial_state_applied = False
        # Запуск ждёт, пока очередь сохранений допишет настройки в хранилище.
        self._start_after_settings_flush = False
        self._initial_state_runtime = OneShotWorkerRuntime()
        self._initial_state_load_started_at = 0.0
        self._relay_check_gen = 0
        self._cleanup_in_progress = False
        self._runtime_initialized = False
        self._built_panel_indexes: set[int] = set()
        self._btn_copy_logs = None
        self._btn_open_log_file = None
        self._btn_clear_logs = None
        self._log_edit = None
        self._log_text_cache = ""
        self._log_text_line_count = 0
        self._diag_desc_label = None
        self._btn_run_diag = None
        self._btn_copy_diag = None
        self._diag_edit = None
        self._diag_text_cache = ""
        self._setup_ui()
        self._request_initial_state_load()
        self._after_ui_built()
        # Запуск Telegram Proxy живёт в общем старте приложения,
        # поэтому страница не поднимает его сама.

    def _proxy_manager(self):
        return self._telegram_proxy.get_proxy_manager()

    def _worker_state(self, state_attr: str, runtime_attr: str) -> TelegramProxyPageWorkerState:
        state = self.__dict__.get(state_attr)
        if state is None:
            state = TelegramProxyPageWorkerState(self.__dict__.get(runtime_attr))
            self.__dict__[state_attr] = state
        return state

    def _queued_worker_state(self, state_attr: str, runtime_attr: str) -> TelegramProxyPageQueuedWorkerState:
        state = self.__dict__.get(state_attr)
        if state is None:
            state = TelegramProxyPageQueuedWorkerState(self.__dict__.get(runtime_attr))
            self.__dict__[state_attr] = state
        return state

    def create_initial_state_worker(self, request_id: int):
        return self._telegram_proxy.create_page_initial_state_worker(request_id, parent=self)

    def _request_initial_state_load(self) -> None:
        self._initial_state_load_started_at = time.perf_counter()

        def bind_worker(worker) -> None:
            worker.completed.connect(self._on_initial_state_loaded)
            worker.failed.connect(self._on_initial_state_failed)

        self._initial_state_runtime.start_qthread_worker(
            worker_factory=self.create_initial_state_worker,
            bind_worker=bind_worker,
        )

    def _on_initial_state_loaded(self, request_id: int, initial_state) -> None:
        if not self._initial_state_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        self._log_ui_timing("telegram_proxy_ui.initial_state.load", self._initial_state_load_started_at)
        self._apply_initial_settings_state(initial_state.settings)

    def _on_initial_state_failed(self, request_id: int, error: str) -> None:
        if not self._initial_state_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        log(f"Не удалось загрузить начальное состояние Telegram Proxy: {error}", "WARNING")

    def _after_ui_built(self) -> None:
        started_at = time.perf_counter()
        self._telegram_proxy.add_settings_flushed_listener(self._on_settings_flushed)
        self._connect_signals()
        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self._flush_log_buffer)
        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._emit_stats_if_visible)
        if self.isVisible():
            self._stats_timer.start(2000)
        self._apply_ui_texts()
        self._log_ui_timing("telegram_proxy_ui.after_ui_built.total", started_at)

    def _run_runtime_init_once(self) -> None:
        plan = telegram_proxy_page_runtime.build_page_init_plan(
            runtime_initialized=self._runtime_initialized,
        )
        if not plan.ensure_hosts_once:
            return
        self._runtime_initialized = True
        self._ensure_telegram_hosts()

    def on_page_activated(self) -> None:
        self._run_runtime_init_once()

    def _setup_ui(self):
        started_at = time.perf_counter()
        shell = build_telegram_proxy_shell(
            segmented_widget_cls=SegmentedWidget,
            parent=self,
            on_switch_tab=self._switch_tab,
        )
        self._pivot = shell.pivot
        self._stacked = shell.stacked
        self._settings_layout = shell.settings_layout

        self._build_settings_panel(shell.settings_layout)
        self._built_panel_indexes.add(0)
        self._logs_layout = shell.logs_layout
        self._diag_layout = shell.diag_layout

        self.add_widget(self._pivot)
        self.add_widget(self._stacked, stretch=1)
        self._stacked.setCurrentIndex(0)
        self._log_ui_timing("telegram_proxy_ui.setup_ui.total", started_at)

    def _ensure_panel_built(self, index: int) -> None:
        if index in self._built_panel_indexes:
            return
        started_at = time.perf_counter()
        if index == 1:
            self._build_logs_panel(self._logs_layout)
            self._built_panel_indexes.add(index)
            self._apply_ui_texts()
            self._flush_log_buffer()
            self._log_ui_timing("telegram_proxy_ui.logs_panel.build", started_at)
        elif index == 2:
            self._build_diag_panel(self._diag_layout)
            self._built_panel_indexes.add(index)
            self._apply_ui_texts()
            self._log_ui_timing("telegram_proxy_ui.diag_panel.build", started_at)

    def _switch_tab(self, index: int):
        self._ensure_panel_built(index)
        self._stacked.setCurrentIndex(index)
        self._sync_log_timer()
        keys = ["settings", "logs", "diag"]
        if 0 <= index < len(keys):
            self._pivot.setCurrentItem(keys[index])

    def _sync_log_timer(self) -> None:
        if self._log_timer is None:
            return
        should_run = bool(self.isVisible() and self._stacked.currentIndex() == 1 and self._log_edit is not None)
        if should_run and not self._log_timer.isActive():
            self._log_timer.start(_LOG_REFRESH_MS)
        elif not should_run and self._log_timer.isActive():
            self._log_timer.stop()

    def _build_settings_panel(self, layout: QVBoxLayout):
        widgets = build_telegram_proxy_settings_panel(
            layout,
            content_parent=self.content,
            status_dot_cls=_StatusDot,
            on_toggle_proxy=self._on_toggle_proxy,
            on_open_in_telegram=self._on_open_in_telegram,
            on_copy_link=self._on_copy_link,
            on_open_zastogram=self._on_open_zastogram,
            on_generate_mtproxy_secret=self._on_generate_mtproxy_secret,
            on_copy_fake_tls_nginx_config=self._on_copy_fake_tls_nginx_config,
            on_open_advanced_settings=self._on_open_advanced_settings,
        )
        self._status_card = widgets.status_card
        self._status_dot = widgets.status_dot
        self._status_label = widgets.status_label
        self._btn_toggle = widgets.btn_toggle
        self._stats_label = widgets.stats_label
        self._setup_title_label = widgets.setup_title_label
        self._setup_open_btn = widgets.setup_open_btn
        self._setup_copy_btn = widgets.setup_copy_btn
        self._setup_zastogram_btn = widgets.setup_zastogram_btn
        self._settings_card = widgets.settings_card
        self._host_port_row = widgets.host_port_row
        self._host_edit = widgets.host_edit
        self._port_spin = widgets.port_spin
        self._proxy_mode_row = widgets.proxy_mode_row
        self._mtproxy_secret_row = widgets.mtproxy_secret_row
        self._mtproxy_secret_edit = widgets.mtproxy_secret_edit
        self._mtproxy_generate_btn = widgets.mtproxy_generate_btn
        self._fake_tls_domain_row = widgets.fake_tls_domain_row
        self._fake_tls_domain_edit = widgets.fake_tls_domain_edit
        self._fake_tls_nginx_btn = widgets.fake_tls_nginx_btn
        self._proxy_protocol_toggle = widgets.proxy_protocol_toggle
        self._auto_deeplink_toggle = widgets.auto_deeplink_toggle
        self._advanced_nav_row = widgets.advanced_nav_row
        self._advanced_nav_btn = widgets.advanced_nav_btn

    def _build_logs_panel(self, layout: QVBoxLayout):
        widgets = build_telegram_proxy_logs_panel(
            layout,
            push_button_cls=PushButton,
            on_copy_all_logs=self._on_copy_all_logs,
            on_open_log_file=self._on_open_log_file,
            on_clear_logs=self._on_clear_logs,
        )
        self._btn_copy_logs = widgets.btn_copy_logs
        self._btn_open_log_file = widgets.btn_open_log_file
        self._btn_clear_logs = widgets.btn_clear_logs
        self._log_edit = widgets.log_edit

    def _build_diag_panel(self, layout: QVBoxLayout):
        widgets = build_telegram_proxy_diag_panel(
            layout,
            caption_label_cls=CaptionLabel,
            primary_push_button_cls=PrimaryPushButton,
            push_button_cls=PushButton,
            on_run_diagnostics=self._on_run_diagnostics,
            on_copy_diag=self._on_copy_diag,
        )
        self._diag_desc_label = widgets.diag_desc_label
        self._btn_run_diag = widgets.btn_run_diag
        self._btn_copy_diag = widgets.btn_copy_diag
        self._diag_edit = widgets.diag_edit

    def _on_run_diagnostics(self):
        """Run network diagnostics in a background thread."""
        started = start_diagnostics(
            page=self,
            cleanup_in_progress=self._cleanup_in_progress,
            btn_run_diag=self._btn_run_diag,
            diag_edit=self._diag_edit,
            existing_poll_timer=self._diag_poll_timer,
            diag_runtime=self._diag_runtime,
            proxy_port=self._port_spin.value(),
            telegram_proxy_feature=self._telegram_proxy,
            publish_diag_result=self._publish_diag_result,
            set_diag_result=lambda value: setattr(self, "_diag_result", value),
            set_thread_done=lambda value: setattr(self, "_diag_thread_done", value),
            poll_diag_callback=self._poll_diag,
        )
        if started is None:
            return
        self._diag_poll_timer = started
        self._diag_proxy_port = self._port_spin.value()

    def _poll_diag(self):
        """Check if diag thread has new results."""
        poll_diagnostics(
            cleanup_in_progress=self._cleanup_in_progress,
            diag_poll_timer=self._diag_poll_timer,
            diag_result=self._diag_result,
            diag_thread_done=self._diag_thread_done,
            telegram_proxy_feature=self._telegram_proxy,
            update_diag=self._update_diag,
            finish_diag=self._diag_finished,
        )

    def _publish_diag_result(self, text: str) -> None:
        if self._cleanup_in_progress:
            return
        self._diag_result = text

    def _update_diag(self, text: str):
        self._diag_text_cache = str(text or "")
        self._diag_edit.setPlainText(text)
        sb = self._diag_edit.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def _diag_finished(self):
        finish_diagnostics(
            btn_run_diag=self._btn_run_diag,
            telegram_proxy_feature=self._telegram_proxy,
        )

    def _refresh_pivot_texts(self) -> None:
        refresh_pivot_texts(self._pivot)

    def _refresh_status_texts(self) -> None:
        mgr = self._proxy_manager()
        refresh_status_texts(
            manager=mgr,
            status_label=getattr(self, "_status_label", None),
            btn_toggle=getattr(self, "_btn_toggle", None),
            restarting=bool(getattr(self, "_restarting", False)),
            starting=bool(getattr(self, "_starting", False)),
        )

    def _apply_ui_texts(self) -> None:
        apply_ui_texts(
            refresh_pivot_texts_callback=self._refresh_pivot_texts,
            refresh_status_texts_callback=self._refresh_status_texts,
            settings_card=getattr(self, "_settings_card", None),
            setup_title_label=getattr(self, "_setup_title_label", None),
            host_port_row=getattr(self, "_host_port_row", None),
            mtproxy_secret_row=getattr(self, "_mtproxy_secret_row", None),
            fake_tls_domain_row=getattr(self, "_fake_tls_domain_row", None),
            advanced_nav_row=getattr(self, "_advanced_nav_row", None),
            diag_desc_label=getattr(self, "_diag_desc_label", None),
            setup_open_btn=getattr(self, "_setup_open_btn", None),
            setup_copy_btn=getattr(self, "_setup_copy_btn", None),
            fake_tls_nginx_btn=getattr(self, "_fake_tls_nginx_btn", None),
            btn_copy_logs=getattr(self, "_btn_copy_logs", None),
            btn_open_log_file=getattr(self, "_btn_open_log_file", None),
            btn_clear_logs=getattr(self, "_btn_clear_logs", None),
            btn_copy_diag=getattr(self, "_btn_copy_diag", None),
            btn_run_diag=getattr(self, "_btn_run_diag", None),
            host_edit=getattr(self, "_host_edit", None),
            mtproxy_secret_edit=getattr(self, "_mtproxy_secret_edit", None),
            fake_tls_domain_edit=getattr(self, "_fake_tls_domain_edit", None),
            log_edit=getattr(self, "_log_edit", None),
            diag_edit=getattr(self, "_diag_edit", None),
            auto_deeplink_toggle=getattr(self, "_auto_deeplink_toggle", None),
            proxy_mode_row=getattr(self, "_proxy_mode_row", None),
            proxy_protocol_toggle=getattr(self, "_proxy_protocol_toggle", None),
        )

    def set_ui_language(self, language: str) -> None:
        super().set_ui_language(language)
        self._apply_ui_texts()

    def _on_copy_diag(self):
        text = str(self.__dict__.get("_diag_text_cache", "") or "")
        plan = self._telegram_proxy.copy_text(
            text,
            success_title="Скопировано",
            success_content="Результат диагностики",
        )
        if plan.ok and InfoBar is not None:
            try:
                InfoBar.success(
                    title=plan.info_title,
                    content=plan.info_content,
                    parent=self,
                    duration=2000,
                    position=InfoBarPosition.TOP,
                )
            except Exception:
                pass

    def _connect_signals(self):
        mgr = self._proxy_manager()
        mgr.status_changed.connect(self._on_manager_status_changed)

        self._port_spin.valueChanged.connect(self._on_port_changed)
        self._host_edit.editingFinished.connect(self._on_host_changed)
        self._proxy_mode_row.currentIndexChanged.connect(self._on_proxy_mode_changed)
        self._mtproxy_secret_edit.editingFinished.connect(self._on_mtproxy_secret_changed)
        self._fake_tls_domain_edit.editingFinished.connect(self._on_fake_tls_domain_changed)
        self._proxy_protocol_toggle.toggled.connect(self._on_proxy_protocol_changed)
        self._auto_deeplink_toggle.toggled.connect(self._on_auto_deeplink_toggled)

        # Прокси мог уже работать (например, запущен из трея или при старте программы).
        self._on_manager_status_changed(bool(mgr.is_running))

    def _on_manager_status_changed(self, running: bool) -> None:
        self._on_status_changed(running)
        self._note_proxy_running_for_auto_deeplink(bool(running))

    def _apply_initial_settings_state(self, state: telegram_proxy_settings.TelegramProxySettingsState) -> None:
        started_at = time.perf_counter()
        self._port_spin.blockSignals(True)
        self._port_spin.setValue(state.port)
        self._port_spin.blockSignals(False)
        self._host_edit.setText(state.host)
        self._proxy_mode_row.setCurrentData(state.mode, block_signals=True)
        self._mtproxy_secret_edit.setText(state.mtproxy_secret)
        self._mtproxy_secret_edit.setCursorPosition(0)
        self._fake_tls_domain_edit.setText(state.fake_tls_domain)
        self._proxy_protocol_toggle.setChecked(state.proxy_protocol, block_signals=True)
        self._auto_deeplink_toggle.setChecked(state.auto_deeplink, block_signals=True)
        self._apply_mtproxy_rows_visibility()
        self._initial_state_applied = True
        self._log_ui_timing("telegram_proxy_ui.settings.apply", started_at)
        self._run_due_auto_deeplink()

    def _apply_mtproxy_rows_visibility(self) -> None:
        """Строки MTProxy видны только в режиме MTProxy."""
        is_mtproxy = self._local_proxy_mode() == "mtproxy"
        self._mtproxy_secret_row.setVisible(is_mtproxy)
        self._fake_tls_domain_row.setVisible(is_mtproxy)
        self._proxy_protocol_toggle.setVisible(is_mtproxy)
        enable_setting_card_group_auto_height(self._settings_card)

    def _on_open_advanced_settings(self) -> None:
        self._open_advanced_settings()

    # -- Авто-настройка Telegram --

    def _note_proxy_running_for_auto_deeplink(self, running: bool) -> None:
        was_running = self._auto_deeplink_last_running
        self._auto_deeplink_last_running = bool(running)
        if running and not was_running:
            self._auto_deeplink_due = True
            self._run_due_auto_deeplink()

    def _run_due_auto_deeplink(self) -> None:
        # Пока настройки не загружены, режим и secret на странице ещё не те,
        # и ссылка получилась бы неверной.
        if not self._auto_deeplink_due or not self._initial_state_applied:
            return
        self._auto_deeplink_due = False
        self._request_auto_deeplink_check()

    def _on_auto_deeplink_toggled(self, checked: bool) -> None:
        self._request_settings_save("auto_deeplink", enabled=bool(checked))

    def create_auto_deeplink_worker(self, request_id: int):
        return self._telegram_proxy.create_auto_deeplink_worker(request_id, parent=self)

    def _request_auto_deeplink_check(self) -> None:
        self._worker_state("_auto_deeplink_state", "_auto_deeplink_runtime").start_or_mark_pending(self._start_auto_deeplink_worker)

    def _start_auto_deeplink_worker(self) -> None:
        self._worker_state("_auto_deeplink_state", "_auto_deeplink_runtime").pending = False
        def bind_worker(worker) -> None:
            worker.completed.connect(self._on_auto_deeplink_checked)
            worker.failed.connect(self._on_auto_deeplink_failed)

        self._auto_deeplink_runtime.start_qthread_worker(
            worker_factory=self.create_auto_deeplink_worker,
            bind_worker=bind_worker,
            on_finished=self._on_auto_deeplink_worker_finished,
        )

    def _on_auto_deeplink_checked(self, request_id: int, should_open: bool) -> None:
        if not self._auto_deeplink_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        # Результат не отбрасываем даже при ждущей повторной проверке:
        # хранилище уже отметило ссылку открытой, повтор вернёт False.
        if not should_open:
            return
        QTimer.singleShot(2000, self._on_open_in_telegram)
        self._append_log_line("Открываем ссылку настройки прокси в Telegram...")

    def _on_auto_deeplink_failed(self, request_id: int, error: str) -> None:
        if not self._auto_deeplink_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        if self._worker_state("_auto_deeplink_state", "_auto_deeplink_runtime").pending:
            return
        log(f"Telegram Proxy auto deeplink check failed: {error}", "WARNING")

    def _on_auto_deeplink_worker_finished(self, _worker) -> None:
        if not self._is_current_worker_finish(self.__dict__.get("_auto_deeplink_runtime"), _worker):
            return
        self._worker_state("_auto_deeplink_state", "_auto_deeplink_runtime").schedule_after_finish(
            _worker,
            is_current_worker_finish=self._is_current_worker_finish,
            schedule_next=self._schedule_auto_deeplink_worker_start,
            cleanup_in_progress=self._cleanup_in_progress,
        )

    def _schedule_auto_deeplink_worker_start(self) -> None:
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        self._worker_state("_auto_deeplink_state", "_auto_deeplink_runtime").schedule_start(
            QTimer.singleShot,
            self._start_auto_deeplink_worker,
        )

    def _run_scheduled_auto_deeplink_worker_start(self) -> None:
        self._worker_state("_auto_deeplink_state", "_auto_deeplink_runtime").run_scheduled(
            self._start_auto_deeplink_worker,
            cleanup_in_progress=self._cleanup_in_progress,
        )

    # -- Log display (throttled via QTimer, no trimming) --

    def _flush_log_buffer(self):
        """Called every 500ms by QTimer. Drains new lines from ProxyLogger."""
        if self._log_edit is None:
            return
        mgr = self._proxy_manager()
        new_lines = mgr.proxy_logger.drain()
        if not new_lines:
            return
        self._append_log_text_cache(new_lines)

        self._log_edit.setUpdatesEnabled(False)
        try:
            for line in new_lines:
                self._log_edit.appendPlainText(line)
        finally:
            self._log_edit.setUpdatesEnabled(True)

        # Auto-scroll to bottom
        sb = self._log_edit.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def _append_log_text_cache(self, lines) -> None:
        normalized_lines = [str(line or "") for line in lines]
        if not normalized_lines:
            return
        chunk = "\n".join(normalized_lines)
        if self._log_text_cache:
            self._log_text_cache = f"{self._log_text_cache}\n{chunk}"
        else:
            self._log_text_cache = chunk
        self._log_text_line_count += len(normalized_lines)

    def _append_log_line(self, msg: str):
        """Append a single line to the log."""
        self._request_log_line_append(str(msg or ""))

    def create_log_line_worker(self, request_id: int, *, message: str):
        return self._telegram_proxy.create_log_line_worker(
            request_id,
            message=message,
            parent=self,
        )

    def _request_log_line_append(self, message: str) -> None:
        if not message:
            return
        state = self._queued_worker_state("_log_line_state", "_log_line_runtime")
        state.start_or_queue(message, self._start_log_line_worker, state.append)

    def _start_log_line_worker(self, message: str) -> None:
        if self._cleanup_in_progress:
            return

        def bind_worker(worker) -> None:
            worker.completed.connect(self._on_log_line_worker_completed)
            worker.failed.connect(self._on_log_line_worker_failed)

        self._log_line_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self.create_log_line_worker(
                request_id,
                message=message,
            ),
            bind_worker=bind_worker,
            on_finished=self._on_log_line_worker_finished,
        )

    def _on_log_line_worker_completed(self, _request_id: int) -> None:
        return

    def _on_log_line_worker_failed(self, request_id: int, error: str) -> None:
        if not self._log_line_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        if self._queued_worker_state("_log_line_state", "_log_line_runtime").has_pending():
            return
        log(f"Telegram Proxy log append failed: {error}", "WARNING")

    def _on_log_line_worker_finished(self, _worker) -> None:
        state = self._queued_worker_state("_log_line_state", "_log_line_runtime")
        state.schedule_next_after_finish(
            _worker,
            is_current_worker_finish=self._is_current_worker_finish,
            single_shot=QTimer.singleShot,
            start=self._start_log_line_worker,
            queue_item=state.append,
            is_cleanup_in_progress=lambda: self.__dict__.get("_cleanup_in_progress", False),
        )

    def _schedule_log_line_worker_start(self, message: str) -> None:
        queued = str(message or "")
        state = self._queued_worker_state("_log_line_state", "_log_line_runtime")
        state.schedule_start(
            queued,
            QTimer.singleShot,
            self._start_log_line_worker,
            queue_item=state.append,
            is_cleanup_in_progress=lambda: self.__dict__.get("_cleanup_in_progress", False),
        )

    def _run_scheduled_log_line_worker_start(self, message: str) -> None:
        self._queued_worker_state("_log_line_state", "_log_line_runtime").run_scheduled(
            str(message or ""),
            self._start_log_line_worker,
            lambda: self.__dict__.get("_cleanup_in_progress", False),
        )

    # -- Log tab buttons --

    def _on_copy_all_logs(self):
        if self._log_edit is None:
            return
        text = str(self.__dict__.get("_log_text_cache", "") or "")
        plan = self._telegram_proxy.copy_text(
            text,
            success_title="Скопировано",
            success_content=f"{int(self.__dict__.get('_log_text_line_count', 0) or 0)} строк",
        )
        if plan.ok and InfoBar is not None:
            try:
                InfoBar.success(
                    title=plan.info_title,
                    content=plan.info_content,
                    parent=self,
                    duration=2000,
                    position=InfoBarPosition.TOP,
                )
            except Exception:
                pass

    def _on_open_log_file(self):
        mgr = self._proxy_manager()
        path = mgr.proxy_logger.log_file_path
        self._start_open_log_file_worker(path)

    def create_open_log_file_worker(self, path: str):
        return self._telegram_proxy.create_open_log_file_worker(path=path, parent=self)

    @staticmethod
    def _mark_worker_request_id(worker, request_id: int):
        try:
            worker._request_id = int(request_id)
        except Exception:
            pass
        return worker

    def _start_open_log_file_worker(self, path: str) -> None:
        state = self._queued_worker_state("_open_log_file_state", "_open_log_file_runtime")
        if state.is_busy():
            self._queue_open_log_file_path(str(path or ""))
            return

        def bind_worker(worker) -> None:
            worker.completed.connect(self._on_open_log_file_finished)
            worker.failed.connect(self._on_open_log_file_failed)

        self._open_log_file_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self._mark_worker_request_id(
                self.create_open_log_file_worker(path),
                request_id,
            ),
            bind_worker=bind_worker,
            on_finished=self._on_open_log_file_worker_finished,
        )

    def _queue_open_log_file_path(self, path: str) -> None:
        queued = str(path or "")
        self._queued_worker_state("_open_log_file_state", "_open_log_file_runtime").append_unique(
            queued,
            key=lambda item: item,
        )

    def _on_open_log_file_finished(self, plan) -> None:
        if self._cleanup_in_progress:
            return
        log_line = str(getattr(plan, "log_line", "") or "")
        if log_line:
            self._append_log_line(log_line)

    def _on_open_log_file_failed(self, error: str) -> None:
        if self._cleanup_in_progress:
            return
        message = str(error or "").strip()
        if message:
            self._append_log_line(f"Failed to open log file: {message}")

    def _on_open_log_file_worker_finished(self, _worker) -> None:
        state = self._queued_worker_state("_open_log_file_state", "_open_log_file_runtime")
        state.schedule_next_after_finish(
            _worker,
            is_current_worker_finish=self._is_current_worker_finish,
            single_shot=QTimer.singleShot,
            start=self._start_open_log_file_worker,
            queue_item=lambda value: state.append_unique(value, key=lambda item: item),
            is_cleanup_in_progress=lambda: self.__dict__.get("_cleanup_in_progress", False),
        )

    def _schedule_open_log_file_worker_start(self, path: str) -> None:
        queued = str(path or "")
        state = self._queued_worker_state("_open_log_file_state", "_open_log_file_runtime")
        state.schedule_start(
            queued,
            QTimer.singleShot,
            self._start_open_log_file_worker,
            queue_item=lambda value: state.append_unique(value, key=lambda item: item),
            is_cleanup_in_progress=lambda: self.__dict__.get("_cleanup_in_progress", False),
        )

    def _run_scheduled_open_log_file_worker_start(self, path: str) -> None:
        self._queued_worker_state("_open_log_file_state", "_open_log_file_runtime").run_scheduled(
            str(path or ""),
            self._start_open_log_file_worker,
            lambda: self.__dict__.get("_cleanup_in_progress", False),
        )

    def _on_clear_logs(self):
        if self._log_edit is not None:
            self._log_edit.clear()
        self._log_text_cache = ""
        self._log_text_line_count = 0

    # -- Handlers --

    def _on_toggle_proxy(self):
        mgr = self._proxy_manager()
        handle_toggle_proxy(
            manager=mgr,
            restarting=bool(getattr(self, "_restarting", False)),
            starting=bool(getattr(self, "_starting", False)),
            # set_restarting(False) здесь = отмена рестарта кнопкой: цикл
            # прерывается, поэтому отложенный повтор рестарта тоже сбрасываем.
            set_restarting=lambda value: self._set_restarting_from_toggle(value),
            stop_proxy=self._stop_proxy,
            start_proxy=self._start_proxy,
            request_proxy_enabled_save=lambda value: self._request_settings_save(
                "proxy_enabled",
                enabled=bool(value),
            ),
        )

    def _set_restarting_from_toggle(self, value: bool) -> None:
        self._restarting = bool(value)
        if not value:
            self.__dict__.pop("_restart_again_pending", None)
            self._start_after_settings_flush = False

    def _restart_if_running(self):
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        self._worker_state("_restart_stop_state", "_restart_stop_runtime").start_or_mark_pending(self._start_restart_stop_worker)

    def _start_restart_stop_worker(self) -> None:
        mgr = self._proxy_manager()
        restart_proxy_if_running(
            page=self,
            manager=mgr,
            restarting=bool(getattr(self, "_restarting", False)),
            set_restarting=lambda value: setattr(self, "_restarting", value),
            status_label=self._status_label,
            create_stop_runtime_worker=self._telegram_proxy.create_stop_runtime_worker,
            on_finished=self._on_restart_stop_worker_finished,
        )

    @pyqtSlot()
    def _finish_restart(self):
        if self._cleanup_in_progress:
            return
        if not self._restarting:
            return
        self._restarting = False
        self._start_proxy()

    def _on_restart_stop_worker_finished(self, _worker) -> None:
        if not self._is_current_worker_finish(self.__dict__.get("_restart_stop_runtime"), _worker):
            return
        self._worker_state("_restart_stop_state", "_restart_stop_runtime").schedule_after_finish(
            _worker,
            is_current_worker_finish=self._is_current_worker_finish,
            schedule_next=self._schedule_restart_stop_worker_start,
            cleanup_in_progress=self._cleanup_in_progress,
        )

    def _schedule_restart_stop_worker_start(self) -> None:
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        self._worker_state("_restart_stop_state", "_restart_stop_runtime").schedule_start(
            QTimer.singleShot,
            self._restart_if_running,
        )

    def _run_scheduled_restart_stop_worker_start(self) -> None:
        self._worker_state("_restart_stop_state", "_restart_stop_runtime").run_scheduled(
            self._restart_if_running,
            cleanup_in_progress=self._cleanup_in_progress,
        )

    def _schedule_restart(self):
        """Debounced полный рестарт для SpinBox (pool_size/buffer_kb)."""
        self._restart_debounce_timer = schedule_upstream_restart(
            page=self,
            timer=self._restart_debounce_timer,
            restart_callback=self._restart_if_running,
            delay_ms=800,
        )

    def _schedule_upstream_apply(self):
        """Debounced горячая замена upstream для SpinBox (порт внешнего прокси)."""
        self._upstream_apply_debounce_timer = schedule_upstream_restart(
            page=self,
            timer=self._upstream_apply_debounce_timer,
            restart_callback=self._apply_upstream_hot_swap,
            delay_ms=800,
        )

    def _apply_upstream_hot_swap(self):
        """Горячая замена upstream-конфига без рестарта прокси.

        Если прокси не запущен — ничего не делаем: настройки подхватятся
        при следующем старте. При ошибке применения — fallback на рестарт.
        """
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        mgr = self._proxy_manager()
        if not mgr.is_running:
            return
        self._worker_state("_upstream_apply_state", "_upstream_apply_runtime").start_or_mark_pending(
            self._start_upstream_apply_worker
        )

    def _start_upstream_apply_worker(self) -> None:
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        self._worker_state("_upstream_apply_state", "_upstream_apply_runtime").pending = False
        mgr = self._proxy_manager()

        def bind_worker(worker) -> None:
            worker.completed.connect(self._on_upstream_apply_completed)

        self._upstream_apply_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self._telegram_proxy.create_upstream_apply_worker(
                manager=mgr,
                parent=self,
            ),
            bind_worker=bind_worker,
            on_finished=self._on_upstream_apply_worker_finished,
        )

    @pyqtSlot(bool)
    def _on_upstream_apply_completed(self, applied: bool) -> None:
        if self._cleanup_in_progress:
            return
        if not applied and self._proxy_manager().is_running:
            # Горячее применение не удалось — надёжный путь через рестарт.
            self._restart_if_running()

    def _on_upstream_apply_worker_finished(self, _worker) -> None:
        if not self._is_current_worker_finish(self.__dict__.get("_upstream_apply_runtime"), _worker):
            return
        self._worker_state("_upstream_apply_state", "_upstream_apply_runtime").schedule_after_finish(
            _worker,
            is_current_worker_finish=self._is_current_worker_finish,
            schedule_next=lambda: self._worker_state(
                "_upstream_apply_state", "_upstream_apply_runtime"
            ).schedule_start(QTimer.singleShot, self._start_upstream_apply_worker),
            cleanup_in_progress=self._cleanup_in_progress,
        )

    def _request_settings_save(self, action: str, **kwargs) -> None:
        self._telegram_proxy.request_settings_save(action, **kwargs)

    def _on_settings_flushed(self, restart: str) -> None:
        """Очередь сохранений опустела: применяем настройки и запускаем отложенный старт."""
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        self._dispatch_pending_restart(restart)
        if self._start_after_settings_flush:
            self._start_after_settings_flush = False
            self._request_proxy_start()

    def _dispatch_pending_restart(self, restart: str) -> None:
        """Выполнить итоговое действие применения настроек."""
        if restart == "schedule":
            self._schedule_restart()
        elif restart == "upstream_schedule":
            self._schedule_upstream_apply()
        elif restart == "upstream":
            self._apply_upstream_hot_swap()
        elif restart == "now":
            self._restart_if_running()

    @pyqtSlot()
    def _start_proxy(self):
        self._request_proxy_start()

    def _request_proxy_start(self) -> None:
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        if self._telegram_proxy.has_pending_settings_saves():
            # Worker запуска читает настройки из хранилища. Пока очередь
            # сохранений не пуста, там ещё старые значения — ждём её.
            self._start_after_settings_flush = True
            return
        self._worker_state("_proxy_start_state", "_proxy_start_runtime").start_or_mark_pending(self._start_proxy_worker)

    def _start_proxy_worker(self) -> None:
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        self._worker_state("_proxy_start_state", "_proxy_start_runtime").pending = False
        mgr = self._proxy_manager()
        start_proxy_runtime(
            page=self,
            manager=mgr,
            starting=bool(getattr(self, "_starting", False)),
            running=bool(mgr.is_running),
            set_starting=lambda value: setattr(self, "_starting", value),
            btn_toggle=self._btn_toggle,
            status_label=self._status_label,
            create_start_worker=self._telegram_proxy.create_start_worker,
            on_finished=self._on_proxy_start_worker_finished,
        )

    @pyqtSlot()
    def _finish_start(self):
        if self._cleanup_in_progress:
            return
        finish_proxy_start(
            start_ok=getattr(self, "_start_result", False),
            set_starting=lambda value: setattr(self, "_starting", value),
            btn_toggle=self._btn_toggle,
            check_relay_after_start=self._check_relay_after_start,
            on_status_changed=self._on_status_changed,
            request_proxy_enabled_save=lambda value: self._request_settings_save(
                "proxy_enabled",
                enabled=bool(value),
            ),
        )
        if not getattr(self, "_start_result", False):
            log("Telegram Proxy: не удалось запустить прокси после рестарта/старта", "WARNING")
        if self.__dict__.pop("_restart_again_pending", False) and getattr(self, "_start_result", False):
            QTimer.singleShot(0, self._restart_if_running)

    def _on_proxy_start_worker_finished(self, _worker) -> None:
        if not self._is_current_worker_finish(self.__dict__.get("_proxy_start_runtime"), _worker):
            return
        self._worker_state("_proxy_start_state", "_proxy_start_runtime").schedule_after_finish(
            _worker,
            is_current_worker_finish=self._is_current_worker_finish,
            schedule_next=self._schedule_proxy_start_worker_start,
            cleanup_in_progress=self._cleanup_in_progress,
        )

    def _schedule_proxy_start_worker_start(self) -> None:
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        try:
            self._worker_state("_proxy_start_state", "_proxy_start_runtime").schedule_start(
                QTimer.singleShot,
                self._start_proxy_worker,
            )
        except Exception:
            self._run_scheduled_proxy_start_worker_start()

    def _run_scheduled_proxy_start_worker_start(self) -> None:
        self._worker_state("_proxy_start_state", "_proxy_start_runtime").run_scheduled(
            self._start_proxy_worker,
            cleanup_in_progress=self._cleanup_in_progress,
        )

    def _check_relay_after_start(self):
        if self._cleanup_in_progress:
            return
        self._worker_state("_relay_check_state", "_relay_check_runtime").start_or_mark_pending(self._start_relay_check_worker)

    def _start_relay_check_worker(self) -> None:
        self._worker_state("_relay_check_state", "_relay_check_runtime").pending = False
        mgr = self._proxy_manager()
        self._on_status_changed(bool(mgr.is_running))
        start_relay_check(
            page=self,
            manager=mgr,
            current_generation=getattr(self, "_relay_check_gen", 0),
            set_generation=lambda value: setattr(self, "_relay_check_gen", value),
            status_label=self._status_label,
            set_relay_diag=lambda value: setattr(self, "_relay_diag", value),
            get_zapret_running=self._get_zapret_running,
            log_warning=lambda text: log(text, "WARNING"),
            create_relay_check_worker=self._telegram_proxy.create_relay_check_worker,
            on_finished=self._on_relay_check_worker_finished,
        )

    def _on_relay_check_worker_finished(self, _worker) -> None:
        if not self._is_current_worker_finish(self.__dict__.get("_relay_check_runtime"), _worker):
            return
        self._worker_state("_relay_check_state", "_relay_check_runtime").schedule_after_finish(
            _worker,
            is_current_worker_finish=self._is_current_worker_finish,
            schedule_next=self._schedule_relay_check_worker_start,
            cleanup_in_progress=self._cleanup_in_progress,
        )

    def _schedule_relay_check_worker_start(self) -> None:
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        try:
            self._worker_state("_relay_check_state", "_relay_check_runtime").schedule_start(
                QTimer.singleShot,
                self._start_relay_check_worker,
            )
        except Exception:
            self._run_scheduled_relay_check_worker_start()

    def _run_scheduled_relay_check_worker_start(self) -> None:
        self._worker_state("_relay_check_state", "_relay_check_runtime").run_scheduled(
            self._start_relay_check_worker,
            cleanup_in_progress=self._cleanup_in_progress,
        )

    @pyqtSlot()
    def _apply_relay_result(self):
        if self._cleanup_in_progress:
            return
        mgr = self._proxy_manager()
        apply_relay_result(
            manager=mgr,
            diag=getattr(self, "_relay_diag", {}),
            status_label=self._status_label,
            info_bar_cls=InfoBar,
            info_bar_position=InfoBarPosition,
            parent=self,
        )

    def _stop_proxy(self):
        self._request_proxy_stop()

    def _request_proxy_stop(self) -> None:
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        self._start_after_settings_flush = False
        self._worker_state("_proxy_stop_state", "_proxy_stop_runtime").start_or_mark_pending(self._start_proxy_stop_worker)

    def _start_proxy_stop_worker(self) -> None:
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        self._worker_state("_proxy_stop_state", "_proxy_stop_runtime").pending = False
        mgr = self._proxy_manager()
        if not bool(getattr(mgr, "is_running", False)):
            return
        stop_proxy_runtime(
            page=self,
            manager=mgr,
            create_stop_runtime_worker=self._telegram_proxy.create_stop_runtime_worker,
            on_finished=self._on_proxy_stop_worker_finished,
        )

    @pyqtSlot()
    def _finish_stop_proxy(self):
        if self._cleanup_in_progress:
            return
        self._request_settings_save("proxy_enabled", enabled=False)

    def _on_proxy_stop_worker_finished(self, _worker) -> None:
        if not self._is_current_worker_finish(self.__dict__.get("_proxy_stop_runtime"), _worker):
            return
        self._worker_state("_proxy_stop_state", "_proxy_stop_runtime").schedule_after_finish(
            _worker,
            is_current_worker_finish=self._is_current_worker_finish,
            schedule_next=self._schedule_proxy_stop_worker_start,
            cleanup_in_progress=self._cleanup_in_progress,
        )

    def _schedule_proxy_stop_worker_start(self) -> None:
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        try:
            self._worker_state("_proxy_stop_state", "_proxy_stop_runtime").schedule_start(
                QTimer.singleShot,
                self._start_proxy_stop_worker,
            )
        except Exception:
            self._run_scheduled_proxy_stop_worker_start()

    def _run_scheduled_proxy_stop_worker_start(self) -> None:
        self._worker_state("_proxy_stop_state", "_proxy_stop_runtime").run_scheduled(
            self._start_proxy_stop_worker,
            cleanup_in_progress=self._cleanup_in_progress,
        )

    def _on_status_changed(self, running: bool):
        mgr = self._proxy_manager()
        apply_status_changed(
            manager=mgr,
            running=bool(running),
            restarting=bool(getattr(self, "_restarting", False)),
            starting=bool(getattr(self, "_starting", False)),
            status_dot=self._status_dot,
            stats_label=self._stats_label,
            status_label=self._status_label,
            btn_toggle=self._btn_toggle,
            port_spin=self._port_spin,
            host_edit=self._host_edit,
            relay_check_gen=getattr(self, "_relay_check_gen", 0),
            set_speed_state=lambda prev_sent, prev_recv, up, down: (
                setattr(self, "_prev_bytes_sent", prev_sent),
                setattr(self, "_prev_bytes_received", prev_recv),
                setattr(self, "_speed_hist_up", up),
                setattr(self, "_speed_hist_down", down),
            ),
            set_generation=lambda value: setattr(self, "_relay_check_gen", value),
        )
        if self._stats_timer is not None:
            if running and self.isVisible() and not self._stats_timer.isActive():
                self._stats_timer.start(2000)
            elif not running:
                self._stats_timer.stop()

    def _emit_stats_if_visible(self):
        if self._cleanup_in_progress or not self.isVisible():
            return
        mgr = self._proxy_manager()
        if not mgr.is_running:
            if self._stats_timer is not None:
                self._stats_timer.stop()
            return
        self._apply_stats(mgr.stats)

    def _apply_stats(self, stats):
        if stats is None:
            return
        apply_stats_updated(
            stats=stats,
            prev_sent=getattr(self, '_prev_bytes_sent', 0),
            prev_recv=getattr(self, '_prev_bytes_received', 0),
            speed_hist_up=tuple(getattr(self, '_speed_hist_up', ()) or ()),
            speed_hist_down=tuple(getattr(self, '_speed_hist_down', ()) or ()),
            stats_label=self._stats_label,
            set_speed_state=lambda prev_sent, prev_recv, up, down: (
                setattr(self, "_prev_bytes_sent", prev_sent),
                setattr(self, "_prev_bytes_received", prev_recv),
                setattr(self, "_speed_hist_up", up),
                setattr(self, "_speed_hist_down", down),
            ),
        )

    def _on_port_changed(self, port: int):
        normalized = telegram_proxy_settings.normalize_port(port)
        if normalized != port:
            self._port_spin.blockSignals(True)
            self._port_spin.setValue(normalized)
            self._port_spin.blockSignals(False)
        self._request_settings_save("port", port=normalized)

    def _on_host_changed(self):
        host = telegram_proxy_settings.normalize_host(self._host_edit.text().strip())
        self._host_edit.setText(host)
        self._request_settings_save("host", host=host)

    def _local_proxy_mode(self) -> str:
        row = getattr(self, "_proxy_mode_row", None)
        if row is None:
            return "socks5"
        try:
            return telegram_proxy_settings.normalize_proxy_mode(row.currentData())
        except Exception:
            return "socks5"

    def _local_mtproxy_secret(self) -> str:
        return telegram_proxy_settings.normalize_secret(self._mtproxy_secret_edit.text())

    def _ensure_mtproxy_secret_if_needed(self) -> str:
        if self._local_proxy_mode() != "mtproxy":
            return ""
        secret = self._local_mtproxy_secret()
        if secret:
            return secret
        secret = telegram_proxy_settings.generate_mtproxy_secret()
        self._mtproxy_secret_edit.setText(secret)
        self._request_settings_save("mtproxy_secret", value=secret, restart="now")
        return secret

    def _local_fake_tls_domain(self) -> str:
        return telegram_proxy_settings.normalize_fake_tls_domain(self._fake_tls_domain_edit.text())

    def _on_proxy_mode_changed(self, _index: int):
        mode = self._local_proxy_mode()
        self._apply_mtproxy_rows_visibility()
        if mode == "mtproxy":
            self._ensure_mtproxy_secret_if_needed()
        self._request_settings_save("proxy_mode", value=mode, restart="now")

    def _on_generate_mtproxy_secret(self):
        secret = telegram_proxy_settings.generate_mtproxy_secret()
        self._mtproxy_secret_edit.setText(secret)
        self._request_settings_save("mtproxy_secret", value=secret, restart="now")

    def _on_mtproxy_secret_changed(self):
        secret = telegram_proxy_settings.normalize_secret(self._mtproxy_secret_edit.text())
        self._mtproxy_secret_edit.setText(secret)
        self._request_settings_save("mtproxy_secret", value=secret, restart="now")

    def _on_fake_tls_domain_changed(self):
        domain = telegram_proxy_settings.normalize_fake_tls_domain(self._fake_tls_domain_edit.text())
        self._fake_tls_domain_edit.setText(domain)
        self._request_settings_save("fake_tls_domain", value=domain, restart="now")

    def _on_proxy_protocol_changed(self, checked: bool):
        self._request_settings_save("proxy_protocol", enabled=bool(checked), restart="now")

    def _on_copy_fake_tls_nginx_config(self):
        text = self._telegram_proxy.get_fake_tls_nginx_config(
            fake_tls_domain=self._local_fake_tls_domain(),
            upstream_host=self._host_edit.text().strip(),
            upstream_port=self._port_spin.value(),
        )
        plan = self._telegram_proxy.copy_text(
            text,
            success_title="Скопировано",
            success_content="Конфиг Nginx для Fake TLS",
        )
        if plan.ok:
            self._show_success_message(plan.info_title, plan.info_content)

    def _show_success_message(self, title: str, content: str) -> None:
        try:
            InfoBar.success(
                title=str(title or ""),
                content=str(content or ""),
                parent=self,
                duration=2500,
                position=InfoBarPosition.TOP,
            )
        except Exception:
            pass

    def _on_open_in_telegram(self):
        """Open Telegram deep link to auto-configure Telegram."""
        url = telegram_proxy_settings.build_proxy_url(
            self._host_edit.text().strip(),
            self._port_spin.value(),
            mode=self._local_proxy_mode(),
            mtproxy_secret=self._ensure_mtproxy_secret_if_needed(),
            fake_tls_domain=self._local_fake_tls_domain(),
        )
        self._start_external_link_worker(
            url,
            success_log=f"Opened deep link: {url}",
            error_prefix="Failed to open link",
        )

    def _on_open_zastogram(self):
        """Open the ZaStoGram Desktop Forgejo page in a browser."""
        self._start_external_link_worker(
            _ZASTOGRAM_URL,
            success_log=f"Opened ZaStoGram page: {_ZASTOGRAM_URL}",
            error_prefix="Failed to open ZaStoGram page",
        )

    def create_external_link_worker(self, *, url: str, success_log: str, error_prefix: str):
        return self._telegram_proxy.create_external_link_worker(
            url=url,
            success_log=success_log,
            error_prefix=error_prefix,
            parent=self,
        )

    def _start_external_link_worker(self, url: str, *, success_log: str, error_prefix: str) -> None:
        state = self._queued_worker_state("_external_link_state", "_external_link_runtime")
        if state.is_busy():
            self._queue_external_link(
                {
                    "url": str(url or ""),
                    "success_log": str(success_log or ""),
                    "error_prefix": str(error_prefix or ""),
                }
            )
            return

        def bind_worker(worker) -> None:
            worker.completed.connect(self._on_external_link_finished)
            worker.failed.connect(self._on_external_link_failed)

        self._external_link_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self._mark_worker_request_id(
                self.create_external_link_worker(
                    url=url,
                    success_log=success_log,
                    error_prefix=error_prefix,
                ),
                request_id,
            ),
            bind_worker=bind_worker,
            on_finished=self._on_external_link_worker_finished,
        )

    def _queue_external_link(self, payload: dict[str, str]) -> None:
        queued = {
            "url": str((payload or {}).get("url") or ""),
            "success_log": str((payload or {}).get("success_log") or ""),
            "error_prefix": str((payload or {}).get("error_prefix") or ""),
        }
        self._queued_worker_state("_external_link_state", "_external_link_runtime").append_unique(
            queued,
            key=lambda item: str(item.get("url") or ""),
        )

    def _on_external_link_finished(self, plan) -> None:
        if self._cleanup_in_progress:
            return
        log_line = str(getattr(plan, "log_line", "") or "")
        if log_line:
            self._append_log_line(log_line)

    def _on_external_link_failed(self, error: str) -> None:
        if self._cleanup_in_progress:
            return
        message = str(error or "").strip()
        if message:
            self._append_log_line(f"Failed to open link: {message}")

    def _on_external_link_worker_finished(self, _worker) -> None:
        state = self._queued_worker_state("_external_link_state", "_external_link_runtime")
        state.schedule_next_after_finish(
            _worker,
            is_current_worker_finish=self._is_current_worker_finish,
            single_shot=QTimer.singleShot,
            start=self._run_external_link_payload,
            queue_item=self._queue_external_link,
            is_cleanup_in_progress=lambda: self.__dict__.get("_cleanup_in_progress", False),
        )

    def _schedule_external_link_worker_start(self, payload: dict) -> None:
        queued = {
            "url": str((payload or {}).get("url") or ""),
            "success_log": str((payload or {}).get("success_log") or ""),
            "error_prefix": str((payload or {}).get("error_prefix") or ""),
        }
        state = self._queued_worker_state("_external_link_state", "_external_link_runtime")
        state.schedule_start(
            queued,
            QTimer.singleShot,
            self._run_external_link_payload,
            queue_item=self._queue_external_link,
            is_cleanup_in_progress=lambda: self.__dict__.get("_cleanup_in_progress", False),
        )

    def _run_scheduled_external_link_worker_start(self, payload: dict) -> None:
        self._queued_worker_state("_external_link_state", "_external_link_runtime").run_scheduled(
            dict(payload or {}),
            self._run_external_link_payload,
            lambda: self.__dict__.get("_cleanup_in_progress", False),
        )

    def _run_external_link_payload(self, payload: dict) -> None:
        self._start_external_link_worker(
            str((payload or {}).get("url") or ""),
            success_log=str((payload or {}).get("success_log") or ""),
            error_prefix=str((payload or {}).get("error_prefix") or ""),
        )

    def _on_copy_link(self):
        """Copy proxy deep link to clipboard."""
        url = telegram_proxy_settings.build_proxy_url(
            self._host_edit.text().strip(),
            self._port_spin.value(),
            mode=self._local_proxy_mode(),
            mtproxy_secret=self._ensure_mtproxy_secret_if_needed(),
            fake_tls_domain=self._local_fake_tls_domain(),
        )
        plan = self._telegram_proxy.copy_text(
            url,
            success_title="Скопировано",
            success_content=url,
            success_log=f"Copied to clipboard: {url}",
        )
        if plan.log_line:
            self._append_log_line(plan.log_line)
        if plan.ok and InfoBar is not None:
            try:
                InfoBar.success(
                    title=plan.info_title,
                    content=plan.info_content,
                    parent=self,
                    duration=2000,
                    position=InfoBarPosition.TOP,
                )
            except Exception:
                pass

    def _ensure_telegram_hosts(self):
        """Проверяет и добавляет Telegram-записи в hosts через worker."""
        self._worker_state("_ensure_hosts_state", "_ensure_hosts_runtime").start_or_mark_pending(self._start_ensure_hosts_worker)

    def _start_ensure_hosts_worker(self) -> None:
        self._worker_state("_ensure_hosts_state", "_ensure_hosts_runtime").pending = False
        def bind_worker(worker) -> None:
            worker.completed.connect(self._on_telegram_hosts_ensured)

        self._ensure_hosts_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self._telegram_proxy.create_ensure_hosts_worker(
                request_id,
                parent=self,
            ),
            bind_worker=bind_worker,
            on_finished=self._on_ensure_hosts_worker_finished,
        )

    def _on_telegram_hosts_ensured(self, request_id: int, plan):
        if not self._ensure_hosts_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        if plan is None:
            return
        if not plan.ok and plan.log_line:
            log(plan.log_line, "WARNING")

    def _on_ensure_hosts_worker_finished(self, _worker) -> None:
        if not self._is_current_worker_finish(self.__dict__.get("_ensure_hosts_runtime"), _worker):
            return
        self._worker_state("_ensure_hosts_state", "_ensure_hosts_runtime").schedule_after_finish(
            _worker,
            is_current_worker_finish=self._is_current_worker_finish,
            schedule_next=self._schedule_ensure_hosts_worker_start,
            cleanup_in_progress=self._cleanup_in_progress,
        )

    def _is_current_worker_finish(self, runtime, worker) -> bool:
        if self.__dict__.get("_cleanup_in_progress", False):
            return False
        request_id = getattr(worker, "_request_id", None)
        if request_id is None:
            current_worker = getattr(runtime, "worker", None)
            if current_worker is not None:
                return worker is current_worker
            return False
        try:
            return int(request_id) == int(getattr(runtime, "request_id", -1))
        except (TypeError, ValueError):
            return False

    def _schedule_ensure_hosts_worker_start(self) -> None:
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        self._worker_state("_ensure_hosts_state", "_ensure_hosts_runtime").schedule_start(
            QTimer.singleShot,
            self._start_ensure_hosts_worker,
        )

    def _run_scheduled_ensure_hosts_worker_start(self) -> None:
        self._worker_state("_ensure_hosts_state", "_ensure_hosts_runtime").run_scheduled(
            self._start_ensure_hosts_worker,
            cleanup_in_progress=self._cleanup_in_progress,
        )

    def showEvent(self, event):
        started_at = time.perf_counter()
        super().showEvent(event)
        self._sync_log_timer()
        if self._stats_timer is not None and self._proxy_manager().is_running and not self._stats_timer.isActive():
            self._stats_timer.start(2000)
            self._emit_stats_if_visible()
        self._log_ui_timing("telegram_proxy_ui.show_event.total", started_at)

    @staticmethod
    def _log_ui_timing(label: str, started_at: float) -> None:
        log_ui_timing_since("ui", "telegram_proxy", label, started_at)

    def hideEvent(self, event):
        if self._log_timer is not None:
            self._log_timer.stop()
        if self._stats_timer is not None:
            self._stats_timer.stop()
        super().hideEvent(event)

    def cleanup(self):
        """Called on app exit."""
        self._cleanup_in_progress = True
        self._relay_check_gen = getattr(self, '_relay_check_gen', 0) + 1
        if self._log_timer is not None:
            self._log_timer.stop()
        if self._stats_timer is not None:
            self._stats_timer.stop()
            self._stats_timer.deleteLater()
            self._stats_timer = None
        if self._diag_poll_timer is not None:
            self._diag_poll_timer.stop()
            self._diag_poll_timer.deleteLater()
            self._diag_poll_timer = None
        self._diag_runtime.stop(
            blocking=False,
            log_fn=log,
            warning_prefix="telegram proxy diagnostics worker",
        )
        self._diag_runtime.cancel()
        self._ensure_hosts_runtime.stop(
            blocking=False,
            log_fn=log,
            warning_prefix="telegram proxy hosts worker",
        )
        self._ensure_hosts_runtime.cancel()
        self._worker_state("_ensure_hosts_state", "_ensure_hosts_runtime").reset()
        self._initial_state_runtime.stop(
            blocking=False,
            log_fn=log,
            warning_prefix="telegram proxy initial state worker",
        )
        self._initial_state_runtime.cancel()
        self._auto_deeplink_runtime.stop(
            blocking=False,
            log_fn=log,
            warning_prefix="telegram proxy auto deeplink worker",
        )
        self._auto_deeplink_runtime.cancel()
        self._worker_state("_auto_deeplink_state", "_auto_deeplink_runtime").reset()
        self._log_line_runtime.stop(
            blocking=False,
            log_fn=log,
            warning_prefix="telegram proxy log line worker",
        )
        self._log_line_runtime.cancel()
        self._open_log_file_runtime.stop(
            blocking=False,
            log_fn=log,
            warning_prefix="telegram proxy open log file worker",
        )
        self._open_log_file_runtime.cancel()
        self._queued_worker_state("_open_log_file_state", "_open_log_file_runtime").reset()
        self._external_link_runtime.stop(
            blocking=False,
            log_fn=log,
            warning_prefix="telegram proxy external link worker",
        )
        self._external_link_runtime.cancel()
        self._queued_worker_state("_external_link_state", "_external_link_runtime").reset()
        self._upstream_apply_runtime.stop(
            blocking=False,
            log_fn=log,
            warning_prefix="telegram proxy upstream apply worker",
        )
        self._upstream_apply_runtime.cancel()
        self._worker_state("_upstream_apply_state", "_upstream_apply_runtime").reset()
        self._proxy_start_runtime.stop(
            blocking=False,
            log_fn=log,
            warning_prefix="telegram proxy start worker",
        )
        self._proxy_start_runtime.cancel()
        self._worker_state("_proxy_start_state", "_proxy_start_runtime").reset()
        self._proxy_stop_runtime.stop(
            blocking=False,
            log_fn=log,
            warning_prefix="telegram proxy stop worker",
        )
        self._proxy_stop_runtime.cancel()
        self._worker_state("_proxy_stop_state", "_proxy_stop_runtime").reset()
        self._restart_stop_runtime.stop(
            blocking=False,
            log_fn=log,
            warning_prefix="telegram proxy restart stop worker",
        )
        self._restart_stop_runtime.cancel()
        self._worker_state("_restart_stop_state", "_restart_stop_runtime").reset()
        self._relay_check_runtime.stop(
            blocking=False,
            log_fn=log,
            warning_prefix="telegram proxy relay check worker",
        )
        self._relay_check_runtime.cancel()
        self._worker_state("_relay_check_state", "_relay_check_runtime").reset()
        for _timer_attr in ("_restart_debounce_timer", "_upstream_apply_debounce_timer"):
            timer = getattr(self, _timer_attr, None)
            if timer is not None:
                timer.stop()
                timer.deleteLater()
                setattr(self, _timer_attr, None)
        self._queued_worker_state("_log_line_state", "_log_line_runtime").reset()
        self._telegram_proxy.remove_settings_flushed_listener(self._on_settings_flushed)
        self._start_after_settings_flush = False
        self.__dict__.pop("_restart_again_pending", None)
        mgr = self._proxy_manager()
        mgr.cleanup()
