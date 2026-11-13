"""Самые известные заблокированные сервисы: что проверять по нажатию на карточку.

Раньше сайт для подбора стратегии выбирали из текстового меню или вводили руками. Карточки
с логотипами нужны, чтобы YouTube, Discord и другие открывались одним нажатием. Список короткий
и состоит из сервисов, которые чаще всего ищут. Здесь только данные: какой адрес подставить и как
сервис называется; логотипы рисует ``blockcheck.ui.brand_icons`` по ключу сервиса.
"""

from __future__ import annotations

from dataclasses import dataclass

from blockcheck.hosts import host_of

__all__ = ["QUICK_SERVICES", "QuickService", "companions_for", "service_for_target"]


@dataclass(frozen=True, slots=True)
class QuickService:
    # Ключ сервиса: он же ключ логотипа в ``brand_icons``.
    key: str
    title: str
    # Адрес, который подставляется в поле «Какой сайт проверить».
    target: str
    # Подсказка под курсором: что именно проверяется.
    hint: str = ""
    # Остальные имена сервиса: подбор стратегии должен открыть и их, а профиль пишется на все имена сразу.
    # У YouTube страница, видеосервер и превью — это разные имена, и закрыт может быть любой.
    # Каждое имя — пара (имя для профиля, хост для пробы): профиль на ytimg.com покрывает и поддомены, проба идёт на i.ytimg.com.
    companions: tuple[tuple[str, str], ...] = ()


QUICK_SERVICES: tuple[QuickService, ...] = (
    QuickService(
        "youtube",
        "YouTube",
        "www.youtube.com",
        "Страница YouTube, превью и видеосервер, который YouTube выдаёт именно вашей сети: открыть должно всё",
        companions=(("googlevideo.com", "googlevideo.com"), ("ytimg.com", "i.ytimg.com")),
    ),
    QuickService("discord", "Discord", "discord.com", "Сайт и вход в Discord"),
    QuickService("telegram", "Telegram", "telegram.org", "Сайт Telegram"),
    QuickService("instagram", "Instagram", "www.instagram.com", "Сайт Instagram"),
    QuickService("facebook", "Facebook", "www.facebook.com", "Сайт Facebook"),
    QuickService("x", "X (Twitter)", "x.com", "Сайт X"),
    QuickService("whatsapp", "WhatsApp", "web.whatsapp.com", "Веб-версия WhatsApp"),
    QuickService("spotify", "Spotify", "www.spotify.com", "Сайт Spotify"),
    QuickService("chatgpt", "ChatGPT", "chatgpt.com", "Сайт ChatGPT"),
    QuickService("rutracker", "RuTracker", "rutracker.org", "Сайт RuTracker"),
    QuickService("twitch", "Twitch", "www.twitch.tv", "Сайт Twitch"),
    QuickService("linkedin", "LinkedIn", "www.linkedin.com", "Сайт LinkedIn"),
)


def service_for_target(value: str) -> QuickService | None:
    """Карточка, чей адрес сейчас стоит в поле: так на ней видно, какой сервис выбран. ``None`` — другой адрес."""
    host = host_of(str(value or "")).lower()
    if not host:
        return None
    return next((item for item in QUICK_SERVICES if host == item.target), None)


def companions_for(value: str) -> tuple[tuple[str, str], ...]:
    """Остальные имена сервиса, если в поле стоит его основной адрес. Другой адрес (например, один googlevideo.com) — пусто:
    кто вписал имя сам, проверяет именно его."""
    host = host_of(str(value or "")).lower()
    service = next((item for item in QUICK_SERVICES if item.target == host), None)
    return service.companions if service else ()
