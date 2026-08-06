from __future__ import annotations

import webbrowser
from dataclasses import dataclass

from log.log import log


@dataclass(slots=True)
class AboutActionResult:
    ok: bool
    message: str


def open_support_discussions() -> AboutActionResult:
    from config.urls import SUPPORT_ISSUES_URL

    try:
        webbrowser.open(SUPPORT_ISSUES_URL)
        log(f"Открыт Forgejo Issues: {SUPPORT_ISSUES_URL}", "INFO")
        return AboutActionResult(True, SUPPORT_ISSUES_URL)
    except Exception as e:
        return AboutActionResult(False, str(e))


def open_docs_home() -> AboutActionResult:
    from config.urls import DOCS_URL

    try:
        webbrowser.open(DOCS_URL)
        log(f"Открыт вики-сайт: {DOCS_URL}", "INFO")
        return AboutActionResult(True, DOCS_URL)
    except Exception as e:
        return AboutActionResult(False, str(e))


def open_telegram(domain: str, *, post: int | None = None) -> AboutActionResult:
    try:
        from config.telegram_links import open_telegram_link

        open_telegram_link(domain, post=post)
        log(f"Открыт Telegram: {domain}", "INFO")
        return AboutActionResult(True, domain)
    except Exception as e:
        return AboutActionResult(False, str(e))


def open_discord(url: str) -> AboutActionResult:
    try:
        webbrowser.open(url)
        log(f"Открыт Discord: {url}", "INFO")
        return AboutActionResult(True, url)
    except Exception as e:
        return AboutActionResult(False, str(e))


def open_github(url: str) -> AboutActionResult:
    try:
        webbrowser.open(url)
        log(f"Открыт Forgejo: {url}", "INFO")
        return AboutActionResult(True, url)
    except Exception as e:
        return AboutActionResult(False, str(e))


__all__ = [
    "AboutActionResult",
    "open_discord",
    "open_docs_home",
    "open_github",
    "open_support_discussions",
    "open_telegram",
]
