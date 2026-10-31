"""Кнопка-ссылка на сайт проекта, которая открывается под открытым именем.

Основные имена сайтов проекта (wiki.zapret.moe, git.zapret.moe) в России режут
по TLS. Обычная ``HyperlinkButton`` открыла бы в браузере именно основное имя,
поэтому все ссылки на вики и git в окне — эта кнопка: адрес уходит в браузер
через ``utils.site_links.open_in_browser``, который подставляет открытое имя.
Ссылки на чужие сайты она открывает как есть.
"""

from __future__ import annotations

from qfluentwidgets import HyperlinkButton

from utils import site_links

__all__ = ["SiteLinkButton"]


class SiteLinkButton(HyperlinkButton):
    """Ссылка на статью вики или страницу git: открывается под открытым именем сайта."""

    def _onClicked(self) -> None:
        url = self.getUrl()
        if url.isValid():
            site_links.open_in_browser(url.toString())
