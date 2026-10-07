"""Значки сайтов и DNS-сервисов в их фирменных цветах.

На карточках BlockCheck сайт узнают по логотипу, а не по цвету результата:
логотип всегда своего цвета (YouTube красный, Telegram голубой), а результат
показывают слово и точка рядом. Рисует значки ``profile.ui.profile_icon`` —
тот же набор логотипов, что у профилей пресета.

- ``site_brand`` — значок сайта по ключу сервиса, названию или адресу;
- ``brands_in_text`` — значки DNS-сервисов, названных в тексте находки;
- ``BrandIcon`` — сам значок на экране.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QLabel

from ui.theme_refresh import ThemeRefreshBinding


@dataclass(frozen=True, slots=True)
class Brand:
    icon: str
    color: str
    name: str = ""


# Ключ сервиса BlockCheck → значок и фирменный цвет.
_SITES: dict[str, Brand] = {
    "discord": Brand("simple:discord:DI", "#5865F2"),
    "youtube": Brand("simple:youtube:YT", "#FF0000"),
    "telegram": Brand("simple:telegram:TG", "#26A5E4"),
    "instagram": Brand("simple:instagram:IN", "#E4405F"),
    "facebook": Brand("simple:facebook:FB", "#0866FF"),
    "x": Brand("simple:x:X", "#000000"),
    "twitter": Brand("simple:x:X", "#000000"),
    "linkedin": Brand("fa5b.linkedin", "#0A66C2"),
    "spotify": Brand("simple:spotify:SP", "#1ED760"),
    "rutracker": Brand("fa5s.magnet", "#4F8FE0"),
    "nnmclub": Brand("fa5s.magnet", "#4F8FE0"),
    "google": Brand("simple:google:G", "#4285F4"),
    "cloudflare": Brand("simple:cloudflare:CF", "#F38020"),
    "yandex": Brand("fa5b.yandex", "#FC3F1D"),
    "vk": Brand("fa5b.vk", "#0077FF"),
    "whatsapp": Brand("simple:whatsapp:WA", "#25D366"),
    "signal": Brand("fa5s.comment-dots", "#3A76F0"),
    "messenger": Brand("fa5b.facebook-messenger", "#0084FF"),
    "twitch": Brand("simple:twitch:TW", "#9146FF"),
    "soundcloud": Brand("simple:soundcloud:SC", "#FF5500"),
    "dailymotion": Brand("fa5b.dailymotion", "#0A7BFF"),
    "patreon": Brand("simple:patreon:PA", "#F96854"),
    "github": Brand("simple:github:GH", "#181717"),
    "docker": Brand("fa5b.docker", "#2496ED"),
    "chatgpt": Brand("own:openai:AI", "#10A37F"),
    "deepl": Brand("simple:deepl:DL", "#0F2B46"),
    "canva": Brand("own:canva:CA", "#00C4CC"),
    "speedtest": Brand("simple:speedtest:ST", "#141526"),
}
# Как сайт называется на экране и в адресе → ключ сервиса.
_ALIASES = {"яндекс": "yandex", "вконтакте": "vk", "ytimg": "youtube", "googlevideo": "youtube"}

# DNS-сервисы: слово в тексте находки → значок. Порядок — как их показывать.
_DNS: tuple[tuple[tuple[str, ...], Brand], ...] = (
    (("cloudflare",), Brand("simple:cloudflare:CF", "#F38020", "Cloudflare")),
    (("google",), Brand("simple:google:G", "#4285F4", "Google DNS")),
    (("adguard",), Brand("simple:adguard:AG", "#68BC71", "AdGuard")),
    (("quad9",), Brand("simple:quad9:Q9", "#DC205E", "Quad9")),
    (("opendns",), Brand("own:opendns:OD", "#F58025", "OpenDNS")),
    (("яндекс", "yandex"), Brand("fa5b.yandex", "#FC3F1D", "Яндекс DNS")),
    (("xbox",), Brand("fa5b.xbox", "#107C10", "Xbox DNS")),
)


def _words(value: str) -> list[str]:
    text = str(value or "").lower()
    for char in "()/,:":
        text = text.replace(char, " ")
    words: list[str] = []
    for chunk in text.split():
        # Адрес сайта: «www.instagram.com» — имя сайта стоит перед последней точкой.
        parts = [part for part in chunk.split(".") if part]
        words.extend(parts[:-1] if len(parts) > 1 else parts)
    return words


def site_brand(*names: str) -> Brand | None:
    """Значок сайта: пробует ключ сервиса, название и адрес по очереди. ``None`` — своего значка нет."""
    for name in names:
        for word in _words(name):
            brand = _SITES.get(_ALIASES.get(word, word))
            if brand is not None:
                return brand
    return None


def brands_in_text(text: str) -> list[Brand]:
    """DNS-сервисы, названные в тексте, каждый один раз."""
    lowered = str(text or "").lower()
    return [brand for needles, brand in _DNS if any(needle in lowered for needle in needles)]


def readable_color(color: str, *, light_theme: bool) -> str:
    """Фирменный цвет, который видно на фоне: чёрный логотип на тёмной теме стал бы невидимым."""
    value = QColor(color)
    if not value.isValid():
        return color
    brightness = (value.red() * 299 + value.green() * 587 + value.blue() * 114) / 255000
    if not light_theme and brightness < 0.22:
        return "#f2f2f2"
    if light_theme and brightness > 0.85:
        return "#1f1f1f"
    return value.name()


class BrandIcon(QLabel):
    """Значок на карточке. ``color`` пустой — нейтральный цвет темы (для значков проверок)."""

    def __init__(self, icon: str, color: str = "", parent=None, *, size: int = 20) -> None:
        super().__init__(parent)
        self._icon = icon
        self._color = color
        self._size = size
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def icon_name(self) -> str:
        return self._icon

    def brand_color(self) -> str:
        return self._color

    def set_icon(self, icon: str, color: str = "") -> None:
        self._icon = icon
        self._color = color
        self._apply_theme_refresh()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        try:
            from profile.ui.profile_icon import profile_icon_pixmap
            from ui.theme import get_theme_tokens

            tokens = tokens or get_theme_tokens()
            if self._color:
                color = readable_color(self._color, light_theme=bool(tokens.is_light))
            else:
                color = str(tokens.icon_fg_muted)
            self.setPixmap(profile_icon_pixmap(self._icon, color=color, size=self._size))
        except Exception:
            pass


__all__ = ["Brand", "BrandIcon", "brands_in_text", "readable_color", "site_brand"]
