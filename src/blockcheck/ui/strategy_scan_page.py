"""Strategy Scanner page — brute-force DPI bypass strategy selection.

Can be used as a standalone page or embedded as a tab inside BlockCheck.
Tests strategies one by one through winws2 + HTTPS probe.
"""

import logging
from collections import deque
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel

from ui.pages.base_page import BasePage
from blockcheck.ui.strategy_scan_page_build import (
    build_strategy_scan_control_section,
    build_strategy_scan_log_section,
    build_strategy_scan_results_section,
)
from blockcheck.ui.strategy_scan_page_results_workflow import (
    add_strategy_result_row,
    append_scan_log,
    apply_finished_scan,
    apply_force_stop_status,
    apply_phase_change,
    apply_strategy_started_progress,
)
from blockcheck.strategy_scan_run_workflow import (
    plan_strategy_scan_resume,
    request_strategy_scan_stop,
    start_strategy_scan_run,
    start_strategy_scan_worker,
)
from blockcheck.ui.strategy_scan_page_runtime_helpers import (
    apply_language_plan_ui,
    set_support_status,
)
from ui.latest_value_worker_state import LatestValueWorkerState
from ui.log_limits import BLOCKCHECK_LOG_VIEW_MAX_LINES
from ui.log_report_dialog import show_log_report_dialog
from ui.one_shot_worker_runtime import OneShotWorkerRuntime
from ui.popup_menu import exec_popup_menu
from ui.accessibility import set_control_accessibility, set_state_text
from ui.combo_accessibility import set_combo_items_accessibility
from app.ui_texts import tr as tr_catalog
from qfluentwidgets import (
    ComboBox,
    BodyLabel,
    PrimaryPushButton,
    PushButton,
    LineEdit,
    RoundMenu,
    Action,
)

