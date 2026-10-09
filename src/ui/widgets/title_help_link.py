"""Ссылка на инструкцию справа от заголовка страницы."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget
from qfluentwidgets import FluentIcon, HyperlinkButton

from ui.accessibility import set_control_accessibility
from ui.fluent_widgets import set_tooltip

HELP_LINK_TEXT = "Как это б#&^ь работает?"


def add_title_help_link(
    page,
    url: str,
    *,
    tooltip: str,
    description: str,
    text: str = HELP_LINK_TEXT,
) -> tuple[QWidget, HyperlinkButton] | None:
    """Ставит заголовок страницы в один ряд со ссылкой на инструкцию.

    Возвращает ряд и саму ссылку. Ряд встаёт на место заголовка, поэтому
    страница, которая прячет заголовок, должна прятать именно ряд.
    """
    title_index = page.layout.indexOf(page.title_label)
    if title_index < 0:
        return None

    page.layout.removeWidget(page.title_label)
    header = QWidget(page.content)
    header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    title_layout = QHBoxLayout(header)
    title_layout.setContentsMargins(0, 0, 0, 0)
    title_layout.setSpacing(12)
    title_layout.addWidget(page.title_label, 0, Qt.AlignmentFlag.AlignVCenter)
    title_layout.addStretch(1)

    # В тексте QPushButton двойной && рисуется как один обычный символ &.
    button = HyperlinkButton(FluentIcon.HELP, url, text.replace("&", "&&"), header)
    set_tooltip(button, tooltip)
    set_control_accessibility(button, name=text, description=description)
    title_layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
    page.layout.insertWidget(title_index, header)
    return header, button
