"""Переключатель «Шифровать DNS (DoH)» страницы «Настройка DNS»: правила в одном месте.

Что это. Серверы с меткой DoH (Cloudflare, Google, Quad9…) умеют отвечать на
шифрованные запросы. По умолчанию переключатель включён: такие серверы ставятся
шифрованно (Windows 11 шифрует сама, Windows 10 — через встроенный dnscrypt-proxy).
Выключен — те же серверы ставятся обычными адресами: провайдер видит запросы.

Что переключатель не трогает: плитки группы «Шифрованные» (DNSCrypt, ODoH).
Это не DoH, Windows их не умеет совсем, поэтому они всегда идут через движок.

Где хранится: settings.sqlite3, секция dns, поле doh_enabled (settings.store).
Читают его runtime.write_doh_templates (что писать адаптеру) и страница (как
ставить плитку); сами решения «через движок или адресом» и «можно ли поставить
такой сервер» принимаются только здесь.
"""

from __future__ import annotations

from dns.dns_providers import is_encrypted_only, proxy_mode_for
from log.log import log

# Сервер принимает только шифрованные запросы, а переключатель выключен.
REFUSE_SWITCH_OFF = "switch_off"
# Сервер принимает только шифрованные запросы, а эта Windows их не шифрует и движка для сервера нет.
REFUSE_WINDOWS = "windows"


def is_enabled() -> bool:
    """Включено ли шифрование DoH; при сбое чтения — включено (так работает программа по умолчанию)."""
    try:
        from settings.store import get_dns_doh_enabled

        return bool(get_dns_doh_enabled())
    except Exception as exc:
        log(f"DNS: не удалось прочитать переключатель DoH: {exc}", "WARNING")
        return True


def set_enabled(value: bool) -> bool:
    from settings.store import set_dns_doh_enabled

    return bool(set_dns_doh_enabled(bool(value)))


def proxy_mode(data: dict, *, doh_supported: bool, doh_enabled: bool = True) -> str:
    """Режим встроенного шифрованного DNS для плитки; пусто — ставить адресами.

    Плитка-режим идёт через движок всегда. Обычный сервер с DoH — только если
    переключатель включён и Windows сама шифровать не умеет (dns_providers.proxy_mode_for).
    """
    if not data.get("local_proxy") and not doh_enabled:
        return ""
    return proxy_mode_for(data, doh_supported=doh_supported)


def tile_encrypts(data: dict, *, doh_supported: bool, doh_enabled: bool = True) -> bool:
    """Будет ли плитка работать шифрованно: от этого зависит метка DoH на ней."""
    if not data.get("doh") or not doh_enabled:
        return False
    return bool(doh_supported or proxy_mode(data, doh_supported=doh_supported, doh_enabled=doh_enabled))


def refusal(data: dict, *, doh_supported: bool, doh_enabled: bool = True) -> str:
    """Почему сервер нельзя поставить сейчас («» — можно).

    Сервер только с шифрованием не отвечает на обычные запросы: поставленный
    так, он оставил бы компьютер без DNS.
    """
    if data.get("local_proxy") or not is_encrypted_only(data):
        return ""
    if not doh_enabled:
        return REFUSE_SWITCH_OFF
    if not doh_supported and not proxy_mode(data, doh_supported=doh_supported, doh_enabled=doh_enabled):
        return REFUSE_WINDOWS
    return ""


__all__ = [
    "REFUSE_SWITCH_OFF",
    "REFUSE_WINDOWS",
    "is_enabled",
    "proxy_mode",
    "refusal",
    "set_enabled",
    "tile_encrypts",
]
