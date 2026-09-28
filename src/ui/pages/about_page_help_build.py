"""Build-helper вкладки «Справка» для About page."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QHBoxLayout, QFrame, QSizePolicy

from ui.pages.about_page_help_accessibility import set_help_card_accessibility
from ui.accessibility import set_state_text
from ui.theme import get_themed_qta_icon
from ui.widgets.shimmer_label import ShimmerLabel


@dataclass(frozen=True, slots=True)
class HelpLink:
    """Одна ссылка «Справки»: карточка с кнопкой «Открыть» справа."""

    action: str
    icon: str
    icon_color: str
    title_key: str
    title: str
    desc_key: str
    desc: str
    accessible_key: str
    accessible: str
    primary: bool = False


@dataclass(frozen=True, slots=True)
class HelpLinkGroup:
    key: str
    title_key: str
    title: str
    links: tuple[HelpLink, ...]


# Все внешние ссылки программы собраны здесь, в трёх понятных группах.
# action — имя действия открытия в page_deps/system.py::build_about_page_kwargs.
HELP_LINK_GROUPS: tuple[HelpLinkGroup, ...] = (
    HelpLinkGroup(
        key="learn",
        title_key="page.about.help.group.learn",
        title="Научиться",
        links=(
            HelpLink(
                action="forum_for_beginners",
                icon="fa5s.book-open",
                icon_color="",
                title_key="page.about.help.docs.forum.title",
                title="Вики-сайт",
                desc_key="page.about.help.docs.forum.desc",
                desc="Документация и инструкции",
                accessible_key="page.about.help.docs.forum.accessible_name",
                accessible="Открыть вики-сайт",
            ),
            HelpLink(
                action="youtube_course",
                icon="fa5b.youtube",
                icon_color="#FF0000",
                title_key="page.about.help.learn.youtube.title",
                title="Видеокурс на YouTube",
                desc_key="page.about.help.learn.youtube.desc",
                desc="Все видео курса по Zapret 2 одним списком",
                accessible_key="page.about.help.learn.youtube.accessible_name",
                accessible="Открыть видеокурс на YouTube",
            ),
            HelpLink(
                action="android_guide",
                icon="fa5b.android",
                icon_color="#3DDC84",
                title_key="page.about.help.docs.android.title",
                title="На Android (Magisk Zapret, ByeByeDPI и др.)",
                desc_key="page.about.help.docs.android.desc",
                desc="Открыть инструкцию на сайте",
                accessible_key="page.about.help.docs.android.accessible_name",
                accessible="Открыть инструкцию для Android",
            ),
        ),
    ),
    HelpLinkGroup(
        key="ask",
        title_key="page.about.help.group.ask",
        title="Спросить",
        links=(
            HelpLink(
                action="chats_folder",
                icon="fa5s.folder-open",
                icon_color="#229ED9",
                title_key="page.about.help.ask.folder.title",
                title="Папка со всеми чатами",
                desc_key="page.about.help.ask.folder.desc",
                desc="Все наши чаты в Telegram одной папкой — добавьте её целиком",
                accessible_key="page.about.help.ask.folder.accessible_name",
                accessible="Открыть папку со всеми чатами в Telegram",
                primary=True,
            ),
            HelpLink(
                action="support_telegram",
                icon="fa5b.telegram",
                icon_color="#229ED9",
                title_key="page.about.help.ask.telegram.title",
                title="Telegram-чат",
                desc_key="page.about.support.telegram.desc",
                desc="Быстрые вопросы и общение с сообществом",
                accessible_key="page.about.help.ask.telegram.accessible_name",
                accessible="Открыть Telegram-чат",
            ),
            HelpLink(
                action="support_discord",
                icon="fa5b.discord",
                icon_color="#5865F2",
                title_key="page.about.support.discord.title",
                title="Discord",
                desc_key="page.about.support.discord.desc",
                desc="Обсуждение и живое общение",
                accessible_key="page.about.support.discord.accessible_name",
                accessible="Открыть Discord",
            ),
            HelpLink(
                action="support_discussions",
                icon="fa5s.bug",
                icon_color="",
                title_key="page.about.help.ask.issues.title",
                title="Сообщить о проблеме",
                desc_key="page.about.help.ask.issues.desc",
                desc="Forgejo Issues: ошибки, пожелания и обмен конфигами",
                accessible_key="page.about.help.ask.issues.accessible_name",
                accessible="Открыть Forgejo Issues",
            ),
        ),
    ),
    HelpLinkGroup(
        key="news",
        title_key="page.about.help.group.follow",
        title="Следить за новостями",
        links=(
            HelpLink(
                action="links_channel",
                icon="fa5s.link",
                icon_color="#229ED9",
                title_key="page.about.help.news.links.title",
                title="Канал со всеми ссылками",
                desc_key="page.about.help.news.links.desc",
                desc="Все наши каналы, чаты и сайты в одном месте",
                accessible_key="page.about.help.news.links.accessible_name",
                accessible="Открыть канал со всеми ссылками",
                primary=True,
            ),
            HelpLink(
                action="telegram_news",
                icon="fa5b.telegram",
                icon_color="#229ED9",
                title_key="page.about.help.news.telegram.title",
                title="Telegram канал",
                desc_key="page.about.help.news.telegram.desc",
                desc="Новости и обновления",
                accessible_key="page.about.help.news.telegram.accessible_name",
                accessible="Открыть Telegram канал",
            ),
            HelpLink(
                action="mastodon",
                icon="fa5b.mastodon",
                icon_color="#6364FF",
                title_key="page.about.help.news.mastodon.title",
                title="Mastodon профиль",
                desc_key="page.about.help.news.mastodon.desc",
                desc="Новости в Fediverse",
                accessible_key="page.about.help.news.mastodon.accessible_name",
                accessible="Открыть Mastodon профиль",
            ),
            HelpLink(
                action="bastyon",
                icon="fa5s.globe",
                icon_color="",
                title_key="page.about.help.news.bastyon.title",
                title="Bastyon профиль",
                desc_key="page.about.help.news.bastyon.desc",
                desc="Новости в Bastyon",
                accessible_key="page.about.help.news.bastyon.accessible_name",
                accessible="Открыть Bastyon профиль",
            ),
            HelpLink(
                action="source_code",
                icon="fa5s.code-branch",
                icon_color="",
                title_key="page.about.help.news.source.title",
                title="Исходный код",
                desc_key="page.about.help.news.source.desc",
                desc="Репозиторий программы в Forgejo",
                accessible_key="page.about.help.news.source.accessible_name",
                accessible="Открыть исходный код в Forgejo",
            ),
        ),
    ),
)


@dataclass(slots=True)
class AboutPageHelpWidgets:
    motto_wrap: object
    groups: dict[str, object]
    cards: dict[str, object]


def build_about_page_motto_block(*, tr_fn: Callable[[str, str], str], tokens):
    motto_wrap = QFrame()
    motto_wrap.setStyleSheet("QFrame { background: transparent; border: none; }")

    motto_row = QHBoxLayout(motto_wrap)
    motto_row.setContentsMargins(0, 0, 0, 0)
    motto_row.setSpacing(0)

    motto_text_wrap = QFrame()
    motto_text_wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    motto_text_wrap.setStyleSheet("QFrame { background: transparent; border: none; }")

    motto_text_layout = QVBoxLayout(motto_text_wrap)
    motto_text_layout.setContentsMargins(0, 0, 0, 0)
    motto_text_layout.setSpacing(2)

    # Строки по очереди выплывают при открытии вкладки, потом по английской
    # и с задержкой по русской ходит лёгкий зацикленный блик.
    glow = tokens.accent_hex if tokens.is_light else "#ffffff"
    motto_title = ShimmerLabel(
        tr_fn("page.about.help.motto.title", "keep thinking, keep searching, keep learning....")
    )
    motto_title.set_glow_color(glow)
    motto_title.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
    motto_title.setWordWrap(True)
    motto_title.setStyleSheet(
        f"QLabel {{ color: {tokens.fg}; font-size: 25px; font-weight: 700; "
        f"letter-spacing: 0.8px; "
        f"font-family: 'Segoe UI Variable Display', 'Segoe UI', sans-serif; }}"
    )

    motto_translate = ShimmerLabel(
        tr_fn(
            "page.about.help.motto.subtitle",
            "Продолжай думать, продолжай искать, продолжай учиться....",
        ),
        enter_delay_ms=180,
        first_delay_ms=1500,
    )
    motto_translate.set_glow_color(glow)
    motto_translate.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
    motto_translate.setWordWrap(True)
    motto_translate.setStyleSheet(
        f"QLabel {{ color: {tokens.fg_muted}; font-size: 17px; font-style: italic; "
        f"font-weight: 600; letter-spacing: 0.5px; "
        f"font-family: 'Palatino Linotype', 'Book Antiqua', 'Georgia', serif; "
        f"padding-top: 2px; }}"
    )

    motto_cta = ShimmerLabel(
        tr_fn(
            "page.about.help.motto.cta",
            "Zapret2 - думай свободно, ищи смелее, учись всегда.",
        ),
        enter_delay_ms=360,
        shimmer=False,
    )
    motto_cta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
    motto_cta.setWordWrap(True)
    motto_cta.setStyleSheet(
        f"QLabel {{ color: {tokens.fg_faint}; font-size: 12px; letter-spacing: 1.1px; "
        f"font-family: 'Segoe UI', sans-serif; text-transform: uppercase; "
        f"padding-top: 6px; }}"
    )

    motto_text_layout.addWidget(motto_title)
    motto_text_layout.addWidget(motto_translate)
    motto_text_layout.addWidget(motto_cta)
    motto_row.addWidget(motto_text_wrap, 1)
    return motto_wrap


def build_about_page_help_content(
    layout: QVBoxLayout,
    *,
    tr_fn: Callable[[str, str], str],
    tokens,
    content_parent,
    make_section_label: Callable[[str], object],
    push_setting_card_cls,
    primary_push_setting_card_cls,
    setting_card_group_cls,
    on_open_link: Callable[[str], None],
) -> AboutPageHelpWidgets:
    motto_wrap = build_about_page_motto_block(tr_fn=tr_fn, tokens=tokens)
    layout.addWidget(motto_wrap)
    layout.addSpacing(6)
    layout.addWidget(make_section_label(tr_fn("page.about.help.section.links", "Ссылки")))

    open_text = tr_fn("page.about.help.button.open", "Открыть")
    groups: dict[str, object] = {}
    cards: dict[str, object] = {}
    for group_spec in HELP_LINK_GROUPS:
        title = tr_fn(group_spec.title_key, group_spec.title)
        group = setting_card_group_cls(title, content_parent)
        set_state_text(group, f"Раздел справки: {title}")
        group_cards = []
        for link in group_spec.links:
            card_cls = primary_push_setting_card_cls if link.primary else push_setting_card_cls
            description = tr_fn(link.desc_key, link.desc)
            card = card_cls(
                open_text,
                get_themed_qta_icon(link.icon, color=link.icon_color or tokens.accent_hex),
                tr_fn(link.title_key, link.title),
                description,
            )
            set_help_card_accessibility(
                card,
                action_name=tr_fn(link.accessible_key, link.accessible),
                description=description,
            )
            card.clicked.connect(lambda _checked=False, action=link.action: on_open_link(action))
            cards[link.action] = card
            group_cards.append(card)
        group.addSettingCards(group_cards)
        groups[group_spec.key] = group
        layout.addWidget(group)
        layout.addSpacing(8)
    layout.addStretch()

    return AboutPageHelpWidgets(motto_wrap=motto_wrap, groups=groups, cards=cards)
