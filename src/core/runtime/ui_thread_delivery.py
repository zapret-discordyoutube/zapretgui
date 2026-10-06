"""Доставка обратных вызовов подписчикам в поток окна.

Службы состояния этого слоя про Qt не знают, но их подписчики — живые
страницы. Опубликовать новое состояние может фоновый работник, и вызванный
прямо из него подписчик тронул бы элементы окна из чужого потока: на Windows
это останавливает программу целиком. Поэтому служба не зовёт подписчиков сама,
а отдаёт вызов сюда.

Маршалер приходит снаружи (duck-typed контракт ``is_ui_thread()`` / ``post()``,
см. ``app/ui_thread_marshaller.py``) и берётся в момент доставки: служба может
быть создана раньше, чем окно. Без маршалера (юнит-тесты, программа без окна)
доставка остаётся синхронной.
"""

from __future__ import annotations

from collections.abc import Callable

UiThreadMarshallerProvider = Callable[[], object | None]


def deliver_in_ui_thread(marshaller_provider: UiThreadMarshallerProvider | None, action: Callable[[], None]) -> None:
    """Выполняет ``action`` в потоке окна: сразу, если вызвано из него, иначе через очередь окна."""
    marshaller = None
    if callable(marshaller_provider):
        try:
            marshaller = marshaller_provider()
        except Exception:
            marshaller = None
    if marshaller is not None:
        try:
            in_ui_thread = bool(marshaller.is_ui_thread())
        except Exception:
            # Не удалось узнать поток — считаем его чужим.
            in_ui_thread = False
        if not in_ui_thread:
            marshaller.post(action)
            return
    action()


__all__ = ["UiThreadMarshallerProvider", "deliver_in_ui_thread"]
