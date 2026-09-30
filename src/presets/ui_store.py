from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from .file_store import PresetFileStore
from .selection_service import PresetSelectionService


class PresetUiStore(QObject):
    """Шина Qt-сигналов о пресетах одного движка (winws1 или winws2).

    Своего состояния почти не держит: выбранный пресет живёт в настройках
    (PresetSelectionService), список — в PresetFileStore. Здесь только
    последнее отправленное имя — чтобы не слать одно переключение дважды.
    """

    presets_changed = pyqtSignal()
    preset_switched = pyqtSignal(str)
    preset_identity_changed = pyqtSignal(str)
    preset_content_changed = pyqtSignal(str)
    preset_content_changed_with_reason = pyqtSignal(str, str)
    # (пропавший файл, запасной файл): выбранный пресет не найден, для запуска
    # используется запасной; выбор в настройках не меняется.
    preset_selection_fallback = pyqtSignal(str, str)
    # Вернулся выбранный файл: runtime обязан перейти на него, даже если
    # «последнее переключение» у него уже это имя (DPI мог перезапуститься
    # на запасном, пока файла не было).
    preset_selection_restored = pyqtSignal(str)

    def __init__(
        self,
        engine: str,
        preset_file_store: PresetFileStore,
        selection_service: PresetSelectionService,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._engine = str(engine or "").strip()
        self._preset_file_store = preset_file_store
        self._selection_service = selection_service
        self._selected_source_file_name: Optional[str] = None

    def notify_preset_content_changed(self, file_name: str, *, content_change_kind: str = "") -> None:
        candidate = str(file_name or "").strip()
        if not candidate:
            return
        clean_kind = str(content_change_kind or "").strip()
        self.preset_content_changed.emit(candidate)
        self.preset_content_changed_with_reason.emit(candidate, clean_kind)

    def notify_presets_changed(self) -> None:
        self.presets_changed.emit()

    def notify_preset_switched(self, file_name: str) -> None:
        selected = str(file_name or "").strip() or None
        current = str(self._selected_source_file_name or "").strip() or None
        if current and selected and current.casefold() == selected.casefold():
            return
        self._selected_source_file_name = selected
        self.preset_switched.emit(self._selected_source_file_name or "")

    def notify_preset_identity_changed(self, file_name: str) -> None:
        selected = str(file_name or "").strip() or None
        self._selected_source_file_name = selected
        self.preset_identity_changed.emit(self._selected_source_file_name or "")

    def notify_selection_changed(self, file_name: str, reason: str, detail: str = "") -> None:
        """Событие PresetSelectionService: единая точка смены выбора."""
        from .selection_service import SELECTION_REASON_FALLBACK, SELECTION_REASON_RESTORED, SELECTION_REASON_USER

        if reason == SELECTION_REASON_FALLBACK:
            # Работающий DPI не переключаем на запасной пресет сам по себе:
            # только сообщаем (страницы покажут запасной активным).
            self.preset_selection_fallback.emit(str(detail or ""), str(file_name or ""))
            return
        if reason == SELECTION_REASON_RESTORED:
            self.preset_selection_restored.emit(str(file_name or ""))
        if reason != SELECTION_REASON_USER or not self._selected_source_file_name:
            # Возврат файла: имя может совпасть с последним отправленным
            # (до подмены) — всё равно сообщаем, иначе страницы и runtime
            # остались бы на показанном запасном.
            self._selected_source_file_name = str(file_name or "").strip() or None
            self.preset_switched.emit(self._selected_source_file_name or "")
            return
        self.notify_preset_switched(file_name)
