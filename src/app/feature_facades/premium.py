from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any


@dataclass(frozen=True, slots=True)
class PremiumPageData:
    device_info: dict | None


@dataclass(slots=True)
class PremiumFeature:
    _thread_parent: Any = None
    _deps: Any = None
    _ui_state_store: Any = None
    _ui_actions: Any = None
    _status_runtime: Any = None
    _checker: Any = None
    _storage: Any = None
    _warmed_page_data: PremiumPageData | None = None

    @staticmethod
    def _commands():
        import donater.commands as premium_commands

        return premium_commands

    def _ensure_ui_actions(self):
        if self._ui_actions is None:
            from donater.subscription_ui import SubscriptionUiActions

            deps = self._deps
            self._ui_actions = SubscriptionUiActions(
                set_status=deps.set_status,
                ui_state_store=self._ui_state_store,
                mark_startup_ready=deps.mark_startup_ready,
            )
        return self._ui_actions

    def _ensure_status_runtime(self):
        # Создаётся и вызывается только из GUI-потока: внутри Qt-таймер.
        if self._status_runtime is None:
            self._status_runtime = self._commands().create_status_runtime(
                thread_parent=self._thread_parent,
                ui_actions=self._ensure_ui_actions(),
            )
        return self._status_runtime

    def prepare_subscription(self) -> None:
        self._ensure_status_runtime()

    def initialize_subscription(self) -> None:
        self._ensure_status_runtime().start()

    def cleanup_subscription(self) -> None:
        runtime = self._status_runtime
        self._status_runtime = None
        if runtime is not None:
            runtime.cleanup()

    def request_status_refresh(self, *, force: bool = False) -> None:
        """Попросить владельца статуса спросить сервер (см. status_runtime)."""
        self._ensure_status_runtime().request_refresh(force=bool(force))

    def subscribe_status_checks(self, on_checked, on_failed):
        """Подписывает страницу на итог каждой проверки. Возвращает отписку."""
        runtime = self._ensure_status_runtime()
        runtime.status_checked.connect(on_checked)
        runtime.status_failed.connect(on_failed)

        def _unsubscribe() -> None:
            for signal, handler in (
                (runtime.status_checked, on_checked),
                (runtime.status_failed, on_failed),
            ):
                try:
                    signal.disconnect(handler)
                except (TypeError, RuntimeError):
                    pass

        return _unsubscribe

    def apply_local_reset(self) -> None:
        """Привязка сброшена на этом устройстве: статус становится Free."""
        self._ensure_status_runtime().apply_local_reset()

    def ensure_checker_ready(self) -> bool:
        if self._checker is not None and self._storage is not None:
            return True
        bundle = self._commands().resolve_checker_bundle()
        self._checker = bundle.checker
        self._storage = bundle.storage
        return bool(bundle.init_ok and self._checker is not None and self._storage is not None)

    def is_checker_ready(self) -> bool:
        return bool(self._checker)

    def _require_checker(self):
        if self.ensure_checker_ready() and self._checker is not None:
            return self._checker
        raise RuntimeError("premium checker init failed")

    def open_extend_bot(self):
        return self._commands().open_extend_bot()

    def create_open_extend_bot_worker(self, request_id: int, *, parent=None):
        from donater.open_bot_worker import PremiumOpenBotWorker

        return PremiumOpenBotWorker(
            request_id,
            open_extend_bot=self.open_extend_bot,
            parent=parent,
        )

    def create_device_info_load_worker(self, request_id: int, *, current_time: int, parent=None):
        from donater.device_info_worker import PremiumDeviceInfoLoadWorker

        return PremiumDeviceInfoLoadWorker(
            request_id,
            read_device_info_snapshot=self.read_device_info_snapshot,
            current_time=int(current_time),
            parent=parent,
        )

    def create_reset_storage_worker(self, request_id: int, *, parent=None):
        from donater.reset_worker import PremiumResetStorageWorker

        return PremiumResetStorageWorker(request_id, reset_storage=self.reset_premium_storage, parent=parent)

    def create_premium_worker_thread(self, task):
        return self._commands().create_premium_worker_thread(task)

    def start_pairing(self):
        return self._commands().start_pairing(self._require_checker())

    def reset_premium_storage(self):
        self.ensure_checker_ready()
        self._commands().reset_premium_storage(self._checker, self._storage)
        self._checker = None
        self._storage = None

    def read_device_info_snapshot(self, *, current_time: int):
        if not self.ensure_checker_ready():
            return None
        snapshot = self._commands().read_device_storage_snapshot(
            self._storage,
            current_time=int(current_time),
        )
        snapshot["device_id"] = str(getattr(self._checker, "device_id", "") or "")
        return snapshot

    def test_connection(self):
        return self._require_checker().test_connection()

    def warm_page_data_cache(self) -> PremiumPageData:
        # Только данные устройства для страницы. Статус подписки в общий
        # UI-store пишет один владелец — status_runtime.
        current_time = int(time.time())
        device_info = self.read_device_info_snapshot(current_time=current_time)
        self._warmed_page_data = PremiumPageData(device_info=device_info)
        return self._warmed_page_data

    def consume_warmed_page_data(self) -> PremiumPageData | None:
        warmed = self._warmed_page_data
        self._warmed_page_data = None
        return warmed


def build_premium_feature(*, deps, ui_state_store) -> PremiumFeature:
    return PremiumFeature(
        _thread_parent=deps.thread_parent,
        _deps=deps,
        _ui_state_store=ui_state_store,
    )
