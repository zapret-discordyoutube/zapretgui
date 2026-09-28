"""Запуск одной стратегии в winws2 и её остановка.

Как понять, что winws2 готов: сразу после открытия WinDivert он пишет в
свой вывод строку ``windivert initialized`` (замерено на Windows: ~0,1 с после
запуска). Сразу за ней грузится Lua; если в стратегии ошибка, процесс
завершится в первые доли секунды. Поэтому после строки готовности ещё
немного ждём и проверяем, что процесс жив. Если строка не пришла (другая
сборка winws2), готовность определяется по тому, что процесс не упал.

Вывод читается отдельным потоком всё время работы: если трубу не читать, она
переполнится, и winws2 зависнет на записи в неё.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

READY_MARKER = "windivert initialized"
# Сколько ждать строки готовности.
READY_TIMEOUT = 3.0
# Сколько ещё подождать после неё, пока загрузится Lua стратегии.
LUA_SETTLE_SECONDS = 0.2
# Если строка готовности не пришла: сколько процесс должен прожить.
FALLBACK_ALIVE_SECONDS = 0.8
STOP_WAIT_SECONDS = 3.0


@dataclass(frozen=True, slots=True)
class SessionStart:
    ok: bool
    ready_ms: float = 0.0
    exit_code: int | None = None
    output_tail: str = ""


class WinwsSession:
    """Один процесс winws2 с конфигом стратегии."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: str,
        popen: Callable[..., subprocess.Popen] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._command = list(command)
        self._cwd = cwd
        self._popen = popen or _hidden_popen
        self._clock = clock
        self._sleep = sleep
        self._process: subprocess.Popen | None = None
        self._lines: deque[str] = deque(maxlen=40)
        self._ready = threading.Event()
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()

    # --- Запуск ------------------------------------------------------------------

    def start(self) -> SessionStart:
        started = self._clock()
        process = self._popen(
            self._command,
            cwd=self._cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        with self._lock:
            self._process = process
        self._reader = threading.Thread(target=self._read_output, args=(process,), daemon=True)
        self._reader.start()

        deadline = started + READY_TIMEOUT
        while self._clock() < deadline:
            if self._ready.is_set():
                self._sleep(LUA_SETTLE_SECONDS)
                break
            if process.poll() is not None:
                break
            if self._clock() - started >= FALLBACK_ALIVE_SECONDS and not self._reader.is_alive():
                # Вывод закрыт, а процесс жив: строки готовности не будет.
                break
            self._sleep(0.02)

        exit_code = process.poll()
        if exit_code is not None:
            self._join_reader()
            return SessionStart(False, exit_code=exit_code, output_tail=self.output_tail())
        if not self._ready.is_set():
            # Строки готовности нет, но процесс жив: подождать и перепроверить.
            remaining = FALLBACK_ALIVE_SECONDS - (self._clock() - started)
            if remaining > 0:
                self._sleep(remaining)
            exit_code = process.poll()
            if exit_code is not None:
                self._join_reader()
                return SessionStart(False, exit_code=exit_code, output_tail=self.output_tail())
        return SessionStart(True, ready_ms=(self._clock() - started) * 1000)

    def _read_output(self, process: subprocess.Popen) -> None:
        stream = process.stdout
        if stream is None:
            return
        try:
            for raw in stream:
                text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
                text = text.rstrip()
                if not text:
                    continue
                self._lines.append(text)
                if READY_MARKER in text:
                    self._ready.set()
        except (OSError, ValueError):
            pass

    def _join_reader(self) -> None:
        if self._reader is not None:
            self._reader.join(timeout=1.0)

    # --- Состояние ---------------------------------------------------------------

    def alive(self) -> bool:
        with self._lock:
            process = self._process
        return process is not None and process.poll() is None

    def exit_code(self) -> int | None:
        with self._lock:
            process = self._process
        return None if process is None else process.poll()

    def output_tail(self, lines: int = 6) -> str:
        return "\n".join(list(self._lines)[-lines:])

    # --- Остановка -----------------------------------------------------------------

    def stop(self) -> bool:
        """Остановить свой процесс. True — процесс точно завершён.

        Общая чистка WinDivert тут не нужна: закрытие процесса само закрывает
        его перехват. Чистка делается только в начале и в конце подбора и
        после аварии.
        """
        with self._lock:
            process = self._process
            self._process = None
        if process is None:
            return True
        stopped = True
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=STOP_WAIT_SECONDS)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        stopped = False
        except OSError:
            stopped = process.poll() is not None
        self._join_reader()
        try:
            if process.stdout is not None:
                process.stdout.close()
        except OSError:
            pass
        return stopped


def _hidden_popen(command, **kwargs) -> subprocess.Popen:
    """Процесс без окна, как обычный запуск winws2 программой."""
    from winws_runtime.public import CREATE_NO_WINDOW, STARTF_USESHOWWINDOW, SW_HIDE

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags = STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = SW_HIDE
    return subprocess.Popen(command, startupinfo=startupinfo, creationflags=CREATE_NO_WINDOW, **kwargs)
