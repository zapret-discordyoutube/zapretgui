from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True, slots=True, init=False)
class WindowGeometryFeature:
    _get_window_geometry: Callable | None = field(default=None, repr=False, compare=False)
    _set_window_geometry: Callable | None = field(default=None, repr=False, compare=False)

    def __init__(self, get_window_geometry: Callable | None = None, set_window_geometry: Callable | None = None) -> None:
        object.__setattr__(self, "_get_window_geometry", get_window_geometry)
        object.__setattr__(self, "_set_window_geometry", set_window_geometry)

    @staticmethod
    def _commands():
        from app import window_geometry_commands

        return window_geometry_commands

    def get_window_geometry(self) -> dict:
        get_window_geometry = self._get_window_geometry
        if get_window_geometry is None:
            get_window_geometry = self._commands().get_window_geometry
            object.__setattr__(self, "_get_window_geometry", get_window_geometry)
        return dict(get_window_geometry() or {})

    def set_window_geometry(self, *, x, y, width, height, maximized: bool) -> None:
        set_window_geometry = self._set_window_geometry
        if set_window_geometry is None:
            set_window_geometry = self._commands().set_window_geometry
            object.__setattr__(self, "_set_window_geometry", set_window_geometry)
        set_window_geometry(
            x=x,
            y=y,
            width=width,
            height=height,
            maximized=bool(maximized),
        )

    def create_geometry_save_worker(
        self,
        request_id: int,
        *,
        geometry: tuple[int, int, int, int] | None,
        maximized: bool,
        write_guard=None,
        parent=None,
    ):
        from app.window_geometry_workers import WindowGeometrySaveWorker

        return WindowGeometrySaveWorker(
            request_id,
            geometry=geometry,
            maximized=bool(maximized),
            get_window_geometry=self.get_window_geometry,
            set_window_geometry=self.set_window_geometry,
            write_guard=write_guard,
            parent=parent,
        )


def build_window_geometry_feature() -> WindowGeometryFeature:
    return WindowGeometryFeature()


__all__ = ["WindowGeometryFeature", "build_window_geometry_feature"]
