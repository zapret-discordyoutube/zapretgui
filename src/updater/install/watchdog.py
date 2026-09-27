from __future__ import annotations

"""Запуск наблюдателя обновления, переживающего закрытие приложения.

Приложение закрывается сразу после передачи управления установщику, поэтому
проверить исход установки изнутри невозможно: процесса уже нет. Наблюдатель —
отдельный PowerShell-процесс из каталога состояния обновления. Он живёт вне
каталога установки (установщик завершает все процессы внутри ``{app}``),
дожидается кода возврата установщика, сверяет версию на диске и записывает
итог в ``handoff.json``. Текст скрипта — в ``watchdog_script``.
"""

import os
import subprocess
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from log.log import log

from . import paths
from .watchdog_script import render_watchdog_script


WATCHDOG_LOG_LEVEL = "🔁 UPDATE"
# Холодный старт PowerShell под проверкой антивируса бывает долгим. Если
# наблюдатель так и не отозвался, приложение отменяет обновление, удаляя
# запись состояния: опоздавший наблюдатель увидит это и ничего не запустит.
WATCHDOG_START_TIMEOUT_SECONDS = 30.0
WATCHDOG_START_POLL_SECONDS = 0.2
PREVIOUS_WATCHDOG_LOG_NAME = "watchdog.prev.log"

_CREATE_NEW_PROCESS_GROUP = 0x00000200
_SHELL_EXECUTE_MIN_SUCCESS = 32


def install_watchdog_script(path: str | Path | None = None) -> Path:
    """Раскладывает скрипт наблюдателя рядом с состоянием обновления.

    Записывается с BOM: Windows PowerShell 5.1 читает файл без BOM в
    системной кодировке и портит русский текст сообщений.
    """
    script_path = Path(path) if path is not None else paths.watchdog_script_path()
    script_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = script_path.with_name(f"{script_path.name}.{os.getpid()}.tmp")
    temporary_path.write_text(render_watchdog_script(), encoding="utf-8-sig")
    os.replace(temporary_path, script_path)
    return script_path


def build_watchdog_command(
    *,
    script_path: str | Path,
    state_path: str | Path,
    recovery: bool = False,
) -> tuple[str, ...]:
    command = (
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "-StatePath",
        str(state_path),
    )
    if recovery:
        return command + ("-Recovery",)
    return command


def _default_process_lister() -> Iterable[tuple[int, Sequence[str]]]:
    import psutil

    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        info = process.info
        name = str(info.get("name") or "").lower()
        if not name.startswith("powershell"):
            continue
        yield int(info.get("pid") or 0), tuple(info.get("cmdline") or ())


def _default_process_killer(pid: int) -> None:
    import psutil

    psutil.Process(pid).kill()


def stop_watchdogs(
    script_paths: Sequence[str | Path],
    *,
    list_processes: Callable[[], Iterable[tuple[int, Sequence[str]]]] = _default_process_lister,
    kill_process: Callable[[int], None] = _default_process_killer,
) -> int:
    """Останавливает уже работающих наблюдателей с указанными скриптами.

    Наблюдатель прежних версий ждал вместе с установщиком и запущенную им
    новую программу, поэтому «просыпался» только при её закрытии. Проснувшись
    посреди следующего обновления, он мог поставить поверх свой, уже старый,
    установщик. Перед новой передачей управления и при первом запуске новой
    версии такие процессы нужно завершить.
    """
    wanted = tuple(
        os.path.normcase(str(path)) for path in script_paths if str(path).strip()
    )
    if not wanted:
        return 0
    stopped = 0
    try:
        processes = list(list_processes())
    except Exception as exc:
        log(f"Не удалось получить список процессов PowerShell: {exc}", "WARNING")
        return 0
    for pid, cmdline in processes:
        if pid <= 0 or pid == os.getpid():
            continue
        line = os.path.normcase(" ".join(str(part) for part in cmdline))
        if not any(path in line for path in wanted):
            continue
        try:
            kill_process(pid)
            stopped += 1
            log(f"Остановлен прежний наблюдатель обновления (PID {pid})", WATCHDOG_LOG_LEVEL)
        except Exception as exc:
            log(f"Не удалось остановить наблюдатель обновления (PID {pid}): {exc}", "WARNING")
    return stopped


def retire_legacy_watchdog() -> None:
    """Однократная уборка после наблюдателя прежних версий.

    Скрипт прежних версий лежит под другим именем. Пока он есть на диске, его
    зависший процесс может быть жив: останавливаем процесс и удаляем скрипт.
    """
    legacy_script = paths.legacy_watchdog_script_path()
    if not legacy_script.exists():
        return
    stop_watchdogs((legacy_script,))
    try:
        legacy_script.unlink(missing_ok=True)
    except OSError as exc:
        log(f"Не удалось удалить прежний скрипт наблюдателя: {exc}", "WARNING")


def rotate_watchdog_log(log_path: str | Path | None = None) -> Path:
    """Отодвигает прошлый журнал: появление нового = наблюдатель ожил."""
    resolved = Path(log_path) if log_path is not None else paths.watchdog_log_path()
    try:
        if resolved.exists():
            os.replace(resolved, resolved.with_name(PREVIOUS_WATCHDOG_LOG_NAME))
    except OSError as exc:
        log(f"Не удалось отодвинуть журнал наблюдателя: {exc}", "WARNING")
    return resolved


def spawn_background(command: Sequence[str]) -> bool:
    """Запуск от уже полученных прав администратора, без запроса UAC.

    ``DETACHED_PROCESS`` здесь применять нельзя. Windows PowerShell 5.1 при
    таком запуске может завершиться с кодом 0 ещё до исполнения ``-File``:
    процесс формально создан, но наблюдатель и установщик не стартуют.
    ``CREATE_NO_WINDOW`` уже скрывает консоль, а обычный дочерний процесс
    Windows продолжает жить после закрытия родителя.
    """
    try:
        subprocess.Popen(
            list(command),
            creationflags=(
                _CREATE_NEW_PROCESS_GROUP
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            ),
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception as exc:
        log(f"❌ Не удалось запустить наблюдателя обновления: {exc}", "🔁❌ ERROR")
        return False


def spawn_elevated(command: Sequence[str]) -> bool:
    """Запасной путь: приложение почему-то работает без прав администратора."""
    import ctypes

    arguments = subprocess.list2cmdline(list(command[1:]))
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            command[0],
            arguments,
            None,
            0,
        )
    except Exception as exc:
        log(f"❌ Не удалось запросить права для наблюдателя: {exc}", "🔁❌ ERROR")
        return False

    if int(result) <= _SHELL_EXECUTE_MIN_SUCCESS:
        log(f"❌ Наблюдатель не запущен, код ShellExecute: {result}", "🔁❌ ERROR")
        return False
    return True


def is_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def wait_for_watchdog_start(
    log_path: Path,
    *,
    timeout_seconds: float = WATCHDOG_START_TIMEOUT_SECONDS,
    poll_seconds: float = WATCHDOG_START_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> bool:
    deadline = monotonic() + float(timeout_seconds)
    while monotonic() < deadline:
        if log_path.exists():
            return True
        sleep(poll_seconds)
    return log_path.exists()


__all__ = [
    "PREVIOUS_WATCHDOG_LOG_NAME",
    "WATCHDOG_START_TIMEOUT_SECONDS",
    "build_watchdog_command",
    "install_watchdog_script",
    "is_admin",
    "retire_legacy_watchdog",
    "rotate_watchdog_log",
    "spawn_background",
    "spawn_elevated",
    "stop_watchdogs",
    "wait_for_watchdog_start",
]
