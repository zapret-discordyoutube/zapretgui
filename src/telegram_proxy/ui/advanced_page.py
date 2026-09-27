"""Вложенная страница «Продвинутые настройки» Telegram Proxy.

Здесь редко нужные параметры: внешний прокси, Cloudflare и сеть.
Страница только показывает значения из хранилища и просит фасад
сохранить изменения. Применение настроек (перезапуск или горячую замену
внешнего прокси) делает основная страница, когда очередь сохранений опустеет.
"""

from __future__ import annotations

from qfluentwidgets import BreadcrumbBar, InfoBar, InfoBarPosition

import telegram_proxy.config.settings as telegram_proxy_settings
from log.log import log
from telegram_proxy.ui.advanced_build import build_telegram_proxy_advanced_panel
from telegram_proxy.ui.runtime_helpers import apply_upstream_preset_ui, apply_upstream_runtime_state
from telegram_proxy.ui.text_plan import TELEGRAM_PROXY_SETTINGS_TEXT
from telegram_proxy.ui.upstream_workflow import handle_upstream_preset_changed, handle_upstream_toggle
from ui.accessibility import set_breadcrumb_accessibility
from ui.fluent_widgets import enable_setting_card_group_auto_height
from ui.one_shot_worker_runtime import OneShotWorkerRuntime
from ui.pages.base_page import BasePage


_BREADCRUMB_ROOT = "Telegram Proxy"


