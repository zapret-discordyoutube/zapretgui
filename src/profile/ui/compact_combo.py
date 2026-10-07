"""Выпадающий список с подробным меню и коротким выбранным значением,
его озвучка для экранного диктора и определение выбранной стратегии профиля.

Вынесено из прежнего файла списка стратегий: этими частями пользуется страница
профиля, к самому списку они не относятся.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QAction
from qfluentwidgets import ComboBox, MenuAnimationType


def _set_widget_text_if_changed(widget, text: str) -> bool:
    """Ленивый мост к profile.ui.profile_setup_page.set_widget_text_if_changed.

    Widget-state сеттеры остаются в модуле страницы (патч-цель тестов по пути
    profile.ui.profile_setup_page.*); ленивый импорт разрывает циклический
    импорт страницы и этого модуля и сохраняет действие патчей."""
    from profile.ui import profile_setup_page

    return profile_setup_page.set_widget_text_if_changed(widget, text)


class CompactDisplayComboBox(ComboBox):
    """ComboBox с подробным меню и коротким выбранным значением."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._compact_text_by_data: dict[str, str] = {}

    def addItem(self, text: str, icon=None, userData=None, compactText: str | None = None):  # noqa: N802
        super().addItem(text, icon=icon, userData=userData)
        if compactText is not None:
            self._compact_text_by_data[str(userData)] = str(compactText)
            self._sync_compact_text()

    def setCurrentIndex(self, index: int):  # noqa: N802
        super().setCurrentIndex(index)
        self._sync_compact_text()

    def setItemAccessibleText(self, index: int, text: str) -> None:  # noqa: N802
        if 0 <= int(index) < len(self.items):
            setattr(self.items[int(index)], "accessibleText", str(text or "").strip())

    def _create_accessible_combo_menu(self):
        menu = self._createComboMenu()
        for index, item in enumerate(self.items):
            action = QAction(item.icon, item.text, triggered=lambda _checked=False, row=index: self._onItemClicked(row))
            action.setEnabled(item.isEnabled)
            menu.addAction(action)
            accessible_text = str(getattr(item, "accessibleText", "") or "").strip()
            menu_item = action.property("item")
            if accessible_text and menu_item is not None:
                menu_item.setData(Qt.ItemDataRole.AccessibleTextRole, accessible_text)
                menu_item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, accessible_text)
        return menu

    def _showComboMenu(self) -> None:
        if not self.items:
            return

        menu = self._create_accessible_combo_menu()
        if menu.view.width() < self.width():
            menu.view.setMinimumWidth(self.width())
            menu.adjustSize()

        menu.setMaxVisibleItems(self.maxVisibleItems())
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.closedSignal.connect(self._onDropMenuClosed)
        self.dropMenu = menu

        if self.currentIndex() >= 0 and self.items:
            menu.setDefaultAction(menu.actions()[self.currentIndex()])

        x = -menu.width() // 2 + menu.layout().contentsMargins().left() + self.width() // 2
        down_pos = self.mapToGlobal(QPoint(x, self.height()))
        down_height = menu.view.heightForAnimation(down_pos, MenuAnimationType.DROP_DOWN)

        up_pos = self.mapToGlobal(QPoint(x, 0))
        up_height = menu.view.heightForAnimation(up_pos, MenuAnimationType.PULL_UP)

        if down_height >= up_height:
            menu.view.adjustSize(down_pos, MenuAnimationType.DROP_DOWN)
            menu.exec(down_pos, aniType=MenuAnimationType.DROP_DOWN)
        else:
            menu.view.adjustSize(up_pos, MenuAnimationType.PULL_UP)
            menu.exec(up_pos, aniType=MenuAnimationType.PULL_UP)

    def _sync_compact_text(self) -> None:
        index = self.currentIndex()
        if index < 0:
            return
        data = str(self.itemData(index))
        compact = self._compact_text_by_data.get(data)
        if compact:
            _set_widget_text_if_changed(self, compact)


def _current_strategy_id(payload) -> str:
    item = getattr(payload, "item", None)
    return str(getattr(item, "strategy_id", "") or "").strip()


def _combo_item_accessible_text(
    *,
    name: str,
    label: str,
    selected: bool,
    selected_word: str = "выбран",
    unselected_word: str = "не выбран",
) -> str:
    state = selected_word if selected else unselected_word
    return f"{str(name or '').strip()}: {str(label or '').strip()}, {state}"


def _sync_combo_items_accessibility(
    combo,
    *,
    name: str,
    selected_word: str = "выбран",
    unselected_word: str = "не выбран",
) -> None:
    if combo is None:
        return
    try:
        current_index = int(combo.currentIndex())
        count = int(combo.count())
    except Exception:
        return
    set_item_accessible_text = getattr(combo, "setItemAccessibleText", None)
    if not callable(set_item_accessible_text):
        return
    for index in range(count):
        try:
            label = str(combo.itemText(index) or "").strip()
        except Exception:
            label = ""
        if not label:
            continue
        set_item_accessible_text(
            index,
            _combo_item_accessible_text(
                name=name,
                label=label,
                selected=index == current_index,
                selected_word=selected_word,
                unselected_word=unselected_word,
            ),
        )


def _join_accessible_options(labels: list[str]) -> str:
    clean_labels = [str(label or "").strip() for label in labels if str(label or "").strip()]
    if not clean_labels:
        return ""
    if len(clean_labels) == 1:
        return clean_labels[0]
    return f"{', '.join(clean_labels[:-1])} или {clean_labels[-1]}"
