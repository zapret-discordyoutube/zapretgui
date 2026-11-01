# utils/antivirus_probe.py
"""Кэшированный детект Kaspersky для runtime-слоя.

WinDivert cleanup вызывается сериями (retry-пути, blockcheck-сканер), поэтому
результат кэшируется по time.monotonic() на _CACHE_TTL_SECONDS. При любой
ошибке проб возвращается False — вызывающий код обязан вести себя как до
появления детекта (полный aggressive cleanup).
"""
from __future__ import annotations

import time

from utils.windows_process_probe import iter_process_names_winapi, iter_uninstall_display_names

_KASPERSKY_PROCESS_NAMES = frozenset(
    {
        "avp.exe",
        "kavfs.exe",
        "klnagent.exe",
        "ksde.exe",
        "kavfswp.exe",
        "kavfswh.exe",
        "kavfsslp.exe",
    }
)

_KASPERSKY_NAME_MARKERS = ("kaspersky", "каспер")

_CACHE_TTL_SECONDS = 120.0
_cached_result: bool | None = None
_cached_at: float = 0.0


def _probe_kaspersky_present() -> bool:
    for process_name in iter_process_names_winapi():
        if str(process_name or "").strip().casefold() in _KASPERSKY_PROCESS_NAMES:
            return True

    for product_name in iter_uninstall_display_names():
        normalized = str(product_name or "").casefold()
        if any(marker in normalized for marker in _KASPERSKY_NAME_MARKERS):
            return True
    return False


def is_kaspersky_present_strict() -> bool:
    """Детект Kaspersky для решения «выгружать ли драйвер»: сбой проб — исключение.

    В отличие от ``is_kaspersky_present`` не кэшируется и не превращает отказ
    снимка процессов в «Kaspersky нет»: выгрузка драйвера рядом с фильтрами
    Kaspersky вызывала синий экран в tcpip.sys, поэтому при сомнении драйвер
    остаётся на месте (ошибку вызывающий код читает как «нельзя выгружать»).
    """
    from utils.windows_process_probe import iter_process_records_winapi_strict

    for _pid, process_name in iter_process_records_winapi_strict():
        if str(process_name or "").strip().casefold() in _KASPERSKY_PROCESS_NAMES:
            return True
    for product_name in iter_uninstall_display_names():
        normalized = str(product_name or "").casefold()
        if any(marker in normalized for marker in _KASPERSKY_NAME_MARKERS):
            return True
    return False


def is_kaspersky_present(*, force_refresh: bool = False) -> bool:
    global _cached_result, _cached_at

    now = time.monotonic()
    if not force_refresh and _cached_result is not None and now - _cached_at < _CACHE_TTL_SECONDS:
        return _cached_result

    try:
        result = _probe_kaspersky_present()
    except Exception:
        result = False

    _cached_result = result
    _cached_at = now
    return result
