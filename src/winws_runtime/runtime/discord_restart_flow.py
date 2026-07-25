from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from log.log import log
from winws_runtime.runtime.status_feedback import runtime_owner_status_callback
from winws_runtime.runtime.thread_runtime import start_worker_thread


class DiscordRestartWorker(QObject):
    """Читает настройку и при необходимости перезапускает Discord вне UI."""

    finished = pyqtSignal()
    progress = pyqtSignal(str)

    def run(self) -> None:
        try:
            from discord.discord_restart import get_discord_restart_setting

            if not bool(get_discord_restart_setting(default=True)):
                return

            from discord.discord import DiscordRestartService

            DiscordRestartService(status_callback=self.progress.emit).restart_once()
        except Exception as exc:
            log(f"Discord restart check error: {exc}", "DEBUG")
        finally:
            self.finished.emit()


def maybe_restart_discord_after_runtime_apply(runtime_owner, *, skip_first_start: bool) -> bool:
    """Ставит проверку/перезапуск Discord в отдельный Qt-поток."""
    try:
        if skip_first_start and runtime_owner._first_runtime_apply:
            return False

        try:
            thread = runtime_owner._discord_restart_thread
            if thread is not None and thread.isRunning():
                return False
        except RuntimeError:
            runtime_owner._discord_restart_thread = None

        start_worker_thread(
            runtime_owner,
            thread_attr="_discord_restart_thread",
            worker_attr="_discord_restart_worker",
            worker=DiscordRestartWorker(),
            progress_slot=runtime_owner_status_callback(runtime_owner),
            cleanup_log_label="потока перезапуска Discord",
        )
        return True
    except Exception as exc:
        log(f"Discord restart dispatch error: {exc}", "DEBUG")
        return False
    finally:
        if skip_first_start:
            runtime_owner._first_runtime_apply = False
