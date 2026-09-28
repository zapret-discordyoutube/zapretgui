"""Показ настоящего контекстного меню во время обучающего тура.

Тур не может открыть меню по-настоящему: меню модальное и забрало бы
мышь у карточки тура. Поэтому страница собирает то же самое меню тем же
кодом, что и по правой кнопке мыши, а здесь с него снимается картинка и
кладётся рядом со строкой списка. Выглядит в точности как живое меню, но
нажать в нём ничего нельзя — тур ничего не меняет в пресете.
"""

from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtWidgets import QLabel, QWidget


MENU_PREVIEW_OBJECT_NAME = "onboardingMenuPreview"


def create_menu_preview(host: QWidget, menu) -> QLabel | None:
    """Снимает картинку с собранного (не показанного) меню и удаляет меню."""
    try:
        menu.adjustSize()
        pixmap = menu.grab()
    finally:
        menu.deleteLater()
    if pixmap.isNull():
        return None
    # Сначала родитель, потом show(): иначе мелькнёт отдельное окно.
    label = QLabel(host)
    label.setObjectName(MENU_PREVIEW_OBJECT_NAME)
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    label.setPixmap(pixmap)
    label.resize(pixmap.deviceIndependentSize().toSize())
    label.hide()
    return label


def place_menu_preview(preview: QLabel, anchor: QWidget, anchor_rect: QRect) -> None:
    """Ставит картинку туда, где открылось бы меню по правой кнопке на строке."""
    host = preview.parentWidget()
    if host is None or sip.isdeleted(anchor):
        return
    top_left = anchor.mapTo(host, anchor_rect.topLeft())
    row = QRect(top_left, anchor_rect.size())
    size = preview.size()
    # Как будто нажали правой кнопкой ближе к началу строки.
    x = row.left() + min(row.width() // 3, 240)
    y = row.center().y()
    bounds = host.rect()
    if y + size.height() > bounds.bottom():
        y = row.top() - size.height()
    x = max(bounds.left(), min(x, bounds.right() - size.width()))
    y = max(bounds.top(), min(y, bounds.bottom() - size.height()))
    position = QPoint(x, y)
    if preview.pos() != position:
        preview.move(position)
    if not preview.isVisible():
        preview.show()
        preview.raise_()


def remove_menu_preview(preview: QLabel | None) -> None:
    if preview is None or sip.isdeleted(preview):
        return
    preview.hide()
    preview.deleteLater()


__all__ = [
    "MENU_PREVIEW_OBJECT_NAME",
    "create_menu_preview",
    "place_menu_preview",
    "remove_menu_preview",
]
