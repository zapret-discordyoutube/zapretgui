# updater/ui/page.py
"""Страница мониторинга серверов обновлений"""

import time

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QHBoxLayout,
)

from ui.pages.base_page import BasePage
from ui.fluent_widgets import SettingsCard
from ui.theme import get_theme_tokens
from app.ui_texts import tr as tr_catalog
from log.log import log
from updater.page_actions import AutoCheckSetting, ChannelOpener
from updater.server_status_table_state import ServerStatusTableState
from updater.ui.main_build import (
    build_servers_header_widgets,
    build_servers_table_widget,
)
from updater.ui.language import apply_servers_page_language
from updater.ui.table_view import (
    refresh_server_rows,
    reset_server_rows as reset_servers_table_rows,
    upsert_server_status as upsert_server_table_status,
)
from updater.ui.settings_build import (
    build_servers_settings_section,
    build_servers_telegram_section,
)
from ui.latest_value_worker_state import LatestValueWorkerState
from ui.one_shot_worker_runtime import OneShotWorkerRuntime
from ui.widgets.win11_controls import Win11ToggleRow
from qfluentwidgets import (
    CaptionLabel, PushSettingCard, SettingCardGroup, InfoBar,
)

from config.build_info import APP_VERSION, CHANNEL

from updater.ui.update_card import UpdateStatusCard
from updater.ui.changelog_card import ChangelogCard



