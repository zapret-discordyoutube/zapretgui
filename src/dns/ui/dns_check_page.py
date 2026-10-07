# dns/ui/dns_check_page.py
"""Страница проверки DNS подмены провайдером."""

from PyQt6.QtCore import QTimer, pyqtSignal

from ui.pages.base_page import BasePage
from ui.latest_value_worker_state import LatestValueWorkerState
from ui.one_shot_worker_runtime import OneShotWorkerRuntime
import dns.dns_check_plans as dns_check_page_plans
from ui.fluent_widgets import SettingsCard, set_tooltip
from ui.theme import get_theme_tokens
from ui.theme_semantic import get_semantic_palette
from ui.accessibility import set_control_accessibility, set_state_text
from ui.widgets.log_report_view import LogReport
from app.ui_texts import tr as tr_catalog

from qfluentwidgets import (
    IndeterminateProgressBar,
    FluentIcon,
    InfoBar,
    PrimaryPushButton,
    PushButton,
    CaptionLabel,
)

from dns.ui.dns_check_widgets import DnsDomainsView, DnsSummaryPanel


class DNSCheckPage(BasePage):
    """Страница проверки DNS подмены провайдером."""

    # Просят показать отчёт страницей: её открывает страница-хозяин вкладки (LogReport).
    report_requested = pyqtSignal(object)

    def __init__(self, parent=None, *, dns_feature, embedded: bool = False, open_dns_settings=None):
        super().__init__(
            "Проверка DNS подмены",
            "Проверка резолвинга доменов YouTube и Discord через различные DNS серверы",
            parent,
            title_key="page.dns_check.title",
            subtitle_key="page.dns_check.subtitle",
        )
        self._dns = dns_feature
        self._open_dns_settings = open_dns_settings
        self._cleanup_in_progress = False
        self._check_runtime = OneShotWorkerRuntime()
        self._check_state = LatestValueWorkerState(
            self._check_runtime,
            empty_value=False,
        )
        self._save_runtime = OneShotWorkerRuntime()
        self._save_results_state = LatestValueWorkerState(
            self._save_runtime,
            empty_value=None,
        )
        self._results_plain_text_cache = ""
        self._status_tone = "muted"
        self._status_bold = False
        self._build_ui()
        if embedded:
            self.hide_page_header()
        self._apply_page_theme(force=True)

    def _apply_interaction_state(
        self,
        *,
        check_enabled: bool,
        save_enabled: bool,
        progress_visible: bool,
    ) -> None:
        self.check_button.setEnabled(check_enabled)
        self.save_button.setEnabled(save_enabled)
        self.progress_bar.setVisible(progress_visible)
        self._update_action_button_state_text()
        progress_state = "выполняется" if progress_visible else "не выполняется"
        set_state_text(self.progress_bar, f"Ход проверки DNS: {progress_state}")
        if progress_visible:
            self.progress_bar.start()
        else:
            self.progress_bar.stop()

    def _set_action_button_state_text(self, button, base_name: str) -> None:
        state = "доступно" if bool(button.isEnabled()) else "недоступно"
        set_state_text(button, f"{base_name}, {state}")

    def _update_action_button_state_text(self) -> None:
        self._set_action_button_state_text(self.check_button, "Начать полную проверку DNS")
        self._set_action_button_state_text(self.save_button, "Сохранить результаты проверки DNS")
        log_button = self.__dict__.get("log_button")
        if log_button is not None:
            self._set_action_button_state_text(log_button, "Открыть подробный лог проверки DNS")
    
    def _build_ui(self):
        """Создаёт интерфейс страницы.

        Страница живёт во вкладке BlockCheck, поэтому без шапок у карточек,
        карточки «Что проверяем» и подписи «Действия»: кнопки и статус — одна
        строка, под ней итог. Подробный лог открывается в отдельном окне.
        """
        # Итог с медоедом; кнопки стоят в нём же, под главной фразой, — как на вкладке «DNS-серверы».
        self.summary_panel = DnsSummaryPanel(on_open_dns_settings=self._open_dns_settings)
        # Имя осталось от отдельной карточки с кнопками: теперь они в панели итога.
        self.control_card = self.summary_panel
        row = self.summary_panel.actions

        self.check_button = PrimaryPushButton(
            tr_catalog("page.dns_check.button.start", language=self._ui_language, default="Начать проверку"),
            icon=FluentIcon.PLAY,
        )
        start_description = self._action_description(
            "page.dns_check.action.start.description",
            "Полностью проверить DNS-резолвинг через разные серверы и собрать расширенный отчёт.",
        )
        set_tooltip(self.check_button, start_description)
        set_control_accessibility(
            self.check_button,
            name="Начать полную проверку DNS",
            description=start_description,
        )
        self.check_button.clicked.connect(self.start_check)
        row.addWidget(self.check_button)


        self.status_label = CaptionLabel()
        self._set_status(
            tr_catalog(
                "page.dns_check.status.ready",
                language=self._ui_language,
                default="Сравниваем ответ DNS с эталоном и видим, подменяет ли провайдер адреса",
            ),
            tone="muted",
            bold=False,
        )

        self.log_button = PushButton(
            tr_catalog("page.dns_check.button.log", language=self._ui_language, default="Подробный лог"),
            icon=FluentIcon.DOCUMENT,
        )
        log_description = self._action_description(
            "page.dns_check.action.log.description",
            "Открыть текстовый отчёт проверки: какие адреса пришли и почему решено именно так.",
        )
        set_tooltip(self.log_button, log_description)
        set_control_accessibility(
            self.log_button,
            name="Открыть подробный лог проверки DNS",
            description=log_description,
        )
        self.log_button.setEnabled(False)
        self.log_button.clicked.connect(self._open_log)
        row.addWidget(self.log_button)

        self.save_button = PushButton(
            tr_catalog("page.dns_check.button.save", language=self._ui_language, default="Сохранить результаты"),
            icon=FluentIcon.SAVE,
        )
        save_description = self._action_description(
            "page.dns_check.action.save.description",
            "Сохранить текущий отчёт DNS-проверки в текстовый файл.",
        )
        set_tooltip(self.save_button, save_description)
        set_control_accessibility(
            self.save_button,
            name="Сохранить результаты проверки DNS",
            description=save_description,
        )
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save_results)
        row.addWidget(self.save_button)

        # start=False: иначе анимация крутится и у скрытой полосы, пока жива страница.
        self.progress_bar = IndeterminateProgressBar(self, start=False)
        self.progress_bar.setVisible(False)
        set_state_text(self.progress_bar, "Ход проверки DNS: не выполняется")
        # Кнопки стоят рядом, как на вкладке «DNS-серверы»; строка состояния — после них.
        row.addSpacing(8)
        row.addWidget(self.status_label, 1)
        self.summary_panel.progress_slot.addWidget(self.progress_bar)
        self._update_action_button_state_text()
        self.layout.addWidget(self.summary_panel)
        self.domains_card = SettingsCard()
        self.domains_view = DnsDomainsView()
        self.domains_card.add_widget(self.domains_view)
        self.domains_card.setVisible(False)
        self.layout.addWidget(self.domains_card)

        self.layout.addStretch()

    def _open_log(self) -> None:
        self.report_requested.emit(
            LogReport(
                title="Подробный лог проверки DNS",
                text=self._resolve_save_results_text(None),
                root_title="DNS подмена",
                empty_text="Проверка ещё не запускалась.",
                description="Текстовый отчёт проверки: какие адреса пришли и почему решено именно так.",
            )
        )

    def _set_log_available(self, available: bool) -> None:
        self.log_button.setEnabled(bool(available))
        self._update_action_button_state_text()

    def _set_status(self, text: str, *, tone: str, bold: bool) -> None:
        tokens = get_theme_tokens()
        semantic = get_semantic_palette()
        tone_map = {
            "muted": tokens.fg_muted,
            "accent": tokens.accent_hex,
            "success": semantic.success,
            "warning": semantic.warning,
            "error": semantic.error,
        }
        color = tone_map.get(tone, tokens.fg_muted)
        weight = "600" if bold else "400"
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"color: {color}; padding: 4px 0; font-weight: {weight};"
        )
        self._status_tone = tone
        self._status_bold = bold
        set_state_text(self.status_label, f"Статус проверки DNS: {self._clean_status_text(text)}")

    def _action_description(self, key: str, default: str) -> str:
        return tr_catalog(key, language=self._ui_language, default=default)

    def _clean_status_text(self, text: str) -> str:
        value = " ".join(str(text or "").strip().split())
        for prefix in ("⚡", "✅"):
            if value.startswith(prefix):
                value = value[len(prefix):].strip()
        return value

    def _apply_page_theme(self, tokens=None, force: bool = False) -> None:
        _ = force
        _ = tokens
        try:
            self._set_status(self.status_label.text(), tone=self._status_tone, bold=self._status_bold)
        except Exception:
            pass

    def start_check(self):
        """Начинает полную проверку DNS."""
        state = self._check_state_obj()
        if state.is_busy():
            state.pending = True
            return
        state.pending = False
        self._cleanup_in_progress = False
        
        self._clear_results_plain_text_cache()
        self._set_log_available(False)
        self.summary_panel.set_pending()
        self.domains_view.clear()
        self.domains_card.setVisible(False)
        start_plan = dns_check_page_plans.build_start_plan()
        self._apply_interaction_state(
            check_enabled=start_plan.check_enabled,
            save_enabled=start_plan.save_enabled,
            progress_visible=start_plan.progress_visible,
        )
        self._set_status(start_plan.status_text, tone=start_plan.status_tone, bold=False)

        self._check_runtime.start_qobject_worker(
            parent=self,
            worker_factory=lambda request_id: self._dns.create_dns_check_worker(request_id),
            on_finished=self._on_check_worker_finished,
            bind_worker=self._bind_check_worker,
        )

    def _bind_check_worker(self, worker) -> None:
        worker.update_signal.connect(self.append_result)
        worker.finished_signal.connect(self.on_check_finished)
    
    def append_result(self, text):
        """Добавляет текст в результаты с форматированием."""
        if self._cleanup_in_progress:
            return
        self._append_results_plain_text_cache(text)
        if not self.log_button.isEnabled():
            self._set_log_available(True)

    def on_check_finished(self, request_id: int, results):
        """Обработчик завершения проверки."""
        if not self._check_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        # Обновляем статус
        plan = dns_check_page_plans.build_finish_plan(results)
        self._apply_interaction_state(
            check_enabled=plan.check_enabled,
            save_enabled=plan.save_enabled,
            progress_visible=plan.progress_visible,
        )
        self._set_status(plan.status_text, tone=plan.status_tone, bold=True)
        self.summary_panel.show_results(results)
        self.domains_view.show_results(results)
        self.domains_card.setVisible(bool(self.domains_view.rows()))

    def _on_check_worker_finished(self, request_id: int, _thread) -> None:
        if not self._is_current_request_finish(self.__dict__.get("_check_runtime"), request_id):
            return
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        if self._check_state_obj().has_pending():
            self._schedule_full_dns_check_start()

    def _schedule_full_dns_check_start(self) -> None:
        self._check_state_obj().schedule_start(
            QTimer.singleShot,
            self._run_scheduled_full_dns_check_start,
            cleanup_in_progress=self.__dict__.get("_cleanup_in_progress", False),
        )

    def _run_scheduled_full_dns_check_start(self) -> None:
        pending = bool(
            self._check_state_obj().take_pending_for_scheduled_start(
                cleanup_in_progress=self.__dict__.get("_cleanup_in_progress", False),
            )
        )
        if not pending:
            return
        self.start_check()

    def _check_state_obj(self) -> LatestValueWorkerState:
        state = self.__dict__.get("_check_state")
        runtime = self.__dict__.get("_check_runtime")
        if state is None:
            pending = bool(self.__dict__.pop("_check_pending", False))
            start_scheduled = bool(self.__dict__.pop("_check_start_scheduled", False))
            state = LatestValueWorkerState(
                runtime,
                empty_value=False,
                pending=pending,
                start_scheduled=start_scheduled,
            )
            self.__dict__["_check_state"] = state
        elif getattr(state, "runtime", None) is None and runtime is not None:
            state.runtime = runtime
        return state

    @property
    def _check_pending(self) -> bool:
        return bool(self._check_state_obj().pending)

    @_check_pending.setter
    def _check_pending(self, value: bool) -> None:
        self._check_state_obj().pending = bool(value)

    @property
    def _check_start_scheduled(self) -> bool:
        return bool(self._check_state_obj().start_scheduled)

    @_check_start_scheduled.setter
    def _check_start_scheduled(self, value: bool) -> None:
        self._check_state_obj().start_scheduled = bool(value)
    
    def save_results(self):
        """Сохраняет результаты в файл."""
        from PyQt6.QtWidgets import QFileDialog
        
        # Выбираем путь для сохранения
        default_filename = dns_check_page_plans.build_save_default_filename()
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить результаты DNS проверки",
            default_filename,
            "Text Files (*.txt);;All Files (*.*)"
        )
        
        if file_path:
            self._start_save_results_worker(
                file_path=file_path,
                plain_text=None,
            )

    def create_dns_check_save_worker(self, request_id: int, *, file_path: str, plain_text: str):
        return self._dns.create_dns_check_save_worker(
            request_id,
            file_path=file_path,
            plain_text=plain_text,
            parent=self,
        )

    def _start_save_results_worker(self, *, file_path: str, plain_text: str | None) -> None:
        state = self._save_results_state_obj()
        if state.is_busy():
            state.pending = {
                "file_path": str(file_path or ""),
                "plain_text": None if plain_text is None else str(plain_text or ""),
            }
            return
        state.pending = None
        plain_text = self._resolve_save_results_text(plain_text)
        self._save_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self.create_dns_check_save_worker(
                request_id,
                file_path=file_path,
                plain_text=plain_text,
            ),
            on_finished=self._on_save_results_worker_finished,
            bind_worker=self._bind_save_results_worker,
        )

    def _bind_save_results_worker(self, worker) -> None:
        worker.saved.connect(self._on_save_results_finished)

    def _resolve_save_results_text(self, plain_text: str | None) -> str:
        if plain_text is not None:
            return str(plain_text or "")
        return str(self.__dict__.get("_results_plain_text_cache", "") or "")

    def _clear_results_plain_text_cache(self) -> None:
        self._results_plain_text_cache = ""

    def _append_results_plain_text_cache(self, text) -> None:
        line = str(text or "")
        current = str(self.__dict__.get("_results_plain_text_cache", "") or "")
        if current:
            self._results_plain_text_cache = f"{current}\n{line}"
        else:
            self._results_plain_text_cache = line

    def _on_save_results_finished(self, request_id: int, plan) -> None:
        if not self._save_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        if self._save_results_state_obj().has_pending():
            return
        if InfoBar:
            if bool(getattr(plan, "success", False)):
                InfoBar.success(title=plan.title, content=plan.content, parent=self.window())
            else:
                InfoBar.error(title=plan.title, content=plan.content, parent=self.window())

    def _on_save_results_worker_finished(self, worker) -> None:
        if not self._is_current_worker_finish(self.__dict__.get("_save_runtime"), worker):
            return
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        if self._save_results_state_obj().has_pending():
            self._schedule_save_results_worker_start()

    def _is_current_request_finish(self, runtime, request_id: int) -> bool:
        if runtime is None:
            return True
        try:
            return int(request_id) == int(getattr(runtime, "request_id", request_id))
        except (TypeError, ValueError):
            return False

    def _is_current_worker_finish(self, runtime, worker) -> bool:
        if runtime is None:
            return True
        request_id = getattr(worker, "_request_id", None)
        if request_id is not None:
            return self._is_current_request_finish(runtime, request_id)
        current_worker = getattr(runtime, "worker", None)
        if current_worker is not None:
            return worker is current_worker
        return True

    def _schedule_save_results_worker_start(self) -> None:
        self._save_results_state_obj().schedule_start(
            QTimer.singleShot,
            self._run_scheduled_save_results_worker_start,
            cleanup_in_progress=self.__dict__.get("_cleanup_in_progress", False),
        )

    def _run_scheduled_save_results_worker_start(self) -> None:
        pending = self._save_results_state_obj().take_pending_for_scheduled_start(
            cleanup_in_progress=self.__dict__.get("_cleanup_in_progress", False),
        )
        if not pending:
            return
        self._start_save_results_worker(
            file_path=str(pending.get("file_path") or ""),
            plain_text=None if pending.get("plain_text") is None else str(pending.get("plain_text") or ""),
        )

    def _save_results_state_obj(self) -> LatestValueWorkerState:
        state = self.__dict__.get("_save_results_state")
        runtime = self.__dict__.get("_save_runtime")
        if state is None:
            pending = self.__dict__.pop("_save_results_pending", None)
            start_scheduled = bool(
                self.__dict__.pop("_save_results_start_scheduled", False)
            )
            state = LatestValueWorkerState(
                runtime,
                empty_value=None,
                pending=pending,
                start_scheduled=start_scheduled,
            )
            self.__dict__["_save_results_state"] = state
        elif getattr(state, "runtime", None) is None and runtime is not None:
            state.runtime = runtime
        return state

    @property
    def _save_results_pending(self):
        return self._save_results_state_obj().pending

    @_save_results_pending.setter
    def _save_results_pending(self, value) -> None:
        self._save_results_state_obj().pending = value

    @property
    def _save_results_start_scheduled(self) -> bool:
        return bool(self._save_results_state_obj().start_scheduled)

    @_save_results_start_scheduled.setter
    def _save_results_start_scheduled(self, value: bool) -> None:
        self._save_results_state_obj().start_scheduled = bool(value)
    
    def cleanup(self):
        """Очистка потоков при закрытии"""
        from log.log import log

        try:
            self._cleanup_in_progress = True
            self._check_state_obj().reset()
            self._save_results_state_obj().reset()
            self._check_runtime.stop(
                blocking=False,
                log_fn=log,
                warning_prefix="DNS check worker",
            )
            self._check_runtime.cancel()
            self._save_runtime.stop(
                blocking=False,
                log_fn=log,
                warning_prefix="DNS check save worker",
            )
            self._save_runtime.cancel()
        except Exception as e:
            log(f"Ошибка при очистке dns_check_page: {e}", "DEBUG")

    def set_ui_language(self, language: str) -> None:
        super().set_ui_language(language)

        self.check_button.setText(tr_catalog("page.dns_check.button.start", language=self._ui_language, default="Начать проверку"))
        self.save_button.setText(tr_catalog("page.dns_check.button.save", language=self._ui_language, default="Сохранить результаты"))
        start_description = self._action_description(
            "page.dns_check.action.start.description",
            "Полностью проверить DNS-резолвинг через разные серверы и собрать расширенный отчёт.",
        )
        set_tooltip(self.check_button, start_description)
        set_control_accessibility(
            self.check_button,
            name="Начать полную проверку DNS",
            description=start_description,
        )
        save_description = self._action_description(
            "page.dns_check.action.save.description",
            "Сохранить текущий отчёт DNS-проверки в текстовый файл.",
        )
        set_tooltip(self.save_button, save_description)
        set_control_accessibility(
            self.save_button,
            name="Сохранить результаты проверки DNS",
            description=save_description,
        )
        self._update_action_button_state_text()
        self._set_status(self.status_label.text(), tone=self._status_tone, bold=self._status_bold)
