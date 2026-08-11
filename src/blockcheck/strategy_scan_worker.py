import logging
import queue
from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)


def _tr(key: str, *, default: str = "") -> str:
    from app.ui_texts import tr

    return tr(key, default=default)


class StrategyScanWorker(QObject):
    """Bridge synchronous StrategyScanner execution to Qt signals."""

    strategy_started = pyqtSignal(str, int, int)
    strategy_result = pyqtSignal(object)
    scan_log = pyqtSignal(str)
    phase_changed = pyqtSignal(str)
    scan_finished = pyqtSignal(object)
    finished = pyqtSignal(object)
    run_log_started = pyqtSignal(object)

    def __init__(
        self,
        target: str,
        mode: str = "quick",
        start_index: int = 0,
        scan_protocol: str = "tcp_https",
        udp_games_scope: str = "all",
        *,
        shutdown_sync: Callable[..., object],
        start_run_log: Callable[..., object],
        append_run_log: Callable[[object, str], None],
        close_run_log: Callable[[object], None],
        parent=None,
    ):
        super().__init__(parent)
        self._target = target
        self._mode = mode
        self._scan_protocol = scan_protocol
        self._udp_games_scope = udp_games_scope
        self._shutdown_sync = shutdown_sync
        self._start_run_log_action = start_run_log
        self._append_run_log_action = append_run_log
        self._close_run_log_action = close_run_log
        try:
            self._start_index = max(0, int(start_index))
        except Exception:
            self._start_index = 0
        self._scanner = None
        self._cancelled = False
        self._run_log_file = None
        self._pending_log_messages: queue.Queue[str] = queue.Queue()
        self._running = False
        self._target_resolution_error = ""

    def run(self):
        self._cancelled = False
        self._running = True
        report = None
        try:
            self._start_run_log()
            scan_target = self._resolve_scan_target()
            if scan_target is None:
                report = self._make_target_resolution_report()
            else:
                from blockcheck.strategy_scanner import StrategyScanner

                self._scanner = StrategyScanner(
                    target=scan_target,
                    mode=self._mode,
                    start_index=self._start_index,
                    callback=self,
                    scan_protocol=self._scan_protocol,
                    udp_games_scope=self._udp_games_scope,
                    shutdown_sync=self._shutdown_sync,
                )
                report = self._scanner.run()
            self._drain_pending_log_messages()
        except Exception as e:
            logger.exception("StrategyScanWorker crashed")
            self._append_run_log(f"ERROR: {e}")
            self.scan_log.emit(f"ERROR: {e}")
        finally:
            self._drain_pending_log_messages()
            try:
                self._close_run_log_action(self._run_log_file)
            except Exception:
                pass
            self._running = False
        self.scan_finished.emit(report)
        self.finished.emit(report)

    _GOOGLEVIDEO_DISCOVERY_TIMEOUT = 8.0

    def _resolve_scan_target(self) -> str | None:
        """Цель для сканера; None — прервать скан (детали в отчёте).

        Голый googlevideo.com не является видеосервером, его проверка всегда
        падает — вместо него подставляется свежий rr-хост этой сети.
        """
        if self._scan_protocol in ("stun_voice", "udp_games"):
            return self._target

        from blockcheck.googlevideo_discovery import (
            discover_googlevideo_host,
            is_bare_googlevideo_host,
        )

        if not is_bare_googlevideo_host(self._target):
            return self._target

        self.on_log(_tr(
            "page.strategy_scan.googlevideo_discovering",
            default=(
                "googlevideo.com — это не видеосервер: ищем актуальный "
                "rr*.googlevideo.com через YouTube..."
            ),
        ))
        result = discover_googlevideo_host(
            cancelled=self.is_cancelled,
            timeout=self._GOOGLEVIDEO_DISCOVERY_TIMEOUT,
        )
        if result.host:
            replaced = _tr(
                "page.strategy_scan.googlevideo_replaced",
                default="googlevideo.com заменён на видеосервер",
            )
            self.on_log(f"{replaced}: {result.host} ({result.detail})")
            return result.host

        self._target_resolution_error = str(result.detail or "")
        return None

    def _make_target_resolution_report(self):
        """Отчёт для скана, который не стартовал: UI получает штатный finish."""
        from blockcheck.scan_models import StrategyScanReport

        fatal = ""
        if not self._cancelled:
            fatal = _tr(
                "page.strategy_scan.googlevideo_failed",
                default=(
                    "Домен googlevideo.com нельзя проверить напрямую — это не "
                    "видеосервер. Найти актуальный rr*.googlevideo.com не удалось"
                ),
            )
            if self._target_resolution_error:
                fatal = f"{fatal}: {self._target_resolution_error}"
            self.on_log(f"ERROR: {fatal}")
        return StrategyScanReport(
            target=self._target,
            total_tested=self._start_index,
            cancelled=True,
            scan_protocol=self._scan_protocol,
            fatal_error=fatal,
        )

    def stop(self):
        self._cancelled = True
        if self._scanner:
            self._scanner.cancel()

    @property
    def is_running(self) -> bool:
        return bool(self._running)

    def on_strategy_started(self, name, index, total):
        self.strategy_started.emit(name, index, total)

    def on_strategy_result(self, result):
        self.strategy_result.emit(result)

    def on_log(self, message):
        self._drain_pending_log_messages()
        self._append_run_log(message)
        self.scan_log.emit(message)

    def on_phase(self, phase):
        self._drain_pending_log_messages()
        self._append_run_log(f"[PHASE] {phase}")
        self.phase_changed.emit(phase)

    def is_cancelled(self):
        return self._cancelled

    def record_run_log_message(self, message: str) -> None:
        self._pending_log_messages.put(str(message or ""))

    def _start_run_log(self) -> None:
        try:
            log_state = self._start_run_log_action(
                target=self._target,
                mode=self._mode,
                scan_protocol=self._scan_protocol,
                resume_index=self._start_index,
                udp_games_scope=self._udp_games_scope,
            )
            self._run_log_file = log_state.path
            self.run_log_started.emit(log_state.path)
        except Exception:
            logger.exception("StrategyScanWorker failed to start run log")
            self._run_log_file = None
            self.run_log_started.emit(None)

    def _append_run_log(self, message: str) -> None:
        try:
            self._append_run_log_action(self._run_log_file, message)
        except Exception:
            logger.exception("StrategyScanWorker failed to append run log")

    def _drain_pending_log_messages(self) -> None:
        while True:
            try:
                message = self._pending_log_messages.get_nowait()
            except queue.Empty:
                return
            self._append_run_log(message)
