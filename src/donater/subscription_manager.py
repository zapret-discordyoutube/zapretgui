from collections.abc import Callable

from PyQt6.QtCore import QObject
from donater.state import premium_state_from_activation_info
from donater.subscription_ui import (
    apply_subscription_progress_to_ui,
    apply_premium_state_to_store,
    apply_subscription_init_failed_to_ui,
    apply_subscription_ready_to_ui,
    apply_subscription_starting_to_ui,
    SubscriptionUiActions,
)
from donater.subscription_worker import SubscriptionInitWorker
from log.log import log
from ui.one_shot_worker_runtime import OneShotWorkerRuntime



class SubscriptionManager:
    """Запускает PremiumService в фоне и передаёт результат в UI-слой."""

    def __init__(
        self,
        *,
        thread_parent,
        ui_actions: SubscriptionUiActions,
        get_premium_checker: Callable[[], object],
        check_device_activation: Callable[..., dict],
    ):
        self.thread_parent = thread_parent
        self.ui_actions = ui_actions
        self._get_premium_checker = get_premium_checker
        self._check_device_activation = check_device_activation
        self._subscription_runtime = OneShotWorkerRuntime()
        self._subscription_worker: QObject | None = None
        self._cleanup_in_progress = False

    def initialize_async(self):
        """Асинхронно запускает первичную проверку подписки."""
        self._cleanup_in_progress = False
        if self._subscription_runtime.is_running():
            log("Инициализация подписки уже выполняется, повторный запуск пропущен", "DEBUG")
            return

        apply_subscription_starting_to_ui(set_status=self.ui_actions.set_status)

        def _create_worker(_request_id: int):
            return SubscriptionInitWorker(
                get_premium_checker=self._get_premium_checker,
                check_device_activation=self._check_device_activation,
            )

        def _bind_worker(worker: QObject) -> None:
            self._subscription_worker = worker
            worker.progress.connect(
                lambda message, actions=self.ui_actions: apply_subscription_progress_to_ui(
                    set_status=actions.set_status,
                    message=message,
                )
            )
            worker.finished.connect(self._on_subscription_ready)

        def _cleanup_subscription_objects(request_id: int, _thread) -> None:
            if self._subscription_runtime.is_current(request_id):
                self._subscription_worker = None

        self._subscription_runtime.start_qobject_worker(
            parent=self.thread_parent,
            worker_factory=_create_worker,
            bind_worker=_bind_worker,
            on_finished=_cleanup_subscription_objects,
        )

    def _on_subscription_ready(self, activation_info, success):
        """Обрабатывает результат фоновой проверки подписки."""
        if self._cleanup_in_progress:
            return
        if not success:
            log("PremiumService не инициализирован", "⚠ WARNING")
            apply_subscription_init_failed_to_ui(
                set_status=self.ui_actions.set_status,
                mark_startup_ready=self.ui_actions.mark_startup_ready,
            )
            return

        state = premium_state_from_activation_info(activation_info)
        log(
            f"Информация о подписке получена: premium={state.is_premium}, "
            f"days={state.days_remaining}, source={state.source}, "
            f"level={state.subscription_level}",
            "DEBUG",
        )

        apply_premium_state_to_store(
            ui_state_store=self.ui_actions.ui_state_store,
            state=state,
        )
        apply_subscription_ready_to_ui(
            init_holiday_effects=self.ui_actions.init_holiday_effects,
            effects_allowed=state.is_premium,
            set_status=self.ui_actions.set_status,
            mark_startup_ready=self.ui_actions.mark_startup_ready,
        )

        log(
            f"Подписка готова: {'Premium' if state.is_premium else 'Free'} "
            f"(уровень: {state.subscription_level})",
            "INFO",
        )

    def cleanup(self) -> None:
        """Останавливает фоновые потоки менеджера подписки при закрытии приложения."""
        self._cleanup_in_progress = True
        self._subscription_runtime.stop(
            blocking=False,
            log_fn=log,
            warning_prefix="Поток подписки",
        )
        self._subscription_runtime.cancel()
        self._subscription_worker = None
