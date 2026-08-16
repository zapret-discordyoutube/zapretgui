"""Минимальные константы панели задач для qframelesswindow без COM."""

from __future__ import annotations

import sys
from types import ModuleType


_SHELLCON_VALUES = {
    "ABM_GETSTATE": 0x00000004,
    "ABM_GETTASKBARPOS": 0x00000005,
    "ABS_AUTOHIDE": 0x00000001,
}


def _package(name: str) -> ModuleType:
    module = ModuleType(name)
    module.__package__ = name
    module.__path__ = []  # type: ignore[attr-defined]
    return module


def install_win32_shellcon_compat() -> ModuleType:
    """Подставляет qframelesswindow только нужные ему WinAPI-константы.

    qframelesswindow импортирует ``win32comext.shell.shellcon`` лишь ради
    трёх чисел для ``SHAppBarMessage``. Настоящий пакет инициализирует COM и
    затягивает ``pythoncom``. Ранняя совместимость сохраняет поведение окна,
    но не включает COM-слой в runtime.
    """
    existing = sys.modules.get("win32comext.shell.shellcon")
    if existing is not None:
        return existing

    win32comext = sys.modules.setdefault("win32comext", _package("win32comext"))
    shell = sys.modules.setdefault(
        "win32comext.shell",
        _package("win32comext.shell"),
    )
    shellcon = ModuleType("win32comext.shell.shellcon")
    shellcon.__package__ = "win32comext.shell"
    for name, value in _SHELLCON_VALUES.items():
        setattr(shellcon, name, value)

    setattr(shell, "shellcon", shellcon)
    setattr(win32comext, "shell", shell)
    sys.modules[shellcon.__name__] = shellcon
    return shellcon


__all__ = ["install_win32_shellcon_compat"]
