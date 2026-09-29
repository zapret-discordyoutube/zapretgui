"""Мост между движком подбора и окном программы.

Подбор идёт в своём потоке: движок (``strategy_search.engine``) блокирующий.
Отсюда в окно уходят сигналы Qt, а обратно приходят только два действия:
«стоп» (флаг и обрыв сетевых проверок, без ожидания процессов) и ответ на
вопрос «цель открывается и без обхода — проверять всё равно?».
"""

import logging
import queue
import threading
from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)


def _tr(key: str, *, default: str = "") -> str:
    from app.ui_texts import tr

    return tr(key, default=default)


class StrategyScanWorker(QObject):
    strategy_started = pyqtSignal(str, int, int)
    # Строки текущей стратегии: окно подбирает к ним шутку про приём обхода.
    strategy_args_started = pyqtSignal(str)
    # Шаг подбора для панели хода: (шаг, статус, текст).
    stage_changed = pyqtSignal(str, str, str)
    strategy_result = pyqtSignal(object)
    scan_log = pyqtSignal(str)
    phase_changed = pyqtSignal(str)
    # Цель открывается без обхода: окно спрашивает пользователя и отвечает
    # через ``answer_continue``.
    continue_question = pyqtSignal(str)
    scan_finished = pyqtSignal(object)
    finished = pyqtSignal(object)
    run_log_started = pyqtSignal(object)

    def __init__(
        self,
        target: str,
        mode: str = "quick",
        scan_protocol: str = "tcp_https",
        udp_games_scope: str = "all",
        *,
        from_start: bool = False,
        shutdown_sync: Callable[..., object],
        start_run_log: Callable[..., object],
        append_run_log: Callable[[object, str], None],
        close_run_log: Callable[[object], None],
        load_fakes_catalog: Callable[[], object] | None = None,
        environment_factory: Callable[..., object] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._target = target
        self._mode = mode
        self._scan_protocol = scan_protocol
        self._udp_games_scope = udp_games_scope
        self._from_start = bool(from_start)
        self._shutdown_sync = shutdown_sync
        self._start_run_log_action = start_run_log
        self._append_run_log_action = append_run_log
        self._close_run_log_action = close_run_log
        self._load_fakes_catalog = load_fakes_catalog
        self._environment_factory = environment_factory
        self._search = None
        self._cancelled = False
        self._run_log_file = None
        self._pending_log_messages: queue.Queue[str] = queue.Queue()
        self._running = False
        self._target_resolution_error = ""
        self._answer_event = threading.Event()
        self._answer = False
        self._runtime_was_running = False
        self._restore_runtime: Callable[[], object] | None = None

    # --- Поток подбора -----------------------------------------------------------

    def run(self):
        self._cancelled = False
        self._running = True
        report = None
        try:
            self._start_run_log()
            probe_host = self._resolve_probe_host()
            if probe_host is None:
                report = self._make_target_resolution_report()
            else:
                from blockcheck.strategy_search.engine import SearchRequest, run_strategy_search

                request = SearchRequest(
                    target=self._target,
                    scan_protocol=self._scan_protocol,
                    mode=self._mode,
                    udp_games_scope=self._udp_games_scope,
                    probe_host=probe_host,
                    from_start=self._from_start,
                )
                report = run_strategy_search(
                    request,
                    env=self._make_environment(),
                    events=_WorkerEvents(self),
                    on_created=self._remember_search,
                )
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

    def _make_environment(self):
        factory = self._environment_factory
        if factory is None:
            from blockcheck.strategy_search.environment import RealEnvironment

            factory = RealEnvironment
        return factory(
            shutdown_sync=self._shutdown_sync,
            load_fakes_catalog=self._load_fakes_catalog,
            log=self.log,
        )

    def _remember_search(self, search) -> None:
        self._search = search
        if self._cancelled:
            search.cancel()

    _GOOGLEVIDEO_DISCOVERY_TIMEOUT = 8.0

    def _resolve_probe_host(self) -> str | None:
        """Какой хост проверять; None — подбор не начинать (детали в отчёте).

        Голый googlevideo.com не является видеосервером, его проверка всегда
        падает — вместо него проверяется свежий rr-хост этой сети, а профиль
        пишется на googlevideo.com, чтобы подошёл любой видеосервер.
        """
        if self._scan_protocol in ("stun_voice", "udp_games"):
            return ""

        from blockcheck.googlevideo_discovery import (
            discover_googlevideo_host,
            is_bare_googlevideo_host,
        )

        if not is_bare_googlevideo_host(self._target):
            return ""

        self.log(_tr(
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
            self.log(f"{replaced}: {result.host} ({result.detail})")
            return result.host

        self._target_resolution_error = str(result.detail or "")
        return None

    def _make_target_resolution_report(self):
        """Отчёт для подбора, который не стартовал: окно получает штатный конец."""
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
            self.log(f"ERROR: {fatal}")
        return StrategyScanReport(
            target=self._target,
            total_tested=0,
            cancelled=True,
            scan_protocol=self._scan_protocol,
            fatal_error=fatal,
        )

    # --- Команды из окна -------------------------------------------------------------

    def stop(self):
        """Из потока окна: флаг и обрыв проверок. Процесс winws2 гасит поток подбора."""
        self._cancelled = True
        self._answer_event.set()
        search = self._search
        if search is not None:
            search.cancel()

    def set_runtime_restore(self, *, was_running: bool, restore: Callable[[], object]) -> None:
        """Работал ли Zapret до подбора и как его вернуть (вызывается из окна)."""
        self._runtime_was_running = bool(was_running)
        self._restore_runtime = restore

    def cancel_runtime_restore(self) -> None:
        """Zapret запускает сам пользователь — возвращать его после подбора не нужно."""
        self._restore_runtime = None

    def restore_runtime_if_needed(self) -> bool:
        """Из потока окна после конца подбора: вернуть Zapret, если он работал."""
        restore = self._restore_runtime
        self._restore_runtime = None
        if not self._runtime_was_running or restore is None:
            return False
        self.log("Подбор закончен — запускаю Zapret снова, как было до подбора")
        restore()
        return True

    def answer_continue(self, proceed: bool) -> None:
        self._answer = bool(proceed)
        self._answer_event.set()

    @property
    def is_running(self) -> bool:
        return bool(self._running)

    # --- События движка (поток подбора) -------------------------------------------------

    def ask_continue(self, reason: str) -> bool:
        self._answer = False
        self._answer_event.clear()
        self.continue_question.emit(str(reason or ""))
        while not self._answer_event.wait(0.5):
            if self._cancelled:
                return False
        return bool(self._answer) and not self._cancelled

    def log(self, message):
        self._drain_pending_log_messages()
        self._append_run_log(message)
        self.scan_log.emit(message)

    def phase(self, text):
        self._drain_pending_log_messages()
        self._append_run_log(f"[PHASE] {text}")
        self.phase_changed.emit(text)

    def is_cancelled(self):
        return self._cancelled

    # --- Лог запуска ---------------------------------------------------------------------

    def record_run_log_message(self, message: str) -> None:
        self._pending_log_messages.put(str(message or ""))

    def _start_run_log(self) -> None:
        try:
            log_state = self._start_run_log_action(
                target=self._target,
                mode=self._mode,
                scan_protocol=self._scan_protocol,
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


class _WorkerEvents:
    """События движка → сигналы воркера (у воркера сигналы с теми же именами)."""

    def __init__(self, worker: StrategyScanWorker) -> None:
        self._worker = worker

    def log(self, message: str) -> None:
        self._worker.log(message)

    def phase(self, text: str) -> None:
        self._worker.phase(text)

    def strategy_started(self, name: str, index: int, total: int, args: str = "") -> None:
        self._worker.strategy_args_started.emit(str(args or ""))
        self._worker.strategy_started.emit(name, index, total)

    def stage(self, step: str, status: str, text: str = "") -> None:
        self._worker.stage_changed.emit(str(step), str(status), str(text or ""))

    def strategy_result(self, result) -> None:
        self._worker.strategy_result.emit(result)

    def ask_continue(self, reason: str) -> bool:
        return self._worker.ask_continue(reason)

    def is_cancelled(self) -> bool:
        return self._worker.is_cancelled()
