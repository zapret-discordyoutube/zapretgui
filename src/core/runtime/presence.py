from __future__ import annotations

"""Видно ли окно программы прямо сейчас.

Окно знает об этом само (показали, свернули, убрали в трей) и отмечает здесь;
читать можно из любого потока, не трогая Qt. Нужно автообновлению: вместе с
вопросом «есть новее?» программа называет серверу, открыта она или висит в
трее, — на сайте виден общий счёт, чем заняты программы.
"""

import threading

_lock = threading.Lock()
_window_shown: bool | None = None


def note_window_shown(shown: bool) -> None:
    global _window_shown
    with _lock:
        _window_shown = bool(shown)


def window_shown() -> bool | None:
    """True — окно на экране, False — свёрнуто или в трее, None — ещё не показывалось."""
    with _lock:
        return _window_shown


__all__ = ["note_window_shown", "window_shown"]
