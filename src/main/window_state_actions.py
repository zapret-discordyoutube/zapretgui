from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ui.window_premium_appearance import WindowPremiumAppearance


@dataclass(frozen=True, slots=True)
class WindowStateActions:
    window: Any
    ui_state_store: Any
    premium_appearance: WindowPremiumAppearance

    def start_premium_appearance(self) -> None:
        """Применяет фон и эффекты к окну по правилам Premium и следит за подпиской."""
        self.premium_appearance.start()

    def set_garland_enabled(self, enabled: bool) -> None:
        try:
            self.premium_appearance.sync_holiday_effects(garland=bool(enabled))
        except Exception as exc:
            from log.log import log

            log(f"❌ Ошибка переключения гирлянды: {exc}", "ERROR")

    def set_snowflakes_enabled(self, enabled: bool) -> None:
        try:
            self.premium_appearance.sync_holiday_effects(snowflakes=bool(enabled))
        except Exception as exc:
            from log.log import log

            log(f"❌ Ошибка переключения снежинок: {exc}", "ERROR")

    def set_animations_enabled(self, enabled: bool) -> None:
        try:
            from ui.window_appearance_state import on_animations_changed

            on_animations_changed(self.window, bool(enabled))
            self.premium_appearance.sync_holiday_effects(animations=bool(enabled))
        except Exception as exc:
            from log.log import log

            log(f"❌ Ошибка переключения анимаций: {exc}", "ERROR")

    def set_window_opacity(self, value: int) -> None:
        try:
            from ui.window_appearance_state import apply_window_opacity_value

            self.ui_state_store.set_window_opacity_value(value)
            apply_window_opacity_value(self.window, value)
        except Exception as exc:
            from log.log import log

            log(f"❌ Ошибка при установке прозрачности окна: {exc}", "ERROR")


__all__ = ["WindowStateActions"]
