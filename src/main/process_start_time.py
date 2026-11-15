"""Замер разрыва между созданием процесса (двойной клик) и входом в Python.

Модуль должен импортироваться первой строкой main.py: момент импорта
фиксируется как «Python вошёл». Время создания процесса берётся у ОС через
платформу (Windows: kernel32.GetProcessTimes, Linux: /proc), поэтому в разрыв попадает всё, что происходит до
Python: PyInstaller bootloader, загрузка DLL, сканирование антивирусом.

Здесь нельзя импортировать ничего тяжелее time (платформа подключается лениво, после замера) — иначе замер
сдвинется и потеряет смысл.
"""

from __future__ import annotations

import time

_PYTHON_ENTERED_WALL = time.time()


def python_entered_wall() -> float:
    return _PYTHON_ENTERED_WALL


def process_creation_unix_seconds() -> float | None:
    """Когда ОС создала процесс (Unix-секунды); как — решает платформа. Импорт отложен: измерение не должно сдвигаться."""
    try:
        from platforms import current

        return current().launch.process_creation_unix_seconds()
    except Exception:
        return None


def exe_to_python_ms() -> int | None:
    """Миллисекунды от создания процесса до входа в Python, либо None."""
    created = process_creation_unix_seconds()
    if created is None:
        return None
    gap_ms = (_PYTHON_ENTERED_WALL - created) * 1000.0
    if gap_ms < 0:
        return None
    return int(round(gap_ms))
