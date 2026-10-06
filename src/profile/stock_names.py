"""Прежние стоковые имена профилей и их нынешние имена.

Встроенные пресеты и шаблоны называют профили по схеме «Сервис · роль»
(«YouTube · видео (googlevideo.com)»). Но пресет, который пользователь
когда-то изменил, лежит у него копией с прежними именами в `--name=`, а менять
текст чужого пресета программа не вправе. Поэтому прежнее стоковое имя здесь
только узнаётся: в интерфейсе такой профиль показывается под нынешним именем,
в файле остаётся как записан.
"""

from __future__ import annotations

# прежнее стоковое имя -> нынешнее
RENAMED_STOCK_PROFILE_NAMES: dict[str, str] = {
    "googlevideo.com (CDN сервера)": "YouTube · видео (googlevideo.com)",
    "youtube.com (интерфейс)": "YouTube · сайт и приложение",
    "i.ytimg.com (превью роликов)": "YouTube · превью роликов",
    "youtube.com (QUIC)": "YouTube · быстрый протокол QUIC",
    "youtube.com (RTMPS Россия)": "YouTube · трансляции (RTMPS)",
    "discord.com": "Discord · сайт и приложение",
    "updates.discord.com": "Discord · обновления",
    "discord.media (voice RTC)": "Discord · голос и видео (discord.media)",
    "discord (images)": "Discord · картинки",
    "Discord UDP (обычно не нужно)": "Discord · UDP (обычно не нужно)",
    "GitHub": "GitHub · сайт",
    "githubusercontent.com": "GitHub · файлы и картинки",
    "WhatsApp": "WhatsApp · сообщения",
    "static.whatsapp.net": "WhatsApp · картинки и файлы",
    "WhatsApp UDP wide": "WhatsApp · звонки (UDP)",
    "Roblox TCP": "Roblox · игра",
    "Roblox UDP": "Roblox · игра (UDP)",
    "js.rbxcdn.com": "Roblox · скрипты сайта (js.rbxcdn.com)",
    "css.rbxcdn.com": "Roblox · оформление сайта (css.rbxcdn.com)",
    "tr.rbxcdn.com": "Roblox · картинки (tr.rbxcdn.com)",
    "Twitter Images (twimg.com)": "Twitter/X · картинки (twimg.com)",
}

_RENAMED_BY_CASEFOLD = {old.casefold(): new for old, new in RENAMED_STOCK_PROFILE_NAMES.items()}


def current_stock_profile_name(name: object) -> str:
    """Нынешнее имя профиля, если name — его прежнее стоковое имя; иначе name как есть."""
    text = str(name or "").strip()
    return _RENAMED_BY_CASEFOLD.get(text.casefold(), text)


__all__ = ["RENAMED_STOCK_PROFILE_NAMES", "current_stock_profile_name"]
