"""Действия runtime-слоя над процессами winws и драйвером WinDivert.

Тонкая прослойка между раннерами и нижним слоем ``winws_runtime.engine``:
здесь решается, ЧЬИ процессы и ЧЕЙ драйвер считать своими (пути установки,
антивирус), а сами действия с подтверждением выполняет engine.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass

from config.runtime_layout import APPLICATION_PATHS
from log.log import log
from settings.mode import ALL_WINWS_EXE_NAMES

from winws_runtime.engine.driver import (
    DEFAULT_WAIT_SECONDS as DEFAULT_DRIVER_WAIT_SECONDS,
    DRIVER_SERVICE_NAMES,
    RELEASE_ERROR,
    DriverPreflight,
    DriverReleaseResult,
    ensure_driver_startable,
    release_driver_if_unused,
)
from winws_runtime.engine.process_control import (
    list_engine_processes,
    reload_engine_lists,
    stop_engine_processes,
)

# Канонические Win32-коды ошибок WinDivert живут в едином центре диагностики.
from winws_runtime.health.windivert_diagnostics import (  # noqa: E402
    _ERROR_SERVICE_DOES_NOT_EXIST,
)

# Имена службы драйвера — единственный список, остальные модули берут его отсюда.
_KNOWN_WINDIVERT_SERVICES = DRIVER_SERVICE_NAMES
_ALL_WINWS_EXE_NAMES = tuple(ALL_WINWS_EXE_NAMES)
_WINDIVERT_LAYER_NETWORK = 0
_WINDIVERT_LAYER_REFLECT = 4
_WINDIVERT_FLAG_SNIFF = 1
_WINDIVERT_FLAG_RECV_ONLY = 4
_WINDIVERT_FLAG_NO_INSTALL = 0x0010


@dataclass(frozen=True, slots=True)
class WinDivertRuntimeProbeResult:
    installed: bool
    ready: bool
    error_code: int | None = None
    stage: str = ""


def own_engine_exe_paths() -> list[str]:
    """Полные пути к winws.exe и winws2.exe нашей установки."""
    from winws_runtime.runtime.process_probe import get_expected_winws_paths

    return list(get_expected_winws_paths().values())


def own_windivert_roots() -> list[str]:
    """Папки нашей установки, в которых может лежать файл драйвера."""
    roots: list[str] = []
    for candidate in (APPLICATION_PATHS.root, APPLICATION_PATHS.exe_dir):
        text = str(candidate or "").strip()
        if text and text not in roots:
            roots.append(text)
    return roots


def stop_own_winws_processes_runtime(*, timeout: float = 5.0) -> bool:
    """Завершает процессы winws из нашей папки. True — выход всех подтверждён.

    Чужие winws (другая копия запрета) не трогаются: убивать по одному только
    имени процесса нельзя.
    """
    try:
        return bool(stop_engine_processes(own_engine_exe_paths(), timeout=timeout).ok)
    except Exception as e:
        log(f"Ошибка остановки процессов winws: {e}", "WARNING")
        return False


def reload_own_engine_lists_runtime() -> int:
    """Просит свои запущенные winws перечитать файлы списков без перезапуска.

    Возвращает, сколько процессов приняли сигнал (0 — движок не запущен или
    это старая сборка без такой возможности). Это несколько системных вызовов,
    ожидания нет; ошибок наружу не бросает.
    """
    try:
        return int(reload_engine_lists(own_engine_exe_paths()))
    except Exception as e:
        log(f"Ошибка обновления списков в winws: {e}", "DEBUG")
        return 0


def has_own_winws_process() -> bool:
    """Жив ли хотя бы один winws из нашей папки."""
    try:
        from winws_runtime.runtime.process_probe import get_canonical_winws_process_pids

        return bool(get_canonical_winws_process_pids())
    except Exception as e:
        log(f"Ошибка проверки процессов winws: {e}", "DEBUG")
        return False


def _is_kaspersky_present_strict() -> bool:
    """Детект Kaspersky для выгрузки драйвера: ошибка детекта пробрасывается.

    Слой драйвера трактует сбой детекта в безопасную сторону — драйвер не
    выгружается: принудительная выгрузка рядом с фильтрами Kaspersky
    провоцировала синий экран в tcpip.sys.
    """
    from utils.antivirus_probe import is_kaspersky_present

    return bool(is_kaspersky_present())


def kill_process_by_pid_runtime(pid: int, *, wait_timeout_ms: int = 3000) -> bool:
    try:
        from utils.process_killer import kill_process_by_pid_winapi

        return bool(kill_process_by_pid_winapi(int(pid), wait_timeout_ms=wait_timeout_ms))
    except Exception as e:
        log(f"Ошибка kill_process_by_pid для PID={pid}: {e}", "DEBUG")
        return False


def ensure_windivert_driver_startable_runtime(
    *,
    wait_seconds: float = DEFAULT_DRIVER_WAIT_SECONDS,
) -> DriverPreflight:
    """Проверяет перед запуском, что служба драйвера не застряла.

    Только чтение. Если драйвер как раз выгружается, ждёт до ``wait_seconds``.
    """
    try:
        return ensure_driver_startable(own_roots=own_windivert_roots(), wait_seconds=wait_seconds)
    except Exception as e:
        # Проверка не должна мешать запуску: причину отказа назовёт сам winws.
        log(f"Ошибка проверки службы драйвера WinDivert: {e}", "DEBUG")
        return DriverPreflight(ok=True)


def release_windivert_driver_runtime(
    *,
    wait_seconds: float = DEFAULT_DRIVER_WAIT_SECONDS,
) -> DriverReleaseResult:
    """Выгружает драйвер WinDivert, если им никто не пользуется.

    Вызывается после полной остановки обхода (кнопка «Стоп», выход из
    программы, обновление), но не при перезапуске и не при смене пресета:
    между запусками драйвер остаётся загруженным, и следующий winws открывает
    его напрямую.
    """
    try:
        return release_driver_if_unused(
            own_roots=own_windivert_roots(),
            engine_in_use=_any_winws_process_alive_strict,
            antivirus_blocks_unload=_is_kaspersky_present_strict,
            wait_seconds=wait_seconds,
        )
    except Exception as e:
        log(f"Ошибка выгрузки драйвера WinDivert: {e}", "WARNING")
        return DriverReleaseResult(RELEASE_ERROR, message=str(e))


def _any_winws_process_alive_strict() -> bool:
    """Жив ли хоть один winws — свой или чужой. Сбой снимка пробрасывается.

    Драйвером пользуется любой winws, не только наш, поэтому здесь проверка
    по имени. Сбой снимка слой драйвера трактует как «занят».
    """
    return bool(list_engine_processes(_ALL_WINWS_EXE_NAMES))


def recover_windivert_runtime() -> bool:
    """Приводит движок и драйвер в исходное состояние после сбоя запуска.

    Порядок следует из того, как на самом деле застревает служба драйвера:

    1. Завершить свои winws с подтверждением выхода. Пока жив хоть один,
       драйвер занят; если его в этот момент останавливали, он висит в
       «останавливается» именно из-за них.
    2. Выгрузить драйвер, если им больше никто не пользуется, — следующий
       запуск поставит его заново из нашей папки.
    3. Убедиться, что запись службы не застряла.

    Реестр не правится, пометка удаления не снимается, тип запуска не
    меняется: такие «лечения» и создавали застрявшую запись.
    """
    log("Восстановление после сбоя запуска: останавливаем свои winws и освобождаем драйвер", "INFO")
    stopped = stop_own_winws_processes_runtime()
    released = release_windivert_driver_runtime()
    preflight = ensure_windivert_driver_startable_runtime()
    if released.stuck and released.message:
        log(released.message, "WARNING")
    if not preflight.ok and preflight.message:
        log(preflight.message, "WARNING")
    return bool(stopped and preflight.ok and not released.stuck)


def _iter_windivert_dll_candidates_runtime() -> list[str]:
    candidates = [
        str(APPLICATION_PATHS.root / "WinDivert.dll"),
        str(APPLICATION_PATHS.exe_dir / "WinDivert.dll"),
        str(APPLICATION_PATHS.bin_dir / "WinDivert.dll"),
    ]

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = os.path.normcase(os.path.normpath(str(candidate or "").strip()))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(candidate)
    return unique


def _load_windivert_dll_runtime():
    if not hasattr(ctypes, "WinDLL"):
        return None

    dll_path = ""
    for candidate in _iter_windivert_dll_candidates_runtime():
        if os.path.exists(candidate):
            dll_path = candidate
            break

    if not dll_path:
        return None

    try:
        return ctypes.WinDLL(dll_path, use_last_error=True)
    except Exception as e:
        log(f"Не удалось загрузить WinDivert.dll для readiness probe: {e}", "DEBUG")
        return None


def _probe_windivert_open_runtime(
    dll,
    *,
    filter_text: bytes,
    layer: int,
    flags: int,
) -> tuple[bool, int | None]:
    try:
        open_fn = dll.WinDivertOpen
        close_fn = dll.WinDivertClose
    except Exception:
        return True, None

    open_fn.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_short, ctypes.c_uint64]
    open_fn.restype = ctypes.c_void_p
    close_fn.argtypes = [ctypes.c_void_p]
    close_fn.restype = ctypes.c_bool

    ctypes.set_last_error(0)
    handle = open_fn(filter_text, layer, 0, flags)
    invalid_handle = ctypes.c_void_p(-1).value
    handle_value = ctypes.c_void_p(handle).value

    if handle_value in (None, invalid_handle):
        return False, int(ctypes.get_last_error() or 0)

    try:
        close_fn(handle)
    except Exception:
        pass
    return True, None


def probe_windivert_state_runtime() -> WinDivertRuntimeProbeResult:
    """Двухступенчатый probe состояния WinDivert.

    1. `NO_INSTALL + REFLECT`:
       проверяем, установлен ли драйвер вообще, без скрытой авто-установки.
    2. `NO_INSTALL + NETWORK + SNIFF`:
       проверяем уже готовый драйвер, но не запускаем установку из GUI-probe.
       Реальную авто-установку должен делать сам winws2: в его коде есть
       отдельная защита от гонок старта WinDivert.
    """
    dll = _load_windivert_dll_runtime()
    if dll is None:
        return WinDivertRuntimeProbeResult(
            installed=True,
            ready=True,
            error_code=None,
            stage="dll_unavailable",
        )

    installed_ok, installed_error = _probe_windivert_open_runtime(
        dll,
        filter_text=b"true",
        layer=_WINDIVERT_LAYER_REFLECT,
        flags=_WINDIVERT_FLAG_SNIFF | _WINDIVERT_FLAG_RECV_ONLY | _WINDIVERT_FLAG_NO_INSTALL,
    )
    installed = bool(installed_ok or int(installed_error or 0) != _ERROR_SERVICE_DOES_NOT_EXIST)

    ready_ok, ready_error = _probe_windivert_open_runtime(
        dll,
        filter_text=b"true",
        layer=_WINDIVERT_LAYER_NETWORK,
        flags=_WINDIVERT_FLAG_SNIFF | _WINDIVERT_FLAG_NO_INSTALL,
    )
    return WinDivertRuntimeProbeResult(
        installed=installed,
        ready=bool(ready_ok),
        error_code=None if ready_ok else ready_error,
        stage="network_open",
    )


