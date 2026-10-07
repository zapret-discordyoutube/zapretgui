from __future__ import annotations

import json

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QListView

from .common import (
    PRESET_DROP_MARKER_PROPERTY,
    preset_canonical_drop_target_for_next_row,
    preset_drop_marker_for_target,
    preset_drop_target_for_position,
    set_current_index_if_changed,
)
from .model import PresetListModel
from ui.accessibility import set_state_text
from qfluentwidgets import ListView


SCREEN_READER_LIST_NAME_PROPERTY = "screenReaderListName"


class LinkedWheelListView(ListView):
    preset_activated = pyqtSignal(str)
    preset_move_requested = pyqtSignal(str, int)
    item_dropped = pyqtSignal(str, str, str, str, str)
    preset_context_requested = pyqtSignal(str, QPoint)
    folder_context_requested = pyqtSignal(str, QPoint)
    folder_toggle_requested = pyqtSignal(str)
    background_context_requested = pyqtSignal(QPoint)

    def __init__(self, parent=None, *, draggable_kinds: set[str] | None = None):
        super().__init__(parent)
        self._drag_start_pos: QPoint | None = None
        self._drag_source: tuple[str, str] | None = None
        self._draggable_kinds = {str(kind) for kind in (draggable_kinds or {"preset"})}
        # Пресеты укладываются слева направо с переносом: в широком окне они
        # встают в несколько столбцов. Ширину плиток задаёт PresetListDelegate,
        # заголовок папки всегда занимает линию целиком.
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.set_drop_marker(-1, "")

    def preset_column_count(self) -> int:
        # Число столбцов знает PresetListDelegate: оно зависит от ширины
        # списка и от самого длинного имени пресета.
        column_layout = getattr(self.itemDelegate(), "column_layout", None)
        if not self.isWrapping() or not callable(column_layout):
            return 1
        return int(column_layout()[0])

    def resizeEvent(self, event):  # noqa: N802
        if event.size().width() != event.oldSize().width():
            # Ширина столбцов зависит от ширины списка. Сам QListView
            # пересчитывает раскладку только через 0,1 с — строки успевают
            # мелькнуть в старом размере.
            self.scheduleDelayedItemsLayout()
        super().resizeEvent(event)

    def set_screen_reader_list_name(self, name: str) -> None:
        value = " ".join(str(name or "").strip().split())
        if value:
            self.setProperty(SCREEN_READER_LIST_NAME_PROPERTY, value)
        self._update_current_row_accessibility(self.currentIndex())

    def currentChanged(self, current, previous):  # noqa: N802
        super().currentChanged(current, previous)
        self._update_current_row_accessibility(current)

    def _update_current_row_accessibility(self, index) -> None:
        list_name = str(self.property(SCREEN_READER_LIST_NAME_PROPERTY) or "").strip()
        if not list_name:
            list_name = str(self.accessibleName() or "").strip()
        row_text = ""
        try:
            if index is not None and index.isValid():
                row_text = str(index.data(Qt.ItemDataRole.AccessibleTextRole) or "").strip()
        except Exception:
            row_text = ""
        if list_name and row_text:
            set_state_text(self, f"{list_name}: {row_text}")
        elif list_name:
            set_state_text(self, list_name)

    def set_drop_marker(self, row: int, destination_kind: str) -> None:
        marker = preset_drop_marker_for_target(row, destination_kind)
        self.set_drop_marker_payload(marker)

    def set_drop_marker_payload(self, marker: dict[str, object]) -> None:
        previous_marker = self.property(PRESET_DROP_MARKER_PROPERTY)
        if previous_marker == marker:
            return
        self.setProperty(PRESET_DROP_MARKER_PROPERTY, marker)
        self._update_drop_marker_rows(previous_marker, marker)

    def _update_drop_marker_rows(self, previous_marker, next_marker) -> None:
        model = self.model()
        if model is None:
            return
        for marker in (previous_marker, next_marker):
            if not isinstance(marker, dict):
                continue
            try:
                row = int(marker.get("row", -1))
            except Exception:
                continue
            if row < 0 or row >= model.rowCount():
                continue
            index = model.index(row, 0)
            if not index.isValid():
                continue
            rect = self.visualRect(index).adjusted(-4, -4, 4, 4)
            if rect.isValid():
                self.viewport().update(rect)

    def _drop_target_at(self, point: QPoint) -> tuple[dict[str, object], str, str]:
        drop_index = self.indexAt(point)
        if not drop_index.isValid():
            return {"marker": {"row": -1, "mode": ""}, "destination_kind": "end", "destination_row": -1}, "", ""
        destination_kind = str(drop_index.data(PresetListModel.KindRole) or "")
        row_rect = self.visualRect(drop_index)
        # В нескольких столбцах пресеты идут слева направо, поэтому место
        # «перед» или «после» строки выбирает её левая или правая половина.
        side_by_side = self.preset_column_count() > 1
        target = preset_drop_target_for_position(
            drop_index.row(),
            destination_kind,
            y=point.x() if side_by_side else point.y(),
            row_top=row_rect.left() if side_by_side else row_rect.top(),
            row_height=row_rect.width() if side_by_side else row_rect.height(),
        )
        if target["destination_kind"] == "preset_after":
            model = self.model()
            next_row = drop_index.row() + 1
            next_index = model.index(next_row, 0) if model is not None and next_row < model.rowCount() else None
            if next_index is not None and next_index.isValid():
                target = preset_canonical_drop_target_for_next_row(
                    target,
                    next_row=next_row,
                    next_kind=str(next_index.data(PresetListModel.KindRole) or ""),
                )
                drop_index = next_index if target["destination_kind"] == "preset" else drop_index
        if target["destination_kind"] in {"preset", "preset_after"}:
            return (
                target,
                str(drop_index.data(PresetListModel.FileNameRole) or ""),
                str(drop_index.data(PresetListModel.FolderKeyRole) or ""),
            )
        if target["destination_kind"] == "folder":
            folder_key = str(drop_index.data(PresetListModel.FolderKeyRole) or "")
            return target, folder_key, folder_key
        return target, "", ""

    def wheelEvent(self, event):
        scrollbar = self.verticalScrollBar()
        if scrollbar is None:
            super().wheelEvent(event)
            return

        delta = event.angleDelta().y()
        at_top = scrollbar.value() <= scrollbar.minimum()
        at_bottom = scrollbar.value() >= scrollbar.maximum()

        if (delta > 0 and at_top) or (delta < 0 and at_bottom):
            event.accept()
            return

        super().wheelEvent(event)
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return

        if self._drag_source is not None:
            self._update_internal_drag(event.position().toPoint())
            event.accept()
            return

        if self._drag_start_pos is None:
            super().mouseMoveEvent(event)
            return

        if (event.position().toPoint() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return

        index = self.indexAt(self._drag_start_pos)
        if not index.isValid():
            super().mouseMoveEvent(event)
            return

        kind = str(index.data(PresetListModel.KindRole) or "")
        if kind not in self._draggable_kinds:
            super().mouseMoveEvent(event)
            return

        source_id = str(index.data(PresetListModel.FileNameRole) or "").strip()
        if kind != "preset" or not source_id:
            super().mouseMoveEvent(event)
            return

        # Внутреннее перемещение строк не должно зависеть от системного OLE
        # drag-and-drop. В повышенном Windows-окне OLE специально отключён,
        # чтобы Проводник мог передавать preset-файлы через WM_DROPFILES.
        # Поэтому перестановку внутри списка ведём обычными событиями мыши.
        self._drag_source = (kind, source_id)
        self._drag_start_pos = None
        self._update_internal_drag(event.position().toPoint())
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = None
            if self._drag_source is not None:
                self._finish_internal_drag(event.position().toPoint())
                event.accept()
                return
        if event.button() == Qt.MouseButton.RightButton:
            index = self.indexAt(event.position().toPoint())
            if index.isValid() and str(index.data(PresetListModel.KindRole) or "") == "folder":
                folder_key = str(index.data(PresetListModel.FolderKeyRole) or "")
                if folder_key:
                    set_current_index_if_changed(self, index)
                    self.folder_context_requested.emit(folder_key, self.viewport().mapToGlobal(event.position().toPoint()))
                    event.accept()
                    return
            if index.isValid() and str(index.data(PresetListModel.KindRole) or "") == "preset":
                name = str(index.data(PresetListModel.FileNameRole) or "")
                if name:
                    set_current_index_if_changed(self, index)
                    self.preset_context_requested.emit(name, self.viewport().mapToGlobal(event.position().toPoint()))
                    event.accept()
                    return
            if not index.isValid():
                self.background_context_requested.emit(self.viewport().mapToGlobal(event.position().toPoint()))
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def _update_internal_drag(self, point: QPoint) -> None:
        if not self.viewport().rect().contains(point):
            self.set_drop_marker(-1, "")
            return
        target, _destination_id, _destination_folder_key = self._drop_target_at(point)
        self.set_drop_marker_payload(dict(target.get("marker") or {}))

    def _finish_internal_drag(self, point: QPoint) -> bool:
        source = self._drag_source
        self._drag_source = None
        self.set_drop_marker(-1, "")
        if source is None or not self.viewport().rect().contains(point):
            return False

        source_kind, source_id = source
        target, destination_id, destination_folder_key = self._drop_target_at(point)
        destination_kind = str(target.get("destination_kind") or "end")
        if destination_kind not in {"folder", "preset", "preset_after"}:
            destination_kind = "end"
        self.item_dropped.emit(
            source_kind,
            source_id,
            destination_kind,
            destination_id,
            destination_folder_key,
        )
        return True

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if not self.currentIndex().isValid() and self.model() is not None:
            for row in range(self.model().rowCount()):
                index = self.model().index(row, 0)
                if str(index.data(PresetListModel.KindRole) or "") == "preset":
                    self.setCurrentIndex(index)
                    break

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        side_by_side = self.preset_column_count() > 1
        arrow_keys = [Qt.Key.Key_Up, Qt.Key.Key_Down]
        if side_by_side:
            arrow_keys += [Qt.Key.Key_Left, Qt.Key.Key_Right]
        if key in arrow_keys:
            direction = -1 if key in (Qt.Key.Key_Up, Qt.Key.Key_Left) else 1
            if modifiers & Qt.KeyboardModifier.ControlModifier:
                if self._request_current_preset_move(direction):
                    event.accept()
                    return
            elif not modifiers & (
                Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.MetaModifier
                | Qt.KeyboardModifier.ShiftModifier
            ):
                # В столбцах стрелки вверх и вниз ведут на линию выше или ниже,
                # а соседний по порядку пресет стоит слева или справа.
                if side_by_side and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                    moved = self._move_current_to_preset_on_adjacent_line(direction)
                else:
                    moved = self._move_current_to_adjacent_preset(direction)
                if moved:
                    event.accept()
                    return
        if event.key() in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            direction = -1 if event.key() == Qt.Key.Key_PageUp else 1
            if self._request_current_preset_move(direction):
                event.accept()
                return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if self._activate_current_index_from_keyboard():
                event.accept()
                return
        if event.key() == Qt.Key.Key_Menu or (
            event.key() == Qt.Key.Key_F10 and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            if self._emit_context_requested_for_current_index():
                event.accept()
                return
        super().keyPressEvent(event)

    def _request_current_preset_move(self, direction: int) -> bool:
        index = self.currentIndex()
        if not index.isValid() or str(index.data(PresetListModel.KindRole) or "") != "preset":
            return False
        name = str(index.data(PresetListModel.FileNameRole) or "").strip()
        if not name:
            return False
        self.preset_move_requested.emit(name, -1 if int(direction) < 0 else 1)
        return True

    def _move_current_to_adjacent_preset(self, direction: int) -> bool:
        model = self.model()
        if model is None or model.rowCount() <= 0:
            return False

        step = -1 if int(direction) < 0 else 1
        current = self.currentIndex()
        row = current.row() if current.isValid() else (-1 if step > 0 else model.rowCount())
        candidate_row = row + step
        while 0 <= candidate_row < model.rowCount():
            candidate = model.index(candidate_row, 0)
            if str(candidate.data(PresetListModel.KindRole) or "") == "preset":
                self.setCurrentIndex(candidate)
                self.scrollTo(candidate)
                return True
            candidate_row += step

        # На границе списка стрелка считается обработанной: иначе базовый
        # QListView может перевести выделение на заголовок папки.
        return current.isValid()

    def _move_current_to_preset_on_adjacent_line(self, direction: int) -> bool:
        model = self.model()
        current = self.currentIndex()
        if model is None or not current.isValid():
            return self._move_current_to_adjacent_preset(direction)

        step = -1 if int(direction) < 0 else 1
        current_rect = self.visualRect(current)
        line_top = None
        best = None
        best_distance = 0
        row = current.row() + step
        while 0 <= row < model.rowCount():
            candidate = model.index(row, 0)
            row += step
            if str(candidate.data(PresetListModel.KindRole) or "") != "preset":
                if line_top is not None:
                    break
                continue
            rect = self.visualRect(candidate)
            if rect.top() == current_rect.top():
                continue
            if line_top is None:
                line_top = rect.top()
            elif rect.top() != line_top:
                break
            distance = abs(rect.left() - current_rect.left())
            if best is None or distance < best_distance:
                best = candidate
                best_distance = distance

        if best is not None:
            self.setCurrentIndex(best)
            self.scrollTo(best)
        # На границе списка стрелка тоже считается обработанной (см. выше).
        return True

    def _activate_current_index_from_keyboard(self) -> bool:
        index = self.currentIndex()
        if not index.isValid():
            return False
        kind = str(index.data(PresetListModel.KindRole) or "")
        if kind == "preset":
            name = str(index.data(PresetListModel.FileNameRole) or "")
            if not name:
                return False
            self.preset_activated.emit(name)
            return True
        if kind == "folder":
            folder_key = str(index.data(PresetListModel.FolderKeyRole) or "")
            if not folder_key:
                return False
            self.folder_toggle_requested.emit(folder_key)
            return True
        return False

    def _emit_context_requested_for_current_index(self) -> bool:
        index = self.currentIndex()
        if not index.isValid():
            return False
        kind = str(index.data(PresetListModel.KindRole) or "")
        if kind not in {"preset", "folder"}:
            return False
        rect = self.visualRect(index)
        if rect.isValid():
            point = rect.center()
        else:
            point = QPoint(0, 0)
        global_point = self.viewport().mapToGlobal(point)
        if kind == "preset":
            name = str(index.data(PresetListModel.FileNameRole) or "")
            if not name:
                return False
            self.preset_context_requested.emit(name, global_point)
            return True
        folder_key = str(index.data(PresetListModel.FolderKeyRole) or "")
        if not folder_key:
            return False
        self.folder_context_requested.emit(folder_key, global_point)
        return True

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-zapret-preset-item"):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-zapret-preset-item"):
            target, _destination_id, _destination_folder_key = self._drop_target_at(event.position().toPoint())
            self.set_drop_marker_payload(dict(target.get("marker") or {}))
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self.set_drop_marker(-1, "")
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if not event.mimeData().hasFormat("application/x-zapret-preset-item"):
            self.set_drop_marker(-1, "")
            super().dropEvent(event)
            return

        try:
            payload = json.loads(bytes(event.mimeData().data("application/x-zapret-preset-item")).decode("utf-8"))
        except Exception:
            self.set_drop_marker(-1, "")
            event.ignore()
            return

        source_kind = str(payload.get("kind") or "")
        source_id = str(payload.get("file_name") or payload.get("name") or "").strip()
        if source_kind != "preset" or not source_id:
            self.set_drop_marker(-1, "")
            event.ignore()
            return

        target, destination_id, destination_folder_key = self._drop_target_at(event.position().toPoint())
        destination_kind = "end"
        if str(target.get("destination_kind") or "") in {"folder", "preset", "preset_after"}:
            destination_kind = str(target.get("destination_kind") or "")

        self.item_dropped.emit(source_kind, source_id, destination_kind, destination_id, destination_folder_key)
        self.set_drop_marker(-1, "")
        event.acceptProposedAction()


__all__ = ["LinkedWheelListView", "preset_drop_marker_for_target", "preset_drop_target_for_position"]
