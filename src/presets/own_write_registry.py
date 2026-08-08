"""Регистрация собственных записей preset-файлов.

QFileSystemWatcher не отличает запись самого приложения от внешней правки.
Каждая запись через PresetFileStore помечается здесь, и runtime-координатор
не трактует её как внешнее изменение. Без этого автосохранение редактора
(publish_content_changed=False) перезапускало бы DPI до commit-а.

Записи идут из worker-потоков, чтение — из GUI-потока, поэтому реестр под
блокировкой.
"""

from __future__ import annotations

import threading
import time

OWN_PRESET_WRITE_WINDOW_SEC = 2.5

_lock = threading.Lock()
_recent_writes: dict[str, float] = {}


def _normalize_file_name(path_or_file_name: str) -> str:
    text = str(path_or_file_name or "").strip().replace("\\", "/")
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text.lower()


def mark_own_preset_write(path_or_file_name: str) -> None:
    file_name = _normalize_file_name(path_or_file_name)
    if not file_name:
        return
    now = time.monotonic()
    with _lock:
        stale_before = now - OWN_PRESET_WRITE_WINDOW_SEC
        for key in [key for key, at in _recent_writes.items() if at < stale_before]:
            _recent_writes.pop(key, None)
        _recent_writes[file_name] = now


def was_recent_own_preset_write(
    path_or_file_name: str,
    *,
    window_sec: float = OWN_PRESET_WRITE_WINDOW_SEC,
) -> bool:
    file_name = _normalize_file_name(path_or_file_name)
    if not file_name:
        return False
    with _lock:
        written_at = _recent_writes.get(file_name)
    if written_at is None:
        return False
    return (time.monotonic() - written_at) <= float(window_sec)


__all__ = [
    "OWN_PRESET_WRITE_WINDOW_SEC",
    "mark_own_preset_write",
    "was_recent_own_preset_write",
]
