import threading

from PyQt6.QtCore import QThread, pyqtSignal

from winws_runtime.runtime.process_probe import WinwsProcessScan, scan_winws_processes


class ProcessMonitorThread(QThread):
    """
    Следит за каноническими процессами winws.exe/winws2.exe через WinAPI.
    Шлёт сигнал когда состояние (запущен/остановлен) изменилось.
    Параллельно замечает посторонние winws-процессы (то же имя, чужой путь).
    """
    processStatusChanged = pyqtSignal(bool)          # True / False
    processDetailsChanged = pyqtSignal(dict)         # имя exe -> список PID
    foreignProcessesChanged = pyqtSignal(dict)       # pid -> путь чужого winws
    checkingStarted = pyqtSignal()                   # Начало проверки
    checkingFinished = pyqtSignal()                  # Конец проверки

    def __init__(self, interval_ms: int = 5000):
        """
        Args:
            interval_ms: Интервал проверки в миллисекундах (по умолчанию 5 сек)
        """
        super().__init__()
        self.interval_ms   = interval_ms
        self._running      = True
        # Пауза между проверками ждёт это событие, а не просто спит: остановка
        # будит поток сразу, и его можно дождаться за миллисекунды.
        self._stop_requested = threading.Event()
        self._cur_state: bool | None = None
        self._cur_details: dict[str, list[int]] | None = None
        self._cur_foreign: dict[int, str] | None = None

    def _scan_processes_fast(self) -> WinwsProcessScan:
        """
        Канонические winws.exe/winws2.exe (PID) и посторонние (PID -> путь)
        за один снимок процессов.

        Канонический здесь означает:
        - имя процесса совпадает;
        - полный путь процесса совпадает с ожидаемым `exe/winws*.exe` проекта.
        """
        try:
            return scan_winws_processes()
        except Exception:
            return WinwsProcessScan(canonical_pids={}, foreign_paths={})

    # ------------------------- ОСНОВНОЙ ЦИКЛ --------------------------
    def run(self):
        from log.log import log            # импорт здесь, чтобы не было циклических импортов
        log("Process-monitor thread started (WinAPI canonical mode)", level="INFO")

        while self._running:
            try:
                # 🔄 Сигнализируем о начале проверки
                self.checkingStarted.emit()
                
                scan = self._scan_processes_fast()
                details = scan.canonical_pids
                is_running = bool(details)
                
                # 🔄 Сигнализируем об окончании проверки
                self.checkingFinished.emit()

                # Если детали изменились — отдаём сигнал в GUI (важно: PID может поменяться без смены bool)
                if details != self._cur_details:
                    self._cur_details = details
                    self.processDetailsChanged.emit(details)

                foreign = scan.foreign_paths
                if foreign != self._cur_foreign:
                    self._cur_foreign = foreign
                    self.foreignProcessesChanged.emit(foreign)

                # Если состояние изменилось — отдаём сигнал в GUI
                if is_running != self._cur_state:
                    self._cur_state = is_running
                    log(f"canonical winws state → {is_running}", level="DEBUG")
                    self.processStatusChanged.emit(is_running)

            except Exception as e:
                from log.log import log

                log(f"Ошибка в потоке мониторинга: {e}", level="❌ ERROR")
                self.checkingFinished.emit()  # На случай ошибки тоже завершаем

            if self._stop_requested.wait(self.interval_ms / 1000.0):
                break

    # ------------------------ СТАНДАРТНЫЙ STOP ------------------------
    def stop(self):
        """Просит поток остановиться и не ждёт его (окно не подвисает)."""
        self._running = False
        self._stop_requested.set()
        self.quit()
