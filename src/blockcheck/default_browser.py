"""Браузер по умолчанию: каким браузером человек откроет сайт.

Проверка после «Применить» открывает сайт браузером без окна (Edge или Chrome),
а человек может пользоваться Firefox. Совет «что сделать» должен быть для его
браузера, а не для всех сразу. Windows хранит выбор в реестре:
``HKCU\\Software\\Microsoft\\Windows\\Shell\\Associations\\UrlAssociations\\https\\UserChoice``,
значение ``ProgId`` — короткое имя программы (``MSEdgeHTM``, ``ChromeHTML``,
``FirefoxURL-308046B0AF4A39CB``…). По нему узнаётся семейство браузера.

Чтение реестра передаётся снаружи (``read_prog_id``), поэтому разбор проверяется
без Windows.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["BROWSER_CHROME", "BROWSER_EDGE", "BROWSER_FIREFOX", "BROWSER_OTHER_CHROMIUM", "DefaultBrowser", "browser_from_prog_id", "default_browser"]

BROWSER_EDGE = "edge"
BROWSER_CHROME = "chrome"
BROWSER_FIREFOX = "firefox"
# Браузеры на движке Chromium, у которых правила (политики) лежат в своём разделе реестра.
BROWSER_OTHER_CHROMIUM = "other_chromium"


# Начало ProgId → (семейство, имя для человека).
_PROG_IDS = (
    ("MSEdgeHTM", BROWSER_EDGE, "Edge"),
    ("ChromeHTML", BROWSER_CHROME, "Chrome"),
    ("FirefoxURL", BROWSER_FIREFOX, "Firefox"),
    ("FirefoxHTML", BROWSER_FIREFOX, "Firefox"),
    ("BraveHTML", BROWSER_OTHER_CHROMIUM, "Brave"),
    ("YandexHTML", BROWSER_OTHER_CHROMIUM, "Яндекс Браузер"),
    ("OperaStable", BROWSER_OTHER_CHROMIUM, "Opera"),
    ("OperaGXStable", BROWSER_OTHER_CHROMIUM, "Opera GX"),
    ("VivaldiHTM", BROWSER_OTHER_CHROMIUM, "Vivaldi"),
    # Сборки Chromium: ProgId «ChromiumHTM.<хеш>» (так на win10, 2026-11-02).
    ("ChromiumHTM", BROWSER_OTHER_CHROMIUM, "Chromium"),
    # Linux: имя файла .desktop из ``xdg-settings get default-web-browser`` (Flatpak: org.mozilla.firefox.desktop).
    ("org.mozilla.firefox", BROWSER_FIREFOX, "Firefox"),
    ("firefox", BROWSER_FIREFOX, "Firefox"),
    ("microsoft-edge", BROWSER_EDGE, "Edge"),
    ("google-chrome", BROWSER_CHROME, "Chrome"),
    ("chromium", BROWSER_OTHER_CHROMIUM, "Chromium"),
    ("brave-browser", BROWSER_OTHER_CHROMIUM, "Brave"),
    ("com.brave.browser", BROWSER_OTHER_CHROMIUM, "Brave"),
    ("vivaldi", BROWSER_OTHER_CHROMIUM, "Vivaldi"),
    ("opera", BROWSER_OTHER_CHROMIUM, "Opera"),
    ("yandex-browser", BROWSER_OTHER_CHROMIUM, "Яндекс Браузер"),
)


@dataclass(frozen=True, slots=True)
class DefaultBrowser:
    # Семейство (BROWSER_*); пусто — браузер не узнан.
    family: str = ""
    title: str = ""
    prog_id: str = ""

    @property
    def known(self) -> bool:
        return bool(self.family)


def browser_from_prog_id(prog_id: str) -> DefaultBrowser:
    text = str(prog_id or "").strip()
    for prefix, family, title in _PROG_IDS:
        if text.lower().startswith(prefix.lower()):
            return DefaultBrowser(family, title, text)
    return DefaultBrowser(prog_id=text)


def _read_prog_id() -> str:
    """Системное имя браузера по умолчанию (Windows: ProgId, Linux: файл .desktop); как узнать — решает платформа."""
    from platforms import current as current_platform

    return str(current_platform().system_settings.default_browser_id() or "")


def default_browser(read_prog_id: Callable[[], str] = _read_prog_id) -> DefaultBrowser:
    """Браузер по умолчанию; не узнан (нет записи, не Windows) — пустой."""
    try:
        return browser_from_prog_id(read_prog_id())
    except (OSError, ImportError):
        return DefaultBrowser()
