"""Разбор адресов целей BlockCheck — единственное место, где живёт эта логика.

До появления модуля один и тот же ``re.sub(r"^https?://", ...)`` был скопирован в
восьми местах runner.py и targets.py, а список двухуровневых публичных суффиксов —
в двух. Расхождения между копиями приводили к тому, что цель и её DNS-результат
переставали склеиваться.
"""

from __future__ import annotations

import re

__all__ = [
    "PSEUDO_SCHEMES",
    "base_domain",
    "host_candidates",
    "host_of",
    "is_pseudo_target",
]


# Значения целей вида ``PING:1.1.1.1`` / ``STUN:...`` / ``TCP:16-20KB`` — не URL.
PSEUDO_SCHEMES = ("PING:", "STUN:", "TCP:")

_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")

# Суффиксы, у которых регистрируемое имя — третьего уровня (``bbc.co.uk``).
_PUBLIC_SUFFIX_2LEVEL = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk",
    "com.au", "net.au", "org.au",
    "co.jp", "ne.jp", "or.jp",
    "com.br", "com.mx", "com.tr", "co.id",
    "com.ua", "co.kr", "co.in",
})


def is_pseudo_target(value: str) -> bool:
    """True для служебных значений (PING:/STUN:/TCP:), которые не являются URL."""
    return str(value or "").startswith(PSEUDO_SCHEMES)


def host_of(value: str) -> str:
    """Имя хоста из значения цели: URL, ``host:port`` или голое имя.

    Служебные значения (``PING:``/``STUN:``/``TCP:``) возвращаются как есть —
    вызывающий обязан отсеять их через :func:`is_pseudo_target`.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    if is_pseudo_target(raw):
        return raw

    raw = _SCHEME_RE.sub("", raw)
    raw = raw.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]

    if raw.startswith("["):  # IPv6-литерал в квадратных скобках
        right = raw.find("]")
        if right > 0:
            return raw[1:right].strip().lower()

    # Порт отрезаем только у имён; голый IPv6 без скобок содержит много ":".
    if raw.count(":") == 1:
        raw = raw.rsplit(":", 1)[0]

    return raw.rstrip(".").lower().strip()


def base_domain(host: str) -> str:
    """Регистрируемое имя: ``www.bbc.co.uk`` → ``bbc.co.uk``, ``a.b.com`` → ``b.com``."""
    normalized = host_of(host)
    if not normalized or is_pseudo_target(normalized):
        return normalized

    if normalized.startswith("www."):
        normalized = normalized[4:]

    parts = normalized.split(".")
    if len(parts) <= 2:
        return normalized

    tail2 = ".".join(parts[-2:])
    if tail2 in _PUBLIC_SUFFIX_2LEVEL and len(parts) >= 3:
        return ".".join(parts[-3:])
    return tail2


def host_candidates(host: str) -> set[str]:
    """Варианты имени для сопоставления цели с DNS-результатом.

    Возвращает само имя, вариант без ``www.`` и регистрируемое имя — так
    ``www.facebook.com`` находит запись DNS по ``facebook.com`` и наоборот.
    """
    normalized = host_of(host)
    if not normalized or is_pseudo_target(normalized) or "." not in normalized:
        return set()

    candidates = {normalized}
    if normalized.startswith("www."):
        candidates.add(normalized[4:])
    candidates.add(base_domain(normalized))
    return {candidate for candidate in candidates if candidate and "." in candidate}
