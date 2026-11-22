"""Ctrl+C (SIGINT) and SIGTERM in a console launch: leave the program the normal way instead of a "crash" in the log.

Why it did not work before: Python only runs its signal handlers between bytecodes, and while Qt's event loop sits in C++
nothing runs them. When one finally ran, the default handler raised ``KeyboardInterrupt`` inside whatever Qt callback was
current (an event filter), where the global exception hook logged it as "UNCAUGHT EXCEPTION" and the program went on.

Now: the handler only asks for a normal exit (``window.request_exit``, the same path as the tray's "Exit" without stopping
the bypass), and a quiet timer wakes Python a few times a second so the handler gets its turn. A second Ctrl+C while the
program is already leaving ends the process at once.
"""

from __future__ import annotations

import signal
from collections.abc import Callable

from PyQt6.QtCore import QTimer

from log.log import log

WAKE_INTERVAL_MS = 250


def install_interrupt_handlers(app, ask_exit: Callable[[], None], force_exit: Callable[[int], None]) -> Callable[[int, object], None]:
    """Route SIGINT/SIGTERM to ``ask_exit`` (first time) and ``force_exit`` (second time). Returns the handler."""
    state = {"asked": False}

    def handler(signum, _frame) -> None:
        if state["asked"]:
            log(f"Сигнал {signum} повторно: выход без ожидания", "WARNING")
            force_exit(128 + int(signum))
            return
        state["asked"] = True
        log(f"Получен сигнал {signum} (Ctrl+C в консоли): обычный выход", "INFO")
        QTimer.singleShot(0, ask_exit)

    for name in ("SIGINT", "SIGTERM"):
        number = getattr(signal, name, None)
        if number is not None:
            try:
                signal.signal(number, handler)
            except (ValueError, OSError):  # not the main thread, or a platform without it
                pass
    wake = QTimer(app)
    wake.setInterval(WAKE_INTERVAL_MS)
    wake.timeout.connect(lambda: None)  # an empty Python callback is all it takes for pending signals to be handled
    wake.start()
    app._interrupt_wake_timer = wake  # keep it alive
    return handler
