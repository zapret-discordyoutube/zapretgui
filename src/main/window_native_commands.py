from __future__ import annotations

from log.log import log


def handle_minimize_request(window, *, minimize_to_tray_enabled=None) -> bool:
    """Общий обработчик команды «свернуть окно». Системные сообщения разбирает платформа (``Platform.desktop``)."""
    try:
        if not _is_minimize_to_tray_enabled(minimize_to_tray_enabled):
            return False
        return bool(window.close_to_tray())
    except Exception as exc:
        log(f"Не удалось обработать команду сворачивания в трей: {exc}", "DEBUG")
        return False


def _is_minimize_to_tray_enabled(provider) -> bool:
    if not callable(provider):
        return False
    return bool(provider())


__all__ = ["handle_minimize_request"]