from ui.fluent_widgets import (
    InfoBarHelper, set_tooltip,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

class StrategyScanPage(BasePage):
    """Strategy Scanner — brute-force DPI bypass strategy testing."""

    def __init__(
        self,
        parent=None,
        *,
        embedded: bool = False,
        blockcheck_feature,
        create_strategy_scan_worker,
    ):
        self._embedded = bool(embedded)
        super().__init__(
            title=tr_catalog("page.blockcheck_public.title", default="Подбор стратегии"),
            subtitle=tr_catalog("page.blockcheck_public.subtitle",
                                default="Найдёт стратегию обхода DPI, которая работает у вашего провайдера"),
            parent=parent,
            title_key="page.blockcheck_public.title",
            subtitle_key="page.blockcheck_public.subtitle",
        )
        self.setObjectName("StrategyScanPage")

        self._blockcheck = blockcheck_feature
        self._create_strategy_scan_worker = create_strategy_scan_worker
        self._result_rows: list[dict] = []
        self._scan_target: str = ""
        self._scan_protocol: str = "tcp_https"
        self._scan_udp_games_scope: str = "all"
        self._scan_mode: str = "quick"
        self._scan_worker = None
        self._run_log_file: Path | None = None
        self._quick_domain_btn: PushButton | None = None
        self._target_label: QLabel | None = None
        self._games_scope_label: QLabel | None = None
        self._games_scope_combo = None
        self._udp_scope_hint_label: QLabel | None = None
        # Подробный лог подбора; показывается в отдельном окне по кнопке.
        self._log_lines: deque[str] = deque(maxlen=BLOCKCHECK_LOG_VIEW_MAX_LINES)
        self._result_objects: list = []
        self._scan_panel = None
        self._protocol_label = None
        self._mode_label = None
        self._prepare_support_btn = None
        self._support_status_label = None
        self._cleanup_in_progress = False
        self._strategy_scan_run_runtime = OneShotWorkerRuntime()
        self._strategy_apply_runtime = OneShotWorkerRuntime()
        self._strategy_apply_state = LatestValueWorkerState(self._strategy_apply_runtime, empty_value=None)
        self._support_prepare_runtime = OneShotWorkerRuntime()
        self._support_prepare_state = LatestValueWorkerState(self._support_prepare_runtime, empty_value=None)
        self._quick_targets_runtime = OneShotWorkerRuntime()
        self._quick_targets_state = LatestValueWorkerState(self._quick_targets_runtime, empty_value=None)
        self._strategy_scan_finalize_runtime = OneShotWorkerRuntime()
        self._strategy_scan_finalize_state = LatestValueWorkerState(
            self._strategy_scan_finalize_runtime,
            empty_value=None,
        )

        self._build_ui()
        if self._embedded:
            self.hide_page_header()

    def create_support_prepare_worker(
        self,
        request_id: int,
        *,
        run_log_file,
        target: str,
        protocol_label: str,
        mode_label: str,
        scan_protocol: str,
    ):
        return self._blockcheck.create_strategy_scan_support_prepare_worker(
            request_id,
            run_log_file=run_log_file,
            target=target,
            protocol_label=protocol_label,
            mode_label=mode_label,
            scan_protocol=scan_protocol,
            parent=self,
        )

    def create_quick_targets_worker(self, request_id: int, *, scan_protocol: str, current_value: str):
        return self._blockcheck.create_strategy_scan_quick_targets_worker(
            request_id,
            scan_protocol=scan_protocol,
            current_value=current_value,
            parent=self,
        )

    def create_strategy_scan_finalize_worker(self, request_id: int, *, report):
        return self._blockcheck.create_strategy_scan_finalize_worker(
            request_id,
            report=report,
            scan_protocol=self._scan_protocol,
            result_rows=list(self._result_rows),
            parent=self,
        )

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ── «Что починить» ──
        control_widgets = build_strategy_scan_control_section(
            tr_fn=lambda key, default: tr_catalog(key, default=default),
            combo_cls=ComboBox,
            body_label_cls=BodyLabel,
            primary_button_cls=PrimaryPushButton,
            push_button_cls=PushButton,
            line_edit_cls=LineEdit,
            on_protocol_changed=self._on_protocol_changed,
            on_udp_games_scope_changed=self._on_udp_games_scope_changed,
            on_show_quick_domains_menu=self._show_quick_domains_menu,
            on_start=self._on_start,
            on_stop=self._on_stop,
        )
        self._control_card = control_widgets.control_card
        self._protocol_label = control_widgets.protocol_label
        self._protocol_combo = control_widgets.protocol_combo
        self._games_scope_label = control_widgets.games_scope_label
        self._games_scope_combo = control_widgets.games_scope_combo
        self._mode_label = control_widgets.mode_label
        self._mode_combo = control_widgets.mode_combo
        self._mode_hint_label = control_widgets.mode_hint_label
        self._target_label = control_widgets.target_label
        self._target_input = control_widgets.target_input
        self._quick_domain_btn = control_widgets.quick_domain_btn
        self._udp_scope_hint_label = control_widgets.udp_scope_hint_label
        self._start_btn = control_widgets.start_btn
        self._stop_btn = control_widgets.stop_btn
        self._mode_combo.currentIndexChanged.connect(self._update_control_accessibility)
        self._mode_combo.currentIndexChanged.connect(self._refresh_mode_hint)
        self.add_widget(self._control_card)

        # ── Ход и итог, результаты ──
        results_widgets = build_strategy_scan_results_section(on_apply_best=self._on_apply_best)
        self._scan_panel = results_widgets.panel
        self._progress_bar = results_widgets.progress_bar
        self._status_label = results_widgets.status_label
        self._results_card = results_widgets.results_card
        self._results_view = results_widgets.results_view
        self.add_widget(self._scan_panel)
        self.add_widget(self._results_card)

        # ── Подробный лог (в отдельном окне) и обращение ──
        log_widgets = build_strategy_scan_log_section(
            tr_fn=lambda key, default: tr_catalog(key, default=default),
            push_button_cls=PushButton,
            on_open_log=self._open_log,
            on_prepare_support=self._prepare_support_from_strategy_scan,
        )
        self._log_card = log_widgets.log_card
        self._log_btn = log_widgets.log_btn
        self._support_status_label = log_widgets.support_status_label
        self._prepare_support_btn = log_widgets.prepare_support_btn
        self.add_widget(self._log_card)
        # Лог и обращение появляются с первым подбором: до него там
        # показывать нечего.
        self._log_card.setVisible(False)

        self._update_control_accessibility()
        self._refresh_mode_hint()
        self._on_protocol_changed(self._protocol_combo.currentIndex())
        self._set_status_text(self._status_label.text())

    def _refresh_mode_hint(self, *_args) -> None:
        from blockcheck.strategy_scan_page_plans import mode_hint_text

        self._mode_hint_label.setText(mode_hint_text(self._mode_combo.currentIndex(), language=self._ui_language))

    # ------------------------------------------------------------------
    # Log
    # ------------------------------------------------------------------

    def _open_log(self) -> None:
        """Подробный лог открывается в отдельном окне; во время подбора — снимок на момент открытия."""
        show_log_report_dialog(
            self.window(),
            title="Подробный лог подбора стратегии",
            text="\n".join(self._log_lines),
            empty_text="Подбор ещё не запускался.",
            description="Технический лог подбора стратегии — он нужен для обращения в поддержку.",
            scroll_to_end=True,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _wrap_layout(layout: QHBoxLayout) -> QWidget:
        """Wrap a layout in a transparent QWidget for add_widget()."""
        w = QWidget()
        w.setLayout(layout)
        w.setStyleSheet("background: transparent;")
        return w

    def _on_protocol_changed(self, _index: int) -> None:
        """Adjust target input defaults when protocol changes."""
        selection = self._blockcheck.build_selection_state(
            protocol_value=self._protocol_combo.currentData(),
            udp_scope_value=self._games_scope_combo.currentData() if self._games_scope_combo is not None else "all",
            mode_index=self._mode_combo.currentIndex() if self._mode_combo is not None else 0,
        )
        plan = self._blockcheck.build_protocol_ui_plan(
            scan_protocol=selection.scan_protocol,
            current_value=self._target_input.text(),
        )

        if self._games_scope_label is not None:
            self._games_scope_label.setVisible(plan.is_udp_games)
        if self._games_scope_combo is not None:
            self._games_scope_combo.setVisible(plan.is_udp_games)
            self._games_scope_combo.setEnabled(plan.is_udp_games)

        if self._target_label is not None:
            self._target_label.setVisible(plan.show_target_controls)
        self._target_input.setVisible(plan.show_target_controls)
        if self._quick_domain_btn is not None:
            self._quick_domain_btn.setVisible(plan.show_target_controls)

        self._target_input.setText(plan.normalized_target)
        self._target_input.setPlaceholderText(plan.placeholder_text)
        self._refresh_udp_scope_hint()
        self._update_control_accessibility()

    def _on_udp_games_scope_changed(self, _index: int) -> None:
        """Update UDP scope helper text after combo change."""
        self._refresh_udp_scope_hint()
        self._update_control_accessibility()

    def _refresh_udp_scope_hint(self) -> None:
        """Refresh compact helper label with resolved UDP ipset sources."""
        if self._udp_scope_hint_label is None:
            return

        selection = self._blockcheck.build_selection_state(
            protocol_value=self._protocol_combo.currentData(),
            udp_scope_value=self._games_scope_combo.currentData() if self._games_scope_combo is not None else "all",
            mode_index=self._mode_combo.currentIndex() if self._mode_combo is not None else 0,
        )
        hint_plan = self._blockcheck.build_udp_scope_hint_plan(
            scan_protocol=selection.scan_protocol,
            udp_games_scope=selection.udp_games_scope,
            scope_all_label=tr_catalog("page.strategy_scan.udp_scope_all", default="Все списки адресов (по умолчанию)"),
            scope_games_only_label=tr_catalog("page.strategy_scan.udp_scope_games_only", default="Только игровые списки"),
        )
        self._udp_scope_hint_label.setText(hint_plan.text)
        set_tooltip(self._udp_scope_hint_label, hint_plan.tooltip)
        self._udp_scope_hint_label.setVisible(hint_plan.visible)

    def _show_quick_domains_menu(self) -> None:
        """Open popup menu with predefined targets for selected protocol."""
        if self._quick_domain_btn is None:
            return

        selection = self._blockcheck.build_selection_state(
            protocol_value=self._protocol_combo.currentData(),
            udp_scope_value=self._games_scope_combo.currentData() if self._games_scope_combo is not None else "all",
            mode_index=self._mode_combo.currentIndex() if self._mode_combo is not None else 0,
        )
        self._request_quick_targets_menu(
            scan_protocol=selection.scan_protocol,
            current_value=self._target_input.text(),
        )

    def _request_quick_targets_menu(self, *, scan_protocol: str, current_value: str) -> None:
        pending = {
            "scan_protocol": str(scan_protocol or ""),
            "current_value": str(current_value or ""),
        }
        state = self._quick_targets_state_obj()
        if state.is_busy():
            state.pending = pending
            return
        state.pending = None

        def worker_factory(request_id: int):
            return self.create_quick_targets_worker(
                request_id,
                scan_protocol=pending["scan_protocol"],
                current_value=pending["current_value"],
            )

        def bind_worker(worker) -> None:
            worker.completed.connect(self._on_quick_targets_loaded)
            worker.failed.connect(self._on_quick_targets_failed)

        self._quick_targets_runtime.start_qthread_worker(
            worker_factory=worker_factory,
            bind_worker=bind_worker,
            on_finished=self._on_quick_targets_worker_finished,
        )

    def _on_quick_targets_loaded(self, request_id: int, menu_plan) -> None:
        if not self._quick_targets_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        if self._quick_targets_state_obj().has_pending():
            return
        self._open_quick_targets_menu(menu_plan)

    def _on_quick_targets_failed(self, request_id: int, error: str) -> None:
        if not self._quick_targets_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        logger.warning("Failed to load quick targets: %s", error)

    def _on_quick_targets_worker_finished(self, worker) -> None:
        self._quick_targets_state_obj().schedule_pending_after_finish(
            worker,
            is_current_worker_finish=self._is_current_worker_finish,
            single_shot=QTimer.singleShot,
            run_scheduled=self._run_scheduled_quick_targets_menu_start,
            cleanup_in_progress=self.__dict__.get("_cleanup_in_progress", False),
        )

    def _schedule_quick_targets_menu_start(self) -> None:
        self._quick_targets_state_obj().schedule_start(
            QTimer.singleShot,
            self._run_scheduled_quick_targets_menu_start,
            cleanup_in_progress=self.__dict__.get("_cleanup_in_progress", False),
        )

    def _run_scheduled_quick_targets_menu_start(self) -> None:
        pending = self._quick_targets_state_obj().take_pending_for_scheduled_start(
            cleanup_in_progress=self.__dict__.get("_cleanup_in_progress", False),
        )
        if not pending:
            return
        self._request_quick_targets_menu(
            scan_protocol=str(pending.get("scan_protocol") or ""),
            current_value=str(pending.get("current_value") or ""),
        )

    def _open_quick_targets_menu(self, menu_plan) -> None:
        if self._quick_domain_btn is None:
            return
        menu = RoundMenu(parent=self)
        for option in menu_plan.options:
            action = Action(option, menu)
            action.setCheckable(True)
            is_current = option == menu_plan.current_value
            action.setChecked(is_current)
            action.triggered.connect(
                lambda checked=False, selected_target=option: self._on_pick_quick_domain(selected_target)
            )
            menu.addAction(action)
            menu_item = menu.view.item(menu.view.count() - 1)
            if menu_item is not None:
                state = "выбрана" if is_current else "не выбрана"
                accessible_text = f"Быстрая цель: {option}, {state}"
                menu_item.setData(Qt.ItemDataRole.AccessibleTextRole, accessible_text)
                menu_item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, accessible_text)

        if not menu.actions():
            return

        exec_popup_menu(
            menu,
            self._quick_domain_btn.mapToGlobal(self._quick_domain_btn.rect().bottomLeft()),
            owner=self,
        )

    def prefill_target(self, target: str, *, protocol: str = "tcp_https") -> None:
        """BlockCheck нашёл проблему: подставляет, что подбирать, но не запускает поиск."""
        if self._strategy_scan_run_runtime.is_running():
            return
        index = self._protocol_combo.findData(protocol)
        if index >= 0 and index != self._protocol_combo.currentIndex():
            self._protocol_combo.setCurrentIndex(index)
        value = str(target or "").strip()
        if value and protocol == "tcp_https":
            self._target_input.setText(value)
        try:
            self._start_btn.setFocus()
        except Exception:
            pass

    def _on_pick_quick_domain(self, domain: str) -> None:
        """Fill the domain field from quick picker."""
        if not domain:
            return
        self._target_input.setText(domain)
        try:
            self._target_input.setFocus(Qt.FocusReason.OtherFocusReason)
            self._target_input.selectAll()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _apply_page_theme(self, tokens=None, force: bool = False):
        # Свои виджеты перекрашиваются сами через ThemeRefreshBinding.
        _ = tokens
        _ = force

    def _apply_language_plan(self, language: str) -> None:
        apply_language_plan_ui(
            blockcheck_feature=self._blockcheck,
            language=language,
            log_btn=self._log_btn,
            protocol_label=self._protocol_label,
            mode_label=self._mode_label,
            mode_combo=self._mode_combo,
            target_label=self._target_label,
            start_btn=self._start_btn,
            stop_btn=self._stop_btn,
            prepare_support_btn=self._prepare_support_btn,
            protocol_combo=self._protocol_combo,
            games_scope_label=self._games_scope_label,
            games_scope_combo=self._games_scope_combo,
            quick_domain_btn=self._quick_domain_btn,
        )

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    def _on_start(self):
        if self._strategy_scan_run_runtime.is_running():
            return
        self._cleanup_in_progress = False
        from_start = self._ask_scan_start_choice()
        if from_start is None:
            return

        run_result = start_strategy_scan_run(
            blockcheck_feature=self._blockcheck,
            create_strategy_scan_worker=self._create_strategy_scan_worker,
            raw_target_input=self._target_input.text(),
            raw_protocol_value=self._protocol_combo.currentData(),
            raw_udp_scope_value=self._games_scope_combo.currentData() if self._games_scope_combo is not None else "all",
            mode_index=self._mode_combo.currentIndex(),
            starting_status_text=tr_catalog("page.blockcheck_public.starting", default="Запуск сканирования..."),
            parent=self,
            on_run_log_started=self._on_run_log_started,
            on_strategy_started=self._on_strategy_started,
            on_strategy_args_started=self._on_strategy_args_started,
            on_stage_changed=self._on_stage_changed,
            on_strategy_result=self._on_strategy_result,
            on_log=self._on_log,
            on_phase_changed=self._on_phase_changed,
            on_continue_question=self._on_continue_question,
            on_finished=self._on_finished,
            from_start=from_start,
        )
        self._target_input.setText(run_result.target)

        self._results_view.clear()
        self._results_card.setVisible(False)
        self._result_rows.clear()
        self._result_objects.clear()
        self._log_lines.clear()
        self._set_support_status("")
        self._log_card.setVisible(True)

        self._scan_target = run_result.target
        self._scan_protocol = run_result.scan_protocol
        self._scan_udp_games_scope = run_result.udp_games_scope
        self._scan_mode = run_result.mode
        self._scan_worker = run_result.worker
        self._run_log_file = None

        self._apply_interaction_plan(self._blockcheck.build_running_interaction_plan())
        self._scan_panel.show_running(run_result.target, self._running_subtitle())
        from blockcheck.ui.fun_texts import phrases

        self._scan_panel.set_phrases(phrases("scan_network", self._ui_language))
        set_state_text(self._progress_bar, "Ход подбора стратегии: выполняется")
        self._set_status_text(self._running_subtitle())
        start_strategy_scan_worker(
            run_result.worker,
            parent=self,
            run_runtime=self._strategy_scan_run_runtime,
        )

    def _ask_scan_start_choice(self) -> bool | None:
        """Для цели уже есть проверенные стратегии: продолжить или начать заново.

        Подбор помнит стратегии, которые недавно не сработали, и ставит их в
        конец очереди, — поэтому повторный запуск идёт дальше по списку.
        Если такие есть, пользователь выбирает сам. Возвращает True — начать
        заново, False — продолжить (или спрашивать нечего), None — отмена.
        """
        try:
            info = plan_strategy_scan_resume(
                blockcheck_feature=self._blockcheck,
                raw_target_input=self._target_input.text(),
                raw_protocol_value=self._protocol_combo.currentData(),
                raw_udp_scope_value=self._games_scope_combo.currentData() if self._games_scope_combo is not None else "all",
                mode_index=self._mode_combo.currentIndex(),
            )
        except Exception:
            logger.exception("Strategy scan resume check failed")
            return False
        if info.tested_count <= 0:
            return False
        try:
            from ui.fluent_dialog import MessageBox
            from ui.message_box_accessibility import set_message_box_button_accessibility

            title = tr_catalog("page.strategy_scan.resume_question_title", default="Подбор уже начинался")
            body = tr_catalog(
                "page.strategy_scan.resume_question_text",
                default=(
                    "Для {target} уже проверено стратегий: {count} — они не сработали "
                    "(подбор помнит их 14 дней).\n\n"
                    "«Продолжить» — проверить следующие, ещё не проверенные стратегии.\n"
                    "«Начать заново» — проверить список с самого начала, как в первый раз."
                ),
            ).format(target=info.target, count=info.tested_count)
            box = MessageBox(title, body, self.window())
            continue_text = tr_catalog("page.strategy_scan.resume_question_continue", default="Продолжить с места остановки")
            restart_text = tr_catalog("page.strategy_scan.resume_question_restart", default="Начать заново")
            box.yesButton.setText(continue_text)
            box.cancelButton.setText(tr_catalog("page.strategy_scan.resume_question_cancel", default="Отмена"))
            restart_button = PushButton(restart_text)
            choice = {"restart": False}

            def _restart() -> None:
                choice["restart"] = True
                box.accept()

            restart_button.clicked.connect(_restart)
            box.buttonLayout.insertWidget(1, restart_button)
            set_state_text(restart_button, restart_text)
            set_control_accessibility(
                restart_button,
                name="Начать подбор заново",
                description="Проверить стратегии с начала списка, не пропуская уже проверенные.",
            )
            set_message_box_button_accessibility(
                box,
                yes_name="Продолжить подбор с места остановки",
                yes_description="Проверить следующие стратегии, которые ещё не проверялись для этой цели.",
                cancel_name="Отменить запуск подбора",
                cancel_description="Подбор не запустится.",
            )
            if not box.exec():
                return None
            return bool(choice["restart"])
        except Exception:
            logger.exception("Strategy scan resume question failed")
            return False

    def _on_stop(self):
        request_strategy_scan_stop(
            worker=self._strategy_scan_run_runtime.worker,
            schedule_stop_check=lambda expected_worker:
            QTimer.singleShot(5000, lambda worker=expected_worker: self._force_stop(worker)),
        )
        self._stop_btn.setEnabled(False)
        self._set_status_text(tr_catalog("page.blockcheck_public.stopping", default="Остановка..."))

    def request_runtime_conflicting_stop(self) -> bool:
        """Останавливает подбор перед ручным запуском основного DPI."""
        if not self._strategy_scan_run_runtime.is_running():
            return False
        # Пользователь сам запускает Zapret: подбор не должен запускать его
        # второй раз, когда остановится.
        cancel_restore = getattr(self.__dict__.get("_scan_worker"), "cancel_runtime_restore", None)
        if cancel_restore is not None:
            cancel_restore()
        self._on_stop()
        return True

    def _force_stop(self, expected_worker=None):
        apply_force_stop_status(
            worker=self._strategy_scan_run_runtime.worker,
            expected_worker=expected_worker,
            status_label=self._status_label,
            set_support_status=self._set_support_status,
        )
        self._set_status_text(self._status_label.text())

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_run_log_started(self, run_log_file) -> None:
        if self._cleanup_in_progress:
            return
        self._run_log_file = run_log_file

    def _on_strategy_started(self, name: str, index: int, total: int):
        if self._cleanup_in_progress:
            return
        apply_strategy_started_progress(
            blockcheck_feature=self._blockcheck,
            strategy_name=name,
            index=index,
            total=total,
            result_rows=self._result_rows,
            progress_bar=self._progress_bar,
            status_label=self._status_label,
            done_count=len(self._result_rows),
        )
        if self._scan_panel is not None:
            self._scan_panel.set_strategy_progress(len(self._result_rows), total)

    def _on_strategy_result(self, result):
        """Строка результата: найденные сверху, остальные — в свёрнутые группы."""
        if self._cleanup_in_progress:
            return
        stored_row = add_strategy_result_row(
            blockcheck_feature=self._blockcheck,
            results_view=self._results_view,
            result=result,
            row_number=len(self._result_rows) + 1,
            on_apply_strategy=self._on_apply_strategy,
        )
        self._result_rows.append(dict(stored_row))
        self._result_objects.append(result)
        self._results_card.setVisible(True)
        self._progress_bar.setValue(len(self._result_rows))
        if getattr(result, "success", False):
            self._scan_panel.note_found(sum(1 for row in self._result_rows if row.get("success")))

    def _running_subtitle(self) -> str:
        mode = self._mode_combo.currentText() if self._mode_combo is not None else ""
        return (
            f"{self._protocol_combo.currentText()} · {mode}. "
            "Zapret на время подбора выключен — потом включится снова, если работал."
        )

    def _on_stage_changed(self, step: str, status: str, text: str) -> None:
        if self._cleanup_in_progress or self._scan_panel is None:
            return
        self._scan_panel.set_step(step, status, text)
        if status == "running" and step in ("network", "baseline", "control"):
            from blockcheck.ui.fun_texts import phrases

            self._scan_panel.set_phrases(phrases(f"scan_{step}", self._ui_language))

    def _on_strategy_args_started(self, args: str) -> None:
        if self._cleanup_in_progress or self._scan_panel is None:
            return
        from blockcheck.ui.fun_texts import strategy_phrases

        self._scan_panel.set_phrases(strategy_phrases(args, self._ui_language))

    def _on_apply_best(self) -> None:
        """«Применить лучшую» — самая быстрая из надёжных стратегий."""
        working = [
            (float(row.get("time_ms") or 1e9), index)
            for index, row in enumerate(self._result_rows)
            if row.get("success")
        ]
        if not working:
            return
        _time, index = min(working)
        if 0 <= index < len(self._result_objects):
            self._on_apply_strategy(self._result_objects[index])

    def _on_continue_question(self, reason: str) -> None:
        """Цель открывается без обхода: спросить, проверять ли всё равно."""
        worker = self._scan_worker
        if worker is None:
            return
        proceed = False
        if not self._cleanup_in_progress:
            try:
                from ui.fluent_dialog import MessageBox
                from ui.message_box_accessibility import set_message_box_button_accessibility

                title = tr_catalog("page.strategy_scan.baseline_question_title", default="Подбирать нечего")
                body = f"{reason}\n\n" + tr_catalog(
                    "page.strategy_scan.baseline_question_text",
                    default="Всё равно проверить стратегии? Результаты будут только для сведения.",
                )
                box = MessageBox(title, body, self.window())
                box.yesButton.setText(
                    tr_catalog("page.strategy_scan.baseline_question_yes", default="Всё равно проверить")
                )
                box.cancelButton.setText(
                    tr_catalog("page.strategy_scan.baseline_question_no", default="Не проверять")
                )
                set_message_box_button_accessibility(
                    box,
                    yes_name="Всё равно проверить стратегии",
                    yes_description=body,
                    cancel_name="Не проверять стратегии",
                    cancel_description="Подбор завершится без проверки стратегий.",
                )
                proceed = bool(box.exec())
            except Exception:
                logger.exception("Strategy scan continue question failed")
                proceed = False
        worker.answer_continue(proceed)

    def _restore_runtime_after_scan(self) -> None:
        """Подбор останавливал Zapret — вернуть его, если он работал до подбора."""
        restore = getattr(self._scan_worker, "restore_runtime_if_needed", None)
        if restore is None:
            return
        try:
            restored = restore()
        except Exception:
            logger.exception("Failed to restore Zapret after strategy scan")
            return
        if restored:
            self._on_log("Подбор закончен — запускаю Zapret снова, как было до подбора")

    def _on_log(self, message: str):
        if self._cleanup_in_progress:
            return
        append_scan_log(
            log_lines=self._log_lines,
            message=message,
        )

    def _on_phase_changed(self, phase: str):
        if self._cleanup_in_progress:
            return
        apply_phase_change(
            status_label=self._status_label,
            phase=phase,
        )
        self._set_status_text(self._status_label.text())

    def _on_finished(self, report):
        """Handle scan completion."""
        if self._cleanup_in_progress:
            return
        self._restore_runtime_after_scan()
        self._request_strategy_scan_finalize(report)

    def _request_strategy_scan_finalize(self, report) -> None:
        state = self._strategy_scan_finalize_state_obj()
        if state.is_busy():
            state.pending = report
            return
        state.pending = None

        def worker_factory(request_id: int):
            return self.create_strategy_scan_finalize_worker(request_id, report=report)

        def bind_worker(worker) -> None:
            worker.completed.connect(self._on_strategy_scan_finalize_finished)
            worker.failed.connect(self._on_strategy_scan_finalize_failed)

        self._strategy_scan_finalize_runtime.start_qthread_worker(
            worker_factory=worker_factory,
            bind_worker=bind_worker,
            on_finished=self._on_strategy_scan_finalize_worker_finished,
        )

    def _on_strategy_scan_finalize_finished(self, request_id: int, finish_plan) -> None:
        if not self._strategy_scan_finalize_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        if self._strategy_scan_finalize_state_obj().has_pending():
            return
        apply_finished_scan(
            blockcheck_feature=self._blockcheck,
            finish_plan=finish_plan,
            reset_ui=self._reset_ui,
            scan_protocol=self._scan_protocol,
            progress_bar=self._progress_bar,
            status_label=self._status_label,
            set_support_status=self._set_support_status,
            parent_widget=self.window(),
            panel=self._scan_panel,
        )

    def _on_strategy_scan_finalize_failed(self, request_id: int, error: str) -> None:
        if not self._strategy_scan_finalize_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        if self._strategy_scan_finalize_state_obj().has_pending():
            return
        logger.warning("Failed to finalize strategy scan: %s", error)
        self._reset_ui()
        self._set_status_text(tr_catalog("page.blockcheck_public.scan_finish_error", default="Ошибка завершения сканирования"))
        self._set_support_status(
            tr_catalog(
                "page.blockcheck_public.support_ready_after_error",
                default="Можно подготовить обращение по логам ошибки",
            )
        )

    def _on_strategy_scan_finalize_worker_finished(self, worker) -> None:
        self._strategy_scan_finalize_state_obj().schedule_pending_after_finish(
            worker,
            is_current_worker_finish=self._is_current_worker_finish,
            single_shot=QTimer.singleShot,
            run_scheduled=self._run_scheduled_strategy_scan_finalize_start,
            cleanup_in_progress=self.__dict__.get("_cleanup_in_progress", False),
        )

    def _schedule_strategy_scan_finalize_start(self) -> None:
        self._strategy_scan_finalize_state_obj().schedule_start(
            QTimer.singleShot,
            self._run_scheduled_strategy_scan_finalize_start,
            cleanup_in_progress=self.__dict__.get("_cleanup_in_progress", False),
        )

    def _run_scheduled_strategy_scan_finalize_start(self) -> None:
        pending = self._strategy_scan_finalize_state_obj().take_pending_for_scheduled_start(
            cleanup_in_progress=self.__dict__.get("_cleanup_in_progress", False),
        )
        if pending is None:
            return
        self._request_strategy_scan_finalize(pending)

    def _is_current_worker_finish(self, runtime, worker) -> bool:
        if runtime is None:
            return True
        request_id = getattr(worker, "_request_id", None)
        if request_id is not None:
            try:
                return int(request_id) == int(getattr(runtime, "request_id", request_id))
            except (TypeError, ValueError):
                return False
        current_worker = getattr(runtime, "worker", None)
        if current_worker is not None:
            return worker is current_worker
        return True

    def _support_prepare_state_obj(self) -> LatestValueWorkerState:
        state = self.__dict__.get("_support_prepare_state")
        runtime = self.__dict__.get("_support_prepare_runtime")
        if state is None:
            pending = self.__dict__.pop("_support_prepare_pending", None)
            start_scheduled = bool(self.__dict__.pop("_support_prepare_start_scheduled", False))
            state = LatestValueWorkerState(
                runtime,
                empty_value=None,
                pending=pending,
                start_scheduled=start_scheduled,
            )
            self.__dict__["_support_prepare_state"] = state
        elif getattr(state, "runtime", None) is None and runtime is not None:
            state.runtime = runtime
        return state

    @property
    def _support_prepare_pending(self):
        return self._support_prepare_state_obj().pending

    @_support_prepare_pending.setter
    def _support_prepare_pending(self, value) -> None:
        self._support_prepare_state_obj().pending = value

    @property
    def _support_prepare_start_scheduled(self) -> bool:
        return bool(self._support_prepare_state_obj().start_scheduled)

    @_support_prepare_start_scheduled.setter
    def _support_prepare_start_scheduled(self, value: bool) -> None:
        self._support_prepare_state_obj().start_scheduled = bool(value)

    def _quick_targets_state_obj(self) -> LatestValueWorkerState:
        state = self.__dict__.get("_quick_targets_state")
        runtime = self.__dict__.get("_quick_targets_runtime")
        if state is None:
            pending = self.__dict__.pop("_quick_targets_pending", None)
            start_scheduled = bool(self.__dict__.pop("_quick_targets_start_scheduled", False))
            state = LatestValueWorkerState(
                runtime,
                empty_value=None,
                pending=pending,
                start_scheduled=start_scheduled,
            )
            self.__dict__["_quick_targets_state"] = state
        elif getattr(state, "runtime", None) is None and runtime is not None:
            state.runtime = runtime
        return state

    @property
    def _quick_targets_pending(self):
        return self._quick_targets_state_obj().pending

    @_quick_targets_pending.setter
    def _quick_targets_pending(self, value) -> None:
        self._quick_targets_state_obj().pending = value

    @property
    def _quick_targets_start_scheduled(self) -> bool:
        return bool(self._quick_targets_state_obj().start_scheduled)

    @_quick_targets_start_scheduled.setter
    def _quick_targets_start_scheduled(self, value: bool) -> None:
        self._quick_targets_state_obj().start_scheduled = bool(value)

    def _strategy_scan_finalize_state_obj(self) -> LatestValueWorkerState:
        state = self.__dict__.get("_strategy_scan_finalize_state")
        runtime = self.__dict__.get("_strategy_scan_finalize_runtime")
        if state is None:
            pending = self.__dict__.pop("_strategy_scan_finalize_pending", None)
            start_scheduled = bool(self.__dict__.pop("_strategy_scan_finalize_start_scheduled", False))
            state = LatestValueWorkerState(
                runtime,
                empty_value=None,
                pending=pending,
                start_scheduled=start_scheduled,
            )
            self.__dict__["_strategy_scan_finalize_state"] = state
        elif getattr(state, "runtime", None) is None and runtime is not None:
            state.runtime = runtime
        return state

    @property
    def _strategy_scan_finalize_pending(self):
        return self._strategy_scan_finalize_state_obj().pending

    @_strategy_scan_finalize_pending.setter
    def _strategy_scan_finalize_pending(self, value) -> None:
        self._strategy_scan_finalize_state_obj().pending = value

    @property
    def _strategy_scan_finalize_start_scheduled(self) -> bool:
        return bool(self._strategy_scan_finalize_state_obj().start_scheduled)

    @_strategy_scan_finalize_start_scheduled.setter
    def _strategy_scan_finalize_start_scheduled(self, value: bool) -> None:
        self._strategy_scan_finalize_state_obj().start_scheduled = bool(value)

    def _strategy_apply_state_obj(self) -> LatestValueWorkerState:
        state = self.__dict__.get("_strategy_apply_state")
        runtime = self.__dict__.get("_strategy_apply_runtime")
        if state is None:
            pending = self.__dict__.pop("_strategy_apply_pending", None)
            start_scheduled = bool(self.__dict__.pop("_strategy_apply_start_scheduled", False))
            state = LatestValueWorkerState(
                runtime,
                empty_value=None,
                pending=pending,
                start_scheduled=start_scheduled,
            )
            self.__dict__["_strategy_apply_state"] = state
        elif getattr(state, "runtime", None) is None and runtime is not None:
            state.runtime = runtime
        return state

    @property
    def _strategy_apply_pending(self):
        return self._strategy_apply_state_obj().pending

    @_strategy_apply_pending.setter
    def _strategy_apply_pending(self, value) -> None:
        self._strategy_apply_state_obj().pending = value

    @property
    def _strategy_apply_start_scheduled(self) -> bool:
        return bool(self._strategy_apply_state_obj().start_scheduled)

    @_strategy_apply_start_scheduled.setter
    def _strategy_apply_start_scheduled(self, value: bool) -> None:
        self._strategy_apply_state_obj().start_scheduled = bool(value)

    # ------------------------------------------------------------------
    # Apply strategy
    # ------------------------------------------------------------------

    def _on_apply_strategy(self, result):
        """Записать проверенную стратегию в выбранный пресет."""
        self._request_strategy_apply(result)

    def create_strategy_apply_worker(self, request_id: int, *, strategy_args: str, strategy_name: str, apply_lines=()):
        return self._blockcheck.create_strategy_apply_worker(
            request_id,
            strategy_args=strategy_args,
            strategy_name=strategy_name,
            scan_target=self._scan_target,
            scan_protocol=self._scan_protocol,
            apply_lines=tuple(apply_lines or ()),
            parent=self,
        )

    def _request_strategy_apply(self, result) -> None:
        payload = {
            "strategy_args": str(getattr(result, "strategy_args", "") or ""),
            "strategy_name": str(getattr(result, "strategy_name", "") or ""),
            "apply_lines": tuple(getattr(result, "apply_lines", ()) or ()),
        }
        state = self._strategy_apply_state_obj()
        if state.is_busy():
            state.pending = dict(payload)
            return
        state.pending = None
        self._start_strategy_apply_worker(payload)

    def _start_strategy_apply_worker(self, payload: dict) -> None:
        def worker_factory(request_id: int):
            return self.create_strategy_apply_worker(
                request_id,
                strategy_args=str(payload.get("strategy_args") or ""),
                strategy_name=str(payload.get("strategy_name") or ""),
                apply_lines=tuple(payload.get("apply_lines") or ()),
            )

        def bind_worker(worker) -> None:
            worker.completed.connect(self._on_strategy_apply_finished)
            worker.failed.connect(self._on_strategy_apply_failed)

        self._strategy_apply_runtime.start_qthread_worker(
            worker_factory=worker_factory,
            bind_worker=bind_worker,
            on_finished=self._on_strategy_apply_runtime_finished,
        )

    def _on_strategy_apply_finished(self, request_id: int, result) -> None:
        if not self._strategy_apply_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        if self._strategy_apply_state_obj().has_pending():
            return
        message_plan = self._blockcheck.build_apply_success_plan(result)

        show = InfoBarHelper.warning if message_plan.kind == "warning" else InfoBarHelper.success
        show(
            self.window(),
            tr_catalog(message_plan.title_key, default=message_plan.title_default),
            message_plan.body_text,
        )

    def _on_strategy_apply_failed(self, request_id: int, error: str) -> None:
        if not self._strategy_apply_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        if self._strategy_apply_state_obj().has_pending():
            return
        logger.warning("Failed to apply strategy: %s", error)
        try:
            message_plan = self._blockcheck.build_apply_error_plan(str(error))
            InfoBarHelper.warning(
                self.window(),
                tr_catalog(message_plan.title_key, default=message_plan.title_default),
                message_plan.body_text,
            )
        except Exception:
            pass

    def _on_strategy_apply_runtime_finished(self, _worker) -> None:
        self._strategy_apply_state_obj().schedule_pending_after_finish(
            _worker,
            is_current_worker_finish=self._is_current_worker_finish,
            single_shot=QTimer.singleShot,
            run_scheduled=self._run_scheduled_strategy_apply_worker_start,
            cleanup_in_progress=self.__dict__.get("_cleanup_in_progress", False),
        )

    def _schedule_strategy_apply_worker_start(self, payload: dict) -> None:
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        state = self._strategy_apply_state_obj()
        state.pending = dict(payload or {})
        state.schedule_start(
            QTimer.singleShot,
            self._run_scheduled_strategy_apply_worker_start,
            cleanup_in_progress=self.__dict__.get("_cleanup_in_progress", False),
        )

    def _run_scheduled_strategy_apply_worker_start(self) -> None:
        pending = self._strategy_apply_state_obj().take_pending_for_scheduled_start(
            cleanup_in_progress=self.__dict__.get("_cleanup_in_progress", False),
        )
        if pending is None:
            return
        self._start_strategy_apply_worker(dict(pending or {}))

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    def _set_status_text(self, text: str) -> None:
        value = str(text or "").strip()
        self._status_label.setText(value)
        set_state_text(self._status_label, f"Статус подбора стратегии: {value}")

    def _update_combo_accessibility(self, combo, *, name: str, description: str) -> None:
        if combo is None:
            return
        selected = str(combo.currentText() or "").strip() or "не выбрано"
        state_text = f"{name}, выбрано: {selected}"
        set_state_text(combo, state_text)
        set_control_accessibility(
            combo,
            name=state_text,
            description=description,
        )
        if isinstance(combo, ComboBox):
            set_combo_items_accessibility(combo, name=name)

    def _update_control_accessibility(self, *_args) -> None:
        self._update_combo_accessibility(
            self._protocol_combo,
            name="Что должно заработать",
            description="Выберите, для чего подобрать стратегию: сайты и приложения, голосовые звонки или игры.",
        )
        self._update_combo_accessibility(
            self._games_scope_combo,
            name="Адреса игр",
            description="Выберите, какие списки адресов проверять в режиме онлайн-игр.",
        )
        self._update_combo_accessibility(
            self._mode_combo,
            name="Тщательность подбора",
            description="Выберите, сколько стратегий нужно проверить: больше стратегий — дольше поиск.",
        )

    def _apply_interaction_plan(self, plan) -> None:
        self._start_btn.setEnabled(plan.start_enabled)
        self._stop_btn.setEnabled(plan.stop_enabled)
        # «Остановить» нужна только во время поиска: в покое она лишь занимала место.
        self._stop_btn.setVisible(plan.stop_enabled or not plan.start_enabled)
        self._protocol_combo.setEnabled(plan.protocol_enabled)
        if self._games_scope_combo is not None:
            self._games_scope_combo.setEnabled(plan.games_scope_enabled)
        self._mode_combo.setEnabled(plan.mode_enabled)
        self._target_input.setEnabled(plan.target_enabled)
        if self._quick_domain_btn is not None:
            self._quick_domain_btn.setEnabled(plan.quick_domain_enabled)

    def _reset_ui(self):
        selection = self._blockcheck.build_selection_state(
            protocol_value=self._protocol_combo.currentData(),
            udp_scope_value=self._games_scope_combo.currentData() if self._games_scope_combo is not None else "all",
            mode_index=self._mode_combo.currentIndex() if self._mode_combo is not None else 0,
        )
        self._apply_interaction_plan(
            self._blockcheck.build_idle_interaction_plan(
                is_udp_games=selection.scan_protocol == "udp_games",
            )
        )

    def _set_support_status(self, text: str) -> None:
        set_support_status(self._support_status_label, text)
        value = str(text or "").strip()
        if value:
            set_state_text(self._support_status_label, f"Статус обращения по подбору стратегии: {value}")

    def _prepare_support_from_strategy_scan(self) -> None:
        if self._cleanup_in_progress:
            return
        support_context = self._blockcheck.build_support_context(
            stored_scan_protocol=self._scan_protocol,
            stored_scan_target=self._scan_target,
            raw_protocol_value=self._protocol_combo.currentData() if self._protocol_combo is not None else None,
            raw_target_input=self._target_input.text(),
            raw_protocol_label=self._protocol_combo.currentText() if self._protocol_combo is not None else "",
            raw_mode_label=self._mode_combo.currentText() if self._mode_combo is not None else "",
            stored_mode=self._scan_mode,
        )
        self._request_support_prepare(
            run_log_file=self._run_log_file,
            target=support_context.target,
            protocol_label=support_context.protocol_label,
            mode_label=support_context.mode_label,
            scan_protocol=support_context.scan_protocol,
        )

    def _request_support_prepare(
        self,
        *,
        run_log_file,
        target: str,
        protocol_label: str,
        mode_label: str,
        scan_protocol: str,
    ) -> None:
        payload = {
            "run_log_file": run_log_file,
            "target": str(target or ""),
            "protocol_label": str(protocol_label or ""),
            "mode_label": str(mode_label or ""),
            "scan_protocol": str(scan_protocol or ""),
        }
        state = self._support_prepare_state_obj()
        if state.is_busy():
            state.pending = dict(payload)
            self._set_support_status("Подготовка уже идёт...")
            return

        state.pending = None
        self._set_support_status("Подготовка обращения...")
        if self._prepare_support_btn is not None:
            self._prepare_support_btn.setEnabled(False)
        self._start_support_prepare_worker(payload)

    def _start_support_prepare_worker(self, payload: dict) -> None:
        def worker_factory(request_id: int):
            return self.create_support_prepare_worker(
                request_id,
                run_log_file=payload.get("run_log_file"),
                target=str(payload.get("target") or ""),
                protocol_label=str(payload.get("protocol_label") or ""),
                mode_label=str(payload.get("mode_label") or ""),
                scan_protocol=str(payload.get("scan_protocol") or ""),
            )

        def bind_worker(worker) -> None:
            worker.completed.connect(self._on_support_prepare_finished)
            worker.failed.connect(self._on_support_prepare_failed)

        self._support_prepare_runtime.start_qthread_worker(
            worker_factory=worker_factory,
            bind_worker=bind_worker,
            on_finished=self._on_support_prepare_runtime_finished,
        )

    def _on_support_prepare_finished(self, request_id: int, feedback) -> None:
        if not self._support_prepare_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        if self._support_prepare_state_obj().has_pending():
            return
        result = feedback.result
        archive_paths = list(getattr(result, "archive_paths", None) or ([result.zip_path] if result.zip_path else []))
        if archive_paths:
            logger.info("Prepared Strategy Scan support archive(s): %s", ", ".join(archive_paths))

        message_plan = self._blockcheck.build_support_success_plan(feedback)
        self._set_support_status(message_plan.status_text)

        try:
            InfoBarHelper.success(
                self.window(),
                tr_catalog(message_plan.title_key, default=message_plan.title_default),
                message_plan.body_text,
            )
        except Exception:
            pass

    def _on_support_prepare_failed(self, request_id: int, error: str) -> None:
        if not self._support_prepare_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        if self._support_prepare_state_obj().has_pending():
            return
        logger.warning("Failed to prepare strategy-scan support bundle: %s", error)
        message_plan = self._blockcheck.build_support_error_plan(str(error))
        self._set_support_status(message_plan.status_text)
        try:
            InfoBarHelper.warning(
                self.window(),
                tr_catalog(message_plan.title_key, default=message_plan.title_default),
                message_plan.body_text,
            )
        except Exception:
            pass

    def _on_support_prepare_runtime_finished(self, _worker) -> None:
        if not self._is_current_worker_finish(self.__dict__.get("_support_prepare_runtime"), _worker):
            return
        state = self._support_prepare_state_obj()
        had_pending = state.has_pending()
        state.schedule_pending_after_finish(
            _worker,
            is_current_worker_finish=self._is_current_worker_finish,
            single_shot=QTimer.singleShot,
            run_scheduled=self._run_scheduled_support_prepare_worker_start,
            cleanup_in_progress=self._cleanup_in_progress,
        )
        if had_pending and state.start_scheduled:
            return
        if not self._cleanup_in_progress and self._prepare_support_btn is not None:
            self._prepare_support_btn.setEnabled(True)

    def _schedule_support_prepare_worker_start(self, payload: dict) -> None:
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        state = self._support_prepare_state_obj()
        state.pending = dict(payload or {})
        state.schedule_start(
            QTimer.singleShot,
            self._run_scheduled_support_prepare_worker_start,
            cleanup_in_progress=self.__dict__.get("_cleanup_in_progress", False),
            pending_when_already_scheduled=dict(payload or {}),
        )

    def _run_scheduled_support_prepare_worker_start(self) -> None:
        pending = self._support_prepare_state_obj().take_pending_for_scheduled_start(
            cleanup_in_progress=self.__dict__.get("_cleanup_in_progress", False)
        )
        if pending is None or self.__dict__.get("_cleanup_in_progress", False):
            return
        self._set_support_status("Подготовка обращения...")
        if self._prepare_support_btn is not None:
            self._prepare_support_btn.setEnabled(False)
        self._start_support_prepare_worker(dict(pending or {}))

    # ------------------------------------------------------------------
    # Language
    # ------------------------------------------------------------------

    def set_ui_language(self, language: str) -> None:
        super().set_ui_language(language)
        try:
            self._apply_language_plan(language)
            self._refresh_udp_scope_hint()
            self._update_control_accessibility()
            self._refresh_mode_hint()
            self._set_status_text(self._status_label.text())
        except Exception:
            pass

    def cleanup(self) -> None:
        self._cleanup_in_progress = True
        self._strategy_apply_runtime.stop(
            blocking=False,
            warning_prefix="strategy scan apply worker",
        )
        self._strategy_apply_runtime.cancel()
        self._strategy_apply_state_obj().reset()
        self._support_prepare_runtime.stop(
            blocking=False,
            warning_prefix="strategy scan support prepare worker",
        )
        self._support_prepare_runtime.cancel()
        self._support_prepare_state_obj().reset()
        self._quick_targets_state_obj().reset()
        self._quick_targets_runtime.stop(
            blocking=False,
            warning_prefix="strategy scan quick targets worker",
        )
        self._quick_targets_runtime.cancel()
        self._strategy_scan_finalize_state_obj().reset()
        self._strategy_scan_finalize_runtime.stop(
            blocking=False,
            warning_prefix="strategy scan finalize worker",
        )
        self._strategy_scan_finalize_runtime.cancel()
        self._strategy_scan_run_runtime.stop(
            blocking=False,
            warning_prefix="strategy scan run worker",
        )
        self._strategy_scan_run_runtime.cancel()
