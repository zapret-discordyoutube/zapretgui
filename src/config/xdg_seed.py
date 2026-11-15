"""Заполнение папки данных пользователя при установке с XDG-раскладкой (Linux).

Поставка в ``/opt/zapretgui`` только для чтения, а программа пишет пресеты (и встроенные копии, которые сама
обновляет) в ``~/.local/share/zapretgui/presets`` и пересобирает итоговые списки из ``lists/base`` рядом с ними.
Здесь первый запуск и запуск после обновления копируют из поставки встроенные пресеты и базовые списки;
пресеты и списки пользователя (всё остальное) не трогаются.
"""

from __future__ import annotations

import shutil
from pathlib import Path

BUILTIN_SUFFIX = "_builtin"
MARKER_NAME = ".seeded-from"


def _tree_stamp(root: Path, *, only_builtin_folders: bool = False) -> str:
    """Отпечаток поставленных файлов: имена и размеры (меняется с их обновлением)."""
    parts: list[str] = []
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if only_builtin_folders:
                if not (folder.is_dir() and folder.name.endswith(BUILTIN_SUFFIX)):
                    continue
                base = folder.parent
                items = [item for item in sorted(folder.rglob("*")) if item.is_file()]
            else:
                if not folder.is_file():
                    continue
                base = root
                items = [folder]
            parts.extend(f"{item.relative_to(base).as_posix()}:{item.stat().st_size}" for item in items)
    return "\n".join(parts)


def _sync(shipped: Path, target: Path, *, only_builtin_folders: bool) -> bool:
    """Копирует поставку в ``target``, если её отпечаток изменился. True — копирование было."""
    stamp = _tree_stamp(shipped, only_builtin_folders=only_builtin_folders)
    marker = target / MARKER_NAME
    try:
        if marker.read_text(encoding="utf-8") == stamp:
            return False
    except OSError:
        pass
    target.mkdir(parents=True, exist_ok=True)
    for item in sorted(shipped.iterdir()) if shipped.is_dir() else ():
        if only_builtin_folders:
            if item.is_dir() and item.name.endswith(BUILTIN_SUFFIX):
                destination = target / item.name
                if destination.exists():
                    shutil.rmtree(destination)  # встроенное принадлежит поставке: заменяется целиком
                shutil.copytree(item, destination)
        elif item.is_file():
            shutil.copy2(item, target / item.name)
    marker.write_text(stamp, encoding="utf-8")
    return True


def seed_user_data(paths) -> bool:
    """Создаёт папки и копирует встроенные пресеты и базовые списки. True — копирование было."""
    if paths.data_root is None:
        return False
    for directory in (paths.data_root, paths.presets_dir, paths.lists_dir, paths.lists_user_dir):
        directory.mkdir(parents=True, exist_ok=True)
    for directory in (paths.config_root, paths.state_root, paths.cache_root):
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)

    presets = _sync(paths.shipped_presets_dir, paths.presets_dir, only_builtin_folders=True)
    lists = _sync(paths.shipped_lists_base_dir, paths.lists_base_dir, only_builtin_folders=False)
    first_copies = _copy_missing(paths.shipped_lists_dir, paths.lists_dir)
    return presets or lists or first_copies


def _copy_missing(shipped: Path, target: Path) -> bool:
    """Поставляемые итоговые списки (``lists/*.txt``, например netrogat.txt): их правит пользователь, поэтому
    копируются только отсутствующие — чужие правки обновление поставки не затирает."""
    copied = False
    for item in sorted(shipped.iterdir()) if shipped.is_dir() else ():
        destination = target / item.name
        if item.is_file() and not destination.exists():
            shutil.copy2(item, destination)
            copied = True
    return copied


__all__ = ["seed_user_data"]
