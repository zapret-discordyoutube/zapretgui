"""Заполнение папки данных пользователя при установке с XDG-раскладкой (Linux).

Поставка в ``/opt/zapretgui`` только для чтения, а программа пишет пресеты (и встроенные копии, которые сама
обновляет) в ``~/.local/share/zapretgui/presets``. Здесь первый запуск и запуск после обновления копируют
встроенные пресеты из поставки; пресеты пользователя (другие подпапки и файлы) не трогаются.
"""

from __future__ import annotations

import shutil
from pathlib import Path

BUILTIN_SUFFIX = "_builtin"
MARKER_NAME = ".seeded-from"


def _stamp(shipped: Path) -> str:
    """Отпечаток поставки: имена и размеры файлов встроенных пресетов (меняется с их обновлением)."""
    parts: list[str] = []
    if shipped.is_dir():
        for folder in sorted(shipped.iterdir()):
            if folder.is_dir() and folder.name.endswith(BUILTIN_SUFFIX):
                for item in sorted(folder.rglob("*")):
                    if item.is_file():
                        parts.append(f"{folder.name}/{item.relative_to(folder).as_posix()}:{item.stat().st_size}")
    return "\n".join(parts)


def seed_user_data(paths) -> bool:
    """Создаёт папки и копирует встроенные пресеты. True — копирование было."""
    if paths.data_root is None:
        return False
    for directory in (paths.data_root, paths.presets_dir, paths.lists_user_dir):
        directory.mkdir(parents=True, exist_ok=True)
    for directory in (paths.config_root, paths.state_root, paths.cache_root):
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)

    shipped = paths.shipped_presets_dir
    stamp = _stamp(shipped)
    marker = paths.presets_dir / MARKER_NAME
    try:
        if marker.read_text(encoding="utf-8") == stamp:
            return False
    except OSError:
        pass
    for folder in sorted(shipped.iterdir()) if shipped.is_dir() else ():
        if folder.is_dir() and folder.name.endswith(BUILTIN_SUFFIX):
            target = paths.presets_dir / folder.name
            if target.exists():
                shutil.rmtree(target)  # встроенное принадлежит поставке: заменяется целиком
            shutil.copytree(folder, target)
    marker.write_text(stamp, encoding="utf-8")
    return True


__all__ = ["seed_user_data"]
