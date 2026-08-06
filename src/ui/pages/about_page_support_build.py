"""Build-helper вкладки «Поддержка» для About page."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

from qfluentwidgets import SettingCardGroup, PushSettingCard, PrimaryPushSettingCard
from ui.accessibility import set_state_text
from ui.pages.about_page_support_accessibility import set_support_card_accessibility
from ui.theme import get_themed_qta_icon


@dataclass(slots=True)
class AboutPageSupportWidgets:
    discussions_group: object
    discussions_card: object
    community_group: object
    telegram_card: object
    discord_card: object


def build_about_page_support_content(
    layout,
    *,
    tr_fn: Callable[[str, str], str],
    content_parent,
    tokens,
    on_open_discussions,
    on_open_telegram,
    on_open_discord,
) -> AboutPageSupportWidgets:
    discussions_title = tr_fn("page.about.support.section.discussions", "Forgejo Issues")
    discussions_group = SettingCardGroup(discussions_title, content_parent)
    set_state_text(discussions_group, f"Раздел поддержки: {discussions_title}")
    discussions_card = PrimaryPushSettingCard(
        tr_fn("page.about.support.discussions.button", "Открыть"),
        get_themed_qta_icon("fa5b.github", color=tokens.accent_hex),
        tr_fn("page.about.support.discussions.title", "Forgejo Issues"),
        tr_fn(
            "page.about.support.discussions.desc",
            "Основной канал поддержки. Здесь можно задать вопрос, описать проблему и приложить материалы вручную.",
        ),
    )
    set_support_card_accessibility(
        discussions_card,
        action_name=tr_fn("page.about.support.discussions.accessible_name", "Открыть Forgejo Issues"),
        description=tr_fn(
            "page.about.support.discussions.desc",
            "Основной канал поддержки. Здесь можно задать вопрос, описать проблему и приложить материалы вручную.",
        ),
    )
    discussions_card.clicked.connect(on_open_discussions)
    discussions_group.addSettingCard(discussions_card)
    layout.addWidget(discussions_group)
    layout.addSpacing(16)

    community_title = tr_fn("page.about.support.section.community", "Каналы сообщества")
    community_group = SettingCardGroup(community_title, content_parent)
    set_state_text(community_group, f"Раздел поддержки: {community_title}")
    telegram_card = PushSettingCard(
        tr_fn("page.about.support.button.open", "Открыть"),
        get_themed_qta_icon("fa5b.telegram", color="#229ED9"),
        tr_fn("page.about.support.telegram.title", "Telegram"),
        tr_fn("page.about.support.telegram.desc", "Быстрые вопросы и общение с сообществом"),
    )
    set_support_card_accessibility(
        telegram_card,
        action_name=tr_fn("page.about.support.telegram.accessible_name", "Открыть Telegram"),
        description=tr_fn("page.about.support.telegram.desc", "Быстрые вопросы и общение с сообществом"),
    )
    telegram_card.clicked.connect(on_open_telegram)

    discord_card = PushSettingCard(
        tr_fn("page.about.support.button.open", "Открыть"),
        get_themed_qta_icon("fa5b.discord", color="#5865F2"),
        tr_fn("page.about.support.discord.title", "Discord"),
        tr_fn("page.about.support.discord.desc", "Обсуждение и живое общение"),
    )
    set_support_card_accessibility(
        discord_card,
        action_name=tr_fn("page.about.support.discord.accessible_name", "Открыть Discord"),
        description=tr_fn("page.about.support.discord.desc", "Обсуждение и живое общение"),
    )
    discord_card.clicked.connect(on_open_discord)

    community_group.addSettingCards([telegram_card, discord_card])
    layout.addWidget(community_group)
    layout.addStretch()

    return AboutPageSupportWidgets(
        discussions_group=discussions_group,
        discussions_card=discussions_card,
        community_group=community_group,
        telegram_card=telegram_card,
        discord_card=discord_card,
    )
