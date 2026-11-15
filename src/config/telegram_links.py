# config/telegram_links.py
"""Утилиты для открытия Telegram ссылок с fallback на https"""

import re
import webbrowser
from log.log import log
from platforms import current as current_platform

# Рекламные метки (параметр start) для ссылок на бота @zapretvpns_bot.
# Формат метки общий для всех проектов Zapret: «<источник>_<место>», источник
# ZapretGUI — «gui». По метке бот показывает на панели «Реклама», откуда пришёл человек.
_START_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def is_telegram_installed() -> bool:
    """Есть ли в системе программа для ссылок tg:// (обычно Telegram)"""
    try:
        return bool(current_platform().system_settings.url_scheme_handler_registered("tg"))
    except Exception as e:
        log(f"Ошибка проверки tg:// протокола: {e}", "DEBUG")
        return False


def telegram_urls(
    domain: str,
    post: int | None = None,
    slug: str | None = None,
    start: str | None = None,
) -> tuple[str, str]:
    """Возвращает пару (tg://-ссылка, https://-ссылка).

    start — рекламная метка бота (deep-link): только A-Za-z0-9_-, до 64 символов,
    префикс «ref_» зарезервирован под рефералов.
    """
    if start is not None:
        if not _START_RE.fullmatch(start) or start.startswith("ref_"):
            raise ValueError(f"Недопустимая метка start: {start!r}")
    if slug:
        # Ссылка на папку с каналами
        return f"tg://addlist?slug={slug}", f"https://t.me/addlist/{slug}"
    if post:
        return (
            f"tg://resolve?domain={domain}&post={post}",
            f"https://t.me/{domain}/{post}",
        )
    if start:
        return (
            f"tg://resolve?domain={domain}&start={start}",
            f"https://t.me/{domain}?start={start}",
        )
    return f"tg://resolve?domain={domain}", f"https://t.me/{domain}"


def open_telegram_link(
    domain: str,
    post: int = None,
    slug: str = None,
    start: str = None,
) -> None:
    """
    Открывает Telegram ссылку через tg:// или https:// (fallback)

    Args:
        domain: Имя канала/бота (например 'zaprethelp')
        post: Номер поста (опционально)
        slug: Slug для addlist (опционально, вместо domain)
        start: Рекламная метка бота (опционально, ?start=<метка>)
    """
    tg_url, https_url = telegram_urls(domain, post=post, slug=slug, start=start)

    if is_telegram_installed():
        log(f"Открываю Telegram: {tg_url}", "DEBUG")
        webbrowser.open(tg_url)
    else:
        log(f"Telegram не установлен, открываю в браузере: {https_url}", "DEBUG")
        webbrowser.open(https_url)


def open_telegram_url(tg_url: str, https_url: str) -> None:
    """
    Открывает Telegram ссылку с готовыми URL

    Args:
        tg_url: URL в формате tg://
        https_url: Fallback URL в формате https://
    """
    if is_telegram_installed():
        webbrowser.open(tg_url)
    else:
        webbrowser.open(https_url)
