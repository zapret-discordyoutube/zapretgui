"""Build-helper вкладки Zapret KVN для About page."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    FluentIcon,
    PrimaryPushButton,
    PushSettingCard,
    SettingCardGroup,
    StrongBodyLabel,
)

from ui.accessibility import set_control_accessibility, set_state_text
from ui.fluent_widgets import insert_widget_into_setting_card_group
from ui.pages.about_page_kvn_accessibility import set_kvn_card_accessibility
from ui.theme import get_cached_qta_pixmap, get_themed_qta_icon
from ui.widgets.motion_icon import MotionIcon
from ui.widgets.shimmer_label import ShimmerLabel
from ui.widgets.stagger_float_in import skip_float_in
from ui.widgets.turning_globe import TurningGlobe


BOT_DESCRIPTION = "Оформление через Telegram-бота @zapretvpns_bot"
FEATURE_TILE_MIN_HEIGHT = 150


@dataclass(slots=True)
class AboutPageKvnWidgets:
    hero_wrap: object
    globe: object
    title_label: object
    bot_btn: object
    features_group: object
    yt_card: object
    game_card: object
    links_group: object
    tg_card: object
    gh_card: object


class _FeatureTile(CardWidget):
    """Плитка возможности: значок, заголовок и описание с переносом строк."""

    def __init__(self, *, icon_name: str, icon_color: str, title: str, description: str, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(6)

        self.icon = MotionIcon(self, size=24)
        self.icon.setPixmap(get_cached_qta_pixmap(icon_name, color=icon_color, size=24))
        layout.addWidget(self.icon)

        self.title_label = StrongBodyLabel(title, self)
        layout.addWidget(self.title_label)

        self.description_label = CaptionLabel(description, self)
        self.description_label.setWordWrap(True)
        layout.addWidget(self.description_label)
        layout.addStretch()
        # Высоту группы считают до того, как известна ширина; запас на три
        # строки описания нужен, когда окно узкое и текст переносится.
        self.setMinimumHeight(FEATURE_TILE_MIN_HEIGHT)

        set_state_text(self, f"{title}. {description}")
        set_control_accessibility(self, name=title, description=description)

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        # При наведении значок возможности коротко подпрыгивает.
        self.icon.bounce()


def _keep_natural_height(widget: QWidget) -> None:
    """Группа не растягивается по высоте.

    Если вкладке достанется лишняя высота, её заберёт растяжка в конце,
    а не промежутки между разделами.
    """
    widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)


def _build_hero(*, tokens, on_open_kvn_bot) -> tuple[QFrame, TurningGlobe, ShimmerLabel, PrimaryPushButton]:
    hero_wrap = QFrame()
    hero_wrap.setStyleSheet("QFrame { background: transparent; border: none; }")
    hero_row = QHBoxLayout(hero_wrap)
    hero_row.setContentsMargins(0, 8, 0, 0)
    hero_row.setSpacing(16)

    globe = TurningGlobe(hero_wrap, size=52, color=tokens.accent_hex)
    hero_row.addWidget(globe, 0, Qt.AlignmentFlag.AlignTop)

    text_column = QVBoxLayout()
    text_column.setSpacing(2)
    title_label = ShimmerLabel("Zapret KVN")
    title_label.set_glow_color(tokens.accent_hex if tokens.is_light else "#ffffff")
    title_label.setStyleSheet(
        f"QLabel {{ color: {tokens.fg}; font-size: 26px; font-weight: 700; "
        f"font-family: 'Segoe UI Variable Display', 'Segoe UI', sans-serif; }}"
    )
    text_column.addWidget(title_label)

    subtitle = ShimmerLabel("Уникальный туннель до любой страны мира", enter_delay_ms=150, shimmer=False)
    subtitle.setWordWrap(True)
    subtitle.setStyleSheet(
        f"QLabel {{ color: {tokens.fg}; font-size: 15px; font-weight: 600; "
        f"font-family: 'Segoe UI Variable Display', 'Segoe UI', sans-serif; }}"
    )
    text_column.addWidget(subtitle)

    desc = ShimmerLabel(
        "Создан передовыми мировыми инженерами (не является тем чем вы думаете)",
        enter_delay_ms=300,
        shimmer=False,
    )
    desc.setWordWrap(True)
    desc.setStyleSheet(
        f"QLabel {{ color: {tokens.fg_muted}; font-size: 12px; font-style: italic; "
        f"font-family: 'Palatino Linotype', 'Book Antiqua', 'Georgia', serif; "
        f"padding-top: 2px; }}"
    )
    text_column.addWidget(desc)
    hero_row.addLayout(text_column, 1)

    # FluentIcon: акцентная кнопка сама подбирает контрастный цвет значка под тему.
    bot_btn = PrimaryPushButton(FluentIcon.SHOPPING_CART, "Купить подписку")
    bot_btn.setMinimumWidth(170)
    set_state_text(bot_btn, "Купить подписку Zapret KVN")
    set_control_accessibility(bot_btn, name="Купить подписку Zapret KVN", description=BOT_DESCRIPTION)
    bot_btn.setToolTip(BOT_DESCRIPTION)
    bot_btn.clicked.connect(on_open_kvn_bot)
    hero_row.addWidget(bot_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    # У шапки свой вход: строки выплывают сами, общий эффект вкладки не нужен.
    skip_float_in(hero_wrap)
    return hero_wrap, globe, title_label, bot_btn


def build_about_page_kvn_content(
    layout: QVBoxLayout,
    *,
    tokens,
    content_parent,
    on_open_kvn_channel,
    on_open_kvn_bot,
    on_open_kvn_github,
) -> AboutPageKvnWidgets:
    hero_wrap, globe, title_label, bot_btn = _build_hero(tokens=tokens, on_open_kvn_bot=on_open_kvn_bot)
    layout.addWidget(hero_wrap)
    layout.addSpacing(8)

    features_title = "Возможности"
    features_group = SettingCardGroup(features_title, content_parent)
    set_state_text(features_group, f"Раздел Zapret KVN: {features_title}")
    _keep_natural_height(features_group)

    tiles_row = QWidget()
    tiles_layout = QHBoxLayout(tiles_row)
    tiles_layout.setContentsMargins(0, 0, 0, 0)
    tiles_layout.setSpacing(8)
    yt_card = _FeatureTile(
        icon_name="fa5s.rocket",
        icon_color=tokens.accent_hex,
        title="Ускорение YouTube и Discord",
        description="Позволяет ускорить замедленные сервера в случае если те перестали работать и начали деградировать",
    )
    game_card = _FeatureTile(
        icon_name="fa5s.gamepad",
        icon_color="#4CAF50",
        title="Игровые серверы",
        description="Также подходит для ускорения игровых серверов",
    )
    tiles_layout.addWidget(yt_card, 1)
    tiles_layout.addWidget(game_card, 1)
    # Не addSettingCard: раскладка карточек группы берёт текущую высоту
    # виджета (у нового — 480 px) и растянула бы плитки. Обычная раскладка
    # группы считает высоту по содержимому, с переносом строк.
    insert_widget_into_setting_card_group(features_group, features_group.vBoxLayout.count(), tiles_row)
    layout.addWidget(features_group)

    links_title = "Ссылки"
    links_group = SettingCardGroup(links_title, content_parent)
    set_state_text(links_group, f"Раздел Zapret KVN: {links_title}")
    _keep_natural_height(links_group)

    tg_card = PushSettingCard(
        "Открыть",
        get_themed_qta_icon("fa5b.telegram", color="#229ED9"),
        "Канал Zapret KVN",
        "Новости и обновления",
    )
    set_kvn_card_accessibility(
        tg_card,
        action_name="Открыть канал Zapret KVN",
        description="Новости и обновления",
    )
    tg_card.clicked.connect(on_open_kvn_channel)

    gh_card = PushSettingCard(
        "Открыть",
        get_themed_qta_icon("fa5b.github", color=tokens.accent_hex),
        "Исходный код",
        "Forgejo репозиторий Zapret KVN",
    )
    set_kvn_card_accessibility(
        gh_card,
        action_name="Открыть исходный код Zapret KVN",
        description="Forgejo репозиторий Zapret KVN",
    )
    gh_card.clicked.connect(on_open_kvn_github)

    links_group.addSettingCards([tg_card, gh_card])
    layout.addWidget(links_group)
    layout.addStretch()

    return AboutPageKvnWidgets(
        hero_wrap=hero_wrap,
        globe=globe,
        title_label=title_label,
        bot_btn=bot_btn,
        features_group=features_group,
        yt_card=yt_card,
        game_card=game_card,
        links_group=links_group,
        tg_card=tg_card,
        gh_card=gh_card,
    )
