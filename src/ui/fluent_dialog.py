"""Безопасный жизненный цикл диалогов qfluentwidgets."""

from __future__ import annotations

from qfluentwidgets import (
    MessageBox as _QFluentMessageBox,
    MessageBoxBase as _QFluentMessageBoxBase,
)


class _ManagedMaskDialogLifecycle:
    """Снимает закрытый диалог со всех фильтров событий и глушит поздние события.

    MaskDialogBase ставит себя фильтром на родительское окно, windowMask и
    centerWidget. На Python 3.14 / PyQt6 6.11 фильтр может получить событие
    уже во время зачистки Python-объекта, когда атрибутов диалога больше нет,
    а C++-объект ещё жив — та же природа, что у setTitleBar в
    ZapretFluentWindow. Отсюда двойная защита: явное снятие всех фильтров при
    закрытии и guard в eventFilter на случай событий после зачистки.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mask_event_filter_host = self.window()

    def _detach_mask_event_filter(self) -> None:
        host = getattr(self, "_mask_event_filter_host", None)
        self._mask_event_filter_host = None
        watched = (
            host,
            getattr(self, "windowMask", None),
            getattr(self, "widget", None),
        )
        for target in watched:
            if target is None:
                continue
            try:
                target.removeEventFilter(self)
            except RuntimeError:
                # Объект уже мог быть уничтожен вместе с диалогом.
                pass

    def eventFilter(self, obj, e):  # noqa: N802 (Qt API)
        if getattr(self, "windowMask", None) is None:
            # Событие пришло до полной инициализации или во время зачистки
            # диалога — базовый eventFilter упал бы на self.windowMask.
            return False
        return super().eventFilter(obj, e)

    def _onDone(self, code):  # noqa: N802 (qfluentwidgets API)
        self._detach_mask_event_filter()
        return super()._onDone(code)

    def exec(self) -> int:
        try:
            return super().exec()
        finally:
            self._detach_mask_event_filter()


class MessageBoxBase(_ManagedMaskDialogLifecycle, _QFluentMessageBoxBase):
    """Основа проектных fluent-диалогов с корректным завершением."""


class MessageBox(_ManagedMaskDialogLifecycle, _QFluentMessageBox):
    """Стандартный fluent-диалог с корректным завершением."""


__all__ = ["MessageBox", "MessageBoxBase"]
