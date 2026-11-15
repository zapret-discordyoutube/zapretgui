# startup/show_window_bridge.py

from __future__ import annotations

from log.log import log

from PyQt6.QtCore import QObject, pyqtSignal

from platforms import current as current_platform


class ShowWindowBridge(QObject):
    """Доставляет сигнал «показать окно» от второго экземпляра в главный поток Qt.

    Поток-наблюдатель ждёт сигнал (см. platforms/base.py: SingleInstance) и
    эмитит show_requested; queued connection переносит вызов в главный поток.
    """

    show_requested = pyqtSignal()

    def __init__(self, window):
        super().__init__()
        self.window = window
        self._watcher_thread = None

    def start(self) -> bool:
        """Подключает обработчик и запускает наблюдатель события.

        False — сигнал не был создан (платформа его не поддерживает или создание не прошло).
        """
        self.show_requested.connect(self._handle_show_window)
        self._watcher_thread = current_platform().single_instance.start_show_watcher(self.show_requested.emit)
        if self._watcher_thread is None:
            log("Наблюдатель события показа окна не запущен", "WARNING")
            return False
        return True

    def _handle_show_window(self):
        """Обработчик сигнала показа окна (выполняется в главном потоке)"""
        if self.window:
            tray_manager = getattr(getattr(self.window, "visual_state", None), "tray_manager", None)
            if tray_manager is not None:
                tray_manager.show_window()
            else:
                from ui.window_adapter import show_window

                show_window(self.window)
            log("Окно показано по запросу от другого экземпляра", "INFO")
