from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout

from qfluentwidgets import CardWidget, FluentIcon

from presets.ui.control.control_page_runtime_shared import (
    BUTTON_ICON_TEXT_GAP_PROPERTY,
    set_button_text_accessibility,
)
from ui.pulsing_dot import PulsingDot, PacketFlowIndicator
from ui.accessibility import enable_keyboard_click, set_control_accessibility, set_state_text
from ui.theme import get_themed_qta_icon


ACTION_CARD_BUTTON_WIDTH = 156


@dataclass(slots=True)
class LastStatusMessageWidgets:
    card: object
    dot: object
    title_label: object
    message_label: object


@dataclass(slots=True)
class ModeStatusWidgets:
    card: object
    status_dot: object
    status_title: object
    status_desc: object
    close_btn: object
    progress_bar: object
    loading_label: object


STATUS_DOT_SIZE = 40


def build_mode_status_section_common(
    *,
    tr_fn,
    strong_body_label_cls,
    caption_label_cls,
    indeterminate_progress_bar_cls,
    close_button_cls,
    checking_key: str,
    checking_default: str,
    detecting_key: str,
    detecting_default: str,
    on_toggle,
    on_close,
    parent=None,
) -> ModeStatusWidgets:
    """Карточка «Статус работы». Точка в ней — выключатель Zapret."""
    status_card = CardWidget()
    status_layout = QHBoxLayout(status_card)
    status_layout.setContentsMargins(16, 12, 16, 12)
    status_layout.setSpacing(16)

    status_dot = PacketFlowIndicator(size=STATUS_DOT_SIZE)
    status_dot.set_clickable(True)
    status_dot.clicked.connect(on_toggle)
    # set_control_accessibility сам подключает Enter/Пробел к status_dot.click().
    set_control_accessibility(
        status_dot,
        description=tr_fn("launch.dot.description", "Нажмите на точку, чтобы запустить или остановить Zapret"),
    )
    set_state_text(status_dot, "Индикатор состояния Zapret: состояние пока не загружено")
    status_layout.addWidget(status_dot, 0, Qt.AlignmentFlag.AlignVCenter)

    status_text = QVBoxLayout()
    status_text.setContentsMargins(0, 0, 0, 0)
    status_text.setSpacing(2)

    status_title = strong_body_label_cls(tr_fn(checking_key, checking_default))
    status_desc = caption_label_cls(tr_fn(detecting_key, detecting_default))
    title_text = str(status_title.text() or "").strip()
    desc_text = str(status_desc.text() or "").strip()
    state_text = f"{title_text}: {desc_text}".strip(": ")
    if state_text:
        set_state_text(status_card, state_text)
    if title_text:
        set_state_text(status_title, f"Статус Zapret: {title_text}")
    if desc_text:
        set_state_text(status_desc, f"Описание состояния Zapret: {desc_text}")

    status_text.addWidget(status_title)
    status_text.addWidget(status_desc)

    progress_bar = indeterminate_progress_bar_cls(parent)
    progress_bar.setVisible(False)
    set_control_accessibility(
        progress_bar,
        name="Ход запуска Zapret: не выполняется",
        description="Показывает, что запуск или остановка Zapret выполняется.",
    )
    set_state_text(progress_bar, "Ход запуска Zapret: не выполняется")
    status_text.addSpacing(4)
    status_text.addWidget(progress_bar)

    loading_label = caption_label_cls("")
    loading_label.setVisible(False)
    set_state_text(loading_label, "Статус запуска Zapret: нет активного запуска")
    status_text.addWidget(loading_label)
    status_layout.addLayout(status_text, 1)

    close_text = tr_fn("launch.action.close_app", "Закрыть программу")
    close_btn = close_button_cls(FluentIcon.POWER_BUTTON, close_text)
    set_control_accessibility(
        close_btn,
        description=tr_fn("launch.action.close_app.description", "Остановить Zapret и закрыть программу"),
    )
    set_state_text(close_btn, close_text)
    close_btn.clicked.connect(on_close)
    close_btn.setVisible(False)
    status_layout.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    return ModeStatusWidgets(
        card=status_card,
        status_dot=status_dot,
        status_title=status_title,
        status_desc=status_desc,
        close_btn=close_btn,
        progress_bar=progress_bar,
        loading_label=loading_label,
    )


def build_last_status_message_card_common(
    *,
    tr_fn,
    strong_body_label_cls,
    caption_label_cls,
):
    card = CardWidget()
    layout = QHBoxLayout(card)
    layout.setContentsMargins(16, 12, 16, 12)
    layout.setSpacing(14)

    dot = PulsingDot()
    dot.set_color("#8ab4f8")
    set_state_text(dot, "Индикатор последнего сообщения: пока нет новых сообщений")
    layout.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)

    text_layout = QVBoxLayout()
    text_layout.setContentsMargins(0, 0, 0, 0)
    text_layout.setSpacing(2)

    title_label = strong_body_label_cls(
        tr_fn("page.control.last_message.title", "Последнее сообщение")
    )
    message_label = caption_label_cls(
        tr_fn("page.control.last_message.empty", "Пока нет новых сообщений")
    )
    message_label.setWordWrap(True)
    title_text = str(title_label.text() or "").strip()
    message_text = str(message_label.text() or "").strip()
    state_text = f"{title_text}: {message_text}".strip(": ")
    if state_text:
        set_state_text(card, state_text)
    if title_text:
        set_state_text(title_label, f"Раздел статуса Zapret: {title_text}")
    if message_text:
        set_state_text(message_label, f"Последнее сообщение Zapret: {message_text}")

    text_layout.addWidget(title_label)
    text_layout.addWidget(message_label)
    layout.addLayout(text_layout, 1)

    return LastStatusMessageWidgets(
        card=card,
        dot=dot,
        title_label=title_label,
        message_label=message_label,
    )