class TelegramProxyAdvancedPage(BasePage):
    """Продвинутые настройки Telegram Proxy."""

    def __init__(self, parent=None, *, telegram_proxy_feature, open_telegram_proxy):
        super().__init__(
            TELEGRAM_PROXY_SETTINGS_TEXT.advanced_nav_title,
            TELEGRAM_PROXY_SETTINGS_TEXT.advanced_nav_description,
            parent,
        )
        self._telegram_proxy = telegram_proxy_feature
        self._open_telegram_proxy = open_telegram_proxy
        self._cleanup_in_progress = False
        self._state_runtime = OneShotWorkerRuntime()
        self._cloudflare_check_runtime = OneShotWorkerRuntime()
        self._cloudflare_check_kind = ""
        self._external_link_runtime = OneShotWorkerRuntime()
        # Перечитать настройки, когда очередь сохранений опустеет.
        self._reload_after_flush = False
        self._upstream_catalog = telegram_proxy_settings.UpstreamCatalog()
        self._upstream_catalog_loaded = False
        self._upstream_runtime_state = None
        self._current_mtproxy_preset_id = ""
        self._breadcrumb = None
        self._build_content()
        self._connect_signals()
        self._telegram_proxy.add_settings_flushed_listener(self._on_settings_flushed)

    # -- Построение --

    def _build_content(self) -> None:
        if self.title_label is not None:
            self.title_label.hide()
        if self.subtitle_label is not None:
            self.subtitle_label.hide()

        self._breadcrumb = BreadcrumbBar(self)
        self._breadcrumb.currentItemChanged.connect(self._on_breadcrumb_item_changed)
        self.layout.addWidget(self._breadcrumb)
        self._rebuild_breadcrumb()

        widgets = build_telegram_proxy_advanced_panel(
            self.layout,
            content_parent=self.content,
            upstream_catalog=self._upstream_catalog,
            on_open_mtproxy=self._on_open_mtproxy,
            on_test_cloudflare=self._on_test_cloudflare,
            on_copy_cloudflare_dns=self._on_copy_cloudflare_dns,
            on_test_cloudflare_worker=self._on_test_cloudflare_worker,
            on_copy_cloudflare_worker_code=self._on_copy_cloudflare_worker_code,
        )
        self._widgets = widgets
        self._upstream_card = widgets.upstream_card
        self._upstream_toggle = widgets.upstream_toggle
        self._upstream_preset_row = widgets.upstream_preset_row
        self._upstream_address_row = widgets.upstream_address_row
        self._upstream_host_edit = widgets.upstream_host_edit
        self._upstream_port_spin = widgets.upstream_port_spin
        self._upstream_user_row = widgets.upstream_user_row
        self._upstream_user_edit = widgets.upstream_user_edit
        self._upstream_pass_row = widgets.upstream_pass_row
        self._upstream_pass_edit = widgets.upstream_pass_edit
        self._mtproxy_action_card = widgets.mtproxy_action_card
        self._upstream_mode_toggle = widgets.upstream_mode_toggle
        self._upstream_udp_toggle = widgets.upstream_udp_toggle
        self._cloudflare_card = widgets.cloudflare_card
        self._cloudflare_toggle = widgets.cloudflare_toggle
        self._cloudflare_domains_row = widgets.cloudflare_domains_row
        self._cloudflare_domains_edit = widgets.cloudflare_domains_edit
        self._cloudflare_test_btn = widgets.cloudflare_test_btn
        self._cloudflare_dns_btn = widgets.cloudflare_dns_btn
        self._cloudflare_worker_toggle = widgets.cloudflare_worker_toggle
        self._cloudflare_worker_domains_row = widgets.cloudflare_worker_domains_row
        self._cloudflare_worker_domains_edit = widgets.cloudflare_worker_domains_edit
        self._cloudflare_worker_test_btn = widgets.cloudflare_worker_test_btn
        self._cloudflare_worker_code_btn = widgets.cloudflare_worker_code_btn
        self._dc_ip_edit = widgets.dc_ip_edit
        self._pool_size_spin = widgets.pool_size_spin
        self._buffer_kb_spin = widgets.buffer_kb_spin
        self._refresh_upstream_preset_description()

    def _connect_signals(self) -> None:
        self._upstream_toggle.toggled.connect(self._on_upstream_changed)
        self._upstream_preset_row.currentIndexChanged.connect(self._on_upstream_preset_changed)
        self._upstream_host_edit.editingFinished.connect(self._on_manual_upstream_edited)
        self._upstream_port_spin.valueChanged.connect(self._on_upstream_port_changed)
        self._upstream_user_edit.editingFinished.connect(self._on_manual_upstream_edited)
        self._upstream_pass_edit.editingFinished.connect(self._on_manual_upstream_edited)
        self._upstream_mode_toggle.toggled.connect(self._on_upstream_mode_changed)
        self._upstream_udp_toggle.toggled.connect(self._on_upstream_udp_changed)
        self._cloudflare_toggle.toggled.connect(self._on_cloudflare_changed)
        self._cloudflare_domains_edit.editingFinished.connect(self._on_cloudflare_domains_changed)
        self._cloudflare_worker_toggle.toggled.connect(self._on_cloudflare_worker_changed)
        self._cloudflare_worker_domains_edit.editingFinished.connect(self._on_cloudflare_worker_domains_changed)
        self._dc_ip_edit.editingFinished.connect(self._on_dc_ip_changed)
        self._pool_size_spin.valueChanged.connect(self._on_pool_size_changed)
        self._buffer_kb_spin.valueChanged.connect(self._on_buffer_kb_changed)
        self._telegram_proxy.get_proxy_manager().upstream_state_changed.connect(self._on_upstream_state_changed)

    def _rebuild_breadcrumb(self) -> None:
        breadcrumb = self._breadcrumb
        if breadcrumb is None:
            return
        items = (_BREADCRUMB_ROOT, TELEGRAM_PROXY_SETTINGS_TEXT.advanced_nav_title)
        breadcrumb.blockSignals(True)
        try:
            breadcrumb.clear()
            breadcrumb.addItem("telegram_proxy", items[0])
            breadcrumb.addItem("advanced", items[1])
            set_breadcrumb_accessibility(breadcrumb, items)
        finally:
            breadcrumb.blockSignals(False)

    def _on_breadcrumb_item_changed(self, key: str) -> None:
        # Клик по крошке уже удалил элементы правее выбранного: восстанавливаем
        # полный путь, иначе при возврате сюда крошки останутся обрезанными.
        self._rebuild_breadcrumb()
        if key == "telegram_proxy":
            self._open_telegram_proxy()

    # -- Загрузка настроек --

    def on_page_activated(self) -> None:
        self._on_upstream_state_changed(self._telegram_proxy.get_proxy_manager().upstream_state)
        self._request_state_reload()

    def _request_state_reload(self) -> None:
        if self._cleanup_in_progress:
            return
        if self._telegram_proxy.has_pending_settings_saves():
            # В хранилище ещё старые значения: перечитаем после сохранения.
            self._reload_after_flush = True
            return
        self._reload_after_flush = False

        def bind_worker(worker) -> None:
            worker.completed.connect(self._on_state_loaded)
            worker.failed.connect(self._on_state_failed)

        self._state_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self._telegram_proxy.create_page_initial_state_worker(
                request_id,
                parent=self,
            ),
            bind_worker=bind_worker,
        )

    def _on_settings_flushed(self, _restart: str) -> None:
        if self._cleanup_in_progress or not self._reload_after_flush:
            return
        self._request_state_reload()

    def _on_state_loaded(self, request_id: int, initial_state) -> None:
        if not self._state_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        if self._telegram_proxy.has_pending_settings_saves():
            # Пока грузили, пользователь что-то поменял: этот снимок уже устарел.
            self._reload_after_flush = True
            return
        self._apply_upstream_catalog(initial_state.upstream_catalog)
        self._apply_settings_state(initial_state.settings)

    def _on_state_failed(self, request_id: int, error: str) -> None:
        if not self._state_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        log(f"Не удалось загрузить продвинутые настройки Telegram Proxy: {error}", "WARNING")

    def _apply_upstream_catalog(self, upstream_catalog) -> None:
        if upstream_catalog is None:
            return
        new_items = [text for text, _data in upstream_catalog.items()]
        old_items = [text for text, _data in self._upstream_catalog.items()]
        self._upstream_catalog = upstream_catalog
        self._upstream_catalog_loaded = True
        if new_items == old_items and self._upstream_preset_row.combo.count() == len(new_items):
            return
        combo = self._upstream_preset_row.combo
        combo.blockSignals(True)
        try:
            combo.clear()
            for text, data in upstream_catalog.items():
                combo.addItem(text, userData=data)
        finally:
            combo.blockSignals(False)
        self._upstream_preset_row.refresh_accessibility()

    def _apply_settings_state(self, state: telegram_proxy_settings.TelegramProxySettingsState) -> None:
        self._upstream_toggle.setChecked(state.upstream_enabled, block_signals=True)
        self._upstream_host_edit.setText(state.upstream_host)
        self._upstream_port_spin.blockSignals(True)
        self._upstream_port_spin.setValue(state.upstream_port)
        self._upstream_port_spin.blockSignals(False)
        self._upstream_user_edit.setText(state.upstream_user)
        self._upstream_pass_edit.setText(state.upstream_password)
        if self._upstream_catalog.choices:
            target_index = min(max(int(state.upstream_preset_index), 0), len(self._upstream_catalog.choices) - 1)
            self._upstream_preset_row.setCurrentIndex(target_index, block_signals=True)
        self._upstream_mode_toggle.setChecked(state.upstream_mode == "always", block_signals=True)
        self._upstream_udp_toggle.setChecked(state.upstream_udp_enabled, block_signals=True)

        self._cloudflare_toggle.setChecked(state.cloudflare_enabled, block_signals=True)
        self._cloudflare_domains_edit.setText(", ".join(state.cloudflare_domains))
        self._cloudflare_worker_toggle.setChecked(state.cloudflare_worker_enabled, block_signals=True)
        self._cloudflare_worker_domains_edit.setText(", ".join(state.cloudflare_worker_domains))

        self._dc_ip_edit.setText(", ".join(state.dc_ip))
        self._pool_size_spin.blockSignals(True)
        self._pool_size_spin.setValue(state.pool_size)
        self._pool_size_spin.blockSignals(False)
        self._buffer_kb_spin.blockSignals(True)
        self._buffer_kb_spin.setValue(state.buffer_kb)
        self._buffer_kb_spin.blockSignals(False)

        self._apply_upstream_preset_ui(self._upstream_preset_row.combo.currentIndex())
        self._apply_cloudflare_rows_visibility()

    # -- Внешний прокси --

    def _apply_upstream_preset_ui(self, index: int) -> None:
        self._current_mtproxy_preset_id = apply_upstream_preset_ui(
            upstream_toggle=self._upstream_toggle,
            upstream_catalog=self._upstream_catalog,
            upstream_preset_row=self._upstream_preset_row,
            upstream_manual_rows=(self._upstream_address_row, self._upstream_user_row, self._upstream_pass_row),
            mtproxy_action_card=self._mtproxy_action_card,
            upstream_mode_toggle=self._upstream_mode_toggle,
            upstream_udp_toggle=self._upstream_udp_toggle,
            index=index,
        )
        self._refresh_upstream_preset_description()
        enable_setting_card_group_auto_height(self._upstream_card)

    def _refresh_upstream_preset_description(self) -> None:
        """Описание строки «Сервер»: какой сервер реально работает или подсказка."""
        text = TELEGRAM_PROXY_SETTINGS_TEXT
        if self._upstream_catalog_loaded and not self._upstream_catalog.has_bundled_presets():
            apply_upstream_runtime_state(self._upstream_preset_row, None, text.upstream_catalog_missing)
            return
        state = self._upstream_runtime_state
        if (
            self._current_mtproxy_preset_id
            or not self._upstream_toggle.isChecked()
            or not self._telegram_proxy.get_proxy_manager().is_running
        ):
            # Прокси остановлен или выбор другой: прошлый снимок уже не про то,
            # что работает сейчас.
            state = None
        apply_upstream_runtime_state(self._upstream_preset_row, state, text.upstream_preset_description)

    def _on_upstream_state_changed(self, state) -> None:
        if self._cleanup_in_progress or state is None:
            return
        self._upstream_runtime_state = state
        self._refresh_upstream_preset_description()

    def _on_upstream_changed(self, checked: bool) -> None:
        # Пользователь поменял выбор: старый снимок сервера больше не актуален.
        self._upstream_runtime_state = None
        handle_upstream_toggle(
            checked=checked,
            request_upstream_enabled=lambda value: self._request_settings_save(
                "upstream_enabled",
                enabled=bool(value),
                restart="upstream",
            ),
            apply_upstream_preset_ui=self._apply_upstream_preset_ui,
            current_index=self._upstream_preset_row.combo.currentIndex(),
            upstream_catalog=self._upstream_catalog,
            request_upstream_preset_save=lambda preset_id: self._request_settings_save(
                "upstream_preset",
                preset_id=preset_id,
                restart="upstream",
            ),
        )

    def _on_upstream_preset_changed(self, index: int) -> None:
        self._upstream_runtime_state = None
        handle_upstream_preset_changed(
            index=index,
            upstream_catalog=self._upstream_catalog,
            apply_upstream_preset_ui=self._apply_upstream_preset_ui,
            upstream_host_edit=self._upstream_host_edit,
            upstream_port_spin=self._upstream_port_spin,
            upstream_user_edit=self._upstream_user_edit,
            upstream_pass_edit=self._upstream_pass_edit,
            request_upstream_preset_save=lambda preset_id: self._request_settings_save(
                "upstream_preset",
                preset_id=preset_id,
                restart="upstream",
            ),
            request_manual_upstream_save=lambda host, port, user, password: self._request_settings_save(
                "manual_upstream",
                host=host,
                port=port,
                user=user,
                password=password,
                restart="upstream",
            ),
        )

    def _request_manual_upstream_save(self, *, port: int, restart: str) -> None:
        self._request_settings_save(
            "manual_upstream",
            host=self._upstream_host_edit.text(),
            port=int(port),
            user=self._upstream_user_edit.text(),
            password=self._upstream_pass_edit.text(),
            restart=restart,
        )

    def _on_manual_upstream_edited(self) -> None:
        self._request_manual_upstream_save(port=self._upstream_port_spin.value(), restart="upstream")

    def _on_upstream_port_changed(self, port: int) -> None:
        self._request_manual_upstream_save(port=port, restart="upstream_schedule")

    def _on_upstream_mode_changed(self, checked: bool) -> None:
        self._request_settings_save("upstream_mode", enabled=bool(checked), restart="upstream")

    def _on_upstream_udp_changed(self, checked: bool) -> None:
        self._request_settings_save("upstream_udp_enabled", enabled=bool(checked), restart="upstream")

    def _on_open_mtproxy(self) -> None:
        link = telegram_proxy_settings.get_upstream_mtproxy_link(self._current_mtproxy_preset_id)
        if not link or self._external_link_runtime.is_running():
            return

        def bind_worker(worker) -> None:
            worker.completed.connect(self._on_external_link_finished)
            worker.failed.connect(self._on_external_link_failed)

        self._external_link_runtime.start_qthread_worker(
            worker_factory=lambda _request_id: self._telegram_proxy.create_external_link_worker(
                url=link,
                success_log="",
                error_prefix="Не удалось открыть ссылку MTProxy",
                parent=self,
            ),
            bind_worker=bind_worker,
        )

    def _on_external_link_finished(self, plan) -> None:
        if self._cleanup_in_progress or bool(getattr(plan, "ok", False)):
            return
        self._show_message("Ссылка не открылась", str(getattr(plan, "log_line", "") or ""), success=False)

    def _on_external_link_failed(self, error: str) -> None:
        if self._cleanup_in_progress:
            return
        self._show_message("Ссылка не открылась", str(error or ""), success=False)

    # -- Cloudflare --

    def _apply_cloudflare_rows_visibility(self) -> None:
        self._cloudflare_domains_row.setVisible(self._cloudflare_toggle.isChecked())
        self._cloudflare_worker_domains_row.setVisible(self._cloudflare_worker_toggle.isChecked())
        enable_setting_card_group_auto_height(self._cloudflare_card)

    @staticmethod
    def _normalized_domains_text(edit) -> str:
        domains = ", ".join(telegram_proxy_settings.normalize_domain_list(edit.text()))
        edit.setText(domains)
        return domains

    def _on_cloudflare_changed(self, checked: bool) -> None:
        self._apply_cloudflare_rows_visibility()
        self._request_settings_save("cloudflare_enabled", enabled=bool(checked), restart="now")

    def _on_cloudflare_domains_changed(self) -> None:
        self._request_settings_save(
            "cloudflare_domains",
            value=self._normalized_domains_text(self._cloudflare_domains_edit),
            restart="now",
        )

    def _on_cloudflare_worker_changed(self, checked: bool) -> None:
        self._apply_cloudflare_rows_visibility()
        self._request_settings_save("cloudflare_worker_enabled", enabled=bool(checked), restart="now")

    def _on_cloudflare_worker_domains_changed(self) -> None:
        self._request_settings_save(
            "cloudflare_worker_domains",
            value=self._normalized_domains_text(self._cloudflare_worker_domains_edit),
            restart="now",
        )

    def _on_test_cloudflare(self) -> None:
        self._start_cloudflare_check("domain", self._normalized_domains_text(self._cloudflare_domains_edit))

    def _on_test_cloudflare_worker(self) -> None:
        domains = self._normalized_domains_text(self._cloudflare_worker_domains_edit)
        if not domains:
            self._show_message(
                "Worker не указан",
                "Введите домен Cloudflare Worker, например name.workers.dev.",
                success=False,
            )
            return
        self._start_cloudflare_check("worker", domains)

    def _on_copy_cloudflare_dns(self) -> None:
        self._copy_to_clipboard(self._telegram_proxy.get_cloudflare_dns_records_text(), "DNS-записи Cloudflare")

    def _on_copy_cloudflare_worker_code(self) -> None:
        self._copy_to_clipboard(self._telegram_proxy.get_cloudflare_worker_code(), "Код Cloudflare Worker")

    def _copy_to_clipboard(self, text: str, what: str) -> None:
        plan = self._telegram_proxy.copy_text(text, success_title="Скопировано", success_content=what)
        if plan.ok:
            self._show_message(plan.info_title, plan.info_content, success=True)

    def _start_cloudflare_check(self, kind: str, domains: str) -> None:
        if self._cleanup_in_progress:
            return
        if self._cloudflare_check_runtime.is_running():
            self._show_message(
                "Проверка уже идёт",
                "Дождитесь результата текущей проверки Cloudflare.",
                success=False,
            )
            return
        self._cloudflare_check_kind = str(kind or "domain").strip().lower()
        self._set_cloudflare_check_running(True)

        def bind_worker(worker) -> None:
            worker.completed.connect(self._on_cloudflare_check_finished)
            worker.failed.connect(self._on_cloudflare_check_failed)

        self._cloudflare_check_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self._telegram_proxy.create_cloudflare_check_worker(
                request_id,
                kind=self._cloudflare_check_kind,
                domains=domains,
                parent=self,
            ),
            bind_worker=bind_worker,
            on_finished=self._on_cloudflare_check_worker_finished,
        )

    def _on_cloudflare_check_finished(self, request_id: int, result) -> None:
        if not self._cloudflare_check_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        summary = result.summary() if hasattr(result, "summary") else str(result or "")
        if bool(getattr(result, "ok", False)):
            self._show_message("Cloudflare отвечает", summary, success=True)
        else:
            self._show_message("Cloudflare не отвечает", summary, success=False)

    def _on_cloudflare_check_failed(self, request_id: int, error: str) -> None:
        if not self._cloudflare_check_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        message = str(error or "").strip() or "Не удалось проверить Cloudflare."
        self._show_message("Ошибка проверки", message, success=False)

    def _on_cloudflare_check_worker_finished(self, _worker) -> None:
        if self._cleanup_in_progress:
            return
        self._set_cloudflare_check_running(False)

    def _set_cloudflare_check_running(self, running: bool) -> None:
        for button in (self._cloudflare_test_btn, self._cloudflare_worker_test_btn):
            button.setEnabled(not running)
            button.setText("Проверить")
        if not running:
            return
        if self._cloudflare_check_kind == "worker":
            self._cloudflare_worker_test_btn.setText("Проверяем...")
        else:
            self._cloudflare_test_btn.setText("Проверяем...")

    # -- Сеть --

    def _on_dc_ip_changed(self) -> None:
        overrides = telegram_proxy_settings.parse_dc_endpoint_overrides(self._dc_ip_edit.text())
        text = ", ".join(f"{dc}:{ip}" for dc, ip in overrides.items())
        self._dc_ip_edit.setText(text)
        self._request_settings_save("dc_ip", value=text, restart="now")

    def _on_pool_size_changed(self, value: int) -> None:
        normalized = telegram_proxy_settings.normalize_pool_size(value)
        if normalized != value:
            self._pool_size_spin.blockSignals(True)
            self._pool_size_spin.setValue(normalized)
            self._pool_size_spin.blockSignals(False)
        self._request_settings_save("pool_size", value=normalized, restart="schedule")

    def _on_buffer_kb_changed(self, value: int) -> None:
        normalized = telegram_proxy_settings.normalize_buffer_kb(value)
        if normalized != value:
            self._buffer_kb_spin.blockSignals(True)
            self._buffer_kb_spin.setValue(normalized)
            self._buffer_kb_spin.blockSignals(False)
        self._request_settings_save("buffer_kb", value=normalized, restart="schedule")

    # -- Общее --

    def _request_settings_save(self, action: str, **kwargs) -> None:
        self._telegram_proxy.request_settings_save(action, **kwargs)

    def _show_message(self, title: str, content: str, *, success: bool) -> None:
        show = InfoBar.success if success else InfoBar.warning
        try:
            show(
                title=str(title or ""),
                content=str(content or ""),
                parent=self,
                duration=2500 if success else 3500,
                position=InfoBarPosition.TOP,
            )
        except Exception:
            pass

    def set_ui_language(self, language: str) -> None:
        super().set_ui_language(language)
        self._rebuild_breadcrumb()

    def cleanup(self) -> None:
        self._cleanup_in_progress = True
        self._reload_after_flush = False
        self._telegram_proxy.remove_settings_flushed_listener(self._on_settings_flushed)
        try:
            self._telegram_proxy.get_proxy_manager().upstream_state_changed.disconnect(self._on_upstream_state_changed)
        except (TypeError, RuntimeError):
            pass
        for runtime, name in (
            (self._state_runtime, "state"),
            (self._cloudflare_check_runtime, "cloudflare check"),
            (self._external_link_runtime, "external link"),
        ):
            runtime.stop(blocking=False, log_fn=log, warning_prefix=f"telegram proxy advanced {name} worker")
            runtime.cancel()
