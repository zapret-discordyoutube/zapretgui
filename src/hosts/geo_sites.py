"""Гео-сайты: сервисы, которые сами не пускают посетителей из России.

В каталоге hosts это сервисы с DNS-профилем (ChatGPT, Claude, Spotify и
другие). Провайдер их не режет — отказывает сам сервис, поэтому стратегия
Zapret им не помогает. Помогает DNS-профиль в «Редакторе hosts» или смена DNS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping


def _site_key(host: object) -> str:
    value = str(host or "").strip().lower().rstrip(".")
    return value[4:] if value.startswith("www.") else value


# Отдельное слово названия сервиса считается его именем, только если оно не
# короче этого: «AI», «Ray», «Web» встречаются в чужих названиях профилей.
_NAME_WORD_MIN_LEN = 4


# Как сервисы называют в жизни: по-русски и сокращённо. Слева — что человек
# напишет в названии профиля, справа — слово из названия сервиса в каталоге.
# Пробелы внутри не нужны: «чат гпт» сравнивается и слитно.
_NAME_ALIASES = {
    "gpt": "chatgpt",
    "гпт": "chatgpt",
    "жпт": "chatgpt",
    "чатгпт": "chatgpt",
    "чатжпт": "chatgpt",
    "чатгбт": "chatgpt",
    "чатджипити": "chatgpt",
    "чатжипити": "chatgpt",
    "опенаи": "openai",
    "опенай": "openai",
    "сора": "sora",
    "джемини": "gemini",
    "джеммини": "gemini",
    "гемини": "gemini",
    "геминай": "gemini",
    "джеминай": "gemini",
    "клод": "claude",
    "клоуд": "claude",
    "клауд": "claude",
    "грок": "grok",
    "копилот": "copilot",
    "копайлот": "copilot",
    "мета": "meta",
    "манус": "manus",
    "курсор": "cursor",
    "виндсерф": "windsurf",
    "опенроутер": "openrouter",
    "дипл": "deepl",
    "ноушн": "notion",
    "ноушен": "notion",
    "спотифай": "spotify",
    "спотик": "spotify",
    "твич": "twitch",
    "канва": "canva",
    "капкут": "capcut",
}


def _name_words(text: object) -> list[str]:
    return re.findall(r"[0-9a-zа-яё]+", str(text or "").casefold())


def _service_name_keys(service_name: str) -> set[str]:
    """Слова, по которым название профиля узнаётся как этот сервис.

    «Gemini AI» → gemini, geminiai; «ChatGPT & Sora (OpenAI)» → chatgpt, sora,
    openai, chatgptsora. Всё название слитно («AMD», «Formula 1») — тоже ключ.
    """
    main = str(service_name or "").split("(", 1)[0]
    keys = {word for word in _name_words(service_name) if len(word) >= _NAME_WORD_MIN_LEN}
    joined = "".join(_name_words(main))
    if joined:
        keys.add(joined)
    return keys


@dataclass(frozen=True, slots=True)
class GeoSites:
    """Адреса гео-сайтов из каталога hosts: адрес → название сервиса."""

    service_by_site: Mapping[str, str]

    def service_for(self, host: str) -> str:
        """Название сервиса, если адрес — его сайт или поддомен сайта; иначе пусто."""
        labels = _site_key(host).split(".")
        for start in range(len(labels) - 1):
            service = self.service_by_site.get(".".join(labels[start:]))
            if service:
                return service
        return ""

    def services_for_name(self, name: str) -> tuple[str, ...]:
        """Гео-сервисы, на которые похоже название нового профиля («gemini» → «Gemini AI»)."""
        words = _name_words(name)
        if not words:
            return ()
        typed = set(words) | {"".join(words)}
        typed |= {_NAME_ALIASES[word] for word in typed if word in _NAME_ALIASES}
        found = {service for service in self.service_by_site.values() if typed & _service_name_keys(service)}
        return tuple(sorted(found))


def build_geo_sites(profile_index: Mapping[str, object]) -> GeoSites:
    """Собирает гео-сайты из индекса каталога (``get_services_profile_index``)."""
    has_proxy = profile_index.get("has_proxy_by_service") or {}
    domain_names = profile_index.get("domain_names_by_service") or {}
    service_by_site: dict[str, str] = {}
    for service_name in profile_index.get("services") or ():
        if not has_proxy.get(service_name, False):
            continue
        for domain in domain_names.get(service_name) or ():
            key = _site_key(domain)
            if key:
                service_by_site.setdefault(key, str(service_name))
    return GeoSites(service_by_site)


# Доля списка, с которой он считается списком гео-сервиса. В общем списке на
# тысячи сайтов пара гео-адресов встречается случайно — о нём молчим.
_GEO_LIST_SHARE_DIVISOR = 20


def geo_services_for_site_list(geo_sites: GeoSites, text: str) -> tuple[str, ...]:
    """Гео-сервисы, которым посвящён список сайтов (текст hostlist-файла).

    Пустой ответ — список не про гео-сервис: его сайтам помогает стратегия.
    Сервисы идут по убыванию числа их адресов в списке.
    """
    total = 0
    counts: dict[str, int] = {}
    for line in str(text or "").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        total += 1
        service = geo_sites.service_for(entry)
        if service:
            counts[service] = counts.get(service, 0) + 1
    if not counts or sum(counts.values()) * _GEO_LIST_SHARE_DIVISOR < total:
        return ()
    return tuple(sorted(counts, key=lambda name: (-counts[name], name)))


def load_geo_sites() -> GeoSites:
    """Читает каталог hosts (файл) — звать вне UI-потока."""
    from hosts.proxy_domains import get_services_profile_index

    return build_geo_sites(get_services_profile_index())


def load_geo_services_for_site_list(text: str) -> tuple[str, ...]:
    """Читает каталог hosts (файл) — звать вне UI-потока."""
    return geo_services_for_site_list(load_geo_sites(), text)


def load_geo_services_for_name(name: str) -> tuple[str, ...]:
    """Читает каталог hosts (файл) — звать вне UI-потока."""
    return load_geo_sites().services_for_name(name)


__all__ = [
    "GeoSites",
    "build_geo_sites",
    "geo_services_for_site_list",
    "load_geo_services_for_name",
    "load_geo_services_for_site_list",
    "load_geo_sites",
]
