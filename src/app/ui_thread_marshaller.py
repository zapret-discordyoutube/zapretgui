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

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal


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


__all__ = ["QtUiThreadMarshaller"]
