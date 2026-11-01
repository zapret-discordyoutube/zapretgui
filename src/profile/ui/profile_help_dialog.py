"""Окно «Что это?» на странице профиля: подробное объяснение и кнопки на статьи вики.

Окно устроено как справка «Настройка пресета» (preset_setup_page): заголовок,
текст и кнопка «OK». Под текстом — кнопки статей вики: каждая открывает свою
статью в браузере под открытым зеркалом (utils.site_links). Тексты и ссылки —
profile.profile_help_texts.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QWidget
from qfluentwidgets import CaptionLabel, FlowLayout, FluentIcon, PushButton

from profile.profile_help_texts import HelpContent
from ui.accessibility import set_control_accessibility, set_state_text
from ui.fluent_dialog import MessageBox
from ui.fluent_widgets import set_tooltip
from ui.message_box_accessibility import set_message_box_button_accessibility
from utils import site_links

HELP_BUTTON_TEXT = "Что это?"


def make_help_button(parent, *, description: str) -> PushButton:
    """Кнопка «Что это?» в том же виде, что у списка профилей пресета."""
    button = PushButton(HELP_BUTTON_TEXT, parent, FluentIcon.QUESTION)
    set_tooltip(button, description)
    set_control_accessibility(button, name=HELP_BUTTON_TEXT, description=description)
    set_state_text(button, HELP_BUTTON_TEXT)
    return button


def _link_button(link, parent) -> PushButton:
    button = PushButton(link.title, parent, FluentIcon.LINK)
    button.clicked.connect(lambda _checked=False, url=link.url: site_links.open_in_browser(url))
    set_tooltip(button, link.description)
    set_control_accessibility(button, name=f"Открыть статью «{link.title}»", description=link.description)
    set_state_text(button, link.title)
    return button


def build_help_box(content: HelpContent, parent) -> MessageBox:
    box = MessageBox(content.title, content.text, parent)
    if content.links:
        caption = CaptionLabel("Подробнее с примерами — в статьях вики:", box)
        box.textLayout.addWidget(caption)
        links_host = QWidget(box)
        flow = FlowLayout(links_host, needAni=False)
        flow.setContentsMargins(0, 0, 0, 0)
        flow.setHorizontalSpacing(8)
        flow.setVerticalSpacing(8)
        box.link_buttons = []
        for link in content.links:
            button = _link_button(link, links_host)
            flow.addWidget(button)
            box.link_buttons.append(button)
        box.textLayout.addWidget(links_host)
    box.cancelButton.hide()
    set_message_box_button_accessibility(
        box,
        yes_name=f"Закрыть справку «{content.title}»",
        yes_description="Закрывает окно с объяснением.",
        cancel_name="Отменить закрытие справки",
        cancel_description="Скрытая кнопка отмены в справочном окне.",
    )
    return box


def show_help(content: HelpContent, parent) -> None:
    build_help_box(content, parent).exec()


__all__ = ["HELP_BUTTON_TEXT", "build_help_box", "make_help_button", "show_help"]
