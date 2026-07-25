from __future__ import annotations

from dataclasses import dataclass


DEFAULT_EXPAND_THRESHOLD = 700

# Сколько секунд одноразовая отметка нажатия на гамбургер ждёт связанный
# displayModeChanged. Срок покрывает анимацию сворачивания (~150ms) с запасом
# на медленные машины; первый сигнал забирает отметку без возможности повторного
# использования.
USER_TOGGLE_INTENT_WINDOW_S = 2.0

_EXPANDED_MODE = "EXPAND"
_OVERLAY_MODE = "MENU"
_COLLAPSED_MODES = frozenset({"COMPACT", "MINIMAL"})


def normalize_display_mode_name(display_mode) -> str:
    return str(getattr(display_mode, "name", display_mode) or "").upper()


@dataclass
class SidebarIntentController:
    """Владелец намерения пользователя «сайдбар развёрнут».

    qfluentwidgets меняет displayMode и по действиям пользователя, и сам —
    responsive-сворачивание, MENU-оверлей на узких окнах и переходные
    сворачивания при смене размеров окна (например, применение maximized на
    старте). Сигнал COMPACT при этом эмитится в конце анимации, когда окно
    уже может быть широким, поэтому ширина окна в момент сигнала не отличает
    пользователя от программной механики. Намерением считается только смена
    режима после подтверждённого нажатия на кнопку-гамбургер. Нажатие отмечается
    до внутреннего toggle() библиотеки, а ближайший displayModeChanged
    одноразово забирает отметку через consume_user_toggle().
    """

    intent: bool
    last_saved: bool | None = None
    applying: bool = False
    flushed: bool | None = None
    pending_user_toggle_at: float | None = None

    def note_user_toggle(self, now: float) -> None:
        """Отмечает нажатие на гамбургер до запуска toggle() библиотеки."""
        self.pending_user_toggle_at = float(now)

    def consume_user_toggle(self, now: float) -> bool:
        """Одноразово подтверждает, что ближайшая смена режима вызвана нажатием."""
        started_at = self.pending_user_toggle_at
        self.pending_user_toggle_at = None
        if started_at is None:
            return False
        elapsed = float(now) - started_at
        return 0.0 <= elapsed <= USER_TOGGLE_INTENT_WINDOW_S

    def classify_display_mode_change(
        self,
        display_mode,
        *,
        window_width: int,
        user_initiated: bool,
        threshold: int = DEFAULT_EXPAND_THRESHOLD,
    ) -> bool | None:
        """Возвращает новое намерение для сохранения или None (игнорировать)."""
        if self.applying:
            return None

        mode = normalize_display_mode_name(display_mode)
        wide = int(window_width) >= int(threshold)

        if mode == _EXPANDED_MODE and wide:
            new_intent = True
        elif mode in _COLLAPSED_MODES and wide:
            new_intent = False
        else:
            # MENU-оверлей и любые переходы на узком окне — responsive-механика.
            return None

        if not bool(user_initiated):
            # Программный переход (старт, maximize, responsive) — не намерение.
            return None

        if new_intent == self.intent:
            return None

        self.intent = new_intent
        return new_intent

    def should_reapply_expand(
        self,
        *,
        window_width: int,
        is_collapsed: bool,
        threshold: int = DEFAULT_EXPAND_THRESHOLD,
    ) -> bool:
        """Нужно ли развернуть сайдбар обратно после расширения окна."""
        if self.applying:
            return False
        return bool(self.intent) and bool(is_collapsed) and int(window_width) >= int(threshold)

    def mark_saved(self, value: bool) -> None:
        self.last_saved = bool(value)

    def pending_flush(self) -> bool | None:
        """Намерение для синхронной записи при выходе (None — уже записано flush'ем).

        last_saved от асинхронного воркера здесь сознательно не учитывается:
        потерянный или ложный сигнал saved не должен стоить пользователю
        состояния панели — дешевле записать значение при выходе ещё раз.
        Повторный вызов в той же цепочке выхода вернёт None благодаря
        mark_flushed.
        """
        if self.flushed is not None and bool(self.flushed) == bool(self.intent):
            return None
        return bool(self.intent)

    def mark_flushed(self, value: bool) -> None:
        """Фиксирует значение, записанное синхронным flush при выходе."""
        self.flushed = bool(value)
        self.mark_saved(value)


__all__ = [
    "DEFAULT_EXPAND_THRESHOLD",
    "USER_TOGGLE_INTENT_WINDOW_S",
    "SidebarIntentController",
    "normalize_display_mode_name",
]
