"""Окно «Что это?» на странице профиля: подробное объяснение и кнопки на статьи вики.

Окно устроено как справка «Настройка пресета» (preset_setup_page): заголовок,
текст и кнопка «OK». Под текстом — кнопки статей вики: каждая открывает свою
статью в браузере под открытым зеркалом (utils.site_links). Тексты и ссылки —
profile.profile_help_texts.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, FlowLayout, FluentIcon, PushButton, SingleDirectionScrollArea

from profile.profile_help_texts import HelpContent
from ui.accessibility import set_control_accessibility, set_state_text
from ui.fluent_dialog import MessageBox
from ui.fluent_widgets import set_tooltip
from ui.message_box_accessibility import set_message_box_button_accessibility
from utils import site_links

HELP_BUTTON_TEXT = "Что это?"

# Окно не выше родителя: в окне программы высотой 760 px длинная справка
# уезжала под кнопки статей и OK, и конец текста было не прочитать (снимок
# с win10, 21.1.7.227). Текст теперь прокручивается в своей области.
_HELP_CHROME_HEIGHT = 330
_HELP_MIN_TEXT_HEIGHT = 160
_HELP_TEXT_WIDTH = 680


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


def _scrolling_text(box: MessageBox, text: str, parent) -> SingleDirectionScrollArea:
    """Текст справки в прокручиваемой области вместо обычной подписи окна."""
    label = BodyLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    area = SingleDirectionScrollArea(box, orient=Qt.Orientation.Vertical)
    area.setWidget(label)
    area.setWidgetResizable(True)
    area.enableTransparentBackground()
    area.setMinimumWidth(_HELP_TEXT_WIDTH)
    host_height = int(getattr(parent, "height", lambda: 0)() or 0)
    area.setMaximumHeight(max(_HELP_MIN_TEXT_HEIGHT, host_height - _HELP_CHROME_HEIGHT) if host_height else 420)
    label.setMinimumWidth(_HELP_TEXT_WIDTH - 24)
    area.setMinimumHeight(min(label.heightForWidth(_HELP_TEXT_WIDTH - 24), area.maximumHeight()))
    return area


def build_help_box(content: HelpContent, parent) -> MessageBox:
    box = MessageBox(content.title, "", parent)
    # Подпись окна с переносом по числу символов не умеет прокрутку: прячем её
    # и ставим на то же место прокручиваемый текст.
    box.contentLabel.hide()
    box.text_area = _scrolling_text(box, content.text, parent)
    box.textLayout.insertWidget(box.textLayout.indexOf(box.contentLabel) + 1, box.text_area)
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
