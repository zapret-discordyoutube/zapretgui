from __future__ import annotations

import ctypes

from platforms.base import MessageKind, print_to_stderr

# MessageBoxW flags: MB_OK | the icon.
_ICONS = {
    MessageKind.INFO: 0x40,
    MessageKind.WARNING: 0x30,
    MessageKind.ERROR: 0x10,
}


class WindowsMessageBox:
    def show(self, title: str, message: str, kind: MessageKind = MessageKind.ERROR) -> None:
        try:
            ctypes.windll.user32.MessageBoxW(None, message, title, _ICONS[kind])
        except Exception:
            print_to_stderr(title, message)
