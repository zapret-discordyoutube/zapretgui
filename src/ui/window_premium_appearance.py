from __future__ import annotations

from typing import Callable

from log.log import log


class WindowPremiumAppearance:
    """Единственное место, где окно применяет Premium-правила оформления.

    Получает статус подписки из общего UI-store (подписку оформляет
    ui/window_state_binder.bind_premium_appearance). Правило «что разрешено»
    берётся из `settings.appearance.resolve_premium_access`, то же самое
    правило использует страница «Оформление» — но страница только показывает,
    а применяет фон и эффекты к окну и сбрасывает настройки Free-версии
    только этот класс.
    """

    def __init__(
        self,
        *,
        window,
        ui_state_store,
        create_reset_worker: Callable[[], object],
    ) -> None:
        from ui.one_shot_worker_runtime import OneShotWorkerRuntime

        self._window = window
        self._ui_state_store = ui_state_store
        self._create_reset_worker = create_reset_worker
        self._reset_runtime = OneShotWorkerRuntime()
        self._unsubscribe: Callable[[], None] | None = None
        self._background_applied = False

    def start(self) -> None:
        """Применяет оформление к окну и начинает следить за подпиской."""
        if self._unsubscribe is not None:
            return
        from ui.window_state_binder import bind_premium_appearance

        self._unsubscribe = bind_premium_appearance(self, self._ui_state_store)

    def stop(self) -> None:
        unsubscribe, self._unsubscribe = self._unsubscribe, None
        if unsubscribe is not None:
            unsubscribe()

    def current_access(self):
        from settings.appearance import resolve_premium_access

        snapshot = self._ui_state_store.snapshot()
        return resolve_premium_access(
            subscription_known=snapshot.subscription_known,
            is_premium=snapshot.subscription_is_premium,
        )

    def sync_holiday_effects(
        self,
        *,
        garland: bool | None = None,
        snowflakes: bool | None = None,
        animations: bool | None = None,
    ) -> None:
        """Включает в окне те праздничные эффекты, которые сейчас разрешены.

        Аргументы — только что выбранные пользователем значения: сохранение
        идёт в фоне, и кэш настроек может ещё не успеть обновиться.
        """
        from settings.appearance import (
            AppearancePremiumEffectsPlan,
            effective_holiday_effects,
            peek_warmed_animations_enabled,
            peek_warmed_premium_effects,
        )
        from ui.window_appearance_state import apply_garland_enabled, apply_snowflakes_enabled

        saved = peek_warmed_premium_effects() or AppearancePremiumEffectsPlan(
            garland_enabled=False,
            snowflakes_enabled=False,
        )
        requested = AppearancePremiumEffectsPlan(
            garland_enabled=saved.garland_enabled if garland is None else bool(garland),
            snowflakes_enabled=saved.snowflakes_enabled if snowflakes is None else bool(snowflakes),
        )
        animations_enabled = bool(peek_warmed_animations_enabled()) if animations is None else bool(animations)
        effects = effective_holiday_effects(
            requested,
            animations_enabled=animations_enabled,
            access=self.current_access(),
        )
        apply_garland_enabled(self._window, effects.garland_enabled)
        apply_snowflakes_enabled(self._window, effects.snowflakes_enabled)

    def on_subscription_changed(self, _state=None, _changed_fields=frozenset()) -> None:
        """Пересчитывает фон и эффекты окна после смены статуса подписки.

        Вызывается прямо из записи статуса подписки в store: сбой оформления не должен
        оборвать дальнейшую обработку ответа сервера о подписке.
        """
        try:
            self._apply_subscription_access()
        except Exception as exc:
            log(f"❌ Не удалось применить Premium-оформление окна: {exc}", "ERROR")

    def _apply_subscription_access(self) -> None:
        from settings.appearance import (
            PREMIUM_BACKGROUND_PRESETS,
            effective_background_preset,
            peek_warmed_background_preset,
            peek_warmed_premium_effects,
        )
        from ui.theme import apply_window_background

        access = self.current_access()
        saved_preset = peek_warmed_background_preset() or "standard"
        preset = effective_background_preset(saved_preset, access)
        if not self._background_applied or preset != saved_preset:
            apply_window_background(self._window, preset=preset)
            self._background_applied = True

        self.sync_holiday_effects()

        if not access.reset_saved_premium:
            return
        effects = peek_warmed_premium_effects()
        has_premium_effects = effects is not None and (effects.garland_enabled or effects.snowflakes_enabled)
        if saved_preset in PREMIUM_BACKGROUND_PRESETS or has_premium_effects:
            self._start_reset()

    def _start_reset(self) -> None:
        if self._reset_runtime.is_running():
            return
        log("Бесплатная версия: сбрасываем сохранённые Premium-фон и эффекты", "INFO")
        self._reset_runtime.start_qthread_worker(
            worker_factory=lambda _request_id: self._create_reset_worker(),
            on_loaded=self._on_reset_completed,
            signal_includes_request_id=False,
            loaded_signal_name="completed",
        )

    def _on_reset_completed(self, _request_id: int, _result) -> None:
        self.sync_holiday_effects()


__all__ = ["WindowPremiumAppearance"]
