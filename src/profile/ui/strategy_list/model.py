"""Модель списка стратегий: видимые строки и ничего кроме них."""

from __future__ import annotations

from PyQt6.QtCore import QAbstractListModel, QModelIndex, Qt

from profile.strategy_list import ROW_SECTION, VisibleRow

# Строка целиком: отрисовка и список читают из неё всё нужное.
ROW_ROLE = int(Qt.ItemDataRole.UserRole) + 1
# Выбрана ли стратегия строки: по этой роли переезжает полоска акцента.
ACTIVE_ROLE = int(Qt.ItemDataRole.UserRole) + 2


def row_accessible_text(row: VisibleRow) -> str:
    if row.item is not None:
        text = row.item.accessible_text
        if row.twin_count > 1:
            state = "раскрыты" if row.twin_open else "свёрнуты"
            text = f"{text}, вариантов: {row.twin_count}, {state}"
        return text
    if row.section is not None:
        return f"Подзаголовок: {row.section.title}"
    group = row.group
    if group is None:
        return ""
    state = "раскрыта" if row.expanded else "свёрнута"
    parts = [f"Группа: {group.title}", f"стратегий: {group.count}", state]
    if group.description:
        parts.append(group.description)
    return ", ".join(parts)


class StrategyListModel(QAbstractListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: tuple[VisibleRow, ...] = ()

    def rows(self) -> tuple[VisibleRow, ...]:
        return self._rows

    def row_at(self, row: int) -> VisibleRow | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def row_of_key(self, key: str) -> int:
        for position, row in enumerate(self._rows):
            if row.key == key:
                return position
        return -1

    def set_rows(self, rows) -> bool:
        """Показывает новые строки. Возвращает True, если состав строк сменился.

        Обновление одно: если строки те же и стоят так же, сообщается только о
        тех, что изменились; иначе список перечитывает модель целиком.
        """
        rows = tuple(rows or ())
        if [row.key for row in rows] == [row.key for row in self._rows]:
            changed = [position for position, row in enumerate(rows) if row.shown != self._rows[position].shown]
            self._rows = rows
            for position in changed:
                index = self.index(position, 0)
                self.dataChanged.emit(index, index)
            return False
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()
        return True

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self._rows)

    def flags(self, index: QModelIndex):
        row = self.row_at(index.row()) if index.isValid() else None
        if row is None or row.kind == ROW_SECTION:
            # Подзаголовок — подпись: ни мышь, ни клавиатура на нём не останавливаются.
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)):
        row = self.row_at(index.row()) if index.isValid() else None
        if row is None:
            return None
        if role == ROW_ROLE:
            return row
        if role == ACTIVE_ROLE:
            return bool(row.item is not None and row.item.is_current)
        if role in (int(Qt.ItemDataRole.DisplayRole), int(Qt.ItemDataRole.AccessibleTextRole)):
            return row_accessible_text(row)
        return None
