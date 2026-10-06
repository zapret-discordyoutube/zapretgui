"""Единственный владелец проверки Premium-статуса на всё время работы программы.

Раньше в сеть ходила только страница Premium: по кнопке и пока она открыта.
Запуск читал один лишь сохранённый ответ сервера, и когда тот устаревал,
подписка «пропадала» до ручного обновления. Теперь расписание принадлежит
этому объекту и от открытых страниц не зависит:

    запуск      -> сохранённый статус без сети, затем проверка на сервере
    дальше      -> повтор по правилам donater.status_schedule
    создан код  -> частый опрос, пока бот не подтвердит привязку

Страница сама в сеть за статусом не ходит: она просит обновление через
request_refresh() и рисует то, что пришло в status_checked.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from donater.state import PremiumState, premium_state_from_activation_info
from donater.status_schedule import (
    REFRESH_MS,
    STARTUP_NETWORK_DELAY_MS,
    next_check_delay_ms,
    soft_refresh_allowed,
)
from donater.status_worker import PremiumStatusCheckWorker
from donater.subscription_ui import (
    SubscriptionUiActions,
    apply_premium_state_to_store,
    apply_subscription_init_failed_to_ui,
    apply_subscription_ready_to_ui,
    apply_subscription_starting_to_ui,
)
from log.log import log
from ui.one_shot_worker_runtime import OneShotWorkerRuntime


class PremiumStatusRuntime(QObject):
    """Планирует проверки PremiumService и передаёт результат в UI-слой."""

    # activation_info каждой завершённой проверки (и сетевой, и из кэша)
    status_checked = pyqtSignal(object)
    # текст ошибки, если проверку не удалось выполнить вовсе
    status_failed = pyqtSignal(str)

    def __init__(
        self,
        *,
        thread_parent,
        ui_actions: SubscriptionUiActions,
        get_premium_checker: Callable[[], object],
        check_device_activation: Callable[..., dict],
    ):
        # Без Qt-родителя: объект живёт столько, сколько его держит фасад
        # Premium, и не исчезает вместе с окном раньше cleanup().
        super().__init__()
        self.thread_parent = thread_parent
        self.ui_actions = ui_actions
        self._get_premium_checker = get_premium_checker
        self._check_device_activation = check_device_activation
        self._worker_runtime = OneShotWorkerRuntime()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timer)
        self._started = False
        self._startup_reported = False
        self._check_uses_cache = False
        self._queued_check = False
        self._queued_check_start_scheduled = False
        self._network_failures = 0
        self._last_network_check_at = 0.0
        self._last_published: tuple[bool, int | None] | None = None
        # Растёт при локальном сбросе привязки: результат проверки, начатой
        # до сброса, уже не описывает устройство и не должен попасть в UI.
        self._epoch = 0
        self._check_epoch = 0
        self._cleanup_in_progress = False

    # ── public ───────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Первый проход — без сети: интерфейс сразу узнаёт сохранённый статус."""
        self._cleanup_in_progress = False
        if self._started:
            log("Проверка подписки уже запущена, повторный запуск пропущен", "DEBUG")
            return
        self._started = True
        apply_subscription_starting_to_ui(set_status=self.ui_actions.set_status)
        if self._worker_runtime.is_running():
            return
        self._start_check(use_cache=True)

    def request_refresh(self, *, force: bool = False) -> None:
        """Спросить сервер сейчас.

        force=False — необязательный повод (открыли страницу Premium): не чаще
        раза в минуту. force=True — кнопка «Обновить статус» или только что
        созданный код привязки.
        """
        if self._cleanup_in_progress:
            return
        if not force and not soft_refresh_allowed(
            now=time.monotonic(),
            last_network_check_at=self._last_network_check_at,
        ):
            return
        if self._worker_runtime.is_running():
            # Идущая проверка могла начаться до события, ради которого нас
            # позвали, поэтому после неё нужен ещё один свежий проход.
            if force:
                self._queued_check = True
            return
        self._start_check(use_cache=False)

    def apply_local_reset(self) -> None:
        """Привязку сбросили на этом устройстве: оно Free без вопроса к серверу."""
        if self._cleanup_in_progress:
            return
        self._epoch += 1
        self._queued_check = False
        self._network_failures = 0
        self._publish(PremiumState(is_premium=False, source="reset"))
        self._timer.start(REFRESH_MS)

    def cleanup(self) -> None:
        """Останавливает расписание и фоновый поток при закрытии приложения."""
        self._cleanup_in_progress = True
        self._timer.stop()
        self._queued_check = False
        self._worker_runtime.stop(
            blocking=False,
            log_fn=log,
            warning_prefix="Поток подписки",
        )
        self._worker_runtime.cancel()

    # ── checks ───────────────────────────────────────────────────────────────

    def _start_check(self, *, use_cache: bool) -> None:
        self._timer.stop()
        self._queued_check = False
        self._check_uses_cache = bool(use_cache)
        self._check_epoch = self._epoch
        if not use_cache:
            self._last_network_check_at = time.monotonic()

        def _create_worker(request_id: int):
            return PremiumStatusCheckWorker(
                request_id,
                get_premium_checker=self._get_premium_checker,
                check_device_activation=self._check_device_activation,
                use_cache=use_cache,
            )

        def _bind_worker(worker: QObject) -> None:
            worker.finished.connect(self._on_check_finished)

        self._worker_runtime.start_qobject_worker(
            parent=self.thread_parent,
            worker_factory=_create_worker,
            bind_worker=_bind_worker,
            on_finished=self._on_worker_thread_finished,
        )

    def _on_timer(self) -> None:
        if self._cleanup_in_progress:
            return
        if self._worker_runtime.is_running():
            self._queued_check = True
            return
        self._start_check(use_cache=False)

    def _on_check_finished(self, request_id: int, payload, success: bool) -> None:
        if not self._worker_runtime.is_current(
            request_id,
            cleanup_in_progress=self._cleanup_in_progress,
        ):
            return
        used_cache = self._check_uses_cache
        first_report = not self._startup_reported
        self._startup_reported = True

        if self._check_epoch != self._epoch:
            # Проверка началась до локального сброса привязки.
            if first_report:
                apply_subscription_ready_to_ui(
                    set_status=self.ui_actions.set_status,
                    mark_startup_ready=self.ui_actions.mark_startup_ready,
                )
            self._timer.start(REFRESH_MS)
            return

        if not success or not isinstance(payload, dict):
            if first_report:
                log("PremiumService не инициализирован", "⚠ WARNING")
                apply_subscription_init_failed_to_ui(
                    set_status=self.ui_actions.set_status,
                    mark_startup_ready=self.ui_actions.mark_startup_ready,
                )
            self.status_failed.emit(str(payload or ""))
            self._schedule_next(None, used_cache=used_cache)
            return

        state = premium_state_from_activation_info(payload)
        changed = self._publish(state)
        if first_report or changed:
            log(
                f"Подписка: {'Premium' if state.is_premium else 'Free'} "
                f"(уровень: {state.subscription_level}, дней: {state.days_remaining}, "
                f"источник: {state.source}, статус: {state.status_msg})",
                "INFO",
            )
        if first_report:
            apply_subscription_ready_to_ui(
                set_status=self.ui_actions.set_status,
                mark_startup_ready=self.ui_actions.mark_startup_ready,
            )
        self.status_checked.emit(payload)
        self._schedule_next(payload, used_cache=used_cache)

    def _schedule_next(self, info: dict | None, *, used_cache: bool) -> None:
        if used_cache:
            # Сохранённый статус показан — теперь сверяемся с сервером.
            self._timer.start(STARTUP_NETWORK_DELAY_MS)
            return
        if info is None or info.get("network_failed"):
            self._network_failures += 1
        else:
            self._network_failures = 0
        self._timer.start(
            next_check_delay_ms(info, network_failures=self._network_failures)
        )

    def _publish(self, state: PremiumState) -> bool:
        """Пишет статус в общий UI-store, только если он изменился."""
        published = (bool(state.is_premium), state.days_remaining)
        if published == self._last_published:
            return False
        self._last_published = published
        apply_premium_state_to_store(
            ui_state_store=self.ui_actions.ui_state_store,
            state=state,
        )
        return True

    # ── queued check ─────────────────────────────────────────────────────────

    def _on_worker_thread_finished(self, _request_id: int, _thread) -> None:
        if not self._queued_check or self._cleanup_in_progress:
            return
        if self._queued_check_start_scheduled:
            return
        # Поток ещё завершается: новую проверку запускаем следующим витком
        # цикла событий, когда прежний worker точно освободит место.
        self._queued_check_start_scheduled = True
        QTimer.singleShot(0, self._run_queued_check)

    def _run_queued_check(self) -> None:
        self._queued_check_start_scheduled = False
        if self._cleanup_in_progress or not self._queued_check:
            return
        if self._worker_runtime.is_running():
            # Место ещё занято: таймер повторит попытку, запрос не теряется.
            self._queued_check = False
            self._timer.start(1_000)
            return
        self._start_check(use_cache=False)


__all__ = ["PremiumStatusRuntime"]
