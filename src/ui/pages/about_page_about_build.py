"""Build-helper вкладки «О программе» для About page."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout

from ui.accessibility import set_state_text
from ui.pages.about_page_accessibility import apply_about_buttons_accessibility
from ui.pages.about_page_help_accessibility import set_help_card_accessibility as set_link_card_accessibility
from ui.fluent_widgets import SettingsCard
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    PushSettingCard,
    StrongBodyLabel,
)
from ui.theme import get_cached_qta_pixmap, get_themed_qta_icon
from ui.widgets.motion_icon import MotionIcon
from ui.widgets.shimmer_label import ShimmerLabel
from ui.widgets.spinning_logo import SpinningLogo


@dataclass(slots=True)
class AboutPageAboutWidgets:
    about_section_version_label: object
    about_app_name_label: object
    about_version_value_label: object
    update_btn: object
    whats_new_btn: object
    about_section_subscription_label: object
    sub_status_icon: object
    sub_status_label: object
    sub_desc_label: object
    premium_btn: object
    kvn_btn: object
    help_card: object


def set_subscription_status_accessibility(label, text: object) -> None:
    value = " ".join(str(text or "").strip().split())
    if not value:
        return
    set_state_text(label, f"Статус подписки: {value}")


def set_subscription_description_accessibility(label, text: object) -> None:
    value = " ".join(str(text or "").strip().split())
    if not value:
        return
    set_state_text(label, f"Описание подписки: {value}")


def set_about_version_accessibility(app_name_label, version_label, *, app_name: object, app_version: object) -> None:
    app_name_value = " ".join(str(app_name or "").strip().split())
    app_version_value = " ".join(str(app_version or "").strip().split())
    if app_name_value:
        set_state_text(app_name_label, f"Название программы: {app_name_value}")
    if app_version_value:
        set_state_text(version_label, f"Версия программы: {app_version_value}")


def _build_about_logo(tokens) -> SpinningLogo:
    """Логотип программы рядом с версией; по клику делает оборот.

    Значок берём тот же, что в верхней панели окна. Если общий значок не
    задан (например, в тестах без файла .ico), показываем прежний щит.
    """
    app = QApplication.instance()
    icon = app.windowIcon() if app is not None else QIcon()
    if icon.isNull():
        icon = QIcon(get_cached_qta_pixmap('fa5s.shield-alt', color=tokens.accent_hex, size=40))
    return SpinningLogo(icon, box_size=48)


def build_about_page_about_content(
    layout: QVBoxLayout,
    *,
    tr_fn: Callable[[str, str], str],
    tokens,
    content_parent,
    app_version: str,
    make_section_label: Callable[[str], object],
    on_open_updates,
    on_open_whats_new,
    on_open_premium,
    on_open_kvn_tab,
    on_open_help_tab,
) -> AboutPageAboutWidgets:
    about_section_version_label = make_section_label(
        tr_fn("page.about.section.version", "Версия")
    )
    layout.addWidget(about_section_version_label)

    version_card = SettingsCard()
    version_layout = QHBoxLayout()
    version_layout.setSpacing(16)

    version_layout.addWidget(_build_about_logo(tokens))

    text_layout = QVBoxLayout()
    text_layout.setSpacing(2)
    app_name_text = tr_fn("page.about.app_name", "Zapret 2 GUI")
    # Название живёт лёгким бликом; выплывает вместе со всей карточкой.
    about_app_name_label = ShimmerLabel(app_name_text, enter=False, first_delay_ms=1400)
    about_app_name_label.set_glow_color(tokens.accent_hex if tokens.is_light else "#ffffff")
    about_app_name_label.setStyleSheet(
        f"QLabel {{ color: {tokens.fg}; font-size: 20px; font-weight: 600; "
        f"font-family: 'Segoe UI Variable Display', 'Segoe UI', sans-serif; }}"
    )
    about_version_value_label = CaptionLabel(
        tr_fn("page.about.version.value_template", "Версия {version}").format(version=app_version)
    )
    set_about_version_accessibility(
        about_app_name_label,
        about_version_value_label,
        app_name=app_name_text,
        app_version=app_version,
    )
    text_layout.addWidget(about_app_name_label)
    text_layout.addWidget(about_version_value_label)
    version_layout.addLayout(text_layout, 1)

    update_btn = PushButton(
        tr_fn("page.about.button.update_settings", "Настройка обновлений"),
        icon=FluentIcon.SYNC,
    )
    update_btn.clicked.connect(on_open_updates)

    # «Что нового»: почитать изменения установленной версии в любой момент.
    whats_new_btn = PushButton(
        tr_fn("page.about.button.whats_new", "Что нового"),
        icon=FluentIcon.INFO,
    )
    whats_new_btn.clicked.connect(on_open_whats_new)
    apply_about_buttons_accessibility(tr_fn=tr_fn, update_btn=update_btn, whats_new_btn=whats_new_btn)
    version_layout.addWidget(whats_new_btn)
    version_layout.addWidget(update_btn)

    version_card.add_layout(version_layout)
    layout.addWidget(version_card)
    layout.addSpacing(16)

    about_section_subscription_label = make_section_label(
        tr_fn("page.about.section.subscription", "Подписка")
    )
    layout.addWidget(about_section_subscription_label)

    sub_card = SettingsCard()
    sub_layout = QVBoxLayout()
    sub_layout.setSpacing(12)

    sub_status_layout = QHBoxLayout()
    sub_status_layout.setSpacing(8)

    # Значок статуса: у Premium звезда изредка поблёскивает (см. AboutPage).
    sub_status_icon = MotionIcon(size=18)
    sub_status_icon.setPixmap(get_cached_qta_pixmap('fa5s.user', color=tokens.fg_faint, size=18))
    sub_status_layout.addWidget(sub_status_icon)

    sub_status_label = StrongBodyLabel(
        tr_fn("page.about.subscription.free", "Free версия")
    )
    set_subscription_status_accessibility(sub_status_label, sub_status_label.text())
    sub_status_layout.addWidget(sub_status_label, 1)
    sub_layout.addLayout(sub_status_layout)

    sub_desc_label = CaptionLabel(
        tr_fn(
            "page.about.subscription.desc",
            "Подписка Zapret Premium открывает доступ к дополнительным темам, приоритетной поддержке и VPN-сервису.",
        )
    )
    sub_desc_label.setWordWrap(True)
    set_subscription_description_accessibility(sub_desc_label, sub_desc_label.text())
    sub_layout.addWidget(sub_desc_label)

    sub_btns = QHBoxLayout()
    sub_btns.setSpacing(8)
    premium_btn = PrimaryPushButton(
        tr_fn("page.about.button.premium_vpn", "Premium и VPN"),
        icon=FluentIcon.HEART,
    )
    apply_about_buttons_accessibility(tr_fn=tr_fn, premium_btn=premium_btn)
    premium_btn.clicked.connect(on_open_premium)
    sub_btns.addWidget(premium_btn)
    sub_btns.addStretch()
    kvn_btn = PushButton(
        tr_fn("page.about.button.zapret_kvn", "Zapret KVN"),
        icon=FluentIcon.GLOBE,
    )
    apply_about_buttons_accessibility(tr_fn=tr_fn, kvn_btn=kvn_btn)
    kvn_btn.clicked.connect(on_open_kvn_tab)
    sub_btns.addWidget(kvn_btn)
    sub_layout.addLayout(sub_btns)

    sub_card.add_layout(sub_layout)
    layout.addWidget(sub_card)
    layout.addSpacing(16)

    # Все ссылки (вики, видеокурс, чаты, новости) живут на вкладке «Справка»,
    # здесь только дорога туда — без дублей.
    help_description = tr_fn(
        "page.about.help_link.desc",
        "Вики, видеокурс, чаты и новости собраны на вкладке «Справка»",
    )
    help_card = PushSettingCard(
        tr_fn("page.about.help_link.button", "Открыть справку"),
        get_themed_qta_icon("fa5s.life-ring", color=tokens.accent_hex),
        tr_fn("page.about.help_link.title", "Нужна помощь?"),
        help_description,
    )
    set_link_card_accessibility(
        help_card,
        action_name=tr_fn("page.about.help_link.accessible_name", "Открыть вкладку «Справка»"),
        description=help_description,
    )
    help_card.clicked.connect(on_open_help_tab)
    layout.addWidget(help_card)

    return AboutPageAboutWidgets(
        about_section_version_label=about_section_version_label,
        about_app_name_label=about_app_name_label,
        about_version_value_label=about_version_value_label,
        update_btn=update_btn,
        whats_new_btn=whats_new_btn,
        about_section_subscription_label=about_section_subscription_label,
        sub_status_icon=sub_status_icon,
        sub_status_label=sub_status_label,
        sub_desc_label=sub_desc_label,
        premium_btn=premium_btn,
        kvn_btn=kvn_btn,
        help_card=help_card,
    )
