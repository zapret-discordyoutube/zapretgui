from __future__ import annotations

from dataclasses import dataclass, replace
from threading import RLock
from typing import Callable, Iterable


@dataclass(frozen=True, slots=True)
class AppUiState:
    launch_method: str = ""
    launch_phase: str = "stopped"
    launch_running: bool = False
    launch_busy: bool = False
    launch_busy_text: str = ""
    launch_last_error: str = ""
    last_status_message: str = ""
    current_strategy_summary: str = ""
    preset_content_change_kind: str = ""
    # Пока первая проверка Premium не завершилась, False в subscription_is_premium
    # означает «ещё не знаем», а не Free — такой статус нельзя показывать и
    # по нему нельзя отключать Premium-настройки.
    subscription_known: bool = False
    subscription_is_premium: bool = False
    subscription_days_remaining: int | None = None
    garland_enabled: bool = False
    snowflakes_enabled: bool = False
    window_opacity: int = 100
    active_preset_revision: int = 0
    active_preset_file_name: str = ""
    preset_content_revision: int = 0
    preset_structure_revision: int = 0
    mode_revision: int = 0


UiStateCallback = Callable[[AppUiState, frozenset[str]], None]


class MainWindowStateStore:
    """Единый window-level store состояния без зависимости от QWidget/QObject.

    Здесь хранится только та часть состояния, которая действительно нужна
    подписчикам UI и общим app-level helper'ам. Внутренний process-tracking
    launch-контура сюда больше не входит.

    Подписчики — живые виджеты, поэтому доставка колбэков обязана происходить
    в GUI-потоке. Сам store про Qt не знает: за перевод в GUI-поток отвечает
    опциональный маршалер (`app/ui_thread_marshaller.py`) с duck-typed
    контрактом `is_ui_thread()` / `post(callable)`. Без маршалера поведение
    остаётся полностью синхронным (тесты, headless-сценарии).
    """

    def __init__(
        self,
        initial_state: AppUiState | None = None,
        *,
        ui_thread_marshaller: object | None = None,
    ) -> None:
        self._state = initial_state or AppUiState()
        self._lock = RLock()
        self._subscribers: list[tuple[frozenset[str] | None, UiStateCallback]] = []
        self._ui_thread_marshaller = ui_thread_marshaller

    def snapshot(self) -> AppUiState:
        with self._lock:
            return replace(self._state)

    def subscribe(
        self,
        callback: UiStateCallback,
        *,
        fields: Iterable[str] | None = None,
        emit_initial: bool = False,
    ) -> Callable[[], None]:
        watched_fields = frozenset(fields) if fields is not None else None
        with self._lock:
            self._subscribers.append((watched_fields, callback))

        if emit_initial:
            callback(self.snapshot(), frozenset())

        def _unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove((watched_fields, callback))
                except ValueError:
                    pass

        return _unsubscribe

    def update(self, **changes) -> bool:
        if not changes:
            return False

        with self._lock:
            state = self._state
            real_changes = {}
            for field_name, value in changes.items():
                if field_name not in AppUiState.__dataclass_fields__:
                    raise AttributeError(f"Unknown AppUiState field: {field_name}")
                if getattr(state, field_name) != value:
                    real_changes[field_name] = value

            if not real_changes:
                return False

            self._state = replace(state, **real_changes)
            snapshot = replace(self._state)

        changed_fields = frozenset(real_changes.keys())
        self._notify_subscribers(snapshot, changed_fields)
        return True

    def set_ui_thread_marshaller(self, marshaller: object | None) -> None:
        """Задаёт маршалер доставки колбэков в GUI-поток."""
        self._ui_thread_marshaller = marshaller

    def post_to_ui_thread(self, action: Callable[[], None]) -> None:
        """Выполняет `action` в GUI-потоке (сразу, если уже в нём)."""
        if not callable(action):
            return
        marshaller = self._ui_thread_marshaller
        if marshaller is not None and not self._is_ui_thread(marshaller):
            marshaller.post(action)
            return
        action()

    def _notify_subscribers(self, snapshot: AppUiState, changed_fields: frozenset[str]) -> None:
        marshaller = self._ui_thread_marshaller
        if marshaller is not None and not self._is_ui_thread(marshaller):
            self._log_cross_thread_update(changed_fields)
            marshaller.post(lambda: self._deliver_to_subscribers(snapshot, changed_fields))
            return

        self._deliver_to_subscribers(snapshot, changed_fields)

    def _deliver_to_subscribers(self, snapshot: AppUiState, changed_fields: frozenset[str]) -> None:
        # Список подписчиков перечитывается в момент доставки: между
        # отложенной публикацией и её выполнением подписчик мог отписаться
        # (страница закрылась), и звать его уже нельзя.
        with self._lock:
            subscribers = list(self._subscribers)

        for watched_fields, callback in subscribers:
            if watched_fields is None or watched_fields & changed_fields:
                callback(snapshot, changed_fields)

    @staticmethod
    def _is_ui_thread(marshaller: object) -> bool:
        checker = getattr(marshaller, "is_ui_thread", None)
        if not callable(checker):
            return True
        try:
            return bool(checker())
        except Exception:
            return True

    @staticmethod
    def _log_cross_thread_update(changed_fields: frozenset[str]) -> None:
        try:
            from threading import current_thread

            from log.log import log

            log(
                "UI state обновлён вне GUI-потока "
                f"(поток={current_thread().name}, поля={sorted(changed_fields)}) — "
                "доставка подписчикам перенесена в GUI-поток",
                "⚠ WARNING",
            )
        except Exception:
            pass

    def set_launch_busy(self, busy: bool, text: str = "") -> bool:
        if not busy:
            text = ""
        return self.update(launch_busy=bool(busy), launch_busy_text=str(text or ""))

    def set_current_strategy_summary(self, summary: str) -> bool:
        return self.update(current_strategy_summary=str(summary or ""))

    def set_last_status_message(self, message: str) -> bool:
        return self.update(last_status_message=str(message or ""))

    def set_subscription(self, is_premium: bool, days_remaining: int | None = None) -> bool:
        normalized_days = None if not is_premium else days_remaining
        return self.update(
            subscription_known=True,
            subscription_is_premium=bool(is_premium),
            subscription_days_remaining=normalized_days,
        )

    def set_holiday_overlays(self, garland_enabled: bool, snowflakes_enabled: bool) -> bool:
        return self.update(
            garland_enabled=bool(garland_enabled),
            snowflakes_enabled=bool(snowflakes_enabled),
        )

    def set_window_opacity_value(self, value: int) -> bool:
        return self.update(window_opacity=max(0, min(100, int(value))))

    def bump_active_preset_revision(self, *, file_name: str = "") -> bool:
        current = self.snapshot().active_preset_revision
        return self.update(
            active_preset_revision=int(current) + 1,
            active_preset_file_name=str(file_name or "").strip(),
        )

    def bump_preset_content_revision(self, *, content_change_kind: str = "") -> bool:
        current = self.snapshot().preset_content_revision
        return self.update(
            preset_content_revision=int(current) + 1,
            preset_content_change_kind=str(content_change_kind or "").strip(),
        )

    def bump_preset_structure_revision(self) -> bool:
        current = self.snapshot().preset_structure_revision
        return self.update(preset_structure_revision=int(current) + 1)

    def bump_mode_revision(self) -> bool:
        current = self.snapshot().mode_revision
        return self.update(mode_revision=int(current) + 1)


class AppRuntimeState:
    """Узкий доступ к состоянию запуска и автозапуска.

    Это не отдельный источник истины. Все данные читаются из
    `MainWindowStateStore`, а состояние DPI записывает `LaunchRuntimeService`.
    """

    def __init__(self, store: MainWindowStateStore) -> None:
        self._store_ref = store

    def snapshot(self) -> AppUiState:
        return self._store_ref.snapshot()

    def is_launch_running(self) -> bool:
        return bool(self.snapshot().launch_running)

    def current_launch_phase(self) -> str:
        return str(self.snapshot().launch_phase or "").strip().lower()

    def last_launch_error(self) -> str:
        return str(self.snapshot().launch_last_error or "").strip()
