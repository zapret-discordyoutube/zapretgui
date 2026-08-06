"""Accessibility helpers for the Support page."""

from __future__ import annotations

from ui.accessibility import enable_keyboard_click, set_control_accessibility, set_state_text


def _set_card_and_button_accessibility(card, *, name: str, description: str) -> None:
    set_state_text(card, name)
    set_control_accessibility(card, name=name, description=description)
    enable_keyboard_click(card)
    button = getattr(card, "button", None)
    if button is not None:
        set_state_text(button, name)
        set_control_accessibility(button, name=name, description=description)


def apply_support_page_accessibility(page) -> None:
    """Задаёт понятные имена карточкам страницы «Поддержка»."""

    tr_fn = page._tr
    if page._support_group is not None:
        support_title = tr_fn("page.support.section.discussions", "Forgejo Issues")
        set_state_text(page._support_group, f"Раздел поддержки: {support_title}")
    if page._community_group is not None:
        community_title = tr_fn("page.support.section.community", "Каналы сообщества")
        set_state_text(page._community_group, f"Раздел поддержки: {community_title}")
    if page._support_card is not None:
        _set_card_and_button_accessibility(
            page._support_card,
            name=tr_fn("page.support.discussions.accessible_name", "Открыть Forgejo Issues"),
            description=tr_fn(
                "page.support.discussions.description",
                "Основной канал поддержки. Здесь можно задать вопрос, описать проблему и приложить нужные материалы вручную.",
            ),
        )
    if page._tg_card is not None:
        _set_card_and_button_accessibility(
            page._tg_card,
            name=tr_fn("page.support.channel.telegram.accessible_name", "Открыть Telegram"),
            description=tr_fn("page.support.channel.telegram.desc", "Быстрые вопросы и общение с сообществом"),
        )
    if page._dc_card is not None:
        _set_card_and_button_accessibility(
            page._dc_card,
            name=tr_fn("page.support.channel.discord.accessible_name", "Открыть Discord"),
            description=tr_fn("page.support.channel.discord.desc", "Обсуждение и живое общение"),
        )


__all__ = ["apply_support_page_accessibility"]
