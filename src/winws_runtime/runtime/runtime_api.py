import os
from typing import Optional, Callable

from log.log import log
from settings.mode import EXE_NAME_WINWS1, WINWS_ENGINE_FAMILY_LABEL

from .process_probe import (
    get_canonical_winws_process_pids,
    is_expected_winws_running,
)
from .system_ops import (
    release_windivert_driver_runtime,
    stop_own_winws_processes_runtime,
)


class PresetLaunchRuntimeApi:
    """Низкоуровневый runtime-слой запуска: статус процесса, ожидаемый exe и очистка WinDivert."""

    def __init__(
        self,
        expected_exe_path: str,
        status_callback: Optional[Callable[[str], None]] = None,
    ):
        """
        Инициализирует PresetLaunchRuntimeApi.

        Args:
            expected_exe_path: Путь к ожидаемому winws.exe/winws2.exe
            status_callback: Функция обратного вызова для отображения статуса
        """
        self.expected_exe_path = expected_exe_path
        self.status_callback = status_callback

    def _set_status(self, text: str) -> None:
        """Внутренний метод для установки статуса"""
        if self.status_callback:
            self.status_callback(text)

    def set_status(self, text: str) -> None:
        """Отображает статусное сообщение."""
        if self.status_callback:
            self.status_callback(text)
        else:
            print(text)

    def set_expected_exe_path(self, exe_path: str) -> None:
        self.expected_exe_path = str(exe_path or "").strip()

    def is_any_running(self, silent: bool = False) -> bool:
        """
        Проверка семейства winws через канонический WinAPI probe.

        Считает запущенными только процессы, чей полный путь совпадает
        с ожидаемыми `exe/winws.exe` и `exe/winws2.exe` этого проекта.
        """
        try:
            is_running = bool(get_canonical_winws_process_pids())
            if not silent:
                log(f"{WINWS_ENGINE_FAMILY_LABEL} state → {is_running} (WinAPI canonical)", "DEBUG")
            return is_running
        except Exception as e:
            if not silent:
                log(f"WinAPI canonical check error: {e}", "DEBUG")
            return False

    def has_residual_processes(self, silent: bool = False) -> bool:
        """Остались ли процессы winws/winws2 из папки программы.

        Считаются только свои процессы — те, чей полный путь совпадает с
        exe этого проекта. Чужой winws (другая копия запрета) программа не
        останавливает, поэтому и «остатком» он не является: иначе остановка
        вечно докладывала бы «не удалось остановить».
        """
        running = bool(self.is_any_running(silent=True))
        if not silent:
            log(f"Residual {WINWS_ENGINE_FAMILY_LABEL} → {running} (WinAPI canonical)", "DEBUG")
        return running

    def is_expected_running(self, silent: bool = False) -> bool:
        """
        Проверка только текущего ожидаемого exe из `self.expected_exe_path`.

        Это нужно там, где нам важен именно активный режим запуска,
        а не любой процесс семейства winws.
        """
        try:
            is_running = bool(is_expected_winws_running(self.expected_exe_path))
            if not silent:
                exe_name = os.path.basename(self.expected_exe_path) or EXE_NAME_WINWS1
                log(f"{exe_name} state → {is_running} (WinAPI canonical)", "DEBUG")
            return is_running
        except Exception as e:
            if not silent:
                log(f"Expected WinAPI canonical check error: {e}", "DEBUG")
            return False

    def cleanup_windivert_service(self) -> bool:
        """Выгружает драйвер WinDivert после полной остановки обхода.

        Вызывается, когда обход остановлен окончательно (кнопка «Стоп», выход
        из программы, обновление), но не при перезапуске и не при смене
        пресета. Драйвер выгружается, только если им никто не пользуется.

        Возвращает False, только если служба драйвера застряла.
        """
        try:
            result = release_windivert_driver_runtime()
        except Exception as e:
            log(f"Ошибка выгрузки драйвера WinDivert: {e}", "⚠ WARNING")
            return False
        if result.stuck and result.message:
            log(result.message, "WARNING")
        return not result.stuck

    def stop_all_processes(self) -> bool:
        """Останавливает свои процессы DPI. True — выход всех подтверждён."""
        log("Останавливаем процессы winws из папки программы...", "INFO")

        ok = False
        try:
            ok = bool(stop_own_winws_processes_runtime())
        except Exception as e:
            log(f"Ошибка остановки через Win API: {e}", "⚠ WARNING")

        log("Все процессы остановлены" if ok else f"{WINWS_ENGINE_FAMILY_LABEL} ещё работает",
            "✅ SUCCESS" if ok else "⚠ WARNING")
        return ok