class ServersPage(BasePage):
    """Страница «Серверы»: статус обновления, таблица источников, установка.

    Страница только показывает. Проверкой владеет ``UpdateCheckService``,
    установкой — ``UpdateInstallService``, общий итог проверки — координатор
    ``UpdaterFeature``: его видят и страница, и проверка при запуске.
    """

    def __init__(
        self,
        parent=None,
        *,
        updater_feature,
        check_service,
        install_service,
        open_about,
        create_changelog_link_open_worker,
    ):
        super().__init__(
            "Серверы",
            "Мониторинг серверов обновлений",
            parent,
            title_key="page.servers.title",
            subtitle_key="page.servers.subtitle",
        )

        self._tokens = get_theme_tokens()
        self._server_table_state = ServerStatusTableState()
        self._updater_feature = updater_feature
        self._check_service = check_service
        self._install_service = install_service
        self._auto_check = AutoCheckSetting(updater_feature=updater_feature, parent=self)
        self._channel_opener = ChannelOpener(updater_feature=updater_feature, parent=self)
        self._create_changelog_link_open_worker = create_changelog_link_open_worker
        self._open_about = open_about
        self._idle_view_applied = False
        self._cleanup_in_progress = False
        self._auto_check_enabled = False
        self._found_version = ""
        self._found_notes = ""
        self._found_source = ""
        self._changelog_link_open_runtime = OneShotWorkerRuntime()
        self._changelog_link_open_runtime_worker = None
        self._changelog_link_open_state = LatestValueWorkerState(
            self._changelog_link_open_runtime,
            empty_value=None,
        )

        self._build_ui()
        self._apply_page_theme(force=True)
        self._connect_services()
        self._unsubscribe_check = self._updater_feature.subscribe_update_check(
            self._apply_check_snapshot,
            emit_initial=True,
        )
        self._auto_check.load()

    def _tr(self, key: str, default: str) -> str:
        return tr_catalog(key, language=self._ui_language, default=default)

    def _connect_services(self) -> None:
        self._check_service.server_status.connect(self._on_server_status)
        self._install_service.stage_changed.connect(self.changelog_card.set_download_status_text)
        self._install_service.progress.connect(self.changelog_card.update_progress)
        self._install_service.downloaded.connect(self.changelog_card.download_complete)
        self._install_service.failed.connect(self._on_install_failed)
        self._auto_check.loaded.connect(self._on_auto_check_loaded)
        self._channel_opener.failed.connect(self.show_update_channel_open_error)

    # ── Проверка ────────────────────────────────────────────────────────

    def _request_check_updates(self) -> None:
        if self._install_service.is_busy:
            return
        if not self._check_service.start(language=self._ui_language):
            return
        # Строки новой проверки приходят очередью Qt уже после этого места.
        self.changelog_card.hide()
        self.reset_server_rows()

    def _on_server_status(self, server_name: str, status: dict) -> None:
        if self._cleanup_in_progress:
            return
        upsert_server_table_status(
            self.servers_table,
            table_state=self._server_table_state,
            server_name=server_name,
            status=status,
            channel=CHANNEL,
            language=self._ui_language,
            accent_hex=self._tokens.accent_hex,
        )

    def _apply_check_snapshot(self, snapshot) -> None:
        """Общий итог проверки: и ручной, и при запуске программы."""
        if self._cleanup_in_progress:
            return
        phase = str(getattr(snapshot, "phase", "") or "")
        if phase == "checking":
            self.update_card.start_checking()
            return
        if phase == "skipped":
            self._show_idle_hint(snapshot)
            return
        if phase == "error":
            message = str(getattr(snapshot, "error", "") or "").strip() or self._tr(
                "page.servers.update.error.check_failed",
                "Не удалось проверить обновления",
            )
            self.update_card.set_error(message)
            return
        if phase != "completed":
            return

        version = str(getattr(snapshot, "version", "") or "")
        if not bool(getattr(snapshot, "has_update", False)):
            self._found_version = ""
            self.update_card.stop_checking(False, version)
            return

        self._found_version = version
        self._found_notes = str(getattr(snapshot, "release_notes", "") or "")
        self._found_source = str(getattr(snapshot, "release_source", "") or "")
        if self._found_source:
            self.update_card.show_found_update(version, self._found_source)
        else:
            self.update_card.stop_checking(True, version)
        # Карточка установки видна при любом найденном обновлении — в том
        # числе если при запуске нажали «Позже».
        if not self._install_service.is_busy:
            self.changelog_card.show_update(self._found_version, self._found_notes)

    def _show_idle_hint(self, snapshot=None) -> None:
        if snapshot is None:
            snapshot = self._updater_feature.current_update_check_snapshot()
        phase = str(getattr(snapshot, "phase", "") or "")
        completed_at = float(getattr(snapshot, "completed_at", 0.0) or 0.0)
        if phase in {"completed", "skipped"} and completed_at > 0:
            self.update_card.show_checked_ago(max(time.time() - completed_at, 0.0))
        elif self._auto_check_enabled:
            self.update_card.show_auto_enabled_hint()
        else:
            self.update_card.show_manual_hint()

    def on_page_activated(self) -> None:
        if self._idle_view_applied:
            return
        self._idle_view_applied = True
        snapshot = self._updater_feature.current_update_check_snapshot()
        phase = str(getattr(snapshot, "phase", "") or "")
        if self._found_version or self._install_service.is_busy or phase in {"checking", "error"}:
            return
        self._show_idle_hint(snapshot)

    # ── Установка ───────────────────────────────────────────────────────

    def present_startup_update(self, version: str, release_notes: str, *, install_after_show: bool = True) -> bool:
        """Обновление, найденное при запуске и подтверждённое пользователем."""
        if self._cleanup_in_progress or self._install_service.is_busy or not version:
            return False
        self._found_version = str(version)
        self._found_notes = str(release_notes or "")
        self.changelog_card.show_update(self._found_version, self._found_notes)
        if install_after_show:
            self._request_install_update()
        return True

    def _request_install_update(self) -> None:
        if self._cleanup_in_progress or not self._found_version:
            return
        if self._check_service.is_busy or not self._install_service.start(self._found_version):
            return
        self.changelog_card.start_download(self._found_version)
        self.update_card.hide()
        self.update_card.set_check_enabled(False)

    def _on_install_failed(self, error: str) -> None:
        if self._cleanup_in_progress:
            return
        self.changelog_card.download_failed(error)
        self.update_card.show()
        self.update_card.show_download_error()
        self.update_card.set_check_enabled(True)

    def _request_dismiss_update(self) -> None:
        if not self._found_version:
            return
        log("Обновление отложено пользователем", "🔄 UPDATE")
        self.update_card.show_deferred(self._found_version)

    # ── Настройки и Telegram ────────────────────────────────────────────

    def _on_auto_check_loaded(self, enabled: bool) -> None:
        if self._cleanup_in_progress or self._auto_check.user_changed:
            return
        self._auto_check_enabled = bool(enabled)
        self._set_auto_check_toggle_checked(bool(enabled))
        if self._idle_view_applied and not self._found_version:
            snapshot = self._updater_feature.current_update_check_snapshot()
            if str(getattr(snapshot, "phase", "") or "") not in {"checking", "error"}:
                self._show_idle_hint(snapshot)

    def _on_auto_check_toggled(self, enabled: bool):
        self._auto_check_enabled = bool(enabled)
        self._auto_check.save(bool(enabled))
        if enabled:
            self.update_card.show_auto_enabled_hint()
        else:
            self.update_card.show_manual_hint()
        log(f"Автопроверка при запуске: {'включена' if enabled else 'отключена'}", "🔄 UPDATE")

    def _set_auto_check_toggle_checked(self, enabled: bool) -> None:
        toggle = getattr(self, "auto_check_toggle", None)
        if toggle is None:
            return
        try:
            toggle.setChecked(bool(enabled), block_signals=True)
        except TypeError:
            previous = toggle.blockSignals(True)
            try:
                toggle.setChecked(bool(enabled))
            finally:
                toggle.blockSignals(previous)

    def _open_telegram_channel(self):
        self._channel_opener.open(CHANNEL)

    def show_update_channel_open_error(self, error: str) -> None:
        InfoBar.warning(
            title=self._tr("page.servers.telegram.error.title", "Ошибка"),
            content=self._tr(
                "page.servers.telegram.error.open_channel",
                "Не удалось открыть Telegram канал:\n{error}",
            ).format(error=str(error or "")),
            parent=self.window(),
        )

    # ── Вид ─────────────────────────────────────────────────────────────

    def _apply_page_theme(self, tokens=None, force: bool = False) -> None:
        _ = force
        self._tokens = tokens or get_theme_tokens()
        tokens = self._tokens

        if hasattr(self, "servers_table"):
            try:
                accent_qcolor = QColor(tokens.accent_hex)
                for r in range(self.servers_table.rowCount()):
                    item = self.servers_table.item(r, 0)
                    if item and (item.text() or "").lstrip().startswith("⭐"):
                        item.setForeground(accent_qcolor)
            except Exception:
                pass

    def _refresh_server_rows(self) -> None:
        refresh_server_rows(
            self.servers_table,
            table_state=self._server_table_state,
            channel=CHANNEL,
            language=self._ui_language,
            accent_hex=self._tokens.accent_hex,
        )

    def reset_server_rows(self) -> None:
        reset_servers_table_rows(
            self.servers_table,
            table_state=self._server_table_state,
        )

    def set_ui_language(self, language: str) -> None:
        super().set_ui_language(language)
        apply_servers_page_language(
            tr_fn=self._tr,
            ui_language=self._ui_language,
            update_card=self.update_card,
            changelog_card=self.changelog_card,
            breadcrumb=self._breadcrumb,
            page_title_label=self._page_title_label,
            servers_title_label=self._servers_title_label,
            legend_active_label=self._legend_active_label,
            servers_table=self.servers_table,
            settings_card=self._settings_card,
            toggle_label=self._toggle_label,
            auto_check_card=getattr(self, "_auto_check_card", None),
            version_info_label=self._version_info_label,
            telegram_card=self._tg_card,
            telegram_info_label=self._tg_info_label,
            telegram_button=self._tg_btn,
            refresh_server_rows=self._refresh_server_rows,
        )

    def get_ui_language(self) -> str:
        return self._ui_language

    def _build_ui(self):
        # ── Custom header (back link + title) ───────────────────────────
        # Hide base title/subtitle and prevent _retranslate_base_texts
        # from re-showing them (it calls setVisible(bool(text))).
        if self.title_label is not None:
            self._title_key = None
            self.title_label.setText("")
            self.title_label.hide()
        if self.subtitle_label is not None:
            self._subtitle_key = None
            self.subtitle_label.setText("")
            self.subtitle_label.hide()

        header_widgets = build_servers_header_widgets(
            tr_fn=self._tr,
            parent=self,
            on_about_clicked=self._on_back_to_about,
        )
        self._breadcrumb = header_widgets.breadcrumb
        self._page_title_label = header_widgets.page_title_label
        self._servers_title_label = header_widgets.servers_title_label
        self._legend_active_label = header_widgets.legend_active_label

        self.add_widget(header_widgets.header_widget)

        # Update status card
        self.update_card = UpdateStatusCard(language=self._ui_language)
        self.update_card.check_clicked.connect(self._request_check_updates)
        self.add_widget(self.update_card)

        # Changelog card (hidden by default)
        self.changelog_card = ChangelogCard(
            language=self._ui_language,
            open_url=self._request_changelog_link_open,
        )
        self.changelog_card.install_clicked.connect(self._request_install_update)
        self.changelog_card.dismiss_clicked.connect(self._request_dismiss_update)
        self.add_widget(self.changelog_card)

        # Table header row
        self.add_widget(header_widgets.servers_header_widget)

        # Servers table
        self.servers_table = build_servers_table_widget(tr_fn=self._tr)
        self.add_widget(self.servers_table, stretch=1)

        # Settings card
        settings_widgets = build_servers_settings_section(
            content_parent=self.content,
            tr_fn=self._tr,
            accent_hex=get_theme_tokens().accent_hex,
            auto_check_enabled=self._auto_check_enabled,
            app_version=APP_VERSION,
            channel=CHANNEL,
            setting_card_group_cls=SettingCardGroup,
            settings_card_cls=SettingsCard,
            win11_toggle_row_cls=Win11ToggleRow,
            caption_label_cls=CaptionLabel,
            qhbox_layout_cls=QHBoxLayout,
            on_auto_check_toggled=self._on_auto_check_toggled,
        )
        self._settings_card = settings_widgets.card
        self._auto_check_card = settings_widgets.auto_check_card
        self.auto_check_toggle = settings_widgets.auto_check_toggle
        self._toggle_label = settings_widgets.toggle_label
        self._version_info_label = settings_widgets.version_info_label
        self.add_widget(self._settings_card)
        self.add_widget(self._version_info_label)

        # Telegram card
        telegram_widgets = build_servers_telegram_section(
            tr_fn=self._tr,
            accent_hex=self._tokens.accent_hex,
            push_setting_card_cls=PushSettingCard,
            on_open_channel=self._open_telegram_channel,
        )
        self._tg_card = telegram_widgets.card
        self._tg_info_label = telegram_widgets.info_label
        self._tg_btn = telegram_widgets.button
        self.add_widget(self._tg_card)

        self._apply_page_theme(force=True)

    def create_changelog_link_open_worker(self, request_id: int, *, url: str):
        return self._create_changelog_link_open_worker(
            request_id,
            url=url,
            parent=self,
        )

    def _request_changelog_link_open(self, url: str) -> None:
        target = str(url or "").strip()
        state = self._changelog_link_open_state_obj()
        if state.is_busy():
            state.pending = target
            return
        state.pending = None
        self._start_changelog_link_open_worker(target)

    def _start_changelog_link_open_worker(self, url: str) -> None:
        started = self._changelog_link_open_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self.create_changelog_link_open_worker(
                request_id,
                url=url,
            ),
            on_loaded=self._on_changelog_link_open_finished,
            on_failed=self._on_changelog_link_open_failed,
            on_finished=self._on_changelog_link_open_worker_finished,
        )
        worker = (
            started[1]
            if isinstance(started, tuple) and len(started) > 1
            else getattr(self._changelog_link_open_runtime, "worker", None)
        )
        self._changelog_link_open_runtime_worker = worker

    def _on_changelog_link_open_finished(self, request_id: int, result) -> None:
        if not self._changelog_link_open_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        if self._changelog_link_open_state_obj().has_pending():
            return
        if getattr(result, "ok", False):
            return
        self._show_changelog_link_open_error(str(getattr(result, "error", "") or ""))

    def _on_changelog_link_open_failed(self, request_id: int, error: str) -> None:
        if not self._changelog_link_open_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        if self._changelog_link_open_state_obj().has_pending():
            return
        self._show_changelog_link_open_error(str(error or ""))

    def _on_changelog_link_open_worker_finished(self, worker) -> None:
        current_worker = self.__dict__.get("_changelog_link_open_runtime_worker")
        if current_worker is not None and worker is not current_worker:
            return
        self._changelog_link_open_runtime_worker = None
        if self._changelog_link_open_state_obj().has_pending() and not self._cleanup_in_progress:
            self._schedule_changelog_link_open_worker_start()

    def _schedule_changelog_link_open_worker_start(self) -> None:
        self._changelog_link_open_state_obj().schedule_start(
            QTimer.singleShot,
            self._run_scheduled_changelog_link_open_worker_start,
            cleanup_in_progress=self.__dict__.get("_cleanup_in_progress", False),
        )

    def _run_scheduled_changelog_link_open_worker_start(self) -> None:
        pending = self._changelog_link_open_state_obj().take_pending_for_scheduled_start(
            cleanup_in_progress=self.__dict__.get("_cleanup_in_progress", False),
        )
        if pending is None:
            return
        self._start_changelog_link_open_worker(str(pending or "").strip())

    def _changelog_link_open_state_obj(self) -> LatestValueWorkerState:
        state = self.__dict__.get("_changelog_link_open_state")
        runtime = self.__dict__.get("_changelog_link_open_runtime")
        if state is None:
            pending = self.__dict__.pop("_changelog_link_open_pending", None)
            start_scheduled = bool(self.__dict__.pop("_changelog_link_open_start_scheduled", False))
            state = LatestValueWorkerState(
                runtime,
                empty_value=None,
                pending=None if pending is None else str(pending or ""),
                start_scheduled=start_scheduled,
            )
            self.__dict__["_changelog_link_open_state"] = state
        elif getattr(state, "runtime", None) is None and runtime is not None:
            state.runtime = runtime
        return state

    @property
    def _changelog_link_open_pending(self) -> str | None:
        pending = self._changelog_link_open_state_obj().pending
        return None if pending is None else str(pending or "")

    @_changelog_link_open_pending.setter
    def _changelog_link_open_pending(self, value: str | None) -> None:
        self._changelog_link_open_state_obj().pending = None if value is None else str(value or "")

    @property
    def _changelog_link_open_start_scheduled(self) -> bool:
        return bool(self._changelog_link_open_state_obj().start_scheduled)

    @_changelog_link_open_start_scheduled.setter
    def _changelog_link_open_start_scheduled(self, value: bool) -> None:
        self._changelog_link_open_state_obj().start_scheduled = bool(value)

    def _show_changelog_link_open_error(self, error: str) -> None:
        InfoBar.warning(
            title=self._tr("page.servers.telegram.error.title", "Ошибка"),
            content=self._tr(
                "page.servers.telegram.error.open_channel",
                "Не удалось открыть Telegram канал:\n{error}",
            ).format(error=str(error or "")),
            parent=self.window(),
        )

    def _stop_changelog_link_open_worker(self) -> None:
        self._changelog_link_open_state_obj().reset()
        self._changelog_link_open_runtime_worker = None
        self._changelog_link_open_runtime.stop(
            blocking=False,
            warning_prefix="Changelog link open worker",
        )
        self._changelog_link_open_runtime.cancel()

    def _on_back_to_about(self):
        try:
            self._open_about()
        except Exception:
            pass

    def cleanup(self):
        self._cleanup_in_progress = True
        self._stop_changelog_link_open_worker()
        unsubscribe, self._unsubscribe_check = self._unsubscribe_check, None
        if callable(unsubscribe):
            unsubscribe()
        self._check_service.shutdown()
        self._install_service.shutdown()
