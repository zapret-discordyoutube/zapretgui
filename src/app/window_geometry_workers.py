from __future__ import annotations

from contextlib import nullcontext

from PyQt6.QtCore import QThread, pyqtSignal

from log.log import log


class WindowGeometrySaveWorker(QThread):
    saved = pyqtSignal(object, object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        request_id: int,
        *,
        geometry: tuple[int, int, int, int] | None,
        maximized: bool,
        get_window_geometry,
        set_window_geometry,
        write_guard=None,
        parent=None,
    ):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._geometry = None if geometry is None else tuple(int(value) for value in geometry)
        self._maximized = bool(maximized)
        self._get_window_geometry = get_window_geometry
        self._set_window_geometry = set_window_geometry
        # write_guard() — контекст, который отвечает, можно ли ещё писать: при
        # закрытии окно уже записало свежую геометрию, старая её не затирает.
        self._write_guard = write_guard

    def run(self) -> None:
        try:
            if self._geometry is None:
                current = self._get_window_geometry()
                x = current.get("x")
                y = current.get("y")
                width = current.get("width")
                height = current.get("height")
            else:
                x, y, width, height = self._geometry

            guard = self._write_guard() if self._write_guard is not None else nullcontext(True)
            with guard as allowed:
                if not allowed:
                    return
                self._set_window_geometry(
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                    maximized=self._maximized,
                )
        except Exception as exc:
            log(f"WindowGeometrySaveWorker: не удалось сохранить геометрию: {exc}", "WARNING")
            self.failed.emit(str(exc))
            return
        self.saved.emit(self._geometry, self._maximized)


__all__ = ["WindowGeometrySaveWorker"]
