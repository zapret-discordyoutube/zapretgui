"""Части находки, общие для итога BlockCheck и отчёта карточки: перечень серверов и его метки.

Находка про DNS приходит одной фразой: «Обычные ответы подменяются у серверов:
Cloudflare (1.1.1.1), Cloudflare (1.0.0.1) и ещё 6. Пояснение». Читать её
сплошным текстом тяжело, поэтому перечень серверов показывают метками: по
одной на сервис, со счётчиком адресов и значком, сами адреса — в подсказке.
"""

from __future__ import annotations

import re

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QHBoxLayout, QWidget
from qfluentwidgets import CaptionLabel

from blockcheck.ui.brand_icons import BrandIcon, named_brand
from ui.fluent_widgets import set_tooltip
from ui.theme_refresh import ThemeRefreshBinding

_SERVER = re.compile(r"\s*([^,()]+?) \(([^()]+)\)")
_MORE = re.compile(r"\s*и ещё (\d+)")


def split_finding(text: str) -> tuple[str, str]:
    """Находка про DNS → заголовок и подробности: «что случилось: подробности» или «что случилось. Пояснение»."""
    text = str(text or "").strip()
    for separator in (": ", ". "):
        head, found, tail = text.partition(separator)
        tail = tail.strip()
        if found and tail:
            tail = f"{tail[:1].upper()}{tail[1:]}"
            return head.rstrip("."), tail if tail.endswith((".", "!", "?")) else f"{tail}."
    return text, ""


def split_server_list(detail: str) -> tuple[list[tuple[str, list[str]]], int, str]:
    """Перечень «Cloudflare (1.1.1.1), Cloudflare (1.0.0.1) и ещё 6. Пояснение» по частям.

    Возвращает серверы по названиям с их адресами, число не названных и
    остаток текста. Если пояснение начинается не с перечня — серверов нет, а
    остаток равен всему тексту.
    """
    text = str(detail or "")
    servers: dict[str, list[str]] = {}
    position = 0
    while True:
        match = _SERVER.match(text, position)
        if match is None:
            break
        servers.setdefault(match.group(1).strip(), []).append(match.group(2).strip())
        position = match.end()
        if not text.startswith(", ", position):
            break
        position += 2
    if not servers:
        return [], 0, text
    more = 0
    match = _MORE.match(text, position)
    if match is not None:
        more = int(match.group(1))
        position = match.end()
    return list(servers.items()), more, text[position:].lstrip(". ").strip()


class ServerChip(QWidget):
    """Метка сервиса (DNS, хостинг, сайт): значок, название и сколько его адресов названо. Адреса — в подсказке."""

    def __init__(self, name: str, addresses: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(22)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 0, 8, 0)
        layout.setSpacing(5)
        brand = named_brand(name)
        self.icon: BrandIcon | None = None
        if brand is not None:
            self.icon = BrandIcon(brand.icon, brand.color, self, size=13)
            layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.text = name if len(addresses) < 2 else f"{name} ×{len(addresses)}"
        layout.addWidget(CaptionLabel(self.text, self), 0, Qt.AlignmentFlag.AlignVCenter)
        if addresses:
            set_tooltip(self, f"{name}: {', '.join(addresses)}")
        self._theme_refresh = ThemeRefreshBinding(self, lambda *_args, **_kwargs: self.update())

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme_color("surface_bg_hover", QColor(255, 255, 255, 18)))
        painter.drawRoundedRect(self.rect(), 4, 4)
        painter.end()


def theme_color(token: str, fallback: QColor) -> QColor:
    try:
        from ui.theme import get_theme_tokens, to_qcolor

        return to_qcolor(getattr(get_theme_tokens(), token), fallback)
    except Exception:
        return QColor(fallback)


__all__ = ["ServerChip", "split_finding", "split_server_list", "theme_color"]
