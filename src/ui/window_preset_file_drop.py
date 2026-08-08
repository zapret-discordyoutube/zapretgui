"""Перетаскивание файлов preset-ов в главное окно."""

from __future__ import annotations

import os
from collections.abc import Callable

from PyQt6.QtCore import QEvent, QObject

from log.log import log


def dropped_preset_file_paths(mime_data) -> list[str]:
    """Возвращает уникальные локальные TXT-файлы из данных перетаскивания."""
    if mime_data is None:
        return []
    try:
        if not mime_data.hasUrls():
            return []
        urls = mime_data.urls()
    except Exception:
        return []

    paths: list[str] = []
    seen: set[str] = set()
    for url in urls:
        try:
            if not url.isLocalFile():
                continue
            path = str(url.toLocalFile() or "").strip()
        except Exception:
            continue
        if not path or not path.lower().endswith(".txt") or not os.path.isfile(path):
            continue
        path_key = os.path.normcase(os.path.normpath(path))
        if path_key in seen:
            continue
        seen.add(path_key)
        paths.append(path)
    return paths


class WindowPresetFileDropFilter(QObject):
    """Направляет TXT-файлы текущей странице, если она умеет их импортировать."""

    def __init__(
        self,
        window,
        *,
        target_resolver: Callable[[], object | None],
    ) -> None:
        super().__init__(window if isinstance(window, QObject) else None)
        self._window = window
        self._target_resolver = target_resolver

    def _belongs_to_window(self, watched) -> bool:
        if watched is self._window:
            return True
        try:
            return watched.window() is self._window
        except Exception:
            return False

    def _import_action(self):
        try:
            target = self._target_resolver()
        except Exception:
            return None
        action = getattr(target, "import_dropped_preset_files", None)
        return action if callable(action) else None

    def eventFilter(self, watched, event):  # noqa: N802 (Qt override)
        if not self._belongs_to_window(watched):
            return False

        event_type = event.type()
        if event_type not in {
            QEvent.Type.DragEnter,
            QEvent.Type.DragMove,
            QEvent.Type.Drop,
        }:
            return False

        import_action = self._import_action()
        if import_action is None:
            return False

        paths = dropped_preset_file_paths(event.mimeData())
        if not paths:
            return False

        if event_type == QEvent.Type.Drop:
            try:
                if not bool(import_action(paths)):
                    return False
            except Exception as exc:
                log(f"Не удалось передать TXT-файл на импорт: {exc}", "ERROR")
                return False

        event.acceptProposedAction()
        return True


__all__ = ["WindowPresetFileDropFilter", "dropped_preset_file_paths"]
