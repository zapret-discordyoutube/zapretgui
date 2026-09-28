"""Runtime/helper слой для ConnectionTestPage."""

from __future__ import annotations

from PyQt6.QtCore import QTimer

import diagnostics.page_plans as connection_page_plans
from app.ui_texts import tr as tr_catalog
from diagnostics.ui.components import clean_connection_status_text
from ui.accessibility import set_control_accessibility, set_state_text
from ui.combo_accessibility import set_combo_items_accessibility
from ui.fluent_widgets import set_tooltip


def apply_interaction_state(
    *,
    start_btn,
    stop_btn,
    test_combo,
    send_log_btn,
    progress_bar,
    start_enabled: bool,
    stop_enabled: bool,
    combo_enabled: bool,
    send_log_enabled: bool,
    progress_visible: bool,
) -> None:
    start_btn.setEnabled(start_enabled)
    stop_btn.setEnabled(stop_enabled)
    test_combo.setEnabled(combo_enabled)
    send_log_btn.setEnabled(send_log_enabled)
    set_state_text(
        start_btn,
        f"Запустить диагностический тест, {'доступно' if start_enabled else 'недоступно'}",
    )
    set_state_text(
        stop_btn,
        f"Остановить диагностический тест, {'доступно' if stop_enabled else 'недоступно'}",
    )
    set_state_text(
        send_log_btn,
        f"Подготовить обращение с логами, {'доступно' if send_log_enabled else 'недоступно'}",
    )
    stop_btn.setVisible(progress_visible)
    progress_bar.setVisible(progress_visible)
    progress_state = "выполняется" if progress_visible else "не выполняется"
    set_state_text(progress_bar, f"Ход диагностики соединений: {progress_state}")

    if progress_visible:
        progress_bar.start()
    else:
        progress_bar.stop()


def set_connection_status(*, status_label, text: str, status: str = "muted") -> None:
    _ = status
    status_label.setText(text)
    value = clean_connection_status_text(text)
    if value:
        set_state_text(status_label, f"Статус диагностики: {value}")


def refresh_test_combo_items(*, combo, language: str) -> None:
    current = combo.currentIndex() if combo is not None else 0
    items = [
        (
            tr_catalog("page.connection.test.all", language=language, default="Discord и YouTube"),
            "all",
        ),
        (
            tr_catalog("page.connection.test.discord_only", language=language, default="Только Discord"),
            "discord",
        ),
        (
            tr_catalog("page.connection.test.youtube_only", language=language, default="Только YouTube"),
            "youtube",
        ),
    ]
    combo.clear()
    for label, test_type in items:
        combo.addItem(label)
        combo.setItemData(combo.count() - 1, test_type)
    combo.setCurrentIndex(max(0, min(current, len(items) - 1)))
    _update_test_combo_accessibility(combo)
    _ensure_test_combo_accessibility_signal(combo)


def _update_test_combo_accessibility(combo) -> None:
    selected = _clean_combo_text(combo.currentText())
    name = "Сценарий диагностики"
    if selected:
        name = f"{name}, выбрано: {selected}"
    set_state_text(combo, name)
    set_control_accessibility(
        combo,
        name=name,
        description=(
            "Выберите, какие соединения проверить: Discord и YouTube, только Discord или только YouTube. "
            "Откройте список и выберите сценарий стрелками вверх и вниз."
        ),
    )
    set_combo_items_accessibility(combo, name="Сценарий диагностики", clean_label=_clean_combo_text)


def _ensure_test_combo_accessibility_signal(combo) -> None:
    if bool(getattr(combo, "_diagnostics_test_combo_accessibility_connected", False)):
        return
    try:
        combo.currentIndexChanged.connect(lambda _index: _update_test_combo_accessibility(combo))
        setattr(combo, "_diagnostics_test_combo_accessibility_connected", True)
    except Exception:
        pass


def _clean_combo_text(text: object) -> str:
    value = " ".join(str(text or "").strip().split())
    for marker in ("🌐", "🎮", "🎬"):
        value = value.replace(marker, "")
    return " ".join(value.split())


