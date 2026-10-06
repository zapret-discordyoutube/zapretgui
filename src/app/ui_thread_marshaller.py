"""Перевод колбэков в GUI-поток для не-Qt слоёв состояния.

`MainWindowStateStore` намеренно не знает про Qt, но его подписчики — живые
виджеты. Любая правка QWidget вне GUI-потока на Windows уходит в `SendMessage`
к оконному потоку, который не может ответить, пока вызывающий фоновый поток
держит GIL, — окно зависает навсегда. Маршалер даёт слою состояния минимальный
duck-typed контракт (`is_ui_thread()` / `post()`), чтобы доставка колбэков всегда
происходила в потоке, которому виджеты принадлежат.
"""

from __future__ import annotations

from collections.abc import Callable

import threading

from PyQt6.QtCore import QCoreApplication, QObject, QThread, Qt, pyqtSignal


class QtUiThreadMarshaller(QObject):
    """Исполняет переданные вызовы в потоке, где создан сам маршалер.

    Создавать строго в GUI-потоке: `post()` доставляет вызов через
    `QueuedConnection`, то есть в поток-владелец этого QObject.
    """

    _posted = pyqtSignal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ui_thread = QThread.currentThread()
        self._posted.connect(self._run_posted, Qt.ConnectionType.QueuedConnection)

    def is_ui_thread(self) -> bool:
        return QThread.currentThread() is self._ui_thread

    def post(self, action: Callable[[], None]) -> None:
        self._posted.emit(action)

    def _run_posted(self, action) -> None:
        if not callable(action):
            return
        action()


_shared_marshaller: QtUiThreadMarshaller | None = None


def install_shared_ui_thread_marshaller() -> QtUiThreadMarshaller:
    """Общий маршалер программы. Первый вызов — из потока окна, при запуске.

    Один на всю программу: им пользуются слой состояния окна, службы с
    подписчиками (настройки программы, проверка обновлений) и страж плашек.
    """
    global _shared_marshaller
    if _shared_marshaller is None:
        _shared_marshaller = QtUiThreadMarshaller()
    return _shared_marshaller


def shared_ui_thread_marshaller() -> QtUiThreadMarshaller | None:
    """Общий маршалер или None, если окно ещё не собрано (или его нет вовсе)."""
    return _shared_marshaller


def reset_shared_ui_thread_marshaller() -> None:
    """Сбрасывает общий маршалер. Нужно тестам, живому коду — нет."""
    global _shared_marshaller
    _shared_marshaller = None


def ensure_window_thread_affinity(obj: QObject, what: str) -> bool:
    """Общий QObject должен жить в потоке окна, кто бы его ни создал.

    Общие объекты с сигналами (шина событий пресетов, менеджер Telegram Proxy)
    создаются по первому требованию, и первым может оказаться фоновый поток.
    QObject принадлежит потоку, который его создал: сигналы такого объекта
    доставлялись бы в поток без цикла событий (то есть никуда) либо
    выполнялись бы прямо в фоновом потоке и оттуда трогали окно. Поэтому сразу
    после создания объект переносится в поток окна.

    Вызывать из потока, создавшего объект, до первого подключения сигналов.
    Возвращает True, если объект пришлось перенести.
    """
    app = QCoreApplication.instance()
    if app is None:
        # Без приложения Qt (юнит-тесты) потока окна нет.
        return False
    window_thread = app.thread()
    if obj.thread() is window_thread:
        return False
    obj.moveToThread(window_thread)
    try:
        from log.log import log

        log(
            f"{what} создан в фоновом потоке «{threading.current_thread().name}» "
            "и перенесён в поток окна",
            "DEBUG",
        )
    except Exception:
        pass
    return True


__all__ = [
    "QtUiThreadMarshaller",
    "ensure_window_thread_affinity",
    "install_shared_ui_thread_marshaller",
    "reset_shared_ui_thread_marshaller",
    "shared_ui_thread_marshaller",
]
