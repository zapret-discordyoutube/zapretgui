from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Callable

from platforms.base import GuiAutostartResult

ENTRY_NAME = "zapretgui.desktop"
TRAY_ARGUMENT = "--tray"


def autostart_dir() -> Path:
    """``$XDG_CONFIG_HOME/autostart`` (default ``~/.config/autostart``): the user's own, no root needed."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart"


def entry_path() -> Path:
    return autostart_dir() / ENTRY_NAME


def _quote_exec(value: str) -> str:
    """One argument of the Exec key (Desktop Entry spec: double quotes, backslash for the special four)."""
    if value and not any(ch in value for ch in ' \t\n"\'\\><~|&;$*?#()`%'):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return '"' + escaped.replace("%", "%%") + '"'


def render_entry(executable: str) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=ZapretGUI\n"
        "Comment=Zapret: bypass of blocks, starts in the tray\n"
        f"Exec={_quote_exec(executable)} {TRAY_ARGUMENT}\n"
        "Icon=zapretgui\n"
        "Terminal=false\n"
        "Categories=Network;\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def registered_executable() -> str | None:
    """The program the entry starts, or None when there is no entry or it cannot be read."""
    try:
        text = entry_path().read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("Exec="):
            try:
                parts = shlex.split(line[5:])
            except ValueError:
                return None
            return parts[0] if parts else None
    return None


class LinuxGuiAutostart:
    def enable(self, executable: str, *, status_cb: Callable[[str], None] | None = None) -> GuiAutostartResult:
        if status_cb is not None:
            status_cb("Создание записи автозапуска (XDG)...")
        path = entry_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".desktop.tmp")
            temporary.write_text(render_entry(executable), encoding="utf-8")
            os.replace(temporary, path)
        except OSError as exc:
            return GuiAutostartResult(success=False, message=f"Не удалось создать запись автозапуска {path}: {exc}")
        if status_cb is not None:
            status_cb("Автозапуск программы включён")
        return GuiAutostartResult(success=True)

    def disable(self) -> GuiAutostartResult:
        path = entry_path()
        removed = 0
        try:
            path.unlink()
            removed = 1
        except FileNotFoundError:
            pass
        except OSError as exc:
            return GuiAutostartResult(success=False, message=f"Не удалось удалить запись автозапуска {path}: {exc}")
        return GuiAutostartResult(success=True, removed_count=removed)

    def ensure_current(self, executable: str) -> bool:
        if registered_executable() == executable:
            return False
        return self.enable(executable).success
