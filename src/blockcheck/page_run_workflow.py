"""Запуск и остановка проверки BlockCheck: состояние кнопок и фоновый поток."""

from collections.abc import Callable

from PyQt6.QtCore import QTimer

from ui.accessibility import set_state_text


def _set_running_controls(*, start_button, stop_button, scope_combo, progress_bar, running: bool) -> None:
    start_button.setEnabled(not running)
    stop_button.setEnabled(running)
    # «Остановить» нужна только во время проверки.
    stop_button.setVisible(running)
    scope_combo.setEnabled(not running)
    set_state_text(start_button, f"Запустить BlockCheck, {'недоступно' if running else 'доступно'}")
    set_state_text(stop_button, f"Остановить BlockCheck, {'доступно' if running else 'недоступно'}")
    set_state_text(
        scope_combo,
        "Что проверить BlockCheck, недоступно во время проверки" if running else "Что проверить BlockCheck, доступно",
    )
    progress_bar.setVisible(running)
    set_state_text(progress_bar, f"Ход BlockCheck: {'выполняется' if running else 'не выполняется'}")
    if running and hasattr(progress_bar, "start"):
        progress_bar.start()
    elif not running and hasattr(progress_bar, "stop"):
        progress_bar.stop()


def start_blockcheck_page_run(
    *,
    blockcheck_feature,
    scope: str,
    user_domains: list[str],
    parent,
    run_runtime,
    start_button,
    stop_button,
    scope_combo,
    progress_bar,
    status_label,
    set_support_status: Callable[[str], None],
    tr_fn: Callable[..., str],
    on_log,
    on_run_log_started,
    on_finished,
) -> None:
    """Готовит экран и запускает фоновую проверку BlockCheck."""
    set_support_status("")
    _set_running_controls(
        start_button=start_button,
        stop_button=stop_button,
        scope_combo=scope_combo,
        progress_bar=progress_bar,
        running=True,
    )
    running_text = tr_fn("page.blockcheck.running", default="Проверяем… обычно это 5–30 секунд")
    status_label.setText(running_text)
    set_state_text(status_label, f"Статус BlockCheck: {running_text}")

    worker = blockcheck_feature.create_blockcheck_worker(
        scope=scope,
        user_domains=list(user_domains or []),
        parent=None,
    )
    worker.log_message.connect(on_log)
    worker.run_log_started.connect(on_run_log_started)
    worker.finished.connect(on_finished)
    run_runtime.start_qobject_worker(
        parent=parent,
        worker_factory=lambda _request_id: worker,
    )


def request_blockcheck_stop(
    *,
    worker,
    stop_button,
    status_label,
    force_stop: Callable[[object], None],
    tr_fn: Callable[..., str],
) -> None:
    """Просит поток остановиться; через 5 с снимает его принудительно."""
    expected_worker = None
    if worker is not None:
        worker.stop()
        expected_worker = worker

    stop_button.setEnabled(False)
    set_state_text(stop_button, "Остановить BlockCheck, недоступно")
    stopping_text = tr_fn("page.blockcheck.stopping", default="Останавливаем…")
    status_label.setText(stopping_text)
    set_state_text(status_label, f"Статус BlockCheck: {stopping_text}")
    QTimer.singleShot(5000, lambda worker=expected_worker: force_stop(worker))


def reset_blockcheck_running_ui(*, start_button, stop_button, scope_combo, progress_bar) -> None:
    """Возвращает кнопки и индикатор в обычное состояние."""
    _set_running_controls(
        start_button=start_button,
        stop_button=stop_button,
        scope_combo=scope_combo,
        progress_bar=progress_bar,
        running=False,
    )
