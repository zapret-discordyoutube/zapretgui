"""Плашки InfoBar создаются только в потоке окна.

Создать элемент окна из фонового потока на Windows нельзя: вызывающий поток
ждёт ответа потока окна, а тот не может ответить, пока вызывающий держит GIL.
Программа встаёт целиком, окно белеет, журнал обрывается без ошибки — так в
Dev 21.1.7.19 фоновая задача запуска подвесила программу своим уведомлением.

Ошибку легко повторить: плашку показывают из десятков мест, и по коду не
видно, в каком потоке окажется вызов. Поэтому правило проверяется в одной
точке, через которую проходит создание любой плашки (InfoBar.new: фабрики
success/info/warning/error идут через него). Вызов из чужого потока плашку не
создаёт: показ переносится в поток окна, а в журнал один раз на место вызова
пишется, кто нарушил правило, — чтобы исправить вызывающий код.

Фоновому коду показывать уведомления положено через
WindowNotificationCenter.notify (ui/window_notification_center.py): он сам
переносит показ в поток окна. Этот страж — страховка для всего остального.
"""

from __future__ import annotations

import threading
import traceback

from app.ui_thread_marshaller import install_shared_ui_thread_marshaller

_PATCHED_ATTR = "_zapret_window_thread_guard_installed"
_ORIGINAL_NEW_ATTR = "_zapret_new_before_window_thread_guard"
# Столько последних строк стека попадает в журнал: по ним видно место вызова.
_REPORTED_STACK_DEPTH = 6

_reported_call_sites: set[tuple[str, int]] = set()
_reported_lock = threading.Lock()


def _log(message: str, level: str) -> None:
    try:
        from log.log import log

        log(message, level)
    except Exception:
        pass


def _report_foreign_thread_call() -> None:
    """Пишет в журнал место вызова из фонового потока — один раз на место."""
    stack = traceback.extract_stack()[:-2]
    caller = stack[-1] if stack else None
    site = (str(getattr(caller, "filename", "")), int(getattr(caller, "lineno", 0) or 0))
    with _reported_lock:
        if site in _reported_call_sites:
            return
        _reported_call_sites.add(site)
    tail = "".join(traceback.format_list(stack[-_REPORTED_STACK_DEPTH:])).rstrip()
    _log(
        "InfoBar запрошен из фонового потока "
        f"«{threading.current_thread().name}»: показ перенесён в поток окна. "
        "Фоновый код должен показывать уведомления через WindowNotificationCenter.notify "
        "или сигнал Qt, а не создавать плашку сам.\n"
        f"{tail}",
        "WARNING",
    )


def install_infobar_window_thread_guard(info_bar_cls: type | None = None) -> None:
    """Один раз ставит страж потока на создание плашек. Вызывать из потока окна.

    Ставится последним из перехватчиков InfoBar.new: проверка потока должна
    идти раньше любого кода, который читает размеры окна-родителя.
    Вызов из чужого потока возвращает None — плашки в этот момент ещё нет.
    """
    if info_bar_cls is None:
        from qfluentwidgets import InfoBar

        info_bar_cls = InfoBar

    if bool(getattr(info_bar_cls, _PATCHED_ATTR, False)):
        return

    original_new = getattr(info_bar_cls, "new")
    # Общий маршалер программы: тот же, что у слоя состояния и служб с подписчиками.
    window_thread = install_shared_ui_thread_marshaller()

    def new_in_window_thread(cls, *args, **kwargs):
        if window_thread.is_ui_thread():
            return original_new(*args, **kwargs)
        _report_foreign_thread_call()

        def show_in_window_thread() -> None:
            try:
                original_new(*args, **kwargs)
            except Exception as exc:
                _log(f"Плашку, запрошенную из фонового потока, показать не удалось: {exc}", "WARNING")

        window_thread.post(show_in_window_thread)
        return None

    setattr(info_bar_cls, _ORIGINAL_NEW_ATTR, original_new)
    setattr(info_bar_cls, "new", classmethod(new_in_window_thread))
    setattr(info_bar_cls, _PATCHED_ATTR, True)


def reset_reported_call_sites() -> None:
    """Сбрасывает список уже записанных мест вызова. Нужно тестам."""
    with _reported_lock:
        _reported_call_sites.clear()


__all__ = ["install_infobar_window_thread_guard", "reset_reported_call_sites"]