def start_connection_test(
    *,
    is_testing: bool,
    ui_language: str,
    test_combo,
    result_text,
    apply_interaction_state_callback,
    set_status_callback,
) -> dict | None:
    if is_testing:
        result_text.append("ℹ️ Тест уже выполняется. Дождитесь завершения.")
        return None

    selection = test_combo.currentText()
    test_type = test_combo.currentData() or "all"
    plan = connection_page_plans.build_start_plan(
        selection=selection,
        test_type=str(test_type),
    )

    result_text.clear()
    for line in plan.start_lines:
        result_text.append(line)
    first_line = _clean_combo_text(
        clean_connection_status_text(plan.start_lines[0] if plan.start_lines else "").replace("🚀", "")
    )
    if first_line:
        set_state_text(result_text, f"Результат диагностики соединений: {first_line}")

    apply_interaction_state_callback(
        start_enabled=plan.start_enabled,
        stop_enabled=plan.stop_enabled,
        combo_enabled=plan.combo_enabled,
        send_log_enabled=plan.send_log_enabled,
        progress_visible=plan.progress_visible,
    )
    set_status_callback(plan.status_text, plan.status_tone)

    return {
        "cleanup_in_progress": False,
        "finish_mode": "completed",
        "is_testing": True,
        "test_type": plan.test_type,
    }


def stop_connection_test(
    *,
    page,
    runtime,
    stop_check_timer,
    append_callback,
    set_status_callback,
    stop_btn,
    worker_finished_handler,
) -> tuple[dict | None, object | None]:
    worker = getattr(runtime, "worker", None)
    if not worker or not runtime.is_running():
        return None, stop_check_timer
    if stop_check_timer is not None:
        return None, stop_check_timer

    plan = connection_page_plans.build_stop_plan()
    for line in plan.append_lines:
        append_callback(line)
    set_status_callback(plan.status_text, plan.status_tone)
    stop_btn.setEnabled(False)
    worker.stop_gracefully()

    attempts = {"count": 0}
    timer = QTimer(page)

    def check_thread():
        attempts["count"] += 1
        poll_plan = connection_page_plans.build_stop_poll_plan(
            attempt_count=attempts["count"],
            thread_running=runtime.is_running(),
            max_attempts=plan.max_attempts,
            finalize_delay_ms=plan.finalize_delay_ms,
        )
        if poll_plan.action == "finish":
            timer.stop()
            if poll_plan.append_line:
                append_callback(poll_plan.append_line)
            worker_finished_handler()
        elif poll_plan.action == "force_terminate":
            timer.stop()
            if poll_plan.append_line:
                append_callback(poll_plan.append_line)
            target = getattr(runtime, "thread", None) or getattr(runtime, "worker", None)
            terminate = getattr(target, "terminate", None)
            if callable(terminate):
                terminate()
                QTimer.singleShot(poll_plan.finalize_delay_ms, worker_finished_handler)

    timer.timeout.connect(check_thread)
    timer.start(plan.poll_interval_ms)
    return {"finish_mode": "stopped"}, timer


def apply_worker_update(*, message: str, append_callback, result_text) -> None:
    append_callback(message)

    scrollbar = result_text.verticalScrollBar()
    scrollbar.setValue(scrollbar.maximum())


def release_worker_resources(worker) -> None:
    if worker is None:
        return
    release = getattr(worker, "release_resources", None)
    if not callable(release):
        return
    try:
        release()
    except Exception:
        pass


def finish_connection_test(
    *,
    cleanup_in_progress: bool,
    is_testing: bool,
    runtime,
    stop_check_timer,
    finish_mode: str,
    apply_interaction_state_callback,
    set_status_callback,
    append_callback,
) -> dict | None:
    if cleanup_in_progress:
        return None
    if not is_testing and not runtime.is_running():
        return None

    if stop_check_timer is not None:
        stop_check_timer.stop()
        stop_check_timer.deleteLater()

    release_worker_resources(getattr(runtime, "worker", None))

    if finish_mode == "stopped":
        plan = connection_page_plans.build_stopped_finish_plan()
    else:
        plan = connection_page_plans.build_finish_plan()

    apply_interaction_state_callback(
        start_enabled=plan.start_enabled,
        stop_enabled=plan.stop_enabled,
        combo_enabled=plan.combo_enabled,
        send_log_enabled=plan.send_log_enabled,
        progress_visible=plan.progress_visible,
    )
    set_status_callback(plan.status_text, plan.status_tone)
    for line in plan.finish_lines:
        append_callback(line)

    return {
        "finish_mode": "completed",
        "is_testing": False,
        "stop_check_timer": None,
    }