def build_onboarding_tour_card_common(*, push_setting_card_cls, tr_fn, on_click, parent=None):
    """Карточка «Как пользоваться программой»: повтор обучающего тура."""
    return build_deferred_themed_push_setting_card_common(
        push_setting_card_cls=push_setting_card_cls,
        button_text=tr_fn("page.control.onboarding_tour.button", "Показать"),
        icon_name="fa5s.graduation-cap",
        icon_color="#b39ddb",
        title_text=tr_fn("page.control.onboarding_tour.title", "Как пользоваться программой"),
        content_text=tr_fn(
            "page.control.onboarding_tour.desc",
            "Пошаговая экскурсия: как устроен Zapret, что такое пресеты, профили и стратегии и где что находится",
        ),
        on_click=on_click,
        button_icon_name=FluentIcon.PLAY,
        button_accessible_name=tr_fn("page.control.onboarding_tour.accessible_name", "Показать обучающий тур"),
        parent=parent,
    )


def build_deferred_themed_push_setting_card_common(
    *,
    push_setting_card_cls,
    button_text: str,
    icon_name: str,
    icon_color: str | None,
    title_text: str,
    content_text: str,
    on_click,
    button_icon_name=FluentIcon.LINK,
    button_alignment: str = "left",
    button_accessible_name: str | None = None,
    parent=None,
    delay_ms: int = 250,
):
    card = build_push_setting_card_common(
        push_setting_card_cls=push_setting_card_cls,
        button_text=button_text,
        icon=QIcon(),
        title_text=title_text,
        content_text=content_text,
        on_click=on_click,
        button_icon_name=button_icon_name,
        button_alignment=button_alignment,
        button_accessible_name=button_accessible_name,
        parent=parent,
    )
    schedule_push_setting_card_icon(card, icon_name=icon_name, icon_color=icon_color, delay_ms=delay_ms)
    return card


def schedule_push_setting_card_icon(card, *, icon_name: str, icon_color: str | None = None, delay_ms: int = 250) -> None:
    def _apply_icon() -> None:
        try:
            icon = get_themed_qta_icon(icon_name, color=icon_color) if icon_color else get_themed_qta_icon(icon_name)
            icon_label = getattr(card, "iconLabel", None)
            set_icon = getattr(icon_label, "setIcon", None)
            if callable(set_icon):
                set_icon(icon)
        except Exception:
            pass

    try:
        QTimer.singleShot(delay_ms, _apply_icon)
    except Exception:
        _apply_icon()


def build_push_setting_card_common(
    *,
    push_setting_card_cls,
    button_text: str,
    icon,
    title_text: str,
    content_text: str,
    on_click,
    button_icon_name=FluentIcon.LINK,
    button_alignment: str = "left",
    button_accessible_name: str | None = None,
    parent=None,
):
    if isinstance(icon, QPixmap):
        icon = QIcon(icon)

    card = push_setting_card_cls(
        button_text,
        icon,
        title_text,
        content_text or None,
        parent,
    )
    card.setProperty("noDrag", True)
    # Тот же размер значка, что у строк-переключателей (Win11ToggleRow), иначе
    # значок мельче, а заголовок сдвинут на пару пикселей относительно соседей.
    card.setIconSize(18, 18)
    accessible_name = button_accessible_name or _push_setting_button_accessible_name(button_text, title_text)
    set_state_text(card, accessible_name)
    set_control_accessibility(card, name=accessible_name, description=content_text)
    enable_keyboard_click(card)
    button = getattr(card, "button", None)
    if button is not None:
        if hasattr(button_icon_name, "icon"):
            button_icon_name = button_icon_name.icon()
        button.setIcon(button_icon_name)
        button.setProperty(BUTTON_ICON_TEXT_GAP_PROPERTY, True)
        button.setFixedWidth(ACTION_CARD_BUTTON_WIDTH)
        set_button_text_accessibility(
            button,
            button_text,
            accessible_name=accessible_name,
            description=content_text,
        )
    card.clicked.connect(on_click)
    return card


def _push_setting_button_accessible_name(button_text: str, title_text: str) -> str:
    button = str(button_text or "").strip()
    title = str(title_text or "").strip()
    if not button:
        return title
    if not title:
        return button
    if title.casefold().startswith(button.casefold()):
        return title
    return f"{button} {title[:1].lower()}{title[1:]}"
