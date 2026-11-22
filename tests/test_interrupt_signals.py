"""Ctrl+C in a console launch asks for a normal exit instead of raising KeyboardInterrupt inside a Qt callback."""

from __future__ import annotations

import signal

from PyQt6.QtCore import QCoreApplication

from main.interrupt_signals import install_interrupt_handlers


def _app() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication([])


def test_ctrl_c_asks_for_a_normal_exit_once_and_a_second_one_forces_it() -> None:
    app = _app()
    asked, forced = [], []
    previous = signal.getsignal(signal.SIGINT)
    try:
        handler = install_interrupt_handlers(app, lambda: asked.append(1), forced.append)
        assert signal.getsignal(signal.SIGINT) is handler and signal.getsignal(signal.SIGTERM) is handler
        signal.raise_signal(signal.SIGINT)  # no KeyboardInterrupt is raised here
        app.processEvents()
        assert asked == [1] and forced == []
        signal.raise_signal(signal.SIGINT)  # the program is already leaving: do not wait any more
        assert forced == [130]
    finally:
        signal.signal(signal.SIGINT, previous)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)


def test_a_quiet_timer_gives_python_the_turn_to_run_the_handler() -> None:
    app = _app()
    previous = signal.getsignal(signal.SIGINT)
    try:
        install_interrupt_handlers(app, lambda: None, lambda code: None)
        timer = app._interrupt_wake_timer
        assert timer.isActive() and 0 < timer.interval() <= 500
    finally:
        signal.signal(signal.SIGINT, previous)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