def apply_connection_language(
    *,
    language: str,
    test_select_label,
    log_hint_label,
    refresh_test_combo_items_callback,
    start_btn,
    stop_btn,
    toggle_log_btn,
    log_visible: bool,
    send_log_btn,
) -> None:
    def _tr(key: str, default: str) -> str:
        return tr_catalog(key, language=language, default=default)

    test_select_label.setText(_tr("page.connection.test.select", "Что проверить:"))
    log_hint_label.setText(
        _tr(
            "page.connection.log.hint",
            "Подробный отчёт: адреса, ответы DNS и время ответа серверов. Пригодится поддержке.",
        )
    )
    refresh_test_combo_items_callback()

    def _set_action_accessibility(widget, *, name: str, description: str) -> None:
        set_control_accessibility(widget, name=name, description=description)
        set_state_text(widget, name)

    start_btn.setText(_tr("page.connection.button.start", "Проверить"))
    stop_btn.setText(_tr("page.connection.button.stop", "Остановить"))
    send_log_btn.setText(_tr("page.connection.button.send_log", "Подготовить обращение"))
    set_log_toggle_text(toggle_log_btn, visible=log_visible, language=language)

    start_description = _tr(
        "page.connection.action.start.description",
        "Проверить, открываются ли Discord и YouTube и не подменяет ли DNS их адреса.",
    )
    set_tooltip(start_btn, start_description)
    _set_action_accessibility(
        start_btn,
        name=_tr("page.connection.action.start.accessible_name", "Запустить диагностический тест"),
        description=start_description,
    )
    stop_description = _tr(
        "page.connection.action.stop.description",
        "Останавливает текущий тест, если он уже запущен.",
    )
    set_tooltip(stop_btn, stop_description)
    _set_action_accessibility(
        stop_btn,
        name=_tr("page.connection.action.stop.accessible_name", "Остановить диагностический тест"),
        description=stop_description,
    )
    support_description = _tr(
        "page.connection.action.support.description",
        "Собрать архив логов и открыть готовое обращение в Forgejo Issues.",
    )
    set_tooltip(send_log_btn, support_description)
    _set_action_accessibility(
        send_log_btn,
        name=_tr("page.connection.action.support.accessible_name", "Подготовить обращение с логами"),
        description=support_description,
    )


def set_log_toggle_text(toggle_btn, *, visible: bool, language: str) -> None:
    if toggle_btn is None:
        return
    if visible:
        text = tr_catalog("page.connection.button.hide_log", language=language, default="Скрыть отчёт")
    else:
        text = tr_catalog("page.connection.button.show_log", language=language, default="Показать отчёт")
    toggle_btn.setText(text)
    set_state_text(toggle_btn, f"{text}, подробный отчёт {'открыт' if visible else 'скрыт'}")


def cleanup_connection_runtime(
    *,
    cleanup_in_progress: bool,
    finish_mode: str,
    stop_check_timer,
    runtime,
    log_debug,
    log_warning,
) -> dict:
    _ = cleanup_in_progress, finish_mode
    if stop_check_timer is not None:
        stop_check_timer.stop()
        stop_check_timer.deleteLater()
        stop_check_timer = None

    worker = getattr(runtime, "worker", None)
    thread_running = runtime.is_running()
    cleanup_plan = connection_page_plans.build_cleanup_plan(
        has_worker=worker is not None,
        thread_running=thread_running,
    )
    if cleanup_plan.should_quit_thread and thread_running:
        log_debug("Останавливаем connection test worker...")
    runtime.stop(
        blocking=False,
        wait_timeout_ms=cleanup_plan.wait_timeout_ms,
        terminate_wait_ms=cleanup_plan.terminate_wait_ms,
        log_fn=lambda text, level="DEBUG": log_warning(text) if str(level).upper() == "WARNING" else log_debug(text),
        warning_prefix="connection_test_worker",
    )
    if not thread_running:
        release_worker_resources(worker)
    runtime.cancel()
    return {
        "cleanup_in_progress": True,
        "finish_mode": "completed",
        "is_testing": False,
        "stop_check_timer": None,
    }
